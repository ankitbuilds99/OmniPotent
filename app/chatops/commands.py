"""ChatOps Commands - Unified command handling for Slack and Teams."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Awaitable
from enum import Enum

log = logging.getLogger("omnipotent.chatops.commands")


class CommandType(str, Enum):
    """Types of ChatOps commands."""
    INVESTIGATE = "investigate"
    STATUS = "status"
    APPROVE = "approve"
    REJECT = "reject"
    ROLLBACK = "rollback"
    SCALE = "scale"
    RESTART = "restart"
    SILENCE = "silence"
    ACK = "ack"
    HELP = "help"
    LIST = "list"
    RUNBOOK = "runbook"


@dataclass
class Command:
    """A parsed ChatOps command."""
    type: CommandType
    args: list[str] = field(default_factory=list)
    flags: dict[str, str] = field(default_factory=dict)
    raw_text: str = ""
    user_id: str = ""
    channel_id: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class CommandResult:
    """Result of command execution."""
    success: bool
    message: str
    data: dict[str, Any] | None = None
    error: str | None = None
    follow_up_actions: list[str] | None = None


class CommandParser:
    """Parses raw text into Command objects."""

    # Command aliases
    ALIASES = {
        "inv": CommandType.INVESTIGATE,
        "investigate": CommandType.INVESTIGATE,
        "check": CommandType.STATUS,
        "status": CommandType.STATUS,
        "approve": CommandType.APPROVE,
        "yes": CommandType.APPROVE,
        "reject": CommandType.REJECT,
        "no": CommandType.REJECT,
        "deny": CommandType.REJECT,
        "rollback": CommandType.ROLLBACK,
        "revert": CommandType.ROLLBACK,
        "scale": CommandType.SCALE,
        "restart": CommandType.RESTART,
        "bounce": CommandType.RESTART,
        "silence": CommandType.SILENCE,
        "mute": CommandType.SILENCE,
        "ack": CommandType.ACK,
        "acknowledge": CommandType.ACK,
        "help": CommandType.HELP,
        "list": CommandType.LIST,
        "ls": CommandType.LIST,
        "runbook": CommandType.RUNBOOK,
        "rb": CommandType.RUNBOOK,
    }

    def parse(self, text: str, user_id: str = "", channel_id: str = "") -> Command | None:
        """Parse command text.

        Args:
            text: Raw command text
            user_id: User who sent the command
            channel_id: Channel where command was sent

        Returns:
            Parsed Command or None if not a valid command
        """
        text = text.strip()
        if not text:
            return None

        # Remove leading slash or @mention
        text = re.sub(r"^[/@]", "", text)
        text = re.sub(r"<@[A-Z0-9]+>\s*", "", text)

        parts = text.split()
        if not parts:
            return None

        command_str = parts[0].lower()
        command_type = self.ALIASES.get(command_str)

        if not command_type:
            return None

        # Parse flags (--key=value or --flag)
        args = []
        flags = {}

        for part in parts[1:]:
            if part.startswith("--"):
                if "=" in part:
                    key, value = part[2:].split("=", 1)
                    flags[key] = value
                else:
                    flags[part[2:]] = "true"
            elif part.startswith("-") and len(part) == 2:
                flags[part[1]] = "true"
            else:
                args.append(part)

        return Command(
            type=command_type,
            args=args,
            flags=flags,
            raw_text=text,
            user_id=user_id,
            channel_id=channel_id,
        )


class CommandHandler:
    """Handles ChatOps command execution.

    Provides a unified interface for executing commands from
    any chat platform (Slack, Teams, etc.)
    """

    def __init__(self):
        """Initialize the command handler."""
        self._parser = CommandParser()
        self._handlers: dict[CommandType, Callable[[Command], Awaitable[CommandResult]]] = {}
        self._middleware: list[Callable[[Command], Awaitable[Command | None]]] = []

        # Register default handlers
        self._register_defaults()

    def _register_defaults(self) -> None:
        """Register default command handlers."""
        self.register(CommandType.HELP, self._handle_help)

    def register(
        self,
        command_type: CommandType,
        handler: Callable[[Command], Awaitable[CommandResult]],
    ) -> None:
        """Register a command handler.

        Args:
            command_type: Type of command to handle
            handler: Async function(command) -> CommandResult
        """
        self._handlers[command_type] = handler
        log.info("Registered handler for: %s", command_type.value)

    def add_middleware(
        self,
        middleware: Callable[[Command], Awaitable[Command | None]],
    ) -> None:
        """Add command middleware.

        Middleware can modify commands or return None to reject them.

        Args:
            middleware: Async function(command) -> command or None
        """
        self._middleware.append(middleware)

    async def execute(
        self,
        text: str,
        user_id: str = "",
        channel_id: str = "",
    ) -> CommandResult:
        """Parse and execute a command.

        Args:
            text: Raw command text
            user_id: User who sent the command
            channel_id: Channel where command was sent

        Returns:
            CommandResult
        """
        # Parse command
        command = self._parser.parse(text, user_id, channel_id)

        if not command:
            return CommandResult(
                success=False,
                message="Unknown command. Type `help` for available commands.",
            )

        # Run middleware
        for middleware in self._middleware:
            result = await middleware(command)
            if result is None:
                return CommandResult(
                    success=False,
                    message="Command rejected by policy.",
                )
            command = result

        # Execute handler
        handler = self._handlers.get(command.type)

        if not handler:
            return CommandResult(
                success=False,
                message=f"No handler for command: {command.type.value}",
            )

        try:
            return await handler(command)
        except Exception as e:
            log.exception("Command execution failed: %s", command.type.value)
            return CommandResult(
                success=False,
                message="Command execution failed.",
                error=str(e),
            )

    async def _handle_help(self, command: Command) -> CommandResult:
        """Handle the help command."""
        help_text = """
