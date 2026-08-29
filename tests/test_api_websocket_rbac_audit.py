"""The WebSocket transport must enforce the same RBAC matrix as its HTTP twins.

`/chat/stream` and `/approve/stream` are declared with
`Depends(require_perm("run_investigation"))` / `Depends(require_perm("approve_action"))`,
but a `@router.websocket` route cannot carry route dependencies, so the role check
has to live inside the message handlers. These tests run the real app in
multi-tenant mode against a tmp tenant store and drive the real /ws/chat route, so
they fail if the in-handler check is removed.

The agent itself is never invoked: `_get_session` is stubbed with a fake session,
which is what makes it observable whether the handler proceeded past the gate.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

import app.api.main as api
import app.api.websocket as ws
import app.config as cfg
from app import runtime, tenant_context
from app.database import engine as db_engine
from app.tenancy import auth as tenant_auth


async def _provision(store):
    await store.setup()
    org = await store.create_org("Acme", plan="team")
    viewer_key, _ = await store.issue_api_key(org.id, "k-view", role="viewer")
    responder_key, _ = await store.issue_api_key(org.id, "k-resp", role="responder")
    return {"viewer": viewer_key, "responder": responder_key, "org": org.id}


class FakeSession:
    """Stands in for a CopilotSession so the gate's effect is observable."""

    def __init__(self, pending: bool) -> None:
        self._pending = pending
        self.asked: str | None = None
        self.resumed: bool | None = None

    async def pending_interrupt(self) -> bool:
        return self._pending

    async def ask_stream(self, message: str):
        self.asked = message
        yield {"type": "trace", "line": f"ASKED {message}"}

    async def resume_stream(self, approved: bool, reason: str = ""):
        self.resumed = approved
        yield {"type": "trace", "line": f"EXECUTED approved={approved}"}


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

    audited: list[tuple] = []
    monkeypatch.setattr(
        ws.audit, "record", lambda action, **kw: audited.append((action, kw))
    )
    data["audited"] = audited

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


def _install_session(monkeypatch, pending: bool) -> FakeSession:
    fake = FakeSession(pending)

    async def _get_session(thread_id: str):
        return fake

    monkeypatch.setattr(api, "_get_session", _get_session)
    return fake


def _connect(client, key: str, thread_id: str = "t1"):
    return client.websocket_connect(
        f"/ws/chat/{thread_id}", subprotocols=["copilot-v1", f"auth-{key}"]
    )


def test_viewer_cannot_start_an_investigation_over_websocket(mt, monkeypatch):
    fake = _install_session(monkeypatch, pending=False)
    with _connect(mt["client"], mt["viewer"]) as sock:
        assert sock.receive_json()["type"] == "connected"
        sock.send_json({"type": "chat", "thread_id": "t1", "message": "investigate now"})
        frame = sock.receive_json()
    assert frame["type"] == "error"
    assert "not permitted to run investigation" in frame["message"]
    # The agent turn must not have started at all.
    assert fake.asked is None


def test_viewer_cannot_approve_over_websocket(mt, monkeypatch):
    fake = _install_session(monkeypatch, pending=True)
    with _connect(mt["client"], mt["viewer"]) as sock:
        assert sock.receive_json()["type"] == "connected"
        sock.send_json({"type": "approval", "thread_id": "t1", "approved": True})
        frame = sock.receive_json()
    assert frame["type"] == "error"
    assert "not permitted to approve action" in frame["message"]
    # The pending write must not have executed...
    assert fake.resumed is None
    # ...and nothing may have been written to the trail as a legitimate approval.
    assert [a for a, _ in mt["audited"] if a == "approval.decided"] == []


def test_responder_may_still_start_an_investigation_over_websocket(mt, monkeypatch):
    fake = _install_session(monkeypatch, pending=False)
    with _connect(mt["client"], mt["responder"]) as sock:
        assert sock.receive_json()["type"] == "connected"
        sock.send_json({"type": "chat", "thread_id": "t1", "message": "investigate now"})
        frame = sock.receive_json()
    assert frame == {"type": "trace", "thread_id": "t1", "line": "ASKED investigate now"}
    assert fake.asked == "investigate now"


def test_responder_may_still_approve_over_websocket(mt, monkeypatch):
    fake = _install_session(monkeypatch, pending=True)
    with _connect(mt["client"], mt["responder"]) as sock:
        assert sock.receive_json()["type"] == "connected"
        sock.send_json({"type": "approval", "thread_id": "t1", "approved": True})
        frame = sock.receive_json()
    assert frame == {"type": "trace", "thread_id": "t1", "line": "EXECUTED approved=True"}
    assert fake.resumed is True
    assert ("approval.decided", {"thread": "t1", "approved": True, "via": "websocket"}) in mt[
        "audited"
    ]


def test_websocket_matches_the_http_verdict_for_the_same_key(mt, monkeypatch):
    """Parity check: the transport must not change the answer for one credential."""
    _install_session(monkeypatch, pending=True)
    http = mt["client"].post(
        "/approve/stream",
        json={"thread_id": "t1", "approved": True},
        headers={"Authorization": f"Bearer {mt['viewer']}"},
    )
    assert http.status_code == 403
    with _connect(mt["client"], mt["viewer"]) as sock:
        sock.receive_json()
        sock.send_json({"type": "approval", "thread_id": "t1", "approved": True})
        frame = sock.receive_json()
    assert frame["type"] == "error"
    assert frame["message"] == http.json()["detail"]


def test_single_tenant_websocket_chat_is_unaffected(tmp_path, monkeypatch):
    """`require_perm` is a no-op without tenants; the offline demo must still run."""
    monkeypatch.delenv("COPILOT_MULTI_TENANT", raising=False)
    monkeypatch.setenv("COPILOT_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/app.db")
    cfg.get_settings.cache_clear()
    tenant_context.current_tenant.set(None)
    api._RL.clear()
    runtime.reset()
    db_engine._engine = None
    db_engine._session_factory = None
    fake = _install_session(monkeypatch, pending=False)
    try:
        with TestClient(api.app) as client:
            with client.websocket_connect(
                "/ws/chat/t1", subprotocols=["copilot-v1"]
            ) as sock:
                assert sock.receive_json()["type"] == "connected"
                sock.send_json({"type": "chat", "thread_id": "t1", "message": "hi there"})
                frame = sock.receive_json()
        assert frame["line"] == "ASKED hi there"
        assert fake.asked == "hi there"
    finally:
        cfg.get_settings.cache_clear()
        api._RL.clear()
        runtime.reset()
        db_engine._engine = None
        db_engine._session_factory = None
