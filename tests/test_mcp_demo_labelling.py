"""Offline MCP fixtures must be labelled as simulated — and demo-gated.

Every connector falls back to bundled fixtures when its credentials are absent.
That fallback is what makes the agent runnable with no external accounts, but
the responses used to be shaped exactly like live ones. An agent reading them
could not tell that its root-cause analysis was built on invented pods,
invented incidents, and invented error rates.

Two invariants are enforced here:
  1. fixtures are served ONLY under COPILOT_DEMO_MODE (fail-closed contract,
     app/config.py) — otherwise the tool returns a not-configured error;
  2. when they are served, every record carries the SIMULATED marker.
"""

import pytest

from app.mcp import fixtures
from app.mcp.servers.argocd import server as argocd
from app.mcp.servers.datadog import server as datadog
from app.mcp.servers.kubernetes import server as k8s
from app.mcp.servers.pagerduty import server as pagerduty
from app.mcp.servers.sentry import server as sentry


@pytest.fixture(autouse=True)
def _demo_mode(monkeypatch):
    """Fixture-serving now requires demo mode; the gate reads env per call."""
    monkeypatch.setenv("COPILOT_DEMO_MODE", "true")


def _labelled(record: dict) -> bool:
    return record.get(fixtures.DEMO_KEY) is True and "SIMULATED" in record.get(
        fixtures.DEMO_NOTICE_KEY, ""
    )


def test_mark_demo_labels_dicts_and_lists_of_dicts():
    assert _labelled(fixtures.mark_demo({"a": 1}, "test"))
    marked = fixtures.mark_demo([{"a": 1}, {"b": 2}], "test")
    assert all(_labelled(r) for r in marked)


def test_mark_demo_leaves_scalar_lists_alone():
    # list_services returns list[str]; a marker element would be read as a service.
    assert fixtures.mark_demo(["a", "b"], "test") == ["a", "b"]
    assert fixtures.mark_demo(None, "test") is None
    assert fixtures.mark_demo("plain", "test") == "plain"


def test_mark_demo_does_not_mutate_the_shared_fixture_tables():
    # get_fixture() hands out references into the module-level scenario tables;
    # mutating in place would contaminate every later call in the process.
    pods = fixtures.get_fixture("kubernetes", "pods")
    fixtures.mark_demo(pods, "kubernetes")
    assert all(fixtures.DEMO_KEY not in p for p in pods)


def test_kubernetes_read_tools_are_labelled():
    pods = k8s.list_pods()
    assert pods and all(_labelled(p) for p in pods)
    assert _labelled(k8s.describe_pod(pods[0]["name"]))
    assert all(_labelled(e) for e in k8s.get_events())
    name = k8s._get_deployment()["name"]
    assert _labelled(k8s.get_deployment_status(name))
    assert all(_labelled(r) for r in k8s.rollout_history(name))


def test_pagerduty_read_tools_are_labelled():
    incidents = pagerduty.list_incidents()
    assert incidents and all(_labelled(i) for i in incidents)
    assert _labelled(pagerduty.get_incident(""))
    assert all(_labelled(a) for a in pagerduty.get_incident_alerts(""))


def test_sentry_read_tools_are_labelled():
    issues = sentry.list_issues()
    assert issues and all(_labelled(i) for i in issues)
    assert _labelled(sentry.get_issue(issues[0]["id"]))


def test_argocd_read_tools_are_labelled(anyio_backend=None):
    import asyncio

    apps = asyncio.run(argocd.list_applications())
    assert apps and all(_labelled(a) for a in apps)
    assert _labelled(asyncio.run(argocd.get_application("checkout-svc")))
    assert _labelled(asyncio.run(argocd.get_sync_status("checkout-svc")))
    assert all(_labelled(h) for h in asyncio.run(argocd.get_application_history("checkout-svc")))


def test_datadog_read_tools_are_labelled():
    assert _labelled(datadog.get_error_summary())
    assert _labelled(datadog.get_metric("checkout-svc", "error_rate_5xx"))
    # search_logs returns list[str]; the notice is a leading comment line.
    lines = datadog.search_logs(limit=3)
    assert lines[0].startswith("# SIMULATED")


def test_notice_names_the_active_scenario():
    notice = fixtures.demo_notice("kubernetes")
    assert fixtures.get_scenario() in notice
    assert "kubernetes" in notice


# --------------------------------------------------------------------------- #
# Fail closed outside demo mode (app/config.py's contract): credential absence
# alone must never fabricate a payload.
# --------------------------------------------------------------------------- #
def _is_gate_error(record: dict, service: str, env_vars: str) -> bool:
    return record == {
        "error": f"{service} is not configured (set {env_vars}); "
        "demo fixtures are disabled outside COPILOT_DEMO_MODE"
    }


def test_offline_gate_helper_contract(monkeypatch):
    monkeypatch.delenv("COPILOT_DEMO_MODE", raising=False)
    err = fixtures.offline_gate("PagerDuty", "PAGERDUTY_API_TOKEN")
    assert _is_gate_error(err, "PagerDuty", "PAGERDUTY_API_TOKEN")
    for truthy in ("1", "true", "YES", "on"):
        monkeypatch.setenv("COPILOT_DEMO_MODE", truthy)
        assert fixtures.offline_gate("PagerDuty", "PAGERDUTY_API_TOKEN") is None
    monkeypatch.setenv("COPILOT_DEMO_MODE", "false")
    assert fixtures.offline_gate("PagerDuty", "PAGERDUTY_API_TOKEN") is not None


def test_reads_fail_closed_without_demo_mode(monkeypatch):
    monkeypatch.delenv("COPILOT_DEMO_MODE", raising=False)
    assert _is_gate_error(k8s.list_pods()[0], "Kubernetes", "KUBE_CONFIG_PATH")
    assert _is_gate_error(pagerduty.get_incident(""), "PagerDuty", "PAGERDUTY_API_TOKEN")
    assert _is_gate_error(sentry.list_issues()[0], "Sentry", "SENTRY_API_TOKEN")
    assert _is_gate_error(datadog.get_error_summary(), "Datadog", "DD_API_KEY and DD_APP_KEY")

    import asyncio

    assert _is_gate_error(
        asyncio.run(argocd.list_applications())[0],
        "ArgoCD", "ARGOCD_SERVER_URL and ARGOCD_AUTH_TOKEN",
    )


def test_simulated_writes_fail_closed_without_demo_mode(monkeypatch):
    monkeypatch.delenv("COPILOT_DEMO_MODE", raising=False)
    assert "not configured" in k8s.scale_deployment("checkout-svc", 3)["error"]
    assert "not configured" in pagerduty.resolve_incident("PINC4242")["error"]


def test_simulated_writes_are_labelled_in_demo_mode():
    res = k8s.scale_deployment("checkout-svc", 3)
    assert "simulated" in res["status"] and _labelled(res)
    note = pagerduty.add_incident_note("PINC4242", "RCA")
    assert "simulated" in note["status"] and _labelled(note)


def test_fake_write_ids_are_obviously_fake_in_demo_mode():
    from app.mcp.servers.jira import server as jira
    from app.mcp.servers.linear import server as linear
    from app.mcp.servers.opsgenie import server as opsgenie

    created = jira.create_issue("s")
    assert created["key"].startswith("SIMULATED-") and _labelled(created)
    created = linear.create_issue("t")
    assert created["identifier"].startswith("SIMULATED-") and _labelled(created)
    created = opsgenie.create_alert("m")
    assert created["id"].startswith("SIMULATED-") and _labelled(created)
