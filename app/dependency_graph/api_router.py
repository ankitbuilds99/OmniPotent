"""Service Dependency Graph API Router.

`app/dependency_graph/` holds the real implementation of three things this product
sells: an auto-discovered service graph, blast-radius analysis, and graph rendering.
Nothing imported it. The package that *was* cited in the gap analysis for blast
radius — `app/risk/blast_radius.py` — is unwired too, and its `BlastRadiusAnalyzer`
(same class name, same 406 lines) is built on a hardcoded ten-service e-commerce
topology with invented request rates. This one takes a real `DependencyGraph` and
does actual breadth-first traversal over it, so this is the implementation worth
having behind an endpoint.

Discovery is explicit, not automatic. `POST /discover` reaches out to Kubernetes,
Consul, Prometheus and Istio; running that on a timer against every tenant would be
a standing load on their infrastructure that nobody asked for, so the graph is
rebuilt only when requested. Sources that are not configured contribute nothing
rather than contributing fabricated services — see `discovery.py`.

Gated on `service_dependency_map` (Growth and up), the same flag that enables the
topology layer of the deduplication pipeline. Unlike deduplication, which degrades
to fewer layers, there is no meaningful free version of a dependency map: without
the flag these endpoints return 402 rather than an emptier graph.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.billing.runtime import current_org_id, enabled_features
from app.dependency_graph.blast_radius import BlastRadiusAnalyzer
from app.dependency_graph.discovery import DiscoveryConfig, ServiceDiscovery
from app.dependency_graph.graph import (
    DependencyEdge,
    DependencyGraph,
    DependencyType,
    ServiceNode,
    create_graph_from_services,
)
from app.dependency_graph.visualizer import GraphVisualizer, VisualizationFormat

log = logging.getLogger("omnipotent.dependency_graph.api")

router = APIRouter(prefix="/dependency-graph", tags=["dependency-graph"])

REQUIRED_FEATURE = "service_dependency_map"

# Derived from the enum rather than written out, so a format added to the renderer
# is servable immediately and one removed stops being advertised. A hand-kept list
# here would drift, which is how `svg` came to be offered by a renderer that raised
# on it.
FORMAT_PATTERN = "^(" + "|".join(f.value for f in VisualizationFormat) + ")$"

# One graph per organization: a shared graph would let one tenant's blast-radius
# query traverse another tenant's services. Process-local, like the other engines
# here — see tests/test_single_replica_until_state_externalised.py.
_graphs: dict[str, DependencyGraph] = {}

# When discovery last ran, per org, so callers can tell a fresh graph from a stale
# one instead of guessing.
_discovered_at: dict[str, datetime] = {}


def reset_graphs() -> None:
    """Drop every per-org graph. Used by tests and process shutdown."""
    _graphs.clear()
    _discovered_at.clear()


async def _require_feature(org: UUID) -> None:
    """402 unless this organization's plan includes the dependency map."""
    features = await enabled_features([REQUIRED_FEATURE], org)
    if not features.get(REQUIRED_FEATURE, False):
        raise HTTPException(
            status_code=402,
            detail={
                "error": "feature_not_available",
                "feature": REQUIRED_FEATURE,
                "message": "Service Dependency Map requires the Growth plan or above",
                "upgrade_url": "/settings/billing/upgrade?plan=growth",
            },
        )


async def _graph_for_request() -> tuple[str, DependencyGraph]:
    """The calling organization's graph, after checking the plan."""
    org = current_org_id()
    await _require_feature(org)
    key = str(org)
    return key, _graphs.setdefault(key, DependencyGraph())


class DiscoverRequest(BaseModel):
    """Which sources to query when rebuilding the graph."""

    namespaces: list[str] = Field(default_factory=lambda: ["default"])
    kubernetes_enabled: bool = True
    kubernetes_label_selector: str | None = None
    consul_enabled: bool = False
    consul_url: str | None = None
    prometheus_enabled: bool = False
    prometheus_url: str | None = None
    istio_enabled: bool = False
    istio_kiali_url: str | None = None
    # Guessing edges from service names is useful for a first look and misleading
    # for impact analysis, so the caller chooses. Off by default: an unlabelled
    # guess in a blast-radius answer is worse than a missing edge.
    infer_dependencies_from_names: bool = False


class NodeRequest(BaseModel):
    """A manually declared service."""

    id: str
    name: str
    namespace: str = "default"
    service_type: str = "api"
    labels: dict[str, str] = Field(default_factory=dict)


class EdgeRequest(BaseModel):
    """A manually declared dependency."""

    source: str
    target: str
    dependency_type: str = "sync_http"


