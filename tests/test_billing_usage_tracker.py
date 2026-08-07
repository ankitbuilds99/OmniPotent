"""Tests for billing usage tracker module."""

from __future__ import annotations

import pytest
from datetime import datetime, date, timedelta, timezone
from uuid import uuid4

from app.billing.models import (
    UsageSummary,
    MetricType,
)
from app.billing.usage_tracker import (
    UsageStats,
    UsageTracker,
    UsageReporter,
)
from app.billing.store import InMemoryBillingStore


class TestUsageStats:
    """Tests for UsageStats dataclass."""

    def test_default_values(self):
        """UsageStats should have correct default values."""
        stats = UsageStats(year_month="2024-01")

        assert stats.year_month == "2024-01"
        assert stats.ai_investigations == 0
        assert stats.alerts_processed == 0
        assert stats.api_calls == 0
        assert stats.postmortems_generated == 0
        assert stats.auto_fix_prs == 0
        assert stats.similar_searches == 0
        assert stats.active_users == 0
        assert stats.storage_bytes == 0

    def test_to_dict(self):
        """to_dict should return all fields."""
        stats = UsageStats(
            year_month="2024-01",
            ai_investigations=10,
            alerts_processed=100,
        )

        result = stats.to_dict()

        assert result["year_month"] == "2024-01"
        assert result["ai_investigations"] == 10
        assert result["alerts_processed"] == 100
        assert "api_calls" in result


