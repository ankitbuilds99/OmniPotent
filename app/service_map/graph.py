"""Service dependency graph.

Manages service topology, dependencies, and health state.
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timezone
from typing import Any

from app.service_map.models import (
    BlastRadius,
    ChangeImpact,
    ConnectionType,
    HealthStatus,
    ImpactedService,
    ImpactLevel,
    ServiceConnection,
    ServiceHealth,
    ServiceNode,
    SLOStatus,
    TeamOwnership,
)

log = logging.getLogger("devcopilot.service_map.graph")


class ServiceGraph:
    """Manages the service dependency graph."""

    def __init__(self) -> None:
        self._nodes: dict[str, ServiceNode] = {}
        self._connections: list[ServiceConnection] = []
        self._tier_weights = {
            "tier0": 1.0,
            "tier1": 0.8,
            "tier2": 0.5,
            "tier3": 0.2,
        }

    def add_service(
        self,
        service_id: str,
        name: str,
        tier: str = "tier2",
        tags: list[str] | None = None,
    ) -> ServiceNode:
        """Add a service to the graph."""
        health = ServiceHealth(
            status=HealthStatus.HEALTHY,
            error_rate=0.0,
            latency_p99_ms=50.0,
            throughput_rps=1000.0,
        )

        node = ServiceNode(
            service_id=service_id,
            name=name,
            tier=tier,
            health=health,
            tags=tags or [],
        )

        self._nodes[service_id] = node
        return node

    def add_connection(
        self,
        source: str,
        target: str,
        connection_type: ConnectionType = ConnectionType.SYNC,
        is_critical: bool = False,
    ) -> ServiceConnection | None:
        """Add a connection between services."""
        if source not in self._nodes or target not in self._nodes:
            return None

        connection = ServiceConnection(
            source=source,
            target=target,
            connection_type=connection_type,
            is_critical=is_critical,
        )

        self._connections.append(connection)
        return connection

    def update_health(
        self,
        service_id: str,
        status: HealthStatus | None = None,
        error_rate: float | None = None,
        latency_p99_ms: float | None = None,
        throughput_rps: float | None = None,
    ) -> ServiceNode | None:
        """Update service health status."""
        node = self._nodes.get(service_id)
        if not node:
            return None

        if status is not None:
            node.health.status = status
        if error_rate is not None:
            node.health.error_rate = error_rate
        if latency_p99_ms is not None:
            node.health.latency_p99_ms = latency_p99_ms
        if throughput_rps is not None:
            node.health.throughput_rps = throughput_rps

        node.health.last_updated = datetime.now(timezone.utc)

        log.info(
            "Updated service health",
            extra={
                "service_id": service_id,
                "status": node.health.status.value,
            },
        )

        return node

    def set_ownership(
        self,
        service_id: str,
        team_name: str,
        team_lead: str,
        slack_channel: str,
    ) -> bool:
        """Set team ownership for a service."""
        node = self._nodes.get(service_id)
        if not node:
            return False

        node.ownership = TeamOwnership(
            team_name=team_name,
            team_lead=team_lead,
            slack_channel=slack_channel,
        )
        return True

    def set_slo(
        self,
        service_id: str,
        availability_target: float,
        latency_target_ms: float,
        availability_current: float,
        latency_current_ms: float,
    ) -> bool:
        """Set SLO status for a service."""
        node = self._nodes.get(service_id)
        if not node:
            return False

        error_budget = availability_target - (1 - availability_current)

        node.slo = SLOStatus(
            availability_target=availability_target,
            availability_current=availability_current,
            latency_target_ms=latency_target_ms,
            latency_current_ms=latency_current_ms,
            error_budget_remaining=max(0, error_budget * 100),
            burn_rate=1.0 if error_budget > 0 else 2.0,
        )
        return True

    def get_service(self, service_id: str) -> ServiceNode | None:
        """Get a service by ID."""
        return self._nodes.get(service_id)

    def get_all_services(self) -> list[ServiceNode]:
        """Get all services."""
        return list(self._nodes.values())

    def get_dependencies(self, service_id: str) -> list[ServiceNode]:
        """Get services that a service depends on."""
        deps: list[ServiceNode] = []
        for conn in self._connections:
            if conn.source == service_id:
                target = self._nodes.get(conn.target)
                if target:
                    deps.append(target)
        return deps

    def get_dependents(self, service_id: str) -> list[ServiceNode]:
        """Get services that depend on a service."""
        deps: list[ServiceNode] = []
        for conn in self._connections:
            if conn.target == service_id:
                source = self._nodes.get(conn.source)
                if source:
                    deps.append(source)
        return deps

    def calculate_blast_radius(
        self,
        service_id: str,
    ) -> BlastRadius:
        """Calculate the blast radius if a service fails."""
        impacted: list[ImpactedService] = []
        visited: set[str] = set()

        def traverse(sid: str, depth: int, is_direct: bool) -> None:
            if sid in visited or depth > 10:
                return
            visited.add(sid)

            dependents = self.get_dependents(sid)
            for dep in dependents:
                node = self._nodes.get(dep.service_id)
                if not node:
                    continue

                tier_weight = self._tier_weights.get(node.tier, 0.5)
                impact_percent = tier_weight * 100 * (1.0 / (depth + 1))

                if impact_percent >= 80:
                    level = ImpactLevel.CRITICAL
                elif impact_percent >= 50:
                    level = ImpactLevel.HIGH
                elif impact_percent >= 20:
                    level = ImpactLevel.MEDIUM
                else:
                    level = ImpactLevel.LOW

                impacted.append(ImpactedService(
                    service_id=dep.service_id,
                    service_name=node.name,
                    impact_level=level,
                    impact_reason=f"Depends on {service_id} (depth {depth})",
                    estimated_impact_percent=impact_percent,
                    is_direct_dependency=(depth == 1),
                ))

                traverse(dep.service_id, depth + 1, False)

        traverse(service_id, 1, True)

        max_depth = 0
        for imp in impacted:
            depth = int(imp.impact_reason.split("depth ")[1].rstrip(")"))
            max_depth = max(max_depth, depth)

        users_estimate = len(impacted) * 1000
        revenue_estimate = len(impacted) * 5000

        return BlastRadius(
            source_service=service_id,
            impacted_services=impacted,
            total_services_affected=len(impacted),
            users_affected_estimate=users_estimate,
            revenue_at_risk_per_hour=revenue_estimate,
            cascade_depth=max_depth,
        )

    def analyze_change_impact(
        self,
        service_id: str,
        change_description: str,
        is_breaking_change: bool = False,
    ) -> ChangeImpact:
        """Analyze the impact of a proposed change."""
        blast = self.calculate_blast_radius(service_id)

        if is_breaking_change:
            for imp in blast.impacted_services:
                if imp.impact_level == ImpactLevel.HIGH:
                    imp.impact_level = ImpactLevel.CRITICAL
                elif imp.impact_level == ImpactLevel.MEDIUM:
                    imp.impact_level = ImpactLevel.HIGH

        critical_count = sum(
            1 for imp in blast.impacted_services
            if imp.impact_level == ImpactLevel.CRITICAL
        )
        high_count = sum(
            1 for imp in blast.impacted_services
            if imp.impact_level == ImpactLevel.HIGH
        )

        if critical_count > 0:
            risk = ImpactLevel.CRITICAL
            strategy = "Canary with 1% traffic, extended monitoring"
            rollback = "HIGH"
        elif high_count > 2:
            risk = ImpactLevel.HIGH
            strategy = "Canary with 5% traffic"
            rollback = "MEDIUM"
        elif blast.total_services_affected > 3:
            risk = ImpactLevel.MEDIUM
            strategy = "Canary with 10% traffic"
            rollback = "MEDIUM"
        else:
            risk = ImpactLevel.LOW
            strategy = "Standard deployment"
            rollback = "LOW"

        recommendations: list[str] = []
        if critical_count > 0:
            recommendations.append("Schedule deployment during low-traffic window")
            recommendations.append("Ensure rollback automation is configured")
        if is_breaking_change:
            recommendations.append("Notify downstream teams before deployment")
            recommendations.append("Consider feature flag for gradual rollout")
        if high_count > 0:
            recommendations.append("Monitor downstream services closely")

        return ChangeImpact(
            change_description=change_description,
            target_service=service_id,
            impacted_services=blast.impacted_services,
            rollback_complexity=rollback,
            recommended_deploy_strategy=strategy,
            risk_level=risk,
            recommendations=recommendations,
        )

    def get_visualization_data(self) -> dict[str, Any]:
        """Get data for graph visualization."""
        nodes = []
        for node in self._nodes.values():
            nodes.append({
                "id": node.service_id,
                "label": node.name,
                "tier": node.tier,
                "status": node.health.status.value,
                "error_rate": node.health.error_rate,
            })

        edges = []
        for conn in self._connections:
            edges.append({
                "source": conn.source,
                "target": conn.target,
                "type": conn.connection_type.value,
                "is_critical": conn.is_critical,
            })

        return {
            "nodes": nodes,
            "edges": edges,
            "total_services": len(nodes),
            "total_connections": len(edges),
        }

    def get_unhealthy_services(self) -> list[ServiceNode]:
        """Get all unhealthy services."""
        return [
            node for node in self._nodes.values()
            if node.health.status != HealthStatus.HEALTHY
        ]

    def get_services_by_team(self, team_name: str) -> list[ServiceNode]:
        """Get services owned by a team."""
        return [
            node for node in self._nodes.values()
            if node.ownership and node.ownership.team_name == team_name
        ]


_graph: ServiceGraph | None = None


def get_service_graph() -> ServiceGraph:
    """Get the default service graph."""
    global _graph
    if _graph is None:
        _graph = ServiceGraph()
    return _graph
