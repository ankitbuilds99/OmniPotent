"""Tests for customer dashboard metrics and trends.

The metrics half of this file is now database-backed. Investigation history used
to live in a module-level dict, so these tests could call `record_investigation`
and read it straight back within one process. They now go through the
`investigations` table, which is what makes three behaviours testable at all:
history surviving a restart, one row per investigation thread rather than per
turn, and the absence of any invented MTTR baseline.
"""

import pytest
from datetime import datetime, timedelta, timezone

from app.config import get_settings
from app.database.engine import close_db


@pytest.fixture
async def dashboard_db(tmp_path, monkeypatch):
    """A throwaway database per test.

    `close_db()` on entry as well as exit: a test that failed part-way, or one
    that ran before this file, can leave an engine pointing at another directory,
    and the engine is module-global.
    """
    monkeypatch.setenv("COPILOT_STATE_DIR", str(tmp_path))
    get_settings.cache_clear()
    await close_db()
    yield
    await close_db()
    get_settings.cache_clear()


class TestDashboardMetrics:
    """Tests for dashboard metrics calculation."""

    def test_mttr_stats_creation(self):
        """Test MTTRStats dataclass."""
        from app.dashboard.metrics import MTTRStats

        stats = MTTRStats(
            current_mttr_minutes=15.0,
            previous_mttr_minutes=25.0,
            # Set explicitly: `improved` is false without it, because a previous
            # value that was never measured is not something to compare against.
            comparison_available=True,
        )

        stats.improvement_percent = (
            (stats.previous_mttr_minutes - stats.current_mttr_minutes)
            / stats.previous_mttr_minutes * 100
        )

        assert stats.current_mttr_minutes == 15.0
        assert stats.improved is True
        assert stats.improvement_percent == 40.0

    def test_mttr_improvement_is_withheld_without_a_comparison(self):
        """A faster-looking period is not an improvement over nothing.

        The previous implementation compared against a hardcoded 45-minute
        "industry baseline" nobody had measured, so every fresh deployment was
        told its MTTR had improved by whatever its first investigations happened
        to take.
        """
        from app.dashboard.metrics import MTTRStats

        stats = MTTRStats(current_mttr_minutes=10.0, improvement_percent=78.0)

        assert stats.comparison_available is False
        assert stats.improved is False
        assert stats.to_dict()["improvement_percent"] is None

    def test_roi_calculation(self):
        """Test ROI metrics calculation."""
        from app.dashboard.metrics import ROIMetrics

        roi = ROIMetrics(
            time_saved_hours=10.0,
            investigations_completed=20,
            downtime_prevented_minutes=30.0,
            hourly_engineer_cost_usd=150.0,
            downtime_cost_per_minute_usd=500.0,
        )

        roi.calculate_savings()

        assert roi.estimated_cost_savings_usd == 10 * 150 + 30 * 500
        assert roi.avg_time_saved_per_investigation_minutes == 30.0

    def test_investigation_summary(self):
        """Test investigation summary calculation."""
        from app.dashboard.metrics import InvestigationSummary

        summary = InvestigationSummary(
            total=100,
            successful=75,
            partial=15,
            failed=5,
            abstained=5,
        )

        summary.calculate_rates()

        assert summary.success_rate == 0.75
        assert summary.abstention_rate == 0.05

    def test_dashboard_health_score(self):
        """Test health score calculation."""
        from app.dashboard.metrics import (
            DashboardMetrics,
            MTTRStats,
            InvestigationSummary,
            UsageStats,
        )

        metrics = DashboardMetrics()
        metrics.summary = InvestigationSummary(
            total=100,
            successful=85,
            abstained=5,
        )
        metrics.summary.calculate_rates()

        metrics.mttr = MTTRStats(
            current_mttr_minutes=10.0,
            previous_mttr_minutes=20.0,
            improvement_percent=50.0,
            comparison_available=True,
        )

        metrics.usage = UsageStats(
            investigations_this_period=15,
        )

        metrics.calculate_health_score()

        # 30 (success rate) + 25 (MTTR improved) + 20 (abstention) + 15 (volume).
        assert metrics.health_score == 90
        assert metrics.health_score_max == 100

    def test_health_score_is_out_of_75_when_mttr_cannot_be_judged(self):
        """The unscoreable MTTR band comes off the denominator, not the score.

        Scoring it out of 100 regardless would report a first-period deployment
        as 65/100 — a mediocre-sounding number for "we have no trend yet".
        """
        from app.dashboard.metrics import (
            DashboardMetrics,
            InvestigationSummary,
            UsageStats,
        )

        metrics = DashboardMetrics()
        metrics.summary = InvestigationSummary(total=100, successful=85, abstained=5)
        metrics.summary.calculate_rates()
        metrics.usage = UsageStats(investigations_this_period=15)

        metrics.calculate_health_score()

        assert metrics.mttr.comparison_available is False
        assert metrics.health_score == 65
        assert metrics.health_score_max == 75

    async def test_record_investigation(self, dashboard_db):
        """Test recording investigations."""
        from app.dashboard.metrics import (
            record_investigation,
            InvestigationOutcome,
        )

        now = datetime.now(timezone.utc)
        started = now - timedelta(minutes=5)

        record = await record_investigation(
            org_id="org_test",
            investigation_id="inv_001",
            started_at=started,
            completed_at=now,
            outcome=InvestigationOutcome.ROOT_CAUSE_FOUND,
            tokens_used=1500,
            severity="SEV2",
            service="checkout",
            time_saved_minutes=30.0,
        )

        assert record.id == "inv_001"
        assert record.outcome == InvestigationOutcome.ROOT_CAUSE_FOUND
        assert record.duration_seconds == pytest.approx(300.0, rel=0.1)

    async def test_get_dashboard_metrics(self, dashboard_db):
        """Test getting complete dashboard metrics."""
        from app.dashboard.metrics import (
            get_dashboard_metrics,
            record_investigation,
            InvestigationOutcome,
        )

        org_id = "org_dashboard_test"
        now = datetime.now(timezone.utc)

        for i in range(5):
            started = now - timedelta(days=i, minutes=10)
            completed = now - timedelta(days=i)
            await record_investigation(
                org_id=org_id,
                investigation_id=f"inv_{i}",
                started_at=started,
                completed_at=completed,
                outcome=InvestigationOutcome.ROOT_CAUSE_FOUND,
                tokens_used=1000 + i * 100,
                time_saved_minutes=20.0,
            )

        metrics = await get_dashboard_metrics(org_id, period_days=30)

        assert metrics.org_id == org_id
        assert metrics.summary.total == 5
        assert metrics.usage.total_investigations == 5
        assert metrics.roi.investigations_completed == 5

    async def test_history_is_scoped_to_one_organization(self, dashboard_db):
        """One table, many tenants — the dashboard must not leak across them."""
        from app.dashboard.metrics import (
            get_dashboard_metrics,
            record_investigation,
            InvestigationOutcome,
        )

        now = datetime.now(timezone.utc)
        for org in ("org_a", "org_b"):
            await record_investigation(
                org_id=org,
                investigation_id="shared_thread_id",
                started_at=now - timedelta(minutes=5),
                completed_at=now,
                outcome=InvestigationOutcome.ROOT_CAUSE_FOUND,
                tokens_used=100,
            )

        metrics = await get_dashboard_metrics("org_a", period_days=30)

        assert metrics.summary.total == 1
        assert metrics.usage.tokens_used == 100

    async def test_a_multi_turn_thread_counts_as_one_investigation(self, dashboard_db):
        """Recording the same thread twice updates its row instead of adding one.

        Every turn of a conversation reaches the recording hook. Appending per
        turn — which the dict-backed version did — meant a ten-turn investigation
        was reported as ten, inflating volume, MTTR sample size and the ROI
        derived from them.
        """
        from app.dashboard.metrics import (
            get_dashboard_metrics,
            record_investigation,
            InvestigationOutcome,
        )

        org_id = "org_multi_turn"
        started = datetime.now(timezone.utc) - timedelta(minutes=30)

        await record_investigation(
            org_id=org_id,
            investigation_id="thread_1",
            started_at=started,
            completed_at=started + timedelta(minutes=5),
            outcome=InvestigationOutcome.PARTIAL_DIAGNOSIS,
            tokens_used=1000,
            tools_used=["search_logs"],
        )
        # A later turn on the same thread: starts after the first, finishes later.
        record = await record_investigation(
            org_id=org_id,
            investigation_id="thread_1",
            started_at=started + timedelta(minutes=6),
            completed_at=started + timedelta(minutes=20),
            outcome=InvestigationOutcome.ROOT_CAUSE_FOUND,
            tokens_used=500,
            severity="SEV1",
            service="checkout",
            tools_used=["search_logs", "get_metric"],
        )

        # Duration is measured from the earliest start, not the latest turn's.
        assert record.duration_seconds == pytest.approx(20 * 60)
        assert record.tokens_used == 1500
        assert record.outcome == InvestigationOutcome.ROOT_CAUSE_FOUND
        assert record.severity == "SEV1"
        assert record.service == "checkout"
        # Merged by reassignment — appending in place leaves the JSON column clean
        # and the write is silently dropped.
        assert sorted(record.tools_used) == ["get_metric", "search_logs"]

        metrics = await get_dashboard_metrics(org_id, period_days=30)
        assert metrics.summary.total == 1
        assert metrics.usage.total_investigations == 1

    async def test_history_survives_the_process_that_recorded_it(self, dashboard_db):
        """The entire point of the move off a module-level dict.

        Disposing the engine drops every connection and the cached session
        factory, which is as close to a restart as a test gets without a
        subprocess. The dict-backed version returned zero here — and returned
        zero on any replica that had not personally served the investigation.
        """
        from app.dashboard.metrics import (
            get_dashboard_metrics,
            record_investigation,
            InvestigationOutcome,
        )

        org_id = "org_durable"
        now = datetime.now(timezone.utc)
        await record_investigation(
            org_id=org_id,
            investigation_id="inv_durable",
            started_at=now - timedelta(minutes=12),
            completed_at=now,
            outcome=InvestigationOutcome.ROOT_CAUSE_FOUND,
            tokens_used=2000,
        )

        await close_db()

        metrics = await get_dashboard_metrics(org_id, period_days=30)
        assert metrics.summary.total == 1
        assert metrics.usage.tokens_used == 2000
        assert metrics.summary.recent_investigations[0]["id"] == "inv_durable"

    async def test_mttr_compares_against_a_real_previous_period(self, dashboard_db):
        """A comparison appears only once there is a prior period to compare to."""
        from app.dashboard.metrics import (
            get_dashboard_metrics,
            record_investigation,
            InvestigationOutcome,
        )

        org_id = "org_mttr"
        now = datetime.now(timezone.utc)

        # Current period (last 7 days): 10 minutes each.
        for i in range(2):
            started = now - timedelta(days=i + 1)
            await record_investigation(
                org_id=org_id,
                investigation_id=f"cur_{i}",
                started_at=started,
                completed_at=started + timedelta(minutes=10),
                outcome=InvestigationOutcome.ROOT_CAUSE_FOUND,
            )

        first = await get_dashboard_metrics(org_id, period_days=7)
        assert first.mttr.comparison_available is False
        assert first.mttr.to_dict()["improvement_percent"] is None

        # Previous period (8-14 days ago): 20 minutes each.
        for i in range(2):
            started = now - timedelta(days=9 + i)
            await record_investigation(
                org_id=org_id,
                investigation_id=f"prev_{i}",
                started_at=started,
                completed_at=started + timedelta(minutes=20),
                outcome=InvestigationOutcome.ROOT_CAUSE_FOUND,
            )

        second = await get_dashboard_metrics(org_id, period_days=7)
        assert second.mttr.comparison_available is True
        assert second.mttr.current_mttr_minutes == pytest.approx(10.0)
        assert second.mttr.previous_mttr_minutes == pytest.approx(20.0)
        assert second.mttr.improvement_percent == pytest.approx(50.0)
        assert second.mttr.improved is True

    async def test_investigation_series_is_oldest_first_and_timezone_aware(
        self, dashboard_db
    ):
        """Trend bucketing compares these against an aware `now()`.

        SQLite returns naive datetimes regardless of what was written, so an
        un-reattached tzinfo surfaces as a TypeError on the first comparison.
        """
        from app.dashboard.metrics import (
            get_investigation_series,
            record_investigation,
            InvestigationOutcome,
        )

        org_id = "org_series"
        now = datetime.now(timezone.utc)
        for i in (3, 1, 2):
            started = now - timedelta(days=i)
            await record_investigation(
                org_id=org_id,
                investigation_id=f"series_{i}",
                started_at=started,
                completed_at=started + timedelta(minutes=i),
                outcome=InvestigationOutcome.ROOT_CAUSE_FOUND,
                tokens_used=i * 100,
            )

        series = await get_investigation_series(org_id, period_days=30)

        assert len(series) == 3
        assert [ts for ts, _, _ in series] == sorted(ts for ts, _, _ in series)
        assert all(ts.tzinfo is not None for ts, _, _ in series)
        assert all(ts < now for ts, _, _ in series)
        assert sorted(tokens for _, _, tokens in series) == [100, 200, 300]

    async def test_clear_investigations_is_scoped(self, dashboard_db):
        """Clearing one org must not empty another's history."""
        from app.dashboard.metrics import (
            clear_investigations,
            get_dashboard_metrics,
            record_investigation,
            InvestigationOutcome,
        )

        now = datetime.now(timezone.utc)
        for org in ("org_keep", "org_drop"):
            await record_investigation(
                org_id=org,
                investigation_id="inv",
                started_at=now - timedelta(minutes=1),
                completed_at=now,
                outcome=InvestigationOutcome.ROOT_CAUSE_FOUND,
            )

        await clear_investigations("org_drop")

        assert (await get_dashboard_metrics("org_drop")).summary.total == 0
        assert (await get_dashboard_metrics("org_keep")).summary.total == 1

    def test_calculate_roi_function(self):
        """Test standalone ROI calculation."""
        from app.dashboard.metrics import calculate_roi

        result = calculate_roi(
            time_saved_minutes=120,
            incidents_resolved=3,
            downtime_prevented_minutes=15,
            hourly_engineer_cost=150.0,
            downtime_cost_per_minute=500.0,
        )

        assert result["time_saved_hours"] == 2.0
        assert result["engineer_savings_usd"] == 300.0
        assert result["downtime_savings_usd"] == 7500.0
        assert result["total_savings_usd"] == 7800.0


