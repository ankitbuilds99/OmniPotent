"""Core Slack Bot service.

Main entry point for all Slack interactions.
Uses the enterprise-grade SlackClient for real API calls.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from app.config import get_settings
from app.slack_bot.client import SlackClient, SlackClientConfig

log = logging.getLogger("omnipotent.slack_bot.core")

# Every messaging method used to fall back to a fabricated success payload when
# SLACK_BOT_TOKEN was unset: `send_message` minted an `ok: True` with a `ts`
# from the wall clock, `create_channel` invented a channel id, and the war-room
# and approval flows built on top of them reported messages as posted that were
# never sent anywhere. Unless the operator opted into demo mode, an
# unconfigured bot now reports the failure instead of the outcome it would
# have liked to produce.
_NO_SLACK = (
    "Slack client not configured (set SLACK_BOT_TOKEN); refusing to report "
    "success for a message that was never sent"
)


class BotStatus(str, Enum):
    INITIALIZING = "initializing"
    READY = "ready"
    PROCESSING = "processing"
    ERROR = "error"
    DISCONNECTED = "disconnected"


class MessageType(str, Enum):
    DIRECT = "direct"
    MENTION = "mention"
    CHANNEL = "channel"
    THREAD = "thread"
    COMMAND = "command"
    INTERACTION = "interaction"


@dataclass
class SlackBotConfig:
    """Configuration for the Slack bot."""

    bot_token: str
    app_token: str
    signing_secret: str
    bot_user_id: str = ""
    default_channel: str = ""
    incident_channel_prefix: str = "inc-"
    auto_create_war_rooms: bool = True
    auto_archive_days: int = 7
    max_context_messages: int = 50
    enable_proactive_alerts: bool = True
    enable_predictive_warnings: bool = True
    require_approval_for_actions: bool = True
    allowed_action_roles: list[str] = field(default_factory=lambda: ["operator", "admin"])

    def to_dict(self) -> dict[str, Any]:
        return {
            "bot_user_id": self.bot_user_id,
            "default_channel": self.default_channel,
            "incident_channel_prefix": self.incident_channel_prefix,
            "auto_create_war_rooms": self.auto_create_war_rooms,
            "auto_archive_days": self.auto_archive_days,
            "max_context_messages": self.max_context_messages,
            "enable_proactive_alerts": self.enable_proactive_alerts,
            "enable_predictive_warnings": self.enable_predictive_warnings,
            "require_approval_for_actions": self.require_approval_for_actions,
        }


@dataclass
class SlackMessage:
    """A Slack message."""

    message_id: str
    channel_id: str
    user_id: str
    text: str
    thread_ts: str | None = None
    message_type: MessageType = MessageType.CHANNEL
    blocks: list[dict[str, Any]] = field(default_factory=list)
    attachments: list[dict[str, Any]] = field(default_factory=list)
    files: list[dict[str, Any]] = field(default_factory=list)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    raw_event: dict[str, Any] = field(default_factory=dict)

    @property
    def is_thread_reply(self) -> bool:
        return self.thread_ts is not None

    @property
    def has_files(self) -> bool:
        return len(self.files) > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.message_id,
            "channel": self.channel_id,
            "user": self.user_id,
            "text": self.text,
            "thread_ts": self.thread_ts,
            "type": self.message_type.value,
            "has_blocks": len(self.blocks) > 0,
            "has_files": self.has_files,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class BotResponse:
    """A response from the bot."""

    text: str
    blocks: list[dict[str, Any]] = field(default_factory=list)
    attachments: list[dict[str, Any]] = field(default_factory=list)
    thread_ts: str | None = None
    channel_id: str | None = None
    ephemeral: bool = False
    response_type: str = "in_channel"
    unfurl_links: bool = False
    unfurl_media: bool = True

    def to_slack_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"text": self.text}

        if self.blocks:
            payload["blocks"] = self.blocks
        if self.attachments:
            payload["attachments"] = self.attachments
        if self.thread_ts:
            payload["thread_ts"] = self.thread_ts
        if self.channel_id:
            payload["channel"] = self.channel_id

        payload["unfurl_links"] = self.unfurl_links
        payload["unfurl_media"] = self.unfurl_media

        if self.ephemeral:
            payload["response_type"] = "ephemeral"
        else:
            payload["response_type"] = self.response_type

        return payload


class SlackBot:
    """Main Slack bot service.

    Uses the enterprise-grade SlackClient for real API calls when
    SLACK_BOT_TOKEN is configured. Falls back to stub responses
    for offline development/testing.
    """

    def __init__(self, config: SlackBotConfig) -> None:
        self.config = config
        self.status = BotStatus.INITIALIZING
        self._message_history: dict[str, list[SlackMessage]] = {}
        self._active_investigations: dict[str, str] = {}
        self._pending_approvals: dict[str, dict[str, Any]] = {}
        self._user_cache: dict[str, dict[str, Any]] = {}
        self._channel_cache: dict[str, dict[str, Any]] = {}

        # Use real client if configured
        self._use_real_client = bool(config.bot_token and config.bot_token != "")
        self._client: SlackClient | None = None

        if self._use_real_client:
            client_config = SlackClientConfig(
                bot_token=config.bot_token,
                app_token=config.app_token,
                signing_secret=config.signing_secret,
            )
            self._client = SlackClient(client_config)

    def _no_client(
        self,
        operation: str,
        simulated: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """The result of ``operation`` when no Slack client is configured.

        In demo mode the simulated payload is returned, explicitly labelled so
        it can never be mistaken for a real Slack response (same convention as
        app/mcp/fixtures.mark_demo and the remediation handlers). Otherwise the
        call fails closed with a not-configured error.
        """
        if get_settings().copilot_demo_mode:
            log.warning(
                "COPILOT_DEMO_MODE=true - returning a SIMULATED %s result", operation
            )
            return {
                "ok": True,
                **(simulated or {}),
                "simulated": True,
                "notice": (
                    f"SIMULATED {operation} — no SLACK_BOT_TOKEN is configured; "
                    "nothing was sent to Slack. Do not present this as a real "
                    "Slack result."
                ),
            }

        log.warning("%s not performed: %s", operation, _NO_SLACK)
        return {
            "ok": False,
            "error": "slack_not_configured",
            "detail": f"{operation} not performed: {_NO_SLACK}",
        }

    async def start(self) -> None:
        """Start the Slack bot."""
        log.info("Starting Slack bot...")
        self.status = BotStatus.READY
        log.info("Slack bot ready")

    async def stop(self) -> None:
        """Stop the Slack bot."""
        log.info("Stopping Slack bot...")
        self.status = BotStatus.DISCONNECTED

    async def handle_message(self, event: dict[str, Any]) -> BotResponse | None:
        """Handle an incoming message event."""
        message = self._parse_message(event)

        if not message:
            return None

        if message.user_id == self.config.bot_user_id:
            return None

        self._store_message(message)

        if message.message_type == MessageType.MENTION:
            return await self._handle_mention(message)
        elif message.message_type == MessageType.DIRECT:
            return await self._handle_direct_message(message)

        return None

    async def handle_command(
        self,
        command: str,
        text: str,
        user_id: str,
        channel_id: str,
        response_url: str,
    ) -> BotResponse:
        """Handle a slash command."""
        log.info(
            "Command received: command=%s user=%s channel=%s",
            command,
            user_id,
            channel_id,
        )

        from app.slack_bot.commands import get_command_router

        router = get_command_router()
        result = await router.route(command, text, user_id, channel_id)

        return BotResponse(
            text=result.text,
            blocks=result.blocks,
            ephemeral=result.ephemeral,
        )

    async def handle_interaction(
        self,
        payload: dict[str, Any],
    ) -> BotResponse | None:
        """Handle an interactive component action."""
        action_type = payload.get("type")
        user_id = payload.get("user", {}).get("id", "")

        log.info("Interaction received: type=%s user=%s", action_type, user_id)

        from app.slack_bot.handlers import get_interaction_handler

        handler = get_interaction_handler()
        return await handler.handle(payload)

    async def send_message(
        self,
        channel_id: str,
        text: str,
        blocks: list[dict[str, Any]] | None = None,
        thread_ts: str | None = None,
    ) -> dict[str, Any]:
        """Send a message to a channel."""
        log.info("Sending message to channel=%s", channel_id)

        if self._client:
            response = await self._client.send_message(
                channel=channel_id,
                text=text,
                blocks=blocks,
                thread_ts=thread_ts,
            )
            return response.data

        return self._no_client(
            "send_message",
            {
                "channel": channel_id,
                "ts": f"{datetime.now(timezone.utc).timestamp()}",
                "message": {"text": text},
            },
        )

    async def send_ephemeral(
        self,
        channel_id: str,
        user_id: str,
        text: str,
        blocks: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Send an ephemeral message visible only to one user."""
        log.info("Sending ephemeral to user=%s in channel=%s", user_id, channel_id)

        if self._client:
            response = await self._client.send_ephemeral(
                channel=channel_id,
                user=user_id,
                text=text,
                blocks=blocks,
            )
            return response.data

        return self._no_client(
            "send_ephemeral",
            {"message_ts": f"{datetime.now(timezone.utc).timestamp()}"},
        )

    async def update_message(
        self,
        channel_id: str,
        message_ts: str,
        text: str,
        blocks: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Update an existing message."""
        log.info("Updating message ts=%s in channel=%s", message_ts, channel_id)

        if self._client:
            response = await self._client.update_message(
                channel=channel_id,
                ts=message_ts,
                text=text,
                blocks=blocks,
            )
            return response.data

        return self._no_client(
            "update_message",
            {"channel": channel_id, "ts": message_ts},
        )

    async def create_channel(
        self,
        name: str,
        is_private: bool = False,
    ) -> dict[str, Any]:
        """Create a new channel."""
        log.info("Creating channel: name=%s private=%s", name, is_private)

        if self._client:
            response = await self._client.create_channel(
                name=name,
                is_private=is_private,
            )
            return response.data

        return self._no_client(
            "create_channel",
            {
                "channel": {
                    "id": f"C{name.upper()[:8]}",
                    "name": name,
                    "is_private": is_private,
                },
            },
        )

    async def invite_to_channel(
        self,
        channel_id: str,
        user_ids: list[str],
    ) -> dict[str, Any]:
        """Invite users to a channel."""
        log.info("Inviting %d users to channel=%s", len(user_ids), channel_id)

        if self._client:
            response = await self._client.invite_to_channel(
                channel=channel_id,
                users=user_ids,
            )
            return response.data

        return self._no_client("invite_to_channel")

    async def set_channel_topic(
        self,
        channel_id: str,
        topic: str,
    ) -> dict[str, Any]:
        """Set channel topic."""
        log.info("Setting topic for channel=%s", channel_id)

        if self._client:
            response = await self._client.set_channel_topic(
                channel=channel_id,
                topic=topic,
            )
            return response.data

        return self._no_client("set_channel_topic", {"topic": topic})

    async def pin_message(
        self,
        channel_id: str,
        message_ts: str,
    ) -> dict[str, Any]:
        """Pin a message in a channel."""
        log.info("Pinning message ts=%s in channel=%s", message_ts, channel_id)

        if self._client:
            response = await self._client.pin_message(
                channel=channel_id,
                ts=message_ts,
            )
            return response.data

        return self._no_client("pin_message")

    async def archive_channel(
        self,
        channel_id: str,
    ) -> dict[str, Any]:
        """Archive a channel."""
        log.info("Archiving channel=%s", channel_id)

        if self._client:
            response = await self._client.archive_channel(channel=channel_id)
            return response.data

        return self._no_client("archive_channel")

    async def get_user_info(self, user_id: str) -> dict[str, Any]:
        """Get user information."""
        if user_id in self._user_cache:
            return self._user_cache[user_id]

        if self._client:
            response = await self._client.get_user_info(user=user_id)
            user_data = response.data.get("user", {})
            user_info = {
                "id": user_data.get("id", user_id),
                "name": user_data.get("name", ""),
                "real_name": user_data.get("real_name", ""),
                "email": user_data.get("profile", {}).get("email", ""),
                "is_admin": user_data.get("is_admin", False),
            }
            self._user_cache[user_id] = user_info
            return user_info

        # Offline fallback - return placeholder (logged as warning)
        log.warning("Using offline fallback for user %s - configure SLACK_BOT_TOKEN for real data", user_id)
        user_info = {
            "id": user_id,
            "name": f"user_{user_id}",
            "real_name": f"User {user_id}",
            "email": "",  # No fake email
            "is_admin": False,
        }

        self._user_cache[user_id] = user_info
        return user_info

    async def get_channel_info(self, channel_id: str) -> dict[str, Any]:
        """Get channel information."""
        if channel_id in self._channel_cache:
            return self._channel_cache[channel_id]

        if self._client:
            response = await self._client.get_channel_info(channel=channel_id)
            channel_data = response.data.get("channel", {})
            channel_info = {
                "id": channel_data.get("id", channel_id),
                "name": channel_data.get("name", ""),
                "is_private": channel_data.get("is_private", False),
                "topic": channel_data.get("topic", {"value": ""}),
            }
            self._channel_cache[channel_id] = channel_info
            return channel_info

        # Offline fallback
        log.warning("Using offline fallback for channel %s - configure SLACK_BOT_TOKEN for real data", channel_id)
        channel_info = {
            "id": channel_id,
            "name": "",  # No fake name
            "is_private": False,
            "topic": {"value": ""},
        }

        self._channel_cache[channel_id] = channel_info
        return channel_info

    def _parse_message(self, event: dict[str, Any]) -> SlackMessage | None:
        """Parse a Slack event into a SlackMessage."""
        if event.get("subtype") == "bot_message":
            return None

        text = event.get("text", "")
        user_id = event.get("user", "")
        channel_id = event.get("channel", "")
        ts = event.get("ts", "")
        thread_ts = event.get("thread_ts")

        if not text or not user_id:
            return None

        message_type = MessageType.CHANNEL
        if f"<@{self.config.bot_user_id}>" in text:
            message_type = MessageType.MENTION
            text = text.replace(f"<@{self.config.bot_user_id}>", "").strip()
        elif channel_id.startswith("D"):
            message_type = MessageType.DIRECT

        if thread_ts:
            message_type = MessageType.THREAD

        return SlackMessage(
            message_id=ts,
            channel_id=channel_id,
            user_id=user_id,
            text=text,
            thread_ts=thread_ts,
            message_type=message_type,
            blocks=event.get("blocks", []),
            files=event.get("files", []),
            raw_event=event,
        )

    def _store_message(self, message: SlackMessage) -> None:
        """Store a message in history for context."""
        key = message.thread_ts or message.channel_id

        if key not in self._message_history:
            self._message_history[key] = []

        self._message_history[key].append(message)

        if len(self._message_history[key]) > self.config.max_context_messages:
            self._message_history[key] = self._message_history[key][
                -self.config.max_context_messages:
            ]

    def get_context(self, channel_id: str, thread_ts: str | None = None) -> list[SlackMessage]:
        """Get conversation context for a channel/thread."""
        key = thread_ts or channel_id
        return self._message_history.get(key, [])

    async def _handle_mention(self, message: SlackMessage) -> BotResponse:
        """Handle a @mention of the bot."""
        from app.slack_bot.conversation import get_conversation_manager

        manager = get_conversation_manager()

        context = self.get_context(message.channel_id, message.thread_ts)

        response = await manager.process_message(
            user_id=message.user_id,
            channel_id=message.channel_id,
            text=message.text,
            context=context,
            thread_ts=message.thread_ts,
        )

        return response

    async def _handle_direct_message(self, message: SlackMessage) -> BotResponse:
        """Handle a direct message to the bot."""
        return await self._handle_mention(message)

    async def start_investigation(
        self,
        incident_id: str,
        channel_id: str,
        initial_query: str,
    ) -> BotResponse:
        """Start an AI investigation in a channel."""
        self._active_investigations[channel_id] = incident_id

        log.info(
            "Starting investigation: incident=%s channel=%s",
            incident_id,
            channel_id,
        )

        from app.slack_bot.blocks import get_block_builder

        builder = get_block_builder()

        blocks = builder.investigation_progress(
            incident_id=incident_id,
            status="Starting investigation...",
            steps_completed=0,
            total_steps=5,
        )

        return BotResponse(
            text="Starting AI-powered investigation...",
            blocks=blocks,
        )

    async def request_approval(
        self,
        action_type: str,
        description: str,
        user_id: str,
        channel_id: str,
        details: dict[str, Any],
        risk_level: str = "medium",
    ) -> BotResponse:
        """Request approval for an action."""
        approval_id = f"approval_{datetime.now(timezone.utc).timestamp()}"

        self._pending_approvals[approval_id] = {
            "action_type": action_type,
            "description": description,
            "requested_by": user_id,
            "channel_id": channel_id,
            "details": details,
            "risk_level": risk_level,
            "status": "pending",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        from app.slack_bot.blocks import get_block_builder

        builder = get_block_builder()

        blocks = builder.approval_card(
            approval_id=approval_id,
            action_type=action_type,
            description=description,
            details=details,
            risk_level=risk_level,
            requested_by=user_id,
        )

        return BotResponse(
            text=f"Action requires approval: {description}",
            blocks=blocks,
        )

    async def process_approval(
        self,
        approval_id: str,
        approved: bool,
        approver_id: str,
    ) -> BotResponse:
        """Process an approval decision."""
        approval = self._pending_approvals.get(approval_id)

        if not approval:
            return BotResponse(text="Approval not found or already processed.")

        approval["status"] = "approved" if approved else "rejected"
        approval["approved_by"] = approver_id
        approval["processed_at"] = datetime.now(timezone.utc).isoformat()

        if approved:
            log.info(
                "Approval granted: id=%s action=%s approver=%s",
                approval_id,
                approval["action_type"],
                approver_id,
            )

            return BotResponse(
                text=f"Approved by <@{approver_id}>. Executing action...",
            )
        else:
            log.info(
                "Approval rejected: id=%s action=%s approver=%s",
                approval_id,
                approval["action_type"],
                approver_id,
            )

            return BotResponse(
                text=f"Rejected by <@{approver_id}>. Action cancelled.",
            )

    def get_stats(self) -> dict[str, Any]:
        """Get bot statistics."""
        return {
            "status": self.status.value,
            "active_investigations": len(self._active_investigations),
            "pending_approvals": len(
                [a for a in self._pending_approvals.values() if a["status"] == "pending"]
            ),
            "cached_users": len(self._user_cache),
            "cached_channels": len(self._channel_cache),
            "message_contexts": len(self._message_history),
        }


_bot: SlackBot | None = None


def get_slack_bot() -> SlackBot:
    """Get the default Slack bot instance."""
    global _bot
    if _bot is None:
        import os
        config = SlackBotConfig(
            bot_token=os.getenv("SLACK_BOT_TOKEN", ""),
            app_token=os.getenv("SLACK_APP_TOKEN", ""),
            signing_secret=os.getenv("SLACK_SIGNING_SECRET", ""),
        )
        _bot = SlackBot(config)
    return _bot
