"""MCP client/health regressions: provider drift, missing read timeout, dead monitor.

Three defects, all on the live path `CopilotSession` takes when it builds a turn:

1. `load_mcp_tools` (and the report node) decided "is this Groq?" from the frozen
   .env value while `llm.py` built the model from `runtime.provider()`. Switching
   provider in the UI, or serving a tenant with its own provider, made the two
   disagree: an Anthropic model with zero tools, or a Groq model handed all ~22
   servers' tools.
2. MCP sessions were created with no read timeout, so a wedged-but-alive server
   subprocess parked the turn forever.
3. `MCPHealthMonitor` had no producer at all, so /healthz and the status page
   reported MCP health that nothing had ever measured.

The tests here start real stdio MCP servers rather than mocking the transport,
because every one of these defects lives in the wiring between the app and that
transport.
"""

from __future__ import annotations

import asyncio
import textwrap
import time
from contextlib import AsyncExitStack

import pytest

import app.config as cfg
import app.mcp.client as mcp_client
import app.mcp.health as mcp_health
from app import runtime
from app.mcp.health import MCPHealthMonitor, MCPServerStatus


@pytest.fixture
def monitor(monkeypatch) -> MCPHealthMonitor:
    """A private health monitor, so one test's servers are not another's."""
    fresh = MCPHealthMonitor()
    monkeypatch.setattr(mcp_health, "_monitor", fresh)
    return fresh


@pytest.fixture
def isolated_runtime(monkeypatch):
    """Copy the process-global provider overrides so `set_model` cannot leak."""
    monkeypatch.setattr(runtime, "_ov", dict(runtime._ov))
    monkeypatch.setattr(runtime, "_keys", dict(runtime._keys))


def _only(monkeypatch, *names: str) -> None:
    """Restrict `load_mcp_tools` to the named servers.

    The real `_server_config()` still runs — that is where the session read
    timeout is attached — and only its result is filtered, so the tests exercise
    the production config rather than a hand-built stand-in.
    """
    real = mcp_client._server_config

    def filtered() -> dict:
        full = real()
        return {n: full[n] for n in names if n in full}

    monkeypatch.setattr(mcp_client, "_server_config", filtered)


def _install_marketplace_server(monkeypatch, name: str, script: str, tmp_path) -> None:
    """Publish a throwaway stdio MCP server as an installed marketplace agent, so
    it flows through the real `_server_config()` merge."""
    import sys

    from app.marketplace import manager

    path = tmp_path / f"{name}_server.py"
    path.write_text(textwrap.dedent(script))
    monkeypatch.setattr(
        manager,
        "installed_server_configs",
        lambda py: {
            name: {
                "command": sys.executable,
                "args": [str(path)],
                "transport": "stdio",
                "env": {},
            }
        },
    )


# --------------------------------------------------------------------------
# 1. The Groq gate must follow the provider actually in use.
# --------------------------------------------------------------------------


async def test_ui_provider_switch_off_groq_gets_its_tools_back(
    isolated_runtime, monkeypatch, monitor
):
    """.env says groq, the operator switched the UI to Anthropic: tools must load.

    Reading `get_settings().copilot_provider` here returned [] — an Anthropic
    model with no tools at all.
    """
    monkeypatch.setenv("COPILOT_PROVIDER", "groq")
    cfg.get_settings.cache_clear()
    runtime.set_model("anthropic", "sk-ant-test")
    assert cfg.get_settings().copilot_provider == "groq"
    assert runtime.provider() == "anthropic"

    _only(monkeypatch, "repo")
    async with AsyncExitStack() as stack:
        tools, _client = await mcp_client.load_mcp_tools(stack)
        assert [t.name for t in tools], "no tools loaded for a non-Groq runtime provider"

    cfg.get_settings.cache_clear()


async def test_runtime_switch_to_groq_still_gets_no_tools(
    isolated_runtime, monkeypatch, monitor
):
    """The mirror case: an Anthropic .env with a Groq runtime provider must hit
    the chat-only gate, not hand a 6K-TPM model 22 servers' tool definitions."""
    monkeypatch.setenv("COPILOT_PROVIDER", "anthropic")
    cfg.get_settings.cache_clear()
    runtime.set_model("groq", "gsk-test")

    _only(monkeypatch, "repo")
    async with AsyncExitStack() as stack:
        tools, _client = await mcp_client.load_mcp_tools(stack)
        assert tools == []

    cfg.get_settings.cache_clear()


