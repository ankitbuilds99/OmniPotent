"""Automation that runs unattended, and reports what it actually did.

Eight defects across the runbook executor, the DAG coordinator, the runbooks
MCP server and the planning layer are pinned here:

1. A step marked `requires_approval` ran without one unless the *runbook* was
   also in `requires_approval` mode. On a `manual` or `auto_approved` runbook —
   both of which start running the moment the route is called — the step flag
   was silently ignored, so a `kubectl drain` declared as needing a human did
   not get one.
2. Both coordinator execute routes registered the execution only after it had
   finished, and returned no id. A DAG parked on an approval — exactly when you
   need to find it — was invisible, and there was nothing to poll with.
3. A DAG with a duplicate task id, an unknown priority or an unknown handler was
   accepted and then failed in the background, where the caller never saw it.
   `resume_from` without Redis behaved the same way.
4. Approvals were matched on task id alone. Two concurrent runs of one DAG both
   park on `restart_service`, and approving it in the run you were looking at
   released the other one too.
5. `TaskExecution.to_dict()` dropped `output`, so a scale-up that only simulated
   serialized identically to one that reached a cluster.
6. The runbooks MCP server read an in-memory object seeded with the built-in
   runbooks, so the agent could not see a customer's runbook and its executions
   left no audit row.
7. Goal decomposition reported `confidence=0.85` and "using template approach"
   for the five of eight categories that have no template, where it had in fact
   emitted a four-step placeholder that never read the goal.
8. Every default planning tool returned a constant success dict — "analyzed",
   "executed", `"result": "success"` — for work that had not happened, so a
   graph of them came back complete with every task green.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

import app.api.main as api
import app.config as cfg
from app import tenant_context
from app.coordinator import api_router as coord_api
from app.coordinator import store as coord_store
from app.coordinator.dag_executor import (
    DAGExecution,
    DAGExecutor,
    ExecutionState,
    TaskDAG,
    TaskExecution,
    TaskNode,
)
from app.database import engine as db_engine
from app.planning.execution_engine import ExecutionEngine, create_default_tools
from app.planning.goal_decomposer import Goal, GoalCategory, GoalDecomposer


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("COPILOT_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/auto.db")
    monkeypatch.delenv("COPILOT_MULTI_TENANT", raising=False)
    monkeypatch.delenv("COPILOT_API_TOKEN", raising=False)
    monkeypatch.delenv("KUBE_CONFIG_PATH", raising=False)
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
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


@pytest.fixture
def coordinator_db(tmp_path, monkeypatch):
    """A private database for the tests that drive the coordinator store direct.

    Executions are rows now, not a module dict, so these tests need somewhere to
    put them — and somewhere that is not shared with the next test.
    """
    monkeypatch.setenv("COPILOT_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/coord.db")
    cfg.get_settings.cache_clear()
    db_engine._engine = None
    db_engine._session_factory = None
    tenant_context.current_tenant.set(None)
    yield
    # Disposed rather than dropped on the floor: aiosqlite connections are bound
    # to the loop that opened them.
    asyncio.run(db_engine.close_db())
    cfg.get_settings.cache_clear()
    tenant_context.current_tenant.set(None)


def _runbook(client, *, mode: str, steps: list[dict]) -> str:
    res = client.post(
        "/api/runbooks",
        json={
            "name": f"Gate check ({mode})",
            "description": "one gated step",
            "service": "api",
            "trigger": "manual",
            "execution_mode": mode,
            "steps": steps,
        },
    )
    assert res.status_code == 200, res.text
    return str(res.json()["id"])


def _wait(step_id: str, *, requires_approval: bool = False) -> dict:
    return {
        "id": step_id,
        "name": step_id,
        "step_type": "wait",
        "action": "sleep",
        "params": {"seconds": 0},
        "requires_approval": requires_approval,
    }


def _execution(client, execution_id: str) -> dict:
    listed = client.get("/api/runbooks/executions")
    assert listed.status_code == 200, listed.text
    match = [e for e in listed.json() if e["id"] == execution_id]
    assert match, f"execution {execution_id} missing from {listed.json()}"
    return dict(match[0])


# --------------------------------------------------------------------------- #
# 1. A step that needs a human gets one, whatever the runbook's mode says
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("mode", ["manual", "auto_approved"])
def test_a_step_needing_approval_stops_even_on_a_runbook_that_auto_runs(client, mode):
    """The gate used to require step *and* runbook to both ask for approval."""
    rb_id = _runbook(
        client,
        mode=mode,
        steps=[_wait("first"), _wait("gated", requires_approval=True), _wait("after")],
    )

    res = client.post(f"/api/runbooks/{rb_id}/execute", json={"service": "api"})
    assert res.status_code == 200, res.text
    execution_id = res.json()["id"]

    parked = _execution(client, execution_id)
    assert parked["status"] == "waiting_approval", parked
    ran = [s["step_id"] for s in parked["steps"] if s["status"] == "success"]
    assert ran == ["first"], f"the gated step ran unattended: {parked['steps']}"
    assert "gated" not in ran
    assert "after" not in ran


@pytest.mark.parametrize("mode", ["manual", "auto_approved"])
def test_approving_the_gated_step_lets_the_rest_of_the_run_finish(client, mode):
    rb_id = _runbook(
        client,
        mode=mode,
        steps=[_wait("first"), _wait("gated", requires_approval=True), _wait("after")],
    )
    execution_id = client.post(
        f"/api/runbooks/{rb_id}/execute", json={"service": "api"}
    ).json()["id"]

    approved = client.post(f"/api/runbooks/executions/{execution_id}/approve")
    assert approved.status_code == 200, approved.text

    done = _execution(client, execution_id)
    assert done["status"] == "success", done
    assert [s["step_id"] for s in done["steps"]] == ["first", "gated", "after"]


def test_a_runbook_with_no_gated_step_still_runs_straight_through(client):
    """The unconditional gate must not stop runs that never asked for one."""
    rb_id = _runbook(client, mode="auto_approved", steps=[_wait("a"), _wait("b")])
    execution_id = client.post(
        f"/api/runbooks/{rb_id}/execute", json={"service": "api"}
    ).json()["id"]

    done = _execution(client, execution_id)
    assert done["status"] == "success", done
    assert [s["status"] for s in done["steps"]] == ["success", "success"]


# --------------------------------------------------------------------------- #
# 2. The coordinator answers with an id, and registers the run before it starts
# --------------------------------------------------------------------------- #
class _HeldBackground:
    """A BackgroundTasks stand-in that collects the task instead of running it.

    TestClient drains background tasks before returning from the request, so
    this is the only way to observe the window the fix is about: between
    "queued" and "finished".
    """

    def __init__(self) -> None:
        self.queued: list = []

    def add_task(self, func, *args, **kwargs) -> None:
        self.queued.append((func, args, kwargs))


def _dag(*tasks: TaskNode) -> TaskDAG:
    dag = TaskDAG("dag_test", "Test DAG")
    for task in tasks:
        dag.add_task(task)
    return dag


def test_an_execution_is_listable_before_it_has_finished(coordinator_db):
    """The row exists the moment `_start` returns, not when the task runs.

    Executions moved from a module dict into `app/coordinator/store.py`, so this
    reads the store; the invariant is unchanged and stricter than it was —
    writing the row from inside the queued task would still leave the caller
    holding an id nothing answers to.
    """
    executor = DAGExecutor(handlers={})
    dag = _dag(TaskNode(id="one", name="One", handler="collect_logs"))
    background = _HeldBackground()

    async def scenario():
        execution_id = await coord_api._start(
            background, executor, dag, {"incident_id": "inc-1"}
        )
        org_id = tenant_context.storage_org_id()
        return (
            execution_id,
            await coord_store.load_execution(org_id, execution_id),
            await coord_store.list_executions(org_id),
        )

    execution_id, stored, listed = asyncio.run(scenario())

    assert background.queued, "nothing was queued"
    assert stored is not None, "the run was invisible until it finished"
    assert stored.dag_id == "dag_test"
    assert stored.state == ExecutionState.RUNNING
    assert stored.initial_context == {"incident_id": "inc-1"}
    assert [row["id"] for row in listed] == [execution_id]


def test_a_background_failure_lands_on_the_execution_instead_of_vanishing(
    coordinator_db,
):
    async def explode(dag, context=None, execution_id=None, resume_from=None):
        raise RuntimeError("checkpoint exec_missing not found")

    executor = DAGExecutor(handlers={})
    executor.execute = explode  # type: ignore[method-assign]
    background = _HeldBackground()

    async def scenario():
        # One loop for both halves: aiosqlite connections are bound to the loop
        # that opened them, so the queued task has to run on the same one.
        execution_id = await coord_api._start(background, executor, _dag(), {})
        func, args, kwargs = background.queued[0]
        await func(*args, **kwargs)
        return await coord_store.load_execution(
            tenant_context.storage_org_id(), execution_id
        )

    failed = asyncio.run(scenario())

    assert failed is not None
    assert failed.state == ExecutionState.FAILED
    assert "checkpoint exec_missing not found" in (failed.error or "")
    assert "checkpoint exec_missing not found" in failed.to_dict()["error"]


def test_starting_an_investigation_returns_the_id_to_poll_with(client):
    # TestClient drains background tasks before returning, so the whole DAG runs
    # inside this call. P3 keeps `execute_fix` off the approval gate it takes at
    # P1/P2, and wait_seconds=0 skips VerificationHandler's minute-long settle.
    res = client.post(
        "/api/coordinator/investigations",
        json={
            "incident_id": "inc-42",
            "affected_services": ["api"],
            "severity": "P3",
            "context": {"wait_seconds": 0},
        },
    )
    assert res.status_code == 200, res.text
    execution_id = res.json()["execution_id"]

    found = client.get(f"/api/coordinator/executions/{execution_id}")
    assert found.status_code == 200, found.text
    assert found.json()["id"] == execution_id


# --------------------------------------------------------------------------- #
# 3. A DAG that cannot run is rejected at submission, not in the background
# --------------------------------------------------------------------------- #
def _dag_body(tasks: list[dict]) -> dict:
    return {"id": "dag_1", "name": "Submitted DAG", "tasks": tasks}


def _task_body(**overrides) -> dict:
    body = {"id": "t1", "name": "T1", "handler": "collect_logs"}
    body.update(overrides)
    return body


def test_a_duplicate_task_id_is_a_400_not_a_500(client):
    res = client.post(
        "/api/coordinator/dags",
        json=_dag_body([_task_body(), _task_body()]),
    )
    assert res.status_code == 400, res.text
    assert "Invalid DAG" in res.json()["detail"]


def test_an_unknown_priority_is_rejected_by_name(client):
    res = client.post(
        "/api/coordinator/dags",
        json=_dag_body([_task_body(priority="URGENT")]),
    )
    assert res.status_code == 422, res.text
    assert "unknown priority" in res.text


def test_an_unknown_handler_is_caught_at_submission(client):
    res = client.post(
        "/api/coordinator/dags",
        json=_dag_body([_task_body(handler="teleport_the_cluster")]),
    )
    assert res.status_code == 400, res.text
    detail = res.json()["detail"]
    assert "teleport_the_cluster" in detail
    assert "/coordinator/handlers" in detail


def test_a_missing_dependency_is_reported_rather_than_run(client):
    res = client.post(
        "/api/coordinator/dags",
        json=_dag_body([_task_body(depends_on=["nobody"])]),
    )
    assert res.status_code == 400, res.text
    assert "Invalid DAG" in res.json()["detail"]


def test_a_task_may_be_declared_before_the_task_it_depends_on(client):
    """Insertion order was part of the contract; `validate()` already covers this."""
    res = client.post(
        "/api/coordinator/dags",
        json=_dag_body([
            _task_body(id="second", depends_on=["first"]),
            _task_body(id="first"),
        ]),
    )
    assert res.status_code == 200, res.text
    assert res.json()["execution_levels"] == [["first"], ["second"]]


def test_resume_without_checkpoint_storage_says_so_up_front(client):
    res = client.post(
        "/api/coordinator/dags/dag_1/execute",
        json={
            "dag_input": _dag_body([_task_body()]),
            "execute_input": {"context": {}, "resume_from": "exec_deadbeef"},
        },
    )
    assert res.status_code == 400, res.text
    assert "COPILOT_REDIS_URL" in res.json()["detail"]


# --------------------------------------------------------------------------- #
# 4. Approvals belong to one execution
# --------------------------------------------------------------------------- #
def _execution_stub(execution_id: str) -> DAGExecution:
    return DAGExecution(
        id=execution_id,
        dag_id="dag_test",
        initial_context={},
        accumulated_context={},
        execution_levels=[["restart_service"]],
    )


def test_approving_one_run_does_not_release_another_parked_on_the_same_task():
    async def scenario() -> tuple[bool, bool]:
        executor = DAGExecutor(handlers={})
        task = TaskNode(
            id="restart_service",
            name="Restart",
            handler="restart_service",
            requires_approval=True,
            approval_timeout_minutes=1,
        )
        first = asyncio.create_task(
            executor._request_approval(task, _execution_stub("exec_aaaa"))
        )
        second = asyncio.create_task(
            executor._request_approval(task, _execution_stub("exec_bbbb"))
        )
        await asyncio.sleep(0)

        assert await executor.approve_task("exec_aaaa", "restart_service", True)

        # Awaited, not yielded to a fixed number of times. `asyncio.wait_for`
        # takes one more trip through the event loop on 3.11 than on 3.12 —
        # 3.11 releases an internal waiter from a done-callback, 3.12 awaits the
        # future directly — so a single `await asyncio.sleep(0)` here was really
        # asserting how the interpreter schedules, not what the executor does.
        # It passed on the 3.12 every test job resolves and failed on the 3.11
        # the deploy job pins.
        try:
            approved = await asyncio.wait_for(first, timeout=5)
        except asyncio.TimeoutError:
            raise AssertionError("the approved run is still parked") from None

        # Still parked, and still parked on a *live* future: the rejection below
        # returns False if anything already resolved it, so that call is the
        # real guard against one approval releasing both runs.
        assert not second.done(), "one approval released two production restarts"

        assert await executor.approve_task("exec_bbbb", "restart_service", False)
        return approved, await second

    approved, rejected = asyncio.run(scenario())
    assert approved is True
    assert rejected is False


def test_pending_approvals_can_be_narrowed_to_one_execution():
    async def scenario() -> tuple[list, list]:
        executor = DAGExecutor(handlers={})
        task = TaskNode(
            id="drain_node",
            name="Drain",
            handler="drain_node",
            requires_approval=True,
            approval_timeout_minutes=1,
        )
        waiters = [
            asyncio.create_task(
                executor._request_approval(task, _execution_stub(eid))
            )
            for eid in ("exec_aaaa", "exec_bbbb")
        ]
        await asyncio.sleep(0)

        everything = executor.pending_approvals()
        just_one = executor.pending_approvals("exec_aaaa")

        for eid in ("exec_aaaa", "exec_bbbb"):
            await executor.approve_task(eid, "drain_node", False)
        await asyncio.gather(*waiters)
        return everything, just_one

    everything, just_one = asyncio.run(scenario())
    assert len(everything) == 2
    assert just_one == [{"execution_id": "exec_aaaa", "task_id": "drain_node"}]


def test_approving_a_task_nobody_is_waiting_on_is_a_404(client):
    res = client.post(
        "/api/coordinator/executions/exec_nope/tasks/restart_service/approve",
        json={"approved": True, "reason": "looks fine"},
    )
    assert res.status_code == 404, res.text
    assert "/coordinator/approvals" in res.json()["detail"]


# --------------------------------------------------------------------------- #
# 5. A task that only simulated says so
# --------------------------------------------------------------------------- #
def test_a_simulated_task_is_not_reported_as_work_that_happened():
    simulated = TaskExecution(task_id="scale_up", state=ExecutionState.COMPLETED)
    simulated.output = {"simulated": True, "action": "scale_deployment", "replicas": 5}
    real = TaskExecution(task_id="collect_logs", state=ExecutionState.COMPLETED)
    real.output = {"log_count": 12}

    assert simulated.simulated is True
    assert real.simulated is False
    assert simulated.to_dict()["simulated"] is True
    assert real.to_dict()["simulated"] is False

    execution = _execution_stub("exec_cccc")
    execution.task_executions = {"scale_up": simulated, "collect_logs": real}
    assert execution.to_dict()["simulated_tasks"] == ["scale_up"]


def test_remediation_handlers_only_reach_a_cluster_when_one_is_configured(monkeypatch):
    """`k8s_client=None` used to mean "simulate", not "work it out from settings"."""
    from app.coordinator.handlers import remediation

    monkeypatch.delenv("KUBE_CONFIG_PATH", raising=False)
    cfg.get_settings.cache_clear()
    try:
        assert remediation.build_k8s_client() is None
        assert remediation.create_remediation_handlers()["scale_deployment"].k8s is None
    finally:
        cfg.get_settings.cache_clear()


# --------------------------------------------------------------------------- #
# 6. The runbooks MCP server reads the app's database
# --------------------------------------------------------------------------- #
@pytest.fixture
def mcp_db(tmp_path, monkeypatch):
    """A database reachable from the MCP server's own event loop.

    Everything here goes through `server._run` so the engine is created on, and
    stays on, the one loop the server uses — which is the point of the shared
    loop: `asyncio.run` per call left pooled connections bound to a loop that
    had already closed.
    """
    from app.mcp.servers.runbooks import server

    monkeypatch.setenv("COPILOT_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/mcp.db")
    cfg.get_settings.cache_clear()
    db_engine._engine = None
    db_engine._session_factory = None
    server._loop = None
    tenant_context.current_tenant.set(None)

    server._run(db_engine.init_db())
    yield server

    server._run(db_engine.close_db())
    cfg.get_settings.cache_clear()
    db_engine._engine = None
    db_engine._session_factory = None
    server._loop = None


def _store_runbook(server, name: str) -> str:
    from app.runbooks import store
    from app.runbooks.models import ExecutionMode, Runbook, RunbookStep, StepType

    runbook = Runbook(
        id=store.new_runbook_id(),
        name=name,
        description="written through the app, not the agent",
        service="api",
        trigger="manual",
        execution_mode=ExecutionMode.AUTO_APPROVED,
        steps=[
            RunbookStep(
                id="pause",
                name="Pause",
                description="a step that needs nothing external",
                step_type=StepType.WAIT,
                action="sleep",
                params={"seconds": 0},
            )
        ],
    )
    return server._run(store.create_runbook(runbook)).id


def test_the_agent_can_see_a_runbook_the_customer_wrote(mcp_db):
    server = mcp_db
    rb_id = _store_runbook(server, "Customer rollback")

    listed = server.list_runbooks()
    assert rb_id in [r["id"] for r in listed["runbooks"]], listed

    fetched = server.get_runbook(rb_id)
    assert fetched["runbook"]["name"] == "Customer rollback"


def test_an_agent_execution_leaves_an_audit_row(mcp_db):
    server = mcp_db
    rb_id = _store_runbook(server, "Customer rollback")

    started = server.execute_runbook(rb_id, service="api")
    assert "error" not in started, started
    assert started["status"] == "success", started

    history = server.list_executions(runbook_id=rb_id)
    assert [e["id"] for e in history["executions"]] == [started["execution_id"]]

    detail = server.get_execution(started["execution_id"])
    assert detail["execution"]["runbook_id"] == rb_id


def test_a_dry_run_records_nothing(mcp_db):
    server = mcp_db
    rb_id = _store_runbook(server, "Customer rollback")

    preview = server.execute_runbook(rb_id, service="api", dry_run=True)
    assert preview["dry_run"] is True
    assert server.list_executions(runbook_id=rb_id)["total"] == 0


def test_unparseable_extra_context_is_an_error_not_a_silent_drop(mcp_db):
    server = mcp_db
    rb_id = _store_runbook(server, "Customer rollback")

    assert "not valid JSON" in server.execute_runbook(rb_id, extra_context="{oops")["error"]
    assert "must be a JSON object" in server.execute_runbook(rb_id, extra_context="[1,2]")["error"]
    assert server.list_executions(runbook_id=rb_id)["total"] == 0


def test_rejecting_an_execution_is_not_recorded_as_a_failure(mcp_db):
    server = mcp_db
    from app.runbooks import store
    from app.runbooks.models import ExecutionMode, Runbook, RunbookStep, StepType

    runbook = Runbook(
        id=store.new_runbook_id(),
        name="Needs a human",
        description="",
        service="api",
        trigger="manual",
        execution_mode=ExecutionMode.REQUIRES_APPROVAL,
        steps=[
            RunbookStep(
                id="pause",
                name="Pause",
                description="a step that needs nothing external",
                step_type=StepType.WAIT,
                action="sleep",
                params={"seconds": 0},
            )
        ],
    )
    rb_id = server._run(store.create_runbook(runbook)).id

    started = server.execute_runbook(rb_id, service="api")
    assert started["requires_approval"] is True

    pending = server.list_pending_approvals()
    assert started["execution_id"] in [p["execution_id"] for p in pending["pending"]]

    rejected = server.reject_execution(started["execution_id"])
    assert rejected["status"] == "skipped", rejected
    assert server.list_pending_approvals()["total"] == 0


def test_approving_a_run_nobody_started_is_an_error_not_a_crash(mcp_db):
    server = mcp_db
    assert "not found" in server.approve_execution("exec-nope")["error"]
    assert "Invalid status" in server.list_executions(status="banana")["error"]


def test_the_runbooks_mcp_subprocess_is_pointed_at_the_app_database(monkeypatch):
    """A stdio server inherits only HOME/PATH/SHELL/TERM/USER, so this is explicit."""
    from app.mcp import client as mcp_client

    monkeypatch.setenv("COPILOT_DATABASE_URL", "postgresql+asyncpg://user@db/omni")
    cfg.get_settings.cache_clear()
    try:
        env = mcp_client._server_config()["runbooks"]["env"]
    finally:
        cfg.get_settings.cache_clear()

    assert env["COPILOT_DATABASE_URL"] == "postgresql+asyncpg://user@db/omni"
    assert "KUBE_CONFIG_PATH" in env


# --------------------------------------------------------------------------- #
# 7. Goal decomposition reports the route it actually took
# --------------------------------------------------------------------------- #
class _FakeLLM:
    def __init__(self, payload: str) -> None:
        self.payload = payload
        self.prompts: list[str] = []

    async def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.payload


def _goal(description: str, category: GoalCategory, **kwargs) -> Goal:
    return Goal(id="goal_1", description=description, category=category, **kwargs)


def test_a_category_with_a_template_says_template():
    result = asyncio.run(
        GoalDecomposer().decompose(_goal("db is down", GoalCategory.INCIDENT_RESPONSE))
    )
    assert result.strategy == "template"
    assert result.confidence == 0.85
    assert "template" in result.reasoning


def test_a_category_without_a_template_and_no_llm_admits_it_is_a_placeholder():
    """This is the case that used to claim 0.85 and "using template approach"."""
    result = asyncio.run(
        GoalDecomposer().decompose(_goal("rotate the signing keys", GoalCategory.SECURITY))
    )
    assert result.strategy == "generic"
    assert result.confidence == 0.3
    assert "no LLM provider is configured" in result.reasoning
    assert "has not read the goal" in result.reasoning
    assert result.to_dict()["strategy"] == "generic"


def test_the_llm_path_sees_the_goal_text():
    llm = _FakeLLM(json.dumps([
        {
            "id": "audit",
            "name": "Audit key usage",
            "description": "find every consumer of the old key",
            "task_type": "investigation",
            "priority": 1,
            "dependencies": [],
            "estimated_duration_minutes": 20,
            "required_tools": ["logs"],
        }
    ]))
    result = asyncio.run(
        GoalDecomposer(llm_client=llm).decompose(
            _goal("rotate the signing keys", GoalCategory.SECURITY)
        )
    )

    assert result.strategy == "llm"
    assert result.confidence == 0.7
    assert [sg.name for sg in result.sub_goals] == ["Audit key usage"]
    assert "rotate the signing keys" in llm.prompts[0]


def test_an_llm_that_fails_falls_back_and_reports_the_fallback():
    class _Broken:
        async def generate(self, prompt: str) -> str:
            raise RuntimeError("401 unauthorized")

    result = asyncio.run(
        GoalDecomposer(llm_client=_Broken()).decompose(
            _goal("shrink the bill", GoalCategory.OPTIMIZATION)
        )
    )
    assert result.strategy == "generic"
    assert "returned no usable sub-goals" in result.reasoning


def test_every_sub_goal_carries_the_goal_text_into_execution():
    result = asyncio.run(
        GoalDecomposer().decompose(
            _goal(
                "checkout p99 doubled after the 14:02 deploy",
                GoalCategory.INCIDENT_RESPONSE,
                success_criteria=["p99 back under 400ms"],
                constraints=["no full restart"],
            )
        )
    )
    for sub_goal in result.sub_goals:
        assert sub_goal.inputs["goal_description"].startswith("checkout p99 doubled")
        assert sub_goal.inputs["goal_success_criteria"] == ["p99 back under 400ms"]
        assert sub_goal.inputs["goal_constraints"] == ["no full restart"]


def test_two_goals_differing_only_in_context_do_not_share_a_cached_plan():
    decomposer = GoalDecomposer()
    long_description = "investigate " + "x" * 80

    async def scenario():
        first = await decomposer.decompose(
            _goal(long_description + "-alpha", GoalCategory.INVESTIGATION,
                  context={"service": "checkout"})
        )
        second = await decomposer.decompose(
            _goal(long_description + "-alpha", GoalCategory.INVESTIGATION,
                  context={"service": "billing"})
        )
        third = await decomposer.decompose(
            _goal(long_description + "-beta", GoalCategory.INVESTIGATION,
                  context={"service": "checkout"})
        )
        return first, second, third

    first, second, third = asyncio.run(scenario())
    assert len(decomposer._decomposition_cache) == 3
    assert "(cached)" not in second.reasoning
    assert "(cached)" not in third.reasoning
    assert first.sub_goals[0].inputs["goal_context"] == {"service": "checkout"}
    assert second.sub_goals[0].inputs["goal_context"] == {"service": "billing"}


def test_an_identical_goal_is_served_from_cache_and_labelled_as_such():
    decomposer = GoalDecomposer()

    async def scenario():
        goal = _goal("db is down", GoalCategory.INCIDENT_RESPONSE)
        await decomposer.decompose(goal)
        return await decomposer.decompose(goal)

    again = asyncio.run(scenario())
    assert again.reasoning.endswith("(cached)")
    assert again.strategy == "template"


def test_the_decomposition_cache_is_bounded():
    decomposer = GoalDecomposer()
    decomposer.CACHE_LIMIT = 3

    async def scenario():
        for index in range(6):
            await decomposer.decompose(
                _goal(f"incident {index}", GoalCategory.INCIDENT_RESPONSE)
            )

    asyncio.run(scenario())
    assert len(decomposer._decomposition_cache) == 3


# --------------------------------------------------------------------------- #
# 8. A planning tool with no backend does not claim success
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "tool_name,expected_setting",
    [
        ("logs", "LOKI_URL"),
        ("metrics", "PROMETHEUS_URL"),
        ("traces", "TRACES_API_URL"),
        ("ci_cd", "register_tool"),
        ("notification", "SLACK_BOT_TOKEN"),
    ],
)
def test_an_unconfigured_tool_reports_itself_rather_than_faking_a_result(
    tool_name, expected_setting
):
    tool = create_default_tools()[tool_name]
    result = asyncio.run(tool({"task_id": "t1", "inputs": {}}))

    assert result["status"] == "not_configured", result
    assert result["simulated"] is True
    assert expected_setting in result["detail"]
    assert "success" not in json.dumps(result)


def test_a_configured_logs_tool_queries_the_backend():
    calls: list[dict] = []

    async def fake_query(self, service, start_time, end_time):
        calls.append({"service": service})
        return [{"level": "error", "message": "connection refused to db-1"}]

    from app.coordinator.handlers.investigation import LogsHandler

    original = LogsHandler._query_loki
    LogsHandler._query_loki = fake_query  # type: ignore[method-assign]
    try:
        tool = create_default_tools(loki_url="http://loki:3100")["logs"]
        result = asyncio.run(tool({
            "task_id": "analyze_logs",
            "inputs": {"affected_services": ["checkout"], "context": {}},
        }))
    finally:
        LogsHandler._query_loki = original  # type: ignore[method-assign]

    assert calls == [{"service": "checkout"}]
    assert result["simulated"] is False
    assert result["status"] == "analyzed"
    assert result["services"]["checkout"]["error_count"] == 1


def test_a_configured_tool_with_no_service_to_query_says_so_instead_of_analyzing():
    tool = create_default_tools(prometheus_url="http://prom:9090")["metrics"]
    result = asyncio.run(tool({"task_id": "t1", "inputs": {}}))

    assert result["status"] == "no_services"
    assert result["simulated"] is True


def test_a_graph_of_stubs_does_not_come_back_as_work_done():
    async def scenario():
        decomposition = await GoalDecomposer().decompose(
            _goal("why is checkout slow", GoalCategory.INVESTIGATION)
        )
        engine = ExecutionEngine()
        for name, handler in create_default_tools().items():
            engine.register_tool(name, handler)
        return await engine.execute(decomposition.task_graph)

    result = asyncio.run(scenario())
    payload = result.to_dict()

    assert result.completed_tasks > 0
    assert len(result.simulated_tasks) == result.completed_tasks
    assert payload["all_simulated"] is True
    for task_id in result.simulated_tasks:
        assert result.outputs[task_id]["status"] == "no_op"


def test_a_task_whose_tool_is_not_registered_names_what_was_missing():
    async def scenario():
        engine = ExecutionEngine()
        graph_result = await GoalDecomposer().decompose(
            _goal("why is checkout slow", GoalCategory.INVESTIGATION)
        )
        return await engine.execute(graph_result.task_graph)

    result = asyncio.run(scenario())
    with_tools = [
        outputs for outputs in result.outputs.values() if outputs.get("missing_tools")
    ]
    assert with_tools, "no task reported an unregistered tool"
    assert "logs" in {tool for o in with_tools for tool in o["missing_tools"]}


def test_real_tool_output_keeps_the_execution_out_of_the_simulated_list():
    async def real_tool(inputs):
        return {"status": "analyzed", "simulated": False, "errors": 3}

    async def scenario():
        engine = ExecutionEngine()
        engine.register_tool("logs", real_tool)
        decomposition = await GoalDecomposer().decompose(
            _goal("why is checkout slow", GoalCategory.INVESTIGATION)
        )
        return await engine.execute(decomposition.task_graph)

    result = asyncio.run(scenario())
    ran_for_real = [
        task_id for task_id, out in result.outputs.items()
        if out.get("status") == "executed"
    ]
    assert ran_for_real, result.outputs
    assert not set(ran_for_real) & set(result.simulated_tasks)
    assert result.to_dict()["all_simulated"] is False
