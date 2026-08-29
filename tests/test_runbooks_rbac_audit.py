"""RBAC on the runbooks API.

`/api/runbooks` carried no authorisation at all: the app-wide dependency is
`require_auth`, which only proves the credential is real, and `require_perm` —
the only authorisation mechanism in this codebase — is a per-route dependency
that this router never declared. A `viewer` key, the role that is refused 403
when it tries to approve a single agent write, could author a runbook of
`kubernetes`/`command` steps, execute it, clear its own approval gate and delete
both the runbook and the execution rows that recorded the run.

These run the real app with COPILOT_MULTI_TENANT=true against a tmp tenant store
(the fixture is the one in tests/test_tenancy_api.py), so they exercise the real
auth path rather than calling the dependency by hand.
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
from app.runbooks import router as runbooks_router
from app.tenancy import auth as tenant_auth


async def _provision(store):
    await store.setup()
    org = await store.create_org("Acme", plan="team")
    admin_key, _ = await store.issue_api_key(org.id, "k-admin", role="admin")
    responder_key, _ = await store.issue_api_key(org.id, "k-resp", role="responder")
    viewer_key, _ = await store.issue_api_key(org.id, "k-view", role="viewer")
    return {
        "admin": admin_key,
        "responder": responder_key,
        "viewer": viewer_key,
        "org": org.id,
    }


@pytest.fixture
def mt(tmp_path, monkeypatch):
    monkeypatch.setenv("COPILOT_MULTI_TENANT", "true")
    monkeypatch.setenv("COPILOT_TENANT_DB", str(tmp_path / "tenants.sqlite"))
    monkeypatch.setenv("COPILOT_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/app.db")
    monkeypatch.delenv("KUBE_CONFIG_PATH", raising=False)
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


def _body(**over):
    body = {
        "name": "Drain the node",
        "description": "authored in a test",
        "service": "api",
        "trigger": "manual",
        "execution_mode": "requires_approval",
        "steps": [
            {
                "id": "step-0",
                "name": "Drain",
                "step_type": "kubernetes",
                "action": "drain_node",
                "params": {"node": "prod-node-1"},
            }
        ],
        "labels": {},
    }
    body.update(over)
    return body


def _create(mt, key: str | None = None) -> str:
    res = mt["client"].post(
        "/api/runbooks", json=_body(), headers=_auth(key or mt["admin"])
    )
    assert res.status_code == 200, res.text
    return res.json()["id"]


def _waiting_execution(mt) -> str:
    """A runbook parked at its approval gate, created by a role allowed to do so."""
    rb_id = _create(mt)
    res = mt["client"].post(
        f"/api/runbooks/{rb_id}/execute", json={}, headers=_auth(mt["responder"])
    )
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "waiting_approval"
    return res.json()["id"]


def test_viewer_cannot_author_a_runbook(mt):
    res = mt["client"].post("/api/runbooks", json=_body(), headers=_auth(mt["viewer"]))
    assert res.status_code == 403, res.text
    assert "viewer" in res.json()["detail"]
    # Nothing was written: the deny happens before the store is touched.
    listed = mt["client"].get("/api/runbooks", headers=_auth(mt["admin"])).json()
    assert all(rb["name"] != "Drain the node" for rb in listed)


def test_viewer_cannot_edit_or_delete_a_runbook(mt):
    rb_id = _create(mt)
    c = mt["client"]
    assert c.put(f"/api/runbooks/{rb_id}", json=_body(name="hijacked"),
                 headers=_auth(mt["viewer"])).status_code == 403
    assert c.delete(f"/api/runbooks/{rb_id}", headers=_auth(mt["viewer"])).status_code == 403
    # The runbook is still there and still says what its author wrote.
    still = c.get(f"/api/runbooks/{rb_id}", headers=_auth(mt["admin"]))
    assert still.status_code == 200
    assert still.json()["name"] == "Drain the node"


def test_viewer_cannot_execute_a_runbook(mt):
    rb_id = _create(mt)
    res = mt["client"].post(
        f"/api/runbooks/{rb_id}/execute", json={"service": "api"}, headers=_auth(mt["viewer"])
    )
    assert res.status_code == 403, res.text
    # No run was recorded, so nothing sits there for someone else to approve.
    executions = mt["client"].get(
        "/api/runbooks/executions", headers=_auth(mt["admin"])
    ).json()
    assert [e for e in executions if e["runbook_id"] == rb_id] == []


def test_viewer_cannot_clear_the_approval_gate(mt):
    """The self-approval half of the escalation: viewer executes, viewer approves."""
    execution_id = _waiting_execution(mt)
    c = mt["client"]
    assert c.post(f"/api/runbooks/executions/{execution_id}/approve",
                  headers=_auth(mt["viewer"])).status_code == 403
    assert c.post(f"/api/runbooks/executions/{execution_id}/reject",
                  headers=_auth(mt["viewer"])).status_code == 403
    # Still parked: the gate held, and the run did not silently advance.
    state = c.get(f"/api/runbooks/executions/{execution_id}", headers=_auth(mt["admin"]))
    assert state.status_code == 200
    assert state.json()["status"] == "waiting_approval"


def test_viewer_cannot_delete_the_execution_record(mt):
    execution_id = _waiting_execution(mt)
    c = mt["client"]
    assert c.delete(f"/api/runbooks/executions/{execution_id}",
                    headers=_auth(mt["viewer"])).status_code == 403
    assert c.get(f"/api/runbooks/executions/{execution_id}",
                 headers=_auth(mt["admin"])).status_code == 200


def test_viewer_may_still_read(mt):
    """`view` is the viewer's one permission, so the read routes stay open to it."""
    rb_id = _create(mt)
    c, v = mt["client"], _auth(mt["viewer"])
    assert c.get("/api/runbooks", headers=v).status_code == 200
    assert c.get(f"/api/runbooks/{rb_id}", headers=v).status_code == 200
    assert c.get("/api/runbooks/executions", headers=v).status_code == 200


