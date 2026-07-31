"""Dependency Graph - Graph representation of service dependencies."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from enum import Enum
from collections import defaultdict

log = logging.getLogger("devcopilot.dependency_graph.graph")


class DependencyType(str, Enum):
    """Types of dependencies."""
    SYNC_HTTP = "sync_http"
    ASYNC_HTTP = "async_http"
    GRPC = "grpc"
    DATABASE = "database"
    CACHE = "cache"
    QUEUE = "queue"
    EVENT = "event"
    FILE = "file"
    UNKNOWN = "unknown"


class HealthStatus(str, Enum):
    """Health status of a service."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


@dataclass
class ServiceNode:
    """A node in the dependency graph representing a service."""
    id: str
    name: str
    namespace: str = "default"
    service_type: str = "api"
    tier: int = 0  # 0 = edge, 1 = mid, 2 = data
    health: HealthStatus = HealthStatus.UNKNOWN
    labels: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    # Computed fields
    in_degree: int = 0  # Number of services depending on this
    out_degree: int = 0  # Number of dependencies

    @property
    def criticality_score(self) -> float:
        """Calculate criticality based on dependents."""
        # More dependents = more critical
        return min(1.0, self.in_degree * 0.2)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "namespace": self.namespace,
            "service_type": self.service_type,
            "tier": self.tier,
            "health": self.health.value,
            "labels": self.labels,
            "in_degree": self.in_degree,
            "out_degree": self.out_degree,
            "criticality_score": self.criticality_score,
        }


@dataclass
class DependencyEdge:
    """An edge in the dependency graph representing a dependency."""
    source: str  # Service that depends
    target: str  # Service being depended on
    dependency_type: DependencyType = DependencyType.UNKNOWN
    weight: float = 1.0  # Traffic weight / importance
    latency_p50_ms: float = 0.0
    latency_p99_ms: float = 0.0
    error_rate: float = 0.0
    requests_per_second: float = 0.0
    is_critical: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "dependency_type": self.dependency_type.value,
            "weight": self.weight,
            "latency_p50_ms": self.latency_p50_ms,
            "latency_p99_ms": self.latency_p99_ms,
            "error_rate": self.error_rate,
            "requests_per_second": self.requests_per_second,
            "is_critical": self.is_critical,
        }


