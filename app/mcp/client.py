"""MCP client wiring.

Registers every MCP server the agent can use and loads their tools as LangChain
tools via `langchain-mcp-adapters`. The agent never imports a server directly —
it only sees the discovered tools, exactly like a real MCP deployment.

Adding a new capability = adding one entry here. No agent code changes.
"""

from __future__ import annotations

import sys
from contextlib import AsyncExitStack

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools as _load_session_tools

from app import policy, runtime, tenant_context
from app.config import get_settings

_SERVERS = (
    "datadog", "repo", "github", "pagerduty", "kubernetes", "sentry", "memory",
    "traces", "deploys", "argocd", "terraform", "docker", "cicd", "security",
    "costopt", "architect", "docs", "multicloud", "marketplace", "slack", "alerting",
    "runbooks",
)


def _isecret(name: str, fallback: str) -> str:
    """Resolve an integration secret: the request's tenant when multi-tenant
    (their decrypted creds, never the host's), else the .env fallback."""
    t = tenant_context.get_tenant()
    if t is not None:
        return t.integration_secrets.get(name, "")
    return fallback


def _server_config() -> dict:
    """Build the MultiServerMCPClient config.

    Each server is launched as a subprocess speaking MCP over stdio. We pass the
    current Python interpreter and forward the relevant paths via env so the
    servers read the same data the app is configured with. Integration secrets
    come from the resolved tenant when multi-tenant, else from .env.
    """
    py = sys.executable
    s = get_settings()
    return {
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
            "env": {
                "GITHUB_TOKEN": runtime.github_token(),
                "GITHUB_REPO": runtime.github_repo(),
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
            "env": {},
        },
    }


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
    """
    client = MultiServerMCPClient(_server_config())
    tools: list[BaseTool] = []
    for name in _SERVERS:
        session = await stack.enter_async_context(client.session(name))
        tools.extend(await _load_session_tools(session))
    return tools, client