def test_responder_may_run_but_not_author(mt):
    """Authoring is a rung above executing, or `execute` would mean "run anything"."""
    rb_id = _create(mt)
    c, r = mt["client"], _auth(mt["responder"])
    assert c.post("/api/runbooks", json=_body(), headers=r).status_code == 403
    assert c.put(f"/api/runbooks/{rb_id}", json=_body(), headers=r).status_code == 403
    assert c.delete(f"/api/runbooks/{rb_id}", headers=r).status_code == 403

    started = c.post(f"/api/runbooks/{rb_id}/execute", json={}, headers=r)
    assert started.status_code == 200, started.text
    execution_id = started.json()["id"]
    assert c.post(f"/api/runbooks/executions/{execution_id}/reject",
                  headers=r).status_code == 200
    assert c.delete(f"/api/runbooks/executions/{execution_id}",
                    headers=r).status_code == 403


def test_admin_retains_the_whole_surface(mt):
    c, a = mt["client"], _auth(mt["admin"])
    rb_id = _create(mt)
    assert c.put(f"/api/runbooks/{rb_id}", json=_body(name="edited"),
                 headers=a).status_code == 200
    started = c.post(f"/api/runbooks/{rb_id}/execute", json={}, headers=a)
    assert started.status_code == 200, started.text
    execution_id = started.json()["id"]
    assert c.delete(f"/api/runbooks/executions/{execution_id}", headers=a).status_code == 200
    assert c.delete(f"/api/runbooks/{rb_id}", headers=a).status_code == 200


def test_every_route_is_classified():
    """A route added later must be given a permission, not inherit none.

    This is the invariant the router lost: the guard is declared once on the
    router, so the only way to leave a handler unprotected now is to omit it
    from the table — which fails closed, but silently as far as anyone reading
    the file is concerned. Assert the two lists match instead.
    """
    endpoints = {r.endpoint for r in runbooks_router.router.routes}
    classified = set(runbooks_router._PERMISSION_BY_ENDPOINT)
    missing = {e.__name__ for e in endpoints - classified}
    assert missing == set(), f"runbook routes with no permission entry: {sorted(missing)}"
    assert not classified - endpoints, "table names endpoints that are not routes"


def test_unclassified_route_is_refused_rather_than_served():
    """The fail-closed half: an unknown endpoint must not fall through to the handler."""

    class _FakeRoute:
        endpoint = staticmethod(lambda: None)

    class _FakeRequest:
        scope = {"route": _FakeRoute()}
        url = type("U", (), {"path": "/api/runbooks/whatever"})()

    with pytest.raises(HTTPException) as exc:
        asyncio.run(runbooks_router._enforce_runbook_rbac(_FakeRequest()))
    assert exc.value.status_code == 500
    assert "no RBAC classification" in exc.value.detail
