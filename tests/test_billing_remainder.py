"""What a real Stripe delivery has to move, driven over HTTP end to end.

Three failures met on this path and every one answered 200 while losing the
money:

1. `StripeBillingService`'s handlers are the only writer of `billing_subscriptions`,
   and nothing called them. `POST /webhooks/stripe` mapped the delivery to
   `orgs.plan` and stopped, so after a real payment `GET /billing/subscription`
   answered `null` and change/cancel/reactivate raised "No subscription for org".
   The receiver now forwards the verified delivery to the billing store as well,
   and these tests drive the whole chain — checkout metadata, signature, receiver,
   store, and the customer's own next request — rather than calling the handlers
   directly, because "the handler works" was true the entire time the product was
   broken.

2. The tier the customer bought could not be stored. `orgs.plan`'s vocabulary
   (`app.tenancy.models.PLAN_QUOTAS`) had three values, so Growth and Business
   were translated to `team` on the way in and the org record had no way to say
   which had been bought — and that column is what `FeatureGateService` resolves
   entitlements from on every deployment that upgrades by webhook.

3. The two halves of one delivery disagreed about where the tier comes from.
   `billing_subscriptions` followed the subscription's Stripe Price, `orgs.plan`
   followed the metadata, and a tier change bought in Stripe's own customer
   portal rewrites the Price and leaves the metadata alone — so the upgraded
   customer got the features they had paid for and was metered on the quota of
   the tier they had left.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

import app.api.main as api
import app.config as cfg
from app import runtime, tenant_context
from app.billing import plans as billing_plans
from app.billing import runtime as billing_runtime
from app.billing import stripe_integration
from app.billing import webhooks as billing_webhooks
from app.billing.api import BillingAPI
from app.billing.feature_gate import FeatureGateService
from app.billing.models import BillingCycle
from app.billing.plans import plan_uuid
from app.billing.stripe_integration import StripeBillingService, subscription_metadata
from app.billing.webhooks import verify_stripe_signature
from app.database import engine as db_engine
from app.database.orgs import SINGLE_TENANT_ORG, org_uuid_for
from app.tenancy import auth as tenant_auth
from app.tenancy import models as tenancy_models
from app.tenancy import scoping
from app.tenant_context import TenantConfig, reset_tenant, set_tenant

WHSEC = "whsec_test"
PRICES = "growth:monthly=price_growth_m,team:monthly=price_team_m"


class SignatureVerificationError(Exception):
    """What `stripe.Webhook.construct_event` raises for a header that does not
    match the body — a different failure from a body it cannot parse."""


class RecordingStripeSDK:
    """A `StripeClient` that verifies the delivery itself and decodes the body.

    Two things this proves that a canned event cannot. The raw bytes and the
    verbatim `Stripe-Signature` header have to survive the whole way from the
    receiver into the store — the store re-verifies against its own secret, so a
    body that had been parsed and re-serialised, or a header dropped along the
    way, fails here. And the event it hands back is a plain `dict`, the shape a
    JSON body decodes to, which every handler has to cope with: `event.type` and
    `data.items.data[0]` raise `AttributeError` on a dict, and this receiver
    reports that as a failed delivery for a perfectly good event.
    """

    def __init__(self) -> None:
        self.deliveries: list[tuple[bytes, str]] = []
        self.checkout_calls: list[dict[str, Any]] = []

    def create_customer(self, **kwargs: Any) -> Any:
        return type("C", (), {"id": "cus_test_1"})()

    def create_checkout_session(self, **kwargs: Any) -> Any:
        self.checkout_calls.append(kwargs)
        return type("S", (), {"id": "cs_1", "url": "https://checkout.test/cs_1"})()

    def construct_webhook_event(self, payload: bytes, signature: str, secret: str) -> Any:
        self.deliveries.append((payload, signature))
        if not verify_stripe_signature(payload, signature, secret, now=int(time.time())):
            # Not a ValueError: the SDK raises `SignatureVerificationError` here
            # and `ValueError` only for a body it cannot parse, and the service
            # tells the two apart.
            raise SignatureVerificationError("signature does not match the payload")
        return json.loads(payload)


def _subscription_body(
    metadata: dict[str, str],
    etype: str = "customer.subscription.created",
    price_id: str = "price_growth_m",
) -> bytes:
    now = int(time.time())
    return json.dumps({
        "type": etype,
        "data": {"object": {
            "id": "sub_test_1",
            "customer": "cus_test_1",
            "status": "active",
            "metadata": metadata,
            "items": {"data": [{"id": "si_1", "price": {
                "id": price_id, "recurring": {"interval": "month"}}}]},
            "current_period_start": now,
            "current_period_end": now + 30 * 86400,
            "cancel_at_period_end": False,
        }},
    }).encode()


def _signed(body: bytes, secret: str = WHSEC) -> dict[str, str]:
    ts = str(int(time.time()))
    mac = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return {"Stripe-Signature": f"t={ts},v1={mac}", "Content-Type": "application/json"}


def _reset() -> None:
    cfg.get_settings.cache_clear()
    tenant_auth.reset_store_cache()
    tenant_context.current_tenant.set(None)
    tenant_context.actor_var.set("-")
    scoping.reset_all()
    api._RL.clear()
    api._reset_limiter()
    runtime.reset()
    db_engine._engine = None
    db_engine._session_factory = None
    billing_runtime._BILLING_STORE = None
    billing_runtime._STRIPE_SERVICE = None
    billing_runtime._BILLING_API = None


async def _provision(store):
    await store.setup()
    org = await store.create_org("Acme", plan="free")
    key, _ = await store.issue_api_key(org.id, "k-owner", role="owner")
    return org, key


@pytest.fixture
def deployment(tmp_path, monkeypatch):
    """The real app, with Stripe configured and a tenant that has paid nothing yet."""
    monkeypatch.setenv("COPILOT_MULTI_TENANT", "true")
    monkeypatch.setenv("COPILOT_TENANT_DB", str(tmp_path / "tenants.sqlite"))
    monkeypatch.setenv("COPILOT_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/app.db")
    monkeypatch.setenv("COPILOT_API_TOKEN", "prodtoken")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", WHSEC)
    monkeypatch.setenv("STRIPE_API_KEY", "sk_test_x")
    monkeypatch.setenv("STRIPE_PRICE_IDS", PRICES)
    _reset()
    org, key = asyncio.run(_provision(tenant_auth.get_store()))

    # The SDK-backed client needs a live Stripe account, so the service is built
    # over the recording double instead. Everything above it — the route, the
    # signature check, `BillingAPI`, the handlers and `DatabaseBillingStore` — is
    # the production object.
    stripe = RecordingStripeSDK()
    service = StripeBillingService(
        store=billing_runtime.get_billing_store(),
        stripe_client=stripe,
        webhook_secret=WHSEC,
        frontend_url="https://app.test",
    )
    monkeypatch.setattr(billing_runtime, "_STRIPE_SERVICE", service)
    monkeypatch.setattr(
        billing_runtime,
        "_BILLING_API",
        BillingAPI(store=billing_runtime.get_billing_store(), stripe_service=service),
    )

    with TestClient(api.app, raise_server_exceptions=False) as client:
        yield {
            "client": client,
            "org": org,
            "key": key,
            "stripe": stripe,
            "service": service,
            "org_uuid": org_uuid_for(org.id),
        }
    _reset()


def _auth(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _checkout_metadata(dep: dict[str, Any], plan: str) -> dict[str, str]:
    """The metadata this product's own checkout writes for `plan`.

    Produced by calling `create_checkout_session`, not hand-written: the defect
    this whole file exists for was the producer and the consumer disagreeing
    about the keys, which a hand-written fixture would hide.
    """
    token = set_tenant(TenantConfig(org_id=dep["org"].id, plan="free", role="owner"))
    try:
        asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
            dep["service"].create_checkout_session(
                dep["org_uuid"], plan, BillingCycle.MONTHLY, email="a@b.test"
            )
        )
    finally:
        reset_tenant(token)
    return dict(dep["stripe"].checkout_calls[-1]["subscription_data"]["metadata"])


# --------------------------------------------------------------------------- #
# 1. The delivery reaches the billing store
# --------------------------------------------------------------------------- #
def test_a_paid_subscription_is_recorded_in_billing_subscriptions(deployment):
    """The whole chain, over HTTP: what checkout wrote, signed as Stripe signs
    it, into the row `GET /billing/subscription` reads back.

    Before the receiver forwarded the delivery, this reply's `billing_store` half
    did not exist and that endpoint answered `null` for a customer who had paid.
    """
    dep = deployment
    metadata = _checkout_metadata(dep, "growth")
    body = _subscription_body(metadata)

    r = dep["client"].post("/webhooks/stripe", content=body, headers=_signed(body))

    assert r.status_code == 200, r.text
    assert r.json()["billing_store"] == {
        "status": "processed",
        "event_type": "customer.subscription.created",
    }

    sub = dep["client"].get("/billing/subscription", headers=_auth(dep["key"])).json()
    assert sub is not None, "a paid subscription left no row in billing_subscriptions"
    assert sub["plan"]["name"] == "growth"
    assert sub["status"] == "active"
    assert sub["billing_cycle"] == "monthly"

    row = asyncio.run(dep["service"]._store.get_subscription(dep["org_uuid"]))
    assert row.plan_id == plan_uuid("growth")
    assert row.stripe_subscription_id == "sub_test_1"
    assert row.stripe_customer_id == "cus_test_1"


def test_the_store_receives_the_bytes_and_the_header_stripe_signed(deployment):
    """`BillingAPI.handle_webhook` re-verifies through the SDK, so it needs the
    body byte for byte and the header verbatim. A re-serialised body verifies
    against nothing."""
    dep = deployment
    body = _subscription_body(_checkout_metadata(dep, "growth"))
    headers = _signed(body)

    dep["client"].post("/webhooks/stripe", content=body, headers=headers)

    assert dep["stripe"].deliveries == [(body, headers["Stripe-Signature"])]


def test_an_event_the_tenant_plan_mapping_ignores_still_reaches_the_store(deployment):
    """`checkout.session.completed` is not a subscription event, so the plan
    mapping returns early. The forward happens first, which is what keeps the
    two halves from being coupled to each other's vocabulary."""
    dep = deployment
    body = json.dumps({
        "type": "checkout.session.completed",
        "data": {"object": {"id": "cs_1", "metadata": _checkout_metadata(dep, "growth")}},
    }).encode()

    r = dep["client"].post("/webhooks/stripe", content=body, headers=_signed(body))

    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ignored"
    assert r.json()["billing_store"]["status"] == "processed"