class TestUsageTracker:
    """Tests for UsageTracker class."""

    @pytest.fixture
    def store(self) -> InMemoryBillingStore:
        """Create a fresh store."""
        return InMemoryBillingStore()

    @pytest.fixture
    def tracker(self, store: InMemoryBillingStore) -> UsageTracker:
        """Create a UsageTracker instance."""
        return UsageTracker(store)

    @pytest.fixture
    def org_id(self):
        """Create an org ID."""
        return uuid4()

    # AI investigation tracking

    @pytest.mark.asyncio
    async def test_track_ai_investigation(
        self, tracker: UsageTracker, store: InMemoryBillingStore, org_id
    ):
        """track_ai_investigation should record usage."""
        await tracker.track_ai_investigation(org_id, incident_id="inc_123")

        year_month = datetime.now(timezone.utc).strftime("%Y-%m")
        summary = await store.get_usage_summary(org_id, year_month)

        assert summary is not None
        assert summary.ai_investigations_count == 1

    @pytest.mark.asyncio
    async def test_track_ai_investigation_with_metadata(
        self, tracker: UsageTracker, store: InMemoryBillingStore, org_id
    ):
        """track_ai_investigation should store metadata."""
        await tracker.track_ai_investigation(
            org_id,
            incident_id="inc_123",
            metadata={"severity": "high"},
        )

        records = await store.get_usage_records(
            org_id,
            MetricType.AI_INVESTIGATION,
            date.today() - timedelta(days=1),
            date.today() + timedelta(days=1),
        )

        assert len(records) == 1
        assert records[0].metadata.get("incident_id") == "inc_123"

    @pytest.mark.asyncio
    async def test_track_ai_investigation_increments(
        self, tracker: UsageTracker, store: InMemoryBillingStore, org_id
    ):
        """track_ai_investigation should increment count."""
        await tracker.track_ai_investigation(org_id)
        await tracker.track_ai_investigation(org_id)
        await tracker.track_ai_investigation(org_id)

        year_month = datetime.now(timezone.utc).strftime("%Y-%m")
        summary = await store.get_usage_summary(org_id, year_month)

        assert summary.ai_investigations_count == 3

    # Alert tracking

    @pytest.mark.asyncio
    async def test_track_alert_processed(
        self, tracker: UsageTracker, store: InMemoryBillingStore, org_id
    ):
        """track_alert_processed should record usage."""
        await tracker.track_alert_processed(org_id, count=5)

        year_month = datetime.now(timezone.utc).strftime("%Y-%m")
        summary = await store.get_usage_summary(org_id, year_month)

        assert summary.alerts_processed_count == 5

    @pytest.mark.asyncio
    async def test_track_alert_processed_with_ids(
        self, tracker: UsageTracker, store: InMemoryBillingStore, org_id
    ):
        """track_alert_processed should store alert IDs."""
        alert_ids = ["alert_1", "alert_2", "alert_3"]
        await tracker.track_alert_processed(
            org_id, count=3, alert_ids=alert_ids
        )

        records = await store.get_usage_records(
            org_id,
            MetricType.ALERT_PROCESSED,
            date.today() - timedelta(days=1),
            date.today() + timedelta(days=1),
        )

        assert len(records) == 1
        assert "alert_ids" in records[0].metadata

    # API call tracking

    @pytest.mark.asyncio
    async def test_track_api_call(
        self, tracker: UsageTracker, store: InMemoryBillingStore, org_id
    ):
        """track_api_call should record usage."""
        await tracker.track_api_call(
            org_id,
            endpoint="/api/incidents",
            method="GET",
            status_code=200,
        )

        year_month = datetime.now(timezone.utc).strftime("%Y-%m")
        summary = await store.get_usage_summary(org_id, year_month)

        assert summary.api_calls_count == 1

    @pytest.mark.asyncio
    async def test_track_api_call_stores_details(
        self, tracker: UsageTracker, store: InMemoryBillingStore, org_id
    ):
        """track_api_call should store endpoint details."""
        await tracker.track_api_call(
            org_id,
            endpoint="/api/incidents",
            method="POST",
            status_code=201,
        )

        records = await store.get_usage_records(
            org_id,
            MetricType.API_CALL,
            date.today() - timedelta(days=1),
            date.today() + timedelta(days=1),
        )

        assert len(records) == 1
        assert records[0].metadata["endpoint"] == "/api/incidents"
        assert records[0].metadata["method"] == "POST"
        assert records[0].metadata["status_code"] == 201

    # Postmortem tracking

    @pytest.mark.asyncio
    async def test_track_postmortem_generated(
        self, tracker: UsageTracker, store: InMemoryBillingStore, org_id
    ):
        """track_postmortem_generated should record usage."""
        await tracker.track_postmortem_generated(org_id, incident_id="inc_123")

        year_month = datetime.now(timezone.utc).strftime("%Y-%m")
        summary = await store.get_usage_summary(org_id, year_month)

        assert summary.postmortems_generated_count == 1

    # Auto-fix PR tracking

    @pytest.mark.asyncio
    async def test_track_auto_fix_pr(
        self, tracker: UsageTracker, store: InMemoryBillingStore, org_id
    ):
        """track_auto_fix_pr should record usage."""
        await tracker.track_auto_fix_pr(
            org_id,
            incident_id="inc_123",
            pr_url="https://github.com/org/repo/pull/1",
        )

        year_month = datetime.now(timezone.utc).strftime("%Y-%m")
        summary = await store.get_usage_summary(org_id, year_month)

        assert summary.auto_fix_prs_count == 1

    @pytest.mark.asyncio
    async def test_track_auto_fix_pr_stores_url(
        self, tracker: UsageTracker, store: InMemoryBillingStore, org_id
    ):
        """track_auto_fix_pr should store PR URL."""
        await tracker.track_auto_fix_pr(
            org_id,
            incident_id="inc_123",
            pr_url="https://github.com/org/repo/pull/1",
        )

        records = await store.get_usage_records(
            org_id,
            MetricType.AUTO_FIX_PR,
            date.today() - timedelta(days=1),
            date.today() + timedelta(days=1),
        )

        assert records[0].metadata["pr_url"] == "https://github.com/org/repo/pull/1"

    # Similar search tracking

    @pytest.mark.asyncio
    async def test_track_similar_search(
        self, tracker: UsageTracker, store: InMemoryBillingStore, org_id
    ):
        """track_similar_search should record usage."""
        await tracker.track_similar_search(
            org_id,
            incident_id="inc_123",
            query="database connection error",
            results_count=5,
        )

        year_month = datetime.now(timezone.utc).strftime("%Y-%m")
        summary = await store.get_usage_summary(org_id, year_month)

        assert summary.similar_searches_count == 1

    @pytest.mark.asyncio
    async def test_track_similar_search_truncates_query(
        self, tracker: UsageTracker, store: InMemoryBillingStore, org_id
    ):
        """track_similar_search should truncate long queries."""
        long_query = "x" * 200
        await tracker.track_similar_search(org_id, query=long_query, results_count=0)

        records = await store.get_usage_records(
            org_id,
            MetricType.SIMILAR_SEARCH,
            date.today() - timedelta(days=1),
            date.today() + timedelta(days=1),
        )

        assert len(records[0].metadata["query"]) == 100

    # Storage tracking

    @pytest.mark.asyncio
    async def test_track_storage(
        self, tracker: UsageTracker, store: InMemoryBillingStore, org_id
    ):
        """track_storage should record storage usage."""
        await tracker.track_storage(org_id, bytes_used=1024 * 1024)

        year_month = datetime.now(timezone.utc).strftime("%Y-%m")
        summary = await store.get_usage_summary(org_id, year_month)

        assert summary.storage_bytes_used == 1024 * 1024

    @pytest.mark.asyncio
    async def test_track_storage_overwrites(
        self, tracker: UsageTracker, store: InMemoryBillingStore, org_id
    ):
        """track_storage should overwrite previous value."""
        await tracker.track_storage(org_id, bytes_used=1000)
        await tracker.track_storage(org_id, bytes_used=2000)

        year_month = datetime.now(timezone.utc).strftime("%Y-%m")
        summary = await store.get_usage_summary(org_id, year_month)

        assert summary.storage_bytes_used == 2000

    # Active users tracking

    @pytest.mark.asyncio
    async def test_update_active_users(
        self, tracker: UsageTracker, store: InMemoryBillingStore, org_id
    ):
        """update_active_users should set active users count."""
        await tracker.update_active_users(org_id, count=10)

        year_month = datetime.now(timezone.utc).strftime("%Y-%m")
        summary = await store.get_usage_summary(org_id, year_month)

        assert summary.active_users_count == 10

    # Get usage stats

    @pytest.mark.asyncio
    async def test_get_usage_stats_empty(self, tracker: UsageTracker, org_id):
        """get_usage_stats should return empty stats for no data."""
        stats = await tracker.get_usage_stats(org_id)

        assert stats.year_month is not None
        assert stats.ai_investigations == 0

    @pytest.mark.asyncio
    async def test_get_usage_stats_current_month(
        self, tracker: UsageTracker, store: InMemoryBillingStore, org_id
    ):
        """get_usage_stats should return current month by default."""
        await tracker.track_ai_investigation(org_id)
        await tracker.track_alert_processed(org_id, count=10)

        stats = await tracker.get_usage_stats(org_id)

        assert stats.ai_investigations == 1
        assert stats.alerts_processed == 10

    @pytest.mark.asyncio
    async def test_get_usage_stats_specific_month(
        self, tracker: UsageTracker, store: InMemoryBillingStore, org_id
    ):
        """get_usage_stats should return specific month stats."""
        # Create summary for specific month
        summary = UsageSummary(
            org_id=org_id,
            year_month="2024-01",
            ai_investigations_count=50,
        )
        await store.upsert_usage_summary(summary)

        stats = await tracker.get_usage_stats(org_id, "2024-01")

        assert stats.year_month == "2024-01"
        assert stats.ai_investigations == 50

    # Get usage history

    @pytest.mark.asyncio
    async def test_get_usage_history_empty(self, tracker: UsageTracker, org_id):
        """get_usage_history should return empty list for no data."""
        history = await tracker.get_usage_history(org_id)

        assert history == []

    @pytest.mark.asyncio
    async def test_get_usage_history(
        self, tracker: UsageTracker, store: InMemoryBillingStore, org_id
    ):
        """get_usage_history should return monthly summaries."""
        for i, month in enumerate(["2024-01", "2024-02", "2024-03"]):
            summary = UsageSummary(
                org_id=org_id,
                year_month=month,
                ai_investigations_count=i * 10,
            )
            await store.upsert_usage_summary(summary)

        history = await tracker.get_usage_history(org_id, months=12)

        assert len(history) == 3
        assert all(isinstance(h, UsageStats) for h in history)

    # Get usage breakdown

    @pytest.mark.asyncio
    async def test_get_usage_breakdown(
        self, tracker: UsageTracker, store: InMemoryBillingStore, org_id
    ):
        """get_usage_breakdown should return detailed breakdown."""
        for _ in range(5):
            await tracker.track_ai_investigation(org_id)

        breakdown = await tracker.get_usage_breakdown(
            org_id,
            MetricType.AI_INVESTIGATION,
        )

        assert breakdown["metric_type"] == "ai_investigation"
        assert breakdown["total"] == 5
        assert "by_day" in breakdown

    @pytest.mark.asyncio
    async def test_get_usage_breakdown_date_range(
        self, tracker: UsageTracker, org_id
    ):
        """get_usage_breakdown should respect date range."""
        end_date = date.today()
        start_date = end_date - timedelta(days=7)

        breakdown = await tracker.get_usage_breakdown(
            org_id,
            MetricType.AI_INVESTIGATION,
            start_date=start_date,
            end_date=end_date,
        )

        assert breakdown["start_date"] == start_date.isoformat()
        assert breakdown["end_date"] == end_date.isoformat()

    # Get usage trends

    @pytest.mark.asyncio
    async def test_get_usage_trends_insufficient_data(
        self, tracker: UsageTracker, org_id
    ):
        """get_usage_trends should return insufficient_data for < 2 months."""
        trends = await tracker.get_usage_trends(org_id)

        assert trends["trend"] == "insufficient_data"

    @pytest.mark.asyncio
    async def test_get_usage_trends_stable(
        self, tracker: UsageTracker, store: InMemoryBillingStore, org_id
    ):
        """get_usage_trends should detect stable trends."""
        # Create similar usage across months
        for month in ["2024-01", "2024-02", "2024-03", "2024-04"]:
            summary = UsageSummary(
                org_id=org_id,
                year_month=month,
                ai_investigations_count=100,
            )
            await store.upsert_usage_summary(summary)

        trends = await tracker.get_usage_trends(org_id, months=4)

        assert trends["trend"] == "stable"
        assert "change_percent" in trends

    @pytest.mark.asyncio
    async def test_get_usage_trends_increasing(
        self, tracker: UsageTracker, store: InMemoryBillingStore, org_id
    ):
        """get_usage_trends should detect increasing trends."""
        counts = [10, 20, 50, 100]
        for i, month in enumerate(["2024-01", "2024-02", "2024-03", "2024-04"]):
            summary = UsageSummary(
                org_id=org_id,
                year_month=month,
                ai_investigations_count=counts[i],
            )
            await store.upsert_usage_summary(summary)

        trends = await tracker.get_usage_trends(org_id, months=4)

        assert trends["trend"] == "increasing"
        assert trends["change_percent"] > 20

    @pytest.mark.asyncio
    async def test_get_usage_trends_decreasing(
        self, tracker: UsageTracker, store: InMemoryBillingStore, org_id
    ):
        """get_usage_trends should detect decreasing trends."""
        counts = [100, 80, 40, 20]
        for i, month in enumerate(["2024-01", "2024-02", "2024-03", "2024-04"]):
            summary = UsageSummary(
                org_id=org_id,
                year_month=month,
                ai_investigations_count=counts[i],
            )
            await store.upsert_usage_summary(summary)

        trends = await tracker.get_usage_trends(org_id, months=4)

        assert trends["trend"] == "decreasing"
        assert trends["change_percent"] < -20


