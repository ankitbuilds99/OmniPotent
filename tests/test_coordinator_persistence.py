"""Coordinator DAG executions are durable, pollable mid-run, and org-scoped.

Three distinct defects are covered here, all of which the module-level
`_active_executions` dict had:

  * the record was written only after `executor.execute()` returned, so a poll
    during the run — the only time anyone polls — got 404;
  * the caller was never told the execution id, so even after the run the only
    way to find it was to list everything;
  * lookups were unscoped, so any org could read any execution by id.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from app import config as cfg
from app import runtime
from app.api import main as api
from app.coordinator import store
from app.coordinator.dag_executor import (
    DAGExecution,
    DAGExecutor,
    ExecutionState,
    RetryPolicy,
    TaskDAG,
    TaskNode,
)
from app.database.engine import close_db


@pytest.fixture(autouse=True)
def db(tmp_path, monkeypatch):
    """A database per test, under tmp_path.

    Autouse because the store-level tests below would otherwise write to the
    repo's own data directory. The engine is a module global, so it is disposed
    on the way in as well as out — otherwise a test inherits the previous test's
    engine, still pointing at *that* test's tmp_path.
    """
    monkeypatch.setenv("COPILOT_STATE_DIR", str(tmp_path))
    cfg.get_settings.cache_clear()
    asyncio.run(close_db())
    yield
    asyncio.run(close_db())
    cfg.get_settings.cache_clear()


@pytest.fixture
def client(db):
    api._RL.clear()
    runtime.reset()
    yield TestClient(api.app)
    # Closed here too: TestClient serves on its own event loop, and aiosqlite
    # connections are bound to the loop that created them.
    asyncio.run(close_db())
    api._RL.clear()
    runtime.reset()


def _as_org(monkeypatch, org_id: str) -> None:
    """Make every request in this test resolve to `org_id`.

    Patched on the resolver rather than the contextvar because TestClient runs
    the app on its own event loop, where a contextvar set here is not visible.
    """
    monkeypatch.setattr("app.coordinator.api_router.storage_org_id", lambda: org_id)


def _dag_body(dag_id: str = "d1") -> dict:
    return {
        "id": dag_id,
        "name": "test dag",
        "tasks": [
            {"id": "t1", "name": "collect", "handler": "collect_logs"},
            {
                "id": "t2",
                "name": "hypothesize",
                "handler": "generate_hypotheses",
                "depends_on": ["t1"],
            },
        ],
    }


def _seed(org_id: str, execution_id: str, state: ExecutionState) -> None:
    """Write one execution row directly, for the read paths."""
    asyncio.run(
        store.save_execution(
            org_id,
            DAGExecution(id=execution_id, dag_id="d1", state=state),
        )
    )
    # aiosqlite connections are bound to the loop that made them; the engine has
    # to be closed before TestClient serves on its own loop.
    asyncio.run(close_db())


def test_execution_is_not_held_in_a_module_dict():
    from app.coordinator import api_router

    assert not hasattr(api_router, "_active_executions"), (
        "app/coordinator/api_router.py has a module-level _active_executions "
        "again; executions must go through app/coordinator/store.py."
    )


def test_execute_returns_the_execution_id(client):
    started = client.post(
        "/api/coordinator/dags/d1/execute",
        json={"dag_input": _dag_body(), "execute_input": {"context": {}}},
    )
    # The body is two models; FastAPI takes them as named keys.
    assert started.status_code == 200, started.text
    body = started.json()
    assert body["status"] == "started"
    assert body["execution_id"].startswith("exec_")


def test_the_execution_is_findable_after_the_background_run(client):
    started = client.post(
        "/api/coordinator/dags/d1/execute",
        json={"dag_input": _dag_body(), "execute_input": {"context": {}}},
    )
    execution_id = started.json()["execution_id"]

    # TestClient runs BackgroundTasks before returning, so by here the run is
    # done. Under the old dict this worked too — the point of the next test is
    # that it also works *during* the run.
    got = client.get(f"/api/coordinator/executions/{execution_id}")
    assert got.status_code == 200, got.text
    assert got.json()["id"] == execution_id
    assert got.json()["state"] in {"completed", "failed"}


def test_a_running_execution_is_pollable(client):
    """The record exists while the DAG runs, not only once it has finished."""
    _seed("default", "exec_running", ExecutionState.RUNNING)

    got = client.get("/api/coordinator/executions/exec_running")
    assert got.status_code == 200, got.text
    assert got.json()["state"] == "running"
    # Not yet finished, so no completion timestamp is invented.
    assert got.json()["completed_at"] is None


async def test_the_record_exists_while_the_dag_is_still_running():
    """The heart of the fix, observed against a run that has not finished.

    The seeded test above proves the read path; this proves the *ordering*. A
    handler is held open, and the store is queried while it is still held: the
    row has to be there and say "running". With the old code nothing was written
    until `execute()` returned, so this is the assertion that fails against it.
    """
    from app.coordinator.api_router import _run_and_record

    released = asyncio.Event()

    async def blocking_handler(task, input_data):
        await released.wait()
        return {"ok": True}

    executor = DAGExecutor(handlers={}, redis_client=None, max_parallel=1)
    executor.register_handler("hold", blocking_handler)

    dag = TaskDAG("d-hold", "held")
    dag.add_task(TaskNode(id="t1", name="t1", handler="hold"))

    run = asyncio.create_task(
        _run_and_record(executor, dag, "default", "exec_inflight")
    )
    try:
        mid_run = None
        for _ in range(200):  # bounded; the row is written before execute()
            await asyncio.sleep(0.01)
            rows = await store.list_executions("default")
            if rows:
                mid_run = rows[0]
                break

        assert mid_run is not None, "no row was written before the DAG started"
        assert mid_run["state"] == "running"
        assert mid_run["completed_at"] is None
        assert not run.done(), "the DAG finished early; the assertion above is vacuous"
    finally:
        released.set()
        await run

    finished = await store.list_executions("default")
    assert finished[0]["state"] == "completed"
    assert finished[0]["completed_at"] is not None


async def test_a_crashing_dag_is_recorded_as_failed():
    """`execute()` re-raises, and a background task has no caller to catch it."""
    from app.coordinator.api_router import _run_and_record

    async def exploding_handler(task, input_data):
        raise RuntimeError("boom")

    executor = DAGExecutor(handlers={}, redis_client=None, max_parallel=1)
    executor.register_handler("boom", exploding_handler)

    dag = TaskDAG("d-boom", "boom")
    # One attempt, no backoff: the default policy would retry three times with
    # exponential sleeps and turn this into a slow test.
    dag.add_task(
        TaskNode(
            id="t1",
            name="t1",
            handler="boom",
            retry_policy=RetryPolicy(max_attempts=1, initial_delay_seconds=0.0),
        )
    )

    await _run_and_record(executor, dag, "default", "exec_boom")

    rows = await store.list_executions("default")
    assert len(rows) == 1
    assert rows[0]["state"] == "failed"
    assert rows[0]["completed_at"] is not None


def test_execution_survives_the_client_that_started_it(client):
    started = client.post(
        "/api/coordinator/dags/d1/execute",
        json={"dag_input": _dag_body(), "execute_input": {"context": {}}},
    )
    execution_id = started.json()["execution_id"]

    with TestClient(api.app) as second:
        assert second.get(f"/api/coordinator/executions/{execution_id}").status_code == 200


def test_investigation_returns_and_stores_an_execution_id(client):
    # P3 deliberately: create_investigation_dag marks "execute_fix" as
    # requires_approval for P1/P2, and TestClient runs BackgroundTasks inline, so
    # a P1 here would block this test on the approval future until its
    # approval_timeout_minutes elapsed. The approval path is the live-object
    # state tracked in tests/test_single_replica_until_state_externalised.py.
    started = client.post(
        "/api/coordinator/investigations",
        json={"incident_id": "INC-9", "affected_services": ["api"], "severity": "P3"},
    )
    assert started.status_code == 200, started.text
    execution_id = started.json()["execution_id"]

    got = client.get(f"/api/coordinator/executions/{execution_id}")
    assert got.status_code == 200
    assert got.json()["dag_id"] == started.json()["dag_id"]


def test_one_org_cannot_read_another_orgs_execution(client, monkeypatch):
    _seed("org-a", "exec_secret", ExecutionState.COMPLETED)

    _as_org(monkeypatch, "org-b")
    assert client.get("/api/coordinator/executions/exec_secret").status_code == 404
    assert client.get("/api/coordinator/executions").json()["count"] == 0

    _as_org(monkeypatch, "org-a")
    assert client.get("/api/coordinator/executions/exec_secret").status_code == 200


def test_one_org_cannot_delete_another_orgs_execution(client, monkeypatch):
    _seed("org-a", "exec_victim", ExecutionState.COMPLETED)

    _as_org(monkeypatch, "org-b")
    assert client.delete("/api/coordinator/executions/exec_victim").json() == {
        "deleted": False
    }

    _as_org(monkeypatch, "org-a")
    assert client.get("/api/coordinator/executions/exec_victim").status_code == 200
    assert client.delete("/api/coordinator/executions/exec_victim").json() == {
        "deleted": True
    }
    assert client.get("/api/coordinator/executions/exec_victim").status_code == 404


def test_unknown_execution_is_404(client):
    assert client.get("/api/coordinator/executions/nope").status_code == 404


def test_listing_is_newest_first(client):
    for i, name in enumerate(["exec_1", "exec_2", "exec_3"]):
        _seed("default", name, ExecutionState.COMPLETED)

    body = client.get("/api/coordinator/executions").json()
    assert body["count"] == 3
    # started_at is server_default now(); ordering is desc, so the last written
    # sorts no later than the first.
    ids = [e["id"] for e in body["executions"]]
    assert set(ids) == {"exec_1", "exec_2", "exec_3"}
    stamps = [e["started_at"] for e in body["executions"]]
    assert stamps == sorted(stamps, reverse=True)


def test_listing_timestamps_carry_a_utc_offset(client):
    """SQLite has no timezone type, so UTC has to be re-attached on read."""
    _seed("default", "exec_tz", ExecutionState.COMPLETED)
    body = client.get("/api/coordinator/executions").json()
    started = body["executions"][0]["started_at"]
    assert started.endswith("+00:00"), started


def test_listing_rejects_an_out_of_range_limit(client):
    for bad in (0, -1, 501):
        assert client.get(f"/api/coordinator/executions?limit={bad}").status_code == 422


@pytest.mark.parametrize(
    "state", [ExecutionState.COMPLETED, ExecutionState.FAILED, ExecutionState.CANCELLED]
)
def test_terminal_states_get_a_completion_timestamp(state):
    asyncio.run(store.save_execution("default", DAGExecution(id="e", dag_id="d", state=state)))
    rows = asyncio.run(store.list_executions("default"))
    asyncio.run(close_db())
    assert rows[0]["completed_at"] is not None


def test_a_running_execution_has_no_completion_timestamp():
    asyncio.run(
        store.save_execution(
            "default", DAGExecution(id="e", dag_id="d", state=ExecutionState.RUNNING)
        )
    )
    rows = asyncio.run(store.list_executions("default"))
    asyncio.run(close_db())
    assert rows[0]["completed_at"] is None


def test_saving_twice_updates_rather_than_duplicates():
    """A run writes once at start and once at the end, under one primary key."""
    asyncio.run(
        store.save_execution(
            "default", DAGExecution(id="e", dag_id="d", state=ExecutionState.RUNNING)
        )
    )
    asyncio.run(
        store.save_execution(
            "default",
            DAGExecution(id="e", dag_id="d", state=ExecutionState.COMPLETED, current_level=3),
        )
    )
    rows = asyncio.run(store.list_executions("default"))
    asyncio.run(close_db())
    assert len(rows) == 1
    assert rows[0]["state"] == "completed"
    assert rows[0]["current_level"] == 3


def test_two_orgs_may_use_the_same_execution_id():
    for org in ("org-a", "org-b"):
        asyncio.run(
            store.save_execution(
                org, DAGExecution(id="same", dag_id=f"dag-{org}", state=ExecutionState.RUNNING)
            )
        )
    a = asyncio.run(store.load_execution("org-a", "same"))
    b = asyncio.run(store.load_execution("org-b", "same"))
    asyncio.run(close_db())
    assert a is not None and b is not None
    assert a.dag_id == "dag-org-a"
    assert b.dag_id == "dag-org-b"


def test_mark_failed_leaves_the_payload_alone():
    """Recording a crash must not invent task results nobody observed."""
    execution = DAGExecution(
        id="e", dag_id="d", state=ExecutionState.RUNNING, initial_context={"k": "v"}
    )
    asyncio.run(store.save_execution("default", execution))
    assert asyncio.run(store.mark_failed("default", "e")) is True

    loaded = asyncio.run(store.load_execution("default", "e"))
    rows = asyncio.run(store.list_executions("default"))
    asyncio.run(close_db())
    assert rows[0]["state"] == "failed"
    assert rows[0]["completed_at"] is not None
    # The stored payload is untouched: still the pre-run snapshot, not a
    # fabricated set of failed tasks.
    assert loaded is not None
    assert loaded.initial_context == {"k": "v"}
    assert loaded.task_executions == {}


def test_mark_failed_reports_a_missing_row():
    assert asyncio.run(store.mark_failed("default", "nope")) is False
    asyncio.run(close_db())


def test_round_trip_preserves_task_results():
    """serialize/deserialize keeps what to_dict drops — outputs and context."""
    dag = TaskDAG("d", "d")
    dag.add_task(TaskNode(id="t1", name="t1", handler="collect_logs"))

    execution = DAGExecution(
        id="e",
        dag_id="d",
        state=ExecutionState.COMPLETED,
        execution_levels=[["t1"]],
        current_level=0,
        initial_context={"incident": "INC-1"},
        accumulated_context={"t1": {"lines": 12}},
    )
    asyncio.run(store.save_execution("default", execution))
    loaded = asyncio.run(store.load_execution("default", "e"))
    asyncio.run(close_db())

    assert loaded is not None
    assert loaded.accumulated_context == {"t1": {"lines": 12}}
    assert loaded.execution_levels == [["t1"]]
    assert loaded.state is ExecutionState.COMPLETED


def test_clear_is_scoped_to_one_org():
    for org in ("org-a", "org-b"):
        asyncio.run(
            store.save_execution(
                org, DAGExecution(id="e", dag_id="d", state=ExecutionState.COMPLETED)
            )
        )
    asyncio.run(store.clear_executions("org-a"))
    remaining_a = asyncio.run(store.list_executions("org-a"))
    remaining_b = asyncio.run(store.list_executions("org-b"))
    asyncio.run(close_db())
    assert remaining_a == []
    assert len(remaining_b) == 1
