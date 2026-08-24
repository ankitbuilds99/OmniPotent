"""Per-tenant feature stores — the isolation that `scoped()` is there to provide.

The remaining feature routers keep their state in process memory with no org
column, so the only thing stopping tenant B from reading tenant A's runbooks and
task graphs is that they hold different instances. These tests pin that: same
org ⇒ same instance, different org ⇒ different instance, no tenant ⇒ the shared
single-tenant bucket.

Incidents, runbooks, the knowledge base and the status page are not here any
more — they moved to tables, where isolation is an org_id filter rather than an
instance. See tests/test_incidents_store.py, tests/test_runbooks_store.py,
tests/test_knowledge_base_store.py and tests/test_status_page_store.py.
"""

from __future__ import annotations

import pytest

from app.planning.api_router import _graphs
from app.teams.store import get_team_store
from app.tenancy.scoping import ScopedRegistry, scoped
from app.tenant_context import TenantConfig, reset_tenant, set_tenant


class _Counter:
    """Factory that records how many instances were built."""

    built = 0

    def __init__(self) -> None:
        type(self).built += 1


@pytest.fixture
def as_org():
    """Run a block as a given org, restoring the previous context afterwards."""
    tokens = []

    def _enter(org_id: str | None):
        cfg = TenantConfig(org_id=org_id) if org_id is not None else None
        tokens.append(set_tenant(cfg))

    yield _enter

    for token in reversed(tokens):
        reset_tenant(token)


def test_same_org_reuses_one_instance(as_org):
    _Counter.built = 0
    registry = ScopedRegistry(_Counter)

    as_org("acme")
    first = registry.get()
    second = registry.get()

    assert first is second
    assert _Counter.built == 1


def test_different_orgs_never_share_an_instance():
    registry = ScopedRegistry(_Counter)

    a = registry.for_org("acme")
    b = registry.for_org("umbrella")

    assert a is not b
    assert registry.for_org("acme") is a  # and A keeps its own across lookups


def test_no_tenant_falls_back_to_one_shared_bucket(as_org):
    """Single-tenant deployments and background jobs have no org; they must all
    land on the same instance or the existing non-tenant behaviour breaks."""
    registry = ScopedRegistry(_Counter)

    as_org(None)
    first = registry.get()
    second = registry.for_org("")

    assert first is second


def test_capacity_evicts_least_recently_used_org():
    registry = ScopedRegistry(_Counter, max_tenants=2)

    a = registry.for_org("a")
    registry.for_org("b")
    registry.for_org("a")  # touch A so B is now the least recently used
    registry.for_org("c")  # evicts B, not A

    assert len(registry) == 2
    assert registry.for_org("a") is a  # A survived
    assert registry.for_org("b") is not a  # B was rebuilt from scratch


def test_scoped_registers_for_reset():
    """`scoped()` returns a working registry and records it for reset_all()."""
    from app.tenancy import scoping

    registry = scoped(_Counter)
    assert registry in scoping._REGISTRIES

    registry.for_org("acme")
    assert len(registry) == 1
    scoping.reset_all()
    assert len(registry) == 0


# --- the real stores, not just the mechanism ---------------------------------


@pytest.mark.parametrize(
    "get_store",
    [
        pytest.param(get_team_store, id="teams"),
        pytest.param(_graphs, id="planning_graphs"),
    ],
)
def test_feature_stores_are_per_org(get_store, as_org):
    as_org("acme")
    acme = get_store()

    reset_tenant(set_tenant(TenantConfig(org_id="acme")))  # no-op, same org
    assert get_store() is acme

    as_org("umbrella")
    assert get_store() is not acme
