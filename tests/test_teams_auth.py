"""The /api/teams surface must accept the credentials this deployment issues.

Two failure modes are both real bugs and both are pinned here:

  * accepting a forged token — the original code base64-decoded the JWT payload
    and trusted `sub`, so anyone who knew a user id could delete their teams;
  * accepting *only* a Supabase JWT — no deployment of this app issues one unless
    `supabase_jwks_url` is configured, so the tenant API keys (`dcp_…`) handed out
    by self-serve signup and the shared `COPILOT_API_TOKEN` both got 401 and the
    entire Team page was dead in production.

`require_auth` (app/api/main.py) has already authenticated every request that
reaches a team handler; these tests check the router agrees about who the caller
is instead of demanding a second, different credential.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import app.api.main as api
import app.config as cfg
from app import runtime, tenant_context
from app.database import engine as db_engine
from app.teams import router as teams_router
from app.tenancy import auth as tenant_auth
from app.tenancy import scoping

TOKEN = "prodtoken"
# Structurally a JWT, signed by nobody.
FORGED = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ2aWN0aW0tdWlkIn0.TOTALLYFAKESIG"


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


async def _provision(store) -> dict[str, str]:
    await store.setup()
    org_a = await store.create_org("Acme", plan="team")
    org_b = await store.create_org("Beta", plan="free")
    admin_key, _ = await store.issue_api_key(org_a.id, "k-admin", role="admin")
    viewer_key, _ = await store.issue_api_key(org_a.id, "k-view", role="viewer")
    b_key, _ = await store.issue_api_key(org_b.id, "k-b", role="admin")
    return {
        "admin": admin_key,
        "viewer": viewer_key,
        "b": b_key,
        "org_a": org_a.id,
        "org_b": org_b.id,
    }


@pytest.fixture
def multi_tenant(tmp_path, monkeypatch):
    """Multi-tenant deployment: the only credential is a `dcp_…` API key."""
    monkeypatch.setenv("COPILOT_MULTI_TENANT", "true")
    monkeypatch.setenv("COPILOT_TENANT_DB", str(tmp_path / "tenants.sqlite"))
    monkeypatch.setenv("COPILOT_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/app.db")
    monkeypatch.delenv("SUPABASE_JWKS_URL", raising=False)
    _reset()
    keys = asyncio.run(_provision(tenant_auth.get_store()))
    with TestClient(api.app, raise_server_exceptions=False) as client:
        keys["client"] = client
        yield keys
    _reset()


@pytest.fixture
def single_tenant(tmp_path, monkeypatch):
    """Single-tenant deployment: the only credential is COPILOT_API_TOKEN."""
    monkeypatch.delenv("COPILOT_MULTI_TENANT", raising=False)
    monkeypatch.setenv("COPILOT_API_TOKEN", TOKEN)
    monkeypatch.setenv("COPILOT_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/app.db")
    monkeypatch.delenv("SUPABASE_JWKS_URL", raising=False)
    _reset()
    with TestClient(api.app, raise_server_exceptions=False) as client:
        yield client
    _reset()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --- the credentials the deployment actually issues ------------------------ #


def test_tenant_api_key_can_create_and_list_teams(multi_tenant):
    c = multi_tenant["client"]
    created = c.post("/api/teams", json={"name": "Platform"}, headers=_auth(multi_tenant["admin"]))
    assert created.status_code == 200, created.text
    assert created.json()["name"] == "Platform"

    listed = c.get("/api/teams", headers=_auth(multi_tenant["admin"]))
    assert listed.status_code == 200, listed.text
    assert [t["name"] for t in listed.json()] == ["Platform"]


def test_shared_api_token_can_create_and_list_teams(single_tenant):
    created = single_tenant.post("/api/teams", json={"name": "SRE"}, headers=_auth(TOKEN))
    assert created.status_code == 200, created.text

    listed = single_tenant.get("/api/teams", headers=_auth(TOKEN))
    assert listed.status_code == 200, listed.text
    assert [t["name"] for t in listed.json()] == ["SRE"]


def test_oauth_session_token_can_create_and_list_teams(single_tenant, monkeypatch):
    """The credential a user holds after GitHub/Google login."""
    monkeypatch.setenv("COPILOT_SECRET_KEY", "k" * 32)
    cfg.get_settings.cache_clear()
    from app.auth.oauth import create_session_token

    session = create_session_token("user-1", "-", "dev@example.com", role="owner")
    created = single_tenant.post("/api/teams", json={"name": "Payments"}, headers=_auth(session))
    assert created.status_code == 200, created.text
    assert created.json()["owner_id"] == "user-1"


# --- forgery stays closed --------------------------------------------------- #


def test_forged_jwt_is_rejected_multi_tenant(multi_tenant):
    c = multi_tenant["client"]
    assert c.get("/api/teams", headers=_auth(FORGED)).status_code == 401
    assert c.post("/api/teams", json={"name": "x"}, headers=_auth(FORGED)).status_code == 401


def test_forged_jwt_is_rejected_single_tenant(single_tenant):
    assert single_tenant.get("/api/teams", headers=_auth(FORGED)).status_code == 401


def test_wrong_shared_token_is_rejected_by_the_router_itself(monkeypatch):
    """Fail closed without leaning on require_auth: the router re-checks the token."""
    monkeypatch.delenv("COPILOT_MULTI_TENANT", raising=False)
    monkeypatch.setenv("COPILOT_API_TOKEN", TOKEN)
    _reset()
    with pytest.raises(HTTPException) as excinfo:
        teams_router._get_user_from_header("Bearer not-the-token")
    assert excinfo.value.status_code == 401
    with pytest.raises(HTTPException):
        teams_router._get_user_from_header(None)
    assert teams_router._get_user_from_header(f"Bearer {TOKEN}")["id"]
    _reset()


def test_missing_header_is_rejected(single_tenant):
    assert single_tenant.get("/api/teams").status_code == 401


# --- one tenant's teams are not another's ---------------------------------- #


def test_another_orgs_key_does_not_see_the_teams(multi_tenant):
    c = multi_tenant["client"]
    assert (
        c.post("/api/teams", json={"name": "Acme Core"}, headers=_auth(multi_tenant["admin"])).status_code
        == 200
    )
    other = c.get("/api/teams", headers=_auth(multi_tenant["b"]))
    assert other.status_code == 200, other.text
    assert other.json() == []


# --- an API key acts for its org, so the org RBAC matrix still applies ------ #


def test_viewer_key_may_read_but_not_create(multi_tenant):
    c = multi_tenant["client"]
    assert c.get("/api/teams", headers=_auth(multi_tenant["viewer"])).status_code == 200
    denied = c.post("/api/teams", json={"name": "Nope"}, headers=_auth(multi_tenant["viewer"]))
    assert denied.status_code == 403, denied.text
    assert "viewer" in denied.json()["detail"]
