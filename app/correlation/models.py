"""Models for multi-incident correlation.

Defines alerts, correlations, and impact analysis structures.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("omnipotent.correlation.models")


class AlertSeverity(Enum):
    """Alert severity levels."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class AlertStatus(Enum):
    """Alert status."""

    FIRING = "firing"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"
    SUPPRESSED = "suppressed"


class CorrelationType(Enum):
    """Type of correlation between alerts."""

    TEMPORAL = "temporal"
    CAUSAL = "causal"
    DEPENDENCY = "dependency"
    SEMANTIC = "semantic"
    HISTORICAL = "historical"


@dataclass
class Alert:
    """An alert from monitoring systems."""

    alert_id: str
    name: str
    service: str
    severity: AlertSeverity
    status: AlertStatus
    message: str
    started_at: datetime
    resolved_at: datetime | None = None
    labels: dict[str, str] = field(default_factory=dict)
    annotations: dict[str, str] = field(default_factory=dict)
    source: str = ""
    fingerprint: str = ""

    @property
    def duration_seconds(self) -> float:
        """Get alert duration in seconds."""
        end = self.resolved_at or datetime.now(timezone.utc)
        return (end - self.started_at).total_seconds()

    def to_dict(self) -> dict[str, Any]:
        return {
            "alert_id": self.alert_id,
            "name": self.name,
            "service": self.service,
            "severity": self.severity.value,
            "status": self.status.value,
            "message": self.message,
            "started_at": self.started_at.isoformat(),
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
            "labels": self.labels,
            "source": self.source,
            "duration_seconds": self.duration_seconds,
        }


@dataclass
class CorrelationConfidence:
    """Confidence scores for correlation factors."""

    temporal: float
    dependency: float
    semantic: float
    historical: float
    overall: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "temporal": round(self.temporal, 2),
            "dependency": round(self.dependency, 2),
            "semantic": round(self.semantic, 2),
            "historical": round(self.historical, 2),
            "overall": round(self.overall, 2),
        }


@dataclass
class CorrelationExplanation:
    """Explanation for why alerts are correlated."""

    correlation_type: CorrelationType
    weight: float
    reason: str
    evidence: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.correlation_type.value,
            "weight": round(self.weight, 2),
            "reason": self.reason,
            "evidence": self.evidence,
        }


@dataclass
class AlertGroup:
    """A group of correlated alerts."""

    group_id: str
    alerts: list[Alert]
    primary_alert: Alert
    confidence: CorrelationConfidence
    explanations: list[CorrelationExplanation]
    cascade_order: list[str]

    @property
    def alert_count(self) -> int:
        return len(self.alerts)

    @property
    def services_affected(self) -> list[str]:
        return list(set(a.service for a in self.alerts))

    def to_dict(self) -> dict[str, Any]:
        return {
            "group_id": self.group_id,
            "alerts": [a.to_dict() for a in self.alerts],
            "primary_alert": self.primary_alert.to_dict(),
            "alert_count": self.alert_count,
            "services_affected": self.services_affected,
            "confidence": self.confidence.to_dict(),
            "explanations": [e.to_dict() for e in self.explanations],
            "cascade_order": self.cascade_order,
        }


@dataclass
class ImpactNode:
    """A node in the impact graph."""

    service: str
    status: str
    impact_level: str
    time_offset_seconds: float
    error_rate: float = 0.0
    latency_increase_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "service": self.service,
            "status": self.status,
            "impact_level": self.impact_level,
            "time_offset_seconds": self.time_offset_seconds,
            "error_rate": round(self.error_rate, 2),
            "latency_increase_ms": round(self.latency_increase_ms, 1),
        }


@dataclass
class ImpactEdge:
    """An edge in the impact graph showing cascade path."""

    source: str
    target: str
    relationship: str
    propagation_delay_seconds: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "relationship": self.relationship,
            "propagation_delay_seconds": self.propagation_delay_seconds,
        }


@dataclass
class ImpactGraph:
    """Graph showing how an incident cascaded across services."""

    nodes: list[ImpactNode]
    edges: list[ImpactEdge]
    root_cause_service: str
    total_services_affected: int
    cascade_depth: int
    propagation_timeline: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
            "root_cause_service": self.root_cause_service,
            "total_services_affected": self.total_services_affected,
            "cascade_depth": self.cascade_depth,
            "propagation_timeline": self.propagation_timeline,
        }


@dataclass
class RootCauseAnalysis:
    """Analysis of the upstream root cause."""

    root_cause_service: str
    root_cause_description: str
    confidence: float
    supporting_evidence: list[str]
    related_commit: str = ""
    related_deploy: str = ""
    chain_of_events: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "root_cause_service": self.root_cause_service,
            "root_cause_description": self.root_cause_description,
            "confidence": round(self.confidence, 2),
            "supporting_evidence": self.supporting_evidence,
            "related_commit": self.related_commit,
            "related_deploy": self.related_deploy,
            "chain_of_events": self.chain_of_events,
            "recommendations": self.recommendations,
        }


@dataclass
class CorrelatedIncident:
    """An incident created from correlated alerts."""

    incident_id: str
    title: str
    severity: AlertSeverity
    status: str
    alert_group: AlertGroup
    impact_graph: ImpactGraph
    root_cause_analysis: RootCauseAnalysis
    started_at: datetime
    detected_at: datetime
    resolved_at: datetime | None = None
    noise_reduction_percent: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "incident_id": self.incident_id,
            "title": self.title,
            "severity": self.severity.value,
            "status": self.status,
            "alert_group": self.alert_group.to_dict(),
            "impact_graph": self.impact_graph.to_dict(),
            "root_cause_analysis": self.root_cause_analysis.to_dict(),
            "started_at": self.started_at.isoformat(),
            "detected_at": self.detected_at.isoformat(),
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
            "noise_reduction_percent": round(self.noise_reduction_percent, 1),
        }


@dataclass
class CorrelationResult:
    """Result of correlating multiple alerts."""

    incidents: list[CorrelatedIncident]
    uncorrelated_alerts: list[Alert]
    total_alerts: int
    correlated_alerts: int
    noise_reduction_percent: float
    correlation_accuracy: float
    processing_time_ms: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "incidents": [i.to_dict() for i in self.incidents],
            "uncorrelated_alerts": [a.to_dict() for a in self.uncorrelated_alerts],
            "total_alerts": self.total_alerts,
            "correlated_alerts": self.correlated_alerts,
            "noise_reduction_percent": round(self.noise_reduction_percent, 1),
            "correlation_accuracy": round(self.correlation_accuracy, 2),
            "processing_time_ms": round(self.processing_time_ms, 2),
        }
