"""The dependency graph must be reachable, plan-gated, and free of invented topology.

`app/dependency_graph/` was unreachable: no router, no importer. Wiring it raised a
second problem — two of its four discovery sources returned hardcoded topology. The
Kubernetes source shipped a fixed list of six services (`api-gateway`, `postgres`,
`redis`, ...) and the Istio source a fixed set of four dependency edges, both
labelled "simulation" in a comment. Served through an endpoint, those become
confident blast-radius answers about infrastructure the customer does not run.

These tests pin: the routes answer, the plan gate holds, and neither discovery
source can invent a service again.
"""

from __future__ import annotations

import ast
import pathlib

import pytest
from uuid import uuid4

from app.billing import runtime as billing_runtime
from app.billing.models import FeatureOverride
from app.dependency_graph import api_router as dg_api
from app.dependency_graph.blast_radius import BlastRadiusAnalyzer, ImpactLevel
from app.dependency_graph.discovery import (
    DiscoveryConfig,
    ServiceDiscovery,
)
from app.dependency_graph.graph import DependencyEdge, DependencyGraph, ServiceNode

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

# The exact fabricated names that used to be returned as real discovered services.
FABRICATED_SERVICE_NAMES = {
    "api-gateway",
    "user-service",
    "order-service",
    "payment-service",
    "postgres",
    "redis",
}


@pytest.fixture(autouse=True)
def _clean_state():
    dg_api.reset_graphs()
    billing_runtime._BILLING_STORE = None
    billing_runtime._BILLING_API = None
    yield
    dg_api.reset_graphs()
    billing_runtime._BILLING_STORE = None
    billing_runtime._BILLING_API = None


async def _entitled_org():
    """An org whose plan includes the dependency map."""
    org = uuid4()
    store = billing_runtime.get_billing_store()
    await store.save_feature_override(
        FeatureOverride(org_id=org, feature_key=dg_api.REQUIRED_FEATURE, enabled=True)
    )
    return org


def _sample_graph() -> DependencyGraph:
    graph = DependencyGraph()
    for node_id in ("web", "orders", "payments", "db"):
        graph.add_node(ServiceNode(id=node_id, name=node_id))
    graph.add_edge(DependencyEdge(source="web", target="orders"))
    graph.add_edge(DependencyEdge(source="orders", target="payments"))
    graph.add_edge(DependencyEdge(source="orders", target="db"))
    graph.add_edge(DependencyEdge(source="payments", target="db"))
    return graph


# --------------------------------------------------------------------------
# No fabricated topology
# --------------------------------------------------------------------------


async def test_kubernetes_discovery_returns_nothing_outside_a_cluster(monkeypatch) -> None:
    """No API server and no token must mean no services — not six invented ones."""
    monkeypatch.delenv("KUBERNETES_SERVICE_HOST", raising=False)
    monkeypatch.delenv("KUBERNETES_SERVICE_PORT", raising=False)
    monkeypatch.delenv("KUBERNETES_SERVICE_PORT_HTTPS", raising=False)

    discovery = ServiceDiscovery(
        DiscoveryConfig(
            kubernetes_enabled=True,
            prometheus_enabled=False,
            auto_detect_dependencies=False,
        )
    )
    try:
        services = await discovery.discover_all()
    finally:
        await discovery.close()

    assert services == [], (
        f"discovery invented {[s.name for s in services]} with no cluster to talk to"
    )


async def test_kubernetes_discovery_reads_the_real_api(monkeypatch, tmp_path) -> None:
    """Services come from the API server's response, whatever it happens to contain."""
    token = tmp_path / "token"
    token.write_text("sa-token")

    captured: dict = {}

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {
                "items": [
                    {
                        "metadata": {
                            "name": "billing-api",
                            "labels": {"tier": "backend"},
                            "annotations": {"owner": "payments"},
                        },
                        "spec": {
                            "ports": [{"port": 8443, "name": "https"}],
                            "clusterIP": "10.0.0.9",
                        },
                    }
                ]
            }

    class _FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def get(self, url, params=None):
            captured["url"] = url
            captured["params"] = params
            return _Resp()

        async def aclose(self):
            return None

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)

    discovery = ServiceDiscovery(
        DiscoveryConfig(
            kubernetes_enabled=True,
            kubernetes_api_url="https://k8s.test",
            kubernetes_token_path=str(token),
            kubernetes_ca_path=str(tmp_path / "missing-ca.crt"),
            kubernetes_namespaces=["prod"],
            prometheus_enabled=False,
            auto_detect_dependencies=False,
        )
    )
    services = await discovery.discover_all()

    assert [s.name for s in services] == ["billing-api"]
    assert services[0].namespace == "prod"
    assert services[0].metadata["cluster_ip"] == "10.0.0.9"
    assert services[0].annotations == {"owner": "payments"}
    assert captured["url"] == "/api/v1/namespaces/prod/services"
    assert captured["headers"]["Authorization"] == "Bearer sa-token"


