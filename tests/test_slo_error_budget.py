"""The error budget and burn rate report the numbers they claim to.

Three formulas were wrong in the same way — they subtracted the objective from
the measurement instead of measuring the errors — and the burn rate was then
multiplied by the ratio of the SLO window to the alerting window. Together they
made the SLO page useless in both directions at once: a service that had spent
its entire budget showed a full one, and a service beating its target by ten
times paged as a critical burn.

These tests are the arithmetic, pinned. They run against real stored
measurements because that is the only way into the calculation.
"""

from __future__ import annotations

import asyncio

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
    return asyncio.run(coro_factory())


def _slo_with(good: int, total: int, target: float = 0.99, **kwargs):
    """An SLO carrying one measurement, as separate requests would leave it."""
    slo = _run(
        lambda: SLOTracker("acme").create_slo(
            name="API availability",
            service="api-gateway",
            sli_type=SLIType.AVAILABILITY,
            target=target,
            **kwargs,
        )
    )
    _run(lambda: SLOTracker("acme").record_sli(slo.id, good, total))
    return slo


def test_consumed_is_the_error_rate_not_the_overshoot(db):
    """99.5% against a 99% target has spent half of a 1% budget."""
    slo = _slo_with(995, 1000, target=0.99)

    budget = _run(lambda: SLOTracker("acme").calculate_error_budget(slo.id))

    assert budget.current_sli == pytest.approx(0.995)
    assert budget.total_budget == pytest.approx(0.01)
    # `max(0, target - current_sli)` read 0 here, and 100% remaining.
    assert budget.consumed == pytest.approx(0.005)
    assert budget.remaining == pytest.approx(0.005)
    assert budget.remaining_percent == pytest.approx(50.0)
    assert budget.burn_rate == pytest.approx(0.5)
    assert budget.status is SLOStatus.HEALTHY


def test_a_budget_spent_down_to_nothing_reads_as_breached(db):
    """Sitting exactly on the target means the budget is gone, not untouched."""
    slo = _slo_with(990, 1000, target=0.99)

    budget = _run(lambda: SLOTracker("acme").calculate_error_budget(slo.id))

    assert budget.consumed == pytest.approx(0.01)
    assert budget.remaining == pytest.approx(0.0)
    assert budget.remaining_percent == pytest.approx(0.0)
    assert budget.status is SLOStatus.BREACHED


def test_overspending_the_budget_does_not_go_backwards(db):
    slo = _slo_with(900, 1000, target=0.99)

    budget = _run(lambda: SLOTracker("acme").calculate_error_budget(slo.id))

    assert budget.consumed == pytest.approx(0.1)  # ten times the budget
    assert budget.remaining == 0
    assert budget.burn_rate == pytest.approx(10.0)
    assert budget.status is SLOStatus.BREACHED


def test_burn_rate_is_not_scaled_by_the_window_length(db):
    """A burn rate of 2x is 2x whether it is read over an hour or six.

    The rate answers "how many times faster than the budget allows", which is
    the error rate over the allowed error. Dividing that again by the window's
    share of the SLO period turned a 1-hour reading against a 30-day objective
    into 720x.
    """
    slo = _slo_with(980, 1000, target=0.99, window=SLOWindow.ROLLING_30D)

    burn = _run(lambda: SLOTracker("acme").calculate_burn_rate(slo.id))

    assert burn.short_window_burn_rate == pytest.approx(2.0)
    assert burn.long_window_burn_rate == pytest.approx(2.0)
    assert burn.severity == "low"
    assert burn.is_alerting is False


def test_a_service_beating_its_target_does_not_page(db):
    """99.9% against a 99% target is a tenth of the allowed burn."""
    slo = _slo_with(999, 1000, target=0.99, window=SLOWindow.ROLLING_30D)

    burn = _run(lambda: SLOTracker("acme").calculate_burn_rate(slo.id))

    assert burn.short_window_burn_rate == pytest.approx(0.1)
    # This was 72x, over the 14.4 threshold: "critical", every time, forever.
    assert burn.is_alerting is False
    assert burn.severity == "none"


def test_a_real_fast_burn_still_pages(db):
    """The fix must not silence the alert it was drowning out."""
    slo = _slo_with(800, 1000, target=0.99, window=SLOWindow.ROLLING_30D)

    burn = _run(lambda: SLOTracker("acme").calculate_burn_rate(slo.id))

    assert burn.short_window_burn_rate == pytest.approx(20.0)
    assert burn.is_alerting is True
    assert burn.severity == "critical"


def test_report_consumption_counts_the_whole_error_rate(db):
    """98% against a 99% target spent twice the budget over the period."""
    from datetime import datetime, timedelta, timezone

    slo = _slo_with(980, 1000, target=0.99)
    now = datetime.now(timezone.utc)  # after the measurement, so it is inside the period

    report = _run(
        lambda: SLOTracker("acme").generate_report(slo.id, now - timedelta(days=1), now)
    )

    assert report.achieved == pytest.approx(0.98)
    assert report.compliant is False
    # `(target - achieved) / (1 - target)` gave 1.0 — short by exactly one
    # whole budget, so a period that spent 100% of it reported 0%.
    assert report.error_budget_consumed == pytest.approx(2.0)
