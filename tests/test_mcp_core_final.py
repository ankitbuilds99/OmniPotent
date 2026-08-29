"""The invariants the MCP health report's consumers rely on.

`tests/test_mcp_core_audit.py` covers the three defects on the live path. These
are the properties the surfaces downstream of it depend on and that a later edit
to `check_all` could break without failing any of those tests:

- /healthz publishes healthy/degraded/unhealthy and nothing else, so those three
  must partition `servers` exactly. A row in `servers` with no verdict makes the
  published counts silently not add up to the number of servers.
- app/status_page/store.py renders one public component per row of `servers` and
  maps STOPPED to MAJOR_OUTAGE, so an idle deployment must not put anything there
  — while still being able to say the server exists and what it did.
- `copilot_mcp_tool_errors_total` carries the failure as a *label*, so its value
  set has to be closed even when the caller is not app/mcp/client.py.
"""

from __future__ import annotations

from contextlib import AsyncExitStack

import pytest

import app.config as cfg
import app.mcp.client as mcp_client
import app.mcp.health as mcp_health
from app import runtime
from app.mcp.health import MCPHealthMonitor, MCPServerStatus


@pytest.fixture
def monitor(monkeypatch) -> MCPHealthMonitor:
    """A private health monitor, installed as the global one so the surfaces
    that call `get_health_monitor()` see this test's servers and no others."""
    fresh = MCPHealthMonitor()
    monkeypatch.setattr(mcp_health, "_monitor", fresh)
    return fresh


@pytest.fixture
def isolated_runtime(monkeypatch):
    monkeypatch.setattr(runtime, "_ov", dict(runtime._ov))
    monkeypatch.setattr(runtime, "_keys", dict(runtime._keys))


# --------------------------------------------------------------------------
# The measured rows partition into the three counts /healthz publishes.
# --------------------------------------------------------------------------


async def test_the_three_counts_add_up_to_the_measured_rows(monitor):
    """/healthz reports healthy + degraded + unhealthy and no other bucket. Any
    row in `servers` that is neither is a server the endpoint mentions in its
    total and in none of its counts."""
    monitor.register_session("repo", tool_count=6)
    monitor.record_tool_call("repo", "list_dir", 5.0, success=True)

    monitor.register_session("slow", tool_count=1)
    monitor.record_tool_call("slow", "scan", 30_000.0, success=True)

    monitor.register_session("wedged", tool_count=1)
    monitor.record_tool_call(
        "wedged", "wedge", 60_000.0, success=False,
        error="McpError: Timed out while waiting for response", health_signal=True,
    )

    monitor.record_start_failure("datadog", "ValueError: bad DD_API_KEY")

    summary = await monitor.check_all()

    assert (
        summary.healthy_count + summary.degraded_count + summary.unhealthy_count
        == len(summary.servers)
        == summary.measured_count
    )
    assert {s.status for s in summary.servers} == {
        MCPServerStatus.HEALTHY,
        MCPServerStatus.DEGRADED,
        MCPServerStatus.UNHEALTHY,
    }


async def test_a_start_failure_with_no_reason_still_has_a_verdict(monitor):
    """`record_start_failure` is called from a `except` block, so its reason is
    whatever `str(exc)` gave — which is empty for plenty of exceptions. The row
    is in `servers` either way, so a blank reason must not demote it to STOPPED
    and break the partition; it stays unhealthy and says the reason is missing."""
    monitor.record_start_failure("mute", "")

    summary = await monitor.check_all()
    mute = {s.name: s for s in summary.servers}["mute"]

    assert mute.status is MCPServerStatus.UNHEALTHY
    assert mute.error_message, "an unhealthy row with no reason is a dead end"
    assert summary.unhealthy_count == 1
    assert (
        summary.healthy_count + summary.degraded_count + summary.unhealthy_count
        == len(summary.servers)
    )