async def test_kubernetes_client_is_not_built_without_a_token(monkeypatch, tmp_path) -> None:
    """A missing ServiceAccount token must not fall back to an unauthenticated read."""
    discovery = ServiceDiscovery(
        DiscoveryConfig(
            kubernetes_api_url="https://k8s.test",
            kubernetes_token_path=str(tmp_path / "absent"),
        )
    )
    assert await discovery._k8s_client() is None
    await discovery.close()


async def test_istio_returns_nothing_without_kiali() -> None:
    """The four hardcoded edges must not come back when the mesh is unconfigured."""
    discovery = ServiceDiscovery(DiscoveryConfig(istio_enabled=True))
    try:
        assert await discovery._discover_istio_dependencies() == {}
    finally:
        await discovery.close()


async def test_istio_parses_the_kiali_graph(monkeypatch) -> None:
    """Edges come from Kiali's cytoscape payload, resolved from node ids to names."""

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {
                "elements": {
                    "nodes": [
                        {"data": {"id": "n1", "service": "checkout"}},
                        {"data": {"id": "n2", "service": "ledger"}},
                    ],
                    "edges": [{"data": {"source": "n1", "target": "n2"}}],
                }
            }

    discovery = ServiceDiscovery(
        DiscoveryConfig(istio_enabled=True, istio_kiali_url="https://kiali.test")
    )

    async def _get(url, params=None):
        return _Resp()

    monkeypatch.setattr(discovery._client, "get", _get)
    try:
        deps = await discovery._discover_istio_dependencies()
    finally:
        await discovery.close()

    assert deps == {"checkout": ["ledger"]}


