"""Tenant isolation for the digital-twin engines behind /v1/digital-twin/*.

The five providers in app/api/v1/digital_twin.py used to hand every request the
same process-global engine instance, and each engine keeps its records in plain
instance dicts that the listing routes return whole. Mounting /v1 turned that
into a live cross-tenant leak: org B's `GET /rollback/scenarios` returned org A's
scenarios, and the ids in that listing are ids org B can act on — these handlers
are not models of infrastructure, `ChaosEngine._inject_pod_kill` calls
`KubernetesConnector.delete_pod` against the live cluster and
`InfrastructureCloner` POSTs real namespaces, deployments and network policies.

Everything below drives the real ASGI app with real per-org API keys, because the
leak only exists on the request path: the engines themselves hold no module-level
state, so a unit test on `RollbackSimulator` could never have caught it.

Fixture shape follows tests/test_api_v1_mount.py — same reset, same real tenant
store in tmp_path.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import app.api.main as api
import app.api.v1.digital_twin as twin_api
import app.config as cfg
from app import runtime, tenant_context
from app.database import engine as db_engine
from app.tenancy import auth as tenant_auth


def _reset() -> None:
    cfg.get_settings.cache_clear()
    tenant_auth.reset_store_cache()
    tenant_context.current_tenant.set(None)
    api._RL.clear()
    runtime.reset()
    db_engine._engine = None
    db_engine._session_factory = None
    # The engine registry outlives a TestClient, so a test that did not clear it
    # would inherit whatever an earlier test in this process created and pass or
    # fail depending on collection order.
    twin_api._ENGINES.clear()


async def _provision_two_orgs(store):
    await store.setup()
    org_a = await store.create_org("Alpha", plan="team")
    org_b = await store.create_org("Beta", plan="team")
    # `admin` on both sides: the point is that a seat which is fully entitled
    # inside its own org still cannot see across the boundary. A viewer refusal
    # would only re-prove the RBAC gate that tests/test_api_v1_mount.py covers.
    key_a, _ = await store.issue_api_key(org_a.id, "k-a", role="admin")
    key_b, _ = await store.issue_api_key(org_b.id, "k-b", role="admin")
    return {"org_a": org_a.id, "org_b": org_b.id, "a": key_a, "b": key_b}


@pytest.fixture
def two_orgs(tmp_path, monkeypatch):
    monkeypatch.setenv("COPILOT_MULTI_TENANT", "true")
    monkeypatch.setenv("COPILOT_TENANT_DB", str(tmp_path / "tenants.sqlite"))
    monkeypatch.setenv("COPILOT_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/app.db")
    _reset()
    data = asyncio.run(_provision_two_orgs(tenant_auth.get_store()))
    with TestClient(api.app) as c:
        data["client"] = c
        yield data
    _reset()


@pytest.fixture
def single_tenant(tmp_path, monkeypatch):
    """The offline-demo default: auth off, no tenant ever on the context."""
    monkeypatch.setenv("COPILOT_MULTI_TENANT", "false")
    monkeypatch.delenv("COPILOT_API_TOKEN", raising=False)
    monkeypatch.setenv("COPILOT_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/app.db")
    _reset()
    with TestClient(api.app) as c:
        yield c
    _reset()


def _auth(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _json(response):
    """Decode, having first proved the body came from a route.

    A `text/html` body is the SPA catch-all answering for an unrecognised path,
    which is how twenty unmounted endpoints looked healthy for so long.
    """
    ctype = response.headers.get("content-type", "")
    assert ctype.startswith("application/json"), (
        f"{response.request.method} {response.request.url.path} answered "
        f"{response.status_code} {ctype!r} — an HTML body means the SPA fallback "
        f"served it and no route matched"
    )
    return response.json()


def _seed(client, key: str, marker: str) -> dict[str, str]:
    """Create one of each kind of twin record under `key`, and return their ids.

    Chosen so nothing here touches a real cluster or the wall clock: the what-if
    simulation clones nothing and predicts nothing, the clone runs against a
    Kubernetes connector with no cluster (it produces a zero-resource
    environment), and the chaos experiment stays `pending` — created, never run.
    """
    scenario = _json(client.post(
        "/v1/digital-twin/rollback/scenario",
        json={
            "deployment_id": f"deploy-{marker}",
            "service": f"svc-{marker}",
            "from_version": "2",
            "to_version": "1",
        },
        headers=_auth(key),
    ))
    simulation = _json(client.post(
        "/v1/digital-twin/simulate",
        json={
            "simulation_type": "what_if",
            "target_service": f"svc-{marker}",
            "clone_environment": False,
            "predict_downtime": False,
            "run_chaos": False,
        },
        headers=_auth(key),
    ))
    environment = _json(client.post(
        "/v1/digital-twin/clone",
        json={"source_namespace": f"ns-{marker}"},
        headers=_auth(key),
    ))
    experiment = _json(client.post(
        "/v1/digital-twin/chaos/experiment",
        json={
            "name": f"exp-{marker}",
            "failures": [{"type": "pod_kill", "percentage": 10.0, "duration_seconds": 5}],
            "target_selector": {"app": marker},
        },
        headers=_auth(key),
    ))
    return {
        "scenario": scenario["id"],
        "simulation": simulation["id"],
        "environment": environment["id"],
        "experiment": experiment["id"],
    }


def _listings(client, key: str) -> dict[str, list]:
    return {
        "scenario": _json(client.get(
            "/v1/digital-twin/rollback/scenarios", headers=_auth(key)))["scenarios"],
        "simulation": _json(client.get(
            "/v1/digital-twin/simulations", headers=_auth(key)))["simulations"],
        "environment": _json(client.get(
            "/v1/digital-twin/clone", headers=_auth(key)))["environments"],
        "experiment": _json(client.get(
            "/v1/digital-twin/chaos/experiments", headers=_auth(key)))["experiments"],
    }


def test_listings_return_only_the_calling_orgs_records(two_orgs):
    """Every listing route returns the whole engine's contents, so this is the
    route by which org A's records reached org B."""
    client = two_orgs["client"]
    ids_a = _seed(client, two_orgs["a"], "alpha")
    ids_b = _seed(client, two_orgs["b"], "beta")

    for key, mine, theirs in (
        (two_orgs["a"], ids_a, ids_b),
        (two_orgs["b"], ids_b, ids_a),
    ):
        listed = _listings(client, key)
        for kind, records in listed.items():
            found = {r["id"] for r in records}
            assert mine[kind] in found, f"{kind}: an org lost sight of its own record"
            assert theirs[kind] not in found, f"{kind}: leaked another org's record"
            # Nothing else got in either, so this is isolation and not a filter
            # that happens to drop one id.
            assert found == {mine[kind]}, f"{kind}: unexpected records {found}"


