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
from app.incidents.models import AgentStatus, IncidentSeverity, IncidentStatus
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


def test_investigation_is_persisted_and_agent_updates_stick(db):
    created = _declare("acme", "checkout down")

    started = _as("acme", lambda: get_incident_store().start_investigation(created.id))
    assert started is not None
    assert set(started.agents) == {
        "knowledge", "reasoning", "action", "learning", "collaboration", "ambient"
    }

    from app.incidents.models import AgentFinding

    finding = AgentFinding(agent_type="knowledge", finding="pool exhausted", confidence=0.9)
    updated = _as(
        "acme",
        lambda: get_incident_store().update_agent_status(
            started.id, "knowledge", AgentStatus.COMPLETE, finding=finding
        ),
    )
    assert updated.agents["knowledge"].progress == 100
    assert updated.overall_confidence == pytest.approx(0.9)

    # And it is on the incident, from a fresh store.
    reloaded = _as("acme", lambda: IncidentStore().get_incident(created.id))
    assert reloaded.investigation is not None
    assert reloaded.investigation.agents["knowledge"].findings[0].finding == "pool exhausted"


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
            lambda: get_incident_store().update_agent_status(
                started.id, "knowledge", AgentStatus.COMPLETE
            ),
        )
        is None
    )