class TestUsageReporter:
    """Tests for UsageReporter class."""

    @pytest.fixture
    def store(self) -> InMemoryBillingStore:
        """Create a fresh store."""
        return InMemoryBillingStore()

    @pytest.fixture
    def tracker(self, store: InMemoryBillingStore) -> UsageTracker:
        """Create a UsageTracker instance."""
        return UsageTracker(store)

    @pytest.fixture
    def reporter(self, tracker: UsageTracker) -> UsageReporter:
        """Create a UsageReporter instance."""
        return UsageReporter(tracker)

    @pytest.fixture
    def org_id(self):
        """Create an org ID."""
        return uuid4()

    # Monthly report tests

    @pytest.mark.asyncio
    async def test_generate_monthly_report_empty(
        self, reporter: UsageReporter, org_id
    ):
        """generate_monthly_report should handle empty data."""
        report = await reporter.generate_monthly_report(org_id)

        assert "org_id" in report
        assert "period" in report
        assert "metrics" in report
        assert report["metrics"]["ai_investigations"]["current"] == 0

    @pytest.mark.asyncio
    async def test_generate_monthly_report_with_data(
        self, reporter: UsageReporter, store: InMemoryBillingStore, org_id
    ):
        """generate_monthly_report should include all metrics."""
        year_month = datetime.now(timezone.utc).strftime("%Y-%m")
        summary = UsageSummary(
            org_id=org_id,
            year_month=year_month,
            ai_investigations_count=50,
            alerts_processed_count=500,
            api_calls_count=1000,
            active_users_count=10,
        )
        await store.upsert_usage_summary(summary)

        report = await reporter.generate_monthly_report(org_id)

        assert report["metrics"]["ai_investigations"]["current"] == 50
        assert report["metrics"]["alerts_processed"]["current"] == 500
        assert report["active_users"] == 10

    @pytest.mark.asyncio
    async def test_generate_monthly_report_with_comparison(
        self, reporter: UsageReporter, store: InMemoryBillingStore, org_id
    ):
        """generate_monthly_report should compare with previous month."""
        now = datetime.now(timezone.utc)
        current_month = now.strftime("%Y-%m")

        # Calculate previous month
        prev_month = now.month - 1
        prev_year = now.year
        if prev_month == 0:
            prev_month = 12
            prev_year -= 1
        prev_month_str = f"{prev_year:04d}-{prev_month:02d}"

        current_summary = UsageSummary(
            org_id=org_id,
            year_month=current_month,
            ai_investigations_count=100,
        )
        prev_summary = UsageSummary(
            org_id=org_id,
            year_month=prev_month_str,
            ai_investigations_count=50,
        )
        await store.upsert_usage_summary(current_summary)
        await store.upsert_usage_summary(prev_summary)

        report = await reporter.generate_monthly_report(org_id)

        assert report["metrics"]["ai_investigations"]["current"] == 100
        assert report["metrics"]["ai_investigations"]["previous"] == 50
        assert report["metrics"]["ai_investigations"]["change"] == 50
        assert report["metrics"]["ai_investigations"]["change_percent"] == 100.0

    @pytest.mark.asyncio
    async def test_generate_monthly_report_specific_month(
        self, reporter: UsageReporter, store: InMemoryBillingStore, org_id
    ):
        """generate_monthly_report should work for specific month."""
        summary = UsageSummary(
            org_id=org_id,
            year_month="2024-06",
            ai_investigations_count=75,
        )
        await store.upsert_usage_summary(summary)

        report = await reporter.generate_monthly_report(org_id, "2024-06")

        assert report["period"] == "2024-06"
        assert report["previous_period"] == "2024-05"

    # Usage forecast tests

    @pytest.mark.asyncio
    async def test_generate_usage_forecast_insufficient_data(
        self, reporter: UsageReporter, org_id
    ):
        """generate_usage_forecast should require minimum data."""
        forecast = await reporter.generate_usage_forecast(org_id)

        assert forecast["forecast_available"] is False
        assert "Insufficient" in forecast["reason"]

    @pytest.mark.asyncio
    async def test_generate_usage_forecast(
        self, reporter: UsageReporter, store: InMemoryBillingStore, org_id
    ):
        """generate_usage_forecast should project future usage."""
        # Create 3+ months of history
        for i, month in enumerate(["2024-01", "2024-02", "2024-03", "2024-04"]):
            summary = UsageSummary(
                org_id=org_id,
                year_month=month,
                ai_investigations_count=100 + i * 10,
            )
            await store.upsert_usage_summary(summary)

        forecast = await reporter.generate_usage_forecast(org_id, months_ahead=3)

        assert forecast["forecast_available"] is True
        assert "forecasts" in forecast
        assert len(forecast["forecasts"]) == 3
        assert "avg_monthly_growth" in forecast

    @pytest.mark.asyncio
    async def test_generate_usage_forecast_includes_confidence(
        self, reporter: UsageReporter, store: InMemoryBillingStore, org_id
    ):
        """generate_usage_forecast should include confidence levels."""
        for month in ["2024-01", "2024-02", "2024-03", "2024-04"]:
            summary = UsageSummary(
                org_id=org_id,
                year_month=month,
                ai_investigations_count=100,
            )
            await store.upsert_usage_summary(summary)

        forecast = await reporter.generate_usage_forecast(org_id, months_ahead=3)

        for f in forecast["forecasts"]:
            assert "confidence" in f
            assert f["confidence"] <= 1.0
            assert f["confidence"] >= 0.5

    @pytest.mark.asyncio
    async def test_generate_usage_forecast_decreasing_confidence(
        self, reporter: UsageReporter, store: InMemoryBillingStore, org_id
    ):
        """generate_usage_forecast should decrease confidence over time."""
        for month in ["2024-01", "2024-02", "2024-03", "2024-04"]:
            summary = UsageSummary(
                org_id=org_id,
                year_month=month,
                ai_investigations_count=100,
            )
            await store.upsert_usage_summary(summary)

        forecast = await reporter.generate_usage_forecast(org_id, months_ahead=5)

        # Confidence should decrease for later months
        confidences = [f["confidence"] for f in forecast["forecasts"]]
        for i in range(len(confidences) - 1):
            assert confidences[i] >= confidences[i + 1]
