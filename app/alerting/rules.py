"""Alert rule definitions and conditions.

Defines the structure of alert rules including:
- Threshold-based conditions
- Rate of change conditions
- SLO burn rate conditions
- Anomaly detection conditions
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class AlertSeverity(Enum):
    """Alert severity levels."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class AlertCondition(Enum):
    """Types of alert conditions."""

    THRESHOLD_ABOVE = "threshold_above"
    THRESHOLD_BELOW = "threshold_below"
    RATE_OF_CHANGE = "rate_of_change"
    SLO_BURN_RATE = "slo_burn_rate"
    ANOMALY = "anomaly"
    ABSENCE = "absence"


@dataclass
class AlertRule:
    """Definition of an alert rule.

    Attributes:
        id: Unique identifier for the rule
        name: Human-readable name
        description: Detailed description
        service: Service this rule monitors
        metric: Metric name to evaluate
        condition: Type of condition to check
        threshold: Threshold value for the condition
        duration_seconds: How long condition must be true before alerting
        severity: Alert severity when triggered
        enabled: Whether the rule is active
        labels: Additional labels for grouping/routing
        annotations: Additional context for the alert
        notification_channels: Where to send alerts (pagerduty, slack, etc.)
    """

    id: str
    name: str
    description: str
    service: str
    metric: str
    condition: AlertCondition
    threshold: float
    duration_seconds: int = 300
    severity: AlertSeverity = AlertSeverity.HIGH
    enabled: bool = True
    labels: dict[str, str] = field(default_factory=dict)
    annotations: dict[str, str] = field(default_factory=dict)
    notification_channels: list[str] = field(default_factory=lambda: ["slack"])
    slo_target: float | None = None
    burn_rate_window: int = 3600

    def to_dict(self) -> dict[str, Any]:
        """Convert rule to dictionary."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "service": self.service,
            "metric": self.metric,
            "condition": self.condition.value,
            "threshold": self.threshold,
            "duration_seconds": self.duration_seconds,
            "severity": self.severity.value,
            "enabled": self.enabled,
            "labels": self.labels,
            "annotations": self.annotations,
            "notification_channels": self.notification_channels,
            "slo_target": self.slo_target,
            "burn_rate_window": self.burn_rate_window,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AlertRule":
        """Create rule from dictionary."""
        return cls(
            id=data["id"],
            name=data["name"],
            description=data.get("description", ""),
            service=data["service"],
            metric=data["metric"],
            condition=AlertCondition(data["condition"]),
            threshold=data["threshold"],
            duration_seconds=data.get("duration_seconds", 300),
            severity=AlertSeverity(data.get("severity", "high")),
            enabled=data.get("enabled", True),
            labels=data.get("labels", {}),
            annotations=data.get("annotations", {}),
            notification_channels=data.get("notification_channels", ["slack"]),
            slo_target=data.get("slo_target"),
            burn_rate_window=data.get("burn_rate_window", 3600),
        )


@dataclass
class AlertEvent:
    """A triggered alert event.

    Attributes:
        id: Unique event identifier
        rule_id: ID of the rule that triggered
        rule_name: Name of the rule
        service: Affected service
        severity: Alert severity
        status: Current status (firing, resolved)
        message: Human-readable alert message
        value: The metric value that triggered the alert
        threshold: The threshold that was exceeded
        started_at: When the alert started firing
        resolved_at: When the alert was resolved (if applicable)
        labels: Labels from the rule
        annotations: Annotations from the rule
    """

    id: str
    rule_id: str
    rule_name: str
    service: str
    severity: AlertSeverity
    status: str
    message: str
    value: float
    threshold: float
    started_at: datetime
    resolved_at: datetime | None = None
    labels: dict[str, str] = field(default_factory=dict)
    annotations: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert event to dictionary."""
        return {
            "id": self.id,
            "rule_id": self.rule_id,
            "rule_name": self.rule_name,
            "service": self.service,
            "severity": self.severity.value,
            "status": self.status,
            "message": self.message,
            "value": self.value,
            "threshold": self.threshold,
            "started_at": self.started_at.isoformat(),
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
            "labels": self.labels,
            "annotations": self.annotations,
        }


# Pre-defined alert rules for common scenarios
DEFAULT_RULES: list[dict[str, Any]] = [
    {
        "id": "high-error-rate",
        "name": "High Error Rate",
        "description": "Error rate exceeds 5% of total requests",
        "service": "*",
        "metric": "error_rate",
        "condition": "threshold_above",
        "threshold": 0.05,
        "duration_seconds": 300,
        "severity": "high",
        "notification_channels": ["slack", "pagerduty"],
    },
    {
        "id": "high-latency-p99",
        "name": "High P99 Latency",
        "description": "P99 latency exceeds 2 seconds",
        "service": "*",
        "metric": "latency_p99",
        "condition": "threshold_above",
        "threshold": 2000,
        "duration_seconds": 300,
        "severity": "high",
        "notification_channels": ["slack"],
    },
    {
        "id": "slo-burn-rate",
        "name": "SLO Burn Rate Alert",
        "description": "Error budget burning faster than expected",
        "service": "*",
        "metric": "error_budget",
        "condition": "slo_burn_rate",
        "threshold": 14.4,
        "duration_seconds": 300,
        "severity": "critical",
        "slo_target": 0.999,
        "burn_rate_window": 3600,
        "notification_channels": ["slack", "pagerduty"],
    },
    {
        "id": "high-cpu-usage",
        "name": "High CPU Usage",
        "description": "CPU usage exceeds 80%",
        "service": "*",
        "metric": "cpu_percent",
        "condition": "threshold_above",
        "threshold": 80,
        "duration_seconds": 600,
        "severity": "medium",
        "notification_channels": ["slack"],
    },
    {
        "id": "high-memory-usage",
        "name": "High Memory Usage",
        "description": "Memory usage exceeds 85%",
        "service": "*",
        "metric": "memory_percent",
        "condition": "threshold_above",
        "threshold": 85,
        "duration_seconds": 600,
        "severity": "medium",
        "notification_channels": ["slack"],
    },
    {
        "id": "pod-restart-loop",
        "name": "Pod Restart Loop",
        "description": "Pod restarting more than 3 times in 15 minutes",
        "service": "*",
        "metric": "pod_restarts",
        "condition": "rate_of_change",
        "threshold": 3,
        "duration_seconds": 900,
        "severity": "high",
        "notification_channels": ["slack", "pagerduty"],
    },
    {
        "id": "deployment-failed",
        "name": "Deployment Failed",
        "description": "Deployment has failed status",
        "service": "*",
        "metric": "deployment_status",
        "condition": "threshold_below",
        "threshold": 1,
        "duration_seconds": 60,
        "severity": "critical",
        "notification_channels": ["slack", "pagerduty"],
    },
    {
        "id": "no-metrics",
        "name": "Metrics Absence",
        "description": "No metrics received for 5 minutes",
        "service": "*",
        "metric": "heartbeat",
        "condition": "absence",
        "threshold": 0,
        "duration_seconds": 300,
        "severity": "high",
        "notification_channels": ["slack", "pagerduty"],
    },
]