class DependencyGraph:
    """Graph representation of service dependencies.

    Features:
    - Add/remove nodes and edges
    - Traverse upstream/downstream
    - Find critical paths
    - Detect cycles
    - Calculate impact scores
    """

    def __init__(self):
        """Initialize an empty dependency graph."""
        self._nodes: dict[str, ServiceNode] = {}
        self._edges: dict[tuple[str, str], DependencyEdge] = {}
        self._adjacency: dict[str, set[str]] = defaultdict(set)  # source -> targets
        self._reverse_adjacency: dict[str, set[str]] = defaultdict(set)  # target -> sources
        self._last_updated: datetime | None = None

    @property
    def node_count(self) -> int:
        return len(self._nodes)

    @property
    def edge_count(self) -> int:
        return len(self._edges)

    def add_node(self, node: ServiceNode) -> None:
        """Add a service node to the graph."""
        self._nodes[node.id] = node
        self._last_updated = datetime.now(timezone.utc)

    def add_edge(self, edge: DependencyEdge) -> None:
        """Add a dependency edge to the graph."""
        key = (edge.source, edge.target)
        self._edges[key] = edge
        self._adjacency[edge.source].add(edge.target)
        self._reverse_adjacency[edge.target].add(edge.source)

        # Update degrees
        if edge.source in self._nodes:
            self._nodes[edge.source].out_degree = len(self._adjacency[edge.source])
        if edge.target in self._nodes:
            self._nodes[edge.target].in_degree = len(self._reverse_adjacency[edge.target])

        self._last_updated = datetime.now(timezone.utc)

    def remove_node(self, node_id: str) -> bool:
        """Remove a node and all its edges."""
        if node_id not in self._nodes:
            return False

        # Remove edges
        edges_to_remove = [
            key for key in self._edges
            if key[0] == node_id or key[1] == node_id
        ]
        for key in edges_to_remove:
            self.remove_edge(key[0], key[1])

        del self._nodes[node_id]
        return True

    def remove_edge(self, source: str, target: str) -> bool:
        """Remove an edge from the graph."""
        key = (source, target)
        if key not in self._edges:
            return False

        del self._edges[key]
        self._adjacency[source].discard(target)
        self._reverse_adjacency[target].discard(source)

        # Update degrees
        if source in self._nodes:
            self._nodes[source].out_degree = len(self._adjacency[source])
        if target in self._nodes:
            self._nodes[target].in_degree = len(self._reverse_adjacency[target])

        return True

    def get_node(self, node_id: str) -> ServiceNode | None:
        """Get a node by ID."""
        return self._nodes.get(node_id)

    def get_edge(self, source: str, target: str) -> DependencyEdge | None:
        """Get an edge by source and target."""
        return self._edges.get((source, target))

    def get_all_nodes(self) -> list[ServiceNode]:
        """Get all nodes."""
        return list(self._nodes.values())

    def get_all_edges(self) -> list[DependencyEdge]:
        """Get all edges."""
        return list(self._edges.values())

    def get_dependencies(self, node_id: str) -> list[ServiceNode]:
        """Get services this node depends on (outgoing edges)."""
        targets = self._adjacency.get(node_id, set())
        return [self._nodes[t] for t in targets if t in self._nodes]

    def get_dependents(self, node_id: str) -> list[ServiceNode]:
        """Get services that depend on this node (incoming edges)."""
        sources = self._reverse_adjacency.get(node_id, set())
        return [self._nodes[s] for s in sources if s in self._nodes]

    def get_upstream(self, node_id: str, max_depth: int = 10) -> set[str]:
        """Get all upstream dependencies (transitive)."""
        visited = set()
        to_visit = [node_id]
        depth = 0

        while to_visit and depth < max_depth:
            current = to_visit.pop(0)
            if current in visited:
                continue
            visited.add(current)

            for target in self._adjacency.get(current, []):
                if target not in visited:
                    to_visit.append(target)
            depth += 1

        visited.discard(node_id)  # Don't include self
        return visited

    def get_downstream(self, node_id: str, max_depth: int = 10) -> set[str]:
        """Get all downstream dependents (transitive)."""
        visited = set()
        to_visit = [node_id]
        depth = 0

        while to_visit and depth < max_depth:
            current = to_visit.pop(0)
            if current in visited:
                continue
            visited.add(current)

            for source in self._reverse_adjacency.get(current, []):
                if source not in visited:
                    to_visit.append(source)
            depth += 1

        visited.discard(node_id)
        return visited

    def find_critical_path(self, source: str, target: str) -> list[str] | None:
        """Find the critical path between two nodes using BFS."""
        if source not in self._nodes or target not in self._nodes:
            return None

        visited = {source}
        queue = [(source, [source])]

        while queue:
            current, path = queue.pop(0)

            if current == target:
                return path

            for neighbor in self._adjacency.get(current, []):
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append((neighbor, path + [neighbor]))

        return None

    def detect_cycles(self) -> list[list[str]]:
        """Detect cycles in the dependency graph."""
        cycles = []
        visited = set()
        rec_stack = set()

        def dfs(node: str, path: list[str]) -> None:
            visited.add(node)
            rec_stack.add(node)
            path.append(node)

            for neighbor in self._adjacency.get(node, []):
                if neighbor not in visited:
                    dfs(neighbor, path)
                elif neighbor in rec_stack:
                    # Found cycle
                    cycle_start = path.index(neighbor)
                    cycles.append(path[cycle_start:] + [neighbor])

            path.pop()
            rec_stack.remove(node)

        for node in self._nodes:
            if node not in visited:
                dfs(node, [])

        return cycles

    def get_leaf_nodes(self) -> list[ServiceNode]:
        """Get nodes with no outgoing dependencies (data stores, etc.)."""
        return [
            node for node in self._nodes.values()
            if node.out_degree == 0
        ]

    def get_root_nodes(self) -> list[ServiceNode]:
        """Get nodes with no incoming dependencies (edge services)."""
        return [
            node for node in self._nodes.values()
            if node.in_degree == 0
        ]

    def calculate_tiers(self) -> dict[str, int]:
        """Calculate tier for each service (edge=0, data=max)."""
        tiers = {}
        leaves = self.get_leaf_nodes()

        # Start from leaves and work backwards
        for leaf in leaves:
            tiers[leaf.id] = 0

        def get_tier(node_id: str) -> int:
            if node_id in tiers:
                return tiers[node_id]

            deps = self.get_dependencies(node_id)
            if not deps:
                tiers[node_id] = 0
                return 0

            max_dep_tier = max(get_tier(d.id) for d in deps)
            tiers[node_id] = max_dep_tier + 1
            return tiers[node_id]

        for node_id in self._nodes:
            get_tier(node_id)

        return tiers

    def to_dict(self) -> dict[str, Any]:
        """Export graph to dictionary format."""
        return {
            "nodes": [n.to_dict() for n in self._nodes.values()],
            "edges": [e.to_dict() for e in self._edges.values()],
            "metadata": {
                "node_count": self.node_count,
                "edge_count": self.edge_count,
                "last_updated": self._last_updated.isoformat() if self._last_updated else None,
            },
        }

    def to_cytoscape(self) -> dict[str, Any]:
        """Export to Cytoscape.js format for visualization."""
        elements = []

        for node in self._nodes.values():
            elements.append({
                "data": {
                    "id": node.id,
                    "label": node.name,
                    "type": node.service_type,
                    "health": node.health.value,
                    "tier": node.tier,
                    "criticality": node.criticality_score,
                }
            })

        for edge in self._edges.values():
            elements.append({
                "data": {
                    "id": f"{edge.source}->{edge.target}",
                    "source": edge.source,
                    "target": edge.target,
                    "type": edge.dependency_type.value,
                    "weight": edge.weight,
                }
            })

        return {"elements": elements}

    def to_mermaid(self) -> str:
        """Export to Mermaid diagram format."""
        lines = ["graph TD"]

        # Add nodes with styling
        for node in self._nodes.values():
            if node.service_type == "database":
                lines.append(f"    {node.id}[({node.name})]")
            elif node.service_type == "queue":
                lines.append(f"    {node.id}>{node.name}]")
            else:
                lines.append(f"    {node.id}[{node.name}]")

        # Add edges
        for edge in self._edges.values():
            arrow = "-->" if edge.dependency_type == DependencyType.SYNC_HTTP else "-..->"
            lines.append(f"    {edge.source} {arrow} {edge.target}")

        return "\n".join(lines)


def create_graph_from_services(services: list[Any]) -> DependencyGraph:
    """Create a graph from discovered services."""
    graph = DependencyGraph()

    # Add nodes
    for service in services:
        svc_type = getattr(service, 'service_type', 'api')
        svc_type_str = svc_type.value if hasattr(svc_type, 'value') else str(svc_type)
        node = ServiceNode(
            id=service.fqdn if hasattr(service, 'fqdn') else service.name,
            name=service.name,
            namespace=getattr(service, 'namespace', 'default'),
            service_type=svc_type_str,
            labels=getattr(service, 'labels', {}),
        )
        graph.add_node(node)

    # Add edges
    for service in services:
        source_id = service.fqdn if hasattr(service, 'fqdn') else service.name
        for dep in getattr(service, 'dependencies', []):
            target_id = dep
            edge = DependencyEdge(
                source=source_id,
                target=target_id,
            )
            graph.add_edge(edge)

    return graph
