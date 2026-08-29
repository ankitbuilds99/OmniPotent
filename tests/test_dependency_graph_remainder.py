"""Two follow-ups to the dependency-name resolution fix in `create_graph_from_services`.

The first fix stopped discovered edges from dangling, but it resolved names against
the graph it was still building. External placeholder nodes are keyed by the bare
dependency name, so the placeholder stood up for an ambiguous `postgres` answered
`get_node("postgres")` for every service processed afterwards: a `prod` service
depending on `postgres` then pointed at the placeholder instead of `postgres.prod`,
and the real database was back to in_degree 0 and an empty blast radius. Whether
that happened was decided by the order discovery returned services in, which the
caller controls through `kubernetes_namespaces` without knowing it.

The second is that distinct dependency strings can name one relation (`postgres`
and `postgres.default`). Edges are keyed by (source, target) and `add_edge` is
last-write-wins, so one edge per string meant the last string's labels silently
replaced the earlier ones — an observed edge could end up marked `inferred`.

These tests pin resolution as a function of the discovered set alone, and pin that
a merged edge still reports every string that declared it.
"""

from __future__ import annotations

from itertools import permutations
from uuid import uuid4

from app.billing import runtime as billing_runtime
from app.billing.models import FeatureOverride
from app.dependency_graph import api_router as dg_api
from app.dependency_graph.discovery import DiscoveredService, ServiceType
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


def _ambiguous_estate() -> list[DiscoveredService]:
    """`postgres` in two namespaces, one depender in each of edge and prod."""
    return [
        _svc("gateway", namespace="edge", dependencies=["postgres"]),
        _svc("orders-service", namespace="prod", dependencies=["postgres"]),
        _svc("postgres", namespace="prod", service_type=ServiceType.DATABASE),
        _svc("postgres", namespace="staging", service_type=ServiceType.DATABASE),
    ]


def _topology(graph) -> set[tuple[str, str]]:
    return {(e.source, e.target) for e in graph.get_all_edges()}


def test_resolution_is_a_function_of_the_discovered_set_not_its_order() -> None:
    """Every ordering of the same services must produce the same topology."""
    results = {}
    for order in permutations(range(len(_ambiguous_estate()))):
        estate = _ambiguous_estate()
        graph = create_graph_from_services([estate[i] for i in order])
        results[order] = (
            frozenset(_topology(graph)),
            frozenset(n.id for n in graph.get_all_nodes()),
            graph.get_node("postgres.prod").in_degree,
        )

    distinct = set(results.values())
    assert len(distinct) == 1, (
        "the same services produced different graphs depending on their order: "
        f"{sorted(str(v) for v in distinct)}"
    )


def test_an_external_placeholder_does_not_shadow_a_later_in_namespace_name() -> None:
    """The placeholder for an ambiguous name is not a resolution target."""
    estate = _ambiguous_estate()  # gateway.edge (ambiguous) is processed first.

    graph = create_graph_from_services(estate)

    assert _topology(graph) == {
        ("gateway.edge", "postgres"),
        ("orders-service.prod", "postgres.prod"),
    }
    assert graph.get_node("postgres").metadata["unresolved_dependency"] is True
    assert graph.get_node("postgres.prod").in_degree == 1
    assert graph.get_node("postgres.prod").criticality_score > 0


def test_two_strings_naming_one_relation_keep_both_labels() -> None:
    """A merged edge reports every dependency string that produced it."""
    services = [
        _svc(
            "orders-service",
            dependencies=["postgres", "postgres.default"],
            metadata={"inferred_dependencies": ["postgres.default"]},
        ),
        _svc("postgres", service_type=ServiceType.DATABASE),
    ]

    graph = create_graph_from_services(services)

    assert graph.edge_count == 1
    edge = graph.get_edge("orders-service.default", "postgres.default")
    assert edge is not None
    assert edge.metadata["declared_as"] == "postgres"
    assert edge.metadata["also_declared_as"] == ["postgres.default"], (
        "the second declaration was dropped instead of recorded"
    )
    # One observed declaration makes the relation observed; the guess only adds a
    # second name for something traffic already showed.
    assert edge.metadata["inferred"] is False
    assert edge.to_dict()["metadata"]["also_declared_as"] == ["postgres.default"]


def test_a_relation_declared_only_by_guesses_stays_inferred() -> None:
    """Merging declarations must not launder a guess into an observation."""
    services = [
        _svc(
            "orders-service",
            dependencies=["postgres", "postgres.default"],
            metadata={"inferred_dependencies": ["postgres", "postgres.default"]},
        ),
        _svc("postgres", service_type=ServiceType.DATABASE),
    ]

    graph = create_graph_from_services(services)
    edge = graph.get_edge("orders-service.default", "postgres.default")

    assert edge.metadata["inferred"] is True
    assert edge.metadata["also_declared_as"] == ["postgres.default"]


def test_a_repeated_dependency_string_is_not_reported_as_two_declarations() -> None:
    """The same name twice is one declaration, not a merged pair."""
    services = [
        _svc("orders-service", dependencies=["postgres", "postgres"]),
        _svc("postgres", service_type=ServiceType.DATABASE),
    ]

    graph = create_graph_from_services(services)
    edge = graph.get_edge("orders-service.default", "postgres.default")

    assert graph.edge_count == 1
    assert edge.metadata["also_declared_as"] == []


@pytest.mark.parametrize("ambiguous_first", [True, False])
async def test_discover_then_blast_radius_agree_whatever_the_scan_order(
    monkeypatch, ambiguous_first: bool
) -> None:
    """End to end: the namespace scan order must not change what /discover builds."""
    org = await _entitled_org()

    estate = _ambiguous_estate()
    gateway, orders, pg_prod, pg_staging = estate
    discovered = (
        [gateway, orders, pg_prod, pg_staging]
        if ambiguous_first
        else [orders, gateway, pg_prod, pg_staging]
    )

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
    assert summary["edges"] == 2

    impact = await dg_api.blast_radius("postgres.prod")
    assert impact["total_impacted"] == 1, (
        "the depender in postgres.prod's own namespace was resolved onto the "
        "placeholder created for an unrelated ambiguous dependency"
    )

    payload = await dg_api.get_graph()
    node_ids = {n["id"] for n in payload["nodes"]}
    for edge in payload["edges"]:
        assert edge["source"] in node_ids and edge["target"] in node_ids
