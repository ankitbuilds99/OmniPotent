"""The status page survives a restart, per org.

Components, incidents, maintenance windows and subscribers lived in four dicts
on a per-org object in process memory. The page a customer's users refresh
during an outage went back to "All Systems Operational" the moment that process
went away, and the subscriber list — their users' email addresses and webhook
URLs — went with it.

These tests pin the durable behaviour, the org filter on every read and write,
and the two calculations the dicts got wrong: uptime, which was permanently
100%, and the in-progress maintenance window that vanished from the page
announcing it.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone

import pytest

import app.config as cfg
from app import tenant_context
from app.database import engine as db_engine
from app.status_page.models import (
    ComponentStatus,
    IncidentSeverity,
    IncidentStatus,
    MaintenanceStatus,
    SubscriptionType,
)
from app.status_page.store import StatusStore
from app.tenant_context import TenantConfig, reset_tenant, set_tenant


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


# Long enough that an outage is unmistakably longer than the store calls around
# it, short enough not to slow the suite down.
_OUTAGE = 0.3


def _as(org: str, coro_factory):
    """One call as `org`, on its own loop, as a separate request would make it."""
    token = set_tenant(TenantConfig(org_id=org))
    try:
        return asyncio.run(coro_factory(StatusStore()))
    finally:
        reset_tenant(token)


# --- components --------------------------------------------------------------


def test_a_component_outlives_the_process(db):
    created = _as("acme", lambda s: s.create_component("Search", "Full-text search", "Core", 6))

    # A brand new store, as a restarted worker would build.
    found = _as("acme", lambda s: s.get_component(created.id))

    assert found is not None
    assert found.name == "Search"
    assert found.description == "Full-text search"
    assert found.group == "Core"
    assert found.display_order == 6
    assert found.status is ComponentStatus.OPERATIONAL


def test_every_org_gets_its_own_built_in_components(db):
    acme = _as("acme", lambda s: s.list_components())
    umbrella = _as("umbrella", lambda s: s.list_components())

    assert [c.id for c in acme] == ["api", "dashboard", "database", "authentication", "notifications"]
    assert [c.id for c in umbrella] == [c.id for c in acme]
    assert [c.display_order for c in acme] == [1, 2, 3, 4, 5]


def test_a_deleted_built_in_stays_deleted_for_that_org_only(db):
    assert _as("acme", lambda s: s.delete_component("database")) is True

    # Not resurrected by the next read, which is what seeding on "no components
    # yet" would have done.
    assert [c.id for c in _as("acme", lambda s: s.list_components())] == [
        "api",
        "dashboard",
        "authentication",
        "notifications",
    ]
    assert _as("acme", lambda s: s.get_component("database")) is None
    assert _as("umbrella", lambda s: s.get_component("database")) is not None


def test_deleting_every_component_does_not_reseed(db):
    for comp_id in ("api", "dashboard", "database", "authentication", "notifications"):
        _as("acme", lambda s, c=comp_id: s.delete_component(c))

    assert _as("acme", lambda s: s.list_components()) == []


def test_one_orgs_components_are_invisible_to_another(db):
    created = _as("acme", lambda s: s.create_component("Search"))

    assert _as("umbrella", lambda s: s.get_component(created.id)) is None
    assert _as("umbrella", lambda s: s.delete_component(created.id)) is False
    assert _as("umbrella", lambda s: s.update_component(created.id, name="mine")) is None
    assert _as("acme", lambda s: s.get_component(created.id)).name == "Search"


def test_components_are_filtered_by_group_and_status(db):
    _as("acme", lambda s: s.update_component("database", status="major_outage"))

    core = _as("acme", lambda s: s.list_components(group="Core"))
    assert [c.id for c in core] == ["api", "dashboard", "authentication"]

    down = _as("acme", lambda s: s.list_components(status=ComponentStatus.MAJOR_OUTAGE))
    assert [c.id for c in down] == ["database"]


def test_updates_persist_and_leave_other_fields_alone(db):
    updated = _as("acme", lambda s: s.update_component("api", name="Public API", display_order=0))

    assert updated.name == "Public API"
    assert updated.display_order == 0
    assert updated.description == "Core REST API endpoints"
    assert _as("acme", lambda s: s.get_component("api")).name == "Public API"


def test_update_ignores_fields_that_are_not_updatable(db):
    """The keys come from a PATCH body and now reach columns. The dict version
    fed them to setattr() behind a hasattr() guard, which also accepted `id` and
    `created_at`."""
    original = _as("acme", lambda s: s.get_component("api"))

    updated = _as(
        "acme",
        lambda s: s.update_component("api", id="stolen", created_at="whenever", nonsense=1),
    )

    assert updated.id == "api"
    assert updated.created_at == original.created_at
    assert _as("acme", lambda s: s.get_component("api")) is not None


def test_updating_something_that_is_not_there_is_a_miss_not_a_crash(db):
    assert _as("acme", lambda s: s.update_component("nope", name="x")) is None
    assert _as("acme", lambda s: s.delete_component("nope")) is False
    assert _as("acme", lambda s: s.get_incident("nope")) is None
    assert _as("acme", lambda s: s.get_maintenance("nope")) is None
    assert _as("acme", lambda s: s.get_subscriber("nope")) is None


# --- incidents ---------------------------------------------------------------


def test_an_incident_and_its_timeline_survive_a_restart(db):
    incident = _as(
        "acme",
        lambda s: s.create_incident(
            "Elevated error rates",
            "We are seeing 5xx responses",
            IncidentSeverity.MAJOR,
            ["api"],
        ),
    )

    found = _as("acme", lambda s: s.get_incident(incident.id))

    assert found.title == "Elevated error rates"
    assert found.severity is IncidentSeverity.MAJOR
    assert found.status is IncidentStatus.INVESTIGATING
    assert found.affected_components == ["api"]
    assert [u.message for u in found.updates] == ["We are seeing 5xx responses"]
    assert found.is_active is True


def test_an_incident_marks_and_then_restores_its_components(db):
    incident = _as("acme", lambda s: s.create_incident("Outage", affected_components=["api"]))

    assert _as("acme", lambda s: s.get_component("api")).status is ComponentStatus.PARTIAL_OUTAGE

    _as("acme", lambda s: s.resolve_incident(incident.id, "Fixed."))

    assert _as("acme", lambda s: s.get_component("api")).status is ComponentStatus.OPERATIONAL


def test_the_timeline_keeps_its_order_within_one_second(db):
    """Updates posted from a script land in the same second, and SQLite's
    timestamps have one-second resolution — the tie-break column is what keeps
    the incident's story in the order it was told."""
    incident = _as("acme", lambda s: s.create_incident("Outage", "Investigating."))

    _as("acme", lambda s: s.update_incident(incident.id, IncidentStatus.IDENTIFIED, "Found it."))
    _as("acme", lambda s: s.update_incident(incident.id, IncidentStatus.MONITORING, "Watching."))
    _as("acme", lambda s: s.resolve_incident(incident.id, "Resolved."))

    found = _as("acme", lambda s: s.get_incident(incident.id))
    assert [u.message for u in found.updates] == [
        "Investigating.",
        "Found it.",
        "Watching.",
        "Resolved.",
    ]
    assert found.status is IncidentStatus.RESOLVED
    assert found.resolved_at is not None
    assert found.is_active is False