def test_a_delivery_the_store_rejects_is_not_reported_as_a_clean_success(deployment):
    """The receiver's own check passed, so the tenant-plan half landed. Saying
    "applied" and nothing else would hide that `billing_subscriptions` never
    moved — and the standing rule is that a half-completed delivery must not read
    as a success."""
    dep = deployment
    dep["service"]._webhook_secret = "whsec_a_different_secret"
    body = _subscription_body(_checkout_metadata(dep, "growth"))

    r = dep["client"].post("/webhooks/stripe", content=body, headers=_signed(body))

    assert r.status_code == 200, r.text
    assert r.json()["status"] == "applied"
    assert r.json()["billing_store"]["status"] == "failed"
    assert "Invalid signature" in r.json()["billing_store"]["detail"]

    sub = dep["client"].get("/billing/subscription", headers=_auth(dep["key"])).json()
    assert sub is None


def test_an_unsigned_delivery_reaches_neither_store(deployment):
    dep = deployment
    body = _subscription_body(_checkout_metadata(dep, "growth"))

    r = dep["client"].post(
        "/webhooks/stripe", content=body, headers={"Stripe-Signature": "t=1,v1=bad"}
    )

    assert r.status_code == 401
    assert dep["stripe"].deliveries == []


def test_the_same_delivery_twice_leaves_one_subscription(deployment):
    """Stripe retries any delivery it did not get a 2xx for, and sends
    `customer.subscription.created` alongside `checkout.session.completed` for
    the same purchase, so a repeat is ordinary traffic rather than an edge case.

    It is only ordinary now that the receiver forwards: while the handlers were
    unreachable no retry could reach them either. `_handle_subscription_created`
    cancels the org's existing subscription before writing the new one, so a
    second copy of the same event has to end where the first did — one active
    row on the tier that was bought, and a customer whose billing page still
    answers.
    """
    dep = deployment
    body = _subscription_body(_checkout_metadata(dep, "growth"))

    first = dep["client"].post("/webhooks/stripe", content=body, headers=_signed(body))
    second = dep["client"].post("/webhooks/stripe", content=body, headers=_signed(body))

    assert first.json()["billing_store"] == second.json()["billing_store"]
    page = dep["client"].get("/billing/subscription", headers=_auth(dep["key"]))
    assert page.status_code == 200, page.text
    assert page.json()["plan"]["name"] == "growth"
    assert page.json()["status"] == "active"