class TestTrendAnalysis:
    """Tests for trend analysis."""

    def test_rising_mttr_is_a_decline_not_an_improvement(self):
        """Direction is stated in terms of health, not of the number.

        Every metric was previously treated as higher-is-better, so a 45% rise in
        mean-time-to-resolution was reported on the dashboard as "mttr improved
        by 45.5%".
        """
        from app.dashboard.trends import TrendAnalyzer, DataPoint, TrendDirection
        from datetime import datetime, timedelta, timezone

        analyzer = TrendAnalyzer()

        now = datetime.now(timezone.utc)
        # Oldest 50 minutes, newest 95 — resolution is getting slower.
        data_points = [
            DataPoint(timestamp=now - timedelta(days=i), value=100 - i * 5)
            for i in range(10, 0, -1)
        ]

        trend = analyzer.analyze("mttr", data_points)

        assert trend.direction == TrendDirection.DECLINING
        assert trend.change_percent > 0, "the number itself rose; only the sign says so"
        assert "declined" in trend.description

    def test_falling_mttr_is_an_improvement(self):
        """The mirror case, so the fix is not just an inverted constant."""
        from app.dashboard.trends import TrendAnalyzer, DataPoint, TrendDirection
        from datetime import datetime, timedelta, timezone

        analyzer = TrendAnalyzer()

        now = datetime.now(timezone.utc)
        data_points = [
            DataPoint(timestamp=now - timedelta(days=i), value=50 + i * 5)
            for i in range(10, 0, -1)
        ]

        trend = analyzer.analyze("mttr_minutes", data_points)

        assert trend.direction == TrendDirection.IMPROVING
        assert trend.change_percent < 0

    def test_falling_error_rate_is_an_improvement(self):
        """Test detecting improvement in a lower-is-better metric."""
        from app.dashboard.trends import TrendAnalyzer, DataPoint, TrendDirection
        from datetime import datetime, timedelta, timezone

        analyzer = TrendAnalyzer(significance_threshold=0.05)

        now = datetime.now(timezone.utc)
        data_points = [
            DataPoint(timestamp=now - timedelta(days=i), value=50 + i * 10)
            for i in range(10, 0, -1)
        ]

        trend = analyzer.analyze("error_rate", data_points)

        assert trend.direction == TrendDirection.IMPROVING

    def test_falling_success_rate_is_a_decline(self):
        """A higher-is-better metric going down is still a decline."""
        from app.dashboard.trends import TrendAnalyzer, DataPoint, TrendDirection
        from datetime import datetime, timedelta, timezone

        analyzer = TrendAnalyzer(significance_threshold=0.05)

        now = datetime.now(timezone.utc)
        data_points = [
            DataPoint(timestamp=now - timedelta(days=i), value=50 + i * 10)
            for i in range(10, 0, -1)
        ]

        trend = analyzer.analyze("success_rate", data_points)

        assert trend.direction == TrendDirection.DECLINING

    def test_explicit_polarity_overrides_the_name_guess(self):
        """Names are a fallback; a caller that knows says so."""
        from app.dashboard.trends import (
            DataPoint,
            MetricPolarity,
            TrendAnalyzer,
            TrendDirection,
        )
        from datetime import datetime, timedelta, timezone

        analyzer = TrendAnalyzer()
        now = datetime.now(timezone.utc)
        # "tokens_per_day" reads neutral, so the name guess calls a rise good.
        data_points = [
            DataPoint(timestamp=now - timedelta(days=i), value=100 - i * 5)
            for i in range(10, 0, -1)
        ]

        guessed = analyzer.analyze("tokens_per_day", data_points)
        stated = analyzer.analyze(
            "tokens_per_day", data_points, polarity=MetricPolarity.LOWER_IS_BETTER
        )

        assert guessed.direction == TrendDirection.IMPROVING
        assert stated.direction == TrendDirection.DECLINING
        assert stated.to_dict()["polarity"] == "lower_is_better"

    def test_infer_polarity(self):
        """The name-based guess, and its limits."""
        from app.dashboard.trends import MetricPolarity, infer_polarity

        assert infer_polarity("mttr_minutes") == MetricPolarity.LOWER_IS_BETTER
        assert infer_polarity("p90_latency") == MetricPolarity.LOWER_IS_BETTER
        assert infer_polarity("avg_investigation_duration") == MetricPolarity.LOWER_IS_BETTER
        assert infer_polarity("success_rate") == MetricPolarity.HIGHER_IS_BETTER
        # No hint in the name — hence the explicit `polarity` argument.
        assert infer_polarity("tokens_per_day") == MetricPolarity.HIGHER_IS_BETTER

    def test_trend_stable(self):
        """Test detecting stable trend."""
        from app.dashboard.trends import TrendAnalyzer, DataPoint, TrendDirection
        from datetime import datetime, timedelta, timezone

        analyzer = TrendAnalyzer(significance_threshold=0.2)

        now = datetime.now(timezone.utc)
        data_points = [
            DataPoint(timestamp=now - timedelta(days=i), value=100 + (i % 2))
            for i in range(10, 0, -1)
        ]

        trend = analyzer.analyze("requests", data_points)

        assert trend.direction == TrendDirection.STABLE

    def test_anomaly_detection_spike(self):
        """Test detecting spike anomaly."""
        from app.dashboard.trends import TrendAnalyzer, DataPoint, AnomalyType
        from datetime import datetime, timedelta, timezone

        analyzer = TrendAnalyzer(anomaly_std_threshold=2.0)

        now = datetime.now(timezone.utc)
        data_points = [
            DataPoint(timestamp=now - timedelta(days=i), value=100 + (i % 3))
            for i in range(10, 1, -1)
        ]
        data_points.append(DataPoint(timestamp=now, value=500))

        trend = analyzer.analyze("metric", data_points)

        assert trend.anomaly == AnomalyType.SPIKE

    def test_anomaly_detection_drop(self):
        """Test detecting drop anomaly."""
        from app.dashboard.trends import TrendAnalyzer, DataPoint, AnomalyType
        from datetime import datetime, timedelta, timezone

        analyzer = TrendAnalyzer(anomaly_std_threshold=2.0)

        now = datetime.now(timezone.utc)
        data_points = [
            DataPoint(timestamp=now - timedelta(days=i), value=100 + (i % 3))
            for i in range(10, 1, -1)
        ]
        data_points.append(DataPoint(timestamp=now, value=10))

        trend = analyzer.analyze("metric", data_points)

        assert trend.anomaly == AnomalyType.DROP

    def test_simple_forecast(self):
        """Test forecasting next value."""
        from app.dashboard.trends import TrendAnalyzer, DataPoint
        from datetime import datetime, timedelta, timezone

        analyzer = TrendAnalyzer()

        now = datetime.now(timezone.utc)
        data_points = [
            DataPoint(timestamp=now - timedelta(days=i), value=i * 10)
            for i in range(10, 0, -1)
        ]

        trend = analyzer.analyze("metric", data_points)

        assert trend.forecast_next is not None

    def test_calculate_trends(self):
        """Test calculating trends for multiple metrics."""
        from app.dashboard.trends import calculate_trends
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)

        metrics = {
            "mttr": [
                (now - timedelta(days=i), 30 - i)
                for i in range(7, 0, -1)
            ],
            "error_rate": [
                (now - timedelta(days=i), 0.05 + i * 0.01)
                for i in range(7, 0, -1)
            ],
        }

        trends = calculate_trends(metrics)

        assert "mttr" in trends
        assert "error_rate" in trends
        assert trends["mttr"].metric_name == "mttr"

    def test_calculate_trends_accepts_stated_polarities(self):
        """Polarity survives the bulk entry point, not just `analyze`."""
        from app.dashboard.trends import MetricPolarity, calculate_trends, TrendDirection
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        rising = {
            "tokens_per_day": [(now - timedelta(days=i), 100 - i * 5) for i in range(10, 0, -1)]
        }

        trends = calculate_trends(
            rising, polarities={"tokens_per_day": MetricPolarity.LOWER_IS_BETTER}
        )

        assert trends["tokens_per_day"].direction == TrendDirection.DECLINING

    def test_trend_report_generation(self):
        """Test generating trend report."""
        from app.dashboard.trends import (
            calculate_trends,
            generate_trend_report,
        )
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)

        metrics = {
            "mttr": [
                (now - timedelta(days=i), 30 - i * 2)
                for i in range(7, 0, -1)
            ],
        }

        trends = calculate_trends(metrics)
        report = generate_trend_report(trends)

        assert "summary" in report
        assert "trends" in report
        assert report["summary"]["total_metrics"] == 1

    def test_insufficient_data(self):
        """Test handling insufficient data."""
        from app.dashboard.trends import TrendAnalyzer, DataPoint, TrendDirection
        from datetime import datetime, timezone

        analyzer = TrendAnalyzer()

        data_points = [
            DataPoint(timestamp=datetime.now(timezone.utc), value=100)
        ]

        trend = analyzer.analyze("metric", data_points)

        assert trend.direction == TrendDirection.UNKNOWN
        assert "Insufficient data" in trend.description


