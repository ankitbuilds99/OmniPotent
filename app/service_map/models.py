"""Models for service dependency mapping.

Defines service nodes, connections, health status, and impact analysis.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("devcopilot.service_map.models")


class HealthStatus(Enum):
    """Service health status."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    FAILING = "failing"
    UNKNOWN = "unknown"


class ConnectionType(Enum):
    """Type of service connection."""

    SYNC = "sync"
    ASYNC = "async"
    DATABASE = "database"
    CACHE = "cache"
    QUEUE = "queue"


class ImpactLevel(Enum):
    """Impact level for changes or failures."""

    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class ServiceHealth:
    """Health status of a service."""

    status: HealthStatus
    error_rate: float
    latency_p99_ms: float
    throughput_rps: float
    cpu_percent: float = 0.0
    memory_percent: float = 0.0
    last_updated: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "error_rate": round(self.error_rate, 2),
            "latency_p99_ms": round(self.latency_p99_ms, 1),
            "throughput_rps": round(self.throughput_rps, 1),
            "cpu_percent": round(self.cpu_percent, 1),
            "memory_percent": round(self.memory_percent, 1),
            "last_updated": self.last_updated.isoformat(),
        }


@dataclass
class TeamOwnership:
    """Team ownership information."""

    team_name: str
    team_lead: str
    slack_channel: str
    oncall_schedule: str = ""
    escalation_policy: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "team_name": self.team_name,
            "team_lead": self.team_lead,
            "slack_channel": self.slack_channel,
            "oncall_schedule": self.oncall_schedule,
            "escalation_policy": self.escalation_policy,
        }


@dataclass
class SLOStatus:
    """SLO status for a service."""

    availability_target: float
    availability_current: float
    latency_target_ms: float
    latency_current_ms: float
    error_budget_remaining: float
    burn_rate: float

    @property
    def meeting_slo(self) -> bool:
        return (
            self.availability_current >= self.availability_target
            and self.latency_current_ms <= self.latency_target_ms
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "availability_target": self.availability_target,
            "availability_current": round(self.availability_current, 4),
            "latency_target_ms": self.latency_target_ms,
            "latency_current_ms": round(self.latency_current_ms, 1),
            "error_budget_remaining": round(self.error_budget_remaining, 2),
            "burn_rate": round(self.burn_rate, 2),
            "meeting_slo": self.meeting_slo,
        }


@dataclass
class ServiceNode:
    """A node in the service dependency graph."""

    service_id: str
    name: str
    tier: str
    health: ServiceHealth
    ownership: TeamOwnership | None = None
    slo: SLOStatus | None = None
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "service_id": self.service_id,
            "name": self.name,
            "tier": self.tier,
            "health": self.health.to_dict(),
            "ownership": self.ownership.to_dict() if self.ownership else None,
            "slo": self.slo.to_dict() if self.slo else None,
            "tags": self.tags,
        }


@dataclass
class ServiceConnection:
    """A connection between two services."""

    source: str
    target: str
    connection_type: ConnectionType
    is_critical: bool = False
    traffic_percent: float = 0.0
    avg_latency_ms: float = 0.0
    error_rate: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "connection_type": self.connection_type.value,
            "is_critical": self.is_critical,
            "traffic_percent": round(self.traffic_percent, 1),
            "avg_latency_ms": round(self.avg_latency_ms, 1),
            "error_rate": round(self.error_rate, 2),
        }


@dataclass
class ImpactedService:
    """A service impacted by an incident or change."""

    service_id: str
    service_name: str
    impact_level: ImpactLevel
    impact_reason: str
    estimated_impact_percent: float
    is_direct_dependency: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "service_id": self.service_id,
            "service_name": self.service_name,
            "impact_level": self.impact_level.value,
            "impact_reason": self.impact_reason,
            "estimated_impact_percent": round(self.estimated_impact_percent, 1),
            "is_direct_dependency": self.is_direct_dependency,
        }


@dataclass
class BlastRadius:
    """Blast radius analysis for an incident."""

    source_service: str
    impacted_services: list[ImpactedService]
    total_services_affected: int
    users_affected_estimate: int
    revenue_at_risk_per_hour: float
    cascade_depth: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_service": self.source_service,
            "impacted_services": [s.to_dict() for s in self.impacted_services],
            "total_services_affected": self.total_services_affected,
            "users_affected_estimate": self.users_affected_estimate,
            "revenue_at_risk_per_hour": round(self.revenue_at_risk_per_hour, 2),
            "cascade_depth": self.cascade_depth,
        }


@dataclass
class ChangeImpact:
    """Impact analysis for a proposed change."""

    change_description: str
    target_service: str
    impacted_services: list[ImpactedService]
    rollback_complexity: str
    recommended_deploy_strategy: str
    risk_level: ImpactLevel
    recommendations: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "change_description": self.change_description,
            "target_service": self.target_service,
            "impacted_services": [s.to_dict() for s in self.impacted_services],
            "rollback_complexity": self.rollback_complexity,
            "recommended_deploy_strategy": self.recommended_deploy_strategy,
            "risk_level": self.risk_level.value,
            "recommendations": self.recommendations,
        }


@dataclass
class FailureScenario:
    """A simulated failure scenario."""

    failed_service: str
    failure_mode: str
    impacted_services: list[ImpactedService]
    total_impact_percent: float
    revenue_impact_per_hour: float
    mitigation_recommendations: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "failed_service": self.failed_service,
            "failure_mode": self.failure_mode,
            "impacted_services": [s.to_dict() for s in self.impacted_services],
            "total_impact_percent": round(self.total_impact_percent, 1),
            "revenue_impact_per_hour": round(self.revenue_impact_per_hour, 2),
            "mitigation_recommendations": self.mitigation_recommendations,
        }


@dataclass
class FailureSimulation:
    """Results of a failure simulation."""

    simulation_id: str
    scenarios: list[FailureScenario]
    most_critical_services: list[str]
    resilience_score: float
    recommendations: list[str]
    simulated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "simulation_id": self.simulation_id,
            "scenarios": [s.to_dict() for s in self.scenarios],
            "most_critical_services": self.most_critical_services,
            "resilience_score": round(self.resilience_score, 2),
            "recommendations": self.recommendations,
            "simulated_at": self.simulated_at.isoformat(),
        }
