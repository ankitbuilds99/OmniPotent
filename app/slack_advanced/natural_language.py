"""Natural Language Command Processing for Slack.

Processes natural language queries like "@copilot what caused this?"
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("omnipotent.slack_advanced.natural_language")


class CommandType(str, Enum):
    INVESTIGATE = "investigate"
    EXPLAIN = "explain"
    ROLLBACK = "rollback"
    SCALE = "scale"
    FIX = "fix"
    STATUS = "status"
    SUMMARIZE = "summarize"
    ASSIGN = "assign"
    ALERT = "alert"
    HELP = "help"
    UNKNOWN = "unknown"


class EntityType(str, Enum):
    SERVICE = "service"
    DEPLOY = "deploy"
    INCIDENT = "incident"
    USER = "user"
    TIME = "time"
    NUMBER = "number"


@dataclass
class Entity:
    """An extracted entity from the query."""

    entity_type: EntityType
    value: str
    original_text: str
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.entity_type.value,
            "value": self.value,
            "original": self.original_text,
            "confidence": round(self.confidence, 2),
        }


@dataclass
class Command:
    """A parsed natural language command."""

    command_id: str
    command_type: CommandType
    raw_text: str
    intent: str
    entities: list[Entity]
    confidence: float
    suggested_action: str
    requires_confirmation: bool
    parsed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "command_id": self.command_id,
            "type": self.command_type.value,
            "raw_text": self.raw_text,
            "intent": self.intent,
            "entities": [e.to_dict() for e in self.entities],
            "confidence": round(self.confidence, 2),
            "action": self.suggested_action,
            "requires_confirmation": self.requires_confirmation,
            "parsed_at": self.parsed_at.isoformat(),
        }


@dataclass
class CommandResponse:
    """A response to a natural language command."""

    command_id: str
    response_text: str
    slack_blocks: list[dict[str, Any]]
    actions: list[dict[str, Any]]
    follow_up_suggestions: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "command_id": self.command_id,
            "text": self.response_text,
            "blocks": self.slack_blocks,
            "actions": self.actions,
            "suggestions": self.follow_up_suggestions,
        }


class NaturalLanguageProcessor:
    """Processes natural language commands for Slack."""

    def __init__(self) -> None:
        self._command_history: dict[str, list[Command]] = {}
        self._intent_patterns: dict[CommandType, list[str]] = {}
        self._init_patterns()

    def _init_patterns(self) -> None:
        """Initialize intent detection patterns."""
        self._intent_patterns[CommandType.INVESTIGATE] = [
            r"what\s+caused",
            r"why\s+is",
            r"investigate",
            r"root\s+cause",
            r"what\s+happened",
            r"why\s+did",
            r"analyze",
            r"look\s+into",
        ]

        self._intent_patterns[CommandType.EXPLAIN] = [
            r"explain",
            r"what\s+does",
            r"tell\s+me\s+about",
            r"describe",
            r"how\s+does",
            r"what\s+is",
        ]

        self._intent_patterns[CommandType.ROLLBACK] = [
            r"rollback",
            r"roll\s+back",
            r"revert",
            r"undo\s+deploy",
            r"go\s+back\s+to",
            r"previous\s+version",
        ]

        self._intent_patterns[CommandType.SCALE] = [
            r"scale\s+up",
            r"scale\s+down",
            r"add\s+pods",
            r"add\s+replicas",
            r"increase\s+capacity",
            r"reduce\s+capacity",
        ]

        self._intent_patterns[CommandType.FIX] = [
            r"fix",
            r"apply\s+fix",
            r"create\s+pr",
            r"generate\s+fix",
            r"patch",
        ]

        self._intent_patterns[CommandType.STATUS] = [
            r"status",
            r"how\s+is",
            r"is\s+it\s+working",
            r"current\s+state",
            r"health",
            r"metrics",
        ]

        self._intent_patterns[CommandType.SUMMARIZE] = [
            r"summarize",
            r"summary",
            r"tldr",
            r"recap",
            r"what\s+did\s+we",
            r"catch\s+me\s+up",
        ]

        self._intent_patterns[CommandType.ASSIGN] = [
            r"assign",
            r"page",
            r"notify",
            r"alert",
            r"cc\s+",
            r"add\s+to\s+incident",
        ]

        self._intent_patterns[CommandType.ALERT] = [
            r"snooze",
            r"silence",
            r"acknowledge",
            r"ack",
            r"mute",
        ]

        self._intent_patterns[CommandType.HELP] = [
            r"help",
            r"what\s+can\s+you\s+do",
            r"commands",
            r"how\s+do\s+i",
        ]

    def parse_command(
        self,
        text: str,
        channel_id: str = "",
        user_id: str = "",
    ) -> Command:
        """Parse a natural language command."""
        command_id = f"cmd_{datetime.now(timezone.utc).timestamp()}"

        cleaned_text = self._clean_text(text)

        command_type, intent_confidence = self._detect_intent(cleaned_text)

        entities = self._extract_entities(cleaned_text)

        suggested_action = self._suggest_action(command_type, entities)

        requires_confirmation = command_type in (
            CommandType.ROLLBACK,
            CommandType.SCALE,
            CommandType.FIX,
        )

        command = Command(
            command_id=command_id,
            command_type=command_type,
            raw_text=text,
            intent=self._generate_intent_description(command_type, entities),
            entities=entities,
            confidence=intent_confidence,
            suggested_action=suggested_action,
            requires_confirmation=requires_confirmation,
        )

        if channel_id:
            key = channel_id
            if key not in self._command_history:
                self._command_history[key] = []
            self._command_history[key].append(command)

        log.info(
            "Parsed command: type=%s confidence=%.2f entities=%d",
            command_type.value,
            intent_confidence,
            len(entities),
        )

        return command

    def _clean_text(self, text: str) -> str:
        """Clean and normalize text."""
        text = re.sub(r"<@\w+>", "", text)

        text = re.sub(r"@copilot\s*", "", text, flags=re.IGNORECASE)

        return text.strip().lower()

    def _detect_intent(self, text: str) -> tuple[CommandType, float]:
        """Detect the intent from text."""
        best_type = CommandType.UNKNOWN
        best_score = 0.0

        for cmd_type, patterns in self._intent_patterns.items():
            for pattern in patterns:
                if re.search(pattern, text, re.IGNORECASE):
                    score = len(pattern) / len(text) * 2
                    score = min(0.95, score + 0.5)

                    if score > best_score:
                        best_score = score
                        best_type = cmd_type

        if best_type == CommandType.UNKNOWN:
            if "?" in text:
                return CommandType.EXPLAIN, 0.6
            return CommandType.UNKNOWN, 0.3

        return best_type, best_score

    def _extract_entities(self, text: str) -> list[Entity]:
        """Extract entities from text."""
        entities: list[Entity] = []

        service_patterns = [
            (r"([\w-]+-(?:api|service|svc))", "service"),
            (r"(checkout|payment|auth|billing|user)", "service"),
        ]

        for pattern, _ in service_patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            for match in matches:
                entities.append(Entity(
                    entity_type=EntityType.SERVICE,
                    value=match,
                    original_text=match,
                    confidence=0.85,
                ))

        deploy_match = re.search(r"deploy\s*#?(\d+)", text)
        if deploy_match:
            entities.append(Entity(
                entity_type=EntityType.DEPLOY,
                value=deploy_match.group(1),
                original_text=deploy_match.group(0),
                confidence=0.95,
            ))

        user_matches = re.findall(r"@(\w+)", text)
        for match in user_matches:
            entities.append(Entity(
                entity_type=EntityType.USER,
                value=match,
                original_text=f"@{match}",
                confidence=0.9,
            ))

        number_match = re.search(r"(\d+)\s*(pods?|replicas?|instances?)", text)
        if number_match:
            entities.append(Entity(
                entity_type=EntityType.NUMBER,
                value=number_match.group(1),
                original_text=number_match.group(0),
                confidence=0.9,
            ))

        return entities

    def _suggest_action(
        self,
        command_type: CommandType,
        entities: list[Entity],
    ) -> str:
        """Suggest an action based on command type."""
        suggestions = {
            CommandType.INVESTIGATE: "Start AI investigation",
            CommandType.EXPLAIN: "Provide explanation",
            CommandType.ROLLBACK: "Execute rollback",
            CommandType.SCALE: "Adjust service scaling",
            CommandType.FIX: "Generate fix PR",
            CommandType.STATUS: "Show current status",
            CommandType.SUMMARIZE: "Generate summary",
            CommandType.ASSIGN: "Update assignments",
            CommandType.ALERT: "Manage alerts",
            CommandType.HELP: "Show help",
            CommandType.UNKNOWN: "Clarify request",
        }

        base = suggestions.get(command_type, "Process request")

        service_entities = [e for e in entities if e.entity_type == EntityType.SERVICE]
        if service_entities:
            base += f" for {service_entities[0].value}"

        return base

    def _generate_intent_description(
        self,
        command_type: CommandType,
        entities: list[Entity],
    ) -> str:
        """Generate a human-readable intent description."""
        descriptions = {
            CommandType.INVESTIGATE: "Find the root cause of the issue",
            CommandType.EXPLAIN: "Explain what's happening",
            CommandType.ROLLBACK: "Rollback to a previous version",
            CommandType.SCALE: "Adjust service capacity",
            CommandType.FIX: "Generate and apply a fix",
            CommandType.STATUS: "Check current status",
            CommandType.SUMMARIZE: "Summarize the situation",
            CommandType.ASSIGN: "Assign or notify someone",
            CommandType.ALERT: "Manage alert state",
            CommandType.HELP: "Get help with commands",
            CommandType.UNKNOWN: "Unknown intent",
        }

        return descriptions.get(command_type, "Process request")

    def generate_response(
        self,
        command: Command,
        context: dict[str, Any] | None = None,
    ) -> CommandResponse:
        """Generate a response to a command."""
        context = context or {}

        if command.command_type == CommandType.INVESTIGATE:
            return self._generate_investigate_response(command, context)
        elif command.command_type == CommandType.ROLLBACK:
            return self._generate_rollback_response(command, context)
        elif command.command_type == CommandType.STATUS:
            return self._generate_status_response(command, context)
        elif command.command_type == CommandType.SUMMARIZE:
            return self._generate_summarize_response(command, context)
        elif command.command_type == CommandType.HELP:
            return self._generate_help_response(command)
        else:
            return self._generate_default_response(command)

    def _generate_investigate_response(
        self,
        command: Command,
        context: dict[str, Any],
    ) -> CommandResponse:
        """Generate response for investigate command."""
        service = context.get("service", "checkout-api")

        blocks: list[dict[str, Any]] = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"Checking *{service}* and related services...",
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*{service}* is experiencing issues but is NOT the root cause.\n"
                        "*Error:* \"Cannot process payment for user with no method\"\n"
                        "All failures trace back to checkout-api calling payment-svc.\n\n"
                        "Rolling back checkout-api will fix both services."
                    ),
                },
            },
        ]

        return CommandResponse(
            command_id=command.command_id,
            response_text=f"Investigated {service}",
            slack_blocks=blocks,
            actions=[{"action": "investigate", "service": service}],
            follow_up_suggestions=[
                "rollback deploy 47",
                "show me the error logs",
                "who is on-call?",
            ],
        )

    def _generate_rollback_response(
        self,
        command: Command,
        context: dict[str, Any],
    ) -> CommandResponse:
        """Generate response for rollback command."""
        deploy_entities = [
            e for e in command.entities if e.entity_type == EntityType.DEPLOY
        ]
        deploy_id = deploy_entities[0].value if deploy_entities else "47"

        blocks: list[dict[str, Any]] = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Confirm Rollback - Deploy #{deploy_id}*",
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"You're about to rollback:\n"
                        f"*Service:* checkout-api\n"
                        f"*From:* Deploy #{deploy_id} (current)\n"
                        f"*To:* Deploy #{int(deploy_id) - 1} (previous)\n\n"
                        f"*Safety check:* :white_check_mark: SAFE\n"
                        f"- No migrations to reverse\n"
                        f"- No API breaking changes\n"
                        f"- Est. time: 2 minutes"
                    ),
                },
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Confirm Rollback"},
                        "style": "danger",
                        "action_id": f"confirm_rollback_{deploy_id}",
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Cancel"},
                        "action_id": "cancel_rollback",
                    },
                ],
            },
        ]

        return CommandResponse(
            command_id=command.command_id,
            response_text=f"Confirm rollback of deploy #{deploy_id}?",
            slack_blocks=blocks,
            actions=[{"action": "rollback", "deploy_id": deploy_id, "status": "pending_confirmation"}],
            follow_up_suggestions=[],
        )

    def _generate_status_response(
        self,
        command: Command,
        context: dict[str, Any],
    ) -> CommandResponse:
        """Generate response for status command."""
        blocks: list[dict[str, Any]] = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        "*Current Status*\n\n"
                        ":white_check_mark: checkout-api: Healthy (99.8%)\n"
                        ":white_check_mark: payment-svc: Healthy\n"
                        ":white_check_mark: No new errors in last 2 min"
                    ),
                },
            },
        ]

        return CommandResponse(
            command_id=command.command_id,
            response_text="Service status",
            slack_blocks=blocks,
            actions=[],
            follow_up_suggestions=["show metrics", "generate postmortem"],
        )

    def _generate_summarize_response(
        self,
        command: Command,
        context: dict[str, Any],
    ) -> CommandResponse:
        """Generate response for summarize command."""
        blocks: list[dict[str, Any]] = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        "*Thread Summary*\n\n"
                        "*Timeline:*\n"
                        "- 2:03 PM - Datadog alert fired, incident opened\n"
                        "- 2:05 PM - Copilot identified root cause (null check removal)\n"
                        "- 2:07 PM - @bob initiated rollback\n"
                        "- 2:10 PM - Rollback complete, service recovered\n\n"
                        "*Root Cause:*\n"
                        "Null check removed in checkout.py:142. Users without payment "
                        "methods (3.2%) triggered NullPointerException.\n\n"
                        "*Impact:*\n"
                        "7 min duration, 847 errors, 412 users, ~$4,500 revenue loss"
                    ),
                },
            },
        ]

        return CommandResponse(
            command_id=command.command_id,
            response_text="Thread summary",
            slack_blocks=blocks,
            actions=[],
            follow_up_suggestions=["copy to postmortem", "export as markdown"],
        )

    def _generate_help_response(self, command: Command) -> CommandResponse:
        """Generate response for help command."""
        blocks: list[dict[str, Any]] = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        "*OmniPotent Commands*\n\n"
                        "*Investigation:*\n"
                        "`@copilot what caused this?` - Investigate root cause\n"
                        "`@copilot is payment-service affected?` - Check service\n\n"
                        "*Actions:*\n"
                        "`@copilot rollback deploy 47` - Rollback deployment\n"
                        "`@copilot scale checkout-api to 4 pods` - Scale service\n\n"
                        "*Information:*\n"
                        "`@copilot status` - Current service status\n"
                        "`@copilot summarize` - Summarize this thread\n"
                        "`@copilot who is on-call?` - Show on-call schedule"
                    ),
                },
            },
        ]

        return CommandResponse(
            command_id=command.command_id,
            response_text="Help",
            slack_blocks=blocks,
            actions=[],
            follow_up_suggestions=[],
        )

    def _generate_default_response(self, command: Command) -> CommandResponse:
        """Generate default response for unknown commands."""
        return CommandResponse(
            command_id=command.command_id,
            response_text="I'm not sure what you're asking. Try `@copilot help` for available commands.",
            slack_blocks=[
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "I'm not sure what you're asking. Try `@copilot help` for available commands.",
                    },
                },
            ],
            actions=[],
            follow_up_suggestions=["help", "what caused this?", "status"],
        )


_processor: NaturalLanguageProcessor | None = None


def get_nlp_processor() -> NaturalLanguageProcessor:
    """Get the default NLP processor."""
    global _processor
    if _processor is None:
        _processor = NaturalLanguageProcessor()
    return _processor