async def test_a_stopped_server_is_reported_but_never_as_a_verdict(monitor):
    """Both halves of the rule at once: the idle server is still in the report,
    with its history, and is in neither the counts nor the measured rows."""
    monitor.register_session("repo", tool_count=6)
    monitor.record_tool_call("repo", "list_dir", 5.0, success=True)
    monitor.release_session("repo")

    summary = await monitor.check_all()

    assert summary.servers == []
    assert summary.measured_count == 0
    assert summary.overall_status is MCPServerStatus.UNKNOWN
    assert summary.healthy_count == summary.degraded_count == summary.unhealthy_count == 0

    idle = {s.name: s for s in summary.unmeasured}["repo"]
    assert idle.status is MCPServerStatus.STOPPED
    assert idle.tool_calls_total == 1
    # The call really happened; a closed session is not a reason to report it as
    # zero.
    assert summary.total_tool_calls == 1

    published = summary.to_dict()
    assert published["measured_count"] == 0
    assert published["unmeasured_count"] == 1
    assert [s["name"] for s in published["unmeasured"]] == ["repo"]


# --------------------------------------------------------------------------
# The public status page is the surface the STOPPED mapping actually burned.
# --------------------------------------------------------------------------


async def test_an_idle_deployment_publishes_no_mcp_outage(monitor):
    """The regression end to end: `_map_mcp_status` sends STOPPED to
    MAJOR_OUTAGE, so with the idle rows in `servers` every server the deployment
    had ever used was announced as a major outage the moment the last
    CopilotSession closed."""
    from app.status_page.store import _collect_mcp_statuses
    from app.status_page.models import ComponentStatus

    monitor.register_session("repo", tool_count=6)
    monitor.register_session("datadog", tool_count=4)
    monitor.record_tool_call("repo", "list_dir", 5.0, success=True)

    live = await _collect_mcp_statuses()
    assert {cid: s for cid, (_n, s) in live.items()} == {
        "mcp_repo": ComponentStatus.OPERATIONAL,
        "mcp_datadog": ComponentStatus.OPERATIONAL,
    }

    monitor.release_session("repo")
    monitor.release_session("datadog")

    idle = await _collect_mcp_statuses()
    assert idle == {}, f"idle servers published as public components: {idle}"


async def test_a_refused_action_publishes_no_mcp_outage(monitor):
    """The other regression on the same surface: MCP reports a policy refusal
    with `isError`, exactly as it reports a broken pipe, so counting refusals as
    errors put a working server past the 50% threshold and onto MAJOR_OUTAGE for
    the life of the process."""
    from app.status_page.store import _collect_mcp_statuses
    from app.status_page.models import ComponentStatus

    monitor.register_session("repo", tool_count=6)
    for _ in range(4):
        monitor.record_tool_call(
            "repo", "read_file", 3.0, success=False,
            error="ToolException: /etc/passwd escapes the repository sandbox",
            health_signal=False,
        )

    statuses = await _collect_mcp_statuses()
    assert statuses["mcp_repo"][1] is ComponentStatus.OPERATIONAL

    server = await monitor.check_server("repo")
    assert server.tool_faults_total == 4
    assert server.tool_errors_total == 0
    # The refusals are not invisible either — the reason is on the row.
    assert "escapes the repository sandbox" in (server.last_fault_message or "")


async def test_a_server_that_never_answers_still_publishes_an_outage(monitor):
    """The rule has to keep working in the direction it is meant to fire in."""
    from app.status_page.store import _collect_mcp_statuses
    from app.status_page.models import ComponentStatus

    monitor.register_session("repo", tool_count=6)
    monitor.record_tool_call(
        "repo", "read_file", 60_000.0, success=False,
        error="McpError: Timed out while waiting for response", health_signal=True,
    )

    statuses = await _collect_mcp_statuses()
    assert statuses["mcp_repo"][1] is ComponentStatus.MAJOR_OUTAGE


