"""Offline MCP fixtures must be labelled as simulated.

Every connector falls back to bundled fixtures when its credentials are absent.
That fallback is deliberate — it is what makes the agent runnable with no
external accounts — but the responses used to be shaped exactly like live ones.
An agent reading them could not tell that its root-cause analysis was built on
invented pods, invented incidents, and invented error rates.
"""

from app.mcp import fixtures
from app.mcp.servers.argocd import server as argocd
from app.mcp.servers.datadog import server as datadog
from app.mcp.servers.kubernetes import server as k8s
from app.mcp.servers.pagerduty import server as pagerduty
from app.mcp.servers.sentry import server as sentry


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
