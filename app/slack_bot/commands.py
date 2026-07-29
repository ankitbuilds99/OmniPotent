"""Slash command router and handlers.

Comprehensive command system with autocomplete support.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Awaitable

log = logging.getLogger("devcopilot.slack_bot.commands")


class CommandCategory(str, Enum):
    INVESTIGATION = "investigation"
    INCIDENT = "incident"
    ACTION = "action"
    INFO = "info"
    POSTMORTEM = "postmortem"
    WORKFLOW = "workflow"
    ADMIN = "admin"


@dataclass
class SlashCommand:
    """Definition of a slash command."""

    name: str
    description: str
    usage: str
    category: CommandCategory
    examples: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    requires_incident: bool = False
    requires_approval: bool = False
    min_role: str = "viewer"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "usage": self.usage,
            "category": self.category.value,
            "examples": self.examples,
            "aliases": self.aliases,
            "requires_incident": self.requires_incident,
            "requires_approval": self.requires_approval,
            "min_role": self.min_role,
        }


@dataclass
class CommandResult:
    """Result of executing a command."""

    text: str
    blocks: list[dict[str, Any]] = field(default_factory=list)
    attachments: list[dict[str, Any]] = field(default_factory=list)
    ephemeral: bool = False
    success: bool = True
    error: str | None = None
    follow_up_commands: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "has_blocks": len(self.blocks) > 0,
            "ephemeral": self.ephemeral,
            "success": self.success,
            "error": self.error,
        }


CommandHandler = Callable[[str, str, str], Awaitable[CommandResult]]


class CommandRouter:
    """Routes and executes slash commands."""

    def __init__(self) -> None:
        self._commands: dict[str, SlashCommand] = {}
        self._handlers: dict[str, CommandHandler] = {}
        self._aliases: dict[str, str] = {}
        self._register_default_commands()

    def _register_default_commands(self) -> None:
        """Register all default commands."""
        commands = [
            SlashCommand(
                name="investigate",
                description="Start an AI-powered investigation",
                usage="/copilot investigate <query>",
                category=CommandCategory.INVESTIGATION,
                examples=[
                    "/copilot investigate why is checkout slow",
                    "/copilot investigate database connection errors",
                ],
            ),
            SlashCommand(
                name="ask",
                description="Ask a question about the current incident",
                usage="/copilot ask <question>",
                category=CommandCategory.INVESTIGATION,
                examples=[
                    "/copilot ask what was the last deploy",
                    "/copilot ask who is affected",
                ],
                requires_incident=True,
            ),
            SlashCommand(
                name="analyze",
                description="Deep dive analysis of a service",
                usage="/copilot analyze <service>",
                category=CommandCategory.INVESTIGATION,
                examples=[
                    "/copilot analyze checkout-api",
                    "/copilot analyze payment-service",
                ],
            ),
            SlashCommand(
                name="compare",
                description="Compare two incidents",
                usage="/copilot compare <incident1> <incident2>",
                category=CommandCategory.INVESTIGATION,
                examples=[
                    "/copilot compare INC-123 INC-456",
                ],
            ),
            SlashCommand(
                name="incident",
                description="Incident management commands",
                usage="/copilot incident <create|update|resolve|escalate|severity>",
                category=CommandCategory.INCIDENT,
                examples=[
                    "/copilot incident create Checkout API 500 errors",
                    "/copilot incident update investigating root cause",
                    "/copilot incident resolve",
                    "/copilot incident escalate",
                    "/copilot incident severity P1",
                ],
            ),
            SlashCommand(
                name="fix",
                description="Generate a fix PR for the identified issue",
                usage="/copilot fix",
                category=CommandCategory.ACTION,
                requires_incident=True,
                requires_approval=True,
                min_role="operator",
            ),
            SlashCommand(
                name="rollback",
                description="Rollback a deployment",
                usage="/copilot rollback <service> [version]",
                category=CommandCategory.ACTION,
                examples=[
                    "/copilot rollback billing-service",
                    "/copilot rollback billing-service v2.3.0",
                ],
                requires_approval=True,
                min_role="operator",
            ),
            SlashCommand(
                name="scale",
                description="Scale a service",
                usage="/copilot scale <service> <replicas>",
                category=CommandCategory.ACTION,
                examples=[
                    "/copilot scale checkout-api 10",
                ],
                requires_approval=True,
                min_role="operator",
            ),
            SlashCommand(
                name="restart",
                description="Restart pods/containers",
                usage="/copilot restart <service|pod>",
                category=CommandCategory.ACTION,
                examples=[
                    "/copilot restart checkout-api",
                    "/copilot restart checkout-api-pod-xyz",
                ],
                requires_approval=True,
                min_role="operator",
            ),
            SlashCommand(
                name="page",
                description="Page a team or individual",
                usage="/copilot page <team|user>",
                category=CommandCategory.ACTION,
                examples=[
                    "/copilot page database-team",
                    "/copilot page @alice",
                ],
                min_role="responder",
            ),
            SlashCommand(
                name="status",
                description="Show current investigation/incident status",
                usage="/copilot status",
                category=CommandCategory.INFO,
            ),
            SlashCommand(
                name="timeline",
                description="Show incident timeline",
                usage="/copilot timeline",
                category=CommandCategory.INFO,
                requires_incident=True,
            ),
            SlashCommand(
                name="metrics",
                description="Show key metrics for a service",
                usage="/copilot metrics <service> [timerange]",
                category=CommandCategory.INFO,
                examples=[
                    "/copilot metrics checkout-api",
                    "/copilot metrics checkout-api 1h",
                ],
            ),
            SlashCommand(
                name="logs",
                description="Fetch relevant logs",
                usage="/copilot logs <service> [timerange]",
                category=CommandCategory.INFO,
                examples=[
                    "/copilot logs checkout-api 30m",
                ],
            ),
            SlashCommand(
                name="similar",
                description="Find similar past incidents",
                usage="/copilot similar",
                category=CommandCategory.INFO,
                requires_incident=True,
            ),
            SlashCommand(
                name="runbook",
                description="Display a runbook",
                usage="/copilot runbook <name>",
                category=CommandCategory.INFO,
                examples=[
                    "/copilot runbook checkout-api-troubleshooting",
                ],
            ),
            SlashCommand(
                name="postmortem",
                description="Postmortem commands",
                usage="/copilot postmortem <generate|actions|export>",
                category=CommandCategory.POSTMORTEM,
                examples=[
                    "/copilot postmortem generate",
                    "/copilot postmortem actions",
                    "/copilot postmortem export notion",
                ],
                requires_incident=True,
            ),
            SlashCommand(
                name="workflow",
                description="Workflow automation commands",
                usage="/copilot workflow <create|run|list>",
                category=CommandCategory.WORKFLOW,
                examples=[
                    "/copilot workflow create restart-safely",
                    "/copilot workflow run restart-safely checkout-api",
                    "/copilot workflow list",
                ],
            ),
            SlashCommand(
                name="handoff",
                description="Generate shift handoff summary",
                usage="/copilot handoff <@user>",
                category=CommandCategory.INCIDENT,
                examples=[
                    "/copilot handoff @bob",
                ],
            ),
            SlashCommand(
                name="suggest",
                description="Suggest who to page for this incident",
                usage="/copilot suggest",
                category=CommandCategory.INFO,
                requires_incident=True,
            ),
            SlashCommand(
                name="help",
                description="Show available commands",
                usage="/copilot help [command]",
                category=CommandCategory.INFO,
            ),
        ]

        for cmd in commands:
            self.register(cmd)

    def register(
        self,
        command: SlashCommand,
        handler: CommandHandler | None = None,
    ) -> None:
        """Register a command."""
        self._commands[command.name] = command

        if handler:
            self._handlers[command.name] = handler
        else:
            self._handlers[command.name] = self._get_default_handler(command.name)

        for alias in command.aliases:
            self._aliases[alias] = command.name

    def _get_default_handler(self, name: str) -> CommandHandler:
        """Get default handler for a command."""
        handlers = {
            "investigate": self._handle_investigate,
            "ask": self._handle_ask,
            "analyze": self._handle_analyze,
            "compare": self._handle_compare,
            "incident": self._handle_incident,
            "fix": self._handle_fix,
            "rollback": self._handle_rollback,
            "scale": self._handle_scale,
            "restart": self._handle_restart,
            "page": self._handle_page,
            "status": self._handle_status,
            "timeline": self._handle_timeline,
            "metrics": self._handle_metrics,
            "logs": self._handle_logs,
            "similar": self._handle_similar,
            "runbook": self._handle_runbook,
            "postmortem": self._handle_postmortem,
            "workflow": self._handle_workflow,
            "handoff": self._handle_handoff,
            "suggest": self._handle_suggest,
            "help": self._handle_help,
        }

        return handlers.get(name, self._handle_unknown)

    async def route(
        self,
        command: str,
        text: str,
        user_id: str,
        channel_id: str,
    ) -> CommandResult:
        """Route a command to its handler."""
        command = command.lstrip("/").lower()

        if command != "copilot":
            return CommandResult(
                text="Unknown command. Use /copilot help for available commands.",
                success=False,
            )

        parts = text.strip().split(maxsplit=1)
        if not parts:
            return await self._handle_help("", user_id, channel_id)

        subcommand = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        actual_command = self._aliases.get(subcommand, subcommand)

        if actual_command not in self._commands:
            suggestions = self._get_suggestions(subcommand)
            suggestion_text = ""
            if suggestions:
                suggestion_text = f"\n\nDid you mean: {', '.join(suggestions)}?"

            return CommandResult(
                text=f"Unknown command: {subcommand}{suggestion_text}",
                success=False,
            )

        handler = self._handlers.get(actual_command, self._handle_unknown)

        log.info(
            "Routing command: command=%s args=%s user=%s",
            actual_command,
            args[:50],
            user_id,
        )

        return await handler(args, user_id, channel_id)

    def _get_suggestions(self, partial: str) -> list[str]:
        """Get command suggestions for partial input."""
        suggestions = []

        for name in self._commands:
            if name.startswith(partial) or partial in name:
                suggestions.append(name)

        return suggestions[:3]

    def get_autocomplete(self, partial: str) -> list[dict[str, str]]:
        """Get autocomplete suggestions."""
        suggestions = []

        for name, cmd in self._commands.items():
            if partial.lower() in name.lower() or partial.lower() in cmd.description.lower():
                suggestions.append({
                    "name": f"/copilot {name}",
                    "description": cmd.description,
                })

        return suggestions[:10]

    async def _handle_investigate(
        self,
        args: str,
        user_id: str,
        channel_id: str,
    ) -> CommandResult:
        """Handle investigate command."""
        if not args:
            return CommandResult(
                text="Please provide a query. Example: /copilot investigate why is checkout slow",
                success=False,
            )

        from app.slack_bot.blocks import get_block_builder

        builder = get_block_builder()

        blocks = builder.investigation_started(
            query=args,
            user_id=user_id,
        )

        return CommandResult(
            text=f"Starting investigation: {args}",
            blocks=blocks,
            follow_up_commands=["status", "timeline"],
        )

    async def _handle_ask(
        self,
        args: str,
        user_id: str,
        channel_id: str,
    ) -> CommandResult:
        """Handle ask command."""
        if not args:
            return CommandResult(
                text="Please provide a question.",
                success=False,
            )

        return CommandResult(
            text=f"Processing question: {args}",
            follow_up_commands=["ask", "investigate"],
        )

    async def _handle_analyze(
        self,
        args: str,
        user_id: str,
        channel_id: str,
    ) -> CommandResult:
        """Handle analyze command."""
        service = args.strip() or "checkout-api"

        from app.slack_bot.blocks import get_block_builder

        builder = get_block_builder()

        blocks = builder.service_analysis(
            service=service,
            health="degraded",
            metrics={
                "latency_p99": "2.3s",
                "error_rate": "5.2%",
                "requests_per_sec": "1,234",
            },
            recent_deploys=[
                {"version": "v2.3.1", "time": "14:02", "author": "sarah"},
                {"version": "v2.3.0", "time": "09:15", "author": "bob"},
            ],
        )

        return CommandResult(
            text=f"Analyzing {service}...",
            blocks=blocks,
        )

    async def _handle_compare(
        self,
        args: str,
        user_id: str,
        channel_id: str,
    ) -> CommandResult:
        """Handle compare command."""
        incidents = re.findall(r"INC-\d+", args.upper())

        if len(incidents) < 2:
            return CommandResult(
                text="Please provide two incident IDs. Example: /copilot compare INC-123 INC-456",
                success=False,
            )

        return CommandResult(
            text=f"Comparing {incidents[0]} and {incidents[1]}...",
        )

    async def _handle_incident(
        self,
        args: str,
        user_id: str,
        channel_id: str,
    ) -> CommandResult:
        """Handle incident subcommands."""
        parts = args.strip().split(maxsplit=1)
        subcommand = parts[0].lower() if parts else ""
        sub_args = parts[1] if len(parts) > 1 else ""

        if subcommand == "create":
            incident_id = f"INC-{int(datetime.now(timezone.utc).timestamp()) % 10000}"

            from app.slack_bot.blocks import get_block_builder

            builder = get_block_builder()

            blocks = builder.incident_created(
                incident_id=incident_id,
                title=sub_args or "New Incident",
                severity="P2",
                created_by=user_id,
            )

            return CommandResult(
                text=f"Incident {incident_id} created",
                blocks=blocks,
            )

        elif subcommand == "update":
            return CommandResult(
                text=f"Status updated: {sub_args}",
            )

        elif subcommand == "resolve":
            return CommandResult(
                text="Incident marked as resolved.",
            )

        elif subcommand == "escalate":
            return CommandResult(
                text="Incident escalated to next tier.",
            )

        elif subcommand == "severity":
            severity = sub_args.upper() if sub_args else "P2"
            return CommandResult(
                text=f"Severity changed to {severity}",
            )

        return CommandResult(
            text="Usage: /copilot incident <create|update|resolve|escalate|severity>",
            success=False,
        )

    async def _handle_fix(
        self,
        args: str,
        user_id: str,
        channel_id: str,
    ) -> CommandResult:
        """Handle fix command."""
        from app.slack_bot.blocks import get_block_builder

        builder = get_block_builder()

        blocks = builder.approval_card(
            approval_id=f"fix_{datetime.now(timezone.utc).timestamp()}",
            action_type="Create Pull Request",
            description="Fix connection leak in billing-service",
            details={
                "repository": "company/billing-service",
                "branch": "fix/connection-leak-inc-1234",
                "changes": "+3 lines, -5 lines",
            },
            risk_level="low",
            requested_by=user_id,
        )

        return CommandResult(
            text="Generating fix PR...",
            blocks=blocks,
        )

    async def _handle_rollback(
        self,
        args: str,
        user_id: str,
        channel_id: str,
    ) -> CommandResult:
        """Handle rollback command."""
        parts = args.strip().split()
        service = parts[0] if parts else "billing-service"
        version = parts[1] if len(parts) > 1 else "previous"

        from app.slack_bot.blocks import get_block_builder

        builder = get_block_builder()

        blocks = builder.approval_card(
            approval_id=f"rollback_{datetime.now(timezone.utc).timestamp()}",
            action_type="Rollback Deployment",
            description=f"Rollback {service} to {version}",
            details={
                "service": service,
                "current_version": "v2.3.1",
                "target_version": version,
                "affected_services": ["checkout-api", "payment-service"],
            },
            risk_level="medium",
            requested_by=user_id,
        )

        return CommandResult(
            text=f"Preparing rollback of {service}...",
            blocks=blocks,
        )

    async def _handle_scale(
        self,
        args: str,
        user_id: str,
        channel_id: str,
    ) -> CommandResult:
        """Handle scale command."""
        parts = args.strip().split()

        if len(parts) < 2:
            return CommandResult(
                text="Usage: /copilot scale <service> <replicas>",
                success=False,
            )

        service = parts[0]
        replicas = parts[1]

        from app.slack_bot.blocks import get_block_builder

        builder = get_block_builder()

        blocks = builder.approval_card(
            approval_id=f"scale_{datetime.now(timezone.utc).timestamp()}",
            action_type="Scale Service",
            description=f"Scale {service} to {replicas} replicas",
            details={
                "service": service,
                "current_replicas": 3,
                "target_replicas": int(replicas),
            },
            risk_level="low",
            requested_by=user_id,
        )

        return CommandResult(
            text=f"Preparing to scale {service}...",
            blocks=blocks,
        )

    async def _handle_restart(
        self,
        args: str,
        user_id: str,
        channel_id: str,
    ) -> CommandResult:
        """Handle restart command."""
        target = args.strip() or "checkout-api"

        from app.slack_bot.blocks import get_block_builder

        builder = get_block_builder()

        blocks = builder.approval_card(
            approval_id=f"restart_{datetime.now(timezone.utc).timestamp()}",
            action_type="Restart Service",
            description=f"Rolling restart of {target}",
            details={
                "target": target,
                "strategy": "rolling",
                "estimated_time": "2 minutes",
            },
            risk_level="low",
            requested_by=user_id,
        )

        return CommandResult(
            text=f"Preparing restart of {target}...",
            blocks=blocks,
        )

    async def _handle_page(
        self,
        args: str,
        user_id: str,
        channel_id: str,
    ) -> CommandResult:
        """Handle page command."""
        target = args.strip()

        if not target:
            return CommandResult(
                text="Please specify who to page. Example: /copilot page @alice or /copilot page database-team",
                success=False,
            )

        return CommandResult(
            text=f"Paging {target}...",
        )

    async def _handle_status(
        self,
        args: str,
        user_id: str,
        channel_id: str,
    ) -> CommandResult:
        """Handle status command."""
        from app.slack_bot.blocks import get_block_builder

        builder = get_block_builder()

        blocks = builder.status_card(
            incident_id="INC-1234",
            status="Investigating",
            duration="12m 34s",
            responders=["alice", "bob"],
            services=["checkout-api", "billing-service"],
            root_cause="Connection leak in billing-service v2.3.1",
            confidence=0.87,
        )

        return CommandResult(
            text="Current status",
            blocks=blocks,
        )

    async def _handle_timeline(
        self,
        args: str,
        user_id: str,
        channel_id: str,
    ) -> CommandResult:
        """Handle timeline command."""
        from app.slack_bot.blocks import get_block_builder

        builder = get_block_builder()

        blocks = builder.timeline_card(
            incident_id="INC-1234",
            events=[
                {"time": "14:02", "event": "Alert fired: checkout-api 500 errors"},
                {"time": "14:03", "event": "Incident created by @alice"},
                {"time": "14:05", "event": "Copilot identified root cause"},
                {"time": "14:07", "event": "@bob initiated rollback"},
                {"time": "14:10", "event": "Rollback complete, verifying"},
            ],
        )

        return CommandResult(
            text="Incident timeline",
            blocks=blocks,
        )

    async def _handle_metrics(
        self,
        args: str,
        user_id: str,
        channel_id: str,
    ) -> CommandResult:
        """Handle metrics command."""
        parts = args.strip().split()
        service = parts[0] if parts else "checkout-api"

        from app.slack_bot.blocks import get_block_builder

        builder = get_block_builder()

        blocks = builder.metrics_card(
            service=service,
            metrics={
                "latency_p99": {"value": "180ms", "trend": "down", "change": "-96%"},
                "error_rate": {"value": "0.1%", "trend": "down", "change": "-99%"},
                "throughput": {"value": "1,234 rps", "trend": "stable", "change": "+2%"},
                "cpu": {"value": "45%", "trend": "stable", "change": "-5%"},
                "memory": {"value": "72%", "trend": "up", "change": "+8%"},
            },
        )

        return CommandResult(
            text=f"Metrics for {service}",
            blocks=blocks,
        )

    async def _handle_logs(
        self,
        args: str,
        user_id: str,
        channel_id: str,
    ) -> CommandResult:
        """Handle logs command."""
        parts = args.strip().split()
        service = parts[0] if parts else "checkout-api"

        return CommandResult(
            text=f"Fetching logs for {service}...",
        )

    async def _handle_similar(
        self,
        args: str,
        user_id: str,
        channel_id: str,
    ) -> CommandResult:
        """Handle similar command."""
        from app.slack_bot.blocks import get_block_builder

        builder = get_block_builder()

        blocks = builder.similar_incidents(
            incidents=[
                {
                    "id": "INC-892",
                    "title": "Billing service connection pool exhausted",
                    "date": "2024-01-02",
                    "similarity": 0.94,
                    "resolution": "Fixed connection leak in payment processor",
                },
                {
                    "id": "INC-756",
                    "title": "Database connection timeout errors",
                    "date": "2023-12-15",
                    "similarity": 0.78,
                    "resolution": "Increased pool size and added connection timeout",
                },
            ],
        )

        return CommandResult(
            text="Similar past incidents",
            blocks=blocks,
        )

    async def _handle_runbook(
        self,
        args: str,
        user_id: str,
        channel_id: str,
    ) -> CommandResult:
        """Handle runbook command."""
        runbook_name = args.strip() or "checkout-api-troubleshooting"

        return CommandResult(
            text=f"Loading runbook: {runbook_name}",
        )

    async def _handle_postmortem(
        self,
        args: str,
        user_id: str,
        channel_id: str,
    ) -> CommandResult:
        """Handle postmortem subcommands."""
        subcommand = args.strip().split()[0].lower() if args else "generate"

        if subcommand == "generate":
            return CommandResult(
                text="Generating postmortem draft...",
            )
        elif subcommand == "actions":
            return CommandResult(
                text="Action items from this incident:\n1. Add connection pool monitoring\n2. Implement circuit breaker\n3. Add pre-deploy connection leak tests",
            )
        elif subcommand == "export":
            return CommandResult(
                text="Exporting postmortem to Notion...",
            )

        return CommandResult(
            text="Usage: /copilot postmortem <generate|actions|export>",
            success=False,
        )

    async def _handle_workflow(
        self,
        args: str,
        user_id: str,
        channel_id: str,
    ) -> CommandResult:
        """Handle workflow subcommands."""
        parts = args.strip().split(maxsplit=1)
        subcommand = parts[0].lower() if parts else "list"

        if subcommand == "create":
            return CommandResult(
                text="Creating workflow. What steps should it include?",
            )
        elif subcommand == "run":
            workflow_name = parts[1].split()[0] if len(parts) > 1 else "restart-safely"
            return CommandResult(
                text=f"Running workflow: {workflow_name}",
            )
        elif subcommand == "list":
            return CommandResult(
                text="Available workflows:\n- restart-safely\n- scale-up-emergency\n- db-failover",
            )

        return CommandResult(
            text="Usage: /copilot workflow <create|run|list>",
            success=False,
        )

    async def _handle_handoff(
        self,
        args: str,
        user_id: str,
        channel_id: str,
    ) -> CommandResult:
        """Handle handoff command."""
        target_user = args.strip() or "@bob"

        from app.slack_bot.blocks import get_block_builder

        builder = get_block_builder()

        blocks = builder.handoff_card(
            from_user=user_id,
            to_user=target_user.lstrip("@"),
            active_incidents=[
                {"id": "INC-1234", "title": "Checkout 500s", "status": "Investigating"},
            ],
            monitoring=[
                "payment-service memory trending up",
                "Scheduled deploy at 18:00",
            ],
            notes="Fix PR ready, needs one more approval. Watch Redis.",
        )

        return CommandResult(
            text=f"Generating handoff for {target_user}",
            blocks=blocks,
        )

    async def _handle_suggest(
        self,
        args: str,
        user_id: str,
        channel_id: str,
    ) -> CommandResult:
        """Handle suggest command."""
        from app.slack_bot.blocks import get_block_builder

        builder = get_block_builder()

        blocks = builder.suggest_responders(
            primary=[
                {"user": "alice", "reason": "checkout-api owner (on-call now)"},
                {"user": "bob", "reason": "billing-service owner"},
            ],
            escalation=[
                {"user": "charlie", "reason": "SRE lead (for P1 incidents)"},
            ],
            historical=[
                {"user": "david", "reason": "resolved similar incident INC-892"},
            ],
        )

        return CommandResult(
            text="Suggested responders",
            blocks=blocks,
        )

    async def _handle_help(
        self,
        args: str,
        user_id: str,
        channel_id: str,
    ) -> CommandResult:
        """Handle help command."""
        specific_command = args.strip().lower() if args else None

        if specific_command and specific_command in self._commands:
            cmd = self._commands[specific_command]
            examples = "\n".join(f"  {ex}" for ex in cmd.examples) if cmd.examples else "  (no examples)"

            return CommandResult(
                text=(
                    f"*{cmd.name}*\n"
                    f"{cmd.description}\n\n"
                    f"*Usage:* `{cmd.usage}`\n\n"
                    f"*Examples:*\n{examples}"
                ),
            )

        categories: dict[str, list[str]] = {}
        for name, cmd in self._commands.items():
            cat = cmd.category.value.title()
            if cat not in categories:
                categories[cat] = []
            categories[cat].append(f"`{name}` - {cmd.description}")

        help_text = "*DevOps Copilot Commands*\n\n"

        for cat, cmds in sorted(categories.items()):
            help_text += f"*{cat}:*\n"
            help_text += "\n".join(cmds[:5])
            if len(cmds) > 5:
                help_text += f"\n  _...and {len(cmds) - 5} more_"
            help_text += "\n\n"

        help_text += "_Type `/copilot help <command>` for detailed help._"

        return CommandResult(text=help_text)

    async def _handle_unknown(
        self,
        args: str,
        user_id: str,
        channel_id: str,
    ) -> CommandResult:
        """Handle unknown command."""
        return CommandResult(
            text="Unknown command. Use /copilot help for available commands.",
            success=False,
        )


_router: CommandRouter | None = None


def get_command_router() -> CommandRouter:
    """Get the default command router."""
    global _router
    if _router is None:
        _router = CommandRouter()
    return _router
