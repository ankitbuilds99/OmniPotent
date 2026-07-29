"""Knowledge graph for incident relationships and insights."""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from app.similar_search.models import (
    IncidentRecord,
    KnowledgeNode,
    KnowledgeEdge,
    RootCauseCategory,
)

log = logging.getLogger("devcopilot.similar_search.knowledge_graph")


class NodeType:
    """Node type constants."""

    INCIDENT = "incident"
    SERVICE = "service"
    ERROR = "error"
    ROOT_CAUSE = "root_cause"
    RESOLUTION = "resolution"
    RESPONDER = "responder"
    TAG = "tag"
    ALERT = "alert"


class EdgeType:
    """Edge type constants."""

    AFFECTS = "affects"
    CAUSED_BY = "caused_by"
    RESOLVED_BY = "resolved_by"
    RESPONDED_TO = "responded_to"
    RELATED_TO = "related_to"
    SIMILAR_TO = "similar_to"
    TRIGGERS = "triggers"
    DEPENDS_ON = "depends_on"
    HAS_TAG = "has_tag"
    PRODUCES = "produces"


@dataclass
class GraphStats:
    """Statistics about the knowledge graph."""

    node_count: int = 0
    edge_count: int = 0
    incident_count: int = 0
    service_count: int = 0
    error_count: int = 0
    avg_connections_per_node: float = 0.0
    most_connected_nodes: list[tuple[str, int]] = field(default_factory=list)


class IncidentKnowledgeGraph:
    """Graph-based knowledge store for incidents."""

    def __init__(self):
        self._nodes: dict[str, KnowledgeNode] = {}
        self._edges: list[KnowledgeEdge] = []
        self._adjacency: dict[str, list[str]] = defaultdict(list)
        self._reverse_adjacency: dict[str, list[str]] = defaultdict(list)
        self._node_edges: dict[str, list[KnowledgeEdge]] = defaultdict(list)

    def add_node(self, node: KnowledgeNode) -> None:
        """Add a node to the graph."""
        self._nodes[node.node_id] = node

    def add_edge(self, edge: KnowledgeEdge) -> None:
        """Add an edge to the graph."""
        self._edges.append(edge)
        self._adjacency[edge.source_id].append(edge.target_id)
        self._reverse_adjacency[edge.target_id].append(edge.source_id)
        self._node_edges[edge.source_id].append(edge)
        self._node_edges[edge.target_id].append(edge)

    def get_node(self, node_id: str) -> KnowledgeNode | None:
        """Get a node by ID."""
        return self._nodes.get(node_id)

    def get_neighbors(self, node_id: str) -> list[str]:
        """Get neighboring node IDs."""
        return self._adjacency.get(node_id, [])

    def get_incoming(self, node_id: str) -> list[str]:
        """Get nodes pointing to this node."""
        return self._reverse_adjacency.get(node_id, [])

    def get_edges_for_node(self, node_id: str) -> list[KnowledgeEdge]:
        """Get all edges connected to a node."""
        return self._node_edges.get(node_id, [])

    def get_edges_of_type(
        self,
        node_id: str,
        edge_type: str,
    ) -> list[KnowledgeEdge]:
        """Get edges of a specific type for a node."""
        return [
            e
            for e in self._node_edges.get(node_id, [])
            if e.edge_type == edge_type and e.source_id == node_id
        ]

    def get_nodes_by_type(self, node_type: str) -> list[KnowledgeNode]:
        """Get all nodes of a specific type."""
        return [n for n in self._nodes.values() if n.node_type == node_type]

    def get_stats(self) -> GraphStats:
        """Get graph statistics."""
        edge_counts: dict[str, int] = defaultdict(int)
        for node_id in self._nodes:
            edge_counts[node_id] = len(self._node_edges.get(node_id, []))

        sorted_by_edges = sorted(edge_counts.items(), key=lambda x: x[1], reverse=True)

        incident_count = len(self.get_nodes_by_type(NodeType.INCIDENT))
        service_count = len(self.get_nodes_by_type(NodeType.SERVICE))
        error_count = len(self.get_nodes_by_type(NodeType.ERROR))

        total_edges = sum(edge_counts.values())
        avg_connections = total_edges / len(self._nodes) if self._nodes else 0

        return GraphStats(
            node_count=len(self._nodes),
            edge_count=len(self._edges),
            incident_count=incident_count,
            service_count=service_count,
            error_count=error_count,
            avg_connections_per_node=round(avg_connections, 2),
            most_connected_nodes=sorted_by_edges[:10],
        )

    def find_path(
        self,
        start_id: str,
        end_id: str,
        max_depth: int = 5,
    ) -> list[str] | None:
        """Find shortest path between two nodes using BFS."""
        if start_id not in self._nodes or end_id not in self._nodes:
            return None

        if start_id == end_id:
            return [start_id]

        visited = {start_id}
        queue = [(start_id, [start_id])]

        while queue:
            current, path = queue.pop(0)

            if len(path) > max_depth:
                continue

            for neighbor in self._adjacency.get(current, []):
                if neighbor == end_id:
                    return path + [neighbor]

                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append((neighbor, path + [neighbor]))

        return None

    def to_dict(self) -> dict[str, Any]:
        """Export graph to dictionary."""
        return {
            "nodes": [
                {
                    "id": n.node_id,
                    "type": n.node_type,
                    "label": n.label,
                    "properties": n.properties,
                }
                for n in self._nodes.values()
            ],
            "edges": [
                {
                    "source": e.source_id,
                    "target": e.target_id,
                    "type": e.edge_type,
                    "weight": e.weight,
                }
                for e in self._edges
            ],
        }


