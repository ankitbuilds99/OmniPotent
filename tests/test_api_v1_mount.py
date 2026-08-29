"""Regression pins for the /v1 router actually being mounted on the served app.

`app/api/v1/` was written in full — chat, config and admin mirrors of the root
routes, the decision engine, the policy engine and twenty digital-twin endpoints
— given its own RBAC dependencies, and then never included on `app.api.main:app`.
The package had exactly one reference in the whole tree: its own `__init__`.
`app.openapi()` reported 303 paths and not one of them started with `/v1`, so
every one of those handlers was unreachable and every `Depends(require_perm(...))`
and `Depends(quota_gate)` inside them was dead code.

What hid it is worth stating, because it will hide it again: a TestClient `GET
/v1/health` answered `200` the whole time. That 200 came from the SPA catch-all
at the bottom of main.py, which serves index.html for any path it does not
recognise — an `text/html` body, not the route. So every assertion below that
touches HTTP checks the *content type* as well as the status: an HTML body means
the route does not exist and the static mount answered instead.

Structure follows tests/test_api_core_rbac_audit.py — the real app under
TestClient so the lifespan runs, a real tenant store in tmp_path, real API keys.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from starlette.routing import Match

import app.api.main as api
import app.config as cfg
from app import runtime, tenant_context
from app.database import engine as db_engine
from app.tenancy import auth as tenant_auth

# Every digital-twin path the package declares. Spelled out rather than derived
# from the router, so that deleting a route from app/api/v1/digital_twin.py fails
# here instead of quietly shrinking the expectation along with the code.
TWIN_PATHS = {
    "/v1/digital-twin/clone",
    "/v1/digital-twin/clone/{env_id}",
    "/v1/digital-twin/clone/cleanup",
    "/v1/digital-twin/predict-downtime",
    "/v1/digital-twin/maintenance-windows",
    "/v1/digital-twin/capacity-forecast",
    "/v1/digital-twin/rollback/scenario",
    "/v1/digital-twin/rollback/simulate/{scenario_id}",
    "/v1/digital-twin/rollback/scenarios",
    "/v1/digital-twin/chaos/experiment",
    "/v1/digital-twin/chaos/run/{experiment_id}",
    "/v1/digital-twin/chaos/abort/{experiment_id}",
    "/v1/digital-twin/chaos/experiments",
    "/v1/digital-twin/chaos/failure-types",
    "/v1/digital-twin/simulate",
    "/v1/digital-twin/simulate/{simulation_id}",
    "/v1/digital-twin/simulations",
    "/v1/digital-twin/simulation-types",
}


def _reset() -> None:
    cfg.get_settings.cache_clear()
    tenant_auth.reset_store_cache()
    tenant_context.current_tenant.set(None)
    api._RL.clear()
    runtime.reset()
    db_engine._engine = None
    db_engine._session_factory = None


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Single-tenant app (auth off), which is the offline-demo default."""
    monkeypatch.setenv("COPILOT_MULTI_TENANT", "false")
    monkeypatch.delenv("COPILOT_API_TOKEN", raising=False)
    monkeypatch.setenv("COPILOT_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/app.db")
    _reset()
    with TestClient(api.app) as c:
        yield c
    _reset()


async def _provision(store):
    await store.setup()
    org = await store.create_org("Acme", plan="team")
    viewer_key, _ = await store.issue_api_key(org.id, "k-view", role="viewer")
    admin_key, _ = await store.issue_api_key(org.id, "k-admin", role="admin")
    return {"viewer": viewer_key, "admin": admin_key}


@pytest.fixture
def mt(tmp_path, monkeypatch):
    """Multi-tenant app with a viewer seat and an admin seat, so the RBAC
    dependencies declared inside the v1 modules have roles to act on."""
    monkeypatch.setenv("COPILOT_MULTI_TENANT", "true")
    monkeypatch.setenv("COPILOT_TENANT_DB", str(tmp_path / "tenants.sqlite"))
    monkeypatch.setenv("COPILOT_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/app.db")
    _reset()
    data = asyncio.run(_provision(tenant_auth.get_store()))
    with TestClient(api.app) as c:
        data["client"] = c
        yield data
    _reset()


