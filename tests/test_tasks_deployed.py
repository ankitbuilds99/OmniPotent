"""The background task package is deployable, and honest about what it cannot do.

`app/tasks/` was production-shaped but had never run: four of its seven actors called
``asyncio.get_event_loop().run_until_complete``, which raises in a worker thread on
Python 3.12+, and Dramatiq runs actors in worker threads. Nothing in the codebase
enqueued any of them, so nothing ever noticed.

These tests pin the three things that took it from shaped to deployed:

1. Actors run in a worker thread (the ``asyncio.run`` fix), with an AST guard so the
   old call cannot come back.
2. The actors that CANNOT work in a separate process say so instead of reporting
   success. Two returned a cheerful status while doing nothing, and a third would
   have fired false alerts against healthy services.
3. The deployment surfaces exist and match that picture: a worker in compose and
   Helm, and no scheduler anywhere, because it has no jobs left to run.
"""

from __future__ import annotations

import ast
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.alerting import AlertCondition, AlertRule, AlertSeverity
from app.alerting.engine import AlertEngine
import app.alerting.engine as alerting_engine
from app.tasks import tasks as task_module
from app.tasks import worker as worker_module

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def fresh_engine(monkeypatch: pytest.MonkeyPatch) -> AlertEngine:
    """A per-test alert engine, standing in for the module singleton.

    The engine is a process-global, and the actor reaches it through ``get_engine``;
    without this a test would evaluate against whatever an earlier test recorded.
    """
    engine = AlertEngine()
    monkeypatch.setattr(alerting_engine, "_engine", engine)
    return engine


# --------------------------------------------------------------------------------
# 1. Actors run in a worker thread
# --------------------------------------------------------------------------------


def test_an_actor_body_runs_in_a_worker_thread() -> None:
    """Dramatiq executes actors on worker threads, which have no event loop.

    The previous ``asyncio.get_event_loop().run_until_complete(...)`` raises
    ``RuntimeError: There is no current event loop in thread`` there on 3.12+, so
    this actor failed on its first execution in every deployment. Running the body
    off the main thread is the condition that reproduces it.
    """
    result: dict[str, Any] = {}
    error: list[BaseException] = []

    def run() -> None:
        try:
            # An unsupported channel returns without any network or settings access,
            # so what is under test is purely the loop handling.
            result.update(task_module.send_notification("carrier-pigeon", "hello"))
        except BaseException as exc:  # noqa: BLE001 — the failure mode being pinned
            error.append(exc)

    thread = threading.Thread(target=run)
    thread.start()
    thread.join(timeout=30)

    assert not error, f"actor raised in a worker thread: {error[0]!r}"
    assert result == {"status": "unsupported", "channel": "carrier-pigeon"}


def test_no_actor_reaches_for_the_ambient_event_loop() -> None:
    """AST guard: ``get_event_loop`` must not reappear anywhere in app/tasks/.

    Asserted structurally rather than by grep so a reformat or an alias cannot slip
    it back in, and scoped to the whole package because the bug was in four separate
    actors — a fix pinned to one of them would not have held.
    """
    offenders = []
    for path in sorted((REPO / "app" / "tasks").rglob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "get_event_loop":
                offenders.append(f"{path.relative_to(REPO)}:{node.lineno}")

    assert offenders == [], (
        "asyncio.get_event_loop() raises in a Dramatiq worker thread on Python "
        f"3.12+; use asyncio.run(). Found at: {', '.join(offenders)}"
    )


# --------------------------------------------------------------------------------
# 2. What cannot work says so
# --------------------------------------------------------------------------------


def test_session_cleanup_declines_instead_of_reporting_success() -> None:
    """Sessions live in the API process; a worker cannot evict them.

    This returned ``{"status": "completed", "cleaned": 0}``, which in a dashboard
    reads as "cleanup ran, nothing was due" rather than "cleanup is not wired".
    """
    result = task_module.cleanup_expired_sessions(24)

    assert result["status"] == "not_applicable"
    assert result["cleaned"] == 0
    assert "no shared store" in result["reason"]


def _absence_rule(metric: str = "request_rate") -> AlertRule:
    return AlertRule(
        id="stopped-reporting",
        name="Service stopped reporting",
        description="No metrics received",
        service="*",
        metric=metric,
        condition=AlertCondition.ABSENCE,
        threshold=0,
        severity=AlertSeverity.HIGH,
    )


def test_absence_does_not_fire_for_a_metric_that_was_never_observed(
    fresh_engine: AlertEngine,
) -> None:
    """"Never sent" is not "stopped sending".

    ``get_metric_value`` returns None for both, and the ABSENCE branch used to treat
    them the same — so a rule fired the first time any service was evaluated. The
    shipped ``metrics-absence`` default rule makes this concrete: it watches
    ``heartbeat`` on service ``*``, nothing records a metric by that name, and it
    pages Slack and PagerDuty at high severity.
    """
    fresh_engine.add_rule(_absence_rule())

    assert fresh_engine.evaluate_all(["checkout"]) == []

    # Including the default rule that shipped in this state.
    fresh_engine.record_metric("checkout", "error_rate", 0.01)
    fired = fresh_engine.evaluate_all(["checkout"])
    assert [a.rule_id for a in fired] == []


def test_absence_still_fires_when_a_reporting_metric_goes_quiet(
    fresh_engine: AlertEngine,
) -> None:
    """The fix must not disable absence detection, only narrow it to real outages."""
    stale = datetime.now() - timedelta(minutes=5)
    fresh_engine.record_metric("checkout", "request_rate", 120.0, timestamp=stale)
    fresh_engine.add_rule(_absence_rule())

    fired = fresh_engine.evaluate_all(["checkout"])

    assert [a.rule_id for a in fired] == ["stopped-reporting"]
    assert fired[0].message == "No metrics received"


def test_alert_evaluation_declines_when_this_process_holds_no_metrics(
    fresh_engine: AlertEngine,
) -> None:
    """Belt and braces over the engine fix: an empty engine has nothing to say.

    "Evaluated 12 services, 0 alerts" from a process that holds no metrics is an
    all-clear nobody checked, so the actor declines rather than reporting zero.
    """
    fresh_engine.add_rule(_absence_rule())

    result = task_module.process_alert_evaluation(["checkout"])

    assert result["status"] == "no_metrics"
    assert result["alerts_fired"] == 0
    assert result["services_evaluated"] == 0
    # And nothing was recorded as firing, so a later evaluation is not poisoned.
    assert fresh_engine.get_firing_alerts() == []


def test_alert_evaluation_skips_services_it_has_no_data_for(
    fresh_engine: AlertEngine,
) -> None:
    """A partially-populated engine evaluates what it knows and names what it skipped.

    The failure this prevents is subtler than the empty case: one recorded metric is
    enough to get past the guard, after which every OTHER service in the argument
    list still looks absent.
    """
    fresh_engine.record_metric("payments", "request_rate", 120.0)
    fresh_engine.add_rule(_absence_rule())

    result = task_module.process_alert_evaluation(["payments", "checkout"])

    assert result["status"] == "completed"
    assert result["services_evaluated"] == 1
    assert result["services_skipped"] == ["checkout"]
    assert result["alerts_fired"] == 0


def test_known_services_reports_only_services_with_history() -> None:
    engine = AlertEngine()
    assert engine.known_services() == []
    assert not engine.has_metrics()

    engine.record_metric("payments", "request_rate", 1.0)
    engine.record_metric("checkout", "error_rate", 0.01)

    assert engine.known_services() == ["checkout", "payments"]
    assert engine.has_metrics()


def test_webhook_actor_is_not_reachable_from_any_api_route() -> None:
    """The webhook actor acknowledges without acting; nothing may enqueue it.

    Its handlers count what arrived and return "processed" — they open no incident
    and notify no one. The API's webhook routes do that work synchronously, so
    pointing a live route here would replace real handling with a success counter.
    """
    enqueue_sites = []
    for path in sorted((REPO / "app").rglob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"send", "send_with_options"}
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "process_webhook"
            ):
                enqueue_sites.append(f"{path.relative_to(REPO)}:{node.lineno}")

    assert enqueue_sites == [], (
        "process_webhook returns {'status': 'processed'} without creating an "
        f"incident or notifying anyone; enqueued at: {', '.join(enqueue_sites)}"
    )


