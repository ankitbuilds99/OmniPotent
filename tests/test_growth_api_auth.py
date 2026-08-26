"""Who can reach the waitlist and the CRM, over HTTP.

Two problems, opposite directions:

* `POST /waitlist` and `GET /waitlist/position` were behind the global
  `require_auth`, so on any deployment with auth configured the only people who
  could join the waitlist were people who already had an account. They are open
  now — but deliberately not in `_OPEN_PATHS`, which would also skip the
  per-IP rate limiter and body-size cap.
* `GET /waitlist/stats` and every `/customers/*` route were behind
  `require_auth` too, which in multi-tenant mode is satisfied by *any* tenant
  API key. The deployment's own lead funnel, customer contact details, health
  scores and MRR were readable by the cheapest self-serve signup. They take
  COPILOT_OPERATOR_TOKEN now, like `/admin/plan`.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

import app.api.main as api
import app.config as cfg
from app import runtime, tenant_context
from app.database import engine as db_engine
from app.tenancy import auth as tenant_auth


async def _provision(store):
    await store.setup()
    org = await store.create_org("Acme", plan="team")
    owner_key, _ = await store.issue_api_key(org.id, "k-owner", role="owner")
    return {"owner": owner_key, "org": org.id}


@pytest.fixture
def mt(tmp_path, monkeypatch):
    monkeypatch.setenv("COPILOT_MULTI_TENANT", "true")
    monkeypatch.setenv("COPILOT_TENANT_DB", str(tmp_path / "tenants.sqlite"))
    monkeypatch.setenv("COPILOT_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/app.db")
    monkeypatch.setenv("COPILOT_OPERATOR_TOKEN", "op-token")
    cfg.get_settings.cache_clear()
    tenant_auth.reset_store_cache()
    tenant_context.current_tenant.set(None)
    api._RL.clear()
    runtime.reset()
    db_engine._engine = None
    db_engine._session_factory = None
    data = asyncio.run(_provision(tenant_auth.get_store()))
    with TestClient(api.app) as client:
        data["client"] = client
        yield data
    cfg.get_settings.cache_clear()
    tenant_auth.reset_store_cache()
    tenant_context.current_tenant.set(None)
    api._RL.clear()
    runtime.reset()
    db_engine._engine = None
    db_engine._session_factory = None


def _auth(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"}


# --------------------------------------------------------------------------- #
# Waitlist: open to join, operator-only to read in aggregate
# --------------------------------------------------------------------------- #
def test_anonymous_visitor_can_join_the_waitlist(mt):
    r = mt["client"].post(
        "/waitlist", json={"email": "visitor@example.com", "company": "Visitor Co"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["position"] == 1
    assert body["referral_code"].startswith("REF")


def test_anonymous_visitor_can_check_their_position(mt):
    c = mt["client"]
    c.post("/waitlist", json={"email": "visitor@example.com"})

    r = c.get("/waitlist/position", params={"email": "visitor@example.com"})
    assert r.status_code == 200
    assert r.json()["position"] == 1

    assert c.get("/waitlist/position", params={"email": "nobody@example.com"}).status_code == 404


def test_waitlist_signup_persists_across_requests(mt):
    """The dict this replaces reset with the process, and each worker had its own."""
    c = mt["client"]
    for i in range(3):
        assert c.post("/waitlist", json={"email": f"p{i}@example.com"}).status_code == 200

    r = c.post("/waitlist", json={"email": "fourth@example.com"})
    assert r.json()["position"] == 4


def test_waitlist_stats_rejects_a_tenant_key(mt):
    c = mt["client"]
    assert c.get("/waitlist/stats").status_code == 401
    assert c.get("/waitlist/stats", headers=_auth(mt["owner"])).status_code == 401


def test_waitlist_stats_accepts_the_operator_token(mt):
    c = mt["client"]
    c.post("/waitlist", json={"email": "counted@example.com"})

    r = c.get("/waitlist/stats", headers=_auth("op-token"))
    assert r.status_code == 200
    assert r.json()["total_signups"] == 1


def test_waitlist_stats_is_closed_without_an_operator_token(mt, monkeypatch):
    monkeypatch.delenv("COPILOT_OPERATOR_TOKEN", raising=False)
    cfg.get_settings.cache_clear()
    assert mt["client"].get("/waitlist/stats", headers=_auth("op-token")).status_code == 403


# --------------------------------------------------------------------------- #
# CRM: operator-only, all of it
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "method,path",
    [
        ("post", "/customers"),
        ("get", "/customers/stats"),
        ("get", "/customers/dashboard"),
        ("get", "/customers/trials/active"),
        ("post", "/customers/usage"),
        ("post", "/customers/convert"),
    ],
)
def test_crm_routes_reject_a_tenant_key(mt, method, path):
    kwargs = {"json": {}} if method == "post" else {}
    r = getattr(mt["client"], method)(path, headers=_auth(mt["owner"]), **kwargs)
    assert r.status_code == 401, f"{path} answered {r.status_code} to a tenant key"


def test_crm_is_reachable_with_the_operator_token(mt):
    c, op = mt["client"], _auth("op-token")

    created = c.post(
        "/customers", json={"email": "lead@corp.test", "company": "Corp"}, headers=op
    )
    assert created.status_code == 200
    body = created.json()
    assert body["success"] is True
    assert body["trial"]["days_remaining"] > 0

    dashboard = c.get("/customers/dashboard", headers=op)
    assert dashboard.status_code == 200
    assert [x["company"] for x in dashboard.json()["recent_customers"]] == ["Corp"]


def test_convert_prices_from_the_catalog_not_the_request_body(mt):
    """`mrr_cents` in the body used to land straight in the revenue total."""
    from app.billing.plans import PRICING_PLANS

    c, op = mt["client"], _auth("op-token")
    created = c.post(
        "/customers", json={"email": "conv@corp.test", "company": "Conv"}, headers=op
    )
    customer_id = created.json()["customer"]["id"]

    r = c.post(
        "/customers/convert",
        json={"customer_id": customer_id, "tier": "team", "mrr_cents": 99_999_999},
        headers=op,
    )
    assert r.status_code == 200

    stats = c.get("/customers/stats", headers=op).json()
    assert stats["mrr_usd"] == PRICING_PLANS["team"].monthly_price_cents / 100


def test_convert_rejects_a_tier_the_catalog_does_not_sell(mt):
    """An unknown tier was silently coerced to the cheapest plan."""
    c, op = mt["client"], _auth("op-token")
    created = c.post(
        "/customers", json={"email": "bad@corp.test", "company": "Bad"}, headers=op
    )
    customer_id = created.json()["customer"]["id"]

    r = c.post(
        "/customers/convert",
        json={"customer_id": customer_id, "tier": "pro"},
        headers=op,
    )
    assert r.status_code == 400
    assert "unknown tier" in r.json()["detail"].lower()
