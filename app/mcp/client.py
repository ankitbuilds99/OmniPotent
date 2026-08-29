"""MCP client wiring.

Registers every MCP server the agent can use and loads their tools as LangChain
tools via `langchain-mcp-adapters`. The agent never imports a server directly —
it only sees the discovered tools, exactly like a real MCP deployment.

Adding a new capability = adding one entry here. No agent code changes.
"""

from __future__ import annotations

import functools
import logging
import sys
import time
from contextlib import AsyncExitStack
from datetime import timedelta

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools as _load_session_tools

from app import policy, runtime, tenant_context
from app.config import get_settings
from app.mcp.health import MCPHealthMonitor, get_health_monitor

log = logging.getLogger("omnipotent.mcp")

# Built-in servers. Marketplace-installed agents are appended at call time by
# _server_config(); iterate the config, not this tuple.
_SERVERS = (
    "datadog", "repo", "github", "pagerduty", "kubernetes", "sentry", "memory",
    "traces", "deploys", "argocd", "terraform", "docker", "cicd", "security",
    "costopt", "architect", "docs", "multicloud", "marketplace", "slack", "alerting",
    "runbooks",
)


def _isecret(name: str, fallback: str) -> str:
    """Resolve an integration secret: the request's tenant when multi-tenant
    (their decrypted creds, never the host's), else the .env fallback."""
    return tenant_context.integration_secret(name, fallback)


