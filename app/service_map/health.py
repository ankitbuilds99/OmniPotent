"""Real-time health overlay for service map.

Provides live health status updates for visualization.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from app.service_map.models import (
    HealthStatus,
    ServiceHealth,
)

log = logging.getLogger("devcopilot.service_map.health")


@dataclass
class HealthSnapshot:
    """A point-in-time health snapshot for a service."""

    service_id: str
    status: HealthStatus
    error_rate: float
    latency_p99_ms: float
    throughput_rps: float
    timestamp: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "service_id": self.service_id,
            "status": self.status.value,
            "error_rate": round(self.error_rate, 2),
            "latency_p99_ms": round(self.latency_p99_ms, 1),
            "throughput_rps": round(self.throughput_rps, 1),
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class HealthTrend:
    """Trend information for service health."""

    service_id: str
    error_rate_trend: str
    latency_trend: str
    throughput_trend: str
    status_changes_last_hour: int
    is_improving: bool
    is_degrading: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "service_id": self.service_id,
            "error_rate_trend": self.error_rate_trend,
            "latency_trend": self.latency_trend,
            "throughput_trend": self.throughput_trend,
            "status_changes_last_hour": self.status_changes_last_hour,
            "is_improving": self.is_improving,
            "is_degrading": self.is_degrading,
        }


@dataclass
class HealthAlert:
    """A health alert for a service."""

    service_id: str
    alert_type: str
    severity: str
    message: str
    threshold: float
    current_value: float
    triggered_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "service_id": self.service_id,
            "alert_type": self.alert_type,
            "severity": self.severity,
            "message": self.message,
            "threshold": self.threshold,
            "current_value": round(self.current_value, 2),
            "triggered_at": self.triggered_at.isoformat(),
        }


class HealthOverlay:
    """Manages real-time health overlay for service map."""

    def __init__(self) -> None:
        self._snapshots: dict[str, list[HealthSnapshot]] = {}
        self._alerts: list[HealthAlert] = []
        self._thresholds = {
            "error_rate_warning": 0.01,
            "error_rate_critical": 0.05,
            "latency_warning": 500,
            "latency_critical": 2000,
        }
        self._retention_hours = 24

    def record_snapshot(
        self,
        service_id: str,
        health: ServiceHealth,
    ) -> HealthSnapshot:
        """Record a health snapshot."""
        snapshot = HealthSnapshot(
            service_id=service_id,
            status=health.status,
            error_rate=health.error_rate,
            latency_p99_ms=health.latency_p99_ms,
            throughput_rps=health.throughput_rps,
            timestamp=datetime.now(timezone.utc),
        )

        if service_id not in self._snapshots:
            self._snapshots[service_id] = []

        self._snapshots[service_id].append(snapshot)

        cutoff = datetime.now(timezone.utc) - timedelta(hours=self._retention_hours)
        self._snapshots[service_id] = [
            s for s in self._snapshots[service_id]
            if s.timestamp > cutoff
        ]

        self._check_alerts(service_id, health)

        return snapshot

    def _check_alerts(
        self,
        service_id: str,
        health: ServiceHealth,
    ) -> None:
        """Check if health metrics trigger alerts."""
        now = datetime.now(timezone.utc)

        if health.error_rate >= self._thresholds["error_rate_critical"]:
            self._alerts.append(HealthAlert(
                service_id=service_id,
                alert_type="error_rate",
                severity="critical",
                message=f"Error rate {health.error_rate*100:.1f}% exceeds critical threshold",
                threshold=self._thresholds["error_rate_critical"],
                current_value=health.error_rate,
                triggered_at=now,
            ))
        elif health.error_rate >= self._thresholds["error_rate_warning"]:
            self._alerts.append(HealthAlert(
                service_id=service_id,
                alert_type="error_rate",
                severity="warning",
                message=f"Error rate {health.error_rate*100:.1f}% exceeds warning threshold",
                threshold=self._thresholds["error_rate_warning"],
                current_value=health.error_rate,
                triggered_at=now,
            ))

        if health.latency_p99_ms >= self._thresholds["latency_critical"]:
            self._alerts.append(HealthAlert(
                service_id=service_id,
                alert_type="latency",
                severity="critical",
                message=f"P99 latency {health.latency_p99_ms:.0f}ms exceeds critical threshold",
                threshold=self._thresholds["latency_critical"],
                current_value=health.latency_p99_ms,
                triggered_at=now,
            ))
        elif health.latency_p99_ms >= self._thresholds["latency_warning"]:
            self._alerts.append(HealthAlert(
                service_id=service_id,
                alert_type="latency",
                severity="warning",
                message=f"P99 latency {health.latency_p99_ms:.0f}ms exceeds warning threshold",
                threshold=self._thresholds["latency_warning"],
                current_value=health.latency_p99_ms,
                triggered_at=now,
            ))

    def get_trend(self, service_id: str) -> HealthTrend | None:
        """Get health trend for a service."""
        snapshots = self._snapshots.get(service_id, [])
        if len(snapshots) < 2:
            return None

        recent = snapshots[-5:]
        older = snapshots[-10:-5] if len(snapshots) >= 10 else snapshots[:len(snapshots)//2]

        if not older:
            older = recent[:1]

        recent_error_avg = sum(s.error_rate for s in recent) / len(recent)
        older_error_avg = sum(s.error_rate for s in older) / len(older)

        recent_latency_avg = sum(s.latency_p99_ms for s in recent) / len(recent)
        older_latency_avg = sum(s.latency_p99_ms for s in older) / len(older)

        recent_throughput_avg = sum(s.throughput_rps for s in recent) / len(recent)
        older_throughput_avg = sum(s.throughput_rps for s in older) / len(older)

        def trend_direction(recent_val: float, older_val: float) -> str:
            if older_val == 0:
                return "stable"
            change = (recent_val - older_val) / older_val
            if change > 0.1:
                return "increasing"
            elif change < -0.1:
                return "decreasing"
            return "stable"

        error_trend = trend_direction(recent_error_avg, older_error_avg)
        latency_trend = trend_direction(recent_latency_avg, older_latency_avg)
        throughput_trend = trend_direction(recent_throughput_avg, older_throughput_avg)

        status_changes = 0
        for i in range(1, len(snapshots)):
            if snapshots[i].status != snapshots[i-1].status:
                status_changes += 1

        is_improving = (
            error_trend == "decreasing"
            or latency_trend == "decreasing"
        )
        is_degrading = (
            error_trend == "increasing"
            or latency_trend == "increasing"
        )

        return HealthTrend(
            service_id=service_id,
            error_rate_trend=error_trend,
            latency_trend=latency_trend,
            throughput_trend=throughput_trend,
            status_changes_last_hour=status_changes,
            is_improving=is_improving,
            is_degrading=is_degrading,
        )

    def get_recent_alerts(
        self,
        service_id: str | None = None,
        severity: str | None = None,
        limit: int = 10,
    ) -> list[HealthAlert]:
        """Get recent alerts, optionally filtered."""
        alerts = self._alerts

        if service_id:
            alerts = [a for a in alerts if a.service_id == service_id]

        if severity:
            alerts = [a for a in alerts if a.severity == severity]

        return sorted(
            alerts,
            key=lambda a: a.triggered_at,
            reverse=True,
        )[:limit]

    def get_health_summary(self) -> dict[str, Any]:
        """Get overall health summary."""
        total_services = len(self._snapshots)

        latest_by_service: dict[str, HealthSnapshot] = {}
        for service_id, snapshots in self._snapshots.items():
            if snapshots:
                latest_by_service[service_id] = snapshots[-1]

        healthy = sum(
            1 for s in latest_by_service.values()
            if s.status == HealthStatus.HEALTHY
        )
        degraded = sum(
            1 for s in latest_by_service.values()
            if s.status == HealthStatus.DEGRADED
        )
        failing = sum(
            1 for s in latest_by_service.values()
            if s.status == HealthStatus.FAILING
        )

        critical_alerts = sum(
            1 for a in self._alerts
            if a.severity == "critical"
        )

        return {
            "total_services": total_services,
            "healthy": healthy,
            "degraded": degraded,
            "failing": failing,
            "health_percent": round(healthy / total_services * 100, 1) if total_services > 0 else 100,
            "critical_alerts": critical_alerts,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    def get_historical_data(
        self,
        service_id: str,
        hours: int = 24,
    ) -> list[HealthSnapshot]:
        """Get historical health data for a service."""
        snapshots = self._snapshots.get(service_id, [])
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

        return [s for s in snapshots if s.timestamp > cutoff]

    def set_threshold(
        self,
        threshold_name: str,
        value: float,
    ) -> bool:
        """Set an alert threshold."""
        if threshold_name in self._thresholds:
            self._thresholds[threshold_name] = value
            return True
        return False


_overlay: HealthOverlay | None = None


def get_health_overlay() -> HealthOverlay:
    """Get the default health overlay."""
    global _overlay
    if _overlay is None:
        _overlay = HealthOverlay()
    return _overlay
