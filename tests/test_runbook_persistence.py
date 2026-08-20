"""Runbooks are durable, org-scoped, and actually executed.

Three separate defects are covered here, because the runbook API had all of
them at once and fixing any one alone would have left it lying about something:

  * `_runbooks` and `_executions` were module-level dicts, so a runbook an
    operator authored during an incident was visible only to the replica that
    served the POST and gone at the next restart;
  * `POST /{id}/execute` accepted a `BackgroundTasks` argument and never
    scheduled anything on it. It recorded status "running" and nothing ever
    advanced it — no step of any runbook was ever run through this API, and
    `approve` flipped the same field with the same non-effect;
  * `GET /executions` was declared after `GET /{runbook_id}`, so every request
    to it was matched as a runbook lookup and answered 404.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app import config as cfg
from app import runtime
from app.api import main as api
from app.database.engine import close_db
from app.runbooks import router as rb_router
from app.runbooks import store
from app.runbooks.executor import RunbookExecutor
from app.runbooks.models import (
    ExecutionMode,
    Runbook,
    RunbookExecution,
    RunbookStep,
    StepExecution,
    StepStatus,
    StepType,
)

WAIT_STEP = {"id": "w1", "name": "wait a moment", "step_type": "wait", "params": {"seconds": 0}}
CMD_STEP = {"id": "c1", "name": "run a command", "step_type": "command", "action": "echo hi"}


@pytest.fixture(autouse=True)
def db(tmp_path, monkeypatch):
    """A database per test, under tmp_path.

    Autouse because the store-level tests would otherwise write to the repo's
    own data directory. The engine is a module global, so it is disposed on the
    way in as well as out — otherwise a test inherits the previous test's
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


def _as_org(monkeypatch, org_id: str) -> None:
    """Serve subsequent requests as `org_id`.

    Patches the router's `storage_org_id` rather than setting the contextvar:
    TestClient runs the app on its own event loop, so a value set here would not
    be visible inside the request.
    """
    monkeypatch.setattr(rb_router, "storage_org_id", lambda: org_id)