def test_no_hardcoded_service_names_in_the_discovery_paths() -> None:
    """Guard the specific regression: fabricated names back in a discovery method.

    Written against the source rather than behaviour because the fabrication was a
    literal list inside a method that stood in for a real API call — a behaviour
    test only catches it if it happens to exercise that exact path.

    Scoped to the two methods that fetch topology. The same names appear elsewhere
    in this module as *inference patterns* (`.*-service$` -> postgres, redis), which
    is a labelled guess recorded under `inferred_dependencies`, not a claim that the
    service was observed. Those are legitimate; a name baked into a fetch path is not.
    """
    source = (REPO_ROOT / "app/dependency_graph/discovery.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    fetchers = {"_get_k8s_services", "_discover_istio_dependencies"}
    checked = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
            continue
        if node.name not in fetchers:
            continue
        checked.add(node.name)
        literals = {
            child.value
            for child in ast.walk(node)
            if isinstance(child, ast.Constant) and isinstance(child.value, str)
        }
        fabricated = FABRICATED_SERVICE_NAMES & literals
        assert not fabricated, (
            f"{sorted(fabricated)} are back as string literals in {node.name}(); these "
            f"were the invented services that discovery returned as if observed"
        )

    assert checked == fetchers, f"discovery methods went missing: {fetchers - checked}"


# --------------------------------------------------------------------------
# Reachability and plan gating
# --------------------------------------------------------------------------


def test_app_serves_the_dependency_graph_routes() -> None:
    from fastapi.testclient import TestClient

    from app.api import main as api

    with TestClient(api.app) as client:
        # No plan flag for the default single-tenant org -> a clear 402, which is
        # itself proof the route is registered and the gate runs.
        resp = client.get("/api/dependency-graph")
    assert resp.status_code == 402, resp.text
    assert resp.json()["detail"]["feature"] == dg_api.REQUIRED_FEATURE


async def test_gate_blocks_without_the_flag() -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await dg_api._require_feature(uuid4())
    assert exc.value.status_code == 402
    assert "upgrade_url" in exc.value.detail


async def test_gate_allows_with_the_flag() -> None:
    org = await _entitled_org()
    await dg_api._require_feature(org)  # must not raise


async def test_each_org_gets_its_own_graph() -> None:
    dg_api._graphs["a"] = _sample_graph()
    dg_api._graphs["b"] = DependencyGraph()
    assert dg_api._graphs["a"] is not dg_api._graphs["b"]
    assert dg_api._graphs["b"].node_count == 0


# --------------------------------------------------------------------------
# The analysis this exists for
# --------------------------------------------------------------------------


def test_blast_radius_walks_the_real_graph() -> None:
    """`db` failing must reach `orders` directly and `web` transitively."""
    result = BlastRadiusAnalyzer(_sample_graph()).analyze("db")

    impacted = {s.service_id for s in result.impacted_services}
    assert {"orders", "payments", "web"} <= impacted

    by_id = {s.service_id: s for s in result.impacted_services}
    assert by_id["orders"].impact_type == "direct"
    assert by_id["web"].impact_type == "transitive"
    assert by_id["web"].distance > by_id["orders"].distance
    assert result.impact_level is not ImpactLevel.NONE
    assert result.mitigation_suggestions


def test_blast_radius_of_a_leaf_impacts_nobody() -> None:
    result = BlastRadiusAnalyzer(_sample_graph()).analyze("web")
    assert result.total_impacted == 0
    assert result.impact_level is ImpactLevel.NONE


def test_cycles_are_detected() -> None:
    graph = _sample_graph()
    graph.add_edge(DependencyEdge(source="db", target="web"))
    assert graph.detect_cycles(), "a web->orders->db->web cycle went undetected"


def test_visualizer_renders_every_offered_format() -> None:
    """Every member of the enum must render — offering a format that raises is a lie.

    `SVG` was such a member: advertised, with no branch in `render`, so asking for it
    raised ValueError. It was removed rather than implemented (SVG needs graphviz).
    """
    from app.dependency_graph.visualizer import GraphVisualizer, VisualizationFormat

    visualizer = GraphVisualizer(_sample_graph())
    for fmt in VisualizationFormat:
        rendered = visualizer.render(fmt)
        assert rendered, f"{fmt.value} rendered empty"


def test_every_renderable_format_is_accepted_by_the_route() -> None:
    """A format the renderer supports must not be rejected by the route's validator."""
    import re

    from app.dependency_graph.visualizer import VisualizationFormat

    for fmt in VisualizationFormat:
        assert re.match(dg_api.FORMAT_PATTERN, fmt.value), (
            f"{fmt.value} renders but /visualize rejects it: {dg_api.FORMAT_PATTERN}"
        )
    assert not re.match(dg_api.FORMAT_PATTERN, "svg")


async def test_discover_replaces_rather_than_accumulates(monkeypatch) -> None:
    """A service that disappears must leave the graph, not linger as a phantom."""
    org = await _entitled_org()
    key = str(org)
    dg_api._graphs[key] = _sample_graph()
    assert dg_api._graphs[key].node_count == 4

    class _EmptyDiscovery:
        def __init__(self, config):
            pass

        async def discover_all(self):
            return []

        async def close(self):
            return None

    monkeypatch.setattr(dg_api, "ServiceDiscovery", _EmptyDiscovery)
    monkeypatch.setattr(dg_api, "current_org_id", lambda: org)

    result = await dg_api.discover(dg_api.DiscoverRequest())

    assert result["nodes"] == 0
    assert dg_api._graphs[key].node_count == 0


async def test_name_based_inference_is_off_by_default() -> None:
    """Guessed edges are misleading in impact analysis, so the caller opts in."""
    assert dg_api.DiscoverRequest().infer_dependencies_from_names is False


async def test_inferred_edges_are_labelled() -> None:
    """A consumer must be able to tell a guessed edge from an observed one."""
    from app.dependency_graph.discovery import DiscoveredService, ServiceType

    discovery = ServiceDiscovery(DiscoveryConfig())
    services = [
        DiscoveredService(name="orders-service", namespace="p", service_type=ServiceType.API),
        DiscoveredService(name="postgres", namespace="p", service_type=ServiceType.DATABASE),
    ]
    await discovery._detect_dependencies(services)
    await discovery.close()

    orders = services[0]
    assert "postgres" in orders.dependencies
    assert orders.metadata["inferred_dependencies"] == ["postgres"], (
        "guessed edges are indistinguishable from measured ones"
    )
