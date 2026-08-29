"""Stripe webhook handling and signature verification.

Inbound webhooks: Stripe subscription events → tenant plan (store.set_plan).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any, Protocol

from app.billing.plans import quota_plan_for
from app.config import get_settings
from app.tenancy.models import PLAN_QUOTAS

log = logging.getLogger("omnipotent.billing.webhooks")


class StripeClient(Protocol):
    def create_meter_event(
        self, customer_id: str, event_name: str, value: int, idempotency_key: str
    ) -> None: ...


class FakeStripeClient:
    """In-memory client for tests / offline — records the meter events it would send."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    def create_meter_event(
        self, customer_id: str, event_name: str, value: int, idempotency_key: str
    ) -> None:
        self.events.append({
            "customer_id": customer_id, "event_name": event_name,
            "value": value, "idempotency_key": idempotency_key,
        })


class _LiveStripeClient:
    """Lazily-imported real client (needs `stripe` + STRIPE_API_KEY)."""

    def __init__(self, api_key: str):
        import stripe

        stripe.api_key = api_key
        self._stripe = stripe

    def create_meter_event(
        self, customer_id: str, event_name: str, value: int, idempotency_key: str
    ) -> None:
        self._stripe.billing.MeterEvent.create(
            event_name=event_name,
            payload={"stripe_customer_id": customer_id, "value": str(value)},
            identifier=idempotency_key,
        )


def get_client() -> StripeClient | None:
    """The live client when STRIPE_API_KEY is set, else None (local-ledger-only)."""
    key = get_settings().stripe_api_key.strip()
    return _LiveStripeClient(key) if key else None


async def sync_usage_to_stripe(store: Any, client: StripeClient, limit: int = 500) -> int:
    """Push unsynced billable usage to Stripe meter events; mark them synced."""
    rows = await store.unsynced_usage(limit=limit)
    event_name = get_settings().stripe_meter_event
    synced: list[str] = []
    for r in rows:
        org = await store.get_org(r["org_id"])
        customer = org.stripe_customer_id if org else ""
        if not customer:
            continue
        try:
            client.create_meter_event(
                customer, event_name, int(r["amount"]), r["event_key"] or r["id"]
            )
            synced.append(r["id"])
        except Exception:
            log.exception("stripe meter event failed (org=%s)", r["org_id"])
    await store.mark_synced(synced)
    return len(synced)


def verify_stripe_signature(
    payload: bytes,
    sig_header: str,
    secret: str,
    now: int | None = None,
    tolerance: int = 300,
) -> bool:
    """Verify a Stripe-Signature header (scheme: `t=<ts>,v1=<hmac>`) over
    `"{t}.{payload}"` with HMAC-SHA256. Pure; `now` injectable for tests."""
    if not secret or not sig_header:
        return False
    parts = dict(p.split("=", 1) for p in sig_header.split(",") if "=" in p)
    ts, v1 = parts.get("t", ""), parts.get("v1", "")
    if not ts or not v1:
        return False
    if tolerance and now is not None:
        try:
            if abs(now - int(ts)) > tolerance:
                return False
        except ValueError:
            return False
    signed = f"{ts}.".encode() + payload
    expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, v1)


class UnknownPlan(ValueError):
    """A subscription event named a plan this deployment does not sell.

    Distinct from "not a subscription event": the caller must reject the
    delivery so Stripe retries and the mismatch is visible, rather than treating
    it as nothing to do. Silently ignoring it left the customer on whatever they
    had before, with Stripe believing the change was accepted.
    """

    def __init__(self, plan: str):
        self.plan = plan
        super().__init__(f"unknown plan in Stripe metadata: {plan!r}")


def _metadata_value(meta: dict, *keys: str) -> str:
    """The first non-empty string among `keys` in Stripe metadata."""
    for key in keys:
        value = meta.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _report_unroutable(etype: str, obj: dict, meta: dict) -> None:
    """Say what happened to a subscription event that names no tenancy org.

    Two very different deployments arrive here and they used to get the same
    ERROR. On a single-tenant deployment there is no `orgs` table row to move at
    all — `subscription_metadata` leaves `org_id` off deliberately, because any
    value it invented would make `set_plan` match zero rows and turn the
    receiver's harmless "ignored" into a permanent 404 and an endless Stripe
    retry — so *every* delivery landed here and logged an error for something
    that is working as designed. The subscription is still recorded: the billing
    store leg of the same delivery files it under `organization_id`, and that is
    the key `FeatureGateService` resolves the deployment's entitlement from.

    On a multi-tenant deployment the same event really is unroutable, and the
    keys that did arrive say which fix is needed: only `organization_id` means a
    subscription written before the shared metadata contract, none at all means
    one created by hand in the Stripe dashboard.
    """
    keys = ", ".join(sorted(str(k) for k in meta)) or "none"
    if not get_settings().copilot_multi_tenant:
        log.info(
            "Stripe %s for subscription %s carries no tenancy org id, which is "
            "expected on a single-tenant deployment: there is no orgs row to "
            "move, and billing_subscriptions is the record of what was bought. "
            "Metadata keys were [%s]",
            etype,
            obj.get("id") or "?",
            keys,
        )
        return
    log.error(
        "Stripe %s for subscription %s carries no tenancy org id, so no plan "
        "was applied; metadata keys were [%s] and `metadata.org_id` is the "
        "one this receiver can route on",
        etype,
        obj.get("id") or "?",
        keys,
    )