# --------------------------------------------------------------------------- #
# 2. The tier the customer bought is the tier they get
# --------------------------------------------------------------------------- #
def test_a_growth_payment_leaves_the_org_on_growth_not_team(deployment):
    """`orgs.plan` is the only record of the tier on a deployment that upgrades
    by webhook. It could hold free/team/enterprise, so a Growth payment was
    stored as `team` and the tier was gone — not misread later, destroyed on the
    way in."""
    dep = deployment
    body = _subscription_body(_checkout_metadata(dep, "growth"))

    r = dep["client"].post("/webhooks/stripe", content=body, headers=_signed(body))

    assert r.json()["plan"] == "growth"
    stored = asyncio.run(tenant_auth.get_store().get_org(dep["org"].id))
    assert stored.plan == "growth"


def test_the_customer_can_use_what_they_paid_for(deployment):
    """The point of the tier. `service_dependency_map` is a Growth feature; with
    the org stored as Team the gate refused it to someone paying $699 a month for
    exactly that."""
    dep = deployment
    body = _subscription_body(_checkout_metadata(dep, "growth"))
    dep["client"].post("/webhooks/stripe", content=body, headers=_signed(body))

    r = dep["client"].get(
        "/billing/features/service_dependency_map", headers=_auth(dep["key"])
    )

    assert r.status_code == 200, r.text
    assert r.json()["available"] is True


