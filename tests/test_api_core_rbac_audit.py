"""Regression pins for the core-API safety gates that had none.

Five fixes landed on the autonomy/tenancy path and an adversarial review found
that essentially nothing held them in place: the behaviour was correct by hand,
so a later refactor could have quietly undone any of it and the suite would still
have been green. Each section below drives the fix on the path a real request
takes, so removing the guard fails a test rather than shipping.

  * `/remediate` carried no RBAC at all — a read-only `viewer` seat could fire a
    cluster write while merely *approving* one needed `approve_action`.
  * `_remediation_namespace` pins a multi-tenant remediation to the org's own
    namespace; before it, `namespace` was a free string on a process-wide
    Kubernetes client, so org A could restart org B's workload.
  * `_tenant_for_org` re-establishes the incident's tenant for a background
    investigation. Left unset, org A's incident ran with the *operator's*
    credentials and was filed under "default".
  * `app.autonomy.is_eligible` distinguishes UNMEASURED confidence from a
    measured-but-weak tier — "we never measured it" and "we measured it and it
    was weak" are different facts and only the second is a tier.
  * `app.policy` classifies every tool we ship; the read-only allowlist is
    hand-maintained, so it is cross-checked here against the real MCP surface.

Same shape as tests/test_api_websocket_rbac_audit.py: the real app under
TestClient (so the lifespan creates the schema), a real tenant store in tmp_path,
real API keys. The Kubernetes and Datadog MCP servers run in their offline mode,
so the "write" is simulated but goes through the real handler, the real policy
resolution and the real audit call.
"""

from __future__ import annotations

import asyncio
import importlib
import pkgutil
from typing import get_args

import pytest
from fastapi.testclient import TestClient

import app.api.main as api
import app.config as cfg
from app import investigation_history, policy, runtime, tenant_context
from app.autonomy import CONFIDENCE_TIERS, UNMEASURED, is_eligible
from app.database import engine as db_engine
from app.mcp.servers.kubernetes import server as k8s_server
from app.tenancy import auth as tenant_auth

ACME_NS = "acme-prod"
UMBRELLA_NS = "umbrella-prod"


