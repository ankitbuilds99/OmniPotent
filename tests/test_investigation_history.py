"""GET /api/investigations must list real, persisted, per-tenant history.

Before this, the endpoint imported a module-level dict out of
`app/api/graphql/schema.py`, which imports strawberry — not a dependency — so
the history page 500'd on every load. The `investigations` table it should have
been reading had existed since the schema was written with no reader and no
writer anywhere in the codebase.

Same shape as tests/test_oncall_tenancy.py: the real app with lifespan (so
init_db creates the tables) against a tmp SQLite file and two real tenants.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

import app.api.main as api
import app.config as cfg
from app import investigation_history, runtime, tenant_context
from app.database import engine as db_engine
from app.tenancy import auth as tenant_auth


async def _provision(store):
    await store.setup()
    org_a = await store.create_org("Acme", plan="team")
    key_a, _ = await store.issue_api_key(org_a.id, "k-a", role="admin")
    org_b = await store.create_org("Umbrella", plan="team")
    key_b, _ = await store.issue_api_key(org_b.id, "k-b", role="admin")
    return {"a": key_a, "b": key_b}


@pytest.fixture
def history(tmp_path, monkeypatch):
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
    with TestClient(api.app) as client:  # `with` runs lifespan, which runs init_db
        data["client"] = client
        yield data

    cfg.get_settings.cache_clear()
    tenant_auth.reset_store_cache()
    tenant_context.current_tenant.set(None)
    api._RL.clear()
    db_engine._engine = None
    db_engine._session_factory = None


def _headers(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _list(client: TestClient, key: str, query: str = "") -> list[dict]:
    response = client.get(f"/api/investigations{query}", headers=_headers(key))
    assert response.status_code == 200, response.text
    return response.json()["investigations"]


def _record(key: str, thread_id: str, query: str, tokens: int = 100) -> None:
    """Record history under `key`'s org, the way a finished investigation does."""

    async def run() -> None:
        resolved = await tenant_auth.resolve(key)
        assert resolved is not None
        token = tenant_context.set_tenant(resolved[0])
        try:
            await investigation_history.record(
                thread_id,
                query,
                status="completed",
                summary="root cause",
                tokens_used=tokens,
            )
        finally:
            tenant_context.reset_tenant(token)

    asyncio.run(run())


def test_history_lists_what_was_recorded(history):
    client, key = history["client"], history["a"]

    assert _list(client, key) == []

    _record(key, "thread-1", "why is checkout slow?")

    listed = _list(client, key)
    assert len(listed) == 1
    assert listed[0]["thread_id"] == "thread-1"
    assert listed[0]["query"] == "why is checkout slow?"
    assert listed[0]["status"] == "completed"
    assert listed[0]["tokens_used"] == 100
    assert listed[0]["completed_at"]


def test_history_does_not_cross_tenants(history):
    client, a, b = history["client"], history["a"], history["b"]

    # The same thread id under both orgs: the unique index is (org_id, thread_id),
    # so this must produce two rows, one visible to each.
    _record(a, "shared-id", "a's investigation")
    _record(b, "shared-id", "b's investigation")

    assert [r["query"] for r in _list(client, a)] == ["a's investigation"]
    assert [r["query"] for r in _list(client, b)] == ["b's investigation"]


def test_repeat_turns_update_one_row(history):
    client, key = history["client"], history["a"]

    _record(key, "thread-1", "first turn")
    _record(key, "thread-1", "second turn")

    rows = _list(client, key)
    assert len(rows) == 1
    # Tokens accumulate across the investigation rather than being overwritten
    # by whichever turn happened to finish last.
    assert rows[0]["tokens_used"] == 200


def test_status_filter(history):
    client, key = history["client"], history["a"]

    _record(key, "done-1", "completed one")

    assert len(_list(client, key, "?status=completed")) == 1
    assert _list(client, key, "?status=error") == []
