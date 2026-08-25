"""Executing a runbook actually runs its steps — and never fakes one.

Three things were wrong and are pinned here:

1. `POST /runbooks/{id}/execute` only wrote a row. Four declared steps produced
   zero StepExecutions and the execution never left `pending`.
2. `POST /runbooks/executions/{id}/approve` only flipped a status column, so an
   approved execution sat in `running` with no steps forever — `completed_at`
   was unreachable because only a terminal status stamps it. Reject recorded a
   human's refusal as `failed`, indistinguishable in the audit trail from a
   remediation that ran and broke.
3. Every step handler returned a canned success ("Posted to Slack: …",
   "Initiated rollout restart for api") without touching Slack or a cluster, so
   the agent reported remediations that never happened.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

import app.config as cfg
import app.api.main as api
from app import tenant_context
from app.database import engine as db_engine
from app.runbooks.executor import RunbookExecutor
from app.runbooks.models import RunbookStep, StepType


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("COPILOT_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/app.db")
    monkeypatch.delenv("COPILOT_MULTI_TENANT", raising=False)
    monkeypatch.delenv("COPILOT_API_TOKEN", raising=False)
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    monkeypatch.delenv("KUBE_CONFIG_PATH", raising=False)
    monkeypatch.delenv("RUNBOOK_COMMAND_ALLOWLIST", raising=False)
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


def _wait_runbook(client, *, mode: str = "auto_approved", steps=None) -> str:
    """A runbook whose steps need no external system, so a test can run it."""
    body = {
        "name": "Pause twice",
        "description": "two waits",
        "service": "api",
        "trigger": "manual",
        "execution_mode": mode,
        "steps": steps
        if steps is not None
        else [
            {
                "id": "first",
                "name": "First wait",
                "step_type": "wait",
                "action": "sleep",
                "params": {"seconds": 0},
            },
            {
                "id": "second",
                "name": "Second wait",
                "step_type": "wait",
                "action": "sleep",
                "params": {"seconds": 0},
            },
        ],
    }
    res = client.post("/api/runbooks", json=body)
    assert res.status_code == 200, res.text
    return str(res.json()["id"])


def _execution(client, execution_id: str) -> dict:
    listed = client.get("/api/runbooks/executions")
    assert listed.status_code == 200, listed.text
    match = [e for e in listed.json() if e["id"] == execution_id]
    assert match, f"execution {execution_id} missing from {listed.json()}"
    return dict(match[0])


# --------------------------------------------------------------------------- #
# 1. Execute really executes
# --------------------------------------------------------------------------- #
def test_executing_a_runbook_runs_its_steps_and_finishes(client):
    rb_id = _wait_runbook(client)

    res = client.post(f"/api/runbooks/{rb_id}/execute", json={"service": "api"})
    assert res.status_code == 200, res.text
    execution_id = res.json()["id"]

    done = _execution(client, execution_id)
    assert done["status"] == "success", done
    assert [s["step_id"] for s in done["steps"]] == ["first", "second"]
    assert [s["status"] for s in done["steps"]] == ["success", "success"]
    assert done["completed_at"] is not None


def test_a_runbook_created_through_the_api_is_visible_to_the_executor(client):
    """The executor kept its own dict seeded from DEFAULT_RUNBOOKS only."""
    rb_id = _wait_runbook(client)
    executor = RunbookExecutor()

    assert executor.get_runbook(rb_id) is None  # the in-memory copy never had it
    found = client.portal.call(executor.resolve_runbook, rb_id)
    assert found is not None and found.id == rb_id


def test_a_failing_step_stops_the_run_and_is_recorded_as_failed(client):
    rb_id = _wait_runbook(
        client,
        steps=[
            {
                "id": "post",
                "name": "Announce",
                "step_type": "slack",
                "action": "post_message",
                "params": {"message": "restarting ${service}"},
            },
            {
                "id": "never",
                "name": "Never runs",
                "step_type": "wait",
                "action": "sleep",
                "params": {"seconds": 0},
            },
        ],
    )

    res = client.post(f"/api/runbooks/{rb_id}/execute", json={"service": "api"})
    execution = _execution(client, res.json()["id"])

    assert execution["status"] == "failed"
    assert [s["step_id"] for s in execution["steps"]] == ["post"]
    step = execution["steps"][0]
    assert step["status"] == "failed"
    # Honest, specific, and names the missing configuration.
    assert "SLACK_WEBHOOK_URL" in step["error"]
    assert "Posted to Slack" not in step["output"]


# --------------------------------------------------------------------------- #
# 2. Approve / reject
# --------------------------------------------------------------------------- #
def test_approval_gate_pauses_resumes_and_completes(client):
    rb_id = _wait_runbook(
        client,
        mode="requires_approval",
        steps=[
            {
                "id": "before",
                "name": "Before the gate",
                "step_type": "wait",
                "action": "sleep",
                "params": {"seconds": 0},
            },
            {
                "id": "gated",
                "name": "Needs a human",
                "step_type": "wait",
                "action": "sleep",
                "params": {"seconds": 0},
                "requires_approval": True,
            },
            {
                "id": "after",
                "name": "After the gate",
                "step_type": "wait",
                "action": "sleep",
                "params": {"seconds": 0},
            },
        ],
    )

    started = client.post(f"/api/runbooks/{rb_id}/execute", json={"service": "api"})
    execution_id = started.json()["id"]
    # requires_approval: nothing runs until a human says so.
    assert started.json()["status"] == "pending"
    assert _execution(client, execution_id)["steps"] == []

    first = client.post(f"/api/runbooks/executions/{execution_id}/approve")
    assert first.status_code == 200, first.text
    parked = _execution(client, execution_id)
    assert parked["status"] == "waiting_approval"
    assert [s["step_id"] for s in parked["steps"]] == ["before"]

    second = client.post(f"/api/runbooks/executions/{execution_id}/approve")
    assert second.status_code == 200, second.text
    finished = _execution(client, execution_id)
    assert finished["status"] == "success", finished
    assert [s["step_id"] for s in finished["steps"]] == ["before", "gated", "after"]
    assert finished["completed_at"] is not None


def test_approving_an_unknown_execution_is_404(client):
    res = client.post("/api/runbooks/executions/not-a-uuid/approve")
    assert res.status_code == 404


def test_rejecting_records_skipped_not_failed(client):
    rb_id = _wait_runbook(client, mode="requires_approval")
    execution_id = client.post(
        f"/api/runbooks/{rb_id}/execute", json={"service": "api"}
    ).json()["id"]

    res = client.post(f"/api/runbooks/executions/{execution_id}/reject")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "skipped"
    assert body["completed_at"] is not None
    assert body["steps"] == []

    # Nothing ran, so a second reject (or an approve) is a conflict, not a
    # silent second transition.
    assert client.post(f"/api/runbooks/executions/{execution_id}/reject").status_code == 400
    assert client.post(f"/api/runbooks/executions/{execution_id}/approve").status_code == 400


def test_rejecting_an_unknown_execution_is_404(client):
    res = client.post(
        "/api/runbooks/executions/00000000-0000-0000-0000-000000000000/reject"
    )
    assert res.status_code == 404


# --------------------------------------------------------------------------- #
# 3. The step handlers themselves
# --------------------------------------------------------------------------- #
def _step(step_type: str, action: str = "", **params) -> RunbookStep:
    return RunbookStep(
        id="s",
        name="s",
        description="",
        step_type=StepType(step_type),
        action=action,
        params=params,
        timeout_seconds=30,
    )


def _run(step: RunbookStep, context: dict | None = None) -> tuple[bool, str, str]:
    return asyncio.run(RunbookExecutor().execute_step(step, context or {}))


def test_command_steps_are_refused_when_no_allowlist_is_configured(monkeypatch):
    monkeypatch.delenv("RUNBOOK_COMMAND_ALLOWLIST", raising=False)
    cfg.get_settings.cache_clear()
    try:
        ok, output, error = _run(_step("command", "rm -rf /tmp/nope"))
    finally:
        cfg.get_settings.cache_clear()

    assert ok is False
    assert output == ""
    assert "RUNBOOK_COMMAND_ALLOWLIST" in error
    assert "was not run" in error


def test_command_steps_refuse_a_binary_outside_the_allowlist(monkeypatch):
    monkeypatch.setenv("RUNBOOK_COMMAND_ALLOWLIST", "echo")
    cfg.get_settings.cache_clear()
    try:
        ok, _out, error = _run(_step("command", "rm -rf /tmp/nope"))
    finally:
        cfg.get_settings.cache_clear()

    assert ok is False
    assert "'rm' is not in RUNBOOK_COMMAND_ALLOWLIST" in error


def test_an_allowlisted_command_really_runs(monkeypatch):
    monkeypatch.setenv("RUNBOOK_COMMAND_ALLOWLIST", "echo,false")
    cfg.get_settings.cache_clear()
    try:
        ok, output, error = _run(_step("command", "echo runbook-ran-this"))
        failed_ok, _out, failed_error = _run(_step("command", "false"))
    finally:
        cfg.get_settings.cache_clear()

    # Real stdout from a real process, not a formatted echo of the action.
    assert (ok, output, error) == (True, "runbook-ran-this", "")
    # And a non-zero exit is a failure rather than "Command executed: false".
    assert failed_ok is False
    assert "exited with code 1" in failed_error


def test_kubernetes_steps_fail_loudly_without_a_kubeconfig(monkeypatch):
    monkeypatch.delenv("KUBE_CONFIG_PATH", raising=False)
    cfg.get_settings.cache_clear()
    try:
        ok, output, error = _run(
            _step("kubernetes", "rollout_restart", deployment="api", namespace="prod")
        )
    finally:
        cfg.get_settings.cache_clear()

    assert ok is False
    assert output == ""
    assert "KUBE_CONFIG_PATH" in error
    assert "Nothing was changed" in error


def test_slack_steps_fail_loudly_without_credentials(monkeypatch):
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    cfg.get_settings.cache_clear()
    try:
        ok, output, error = _run(
            _step("slack", "post_message", message="restarting ${service}"),
            {"service": "api"},
        )
    finally:
        cfg.get_settings.cache_clear()

    assert (ok, output) == (False, "")
    assert "SLACK_WEBHOOK_URL" in error
    assert "not posted" in error


def test_http_steps_do_not_invent_a_200(monkeypatch):
    # Nothing is listening on this port; the old handler answered "200 OK".
    ok, output, error = _run(
        _step("http", "check", url="http://127.0.0.1:1/health", method="GET")
    )
    assert ok is False
    assert "200 OK" not in output
    assert "http://127.0.0.1:1/health failed" in error

    missing_ok, _out, missing_error = _run(_step("http", "check"))
    assert missing_ok is False
    assert "no 'url' parameter" in missing_error


def test_wait_reports_the_time_it_actually_slept():
    ok, output, error = _run(
        RunbookStep(
            id="w",
            name="w",
            description="",
            step_type=StepType.WAIT,
            action="sleep",
            params={"seconds": 30},
            timeout_seconds=0,
        )
    )
    # Clamped to the step timeout — and says so instead of claiming 30 seconds.
    assert (ok, error) == (True, "")
    assert "Waited 0 seconds" in output
    assert "30 were requested" in output


def test_an_unsupported_step_type_is_not_a_success():
    ok, output, error = _run(_step("terraform", "apply"))
    assert (ok, output) == (False, "")
    assert "terraform" in error
    assert "nothing was run" in error
