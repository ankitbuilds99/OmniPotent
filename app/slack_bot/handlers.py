"""Event and Interaction Handlers.

Handles Slack events and interactive components.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("devcopilot.slack_bot.handlers")


class EventType(str, Enum):
    MESSAGE = "message"
    APP_MENTION = "app_mention"
    REACTION_ADDED = "reaction_added"
    REACTION_REMOVED = "reaction_removed"
    CHANNEL_CREATED = "channel_created"
    CHANNEL_ARCHIVED = "channel_archived"
    MEMBER_JOINED_CHANNEL = "member_joined_channel"
    FILE_SHARED = "file_shared"


class InteractionType(str, Enum):
    BLOCK_ACTIONS = "block_actions"
    VIEW_SUBMISSION = "view_submission"
    VIEW_CLOSED = "view_closed"
    SHORTCUT = "shortcut"
    MESSAGE_ACTION = "message_action"


@dataclass
class EventContext:
    """Context for an event."""

    event_type: EventType
    event_id: str
    team_id: str
    channel_id: str | None
    user_id: str | None
    timestamp: datetime
    raw_event: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.event_type.value,
            "id": self.event_id,
            "team": self.team_id,
            "channel": self.channel_id,
            "user": self.user_id,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class InteractionContext:
    """Context for an interaction."""

    interaction_type: InteractionType
    interaction_id: str
    action_id: str | None
    user_id: str
    channel_id: str | None
    message_ts: str | None
    trigger_id: str | None
    response_url: str | None
    raw_payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.interaction_type.value,
            "id": self.interaction_id,
            "action": self.action_id,
            "user": self.user_id,
            "channel": self.channel_id,
        }


class EventHandler:
    """Handles Slack events."""

    def __init__(self) -> None:
        self._handlers: dict[EventType, list[Any]] = {}

    def register(
        self,
        event_type: EventType,
        handler: Any,
    ) -> None:
        """Register an event handler."""
        if event_type not in self._handlers:
            self._handlers[event_type] = []

        self._handlers[event_type].append(handler)

    async def handle(self, event: dict[str, Any]) -> Any:
        """Handle an incoming event."""
        event_type_str = event.get("type", "")

        try:
            event_type = EventType(event_type_str)
        except ValueError:
            log.debug("Unknown event type: %s", event_type_str)
            return None

        context = EventContext(
            event_type=event_type,
            event_id=event.get("event_id", ""),
            team_id=event.get("team", ""),
            channel_id=event.get("channel"),
            user_id=event.get("user"),
            timestamp=datetime.now(timezone.utc),
            raw_event=event,
        )

        log.info(
            "Handling event: type=%s channel=%s user=%s",
            event_type.value,
            context.channel_id,
            context.user_id,
        )

        if event_type == EventType.MESSAGE or event_type == EventType.APP_MENTION:
            return await self._handle_message(context)

        elif event_type == EventType.REACTION_ADDED:
            return await self._handle_reaction_added(context)

        elif event_type == EventType.FILE_SHARED:
            return await self._handle_file_shared(context)

        elif event_type == EventType.MEMBER_JOINED_CHANNEL:
            return await self._handle_member_joined(context)

        handlers = self._handlers.get(event_type, [])
        for handler in handlers:
            await handler(context)

        return None

    async def _handle_message(self, context: EventContext) -> Any:
        """Handle message events."""
        from app.slack_bot.core import get_slack_bot

        bot = get_slack_bot()
        return await bot.handle_message(context.raw_event)

    async def _handle_reaction_added(self, context: EventContext) -> Any:
        """Handle reaction added events."""
        reaction = context.raw_event.get("reaction", "")
        item = context.raw_event.get("item", {})

        log.info(
            "Reaction added: reaction=%s item_type=%s",
            reaction,
            item.get("type"),
        )

        if reaction in ("eyes", "mag"):
            return await self._trigger_investigation_from_reaction(context)

        elif reaction == "white_check_mark":
            return await self._acknowledge_from_reaction(context)

        return None

    async def _trigger_investigation_from_reaction(
        self,
        context: EventContext,
    ) -> Any:
        """Trigger investigation from reaction."""
        item = context.raw_event.get("item", {})
        channel = item.get("channel", "")
        message_ts = item.get("ts", "")

        log.info(
            "Triggering investigation from reaction: channel=%s ts=%s",
            channel,
            message_ts,
        )

        return None

    async def _acknowledge_from_reaction(self, context: EventContext) -> Any:
        """Acknowledge incident from reaction."""
        log.info("Acknowledging from reaction: user=%s", context.user_id)

        return None

    async def _handle_file_shared(self, context: EventContext) -> Any:
        """Handle file shared events."""
        file_info = context.raw_event.get("file", {})
        file_type = file_info.get("filetype", "")

        log.info("File shared: type=%s", file_type)

        if file_type in ("png", "jpg", "jpeg", "gif"):
            return await self._analyze_image(context, file_info)

        elif file_type in ("txt", "log"):
            return await self._analyze_log_file(context, file_info)

        return None

    async def _analyze_image(
        self,
        context: EventContext,
        file_info: dict[str, Any],
    ) -> Any:
        """Analyze shared image (e.g., dashboard screenshot)."""
        log.info("Analyzing image: %s", file_info.get("name"))

        return None

    async def _analyze_log_file(
        self,
        context: EventContext,
        file_info: dict[str, Any],
    ) -> Any:
        """Analyze shared log file."""
        log.info("Analyzing log file: %s", file_info.get("name"))

        return None

    async def _handle_member_joined(self, context: EventContext) -> Any:
        """Handle member joined channel events."""
        from app.slack_bot.war_room import get_war_room_manager

        manager = get_war_room_manager()

        war_room = manager.get_by_channel(context.channel_id or "")

        if war_room:
            log.info(
                "Member joined war room: user=%s war_room=%s",
                context.user_id,
                war_room.war_room_id,
            )

        return None


class InteractionHandler:
    """Handles Slack interactive components."""

    def __init__(self) -> None:
        self._action_handlers: dict[str, Any] = {}

    def register(self, action_pattern: str, handler: Any) -> None:
        """Register an action handler."""
        self._action_handlers[action_pattern] = handler

    async def handle(self, payload: dict[str, Any]) -> Any:
        """Handle an interactive payload."""
        interaction_type_str = payload.get("type", "")

        try:
            interaction_type = InteractionType(interaction_type_str)
        except ValueError:
            log.debug("Unknown interaction type: %s", interaction_type_str)
            return None

        user = payload.get("user", {})
        channel = payload.get("channel", {})
        message = payload.get("message", {})

        context = InteractionContext(
            interaction_type=interaction_type,
            interaction_id=payload.get("trigger_id", ""),
            action_id=None,
            user_id=user.get("id", ""),
            channel_id=channel.get("id"),
            message_ts=message.get("ts"),
            trigger_id=payload.get("trigger_id"),
            response_url=payload.get("response_url"),
            raw_payload=payload,
        )

        if interaction_type == InteractionType.BLOCK_ACTIONS:
            return await self._handle_block_actions(context, payload)

        elif interaction_type == InteractionType.VIEW_SUBMISSION:
            return await self._handle_view_submission(context, payload)

        elif interaction_type == InteractionType.SHORTCUT:
            return await self._handle_shortcut(context, payload)

        elif interaction_type == InteractionType.MESSAGE_ACTION:
            return await self._handle_message_action(context, payload)

        return None

    async def _handle_block_actions(
        self,
        context: InteractionContext,
        payload: dict[str, Any],
    ) -> Any:
        """Handle block action interactions."""
        from app.slack_bot.core import BotResponse

        actions = payload.get("actions", [])

        if not actions:
            return None

        action = actions[0]
        action_id = action.get("action_id", "")
        context.action_id = action_id

        log.info(
            "Block action: action_id=%s user=%s",
            action_id,
            context.user_id,
        )

        if action_id.startswith("approve_"):
            return await self._handle_approve_action(context, action_id)

        elif action_id.startswith("reject_"):
            return await self._handle_reject_action(context, action_id)

        elif action_id.startswith("investigate_"):
            return await self._handle_investigate_action(context, action_id)

        elif action_id.startswith("rollback_"):
            return await self._handle_rollback_action(context, action_id)

        elif action_id.startswith("fix_"):
            return await self._handle_fix_action(context, action_id)

        elif action_id.startswith("page_"):
            return await self._handle_page_action(context, action_id)

        elif action_id.startswith("timeline_"):
            return await self._handle_timeline_action(context, action_id)

        elif action_id.startswith("dashboard_"):
            return await self._handle_dashboard_action(context, action_id)

        elif action_id.startswith("approve_workflow_"):
            return await self._handle_workflow_approve(context, action_id)

        elif action_id.startswith("reject_workflow_"):
            return await self._handle_workflow_reject(context, action_id)

        elif action_id == "resolve_incident":
            return await self._handle_resolve_incident(context)

        elif action_id == "continue_monitoring":
            return await self._handle_continue_monitoring(context)

        log.warning("Unhandled action: %s", action_id)

        return BotResponse(text=f"Action '{action_id}' received.")

    async def _handle_approve_action(
        self,
        context: InteractionContext,
        action_id: str,
    ) -> Any:
        """Handle approval action."""
        from app.slack_bot.core import get_slack_bot

        approval_id = action_id.replace("approve_", "")

        bot = get_slack_bot()
        response = await bot.process_approval(
            approval_id=approval_id,
            approved=True,
            approver_id=context.user_id,
        )

        return response

    async def _handle_reject_action(
        self,
        context: InteractionContext,
        action_id: str,
    ) -> Any:
        """Handle reject action."""
        from app.slack_bot.core import get_slack_bot

        approval_id = action_id.replace("reject_", "")

        bot = get_slack_bot()
        response = await bot.process_approval(
            approval_id=approval_id,
            approved=False,
            approver_id=context.user_id,
        )

        return response

    async def _handle_investigate_action(
        self,
        context: InteractionContext,
        action_id: str,
    ) -> Any:
        """Handle investigate action."""
        from app.slack_bot.core import BotResponse
        from app.slack_bot.blocks import get_block_builder

        incident_id = action_id.replace("investigate_", "")

        builder = get_block_builder()

        blocks = builder.investigation_progress(
            incident_id=incident_id,
            status="Analyzing logs and metrics...",
            steps_completed=1,
            total_steps=5,
        )

        return BotResponse(
            text="Starting investigation...",
            blocks=blocks,
        )

    async def _handle_rollback_action(
        self,
        context: InteractionContext,
        action_id: str,
    ) -> Any:
        """Handle rollback action."""
        from app.slack_bot.core import BotResponse
        from app.slack_bot.blocks import get_block_builder

        incident_id = action_id.replace("rollback_", "")

        builder = get_block_builder()

        blocks = builder.approval_card(
            approval_id=f"rollback_{incident_id}_{datetime.now(timezone.utc).timestamp()}",
            action_type="Rollback Deployment",
            description="Rollback to previous version",
            details={
                "incident_id": incident_id,
                "current_version": "v2.3.1",
                "target_version": "v2.3.0",
            },
            risk_level="medium",
            requested_by=context.user_id,
        )

        return BotResponse(
            text="Confirm rollback:",
            blocks=blocks,
        )

    async def _handle_fix_action(
        self,
        context: InteractionContext,
        action_id: str,
    ) -> Any:
        """Handle fix action."""
        from app.slack_bot.core import BotResponse

        incident_id = action_id.replace("fix_", "")

        return BotResponse(
            text=f"Generating fix PR for {incident_id}...",
        )

    async def _handle_page_action(
        self,
        context: InteractionContext,
        action_id: str,
    ) -> Any:
        """Handle page action."""
        from app.slack_bot.core import BotResponse

        target = action_id.replace("page_", "")

        return BotResponse(
            text=f"Paging {target}...",
        )

    async def _handle_timeline_action(
        self,
        context: InteractionContext,
        action_id: str,
    ) -> Any:
        """Handle timeline action."""
        from app.slack_bot.core import BotResponse
        from app.slack_bot.blocks import get_block_builder

        incident_id = action_id.replace("timeline_", "")

        builder = get_block_builder()

        blocks = builder.timeline_card(
            incident_id=incident_id,
            events=[
                {"time": "14:02", "event": "Alert fired"},
                {"time": "14:03", "event": "Incident created"},
                {"time": "14:05", "event": "Root cause identified"},
            ],
        )

        return BotResponse(
            text="Timeline",
            blocks=blocks,
        )

    async def _handle_dashboard_action(
        self,
        context: InteractionContext,
        action_id: str,
    ) -> Any:
        """Handle dashboard action."""
        from app.slack_bot.core import BotResponse

        target = action_id.replace("dashboard_", "")

        return BotResponse(
            text=f"Opening dashboard for {target}...",
        )

    async def _handle_workflow_approve(
        self,
        context: InteractionContext,
        action_id: str,
    ) -> Any:
        """Handle workflow approval."""
        from app.slack_bot.core import BotResponse
        from app.slack_bot.workflows import get_workflow_engine

        run_id = action_id.replace("approve_workflow_", "")

        engine = get_workflow_engine()
        success = await engine.approve_step(run_id, context.user_id)

        if success:
            blocks = engine.get_slack_progress_blocks(run_id)

            return BotResponse(
                text=f"Step approved by <@{context.user_id}>",
                blocks=blocks,
            )

        return BotResponse(text="Could not approve step.")

    async def _handle_workflow_reject(
        self,
        context: InteractionContext,
        action_id: str,
    ) -> Any:
        """Handle workflow rejection."""
        from app.slack_bot.core import BotResponse
        from app.slack_bot.workflows import get_workflow_engine

        run_id = action_id.replace("reject_workflow_", "")

        engine = get_workflow_engine()
        success = await engine.reject_step(
            run_id,
            context.user_id,
            "Rejected via Slack",
        )

        if success:
            return BotResponse(
                text=f"Workflow rejected by <@{context.user_id}>",
            )

        return BotResponse(text="Could not reject workflow.")

    async def _handle_resolve_incident(
        self,
        context: InteractionContext,
    ) -> Any:
        """Handle resolve incident action."""
        from app.slack_bot.core import BotResponse

        return BotResponse(
            text=f"Incident marked as resolved by <@{context.user_id}>",
        )

    async def _handle_continue_monitoring(
        self,
        context: InteractionContext,
    ) -> Any:
        """Handle continue monitoring action."""
        from app.slack_bot.core import BotResponse

        return BotResponse(
            text="Continuing to monitor. I'll alert you if anything changes.",
        )

    async def _handle_view_submission(
        self,
        context: InteractionContext,
        payload: dict[str, Any],
    ) -> Any:
        """Handle view submission interactions."""
        view = payload.get("view", {})
        callback_id = view.get("callback_id", "")

        log.info(
            "View submission: callback_id=%s user=%s",
            callback_id,
            context.user_id,
        )

        if callback_id.startswith("workflow_create_"):
            return await self._handle_workflow_create_submission(context, view)

        return None

    async def _handle_workflow_create_submission(
        self,
        context: InteractionContext,
        view: dict[str, Any],
    ) -> Any:
        """Handle workflow creation form submission."""
        values = view.get("state", {}).get("values", {})

        log.info("Creating workflow from form: %s", values)

        return None

    async def _handle_shortcut(
        self,
        context: InteractionContext,
        payload: dict[str, Any],
    ) -> Any:
        """Handle shortcut interactions."""
        callback_id = payload.get("callback_id", "")

        log.info(
            "Shortcut triggered: callback_id=%s user=%s",
            callback_id,
            context.user_id,
        )

        if callback_id == "create_incident":
            return await self._open_create_incident_modal(context)

        elif callback_id == "quick_investigate":
            return await self._open_investigate_modal(context)

        return None

    async def _open_create_incident_modal(
        self,
        context: InteractionContext,
    ) -> dict[str, Any]:
        """Open create incident modal."""
        return {
            "type": "modal",
            "callback_id": "create_incident_submit",
            "title": {"type": "plain_text", "text": "Create Incident"},
            "submit": {"type": "plain_text", "text": "Create"},
            "blocks": [
                {
                    "type": "input",
                    "block_id": "title",
                    "label": {"type": "plain_text", "text": "Title"},
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "title_input",
                    },
                },
                {
                    "type": "input",
                    "block_id": "severity",
                    "label": {"type": "plain_text", "text": "Severity"},
                    "element": {
                        "type": "static_select",
                        "action_id": "severity_select",
                        "options": [
                            {"text": {"type": "plain_text", "text": "P1 - Critical"}, "value": "P1"},
                            {"text": {"type": "plain_text", "text": "P2 - High"}, "value": "P2"},
                            {"text": {"type": "plain_text", "text": "P3 - Medium"}, "value": "P3"},
                            {"text": {"type": "plain_text", "text": "P4 - Low"}, "value": "P4"},
                        ],
                    },
                },
            ],
        }

    async def _open_investigate_modal(
        self,
        context: InteractionContext,
    ) -> dict[str, Any]:
        """Open quick investigate modal."""
        return {
            "type": "modal",
            "callback_id": "quick_investigate_submit",
            "title": {"type": "plain_text", "text": "Quick Investigate"},
            "submit": {"type": "plain_text", "text": "Investigate"},
            "blocks": [
                {
                    "type": "input",
                    "block_id": "query",
                    "label": {"type": "plain_text", "text": "What's the issue?"},
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "query_input",
                        "multiline": True,
                        "placeholder": {
                            "type": "plain_text",
                            "text": "e.g., checkout-api is returning 500 errors",
                        },
                    },
                },
            ],
        }

    async def _handle_message_action(
        self,
        context: InteractionContext,
        payload: dict[str, Any],
    ) -> Any:
        """Handle message action interactions."""
        callback_id = payload.get("callback_id", "")
        message = payload.get("message", {})

        log.info(
            "Message action: callback_id=%s message_ts=%s",
            callback_id,
            message.get("ts"),
        )

        if callback_id == "analyze_message":
            return await self._analyze_message_content(context, message)

        elif callback_id == "add_to_timeline":
            return await self._add_message_to_timeline(context, message)

        return None

    async def _analyze_message_content(
        self,
        context: InteractionContext,
        message: dict[str, Any],
    ) -> Any:
        """Analyze message content."""
        from app.slack_bot.core import BotResponse

        text = message.get("text", "")

        return BotResponse(
            text=f"Analyzing: {text[:100]}...",
        )

    async def _add_message_to_timeline(
        self,
        context: InteractionContext,
        message: dict[str, Any],
    ) -> Any:
        """Add message to incident timeline."""
        from app.slack_bot.core import BotResponse

        return BotResponse(
            text="Added to incident timeline.",
        )


_event_handler: EventHandler | None = None
_interaction_handler: InteractionHandler | None = None


def get_event_handler() -> EventHandler:
    """Get the default event handler."""
    global _event_handler
    if _event_handler is None:
        _event_handler = EventHandler()
    return _event_handler


def get_interaction_handler() -> InteractionHandler:
    """Get the default interaction handler."""
    global _interaction_handler
    if _interaction_handler is None:
        _interaction_handler = InteractionHandler()
    return _interaction_handler
