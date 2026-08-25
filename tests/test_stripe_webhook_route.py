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
    """Production posture: the shared bearer is set, so require_auth is live."""
    monkeypatch.setenv("COPILOT_MULTI_TENANT", "true")
    monkeypatch.setenv("COPILOT_TENANT_DB", str(tmp_path / "tenants.sqlite"))
    monkeypatch.setenv("COPILOT_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/app.db")
    monkeypatch.setenv("COPILOT_API_TOKEN", TOKEN)
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", WHSEC)
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
    assert r.json() == {"status": "applied", "org_id": org.id, "plan": "team"}

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


def test_the_duplicate_billing_receiver_is_gone(client):
    """404/405 — whatever the SPA static mount says about an unrouted path — but
    never a billing response (200/401/503), which would mean it is still wired."""
    c, _ = client
    r = c.post("/billing/webhooks/stripe", content=b"{}", headers={"Stripe-Signature": "t=1,v1=x"})
    assert r.status_code in (404, 405), r.text
