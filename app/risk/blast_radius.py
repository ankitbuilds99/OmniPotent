"""Blast radius analysis for code changes.

Analyzes the impact radius of changes across the codebase.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger("omnipotent.risk.blast_radius")


@dataclass
class DependencyNode:
    """A node in the dependency graph."""

    id: str
    name: str
    type: str
    file_path: str | None = None
    requests_per_hour: int = 0
    is_user_facing: bool = False
    is_revenue_critical: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "file_path": self.file_path,
            "requests_per_hour": self.requests_per_hour,
            "is_user_facing": self.is_user_facing,
            "is_revenue_critical": self.is_revenue_critical,
        }


@dataclass
class DependencyEdge:
    """An edge in the dependency graph."""

    source: str
    target: str
    type: str
    strength: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "type": self.type,
            "strength": self.strength,
        }


@dataclass
class BlastRadius:
    """Blast radius analysis result."""

    analysis_id: str
    changed_files: list[str]
    direct_consumers: list[DependencyNode]
    indirect_consumers: list[DependencyNode]
    affected_endpoints: list[str]
    total_requests_at_risk: int
    user_facing_impact: bool
    revenue_critical_impact: bool
    risk_score: float
    visualization_data: dict[str, Any]
    analyzed_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "analysis_id": self.analysis_id,
            "changed_files": self.changed_files,
            "direct_consumers": [c.to_dict() for c in self.direct_consumers],
            "indirect_consumers": [c.to_dict() for c in self.indirect_consumers],
            "affected_endpoints": self.affected_endpoints,
            "total_requests_at_risk": self.total_requests_at_risk,
            "user_facing_impact": self.user_facing_impact,
            "revenue_critical_impact": self.revenue_critical_impact,
            "risk_score": round(self.risk_score, 2),
            "analyzed_at": self.analyzed_at.isoformat(),
        }


class BlastRadiusAnalyzer:
    """Analyzes blast radius of code changes."""

    def __init__(self) -> None:
        self._analyses: dict[str, BlastRadius] = {}
        self._analysis_counter = 0
        self._nodes: dict[str, DependencyNode] = {}
        self._edges: list[DependencyEdge] = []
        self._setup_dependency_graph()

    def _setup_dependency_graph(self) -> None:
        """Set up the dependency graph."""
        nodes = [
            DependencyNode(
                id="checkout_api",
                name="checkout-api",
                type="service",
                requests_per_hour=45000,
                is_user_facing=True,
                is_revenue_critical=True,
            ),
            DependencyNode(
                id="payment_service",
                name="payment-service",
                type="service",
                requests_per_hour=12000,
                is_user_facing=False,
                is_revenue_critical=True,
            ),
            DependencyNode(
                id="user_service",
                name="user-service",
                type="service",
                requests_per_hour=60000,
                is_user_facing=True,
                is_revenue_critical=False,
            ),
            DependencyNode(
                id="billing_module",
                name="billing",
                type="module",
                file_path="billing/",
                requests_per_hour=8000,
                is_revenue_critical=True,
            ),
            DependencyNode(
                id="web_frontend",
                name="web-frontend",
                type="frontend",
                requests_per_hour=80000,
                is_user_facing=True,
            ),
            DependencyNode(
                id="mobile_app",
                name="mobile-app",
                type="frontend",
                requests_per_hour=50000,
                is_user_facing=True,
            ),
            DependencyNode(
                id="admin_panel",
                name="admin-panel",
                type="frontend",
                requests_per_hour=1000,
                is_user_facing=False,
            ),
            DependencyNode(
                id="subscription_service",
                name="subscription-service",
                type="service",
                requests_per_hour=12000,
                is_revenue_critical=True,
            ),
            DependencyNode(
                id="notification_service",
                name="notification-service",
                type="service",
                requests_per_hour=5000,
            ),
            DependencyNode(
                id="refunds_module",
                name="refunds",
                type="module",
                file_path="refunds/",
                requests_per_hour=3000,
                is_revenue_critical=True,
            ),
        ]

        for node in nodes:
            self._nodes[node.id] = node

        edges = [
            DependencyEdge("checkout_api", "payment_service", "calls"),
            DependencyEdge("checkout_api", "user_service", "calls"),
            DependencyEdge("payment_service", "billing_module", "imports"),
            DependencyEdge("web_frontend", "checkout_api", "calls"),
            DependencyEdge("mobile_app", "checkout_api", "calls"),
            DependencyEdge("admin_panel", "checkout_api", "calls"),
            DependencyEdge("subscription_service", "billing_module", "imports"),
            DependencyEdge("subscription_service", "payment_service", "calls"),
            DependencyEdge("refunds_module", "billing_module", "imports"),
            DependencyEdge("payment_service", "notification_service", "calls"),
        ]

        self._edges = edges

    def analyze(
        self,
        changed_files: list[str],
    ) -> BlastRadius:
        """Analyze blast radius of changed files."""
        self._analysis_counter += 1
        analysis_id = f"blast_{self._analysis_counter}"

        affected_nodes = self._find_affected_nodes(changed_files)
        direct_consumers = self._find_direct_consumers(affected_nodes)
        indirect_consumers = self._find_indirect_consumers(direct_consumers)

        all_affected = set(affected_nodes + direct_consumers + indirect_consumers)
        total_requests = sum(
            self._nodes[n].requests_per_hour
            for n in all_affected
            if n in self._nodes
        )

        user_facing = any(
            self._nodes[n].is_user_facing
            for n in all_affected
            if n in self._nodes
        )

        revenue_critical = any(
            self._nodes[n].is_revenue_critical
            for n in all_affected
            if n in self._nodes
        )

        endpoints = self._get_affected_endpoints(all_affected)
        risk_score = self._calculate_risk_score(
            direct_consumers,
            indirect_consumers,
            total_requests,
            user_facing,
            revenue_critical,
        )

        direct_nodes = [
            self._nodes[n] for n in direct_consumers if n in self._nodes
        ]
        indirect_nodes = [
            self._nodes[n] for n in indirect_consumers if n in self._nodes
        ]

        analysis = BlastRadius(
            analysis_id=analysis_id,
            changed_files=changed_files,
            direct_consumers=direct_nodes,
            indirect_consumers=indirect_nodes,
            affected_endpoints=endpoints,
            total_requests_at_risk=total_requests,
            user_facing_impact=user_facing,
            revenue_critical_impact=revenue_critical,
            risk_score=risk_score,
            visualization_data=self._build_visualization(
                affected_nodes,
                direct_consumers,
                indirect_consumers,
            ),
            analyzed_at=datetime.now(timezone.utc),
        )

        self._analyses[analysis_id] = analysis

        log.info(
            "blast radius analyzed",
            extra={
                "analysis_id": analysis_id,
                "direct_consumers": len(direct_consumers),
                "indirect_consumers": len(indirect_consumers),
                "total_requests": total_requests,
            },
        )

        return analysis

    def _find_affected_nodes(self, changed_files: list[str]) -> list[str]:
        """Find nodes directly affected by file changes."""
        affected = []

        for node_id, node in self._nodes.items():
            if node.file_path:
                for file in changed_files:
                    if node.file_path in file:
                        affected.append(node_id)
                        break

        for file in changed_files:
            for node_id, node in self._nodes.items():
                if node.name.replace("-", "_") in file.replace("-", "_"):
                    if node_id not in affected:
                        affected.append(node_id)

        return affected

    def _find_direct_consumers(self, affected_nodes: list[str]) -> list[str]:
        """Find nodes that directly depend on affected nodes."""
        consumers = []

        for edge in self._edges:
            if edge.target in affected_nodes and edge.source not in affected_nodes:
                if edge.source not in consumers:
                    consumers.append(edge.source)

        return consumers

    def _find_indirect_consumers(self, direct_consumers: list[str]) -> list[str]:
        """Find nodes that indirectly depend on affected nodes."""
        indirect = []

        for edge in self._edges:
            if edge.target in direct_consumers:
                if edge.source not in direct_consumers and edge.source not in indirect:
                    indirect.append(edge.source)

        return indirect

    def _get_affected_endpoints(self, affected_nodes: set[str]) -> list[str]:
        """Get API endpoints affected by changes."""
        endpoints = []
        endpoint_map = {
            "checkout_api": ["/api/v1/checkout", "/api/v1/cart", "/api/v1/order"],
            "payment_service": ["/api/v1/payment", "/api/v1/charge"],
            "user_service": ["/api/v1/user", "/api/v1/profile", "/api/v1/auth"],
            "subscription_service": ["/api/v1/subscription", "/api/v1/plan"],
            "billing_module": ["/api/v1/invoice", "/api/v1/billing"],
        }

        for node in affected_nodes:
            if node in endpoint_map:
                endpoints.extend(endpoint_map[node])

        return endpoints

    def _calculate_risk_score(
        self,
        direct: list[str],
        indirect: list[str],
        total_requests: int,
        user_facing: bool,
        revenue_critical: bool,
    ) -> float:
        """Calculate overall risk score."""
        base_score = len(direct) * 2 + len(indirect) * 0.5
        request_factor = min(5.0, total_requests / 20000)

        if user_facing:
            base_score *= 1.5
        if revenue_critical:
            base_score *= 1.5

        return min(10.0, base_score + request_factor)

    def _build_visualization(
        self,
        affected: list[str],
        direct: list[str],
        indirect: list[str],
    ) -> dict[str, Any]:
        """Build visualization data for the blast radius."""
        nodes = []
        edges = []

        all_nodes = set(affected + direct + indirect)

        for node_id in all_nodes:
            if node_id in self._nodes:
                node = self._nodes[node_id]
                category = "affected" if node_id in affected else "direct" if node_id in direct else "indirect"
                nodes.append({
                    "id": node_id,
                    "label": node.name,
                    "category": category,
                    "requests": node.requests_per_hour,
                })

        for edge in self._edges:
            if edge.source in all_nodes and edge.target in all_nodes:
                edges.append({
                    "source": edge.source,
                    "target": edge.target,
                    "type": edge.type,
                })

        return {"nodes": nodes, "edges": edges}

    def get_analysis(self, analysis_id: str) -> BlastRadius | None:
        """Get an analysis by ID."""
        return self._analyses.get(analysis_id)

    def add_node(self, node: DependencyNode) -> None:
        """Add a node to the graph."""
        self._nodes[node.id] = node

    def add_edge(self, edge: DependencyEdge) -> None:
        """Add an edge to the graph."""
        self._edges.append(edge)


_analyzer: BlastRadiusAnalyzer | None = None


def get_blast_radius_analyzer() -> BlastRadiusAnalyzer:
    """Get the default blast radius analyzer."""
    global _analyzer
    if _analyzer is None:
        _analyzer = BlastRadiusAnalyzer()
    return _analyzer
