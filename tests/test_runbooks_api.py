"""HTTP contract for editing a runbook.

The Runbook Builder saves with `PUT /api/runbooks/{id}`. That route did not
exist — the probe returned 405 Method Not Allowed — and the page only acted on
`res.ok`, so clicking Save appeared to work and wrote nothing.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.api.main as api
import app.config as cfg
from app import tenant_context
from app.database import engine as db_engine


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("COPILOT_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/app.db")
    monkeypatch.delenv("COPILOT_MULTI_TENANT", raising=False)
    monkeypatch.delenv("COPILOT_API_TOKEN", raising=False)
    cfg.get_settings.cache_clear()
    db_engine._engine = None
    db_engine._session_factory = None
    tenant_context.current_tenant.set(None)
    api._RL.clear()
    with TestClient(api.app) as c:
        yield c
    cfg.get_settings.cache_clear()
    db_engine._engine = None
    db_engine._session_factory = None
    tenant_context.current_tenant.set(None)


def _payload(**over):
    body = {
        "name": "Restart service (edited)",
        "description": "edited by the builder",
        "service": "api",
        "trigger": "manual",
        "execution_mode": "requires_approval",
        "steps": [
            {"id": "s1", "name": "Restart", "step_type": "command", "action": "systemctl restart api"}
        ],
        "labels": {},
    }
    body.update(over)
    return body


def test_put_persists_the_edit(client):
    res = client.put("/api/runbooks/restart-service", json=_payload())
    assert res.status_code == 200, res.text
    assert res.json()["name"] == "Restart service (edited)"

    again = client.get("/api/runbooks/restart-service")
    assert again.status_code == 200
    body = again.json()
    assert body["name"] == "Restart service (edited)"
    assert [s["action"] for s in body["steps"]] == ["systemctl restart api"]


def test_put_on_an_unknown_runbook_is_404_not_a_silent_create(client):
    res = client.put("/api/runbooks/no-such-runbook", json=_payload())
    assert res.status_code == 404
    assert client.get("/api/runbooks/no-such-runbook").status_code == 404


def test_put_with_a_bad_step_type_is_a_400_not_a_500(client):
    res = client.put(
        "/api/runbooks/restart-service",
        json=_payload(steps=[{"name": "x", "step_type": "not-a-real-type"}]),
    )
    assert res.status_code == 400
    # The runbook must be untouched by the rejected write.
    assert client.get("/api/runbooks/restart-service").json()["name"] != "Restart service (edited)"


def test_post_then_put_round_trips_a_new_runbook(client):
    """What the builder does for a runbook it just created: POST, then PUT the id."""
    created = client.post(
        "/api/runbooks",
        json={"name": "Brand new", "description": "", "steps": []},
    )
    assert created.status_code == 200, created.text
    rb_id = created.json()["id"]

    edited = client.put(f"/api/runbooks/{rb_id}", json=_payload(name="Brand new v2"))
    assert edited.status_code == 200, edited.text
    assert client.get(f"/api/runbooks/{rb_id}").json()["name"] == "Brand new v2"
