"""Layer 4: Topological/dependency-based correlation."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from app.deduplication.models import (
    ServiceDependency,
    DependencyType,
    SuppressionRule,
    SuppressionType,
)

log = logging.getLogger("devcopilot.deduplication.topology")


@dataclass
class TopologyConfig:
    """Configuration for topology-based correlation."""

    auto_suppress_downstream: bool = True
    suppress_duration_minutes: int = 30
    min_dependency_confidence: float = 0.7
    auto_discover_from_tracing: bool = True
    auto_discover_from_k8s: bool = True
    max_cascade_depth: int = 5


@dataclass
class ServiceNode:
    """Node in the service dependency graph."""

    service_name: str
    service_type: str = "service"
    team: str | None = None
    tier: int = 0
    dependencies: list[str] = field(default_factory=list)
    dependents: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TopologyMatch:
    """Result of topology-based matching."""

    upstream_service: str
    upstream_incident_id: UUID
    downstream_service: str
    dependency_path: list[str]
    confidence: float
    suppress_recommendation: bool


class ServiceDependencyGraph:
    """Service dependency graph for topology-aware correlation."""

    def __init__(self, config: TopologyConfig | None = None):
        self.config = config or TopologyConfig()
        self._nodes: dict[str, ServiceNode] = {}
        self._edges: dict[tuple[str, str], ServiceDependency] = {}
        self._last_updated: datetime = datetime.utcnow()

    def add_service(
        self,
        service_name: str,
        service_type: str = "service",
        team: str | None = None,
        tier: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> ServiceNode:
        """Add a service to the graph."""
        if service_name not in self._nodes:
            self._nodes[service_name] = ServiceNode(
                service_name=service_name,
                service_type=service_type,
                team=team,
                tier=tier,
                metadata=metadata or {},
            )
        return self._nodes[service_name]

    def add_dependency(
        self,
        upstream: str,
        downstream: str,
        dependency_type: DependencyType = DependencyType.CALLS,
        confidence: float = 1.0,
        source: str = "manual",
        metadata: dict[str, Any] | None = None,
    ) -> ServiceDependency:
        """Add a dependency edge: downstream depends on upstream."""
        self.add_service(upstream)
        self.add_service(downstream)

        dep = ServiceDependency(
            upstream_service=upstream,
            downstream_service=downstream,
            dependency_type=dependency_type,
            confidence=confidence,
            source=source,
            metadata=metadata or {},
        )

        self._edges[(upstream, downstream)] = dep

        if upstream not in self._nodes[downstream].dependencies:
            self._nodes[downstream].dependencies.append(upstream)
        if downstream not in self._nodes[upstream].dependents:
            self._nodes[upstream].dependents.append(downstream)

        self._last_updated = datetime.utcnow()
        return dep

    def has_dependency(self, downstream: str, upstream: str) -> bool:
        """Check if downstream depends on upstream."""
        return (upstream, downstream) in self._edges

    def get_dependency(
        self,
        upstream: str,
        downstream: str,
    ) -> ServiceDependency | None:
        """Get dependency edge if exists."""
        return self._edges.get((upstream, downstream))

    def get_upstream_services(
        self,
        service: str,
        max_depth: int | None = None,
    ) -> list[str]:
        """Get all services that this service depends on."""
        max_depth = max_depth or self.config.max_cascade_depth
        visited: set[str] = set()
        result: list[str] = []

        def dfs(svc: str, depth: int) -> None:
            if depth > max_depth or svc in visited:
                return
            visited.add(svc)

            node = self._nodes.get(svc)
            if node:
                for dep in node.dependencies:
                    if dep not in visited:
                        result.append(dep)
                        dfs(dep, depth + 1)

        dfs(service, 0)
        return result

    def get_downstream_services(
        self,
        service: str,
        max_depth: int | None = None,
    ) -> list[str]:
        """Get all services that depend on this service."""
        max_depth = max_depth or self.config.max_cascade_depth
        visited: set[str] = set()
        result: list[str] = []

        def dfs(svc: str, depth: int) -> None:
            if depth > max_depth or svc in visited:
                return
            visited.add(svc)

            node = self._nodes.get(svc)
            if node:
                for dependent in node.dependents:
                    if dependent not in visited:
                        result.append(dependent)
                        dfs(dependent, depth + 1)

        dfs(service, 0)
        return result

    def get_dependency_path(
        self,
        from_service: str,
        to_service: str,
    ) -> list[str] | None:
        """Find shortest dependency path between services."""
        if from_service == to_service:
            return [from_service]

        visited: set[str] = set()
        queue: list[tuple[str, list[str]]] = [(from_service, [from_service])]

        while queue:
            current, path = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)

            node = self._nodes.get(current)
            if not node:
                continue

            for dep in node.dependencies:
                if dep == to_service:
                    return path + [dep]
                if dep not in visited:
                    queue.append((dep, path + [dep]))

        return None

    def get_all_services(self) -> list[str]:
        """Get all service names."""
        return list(self._nodes.keys())

    def get_service_info(self, service: str) -> dict[str, Any] | None:
        """Get service node information."""
        node = self._nodes.get(service)
        if not node:
            return None

        return {
            "service_name": node.service_name,
            "service_type": node.service_type,
            "team": node.team,
            "tier": node.tier,
            "dependencies": node.dependencies,
            "dependents": node.dependents,
            "metadata": node.metadata,
        }

    def to_dict(self) -> dict[str, Any]:
        """Export graph as dictionary."""
        return {
            "nodes": [
                {
                    "name": node.service_name,
                    "type": node.service_type,
                    "team": node.team,
                    "tier": node.tier,
                }
                for node in self._nodes.values()
            ],
            "edges": [
                {
                    "upstream": dep.upstream_service,
                    "downstream": dep.downstream_service,
                    "type": dep.dependency_type.value,
                    "confidence": dep.confidence,
                    "source": dep.source,
                }
                for dep in self._edges.values()
            ],
            "last_updated": self._last_updated.isoformat(),
        }

    def visualize_ascii(self, root_service: str | None = None) -> str:
        """Generate ASCII visualization of the graph."""
        lines = []
        lines.append("Service Dependency Graph")
        lines.append("=" * 50)

        if root_service and root_service in self._nodes:
            lines.append(f"\nStarting from: {root_service}")
            lines.append("")
            self._visualize_tree(root_service, lines, "", True, set())
        else:
            roots = [
                name
                for name, node in self._nodes.items()
                if not node.dependencies
            ]
            for root in roots:
                self._visualize_tree(root, lines, "", True, set())
                lines.append("")

        return "\n".join(lines)

    def _visualize_tree(
        self,
        service: str,
        lines: list[str],
        prefix: str,
        is_last: bool,
        visited: set[str],
    ) -> None:
        """Helper to visualize tree recursively."""
        if service in visited:
            lines.append(f"{prefix}{'└── ' if is_last else '├── '}{service} (cycle)")
            return

        visited.add(service)
        connector = "└── " if is_last else "├── "
        lines.append(f"{prefix}{connector}{service}")

        node = self._nodes.get(service)
        if not node:
            return

        dependents = node.dependents
        for i, dependent in enumerate(dependents):
            is_last_child = i == len(dependents) - 1
            new_prefix = prefix + ("    " if is_last else "│   ")
            self._visualize_tree(
                dependent, lines, new_prefix, is_last_child, visited.copy()
            )


class DependencyDiscovery:
    """Auto-discover service dependencies from various sources."""

    def __init__(self, graph: ServiceDependencyGraph):
        self._graph = graph

    def discover_from_tracing(
        self,
        traces: list[dict[str, Any]],
    ) -> int:
        """Discover dependencies from distributed traces."""
        discovered = 0

        for trace in traces:
            spans = trace.get("spans", [])
            span_map: dict[str, dict[str, Any]] = {}

            for span in spans:
                span_map[span.get("span_id", "")] = span

            for span in spans:
                service = span.get("service_name")
                parent_id = span.get("parent_span_id")

                if service and parent_id and parent_id in span_map:
                    parent_service = span_map[parent_id].get("service_name")
                    if parent_service and parent_service != service:
                        if not self._graph.has_dependency(parent_service, service):
                            self._graph.add_dependency(
                                upstream=parent_service,
                                downstream=service,
                                dependency_type=DependencyType.CALLS,
                                confidence=0.8,
                                source="tracing",
                            )
                            discovered += 1

        log.info("Discovered %d dependencies from tracing", discovered)
        return discovered

    def discover_from_kubernetes(
        self,
        k8s_services: list[dict[str, Any]],
    ) -> int:
        """Discover dependencies from Kubernetes service definitions."""
        discovered = 0

        for svc in k8s_services:
            name = svc.get("name", "")
            namespace = svc.get("namespace", "default")
            service_name = f"{namespace}/{name}" if namespace != "default" else name

            self._graph.add_service(
                service_name=service_name,
                service_type=svc.get("type", "service"),
                metadata={"namespace": namespace, "kind": "kubernetes"},
            )

            env_vars = svc.get("env_vars", {})
            for env_name, env_value in env_vars.items():
                if "_HOST" in env_name or "_SERVICE" in env_name:
                    dep_service = env_value.replace(".svc.cluster.local", "")
                    if dep_service != service_name:
                        if not self._graph.has_dependency(dep_service, service_name):
                            self._graph.add_dependency(
                                upstream=dep_service,
                                downstream=service_name,
                                dependency_type=DependencyType.CALLS,
                                confidence=0.7,
                                source="kubernetes",
                            )
                            discovered += 1

        log.info("Discovered %d dependencies from Kubernetes", discovered)
        return discovered

    def discover_from_service_mesh(
        self,
        mesh_config: dict[str, Any],
    ) -> int:
        """Discover dependencies from service mesh configuration."""
        discovered = 0

        virtual_services = mesh_config.get("virtual_services", [])
        for vs in virtual_services:
            source = vs.get("source_service")
            destinations = vs.get("destinations", [])

            for dest in destinations:
                dest_service = dest.get("service")
                if source and dest_service and source != dest_service:
                    if not self._graph.has_dependency(dest_service, source):
                        self._graph.add_dependency(
                            upstream=dest_service,
                            downstream=source,
                            dependency_type=DependencyType.CALLS,
                            confidence=0.9,
                            source="service_mesh",
                        )
                        discovered += 1

        log.info("Discovered %d dependencies from service mesh", discovered)
        return discovered


class TopologyCorrelator:
    """Correlate alerts based on service topology."""

    def __init__(
        self,
        graph: ServiceDependencyGraph,
        config: TopologyConfig | None = None,
    ):
        self._graph = graph
        self.config = config or TopologyConfig()
        self._active_incidents: dict[str, UUID] = {}
        self._suppressed_services: dict[str, datetime] = {}

    def register_incident(
        self,
        service: str,
        incident_id: UUID,
    ) -> None:
        """Register an active incident for a service."""
        self._active_incidents[service] = incident_id

    def clear_incident(self, service: str) -> None:
        """Clear active incident for a service."""
        self._active_incidents.pop(service, None)

    def find_upstream_incident(
        self,
        service: str,
    ) -> TopologyMatch | None:
        """Find if there's an active incident in an upstream service."""
        upstream_services = self._graph.get_upstream_services(service)

        for upstream in upstream_services:
            if upstream in self._active_incidents:
                path = self._graph.get_dependency_path(service, upstream)
                dep = self._graph.get_dependency(upstream, service)
                confidence = dep.confidence if dep else 0.5

                return TopologyMatch(
                    upstream_service=upstream,
                    upstream_incident_id=self._active_incidents[upstream],
                    downstream_service=service,
                    dependency_path=path or [service, upstream],
                    confidence=confidence,
                    suppress_recommendation=self.config.auto_suppress_downstream,
                )

        return None

    def get_affected_services(
        self,
        service: str,
    ) -> list[str]:
        """Get all services affected by an issue in the given service."""
        return self._graph.get_downstream_services(service)

    def should_suppress(self, service: str) -> bool:
        """Check if alerts from this service should be suppressed."""
        if service in self._suppressed_services:
            suppress_until = self._suppressed_services[service]
            if datetime.utcnow() < suppress_until:
                return True
            else:
                del self._suppressed_services[service]

        return False

    def suppress_service(
        self,
        service: str,
        duration_minutes: int | None = None,
    ) -> None:
        """Suppress alerts from a service."""
        duration = duration_minutes or self.config.suppress_duration_minutes
        self._suppressed_services[service] = datetime.utcnow() + timedelta(
            minutes=duration
        )

    def unsuppress_service(self, service: str) -> None:
        """Remove suppression from a service."""
        self._suppressed_services.pop(service, None)

    def get_suppression_status(self) -> dict[str, Any]:
        """Get current suppression status."""
        now = datetime.utcnow()
        active = {
            svc: (until - now).total_seconds()
            for svc, until in self._suppressed_services.items()
            if until > now
        }
        return {
            "suppressed_services": list(active.keys()),
            "details": {
                svc: {"remaining_seconds": remaining}
                for svc, remaining in active.items()
            },
        }


