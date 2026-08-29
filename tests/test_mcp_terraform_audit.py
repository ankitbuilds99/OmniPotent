"""Regression tests for the Terraform MCP server.

Two defects are covered here.

The first is the repo-wide fixture-labelling invariant (app/mcp/fixtures.mark_demo,
enforced for the other connectors by tests/test_mcp_demo_labelling.py): with no
TERRAFORM_CLOUD_TOKEN configured — the default — every terraform read tool
returned bundled fixtures shaped exactly like Terraform Cloud responses, so an
invented workspace list, an invented state serial and an invented errored run
entered investigations as measured fact.

The second is live mode: Terraform Cloud answers the JSON-plan endpoint with a
307 to a temporary archivist URL, which is the documented success path, but the
request did not follow redirects and raise_for_status() rejected the 3xx, so
get_plan_output could never return a plan against a real organization.
"""

from __future__ import annotations

import httpx
import pytest

from app.mcp import fixtures
from app.mcp.servers.terraform import server as tf

PLAN_JSON = '{"format_version":"1.1","resource_changes":[{"address":"aws_ecs_service.checkout"}]}'
PLAN_LOG = "Terraform will perform the following actions:\n  ~ aws_ecs_service.checkout\n"
ARCHIVIST = "https://archivist.terraform.io/v1/object/plan-blob"
LOG_URL = "https://archivist.terraform.io/v1/object/plan-log"


def _labelled(record: dict) -> bool:
    return record.get(fixtures.DEMO_KEY) is True and "SIMULATED" in record.get(
        fixtures.DEMO_NOTICE_KEY, ""
    )


# --------------------------------------------------------------------------- #
# Offline fixtures must announce themselves
# --------------------------------------------------------------------------- #
def test_offline_is_the_default_for_this_process():
    # The rest of the offline assertions are only meaningful if the test process
    # really is in fixture mode.
    assert tf.OFFLINE is True


def test_terraform_read_tools_are_labelled():
    workspaces = tf.list_workspaces()
    assert workspaces and all(_labelled(w) for w in workspaces)
    assert _labelled(tf.get_workspace("checkout-prod"))
    assert _labelled(tf.get_state("checkout-prod"))
    resources = tf.list_state_resources("checkout-prod")
    assert resources and all(_labelled(r) for r in resources)
    runs = tf.get_run_history("checkout-prod")
    assert runs and all(_labelled(r) for r in runs)
    assert _labelled(tf.get_run_details("run-abc123"))


def test_the_invented_state_and_run_carry_the_marker():
    # The specific values that used to read as live infrastructure.
    state = tf.get_state("checkout-prod")
    assert state["outputs"]["service_endpoint"] == "https://checkout.acme.com"
    assert _labelled(state)

    details = tf.get_run_details("run-abc123")
    assert details["status"] == "errored"
    assert _labelled(details)


def test_not_found_replies_are_labelled_because_they_list_invented_names():
    missing = tf.get_workspace("does-not-exist")
    assert missing["available"] == [w["name"] for w in tf._DEMO_WORKSPACES]
    assert _labelled(missing)
    assert _labelled(tf.get_run_details("run-nope"))


def test_offline_write_tools_are_labelled_as_well_as_self_describing():
    plan = tf.trigger_plan("checkout-prod")
    assert "simulated" in plan["status"]
    assert _labelled(plan)
    apply = tf.trigger_apply("run-abc123")
    assert "simulated" in apply["status"]
    assert _labelled(apply)


def test_plan_output_carries_the_notice_as_a_comment_line():
    # get_plan_output returns a bare string, so the marker goes in as a leading
    # comment the way datadog's search_logs does.
    out = tf.get_plan_output("run-abc123")
    assert out.startswith("# SIMULATED")
    assert "aws_ecs_service" in out
    assert tf.get_plan_output("run-unknown").startswith("# SIMULATED")


def test_workspaces_without_fixtures_report_unmeasured_not_empty():
    # "api-gateway" exists in the workspace list but has no fixture state or run
    # history; a bare [] would read as a measured "nothing here".
    resources = tf.list_state_resources("api-gateway")
    assert resources[0]["resources"] == "unmeasured"
    runs = tf.get_run_history("api-gateway")
    assert runs[0]["runs"] == "unmeasured"


def test_marking_does_not_contaminate_the_module_fixture_tables():
    tf.list_workspaces()
    tf.get_run_history("checkout-prod")
    assert all(fixtures.DEMO_KEY not in w for w in tf._DEMO_WORKSPACES)
    assert all(fixtures.DEMO_KEY not in r for r in tf._DEMO_RUN_HISTORY)


