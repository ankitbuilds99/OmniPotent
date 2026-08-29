"""There is one Stripe webhook receiver, and Stripe can reach it.

Stripe posts to a single configured URL with a `Stripe-Signature` header and no
`Authorization` header. The app carried two receivers:

  * `POST /webhooks/stripe` (app/api/main.py) — exempt from `require_auth`,
    verifies the signature, applies the plan change. This is the one.
  * `POST /billing/webhooks/stripe` (app/api/billing_router.py) — behind
    `require_auth`, so every real Stripe delivery got 401 before reaching it, and
    behind an adapter the installed `stripe` SDK does not implement. A second
    endpoint that cannot work is worse than no endpoint: it looks configured.

These tests pin the surviving receiver's reachability and that the duplicate does
not come back.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

import app.api.main as api
import app.config as cfg
from app import runtime, tenant_context
from app.database import engine as db_engine
from app.tenancy import auth as tenant_auth
from app.tenancy import scoping

TOKEN = "prodtoken"
WHSEC = "whsec_test"


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


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Production posture: the shared bearer is set, so require_auth is live.

    STRIPE_API_KEY is explicitly removed rather than left to the environment, so
    the billing-store half of every reply below has one known answer
    (`not_configured`) instead of one that depends on whose machine this runs on.
    That is the posture these tests are about: a deployment that upgrades by
    webhook alone, where `orgs.plan` is the only record of the tier.
    """
    monkeypatch.setenv("COPILOT_MULTI_TENANT", "true")
    monkeypatch.setenv("COPILOT_TENANT_DB", str(tmp_path / "tenants.sqlite"))
    monkeypatch.setenv("COPILOT_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/app.db")
    monkeypatch.setenv("COPILOT_API_TOKEN", TOKEN)
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", WHSEC)
    monkeypatch.delenv("STRIPE_API_KEY", raising=False)
    _reset()
    store = tenant_auth.get_store()
    org = asyncio.run(_provision(store))
    with TestClient(api.app, raise_server_exceptions=False) as c:
        yield c, org
    _reset()


async def _provision(store):
    await store.setup()
    return await store.create_org("Acme", plan="free")


def _signed(body: bytes, secret: str = WHSEC) -> dict[str, str]:
    ts = str(int(time.time()))
    mac = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return {"Stripe-Signature": f"t={ts},v1={mac}", "Content-Type": "application/json"}


def _halves(response) -> tuple[dict, dict]:
    """The tenant-plan half of the receiver's reply and the billing-store half.

    One verified delivery has to land in two records — `orgs.plan`, the quota
    bracket this app enforces, and `billing_subscriptions`, the subscription
    itself — so the reply names both outcomes. `billing_store` is not optional
    decoration: without it a delivery that moved one store and not the other read
    as a clean `{"status": "applied"}`, which is exactly how a paid subscription
    could exist in Stripe and nowhere here. Its absence fails here, and the
    tenant-plan half is still compared exactly, so a stray extra key is still a
    failure.
    """
    payload = response.json()
    store_leg = payload.pop("billing_store", None)
    assert store_leg is not None, "the reply must say what the billing store did"
    return payload, store_leg


def _stripe_webhook_paths() -> set[str]:
    return {
        route.path
        for route in api.app.routes
        if isinstance(route, APIRoute) and "stripe" in route.path and "webhook" in route.path
    }


def test_exactly_one_stripe_webhook_route_is_registered():
    assert _stripe_webhook_paths() == {"/webhooks/stripe"}


def test_the_receiver_lives_under_the_auth_exempt_prefix():
    """The exemption in require_auth is by prefix, so the path has to match it."""
    assert all(p.startswith("/webhooks/") for p in _stripe_webhook_paths())


def test_stripe_reaches_the_handler_without_a_bearer(client):
    """No Authorization header — what Stripe sends. 401 must come from the
    signature check, not from the bearer gate."""
    c, _ = client
    r = c.post("/webhooks/stripe", content=b"{}", headers={"Stripe-Signature": "t=1,v1=bad"})
    assert r.status_code == 401
    assert r.json()["detail"] == "Invalid Stripe signature."


def test_a_correctly_signed_event_applies_the_plan(client):
    c, org = client
    body = json.dumps({
        "type": "customer.subscription.updated",
        "data": {"object": {"metadata": {"org_id": org.id, "plan": "team"}}},
    }).encode()

    r = c.post("/webhooks/stripe", content=body, headers=_signed(body))
    assert r.status_code == 200, r.text
    plan_leg, store_leg = _halves(r)
    assert plan_leg == {"status": "applied", "org_id": org.id, "plan": "team"}
    # No Stripe API key on this deployment, so there is no billing-store leg at
    # all. That is neither a success nor a failure, and saying so is what keeps
    # an empty `billing_subscriptions` from looking like a store that was written.
    assert store_leg["status"] == "not_configured"
    assert "Stripe API key" in store_leg["detail"]

    stored = asyncio.run(tenant_auth.get_store().get_org(org.id))
    assert stored is not None and stored.plan == "team"


def test_a_signature_from_the_wrong_secret_changes_nothing(client):
    c, org = client
    body = json.dumps({
        "type": "customer.subscription.updated",
        "data": {"object": {"metadata": {"org_id": org.id, "plan": "enterprise"}}},
    }).encode()

    r = c.post("/webhooks/stripe", content=body, headers=_signed(body, secret="not-the-secret"))
    assert r.status_code == 401
    stored = asyncio.run(tenant_auth.get_store().get_org(org.id))
    assert stored is not None and stored.plan == "free"


