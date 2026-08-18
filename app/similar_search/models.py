"""Core data models for similar incident search."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from app.config import get_settings


def _default_min_similarity() -> float:
    """The one "similar enough" bar (SIMILAR_INCIDENT_THRESHOLD).

    Used as a dataclass default_factory so it is read when a filter object is
    built, not when this module is imported — an import-time read would freeze
    whatever the environment was at first import.
    """
    return get_settings().similar_incident_threshold


class IncidentSeverity(Enum):
    """Incident severity levels."""

    P1 = "P1"
    P2 = "P2"
    P3 = "P3"
    P4 = "P4"


class IncidentStatus(Enum):
    """Incident status."""

    ACTIVE = "active"
    INVESTIGATING = "investigating"
    IDENTIFIED = "identified"
    MITIGATED = "mitigated"
    RESOLVED = "resolved"
    CLOSED = "closed"


class RootCauseCategory(Enum):
    """Root cause categories for taxonomy matching."""

    CODE_DEFECT = "code_defect"
    CONFIGURATION = "configuration"
    INFRASTRUCTURE = "infrastructure"
    EXTERNAL_DEPENDENCY = "external_dependency"
    CAPACITY = "capacity"
    SECURITY = "security"
    HUMAN_ERROR = "human_error"
    UNKNOWN = "unknown"


class SimilarityDimension(Enum):
    """Dimensions for similarity scoring."""

    SYMPTOMS = "symptoms"
    ERROR_MESSAGES = "error_messages"
    SERVICES = "services"
    ROOT_CAUSE = "root_cause"
    TIME_PATTERN = "time_pattern"
    METRICS_SHAPE = "metrics_shape"
    CODE_CHANGES = "code_changes"


@dataclass
class IncidentRecord:
    """Complete incident record for similarity analysis."""

    incident_id: UUID = field(default_factory=uuid4)
    title: str = ""
    description: str = ""
    severity: IncidentSeverity = IncidentSeverity.P3
    status: IncidentStatus = IncidentStatus.RESOLVED
    services: list[str] = field(default_factory=list)
    alerts: list[str] = field(default_factory=list)
    error_messages: list[str] = field(default_factory=list)
    root_cause_category: RootCauseCategory = RootCauseCategory.UNKNOWN
    root_cause_description: str = ""
    resolution_summary: str = ""
    resolution_steps: list[str] = field(default_factory=list)
    code_changes: list[str] = field(default_factory=list)
    metrics_affected: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.utcnow)
    resolved_at: datetime | None = None
    duration_minutes: int = 0
    customer_impact: str = ""
    action_items: list[dict[str, Any]] = field(default_factory=list)
    postmortem_url: str | None = None
    responders: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "incident_id": str(self.incident_id),
            "title": self.title,
            "description": self.description,
            "severity": self.severity.value,
            "status": self.status.value,
            "services": self.services,
            "alerts": self.alerts,
            "error_messages": self.error_messages,
            "root_cause_category": self.root_cause_category.value,
            "root_cause_description": self.root_cause_description,
            "resolution_summary": self.resolution_summary,
            "resolution_steps": self.resolution_steps,
            "code_changes": self.code_changes,
            "metrics_affected": self.metrics_affected,
            "created_at": self.created_at.isoformat(),
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
            "duration_minutes": self.duration_minutes,
            "customer_impact": self.customer_impact,
            "action_items": self.action_items,
            "postmortem_url": self.postmortem_url,
            "responders": self.responders,
            "tags": self.tags,
        }

    def get_searchable_text(self) -> str:
        """Get combined text for embedding."""
        parts = [
            self.title,
            self.description,
            " ".join(self.services),
            " ".join(self.alerts),
            " ".join(self.error_messages),
            self.root_cause_description,
            self.resolution_summary,
            " ".join(self.resolution_steps),
            self.customer_impact,
        ]
        return " ".join(p for p in parts if p)


@dataclass
class SimilarityScore:
    """Detailed similarity score breakdown."""

    overall_score: float = 0.0
    dimension_scores: dict[SimilarityDimension, float] = field(default_factory=dict)
    dimension_details: dict[SimilarityDimension, str] = field(default_factory=dict)
    confidence: float = 0.0
    match_reasons: list[str] = field(default_factory=list)
    difference_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "overall_score": self.overall_score,
            "dimension_scores": {
                k.value: v for k, v in self.dimension_scores.items()
            },
            "dimension_details": {
                k.value: v for k, v in self.dimension_details.items()
            },
            "confidence": self.confidence,
            "match_reasons": self.match_reasons,
            "difference_reasons": self.difference_reasons,
        }


@dataclass
class SimilarIncident:
    """Similar incident with similarity analysis."""

    incident: IncidentRecord
    similarity: SimilarityScore
    suggested_actions: list[str] = field(default_factory=list)
    resolution_applicable: bool = False
    resolution_confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "incident": self.incident.to_dict(),
            "similarity": self.similarity.to_dict(),
            "suggested_actions": self.suggested_actions,
            "resolution_applicable": self.resolution_applicable,
            "resolution_confidence": self.resolution_confidence,
        }


@dataclass
class SearchFilters:
    """Filters for incident search."""

    time_range_days: int = 365
    min_similarity: float = field(default_factory=_default_min_similarity)
    severities: list[IncidentSeverity] = field(default_factory=list)
    services: list[str] = field(default_factory=list)
    include_dependent_services: bool = True
    root_cause_categories: list[RootCauseCategory] = field(default_factory=list)
    resolved_only: bool = True
    with_postmortem: bool = False
    with_action_items_completed: bool = False
    exclude_incident_ids: list[UUID] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    dimension_weights: dict[SimilarityDimension, str] = field(default_factory=dict)


@dataclass
class SearchResult:
    """Search result with similar incidents."""

    query: str
    current_incident: IncidentRecord | None
    similar_incidents: list[SimilarIncident]
    total_found: int
    search_time_ms: float
    filters_applied: SearchFilters | None
    suggestions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "query": self.query,
            "current_incident_id": str(self.current_incident.incident_id)
            if self.current_incident
            else None,
            "similar_incidents": [s.to_dict() for s in self.similar_incidents],
            "total_found": self.total_found,
            "search_time_ms": self.search_time_ms,
            "suggestions": self.suggestions,
        }


@dataclass
class ResolutionRecommendation:
    """Resolution recommendation from past incidents."""

    recommendation_id: UUID = field(default_factory=uuid4)
    title: str = ""
    description: str = ""
    confidence: float = 0.0
    based_on_incidents: list[UUID] = field(default_factory=list)
    diagnostic_steps: list[str] = field(default_factory=list)
    fix_steps: list[str] = field(default_factory=list)
    code_example: str | None = None
    success_rate: float = 0.0
    avg_resolution_time_minutes: int = 0
    is_temporary_fix: bool = False
    warning: str | None = None
    runbook_url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "recommendation_id": str(self.recommendation_id),
            "title": self.title,
            "description": self.description,
            "confidence": self.confidence,
            "based_on_incidents": [str(i) for i in self.based_on_incidents],
            "diagnostic_steps": self.diagnostic_steps,
            "fix_steps": self.fix_steps,
            "code_example": self.code_example,
            "success_rate": self.success_rate,
            "avg_resolution_time_minutes": self.avg_resolution_time_minutes,
            "is_temporary_fix": self.is_temporary_fix,
            "warning": self.warning,
            "runbook_url": self.runbook_url,
        }


@dataclass
class PatternOccurrence:
    """Single occurrence of a pattern."""

    incident_id: UUID
    occurred_at: datetime
    service: str
    root_cause: str
    duration_minutes: int
    resolved: bool


@dataclass
class RecurringPattern:
    """Detected recurring incident pattern."""

    pattern_id: UUID = field(default_factory=uuid4)
    name: str = ""
    description: str = ""
    occurrences: list[PatternOccurrence] = field(default_factory=list)
    services_affected: list[str] = field(default_factory=list)
    root_causes: dict[str, int] = field(default_factory=dict)
    trend: str = "stable"
    estimated_cost: float = 0.0
    unfulfilled_action_items: list[dict[str, Any]] = field(default_factory=list)
    recommendation: str = ""
    first_seen: datetime = field(default_factory=datetime.utcnow)
    last_seen: datetime = field(default_factory=datetime.utcnow)

    @property
    def occurrence_count(self) -> int:
        """Get total occurrences."""
        return len(self.occurrences)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "pattern_id": str(self.pattern_id),
            "name": self.name,
            "description": self.description,
            "occurrence_count": self.occurrence_count,
            "services_affected": self.services_affected,
            "root_causes": self.root_causes,
            "trend": self.trend,
            "estimated_cost": self.estimated_cost,
            "unfulfilled_action_items": self.unfulfilled_action_items,
            "recommendation": self.recommendation,
            "first_seen": self.first_seen.isoformat(),
            "last_seen": self.last_seen.isoformat(),
        }


@dataclass
class KnowledgeNode:
    """Node in the incident knowledge graph."""

    node_id: str
    node_type: str
    label: str
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass
class KnowledgeEdge:
    """Edge in the incident knowledge graph."""

    source_id: str
    target_id: str
    edge_type: str
    weight: float = 1.0
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass
class FixEffectiveness:
    """Effectiveness tracking for a fix type."""

    fix_type: str
    total_applied: int = 0
    success_count: int = 0
    recurrence_count: int = 0
    avg_time_to_recurrence_days: float | None = None
    is_permanent_fix: bool = True
    recommendation: str = ""

    @property
    def success_rate(self) -> float:
        """Calculate success rate."""
        if self.total_applied == 0:
            return 0.0
        return self.success_count / self.total_applied

    @property
    def recurrence_rate(self) -> float:
        """Calculate recurrence rate."""
        if self.success_count == 0:
            return 0.0
        return self.recurrence_count / self.success_count

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "fix_type": self.fix_type,
            "total_applied": self.total_applied,
            "success_rate": self.success_rate,
            "recurrence_rate": self.recurrence_rate,
            "avg_time_to_recurrence_days": self.avg_time_to_recurrence_days,
            "is_permanent_fix": self.is_permanent_fix,
            "recommendation": self.recommendation,
        }


@dataclass
class RunbookSuggestion:
    """Suggested runbook for incident resolution."""

    runbook_id: str
    title: str
    description: str = ""
    url: str | None = None
    relevance_score: float = 0.0
    used_in_incidents: list[UUID] = field(default_factory=list)
    success_rate: float = 0.0
    avg_resolution_time_minutes: int = 0
    quick_steps: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "runbook_id": self.runbook_id,
            "title": self.title,
            "description": self.description,
            "url": self.url,
            "relevance_score": self.relevance_score,
            "used_in_incidents": [str(i) for i in self.used_in_incidents],
            "success_rate": self.success_rate,
            "avg_resolution_time_minutes": self.avg_resolution_time_minutes,
            "quick_steps": self.quick_steps,
        }


@dataclass
class IncidentEmbedding:
    """Embedding for an incident."""

    incident_id: UUID
    text_embedding: list[float] = field(default_factory=list)
    symptom_embedding: list[float] = field(default_factory=list)
    service_embedding: list[float] = field(default_factory=list)
    error_embedding: list[float] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.utcnow)
    model_version: str = ""


@dataclass
class TimePattern:
    """Time-based pattern for incidents."""

    hour_of_day: int | None = None
    day_of_week: int | None = None
    is_business_hours: bool = True
    is_deploy_window: bool = False
    recurring_schedule: str | None = None

    @classmethod
    def from_datetime(cls, dt: datetime) -> TimePattern:
        """Create pattern from datetime."""
        is_business = 9 <= dt.hour < 18 and dt.weekday() < 5
        is_deploy = dt.weekday() in [3, 4] and 12 <= dt.hour < 18

        return cls(
            hour_of_day=dt.hour,
            day_of_week=dt.weekday(),
            is_business_hours=is_business,
            is_deploy_window=is_deploy,
        )


@dataclass
class ServiceOverlap:
    """Service overlap analysis between incidents."""

    common_services: list[str] = field(default_factory=list)
    only_in_first: list[str] = field(default_factory=list)
    only_in_second: list[str] = field(default_factory=list)
    dependency_related: bool = False
    overlap_score: float = 0.0

    @classmethod
    def calculate(
        cls,
        services1: list[str],
        services2: list[str],
        dependency_graph: dict[str, list[str]] | None = None,
    ) -> ServiceOverlap:
        """Calculate service overlap."""
        set1 = set(services1)
        set2 = set(services2)

        common = set1 & set2
        only1 = set1 - set2
        only2 = set2 - set1

        union = set1 | set2
        overlap_score = len(common) / len(union) if union else 0.0

        dependency_related = False
        if dependency_graph:
            for s1 in set1:
                deps = dependency_graph.get(s1, [])
                if any(d in set2 for d in deps):
                    dependency_related = True
                    break

        return cls(
            common_services=list(common),
            only_in_first=list(only1),
            only_in_second=list(only2),
            dependency_related=dependency_related,
            overlap_score=overlap_score,
        )


@dataclass
class DimensionWeights:
    """Configurable weights for similarity dimensions."""

    symptoms: float = 0.25
    error_messages: float = 0.20
    services: float = 0.15
    root_cause: float = 0.15
    time_pattern: float = 0.10
    metrics_shape: float = 0.10
    code_changes: float = 0.05

    def to_dict(self) -> dict[str, float]:
        """Convert to dictionary."""
        return {
            "symptoms": self.symptoms,
            "error_messages": self.error_messages,
            "services": self.services,
            "root_cause": self.root_cause,
            "time_pattern": self.time_pattern,
            "metrics_shape": self.metrics_shape,
            "code_changes": self.code_changes,
        }

    def normalize(self) -> DimensionWeights:
        """Normalize weights to sum to 1.0."""
        total = (
            self.symptoms
            + self.error_messages
            + self.services
            + self.root_cause
            + self.time_pattern
            + self.metrics_shape
            + self.code_changes
        )
        if total == 0:
            return self

        return DimensionWeights(
            symptoms=self.symptoms / total,
            error_messages=self.error_messages / total,
            services=self.services / total,
            root_cause=self.root_cause / total,
            time_pattern=self.time_pattern / total,
            metrics_shape=self.metrics_shape / total,
            code_changes=self.code_changes / total,
        )