async def _provision(store):
    """Two orgs that each own a namespace, plus one that owns none.

    Acme gets a viewer and a responder key so the RBAC verdict can be compared
    for two seats on otherwise identical requests.
    """
    await store.setup()
    acme = await store.create_org("Acme", plan="team")
    viewer_key, _ = await store.issue_api_key(acme.id, "k-view", role="viewer")
    responder_key, _ = await store.issue_api_key(acme.id, "k-resp", role="responder")
    await store.set_integration_secret(acme.id, "KUBE_NAMESPACE", ACME_NS)

    umbrella = await store.create_org("Umbrella", plan="team")
    umbrella_key, _ = await store.issue_api_key(umbrella.id, "k-umb", role="responder")
    await store.set_integration_secret(umbrella.id, "KUBE_NAMESPACE", UMBRELLA_NS)

    # No KUBE_NAMESPACE at all: there is no scope a remediation could act in.
    nowhere = await store.create_org("Nowhere", plan="team")
    nowhere_key, _ = await store.issue_api_key(nowhere.id, "k-now", role="responder")

    # Its own cluster, which this single-client process cannot honour.
    byo = await store.create_org("BringYourOwn", plan="team")
    byo_key, _ = await store.issue_api_key(byo.id, "k-byo", role="responder")
    await store.set_integration_secret(byo.id, "KUBE_NAMESPACE", "byo-prod")
    await store.set_integration_secret(byo.id, "KUBE_CONFIG_PATH", "/etc/byo/kubeconfig")

    return {
        "viewer": viewer_key,
        "responder": responder_key,
        "umbrella": umbrella_key,
        "nowhere": nowhere_key,
        "byo": byo_key,
        "acme_org": acme.id,
        "umbrella_org": umbrella.id,
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
def mt(tmp_path, monkeypatch):
    monkeypatch.setenv("COPILOT_MULTI_TENANT", "true")
    monkeypatch.setenv("COPILOT_TENANT_DB", str(tmp_path / "tenants.sqlite"))
    monkeypatch.setenv("COPILOT_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/app.db")
    # Autonomy on and NOT dry-run: the point of these tests is what the write
    # actually did, and dry-run returns before touching the action functions.
    monkeypatch.setenv("COPILOT_AUTONOMY", "true")
    monkeypatch.setenv("COPILOT_AUTONOMY_DRYRUN", "false")
    monkeypatch.setenv("COPILOT_AUTONOMY_WATCH_S", "0")
    _reset()
    data = asyncio.run(_provision(tenant_auth.get_store()))

    audited: list[tuple[str, dict]] = []
    # `event` rather than `action`: the remediation trail line passes an `action`
    # keyword of its own, which would collide with the positional name.
    monkeypatch.setattr(api.audit, "record", lambda event, **kw: audited.append((event, kw)))
    data["audited"] = audited

    # Every real k8s write goes through these, so "did anything execute, and in
    # whose namespace" is observable without a cluster.
    executed: list[tuple[str, str, str]] = []

    def _restart(deployment: str, namespace: str = "") -> dict:
        executed.append(("restart_deployment", deployment, namespace))
        return {"status": "restart triggered (simulated)", "namespace": namespace}

    def _rollback(deployment: str, namespace: str = "") -> dict:
        executed.append(("rollback_deployment", deployment, namespace))
        return {"status": "rolled back (simulated)", "namespace": namespace}

    monkeypatch.setattr(k8s_server, "restart_deployment", _restart)
    monkeypatch.setattr(k8s_server, "rollback_deployment", _rollback)
    data["executed"] = executed

    with TestClient(api.app) as client:  # `with` runs the lifespan, which creates the schema
        data["client"] = client
        yield data

    _reset()


def _auth(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _seed_investigation(key: str, thread_id: str, confidence: str) -> None:
    """Store a finished investigation under `key`'s org with a recorded tier.

    The remediation gate reads its confidence off this row and nowhere else, so
    a test that wants to reach the namespace logic has to leave one behind the
    same way a completed investigation does.
    """

    async def run() -> None:
        resolved = await tenant_auth.resolve(key)
        assert resolved is not None
        token = tenant_context.set_tenant(resolved[0])
        try:
            await investigation_history.record(
                thread_id,
                "why is checkout erroring?",
                status="completed",
                summary="bad deploy",
                report={"root_cause": "bad deploy", "confidence": confidence},
            )
        finally:
            tenant_context.reset_tenant(token)

    asyncio.run(run())


def _remediate(client: TestClient, key: str, **body) -> object:
    payload = {"action": "restart_deployment", "target": "checkout-svc", **body}
    return client.post("/remediate", json=payload, headers=_auth(key))


# --------------------------------------------------------------------------- #
# /remediate RBAC — a viewer seat may not fire a cluster write.
# --------------------------------------------------------------------------- #
def test_viewer_may_not_remediate(mt):
    """The route's `require_perm("approve_action")` dependency, on the real path.

    Everything else about the request is valid — autonomy is enabled, the org has
    a namespace, and the investigation it quotes really did finish at high
    confidence — so a 403 here can only come from the role check.
    """
    _seed_investigation(mt["viewer"], "inv-viewer", "high")
    r = _remediate(mt["client"], mt["viewer"], thread_id="inv-viewer")
    assert r.status_code == 403
    assert r.json()["detail"] == "Role 'viewer' is not permitted to approve action."
    # And the gate ran *before* the handler: nothing was executed and nothing was
    # written to the trail as a remediation that happened.
    assert mt["executed"] == []
    assert [a for a, _ in mt["audited"] if a == "autonomy.remediation"] == []


def test_responder_may_remediate(mt):
    """The counterpart: the same request from a responder seat goes through."""
    _seed_investigation(mt["responder"], "inv-resp", "high")
    r = _remediate(mt["client"], mt["responder"], thread_id="inv-resp")
    assert r.status_code == 200, r.text
    assert mt["executed"] == [("restart_deployment", "checkout-svc", ACME_NS)]


def test_remediate_matches_the_approval_gate_for_the_same_key(mt):
    """`/remediate` acts on infrastructure directly, so it cannot be *easier* to
    reach than approving the same class of write through `/approve`."""
    approve = mt["client"].post(
        "/approve", json={"thread_id": "t1", "approved": True}, headers=_auth(mt["viewer"])
    )
    remediate = _remediate(mt["client"], mt["viewer"], thread_id="inv-missing")
    assert approve.status_code == 403
    assert remediate.status_code == 403
    assert remediate.json()["detail"] == approve.json()["detail"]


# --------------------------------------------------------------------------- #
# Cross-tenant namespace pin (_remediation_namespace).
# --------------------------------------------------------------------------- #
def test_remediation_is_pinned_to_the_orgs_own_namespace(mt):
    """A request that names no namespace still acts only in the org's own."""
    _seed_investigation(mt["responder"], "inv-1", "high")
    r = _remediate(mt["client"], mt["responder"], thread_id="inv-1")
    assert r.status_code == 200, r.text
    assert mt["executed"] == [("restart_deployment", "checkout-svc", ACME_NS)]
    trail = [kw for a, kw in mt["audited"] if a == "autonomy.remediation"]
    assert len(trail) == 1
    # The trail names the namespace actually acted in, not the one asked for.
    assert trail[0]["namespace"] == ACME_NS
    assert trail[0]["confidence"] == "high"


def test_org_a_cannot_remediate_in_org_bs_namespace(mt):
    """The defect this pins: `namespace` was a free string on a process-wide
    Kubernetes client, so naming another tenant's namespace restarted their
    workload on the operator's cluster."""
    _seed_investigation(mt["responder"], "inv-2", "high")
    r = _remediate(mt["client"], mt["responder"], thread_id="inv-2", namespace=UMBRELLA_NS)
    assert r.status_code == 403
    detail = r.json()["detail"]
    assert ACME_NS in detail and UMBRELLA_NS in detail
    assert "belongs to someone else" in detail
    # Refused, not silently redirected: no write happened at all.
    assert mt["executed"] == []


def test_naming_your_own_namespace_explicitly_is_allowed(mt):
    _seed_investigation(mt["responder"], "inv-3", "high")
    r = _remediate(mt["client"], mt["responder"], thread_id="inv-3", namespace=ACME_NS)
    assert r.status_code == 200, r.text
    assert mt["executed"] == [("restart_deployment", "checkout-svc", ACME_NS)]


def test_org_with_no_namespace_configured_is_refused(mt):
    """No configured scope is a refusal, not a fallback to the process default —
    which is the operator's own `default` namespace."""
    _seed_investigation(mt["nowhere"], "inv-4", "high")
    r = _remediate(mt["client"], mt["nowhere"], thread_id="inv-4")
    assert r.status_code == 409
    assert "KUBE_NAMESPACE" in r.json()["detail"]
    assert mt["executed"] == []


def test_org_with_its_own_kubeconfig_is_refused_rather_than_run_elsewhere(mt):
    """This process holds one cluster client bound at import time. An org that
    configured its own cluster must be told remediation is unavailable, not have
    its remediation executed against the operator's cluster and reported as a
    success."""
    _seed_investigation(mt["byo"], "inv-5", "high")
    r = _remediate(mt["client"], mt["byo"], thread_id="inv-5")
    assert r.status_code == 501
    assert "kubeconfig" in r.json()["detail"]
    assert mt["executed"] == []


def test_confidence_is_read_from_the_org_that_asked(mt):
    """Acme's high-confidence investigation cannot authorise Umbrella's write:
    the lookup is org-scoped, so the id simply does not resolve for them."""
    _seed_investigation(mt["responder"], "shared-id", "high")
    r = _remediate(mt["client"], mt["umbrella"], thread_id="shared-id")
    assert r.status_code == 400
    assert "no investigation 'shared-id' belongs to this organization" in r.json()["detail"]
    assert mt["executed"] == []


def test_a_declared_confidence_is_refused_not_ignored(mt):
    _seed_investigation(mt["responder"], "inv-6", "high")
    r = _remediate(mt["client"], mt["responder"], thread_id="inv-6", confidence="high")
    assert r.status_code == 400
    assert "no longer accepted in the request body" in r.json()["detail"]
    assert mt["executed"] == []


def test_measured_but_weak_confidence_blocks_the_write(mt):
    _seed_investigation(mt["responder"], "inv-7", "medium")
    r = _remediate(mt["client"], mt["responder"], thread_id="inv-7")
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "'medium'" in detail and "not high enough" in detail
    assert mt["executed"] == []


def test_no_investigation_is_reported_as_unmeasured_not_as_low(mt):
    """The standing rule, end to end: an absent measurement is not a weak one."""
    r = _remediate(mt["client"], mt["responder"], thread_id="")
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "no investigation confidence was measured" in detail
    assert "no thread_id was supplied" in detail
    assert "not high enough" not in detail
    assert mt["executed"] == []


def test_single_tenant_keeps_the_namespace_the_operator_asked_for(tmp_path, monkeypatch):
    """The pin is a multi-tenant rule. On a single-tenant deployment the cluster
    and the operator are the same party, so the offline demo keeps working."""
    monkeypatch.delenv("COPILOT_MULTI_TENANT", raising=False)
    monkeypatch.setenv("COPILOT_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/app.db")
    monkeypatch.setenv("COPILOT_AUTONOMY", "true")
    monkeypatch.setenv("COPILOT_AUTONOMY_DRYRUN", "false")
    monkeypatch.setenv("COPILOT_AUTONOMY_WATCH_S", "0")
    _reset()
    executed: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        k8s_server,
        "restart_deployment",
        lambda deployment, namespace="": (
            executed.append(("restart_deployment", deployment, namespace)) or {"status": "ok"}
        ),
    )
    try:
        with TestClient(api.app) as client:

            async def seed() -> None:
                await investigation_history.record(
                    "inv-st", "why?", status="completed",
                    report={"confidence": "high"},
                )

            asyncio.run(seed())
            r = client.post(
                "/remediate",
                json={
                    "action": "restart_deployment",
                    "target": "checkout-svc",
                    "thread_id": "inv-st",
                    "namespace": "whatever-ns",
                },
            )
        assert r.status_code == 200, r.text
        assert executed == [("restart_deployment", "checkout-svc", "whatever-ns")]
    finally:
        _reset()


# --------------------------------------------------------------------------- #
# Background investigations must run as the incident's tenant (_tenant_for_org).
# --------------------------------------------------------------------------- #
def test_tenant_for_org_rebuilds_the_orgs_credentials(mt):
    cfgs = asyncio.run(api._tenant_for_org(mt["acme_org"]))
    assert cfgs is not None
    assert cfgs.org_id == mt["acme_org"]
    assert cfgs.integration_secrets.get("KUBE_NAMESPACE") == ACME_NS
    # Least privilege: a background job has no human actor whose seat could be
    # consulted, so it may run an investigation and nothing more.
    assert cfgs.role == "responder"


def test_tenant_for_org_returns_none_for_an_unknown_org(mt):
    assert asyncio.run(api._tenant_for_org("no-such-org")) is None


def test_triggered_investigation_runs_as_the_incidents_tenant(mt, monkeypatch):
    """The worker drains the queue from a lifespan task with no request context,
    so without this the run used the deployment's own credentials and filed the
    result under "default"."""
    seen: list[object] = []

    async def _fake_investigate(incident: dict) -> None:
        seen.append(tenant_context.get_tenant())

    monkeypatch.setattr(api, "_investigate_incident", _fake_investigate)
    asyncio.run(api._run_triggered_investigation({"id": "42", "org": mt["acme_org"]}))
    assert len(seen) == 1
    assert seen[0] is not None
    assert seen[0].org_id == mt["acme_org"]
    assert seen[0].integration_secrets.get("KUBE_NAMESPACE") == ACME_NS


def test_triggered_investigation_is_refused_when_the_org_cannot_be_resolved(mt, monkeypatch):
    """Running it with no tenant is the failure being fixed, not a degraded
    success — so the job is refused and the reason is recorded."""
    ran: list[dict] = []
    outcomes: list[tuple] = []

    async def _fake_investigate(incident: dict) -> None:
        ran.append(incident)

    async def _record_outcome(incident, result, error: str = "") -> None:
        outcomes.append((incident, error))

    monkeypatch.setattr(api, "_investigate_incident", _fake_investigate)
    monkeypatch.setattr(
        api.webhook_receivers, "record_incident_outcome", _record_outcome
    )
    asyncio.run(api._run_triggered_investigation({"id": "43", "org": "ghost-org"}))
    assert ran == []
    assert len(outcomes) == 1
    assert "ghost-org" in outcomes[0][1]


def test_deployment_level_trigger_without_an_org_still_runs(mt, monkeypatch):
    """The SLO poller belongs to no org. There is no tenant to restore, so the
    run proceeds on the deployment's own configuration rather than being refused
    — the refusal above is for an org that was named and could not be found."""
    seen: list[object] = []

    async def _fake_investigate(incident: dict) -> None:
        seen.append(tenant_context.get_tenant())

    monkeypatch.setattr(api, "_investigate_incident", _fake_investigate)
    asyncio.run(api._run_triggered_investigation({"id": "slo-checkout"}))
    assert seen == [None]


# --------------------------------------------------------------------------- #
# The UNMEASURED tier in app/autonomy.py.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("supplied", ["", "   ", None, "unknown", "very-high", "HIGHISH", "0.92"])
def test_a_confidence_that_is_not_a_tier_is_refused_as_unmeasured(supplied):
    """Anything that is not one of the three real tiers means nobody measured a
    confidence for this remediation. Reporting that as a tier too low to act on
    would claim a measurement that never happened."""
    ok, why = is_eligible("restart_deployment", supplied, True)
    assert ok is False
    assert "no investigation confidence was measured" in why
    # It must NOT be described as a measured tier that fell short.
    assert "not high enough to act autonomously" not in why


def test_the_unmeasured_reason_quotes_what_the_caller_actually_sent():
    _, why = is_eligible("restart_deployment", "very-high", True)
    assert "'very-high'" in why
    _, blank = is_eligible("restart_deployment", "", True)
    assert repr(UNMEASURED) in blank  # nothing to quote — say "unmeasured", not "''"


def test_a_measured_but_weak_tier_says_so_and_names_the_tier():
    for tier in ("low", "medium"):
        ok, why = is_eligible("restart_deployment", tier, True)
        assert ok is False
        assert f"'{tier}'" in why
        assert "not high enough to act autonomously" in why
        assert "no investigation confidence was measured" not in why


def test_high_confidence_is_accepted_however_it_is_cased():
    for tier in ("high", "HIGH", "  High "):
        assert is_eligible("rollback_deployment", tier, True) == (True, "")


def test_autonomy_tiers_are_the_policy_tiers():
    """`is_eligible` refuses anything outside CONFIDENCE_TIERS, so a tier the
    policy engine can produce but this tuple omits would have a real, measured
    confidence reported as one nobody measured."""
    assert set(CONFIDENCE_TIERS) == set(get_args(policy.Confidence))
    assert UNMEASURED not in CONFIDENCE_TIERS
    # Every tier the policy engine can produce must be recognised *as* a tier —
    # refused for being too weak, never for being absent.
    for tier in get_args(policy.Confidence):
        _, why = is_eligible("restart_deployment", tier, True)
        assert "no investigation confidence was measured" not in why, tier


# --------------------------------------------------------------------------- #
# The read-only allowlist must not drift from the tools we actually ship.
# --------------------------------------------------------------------------- #
def _shipped_tool_names() -> set[str]:
    """Every tool name the bundled MCP servers register, read off the servers.

    This walks `app/mcp/servers/*/server.py` and asks each FastMCP instance what
    it exposes, so the set is whatever the code really ships rather than a second
    hand-written list that could drift in the same way.
    """
    import app.mcp.servers as servers_pkg

    names: set[str] = set()
    for module in pkgutil.iter_modules(servers_pkg.__path__):
        server = importlib.import_module(f"app.mcp.servers.{module.name}.server")
        names |= {tool.name for tool in asyncio.run(server.mcp.list_tools())}
    return names


def test_every_shipped_tool_has_an_explicit_policy_verdict():
    """`_READ_ONLY` is hand-enumerated and the default is fail-closed, so a
    genuinely read-only tool omitted from it starts demanding human approval on
    every investigation that uses it — a silent, invisible regression. This
    cross-checks the hand-written verdicts against the real tool surface."""
    shipped = _shipped_tool_names()
    assert shipped, "no MCP tools were discovered — the enumeration itself broke"
    unclassified = sorted(shipped - policy.classified_tools())
    assert unclassified == [], (
        "these shipped MCP tools have no policy verdict and would be gated as "
        f"UNCLASSIFIED writes: {unclassified}"
    )


def test_no_policy_entry_names_a_tool_we_no_longer_ship():
    """The other direction: a stale entry is a verdict for a tool that does not
    exist, which hides that its replacement was never classified."""
    stale = sorted(policy.classified_tools() - _shipped_tool_names())
    assert stale == [], f"policy classifies tools no server exposes: {stale}"


def test_the_two_verdict_tables_do_not_overlap():
    """A tool in both would get whichever verdict `classify` happens to check
    first — here `_POLICY` — so a read-only listing could mask a write."""
    assert set(policy._POLICY).isdisjoint(policy._READ_ONLY)


def test_every_read_only_tool_classifies_as_allow():
    for tool in policy._READ_ONLY:
        verdict = policy.classify(tool)
        assert verdict["decision"] == "allow", tool
        assert verdict["write"] is False, tool