def _auth(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _json(response) -> object:
    """Assert the body really came from a route, then decode it.

    A `text/html` body is the SPA catch-all answering for a path no route owns.
    Checking only `status_code == 200` cannot tell the two apart, which is the
    precise reason twenty unmounted endpoints looked fine for so long.
    """
    ctype = response.headers.get("content-type", "")
    assert ctype.startswith("application/json"), (
        f"{response.request.method} {response.request.url.path} answered "
        f"{response.status_code} {ctype!r} — an HTML body means the SPA fallback "
        f"served it and the route is still not registered"
    )
    return response.json()


# --------------------------------------------------------------------------- #
# The router is on the app at all.
# --------------------------------------------------------------------------- #
def test_v1_paths_are_in_the_openapi_schema():
    """The schema is the authoritative list of what the app serves."""
    paths = api.app.openapi()["paths"]
    v1 = {p for p in paths if p.startswith("/v1/")}
    assert v1, "no /v1 path is registered: app/api/v1 is not mounted on the app"

    # The four sub-surfaces the package declares, each named so a partial mount
    # (one router included, the rest forgotten) is a failure and not a pass.
    assert "/v1/health" in v1
    assert "/v1/chat" in v1
    assert "/v1/config" in v1
    assert "/v1/admin/org" in v1
    assert "/v1/decision/decide" in v1
    assert "/v1/policy/evaluate" in v1
    assert TWIN_PATHS <= v1, sorted(TWIN_PATHS - v1)


def test_all_twenty_digital_twin_operations_are_registered():
    """18 paths, 20 operations — `/clone` and `/simulate/*` carry two verbs each."""
    paths = api.app.openapi()["paths"]
    operations = [
        (path, method)
        for path in TWIN_PATHS
        for method in paths.get(path, {})
        if method in {"get", "post", "put", "patch", "delete"}
    ]
    assert len(operations) == 20, sorted(operations)


def test_a_v1_route_owns_the_path_in_the_routing_table(client):
    """Starlette itself must own the path, independent of any response body.

    Asks the routing table rather than scanning `app.routes` for a `.path`:
    FastAPI 0.137 leaves one opaque `_IncludedRouter` per `include_router` call
    instead of flattening the routes into the top level, so a flat scan finds
    nothing for any included router. `route.matches` is what dispatch uses.
    """
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/v1/digital-twin/simulation-types",
        "root_path": "",
        "headers": [],
    }
    owners = [
        r
        for r in api.app.routes
        if getattr(r, "name", None) != "spa" and r.matches(scope)[0] is not Match.NONE
    ]
    assert owners, "no route matches /v1/digital-twin/simulation-types"


@pytest.mark.skipif(
    not api._FRONTEND_DIST.is_dir(),
    reason="no frontend build, so there is no SPA catch-all to be fooled by",
)
def test_the_spa_fallback_trap_is_real_and_v1_is_not_in_it(client):
    """Pins the mechanism that hid this for two rounds of review.

    An unknown path outside every API segment gets `200 text/html` from the
    static mount — so "GET /v1/health returned 200" proved nothing at all while
    `v1` was not a registered segment. /v1/health has to answer with JSON from
    the health checker instead, and that is only possible if the router is on
    the app: unmount it and this reverts to the index.html body asserted three
    lines above.
    """
    spa = client.get("/totally-made-up-path")
    assert spa.status_code == 200
    assert spa.headers["content-type"].startswith("text/html")
    assert "<!doctype html>" in spa.text.lower()

    live = client.get("/v1/health")
    assert live.status_code == 200
    assert "uptime_seconds" in _json(live)


def test_unknown_v1_path_still_404s_as_json(client):
    """`v1` has to reach `_API_SEGMENTS`, which is built from the routes present
    at the moment the SPA mount is created. Including the router after that mount
    would leave a mistyped /v1 call answering 200 text/html."""
    r = client.get("/v1/no-such-endpoint")
    assert r.status_code == 404
    assert _json(r) == {"detail": "Not Found"}


# --------------------------------------------------------------------------- #
# The digital-twin endpoints answer for real.
# --------------------------------------------------------------------------- #
def test_twin_static_catalogues_return_their_real_payloads(client):
    """Two handler-computed catalogues; the exact ids come from the handlers."""
    types = _json(client.get("/v1/digital-twin/simulation-types"))
    assert [t["id"] for t in types["types"]] == [
        "deployment",
        "rollback",
        "scaling",
        "chaos",
        "what_if",
    ]

    failures = _json(client.get("/v1/digital-twin/chaos/failure-types"))
    ids = {f["id"] for f in failures["failure_types"]}
    assert {"pod_kill", "network_partition", "cpu_stress", "database_failure"} <= ids


