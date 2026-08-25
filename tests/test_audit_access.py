"""Who may read the audit trail, and how much of it they see.

The trail records key ids, actor ids, approval decisions and security events, so
two properties have to hold on a multi-tenant deployment:

  * reading it is an admin action — a `viewer` key must not be able to enumerate
    the org's API-key ids and the identities that used them;
  * every answer is scoped to the caller's org. `/audit` filters, but
    `/audit/verify` used to run over the process-wide buffer, so its `count` was
    the whole fleet's activity and a broken link could name another tenant's event.

The hash chain links every entry in the process, so the *verification walk* still
has to cover all of them — only the report is scoped. That is what the unit tests
at the bottom pin.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

import app.api.main as api
import app.config as cfg
from app import audit, runtime, tenant_context
from app.database import engine as db_engine
from app.tenancy import auth as tenant_auth
from app.tenancy import models as tenancy_models
from app.tenancy import scoping


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
    audit.clear()


async def _provision(store) -> dict[str, str]:
    await store.setup()
    org_a = await store.create_org("Acme", plan="team")
    org_b = await store.create_org("Beta", plan="team")
    a_admin, _ = await store.issue_api_key(org_a.id, "a-admin", role="admin")
    a_viewer, _ = await store.issue_api_key(org_a.id, "a-view", role="viewer")
    b_admin, _ = await store.issue_api_key(org_b.id, "b-admin", role="admin")
    return {
        "a_admin": a_admin,
        "a_viewer": a_viewer,
        "b_admin": b_admin,
        "org_a": org_a.id,
        "org_b": org_b.id,
    }


@pytest.fixture
def mt(tmp_path, monkeypatch):
    monkeypatch.setenv("COPILOT_MULTI_TENANT", "true")
    monkeypatch.setenv("COPILOT_TENANT_DB", str(tmp_path / "tenants.sqlite"))
    monkeypatch.setenv("COPILOT_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/app.db")
    _reset()
    data = asyncio.run(_provision(tenant_auth.get_store()))
    with TestClient(api.app, raise_server_exceptions=False) as client:
        data["client"] = client
        yield data
    _reset()


def _auth(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _make_an_event(client: TestClient, key: str, name: str) -> None:
    """Any audited admin action; issuing an API key records `apikey.created`."""
    r = client.post("/admin/api-keys", json={"name": name, "role": "viewer"}, headers=_auth(key))
    assert r.status_code == 200, r.text


# --- RBAC ------------------------------------------------------------------ #


def test_viewer_cannot_read_the_audit_trail(mt):
    c = mt["client"]
    _make_an_event(c, mt["a_admin"], "ASECRET")

    denied = c.get("/audit?limit=20", headers=_auth(mt["a_viewer"]))
    assert denied.status_code == 403, denied.text
    assert "viewer" in denied.json()["detail"]


def test_viewer_cannot_verify_the_audit_chain(mt):
    denied = mt["client"].get("/audit/verify", headers=_auth(mt["a_viewer"]))
    assert denied.status_code == 403, denied.text


def test_admin_can_read_the_audit_trail(mt):
    c = mt["client"]
    _make_an_event(c, mt["a_admin"], "ASECRET")

    allowed = c.get("/audit?limit=20", headers=_auth(mt["a_admin"]))
    assert allowed.status_code == 200, allowed.text
    assert [e["event"] for e in allowed.json()["events"]] == ["apikey.created"]


def test_view_audit_is_an_admin_action_in_the_matrix():
    assert "view_audit" in tenancy_models.ACTIONS
    assert tenancy_models.can("admin", "view_audit") is True
    assert tenancy_models.can("owner", "view_audit") is True
    assert tenancy_models.can("responder", "view_audit") is False
    assert tenancy_models.can("viewer", "view_audit") is False


# --- tenant scoping of /audit/verify --------------------------------------- #


def test_verify_counts_only_the_callers_own_events(mt):
    c = mt["client"]
    _make_an_event(c, mt["a_admin"], "ASECRET")
    _make_an_event(c, mt["b_admin"], "BSECRET")
    _make_an_event(c, mt["b_admin"], "BSECRET2")

    a = c.get("/audit/verify", headers=_auth(mt["a_admin"]))
    b = c.get("/audit/verify", headers=_auth(mt["b_admin"]))
    assert a.status_code == 200 and b.status_code == 200
    assert a.json() == {"valid": True, "count": 1}, a.text
    assert b.json() == {"valid": True, "count": 2}, b.text


def test_audit_read_is_scoped_to_the_caller(mt):
    c = mt["client"]
    _make_an_event(c, mt["a_admin"], "ASECRET")
    _make_an_event(c, mt["b_admin"], "BSECRET")

    body = c.get("/audit?limit=50", headers=_auth(mt["a_admin"])).text
    assert "BSECRET" not in body
    assert mt["org_b"] not in body


# --- the chain maths, unit level ------------------------------------------- #


def test_scoped_verify_still_validates_across_the_whole_chain():
    """Filtering first would report a false 'tampered' — entries interleave."""
    audit.clear()
    token = tenant_context.set_tenant(tenant_context.TenantConfig(org_id="A"))
    audit.record("a.one")
    tenant_context.reset_tenant(token)
    token = tenant_context.set_tenant(tenant_context.TenantConfig(org_id="B"))
    audit.record("b.one")
    tenant_context.reset_tenant(token)
    token = tenant_context.set_tenant(tenant_context.TenantConfig(org_id="A"))
    audit.record("a.two")
    tenant_context.reset_tenant(token)

    assert audit.verify_chain(org_id="A") == {"valid": True, "count": 2}
    assert audit.verify_chain(org_id="B") == {"valid": True, "count": 1}
    assert audit.verify_chain() == {"valid": True, "count": 3}
    audit.clear()


def test_tampering_with_another_orgs_entry_does_not_name_it():
    audit.clear()
    token = tenant_context.set_tenant(tenant_context.TenantConfig(org_id="A"))
    audit.record("a.one")
    tenant_context.reset_tenant(token)
    token = tenant_context.set_tenant(tenant_context.TenantConfig(org_id="B"))
    audit.record("b.secret_event")
    tenant_context.reset_tenant(token)

    list(audit._BUFFER)[1]["stolen"] = True  # tamper with B's entry

    seen_by_a = audit.verify_chain(org_id="A")
    assert seen_by_a["valid"] is False
    assert seen_by_a["event"] is None  # B's event name is not leaked to A
    assert seen_by_a["broken_at"] is None

    seen_by_b = audit.verify_chain(org_id="B")
    assert seen_by_b["valid"] is False
    assert seen_by_b["event"] == "b.secret_event"
    audit.clear()


def test_unscoped_verify_keeps_its_contract():
    audit.clear()
    audit.record("a")
    audit.record("b", x=1)
    audit.record("c")
    list(audit._BUFFER)[1]["x"] = 999
    res = audit.verify_chain()
    assert res["valid"] is False
    assert res["broken_at"] == 1
    assert res["event"] == "b"
    assert res["count"] == 3
    audit.clear()
