"""Agent registry — catalog of marketplace agents.

The registry is the catalog half of the marketplace: what exists, what it does,
what it needs. Installing is the manager's job.

`module` is the load-bearing field. It names the importable MCP server module
that provides the agent's tools, and an entry is only *installable* when that
module actually resolves in this deployment — see `AgentEntry.is_available()`.
Without it there is nothing for the manager to launch and nothing for the agent
to call, so the catalog reports `available: false` and `install` refuses rather
than recording a row and calling it a success.

The built-in catalogue below is exactly that case: these are integrations we
publish metadata for but do not ship an implementation of, so every one of them
is browsable and none of them is installable. Adding
`app/mcp/servers/prometheus/server.py` and setting `module` on the entry is all
it takes to make one real.
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field


def module_resolves(module: str) -> bool:
    """True when `module` is importable here — without importing it.

    `find_spec` walks the import machinery, so this covers agents shipped in the
    image, installed into site-packages, or anywhere else on sys.path.
    """
    if not module:
        return False
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError, ModuleNotFoundError):
        # A parent package that itself fails to import raises rather than
        # returning None. Either way the agent cannot be launched.
        return False


@dataclass
class AgentEntry:
    """An agent listed in the marketplace."""

    id: str
    name: str
    description: str
    version: str
    author: str
    category: str
    tools: list[str] = field(default_factory=list)
    source: str = ""
    docs_url: str = ""
    stars: int = 0
    downloads: int = 0
    verified: bool = False
    dependencies: list[str] = field(default_factory=list)
    env_vars: list[str] = field(default_factory=list)
    # Importable MCP server module providing this agent's tools. Empty means the
    # listing is metadata only and the agent cannot be installed.
    module: str = ""

    def is_available(self) -> bool:
        """Whether this agent can actually be installed and run here."""
        return module_resolves(self.module)

    @classmethod
    def from_dict(cls, data: dict) -> "AgentEntry":
        return cls(
            id=data.get("id", ""),
            name=data.get("name", ""),
            description=data.get("description", ""),
            version=data.get("version", "0.0.0"),
            author=data.get("author", ""),
            category=data.get("category", "other"),
            tools=data.get("tools", []),
            source=data.get("source", ""),
            docs_url=data.get("docs_url", ""),
            stars=data.get("stars", 0),
            downloads=data.get("downloads", 0),
            verified=data.get("verified", False),
            dependencies=data.get("dependencies", []),
            env_vars=data.get("env_vars", []),
            module=data.get("module", ""),
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "author": self.author,
            "category": self.category,
            "tools": self.tools,
            "source": self.source,
            "docs_url": self.docs_url,
            "stars": self.stars,
            "downloads": self.downloads,
            "verified": self.verified,
            "dependencies": self.dependencies,
            "env_vars": self.env_vars,
            "module": self.module,
            # Callers list before they install; without this the only way to find
            # out an agent is unavailable was to try installing it.
            "available": self.is_available(),
        }


# None of these carry a `module`, because this repo ships no implementation for
# any of them. They are a published roadmap, not an installable set; the manager
# says so instead of pretending the install worked.
BUILTIN_AGENTS: list[dict] = [
    {
        "id": "prometheus-metrics",
        "name": "Prometheus Metrics",
        "description": "Query Prometheus metrics, run PromQL, and analyze time series data",
        "version": "1.0.0",
        "author": "OmniPotent Community",
        "category": "monitoring",
        "tools": ["query_prometheus", "list_metrics", "get_alerts", "analyze_series"],
        "source": "https://github.com/omnipotent/agent-prometheus",
        "verified": True,
        "env_vars": ["PROMETHEUS_URL", "PROMETHEUS_TOKEN"],
    },
    {
        "id": "grafana-dashboards",
        "name": "Grafana Dashboards",
        "description": "Manage Grafana dashboards, query panels, and create alerts",
        "version": "1.0.0",
        "author": "OmniPotent Community",
        "category": "monitoring",
        "tools": ["list_dashboards", "get_dashboard", "create_alert", "query_panel"],
        "source": "https://github.com/omnipotent/agent-grafana",
        "verified": True,
        "env_vars": ["GRAFANA_URL", "GRAFANA_API_KEY"],
    },
    {
        "id": "jenkins-ci",
        "name": "Jenkins CI",
        "description": "Trigger builds, view job status, and analyze build logs",
        "version": "1.0.0",
        "author": "OmniPotent Community",
        "category": "cicd",
        "tools": ["list_jobs", "trigger_build", "get_build_log", "get_build_status"],
        "source": "https://github.com/omnipotent/agent-jenkins",
        "verified": True,
        "env_vars": ["JENKINS_URL", "JENKINS_USER", "JENKINS_TOKEN"],
    },
    {
        "id": "aws-cloudwatch",
        "name": "AWS CloudWatch",
        "description": "Query CloudWatch metrics, logs, and alarms",
        "version": "1.0.0",
        "author": "OmniPotent Community",
        "category": "monitoring",
        "tools": ["get_metric_data", "query_logs", "list_alarms", "describe_alarm"],
        "source": "https://github.com/omnipotent/agent-cloudwatch",
        "verified": True,
        "env_vars": ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_REGION"],
    },
    {
        "id": "jira-tickets",
        "name": "Jira Tickets",
        "description": "Create, update, and query Jira tickets and projects",
        "version": "1.0.0",
        "author": "OmniPotent Community",
        "category": "ticketing",
        "tools": ["create_ticket", "update_ticket", "search_tickets", "get_ticket"],
        "source": "https://github.com/omnipotent/agent-jira",
        "verified": True,
        "env_vars": ["JIRA_URL", "JIRA_USER", "JIRA_API_TOKEN"],
    },
    {
        "id": "opsgenie-alerts",
        "name": "OpsGenie Alerts",
        "description": "Manage OpsGenie alerts, on-call schedules, and escalations",
        "version": "1.0.0",
        "author": "OmniPotent Community",
        "category": "alerting",
        "tools": ["list_alerts", "acknowledge_alert", "close_alert", "get_oncall"],
        "source": "https://github.com/omnipotent/agent-opsgenie",
        "verified": True,
        "env_vars": ["OPSGENIE_API_KEY"],
    },
    {
        "id": "elasticsearch-logs",
        "name": "Elasticsearch Logs",
        "description": "Query Elasticsearch indices and analyze log data",
        "version": "1.0.0",
        "author": "OmniPotent Community",
        "category": "logging",
        "tools": ["search_logs", "list_indices", "get_mapping", "aggregate_logs"],
        "source": "https://github.com/omnipotent/agent-elasticsearch",
        "verified": True,
        "env_vars": ["ELASTICSEARCH_URL", "ELASTICSEARCH_API_KEY"],
    },
    {
        "id": "vault-secrets",
        "name": "HashiCorp Vault",
        "description": "Manage secrets in HashiCorp Vault",
        "version": "1.0.0",
        "author": "OmniPotent Community",
        "category": "security",
        "tools": ["list_secrets", "get_secret", "rotate_secret", "list_policies"],
        "source": "https://github.com/omnipotent/agent-vault",
        "verified": True,
        "env_vars": ["VAULT_ADDR", "VAULT_TOKEN"],
    },
    {
        "id": "ansible-automation",
        "name": "Ansible Automation",
        "description": "Run Ansible playbooks and manage inventory",
        "version": "1.0.0",
        "author": "OmniPotent Community",
        "category": "automation",
        "tools": ["run_playbook", "list_inventory", "check_syntax", "list_roles"],
        "source": "https://github.com/omnipotent/agent-ansible",
        "verified": True,
        "dependencies": ["ansible"],
    },
    {
        "id": "pulumi-iac",
        "name": "Pulumi IaC",
        "description": "Manage Pulumi stacks and preview/apply changes",
        "version": "1.0.0",
        "author": "OmniPotent Community",
        "category": "iac",
        "tools": ["list_stacks", "preview", "apply", "get_outputs", "refresh"],
        "source": "https://github.com/omnipotent/agent-pulumi",
        "verified": True,
        "env_vars": ["PULUMI_ACCESS_TOKEN"],
        "dependencies": ["pulumi"],
    },
]


class Registry:
    """Registry of available marketplace agents."""

    def __init__(self):
        self._agents: dict[str, AgentEntry] = {}
        self._load_builtin()

    def _load_builtin(self) -> None:
        """Load built-in agent definitions."""
        for agent_data in BUILTIN_AGENTS:
            agent = AgentEntry.from_dict(agent_data)
            self._agents[agent.id] = agent

    def list_agents(
        self,
        category: str | None = None,
        verified_only: bool = False,
        search: str | None = None,
        available_only: bool = False,
    ) -> list[AgentEntry]:
        """List catalogued agents with optional filtering.

        `verified_only` is a claim about the publisher; `available_only` is a
        fact about this deployment — only the latter tells you what will install.
        """
        agents = list(self._agents.values())

        if category:
            agents = [a for a in agents if a.category == category]

        if verified_only:
            agents = [a for a in agents if a.verified]

        if available_only:
            agents = [a for a in agents if a.is_available()]

        if search:
            search_lower = search.lower()
            agents = [
                a for a in agents
                if search_lower in a.name.lower()
                or search_lower in a.description.lower()
                or any(search_lower in t.lower() for t in a.tools)
            ]

        return sorted(agents, key=lambda a: (-a.stars, a.name))

    def get_agent(self, agent_id: str) -> AgentEntry | None:
        """Get a specific agent by ID."""
        return self._agents.get(agent_id)

    def register(self, entry: AgentEntry) -> None:
        """Add or replace a catalogue entry."""
        self._agents[entry.id] = entry

    def list_categories(self) -> list[dict]:
        """List all agent categories with counts."""
        categories: dict[str, int] = {}
        for agent in self._agents.values():
            categories[agent.category] = categories.get(agent.category, 0) + 1

        return [
            {"id": cat, "count": count}
            for cat, count in sorted(categories.items())
        ]


_registry: Registry | None = None


def get_registry() -> Registry:
    """Get the singleton registry instance."""
    global _registry
    if _registry is None:
        _registry = Registry()
    return _registry


def reset_registry() -> None:
    """Drop the singleton so the next call rebuilds it (tests)."""
    global _registry
    _registry = None
