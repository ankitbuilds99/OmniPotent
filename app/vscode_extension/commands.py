"""VS Code Command Palette Integration.

Provides commands for the VS Code extension.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

log = logging.getLogger("devcopilot.vscode_extension.commands")


@dataclass
class VSCodeCommand:
    """A VS Code command."""

    command_id: str
    title: str
    description: str
    category: str = "DevOps Copilot"
    keybinding: str | None = None
    when: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": f"devcopilot.{self.command_id}",
            "title": self.title,
            "description": self.description,
            "category": self.category,
            "keybinding": self.keybinding,
            "when": self.when,
        }


@dataclass
class CommandResult:
    """Result of executing a command."""

    command_id: str
    success: bool
    data: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    executed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command_id,
            "success": self.success,
            "data": self.data,
            "error": self.error,
            "executed_at": self.executed_at.isoformat(),
        }


class CommandPalette:
    """Manages VS Code commands."""

    def __init__(self) -> None:
        self._commands: dict[str, VSCodeCommand] = {}
        self._handlers: dict[str, Callable[..., CommandResult]] = {}
        self._init_default_commands()

    def _init_default_commands(self) -> None:
        """Initialize default commands."""
        commands = [
            VSCodeCommand(
                command_id="investigateLastError",
                title="Investigate Last Error",
                description="Run AI investigation on the last error",
                keybinding="ctrl+shift+i",
            ),
            VSCodeCommand(
                command_id="showLogs",
                title="Show Logs for This Service",
                description="Stream logs for the current service in terminal",
                keybinding="ctrl+shift+l",
            ),
            VSCodeCommand(
                command_id="runPlaybook",
                title="Run Playbook...",
                description="Select and run a playbook",
            ),
            VSCodeCommand(
                command_id="createIncident",
                title="Create Incident from Selection",
                description="Create an incident from selected code/error",
                when="editorHasSelection",
            ),
            VSCodeCommand(
                command_id="viewRelatedIncidents",
                title="View Related Incidents",
                description="Show incidents related to this file",
            ),
            VSCodeCommand(
                command_id="toggleAnnotations",
                title="Toggle Incident Annotations",
                description="Show/hide incident annotations in editor",
            ),
            VSCodeCommand(
                command_id="streamLogs",
                title="Stream Logs in Terminal",
                description="Open log stream in integrated terminal",
            ),
            VSCodeCommand(
                command_id="openInvestigation",
                title="Open Investigation Panel",
                description="Open the investigation sidebar panel",
            ),
            VSCodeCommand(
                command_id="checkPRRisk",
                title="Check PR Risk Score",
                description="Analyze risk score for staged changes",
                keybinding="ctrl+shift+r",
            ),
            VSCodeCommand(
                command_id="applyFix",
                title="Apply AI Fix",
                description="Apply suggested AI fix for an incident",
            ),
            VSCodeCommand(
                command_id="showTraceViewer",
                title="Show Trace Viewer",
                description="Open distributed trace viewer",
            ),
            VSCodeCommand(
                command_id="connectIntegration",
                title="Connect Integration...",
                description="Connect a new integration",
            ),
        ]

        for cmd in commands:
            self._commands[cmd.command_id] = cmd

    def get_commands(self) -> list[VSCodeCommand]:
        """Get all available commands."""
        return list(self._commands.values())

    def get_command(self, command_id: str) -> VSCodeCommand | None:
        """Get a command by ID."""
        return self._commands.get(command_id)

    def register_handler(
        self,
        command_id: str,
        handler: Callable[..., CommandResult],
    ) -> bool:
        """Register a handler for a command."""
        if command_id not in self._commands:
            return False
        self._handlers[command_id] = handler
        return True

    def execute(
        self,
        command_id: str,
        **kwargs: Any,
    ) -> CommandResult:
        """Execute a command."""
        command = self._commands.get(command_id)
        if not command:
            return CommandResult(
                command_id=command_id,
                success=False,
                error=f"Unknown command: {command_id}",
            )

        handler = self._handlers.get(command_id)
        if handler:
            try:
                return handler(**kwargs)
            except Exception as e:
                log.error("Command failed: %s - %s", command_id, str(e))
                return CommandResult(
                    command_id=command_id,
                    success=False,
                    error=str(e),
                )

        return self._execute_default(command, kwargs)

    def _execute_default(
        self,
        command: VSCodeCommand,
        kwargs: dict[str, Any],
    ) -> CommandResult:
        """Execute command with default behavior."""
        command_id = command.command_id

        if command_id == "investigateLastError":
            return CommandResult(
                command_id=command_id,
                success=True,
                data={
                    "investigation_id": f"inv_{datetime.now(timezone.utc).timestamp()}",
                    "status": "started",
                    "message": "Investigation started for last error",
                },
            )

        elif command_id == "checkPRRisk":
            return CommandResult(
                command_id=command_id,
                success=True,
                data={
                    "score": 3.5,
                    "risk_level": "medium",
                    "files_analyzed": kwargs.get("files", 0),
                },
            )

        elif command_id == "viewRelatedIncidents":
            file_path = kwargs.get("file", "")
            return CommandResult(
                command_id=command_id,
                success=True,
                data={
                    "file": file_path,
                    "incidents": [
                        {"id": "INC-5521", "title": "Checkout 500 errors"},
                        {"id": "INC-5520", "title": "Redis timeout"},
                    ],
                },
            )

        elif command_id == "runPlaybook":
            return CommandResult(
                command_id=command_id,
                success=True,
                data={
                    "available_playbooks": [
                        {"id": "k8s-oom", "name": "K8s OOM Recovery"},
                        {"id": "pg-pool", "name": "PostgreSQL Connection Pool"},
                    ],
                },
            )

        elif command_id == "toggleAnnotations":
            return CommandResult(
                command_id=command_id,
                success=True,
                data={"annotations_visible": True},
            )

        return CommandResult(
            command_id=command_id,
            success=True,
            data={"message": f"Command {command_id} executed"},
        )

    def get_status_bar_items(self) -> list[dict[str, Any]]:
        """Get status bar items for VS Code."""
        return [
            {
                "id": "devcopilot.status",
                "alignment": "right",
                "priority": 100,
                "text": "$(shield) DevOps Copilot",
                "tooltip": "System status and quick actions",
                "command": "devcopilot.openInvestigation",
            },
            {
                "id": "devcopilot.incidents",
                "alignment": "right",
                "priority": 99,
                "text": "$(bell) 1 Active",
                "tooltip": "Active incidents",
                "command": "devcopilot.viewRelatedIncidents",
            },
            {
                "id": "devcopilot.oncall",
                "alignment": "right",
                "priority": 98,
                "text": "$(person) On-Call: You",
                "tooltip": "Current on-call status",
            },
        ]

    def get_sidebar_views(self) -> list[dict[str, Any]]:
        """Get sidebar view definitions."""
        return [
            {
                "id": "devcopilot.incidents",
                "name": "Active Incidents",
                "icon": "media/alert.svg",
            },
            {
                "id": "devcopilot.stats",
                "name": "Quick Stats",
                "icon": "media/chart.svg",
            },
            {
                "id": "devcopilot.investigations",
                "name": "Recent Investigations",
                "icon": "media/search.svg",
            },
            {
                "id": "devcopilot.fileRisk",
                "name": "File Risk Analysis",
                "icon": "media/warning.svg",
            },
        ]


_palette: CommandPalette | None = None


def get_command_palette() -> CommandPalette:
    """Get the default command palette."""
    global _palette
    if _palette is None:
        _palette = CommandPalette()
    return _palette
