"""Graph Visualizer - Generate visualizations for dependency graphs."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any
from enum import Enum

from app.dependency_graph.graph import DependencyGraph, HealthStatus

log = logging.getLogger("omnipotent.dependency_graph.visualizer")


class VisualizationFormat(str, Enum):
    """Output formats for visualization."""
    CYTOSCAPE = "cytoscape"
    MERMAID = "mermaid"
    D3 = "d3"
    DOT = "dot"
    ASCII = "ascii"
    # No SVG member: it was offered here but `render` had no branch for it, so
    # every request for it raised ValueError. Rendering SVG means running the DOT
    # output through graphviz, which is a system dependency this package does not
    # have. Callers who want SVG should render DOT and pipe it through `dot -Tsvg`.


@dataclass
class VisualizationConfig:
    """Configuration for graph visualization."""
    # Layout
    layout: str = "dagre"  # dagre, circle, grid, concentric
    direction: str = "TB"  # TB, BT, LR, RL

    # Node styling
    show_health: bool = True
    show_metrics: bool = False
    node_size_by: str = "criticality"  # criticality, traffic, fixed

    # Edge styling
    show_latency: bool = False
    show_error_rate: bool = False
    edge_width_by: str = "traffic"  # traffic, fixed

    # Highlighting
    highlight_critical: bool = True
    highlight_unhealthy: bool = True
    fade_healthy: bool = False

    # Filtering
    min_tier: int = 0
    max_tier: int = 100
    namespaces: list[str] = field(default_factory=list)


class GraphVisualizer:
    """Generates visualizations for dependency graphs.

    Features:
    - Multiple output formats (Cytoscape, Mermaid, D3, DOT)
    - Customizable styling
    - Health-based coloring
    - Blast radius highlighting
    """

    # Color schemes
    HEALTH_COLORS = {
        HealthStatus.HEALTHY: "#4CAF50",
        HealthStatus.DEGRADED: "#FF9800",
        HealthStatus.UNHEALTHY: "#F44336",
        HealthStatus.UNKNOWN: "#9E9E9E",
    }

    TIER_COLORS = {
        0: "#E3F2FD",  # Edge (light blue)
        1: "#BBDEFB",  # API (blue)
        2: "#90CAF9",  # Service (medium blue)
        3: "#64B5F6",  # Data (dark blue)
    }

    def __init__(self, graph: DependencyGraph, config: VisualizationConfig | None = None):
        """Initialize the visualizer.

        Args:
            graph: Dependency graph to visualize
            config: Visualization configuration
        """
        self.graph = graph
        self.config = config or VisualizationConfig()

    def render(
        self,
        format: VisualizationFormat = VisualizationFormat.CYTOSCAPE,
        highlight_nodes: list[str] | None = None,
    ) -> str | dict[str, Any]:
        """Render the graph in the specified format.

        Args:
            format: Output format
            highlight_nodes: Nodes to highlight (e.g., blast radius)

        Returns:
            Rendered visualization (string or dict)
        """
        if format == VisualizationFormat.CYTOSCAPE:
            return self._render_cytoscape(highlight_nodes)
        elif format == VisualizationFormat.MERMAID:
            return self._render_mermaid(highlight_nodes)
        elif format == VisualizationFormat.D3:
            return self._render_d3(highlight_nodes)
        elif format == VisualizationFormat.DOT:
            return self._render_dot(highlight_nodes)
        elif format == VisualizationFormat.ASCII:
            return self._render_ascii(highlight_nodes)
        else:
            raise ValueError(f"Unsupported format: {format}")

    def _render_cytoscape(self, highlight_nodes: list[str] | None = None) -> dict[str, Any]:
        """Render as Cytoscape.js format."""
        elements = []
        highlight_set = set(highlight_nodes or [])

        # Add nodes
        for node in self.graph.get_all_nodes():
            # Apply filters
            if self.config.namespaces and node.namespace not in self.config.namespaces:
                continue

            if not (self.config.min_tier <= node.tier <= self.config.max_tier):
                continue

            classes: list[str] = []

            # Styling classes
            if node.id in highlight_set:
                classes.append("highlighted")

            if self.config.show_health:
                classes.append(f"health-{node.health.value}")

            if self.config.highlight_critical and node.criticality_score >= 0.8:
                classes.append("critical")

            if self.config.highlight_unhealthy and node.health == HealthStatus.UNHEALTHY:
                classes.append("unhealthy")

            node_data: dict[str, Any] = {
                "data": {
                    "id": node.id,
                    "label": node.name,
                    "namespace": node.namespace,
                    "type": node.service_type,
                    "tier": node.tier,
                    "health": node.health.value,
                    "criticality": node.criticality_score,
                    "in_degree": node.in_degree,
                    "out_degree": node.out_degree,
                },
                "classes": " ".join(classes),
            }
            elements.append(node_data)

        # Add edges
        for edge in self.graph.get_all_edges():
            edge_classes: list[str] = []

            if edge.is_critical:
                edge_classes.append("critical-path")

            edge_data_dict: dict[str, Any] = {
                "id": f"{edge.source}->{edge.target}",
                "source": edge.source,
                "target": edge.target,
                "type": edge.dependency_type.value,
                "weight": edge.weight,
            }

            if self.config.show_latency:
                edge_data_dict["latency"] = edge.latency_p50_ms

            if self.config.show_error_rate:
                edge_data_dict["error_rate"] = edge.error_rate

            edge_data: dict[str, Any] = {
                "data": edge_data_dict,
                "classes": " ".join(edge_classes),
            }
            elements.append(edge_data)

        return {
            "elements": elements,
            "layout": {
                "name": self.config.layout,
                "rankDir": self.config.direction,
            },
            "style": self._get_cytoscape_style(),
        }

    def _get_cytoscape_style(self) -> list[dict[str, Any]]:
        """Get Cytoscape.js style configuration."""
        return [
            {
                "selector": "node",
                "style": {
                    "label": "data(label)",
                    "background-color": "#64B5F6",
                    "text-valign": "center",
                    "text-halign": "center",
                    "font-size": "12px",
                },
            },
            {
                "selector": "node.health-healthy",
                "style": {"background-color": self.HEALTH_COLORS[HealthStatus.HEALTHY]},
            },
            {
                "selector": "node.health-degraded",
                "style": {"background-color": self.HEALTH_COLORS[HealthStatus.DEGRADED]},
            },
            {
                "selector": "node.health-unhealthy",
                "style": {"background-color": self.HEALTH_COLORS[HealthStatus.UNHEALTHY]},
            },
            {
                "selector": "node.highlighted",
                "style": {
                    "border-width": "3px",
                    "border-color": "#F44336",
                },
            },
            {
                "selector": "edge",
                "style": {
                    "width": "2px",
                    "target-arrow-shape": "triangle",
                    "curve-style": "bezier",
                    "line-color": "#9E9E9E",
                    "target-arrow-color": "#9E9E9E",
                },
            },
            {
                "selector": "edge.critical-path",
                "style": {
                    "line-color": "#F44336",
                    "target-arrow-color": "#F44336",
                    "width": "3px",
                },
            },
        ]

    def _render_mermaid(self, highlight_nodes: list[str] | None = None) -> str:
        """Render as Mermaid diagram."""
        lines = [f"graph {self.config.direction}"]
        highlight_set = set(highlight_nodes or [])

        # Add nodes with styling
        for node in self.graph.get_all_nodes():
            node_id = node.id.replace("-", "_").replace(".", "_")

            # Choose shape based on service type
            if "database" in node.service_type.lower():
                node_def = f"    {node_id}[({node.name})]"
            elif "queue" in node.service_type.lower():
                node_def = f"    {node_id}>{node.name}]"
            elif "cache" in node.service_type.lower():
                node_def = f"    {node_id}{{{{{node.name}}}}}"
            else:
                node_def = f"    {node_id}[{node.name}]"

            lines.append(node_def)

        # Add edges
        for edge in self.graph.get_all_edges():
            source = edge.source.replace("-", "_").replace(".", "_")
            target = edge.target.replace("-", "_").replace(".", "_")

            # Choose arrow style
            if edge.dependency_type.value in ("sync_http", "grpc"):
                arrow = "-->"
            else:
                arrow = "-.->"

            lines.append(f"    {source} {arrow} {target}")

        # Add styles
        lines.append("")
        lines.append("    %% Styling")

        # Style by health
        for node in self.graph.get_all_nodes():
            node_id = node.id.replace("-", "_").replace(".", "_")

            if node.health == HealthStatus.HEALTHY:
                lines.append(f"    style {node_id} fill:#4CAF50")
            elif node.health == HealthStatus.DEGRADED:
                lines.append(f"    style {node_id} fill:#FF9800")
            elif node.health == HealthStatus.UNHEALTHY:
                lines.append(f"    style {node_id} fill:#F44336")

        # Highlight specific nodes
        for node_id in highlight_set:
            safe_id = node_id.replace("-", "_").replace(".", "_")
            lines.append(f"    style {safe_id} stroke:#F44336,stroke-width:3px")

        return "\n".join(lines)

    def _render_d3(self, highlight_nodes: list[str] | None = None) -> dict[str, Any]:
        """Render as D3.js force-directed graph format."""
        highlight_set = set(highlight_nodes or [])

        nodes = []
        for node in self.graph.get_all_nodes():
            nodes.append({
                "id": node.id,
                "name": node.name,
                "group": node.tier,
                "health": node.health.value,
                "criticality": node.criticality_score,
                "highlighted": node.id in highlight_set,
                "radius": 10 + (node.criticality_score * 20),
            })

        links = []
        for edge in self.graph.get_all_edges():
            links.append({
                "source": edge.source,
                "target": edge.target,
                "type": edge.dependency_type.value,
                "value": edge.weight,
            })

        return {
            "nodes": nodes,
            "links": links,
            "config": {
                "colors": {
                    "healthy": self.HEALTH_COLORS[HealthStatus.HEALTHY],
                    "degraded": self.HEALTH_COLORS[HealthStatus.DEGRADED],
                    "unhealthy": self.HEALTH_COLORS[HealthStatus.UNHEALTHY],
                    "unknown": self.HEALTH_COLORS[HealthStatus.UNKNOWN],
                    "highlight": "#F44336",
                },
            },
        }

    def _render_dot(self, highlight_nodes: list[str] | None = None) -> str:
        """Render as GraphViz DOT format."""
        highlight_set = set(highlight_nodes or [])

        lines = ["digraph G {"]
        lines.append("    rankdir=%s;" % self.config.direction)
        lines.append("    node [shape=box, style=rounded];")
        lines.append("")

        # Add nodes
        for node in self.graph.get_all_nodes():
            node_id = node.id.replace("-", "_").replace(".", "_")

            # Determine color
            color = self.HEALTH_COLORS.get(node.health, "#9E9E9E")

            attrs = [
                f'label="{node.name}"',
                f'fillcolor="{color}"',
                'style="filled,rounded"',
            ]

            if node.id in highlight_set:
                attrs.append('penwidth="3"')
                attrs.append('color="#F44336"')

            # Shape by type
            if "database" in node.service_type.lower():
                attrs.append('shape="cylinder"')
            elif "queue" in node.service_type.lower():
                attrs.append('shape="parallelogram"')

            lines.append(f"    {node_id} [{', '.join(attrs)}];")

        lines.append("")

        # Add edges
        for edge in self.graph.get_all_edges():
            source = edge.source.replace("-", "_").replace(".", "_")
            target = edge.target.replace("-", "_").replace(".", "_")

            attrs = []
            if edge.is_critical:
                attrs.append('color="red"')
                attrs.append('penwidth="2"')

            if edge.dependency_type.value not in ("sync_http", "grpc"):
                attrs.append('style="dashed"')

            attr_str = f" [{', '.join(attrs)}]" if attrs else ""
            lines.append(f"    {source} -> {target}{attr_str};")

        lines.append("}")
        return "\n".join(lines)

    def _render_ascii(self, highlight_nodes: list[str] | None = None) -> str:
        """Render as ASCII art (simplified tree view)."""
        highlight_set = set(highlight_nodes or [])
        lines = []

        # Get root nodes (no incoming edges)
        roots = self.graph.get_root_nodes()

        if not roots:
            roots = list(self.graph.get_all_nodes())[:1]

        visited: set[str] = set()

        def render_node(node_id: str, prefix: str, is_last: bool) -> None:
            if node_id in visited:
                return
            visited.add(node_id)

            node = self.graph.get_node(node_id)
            if not node:
                return

            # Determine connector
            connector = "└── " if is_last else "├── "

            # Determine status indicator
            if node.health == HealthStatus.HEALTHY:
                status = "●"
            elif node.health == HealthStatus.DEGRADED:
                status = "◐"
            elif node.health == HealthStatus.UNHEALTHY:
                status = "○"
            else:
                status = "?"

            # Highlight marker
            highlight = " ⚠" if node_id in highlight_set else ""

            lines.append(f"{prefix}{connector}{status} {node.name}{highlight}")

            # Get dependencies and render them
            deps = self.graph.get_dependencies(node_id)
            new_prefix = prefix + ("    " if is_last else "│   ")

            for i, dep in enumerate(deps):
                is_last_dep = i == len(deps) - 1
                render_node(dep.id, new_prefix, is_last_dep)

        lines.append("Service Dependency Graph")
        lines.append("=" * 40)
        lines.append("")

        for i, root in enumerate(roots):
            is_last_root = i == len(roots) - 1
            render_node(root.id, "", is_last_root)

        return "\n".join(lines)

    def render_blast_radius(
        self,
        failing_service: str,
        impacted_services: list[str],
        format: VisualizationFormat = VisualizationFormat.MERMAID,
    ) -> str | dict[str, Any]:
        """Render graph with blast radius highlighted.

        Args:
            failing_service: The failing service (red)
            impacted_services: Impacted services (orange outline)
            format: Output format

        Returns:
            Rendered visualization
        """
        all_highlighted = [failing_service] + impacted_services
        return self.render(format, highlight_nodes=all_highlighted)


def create_visualizer(
    graph: DependencyGraph,
    **config_kwargs,
) -> GraphVisualizer:
    """Factory function to create a graph visualizer."""
    config = VisualizationConfig(**config_kwargs)
    return GraphVisualizer(graph, config)
