"""RBAC pins for the /v1 policy and decision routers.

Both files were written with no `require_perm` anywhere in them. That cost
nothing while `app/api/v1` was not included on the served app, and became a live
privilege escalation the moment it was: with a real multi-tenant `viewer` key —
the cheapest read-only seat an org can hand out — `POST /v1/policy/windows/freeze`
returned 200 and *persisted* a production change freeze, `POST
/v1/policy/windows/emergency-override` activated an override that lifts every
freeze, `POST /v1/decision/record-outcome/{id}` wrote into the success
predictor's calibration set, and `GET /v1/policy/audit` returned the policy
audit trail to the same key that the root `GET /audit` answers with
`403 Role 'viewer' is not permitted to view audit.` That last one is the reason
this file exists as its own module: it was not a missing gate but a working way
round a gate the deployment already enforces.

Every case asserts both directions. A route that is simply broken answers 403 to
everyone, so "the viewer is refused" on its own would pass against a router that
refuses all comers; each refusal below is paired with the same call succeeding
for the seat that is supposed to be able to make it.

Fixtures mirror tests/test_api_v1_mount.py: the real app under TestClient so the
lifespan runs, a real tenant store in tmp_path, real issued API keys.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

import app.api.main as api
import app.config as cfg
from app import runtime, tenant_context
from app.api.v1 import decision as decision_v1
from app.api.v1 import policy as policy_v1
from app.database import engine as db_engine
from app.tenancy import auth as tenant_auth
from app.tenancy import models as tn_models


def _reset() -> None:
    cfg.get_settings.cache_clear()
    tenant_auth.reset_store_cache()
    tenant_context.current_tenant.set(None)
    api._RL.clear()
    runtime.reset()
    db_engine._engine = None
    db_engine._session_factory = None


async def _provision(store):
    await store.setup()
    org = await store.create_org("Acme", plan="team")
    keys = {}
    for role in ("viewer", "responder", "admin"):
        key, _ = await store.issue_api_key(org.id, f"k-{role}", role=role)
        keys[role] = key
    return keys


@pytest.fixture
def mt(tmp_path, monkeypatch):
    """Multi-tenant app with one seat per role, so `require_perm` has a role to
    act on. In single-tenant mode it returns immediately and proves nothing."""
    monkeypatch.setenv("COPILOT_MULTI_TENANT", "true")
    monkeypatch.setenv("COPILOT_TENANT_DB", str(tmp_path / "tenants.sqlite"))
    monkeypatch.setenv("COPILOT_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/app.db")
    _reset()
    keys = asyncio.run(_provision(tenant_auth.get_store()))
    with TestClient(api.app) as c:
        keys["client"] = c
        yield keys
    _reset()


def _auth(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _json(response) -> object:
    """Decode, having first checked the body came from a route and not from the
    SPA catch-all, which answers `200 text/html` for any path no route owns."""
    ctype = response.headers.get("content-type", "")
    assert ctype.startswith("application/json"), (
        f"{response.request.method} {response.request.url.path} answered "
        f"{response.status_code} {ctype!r} — an HTML body means the route is "
        f"not registered and the static mount replied"
    )
    return response.json()


# Fixed offsets, never `now()`: these tests assert who may create a freeze, not
# whether the freeze is currently in force, and an offset-aware pair is required
# because ChangeWindow.is_active compares against an aware `now`.
FREEZE_PARAMS = {
    "name": "rbac-probe",
    "start_time": "2031-03-01T00:00:00+00:00",
    "end_time": "2031-03-02T00:00:00+00:00",
    "reason": "rbac regression pin",
}


# --------------------------------------------------------------------------- #
# Policy: change windows are change control, not a view of it.
# --------------------------------------------------------------------------- #
def test_viewer_cannot_change_the_change_control_record(mt):
    """Freezing, overriding, deleting a window and rolling a policy version back
    all take `manage_integrations` (admin). A freeze blocks every deploy in its
    scope and an emergency override lifts every freeze at once — that is
    configuration of how the fleet may be changed."""
    client = mt["client"]
    for method, path, kwargs in (
        ("POST", "/v1/policy/windows/freeze", {"params": FREEZE_PARAMS}),
        ("POST", "/v1/policy/windows", {"json": {
            "name": "w", "window_type": "maintenance",
            "start_time": "2031-03-01T00:00:00+00:00",
            "end_time": "2031-03-02T00:00:00+00:00",
        }}),
        ("POST", "/v1/policy/windows/emergency-override",
         {"json": {"reason": "r", "activated_by": "viewer@acme.test"}}),
        ("DELETE", "/v1/policy/windows/emergency-override", {}),
        ("DELETE", "/v1/policy/windows/window_1", {}),
        ("POST", "/v1/policy/versions/deploy-guard/rollback/1", {}),
    ):
        r = client.request(method, path, headers=_auth(mt["viewer"]), **kwargs)
        assert r.status_code == 403, f"{method} {path} -> {r.status_code} {r.text[:200]}"
        assert _json(r)["detail"] == "Role 'viewer' is not permitted to manage integrations."


def test_admin_may_still_run_the_change_window_routes(mt):
    """The counterpart: the refusals above are the role check and not a router
    that rejects everything. An admin creates a freeze, sees it in the listing,
    and deletes it again by the id the create call returned."""
    client = mt["client"]
    admin = _auth(mt["admin"])

    created = _json(client.post("/v1/policy/windows/freeze", params=FREEZE_PARAMS, headers=admin))
    assert created["window_type"] == "freeze"
    assert created["name"] == "rbac-probe"

    listed = _json(client.get("/v1/policy/windows", headers=admin))
    assert created["id"] in [w["id"] for w in listed["windows"]]

    gone = _json(client.delete(f"/v1/policy/windows/{created['id']}", headers=admin))
    assert gone == {"status": "deleted", "window_id": created["id"]}

    override = _json(client.post(
        "/v1/policy/windows/emergency-override",
        json={"reason": "rbac pin", "activated_by": "admin@acme.test", "environment": "rbac-env"},
        headers=admin,
    ))
    assert override["status"] == "active"
    dropped = client.delete(
        "/v1/policy/windows/emergency-override", params={"environment": "rbac-env"}, headers=admin
    )
    assert _json(dropped) == {"status": "deactivated"}

    # No such policy, so the handler's own 404 — which is only reachable past the
    # gate, and is a different answer from the 403 the viewer gets.
    rolled = client.post("/v1/policy/versions/deploy-guard/rollback/1", headers=admin)
    assert rolled.status_code == 404
    assert _json(rolled)["detail"] == "Policy or version not found"


def test_viewer_may_still_read_and_evaluate_policy(mt):
    """The router-wide gate is `view`, so a read-only seat keeps the read-only
    surface: listing windows, asking whether a change would be allowed, and the
    criteria templates. If these 403'd, the tests above would be pinning a dead
    router rather than a permission matrix."""
    client = mt["client"]
    viewer = _auth(mt["viewer"])

    listed = _json(client.get("/v1/policy/windows", headers=viewer))
    assert listed["count"] == len(listed["windows"])

    check = _json(client.post(
        "/v1/policy/windows/check",
        json={"action_type": "deploy", "risk_score": 0.1, "environment": "staging"},
        headers=viewer,
    ))
    assert isinstance(check["allowed"], bool)
    assert isinstance(check["active_windows"], list)

    templates = _json(client.get("/v1/policy/criteria/templates", headers=viewer))
    assert isinstance(templates["entry_templates"], list)


# --------------------------------------------------------------------------- #
# Policy: the audit bypass.
# --------------------------------------------------------------------------- #
def test_v1_policy_audit_no_longer_bypasses_the_root_audit_gate(mt):
    """The most serious part of the mount. `GET /audit` refuses a viewer with
    `require_perm("view_audit")`; an ungated `GET /v1/policy/audit` handed the
    same key the policy engine's trail — evaluation ids, the actor named on each
    request, every approval decision. Both now answer the same way to the same
    key, which is the property that was violated."""
    client = mt["client"]
    detail = "Role 'viewer' is not permitted to view audit."

    root = client.get("/audit", headers=_auth(mt["viewer"]))
    assert root.status_code == 403
    assert _json(root)["detail"] == detail

    mirrored = client.get("/v1/policy/audit", headers=_auth(mt["viewer"]))
    assert mirrored.status_code == 403, mirrored.text[:200]
    assert _json(mirrored)["detail"] == detail

    # A responder is not enough either — `view_audit` is admin+ in the matrix.
    responder = client.get("/v1/policy/audit", headers=_auth(mt["responder"]))
    assert responder.status_code == 403
    assert _json(responder)["detail"] == "Role 'responder' is not permitted to view audit."


def test_admin_still_reads_the_v1_policy_audit(mt):
    """...and the trail is not simply unreachable now. An admin gets the engine's
    records with the count the handler computed over them."""
    body = _json(mt["client"].get("/v1/policy/audit", headers=_auth(mt["admin"])))
    assert isinstance(body["records"], list)
    assert body["count"] == len(body["records"])


# --------------------------------------------------------------------------- #
# Policy: approvals.
# --------------------------------------------------------------------------- #
def test_viewer_cannot_drive_the_approval_workflow(mt):
    """Requesting, approving and rejecting all take `approve_action` — the
    matrix entry that names exactly this workflow ("approve/reject a write the
    agent proposes")."""
    client = mt["client"]
    for path, kwargs in (
        ("/v1/policy/approvals/request",
         {"json": {"evaluation_id": "eval-1", "reason": "r", "requested_by": "viewer@acme.test"}}),
        ("/v1/policy/approvals/nope/approve",
         {"json": {"approved_by": "viewer@acme.test"}}),
        ("/v1/policy/approvals/nope/reject",
         {"params": {"rejected_by": "viewer@acme.test"}}),
    ):
        r = client.post(path, headers=_auth(mt["viewer"]), **kwargs)
        assert r.status_code == 403, f"POST {path} -> {r.status_code} {r.text[:200]}"
        assert _json(r)["detail"] == "Role 'viewer' is not permitted to approve action."


def test_responder_completes_the_approval_workflow(mt):
    """A responder is the minimum role for `approve_action`, so the same three
    routes work end to end for one: request an approval, then approve the id it
    handed back and see the engine's own status change."""
    client = mt["client"]
    responder = _auth(mt["responder"])

    requested = _json(client.post(
        "/v1/policy/approvals/request",
        json={"evaluation_id": "eval-rbac", "reason": "rbac pin", "requested_by": "resp@acme.test"},
        headers=responder,
    ))
    assert requested["status"] == "pending"
    approval_id = requested["approval_id"]

    decided = _json(client.post(
        f"/v1/policy/approvals/{approval_id}/approve",
        json={"approved_by": "resp@acme.test", "notes": "ok"},
        headers=responder,
    ))
    assert decided == {"status": "approved", "approval_id": approval_id}

    status = _json(client.get(f"/v1/policy/approvals/{approval_id}", headers=responder))
    assert status["status"] == "approved"


# --------------------------------------------------------------------------- #
# Decision engine.
# --------------------------------------------------------------------------- #
def test_viewer_cannot_write_into_the_predictors_training_record(mt):
    """`record-outcome` moves `SuccessPredictor`'s calibration factor and appends
    to the historical set every later prediction is scored against, so a
    read-only seat posting outcomes steers every future recommendation. It takes
    `approve_action`."""
    r = mt["client"].post(
        "/v1/decision/record-outcome/pred-1",
        params={"was_successful": True, "actual_duration_seconds": 12},
        headers=_auth(mt["viewer"]),
    )
    assert r.status_code == 403, r.text[:200]
    assert _json(r)["detail"] == "Role 'viewer' is not permitted to approve action."


def test_responder_may_record_an_outcome(mt):
    """The paired direction, so the 403 above is the gate and not a broken
    route."""
    r = mt["client"].post(
        "/v1/decision/record-outcome/pred-1",
        params={"was_successful": True, "actual_duration_seconds": 12},
        headers=_auth(mt["responder"]),
    )
    body = _json(r)
    assert r.status_code == 200, body
    assert body == {"status": "recorded", "prediction_id": "pred-1"}


def test_viewer_keeps_the_advisory_decision_surface(mt):
    """Predicting, ranking and deciding recommend rather than act — the caller
    supplies the incident and the candidate options and gets a ranking back — so
    they stay at the router's `view`. Pinned so a later round does not quietly
    lock a read-only seat out of the thing it is meant to read."""
    client = mt["client"]
    viewer = _auth(mt["viewer"])

    predicted = _json(client.post(
        "/v1/decision/predict-success",
        json={"action_type": "restart_service"},
        headers=viewer,
    ))
    assert 0.0 <= predicted["probability"] <= 1.0

    decided = _json(client.post(
        "/v1/decision/decide",
        json={
            "context": {
                "incident_id": "inc-rbac",
                "title": "checkout 5xx",
                "started_at": "2031-03-01T00:00:00+00:00",
            },
            "options": [],
        },
        headers=viewer,
    ))
    assert decided["incident_id"] == "inc-rbac"

    strategies = _json(client.get("/v1/decision/strategies", headers=viewer))
    assert [s["id"] for s in strategies["strategies"]] == [
        "balanced", "speed_focused", "safety_focused", "cost_focused",
    ]


# --------------------------------------------------------------------------- #
# Every route in both routers is gated, not just the ones exercised above.
# --------------------------------------------------------------------------- #
# Route -> the strongest action it must require. Anything absent from this map is
# expected to sit at the router-wide "view". Spelled out rather than derived from
# the routers so that weakening a gate fails here instead of silently agreeing
# with itself, and so a route added later with no entry is a failure too.
EXPECTED_ACTIONS = {
    ("POST", "/policy/windows"): "manage_integrations",
    ("POST", "/policy/windows/freeze"): "manage_integrations",
    ("POST", "/policy/windows/emergency-override"): "manage_integrations",
    ("DELETE", "/policy/windows/emergency-override"): "manage_integrations",
    ("DELETE", "/policy/windows/{window_id}"): "manage_integrations",
    ("POST", "/policy/versions/{policy_name}/rollback/{version}"): "manage_integrations",
    ("GET", "/policy/audit"): "view_audit",
    ("POST", "/policy/approvals/request"): "approve_action",
    ("POST", "/policy/approvals/{approval_id}/approve"): "approve_action",
    ("POST", "/policy/approvals/{approval_id}/reject"): "approve_action",
    ("POST", "/policy/mutation-freeze"): "manage_integrations",
    ("DELETE", "/policy/mutation-freeze"): "manage_integrations",
    ("POST", "/decision/record-outcome/{prediction_id}"): "approve_action",
}


def _perm_actions(route) -> list[str]:
    """The actions the `require_perm` closures on `route` were built with.

    Read off the closure cell rather than the source, so this sees exactly what
    the dependency will check at request time — including the router-level gate,
    which FastAPI folds into each route's dependant list when the route is added.
    """
    actions = []
    for dep in route.dependant.dependencies:
        call = dep.call
        if getattr(call, "__qualname__", "") != "require_perm.<locals>._check":
            continue
        actions += [c.cell_contents for c in (call.__closure__ or ())]
    return actions


@pytest.mark.parametrize("module", [policy_v1, decision_v1], ids=["policy", "decision"])
def test_every_route_in_both_v1_routers_is_gated(module):
    """No route in either file may be reachable without a permission check.

    The live-key tests above cover the routes that were demonstrably exploitable;
    this one covers the rest of both files, so the next route added here cannot
    repeat the original defect by simply not being in anybody's test.
    """
    for route in module.router.routes:
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            actions = _perm_actions(route)
            assert "view" in actions, (
                f"{method} {route.path} does not inherit the router-wide view gate: {actions}"
            )
            expected = EXPECTED_ACTIONS.get((method, route.path))
            if expected is None:
                assert actions == ["view"], (
                    f"{method} {route.path} requires {actions}, which EXPECTED_ACTIONS "
                    f"does not describe — add it there or drop the gate"
                )
            else:
                assert expected in actions, (
                    f"{method} {route.path} should require {expected!r}, requires {actions}"
                )


def test_the_expected_action_map_names_only_real_routes():
    """Guards the guard: a typo'd path in EXPECTED_ACTIONS would make the test
    above assert nothing about the route it meant to cover."""
    declared = {
        (method, route.path)
        for module in (policy_v1, decision_v1)
        for route in module.router.routes
        for method in route.methods - {"HEAD", "OPTIONS"}
    }
    assert set(EXPECTED_ACTIONS) <= declared, sorted(set(EXPECTED_ACTIONS) - declared)


def test_every_action_used_is_in_the_tenancy_matrix():
    """The gates have to name actions the matrix actually knows. `can()` returns
    False for an unknown action, so an invented one would lock out every role
    including the owner — a 403 that looks like RBAC working."""
    used = {
        action
        for module in (policy_v1, decision_v1)
        for route in module.router.routes
        for action in _perm_actions(route)
    }
    assert used, "no require_perm dependency found on either router at all"
    assert used <= set(tn_models.ACTIONS), sorted(used - set(tn_models.ACTIONS))