def test_the_quota_matches_the_tier_that_was_sold(deployment):
    """Growth sells unlimited AI investigations. Metered as `team` the customer
    was cut off at 1000 a month with nothing saying why."""
    dep = deployment
    body = _subscription_body(_checkout_metadata(dep, "growth"))
    dep["client"].post("/webhooks/stripe", content=body, headers=_signed(body))

    usage = dep["client"].get("/usage", headers=_auth(dep["key"])).json()

    assert usage["plan"] == "growth"
    assert usage["investigations_quota"] == -1


def test_the_entitlement_survives_with_no_billing_row_at_all(deployment):
    """The deployment the finding was about: Stripe is not configured, so
    `billing_subscriptions` is empty and the tenant record is the only evidence.
    `FeatureGateService._plan_from_tenant_context` reads `orgs.plan` through
    `catalog_plan_for`, and every tier above Team used to arrive here spelled
    "team"."""
    dep = deployment
    asyncio.run(tenant_auth.get_store().set_plan(dep["org"].id, "business"))

    r = dep["client"].get("/billing/features/sso_saml", headers=_auth(dep["key"]))

    assert r.status_code == 200, r.text
    assert r.json()["available"] is True, "a Business customer was refused SSO"


def test_a_portal_upgrade_moves_the_entitlement_and_the_meter(deployment):
    """`POST /billing/portal` sends customers to Stripe to change plans, and that
    change comes back only as `customer.subscription.updated` — with the item's
    Price rewritten and the metadata still naming the tier they moved off,
    because Stripe does not rewrite metadata a portal change did not set.

    Both halves of the delivery have to follow the Price, and for a while only
    one did. `billing_subscriptions` did, through `_apply_price_change`, so the
    features arrived; `orgs.plan` did not, because `plan_from_stripe_event` read
    the stale metadata — leaving a customer who was being charged for Growth
    entitled to Growth's features and metered on Team's 1000 investigations. The
    receiver now applies the same precedence on both halves, so the subscription
    row, the feature gate and the quota all name the tier being paid for.

    The Team purchase is asserted before the upgrade so that "growth" below is a
    tier that moved, not one that was there all along.
    """
    dep = deployment
    bought = _subscription_body(_checkout_metadata(dep, "team"), price_id="price_team_m")
    dep["client"].post("/webhooks/stripe", content=bought, headers=_signed(bought))
    assert asyncio.run(tenant_auth.get_store().get_org(dep["org"].id)).plan == "team"
    upgraded = _subscription_body(
        _checkout_metadata(dep, "team"),
        etype="customer.subscription.updated",
        price_id="price_growth_m",
    )

    r = dep["client"].post("/webhooks/stripe", content=upgraded, headers=_signed(upgraded))

    assert r.json()["billing_store"]["status"] == "processed"
    assert r.json()["plan"] == "growth"
    sub = dep["client"].get("/billing/subscription", headers=_auth(dep["key"])).json()
    assert sub["plan"]["name"] == "growth"
    feature = dep["client"].get(
        "/billing/features/service_dependency_map", headers=_auth(dep["key"])
    )
    assert feature.json()["available"] is True

    assert asyncio.run(tenant_auth.get_store().get_org(dep["org"].id)).plan == "growth"
    usage = dep["client"].get("/usage", headers=_auth(dep["key"])).json()
    assert usage["plan"] == "growth"
    assert usage["investigations_quota"] == -1


