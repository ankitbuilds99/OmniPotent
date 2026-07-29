"""Usage tracking service for billing and limits."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, date, timedelta, timezone
from typing import Any, Protocol
from uuid import UUID, uuid4

from app.billing.models import (
    UsageRecord,
    UsageSummary,
    MetricType,
)

log = logging.getLogger("devcopilot.billing.usage_tracker")


class UsageStore(Protocol):
    """Protocol for usage data storage."""

    async def create_usage_record(self, record: UsageRecord) -> None: ...
    async def get_usage_summary(
        self, org_id: UUID, year_month: str
    ) -> UsageSummary | None: ...
    async def upsert_usage_summary(self, summary: UsageSummary) -> None: ...
    async def increment_summary_field(
        self, org_id: UUID, year_month: str, field: str, increment: int
    ) -> None: ...
    async def get_usage_history(
        self, org_id: UUID, months: int
    ) -> list[UsageSummary]: ...
    async def get_usage_records(
        self,
        org_id: UUID,
        metric_type: MetricType | None,
        start_date: date,
        end_date: date,
    ) -> list[UsageRecord]: ...


@dataclass
class UsageStats:
    """Usage statistics for a period."""

    year_month: str
    ai_investigations: int = 0
    alerts_processed: int = 0
    api_calls: int = 0
    postmortems_generated: int = 0
    auto_fix_prs: int = 0
    similar_searches: int = 0
    active_users: int = 0
    storage_bytes: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "year_month": self.year_month,
            "ai_investigations": self.ai_investigations,
            "alerts_processed": self.alerts_processed,
            "api_calls": self.api_calls,
            "postmortems_generated": self.postmortems_generated,
            "auto_fix_prs": self.auto_fix_prs,
            "similar_searches": self.similar_searches,
            "active_users": self.active_users,
            "storage_bytes": self.storage_bytes,
        }


class UsageTracker:
    """Track usage metrics for billing and limits."""

    def __init__(self, store: UsageStore):
        self._store = store

    async def track_ai_investigation(
        self,
        org_id: UUID,
        incident_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Track an AI investigation."""
        meta = metadata or {}
        if incident_id:
            meta["incident_id"] = incident_id

        await self._record_usage(org_id, MetricType.AI_INVESTIGATION, 1, meta)
        await self._update_summary(org_id, "ai_investigations_count", 1)

        log.debug("Tracked AI investigation for org %s", org_id)

    async def track_alert_processed(
        self,
        org_id: UUID,
        count: int = 1,
        alert_ids: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Track alerts processed."""
        meta = metadata or {}
        if alert_ids:
            meta["alert_ids"] = alert_ids[:10]

        await self._record_usage(org_id, MetricType.ALERT_PROCESSED, count, meta)
        await self._update_summary(org_id, "alerts_processed_count", count)

        log.debug("Tracked %d alerts for org %s", count, org_id)

    async def track_api_call(
        self,
        org_id: UUID,
        endpoint: str,
        method: str = "GET",
        status_code: int = 200,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Track API calls."""
        meta = metadata or {}
        meta.update({
            "endpoint": endpoint,
            "method": method,
            "status_code": status_code,
        })

        await self._record_usage(org_id, MetricType.API_CALL, 1, meta)
        await self._update_summary(org_id, "api_calls_count", 1)

    async def track_postmortem_generated(
        self,
        org_id: UUID,
        incident_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Track postmortem generation."""
        meta = metadata or {}
        meta["incident_id"] = incident_id

        await self._record_usage(org_id, MetricType.POSTMORTEM_GENERATED, 1, meta)
        await self._update_summary(org_id, "postmortems_generated_count", 1)

    async def track_auto_fix_pr(
        self,
        org_id: UUID,
        incident_id: str,
        pr_url: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Track auto-fix PR generation."""
        meta = metadata or {}
        meta["incident_id"] = incident_id
        if pr_url:
            meta["pr_url"] = pr_url

        await self._record_usage(org_id, MetricType.AUTO_FIX_PR, 1, meta)
        await self._update_summary(org_id, "auto_fix_prs_count", 1)

    async def track_similar_search(
        self,
        org_id: UUID,
        incident_id: str | None = None,
        query: str | None = None,
        results_count: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Track similar incident search."""
        meta = metadata or {}
        if incident_id:
            meta["incident_id"] = incident_id
        if query:
            meta["query"] = query[:100]
        meta["results_count"] = results_count

        await self._record_usage(org_id, MetricType.SIMILAR_SEARCH, 1, meta)
        await self._update_summary(org_id, "similar_searches_count", 1)

    async def track_storage(
        self,
        org_id: UUID,
        bytes_used: int,
        storage_type: str = "general",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Track storage usage."""
        meta = metadata or {}
        meta["storage_type"] = storage_type

        await self._record_usage(org_id, MetricType.STORAGE_BYTES, bytes_used, meta)
        await self._set_summary_field(org_id, "storage_bytes_used", bytes_used)

    async def update_active_users(self, org_id: UUID, count: int) -> None:
        """Update active users count."""
        await self._set_summary_field(org_id, "active_users_count", count)

    async def get_usage_stats(
        self,
        org_id: UUID,
        year_month: str | None = None,
    ) -> UsageStats:
        """Get usage statistics for an organization."""
        if not year_month:
            year_month = datetime.now(timezone.utc).strftime("%Y-%m")

        summary = await self._store.get_usage_summary(org_id, year_month)

        if not summary:
            return UsageStats(year_month=year_month)

        return UsageStats(
            year_month=year_month,
            ai_investigations=summary.ai_investigations_count,
            alerts_processed=summary.alerts_processed_count,
            api_calls=summary.api_calls_count,
            postmortems_generated=summary.postmortems_generated_count,
            auto_fix_prs=summary.auto_fix_prs_count,
            similar_searches=summary.similar_searches_count,
            active_users=summary.active_users_count,
            storage_bytes=summary.storage_bytes_used,
        )

    async def get_usage_history(
        self,
        org_id: UUID,
        months: int = 12,
    ) -> list[UsageStats]:
        """Get usage history for the last N months."""
        summaries = await self._store.get_usage_history(org_id, months)

        return [
            UsageStats(
                year_month=s.year_month,
                ai_investigations=s.ai_investigations_count,
                alerts_processed=s.alerts_processed_count,
                api_calls=s.api_calls_count,
                postmortems_generated=s.postmortems_generated_count,
                auto_fix_prs=s.auto_fix_prs_count,
                similar_searches=s.similar_searches_count,
                active_users=s.active_users_count,
                storage_bytes=s.storage_bytes_used,
            )
            for s in summaries
        ]

    async def get_usage_breakdown(
        self,
        org_id: UUID,
        metric_type: MetricType,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> dict[str, Any]:
        """Get detailed usage breakdown for a metric."""
        if not end_date:
            end_date = date.today()
        if not start_date:
            start_date = end_date - timedelta(days=30)

        records = await self._store.get_usage_records(
            org_id, metric_type, start_date, end_date
        )

        total = sum(r.quantity for r in records)
        by_day: dict[str, int] = {}

        for record in records:
            day_key = record.period_start.isoformat()
            by_day[day_key] = by_day.get(day_key, 0) + record.quantity

        return {
            "metric_type": metric_type.value,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "total": total,
            "record_count": len(records),
            "by_day": by_day,
        }

    async def get_usage_trends(
        self,
        org_id: UUID,
        months: int = 6,
    ) -> dict[str, Any]:
        """Analyze usage trends over time."""
        history = await self.get_usage_history(org_id, months)

        if len(history) < 2:
            return {
                "trend": "insufficient_data",
                "months_analyzed": len(history),
            }

        history_sorted = sorted(history, key=lambda x: x.year_month)

        first_half = history_sorted[: len(history_sorted) // 2]
        second_half = history_sorted[len(history_sorted) // 2 :]

        def avg_investigations(data: list[UsageStats]) -> float:
            if not data:
                return 0
            return sum(d.ai_investigations for d in data) / len(data)

        first_avg = avg_investigations(first_half)
        second_avg = avg_investigations(second_half)

        if first_avg == 0:
            change_pct = 100.0 if second_avg > 0 else 0.0
        else:
            change_pct = ((second_avg - first_avg) / first_avg) * 100

        if change_pct > 20:
            trend = "increasing"
        elif change_pct < -20:
            trend = "decreasing"
        else:
            trend = "stable"

        return {
            "trend": trend,
            "change_percent": round(change_pct, 1),
            "months_analyzed": len(history),
            "first_period_avg": round(first_avg, 1),
            "second_period_avg": round(second_avg, 1),
            "history": [h.to_dict() for h in history_sorted],
        }

    async def _record_usage(
        self,
        org_id: UUID,
        metric_type: MetricType,
        quantity: int,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Record a usage event."""
        today = date.today()

        record = UsageRecord(
            record_id=uuid4(),
            org_id=org_id,
            metric_type=metric_type,
            quantity=quantity,
            period_start=today.replace(day=1),
            period_end=today,
            metadata=metadata or {},
        )

        await self._store.create_usage_record(record)

    async def _update_summary(
        self,
        org_id: UUID,
        field: str,
        increment: int,
    ) -> None:
        """Update usage summary atomically."""
        year_month = datetime.now(timezone.utc).strftime("%Y-%m")
        await self._store.increment_summary_field(org_id, year_month, field, increment)

    async def _set_summary_field(
        self,
        org_id: UUID,
        field: str,
        value: int,
    ) -> None:
        """Set a usage summary field value."""
        year_month = datetime.now(timezone.utc).strftime("%Y-%m")

        summary = await self._store.get_usage_summary(org_id, year_month)

        if summary:
            setattr(summary, field, value)
            summary.updated_at = datetime.now(timezone.utc)
            await self._store.upsert_usage_summary(summary)
        else:
            summary = UsageSummary(
                summary_id=uuid4(),
                org_id=org_id,
                year_month=year_month,
            )
            setattr(summary, field, value)
            await self._store.upsert_usage_summary(summary)


class UsageReporter:
    """Generate usage reports for billing and analytics."""

    def __init__(self, tracker: UsageTracker):
        self._tracker = tracker

    async def generate_monthly_report(
        self,
        org_id: UUID,
        year_month: str | None = None,
    ) -> dict[str, Any]:
        """Generate a monthly usage report."""
        if not year_month:
            year_month = datetime.now(timezone.utc).strftime("%Y-%m")

        current = await self._tracker.get_usage_stats(org_id, year_month)

        year, month = year_month.split("-")
        prev_month = int(month) - 1
        prev_year = int(year)
        if prev_month == 0:
            prev_month = 12
            prev_year -= 1
        prev_year_month = f"{prev_year:04d}-{prev_month:02d}"

        previous = await self._tracker.get_usage_stats(org_id, prev_year_month)

        def calc_change(current_val: int, prev_val: int) -> dict[str, Any]:
            if prev_val == 0:
                change_pct = 100.0 if current_val > 0 else 0.0
            else:
                change_pct = ((current_val - prev_val) / prev_val) * 100

            return {
                "current": current_val,
                "previous": prev_val,
                "change": current_val - prev_val,
                "change_percent": round(change_pct, 1),
            }

        return {
            "org_id": str(org_id),
            "period": year_month,
            "previous_period": prev_year_month,
            "metrics": {
                "ai_investigations": calc_change(
                    current.ai_investigations, previous.ai_investigations
                ),
                "alerts_processed": calc_change(
                    current.alerts_processed, previous.alerts_processed
                ),
                "api_calls": calc_change(current.api_calls, previous.api_calls),
                "postmortems": calc_change(
                    current.postmortems_generated, previous.postmortems_generated
                ),
                "auto_fix_prs": calc_change(
                    current.auto_fix_prs, previous.auto_fix_prs
                ),
                "similar_searches": calc_change(
                    current.similar_searches, previous.similar_searches
                ),
            },
            "active_users": current.active_users,
            "storage_bytes": current.storage_bytes,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    async def generate_usage_forecast(
        self,
        org_id: UUID,
        months_ahead: int = 3,
    ) -> dict[str, Any]:
        """Generate usage forecast based on historical data."""
        history = await self._tracker.get_usage_history(org_id, 6)

        if len(history) < 3:
            return {
                "forecast_available": False,
                "reason": "Insufficient historical data",
            }

        sorted_history = sorted(history, key=lambda x: x.year_month)

        inv_values = [h.ai_investigations for h in sorted_history]
        avg_growth = 0.0

        if len(inv_values) > 1:
            growths = []
            for i in range(1, len(inv_values)):
                if inv_values[i - 1] > 0:
                    growth = (inv_values[i] - inv_values[i - 1]) / inv_values[i - 1]
                    growths.append(growth)

            if growths:
                avg_growth = sum(growths) / len(growths)

        last_value = inv_values[-1]
        forecasts = []
        current_date = datetime.now(timezone.utc)

        for i in range(1, months_ahead + 1):
            future_date = current_date + timedelta(days=30 * i)
            year_month = future_date.strftime("%Y-%m")
            projected = int(last_value * ((1 + avg_growth) ** i))

            forecasts.append({
                "year_month": year_month,
                "projected_investigations": projected,
                "confidence": max(0.5, 1 - (i * 0.1)),
            })

        return {
            "forecast_available": True,
            "avg_monthly_growth": round(avg_growth * 100, 1),
            "forecasts": forecasts,
            "based_on_months": len(history),
        }
