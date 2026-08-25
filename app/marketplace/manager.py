"""Agent manager — install, configure, and manage marketplace agents.

Three things were wrong with install, all variations on the same theme: it said
yes to everything.

1. It never checked that the agent had an implementation. Every catalogue entry
   points at a `github.com/omnipotent/agent-*` repo that this deployment cannot
   fetch, cannot verify and cannot run, and `install()` returned
   ``{"status": "success", "message": "Installed Prometheus Metrics v1.0.0"}``
   for all of them. The investigating agent relayed that to the operator and
   then called `query_prometheus`, which does not exist. Installing now requires
   `AgentEntry.is_available()` — the entry names an importable MCP server module
   and that module resolves here. Nothing is downloaded and nothing remote is
   executed; install wires up code that is already present.
2. Installed agents were recorded and then ignored. `app/mcp/client.py` builds
   its server list from a hard-coded tuple, so an "installed" agent contributed
   exactly zero tools. `installed_server_configs()` below is what that module
   now merges in, so installing an agent really does add its tools and disabling
   one really does take them away.
3. State lived at `app/marketplace/installed.json`, inside the source tree —
   wiped by every image rebuild, and unwritable on a read-only rootfs, where
   `_save` swallowed the OSError and install still reported success. It is under
   the data directory now (`Settings.marketplace_state_path`), and a failed save
   rolls the change back and reports the failure.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from app.config import get_settings
from app.marketplace.registry import get_registry, module_resolves

log = logging.getLogger("omnipotent.marketplace")


@dataclass
class InstalledAgent:
    """An installed agent instance."""

    id: str
    version: str
    enabled: bool = True
    config: dict = field(default_factory=dict)
    installed_at: str = ""
    # Module recorded at install time, so a running deployment keeps launching
    # what it installed even if the catalogue entry is later re-pointed.
    module: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "InstalledAgent":
        return cls(
            id=data.get("id", ""),
            version=data.get("version", ""),
            enabled=data.get("enabled", True),
            config=data.get("config", {}),
            installed_at=data.get("installed_at", ""),
            module=data.get("module", ""),
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "version": self.version,
            "enabled": self.enabled,
            "config": self.config,
            "installed_at": self.installed_at,
            "module": self.module,
        }


class AgentManager:
    """Manage installed marketplace agents."""

    def __init__(self, data_path: Path | None = None):
        self._data_path = data_path or get_settings().marketplace_state_path
        self._installed: dict[str, InstalledAgent] = {}
        self._load()

    @property
    def data_path(self) -> Path:
        """Where install state is persisted."""
        return self._data_path

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
        """Persist install state, atomically.

        Raises OSError on failure — callers roll back and report it. Swallowing
        it here is what let an install "succeed" against a read-only filesystem
        and then vanish on restart.
        """
        data = {"agents": [a.to_dict() for a in self._installed.values()]}
        self._data_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._data_path.with_suffix(self._data_path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        os.replace(tmp, self._data_path)

    def _commit(self, rollback: dict[str, InstalledAgent]) -> dict | None:
        """Save, or restore `rollback` and return an error result."""
        try:
            self._save()
        except OSError as e:
            self._installed = rollback
            log.warning("Failed to save installed agents: %s", e)
            return {
                "status": "error",
                "message": f"Could not persist the change to {self._data_path}: {e}",
            }
        return None

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

        Only agents whose MCP server module resolves in this deployment can be
        installed; everything else is a catalogue listing with no implementation
        behind it and is refused with `status: "unavailable"`.

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

        if not agent_entry.is_available():
            detail = (
                f"its MCP server module '{agent_entry.module}' is not importable here"
                if agent_entry.module
                else "this deployment ships no implementation for it"
            )
            return {
                "status": "unavailable",
                "message": (
                    f"'{agent_entry.name}' cannot be installed: {detail}. "
                    "The catalogue entry is metadata only — nothing was installed "
                    "and none of its tools are available."
                ),
                "agent_id": agent_id,
                "source": agent_entry.source,
                "docs_url": agent_entry.docs_url,
            }

        installed = InstalledAgent(
            id=agent_id,
            version=agent_entry.version,
            enabled=True,
            config=config or {},
            installed_at=datetime.now(UTC).isoformat(),
            module=agent_entry.module,
        )

        missing_vars = [v for v in agent_entry.env_vars if not os.environ.get(v)]

        rollback = dict(self._installed)
        self._installed[agent_id] = installed
        if (failure := self._commit(rollback)) is not None:
            return failure

        log.info("Installed agent: %s v%s", agent_id, agent_entry.version)

        return {
            "status": "success",
            "message": f"Installed {agent_entry.name} v{agent_entry.version}",
            "agent": installed.to_dict(),
            "warnings": (
                [
                    f"Missing environment variables: {', '.join(missing_vars)}. "
                    "The agent is installed but its tools will fail until these are set."
                ]
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

        rollback = dict(self._installed)
        del self._installed[agent_id]
        if (failure := self._commit(rollback)) is not None:
            return failure

        log.info("Uninstalled agent: %s", agent_id)

        return {
            "status": "success",
            "message": f"Uninstalled agent '{agent_id}'",
        }

    def _set_enabled(self, agent_id: str, enabled: bool) -> dict:
        if agent_id not in self._installed:
            return {
                "status": "error",
                "message": f"Agent '{agent_id}' is not installed",
            }

        rollback = {k: v for k, v in self._installed.items()}
        previous = self._installed[agent_id].enabled
        self._installed[agent_id].enabled = enabled
        if (failure := self._commit(rollback)) is not None:
            # _commit restored the dict, but this entry is the same object we
            # mutated in place, so undo the flag too.
            self._installed[agent_id].enabled = previous
            return failure

        verb = "Enabled" if enabled else "Disabled"
        return {"status": "success", "message": f"{verb} agent '{agent_id}'"}

    def enable(self, agent_id: str) -> dict:
        """Enable an installed agent."""
        return self._set_enabled(agent_id, True)

    def disable(self, agent_id: str) -> dict:
        """Disable an enabled agent without uninstalling it."""
        return self._set_enabled(agent_id, False)

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

        entry = self._installed[agent_id]
        previous = dict(entry.config)
        entry.config.update(config)
        if (failure := self._commit(dict(self._installed))) is not None:
            entry.config = previous
            return failure

        return {
            "status": "success",
            "message": f"Updated configuration for '{agent_id}'",
            "config": entry.config,
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
            "is_available": entry.is_available(),
        }


def installed_server_configs(python_executable: str) -> dict[str, dict]:
    """MultiServerMCPClient entries for every enabled installed agent.

    This is the half that was missing: `app/mcp/client.py` merges these into its
    own server map, so an installed agent's tools actually reach the LLM.

    Agents whose module has since stopped resolving are skipped rather than
    launched — a subprocess that exits immediately would otherwise look like a
    working install. The agent's `config` is passed through as environment for
    its subprocess, with the entry's declared `env_vars` inherited from the host
    process when not set explicitly.
    """
    registry = get_registry()
    configs: dict[str, dict] = {}

    for agent in get_manager().list_installed(enabled_only=True):
        if not module_resolves(agent.module):
            log.warning(
                "Installed agent '%s' skipped: module '%s' no longer resolves",
                agent.id, agent.module,
            )
            continue

        env = {}
        entry = registry.get_agent(agent.id)
        if entry is not None:
            env.update({v: os.environ.get(v, "") for v in entry.env_vars})
        env.update({str(k): str(v) for k, v in agent.config.items()})

        configs[f"marketplace:{agent.id}"] = {
            "command": python_executable,
            "args": ["-m", agent.module],
            "transport": "stdio",
            "env": env,
        }

    return configs


_manager: AgentManager | None = None


def get_manager() -> AgentManager:
    """Get the singleton manager instance."""
    global _manager
    if _manager is None:
        _manager = AgentManager()
    return _manager


def reset_manager() -> None:
    """Drop the singleton so the next call re-reads the configured path (tests)."""
    global _manager
    _manager = None