class GraphBuilder:
    """Build knowledge graph from incidents."""

    def __init__(self, graph: IncidentKnowledgeGraph | None = None):
        self._graph = graph or IncidentKnowledgeGraph()

    @property
    def graph(self) -> IncidentKnowledgeGraph:
        """Get the graph."""
        return self._graph

    def add_incident(self, incident: IncidentRecord) -> None:
        """Add an incident and its relationships to the graph."""
        incident_node = KnowledgeNode(
            node_id=f"incident:{incident.incident_id}",
            node_type=NodeType.INCIDENT,
            label=incident.title,
            properties={
                "severity": incident.severity.value,
                "status": incident.status.value,
                "created_at": incident.created_at.isoformat(),
                "duration_minutes": incident.duration_minutes,
            },
        )
        self._graph.add_node(incident_node)

        for service in incident.services:
            service_id = f"service:{service}"
            if not self._graph.get_node(service_id):
                self._graph.add_node(
                    KnowledgeNode(
                        node_id=service_id,
                        node_type=NodeType.SERVICE,
                        label=service,
                    )
                )
            self._graph.add_edge(
                KnowledgeEdge(
                    source_id=incident_node.node_id,
                    target_id=service_id,
                    edge_type=EdgeType.AFFECTS,
                )
            )

        root_cause_id = f"root_cause:{incident.root_cause_category.value}"
        if not self._graph.get_node(root_cause_id):
            self._graph.add_node(
                KnowledgeNode(
                    node_id=root_cause_id,
                    node_type=NodeType.ROOT_CAUSE,
                    label=incident.root_cause_category.value,
                )
            )
        self._graph.add_edge(
            KnowledgeEdge(
                source_id=incident_node.node_id,
                target_id=root_cause_id,
                edge_type=EdgeType.CAUSED_BY,
            )
        )

        for error in incident.error_messages[:5]:
            error_key = self._normalize_error(error)
            error_id = f"error:{error_key}"
            if not self._graph.get_node(error_id):
                self._graph.add_node(
                    KnowledgeNode(
                        node_id=error_id,
                        node_type=NodeType.ERROR,
                        label=error[:100],
                    )
                )
            self._graph.add_edge(
                KnowledgeEdge(
                    source_id=incident_node.node_id,
                    target_id=error_id,
                    edge_type=EdgeType.PRODUCES,
                )
            )

        if incident.resolution_summary:
            resolution_id = f"resolution:{incident.incident_id}"
            self._graph.add_node(
                KnowledgeNode(
                    node_id=resolution_id,
                    node_type=NodeType.RESOLUTION,
                    label=incident.resolution_summary[:100],
                    properties={
                        "steps": incident.resolution_steps,
                        "code_changes": incident.code_changes,
                    },
                )
            )
            self._graph.add_edge(
                KnowledgeEdge(
                    source_id=incident_node.node_id,
                    target_id=resolution_id,
                    edge_type=EdgeType.RESOLVED_BY,
                )
            )

        for responder in incident.responders:
            responder_id = f"responder:{responder}"
            if not self._graph.get_node(responder_id):
                self._graph.add_node(
                    KnowledgeNode(
                        node_id=responder_id,
                        node_type=NodeType.RESPONDER,
                        label=responder,
                    )
                )
            self._graph.add_edge(
                KnowledgeEdge(
                    source_id=responder_id,
                    target_id=incident_node.node_id,
                    edge_type=EdgeType.RESPONDED_TO,
                )
            )

        for tag in incident.tags:
            tag_id = f"tag:{tag}"
            if not self._graph.get_node(tag_id):
                self._graph.add_node(
                    KnowledgeNode(
                        node_id=tag_id,
                        node_type=NodeType.TAG,
                        label=tag,
                    )
                )
            self._graph.add_edge(
                KnowledgeEdge(
                    source_id=incident_node.node_id,
                    target_id=tag_id,
                    edge_type=EdgeType.HAS_TAG,
                )
            )

        for alert in incident.alerts[:10]:
            alert_id = f"alert:{alert.lower().replace(' ', '_')}"
            if not self._graph.get_node(alert_id):
                self._graph.add_node(
                    KnowledgeNode(
                        node_id=alert_id,
                        node_type=NodeType.ALERT,
                        label=alert,
                    )
                )
            self._graph.add_edge(
                KnowledgeEdge(
                    source_id=alert_id,
                    target_id=incident_node.node_id,
                    edge_type=EdgeType.TRIGGERS,
                )
            )

    def _normalize_error(self, error: str) -> str:
        """Normalize error message for deduplication."""
        normalized = error.lower()[:50]
        normalized = "".join(c if c.isalnum() else "_" for c in normalized)
        return normalized

    def add_similarity_edge(
        self,
        incident1_id: UUID,
        incident2_id: UUID,
        similarity_score: float,
    ) -> None:
        """Add similarity relationship between incidents."""
        self._graph.add_edge(
            KnowledgeEdge(
                source_id=f"incident:{incident1_id}",
                target_id=f"incident:{incident2_id}",
                edge_type=EdgeType.SIMILAR_TO,
                weight=similarity_score,
            )
        )

    def add_service_dependency(
        self,
        upstream: str,
        downstream: str,
    ) -> None:
        """Add service dependency relationship."""
        upstream_id = f"service:{upstream}"
        downstream_id = f"service:{downstream}"

        if not self._graph.get_node(upstream_id):
            self._graph.add_node(
                KnowledgeNode(
                    node_id=upstream_id,
                    node_type=NodeType.SERVICE,
                    label=upstream,
                )
            )
        if not self._graph.get_node(downstream_id):
            self._graph.add_node(
                KnowledgeNode(
                    node_id=downstream_id,
                    node_type=NodeType.SERVICE,
                    label=downstream,
                )
            )

        self._graph.add_edge(
            KnowledgeEdge(
                source_id=downstream_id,
                target_id=upstream_id,
                edge_type=EdgeType.DEPENDS_ON,
            )
        )

    def build_from_incidents(
        self,
        incidents: list[IncidentRecord],
    ) -> IncidentKnowledgeGraph:
        """Build complete graph from list of incidents."""
        for incident in incidents:
            self.add_incident(incident)
        return self._graph