def _server_config() -> dict:
    """Build the MultiServerMCPClient config.

    Each server is launched as a subprocess speaking MCP over stdio. We pass the
    current Python interpreter and forward the relevant paths via env so the
    servers read the same data the app is configured with. Integration secrets
    come from the resolved tenant when multi-tenant, else from .env.

    Enabled marketplace agents are merged in on top, so installing one adds its
    tools and disabling one removes them. Built-ins win on a name collision.
    """
    py = sys.executable
    s = get_settings()
    builtin: dict = {
        "datadog": {
            "command": py,
            "args": ["-m", "app.mcp.servers.datadog.server"],
            "transport": "stdio",
            # LOGS_DATA_PATH backs offline-demo mode; DD_* enable the live API.
            "env": {
                "LOGS_DATA_PATH": str(runtime.logs_path()),
                "DD_API_KEY": _isecret("DD_API_KEY", s.dd_api_key),
                "DD_APP_KEY": _isecret("DD_APP_KEY", s.dd_app_key),
                "DD_SITE": _isecret("DD_SITE", s.dd_site),
            },
        },
        "repo": {
            "command": py,
            "args": ["-m", "app.mcp.servers.repo.server"],
            "transport": "stdio",
            "env": {"TARGET_REPO_PATH": str(runtime.repo_path())},
        },
        "github": {
            "command": py,
            "args": ["-m", "app.mcp.servers.github.server"],
            "transport": "stdio",
            # Runtime creds (set via the UI "Connect GitHub" flow) take
            # precedence over .env, so live mode can be toggled without restart.
            #
            # The timeouts must be forwarded explicitly. A stdio server inherits
            # only DEFAULT_INHERITED_ENV_VARS (HOME, PATH, SHELL, TERM, USER,
            # LOGNAME) plus whatever is in this dict — not os.environ — so
            # HTTP_TIMEOUT_GITHUB / HTTP_TIMEOUT_MCP set in .env never reached the
            # subprocess and the server always fell back to its own literals,
            # making both knobs silently inert.
            "env": {
                "GITHUB_TOKEN": runtime.github_token(),
                "GITHUB_REPO": runtime.github_repo(),
                "HTTP_TIMEOUT_GITHUB": str(s.http_timeout_github),
                "HTTP_TIMEOUT_MCP": str(s.http_timeout_mcp),
            },
        },
        "pagerduty": {
            "command": py,
            "args": ["-m", "app.mcp.servers.pagerduty.server"],
            "transport": "stdio",
            "env": {  # blank token = offline fixtures
                "PAGERDUTY_API_TOKEN": _isecret("PAGERDUTY_API_TOKEN", s.pagerduty_api_token),
                "PAGERDUTY_FROM_EMAIL": _isecret("PAGERDUTY_FROM_EMAIL", s.pagerduty_from_email),
            },
        },
        "kubernetes": {
            "command": py,
            "args": ["-m", "app.mcp.servers.kubernetes.server"],
            "transport": "stdio",
            # KUBE_CONFIG_PATH enables live cluster mode; blank = offline fixtures.
            "env": {
                "KUBE_CONFIG_PATH": _isecret("KUBE_CONFIG_PATH", s.kube_config_path),
                "KUBE_NAMESPACE": _isecret("KUBE_NAMESPACE", s.kube_namespace),
            },
        },
        "sentry": {
            "command": py,
            "args": ["-m", "app.mcp.servers.sentry.server"],
            "transport": "stdio",
            # SENTRY_API_TOKEN enables live mode; blank = offline fixtures.
            "env": {
                "SENTRY_API_TOKEN": _isecret("SENTRY_API_TOKEN", s.sentry_api_token),
                "SENTRY_ORG": _isecret("SENTRY_ORG", s.sentry_org),
                "SENTRY_PROJECT": _isecret("SENTRY_PROJECT", s.sentry_project),
            },
        },
        "memory": {
            "command": py,
            "args": ["-m", "app.mcp.servers.memory.server"],
            "transport": "stdio",
            # CORPUS_PATH overrides the bundled prior-incident corpus; blank = bundled.
            "env": {"CORPUS_PATH": s.incident_corpus_path},
        },
        "traces": {
            "command": py,
            "args": ["-m", "app.mcp.servers.traces.server"],
            "transport": "stdio",
            # TRACES_API_URL enables live (Jaeger-compatible) mode; blank = fixtures.
            "env": {"TRACES_API_URL": _isecret("TRACES_API_URL", s.traces_api_url)},
        },
        "deploys": {
            "command": py,
            "args": ["-m", "app.mcp.servers.deploys.server"],
            "transport": "stdio",
            # DEPLOYS_API_URL enables live mode; blank = offline deploy fixtures.
            "env": {
                "DEPLOYS_API_URL": _isecret("DEPLOYS_API_URL", s.deploys_api_url),
                "DEPLOYS_API_TOKEN": _isecret("DEPLOYS_API_TOKEN", s.deploys_api_token),
            },
        },
        "argocd": {
            "command": py,
            "args": ["-m", "app.mcp.servers.argocd.server"],
            "transport": "stdio",
            # ARGOCD_SERVER_URL + ARGOCD_AUTH_TOKEN enable live mode; blank = offline fixtures.
            "env": {
                "ARGOCD_SERVER_URL": _isecret("ARGOCD_SERVER_URL", s.argocd_server_url),
                "ARGOCD_AUTH_TOKEN": _isecret("ARGOCD_AUTH_TOKEN", s.argocd_auth_token),
            },
        },
        "terraform": {
            "command": py,
            "args": ["-m", "app.mcp.servers.terraform.server"],
            "transport": "stdio",
            # TERRAFORM_CLOUD_TOKEN enables live mode; blank = offline fixtures.
            "env": {
                "TERRAFORM_CLOUD_TOKEN": _isecret("TERRAFORM_CLOUD_TOKEN", s.terraform_cloud_token),
                "TERRAFORM_ORGANIZATION": _isecret("TERRAFORM_ORGANIZATION", s.terraform_organization),
            },
        },
        "docker": {
            "command": py,
            "args": ["-m", "app.mcp.servers.docker.server"],
            "transport": "stdio",
            "env": {"TARGET_REPO_PATH": str(runtime.repo_path())},
        },
        "cicd": {
            "command": py,
            "args": ["-m", "app.mcp.servers.cicd.server"],
            "transport": "stdio",
            "env": {"TARGET_REPO_PATH": str(runtime.repo_path())},
        },
        "security": {
            "command": py,
            "args": ["-m", "app.mcp.servers.security.server"],
            "transport": "stdio",
            "env": {"TARGET_REPO_PATH": str(runtime.repo_path())},
        },
        "costopt": {
            "command": py,
            "args": ["-m", "app.mcp.servers.costopt.server"],
            "transport": "stdio",
            "env": {
                "AWS_ACCESS_KEY_ID": _isecret("AWS_ACCESS_KEY_ID", s.aws_access_key_id),
                "AWS_SECRET_ACCESS_KEY": _isecret("AWS_SECRET_ACCESS_KEY", s.aws_secret_access_key),
            },
        },
        "architect": {
            "command": py,
            "args": ["-m", "app.mcp.servers.architect.server"],
            "transport": "stdio",
            "env": {"TARGET_REPO_PATH": str(runtime.repo_path())},
        },
        "docs": {
            "command": py,
            "args": ["-m", "app.mcp.servers.docs.server"],
            "transport": "stdio",
            "env": {"TARGET_REPO_PATH": str(runtime.repo_path())},
        },
        "multicloud": {
            "command": py,
            "args": ["-m", "app.mcp.servers.multicloud.server"],
            "transport": "stdio",
            "env": {
                "AWS_ACCESS_KEY_ID": _isecret("AWS_ACCESS_KEY_ID", s.aws_access_key_id),
                "AWS_SECRET_ACCESS_KEY": _isecret("AWS_SECRET_ACCESS_KEY", s.aws_secret_access_key),
                "GCP_PROJECT_ID": _isecret("GCP_PROJECT_ID", s.gcp_project_id),
                "AZURE_SUBSCRIPTION_ID": _isecret("AZURE_SUBSCRIPTION_ID", s.azure_subscription_id),
            },
        },
        "marketplace": {
            "command": py,
            "args": ["-m", "app.mcp.servers.marketplace.server"],
            "transport": "stdio",
            "env": {},
        },
        "slack": {
            "command": py,
            "args": ["-m", "app.mcp.servers.slack.server"],
            "transport": "stdio",
            "env": {
                "SLACK_BOT_TOKEN": _isecret("SLACK_BOT_TOKEN", s.slack_bot_token),
                "SLACK_DEFAULT_CHANNEL": _isecret("SLACK_CHANNEL", s.slack_channel),
            },
        },
        "alerting": {
            "command": py,
            "args": ["-m", "app.mcp.servers.alerting.server"],
            "transport": "stdio",
            "env": {},
        },
        "runbooks": {
            "command": py,
            "args": ["-m", "app.mcp.servers.runbooks.server"],
            "transport": "stdio",
            # This server reads and writes the same runbook tables as the HTTP
            # API, so it has to be pointed at the same database. A stdio server
            # is started with `get_default_environment()` plus whatever is in
            # this dict — HOME, PATH, SHELL, TERM, USER and nothing else — so
            # none of this is inherited implicitly. With the dict empty the
            # subprocess fell back to the local sqlite default, which meant the
            # agent listed the built-in runbooks only (never the customer's) and
            # wrote its executions somewhere the UI does not read.
            "env": {
                "COPILOT_DATABASE_URL": s.copilot_database_url,
                # Executed steps shell out to the cluster; without these the
                # server would run every remediation against a different
                # cluster, or none, from the one the app is configured with.
                "KUBE_CONFIG_PATH": _isecret("KUBE_CONFIG_PATH", s.kube_config_path),
                "KUBE_NAMESPACE": _isecret("KUBE_NAMESPACE", s.kube_namespace),
            },
        },
    }

    # Imported lazily: reading install state costs a file read, and this module
    # is imported by tooling that never launches a server.
    from app.marketplace.manager import installed_server_configs

    try:
        installed = installed_server_configs(py)
    except Exception as exc:  # a broken install file must not take the agent down
        log.warning("Could not load marketplace agents: %s", exc)
        installed = {}

    merged = {**installed, **builtin}

    # Every session gets a read deadline. Without one, ClientSession waits on a
    # response forever (mcp/shared/session.py does `fail_after(None)`), so a
    # wedged-but-alive subprocess — a kubernetes call against an unresponsive API
    # server, say — hung the turn for the life of the process: the thread lock is
    # held for the whole turn and a running thread is never evictable, so the
    # session slot was stranded too. setdefault, so a marketplace agent that ships
    # its own session_kwargs keeps them.
    for entry in merged.values():
        entry.setdefault("session_kwargs", {}).setdefault(
            "read_timeout_seconds", timedelta(seconds=_read_timeout_s(s))
        )

    return merged


