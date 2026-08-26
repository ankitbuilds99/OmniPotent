"""A second message on the same thread_id must work over /chat/stream.

The reported failure: turn 1 on a thread streamed to `done`, and every turn
after it on that same thread_id returned

    data: {"type": "error", "answer": "Something went wrong while running the
           agent. Please try again."}

forever — the thread was bricked. The root cause was the MCP stdio transport
being torn down when sse-starlette cancelled the SSE generator task at the end
of turn 1 (fixed in app/session.py), but the endpoint made it *permanent*: the
error handler only evicted the pooled session `if not existed`, i.e. never on
turn 2+, so the dead session stayed in `_SESSIONS` and answered every later
message with the same error.

These tests drive the real endpoint with a fake session that reproduces that
shape — a fresh session answers one turn, then its transport is dead — and
assert the thread keeps working.
"""

from __future__ import annotations

import json

import anyio
import pytest
from fastapi.testclient import TestClient

import app.api.main as api
import app.config as cfg
from app import runtime
from app.database import engine as db_engine


class _DeadAfterFirstTurn:
    """Session whose tool transport dies once its first turn finishes.

    Faithful to the production failure: a *fresh* session answers fine, a reused
    one raises ``anyio.ClosedResourceError`` from the tools node mid-stream.
    """

    built: list["_DeadAfterFirstTurn"] = []

    def __init__(self, thread_id: str = "default") -> None:
        self.thread_id = thread_id
        self.turns = 0
        _DeadAfterFirstTurn.built.append(self)

    async def __aenter__(self) -> "_DeadAfterFirstTurn":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def pending_interrupt(self) -> bool:
        return False

    async def ask_stream(self, question: str):
        self.turns += 1
        yield {"type": "trace", "node": "agent", "line": "🤖 calling tool(s): search_logs"}
        if self.turns > 1:
            raise anyio.ClosedResourceError()
        yield {
            "type": "done",
            "final_text": f"answer for: {question}",
            "trace": ["🤖 calling tool(s): search_logs"],
            "report": None,
            "tokens_used": 7,
        }


class _AlwaysBroken(_DeadAfterFirstTurn):
    """Every turn on every instance fails with a non-transport error."""

    async def ask_stream(self, question: str):
        self.turns += 1
        yield {"type": "trace", "node": "plan", "line": "📋 planned 1 step(s)"}
        raise ValueError("model provider rejected the request")


class _ApprovalTransportDies(_DeadAfterFirstTurn):
    """Paused thread whose transport is dead when the human approves."""

    async def pending_interrupt(self) -> bool:
        return True

    async def resume_stream(self, approved: bool, reason: str = "", auto: bool = False):
        self.turns += 1
        yield {"type": "trace", "node": "agent", "line": "🤖 applying the approved fix"}
        raise anyio.ClosedResourceError()


def _events(resp) -> list[dict]:
    return [
        json.loads(line[len("data: "):])
        for line in resp.text.splitlines()
        if line.startswith("data: ")
    ]


@pytest.fixture
def client(tmp_path, monkeypatch):
    # A database of this test's own, set through monkeypatch rather than at
    # import time: a module-level os.environ write leaks the DSN into the rest
    # of the session, and tests/integration/conftest.py reads that variable to
    # decide a real Postgres is available and hands it straight to asyncpg.
    monkeypatch.setenv("COPILOT_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/history.db")
    cfg.get_settings.cache_clear()
    db_engine._engine = None
    db_engine._session_factory = None
    api._RL.clear()
    api._SESSIONS.clear()
    api._AWAITING.clear()
    runtime.reset()
    _DeadAfterFirstTurn.built.clear()
    monkeypatch.setattr(api, "CopilotSession", _DeadAfterFirstTurn)
    with TestClient(api.app) as c:
        yield c
    api._SESSIONS.clear()
    api._AWAITING.clear()
    api._RL.clear()
    cfg.get_settings.cache_clear()
    db_engine._engine = None
    db_engine._session_factory = None
    runtime.reset()


def _turn(client, thread_id: str, message: str) -> list[dict]:
    r = client.post("/chat/stream", json={"thread_id": thread_id, "message": message})
    assert r.status_code == 200, r.text
    return _events(r)


def test_second_and_third_turns_on_one_thread_still_answer(client):
    """Turn 1 works today; turns 2 and 3 used to be permanently bricked."""
    first = _turn(client, "live1", "why is checkout failing")
    assert first[-1]["status"] == "completed", first

    second = _turn(client, "live1", "and what changed at 14:00?")
    assert second[-1]["status"] == "completed", second
    assert second[-1]["answer"] == "answer for: and what changed at 14:00?"

    third = _turn(client, "live1", "who owns that service?")
    assert third[-1]["status"] == "completed", third

    # Each recovery rebuilt the session rather than reusing the dead one.
    assert len(_DeadAfterFirstTurn.built) == 3


def test_dead_session_is_not_left_in_the_pool(client):
    """Whatever the turn's outcome, a session that raised is never reused."""
    _turn(client, "live2", "first")
    dead = api._SESSIONS.get(api._scoped("live2"))
    _turn(client, "live2", "second")
    assert api._SESSIONS.get(api._scoped("live2")) is not dead


def test_non_transport_failure_is_reported_once_and_not_retried(client, monkeypatch):
    """A model/provider error must surface honestly — no silent re-run of the
    turn — but must still not poison the thread for the next message."""
    monkeypatch.setattr(api, "CopilotSession", _AlwaysBroken)
    _AlwaysBroken.built.clear()

    events = _turn(client, "live3", "why 500s?")
    assert events[-1]["type"] == "error"
    assert len(_AlwaysBroken.built) == 1
    assert _AlwaysBroken.built[0].turns == 1  # ran once, not retried

    # ...and the broken session was dropped, so the next message gets a fresh one.
    assert api._scoped("live3") not in api._SESSIONS
    _turn(client, "live3", "try again")
    assert len(_AlwaysBroken.built) == 2


def test_failed_approval_stream_drops_the_session_without_replaying_the_write(
    client, monkeypatch
):
    """/approve/stream must not leave a broken session in the pool — and must not
    silently re-run an approved (possibly already executed) write action."""
    monkeypatch.setattr(api, "CopilotSession", _ApprovalTransportDies)
    _ApprovalTransportDies.built.clear()

    r = client.post("/approve/stream", json={"thread_id": "live4", "approved": True})
    assert r.status_code == 200, r.text
    events = _events(r)
    assert events[-1]["type"] == "error"

    assert api._scoped("live4") not in api._SESSIONS  # evicted, thread not bricked
    assert len(_ApprovalTransportDies.built) == 1
    assert _ApprovalTransportDies.built[0].turns == 1  # the write was not replayed