*Available Commands:*

*Investigation:*
• `investigate <incident-id>` - Start AI investigation
• `status <incident-id>` - Get incident status

*Actions:*
• `approve <action-id>` - Approve a pending action
• `reject <action-id>` - Reject a pending action
• `rollback <service> [--revision=X]` - Rollback a deployment
• `restart <service>` - Restart a service
• `scale <service> <replicas>` - Scale a deployment

*Alerts:*
• `ack <alert-id>` - Acknowledge an alert
• `silence <alert-id> [--duration=1h]` - Silence an alert

*Other:*
• `list [incidents|alerts|approvals]` - List items
• `runbook <name>` - Execute a runbook
• `help` - Show this help message

*Flags:*
• `--force` - Skip confirmation
• `--dry-run` - Show what would happen
• `--reason="..."` - Provide a reason
        """.strip()

        return CommandResult(
            success=True,
            message=help_text,
        )


# Default handlers for integration with OmniPotent

async def create_investigate_handler(
    investigation_service: Any,  # ParallelInvestigationCoordinator
) -> Callable[[Command], Awaitable[CommandResult]]:
    """Create an investigate command handler."""

    async def handler(command: Command) -> CommandResult:
        if not command.args:
            return CommandResult(
                success=False,
                message="Usage: `investigate <incident-id>`",
            )

        incident_id = command.args[0]

        try:
            # Start investigation
            result = await investigation_service.investigate(
                incident_id=incident_id,
                context={"source": "chatops", "user": command.user_id},
            )

            return CommandResult(
                success=True,
                message=f"🔍 Investigation started for incident `{incident_id}`\n"
                        f"Session: `{result.session_id}`\n"
                        f"I'll post updates as I find things.",
                data={"session_id": str(result.session_id)},
            )

        except Exception as e:
            return CommandResult(
                success=False,
                message=f"Failed to start investigation: {e}",
                error=str(e),
            )

    return handler


async def create_approve_handler(
    approval_service: Any,
) -> Callable[[Command], Awaitable[CommandResult]]:
    """Create an approve command handler."""

    async def handler(command: Command) -> CommandResult:
        if not command.args:
            return CommandResult(
                success=False,
                message="Usage: `approve <action-id>`",
            )

        action_id = command.args[0]
        reason = command.flags.get("reason", "Approved via ChatOps")

        try:
            result = await approval_service.approve(
                action_id=action_id,
                approver_id=command.user_id,
                reason=reason,
            )

            return CommandResult(
                success=True,
                message=f"✅ Action `{action_id}` approved.\n{result.get('message', '')}",
                data=result,
            )

        except Exception as e:
            return CommandResult(
                success=False,
                message=f"Failed to approve: {e}",
                error=str(e),
            )

    return handler


async def create_status_handler(
    incident_service: Any,
) -> Callable[[Command], Awaitable[CommandResult]]:
    """Create a status command handler."""

    async def handler(command: Command) -> CommandResult:
        if not command.args:
            # List recent incidents
            return CommandResult(
                success=True,
                message="Usage: `status <incident-id>` or `list incidents`",
            )

        incident_id = command.args[0]

        try:
            status = await incident_service.get_status(incident_id)

            message = f"""
*Incident: {incident_id}*
• Status: {status.get('status', 'unknown')}
• Severity: {status.get('severity', 'unknown')}
• Duration: {status.get('duration_minutes', 0)} minutes
• Root Cause: {status.get('root_cause', 'Under investigation')}
            """.strip()

            return CommandResult(
                success=True,
                message=message,
                data=status,
            )

        except Exception as e:
            return CommandResult(
                success=False,
                message=f"Failed to get status: {e}",
                error=str(e),
            )

    return handler