def test_incidents_are_filtered_by_activity_and_age(db):
    stale = _as("acme", lambda s: s.create_incident("Old news"))
    _as("acme", lambda s: s.resolve_incident(stale.id))
    active = _as("acme", lambda s: s.create_incident("Happening now"))

    assert [i.id for i in _as("acme", lambda s: s.list_incidents(active_only=True))] == [active.id]
    assert len(_as("acme", lambda s: s.list_incidents())) == 2
    assert len(_as("acme", lambda s: s.list_incidents(days=7))) == 2
    assert len(_as("acme", lambda s: s.list_incidents(limit=1))) == 1


def test_one_orgs_incidents_are_invisible_to_another(db):
    incident = _as("acme", lambda s: s.create_incident("Outage"))

    assert _as("umbrella", lambda s: s.get_incident(incident.id)) is None
    assert _as("umbrella", lambda s: s.list_incidents()) == []
    assert _as("umbrella", lambda s: s.update_incident(incident.id, message="mine")) is None
    assert _as("umbrella", lambda s: s.resolve_incident(incident.id)) is None
    assert _as("acme", lambda s: s.get_incident(incident.id)).is_active is True


# --- maintenance -------------------------------------------------------------


def test_a_maintenance_window_survives_a_restart(db):
    start = datetime.now(timezone.utc) + timedelta(days=1)
    window = _as(
        "acme",
        lambda s: s.create_maintenance(
            "Database upgrade",
            "Postgres 16",
            start,
            start + timedelta(hours=2),
            ["database"],
        ),
    )

    found = _as("acme", lambda s: s.get_maintenance(window.id))

    assert found.title == "Database upgrade"
    assert found.scheduled_start == start
    assert found.affected_components == ["database"]
    assert found.status is MaintenanceStatus.SCHEDULED
    assert found.is_upcoming is True