# --------------------------------------------------------------------------- #
# 3. Single-tenant deployments
# --------------------------------------------------------------------------- #
async def _single_tenant_subscription(service) -> Any:
    metadata = subscription_metadata(SINGLE_TENANT_ORG, "growth")
    assert "org_id" not in metadata, "there is no orgs row to name on this deployment"
    body = _subscription_body(metadata)
    await service.handle_webhook(body, _signed(body)["Stripe-Signature"])
    return await service._store.get_subscription(SINGLE_TENANT_ORG)


def test_a_single_tenant_payment_is_still_attributed(deployment):
    """With no tenant on the request, `subscription_metadata` leaves `org_id`
    out — correctly: there is no `orgs` row, so any value would make `set_plan`
    match nothing and turn a harmless "ignored" into an endless Stripe retry
    against a 404. Attribution is not lost by that, because `organization_id` is
    always written and it is what the billing store and the feature gate route
    on. This asserts the payment lands somewhere a reader can find it."""
    dep = deployment
    sub = asyncio.run(_single_tenant_subscription(dep["service"]))

    assert sub is not None, "a single-tenant payment was recorded nowhere"
    assert sub.org_id == SINGLE_TENANT_ORG
    assert sub.plan_id == plan_uuid("growth")


def test_a_single_tenant_payment_entitles_the_deployment(deployment):
    """Attribution is only worth anything if something can act on it.

    The row existing proves storage; this proves the entitlement, which is the
    thing the customer paid for. With no tenant on the request
    `_plan_from_tenant_context` returns None by design — there is no `orgs` row to
    read — so `billing_subscriptions` is the sole evidence, and the whole question
    raised against the omitted `org_id` is whether that evidence is enough. It is:
    the gate resolves the tier through the subscription's `plan_id`.
    """
    dep = deployment
    asyncio.run(_single_tenant_subscription(dep["service"]))
    gate = FeatureGateService(dep["service"]._store)

    result = asyncio.run(
        gate.can_access_feature(SINGLE_TENANT_ORG, "service_dependency_map")
    )

    assert result.allowed is True, result.reason


# --------------------------------------------------------------------------- #
# 4. Both event shapes
# --------------------------------------------------------------------------- #
class _Attr:
    """A Stripe SDK-shaped object: attribute access, absent fields are None."""

    def __init__(self, **kw: Any) -> None:
        self.__dict__.update(kw)

    def __getattr__(self, name: str) -> Any:
        return None


def _as_attr(value: Any) -> Any:
    if isinstance(value, dict):
        return _Attr(**{k: _as_attr(v) for k, v in value.items()})
    if isinstance(value, list):
        return [_as_attr(v) for v in value]
    return value


