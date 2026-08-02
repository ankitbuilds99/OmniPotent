"""Blast Radius Analysis - Calculate impact of service failures."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from enum import Enum

from app.dependency_graph.graph import DependencyGraph, ServiceNode

log = logging.getLogger("omnipotent.dependency_graph.blast_radius")


class ImpactLevel(str, Enum):
    """Impact levels for blast radius."""
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class ImpactedService:
    """A service impacted by a failure."""
    service_id: str
    name: str
    impact_type: str  # direct, transitive
    distance: int  # hops from failing service
    impact_path: list[str]  # path from failing service
    estimated_user_impact: float  # 0-1 scale
    failure_modes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "service_id": self.service_id,
            "name": self.name,
            "impact_type": self.impact_type,
            "distance": self.distance,
            "impact_path": self.impact_path,
            "estimated_user_impact": self.estimated_user_impact,
            "failure_modes": self.failure_modes,
        }


@dataclass
class BlastRadiusResult:
    """Result of blast radius analysis."""
    failing_service: str
    impact_level: ImpactLevel
    total_impacted: int
    direct_impacted: int
    transitive_impacted: int
    impacted_services: list[ImpactedService]
    user_facing_impact: float  # 0-1 scale
    estimated_revenue_impact: float | None  # Optional $/hour
    mitigation_suggestions: list[str]
    analyzed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "failing_service": self.failing_service,
            "impact_level": self.impact_level.value,
            "total_impacted": self.total_impacted,
            "direct_impacted": self.direct_impacted,
            "transitive_impacted": self.transitive_impacted,
            "impacted_services": [s.to_dict() for s in self.impacted_services],
            "user_facing_impact": self.user_facing_impact,
            "estimated_revenue_impact": self.estimated_revenue_impact,
            "mitigation_suggestions": self.mitigation_suggestions,
            "analyzed_at": self.analyzed_at.isoformat(),
        }


class BlastRadiusAnalyzer:
    """Analyzes blast radius of service failures.

    Features:
    - Calculate direct and transitive impact
    - Estimate user-facing impact
    - Suggest mitigations
    - Identify critical paths
    """

    # Service importance weights
    SERVICE_WEIGHTS = {
        "api-gateway": 1.0,
        "auth": 0.95,
        "payment": 0.9,
        "checkout": 0.9,
        "user": 0.7,
        "notification": 0.3,
        "analytics": 0.2,
    }

    # Failure modes by dependency type
    FAILURE_MODES = {
        "sync_http": ["timeout", "5xx_errors", "connection_refused"],
        "async_http": ["delayed_processing", "queue_buildup"],
        "grpc": ["connection_failed", "deadline_exceeded"],
        "database": ["connection_exhausted", "query_timeout", "data_unavailable"],
        "cache": ["cache_miss_spike", "fallback_to_db"],
        "queue": ["message_backup", "processing_delay"],
    }

    def __init__(self, graph: DependencyGraph):
        """Initialize the analyzer.

        Args:
            graph: Dependency graph to analyze
        """
        self.graph = graph

    def analyze(
        self,
        failing_service: str,
        failure_type: str = "complete",
    ) -> BlastRadiusResult:
        """Analyze blast radius of a service failure.

        Args:
            failing_service: Service that is failing
            failure_type: Type of failure (complete, degraded, latency)

        Returns:
            BlastRadiusResult with impact analysis
        """
        log.info("Analyzing blast radius for: %s", failing_service)

        # Get all impacted services
        impacted_services = self._calculate_impact(failing_service, failure_type)

        # Count direct vs transitive
        direct = [s for s in impacted_services if s.impact_type == "direct"]
        transitive = [s for s in impacted_services if s.impact_type == "transitive"]

        # Calculate user-facing impact
        user_impact = self._calculate_user_impact(impacted_services, failing_service)

        # Determine impact level
        impact_level = self._determine_impact_level(
            len(impacted_services), user_impact
        )

        # Generate mitigations
        mitigations = self._generate_mitigations(
            failing_service, impacted_services, failure_type
        )

        return BlastRadiusResult(
            failing_service=failing_service,
            impact_level=impact_level,
            total_impacted=len(impacted_services),
            direct_impacted=len(direct),
            transitive_impacted=len(transitive),
            impacted_services=impacted_services,
            user_facing_impact=user_impact,
            estimated_revenue_impact=None,  # Would need business metrics
            mitigation_suggestions=mitigations,
        )

    def _calculate_impact(
        self,
        failing_service: str,
        failure_type: str,
    ) -> list[ImpactedService]:
        """Calculate all impacted services."""
        impacted: list[ImpactedService] = []
        visited: set[str] = {failing_service}

        # BFS to find all dependents
        queue: list[tuple[str, int, list[str]]] = []

        # Add direct dependents
        dependents = self.graph.get_dependents(failing_service)
        for dep in dependents:
            queue.append((dep.id, 1, [failing_service, dep.id]))

        while queue:
            service_id, distance, path = queue.pop(0)

            if service_id in visited:
                continue
            visited.add(service_id)

            node = self.graph.get_node(service_id)
            if not node:
                continue

            # Get failure modes based on edge type
            edge = self.graph.get_edge(path[-2], service_id) if len(path) >= 2 else None
            failure_modes = []
            if edge:
                dep_type = edge.dependency_type.value
                failure_modes = self.FAILURE_MODES.get(dep_type, ["degraded"])

            # Calculate estimated user impact
            user_impact = self._estimate_service_user_impact(node)

            impacted_service = ImpactedService(
                service_id=service_id,
                name=node.name,
                impact_type="direct" if distance == 1 else "transitive",
                distance=distance,
                impact_path=path,
                estimated_user_impact=user_impact,
                failure_modes=failure_modes,
            )
            impacted.append(impacted_service)

            # Add this service's dependents
            next_dependents = self.graph.get_dependents(service_id)
            for next_dep in next_dependents:
                if next_dep.id not in visited:
                    queue.append((next_dep.id, distance + 1, path + [next_dep.id]))

        return impacted

    def _estimate_service_user_impact(self, node: ServiceNode) -> float:
        """Estimate user-facing impact of a service."""
        # Check explicit weight
        for name_pattern, weight in self.SERVICE_WEIGHTS.items():
            if name_pattern in node.name.lower():
                return weight

        # Use criticality score
        return node.criticality_score

    def _calculate_user_impact(
        self,
        impacted_services: list[ImpactedService],
        failing_service: str,
    ) -> float:
        """Calculate overall user-facing impact."""
        if not impacted_services:
            # Check if failing service itself is user-facing
            node = self.graph.get_node(failing_service)
            if node:
                return self._estimate_service_user_impact(node)
            return 0.0

        # Aggregate impact from all impacted services
        # Weight by distance (closer = more impact)
        total_impact = 0.0
        for service in impacted_services:
            distance_weight = 1.0 / (service.distance + 1)
            total_impact += service.estimated_user_impact * distance_weight

        return min(1.0, total_impact)

    def _determine_impact_level(
        self,
        total_impacted: int,
        user_impact: float,
    ) -> ImpactLevel:
        """Determine impact level based on metrics."""
        if user_impact >= 0.8 or total_impacted >= 10:
            return ImpactLevel.CRITICAL
        if user_impact >= 0.5 or total_impacted >= 5:
            return ImpactLevel.HIGH
        if user_impact >= 0.2 or total_impacted >= 2:
            return ImpactLevel.MEDIUM
        if total_impacted >= 1:
            return ImpactLevel.LOW
        return ImpactLevel.NONE

    def _generate_mitigations(
        self,
        failing_service: str,
        impacted_services: list[ImpactedService],
        failure_type: str,
    ) -> list[str]:
        """Generate mitigation suggestions."""
        mitigations = []

        node = self.graph.get_node(failing_service)

        # Check for circuit breakers
        if len(impacted_services) >= 3:
            mitigations.append(
                f"Enable circuit breakers on services calling {failing_service}"
            )

        # Check for fallbacks
        if node and "database" in node.service_type.lower():
            mitigations.append(
                "Consider read replicas or cache-based fallbacks"
            )

        if node and "cache" in node.service_type.lower():
            mitigations.append(
                "Services should handle cache misses gracefully"
            )

        # Retry/timeout suggestions
        direct = [s for s in impacted_services if s.impact_type == "direct"]
        if direct:
            mitigations.append(
                f"Review timeout configurations for: {', '.join(s.name for s in direct[:3])}"
            )

        # Generic suggestions based on failure type
        if failure_type == "complete":
            mitigations.append(
                f"Consider adding redundancy/HA for {failing_service}"
            )
        elif failure_type == "latency":
            mitigations.append(
                "Implement async patterns where possible to reduce latency coupling"
            )

        # If no specific mitigations, add general ones
        if not mitigations:
            mitigations.append(
                "Implement health checks and auto-recovery"
            )

        return mitigations

    def find_single_points_of_failure(self) -> list[dict[str, Any]]:
        """Find services that are single points of failure.

        These are services where:
        - Many services depend on them
        - No redundancy exists
        - High user impact if they fail
        """
        spofs = []

        for node in self.graph.get_all_nodes():
            dependents = self.graph.get_dependents(node.id)

            if len(dependents) >= 3:  # Multiple services depend on this
                # Analyze blast radius
                result = self.analyze(node.id)

                if result.impact_level in (ImpactLevel.HIGH, ImpactLevel.CRITICAL):
                    spofs.append({
                        "service": node.name,
                        "dependents": len(dependents),
                        "total_impact": result.total_impacted,
                        "user_impact": result.user_facing_impact,
                        "impact_level": result.impact_level.value,
                        "mitigations": result.mitigation_suggestions,
                    })

        # Sort by impact
        spofs.sort(key=lambda x: x["total_impact"], reverse=True)

        return spofs

    def simulate_cascade_failure(
        self,
        initial_failures: list[str],
    ) -> dict[str, Any]:
        """Simulate cascade failure from multiple initial failures.

        Args:
            initial_failures: List of initially failing services

        Returns:
            Cascade analysis result
        """
        all_impacted: set[str] = set()
        cascade_waves: list[dict[str, Any]] = []

        current_failures = set(initial_failures)

        wave = 0
        while current_failures and wave < 10:  # Max 10 waves
            wave += 1
            wave_impacted: set[str] = set()

            for failing in current_failures:
                result = self.analyze(failing)
                for service in result.impacted_services:
                    if service.service_id not in all_impacted:
                        wave_impacted.add(service.service_id)

            cascade_waves.append({
                "wave": wave,
                "new_failures": list(current_failures),
                "impacted": list(wave_impacted),
            })

            all_impacted.update(wave_impacted)
            current_failures = wave_impacted

        return {
            "initial_failures": initial_failures,
            "total_waves": wave,
            "total_impacted": len(all_impacted),
            "cascade_waves": cascade_waves,
            "final_state": {
                "healthy": len(self.graph.get_all_nodes()) - len(all_impacted) - len(initial_failures),
                "impacted": len(all_impacted),
                "failed": len(initial_failures),
            },
        }


def create_analyzer(graph: DependencyGraph) -> BlastRadiusAnalyzer:
    """Factory function to create a blast radius analyzer."""
    return BlastRadiusAnalyzer(graph)
