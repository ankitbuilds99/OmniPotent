"""Conversational AI for Slack.

Context-aware natural language understanding with
multi-turn conversation support.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("omnipotent.slack_bot.conversation")


class Intent(str, Enum):
    INVESTIGATE = "investigate"
    ROLLBACK = "rollback"
    SCALE = "scale"
    RESTART = "restart"
    STATUS = "status"
    EXPLAIN = "explain"
    COMPARE = "compare"
    FIX = "fix"
    PAGE = "page"
    HELP = "help"
    CONFIRM = "confirm"
    CANCEL = "cancel"
    FOLLOW_UP = "follow_up"
    UNKNOWN = "unknown"


class EntityType(str, Enum):
    SERVICE = "service"
    DEPLOY = "deploy"
    INCIDENT = "incident"
    USER = "user"
    TEAM = "team"
    TIME = "time"
    VERSION = "version"
    NUMBER = "number"
    METRIC = "metric"


@dataclass
class Entity:
    """An extracted entity from user input."""

    entity_type: EntityType
    value: str
    confidence: float = 1.0
    start: int = 0
    end: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.entity_type.value,
            "value": self.value,
            "confidence": self.confidence,
        }


@dataclass
class ParsedIntent:
    """A parsed user intent."""

    intent: Intent
    confidence: float
    entities: list[Entity]
    raw_text: str
    is_follow_up: bool = False

    def get_entity(self, entity_type: EntityType) -> Entity | None:
        for entity in self.entities:
            if entity.entity_type == entity_type:
                return entity
        return None

    def get_entities(self, entity_type: EntityType) -> list[Entity]:
        return [e for e in self.entities if e.entity_type == entity_type]

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent.value,
            "confidence": self.confidence,
            "entities": [e.to_dict() for e in self.entities],
            "is_follow_up": self.is_follow_up,
        }


@dataclass
class ConversationContext:
    """Context for a conversation."""

    conversation_id: str
    channel_id: str
    user_id: str
    thread_ts: str | None = None
    current_incident: str | None = None
    current_service: str | None = None
    last_intent: Intent | None = None
    last_entities: list[Entity] = field(default_factory=list)
    pending_action: str | None = None
    pending_details: dict[str, Any] = field(default_factory=dict)
    history: list[dict[str, Any]] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_active: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def is_stale(self) -> bool:
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=30)
        return self.last_active < cutoff

    def add_turn(
        self,
        role: str,
        content: str,
        intent: Intent | None = None,
        entities: list[Entity] | None = None,
    ) -> None:
        """Add a conversation turn."""
        self.history.append({
            "role": role,
            "content": content,
            "intent": intent.value if intent else None,
            "entities": [e.to_dict() for e in entities] if entities else [],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        if len(self.history) > 50:
            self.history = self.history[-50:]

        self.last_active = datetime.now(timezone.utc)

        if intent:
            self.last_intent = intent
        if entities:
            self.last_entities = entities

    def to_dict(self) -> dict[str, Any]:
        return {
            "conversation_id": self.conversation_id,
            "channel_id": self.channel_id,
            "user_id": self.user_id,
            "current_incident": self.current_incident,
            "current_service": self.current_service,
            "last_intent": self.last_intent.value if self.last_intent else None,
            "pending_action": self.pending_action,
            "history_length": len(self.history),
            "last_active": self.last_active.isoformat(),
        }


class IntentClassifier:
    """Classifies user intents from natural language."""

    def __init__(self) -> None:
        self._patterns: dict[Intent, list[str]] = {
            Intent.INVESTIGATE: [
                r"what('s| is) (wrong|happening|going on)",
                r"why is .+ (slow|down|failing|broken|erroring)",
                r"what caused",
                r"investigate",
                r"look into",
                r"check .+ (status|health)",
                r"analyze",
                r"dig into",
                r"find (the |)root cause",
                r"debug",
            ],
            Intent.ROLLBACK: [
                r"rollback",
                r"revert",
                r"undo (the |)(last |)deploy",
                r"go back to",
                r"roll back",
                r"restore (previous|last)",
            ],
            Intent.SCALE: [
                r"scale .+ to",
                r"increase .+ capacity",
                r"add more .+ (pods|replicas|instances)",
                r"scale (up|down|out)",
            ],
            Intent.RESTART: [
                r"restart",
                r"reboot",
                r"bounce",
                r"recycle",
            ],
            Intent.STATUS: [
                r"(what('s| is) the |)status",
                r"how('s| is) .+ (doing|performing)",
                r"is .+ (ok|healthy|up|down)",
                r"current state",
                r"show me",
            ],
            Intent.EXPLAIN: [
                r"explain",
                r"what does .+ (do|mean)",
                r"how does .+ work",
                r"tell me about",
                r"what is",
            ],
            Intent.COMPARE: [
                r"is this like",
                r"have we seen this before",
                r"similar (incidents|issues|problems)",
                r"compare (to|with)",
                r"like last time",
            ],
            Intent.FIX: [
                r"fix (it|this)",
                r"generate .+ (fix|patch|pr)",
                r"create .+ pr",
                r"auto(-| )fix",
            ],
            Intent.PAGE: [
                r"page",
                r"notify",
                r"alert",
                r"get .+ (on call|oncall)",
                r"call .+ in",
                r"escalate to",
            ],
            Intent.HELP: [
                r"help",
                r"what can you do",
                r"how do i",
                r"commands",
            ],
            Intent.CONFIRM: [
                r"^yes$",
                r"^ok$",
                r"^confirm$",
                r"^do it$",
                r"^proceed$",
                r"^go ahead$",
                r"^sure$",
                r"^yep$",
                r"^yeah$",
            ],
            Intent.CANCEL: [
                r"^no$",
                r"^cancel$",
                r"^stop$",
                r"^abort$",
                r"^nevermind$",
                r"^never mind$",
                r"^nope$",
            ],
        }

        self._compiled: dict[Intent, list[re.Pattern[str]]] = {
            intent: [re.compile(p, re.IGNORECASE) for p in patterns]
            for intent, patterns in self._patterns.items()
        }

    def classify(
        self,
        text: str,
        context: ConversationContext | None = None,
    ) -> ParsedIntent:
        """Classify user intent from text."""
        text_lower = text.lower().strip()

        for intent, patterns in self._compiled.items():
            for pattern in patterns:
                if pattern.search(text_lower):
                    entities = self._extract_entities(text)

                    is_follow_up = self._is_follow_up(text, context)

                    return ParsedIntent(
                        intent=intent,
                        confidence=0.9,
                        entities=entities,
                        raw_text=text,
                        is_follow_up=is_follow_up,
                    )

        entities = self._extract_entities(text)

        is_follow_up = self._is_follow_up(text, context)
        if is_follow_up:
            return ParsedIntent(
                intent=Intent.FOLLOW_UP,
                confidence=0.7,
                entities=entities,
                raw_text=text,
                is_follow_up=True,
            )

        return ParsedIntent(
            intent=Intent.UNKNOWN,
            confidence=0.3,
            entities=entities,
            raw_text=text,
            is_follow_up=False,
        )

    def _is_follow_up(
        self,
        text: str,
        context: ConversationContext | None,
    ) -> bool:
        """Check if this is a follow-up to a previous message."""
        if not context or not context.history:
            return False

        follow_up_indicators = [
            "it", "that", "this", "the same", "those",
            "what about", "how about", "and", "also",
        ]

        text_lower = text.lower()

        for indicator in follow_up_indicators:
            if text_lower.startswith(indicator) or f" {indicator} " in text_lower:
                return True

        return False

    def _extract_entities(self, text: str) -> list[Entity]:
        """Extract entities from text."""
        entities: list[Entity] = []

        service_patterns = [
            r"([\w-]+-(?:api|service|svc|app|worker))",
            r"(?:service |the )(\w[\w-]*)",
        ]

        for pattern in service_patterns:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                entities.append(Entity(
                    entity_type=EntityType.SERVICE,
                    value=match.group(1),
                    start=match.start(1),
                    end=match.end(1),
                ))

        deploy_match = re.search(r"deploy[ment]?\s*[#]?(\w+[-.]?\w*)", text, re.IGNORECASE)
        if deploy_match:
            entities.append(Entity(
                entity_type=EntityType.DEPLOY,
                value=deploy_match.group(1),
                start=deploy_match.start(1),
                end=deploy_match.end(1),
            ))

        incident_match = re.search(r"INC-(\d+)", text, re.IGNORECASE)
        if incident_match:
            entities.append(Entity(
                entity_type=EntityType.INCIDENT,
                value=f"INC-{incident_match.group(1)}",
                start=incident_match.start(),
                end=incident_match.end(),
            ))

        user_matches = re.findall(r"<@(\w+)>", text)
        for user_id in user_matches:
            entities.append(Entity(
                entity_type=EntityType.USER,
                value=user_id,
            ))

        at_matches = re.findall(r"@(\w+)", text)
        for username in at_matches:
            if not any(e.value == username for e in entities):
                entities.append(Entity(
                    entity_type=EntityType.USER,
                    value=username,
                ))

        version_match = re.search(r"v?(\d+\.\d+\.?\d*)", text)
        if version_match:
            entities.append(Entity(
                entity_type=EntityType.VERSION,
                value=version_match.group(0),
                start=version_match.start(),
                end=version_match.end(),
            ))

        number_match = re.search(r"(\d+)\s*(replicas?|pods?|instances?)?", text, re.IGNORECASE)
        if number_match:
            entities.append(Entity(
                entity_type=EntityType.NUMBER,
                value=number_match.group(1),
                start=number_match.start(1),
                end=number_match.end(1),
            ))

        time_patterns = [
            (r"(\d+)\s*(m(?:in(?:ute)?s?)?)", "minutes"),
            (r"(\d+)\s*(h(?:(?:ou)?rs?)?)", "hours"),
            (r"(\d+)\s*(d(?:ays?)?)", "days"),
            (r"last\s+(\d+)\s+(\w+)", "relative"),
        ]

        for pattern, _ in time_patterns:
            time_match = re.search(pattern, text, re.IGNORECASE)
            if time_match:
                entities.append(Entity(
                    entity_type=EntityType.TIME,
                    value=time_match.group(0),
                    start=time_match.start(),
                    end=time_match.end(),
                ))
                break

        return entities


class ConversationManager:
    """Manages multi-turn conversations."""

    def __init__(self) -> None:
        self._contexts: dict[str, ConversationContext] = {}
        self._classifier = IntentClassifier()

    def get_context(
        self,
        channel_id: str,
        user_id: str,
        thread_ts: str | None = None,
    ) -> ConversationContext:
        """Get or create conversation context."""
        key = f"{channel_id}:{thread_ts or 'main'}:{user_id}"

        if key in self._contexts:
            context = self._contexts[key]
            if not context.is_stale:
                return context

        context = ConversationContext(
            conversation_id=key,
            channel_id=channel_id,
            user_id=user_id,
            thread_ts=thread_ts,
        )

        self._contexts[key] = context
        return context

    async def process_message(
        self,
        user_id: str,
        channel_id: str,
        text: str,
        context: list[Any] | None = None,
        thread_ts: str | None = None,
    ) -> Any:
        """Process a user message and generate response."""
        conv_context = self.get_context(channel_id, user_id, thread_ts)

        parsed = self._classifier.classify(text, conv_context)

        conv_context.add_turn("user", text, parsed.intent, parsed.entities)

        if parsed.is_follow_up:
            parsed = self._resolve_follow_up(parsed, conv_context)

        response = await self._generate_response(parsed, conv_context)

        conv_context.add_turn("assistant", response.text)

        return response

    def _resolve_follow_up(
        self,
        parsed: ParsedIntent,
        context: ConversationContext,
    ) -> ParsedIntent:
        """Resolve references in follow-up messages."""
        if context.last_intent and parsed.intent == Intent.FOLLOW_UP:
            parsed.intent = context.last_intent

        if context.current_service:
            has_service = any(
                e.entity_type == EntityType.SERVICE for e in parsed.entities
            )
            if not has_service:
                parsed.entities.append(Entity(
                    entity_type=EntityType.SERVICE,
                    value=context.current_service,
                    confidence=0.8,
                ))

        if context.current_incident:
            has_incident = any(
                e.entity_type == EntityType.INCIDENT for e in parsed.entities
            )
            if not has_incident:
                parsed.entities.append(Entity(
                    entity_type=EntityType.INCIDENT,
                    value=context.current_incident,
                    confidence=0.8,
                ))

        return parsed

    async def _generate_response(
        self,
        parsed: ParsedIntent,
        context: ConversationContext,
    ) -> Any:
        """Generate response based on parsed intent."""
        from app.slack_bot.core import BotResponse
        from app.slack_bot.blocks import get_block_builder

        builder = get_block_builder()

        if parsed.intent == Intent.INVESTIGATE:
            service_entity = parsed.get_entity(EntityType.SERVICE)
            service = service_entity.value if service_entity else "checkout-api"

            context.current_service = service

            blocks = builder.investigation_result(
                incident_id=context.current_incident or "INC-1234",
                root_cause=f"Connection leak identified in {service}",
                confidence=0.87,
                evidence=[
                    "Error rate spike at 14:02 UTC",
                    "Deploy billing-v2.3.1 at 13:58 UTC",
                    "Connection pool exhausted logs",
                ],
                code_snippet=(
                    "def process_payment(self, order):\n"
                    "    conn = self.pool.acquire()\n"
                    "    if not result.success:\n"
                    "        return None  # LEAK: connection not released"
                ),
                file_path="billing/service.py",
                line_number=142,
            )

            return BotResponse(
                text=f"Investigated {service}. Root cause identified.",
                blocks=blocks,
                thread_ts=context.thread_ts,
            )

        elif parsed.intent == Intent.ROLLBACK:
            deploy_entity = parsed.get_entity(EntityType.DEPLOY)
            service_entity = parsed.get_entity(EntityType.SERVICE)

            deploy = deploy_entity.value if deploy_entity else "billing-v2.3.1"
            service = service_entity.value if service_entity else context.current_service or "billing-service"

            context.pending_action = "rollback"
            context.pending_details = {
                "deploy": deploy,
                "service": service,
            }

            blocks = builder.approval_card(
                approval_id=f"rollback_{datetime.now(timezone.utc).timestamp()}",
                action_type="Rollback Deployment",
                description=f"Rollback {service} deploy {deploy}",
                details={
                    "service": service,
                    "deploy": deploy,
                    "affected_services": ["checkout-api", "payment-service"],
                },
                risk_level="medium",
                requested_by=context.user_id,
            )

            return BotResponse(
                text=f"Confirm rollback of {deploy}?",
                blocks=blocks,
                thread_ts=context.thread_ts,
            )

        elif parsed.intent == Intent.CONFIRM:
            if context.pending_action == "rollback":
                details = context.pending_details

                context.pending_action = None
                context.pending_details = {}

                blocks = builder.rollback_complete(
                    service=details.get("service", "billing-service"),
                    from_version=details.get("deploy", "v2.3.1"),
                    to_version="v2.3.0",
                    verification_results={
                        "latency": {"before": "5.2s", "after": "180ms", "ok": "true"},
                        "error_rate": {"before": "23%", "after": "0.1%", "ok": "true"},
                        "connections": {"before": "100/100", "after": "12/100", "ok": "true"},
                    },
                )

                return BotResponse(
                    text="Rollback complete. Service verified healthy.",
                    blocks=blocks,
                    thread_ts=context.thread_ts,
                )

            return BotResponse(
                text="Nothing to confirm. What would you like to do?",
                thread_ts=context.thread_ts,
            )

        elif parsed.intent == Intent.CANCEL:
            if context.pending_action:
                action = context.pending_action
                context.pending_action = None
                context.pending_details = {}

                return BotResponse(
                    text=f"Cancelled {action}.",
                    thread_ts=context.thread_ts,
                )

            return BotResponse(
                text="Nothing to cancel.",
                thread_ts=context.thread_ts,
            )

        elif parsed.intent == Intent.STATUS:
            blocks = builder.status_card(
                incident_id=context.current_incident or "INC-1234",
                status="Investigating",
                duration="12m 34s",
                responders=["alice", "bob"],
                services=[context.current_service or "checkout-api", "billing-service"],
                root_cause="Connection leak in billing-service v2.3.1",
                confidence=0.87,
            )

            return BotResponse(
                text="Current status",
                blocks=blocks,
                thread_ts=context.thread_ts,
            )

        elif parsed.intent == Intent.EXPLAIN:
            return BotResponse(
                text=(
                    "This error means all database connections are in use. "
                    "847 requests are waiting for a connection.\n\n"
                    "Common causes:\n"
                    "1. Connection leak (acquired but not released)\n"
                    "2. Long-running queries blocking connections\n"
                    "3. Traffic spike exceeding pool capacity\n\n"
                    "Given the timing correlates with deploy billing-v2.3.1, "
                    "I suspect a connection leak. Want me to analyze the deploy diff?"
                ),
                thread_ts=context.thread_ts,
            )

        elif parsed.intent == Intent.COMPARE:
            blocks = builder.similar_incidents([
                {
                    "id": "INC-892",
                    "title": "Billing service connection pool exhausted",
                    "similarity": 0.94,
                    "resolution": "Fixed connection leak in payment processor",
                },
                {
                    "id": "INC-756",
                    "title": "Database connection timeout errors",
                    "similarity": 0.78,
                    "resolution": "Increased pool size, added timeout",
                },
            ])

            return BotResponse(
                text="Found similar past incidents",
                blocks=blocks,
                thread_ts=context.thread_ts,
            )

        elif parsed.intent == Intent.FIX:
            blocks = builder.approval_card(
                approval_id=f"fix_{datetime.now(timezone.utc).timestamp()}",
                action_type="Create Fix PR",
                description="Fix connection leak with context manager",
                details={
                    "repository": "company/billing-service",
                    "branch": f"fix/connection-leak-{context.current_incident or 'inc-1234'}",
                    "changes": "+3 lines, -5 lines",
                },
                risk_level="low",
                requested_by=context.user_id,
            )

            return BotResponse(
                text="Generated fix PR. Review and approve:",
                blocks=blocks,
                thread_ts=context.thread_ts,
            )

        elif parsed.intent == Intent.PAGE:
            user_entity = parsed.get_entity(EntityType.USER)
            team_entity = parsed.get_entity(EntityType.TEAM)

            target = user_entity.value if user_entity else (
                team_entity.value if team_entity else "database-team"
            )

            return BotResponse(
                text=f"Paging {target}... They will be notified via PagerDuty.",
                thread_ts=context.thread_ts,
            )

        elif parsed.intent == Intent.HELP:
            return BotResponse(
                text=(
                    "*I can help you with:*\n\n"
                    "*Investigation:*\n"
                    "- \"what caused this?\" - Investigate root cause\n"
                    "- \"is payment-service affected?\" - Check service\n\n"
                    "*Actions:*\n"
                    "- \"rollback deploy 47\" - Rollback deployment\n"
                    "- \"scale checkout-api to 10\" - Scale service\n"
                    "- \"page @alice\" - Page someone\n\n"
                    "*Info:*\n"
                    "- \"status\" - Current status\n"
                    "- \"similar incidents\" - Find patterns\n"
                    "- \"explain this error\" - Get explanation\n\n"
                    "Just talk to me naturally!"
                ),
                thread_ts=context.thread_ts,
            )

        return BotResponse(
            text=(
                "I'm not sure what you're asking. Try:\n"
                "- \"investigate checkout-api\"\n"
                "- \"what's the status?\"\n"
                "- \"rollback billing-service\"\n"
                "- \"help\" for more options"
            ),
            thread_ts=context.thread_ts,
        )

    def clear_context(
        self,
        channel_id: str,
        user_id: str,
        thread_ts: str | None = None,
    ) -> None:
        """Clear conversation context."""
        key = f"{channel_id}:{thread_ts or 'main'}:{user_id}"

        if key in self._contexts:
            del self._contexts[key]

    def cleanup_stale_contexts(self) -> int:
        """Clean up stale conversation contexts."""
        stale_keys = [
            key for key, ctx in self._contexts.items()
            if ctx.is_stale
        ]

        for key in stale_keys:
            del self._contexts[key]

        return len(stale_keys)


_manager: ConversationManager | None = None


def get_conversation_manager() -> ConversationManager:
    """Get the default conversation manager."""
    global _manager
    if _manager is None:
        _manager = ConversationManager()
    return _manager
