"""Discovered graphs must not contain edges pointing at nodes that do not exist.

`create_graph_from_services` keyed nodes by FQDN (`postgres.default`) but took edge
targets straight from `service.dependencies`, which every producer fills with bare
service names (`postgres`). The result was a graph whose edge count looked healthy
while every traversal quietly skipped the dangling targets: blast radius zero, no
single points of failure, no cycles, in_degree zero on the busiest database in the
estate. Nothing in tests/test_dependency_graph_wired.py caught it because its
`_sample_graph()` uses node ids that already equal names.

These tests pin the resolution rules: dependencies land on real node ids, a name
that matches nothing discovered becomes an explicit and labelled external node,
and no discovered graph is allowed to carry a dangling endpoint.
"""

from __future__ import annotations

from uuid import uuid4

from app.billing import runtime as billing_runtime
from app.billing.models import FeatureOverride
from app.dependency_graph import api_router as dg_api
from app.dependency_graph.blast_radius import BlastRadiusAnalyzer, ImpactLevel
from app.dependency_graph.discovery import (
    DiscoveredService,
    DiscoveryConfig,
    ServiceDiscovery,
    ServiceType,
)
from app.dependency_graph.graph import create_graph_from_services

import pytest


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
    org = uuid4()
    store = billing_runtime.get_billing_store()
    await store.save_feature_override(
        FeatureOverride(org_id=org, feature_key=dg_api.REQUIRED_FEATURE, enabled=True)
    )
    return org


def _svc(name: str, namespace: str = "default", **kwargs) -> DiscoveredService:
    return DiscoveredService(
        name=name,
        namespace=namespace,
        service_type=kwargs.pop("service_type", ServiceType.API),
        **kwargs,
    )


def _assert_no_dangling_edges(graph) -> None:
    ids = {n.id for n in graph.get_all_nodes()}
    dangling = [
        (e.source, e.target)
        for e in graph.get_all_edges()
        if e.source not in ids or e.target not in ids
    ]
    assert not dangling, f"edges reference ids no node has: {dangling}"


def test_bare_dependency_names_resolve_to_fqdn_node_ids() -> None:
    """A Kiali-style dependency on `postgres` must reach node `postgres.default`."""
    services = [
        _svc("orders-service", dependencies=["postgres", "redis"]),
        _svc("postgres", service_type=ServiceType.DATABASE),
        _svc("redis", service_type=ServiceType.CACHE),
    ]

    graph = create_graph_from_services(services)

    _assert_no_dangling_edges(graph)
    assert {e.target for e in graph.get_all_edges()} == {
        "postgres.default",
        "redis.default",
    }
    assert {n.id for n in graph.get_dependencies("orders-service.default")} == {
        "postgres.default",
        "redis.default",
    }
    assert [n.id for n in graph.get_dependents("postgres.default")] == [
        "orders-service.default"
    ]
    assert graph.get_node("postgres.default").in_degree == 1
    assert graph.get_node("postgres.default").criticality_score > 0


def test_blast_radius_of_a_shared_database_is_not_zero() -> None:
    """The headline analysis must see the estate that depends on the database."""
    dependants = [
        "orders-service",
        "billing-service",
        "shipping-service",
        "search-service",
        "audit-service",
    ]
    services = [_svc(name, dependencies=["postgres"]) for name in dependants]
    services.append(_svc("postgres", service_type=ServiceType.DATABASE))

    graph = create_graph_from_services(services)
    result = BlastRadiusAnalyzer(graph).analyze("postgres.default")

    assert result.total_impacted == len(dependants), (
        "a database the whole estate depends on reported an empty blast radius"
    )
    assert result.impact_level is not ImpactLevel.NONE
    # find_single_points_of_failure only considers nodes with three or more
    # dependents, and only reports them at HIGH impact (five impacted services).
    spofs = BlastRadiusAnalyzer(graph).find_single_points_of_failure()
    assert "postgres" in {s["service"] for s in spofs}


def test_a_dependency_resolves_inside_its_own_namespace_first() -> None:
    """Two namespaces each running `postgres` must not be collapsed into one."""
    services = [
        _svc("orders-service", namespace="prod", dependencies=["postgres"]),
        _svc("orders-service", namespace="staging", dependencies=["postgres"]),
        _svc("postgres", namespace="prod", service_type=ServiceType.DATABASE),
        _svc("postgres", namespace="staging", service_type=ServiceType.DATABASE),
    ]

    graph = create_graph_from_services(services)

    _assert_no_dangling_edges(graph)
    assert graph.get_edge("orders-service.prod", "postgres.prod") is not None
    assert graph.get_edge("orders-service.staging", "postgres.staging") is not None
    assert graph.get_edge("orders-service.prod", "postgres.staging") is None


