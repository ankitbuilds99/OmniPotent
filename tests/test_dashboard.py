"""Tests for customer dashboard metrics and trends."""

import pytest
from datetime import datetime, timedelta, timezone


class TestDashboardMetrics:
    """Tests for dashboard metrics calculation."""

    def test_mttr_stats_creation(self):
        """Test MTTRStats dataclass."""
        from app.dashboard.metrics import MTTRStats

        stats = MTTRStats(
            current_mttr_minutes=15.0,
            previous_mttr_minutes=25.0,
        )

        stats.improvement_percent = (
            (stats.previous_mttr_minutes - stats.current_mttr_minutes)
            / stats.previous_mttr_minutes * 100
        )

        assert stats.current_mttr_minutes == 15.0
        assert stats.improved is True
        assert stats.improvement_percent == 40.0

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
        )

        metrics.usage = UsageStats(
            investigations_this_period=15,
        )

        metrics.calculate_health_score()

        assert metrics.health_score >= 50

    def test_record_investigation(self):
        """Test recording investigations."""
        from app.dashboard.metrics import (
            record_investigation,
            InvestigationOutcome,
        )

        now = datetime.now(timezone.utc)
        started = now - timedelta(minutes=5)

        record = record_investigation(
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

    def test_get_dashboard_metrics(self):
        """Test getting complete dashboard metrics."""
        from app.dashboard.metrics import (
            get_dashboard_metrics,
            record_investigation,
            InvestigationOutcome,
            _investigation_store,
        )

        _investigation_store.clear()

        org_id = "org_dashboard_test"
        now = datetime.now(timezone.utc)

        for i in range(5):
            started = now - timedelta(days=i, minutes=10)
            completed = now - timedelta(days=i)
            record_investigation(
                org_id=org_id,
                investigation_id=f"inv_{i}",
                started_at=started,
                completed_at=completed,
                outcome=InvestigationOutcome.ROOT_CAUSE_FOUND,
                tokens_used=1000 + i * 100,
                time_saved_minutes=20.0,
            )

        metrics = get_dashboard_metrics(org_id, period_days=30)

        assert metrics.org_id == org_id
        assert metrics.summary.total == 5
        assert metrics.usage.total_investigations == 5
        assert metrics.roi.investigations_completed == 5

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

    def test_trend_direction_improving(self):
        """Test detecting improving trend."""
        from app.dashboard.trends import TrendAnalyzer, DataPoint, TrendDirection
        from datetime import datetime, timedelta, timezone

        analyzer = TrendAnalyzer()

        now = datetime.now(timezone.utc)
        data_points = [
            DataPoint(timestamp=now - timedelta(days=i), value=100 - i * 5)
            for i in range(10, 0, -1)
        ]

        trend = analyzer.analyze("mttr", data_points)

        assert trend.direction in (TrendDirection.IMPROVING, TrendDirection.STABLE)

    def test_trend_direction_declining(self):
        """Test detecting declining trend."""
        from app.dashboard.trends import TrendAnalyzer, DataPoint, TrendDirection
        from datetime import datetime, timedelta, timezone

        analyzer = TrendAnalyzer(significance_threshold=0.05)

        now = datetime.now(timezone.utc)
        data_points = [
            DataPoint(timestamp=now - timedelta(days=i), value=50 + i * 10)
            for i in range(10, 0, -1)
        ]

        trend = analyzer.analyze("error_rate", data_points)

        assert trend.direction == TrendDirection.DECLINING

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
