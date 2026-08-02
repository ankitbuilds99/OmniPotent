"""Feature Store for Event Intelligence.

Manages time-series statistics and features for ML-based
noise reduction and severity prediction.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from collections import defaultdict
import statistics

log = logging.getLogger("omnipotent.event_intelligence.feature_store")


@dataclass
class AlertFeatures:
    """Features extracted for an alert."""
    fingerprint: str
    service: str
    metric_name: str

    # Time-series statistics
    hourly_avg: float = 0.0
    hourly_stddev: float = 0.0
    hourly_min: float = 0.0
    hourly_max: float = 0.0
    daily_avg: float = 0.0
    daily_stddev: float = 0.0
    weekly_avg: float = 0.0

    # Alert patterns
    alert_count_1h: int = 0
    alert_count_24h: int = 0
    alert_count_7d: int = 0
    flap_count_1h: int = 0
    auto_resolve_rate_7d: float = 0.0
    avg_resolution_time_seconds: float = 0.0

    # Service patterns
    service_alert_count_1h: int = 0
    service_incident_count_24h: int = 0

    # Temporal patterns
    hour_of_day: int = 0
    day_of_week: int = 0
    is_business_hours: bool = False
    is_weekend: bool = False

    # Context
    deployment_in_progress: bool = False
    maintenance_active: bool = False

    def to_vector(self) -> list[float]:
        """Convert features to ML-ready vector."""
        return [
            self.hourly_avg,
            self.hourly_stddev,
            self.daily_avg,
            self.daily_stddev,
            float(self.alert_count_1h),
            float(self.alert_count_24h),
            float(self.flap_count_1h),
            self.auto_resolve_rate_7d,
            self.avg_resolution_time_seconds / 3600,  # Normalize to hours
            float(self.service_alert_count_1h),
            float(self.hour_of_day) / 24,  # Normalize
            float(self.day_of_week) / 7,  # Normalize
            float(self.is_business_hours),
            float(self.is_weekend),
            float(self.deployment_in_progress),
            float(self.maintenance_active),
        ]


@dataclass
class MetricDataPoint:
    """A single metric data point."""
    value: float
    timestamp: datetime
    labels: dict[str, str] = field(default_factory=dict)


class FeatureStore:
    """Manages features and time-series data for event intelligence.

    Features:
    - Stores metric time-series for statistical analysis
    - Tracks alert history for pattern detection
    - Computes rolling statistics (mean, stddev, percentiles)
    - Integrates with Redis for distributed state
    """

    def __init__(
        self,
        redis_client: Any = None,
        retention_hours: int = 168,  # 7 days
    ):
        self.redis = redis_client
        self.retention_hours = retention_hours

        # In-memory storage (fallback when Redis unavailable)
        self._metric_history: dict[str, list[MetricDataPoint]] = defaultdict(list)
        self._alert_history: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._service_alerts: dict[str, list[datetime]] = defaultdict(list)
        self._deployments: dict[str, datetime] = {}
        self._maintenance_windows: list[dict[str, Any]] = []

    async def record_metric(
        self,
        service: str,
        metric_name: str,
        value: float,
        timestamp: Optional[datetime] = None,
        labels: Optional[dict[str, str]] = None,
    ) -> None:
        """Record a metric data point."""
        ts = timestamp or datetime.now(timezone.utc)
        key = f"{service}:{metric_name}"

        point = MetricDataPoint(value=value, timestamp=ts, labels=labels or {})

        # Store in memory
        self._metric_history[key].append(point)
        self._cleanup_old_data(key)

        # Store in Redis if available
        if self.redis:
            try:
                redis_key = f"metrics:{key}"
                await self.redis.zadd(
                    redis_key,
                    {f"{ts.timestamp()}:{value}": ts.timestamp()}
                )
                await self.redis.expire(redis_key, self.retention_hours * 3600)
            except Exception as e:
                log.warning(f"Redis metric storage failed: {e}")

    async def record_alert(
        self,
        fingerprint: str,
        service: str,
        metric_name: str,
        severity: str,
        timestamp: Optional[datetime] = None,
        resolved: bool = False,
        resolution_time_seconds: Optional[int] = None,
        auto_resolved: bool = False,
    ) -> None:
        """Record an alert occurrence."""
        ts = timestamp or datetime.now(timezone.utc)

        alert_record = {
            "timestamp": ts,
            "service": service,
            "metric_name": metric_name,
            "severity": severity,
            "resolved": resolved,
            "resolution_time_seconds": resolution_time_seconds,
            "auto_resolved": auto_resolved,
        }

        self._alert_history[fingerprint].append(alert_record)
        self._service_alerts[service].append(ts)

        # Cleanup
        self._cleanup_alert_history(fingerprint)
        self._cleanup_service_alerts(service)

        # Store in Redis if available
        if self.redis:
            try:
                await self.redis.hincrby(f"alert_counts:{service}", "total", 1)
                if auto_resolved:
                    await self.redis.hincrby(f"alert_counts:{service}", "auto_resolved", 1)
            except Exception as e:
                log.warning(f"Redis alert storage failed: {e}")

    def record_deployment(
        self,
        service: str,
        started_at: Optional[datetime] = None,
    ) -> None:
        """Record a deployment start."""
        self._deployments[service] = started_at or datetime.now(timezone.utc)

    def clear_deployment(self, service: str) -> None:
        """Clear deployment flag for a service."""
        self._deployments.pop(service, None)

    def is_deployment_in_progress(self, service: str, window_minutes: int = 30) -> bool:
        """Check if deployment is in progress for a service."""
        if service not in self._deployments:
            return False

        started = self._deployments[service]
        elapsed = (datetime.now(timezone.utc) - started).total_seconds() / 60
        return elapsed < window_minutes

    def add_maintenance_window(
        self,
        services: list[str],
        start: datetime,
        end: datetime,
        description: str = "",
    ) -> None:
        """Add a maintenance window."""
        self._maintenance_windows.append({
            "services": services,
            "start": start,
            "end": end,
            "description": description,
        })

    def is_in_maintenance(self, service: str) -> bool:
        """Check if service is currently in maintenance."""
        now = datetime.now(timezone.utc)

        for window in self._maintenance_windows:
            if now < window["start"] or now > window["end"]:
                continue
            if service in window["services"] or "*" in window["services"]:
                return True

        return False

    async def get_features(
        self,
        fingerprint: str,
        service: str,
        metric_name: str,
    ) -> AlertFeatures:
        """Get features for an alert."""
        key = f"{service}:{metric_name}"
        now = datetime.now(timezone.utc)

        # Calculate time-series statistics
        hourly_stats = self._calculate_stats(key, hours=1)
        daily_stats = self._calculate_stats(key, hours=24)
        weekly_stats = self._calculate_stats(key, hours=168)

        # Calculate alert patterns
        alert_history = self._alert_history.get(fingerprint, [])
        hour_ago = now - timedelta(hours=1)
        day_ago = now - timedelta(hours=24)
        week_ago = now - timedelta(days=7)

        alert_count_1h = sum(1 for a in alert_history if a["timestamp"] > hour_ago)
        alert_count_24h = sum(1 for a in alert_history if a["timestamp"] > day_ago)
        alert_count_7d = sum(1 for a in alert_history if a["timestamp"] > week_ago)

        # Calculate flap count (rapid state changes)
        flap_count = self._calculate_flap_count(fingerprint, hours=1)

        # Calculate auto-resolve rate
        resolved_alerts = [a for a in alert_history if a.get("resolved")]
        auto_resolved = [a for a in resolved_alerts if a.get("auto_resolved")]
        auto_resolve_rate = (
            len(auto_resolved) / len(resolved_alerts) if resolved_alerts else 0
        )

        # Calculate average resolution time
        resolution_times = [
            a["resolution_time_seconds"]
            for a in alert_history
            if a.get("resolution_time_seconds") is not None
        ]
        avg_resolution_time = (
            statistics.mean(resolution_times) if resolution_times else 0
        )

        # Service-level patterns
        service_alerts = self._service_alerts.get(service, [])
        service_alert_count_1h = sum(1 for t in service_alerts if t > hour_ago)

        # Temporal features
        hour_of_day = now.hour
        day_of_week = now.weekday()
        is_business_hours = 9 <= hour_of_day <= 18 and day_of_week < 5
        is_weekend = day_of_week >= 5

        return AlertFeatures(
            fingerprint=fingerprint,
            service=service,
            metric_name=metric_name,
            hourly_avg=hourly_stats["mean"],
            hourly_stddev=hourly_stats["stddev"],
            hourly_min=hourly_stats["min"],
            hourly_max=hourly_stats["max"],
            daily_avg=daily_stats["mean"],
            daily_stddev=daily_stats["stddev"],
            weekly_avg=weekly_stats["mean"],
            alert_count_1h=alert_count_1h,
            alert_count_24h=alert_count_24h,
            alert_count_7d=alert_count_7d,
            flap_count_1h=flap_count,
            auto_resolve_rate_7d=auto_resolve_rate,
            avg_resolution_time_seconds=avg_resolution_time,
            service_alert_count_1h=service_alert_count_1h,
            service_incident_count_24h=0,  # Would need incident tracking
            hour_of_day=hour_of_day,
            day_of_week=day_of_week,
            is_business_hours=is_business_hours,
            is_weekend=is_weekend,
            deployment_in_progress=self.is_deployment_in_progress(service),
            maintenance_active=self.is_in_maintenance(service),
        )

    def _calculate_stats(self, key: str, hours: int) -> dict[str, float]:
        """Calculate statistics for a metric over a time window."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        points = self._metric_history.get(key, [])
        values = [p.value for p in points if p.timestamp > cutoff]

        if not values:
            return {"mean": 0, "stddev": 0, "min": 0, "max": 0}

        return {
            "mean": statistics.mean(values),
            "stddev": statistics.stdev(values) if len(values) > 1 else 0,
            "min": min(values),
            "max": max(values),
        }

    def _calculate_flap_count(self, fingerprint: str, hours: int) -> int:
        """Calculate number of state changes (flaps) in time window."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        alerts = self._alert_history.get(fingerprint, [])

        # Count alerts in window as a proxy for flapping
        return sum(1 for a in alerts if a["timestamp"] > cutoff)

    def _cleanup_old_data(self, key: str) -> None:
        """Remove data older than retention period."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=self.retention_hours)
        self._metric_history[key] = [
            p for p in self._metric_history.get(key, [])
            if p.timestamp > cutoff
        ]

    def _cleanup_alert_history(self, fingerprint: str) -> None:
        """Remove old alert history."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=self.retention_hours)
        self._alert_history[fingerprint] = [
            a for a in self._alert_history.get(fingerprint, [])
            if a["timestamp"] > cutoff
        ]

    def _cleanup_service_alerts(self, service: str) -> None:
        """Remove old service alert timestamps."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        self._service_alerts[service] = [
            t for t in self._service_alerts.get(service, [])
            if t > cutoff
        ]

    def get_stats(self) -> dict[str, Any]:
        """Get feature store statistics."""
        return {
            "metric_keys": len(self._metric_history),
            "total_metrics": sum(len(v) for v in self._metric_history.values()),
            "alert_fingerprints": len(self._alert_history),
            "total_alerts": sum(len(v) for v in self._alert_history.values()),
            "active_deployments": len(self._deployments),
            "maintenance_windows": len(self._maintenance_windows),
        }

    def clear(self) -> None:
        """Clear all stored data."""
        self._metric_history.clear()
        self._alert_history.clear()
        self._service_alerts.clear()
        self._deployments.clear()
        self._maintenance_windows.clear()
