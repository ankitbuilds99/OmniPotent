"""Anomaly Detection.

Statistical anomaly detection for incident metrics and patterns.
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("devcopilot.analytics_advanced.anomaly_detection")


class AnomalyType(str, Enum):
    INCIDENT_SPIKE = "incident_spike"
    MTTR_SPIKE = "mttr_spike"
    ERROR_RATE_SPIKE = "error_rate_spike"
    LATENCY_SPIKE = "latency_spike"
    UNUSUAL_HOUR = "unusual_hour"
    UNUSUAL_SERVICE = "unusual_service"
    SEVERITY_ESCALATION = "severity_escalation"
    REPEAT_INCIDENT = "repeat_incident"


class AnomalySeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class MetricDataPoint:
    """A metric data point for anomaly detection."""

    timestamp: datetime
    metric_name: str
    value: float
    service: str | None = None
    labels: dict[str, str] = field(default_factory=dict)


@dataclass
class Anomaly:
    """A detected anomaly."""

    anomaly_id: str
    anomaly_type: AnomalyType
    severity: AnomalySeverity
    detected_at: datetime
    metric_name: str
    actual_value: float
    expected_value: float
    deviation_sigma: float
    description: str
    service: str | None = None
    recommendation: str | None = None
    related_incidents: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.anomaly_id,
            "type": self.anomaly_type.value,
            "severity": self.severity.value,
            "detected_at": self.detected_at.isoformat(),
            "metric": self.metric_name,
            "values": {
                "actual": round(self.actual_value, 2),
                "expected": round(self.expected_value, 2),
                "deviation_sigma": round(self.deviation_sigma, 2),
            },
            "description": self.description,
            "service": self.service,
            "recommendation": self.recommendation,
            "related_incidents": self.related_incidents,
        }


@dataclass
class AnomalyDetectionConfig:
    """Configuration for anomaly detection."""

    sigma_threshold: float = 2.5
    min_data_points: int = 10
    lookback_hours: int = 24
    baseline_days: int = 14
    suppress_repeat_minutes: int = 30


@dataclass
class DetectionResult:
    """Result of anomaly detection run."""

    timestamp: datetime
    anomalies: list[Anomaly]
    metrics_checked: int
    data_points_analyzed: int
    config: AnomalyDetectionConfig

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "summary": {
                "anomalies_found": len(self.anomalies),
                "metrics_checked": self.metrics_checked,
                "data_points_analyzed": self.data_points_analyzed,
            },
            "anomalies": [a.to_dict() for a in self.anomalies],
            "config": {
                "sigma_threshold": self.config.sigma_threshold,
                "baseline_days": self.config.baseline_days,
            },
        }


class AnomalyDetector:
    """Detects anomalies in incident and metric data."""

    def __init__(
        self,
        config: AnomalyDetectionConfig | None = None,
    ) -> None:
        self._config = config or AnomalyDetectionConfig()
        self._data_points: list[MetricDataPoint] = []
        self._recent_anomalies: list[Anomaly] = []
        self._anomaly_counter = 0

    def ingest_data_point(self, point: MetricDataPoint) -> None:
        """Ingest a metric data point."""
        self._data_points.append(point)

        if len(self._data_points) > 100000:
            cutoff = datetime.now(timezone.utc) - timedelta(days=30)
            self._data_points = [
                p for p in self._data_points if p.timestamp >= cutoff
            ]

    def ingest_batch(self, points: list[MetricDataPoint]) -> None:
        """Ingest a batch of data points."""
        for point in points:
            self.ingest_data_point(point)

    def detect_anomalies(
        self,
        metric_name: str | None = None,
        service: str | None = None,
    ) -> DetectionResult:
        """Run anomaly detection."""
        now = datetime.now(timezone.utc)

        relevant_points = self._get_relevant_points(metric_name, service)

        if not relevant_points:
            return DetectionResult(
                timestamp=now,
                anomalies=[],
                metrics_checked=0,
                data_points_analyzed=0,
                config=self._config,
            )

        by_metric: dict[str, list[MetricDataPoint]] = {}
        for point in relevant_points:
            key = f"{point.metric_name}:{point.service or 'all'}"
            if key not in by_metric:
                by_metric[key] = []
            by_metric[key].append(point)

        anomalies = []

        for metric_key, points in by_metric.items():
            detected = self._detect_for_metric(points)
            anomalies.extend(detected)

        anomalies = self._suppress_duplicates(anomalies)

        self._recent_anomalies.extend(anomalies)
        if len(self._recent_anomalies) > 1000:
            cutoff = now - timedelta(days=7)
            self._recent_anomalies = [
                a for a in self._recent_anomalies if a.detected_at >= cutoff
            ]

        return DetectionResult(
            timestamp=now,
            anomalies=anomalies,
            metrics_checked=len(by_metric),
            data_points_analyzed=len(relevant_points),
            config=self._config,
        )

    def _get_relevant_points(
        self,
        metric_name: str | None,
        service: str | None,
    ) -> list[MetricDataPoint]:
        """Get relevant data points for detection."""
        now = datetime.now(timezone.utc)
        baseline_start = now - timedelta(days=self._config.baseline_days)

        points = [
            p for p in self._data_points
            if p.timestamp >= baseline_start
        ]

        if metric_name:
            points = [p for p in points if p.metric_name == metric_name]

        if service:
            points = [p for p in points if p.service == service]

        return points

    def _detect_for_metric(
        self,
        points: list[MetricDataPoint],
    ) -> list[Anomaly]:
        """Detect anomalies for a specific metric."""
        if len(points) < self._config.min_data_points:
            return []

        sorted_points = sorted(points, key=lambda p: p.timestamp)

        now = datetime.now(timezone.utc)
        lookback_start = now - timedelta(hours=self._config.lookback_hours)

        baseline = [p for p in sorted_points if p.timestamp < lookback_start]
        recent = [p for p in sorted_points if p.timestamp >= lookback_start]

        if len(baseline) < self._config.min_data_points:
            return []

        baseline_values = [p.value for p in baseline]
        mean = statistics.mean(baseline_values)
        std_dev = statistics.stdev(baseline_values) if len(baseline_values) > 1 else 0

        if std_dev == 0:
            std_dev = mean * 0.1 if mean > 0 else 1.0

        anomalies = []

        for point in recent:
            deviation = abs(point.value - mean) / std_dev

            if deviation >= self._config.sigma_threshold:
                anomaly = self._create_anomaly(
                    point=point,
                    expected=mean,
                    deviation=deviation,
                )
                anomalies.append(anomaly)

        return anomalies

    def _create_anomaly(
        self,
        point: MetricDataPoint,
        expected: float,
        deviation: float,
    ) -> Anomaly:
        """Create an anomaly from a data point."""
        self._anomaly_counter += 1

        anomaly_type = self._determine_anomaly_type(point.metric_name)
        severity = self._determine_severity(deviation)

        direction = "above" if point.value > expected else "below"
        description = (
            f"{point.metric_name} is {deviation:.1f} sigma {direction} normal "
            f"(actual: {point.value:.1f}, expected: {expected:.1f})"
        )

        recommendation = self._generate_recommendation(anomaly_type, severity, point)

        return Anomaly(
            anomaly_id=f"anomaly_{self._anomaly_counter}",
            anomaly_type=anomaly_type,
            severity=severity,
            detected_at=point.timestamp,
            metric_name=point.metric_name,
            actual_value=point.value,
            expected_value=expected,
            deviation_sigma=deviation,
            description=description,
            service=point.service,
            recommendation=recommendation,
        )

    def _determine_anomaly_type(self, metric_name: str) -> AnomalyType:
        """Determine anomaly type from metric name."""
        metric_lower = metric_name.lower()

        if "incident" in metric_lower or "count" in metric_lower:
            return AnomalyType.INCIDENT_SPIKE
        if "mttr" in metric_lower or "resolution" in metric_lower:
            return AnomalyType.MTTR_SPIKE
        if "error" in metric_lower:
            return AnomalyType.ERROR_RATE_SPIKE
        if "latency" in metric_lower or "response" in metric_lower:
            return AnomalyType.LATENCY_SPIKE

        return AnomalyType.INCIDENT_SPIKE

    def _determine_severity(self, deviation: float) -> AnomalySeverity:
        """Determine severity from deviation."""
        if deviation >= 5:
            return AnomalySeverity.CRITICAL
        if deviation >= 4:
            return AnomalySeverity.HIGH
        if deviation >= 3:
            return AnomalySeverity.MEDIUM
        return AnomalySeverity.LOW

    def _generate_recommendation(
        self,
        anomaly_type: AnomalyType,
        severity: AnomalySeverity,
        point: MetricDataPoint,
    ) -> str:
        """Generate recommendation for anomaly."""
        recommendations = {
            AnomalyType.INCIDENT_SPIKE: (
                "Investigate recent deployments and infrastructure changes. "
                "Check for cascading failures."
            ),
            AnomalyType.MTTR_SPIKE: (
                "Review investigation workflow and runbooks. "
                "Consider additional training or tooling."
            ),
            AnomalyType.ERROR_RATE_SPIKE: (
                "Check application logs and recent deployments. "
                "Consider rolling back if release-correlated."
            ),
            AnomalyType.LATENCY_SPIKE: (
                "Check database performance, external dependencies, and resource utilization."
            ),
            AnomalyType.UNUSUAL_HOUR: (
                "Verify on-call coverage and incident response for off-hours."
            ),
            AnomalyType.UNUSUAL_SERVICE: (
                "Review service health and recent changes to this service."
            ),
            AnomalyType.SEVERITY_ESCALATION: (
                "Investigate why severity is escalating. Review triage process."
            ),
            AnomalyType.REPEAT_INCIDENT: (
                "Create tracking ticket for root cause. "
                "Consider allocating engineering time for permanent fix."
            ),
        }

        base = recommendations.get(anomaly_type, "Investigate the anomaly.")

        if severity == AnomalySeverity.CRITICAL:
            base = f"URGENT: {base} Page on-call if not already engaged."
        elif severity == AnomalySeverity.HIGH:
            base = f"HIGH PRIORITY: {base}"

        return base

    def _suppress_duplicates(
        self,
        anomalies: list[Anomaly],
    ) -> list[Anomaly]:
        """Suppress duplicate anomalies within time window."""
        suppress_window = timedelta(minutes=self._config.suppress_repeat_minutes)

        unique = []
        seen: dict[str, datetime] = {}

        for anomaly in anomalies:
            key = f"{anomaly.metric_name}:{anomaly.service}:{anomaly.anomaly_type.value}"

            if key in seen:
                if anomaly.detected_at - seen[key] < suppress_window:
                    continue

            seen[key] = anomaly.detected_at
            unique.append(anomaly)

        return unique

    def detect_incident_anomalies(
        self,
        incidents: list[dict[str, Any]],
    ) -> list[Anomaly]:
        """Detect anomalies from incident list."""
        now = datetime.now(timezone.utc)

        for inc in incidents:
            created_at = inc.get("created_at", now)
            if isinstance(created_at, str):
                created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))

            self.ingest_data_point(MetricDataPoint(
                timestamp=created_at,
                metric_name="incident_count",
                value=1,
                service=inc.get("service"),
            ))

            if "mttr_minutes" in inc:
                self.ingest_data_point(MetricDataPoint(
                    timestamp=created_at,
                    metric_name="mttr_minutes",
                    value=inc["mttr_minutes"],
                    service=inc.get("service"),
                ))

        result = self.detect_anomalies()
        return result.anomalies

    def get_anomaly_summary(
        self,
        hours: int = 24,
    ) -> dict[str, Any]:
        """Get summary of recent anomalies."""
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=hours)

        recent = [
            a for a in self._recent_anomalies if a.detected_at >= cutoff
        ]

        by_type: dict[str, int] = {}
        by_severity: dict[str, int] = {}
        by_service: dict[str, int] = {}

        for a in recent:
            by_type[a.anomaly_type.value] = by_type.get(a.anomaly_type.value, 0) + 1
            by_severity[a.severity.value] = by_severity.get(a.severity.value, 0) + 1
            if a.service:
                by_service[a.service] = by_service.get(a.service, 0) + 1

        return {
            "hours": hours,
            "total_anomalies": len(recent),
            "by_type": by_type,
            "by_severity": by_severity,
            "by_service": by_service,
            "critical_count": by_severity.get("critical", 0),
            "requires_attention": by_severity.get("critical", 0) + by_severity.get("high", 0),
        }

    def configure(self, config: AnomalyDetectionConfig) -> None:
        """Update configuration."""
        self._config = config


_detector: AnomalyDetector | None = None


def get_anomaly_detector() -> AnomalyDetector:
    """Get the default anomaly detector."""
    global _detector
    if _detector is None:
        _detector = AnomalyDetector()
    return _detector