class GraphQuery:
    """Query interface for the knowledge graph."""

    def __init__(self, graph: IncidentKnowledgeGraph):
        self._graph = graph

    def find_related_incidents(
        self,
        incident_id: UUID,
        max_depth: int = 2,
    ) -> list[tuple[str, float]]:
        """Find incidents related through shared entities."""
        start_id = f"incident:{incident_id}"
        related: dict[str, float] = {}

        visited = {start_id}
        queue = [(start_id, 0, 1.0)]

        while queue:
            current, depth, weight = queue.pop(0)

            if depth >= max_depth:
                continue

            for edge in self._graph.get_edges_for_node(current):
                neighbor = (
                    edge.target_id
                    if edge.source_id == current
                    else edge.source_id
                )

                if neighbor in visited:
                    continue

                visited.add(neighbor)
                new_weight = weight * 0.5

                if neighbor.startswith("incident:") and neighbor != start_id:
                    if neighbor not in related or related[neighbor] < new_weight:
                        related[neighbor] = new_weight
                else:
                    queue.append((neighbor, depth + 1, new_weight))

        sorted_related = sorted(related.items(), key=lambda x: x[1], reverse=True)
        return sorted_related

    def find_incidents_by_service(
        self,
        service: str,
        limit: int = 20,
    ) -> list[str]:
        """Find incidents affecting a service."""
        service_id = f"service:{service}"
        incidents = []

        for edge in self._graph.get_edges_for_node(service_id):
            if edge.edge_type == EdgeType.AFFECTS:
                incidents.append(edge.source_id)

        return incidents[:limit]

    def find_incidents_by_error(
        self,
        error_pattern: str,
    ) -> list[str]:
        """Find incidents with similar error messages."""
        error_nodes = self._graph.get_nodes_by_type(NodeType.ERROR)
        matching_errors = [
            n for n in error_nodes if error_pattern.lower() in n.label.lower()
        ]

        incidents = set()
        for error_node in matching_errors:
            for edge in self._graph.get_edges_for_node(error_node.node_id):
                if edge.edge_type == EdgeType.PRODUCES:
                    incidents.add(edge.source_id)

        return list(incidents)

    def find_common_responders(
        self,
        service: str,
    ) -> list[tuple[str, int]]:
        """Find responders who frequently handle incidents for a service."""
        incidents = self.find_incidents_by_service(service)
        responder_counts: dict[str, int] = defaultdict(int)

        for incident_id in incidents:
            for edge in self._graph.get_edges_for_node(incident_id):
                if edge.edge_type == EdgeType.RESPONDED_TO:
                    responder_counts[edge.source_id] += 1

        return sorted(responder_counts.items(), key=lambda x: x[1], reverse=True)

    def find_resolution_patterns(
        self,
        root_cause: RootCauseCategory,
    ) -> list[KnowledgeNode]:
        """Find resolution patterns for a root cause."""
        root_cause_id = f"root_cause:{root_cause.value}"
        resolutions = []

        for edge in self._graph.get_edges_for_node(root_cause_id):
            if edge.edge_type == EdgeType.CAUSED_BY:
                incident_id = edge.source_id

                for res_edge in self._graph.get_edges_for_node(incident_id):
                    if res_edge.edge_type == EdgeType.RESOLVED_BY:
                        resolution = self._graph.get_node(res_edge.target_id)
                        if resolution:
                            resolutions.append(resolution)

        return resolutions

    def get_service_incident_history(
        self,
        service: str,
        days: int = 30,
    ) -> dict[str, Any]:
        """Get incident history for a service."""
        service_id = f"service:{service}"
        service_node = self._graph.get_node(service_id)

        if not service_node:
            return {"service": service, "found": False}

        incidents = []
        cutoff = datetime.utcnow() - timedelta(days=days)

        for edge in self._graph.get_edges_for_node(service_id):
            if edge.edge_type == EdgeType.AFFECTS:
                incident_node = self._graph.get_node(edge.source_id)
                if incident_node:
                    created_at = incident_node.properties.get("created_at", "")
                    if created_at:
                        try:
                            if datetime.fromisoformat(created_at) >= cutoff:
                                incidents.append(incident_node)
                        except ValueError:
                            pass

        root_cause_counts: dict[str, int] = defaultdict(int)
        for inc in incidents:
            for edge in self._graph.get_edges_for_node(inc.node_id):
                if edge.edge_type == EdgeType.CAUSED_BY:
                    rc_node = self._graph.get_node(edge.target_id)
                    if rc_node:
                        root_cause_counts[rc_node.label] += 1

        return {
            "service": service,
            "found": True,
            "incident_count": len(incidents),
            "period_days": days,
            "root_cause_breakdown": dict(root_cause_counts),
            "incidents": [
                {
                    "id": i.node_id,
                    "title": i.label,
                    "severity": i.properties.get("severity"),
                    "created_at": i.properties.get("created_at"),
                }
                for i in incidents[:10]
            ],
        }


