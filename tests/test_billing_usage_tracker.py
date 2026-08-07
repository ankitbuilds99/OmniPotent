"""Tests for billing usage tracker module."""
from __future__ import annotations
import pytest
from datetime import datetime, timezone
from uuid import uuid4
from app.billing.models import UsageSummary, MetricType
from app.billing.usage_tracker import UsageStats, UsageTracker, UsageReporter
from app.billing.store import InMemoryBillingStore

class TestUsageStats:
    def test_default_values(self):
        stats = UsageStats(year_month="2024-01")
        assert stats.year_month == "2024-01"
        assert stats.ai_investigations == 0
        assert stats.alerts_processed == 0

    def test_to_dict(self):
        stats = UsageStats(year_month="2024-01", ai_investigations=10)
        result = stats.to_dict()
        assert result["year_month"] == "2024-01"
        assert result["ai_investigations"] == 10

class TestUsageTracker:
    @pytest.fixture
    def store(self):
        return InMemoryBillingStore()

    @pytest.fixture
    def tracker(self, store):
        return UsageTracker(store)

    @pytest.fixture
    def org_id(self):
        return uuid4()

    @pytest.mark.asyncio
    async def test_track_ai_investigation(self, tracker, store, org_id):
        await tracker.track_ai_investigation(org_id)
        year_month = datetime.now(timezone.utc).strftime("%Y-%m")
        summary = await store.get_usage_summary(org_id, year_month)
        assert summary.ai_investigations_count == 1

    @pytest.mark.asyncio
    async def test_track_ai_investigation_increments(self, tracker, store, org_id):
        await tracker.track_ai_investigation(org_id)
        await tracker.track_ai_investigation(org_id)
        year_month = datetime.now(timezone.utc).strftime("%Y-%m")
        summary = await store.get_usage_summary(org_id, year_month)
        assert summary.ai_investigations_count == 2

    @pytest.mark.asyncio
    async def test_track_alert_processed(self, tracker, store, org_id):
        await tracker.track_alert_processed(org_id, count=5)
        year_month = datetime.now(timezone.utc).strftime("%Y-%m")
        summary = await store.get_usage_summary(org_id, year_month)
        assert summary.alerts_processed_count == 5

    @pytest.mark.asyncio
    async def test_track_api_call(self, tracker, store, org_id):
        await tracker.track_api_call(org_id, endpoint="/api/test", method="GET")
        year_month = datetime.now(timezone.utc).strftime("%Y-%m")
        summary = await store.get_usage_summary(org_id, year_month)
        assert summary.api_calls_count == 1

    @pytest.mark.asyncio
    async def test_track_postmortem_generated(self, tracker, store, org_id):
        await tracker.track_postmortem_generated(org_id, incident_id="inc_123")
        year_month = datetime.now(timezone.utc).strftime("%Y-%m")
        summary = await store.get_usage_summary(org_id, year_month)
        assert summary.postmortems_generated_count == 1

    @pytest.mark.asyncio
    async def test_track_auto_fix_pr(self, tracker, store, org_id):
        await tracker.track_auto_fix_pr(org_id, incident_id="inc_123")
        year_month = datetime.now(timezone.utc).strftime("%Y-%m")
        summary = await store.get_usage_summary(org_id, year_month)
        assert summary.auto_fix_prs_count == 1

    @pytest.mark.asyncio
    async def test_track_similar_search(self, tracker, store, org_id):
        await tracker.track_similar_search(org_id, query="test", results_count=5)
        year_month = datetime.now(timezone.utc).strftime("%Y-%m")
        summary = await store.get_usage_summary(org_id, year_month)
        assert summary.similar_searches_count == 1

    @pytest.mark.asyncio
    async def test_track_storage(self, tracker, store, org_id):
        await tracker.track_storage(org_id, bytes_used=1024 * 1024)
        year_month = datetime.now(timezone.utc).strftime("%Y-%m")
        summary = await store.get_usage_summary(org_id, year_month)
        assert summary.storage_bytes_used == 1024 * 1024

    @pytest.mark.asyncio
    async def test_update_active_users(self, tracker, store, org_id):
        await tracker.update_active_users(org_id, count=10)
        year_month = datetime.now(timezone.utc).strftime("%Y-%m")
        summary = await store.get_usage_summary(org_id, year_month)
        assert summary.active_users_count == 10

    @pytest.mark.asyncio
    async def test_get_usage_stats_empty(self, tracker, org_id):
        stats = await tracker.get_usage_stats(org_id)
        assert stats.ai_investigations == 0

    @pytest.mark.asyncio
    async def test_get_usage_stats_current_month(self, tracker, store, org_id):
        await tracker.track_ai_investigation(org_id)
        await tracker.track_alert_processed(org_id, count=10)
        stats = await tracker.get_usage_stats(org_id)
        assert stats.ai_investigations == 1
        assert stats.alerts_processed == 10

    @pytest.mark.asyncio
    async def test_get_usage_history_empty(self, tracker, org_id):
        history = await tracker.get_usage_history(org_id)
        assert history == []

    @pytest.mark.asyncio
    async def test_get_usage_history(self, tracker, store, org_id):
        for month in ["2024-01", "2024-02"]:
            summary = UsageSummary(org_id=org_id, year_month=month, ai_investigations_count=10)
            await store.upsert_usage_summary(summary)
        history = await tracker.get_usage_history(org_id, months=12)
        assert len(history) == 2

    @pytest.mark.asyncio
    async def test_get_usage_breakdown(self, tracker, org_id):
        breakdown = await tracker.get_usage_breakdown(org_id, MetricType.AI_INVESTIGATION)
        assert breakdown["metric_type"] == "ai_investigation"
        assert "total" in breakdown

    @pytest.mark.asyncio
    async def test_get_usage_trends_insufficient_data(self, tracker, org_id):
        trends = await tracker.get_usage_trends(org_id)
        assert trends["trend"] == "insufficient_data"

    @pytest.mark.asyncio
    async def test_get_usage_trends_stable(self, tracker, store, org_id):
        for month in ["2024-01", "2024-02", "2024-03", "2024-04"]:
            summary = UsageSummary(org_id=org_id, year_month=month, ai_investigations_count=100)
            await store.upsert_usage_summary(summary)
        trends = await tracker.get_usage_trends(org_id, months=4)
        assert trends["trend"] == "stable"

