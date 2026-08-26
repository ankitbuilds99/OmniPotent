"""Incidents survive a restart, and do not cross tenants.

The store was a process-memory dict seeded with three fabricated incidents:
anything a customer declared was gone on the next deploy, and the seeded rows
showed up in every org's list as if they were real. It is the `incidents` table
now, so these tests assert both halves — durability across a fresh store
instance, and an org_id filter on every read and write.
"""

from __future__ import annotations

import asyncio

import pytest

import app.config as cfg
from app import tenant_context
from app.database import engine as db_engine
from app.incidents.models import IncidentSeverity, IncidentStatus, SafetyGateStatus
from app.incidents.store import IncidentStore, get_incident_store


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


def _declare(org: str, title: str, service: str = "pay"):
    return _as(
        org,
        lambda: get_incident_store().create_incident(
            title=title, severity=IncidentSeverity.CRITICAL, service=service
        ),
    )


def test_incident_survives_a_new_store_instance(db):
    created = _declare("acme", "checkout down")

    # A brand new store object, as a fresh process would build: the dict-backed
    # store lost everything at exactly this point.
    fetched = _as("acme", lambda: IncidentStore().get_incident(created.id))

    assert fetched is not None
    assert fetched.title == "checkout down"
    assert fetched.status == IncidentStatus.TRIGGERED
    assert [e.type for e in fetched.timeline] == ["triggered"]


def test_a_new_org_starts_with_no_incidents(db):
    assert _as("acme", lambda: get_incident_store().list_incidents()) == []


def test_incidents_do_not_cross_tenants(db):
    created = _declare("acme", "checkout down")

    assert _as("umbrella", lambda: get_incident_store().get_incident(created.id)) is None
    assert _as("umbrella", lambda: get_incident_store().list_incidents()) == []
    assert len(_as("acme", lambda: get_incident_store().list_incidents())) == 1


def test_another_tenant_cannot_resolve_your_incident(db):
    created = _declare("acme", "checkout down")

    assert (
        _as("umbrella", lambda: get_incident_store().resolve_incident(created.id, "mallory"))
        is None
    )

    mine = _as("acme", lambda: get_incident_store().get_incident(created.id))
    assert mine.status == IncidentStatus.TRIGGERED
    assert mine.resolved_by is None


def test_acknowledge_and_resolve_persist_and_append_to_the_timeline(db):
    created = _declare("acme", "checkout down")

    _as("acme", lambda: get_incident_store().acknowledge_incident(created.id, "ada"))
    _as("acme", lambda: get_incident_store().resolve_incident(created.id, "ada", "restarted"))

    row = _as("acme", lambda: IncidentStore().get_incident(created.id))
    assert row.status == IncidentStatus.RESOLVED
    assert row.acknowledged_by == "ada"
    assert row.resolved_by == "ada"
    assert row.resolved_at is not None
    # The JSON column is rebound rather than mutated in place, or these
    # appends would be dropped silently at flush.
    assert [e.type for e in row.timeline] == ["triggered", "acknowledged", "resolved"]
    assert "restarted" in row.timeline[-1].message


def test_list_filters_are_applied_in_sql(db):
    _declare("acme", "checkout down", service="pay")
    _declare("acme", "search slow", service="search")
    _as("acme", lambda: get_incident_store().list_incidents())

    by_service = _as(
        "acme", lambda: get_incident_store().list_incidents(service="search")
    )
    assert [i.title for i in by_service] == ["search slow"]

    by_status = _as(
        "acme", lambda: get_incident_store().list_incidents(status=IncidentStatus.RESOLVED)
    )
    assert by_status == []


def test_update_records_a_status_change(db):
    created = _declare("acme", "checkout down")

    updated = _as(
        "acme",
        lambda: get_incident_store().update_incident(
            created.id, {"status": IncidentStatus.IDENTIFIED, "assignee": "ada"}
        ),
    )

    assert updated.status == IncidentStatus.IDENTIFIED
    assert updated.assignee == "ada"
    assert [e.type for e in updated.timeline] == ["triggered", "status_change"]


def test_investigation_is_persisted_and_the_result_sticks(db):
    """An investigation starts empty and is filled by the run that finishes it.

    This used to assert six `AgentState`s — knowledge, reasoning, action,
    learning, collaboration, ambient — seeded at `idle` by `start_investigation`
    and moved by `update_agent_status`. Nothing in `app/` ever called that
    method; the six agents were written once, displayed, and never touched
    again, so the test was the only thing that made them look alive. Both are
    gone. What replaced them is the path the webhook actually drives:
    `start_investigation` parks a row, and `record_investigation_result` writes
    back what the agent run produced.
    """
    created = _declare("acme", "checkout down")

    started = _as("acme", lambda: get_incident_store().start_investigation(created.id))
    assert started is not None
    # Nothing is claimed until the run reports in.
    assert started.agents == {}
    assert started.completed_at is None
    assert started.root_cause is None

    finished = _as(
        "acme",
        lambda: get_incident_store().record_investigation_result(
            created.id,
            root_cause="connection pool exhausted",
            recommended_actions=["raise pool size to 40"],
            confidence=0.9,
        ),
    )
    assert finished.root_cause == "connection pool exhausted"
    assert finished.overall_confidence == pytest.approx(0.9)
    assert finished.completed_at is not None

    # The confidence gate is measured, not parked at "pending".
    gate = next(g for g in finished.safety_gates if g.gate_type == "confidence")
    assert gate.status == SafetyGateStatus.PASSED
    assert gate.current_value == pytest.approx(0.9)

    # And it is on the incident, from a fresh store.
    reloaded = _as("acme", lambda: IncidentStore().get_incident(created.id))
    assert reloaded.investigation is not None
    assert reloaded.investigation.root_cause == "connection pool exhausted"
    assert reloaded.investigation.recommended_actions == ["raise pool size to 40"]
    # A root cause moves the incident off "triggered".
    assert reloaded.status == IncidentStatus.IDENTIFIED