def test_report_node_skips_structured_output_for_the_runtime_provider(
    isolated_runtime, monkeypatch
):
    """`make_report_node` gated structured output on the same stale setting."""
    from langchain_core.messages import AIMessage, HumanMessage

    import app.graph.nodes as nodes

    class _FakeLLM:
        def __init__(self) -> None:
            self.structured_calls = 0

        def with_structured_output(self, *_a, **_kw):
            self.structured_calls += 1
            raise RuntimeError("forced fallback to the text path")

        def invoke(self, _msgs):
            return AIMessage(content="")

    def build(env_provider: str, ui_provider: str) -> int:
        monkeypatch.setenv("COPILOT_PROVIDER", env_provider)
        cfg.get_settings.cache_clear()
        runtime.set_model(ui_provider, "sk-test")
        fake = _FakeLLM()
        monkeypatch.setattr(nodes, "get_llm", lambda **_kw: fake)
        node = nodes.make_report_node()
        node({"messages": [HumanMessage(content="why did checkout 500?")]})
        return fake.structured_calls

    # .env groq + UI switched to Anthropic: structured output is supported now.
    assert build("groq", "anthropic") == 1
    # .env anthropic + a groq runtime provider: still skipped.
    assert build("anthropic", "groq") == 0

    cfg.get_settings.cache_clear()


# --------------------------------------------------------------------------
# 2. Every MCP session must carry a read deadline.
# --------------------------------------------------------------------------


def test_every_server_config_carries_a_read_timeout():
    config = mcp_client._server_config()
    assert config, "no MCP servers configured"
    for name, entry in config.items():
        timeout = entry.get("session_kwargs", {}).get("read_timeout_seconds")
        assert timeout is not None, f"{name} would wait forever for a response"
        assert timeout.total_seconds() > 0


_HANGING_SERVER = """
    from mcp.server.fastmcp import FastMCP
    import time

    mcp = FastMCP("hang")

    @mcp.tool()
    def wedge() -> str:
        time.sleep(3600)
        return "never"

    mcp.run()
    """


async def test_a_wedged_server_fails_the_call_instead_of_hanging_the_turn(
    monkeypatch, tmp_path, monitor, isolated_runtime
):
    """A server that answers the handshake and then never answers a tool call
    must raise once the read deadline passes.

    Without `read_timeout_seconds` the MCP SDK does `fail_after(None)` and this
    await never returns: the turn holds its thread lock for the life of the
    process and the session slot can never be evicted.
    """
    monkeypatch.setenv("COPILOT_PROVIDER", "anthropic")
    monkeypatch.setenv("HTTP_TIMEOUT_MCP", "1")
    cfg.get_settings.cache_clear()
    _install_marketplace_server(monkeypatch, "hang", _HANGING_SERVER, tmp_path)
    _only(monkeypatch, "hang")

    async with AsyncExitStack() as stack:
        tools, _client = await mcp_client.load_mcp_tools(stack)
        wedge = {t.name: t for t in tools}["wedge"]
        started = time.perf_counter()
        with pytest.raises(Exception) as caught:
            # The outer wait_for is the test's own tripwire: if the deadline is
            # gone, this surfaces as a TimeoutError instead of hanging the suite.
            await asyncio.wait_for(wedge.ainvoke({}), timeout=30)
        elapsed = time.perf_counter() - started

    assert not isinstance(caught.value, asyncio.TimeoutError), (
        "the MCP session had no read deadline; only the test's own timeout fired"
    )
    assert elapsed < 20, f"call took {elapsed:.1f}s to give up"

    cfg.get_settings.cache_clear()


# --------------------------------------------------------------------------
# 3. The health monitor must actually be fed.
# --------------------------------------------------------------------------