def test_the_handlers_read_the_sdk_object_and_the_plain_dict_alike(deployment):
    """The claim that the handlers were shape-tolerant used to hold for metadata
    only: `event.type`, `data.items.data[0].price`, `data.status`, `data.id` and
    the invoice fields were all attribute access, so a decoded JSON body raised
    `AttributeError` at the first of them. Both shapes now produce the same row.
    """
    dep = deployment
    body = _subscription_body(_checkout_metadata(dep, "growth"))
    event = json.loads(body)
    event["data"]["object"]["metadata"] = dict(event["data"]["object"]["metadata"])

    async def _store_both():
        store = dep["service"]._store
        await dep["service"]._handle_subscription_created(event["data"]["object"])
        from_dict = await store.get_subscription(dep["org_uuid"])
        attr = _as_attr(event)
        # `metadata` stays a plain dict: that is the shape the SDK's own
        # StripeObject exposes it as.
        attr.data.object.metadata = event["data"]["object"]["metadata"]
        await dep["service"]._handle_subscription_created(attr.data.object)
        return from_dict, await store.get_subscription(dep["org_uuid"])

    from_dict, from_attr = asyncio.run(_store_both())

    assert from_dict is not None and from_attr is not None
    for field in ("org_id", "plan_id", "status", "billing_cycle",
                  "stripe_subscription_id", "stripe_customer_id"):
        assert getattr(from_dict, field) == getattr(from_attr, field), field
    assert from_dict.billing_cycle is BillingCycle.MONTHLY


# --------------------------------------------------------------------------- #
# 5. Each repair, put back at runtime, reproduces the failure it was made for
# --------------------------------------------------------------------------- #
# The tests above would also pass if something else in the chain happened to
# carry them, so each one below neutralises exactly one repair — by rebinding a
# name inside the running process, never by touching the tree — and asserts the
# audited symptom comes back. That is what makes them regression tests rather
# than descriptions of the current behaviour.

# `orgs.plan`'s vocabulary as it stood: three brackets for a catalog of six
# tiers, and the collapse that squeezed the paid ones into `team` on the way in.
_THREE_VALUE_VOCABULARY = {
    name: tenancy_models.PLAN_QUOTAS[name] for name in ("free", "team", "enterprise")
}


def _narrow_the_plan_vocabulary(monkeypatch) -> None:
    """Put the pre-fix plan vocabulary back for the duration of one test.

    Three bindings, because three modules read the table: `normalize_plan` and
    `quota` resolve it in `app.tenancy.models`, and `plan_from_stripe_event`
    imported it by value into `app.billing.webhooks`. `CATALOG_TO_QUOTA_PLAN` is
    the map that did the squeezing.
    """
    monkeypatch.setattr(tenancy_models, "PLAN_QUOTAS", _THREE_VALUE_VOCABULARY)
    monkeypatch.setattr(billing_webhooks, "PLAN_QUOTAS", _THREE_VALUE_VOCABULARY)
    monkeypatch.setattr(
        billing_plans,
        "CATALOG_TO_QUOTA_PLAN",
        {**billing_plans.CATALOG_TO_QUOTA_PLAN, "growth": "team", "business": "team"},
    )


def test_the_narrow_vocabulary_is_what_lost_the_tier(deployment, monkeypatch):
    """Same signed Growth delivery, same route — and the org lands on Team.

    The plan is not misread afterwards, it never reaches the database: `set_plan`
    stores `normalize_plan(quota_plan_for("growth"))`, and with the narrow table
    that composition cannot produce anything but `team`.
    """
    dep = deployment
    body = _subscription_body(_checkout_metadata(dep, "growth"))
    _narrow_the_plan_vocabulary(monkeypatch)

    r = dep["client"].post("/webhooks/stripe", content=body, headers=_signed(body))

    assert r.json()["plan"] == "team"
    stored = asyncio.run(tenant_auth.get_store().get_org(dep["org"].id))
    assert stored.plan == "team"


