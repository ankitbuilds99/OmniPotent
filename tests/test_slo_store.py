"""SLOs and their measurement series survive a restart, per org.

`_SLOS` and `_SLI_DATA` were module-level dicts. A restart deleted every
objective a customer had defined together with the data behind it, so the error
budget reset to "no data" and the burn-rate alerts went quiet immediately after
a deploy — the moment the budget is actually being spent. These tests pin the
durable behaviour, the org filter on every read and write, and the timestamp
handling the dicts never needed.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

import app.config as cfg
from app import tenant_context
from app.database import engine as db_engine
from app.slo.tracker import SLIType, SLOStatus, SLOTracker, SLOWindow


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


def _run(coro_factory):
    """One call on its own loop, as a separate request would make it."""
    return asyncio.run(coro_factory())


def _make(org: str = "acme", **kwargs):
    """Create an SLO through a throwaway tracker, as one request would."""
    return _run(
        lambda: SLOTracker(org).create_slo(
            name=kwargs.pop("name", "API availability"),
            service=kwargs.pop("service", "api-gateway"),
            sli_type=kwargs.pop("sli_type", SLIType.AVAILABILITY),
            target=kwargs.pop("target", 0.99),
            **kwargs,
        )
    )


def test_an_slo_outlives_the_process(db):
    slo = _make(target=0.999, window=SLOWindow.ROLLING_7D, team_id="platform", owner="ankit")

    # A brand new tracker, as a restarted worker would build. The dict-backed
    # version returned None here — the objective was simply gone.
    found = _run(lambda: SLOTracker("acme").get_slo(slo.id))

    assert found is not None
    assert found.name == "API availability"
    assert found.target == 0.999
    assert found.window is SLOWindow.ROLLING_7D
    assert found.sli_type is SLIType.AVAILABILITY
    assert found.team_id == "platform"
    assert found.owner == "ankit"


def test_one_orgs_slo_is_invisible_to_another(db):
    slo = _make("acme")

    other = SLOTracker("umbrella")
    assert _run(lambda: other.get_slo(slo.id)) is None
    assert _run(lambda: other.list_slos()) == []
    assert _run(lambda: other.delete_slo(slo.id)) is False
    assert _run(lambda: other.update_slo(slo.id, name="mine now")) is None
    assert _run(lambda: other.record_sli(slo.id, 1, 1)) is None
    # ...and none of that touched the owner's copy.
    assert _run(lambda: SLOTracker("acme").get_slo(slo.id)).name == "API availability"


def test_list_filters_by_service_and_team(db):
    _make(service="api-gateway", team_id="platform")
    _make(name="Checkout latency", service="checkout", team_id="payments")

    tracker = SLOTracker("acme")
    assert len(_run(lambda: tracker.list_slos())) == 2
    assert [s.service for s in _run(lambda: tracker.list_slos(service="checkout"))] == ["checkout"]
    assert [s.team_id for s in _run(lambda: tracker.list_slos(team_id="platform"))] == ["platform"]
    assert _run(lambda: tracker.list_slos(service="nothing-here")) == []


def test_updates_persist_and_leave_other_fields_alone(db):
    slo = _make()

    updated = _run(
        lambda: SLOTracker("acme").update_slo(slo.id, target=0.995, owner="sre", labels={"tier": "1"})
    )

    assert updated.target == 0.995
    assert updated.owner == "sre"
    assert updated.labels == {"tier": "1"}
    assert updated.name == slo.name
    assert updated.updated_at >= slo.updated_at

    # And a fresh tracker sees the same thing, not the pre-update copy.
    assert _run(lambda: SLOTracker("acme").get_slo(slo.id)).target == 0.995


def test_update_ignores_fields_that_are_not_updatable(db):
    """The key comes from the caller and now reaches a column. The dict version
    fed it to setattr() behind a hasattr() guard, which also let a caller
    overwrite id, org_id and created_at."""
    slo = _make()

    updated = _run(
        lambda: SLOTracker("acme").update_slo(slo.id, id="slo_stolen", org_id="umbrella", nonsense=1)
    )

    assert updated.id == slo.id
    assert updated.org_id == "acme"
    assert _run(lambda: SLOTracker("acme").get_slo(slo.id)) is not None


def test_deleting_an_slo_takes_its_measurements_with_it(db):
    slo = _make()
    _run(lambda: SLOTracker("acme").record_sli(slo.id, 990, 1000))

    assert _run(lambda: SLOTracker("acme").delete_slo(slo.id)) is True
    assert _run(lambda: SLOTracker("acme").get_slo(slo.id)) is None
    # Not just unreachable through the SLO — actually gone.
    assert _run(lambda: SLOTracker("acme").get_sli_data(slo.id)) == []


def test_deleting_something_that_is_not_there_is_a_miss_not_a_crash(db):
    assert _run(lambda: SLOTracker("acme").delete_slo("slo_nope")) is False
    assert _run(lambda: SLOTracker("acme").update_slo("slo_nope", name="x")) is None
    assert _run(lambda: SLOTracker("acme").get_sli_data("slo_nope")) == []


def test_the_measurement_series_survives_a_restart(db):
    slo = _make()
    _run(lambda: SLOTracker("acme").record_sli(slo.id, 990, 1000))
    _run(lambda: SLOTracker("acme").record_sli(slo.id, 999, 1000))

    data = _run(lambda: SLOTracker("acme").get_sli_data(slo.id))

    assert [s.total_count for s in data] == [1000, 1000]
    assert data[0].value == 0.99
    assert data[1].value == 0.999
    assert all(s.slo_id == slo.id for s in data)


def test_measurements_are_ordered_and_filtered_by_window(db):
    slo = _make()
    now = datetime.now(timezone.utc)
    for minutes, good in ((90, 900), (30, 950), (5, 999)):
        _run(
            lambda m=minutes, g=good: SLOTracker("acme").record_sli(
                slo.id, g, 1000, timestamp=now - timedelta(minutes=m)
            )
        )

    everything = _run(lambda: SLOTracker("acme").get_sli_data(slo.id))
    assert [s.good_count for s in everything] == [900, 950, 999]  # oldest first

    last_hour = _run(
        lambda: SLOTracker("acme").get_sli_data(slo.id, start_time=now - timedelta(hours=1))
    )
    assert [s.good_count for s in last_hour] == [950, 999]


def test_a_measurement_keeps_its_instant_across_timezones(db):
    """SQLAlchemy's SQLite DATETIME writes the wall clock and drops the offset,
    so 09:00+05:30 would read back as 09:00 UTC — hours in the future, in the
    wrong burn-rate window."""
    slo = _make()
    tehran = timezone(timedelta(hours=3, minutes=30))
    stamp = datetime(2026, 3, 1, 9, 0, tzinfo=tehran)

    _run(lambda: SLOTracker("acme").record_sli(slo.id, 1, 1, timestamp=stamp))

    read_back = _run(lambda: SLOTracker("acme").get_sli_data(slo.id))[0]
    assert read_back.timestamp.tzinfo is not None
    assert read_back.timestamp == stamp
    assert read_back.timestamp.hour == 5  # 09:00+03:30 is 05:30 UTC
    assert read_back.timestamp.minute == 30


def test_measurements_older_than_twice_the_window_are_pruned(db):
    slo = _make(window=SLOWindow.ROLLING_1H)
    now = datetime.now(timezone.utc)

    _run(lambda: SLOTracker("acme").record_sli(slo.id, 1, 1, timestamp=now - timedelta(hours=5)))
    _run(lambda: SLOTracker("acme").record_sli(slo.id, 1, 1, timestamp=now))

    # The stale sample is dropped on the next write, as the dict version did —
    # it matters more now that rows outlive the process.
    kept = _run(lambda: SLOTracker("acme").get_sli_data(slo.id))
    assert len(kept) == 1


def test_error_budget_reads_the_stored_series(db):
    slo = _make(target=0.99)
    _run(lambda: SLOTracker("acme").record_sli(slo.id, 995, 1000))

    budget = _run(lambda: SLOTracker("acme").calculate_error_budget(slo.id))

    assert budget is not None
    assert budget.total_budget == pytest.approx(0.01)
    assert budget.current_sli == pytest.approx(0.995)
    assert budget.total_count == 1000
    assert budget.good_count == 995
    assert budget.status is not SLOStatus.NO_DATA
    assert budget.window_start.tzinfo is not None


def test_error_budget_with_no_measurements_says_no_data(db):
    slo = _make(target=0.99)

    budget = _run(lambda: SLOTracker("acme").calculate_error_budget(slo.id))

    assert budget.status is SLOStatus.NO_DATA
    assert budget.remaining_percent == 100.0
    assert _run(lambda: SLOTracker("acme").calculate_error_budget("slo_nope")) is None


def test_burn_rate_and_status_run_against_the_stored_series(db):
    slo = _make(target=0.99)
    _run(lambda: SLOTracker("acme").record_sli(slo.id, 900, 1000))

    burn = _run(lambda: SLOTracker("acme").calculate_burn_rate(slo.id))
    assert burn is not None
    assert burn.short_window == "1h"
    assert burn.long_window == "6h"
    assert burn.short_window_burn_rate > 0

    status = _run(lambda: SLOTracker("acme").get_slo_status(slo.id))
    assert status["slo"]["id"] == slo.id
    assert status["total_events"] == 1000
    assert status["good_events"] == 900
    assert _run(lambda: SLOTracker("acme").get_slo_status("slo_nope")) is None


def test_a_report_covers_the_requested_period(db):
    slo = _make(target=0.99, window=SLOWindow.ROLLING_30D)
    now = datetime.now(timezone.utc)
    _run(lambda: SLOTracker("acme").record_sli(slo.id, 980, 1000, timestamp=now - timedelta(days=1)))

    report = _run(
        lambda: SLOTracker("acme").generate_report(slo.id, now - timedelta(days=2), now)
    )

    assert report is not None
    assert report.total_events == 1000
    assert report.good_events == 980
    assert report.bad_events == 20
    assert report.achieved == pytest.approx(0.98)
    assert report.compliant is False
    assert len(report.daily_values) == 2
    assert _run(lambda: SLOTracker("acme").generate_report("slo_nope", now, now)) is None


def test_timestamps_come_back_timezone_aware(db):
    slo = _make()

    found = _run(lambda: SLOTracker("acme").get_slo(slo.id))
    assert found.created_at.tzinfo is not None
    assert found.updated_at.tzinfo is not None
