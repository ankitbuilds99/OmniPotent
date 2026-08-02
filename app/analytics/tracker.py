"""Analytics event tracking and metrics collection.

Tracks:
- User activity and feature usage
- Investigation performance metrics
- Runbook execution stats
- API usage and latency
- Error rates and patterns
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("omnipotent.analytics")


class EventType(Enum):
    """Types of analytics events."""

    PAGE_VIEW = "page_view"
    USER_ACTION = "user_action"
    INVESTIGATION_START = "investigation_start"
    INVESTIGATION_COMPLETE = "investigation_complete"
    RUNBOOK_START = "runbook_start"
    RUNBOOK_COMPLETE = "runbook_complete"
    ALERT_RECEIVED = "alert_received"
    API_CALL = "api_call"
    INTEGRATION_SYNC = "integration_sync"
    ERROR = "error"
    FEATURE_USAGE = "feature_usage"
    SEARCH = "search"
    EXPORT = "export"
    LOGIN = "login"
    LOGOUT = "logout"


@dataclass
class AnalyticsEvent:
    """An analytics event."""

    id: str
    event_type: EventType
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    user_id: str = ""
    org_id: str = ""
    session_id: str = ""
    properties: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "event_type": self.event_type.value,
            "timestamp": self.timestamp.isoformat(),
            "user_id": self.user_id,
            "org_id": self.org_id,
            "session_id": self.session_id,
            "properties": self.properties,
            "context": self.context,
        }


@dataclass
class MetricPoint:
    """A metric data point."""

    timestamp: datetime
    value: float
    labels: dict[str, str] = field(default_factory=dict)


@dataclass
class UsageSummary:
    """Usage summary for a period."""

    period_start: datetime
    period_end: datetime
    total_investigations: int = 0
    completed_investigations: int = 0
    total_runbook_executions: int = 0
    successful_runbook_executions: int = 0
    total_alerts: int = 0
    total_api_calls: int = 0
    active_users: int = 0
    avg_investigation_time_minutes: float = 0
    avg_runbook_duration_seconds: float = 0
    top_features: list[tuple[str, int]] = field(default_factory=list)
    investigations_by_severity: dict[str, int] = field(default_factory=dict)
    errors_by_type: dict[str, int] = field(default_factory=dict)


class AnalyticsTracker:
    """Tracks and aggregates analytics events."""

    def __init__(self, buffer_size: int = 10000):
        self.buffer_size = buffer_size
        self._events: list[AnalyticsEvent] = []
        self._counters: dict[str, int] = defaultdict(int)
        self._gauges: dict[str, float] = {}
        self._histograms: dict[str, list[float]] = defaultdict(list)

    def track(self, event: AnalyticsEvent) -> None:
        """Track an analytics event."""
        self._events.append(event)

        if len(self._events) > self.buffer_size:
            self._events = self._events[-self.buffer_size :]

        self._counters[f"events.{event.event_type.value}"] += 1
        self._counters["events.total"] += 1

        if event.user_id:
            self._counters[f"users.active.{event.user_id}"] = 1

        if event.org_id:
            self._counters[f"orgs.active.{event.org_id}"] = 1

    def increment(self, metric: str, value: int = 1, labels: dict[str, str] | None = None) -> None:
        """Increment a counter metric."""
        key = self._metric_key(metric, labels)
        self._counters[key] += value

    def gauge(self, metric: str, value: float, labels: dict[str, str] | None = None) -> None:
        """Set a gauge metric."""
        key = self._metric_key(metric, labels)
        self._gauges[key] = value

    def histogram(self, metric: str, value: float, labels: dict[str, str] | None = None) -> None:
        """Record a histogram value."""
        key = self._metric_key(metric, labels)
        self._histograms[key].append(value)

        if len(self._histograms[key]) > 10000:
            self._histograms[key] = self._histograms[key][-10000:]

    def _metric_key(self, metric: str, labels: dict[str, str] | None) -> str:
        """Generate metric key with labels."""
        if not labels:
            return metric
        label_str = ",".join(f"{k}={v}" for k, v in sorted(labels.items()))
        return f"{metric}{{{label_str}}}"

    def get_events(
        self,
        event_type: EventType | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        user_id: str | None = None,
        org_id: str | None = None,
        limit: int = 1000,
    ) -> list[AnalyticsEvent]:
        """Get filtered events."""
        events = self._events

        if event_type:
            events = [e for e in events if e.event_type == event_type]

        if start_time:
            events = [e for e in events if e.timestamp >= start_time]

        if end_time:
            events = [e for e in events if e.timestamp <= end_time]

        if user_id:
            events = [e for e in events if e.user_id == user_id]

        if org_id:
            events = [e for e in events if e.org_id == org_id]

        return events[-limit:]

    def get_counter(self, metric: str, labels: dict[str, str] | None = None) -> int:
        """Get counter value."""
        key = self._metric_key(metric, labels)
        return self._counters.get(key, 0)

    def get_gauge(self, metric: str, labels: dict[str, str] | None = None) -> float | None:
        """Get gauge value."""
        key = self._metric_key(metric, labels)
        return self._gauges.get(key)

    def get_histogram_stats(
        self,
        metric: str,
        labels: dict[str, str] | None = None,
    ) -> dict[str, float]:
        """Get histogram statistics."""
        key = self._metric_key(metric, labels)
        values = self._histograms.get(key, [])

        if not values:
            return {"count": 0, "min": 0, "max": 0, "avg": 0, "p50": 0, "p95": 0, "p99": 0}

        sorted_values = sorted(values)
        count = len(sorted_values)

        return {
            "count": count,
            "min": sorted_values[0],
            "max": sorted_values[-1],
            "avg": sum(sorted_values) / count,
            "p50": sorted_values[int(count * 0.5)],
            "p95": sorted_values[int(count * 0.95)] if count >= 20 else sorted_values[-1],
            "p99": sorted_values[int(count * 0.99)] if count >= 100 else sorted_values[-1],
        }

    def get_usage_summary(
        self,
        start_time: datetime,
        end_time: datetime,
        org_id: str | None = None,
    ) -> UsageSummary:
        """Get usage summary for a period."""
        events = self.get_events(start_time=start_time, end_time=end_time, org_id=org_id)

        inv_starts = [e for e in events if e.event_type == EventType.INVESTIGATION_START]
        inv_completes = [e for e in events if e.event_type == EventType.INVESTIGATION_COMPLETE]
        rb_starts = [e for e in events if e.event_type == EventType.RUNBOOK_START]
        rb_completes = [e for e in events if e.event_type == EventType.RUNBOOK_COMPLETE]
        alerts = [e for e in events if e.event_type == EventType.ALERT_RECEIVED]
        api_calls = [e for e in events if e.event_type == EventType.API_CALL]
        errors = [e for e in events if e.event_type == EventType.ERROR]
        feature_events = [e for e in events if e.event_type == EventType.FEATURE_USAGE]

        active_users = len(set(e.user_id for e in events if e.user_id))

        inv_times = [
            e.properties.get("duration_minutes", 0)
            for e in inv_completes
            if e.properties.get("duration_minutes")
        ]
        avg_inv_time = sum(inv_times) / len(inv_times) if inv_times else 0

        rb_durations = [
            e.properties.get("duration_seconds", 0)
            for e in rb_completes
            if e.properties.get("duration_seconds")
        ]
        avg_rb_duration = sum(rb_durations) / len(rb_durations) if rb_durations else 0

        feature_counts: dict[str, int] = defaultdict(int)
        for e in feature_events:
            feature = e.properties.get("feature", "unknown")
            feature_counts[feature] += 1

        top_features = sorted(feature_counts.items(), key=lambda x: x[1], reverse=True)[:10]

        sev_counts: dict[str, int] = defaultdict(int)
        for e in inv_starts:
            sev = e.properties.get("severity", "unknown")
            sev_counts[sev] += 1

        error_counts: dict[str, int] = defaultdict(int)
        for e in errors:
            err_type = e.properties.get("error_type", "unknown")
            error_counts[err_type] += 1

        successful_rb = sum(
            1 for e in rb_completes
            if e.properties.get("status") == "success"
        )

        return UsageSummary(
            period_start=start_time,
            period_end=end_time,
            total_investigations=len(inv_starts),
            completed_investigations=len(inv_completes),
            total_runbook_executions=len(rb_starts),
            successful_runbook_executions=successful_rb,
            total_alerts=len(alerts),
            total_api_calls=len(api_calls),
            active_users=active_users,
            avg_investigation_time_minutes=avg_inv_time,
            avg_runbook_duration_seconds=avg_rb_duration,
            top_features=top_features,
            investigations_by_severity=dict(sev_counts),
            errors_by_type=dict(error_counts),
        )

    def get_trend_data(
        self,
        metric: str,
        start_time: datetime,
        end_time: datetime,
        granularity: str = "hour",
        org_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Get trend data for a metric over time."""
        event_type_map = {
            "investigations": EventType.INVESTIGATION_START,
            "runbooks": EventType.RUNBOOK_START,
            "alerts": EventType.ALERT_RECEIVED,
            "api_calls": EventType.API_CALL,
            "errors": EventType.ERROR,
        }

        event_type = event_type_map.get(metric)
        events = self.get_events(
            event_type=event_type,
            start_time=start_time,
            end_time=end_time,
            org_id=org_id,
        )

        if granularity == "hour":
            delta = timedelta(hours=1)
            fmt = "%Y-%m-%d %H:00"
        elif granularity == "day":
            delta = timedelta(days=1)
            fmt = "%Y-%m-%d"
        elif granularity == "week":
            delta = timedelta(weeks=1)
            fmt = "%Y-W%W"
        else:
            delta = timedelta(hours=1)
            fmt = "%Y-%m-%d %H:00"

        buckets: dict[str, int] = defaultdict(int)

        for event in events:
            bucket = event.timestamp.strftime(fmt)
            buckets[bucket] += 1

        current = start_time
        result = []
        while current < end_time:
            bucket = current.strftime(fmt)
            result.append({
                "timestamp": current.isoformat(),
                "bucket": bucket,
                "value": buckets.get(bucket, 0),
            })
            current += delta

        return result

    def get_top_users(
        self,
        start_time: datetime,
        end_time: datetime,
        org_id: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Get top users by activity."""
        events = self.get_events(start_time=start_time, end_time=end_time, org_id=org_id)

        user_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

        for event in events:
            if event.user_id:
                user_counts[event.user_id]["total"] += 1
                user_counts[event.user_id][event.event_type.value] += 1

        users: list[dict[str, Any]] = []
        for user_id, counts in user_counts.items():
            users.append({
                "user_id": user_id,
                "total_events": counts["total"],
                "investigations": counts.get("investigation_start", 0),
                "runbooks": counts.get("runbook_start", 0),
                "api_calls": counts.get("api_call", 0),
            })

        users.sort(key=lambda x: x.get("total_events", 0), reverse=True)
        return users[:limit]


_tracker: AnalyticsTracker | None = None


def _get_tracker() -> AnalyticsTracker:
    """Get or create tracker singleton."""
    global _tracker
    if _tracker is None:
        _tracker = AnalyticsTracker()
    return _tracker


def track_event(
    event_type: EventType,
    user_id: str = "",
    org_id: str = "",
    properties: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
) -> None:
    """Track an analytics event."""
    import secrets

    event = AnalyticsEvent(
        id=f"evt_{secrets.token_hex(8)}",
        event_type=event_type,
        user_id=user_id,
        org_id=org_id,
        properties=properties or {},
        context=context or {},
    )

    tracker = _get_tracker()
    tracker.track(event)


def track_investigation(
    investigation_id: str,
    status: str,
    user_id: str = "",
    org_id: str = "",
    severity: str = "",
    duration_minutes: float = 0,
    **properties: Any,
) -> None:
    """Track investigation event."""
    event_type = (
        EventType.INVESTIGATION_COMPLETE
        if status == "completed"
        else EventType.INVESTIGATION_START
    )

    track_event(
        event_type=event_type,
        user_id=user_id,
        org_id=org_id,
        properties={
            "investigation_id": investigation_id,
            "status": status,
            "severity": severity,
            "duration_minutes": duration_minutes,
            **properties,
        },
    )


def track_runbook_execution(
    runbook_id: str,
    execution_id: str,
    status: str,
    user_id: str = "",
    org_id: str = "",
    duration_seconds: float = 0,
    **properties: Any,
) -> None:
    """Track runbook execution event."""
    event_type = (
        EventType.RUNBOOK_COMPLETE
        if status in ("completed", "success", "failed")
        else EventType.RUNBOOK_START
    )

    track_event(
        event_type=event_type,
        user_id=user_id,
        org_id=org_id,
        properties={
            "runbook_id": runbook_id,
            "execution_id": execution_id,
            "status": status,
            "duration_seconds": duration_seconds,
            **properties,
        },
    )


def track_user_action(
    action: str,
    user_id: str = "",
    org_id: str = "",
    **properties: Any,
) -> None:
    """Track user action event."""
    track_event(
        event_type=EventType.USER_ACTION,
        user_id=user_id,
        org_id=org_id,
        properties={"action": action, **properties},
    )


def get_usage_summary(
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    org_id: str | None = None,
) -> UsageSummary:
    """Get usage summary."""
    if end_time is None:
        end_time = datetime.now(timezone.utc)
    if start_time is None:
        start_time = end_time - timedelta(days=30)

    tracker = _get_tracker()
    return tracker.get_usage_summary(start_time, end_time, org_id)


def get_trend_data(
    metric: str,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    granularity: str = "hour",
    org_id: str | None = None,
) -> list[dict[str, Any]]:
    """Get trend data for a metric."""
    if end_time is None:
        end_time = datetime.now(timezone.utc)
    if start_time is None:
        start_time = end_time - timedelta(days=7)

    tracker = _get_tracker()
    return tracker.get_trend_data(metric, start_time, end_time, granularity, org_id)


def get_top_users(
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    org_id: str | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Get top users by activity."""
    if end_time is None:
        end_time = datetime.now(timezone.utc)
    if start_time is None:
        start_time = end_time - timedelta(days=30)

    tracker = _get_tracker()
    return tracker.get_top_users(start_time, end_time, org_id, limit)


def get_investigation_metrics(
    org_id: str | None = None,
) -> dict[str, Any]:
    """Get investigation performance metrics."""
    tracker = _get_tracker()

    stats = tracker.get_histogram_stats("investigation.duration_minutes")

    return {
        "total_investigations": tracker.get_counter("events.investigation_start"),
        "completed_investigations": tracker.get_counter("events.investigation_complete"),
        "duration_stats": stats,
        "by_severity": {
            "critical": tracker.get_counter("investigations", {"severity": "critical"}),
            "high": tracker.get_counter("investigations", {"severity": "high"}),
            "medium": tracker.get_counter("investigations", {"severity": "medium"}),
            "low": tracker.get_counter("investigations", {"severity": "low"}),
        },
    }