# --------------------------------------------------------------------------
# The Prometheus error label stays a closed set, whoever calls.
# --------------------------------------------------------------------------


def test_the_metric_refuses_a_message_as_a_label_value():
    """`record_mcp_tool_call` is exported from app.observability and reachable
    from anywhere. A caller that passes an exception message must not mint a
    series per distinct message — the message quotes what the model asked for."""
    from app.observability import metrics

    metrics._registry.reset()
    for path in ("/etc/passwd", "/var/log/app.log", "../../secrets.env"):
        metrics.record_mcp_tool_call(
            "repo", "read_file", 0.01, success=False,
            error=f"ToolException: {path} escapes the repository sandbox",
        )

    rendered = metrics._registry.render_prometheus()
    assert "/etc/passwd" not in rendered
    assert "secrets.env" not in rendered
    error_series = [
        line for line in rendered.splitlines()
        if line.startswith("copilot_mcp_tool_errors_total{")
    ]
    assert len(error_series) == 1, f"one series per message: {error_series}"
    # Bucketed, not dropped: all three failures are still counted.
    assert float(error_series[0].rsplit(" ", 1)[1]) == 3.0

    metrics._registry.reset()


def test_a_classified_kind_reaches_prometheus_unchanged():
    """The bound must not eat the value the alert rules group by."""
    from app.observability import metrics

    metrics._registry.reset()
    for kind in sorted(mcp_client._ERROR_KINDS):
        metrics.record_mcp_tool_call(
            "repo", "read_file", 0.01, success=False, error=kind
        )

    rendered = metrics._registry.render_prometheus()
    for kind in mcp_client._ERROR_KINDS:
        assert f'error="{kind}"' in rendered

    metrics._registry.reset()


_REFUSING_SERVER = """
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("sandbox")

    @mcp.tool()
    def read_file(path: str) -> str:
        raise ValueError(f"{path} escapes the repository sandbox")

    mcp.run()
    """


async def test_a_refusal_on_the_real_path_labels_the_kind_not_the_path(
    monkeypatch, tmp_path, monitor, isolated_runtime
):
    """The same guarantee through a real stdio server, where the message is
    genuinely model-controlled: the refused path appears in the operator-facing
    reason on the health row and nowhere in the metrics registry."""
    import sys

    from app.marketplace import manager
    from app.observability import metrics

    monkeypatch.setenv("COPILOT_PROVIDER", "anthropic")
    cfg.get_settings.cache_clear()

    script = tmp_path / "sandbox_server.py"
    script.write_text(__import__("textwrap").dedent(_REFUSING_SERVER))
    monkeypatch.setattr(
        manager,
        "installed_server_configs",
        lambda py: {
            "sandbox": {
                "command": sys.executable,
                "args": [str(script)],
                "transport": "stdio",
                "env": {},
            }
        },
    )
    real = mcp_client._server_config
    monkeypatch.setattr(
        mcp_client, "_server_config", lambda: {"sandbox": real()["sandbox"]}
    )

    metrics._registry.reset()
    async with AsyncExitStack() as stack:
        tools, _client = await mcp_client.load_mcp_tools(stack)
        read_file = {t.name: t for t in tools}["read_file"]
        for path in ("/etc/passwd", "/etc/shadow", "../../id_rsa"):
            with pytest.raises(Exception):
                await read_file.ainvoke({"path": path})

        server = {s.name: s for s in (await monitor.check_all()).servers}["sandbox"]

    assert server.status is MCPServerStatus.HEALTHY
    assert server.tool_calls_total == 3
    assert server.tool_errors_total == 0
    assert "id_rsa" in (server.last_fault_message or "")

    rendered = metrics._registry.render_prometheus()
    assert 'error="tool_error"' in rendered
    for path in ("/etc/passwd", "/etc/shadow", "id_rsa"):
        assert path not in rendered

    metrics._registry.reset()
    cfg.get_settings.cache_clear()