async def test_loading_tools_registers_the_server_and_records_its_calls(
    monkeypatch, monitor, isolated_runtime
):
    monkeypatch.setenv("COPILOT_PROVIDER", "anthropic")
    cfg.get_settings.cache_clear()
    _only(monkeypatch, "repo")

    async with AsyncExitStack() as stack:
        tools, _client = await mcp_client.load_mcp_tools(stack)

        summary = await monitor.check_all()
        repo = {s.name: s for s in summary.servers}["repo"]
        assert repo.status is MCPServerStatus.HEALTHY
        assert repo.open_sessions == 1
        # No process handle exists on the stdio path, and the report says so
        # rather than implying an unsampled pid/RSS was measured.
        assert repo.liveness_source == "session"
        assert repo.pid is None
        assert repo.memory_mb is None

        await {t.name: t for t in tools}["list_dir"].ainvoke({})
        summary = await monitor.check_all()
        repo = {s.name: s for s in summary.servers}["repo"]
        assert repo.tool_calls_total == 1
        assert repo.tool_errors_total == 0
        assert repo.avg_latency_ms > 0
        assert summary.total_tool_calls == 1

    # Torn down with the session. The server is still reported — it exists and it
    # made a call — but under `unmeasured`, because nothing is observing it now.
    # It must not appear among the measured rows: /healthz counts those and the
    # public status page renders each one as a component, mapping STOPPED to
    # MAJOR_OUTAGE, which turned the idle steady state into a public outage.
    summary = await monitor.check_all()
    assert "repo" not in {s.name for s in summary.servers}
    idle = {s.name: s for s in summary.unmeasured}["repo"]
    assert idle.status is MCPServerStatus.STOPPED
    assert idle.open_sessions == 0
    # Not amnesia either: the call it made is still counted.
    assert idle.tool_calls_total == 1
    assert summary.total_tool_calls == 1
    # Nothing is measuring MCP any more, and the summary says exactly that
    # instead of "zero servers are healthy".
    assert summary.overall_status is MCPServerStatus.UNKNOWN
    assert summary.measured_count == 0

    cfg.get_settings.cache_clear()


async def test_tool_calls_also_feed_the_prometheus_series(
    monkeypatch, monitor, isolated_runtime
):
    """`copilot_mcp_tool_*` is what the shipped alert rules query; nothing on the
    request path used to increment it, so those rules watched empty series."""
    from app.observability import metrics

    monkeypatch.setenv("COPILOT_PROVIDER", "anthropic")
    cfg.get_settings.cache_clear()
    _only(monkeypatch, "repo")

    async with AsyncExitStack() as stack:
        tools, _client = await mcp_client.load_mcp_tools(stack)
        await {t.name: t for t in tools}["list_dir"].ainvoke({})

    rendered = metrics._registry.render_prometheus()
    assert 'copilot_mcp_tool_calls_total{server="repo",status="success",tool="list_dir"}' in rendered
    assert "copilot_mcp_tool_latency_seconds_bucket" in rendered

    cfg.get_settings.cache_clear()


_FAILING_TOOL_SERVER = """
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("boom")

    @mcp.tool()
    def get_incident() -> str:
        raise RuntimeError("upstream said no")

    mcp.run()
    """


async def test_tool_failures_are_recorded_and_still_raised(
    monkeypatch, tmp_path, monitor, isolated_runtime
):
    """A tool that fails is counted as a fault, re-raised, and does not condemn
    the server.

    The server received the request, ran the tool and composed an error response:
    that is a working server delivering bad news. MCP cannot tell us more — an
    `isError` result is how the protocol reports a policy refusal, a rejected
    argument and a tool that raised alike, and langchain-mcp-adapters turns all
    three into the same ToolException — so treating them as evidence the
    subprocess is failing meant two correctly refused `read_file('/etc/passwd')`
    calls put the repo server past the 50% error threshold and pinned it on
    UNHEALTHY, i.e. MAJOR_OUTAGE on the public status page, for the life of the
    process. Only a call the server never answered counts as an error.
    """
    monkeypatch.setenv("COPILOT_PROVIDER", "anthropic")
    cfg.get_settings.cache_clear()
    _install_marketplace_server(monkeypatch, "boom", _FAILING_TOOL_SERVER, tmp_path)
    _only(monkeypatch, "boom")

    async with AsyncExitStack() as stack:
        tools, _client = await mcp_client.load_mcp_tools(stack)
        with pytest.raises(Exception):
            await {t.name: t for t in tools}["get_incident"].ainvoke({})

        server = {s.name: s for s in (await monitor.check_all()).servers}["boom"]
        assert server.tool_calls_total == 1
        assert server.tool_faults_total == 1
        assert server.tool_errors_total == 0
        assert server.status is MCPServerStatus.HEALTHY
        # Counted is not enough — the reason has to survive, or a HEALTHY row
        # with a fault count is a dead end for whoever is looking into it.
        assert "upstream said no" in (server.last_fault_message or "")

    cfg.get_settings.cache_clear()


async def test_a_transport_failure_does_condemn_the_server(monitor):
    """The other half of the same rule: a call the server never answered is a
    real health signal, and still drives the status."""
    monitor.register_session("wedged", tool_count=1)
    monitor.record_tool_call(
        "wedged", "wedge", 60_000.0, success=False,
        error="McpError: Timed out while waiting for response",
        health_signal=True,
    )

    health = await monitor.check_server("wedged")
    assert health.tool_errors_total == 1
    assert health.tool_faults_total == 0
    assert health.status is MCPServerStatus.UNHEALTHY
    assert "Timed out" in (health.error_message or "")


