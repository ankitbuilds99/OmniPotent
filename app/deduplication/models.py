"""Core data models for alert deduplication."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any
from uuid import UUID, uuid4


class Decision(Enum):
    """Deduplication decision types."""

    NEW_INCIDENT = "new_incident"
    ADD_TO_EXISTING = "add_to_existing"
    SUPPRESS = "suppress"
    FLAPPING = "flapping"


class SuppressionType(Enum):
    """Types of alert suppression."""

    MAINTENANCE = "maintenance"
    KNOWN_ISSUE = "known_issue"
    DEPENDENCY = "dependency"
    FLAPPING = "flapping"
    BUSINESS_RULE = "business_rule"
    AUTO_RESOLVE = "auto_resolve"


class DependencyType(Enum):
    """Service dependency relationship types."""

    CALLS = "calls"
    READS_FROM = "reads_from"
    WRITES_TO = "writes_to"
    DEPENDS_ON = "depends_on"
    PUBLISHES_TO = "publishes_to"
    SUBSCRIBES_TO = "subscribes_to"


class WindowConfidence(Enum):
    """Confidence level for temporal windows."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class AlertSeverity(Enum):
    """Alert severity levels."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class AlertSource(Enum):
    """Alert source systems."""

    DATADOG = "datadog"
    PROMETHEUS = "prometheus"
    PAGERDUTY = "pagerduty"
    NEW_RELIC = "newrelic"
    CLOUDWATCH = "cloudwatch"
    GRAFANA = "grafana"
    CUSTOM = "custom"


@dataclass
class RawAlert:
    """Raw alert from monitoring system."""

    alert_id: UUID = field(default_factory=uuid4)
    source: AlertSource = AlertSource.CUSTOM
    alert_name: str = ""
    alert_type: str = "metric"
    service: str = ""
    environment: str = "production"
    message: str = ""
    severity: AlertSeverity = AlertSeverity.MEDIUM
    timestamp: datetime = field(default_factory=datetime.utcnow)
    labels: dict[str, str] = field(default_factory=dict)
    annotations: dict[str, str] = field(default_factory=dict)
    value: float | None = None
    threshold: float | None = None
    region: str | None = None
    cluster: str | None = None
    namespace: str | None = None
    pod_name: str | None = None
    host: str | None = None
    trace_id: str | None = None
    raw_payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "alert_id": str(self.alert_id),
            "source": self.source.value,
            "alert_name": self.alert_name,
            "alert_type": self.alert_type,
            "service": self.service,
            "environment": self.environment,
            "message": self.message,
            "severity": self.severity.value,
            "timestamp": self.timestamp.isoformat(),
            "labels": self.labels,
            "annotations": self.annotations,
            "value": self.value,
            "threshold": self.threshold,
            "region": self.region,
            "cluster": self.cluster,
            "namespace": self.namespace,
            "pod_name": self.pod_name,
            "host": self.host,
            "trace_id": self.trace_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RawAlert:
        """Create from dictionary."""
        return cls(
            alert_id=UUID(data["alert_id"]) if "alert_id" in data else uuid4(),
            source=AlertSource(data.get("source", "custom")),
            alert_name=data.get("alert_name", ""),
            alert_type=data.get("alert_type", "metric"),
            service=data.get("service", ""),
            environment=data.get("environment", "production"),
            message=data.get("message", ""),
            severity=AlertSeverity(data.get("severity", "medium")),
            timestamp=datetime.fromisoformat(data["timestamp"])
            if "timestamp" in data
            else datetime.utcnow(),
            labels=data.get("labels", {}),
            annotations=data.get("annotations", {}),
            value=data.get("value"),
            threshold=data.get("threshold"),
            region=data.get("region"),
            cluster=data.get("cluster"),
            namespace=data.get("namespace"),
            pod_name=data.get("pod_name"),
            host=data.get("host"),
            trace_id=data.get("trace_id"),
            raw_payload=data.get("raw_payload", {}),
        )


@dataclass
class AlertFingerprint:
    """Alert fingerprint for deduplication."""

    fingerprint: str
    fields_used: list[str]
    rule_applied: str | None = None
    raw_values: dict[str, str] = field(default_factory=dict)

    @staticmethod
    def compute(
        alert: RawAlert,
        fields: list[str] | None = None,
        rule_name: str | None = None,
    ) -> AlertFingerprint:
        """Compute fingerprint for an alert."""
        if fields is None:
            fields = ["source", "alert_name", "service", "environment"]

        values: dict[str, str] = {}
        for fld in sorted(fields):
            val = getattr(alert, fld, None)
            if val is None:
                val = alert.labels.get(fld, "")
            if isinstance(val, Enum):
                val = val.value
            values[fld] = str(val) if val else ""

        fingerprint_parts = [f"{k}={v}" for k, v in sorted(values.items())]
        fingerprint_string = "|".join(fingerprint_parts)
        fp_hash = hashlib.sha256(fingerprint_string.encode()).hexdigest()[:16]

        return AlertFingerprint(
            fingerprint=fp_hash,
            fields_used=fields,
            rule_applied=rule_name,
            raw_values=values,
        )


@dataclass
class FingerprintRule:
    """Custom fingerprint rule for specific alert types."""

    rule_id: UUID = field(default_factory=uuid4)
    name: str = ""
    description: str = ""
    match_criteria: dict[str, Any] = field(default_factory=dict)
    fingerprint_fields: list[str] = field(default_factory=list)
    priority: int = 0
    enabled: bool = True
    created_at: datetime = field(default_factory=datetime.utcnow)
    created_by: str | None = None

    def matches(self, alert: RawAlert) -> bool:
        """Check if this rule matches an alert."""
        for key, value in self.match_criteria.items():
            alert_value = getattr(alert, key, None)
            if alert_value is None:
                alert_value = alert.labels.get(key)

            if isinstance(alert_value, Enum):
                alert_value = alert_value.value

            if isinstance(value, str):
                if value.startswith("contains:"):
                    if value[9:] not in str(alert_value or ""):
                        return False
                elif value.startswith("regex:"):
                    import re

                    if not re.match(value[6:], str(alert_value or "")):
                        return False
                elif str(alert_value) != value:
                    return False
            elif alert_value != value:
                return False

        return True


@dataclass
class DeduplicationDecision:
    """Result of deduplication processing."""

    raw_alert_id: UUID
    decision: Decision
    incident_id: UUID | None = None
    reason: str = ""
    confidence: float = 1.0
    layers_applied: list[str] = field(default_factory=list)
    processing_time_ms: float = 0.0
    fingerprint: str | None = None
    semantic_score: float | None = None
    temporal_window: str | None = None
    cascade_root: str | None = None
    cluster_label: str | None = None
    suppression_rule: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "raw_alert_id": str(self.raw_alert_id),
            "decision": self.decision.value,
            "incident_id": str(self.incident_id) if self.incident_id else None,
            "reason": self.reason,
            "confidence": self.confidence,
            "layers_applied": self.layers_applied,
            "processing_time_ms": self.processing_time_ms,
            "fingerprint": self.fingerprint,
            "semantic_score": self.semantic_score,
            "temporal_window": self.temporal_window,
            "cascade_root": self.cascade_root,
            "cluster_label": self.cluster_label,
            "suppression_rule": self.suppression_rule,
        }


@dataclass
class SemanticMatch:
    """Semantic similarity match result."""

    alert_id: UUID
    incident_id: UUID
    similarity_score: float
    method: str
    matched_entities: dict[str, str] = field(default_factory=dict)
    embedding_distance: float | None = None


@dataclass
class TemporalWindow:
    """Temporal correlation window."""

    window_type: str
    start_time: datetime
    end_time: datetime
    confidence: WindowConfidence
    max_delay_seconds: int = 30
    alerts_in_window: list[UUID] = field(default_factory=list)


@dataclass
class CorrelationCluster:
    """ML-detected alert cluster."""

    cluster_id: UUID = field(default_factory=uuid4)
    alerts: list[UUID] = field(default_factory=list)
    cluster_label: str = ""
    root_cause_alert_id: UUID | None = None
    confidence: float = 0.0
    features_used: list[str] = field(default_factory=list)
    centroid: list[float] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.utcnow)
    incident_id: UUID | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "cluster_id": str(self.cluster_id),
            "alerts": [str(a) for a in self.alerts],
            "cluster_label": self.cluster_label,
            "root_cause_alert_id": str(self.root_cause_alert_id)
            if self.root_cause_alert_id
            else None,
            "confidence": self.confidence,
            "features_used": self.features_used,
            "created_at": self.created_at.isoformat(),
            "incident_id": str(self.incident_id) if self.incident_id else None,
        }


@dataclass
class ServiceDependency:
    """Service dependency relationship."""

    dependency_id: UUID = field(default_factory=uuid4)
    upstream_service: str = ""
    downstream_service: str = ""
    dependency_type: DependencyType = DependencyType.CALLS
    confidence: float = 1.0
    source: str = "manual"
    latency_p99_ms: float | None = None
    request_rate_per_min: float | None = None
    error_rate: float | None = None
    last_verified: datetime = field(default_factory=datetime.utcnow)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "dependency_id": str(self.dependency_id),
            "upstream_service": self.upstream_service,
            "downstream_service": self.downstream_service,
            "dependency_type": self.dependency_type.value,
            "confidence": self.confidence,
            "source": self.source,
            "latency_p99_ms": self.latency_p99_ms,
            "request_rate_per_min": self.request_rate_per_min,
            "error_rate": self.error_rate,
            "last_verified": self.last_verified.isoformat(),
        }


@dataclass
class CascadeChain:
    """Detected cascade chain of alerts."""

    chain_id: UUID = field(default_factory=uuid4)
    root_alert_id: UUID | None = None
    root_service: str = ""
    cascade_alerts: list[UUID] = field(default_factory=list)
    cascade_services: list[str] = field(default_factory=list)
    detection_confidence: float = 0.0
    cascade_delay_seconds: float = 0.0
    detected_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "chain_id": str(self.chain_id),
            "root_alert_id": str(self.root_alert_id) if self.root_alert_id else None,
            "root_service": self.root_service,
            "cascade_alerts": [str(a) for a in self.cascade_alerts],
            "cascade_services": self.cascade_services,
            "detection_confidence": self.detection_confidence,
            "cascade_delay_seconds": self.cascade_delay_seconds,
            "detected_at": self.detected_at.isoformat(),
        }


@dataclass
class SuppressionRule:
    """Alert suppression rule."""

    rule_id: UUID = field(default_factory=uuid4)
    name: str = ""
    description: str = ""
    match_criteria: dict[str, Any] = field(default_factory=dict)
    suppression_type: SuppressionType = SuppressionType.BUSINESS_RULE
    duration: timedelta | None = None
    starts_at: datetime | None = None
    expires_at: datetime | None = None
    created_by: str | None = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    alerts_suppressed: int = 0
    enabled: bool = True
    linked_jira: str | None = None
    linked_incident_id: UUID | None = None

    def is_active(self, at_time: datetime | None = None) -> bool:
        """Check if rule is currently active."""
        if not self.enabled:
            return False

        now = at_time or datetime.utcnow()

        if self.starts_at and now < self.starts_at:
            return False

        if self.expires_at and now > self.expires_at:
            return False

        return True

    def matches(self, alert: RawAlert) -> bool:
        """Check if this rule matches an alert."""
        for key, value in self.match_criteria.items():
            alert_value = getattr(alert, key, None)
            if alert_value is None:
                alert_value = alert.labels.get(key)

            if isinstance(alert_value, Enum):
                alert_value = alert_value.value

            if isinstance(value, list):
                if alert_value not in value:
                    return False
            elif isinstance(value, str) and value.startswith("*"):
                if not str(alert_value or "").endswith(value[1:]):
                    return False
            elif isinstance(value, str) and value.endswith("*"):
                if not str(alert_value or "").startswith(value[:-1]):
                    return False
            elif alert_value != value:
                return False

        return True

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "rule_id": str(self.rule_id),
            "name": self.name,
            "description": self.description,
            "match_criteria": self.match_criteria,
            "suppression_type": self.suppression_type.value,
            "duration_seconds": self.duration.total_seconds() if self.duration else None,
            "starts_at": self.starts_at.isoformat() if self.starts_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat(),
            "alerts_suppressed": self.alerts_suppressed,
            "enabled": self.enabled,
            "linked_jira": self.linked_jira,
        }


@dataclass
class MaintenanceWindow:
    """Scheduled maintenance window."""

    window_id: UUID = field(default_factory=uuid4)
    name: str = ""
    description: str = ""
    services: list[str] = field(default_factory=list)
    regions: list[str] = field(default_factory=list)
    starts_at: datetime = field(default_factory=datetime.utcnow)
    ends_at: datetime = field(default_factory=datetime.utcnow)
    created_by: str | None = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    alerts_suppressed: int = 0
    is_recurring: bool = False
    recurrence_pattern: str | None = None
    ended_early: bool = False
    extended_until: datetime | None = None

    def is_active(self, at_time: datetime | None = None) -> bool:
        """Check if window is currently active."""
        now = at_time or datetime.utcnow()
        end_time = self.extended_until or self.ends_at

        if self.ended_early:
            return False

        return self.starts_at <= now <= end_time

    def matches_service(self, service: str) -> bool:
        """Check if service is covered by this window."""
        if not self.services:
            return True

        for svc in self.services:
            if svc.endswith("*") and service.startswith(svc[:-1]):
                return True
            if svc == service:
                return True

        return False

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "window_id": str(self.window_id),
            "name": self.name,
            "description": self.description,
            "services": self.services,
            "regions": self.regions,
            "starts_at": self.starts_at.isoformat(),
            "ends_at": self.ends_at.isoformat(),
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat(),
            "alerts_suppressed": self.alerts_suppressed,
            "is_recurring": self.is_recurring,
            "recurrence_pattern": self.recurrence_pattern,
            "ended_early": self.ended_early,
            "extended_until": self.extended_until.isoformat()
            if self.extended_until
            else None,
        }


@dataclass
class KnownIssue:
    """Known issue for alert suppression."""

    issue_id: UUID = field(default_factory=uuid4)
    title: str = ""
    description: str = ""
    alert_pattern: dict[str, Any] = field(default_factory=dict)
    jira_ticket: str | None = None
    vendor_status_url: str | None = None
    created_by: str | None = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    expires_at: datetime | None = None
    auto_resolve_on_vendor: bool = False
    alerts_suppressed: int = 0
    acknowledged_by: list[str] = field(default_factory=list)

    def is_active(self, at_time: datetime | None = None) -> bool:
        """Check if known issue is still active."""
        if self.expires_at:
            now = at_time or datetime.utcnow()
            return now < self.expires_at
        return True

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "issue_id": str(self.issue_id),
            "title": self.title,
            "description": self.description,
            "alert_pattern": self.alert_pattern,
            "jira_ticket": self.jira_ticket,
            "vendor_status_url": self.vendor_status_url,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "alerts_suppressed": self.alerts_suppressed,
        }


@dataclass
class FlappingAlert:
    """Flapping alert detection state."""

    alert_fingerprint: str
    service: str = ""
    alert_name: str = ""
    state_changes: list[tuple[datetime, str]] = field(default_factory=list)
    first_seen: datetime = field(default_factory=datetime.utcnow)
    last_seen: datetime = field(default_factory=datetime.utcnow)
    is_flapping: bool = False
    flapping_score: float = 0.0
    suppressed_until: datetime | None = None
    suggested_threshold_adjustment: float | None = None
    pattern_description: str = ""

    def add_state_change(self, timestamp: datetime, state: str) -> None:
        """Record a state change."""
        self.state_changes.append((timestamp, state))
        self.last_seen = timestamp
        self._recalculate_flapping()

    def _recalculate_flapping(
        self,
        window_minutes: int = 30,
        min_changes: int = 3,
    ) -> None:
        """Recalculate flapping status."""
        cutoff = datetime.utcnow() - timedelta(minutes=window_minutes)
        recent_changes = [
            (t, s) for t, s in self.state_changes if t > cutoff
        ]

        self.flapping_score = len(recent_changes) / (window_minutes / 5)
        self.is_flapping = len(recent_changes) >= min_changes

        if self.is_flapping and len(recent_changes) >= 2:
            self.pattern_description = f"Alert changed state {len(recent_changes)} times in {window_minutes} minutes"

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "alert_fingerprint": self.alert_fingerprint,
            "service": self.service,
            "alert_name": self.alert_name,
            "state_change_count": len(self.state_changes),
            "first_seen": self.first_seen.isoformat(),
            "last_seen": self.last_seen.isoformat(),
            "is_flapping": self.is_flapping,
            "flapping_score": self.flapping_score,
            "suppressed_until": self.suppressed_until.isoformat()
            if self.suppressed_until
            else None,
            "pattern_description": self.pattern_description,
        }


@dataclass
class AlertQualityScore:
    """Quality score for an alert type."""

    alert_fingerprint: str
    alert_name: str = ""
    service: str = ""
    overall_score: float = 0.0
    actionability_score: float = 0.0
    signal_to_noise_ratio: float = 0.0
    time_to_action_avg_seconds: float = 0.0
    flapping_rate: float = 0.0
    correlation_rate: float = 0.0
    total_fires: int = 0
    total_actioned: int = 0
    total_ignored: int = 0
    total_auto_resolved: int = 0
    last_calculated: datetime = field(default_factory=datetime.utcnow)
    recommendations: list[str] = field(default_factory=list)

    def calculate_overall(self) -> float:
        """Calculate overall quality score."""
        weights = {
            "actionability": 0.40,
            "signal_noise": 0.25,
            "time_to_action": 0.15,
            "flapping": 0.10,
            "correlation": 0.10,
        }

        snr_normalized = min(self.signal_to_noise_ratio, 1.0)
        tta_normalized = max(0, 1 - (self.time_to_action_avg_seconds / 600))
        flap_normalized = 1 - min(self.flapping_rate, 1.0)
        corr_normalized = 1 - min(self.correlation_rate, 1.0)

        self.overall_score = (
            weights["actionability"] * self.actionability_score
            + weights["signal_noise"] * snr_normalized
            + weights["time_to_action"] * tta_normalized
            + weights["flapping"] * flap_normalized
            + weights["correlation"] * corr_normalized
        ) * 100

        return self.overall_score

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "alert_fingerprint": self.alert_fingerprint,
            "alert_name": self.alert_name,
            "service": self.service,
            "overall_score": self.overall_score,
            "actionability_score": self.actionability_score,
            "signal_to_noise_ratio": self.signal_to_noise_ratio,
            "time_to_action_avg_seconds": self.time_to_action_avg_seconds,
            "flapping_rate": self.flapping_rate,
            "correlation_rate": self.correlation_rate,
            "total_fires": self.total_fires,
            "total_actioned": self.total_actioned,
            "total_ignored": self.total_ignored,
            "recommendations": self.recommendations,
        }


@dataclass
class FatigueMetrics:
    """Alert fatigue metrics for a team or period."""

    period_start: datetime = field(default_factory=datetime.utcnow)
    period_end: datetime = field(default_factory=datetime.utcnow)
    team: str | None = None
    alerts_per_oncall_day: float = 0.0
    pages_per_oncall_day: float = 0.0
    acknowledged_within_5min_pct: float = 0.0
    ignored_dismissed_pct: float = 0.0
    mean_time_to_acknowledge_seconds: float = 0.0
    night_pages_pct: float = 0.0
    weekend_pages_pct: float = 0.0
    fatigue_score: float = 0.0
    trend: str = "stable"
    total_alerts: int = 0
    total_pages: int = 0
    total_acknowledged: int = 0
    total_ignored: int = 0

    def calculate_fatigue_score(self) -> float:
        """Calculate overall fatigue score (lower is better)."""
        score = 0.0
        score += min(self.alerts_per_oncall_day / 50, 1.0) * 20
        score += min(self.pages_per_oncall_day / 10, 1.0) * 25
        score += (1 - self.acknowledged_within_5min_pct) * 15
        score += self.ignored_dismissed_pct * 20
        score += min(self.night_pages_pct * 2, 1.0) * 10
        score += min(self.weekend_pages_pct * 2, 1.0) * 10

        self.fatigue_score = score
        return score

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "team": self.team,
            "alerts_per_oncall_day": self.alerts_per_oncall_day,
            "pages_per_oncall_day": self.pages_per_oncall_day,
            "acknowledged_within_5min_pct": self.acknowledged_within_5min_pct,
            "ignored_dismissed_pct": self.ignored_dismissed_pct,
            "mean_time_to_acknowledge_seconds": self.mean_time_to_acknowledge_seconds,
            "night_pages_pct": self.night_pages_pct,
            "weekend_pages_pct": self.weekend_pages_pct,
            "fatigue_score": self.fatigue_score,
            "trend": self.trend,
            "total_alerts": self.total_alerts,
            "total_pages": self.total_pages,
        }


@dataclass
class IncidentGroup:
    """Group of related alerts forming an incident."""

    incident_id: UUID = field(default_factory=uuid4)
    title: str = ""
    alerts: list[UUID] = field(default_factory=list)
    root_cause_alert_id: UUID | None = None
    root_cause_service: str | None = None
    severity: AlertSeverity = AlertSeverity.MEDIUM
    status: str = "active"
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    resolved_at: datetime | None = None
    grouping_reason: str = ""
    grouping_confidence: float = 0.0
    ai_analysis: str = ""
    cascade_chain_id: UUID | None = None
    cluster_id: UUID | None = None
    merged_from: list[UUID] = field(default_factory=list)
    split_from: UUID | None = None

    def add_alert(self, alert_id: UUID, reason: str = "") -> None:
        """Add alert to group."""
        if alert_id not in self.alerts:
            self.alerts.append(alert_id)
            self.updated_at = datetime.utcnow()
            if reason:
                self.grouping_reason = reason

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "incident_id": str(self.incident_id),
            "title": self.title,
            "alerts": [str(a) for a in self.alerts],
            "alert_count": len(self.alerts),
            "root_cause_alert_id": str(self.root_cause_alert_id)
            if self.root_cause_alert_id
            else None,
            "root_cause_service": self.root_cause_service,
            "severity": self.severity.value,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
            "grouping_reason": self.grouping_reason,
            "grouping_confidence": self.grouping_confidence,
            "ai_analysis": self.ai_analysis,
        }