def test_a_window_keeps_its_instant_across_timezones(db):
    """SQLAlchemy's SQLite DATETIME writes the wall clock and drops the offset,
    so 09:00+05:30 would be announced, and started, five and a half hours late."""
    delhi = timezone(timedelta(hours=5, minutes=30))
    start = datetime(2026, 9, 1, 9, 0, tzinfo=delhi)

    window = _as("acme", lambda s: s.create_maintenance("Upgrade", scheduled_start=start))

    found = _as("acme", lambda s: s.get_maintenance(window.id))
    assert found.scheduled_start == start
    assert found.scheduled_start.hour == 3  # 09:00+05:30 is 03:30 UTC
    assert found.scheduled_start.minute == 30


def test_maintenance_in_progress_stays_on_the_status_page(db):
    """`upcoming_only` asked for SCHEDULED *or* IN_PROGRESS and then also for
    `scheduled_start >= now`, which an in-progress window can never satisfy. It
    disappeared from the page the moment it began."""
    window = _as(
        "acme",
        lambda s: s.create_maintenance(
            "Database upgrade",
            scheduled_start=datetime.now(timezone.utc) + timedelta(hours=1),
            affected_components=["database"],
        ),
    )
    assert [m.id for m in _as("acme", lambda s: s.list_maintenances(upcoming_only=True))] == [window.id]

    _as("acme", lambda s: s.start_maintenance(window.id))

    still_shown = _as("acme", lambda s: s.list_maintenances(upcoming_only=True))
    assert [m.id for m in still_shown] == [window.id]
    assert still_shown[0].is_active is True
    assert _as("acme", lambda s: s.get_component("database")).status is ComponentStatus.MAINTENANCE

    _as("acme", lambda s: s.complete_maintenance(window.id))

    # ...and it drops off once it is done.
    assert _as("acme", lambda s: s.list_maintenances(upcoming_only=True)) == []
    assert _as("acme", lambda s: s.get_component("database")).status is ComponentStatus.OPERATIONAL


def test_a_past_window_is_not_upcoming(db):
    _as(
        "acme",
        lambda s: s.create_maintenance(
            "Last week",
            scheduled_start=datetime.now(timezone.utc) - timedelta(days=7),
        ),
    )

    assert _as("acme", lambda s: s.list_maintenances(upcoming_only=True)) == []
    assert len(_as("acme", lambda s: s.list_maintenances())) == 1