class UpstreamSuppressor:
    """Suppress downstream alerts when upstream has issues."""

    def __init__(
        self,
        graph: ServiceDependencyGraph,
        correlator: TopologyCorrelator,
        config: TopologyConfig | None = None,
    ):
        self._graph = graph
        self._correlator = correlator
        self.config = config or TopologyConfig()
        self._suppression_rules: dict[UUID, SuppressionRule] = {}

    def create_upstream_suppression(
        self,
        upstream_service: str,
        upstream_incident_id: UUID,
        duration_minutes: int | None = None,
    ) -> SuppressionRule:
        """Create suppression rule for downstream services."""
        downstream_services = self._graph.get_downstream_services(upstream_service)

        duration = duration_minutes or self.config.suppress_duration_minutes

        rule = SuppressionRule(
            name=f"Upstream suppression: {upstream_service}",
            description=(
                f"Suppressing alerts from services dependent on {upstream_service} "
                f"due to incident {upstream_incident_id}"
            ),
            match_criteria={
                "service": downstream_services,
            },
            suppression_type=SuppressionType.DEPENDENCY,
            duration=timedelta(minutes=duration),
            starts_at=datetime.utcnow(),
            expires_at=datetime.utcnow() + timedelta(minutes=duration),
            linked_incident_id=upstream_incident_id,
        )

        self._suppression_rules[rule.rule_id] = rule

        for svc in downstream_services:
            self._correlator.suppress_service(svc, duration)

        log.info(
            "Created upstream suppression for %s, affecting %d services",
            upstream_service,
            len(downstream_services),
        )

        return rule

    def end_suppression(self, rule_id: UUID) -> bool:
        """End a suppression rule early."""
        rule = self._suppression_rules.get(rule_id)
        if not rule:
            return False

        services = rule.match_criteria.get("service", [])
        for svc in services:
            self._correlator.unsuppress_service(svc)

        rule.enabled = False
        log.info("Ended suppression rule: %s", rule.name)
        return True

    def extend_suppression(
        self,
        rule_id: UUID,
        additional_minutes: int,
    ) -> bool:
        """Extend a suppression rule."""
        rule = self._suppression_rules.get(rule_id)
        if not rule:
            return False

        new_expiry = (rule.expires_at or datetime.utcnow()) + timedelta(
            minutes=additional_minutes
        )
        rule.expires_at = new_expiry
        rule.duration = (rule.duration or timedelta()) + timedelta(
            minutes=additional_minutes
        )

        services = rule.match_criteria.get("service", [])
        for svc in services:
            self._correlator.suppress_service(
                svc,
                int((new_expiry - datetime.utcnow()).total_seconds() / 60),
            )

        log.info("Extended suppression rule: %s by %d minutes", rule.name, additional_minutes)
        return True

    def get_active_suppressions(self) -> list[SuppressionRule]:
        """Get all active suppression rules."""
        now = datetime.utcnow()
        return [
            rule
            for rule in self._suppression_rules.values()
            if rule.is_active(now)
        ]

    def get_suppression_stats(self) -> dict[str, Any]:
        """Get suppression statistics."""
        active = self.get_active_suppressions()
        total_suppressed = sum(r.alerts_suppressed for r in active)

        return {
            "active_rules": len(active),
            "total_alerts_suppressed": total_suppressed,
            "rules": [r.to_dict() for r in active],
        }