# --------------------------------------------------------------------------------
# 3. Deployment surfaces
# --------------------------------------------------------------------------------


def test_scheduler_refuses_to_start_with_no_jobs() -> None:
    """An empty scheduler must exit, not idle.

    Both of its jobs were removed: session cleanup cannot reach the API's sessions,
    and alert evaluation was being handed an empty service list — ``evaluate_all([])``
    loops over services inside its rule loop, so it evaluated nothing on every tick.
    A pod running an empty job table looks healthy and never restarts, which is a
    worse signal than a process that exits with a reason.
    """
    assert worker_module.SCHEDULED_JOBS == []
    assert worker_module.run_scheduler() is False


def test_compose_runs_a_worker_and_no_scheduler() -> None:
    compose = yaml.safe_load((REPO / "docker-compose.yml").read_text())
    services = compose["services"]

    assert "worker" in services, "the worker is what makes the task package deployed"
    worker = services["worker"]
    assert worker["command"] == ["python", "-m", "app.tasks.worker", "--mode", "worker"]
    # Without a broker URL the worker falls back to a stub broker and processes
    # nothing while looking healthy — see app/tasks/broker.py.
    assert "COPILOT_REDIS_URL" in worker["environment"]
    assert "redis" in worker["depends_on"]

    assert "scheduler" not in services, (
        "`--mode scheduler` exits 1 while SCHEDULED_JOBS is empty; a compose service "
        "would restart-loop"
    )


def test_helm_ships_a_worker_deployment_gated_on_a_broker() -> None:
    template = (
        REPO / "deploy" / "helm" / "copilot" / "templates" / "worker-deployment.yaml"
    ).read_text()

    assert "{{- if .Values.worker.enabled }}" in template
    assert '"app.tasks.worker"' in template
    assert "kind: Deployment" in template
    # The worker takes work from Redis and holds nothing authoritative, so it is not
    # subject to the single-replica pin the API is under.
    assert "{{ .Values.worker.replicaCount }}" in template

    values = yaml.safe_load(
        (REPO / "deploy" / "helm" / "copilot" / "values.yaml").read_text()
    )
    assert values["worker"]["enabled"] is False, (
        "enabled by default would run a worker with no COPILOT_REDIS_URL, which "
        "silently processes nothing"
    )
    # Above run_investigation's 300s time_limit, so shutdown can drain in-flight work.
    assert values["worker"]["terminationGracePeriodSeconds"] > 300


def test_no_scheduler_deployment_ships_in_the_chart() -> None:
    templates = (REPO / "deploy" / "helm" / "copilot" / "templates").glob("*.yaml")
    names = [p.name for p in templates]

    assert "scheduler-deployment.yaml" not in names, (
        "run_scheduler exits non-zero with no jobs; a Deployment would CrashLoopBackOff"
    )