def test_a_low_confidence_run_fails_the_gate(db):
    """The gate has to be able to say no, or it is decoration again."""
    created = _declare("acme", "checkout down")
    _as("acme", lambda: get_incident_store().start_investigation(created.id))

    finished = _as(
        "acme",
        lambda: get_incident_store().record_investigation_result(
            created.id, root_cause=None, recommended_actions=[], confidence=0.2
        ),
    )
    gate = next(g for g in finished.safety_gates if g.gate_type == "confidence")
    assert gate.status == SafetyGateStatus.FAILED
    # No root cause means the incident stays where it was.
    reloaded = _as("acme", lambda: IncidentStore().get_incident(created.id))
    assert reloaded.status == IncidentStatus.TRIGGERED


def test_a_failed_run_is_not_left_looking_like_it_is_still_going(db):
    created = _declare("acme", "checkout down")
    _as("acme", lambda: get_incident_store().start_investigation(created.id))

    failed = _as(
        "acme",
        lambda: get_incident_store().record_investigation_failure(created.id, "no API key"),
    )
    assert failed.completed_at is not None
    reloaded = _as("acme", lambda: IncidentStore().get_incident(created.id))
    assert any(e.type == "investigation_failed" for e in reloaded.timeline)


def test_repeated_investigate_returns_the_running_one(db):
    """The auto-investigate webhook fires on every matching alert, and the FK is
    unique — a second trigger must not blow up on a constraint violation."""
    created = _declare("acme", "checkout down")

    first = _as("acme", lambda: get_incident_store().start_investigation(created.id))
    second = _as("acme", lambda: get_incident_store().start_investigation(created.id))

    assert first.id == second.id


def test_investigation_is_not_readable_by_another_tenant(db):
    created = _declare("acme", "checkout down")
    started = _as("acme", lambda: get_incident_store().start_investigation(created.id))

    assert _as("umbrella", lambda: get_incident_store().get_investigation(started.id)) is None
    assert (
        _as(
            "umbrella",
            lambda: get_incident_store().record_investigation_result(
                created.id, root_cause="mine now", recommended_actions=[], confidence=1.0
            ),
        )
        is None
    )
    assert (
        _as(
            "umbrella",
            lambda: get_incident_store().record_investigation_failure(created.id, "sabotage"),
        )
        is None
    )

    # Neither write landed.
    mine = _as("acme", lambda: IncidentStore().get_investigation(started.id))
    assert mine.root_cause is None
    assert mine.completed_at is None


def test_timestamps_come_back_as_utc_aware(db):
    """One response must not mix two timezone conventions.

    SQLite has no timezone type, so a `DateTime(timezone=True)` column round
    trips naive. The timeline entries are JSON and keep their offset, so
    without normalisation `triggered_at` was serialised with no offset next to
    timeline entries that had one — and a client reading the offset-less value
    as local time is hours out. Any comparison against `datetime.now(utc)`
    was a TypeError too.
    """
    created = _declare("acme", "checkout down")
    _as("acme", lambda: get_incident_store().acknowledge_incident(created.id, "sam"))
    resolved = _as(
        "acme", lambda: get_incident_store().resolve_incident(created.id, "sam", "rolled back")
    )

    # A fresh instance, so every value has been through the database.
    fetched = _as("acme", lambda: IncidentStore().get_incident(created.id))

    assert fetched.triggered_at.tzinfo is not None
    assert fetched.acknowledged_at.tzinfo is not None
    assert fetched.resolved_at.tzinfo is not None
    assert resolved.resolved_at.tzinfo is not None
    # The same convention as the JSON timeline in the same payload.
    assert all(e.timestamp.tzinfo is not None for e in fetched.timeline)
    # And usable in the arithmetic the API does on them.
    assert (fetched.resolved_at - fetched.triggered_at).total_seconds() >= 0


def test_investigation_timestamps_come_back_as_utc_aware(db):
    created = _declare("acme", "checkout down")
    started = _as("acme", lambda: get_incident_store().start_investigation(created.id))

    fetched = _as("acme", lambda: IncidentStore().get_investigation(started.id))

    assert fetched.started_at.tzinfo is not None
