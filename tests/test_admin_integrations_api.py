"""Disconnecting an integration.

The marketplace's Disconnect button issues `DELETE /admin/integrations/{name}`.
There was no such route — the probe returned 405 Method Not Allowed even with a
valid tenant key — so a credential could be stored but never withdrawn, and the
card kept saying "Connected".
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
    admin_key, _ = await store.issue_api_key(org.id, "k-admin", role="admin")
    viewer_key, _ = await store.issue_api_key(org.id, "k-view", role="viewer")
    other = await store.create_org("Beta", plan="free")
    other_key, _ = await store.issue_api_key(other.id, "k-b", role="admin")
    return {"admin": admin_key, "viewer": viewer_key, "other": other_key}


@pytest.fixture
def mt(tmp_path, monkeypatch):
    monkeypatch.setenv("COPILOT_MULTI_TENANT", "true")
    monkeypatch.setenv("COPILOT_TENANT_DB", str(tmp_path / "tenants.sqlite"))
    monkeypatch.setenv("COPILOT_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/app.db")
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


def _connect(client, key: str, name: str = "slack") -> None:
    res = client.post(
        "/admin/integrations", json={"name": name, "value": "secret"}, headers=_auth(key)
    )
    assert res.status_code == 200, res.text


def test_disconnect_removes_the_integration(mt):
    pytest.importorskip("cryptography")
    c = mt["client"]
    _connect(c, mt["admin"])
    assert "slack" in c.get("/admin/integrations", headers=_auth(mt["admin"])).json()["integrations"]

    res = c.delete("/admin/integrations/slack", headers=_auth(mt["admin"]))
    assert res.status_code == 200, res.text
    assert res.json() == {"status": "disconnected", "name": "slack"}
    assert c.get("/admin/integrations", headers=_auth(mt["admin"])).json()["integrations"] == []


def test_disconnecting_something_not_configured_is_404(mt):
    res = mt["client"].delete("/admin/integrations/nope", headers=_auth(mt["admin"]))
    assert res.status_code == 404
    assert "not configured" in res.json()["detail"]


def test_viewer_cannot_disconnect(mt):
    pytest.importorskip("cryptography")
    c = mt["client"]
    _connect(c, mt["admin"])

    assert c.delete("/admin/integrations/slack", headers=_auth(mt["viewer"])).status_code == 403
    # ...and the secret is still there.
    assert "slack" in c.get("/admin/integrations", headers=_auth(mt["admin"])).json()["integrations"]


def test_one_org_cannot_disconnect_anothers_integration(mt):
    pytest.importorskip("cryptography")
    c = mt["client"]
    _connect(c, mt["admin"])

    assert c.delete("/admin/integrations/slack", headers=_auth(mt["other"])).status_code == 404
    assert "slack" in c.get("/admin/integrations", headers=_auth(mt["admin"])).json()["integrations"]


def test_disconnect_requires_a_credential(mt):
    assert mt["client"].delete("/admin/integrations/slack").status_code == 401