# The client-side deadline is the last resort, not the primary timeout: a server
# that bounds its own outbound calls at http_timeout_mcp should get the chance to
# fail with its own, far more specific error before we cut the transport out from
# under it. Hence the headroom multiple rather than the raw setting.
_READ_TIMEOUT_HEADROOM = 3.0


def _read_timeout_s(s) -> float:
    """Seconds to wait for any single MCP response before giving up."""
    return max(float(s.http_timeout_mcp), 1.0) * _READ_TIMEOUT_HEADROOM


# Tool names that mutate external state and require human approval. The action
# policy engine (app/policy.py) is the single source of truth; this alias keeps
# existing imports working while approve/notify/allow classification lives there.
WRITE_TOOLS: set[str] = policy.APPROVE_TOOLS


async def load_mcp_tools(stack: AsyncExitStack) -> tuple[list[BaseTool], MultiServerMCPClient]:
    """Open ONE long-lived MCP session per server and load each server's tools
    bound to that session.

    The sessions are entered into the caller's AsyncExitStack, so they (and their
    stdio subprocesses) close when the CopilotSession does. This reuses a single
    subprocess per server for the whole session, instead of `get_tools()`, whose
    tools spawn and tear down a fresh subprocess on *every* tool call — a stdio
    handshake per call that dominated latency in a multi-tool investigation.

    This is also the only place that knows which MCP servers actually came up, so
    it is where the health monitor is fed: every server that completes the stdio
    handshake is registered, every one that does not is recorded with the real
    reason it failed, and each loaded tool is wrapped to report its latency and
    errors. Before that, `MCPHealthMonitor` had no producer at all and /healthz
    published an MCP block nothing had ever measured.
    """
    config = _server_config()
    client = MultiServerMCPClient(config)
    tools: list[BaseTool] = []

    # Groq free tier has severe limitations:
    # - 6K tokens/min TPM limit (tool definitions alone can exceed this)
    # - 128 tool limit
    # - Model hallucinates tool calls not in the request
    # For Groq, run in "chat-only" mode with no tools to avoid these issues.
    # Resolve the provider the way llm.py builds the model — runtime.provider(),
    # which honours the UI "Connect" override and the request's tenant. The frozen
    # .env value disagreed with the model actually in use: switching the UI off
    # Groq left the agent with zero tools (silently chat-only), and a Groq tenant
    # on an Anthropic host got all ~22 servers' tools and hit the 128-tool cap.
    if runtime.provider() == "groq":
        # No tools for Groq - avoids hallucination and rate limits
        return [], client

    monitor = get_health_monitor()

    # Iterate the config, not _SERVERS: marketplace-installed agents live only in
    # the config, and a name in _SERVERS with no config entry raised KeyError into
    # the swallow below — silently dropping the server.
    for name in config:
        try:
            session = await stack.enter_async_context(client.session(name))
        except Exception as exc:
            # Not fatal — an unconfigured integration is a normal state — but it
            # is the answer to "why are this agent's tools missing?", so say so,
            # in the logs and on the health surface.
            log.debug("MCP server %r did not start: %s", name, exc)
            monitor.record_start_failure(name, f"did not start: {type(exc).__name__}: {exc}")
            continue
        try:
            loaded = await _load_session_tools(session)
        except Exception as exc:
            # A different failure with the same effect on the agent, and it must
            # not be reported as the one above: the subprocess did start and its
            # session is open on the exit stack; what failed was listing its
            # tools. Saying "did not start" would send an operator to look at a
            # process that is running fine.
            log.debug("MCP server %r started but listing its tools failed: %s", name, exc)
            monitor.record_start_failure(
                name, f"started, but listing its tools failed: {type(exc).__name__}: {exc}"
            )
            continue
        monitor.register_session(name, tool_count=len(loaded))
        # Released before the session's own teardown (exit stacks unwind LIFO), so
        # a server stops counting as live at the moment its owner starts closing.
        stack.callback(monitor.release_session, name)
        tools.extend(_instrument(loaded, name, monitor))

    return tools, client