def test_maintenance_updates_persist_and_are_whitelisted(db):
    window = _as("acme", lambda s: s.create_maintenance("Upgrade"))

    updated = _as(
        "acme",
        lambda s: s.update_maintenance(
            window.id, title="Postgres upgrade", status=MaintenanceStatus.CANCELLED, id="stolen"
        ),
    )

    assert updated.title == "Postgres upgrade"
    assert updated.status is MaintenanceStatus.CANCELLED
    assert updated.id == window.id
    assert _as("acme", lambda s: s.get_maintenance(window.id)).status is MaintenanceStatus.CANCELLED


def test_one_orgs_maintenance_is_invisible_to_another(db):
    window = _as("acme", lambda s: s.create_maintenance("Upgrade"))

    assert _as("umbrella", lambda s: s.get_maintenance(window.id)) is None
    assert _as("umbrella", lambda s: s.list_maintenances()) == []
    assert _as("umbrella", lambda s: s.start_maintenance(window.id)) is None
    assert _as("umbrella", lambda s: s.complete_maintenance(window.id)) is None
    assert _as("umbrella", lambda s: s.update_maintenance(window.id, title="mine")) is None


# --- subscribers -------------------------------------------------------------


def test_a_subscriber_outlives_the_process(db):
    subscriber = _as(
        "acme",
        lambda s: s.create_subscriber(
            SubscriptionType.EMAIL, "sre@example.com", ["api"], notify_on_resolved=False
        ),
    )

    found = _as("acme", lambda s: s.get_subscriber(subscriber.id))

    assert found.target == "sre@example.com"
    assert found.components == ["api"]
    assert found.verified is False
    assert found.notify_on_incidents is True
    # Set at creation, not assigned to the returned object afterwards — that
    # only stored anything while the object was the one in the dict.
    assert found.notify_on_resolved is False


def test_a_webhook_subscription_is_created_already_verified(db):
    subscriber = _as(
        "acme",
        lambda s: s.create_subscriber(
            SubscriptionType.WEBHOOK, "https://hooks.example.com/x", verified=True
        ),
    )

    found = _as("acme", lambda s: s.get_subscriber(subscriber.id))
    assert found.verified is True
    assert found.verification_token is None


def test_verification_consumes_the_token(db):
    subscriber = _as("acme", lambda s: s.create_subscriber(SubscriptionType.EMAIL, "a@b.com"))
    token = subscriber.verification_token
    assert token

    assert _as("acme", lambda s: s.verify_subscriber(subscriber.id, "wrong")) is False
    assert _as("acme", lambda s: s.verify_subscriber(subscriber.id, token)) is True
    assert _as("acme", lambda s: s.get_subscriber(subscriber.id)).verified is True
    # The link cannot be replayed.
    assert _as("acme", lambda s: s.verify_subscriber(subscriber.id, token)) is False


def test_subscribers_are_listed_by_verification_and_component(db):
    everything = _as("acme", lambda s: s.create_subscriber(SubscriptionType.EMAIL, "all@b.com", verified=True))
    just_api = _as(
        "acme",
        lambda s: s.create_subscriber(SubscriptionType.EMAIL, "api@b.com", ["api"], verified=True),
    )
    _as("acme", lambda s: s.create_subscriber(SubscriptionType.EMAIL, "pending@b.com"))

    verified = _as("acme", lambda s: s.list_subscribers())
    assert {s.id for s in verified} == {everything.id, just_api.id}
    assert len(_as("acme", lambda s: s.list_subscribers(verified_only=False))) == 3

    # An empty component list means "everything", so it matches too.
    for_api = _as("acme", lambda s: s.list_subscribers(component_id="api"))
    assert {s.id for s in for_api} == {everything.id, just_api.id}
    for_db = _as("acme", lambda s: s.list_subscribers(component_id="database"))
    assert {s.id for s in for_db} == {everything.id}


def test_one_address_gets_one_subscription(db):
    first = _as("acme", lambda s: s.create_subscriber(SubscriptionType.EMAIL, "sre@example.com"))

    # As the second request in a race past the router's existence check would.
    second = _as("acme", lambda s: s.create_subscriber(SubscriptionType.EMAIL, "sre@example.com"))

    assert second.id == first.id
    assert len(_as("acme", lambda s: s.list_subscribers(verified_only=False))) == 1