def _create(client, **overrides) -> dict:
    body = {
        "name": "Test runbook",
        "description": "created by a test",
        "service": "*",
        "trigger": "manual",
        "execution_mode": "auto_approved",
        "steps": [WAIT_STEP],
        "labels": {},
    }
    body.update(overrides)
    resp = client.post("/api/runbooks", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _run(client, runbook_id: str) -> dict:
    """Execute a runbook and return the finished execution record.

    TestClient runs BackgroundTasks inline before returning, so by the time the
    POST response is in hand the run has already finished.
    """
    started = client.post(f"/api/runbooks/{runbook_id}/execute", json={})
    assert started.status_code == 200, started.text
    execution_id = started.json()["id"]
    final = client.get(f"/api/runbooks/executions/{execution_id}")
    assert final.status_code == 200, final.text
    return final.json()


# ---------------------------------------------------------------------------
# The routing defect
# ---------------------------------------------------------------------------


def test_the_executions_listing_is_reachable_at_all(client):
    """Regression: `/runbooks/executions` used to be matched as a runbook id.

    `GET /{runbook_id}` was declared first, so FastAPI — which matches in
    declaration order — resolved this path with runbook_id="executions" and
    answered 404 "Runbook not found". The listing endpoint had never once
    returned a listing.
    """
    resp = client.get("/api/runbooks/executions")
    assert resp.status_code == 200, resp.text
    assert isinstance(resp.json(), list)


def test_literal_execution_routes_are_declared_before_the_parameterised_one():
    """Pin the ordering, not just its current effect.

    Re-adding a route above these would silently reintroduce the bug, and the
    only symptom is a 404 that looks like an ordinary missing runbook.
    """
    paths = [r.path for r in rb_router.router.routes]
    assert paths.index("/runbooks/executions") < paths.index("/runbooks/{runbook_id}")
    assert paths.index("/runbooks/executions/{execution_id}") < paths.index(
        "/runbooks/{runbook_id}"
    )


def test_an_unknown_runbook_id_still_404s(client):
    assert client.get("/api/runbooks/nope").status_code == 404
    assert client.delete("/api/runbooks/nope").status_code == 404
    assert client.post("/api/runbooks/nope/execute", json={}).status_code == 404
    assert client.get("/api/runbooks/executions/nope").status_code == 404
    assert client.delete("/api/runbooks/executions/nope").status_code == 404


# ---------------------------------------------------------------------------
# Steps really run
# ---------------------------------------------------------------------------


def test_executing_a_runbook_actually_runs_its_steps(client):
    """The heart of the fix: before this, no step ever ran.

    The old route created a record with status "running" and returned. Nothing
    was scheduled, so the record stayed "running" forever and its `steps` list
    stayed empty — an operator watching it would conclude the runbook was still
    working on their incident.
    """
    rb = _create(client)
    execution = _run(client, rb["id"])

    assert execution["status"] == "success"
    assert [s["step_id"] for s in execution["steps"]] == ["w1"]
    assert execution["steps"][0]["status"] == "success"
    assert execution["completed_at"] is not None


def test_a_step_with_no_backend_fails_rather_than_claiming_success(client):
    """Fail-closed, end to end through the API.

    No command runner is wired into the executor. Reporting success here would
    tell a responder that a mitigation ran when nothing was spawned — the one
    failure mode worse than the step erroring out.
    """
    rb = _create(client, steps=[CMD_STEP])
    execution = _run(client, rb["id"])

    assert execution["status"] == "failed"
    step = execution["steps"][0]
    assert step["status"] == "failed"
    assert "refusing to report success" in step["error"]
    assert step["output"] == ""


def test_the_execution_record_exists_before_the_run_finishes(client):
    """The id returned by execute resolves immediately, not only at the end.

    Written before the run is scheduled, so a caller that polls the id it was
    just handed gets a record rather than the 404 that the coordinator's old
    store produced for the entire duration of a run.
    """
    rb = _create(client)
    started = client.post(f"/api/runbooks/{rb['id']}/execute", json={})
    assert started.status_code == 200
    assert started.json()["status"] == "pending"
    assert started.json()["completed_at"] is None


async def test_progress_is_persisted_after_each_step_not_only_at_the_end():
    """A run that reports only its final state is indistinguishable from a hung one.

    Observed directly against the executor: the hook fires once per completed
    step, so a caller polling a long runbook sees it advance.
    """
    seen: list[list[str]] = []

    async def record(execution: RunbookExecution) -> None:
        seen.append([s.step_id for s in execution.steps])

    runbook = Runbook(
        id="rb-multi",
        name="multi",
        description="",
        service="*",
        trigger="manual",
        execution_mode=ExecutionMode.AUTO_APPROVED,
        steps=[
            RunbookStep(
                id=f"s{i}",
                name=f"s{i}",
                description="",
                step_type=StepType.WAIT,
                action="",
                params={"seconds": 0},
            )
            for i in range(3)
        ],
    )
    executor = RunbookExecutor()
    await executor.execute(
        runbook.id, context={}, runbook=runbook, execution_id="e1", on_progress=record
    )

    # One call per step, plus the final one; each sees one more step than the last.
    assert seen[0] == ["s0"]
    assert seen[1] == ["s0", "s1"]
    assert seen[-1] == ["s0", "s1", "s2"]


async def test_a_failing_progress_hook_does_not_un_run_the_steps():
    """The hook is bookkeeping; a step that really ran must still be reported."""

    async def explode(execution: RunbookExecution) -> None:
        raise RuntimeError("database went away")

    runbook = Runbook(
        id="rb-h",
        name="h",
        description="",
        service="*",
        trigger="manual",
        execution_mode=ExecutionMode.AUTO_APPROVED,
        steps=[
            RunbookStep(
                id="s0",
                name="s0",
                description="",
                step_type=StepType.WAIT,
                action="",
                params={"seconds": 0},
            )
        ],
    )
    result = await RunbookExecutor().execute(
        runbook.id, context={}, runbook=runbook, execution_id="e1", on_progress=explode
    )
    assert result.status == StepStatus.SUCCESS


# ---------------------------------------------------------------------------
# Approval gates
# ---------------------------------------------------------------------------


def test_a_runbook_needing_approval_pauses_and_then_resumes(client):
    """The approval gate holds the run, and approving it runs the remaining steps.

    Previously `approve` set a status field to "running" and returned; no step
    ever followed. Here the second step must actually appear in the record.
    """
    rb = _create(
        client,
        execution_mode="requires_approval",
        steps=[
            {**WAIT_STEP, "id": "s0", "requires_approval": True},
            {**WAIT_STEP, "id": "s1"},
        ],
    )
    execution_id = client.post(f"/api/runbooks/{rb['id']}/execute", json={}).json()["id"]

    paused = client.get(f"/api/runbooks/executions/{execution_id}").json()
    assert paused["status"] == "waiting_approval"
    assert paused["completed_at"] is None

    approved = client.post(f"/api/runbooks/executions/{execution_id}/approve")
    assert approved.status_code == 200

    final = client.get(f"/api/runbooks/executions/{execution_id}").json()
    assert final["status"] == "success"
    assert [s["step_id"] for s in final["steps"]] == ["s0", "s1"], (
        "both steps must run, and the waiting placeholder must not linger"
    )


def test_rejecting_records_skipped_not_failed(client):
    """A human declining is not the runbook going wrong.

    The old route wrote FAILED, which would have inflated the failure rate of
    every runbook anyone ever thought better of.
    """
    rb = _create(
        client,
        execution_mode="requires_approval",
        steps=[{**WAIT_STEP, "id": "s0", "requires_approval": True}],
    )
    execution_id = client.post(f"/api/runbooks/{rb['id']}/execute", json={}).json()["id"]

    rejected = client.post(f"/api/runbooks/executions/{execution_id}/reject")
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "skipped"


def test_approving_a_run_that_is_not_paused_is_rejected(client):
    rb = _create(client)
    execution = _run(client, rb["id"])
    assert execution["status"] == "success"

    assert client.post(f"/api/runbooks/executions/{execution['id']}/approve").status_code == 400
    assert client.post(f"/api/runbooks/executions/{execution['id']}/reject").status_code == 400


async def test_resuming_at_a_step_that_no_longer_exists_refuses(client):
    """An edit between the pause and the approval must not run a guessed step.

    Resuming by position would run whatever now occupies that offset, which for
    a runbook full of scale and drain actions is how the wrong cluster gets
    touched.
    """
    runbook = Runbook(
        id="rb-edited",
        name="edited",
        description="",
        service="*",
        trigger="manual",
        execution_mode=ExecutionMode.REQUIRES_APPROVAL,
        steps=[
            RunbookStep(
                id="new-step",
                name="new",
                description="",
                step_type=StepType.WAIT,
                action="",
                params={"seconds": 0},
            )
        ],
    )
    execution = RunbookExecution(
        id="e-old",
        runbook_id=runbook.id,
        runbook_name=runbook.name,
        status=StepStatus.WAITING_APPROVAL,
        triggered_by="manual",
        started_at=datetime.now(timezone.utc),
    )
    assert await RunbookExecutor().resume(execution, runbook, "deleted-step") is None


# ---------------------------------------------------------------------------
# Durability
# ---------------------------------------------------------------------------


def test_a_runbook_outlives_the_process_that_created_it(db):
    """The point of the whole slice: author on one client, read on another."""
    with TestClient(api.app) as first:
        rb = _create(first)
    asyncio.run(close_db())

    with TestClient(api.app) as second:
        found = second.get(f"/api/runbooks/{rb['id']}")
    asyncio.run(close_db())

    assert found.status_code == 200
    assert found.json()["name"] == "Test runbook"


def test_an_execution_outlives_the_process_that_ran_it(db):
    with TestClient(api.app) as first:
        rb = _create(first)
        execution = _run(first, rb["id"])
    asyncio.run(close_db())

    with TestClient(api.app) as second:
        found = second.get(f"/api/runbooks/executions/{execution['id']}")
    asyncio.run(close_db())

    assert found.status_code == 200
    assert found.json()["status"] == "success"


def test_the_router_keeps_no_module_level_stores():
    """Guard against a dict creeping back in alongside the table."""
    assert not hasattr(rb_router, "_runbooks")
    assert not hasattr(rb_router, "_executions")


# ---------------------------------------------------------------------------
# Tenant scoping
# ---------------------------------------------------------------------------


def test_one_org_cannot_read_or_delete_another_orgs_runbook(client, monkeypatch):
    _as_org(monkeypatch, "org-a")
    rb = _create(client, name="Org A private")

    _as_org(monkeypatch, "org-b")
    assert client.get(f"/api/runbooks/{rb['id']}").status_code == 404
    assert client.delete(f"/api/runbooks/{rb['id']}").status_code == 404
    assert client.post(f"/api/runbooks/{rb['id']}/execute", json={}).status_code == 404

    _as_org(monkeypatch, "org-a")
    assert client.get(f"/api/runbooks/{rb['id']}").status_code == 200, (
        "org A's own runbook must survive org B's delete attempt"
    )


def test_one_org_cannot_read_approve_or_delete_another_orgs_execution(client, monkeypatch):
    _as_org(monkeypatch, "org-a")
    rb = _create(client)
    execution = _run(client, rb["id"])

    _as_org(monkeypatch, "org-b")
    assert client.get(f"/api/runbooks/executions/{execution['id']}").status_code == 404
    assert client.post(f"/api/runbooks/executions/{execution['id']}/approve").status_code == 404
    assert client.post(f"/api/runbooks/executions/{execution['id']}/reject").status_code == 404
    assert client.delete(f"/api/runbooks/executions/{execution['id']}").status_code == 404
    assert client.get("/api/runbooks/executions").json() == []


def test_builtins_are_seeded_per_org_so_an_edit_is_not_shared(client, monkeypatch):
    """One tenant deleting a shipped runbook must not remove everyone else's.

    The old module dict held one copy of DEFAULT_RUNBOOKS for the whole process.
    """
    _as_org(monkeypatch, "org-a")
    before = {rb["id"] for rb in client.get("/api/runbooks").json()}
    assert before, "the shipped runbooks should be visible"
    victim = sorted(before)[0]
    assert client.delete(f"/api/runbooks/{victim}").status_code == 200

    _as_org(monkeypatch, "org-b")
    assert victim in {rb["id"] for rb in client.get("/api/runbooks").json()}


def test_the_same_runbook_id_can_exist_in_two_orgs(db):
    """Composite key: id alone is not identity."""
    rb_a = Runbook(
        id="shared-id",
        name="A's version",
        description="",
        service="*",
        trigger="manual",
        execution_mode=ExecutionMode.AUTO_APPROVED,
    )
    rb_b = Runbook(
        id="shared-id",
        name="B's version",
        description="",
        service="*",
        trigger="manual",
        execution_mode=ExecutionMode.AUTO_APPROVED,
    )

    async def scenario():
        await store.save_runbook("org-a", rb_a)
        await store.save_runbook("org-b", rb_b)
        a = await store.load_runbook("org-a", "shared-id")
        b = await store.load_runbook("org-b", "shared-id")
        return a, b

    a, b = asyncio.run(scenario())
    asyncio.run(close_db())
    assert a is not None and b is not None
    assert (a.name, b.name) == ("A's version", "B's version")


# ---------------------------------------------------------------------------
# Filtering, validation, and the store's own contract
# ---------------------------------------------------------------------------


def test_listing_filters_by_service_trigger_and_enabled(client):
    _create(client, name="payments only", service="payments", trigger="alert")
    _create(client, name="everywhere", service="*", trigger="manual")

    payments = client.get("/api/runbooks", params={"service": "payments"}).json()
    names = {rb["name"] for rb in payments}
    assert "payments only" in names
    assert "everywhere" in names, "a wildcard runbook applies to every service"

    other = {rb["name"] for rb in client.get("/api/runbooks", params={"service": "search"}).json()}
    assert "payments only" not in other

    by_trigger = {rb["name"] for rb in client.get("/api/runbooks", params={"trigger": "alert"}).json()}
    assert by_trigger == {"payments only"}


def test_execution_listing_filters_and_validates_its_limit(client):
    rb = _create(client)
    execution = _run(client, rb["id"])

    scoped = client.get("/api/runbooks/executions", params={"runbook_id": rb["id"]}).json()
    assert [e["id"] for e in scoped] == [execution["id"]]
    assert client.get(
        "/api/runbooks/executions", params={"runbook_id": "other"}
    ).json() == []
    assert client.get(
        "/api/runbooks/executions", params={"status": "failed"}
    ).json() == []

    for bad in (0, -1, 501):
        assert client.get(
            "/api/runbooks/executions", params={"limit": bad}
        ).status_code == 422, f"limit={bad} should be rejected"


def test_unknown_enum_values_are_422_not_500(client):
    """`ExecutionMode("automatic")` used to raise ValueError out of the route."""
    bad_mode = client.post(
        "/api/runbooks",
        json={"name": "n", "description": "d", "execution_mode": "automatic", "steps": []},
    )
    assert bad_mode.status_code == 422
    assert "automatic" in bad_mode.json()["detail"]

    bad_step = client.post(
        "/api/runbooks",
        json={
            "name": "n",
            "description": "d",
            "execution_mode": "auto_approved",
            "steps": [{"id": "s", "name": "s", "step_type": "telepathy"}],
        },
    )
    assert bad_step.status_code == 422
    assert "telepathy" in bad_step.json()["detail"]


def test_delete_reports_what_it_actually_did(client):
    rb = _create(client)
    assert client.delete(f"/api/runbooks/{rb['id']}").json()["deleted"] is True
    assert client.delete(f"/api/runbooks/{rb['id']}").status_code == 404


def test_timestamps_come_back_with_an_offset(client):
    """SQLite drops the timezone; a naive string reads as local time downstream."""
    rb = _create(client)
    execution = _run(client, rb["id"])
    assert execution["started_at"].endswith("+00:00")
    assert execution["completed_at"].endswith("+00:00")


def test_mark_failed_records_the_reason_without_inventing_steps(db):
    """An exception carries no per-step detail; writing one would be fabrication."""

    async def scenario():
        execution = RunbookExecution(
            id="e-crash",
            runbook_id="rb-1",
            runbook_name="rb",
            status=StepStatus.RUNNING,
            triggered_by="manual",
            started_at=datetime.now(timezone.utc),
            steps=[
                StepExecution(
                    step_id="s0",
                    step_name="s0",
                    status=StepStatus.SUCCESS,
                    started_at=datetime.now(timezone.utc),
                    output="did a thing",
                )
            ],
        )
        await store.save_execution("org-a", execution)
        marked = await store.mark_failed("org-a", "e-crash", "connection reset")
        missing = await store.mark_failed("org-a", "no-such-run", "irrelevant")
        return marked, missing, await store.load_execution("org-a", "e-crash")

    marked, missing, reloaded = asyncio.run(scenario())
    asyncio.run(close_db())

    assert marked is True
    assert missing is False, "marking a run that does not exist must not create one"
    assert reloaded is not None
    assert reloaded.status is StepStatus.FAILED
    assert [s.step_id for s in reloaded.steps] == ["s0"]
    assert reloaded.steps[0].status is StepStatus.SUCCESS, (
        "the step that really did succeed must not be rewritten as failed"
    )


def test_an_unreadable_row_does_not_take_out_the_listing(db):
    """One corrupt payload must not hide an operator's other runbooks."""

    async def scenario():
        good = Runbook(
            id="rb-good",
            name="good",
            description="",
            service="*",
            trigger="manual",
            execution_mode=ExecutionMode.AUTO_APPROVED,
        )
        await store.save_runbook("org-a", good)
        await store.save_runbook("org-a", good)  # idempotent update path
        # Corrupt one row's payload behind the store's back.
        from app.database.engine import get_session
        from app.database.models import RunbookDefinition

        async with get_session() as session:
            row = RunbookDefinition(
                id="rb-bad",
                org_id="org-a",
                name="bad",
                service="*",
                trigger="manual",
                definition={"steps": "not a list"},
            )
            session.add(row)
            await session.commit()
        return await store.list_runbooks("org-a")

    listed = asyncio.run(scenario())
    asyncio.run(close_db())
    assert [rb.id for rb in listed] == ["rb-good"]


# ---------------------------------------------------------------------------
# The out-of-process worker
# ---------------------------------------------------------------------------


def test_the_worker_runs_an_api_authored_runbook_and_the_api_can_read_the_result(client):
    """The worker and the API now share runbook state.

    `execute_runbook_async` used to construct a bare `RunbookExecutor`, which
    loads only DEFAULT_RUNBOOKS — so a runbook authored through the API raised
    ValueError in the worker — and wrote its record to a process-local dict, so
    the `execution_id` it returned 404'd through the API. Both were consequences
    of runbook state being per-process.
    """
    from app.tasks.tasks import execute_runbook_async

    rb = _create(client, name="Worker runbook")

    # `.fn` is the undecorated callable; calling the actor directly would enqueue.
    result = execute_runbook_async.fn(rb["id"], {"service": "payments"}, "scheduler")

    assert result["status"] == "success"
    assert result["runbook_id"] == rb["id"]

    found = client.get(f"/api/runbooks/executions/{result['execution_id']}")
    assert found.status_code == 200, "the API must resolve the worker's execution id"
    assert found.json()["triggered_by"] == "scheduler"
    assert [s["step_id"] for s in found.json()["steps"]] == ["w1"]


def test_the_worker_refuses_a_runbook_the_org_does_not_have(client, monkeypatch):
    """Scoped in the worker too, where there is no request to inherit a tenant from."""
    from app.tasks.tasks import execute_runbook_async

    _as_org(monkeypatch, "org-a")
    rb = _create(client, name="Org A only")

    with pytest.raises(ValueError, match="not found"):
        execute_runbook_async.fn(rb["id"], {}, "scheduler", org_id="org-b")
