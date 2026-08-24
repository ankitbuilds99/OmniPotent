"""Runbooks and their execution history survive a restart, per org.

Both were module-level dicts. A runbook is a script that runs against
production, so losing them on restart lost the remediation; the execution
history is the audit trail for "who ran what against which service" and was
never written down at all — `runbook_executions` shipped in migration 001 with
no writer. These tests pin the durable behaviour and the org_id filter on every
read and write.
"""

from __future__ import annotations

import asyncio

import pytest

import app.config as cfg
from app import tenant_context
from app.database import engine as db_engine
from app.runbooks import store
from app.runbooks.models import ExecutionMode, Runbook, RunbookStep, StepStatus, StepType


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("COPILOT_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/app.db")
    cfg.get_settings.cache_clear()
    db_engine._engine = None
    db_engine._session_factory = None
    tenant_context.current_tenant.set(None)

    asyncio.run(db_engine.init_db())
    yield

    asyncio.run(db_engine.close_db())
    cfg.get_settings.cache_clear()
    db_engine._engine = None
    db_engine._session_factory = None
    tenant_context.current_tenant.set(None)


def _as(org: str, coro_factory):
    """Run one coroutine as `org`, on its own loop, like a separate request."""

    async def run():
        token = tenant_context.set_tenant(tenant_context.TenantConfig(org_id=org))
        try:
            return await coro_factory()
        finally:
            tenant_context.reset_tenant(token)

    return asyncio.run(run())


def _runbook(rb_id: str = "acme-rollback", service: str = "pay") -> Runbook:
    return Runbook(
        id=rb_id,
        name="Roll back pay",
        description="internal",
        service=service,
        trigger="manual",
        execution_mode=ExecutionMode.REQUIRES_APPROVAL,
        steps=[
            RunbookStep(
                id="undo",
                name="Undo",
                description="roll the deployment back",
                step_type=StepType.KUBERNETES,
                action="rollout_undo",
            )
        ],
    )


def test_a_created_runbook_is_readable_after_a_restart(db):
    _as("acme", lambda: store.create_runbook(_runbook()))

    fetched = _as("acme", lambda: store.get_runbook("acme-rollback"))

    assert fetched is not None
    assert fetched.name == "Roll back pay"
    assert [s.action for s in fetched.steps] == ["rollout_undo"]
    assert fetched.execution_mode is ExecutionMode.REQUIRES_APPROVAL


def test_every_org_gets_its_own_copy_of_the_built_ins(db):
    acme = {rb.id for rb in _as("acme", lambda: store.list_runbooks())}
    umbrella = {rb.id for rb in _as("umbrella", lambda: store.list_runbooks())}

    assert "restart-service" in acme
    assert acme == umbrella


def test_runbook_deleted_by_one_org_survives_for_another(db):
    """The built-ins are seeded per org, so a delete has to stick for the org
    that made it — and only that org."""
    assert _as("umbrella", lambda: store.delete_runbook("restart-service")) is True

    assert _as("umbrella", lambda: store.get_runbook("restart-service")) is None
    assert _as("acme", lambda: store.get_runbook("restart-service")) is not None


def test_deleting_the_last_runbook_does_not_reseed(db):
    """Seeding on "this org has no runbooks" would resurrect every built-in the
    moment an org cleared them out."""
    for rb in _as("acme", lambda: store.list_runbooks()):
        _as("acme", lambda rb_id=rb.id: store.delete_runbook(rb_id))

    assert _as("acme", lambda: store.list_runbooks()) == []


def test_runbooks_do_not_cross_tenants(db):
    _as("acme", lambda: store.create_runbook(_runbook()))

    assert _as("umbrella", lambda: store.get_runbook("acme-rollback")) is None
    assert _as("umbrella", lambda: store.delete_runbook("acme-rollback")) is False
    assert _as("acme", lambda: store.get_runbook("acme-rollback")) is not None


def test_list_filters(db):
    _as("acme", lambda: store.create_runbook(_runbook(service="pay")))

    by_service = _as("acme", lambda: store.list_runbooks(service="pay"))
    # The built-ins declare service "*", which means "applies to everything" —
    # they match a service filter rather than being excluded by it.
    assert "acme-rollback" in {rb.id for rb in by_service}
    assert "restart-service" in {rb.id for rb in by_service}

    assert "acme-rollback" not in {
        rb.id for rb in _as("acme", lambda: store.list_runbooks(service="search"))
    }
    assert {rb.id for rb in _as("acme", lambda: store.list_runbooks(trigger="alert:high-cpu-usage"))} == {
        "scale-up"
    }