@pytest.mark.parametrize("header", ["garbage", "t=notanumber,v1=x", "", "t=,v1="])
def test_malformed_signature_headers_are_refused_not_crashed(client, header):
    c, _ = client
    r = c.post("/webhooks/stripe", content=b"{}", headers={"Stripe-Signature": header})
    assert r.status_code == 401, r.text


def test_a_cancellation_drops_the_org_to_free(client):
    c, org = client
    asyncio.run(tenant_auth.get_store().set_plan(org.id, "team"))
    body = json.dumps({
        "type": "customer.subscription.deleted",
        "data": {"object": {"metadata": {"org_id": org.id}}},
    }).encode()

    r = c.post("/webhooks/stripe", content=body, headers=_signed(body))
    plan_leg, store_leg = _halves(r)
    assert plan_leg == {"status": "applied", "org_id": org.id, "plan": "free"}
    assert store_leg["status"] == "not_configured"

    stored = asyncio.run(tenant_auth.get_store().get_org(org.id))
    assert stored.plan == "free"


def test_a_catalog_tier_is_kept_as_the_plan_that_is_enforced(client):
    """Stripe metadata carries a catalog tier; `orgs.plan` holds a quota plan.

    "growth" is a real, sold tier, and `PLAN_QUOTAS` now has a bracket for it —
    so the tier that was bought is the tier that is stored. Two answers were
    wrong before that bracket existed and both cost the customer what they paid
    for: untranslated, "growth" hit `normalize_plan` and became `free`; mapped
    onto "team" it silently withheld every Growth entitlement (unlimited
    investigations, the dependency map, the MTTA/MTTR dashboard) from someone
    being billed for exactly those. `orgs.plan` is the only record of the tier on
    a deployment that upgrades by webhook, so a lossy write here is not a display
    detail — it destroys the entitlement.
    """
    c, org = client
    body = json.dumps({
        "type": "customer.subscription.updated",
        "data": {"object": {"metadata": {"org_id": org.id, "plan": "growth"}}},
    }).encode()

    r = c.post("/webhooks/stripe", content=body, headers=_signed(body))
    assert r.status_code == 200, r.text
    assert r.json()["plan"] == "growth"

    stored = asyncio.run(tenant_auth.get_store().get_org(org.id))
    assert stored.plan == "growth"


def test_a_tier_with_no_bracket_of_its_own_is_translated_upwards(client):
    """"scale" has no PLAN_QUOTAS bracket, so it resolves to Enterprise's.

    Lossy, but only upwards: the customer gets at least what they bought. The
    translation table is the one place that decides this, and this pins that a
    tier without a bracket is never rounded *down*.
    """
    c, org = client
    body = json.dumps({
        "type": "customer.subscription.updated",
        "data": {"object": {"metadata": {"org_id": org.id, "plan": "scale"}}},
    }).encode()

    r = c.post("/webhooks/stripe", content=body, headers=_signed(body))
    assert r.status_code == 200, r.text
    assert r.json()["plan"] == "enterprise"

    stored = asyncio.run(tenant_auth.get_store().get_org(org.id))
    assert stored.plan == "enterprise"


@pytest.mark.parametrize("plan", ["pro_monthly", "starter_yearly", "platinum", ""])
def test_a_plan_the_product_does_not_sell_is_rejected(client, plan):
    """This answered 200 and left the customer on their old plan.

    `pro_monthly` was in the receiver's own hardcoded allowlist, so the reply said
    `{"status": "applied", "plan": "pro_monthly"}` — and `set_plan` normalised the
    unrecognised value to `free`. 400 makes Stripe retry and show the failure.
    """
    c, org = client
    body = json.dumps({
        "type": "customer.subscription.updated",
        "data": {"object": {"metadata": {"org_id": org.id, "plan": plan}}},
    }).encode()

    r = c.post("/webhooks/stripe", content=body, headers=_signed(body))
    assert r.status_code == 400, r.text
    assert plan in r.json()["detail"] or "Unknown plan" in r.json()["detail"]

    stored = asyncio.run(tenant_auth.get_store().get_org(org.id))
    assert stored.plan == "free", "a rejected event must not have changed anything"


def test_an_event_for_an_org_that_does_not_exist_is_not_applied(client):
    """The UPDATE matched zero rows and the receiver still said "applied"."""
    c, _ = client
    body = json.dumps({
        "type": "customer.subscription.updated",
        "data": {"object": {"metadata": {"org_id": "org_does_not_exist", "plan": "team"}}},
    }).encode()

    r = c.post("/webhooks/stripe", content=body, headers=_signed(body))
    assert r.status_code == 404, r.text
    assert "org_does_not_exist" in r.json()["detail"]


def test_a_non_subscription_event_is_still_ignored_not_rejected(client):
    """Stripe sends plenty of event types we have nothing to do with; those must
    stay 200-ignored, or every one of them becomes a retry loop."""
    c, _ = client
    body = json.dumps({
        "type": "payment_intent.created",
        "data": {"object": {"metadata": {"org_id": "whatever"}}},
    }).encode()

    r = c.post("/webhooks/stripe", content=body, headers=_signed(body))
    assert r.status_code == 200
    plan_leg, store_leg = _halves(r)
    assert plan_leg == {"status": "ignored", "type": "payment_intent.created"}
    assert store_leg["status"] == "not_configured"


def test_the_duplicate_billing_receiver_is_gone(client):
    """404/405 — whatever the SPA static mount says about an unrouted path — but
    never a billing response (200/401/503), which would mean it is still wired."""
    c, _ = client
    r = c.post("/billing/webhooks/stripe", content=b"{}", headers={"Stripe-Signature": "t=1,v1=x"})
    assert r.status_code in (404, 405), r.text
