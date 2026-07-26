"""Agent manager — install, configure, and manage marketplace agents.

The manager handles the lifecycle of installed agents, including installation,
configuration, enabling/disabling, and removal.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from app.marketplace.registry import AgentEntry, get_registry

log = logging.getLogger("devcopilot.marketplace")

INSTALLED_AGENTS_PATH = Path(__file__).parent / "installed.json"


@dataclass
class InstalledAgent:
    """An installed agent instance."""

    id: str
    version: str
    enabled: bool = True
    config: dict = field(default_factory=dict)
    installed_at: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "InstalledAgent":
        return cls(
            id=data.get("id", ""),
            version=data.get("version", ""),
            enabled=data.get("enabled", True),
            config=data.get("config", {}),
            installed_at=data.get("installed_at", ""),
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "version": self.version,
            "enabled": self.enabled,
            "config": self.config,
            "installed_at": self.installed_at,
        }


class AgentManager:
    """Manage installed marketplace agents."""

    def __init__(self, data_path: Path | None = None):
        self._data_path = data_path or INSTALLED_AGENTS_PATH
        self._installed: dict[str, InstalledAgent] = {}
        self._load()

    def _load(self) -> None:
        """Load installed agents from disk."""
        if self._data_path.exists():
            try:
                data = json.loads(self._data_path.read_text())
                for agent_data in data.get("agents", []):
                    agent = InstalledAgent.from_dict(agent_data)
                    self._installed[agent.id] = agent
            except (json.JSONDecodeError, OSError) as e:
                log.warning("Failed to load installed agents: %s", e)

    def _save(self) -> None:
        """Save installed agents to disk."""
        try:
            data = {
                "agents": [a.to_dict() for a in self._installed.values()]
            }
            self._data_path.parent.mkdir(parents=True, exist_ok=True)
            self._data_path.write_text(json.dumps(data, indent=2))
        except OSError as e:
            log.warning("Failed to save installed agents: %s", e)

    def list_installed(self, enabled_only: bool = False) -> list[InstalledAgent]:
        """List installed agents."""
        agents = list(self._installed.values())
        if enabled_only:
            agents = [a for a in agents if a.enabled]
        return agents

    def is_installed(self, agent_id: str) -> bool:
        """Check if an agent is installed."""
        return agent_id in self._installed

    def get_installed(self, agent_id: str) -> InstalledAgent | None:
        """Get an installed agent by ID."""
        return self._installed.get(agent_id)

    def install(self, agent_id: str, config: dict | None = None) -> dict:
        """Install an agent from the registry.

        Args:
            agent_id: ID of the agent to install
            config: Optional configuration for the agent

        Returns:
            Installation result with status and any issues
        """
        registry = get_registry()
        agent_entry = registry.get_agent(agent_id)

        if not agent_entry:
            return {
                "status": "error",
                "message": f"Agent '{agent_id}' not found in registry",
            }

        if agent_id in self._installed:
            return {
                "status": "error",
                "message": f"Agent '{agent_id}' is already installed",
            }

        import datetime
        installed = InstalledAgent(
            id=agent_id,
            version=agent_entry.version,
            enabled=True,
            config=config or {},
            installed_at=datetime.datetime.now(datetime.UTC).isoformat(),
        )

        missing_vars = []
        import os
        for var in agent_entry.env_vars:
            if not os.environ.get(var):
                missing_vars.append(var)

        self._installed[agent_id] = installed
        self._save()

        log.info("Installed agent: %s v%s", agent_id, agent_entry.version)

        return {
            "status": "success",
            "message": f"Installed {agent_entry.name} v{agent_entry.version}",
            "agent": installed.to_dict(),
            "warnings": (
                [f"Missing environment variables: {', '.join(missing_vars)}"]
                if missing_vars else []
            ),
        }

    def uninstall(self, agent_id: str) -> dict:
        """Uninstall an agent.

        Args:
            agent_id: ID of the agent to uninstall

        Returns:
            Uninstallation result
        """
        if agent_id not in self._installed:
            return {
                "status": "error",
                "message": f"Agent '{agent_id}' is not installed",
            }

        del self._installed[agent_id]
        self._save()

        log.info("Uninstalled agent: %s", agent_id)

        return {
            "status": "success",
            "message": f"Uninstalled agent '{agent_id}'",
        }

    def enable(self, agent_id: str) -> dict:
        """Enable an installed agent."""
        if agent_id not in self._installed:
            return {
                "status": "error",
                "message": f"Agent '{agent_id}' is not installed",
            }

        self._installed[agent_id].enabled = True
        self._save()

        return {
            "status": "success",
            "message": f"Enabled agent '{agent_id}'",
        }

    def disable(self, agent_id: str) -> dict:
        """Disable an installed agent."""
        if agent_id not in self._installed:
            return {
                "status": "error",
                "message": f"Agent '{agent_id}' is not installed",
            }

        self._installed[agent_id].enabled = False
        self._save()

        return {
            "status": "success",
            "message": f"Disabled agent '{agent_id}'",
        }

    def configure(self, agent_id: str, config: dict) -> dict:
        """Update configuration for an installed agent.

        Args:
            agent_id: ID of the agent
            config: New configuration (merged with existing)

        Returns:
            Configuration result
        """
        if agent_id not in self._installed:
            return {
                "status": "error",
                "message": f"Agent '{agent_id}' is not installed",
            }

        self._installed[agent_id].config.update(config)
        self._save()

        return {
            "status": "success",
            "message": f"Updated configuration for '{agent_id}'",
            "config": self._installed[agent_id].config,
        }

    def get_agent_info(self, agent_id: str) -> dict | None:
        """Get full information about an agent (registry + installed status)."""
        registry = get_registry()
        entry = registry.get_agent(agent_id)

        if not entry:
            return None

        installed = self._installed.get(agent_id)

        return {
            "registry": entry.to_dict(),
            "installed": installed.to_dict() if installed else None,
            "is_installed": installed is not None,
            "is_enabled": installed.enabled if installed else False,
        }


_manager: AgentManager | None = None


def get_manager() -> AgentManager:
    """Get the singleton manager instance."""
    global _manager
    if _manager is None:
        _manager = AgentManager()
    return _manager