def test_one_orgs_subscribers_are_invisible_to_another(db):
    """These are the customer's own users' addresses; a leak here is a leak of
    someone else's contact list."""
    subscriber = _as("acme", lambda s: s.create_subscriber(SubscriptionType.EMAIL, "sre@acme.com"))

    assert _as("umbrella", lambda s: s.get_subscriber(subscriber.id)) is None
    assert _as("umbrella", lambda s: s.get_subscriber_by_target("sre@acme.com")) is None
    assert _as("umbrella", lambda s: s.list_subscribers(verified_only=False)) == []
    assert _as("umbrella", lambda s: s.delete_subscriber(subscriber.id)) is False
    assert _as(
        "umbrella", lambda s: s.verify_subscriber(subscriber.id, subscriber.verification_token)
    ) is False
    assert _as("acme", lambda s: s.get_subscriber(subscriber.id)).verified is False


def test_the_same_address_can_subscribe_to_two_orgs(db):
    """One person watching two vendors' status pages is not a duplicate."""
    acme = _as("acme", lambda s: s.create_subscriber(SubscriptionType.EMAIL, "sre@example.com"))
    umbrella = _as("umbrella", lambda s: s.create_subscriber(SubscriptionType.EMAIL, "sre@example.com"))

    assert acme.id != umbrella.id
    assert _as("acme", lambda s: s.get_subscriber_by_target("sre@example.com")).id == acme.id
    assert _as("umbrella", lambda s: s.get_subscriber_by_target("sre@example.com")).id == umbrella.id


def test_deleting_a_subscriber_unsubscribes_them(db):
    subscriber = _as("acme", lambda s: s.create_subscriber(SubscriptionType.EMAIL, "a@b.com"))

    assert _as("acme", lambda s: s.delete_subscriber(subscriber.id)) is True
    assert _as("acme", lambda s: s.get_subscriber(subscriber.id)) is None
    assert _as("acme", lambda s: s.delete_subscriber(subscriber.id)) is False


def test_notification_timestamps_persist(db):
    subscriber = _as("acme", lambda s: s.create_subscriber(SubscriptionType.EMAIL, "a@b.com"))
    assert subscriber.last_notified_at is None

    _as("acme", lambda s: s.update_subscriber_notified(subscriber.id))

    stamped = _as("acme", lambda s: s.get_subscriber(subscriber.id)).last_notified_at
    assert stamped is not None
    assert stamped.tzinfo is not None


# --- uptime ------------------------------------------------------------------


def test_uptime_records_the_interval_a_component_was_down(db):
    """No uptime record was ever written. `update_component` measured the
    interval as `now - updated_at` after setting `updated_at = now`, so it was
    always 0 and the `if duration > 0` guard never passed — the 30- and 90-day
    figures on the page were the "no records, assume perfect" fallback, forever.
    """
    _as("acme", lambda s: s.record_uptime("api", 90.0, True))
    _as("acme", lambda s: s.record_uptime("api", 10.0, False))

    assert _as("acme", lambda s: s.calculate_uptime("api", days=30)) == 90.0


def test_a_status_change_writes_the_interval_it_ends(db):
    _as("acme", lambda s: s.update_component("api", status="major_outage"))
    time.sleep(_OUTAGE)
    _as("acme", lambda s: s.update_component("api", status="operational"))

    # The component was up for the few milliseconds before the outage and down
    # for the sleep, so uptime is a small fraction. Without a record of either
    # this reads 100.0 — which is what it always read.
    assert _as("acme", lambda s: s.calculate_uptime("api", days=30)) < 25.0


