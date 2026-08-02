"""Incident knowledge graph.

Links incidents, services, people, and code.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("omnipotent.learning.knowledge_graph")


class NodeType(Enum):
    """Type of knowledge graph node."""

    INCIDENT = "incident"
    SERVICE = "service"
    ENGINEER = "engineer"
    CODE_FILE = "code_file"
    ROOT_CAUSE = "root_cause"
    FIX = "fix"
    RUNBOOK = "runbook"
    PATTERN = "pattern"


class EdgeType(Enum):
    """Type of knowledge graph edge."""

    AFFECTS = "affects"
    RESOLVED_BY = "resolved_by"
    CAUSED_BY = "caused_by"
    OWNS = "owns"
    SIMILAR_TO = "similar_to"
    PREVENTS = "prevents"
    REFERENCES = "references"
    EXPERT_IN = "expert_in"


@dataclass
class KnowledgeNode:
    """A node in the knowledge graph."""

    node_id: str
    node_type: NodeType
    name: str
    properties: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "node_type": self.node_type.value,
            "name": self.name,
            "properties": self.properties,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class KnowledgeEdge:
    """An edge in the knowledge graph."""

    edge_id: str
    edge_type: EdgeType
    source_id: str
    target_id: str
    weight: float = 1.0
    properties: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "edge_id": self.edge_id,
            "edge_type": self.edge_type.value,
            "source_id": self.source_id,
            "target_id": self.target_id,
            "weight": self.weight,
            "properties": self.properties,
        }


@dataclass
class GraphInsight:
    """An insight derived from the knowledge graph."""

    insight_id: str
    insight_type: str
    title: str
    description: str
    related_nodes: list[str]
    confidence: float
    actionable: bool
    action: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "insight_id": self.insight_id,
            "insight_type": self.insight_type,
            "title": self.title,
            "description": self.description,
            "related_nodes": self.related_nodes,
            "confidence": round(self.confidence, 2),
            "actionable": self.actionable,
            "action": self.action,
        }


class IncidentKnowledgeGraph:
    """Knowledge graph linking incidents, services, people, and code."""

    def __init__(self) -> None:
        self._nodes: dict[str, KnowledgeNode] = {}
        self._edges: list[KnowledgeEdge] = []
        self._edge_counter = 0
        self._insight_counter = 0

    def add_node(
        self,
        node_id: str,
        node_type: NodeType,
        name: str,
        properties: dict[str, Any] | None = None,
    ) -> KnowledgeNode:
        """Add a node to the graph."""
        node = KnowledgeNode(
            node_id=node_id,
            node_type=node_type,
            name=name,
            properties=properties or {},
        )
        self._nodes[node_id] = node
        return node

    def add_edge(
        self,
        source_id: str,
        target_id: str,
        edge_type: EdgeType,
        weight: float = 1.0,
        properties: dict[str, Any] | None = None,
    ) -> KnowledgeEdge | None:
        """Add an edge to the graph."""
        if source_id not in self._nodes or target_id not in self._nodes:
            return None

        self._edge_counter += 1
        edge = KnowledgeEdge(
            edge_id=f"edge_{self._edge_counter}",
            edge_type=edge_type,
            source_id=source_id,
            target_id=target_id,
            weight=weight,
            properties=properties or {},
        )
        self._edges.append(edge)
        return edge

    def add_incident(
        self,
        incident_id: str,
        title: str,
        affected_services: list[str],
        resolved_by: str | None = None,
        root_cause: str | None = None,
        code_files: list[str] | None = None,
        similar_incidents: list[str] | None = None,
    ) -> KnowledgeNode:
        """Add an incident with all its relationships."""
        incident_node = self.add_node(
            node_id=incident_id,
            node_type=NodeType.INCIDENT,
            name=title,
            properties={"status": "resolved" if resolved_by else "open"},
        )

        for service in affected_services:
            service_id = f"service_{service}"
            if service_id not in self._nodes:
                self.add_node(
                    node_id=service_id,
                    node_type=NodeType.SERVICE,
                    name=service,
                )
            self.add_edge(incident_id, service_id, EdgeType.AFFECTS)

        if resolved_by:
            engineer_id = f"eng_{resolved_by}"
            if engineer_id not in self._nodes:
                self.add_node(
                    node_id=engineer_id,
                    node_type=NodeType.ENGINEER,
                    name=resolved_by,
                    properties={"incidents_resolved": 1},
                )
            else:
                self._nodes[engineer_id].properties["incidents_resolved"] = (
                    self._nodes[engineer_id].properties.get("incidents_resolved", 0) + 1
                )
            self.add_edge(incident_id, engineer_id, EdgeType.RESOLVED_BY)

        if root_cause:
            cause_id = f"cause_{root_cause.replace(' ', '_').lower()}"
            if cause_id not in self._nodes:
                self.add_node(
                    node_id=cause_id,
                    node_type=NodeType.ROOT_CAUSE,
                    name=root_cause,
                )
            self.add_edge(incident_id, cause_id, EdgeType.CAUSED_BY)

        for file in (code_files or []):
            file_id = f"file_{file.replace('/', '_')}"
            if file_id not in self._nodes:
                self.add_node(
                    node_id=file_id,
                    node_type=NodeType.CODE_FILE,
                    name=file,
                )
            self.add_edge(incident_id, file_id, EdgeType.REFERENCES)

        for similar_id in (similar_incidents or []):
            if similar_id in self._nodes:
                self.add_edge(incident_id, similar_id, EdgeType.SIMILAR_TO, weight=0.8)

        return incident_node

    def get_node(self, node_id: str) -> KnowledgeNode | None:
        """Get a node by ID."""
        return self._nodes.get(node_id)

    def get_neighbors(
        self,
        node_id: str,
        edge_type: EdgeType | None = None,
        direction: str = "outgoing",
    ) -> list[KnowledgeNode]:
        """Get neighboring nodes."""
        neighbors = []

        for edge in self._edges:
            if direction in ("outgoing", "both") and edge.source_id == node_id:
                if edge_type is None or edge.edge_type == edge_type:
                    if edge.target_id in self._nodes:
                        neighbors.append(self._nodes[edge.target_id])

            if direction in ("incoming", "both") and edge.target_id == node_id:
                if edge_type is None or edge.edge_type == edge_type:
                    if edge.source_id in self._nodes:
                        neighbors.append(self._nodes[edge.source_id])

        return neighbors

    def find_path(
        self,
        start_id: str,
        end_id: str,
        max_depth: int = 5,
    ) -> list[str] | None:
        """Find a path between two nodes."""
        if start_id not in self._nodes or end_id not in self._nodes:
            return None

        visited = set()
        queue = [(start_id, [start_id])]

        while queue and len(queue[0][1]) <= max_depth:
            current, path = queue.pop(0)

            if current == end_id:
                return path

            if current in visited:
                continue
            visited.add(current)

            for neighbor in self.get_neighbors(current, direction="both"):
                if neighbor.node_id not in visited:
                    queue.append((neighbor.node_id, path + [neighbor.node_id]))

        return None

    def get_service_incident_stats(
        self,
        service_name: str,
    ) -> dict[str, Any]:
        """Get incident statistics for a service."""
        service_id = f"service_{service_name}"
        if service_id not in self._nodes:
            return {}

        incidents = []
        for edge in self._edges:
            if edge.target_id == service_id and edge.edge_type == EdgeType.AFFECTS:
                if edge.source_id in self._nodes:
                    incidents.append(self._nodes[edge.source_id])

        resolvers: dict[str, int] = {}
        root_causes: dict[str, int] = {}

        for incident in incidents:
            for engineer in self.get_neighbors(
                incident.node_id,
                EdgeType.RESOLVED_BY,
            ):
                resolvers[engineer.name] = resolvers.get(engineer.name, 0) + 1

            for cause in self.get_neighbors(incident.node_id, EdgeType.CAUSED_BY):
                root_causes[cause.name] = root_causes.get(cause.name, 0) + 1

        return {
            "service": service_name,
            "total_incidents": len(incidents),
            "top_resolvers": sorted(
                resolvers.items(),
                key=lambda x: x[1],
                reverse=True,
            )[:3],
            "common_root_causes": sorted(
                root_causes.items(),
                key=lambda x: x[1],
                reverse=True,
            )[:3],
        }

    def get_engineer_expertise(
        self,
        engineer_name: str,
    ) -> dict[str, Any]:
        """Get expertise profile for an engineer."""
        engineer_id = f"eng_{engineer_name}"
        if engineer_id not in self._nodes:
            return {}

        resolved_incidents = []
        for edge in self._edges:
            if edge.target_id == engineer_id and edge.edge_type == EdgeType.RESOLVED_BY:
                if edge.source_id in self._nodes:
                    resolved_incidents.append(self._nodes[edge.source_id])

        services: dict[str, int] = {}
        causes: dict[str, int] = {}

        for incident in resolved_incidents:
            for service in self.get_neighbors(incident.node_id, EdgeType.AFFECTS):
                services[service.name] = services.get(service.name, 0) + 1

            for cause in self.get_neighbors(incident.node_id, EdgeType.CAUSED_BY):
                causes[cause.name] = causes.get(cause.name, 0) + 1

        return {
            "engineer": engineer_name,
            "incidents_resolved": len(resolved_incidents),
            "expertise_services": sorted(
                services.items(),
                key=lambda x: x[1],
                reverse=True,
            )[:5],
            "expertise_causes": sorted(
                causes.items(),
                key=lambda x: x[1],
                reverse=True,
            )[:5],
        }

    def generate_insights(self) -> list[GraphInsight]:
        """Generate insights from the knowledge graph."""
        insights = []

        service_incidents: dict[str, int] = {}
        for edge in self._edges:
            if edge.edge_type == EdgeType.AFFECTS:
                service_id = edge.target_id
                service_incidents[service_id] = service_incidents.get(service_id, 0) + 1

        for service_id, count in service_incidents.items():
            if count >= 5:
                service = self._nodes.get(service_id)
                if service:
                    self._insight_counter += 1
                    insights.append(
                        GraphInsight(
                            insight_id=f"insight_{self._insight_counter}",
                            insight_type="hotspot",
                            title=f"{service.name} is an incident hotspot",
                            description=f"{count} incidents have affected {service.name}",
                            related_nodes=[service_id],
                            confidence=min(1.0, count / 10),
                            actionable=True,
                            action=f"Prioritize stability improvements for {service.name}",
                        )
                    )

        cause_counts: dict[str, int] = {}
        for node in self._nodes.values():
            if node.node_type == NodeType.ROOT_CAUSE:
                edges = [
                    e for e in self._edges
                    if e.target_id == node.node_id and e.edge_type == EdgeType.CAUSED_BY
                ]
                cause_counts[node.node_id] = len(edges)

        for cause_id, count in cause_counts.items():
            if count >= 3:
                cause = self._nodes.get(cause_id)
                if cause:
                    self._insight_counter += 1
                    insights.append(
                        GraphInsight(
                            insight_id=f"insight_{self._insight_counter}",
                            insight_type="recurring_cause",
                            title=f"Recurring root cause: {cause.name}",
                            description=f"{count} incidents caused by {cause.name}",
                            related_nodes=[cause_id],
                            confidence=0.9,
                            actionable=True,
                            action=f"Implement systemic fix for {cause.name}",
                        )
                    )

        return insights

    def get_visualization_data(self) -> dict[str, Any]:
        """Get data for graph visualization."""
        nodes = [
            {
                "id": n.node_id,
                "label": n.name,
                "type": n.node_type.value,
            }
            for n in self._nodes.values()
        ]

        edges = [
            {
                "id": e.edge_id,
                "source": e.source_id,
                "target": e.target_id,
                "type": e.edge_type.value,
            }
            for e in self._edges
        ]

        return {"nodes": nodes, "edges": edges}


_graph: IncidentKnowledgeGraph | None = None


def get_knowledge_graph() -> IncidentKnowledgeGraph:
    """Get the default knowledge graph."""
    global _graph
    if _graph is None:
        _graph = IncidentKnowledgeGraph()
    return _graph
