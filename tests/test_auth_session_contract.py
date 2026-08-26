"""The credential a completed OAuth login produces must actually authorize API calls.

Before this, `require_auth` accepted only `dcp_…` tenant keys and Supabase-JWKS
JWTs. The session token our own OAuth callback mints (HS256 over
COPILOT_SECRET_KEY, app/auth/oauth.py) matched neither, so a user who finished
GitHub/Google login got 401 on every route and saw an entirely empty product.
"""

import asyncio

import pytest
from fastapi.testclient import TestClient

import app.api.main as api
import app.config as cfg
from app import runtime, tenant_context
from app.auth.oauth import create_session_token
from app.database import engine as db_engine
from app.tenancy import auth as tenant_auth

MEMBER_EMAIL = "owner@acme.test"
STRANGER_EMAIL = "nobody@elsewhere.test"

# Routes that are behind require_auth and read-only, so a 200 proves the gate
# opened rather than that a handler happened to tolerate the call.
GATED_ROUTES = ["/api/incidents", "/oncall/teams", "/api/investigations"]


def _reset() -> None:
    cfg.get_settings.cache_clear()
    tenant_auth.reset_store_cache()
    tenant_context.current_tenant.set(None)
    api._RL.clear()
    runtime.reset()
    db_engine._engine = None
    db_engine._session_factory = None


async def _provision(store) -> str:
    await store.setup()
    org = await store.create_org("Acme", plan="team", owner_email=MEMBER_EMAIL)
    return org.id


@pytest.fixture
def mt_session(tmp_path, monkeypatch):
    """Multi-tenant app with one provisioned org whose owner is MEMBER_EMAIL."""
    monkeypatch.setenv("COPILOT_MULTI_TENANT", "true")
    monkeypatch.setenv("COPILOT_TENANT_DB", str(tmp_path / "tenants.sqlite"))
    monkeypatch.setenv("COPILOT_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/app.db")
    monkeypatch.setenv("COPILOT_SECRET_KEY", "s" * 40)
    _reset()
    org_id = asyncio.run(_provision(tenant_auth.get_store()))
    with TestClient(api.app) as client:
        yield {"client": client, "org_id": org_id}
    _reset()


@pytest.fixture
def single_tenant(tmp_path, monkeypatch):
    """Single-tenant app with COPILOT_API_TOKEN set (the classic self-host setup)."""
    monkeypatch.delenv("COPILOT_MULTI_TENANT", raising=False)
    monkeypatch.setenv("COPILOT_API_TOKEN", "prod-token")
    monkeypatch.setenv("COPILOT_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/app.db")
    monkeypatch.setenv("COPILOT_SECRET_KEY", "s" * 40)
    _reset()
    with TestClient(api.app) as client:
        yield client
    _reset()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_multi_tenant_accepts_oauth_session_token(mt_session):
    """A session token for a provisioned member authorizes every gated route."""
    token = create_session_token("user_1", mt_session["org_id"], MEMBER_EMAIL, role="owner")
    for path in GATED_ROUTES:
        res = mt_session["client"].get(path, headers=_auth(token))
        assert res.status_code == 200, f"{path} -> {res.status_code} {res.text}"


def test_multi_tenant_session_token_binds_the_tenant(mt_session):
    """The session must resolve to the member's real org, not an anonymous request."""
    token = create_session_token("user_1", mt_session["org_id"], MEMBER_EMAIL, role="owner")
    res = mt_session["client"].get("/admin/org", headers=_auth(token))
    assert res.status_code == 200, res.text
    assert res.json()["id"] == mt_session["org_id"]


def test_multi_tenant_rejects_session_for_non_member(mt_session):
    """Signature-valid but not provisioned into any org: still 401, never a blank pass."""
    token = create_session_token("user_2", "org_whatever", STRANGER_EMAIL, role="owner")
    res = mt_session["client"].get("/api/incidents", headers=_auth(token))
    assert res.status_code == 401


def test_multi_tenant_rejects_forged_session_token(mt_session):
    """A token signed with the wrong key must not authenticate."""
    import jwt

    forged = jwt.encode(
        {"sub": "user_1", "org_id": mt_session["org_id"], "email": MEMBER_EMAIL,
         "role": "owner", "type": "session"},
        "not-the-secret-key",
        algorithm="HS256",
    )
    res = mt_session["client"].get("/api/incidents", headers=_auth(forged))
    assert res.status_code == 401


def test_multi_tenant_role_comes_from_the_store_not_the_token(mt_session):
    """A token claiming role=owner must not out-rank the stored membership role."""
    store = tenant_auth.get_store()

    async def _demote():
        user = await store.get_user_by_email(MEMBER_EMAIL)
        assert user is not None
        await store.add_member(mt_session["org_id"], user.id, "viewer")

    asyncio.run(_demote())
    token = create_session_token("user_1", mt_session["org_id"], MEMBER_EMAIL, role="owner")
    # `view` is allowed for a viewer...
    assert mt_session["client"].get("/api/incidents", headers=_auth(token)).status_code == 200
    # ...but issuing an API key needs admin, and the token's owner claim is ignored.
    res = mt_session["client"].post(
        "/admin/api-keys", json={"name": "k"}, headers=_auth(token)
    )
    assert res.status_code == 403, res.text


def test_single_tenant_accepts_oauth_session_token(single_tenant):
    """A single-tenant deploy with OAuth login on must honour the session it issued."""
    token = create_session_token("user_1", "org_acme_test", MEMBER_EMAIL, role="owner")
    for path in GATED_ROUTES:
        res = single_tenant.get(path, headers=_auth(token))
        assert res.status_code == 200, f"{path} -> {res.status_code} {res.text}"


def test_single_tenant_still_rejects_garbage(single_tenant):
    """The shared-token gate has not been loosened into an open door."""
    assert single_tenant.get("/api/incidents").status_code == 401
    assert single_tenant.get("/api/incidents", headers=_auth("nope")).status_code == 401
    assert single_tenant.get("/api/incidents", headers=_auth("a.b.c")).status_code == 401
    assert single_tenant.get("/api/incidents", headers=_auth("prod-token")).status_code == 200