async def test_refusals_do_not_accumulate_into_an_outage(monitor):
    """The reproduction from the review, at the monitor's own boundary: the path
    sandbox refusing two reads must not take the server down."""
    monitor.register_session("repo", tool_count=6)
    monitor.record_tool_call("repo", "list_dir", 4.0, success=True)
    for path in ("/etc/passwd", "../../etc/hosts"):
        monitor.record_tool_call(
            "repo", "read_file", 3.0, success=False,
            error=f"ToolException: {path} escapes the repository sandbox",
            health_signal=False,
        )

    health = await monitor.check_server("repo")
    assert health.status is MCPServerStatus.HEALTHY
    assert health.tool_calls_total == 3
    assert health.tool_faults_total == 2
    assert health.tool_errors_total == 0


async def test_the_error_reaching_prometheus_is_a_closed_set_not_the_message(
    monkeypatch, tmp_path, monitor, isolated_runtime
):
    """`error` is a metric *label*. The exception message contains whatever the
    model asked for — a path, a query — so passing it through mints a new time
    series per distinct request and grows the registry with no bound and no
    eviction. Only the classified kind may reach it."""
    from app.observability import metrics

    monkeypatch.setenv("COPILOT_PROVIDER", "anthropic")
    cfg.get_settings.cache_clear()
    _install_marketplace_server(monkeypatch, "boom", _FAILING_TOOL_SERVER, tmp_path)
    _only(monkeypatch, "boom")

    seen: list[str | None] = []
    real = metrics.record_mcp_tool_call
    monkeypatch.setattr(
        metrics,
        "record_mcp_tool_call",
        lambda *a, **kw: (seen.append(kw.get("error")), real(*a, **kw))[1],
    )

    async with AsyncExitStack() as stack:
        tools, _client = await mcp_client.load_mcp_tools(stack)
        with pytest.raises(Exception):
            await {t.name: t for t in tools}["get_incident"].ainvoke({})

    assert seen, "no metric was recorded for the failed call"
    for label in seen:
        assert label in mcp_client._ERROR_KINDS, (
            f"unbounded Prometheus label from a tool error: {label!r}"
        )
        assert "upstream said no" not in (label or "")

    cfg.get_settings.cache_clear()


async def test_a_server_that_will_not_start_is_reported_with_its_reason(
    monkeypatch, monitor, isolated_runtime
):
    """"Why does the agent have no tools for X?" must have an answer on the health
    surface, not just a debug log line."""
    monkeypatch.setenv("COPILOT_PROVIDER", "anthropic")
    cfg.get_settings.cache_clear()

    real = mcp_client._server_config
    monkeypatch.setattr(
        mcp_client,
        "_server_config",
        lambda: {
            "nosuch": {
                **real()["repo"],
                "command": "/nonexistent/interpreter",
            }
        },
    )

    async with AsyncExitStack() as stack:
        tools, _client = await mcp_client.load_mcp_tools(stack)
        assert tools == []

    summary = await monitor.check_all()
    nosuch = {s.name: s for s in summary.servers}["nosuch"]
    assert nosuch.status is MCPServerStatus.UNHEALTHY
    assert "/nonexistent/interpreter" in (nosuch.error_message or "") or nosuch.error_message
    assert summary.unhealthy_count == 1

    cfg.get_settings.cache_clear()


async def test_one_session_closing_does_not_stop_a_server_another_still_uses(monitor):
    """Sessions are per-CopilotSession and several run the same server at once."""
    monitor.register_session("repo", tool_count=4)
    monitor.register_session("repo", tool_count=4)
    monitor.release_session("repo")

    still = await monitor.check_server("repo")
    assert still.status is MCPServerStatus.HEALTHY
    assert still.open_sessions == 1

    monitor.release_session("repo")
    assert (await monitor.check_server("repo")).status is MCPServerStatus.STOPPED


async def test_a_server_that_comes_up_clears_its_old_failure(monitor):
    monitor.record_start_failure("datadog", "ValueError: bad DD_API_KEY")
    assert (await monitor.check_server("datadog")).status is MCPServerStatus.UNHEALTHY

    monitor.register_session("datadog", tool_count=7)
    healed = await monitor.check_server("datadog")
    assert healed.status is MCPServerStatus.HEALTHY
    assert healed.error_message is None


async def test_an_unfed_monitor_reports_unknown_not_healthy(monitor):
    """No MCP session open is 'nothing measured', never 'everything is fine'."""
    summary = await monitor.check_all()
    assert summary.overall_status is MCPServerStatus.UNKNOWN
    assert summary.servers == []