class TestUsageReporter:
    @pytest.fixture
    def store(self):
        return InMemoryBillingStore()

    @pytest.fixture
    def tracker(self, store):
        return UsageTracker(store)

    @pytest.fixture
    def reporter(self, tracker):
        return UsageReporter(tracker)

    @pytest.fixture
    def org_id(self):
        return uuid4()

    @pytest.mark.asyncio
    async def test_generate_monthly_report_empty(self, reporter, org_id):
        report = await reporter.generate_monthly_report(org_id)
        assert "org_id" in report
        assert "metrics" in report

    @pytest.mark.asyncio
    async def test_generate_monthly_report_with_data(self, reporter, store, org_id):
        year_month = datetime.now(timezone.utc).strftime("%Y-%m")
        summary = UsageSummary(org_id=org_id, year_month=year_month, ai_investigations_count=50)
        await store.upsert_usage_summary(summary)
        report = await reporter.generate_monthly_report(org_id)
        assert report["metrics"]["ai_investigations"]["current"] == 50

    @pytest.mark.asyncio
    async def test_generate_usage_forecast_insufficient_data(self, reporter, org_id):
        forecast = await reporter.generate_usage_forecast(org_id)
        assert forecast["forecast_available"] is False

    @pytest.mark.asyncio
    async def test_generate_usage_forecast(self, reporter, store, org_id):
        for i, month in enumerate(["2024-01", "2024-02", "2024-03", "2024-04"]):
            summary = UsageSummary(org_id=org_id, year_month=month, ai_investigations_count=100 + i * 10)
            await store.upsert_usage_summary(summary)
        forecast = await reporter.generate_usage_forecast(org_id, months_ahead=3)
        assert forecast["forecast_available"] is True
        assert len(forecast["forecasts"]) == 3