def test_the_narrow_vocabulary_is_what_withheld_the_features(deployment, monkeypatch):
    """The mirror of `test_the_entitlement_survives_with_no_billing_row_at_all`.

    Same Business customer, same request, one binding different — and SSO, which
    is the headline of the tier they bought, is refused."""
    dep = deployment
    _narrow_the_plan_vocabulary(monkeypatch)
    asyncio.run(
        tenant_auth.get_store().set_plan(
            dep["org"].id, billing_plans.quota_plan_for("business")
        )
    )
    assert asyncio.run(tenant_auth.get_store().get_org(dep["org"].id)).plan == "team"

    r = dep["client"].get("/billing/features/sso_saml", headers=_auth(dep["key"]))

    assert r.status_code == 200, r.text
    assert r.json()["available"] is False


def test_the_forward_is_what_writes_billing_subscriptions(deployment, monkeypatch):
    """With the receiver's forward neutralised the reply still says "applied" and
    the subscription exists nowhere but Stripe — the audited failure exactly: the
    tenant-plan half lands, so nothing looks wrong, and every subscription-shaped
    question the customer can ask answers null."""
    dep = deployment

    async def _no_forward(raw: bytes, signature: str) -> dict[str, str]:
        return {"status": "not_called"}

    monkeypatch.setattr(api, "_record_stripe_delivery", _no_forward)
    body = _subscription_body(_checkout_metadata(dep, "growth"))

    r = dep["client"].post("/webhooks/stripe", content=body, headers=_signed(body))

    assert r.json()["status"] == "applied"
    assert dep["stripe"].deliveries == [], "the store was reached after all"
    assert dep["client"].get("/billing/subscription", headers=_auth(dep["key"])).json() is None


def test_reading_the_price_is_what_moved_the_meter_with_the_entitlement(
    deployment, monkeypatch
):
    """`_tier_from_price` neutralised, which puts the metadata-only precedence
    `plan_from_stripe_event` used to have back.

    The same portal upgrade then splits in two: `billing_subscriptions` still
    follows the Price — `_apply_price_change` never consulted the metadata — so
    the customer keeps Growth's features, while `orgs.plan` follows the metadata
    the portal left naming Team and the quota check cuts them off at 1000
    investigations. Nothing in the reply says so; it answers 200 and names the
    tier it applied, which is the tier the customer left.
    """
    dep = deployment
    bought = _subscription_body(_checkout_metadata(dep, "team"), price_id="price_team_m")
    dep["client"].post("/webhooks/stripe", content=bought, headers=_signed(bought))
    monkeypatch.setattr(billing_webhooks, "_tier_from_price", lambda obj: "")
    upgraded = _subscription_body(
        _checkout_metadata(dep, "team"),
        etype="customer.subscription.updated",
        price_id="price_growth_m",
    )

    r = dep["client"].post("/webhooks/stripe", content=upgraded, headers=_signed(upgraded))

    assert r.json()["plan"] == "team"
    sub = dep["client"].get("/billing/subscription", headers=_auth(dep["key"])).json()
    assert sub["plan"]["name"] == "growth", "the billing store follows the Price"
    usage = dep["client"].get("/usage", headers=_auth(dep["key"])).json()
    assert usage["plan"] == "team"
    assert usage["investigations_quota"] == 1000


def test_shape_tolerant_reads_are_what_let_a_json_body_be_handled(deployment, monkeypatch):
    """`_field` put back to bare `getattr`, which is what the handlers used to do.

    A delivery decoded from the raw JSON body is a plain dict, so every one of
    those lookups misses: the event type reads as empty, no handler matches, and
    a perfectly good subscription event is answered "ignored" with nothing
    written. The reply says so rather than claiming success, but the money still
    moved and the row still does not exist.
    """
    dep = deployment
    body = _subscription_body(_checkout_metadata(dep, "growth"))
    monkeypatch.setattr(
        stripe_integration, "_field", lambda obj, name: getattr(obj, name, None)
    )

    r = dep["client"].post("/webhooks/stripe", content=body, headers=_signed(body))

    assert r.json()["billing_store"]["status"] == "ignored"
    assert dep["client"].get("/billing/subscription", headers=_auth(dep["key"])).json() is None