def test_an_unknown_dependency_becomes_a_labelled_external_node() -> None:
    """A dependency discovery never saw is recorded, with the reason, not dropped."""
    services = [_svc("orders-service", dependencies=["stripe-api"])]

    graph = create_graph_from_services(services)

    _assert_no_dangling_edges(graph)
    external = graph.get_node("stripe-api")
    assert external is not None, "the edge would dangle without the external node"
    assert external.service_type == "external"
    assert external.metadata["unresolved_dependency"] is True
    assert "not among the discovered services" in external.metadata["reason"]
    assert external.to_dict()["metadata"]["external"] is True
    assert [n.id for n in graph.get_dependencies("orders-service.default")] == [
        "stripe-api"
    ]


@pytest.mark.parametrize("ambiguous_first", [True, False])
def test_an_ambiguous_cross_namespace_name_is_not_guessed(ambiguous_first: bool) -> None:
    """When a bare name exists in two namespaces and neither is the caller's, say so.

    The second depender lives in one of the ambiguous namespaces and must still
    resolve inside it. Both orders are exercised because the external placeholder
    stood up for the ambiguous name is keyed by the bare name: resolution that
    consulted the graph would match that placeholder for whichever depender came
    after it, handing `postgres.prod` an in_degree of 0 in one order and 1 in the
    other from the same set of services.
    """
    ambiguous = _svc("gateway", namespace="edge", dependencies=["postgres"])
    unambiguous = _svc("orders-service", namespace="prod", dependencies=["postgres"])
    dependers = [ambiguous, unambiguous] if ambiguous_first else [unambiguous, ambiguous]
    services = [
        *dependers,
        _svc("postgres", namespace="prod", service_type=ServiceType.DATABASE),
        _svc("postgres", namespace="staging", service_type=ServiceType.DATABASE),
    ]

    graph = create_graph_from_services(services)

    _assert_no_dangling_edges(graph)
    assert graph.get_edge("gateway.edge", "postgres.prod") is None
    assert graph.get_edge("gateway.edge", "postgres.staging") is None
    unresolved = graph.get_node("postgres")
    assert unresolved is not None
    assert "2 namespaces" in unresolved.metadata["reason"]

    assert graph.get_edge("orders-service.prod", "postgres.prod") is not None, (
        "an unrelated ambiguous dependency stole a resolvable in-namespace one"
    )
    assert graph.get_edge("orders-service.prod", "postgres") is None
    assert [n.id for n in graph.get_dependents("postgres.prod")] == [
        "orders-service.prod"
    ]
    assert graph.get_node("postgres.prod").in_degree == 1


def test_a_dependency_on_itself_does_not_invent_a_cycle() -> None:
    """Resolving a name back to its own node must not create a one-node cycle."""
    services = [_svc("postgres", dependencies=["postgres", "postgres.default"])]

    graph = create_graph_from_services(services)

    assert graph.edge_count == 0
    assert graph.detect_cycles() == []


async def test_inferred_edges_stay_labelled_in_the_graph() -> None:
    """A guessed edge must still read as a guess after it reaches the graph."""
    discovery = ServiceDiscovery(DiscoveryConfig())
    services = [
        _svc("orders-service", namespace="p"),
        _svc("postgres", namespace="p", service_type=ServiceType.DATABASE),
    ]
    await discovery._detect_dependencies(services)
    await discovery.close()

    graph = create_graph_from_services(services)

    edge = graph.get_edge("orders-service.p", "postgres.p")
    assert edge is not None, "name inference produced an edge that reaches no node"
    assert edge.metadata["inferred"] is True
    assert edge.to_dict()["metadata"]["declared_as"] == "postgres"


async def test_discover_endpoint_produces_a_traversable_graph(monkeypatch) -> None:
    """End to end: /discover then /blast-radius must agree about the topology."""
    org = await _entitled_org()

    discovered = [
        _svc("orders-service", dependencies=["postgres", "redis"]),
        _svc("billing-service", dependencies=["postgres"]),
        _svc("postgres", service_type=ServiceType.DATABASE),
        _svc("redis", service_type=ServiceType.CACHE),
    ]

    class _StubDiscovery:
        def __init__(self, config):
            pass

        async def discover_all(self):
            return discovered

        async def close(self):
            return None

    monkeypatch.setattr(dg_api, "ServiceDiscovery", _StubDiscovery)
    monkeypatch.setattr(dg_api, "current_org_id", lambda: org)

    summary = await dg_api.discover(dg_api.DiscoverRequest())
    assert summary["edges"] == 3

    _assert_no_dangling_edges(dg_api._graphs[str(org)])

    impact = await dg_api.blast_radius("postgres.default")
    assert impact["total_impacted"] == 2, (
        "the endpoint reported edges it could not then traverse"
    )
    assert impact["impact_level"] != ImpactLevel.NONE.value

    payload = await dg_api.get_graph()
    node_ids = {n["id"] for n in payload["nodes"]}
    for edge in payload["edges"]:
        assert edge["source"] in node_ids and edge["target"] in node_ids