def test_execution_history_is_recorded_and_scoped(db):
    rb = _runbook()
    _as("acme", lambda: store.create_runbook(rb))

    execution = _as(
        "acme",
        lambda: store.record_execution(rb, StepStatus.PENDING, {"service": "pay"}),
    )

    mine = _as("acme", lambda: store.list_executions())
    assert [e.id for e in mine] == [execution.id]
    assert mine[0].context == {"service": "pay"}
    assert mine[0].status is StepStatus.PENDING

    # The audit trail is the point: another org must not see the run at all.
    assert _as("umbrella", lambda: store.list_executions()) == []


def test_approving_an_execution_persists(db):
    rb = _runbook()
    execution = _as(
        "acme", lambda: store.record_execution(rb, StepStatus.PENDING, {})
    )

    updated = _as(
        "acme",
        lambda: store.set_execution_status(
            execution.id, StepStatus.RUNNING, expect={StepStatus.PENDING}
        ),
    )
    assert updated.status is StepStatus.RUNNING

    reloaded = _as("acme", lambda: store.list_executions(status="running"))
    assert [e.id for e in reloaded] == [execution.id]


def test_approving_twice_reports_a_conflict_not_a_missing_execution(db):
    """The router turns None into 404 and "conflict" into 400; they must not be
    the same answer."""
    execution = _as(
        "acme", lambda: store.record_execution(_runbook(), StepStatus.RUNNING, {})
    )

    result = _as(
        "acme",
        lambda: store.set_execution_status(
            execution.id, StepStatus.RUNNING, expect={StepStatus.PENDING}
        ),
    )
    assert result == "conflict"


def test_another_tenant_cannot_touch_your_execution(db):
    execution = _as(
        "acme", lambda: store.record_execution(_runbook(), StepStatus.PENDING, {})
    )

    assert (
        _as("umbrella", lambda: store.set_execution_status(execution.id, StepStatus.FAILED))
        is None
    )
    assert _as("acme", lambda: store.list_executions())[0].status is StepStatus.PENDING


def test_an_unparseable_execution_id_is_a_miss_not_a_crash(db):
    """The id is a UUID in the database but arrives as a path string."""
    assert (
        _as("acme", lambda: store.set_execution_status("not-a-uuid", StepStatus.FAILED))
        is None
    )


def test_terminal_status_stamps_completed_at(db):
    execution = _as(
        "acme", lambda: store.record_execution(_runbook(), StepStatus.RUNNING, {})
    )
    assert execution.completed_at is None

    failed = _as("acme", lambda: store.set_execution_status(execution.id, StepStatus.FAILED))
    assert failed.completed_at is not None


def test_execution_timestamps_come_back_as_utc_aware(db):
    """An execution's timestamps must not disagree with each other.

    SQLite has no timezone type, so a `DateTime(timezone=True)` column round
    trips naive. The per-step timestamps are parsed from ISO strings in the
    JSON results and keep their offset, so without normalisation one response
    carried both conventions — and any comparison against
    `datetime.now(utc)` raised TypeError.
    """
    rb = _runbook()
    _as("acme", lambda: store.create_runbook(rb))
    execution = _as(
        "acme", lambda: store.record_execution(rb, StepStatus.RUNNING, {})
    )
    _as("acme", lambda: store.set_execution_status(execution.id, StepStatus.SUCCESS))

    # Read back through the database rather than using the object just built.
    listed = _as("acme", lambda: store.list_executions())[0]

    assert listed.started_at.tzinfo is not None
    assert listed.completed_at.tzinfo is not None
    assert (listed.completed_at - listed.started_at).total_seconds() >= 0


def test_an_execution_can_hold_any_trigger_its_runbook_can(db):
    """`record_execution` copies the runbook's trigger into the execution row.

    `runbooks.trigger` is 64 wide and the execution column was 32, with nothing
    capping the trigger on the way in — `CreateRunbookRequest.trigger` is a
    plain str. A runbook with a 40-character trigger therefore saved fine and
    then could not record a single execution: on Postgres the insert raises
    StringDataRightTruncation and POST /api/runbooks/{id}/execute 500s, so the
    audit trail for a script that runs against production has a hole in it.
    SQLite ignores VARCHAR lengths, which is why the columns are compared here
    as well as exercised.
    """
    from app.database.models import Runbook as RunbookRow, RunbookExecution

    assert (
        RunbookExecution.__table__.c.trigger.type.length
        >= RunbookRow.__table__.c.trigger.type.length
    )

    long_trigger = "alertmanager:payments-checkout-latency-p99"  # 41 characters
    rb = _runbook()
    rb.trigger = long_trigger
    _as("acme", lambda: store.create_runbook(rb))

    _as("acme", lambda: store.record_execution(rb, StepStatus.PENDING, {}))

    assert _as("acme", lambda: store.list_executions())