def test_counts_match_the_records_actually_returned(two_orgs):
    """The `count` each listing reports is the length of the scoped list, not the
    engine's unscoped total — a count taken before filtering would tell org B how
    many records org A holds."""
    client = two_orgs["client"]
    _seed(client, two_orgs["a"], "alpha")
    _seed(client, two_orgs["b"], "beta")

    for path, field in (
        ("/v1/digital-twin/rollback/scenarios", "scenarios"),
        ("/v1/digital-twin/simulations", "simulations"),
        ("/v1/digital-twin/clone", "environments"),
        ("/v1/digital-twin/chaos/experiments", "experiments"),
    ):
        body = _json(client.get(path, headers=_auth(two_orgs["b"])))
        assert body["count"] == len(body[field]) == 1, f"{path} -> {body['count']}"


def test_another_orgs_record_cannot_be_read_by_id(two_orgs):
    """Ids are short (an 8-char uuid prefix) and were previously handed out in
    listings, so the id lookups have to refuse on their own."""
    client = two_orgs["client"]
    ids_a = _seed(client, two_orgs["a"], "alpha")

    for path in (
        f"/v1/digital-twin/simulate/{ids_a['simulation']}",
        f"/v1/digital-twin/clone/{ids_a['environment']}",
    ):
        r = client.get(path, headers=_auth(two_orgs["b"]))
        assert r.status_code == 404, f"{path} -> {r.status_code} {r.text[:200]}"
        # The owner still reads it, so the 404 is the tenant boundary and not a
        # record that failed to be created.
        assert client.get(path, headers=_auth(two_orgs["a"])).status_code == 200


def test_another_orgs_record_cannot_be_acted_on(two_orgs):
    """The destructive half. `DELETE /clone/{id}` tears down the real namespace,
    deployments and network policies the clone created, and
    `POST /rollback/simulate/{id}` runs another org's rollback plan."""
    client = two_orgs["client"]
    ids_a = _seed(client, two_orgs["a"], "alpha")

    r = client.post(
        f"/v1/digital-twin/rollback/simulate/{ids_a['scenario']}", headers=_auth(two_orgs["b"])
    )
    assert r.status_code == 404, r.text[:200]

    r = client.delete(f"/v1/digital-twin/clone/{ids_a['environment']}", headers=_auth(two_orgs["b"]))
    assert r.status_code == 404, r.text[:200]

    # Org A's environment survived org B's delete, and org A can still act on it.
    assert client.get(
        f"/v1/digital-twin/clone/{ids_a['environment']}", headers=_auth(two_orgs["a"])
    ).status_code == 200
    assert client.delete(
        f"/v1/digital-twin/clone/{ids_a['environment']}", headers=_auth(two_orgs["a"])
    ).status_code == 200