def _instrument(
    loaded: list[BaseTool], server_name: str, monitor: MCPHealthMonitor
) -> list[BaseTool]:
    """Wrap each tool's coroutine so every call reports its latency and outcome to
    the health monitor.

    The wrap happens here rather than in the graph's tool node because this is the
    only layer that knows which server a tool came from — by the time LangGraph
    invokes it, a tool is just a name. Failures are recorded and re-raised
    unchanged: the monitor observes, it never swallows.
    """
    for tool in loaded:
        inner = getattr(tool, "coroutine", None)
        if inner is None:  # nothing to time; a sync-only tool is not an MCP tool
            continue
        tool.coroutine = _timed(inner, monitor, server_name, tool.name)
    return loaded


def _timed(inner, monitor: MCPHealthMonitor, server_name: str, tool_name: str):
    @functools.wraps(inner)
    async def call(*args, **kwargs):
        started = time.perf_counter()
        try:
            result = await inner(*args, **kwargs)
        except Exception as exc:
            # Deliberately not BaseException: a cancelled call (the SSE client
            # hung up, the turn was abandoned) measured nothing about the server
            # — it may still be answering — and booking it as a server failure
            # made client disconnects look like an MCP outage. It propagates
            # unrecorded, as do KeyboardInterrupt/SystemExit.
            kind = _error_kind(exc)
            _observe(monitor, server_name, tool_name, started, success=False,
                     kind=kind, detail=f"{type(exc).__name__}: {exc}")
            raise
        _observe(monitor, server_name, tool_name, started, success=True)
        return result

    return call