@router.post("/discover")
async def discover(req: DiscoverRequest) -> dict[str, Any]:
    """Rebuild this organization's graph from the configured sources.

    Replaces the previous graph wholesale — a service that no longer exists should
    disappear from impact analysis rather than linger as a phantom dependency.
    """
    key, _ = await _graph_for_request()

    config = DiscoveryConfig(
        kubernetes_enabled=req.kubernetes_enabled,
        kubernetes_namespaces=req.namespaces,
        kubernetes_label_selector=req.kubernetes_label_selector,
        consul_enabled=req.consul_enabled,
        prometheus_enabled=req.prometheus_enabled,
        istio_enabled=req.istio_enabled,
        istio_kiali_url=req.istio_kiali_url,
        auto_detect_dependencies=req.infer_dependencies_from_names,
    )
    # Only override the defaults when the caller actually supplied a URL.
    if req.consul_url:
        config.consul_url = req.consul_url
    if req.prometheus_url:
        config.prometheus_url = req.prometheus_url

    discovery = ServiceDiscovery(config)
    try:
        services = await discovery.discover_all()
    finally:
        await discovery.close()

    graph = create_graph_from_services(services)
    _graphs[key] = graph
    _discovered_at[key] = datetime.now(timezone.utc)

    inferred = sum(1 for s in services if s.metadata.get("inferred_dependencies"))
    return {
        "services_discovered": len(services),
        "nodes": graph.node_count,
        "edges": graph.edge_count,
        "services_with_inferred_edges": inferred,
        "discovered_at": _discovered_at[key].isoformat(),
        "sources": [s.discovery_source.value for s in services],
    }


@router.get("")
async def get_graph() -> dict[str, Any]:
    """The current graph, plus when it was last discovered."""
    key, graph = await _graph_for_request()
    payload = graph.to_dict()
    stamp = _discovered_at.get(key)
    payload["discovered_at"] = stamp.isoformat() if stamp else None
    return payload


@router.post("/nodes")
async def add_node(req: NodeRequest) -> dict[str, Any]:
    """Declare a service by hand, for anything discovery cannot see."""
    _, graph = await _graph_for_request()
    graph.add_node(
        ServiceNode(
            id=req.id,
            name=req.name,
            namespace=req.namespace,
            service_type=req.service_type,
            labels=req.labels,
        )
    )
    return {"nodes": graph.node_count, "edges": graph.edge_count}


@router.post("/edges")
async def add_edge(req: EdgeRequest) -> dict[str, Any]:
    """Declare a dependency by hand."""
    _, graph = await _graph_for_request()

    try:
        dep_type = DependencyType(req.dependency_type)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Unknown dependency_type '{req.dependency_type}'. Expected one of: "
                + ", ".join(d.value for d in DependencyType)
            ),
        ) from None

    if graph.get_node(req.source) is None or graph.get_node(req.target) is None:
        missing = [
            n for n in (req.source, req.target) if graph.get_node(n) is None
        ]
        raise HTTPException(
            status_code=404,
            detail=f"Unknown service(s): {', '.join(missing)}. Add the node first.",
        )

    graph.add_edge(
        DependencyEdge(source=req.source, target=req.target, dependency_type=dep_type)
    )
    return {"nodes": graph.node_count, "edges": graph.edge_count}


@router.get("/blast-radius/{service_id}")
async def blast_radius(
    service_id: str,
    failure_type: str = Query("complete", pattern="^(complete|degraded|latency)$"),
) -> dict[str, Any]:
    """What breaks if `service_id` fails, and what to do about it."""
    _, graph = await _graph_for_request()

    if graph.get_node(service_id) is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Service '{service_id}' is not in the graph. Run POST "
                f"/dependency-graph/discover or add it with POST /dependency-graph/nodes."
            ),
        )

    result = BlastRadiusAnalyzer(graph).analyze(service_id, failure_type)
    return result.to_dict()


@router.get("/single-points-of-failure")
async def single_points_of_failure() -> dict[str, Any]:
    """Services that many others depend on and whose failure has wide impact."""
    _, graph = await _graph_for_request()
    spofs = BlastRadiusAnalyzer(graph).find_single_points_of_failure()
    return {"count": len(spofs), "services": spofs}


@router.get("/cycles")
async def cycles() -> dict[str, Any]:
    """Dependency cycles, which break ordered restarts and deploys."""
    _, graph = await _graph_for_request()
    found = graph.detect_cycles()
    return {"count": len(found), "cycles": found}


@router.get("/visualize")
async def visualize(
    format: str = Query("cytoscape", pattern=FORMAT_PATTERN),
    highlight: str | None = Query(
        None, description="Comma-separated service ids to highlight"
    ),
) -> dict[str, Any]:
    """Render the graph for a UI or a terminal."""
    _, graph = await _graph_for_request()
    highlight_nodes = [h for h in (highlight or "").split(",") if h] or None

    rendered = GraphVisualizer(graph).render(
        VisualizationFormat(format), highlight_nodes
    )
    return {"format": format, "graph": rendered}