def test_cleanup_does_not_reach_another_orgs_environments(two_orgs):
    """`POST /clone/cleanup` deletes every *expired* environment the engine holds,
    and deleting a cloned environment tears its namespace off the cluster. On a
    shared engine that is one org sweeping away another org's infrastructure.

    Org A's environment is expired by writing a fixed past instant onto the
    record rather than by waiting or by moving the clock: the route only sweeps
    what `ClonedEnvironment.is_expired` selects, and a freshly created clone has
    hours of TTL left, so without this the sweep would find nothing to do and the
    test would pass against a shared engine too.
    """
    client = two_orgs["client"]
    ids_a = _seed(client, two_orgs["a"], "alpha")

    env_id = ids_a["environment"]
    cloner = next(
        bucket["cloner"]
        for bucket in twin_api._ENGINES.values()
        if env_id in bucket["cloner"]._environments
    )
    cloner._environments[env_id].expires_at = datetime(2020, 1, 1, tzinfo=timezone.utc)

    assert _json(client.post("/v1/digital-twin/clone/cleanup", headers=_auth(two_orgs["b"])))[
        "cleaned_up"
    ] == 0
    assert client.get(
        f"/v1/digital-twin/clone/{env_id}", headers=_auth(two_orgs["a"])
    ).status_code == 200

    # Its owner's own sweep does reach it, so the refusal above is the tenant
    # boundary and not a sweep that never selects anything.
    assert _json(client.post("/v1/digital-twin/clone/cleanup", headers=_auth(two_orgs["a"])))[
        "cleaned_up"
    ] == 1


def test_the_leak_comes_back_if_the_registry_stops_keying_on_the_org(two_orgs, monkeypatch):
    """Pin: restore the old process-global lookup at runtime and org B sees org
    A's scenario again. Without this, the assertions above would still pass
    against an engine that simply had not been given anything to leak."""
    client = two_orgs["client"]
    monkeypatch.setattr(twin_api, "_engine_org", lambda: "one-process-wide-bucket")
    twin_api._ENGINES.clear()

    ids_a = _seed(client, two_orgs["a"], "alpha")
    leaked = {r["id"] for r in _listings(client, two_orgs["b"])["scenario"]}
    assert ids_a["scenario"] in leaked

    monkeypatch.undo()
    twin_api._ENGINES.clear()
    ids_a = _seed(client, two_orgs["a"], "alpha")
    assert ids_a["scenario"] not in {
        r["id"] for r in _listings(client, two_orgs["b"])["scenario"]
    }


def test_no_resolved_tenant_gets_no_engine_at_all(two_orgs):
    """Deny-by-default, the rule app/incident_memory.py applies to stored records:
    with multi-tenancy on, a caller with no tenant has no org whose records it may
    read, and falling back to `storage_org_id()`'s shared "default" key would put
    every such caller in one bucket. `require_auth` rejects an unresolved tenant
    before the route runs, so this asserts the backstop directly."""
    from fastapi import HTTPException

    tenant_context.current_tenant.set(None)
    with pytest.raises(HTTPException) as excinfo:
        twin_api._engine_org()
    assert excinfo.value.status_code == 403
    assert "no tenant is resolved" in str(excinfo.value.detail).lower()
    assert twin_api._ENGINES == {}, "an engine was built for a caller with no org"


def test_each_org_gets_its_own_instance_of_every_engine(two_orgs):
    """The registry key is the org, and it covers all five engines — the twin
    itself plus the four it composes."""
    client = two_orgs["client"]
    _seed(client, two_orgs["a"], "alpha")
    _seed(client, two_orgs["b"], "beta")

    assert set(twin_api._ENGINES) == {two_orgs["org_a"], two_orgs["org_b"]}
    for kind in ("twin", "cloner", "rollback", "chaos"):
        a = twin_api._ENGINES[two_orgs["org_a"]][kind]
        b = twin_api._ENGINES[two_orgs["org_b"]][kind]
        assert a is not b, f"{kind} is shared between orgs"


def test_single_tenant_still_shares_one_set_of_engines(single_tenant):
    """The behaviour that must not change. With no tenant on the context there is
    one org, `storage_org_id()` resolves to its one key, and every request reaches
    the same engine instances it did before — including across the two providers
    that build an engine each (`get_cloner` and the twin's own cloner)."""
    ids = _seed(single_tenant, "", "solo")

    for kind, path, field in (
        ("scenario", "/v1/digital-twin/rollback/scenarios", "scenarios"),
        ("simulation", "/v1/digital-twin/simulations", "simulations"),
        ("environment", "/v1/digital-twin/clone", "environments"),
        ("experiment", "/v1/digital-twin/chaos/experiments", "experiments"),
    ):
        body = _json(single_tenant.get(path))
        assert ids[kind] in {r["id"] for r in body[field]}, path

    assert set(twin_api._ENGINES) == {tenant_context.DEFAULT_STORAGE_ORG}
    assert twin_api.get_cloner() is twin_api.get_cloner()
    assert twin_api.get_chaos_engine() is twin_api.get_chaos_engine()