# Failure kinds, deliberately a closed set: one of these is what reaches the
# Prometheus `error` label, which is a metric label and must not carry free-form
# text (see app/observability/metrics.record_mcp_tool_call).
#
# Only TIMEOUT and TRANSPORT say anything about the server's health. MCP reports
# a refused action and a broken pipe the same way at the type level — the adapter
# raises ToolException for any `isError` result — so a sandbox correctly refusing
# to read /etc/passwd used to be indistinguishable from a crashed subprocess, and
# two such refusals were enough to park the server on UNHEALTHY for good.
_KIND_TOOL_ERROR = "tool_error"   # the server answered: refused, or rejected the arguments
_KIND_RPC_ERROR = "rpc_error"     # the server answered with a JSON-RPC error
_KIND_TIMEOUT = "timeout"         # no answer before the read deadline
_KIND_TRANSPORT = "transport"     # the connection itself failed

# The entire label domain of `copilot_mcp_tool_errors_total`'s `error` label.
# Naming it, rather than leaving the four constants scattered, is what lets
# `_observe` refuse anything else on the way out: the series count stays bounded
# at servers x tools x 4 no matter what a future caller passes.
_ERROR_KINDS = frozenset(
    {_KIND_TOOL_ERROR, _KIND_RPC_ERROR, _KIND_TIMEOUT, _KIND_TRANSPORT}
)

_HEALTH_SIGNAL_KINDS = frozenset({_KIND_TIMEOUT, _KIND_TRANSPORT})

# httpx.codes.REQUEST_TIMEOUT, the code mcp/shared/session.py attaches when its
# own read deadline expires. Imported by value to keep httpx out of this path.
_MCP_REQUEST_TIMEOUT_CODE = 408


def _error_kind(exc: Exception) -> str:
    """Classify a failed tool call: did the server answer, or not?"""
    from langchain_core.tools import ToolException
    from mcp.shared.exceptions import McpError

    if isinstance(exc, ToolException):
        return _KIND_TOOL_ERROR
    if isinstance(exc, McpError):
        code = getattr(getattr(exc, "error", None), "code", None)
        if code == _MCP_REQUEST_TIMEOUT_CODE:
            return _KIND_TIMEOUT
        # Any other McpError arrived as a JSON-RPC error response, which means the
        # subprocess was alive enough to compose and send one.
        return _KIND_RPC_ERROR
    if isinstance(exc, TimeoutError):  # asyncio.TimeoutError is this since 3.11
        return _KIND_TIMEOUT
    return _KIND_TRANSPORT


def _observe(
    monitor: MCPHealthMonitor,
    server_name: str,
    tool_name: str,
    started: float,
    *,
    success: bool,
    kind: str | None = None,
    detail: str | None = None,
) -> None:
    """Report one finished tool call to both MCP observability surfaces.

    `detail` (the exception type and message) goes to the health monitor, which
    keeps one string per server as the operator-facing reason. `kind` is what
    reaches Prometheus, because that value is used as a metric label: the message
    contains whatever the model asked for — a file path, a query — so passing it
    through would mint a new time series per distinct request and grow the
    registry without bound. Only a member of `_ERROR_KINDS` is forwarded; an
    unclassified failure is still counted as an error, under the metric's own
    "unknown" label, rather than smuggling free text in as a new series.

    The Prometheus counters are fed here for the same reason the health monitor
    is: `copilot_mcp_tool_calls_total` / `_errors_total` / `_latency_seconds` are
    what deploy/observability/prometheus-alerts.yaml alerts on, and nothing on the
    request path had ever incremented them, so those rules watched series that
    never existed. Instrumentation must never take a tool call down with it, so a
    metrics failure is logged and swallowed — the tool result is not.
    """
    elapsed_s = time.perf_counter() - started
    health_signal = success or kind in _HEALTH_SIGNAL_KINDS
    monitor.record_tool_call(
        server_name, tool_name, elapsed_s * 1000.0, success=success, error=detail,
        health_signal=health_signal,
    )
    try:
        from app.observability import metrics

        metrics.record_mcp_tool_call(
            server_name, tool_name, elapsed_s, success=success,
            error=kind if kind in _ERROR_KINDS else None,
        )
    except Exception:  # noqa: BLE001 — observability is never load-bearing
        log.debug("could not record MCP tool metrics for %s.%s",
                  server_name, tool_name, exc_info=True)