def test_twin_listing_endpoints_reach_their_engines(client):
    """Each listing endpoint has to answer out of its engine — a real list with
    the count the handler computed over that same list, not a static-file body
    that happens to parse.

    Deliberately not asserting the stores are empty. `get_digital_twin` and the
    other accessors in app/api/v1/digital_twin.py hand out process-global
    singletons, so what is in them depends on which tests ran earlier in this
    process: `== []` pinned the test *ordering*, not the code, and broke the
    moment anything ran a simulation first. What genuinely belongs to the handler
    is that `count` matches the list it returned, and — for the one store this
    test can write to — that a record it just created comes back out.
    """
    for path, key in (
        ("/v1/digital-twin/clone", "environments"),
        ("/v1/digital-twin/simulations", "simulations"),
        ("/v1/digital-twin/chaos/experiments", "experiments"),
        ("/v1/digital-twin/rollback/scenarios", "scenarios"),
    ):
        body = _json(client.get(path))
        assert isinstance(body[key], list), body
        assert body["count"] == len(body[key]), body

    made = _json(client.post(
        "/v1/digital-twin/simulate",
        json={"simulation_type": "what_if", "target_service": "listing-probe"},
    ))
    listed = _json(client.get("/v1/digital-twin/simulations"))
    assert made["id"] in [s["id"] for s in listed["simulations"]]
    assert listed["count"] == len(listed["simulations"])


def test_twin_simulate_runs_the_simulator_and_returns_its_result(client):
    """A POST with a body, through the response_model, into the real simulator."""
    r = client.post(
        "/v1/digital-twin/simulate",
        json={"simulation_type": "deployment", "target_service": "checkout"},
    )
    body = _json(r)
    assert r.status_code == 200, body
    assert body["simulation_type"] == "deployment"
    assert body["target_service"] == "checkout"
    assert body["status"] == "completed"
    assert 0.0 <= body["success_probability"] <= 1.0
    assert isinstance(body["recommendations"], list)

    # ...and the run it just did is now visible through the listing endpoint,
    # which proves both handlers are talking to the same live simulator. Asserted
    # as membership, not as a one-element list: the simulator behind both
    # handlers is a process-global singleton, so any earlier test in this process
    # that ran a simulation leaves its record in there too, and demanding an
    # exact list made this test depend on running first.
    listed = _json(client.get("/v1/digital-twin/simulations"))
    ids = [s["id"] for s in listed["simulations"]]
    assert body["id"] in ids, ids
    assert listed["count"] == len(ids)


def test_twin_missing_environment_is_a_json_404_from_the_handler(client):
    """The handler's own 404, distinguishable from "no such route" by its detail:
    the catch-all has no opinion about environments."""
    r = client.get("/v1/digital-twin/clone/does-not-exist")
    assert r.status_code == 404
    assert _json(r)["detail"] == "Environment not found"


def test_twin_rejects_a_body_the_model_forbids(client):
    """422 from FastAPI's validation of CapacityForecastRequest (horizon_days is
    bounded 7..90). Nothing but a mounted, validated route produces this."""
    r = client.post(
        "/v1/digital-twin/capacity-forecast",
        json={"service": "checkout", "resource_type": "cpu", "horizon_days": 5000},
    )
    assert r.status_code == 422
    body = _json(r)
    assert [d["field"] for d in body["details"]] == ["body.horizon_days"]


# --------------------------------------------------------------------------- #
# The RBAC declared inside the v1 modules is now live.
# --------------------------------------------------------------------------- #
def test_viewer_is_refused_by_the_v1_rbac_dependencies(mt):
    """The `Depends(require_perm(...))` restated in v1/chat.py, v1/config.py and
    v1/admin.py. They were unreachable, so nothing had ever run them; each one
    guards a route whose root twin is guarded the same way."""
    client = mt["client"]
    for method, path, body, action in (
        ("POST", "/v1/chat", {"message": "why is checkout down?"}, "run investigation"),
        ("POST", "/v1/chat/stream", {"message": "why?"}, "run investigation"),
        ("POST", "/v1/chat/approve", {"thread_id": "t", "approved": True}, "approve action"),
        ("GET", "/v1/admin/api-keys", None, "manage api keys"),
        ("POST", "/v1/admin/api-keys", {"name": "k"}, "manage api keys"),
        ("POST", "/v1/admin/members", {"email": "x@y.z", "role": "viewer"}, "manage members"),
        ("POST", "/v1/config/github/disconnect", {}, "manage integrations"),
        ("POST", "/v1/config/reset", {}, "manage integrations"),
    ):
        r = client.request(method, path, json=body, headers=_auth(mt["viewer"]))
        assert r.status_code == 403, f"{method} {path} -> {r.status_code} {r.text[:200]}"
        assert _json(r)["detail"] == f"Role 'viewer' is not permitted to {action}."