class TestUsageStats:
    """Tests for usage statistics."""

    def test_usage_stats_creation(self):
        """Test UsageStats dataclass."""
        from app.dashboard.metrics import UsageStats

        stats = UsageStats(
            total_investigations=100,
            investigations_this_period=25,
            tokens_used=150000,
            active_users=5,
        )

        assert stats.total_investigations == 100
        assert stats.investigations_this_period == 25

    def test_usage_stats_to_dict(self):
        """Test usage stats serialization."""
        from app.dashboard.metrics import UsageStats

        stats = UsageStats(
            total_investigations=50,
            tokens_used=75000,
            avg_investigation_duration_seconds=300.5,
        )

        data = stats.to_dict()

        assert data["total_investigations"] == 50
        assert data["tokens_used"] == 75000
        assert data["avg_investigation_duration_seconds"] == 300.5


class TestInvestigationOutcomes:
    """Tests for investigation outcome tracking."""

    def test_outcome_enum_values(self):
        """Test InvestigationOutcome enum."""
        from app.dashboard.metrics import InvestigationOutcome

        assert InvestigationOutcome.ROOT_CAUSE_FOUND.value == "root_cause_found"
        assert InvestigationOutcome.ABSTAINED.value == "abstained"

    def test_investigation_record(self):
        """Test InvestigationRecord dataclass."""
        from app.dashboard.metrics import InvestigationRecord, InvestigationOutcome
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)

        record = InvestigationRecord(
            id="inv_123",
            started_at=now,
            completed_at=now,
            outcome=InvestigationOutcome.RESOLVED,
            tokens_used=1500,
            tools_used=["search_logs", "get_metric"],
        )

        assert record.id == "inv_123"
        assert record.outcome == InvestigationOutcome.RESOLVED
        assert len(record.tools_used) == 2