# --------------------------------------------------------------------------- #
# Live mode: the 307 to archivist is the success path
# --------------------------------------------------------------------------- #
def _install_httpx(monkeypatch, handler, seen=None):
    """Route the server's module-level httpx.get through a MockTransport.

    Real httpx redirect semantics are used rather than a hand-rolled stub, so the
    test fails for the same reason production did if follow_redirects is dropped.
    """

    def _get(url, headers=None, params=None, timeout=None, follow_redirects=False):
        if seen is not None:
            seen.append((str(url), follow_redirects))
        with httpx.Client(
            transport=httpx.MockTransport(handler), follow_redirects=follow_redirects
        ) as client:
            return client.get(url, headers=headers or {}, params=params)

    monkeypatch.setattr(tf, "OFFLINE", False)
    monkeypatch.setattr(httpx, "get", _get)


def _run_response() -> httpx.Response:
    return httpx.Response(
        200,
        json={"data": {"id": "run-abc123", "relationships": {"plan": {"data": {"id": "plan-1"}}}}},
    )


def test_get_plan_output_follows_the_archivist_redirect(monkeypatch):
    archivist_auth: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/runs/run-abc123":
            return _run_response()
        if request.url.path == "/api/v2/plans/plan-1/json-output":
            return httpx.Response(307, headers={"Location": ARCHIVIST})
        if str(request.url) == ARCHIVIST:
            archivist_auth.append(request.headers.get("Authorization"))
            return httpx.Response(200, text=PLAN_JSON)
        raise AssertionError(f"unexpected request: {request.url}")

    _install_httpx(monkeypatch, handler)

    assert tf.get_plan_output("run-abc123") == PLAN_JSON
    # httpx strips Authorization across origins; the archivist URL is pre-signed.
    assert archivist_auth == [None]


def test_get_plan_output_asks_httpx_to_follow_redirects(monkeypatch):
    seen: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/runs/run-abc123":
            return _run_response()
        if request.url.path == "/api/v2/plans/plan-1/json-output":
            return httpx.Response(307, headers={"Location": ARCHIVIST})
        return httpx.Response(200, text=PLAN_JSON)

    _install_httpx(monkeypatch, handler, seen=seen)
    tf.get_plan_output("run-abc123")

    json_output = [entry for entry in seen if entry[0].endswith("/json-output")]
    assert json_output and all(follow is True for _, follow in json_output)


def test_get_plan_output_reports_a_still_running_plan_instead_of_an_empty_diff(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/runs/run-abc123":
            return _run_response()
        return httpx.Response(204)

    _install_httpx(monkeypatch, handler)

    out = tf.get_plan_output("run-abc123")
    assert out.strip()
    assert "204" in out and "still running" in out


def test_get_plan_output_fallback_returns_the_log_body_not_the_signed_url(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/runs/run-abc123":
            return _run_response()
        if request.url.path == "/api/v2/plans/plan-1/json-output":
            return httpx.Response(422)
        if request.url.path == "/api/v2/plans/plan-1":
            return httpx.Response(
                200, json={"data": {"attributes": {"log-read-url": LOG_URL}}}
            )
        if str(request.url) == LOG_URL:
            return httpx.Response(200, text=PLAN_LOG)
        raise AssertionError(f"unexpected request: {request.url}")

    _install_httpx(monkeypatch, handler)

    out = tf.get_plan_output("run-abc123")
    assert out == PLAN_LOG
    assert LOG_URL not in out


def test_get_plan_output_says_which_step_failed(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/runs/run-abc123":
            return _run_response()
        if request.url.path == "/api/v2/plans/plan-1/json-output":
            return httpx.Response(404)
        if request.url.path == "/api/v2/plans/plan-1":
            return httpx.Response(200, json={"data": {"attributes": {}}})
        raise AssertionError(f"unexpected request: {request.url}")

    _install_httpx(monkeypatch, handler)

    out = tf.get_plan_output("run-abc123")
    assert "no log-read-url" in out


def test_get_plan_output_still_raises_on_a_real_server_error(monkeypatch):
    # Following redirects must not swallow genuine failures.
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/runs/run-abc123":
            return _run_response()
        return httpx.Response(500, text="boom")

    _install_httpx(monkeypatch, handler)

    with pytest.raises(httpx.HTTPStatusError):
        tf.get_plan_output("run-abc123")