def test_admin_seat_passes_the_same_v1_gates(mt):
    """The counterpart: the refusal above is the role check, not a broken route.
    An admin may read the key list, and gets a list."""
    r = mt["client"].get("/v1/admin/api-keys", headers=_auth(mt["admin"]))
    body = _json(r)
    assert r.status_code == 200, body
    assert [k["name"] for k in body["api_keys"]] == ["k-view", "k-admin"]


def test_viewer_may_read_the_twin_but_not_run_it(mt):
    """The digital-twin router is not a model of infrastructure: `/chaos/run`
    reaches `KubernetesConnector.delete_pod` and `/clone` POSTs real namespaces
    and deployments with the deployment's own kube credentials. It carried no
    RBAC at all, which cost nothing while nothing could reach it — mounting the
    router is what turns that into "a viewer key deletes production pods". Reads
    stay open to `view`; anything that creates, destroys or executes needs
    `approve_action`, the same gate the root /remediate route uses."""
    client = mt["client"]
    for method, path, body in (
        ("POST", "/v1/digital-twin/chaos/run/anything", None),
        ("POST", "/v1/digital-twin/chaos/experiment", {"name": "x", "failures": []}),
        ("POST", "/v1/digital-twin/chaos/abort/anything", None),
        ("POST", "/v1/digital-twin/clone", {"source_namespace": "prod"}),
        ("DELETE", "/v1/digital-twin/clone/anything", None),
        ("POST", "/v1/digital-twin/clone/cleanup", None),
        ("POST", "/v1/digital-twin/simulate", {"simulation_type": "chaos", "target_service": "s"}),
        ("POST", "/v1/digital-twin/rollback/scenario", {"deployment_id": "d", "service": "s",
                                                        "from_version": "2", "to_version": "1"}),
    ):
        r = client.request(method, path, json=body, headers=_auth(mt["viewer"]))
        assert r.status_code == 403, f"{method} {path} -> {r.status_code} {r.text[:200]}"
        assert _json(r)["detail"] == "Role 'viewer' is not permitted to approve action."

    # The same seat still reads, so the refusals above are the role check and not
    # a router that rejects everything. Asserting the shape rather than an empty
    # list on purpose: the engines behind this router are process-global
    # singletons (`app/api/v1/digital_twin.py::get_digital_twin` et al.), so a
    # simulation another test in this process ran is still in there. That is a
    # real cross-tenant leak and it is recorded in the report, not papered over
    # here — this test is about the role gate.
    listed = _json(client.get("/v1/digital-twin/simulations", headers=_auth(mt["viewer"])))
    assert isinstance(listed["simulations"], list)
    assert listed["count"] == len(listed["simulations"])


def test_v1_requires_a_credential_like_every_other_route(mt):
    """The app-level Depends(require_auth) covers the included router too, so the
    /v1 mount did not open an unauthenticated way round the root routes."""
    r = mt["client"].get("/v1/admin/org")
    assert r.status_code == 401
    assert _json(r)["detail"] == "Invalid or revoked credential."


def test_v1_webhook_mirror_reaches_its_signature_check(mt):
    """/v1/webhooks/* calls the same receivers as /webhooks/*, and the senders
    (GitHub et al.) cannot present a bearer token — only the signature they sign
    the body with. So the prefix skips the bearer gate exactly like the other two
    webhook mounts, and the receiver's own fail-closed check is what answers."""
    r = mt["client"].post("/v1/webhooks/github", json={}, headers={"x-github-event": "push"})
    assert r.status_code == 401
    detail = _json(r)["detail"]
    assert "GITHUB_WEBHOOK_SECRET" in detail, detail