class RelationshipAnalyzer:
    """Analyze relationships in the knowledge graph."""

    def __init__(self, graph: IncidentKnowledgeGraph):
        self._graph = graph
        self._query = GraphQuery(graph)

    def find_cascade_patterns(self) -> list[dict[str, Any]]:
        """Find service cascade patterns from incident data."""
        service_nodes = self._graph.get_nodes_by_type(NodeType.SERVICE)
        cascades = []

        for service_node in service_nodes:
            incidents = self._query.find_incidents_by_service(service_node.label)
            if len(incidents) < 2:
                continue

            co_occurring: dict[str, int] = defaultdict(int)

            for incident_id in incidents:
                affected_services = []
                for edge in self._graph.get_edges_for_node(incident_id):
                    if edge.edge_type == EdgeType.AFFECTS:
                        target = self._graph.get_node(edge.target_id)
                        if target and target.label != service_node.label:
                            affected_services.append(target.label)

                for other_service in affected_services:
                    co_occurring[other_service] += 1

            if co_occurring:
                top_co = sorted(
                    co_occurring.items(), key=lambda x: x[1], reverse=True
                )[:5]
                cascades.append(
                    {
                        "primary_service": service_node.label,
                        "incident_count": len(incidents),
                        "co_occurring_services": [
                            {"service": s, "count": c} for s, c in top_co
                        ],
                    }
                )

        return sorted(cascades, key=lambda x: x["incident_count"], reverse=True)

    def find_error_service_correlation(self) -> list[dict[str, Any]]:
        """Find correlations between errors and services."""
        error_nodes = self._graph.get_nodes_by_type(NodeType.ERROR)
        correlations = []

        for error_node in error_nodes:
            service_counts: dict[str, int] = defaultdict(int)

            for edge in self._graph.get_edges_for_node(error_node.node_id):
                if edge.edge_type == EdgeType.PRODUCES:
                    incident_id = edge.source_id

                    for inc_edge in self._graph.get_edges_for_node(incident_id):
                        if inc_edge.edge_type == EdgeType.AFFECTS:
                            service = self._graph.get_node(inc_edge.target_id)
                            if service:
                                service_counts[service.label] += 1

            if service_counts:
                correlations.append(
                    {
                        "error": error_node.label,
                        "services": dict(service_counts),
                        "total_occurrences": sum(service_counts.values()),
                    }
                )

        return sorted(correlations, key=lambda x: x["total_occurrences"], reverse=True)

    def find_expert_responders(
        self,
        root_cause: RootCauseCategory | None = None,
    ) -> list[dict[str, Any]]:
        """Find expert responders for specific root causes."""
        responder_nodes = self._graph.get_nodes_by_type(NodeType.RESPONDER)
        expertise: dict[str, dict[str, int]] = {}

        for responder in responder_nodes:
            expertise[responder.label] = defaultdict(int)

            for edge in self._graph.get_edges_for_node(responder.node_id):
                if edge.edge_type == EdgeType.RESPONDED_TO:
                    incident_id = edge.target_id

                    for inc_edge in self._graph.get_edges_for_node(incident_id):
                        if inc_edge.edge_type == EdgeType.CAUSED_BY:
                            rc = self._graph.get_node(inc_edge.target_id)
                            if rc:
                                expertise[responder.label][rc.label] += 1

        experts: list[dict[str, Any]] = []
        for resp_name, causes in expertise.items():
            if not causes:
                continue

            if root_cause:
                count = causes.get(root_cause.value, 0)
                if count > 0:
                    experts.append(
                        {
                            "responder": resp_name,
                            "root_cause": root_cause.value,
                            "incident_count": count,
                        }
                    )
            else:
                top_cause = max(causes.items(), key=lambda x: x[1])
                experts.append(
                    {
                        "responder": resp_name,
                        "primary_expertise": top_cause[0],
                        "incident_count": top_cause[1],
                        "all_expertise": dict(causes),
                    }
                )

        return sorted(experts, key=lambda x: int(x["incident_count"]), reverse=True)

    def calculate_service_risk_scores(self) -> list[dict[str, Any]]:
        """Calculate risk scores for services based on incident history."""
        service_nodes = self._graph.get_nodes_by_type(NodeType.SERVICE)
        scores = []

        for service in service_nodes:
            incidents = []
            for edge in self._graph.get_edges_for_node(service.node_id):
                if edge.edge_type == EdgeType.AFFECTS:
                    incident = self._graph.get_node(edge.source_id)
                    if incident:
                        incidents.append(incident)

            if not incidents:
                continue

            severity_weights = {"P1": 10, "P2": 5, "P3": 2, "P4": 1}
            severity_score = sum(
                severity_weights.get(i.properties.get("severity", "P4"), 1)
                for i in incidents
            )

            total_duration = sum(
                i.properties.get("duration_minutes", 0) for i in incidents
            )

            risk_score = (severity_score * 0.6) + (len(incidents) * 0.3) + (
                total_duration / 60 * 0.1
            )

            scores.append(
                {
                    "service": service.label,
                    "risk_score": round(risk_score, 2),
                    "incident_count": len(incidents),
                    "total_duration_hours": round(total_duration / 60, 1),
                    "severity_breakdown": self._count_severities(incidents),
                }
            )

        return sorted(scores, key=lambda x: x["risk_score"], reverse=True)

    def _count_severities(
        self,
        incidents: list[KnowledgeNode],
    ) -> dict[str, int]:
        """Count incidents by severity."""
        counts: dict[str, int] = defaultdict(int)
        for inc in incidents:
            sev = inc.properties.get("severity", "P4")
            counts[sev] += 1
        return dict(counts)