def test_renaming_a_component_does_not_end_its_outage(db):
    """`status_since` is separate from `updated_at` for this: measured from the
    last time the row was touched, an edit part-way through an outage would
    erase the downtime before it and only the tail would be counted."""
    _as("acme", lambda s: s.update_component("api", status="major_outage"))
    time.sleep(_OUTAGE)
    _as("acme", lambda s: s.update_component("api", name="Public API"))
    _as("acme", lambda s: s.update_component("api", status="operational"))

    # The outage interval has to cover the sleep, which dwarfs the millisecond
    # the component was up beforehand. Restarted by the rename, it would be
    # milliseconds too, and this would sit near 50%.
    assert _as("acme", lambda s: s.calculate_uptime("api", days=30)) < 25.0


def test_a_component_with_no_history_reads_as_fully_up(db):
    assert _as("acme", lambda s: s.calculate_uptime("api", days=30)) == 100.0
    assert _as("acme", lambda s: s.calculate_uptime("nonexistent", days=90)) == 100.0


def test_uptime_is_scoped_to_the_org(db):
    _as("acme", lambda s: s.record_uptime("api", 50.0, False))
    _as("acme", lambda s: s.record_uptime("api", 50.0, True))

    assert _as("acme", lambda s: s.calculate_uptime("api")) == 50.0
    assert _as("umbrella", lambda s: s.calculate_uptime("api")) == 100.0


def test_deleting_a_component_takes_its_uptime_history(db):
    _as("acme", lambda s: s.record_uptime("api", 50.0, False))
    _as("acme", lambda s: s.record_uptime("api", 50.0, True))
    assert _as("acme", lambda s: s.delete_component("api")) is True

    # Not merely unreachable through the component — actually gone, so a
    # component later created with the same id does not inherit it.
    assert _as("acme", lambda s: s.calculate_uptime("api")) == 100.0


# --- the page itself ---------------------------------------------------------


def test_the_summary_is_built_from_the_stored_rows(db):
    _as("acme", lambda s: s.create_incident("Elevated errors", affected_components=["api"]))
    resolved = _as("acme", lambda s: s.create_incident("Yesterday's blip"))
    _as("acme", lambda s: s.resolve_incident(resolved.id))
    _as(
        "acme",
        lambda s: s.create_maintenance(
            "Upgrade", scheduled_start=datetime.now(timezone.utc) + timedelta(days=1)
        ),
    )

    summary = _as("acme", lambda s: s.get_status_summary())

    # The API component is in a partial outage, so the page cannot say
    # everything is fine.
    assert summary["overall_status"] == "partial_outage"
    assert summary["overall_status_display"] == "Partial Outage"
    assert [i["title"] for i in summary["active_incidents"]] == ["Elevated errors"]
    assert [i["title"] for i in summary["recent_incidents"]] == ["Yesterday's blip"]
    assert [m["title"] for m in summary["upcoming_maintenances"]] == ["Upgrade"]
    assert set(summary["groups"]) == {"Core", "Infrastructure", "Services"}
    assert summary["uptimes_30d"]["dashboard"] == 100.0
    assert summary["uptimes_90d"]["dashboard"] == 100.0


def test_the_summary_is_scoped_to_the_org(db):
    _as("acme", lambda s: s.create_incident("Elevated errors", affected_components=["api"]))

    summary = _as("umbrella", lambda s: s.get_status_summary())

    assert summary["overall_status"] == "operational"
    assert summary["active_incidents"] == []
    assert len(summary["components"]) == 5


def test_timestamps_come_back_timezone_aware(db):
    component = _as("acme", lambda s: s.get_component("api"))
    incident = _as("acme", lambda s: s.create_incident("Outage"))
    window = _as("acme", lambda s: s.create_maintenance("Upgrade"))

    assert component.created_at.tzinfo is not None
    assert component.updated_at.tzinfo is not None
    assert incident.created_at.tzinfo is not None
    assert _as("acme", lambda s: s.get_incident(incident.id)).updates[0].created_at.tzinfo is not None
    assert _as("acme", lambda s: s.get_maintenance(window.id)).scheduled_end.tzinfo is not None