def _tier_from_price(obj: dict) -> str:
    """The catalog tier the subscription's current Stripe Price sells, if known.

    Empty when the deployment has no STRIPE_PRICE_IDS entry for the Price, which
    is the normal state for a deployment that never configured the mapping — the
    caller falls back to the metadata there rather than refusing the delivery.
    """
    from app.billing.stripe_integration import plan_for_price_id, subscription_price_id

    price_id = subscription_price_id(obj)
    if not price_id:
        return ""
    priced = plan_for_price_id(price_id)
    if priced is None:
        return ""
    name = priced[0]
    if quota_plan_for(name) is None:
        log.warning(
            "STRIPE_PRICE_IDS maps price %s to %r, which is not a tier this "
            "product sells; falling back to the subscription's metadata",
            price_id,
            name,
        )
        return ""
    return name


def plan_from_stripe_event(event: dict) -> tuple[str, str] | None:
    """Map a Stripe subscription event to (org_id, enforced plan).

    The returned plan is an `app.tenancy.models.PLAN_QUOTAS` value, because that
    is what `set_plan` stores and what the quota check reads. Stripe metadata
    carries a *catalog* tier name ("growth"), so it is translated here rather
    than at each call site.

    Both metadata spellings are accepted. This read `org_id`/`plan` only, while
    the checkout session this product creates wrote `organization_id`/`plan_name`
    (app/billing/stripe_integration.py): every real upgrade therefore arrived
    here carrying keys this function did not look at, resolved to None, and was
    answered `{"status": "ignored"}` with a 200 — so Stripe never retried, no
    alarm fired, and the customer who had just been charged stayed on the free
    plan. The checkout session now writes both pairs and this reads both, so the
    producer and the receiver cannot drift apart again.

    `organization_id` is deliberately *not* accepted as the org id: it is the
    billing org UUID, `uuid5(ORG_NAMESPACE, tenancy_org_id)`, and that derivation
    is one-way — handing it to `set_plan` would match zero rows.

    On `created`/`updated` the subscription's own Stripe Price outranks the
    metadata, the same precedence `_apply_price_change` uses on the billing-store
    half. A tier change bought in Stripe's own customer portal — which is where
    `POST /billing/portal` sends customers — reaches this product only as
    `customer.subscription.updated`, with the item's Price rewritten and the
    metadata still naming the tier they moved *off*. Reading the metadata alone
    moved `billing_subscriptions` (which follows the Price) and left `orgs.plan`
    behind, so an upgraded customer got the features and was still metered on the
    quota of the tier they had left. The metadata is still the answer when the
    Price maps to nothing — a deployment that never configured STRIPE_PRICE_IDS
    has no other source, and guessing from an unmapped Price would be a billing
    decision made on no evidence.

    Returns None when the event is not a subscription change at all, and raises
    `UnknownPlan` when it is one but names a plan that does not exist. Those two
    used to be the same answer, which is how `plan: "pro_monthly"` — a value in
    the old hardcoded allowlist that no part of the product sells — could be
    accepted here and then normalised to `free` on the way into the database:
    the webhook replied `{"status": "applied", "plan": "pro_monthly"}` while the
    customer was downgraded.
    """
    etype = event.get("type", "")
    if etype not in (
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.deleted",
    ):
        return None
    obj = (event.get("data") or {}).get("object") or {}
    meta = obj.get("metadata") or {}
    org_id = _metadata_value(meta, "org_id")
    if not org_id:
        # A subscription event nothing can be applied to is not "nothing to do",
        # but on a single-tenant deployment it is also not a fault; the reporting
        # tells the two apart.
        _report_unroutable(etype, obj, meta)
        return None
    if etype == "customer.subscription.deleted":
        return org_id, "free"
    stated = _metadata_value(meta, "plan", "plan_name").lower()
    priced = _tier_from_price(obj)
    if priced and stated and priced != stated:
        log.info(
            "Stripe %s for subscription %s is on the price for %r while its "
            "metadata still names %r; applying %r, which is what the customer "
            "is being charged for",
            etype,
            obj.get("id") or "?",
            priced,
            stated,
            priced,
        )
    raw = priced or stated
    # "free" is not a catalog tier — it is what a cancellation resolves to —
    # but Stripe metadata may name it directly, so accept it.
    if raw in PLAN_QUOTAS:
        return org_id, raw
    plan = quota_plan_for(raw)
    if plan is None:
        raise UnknownPlan(raw)
    return org_id, plan
