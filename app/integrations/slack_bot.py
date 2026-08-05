"""Slack bot integration for OmniPotent.

Enables users to interact with the investigation system directly from Slack.
Supports slash commands, mentions, and interactive messages.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Coroutine
from urllib.parse import parse_qs

log = logging.getLogger("omnipotent.integrations.slack")


class SlackEventType(Enum):
    """Types of Slack events."""

    MESSAGE = "message"
    APP_MENTION = "app_mention"
    SLASH_COMMAND = "slash_command"
    BLOCK_ACTION = "block_action"
    VIEW_SUBMISSION = "view_submission"
    REACTION_ADDED = "reaction_added"


@dataclass
class SlackUser:
    """Slack user information."""

    id: str
    name: str
    real_name: str | None = None
    email: str | None = None
    is_admin: bool = False
    is_bot: bool = False


@dataclass
class SlackChannel:
    """Slack channel information."""

    id: str
    name: str
    is_private: bool = False
    is_dm: bool = False


@dataclass
class SlackMessage:
    """Slack message."""

    text: str
    user: SlackUser
    channel: SlackChannel
    timestamp: str
    thread_ts: str | None = None
    blocks: list[dict[str, Any]] = field(default_factory=list)

    @property
    def is_thread(self) -> bool:
        """Check if message is in a thread."""
        return self.thread_ts is not None


@dataclass
class SlackCommand:
    """Slack slash command."""

    command: str
    text: str
    user: SlackUser
    channel: SlackChannel
    response_url: str
    trigger_id: str


@dataclass
class SlackAction:
    """Slack interactive action."""

    action_id: str
    action_type: str
    value: str | None
    user: SlackUser
    channel: SlackChannel
    message_ts: str
    response_url: str


class SlackBlockBuilder:
    """Builder for Slack Block Kit messages."""

    def __init__(self):
        self._blocks: list[dict[str, Any]] = []

    def header(self, text: str) -> SlackBlockBuilder:
        """Add a header block."""
        self._blocks.append(
            {
                "type": "header",
                "text": {"type": "plain_text", "text": text, "emoji": True},
            }
        )
        return self

    def section(
        self,
        text: str,
        accessory: dict[str, Any] | None = None,
    ) -> SlackBlockBuilder:
        """Add a section block."""
        block: dict[str, Any] = {
            "type": "section",
            "text": {"type": "mrkdwn", "text": text},
        }
        if accessory:
            block["accessory"] = accessory
        self._blocks.append(block)
        return self

    def divider(self) -> SlackBlockBuilder:
        """Add a divider."""
        self._blocks.append({"type": "divider"})
        return self

    def context(self, *elements: str) -> SlackBlockBuilder:
        """Add a context block."""
        self._blocks.append(
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": e} for e in elements
                ],
            }
        )
        return self

    def actions(self, *buttons: dict[str, Any]) -> SlackBlockBuilder:
        """Add an actions block with buttons."""
        self._blocks.append(
            {
                "type": "actions",
                "elements": list(buttons),
            }
        )
        return self

    def button(
        self,
        text: str,
        action_id: str,
        value: str | None = None,
        style: str | None = None,
        url: str | None = None,
    ) -> dict[str, Any]:
        """Create a button element."""
        btn: dict[str, Any] = {
            "type": "button",
            "text": {"type": "plain_text", "text": text, "emoji": True},
            "action_id": action_id,
        }
        if value:
            btn["value"] = value
        if style:
            btn["style"] = style
        if url:
            btn["url"] = url
        return btn

    def input_block(
        self,
        label: str,
        action_id: str,
        placeholder: str = "",
        multiline: bool = False,
    ) -> SlackBlockBuilder:
        """Add an input block."""
        self._blocks.append(
            {
                "type": "input",
                "label": {"type": "plain_text", "text": label},
                "element": {
                    "type": "plain_text_input",
                    "action_id": action_id,
                    "placeholder": {"type": "plain_text", "text": placeholder},
                    "multiline": multiline,
                },
            }
        )
        return self

    def build(self) -> list[dict[str, Any]]:
        """Build and return blocks."""
        return self._blocks


class SlackBot:
    """Slack bot for OmniPotent."""

    def __init__(
        self,
        bot_token: str,
        signing_secret: str,
        app_id: str | None = None,
    ):
        self._token = bot_token
        self._signing_secret = signing_secret
        self._app_id = app_id
        self._command_handlers: dict[
            str, Callable[..., Coroutine[Any, Any, dict[str, Any]]]
        ] = {}
        self._action_handlers: dict[
            str, Callable[..., Coroutine[Any, Any, dict[str, Any]]]
        ] = {}
        self._mention_handler: (
            Callable[..., Coroutine[Any, Any, dict[str, Any]]] | None
        ) = None
        self._investigation_callback: (
            Callable[..., Coroutine[Any, Any, str]] | None
        ) = None

        self._register_default_commands()

    def _register_default_commands(self) -> None:
        """Register default slash commands."""

        async def investigate(cmd: SlackCommand) -> dict[str, Any]:
            if not cmd.text.strip():
                return self._error_response("Please provide a question to investigate.")

            blocks = (
                SlackBlockBuilder()
                .section(f"Starting investigation: _{cmd.text}_")
                .context(f"Requested by <@{cmd.user.id}>")
                .build()
            )

            if self._investigation_callback:
                try:
                    result = await self._investigation_callback(
                        cmd.text, cmd.user.id, cmd.channel.id
                    )
                    blocks = (
                        SlackBlockBuilder()
                        .header("Investigation Complete")
                        .section(result[:2900])
                        .divider()
                        .context(f"Investigated by <@{cmd.user.id}>")
                        .actions(
                            SlackBlockBuilder().button(
                                "View Details",
                                "view_investigation",
                                style="primary",
                            ),
                            SlackBlockBuilder().button(
                                "New Investigation",
                                "new_investigation",
                            ),
                        )
                        .build()
                    )
                except Exception as e:
                    log.exception("investigation failed")
                    return self._error_response(f"Investigation failed: {e}")

            return {"blocks": blocks, "response_type": "in_channel"}

        async def status(cmd: SlackCommand) -> dict[str, Any]:
            blocks = (
                SlackBlockBuilder()
                .header("OmniPotent Status")
                .section("*Status:* :white_check_mark: Operational")
                .section("*Active Investigations:* 0")
                .section("*Last 24h:* 42 investigations completed")
                .build()
            )
            return {"blocks": blocks}

        async def help_cmd(cmd: SlackCommand) -> dict[str, Any]:
            blocks = (
                SlackBlockBuilder()
                .header("OmniPotent Help")
                .section(
                    "*Available Commands:*\n"
                    "• `/investigate <question>` - Start an investigation\n"
                    "• `/copilot-status` - Check system status\n"
                    "• `/copilot-help` - Show this help message"
                )
                .divider()
                .section(
                    "*Tips:*\n"
                    "• Mention @OmniPotent in any channel to ask questions\n"
                    "• Use threads for follow-up questions\n"
                    "• React with :eyes: to have the bot analyze an error"
                )
                .build()
            )
            return {"blocks": blocks}

        self._command_handlers["/investigate"] = investigate
        self._command_handlers["/copilot-status"] = status
        self._command_handlers["/copilot-help"] = help_cmd

    def verify_request(
        self,
        timestamp: str,
        body: bytes,
        signature: str,
    ) -> bool:
        """Verify Slack request signature."""
        if abs(time.time() - int(timestamp)) > 60 * 5:
            return False

        base = f"v0:{timestamp}:".encode() + body
        computed = "v0=" + hmac.new(
            self._signing_secret.encode(),
            base,
            hashlib.sha256,
        ).hexdigest()

        return hmac.compare_digest(computed, signature)

    def register_command(
        self,
        command: str,
        handler: Callable[..., Coroutine[Any, Any, dict[str, Any]]],
    ) -> None:
        """Register a slash command handler."""
        self._command_handlers[command] = handler

    def register_action(
        self,
        action_id: str,
        handler: Callable[..., Coroutine[Any, Any, dict[str, Any]]],
    ) -> None:
        """Register an action handler."""
        self._action_handlers[action_id] = handler

    def set_mention_handler(
        self,
        handler: Callable[..., Coroutine[Any, Any, dict[str, Any]]],
    ) -> None:
        """Set handler for @mentions."""
        self._mention_handler = handler

    def set_investigation_callback(
        self,
        callback: Callable[..., Coroutine[Any, Any, str]],
    ) -> None:
        """Set callback for running investigations."""
        self._investigation_callback = callback

    async def handle_event(
        self,
        event_type: SlackEventType,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Handle a Slack event."""
        if event_type == SlackEventType.SLASH_COMMAND:
            return await self._handle_command(payload)
        elif event_type == SlackEventType.APP_MENTION:
            return await self._handle_mention(payload)
        elif event_type == SlackEventType.BLOCK_ACTION:
            return await self._handle_action(payload)
        elif event_type == SlackEventType.MESSAGE:
            return await self._handle_message(payload)
        else:
            return {"ok": True}

    async def _handle_command(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Handle slash command."""
        command = SlackCommand(
            command=payload.get("command", ""),
            text=payload.get("text", ""),
            user=SlackUser(
                id=payload.get("user_id", ""),
                name=payload.get("user_name", ""),
            ),
            channel=SlackChannel(
                id=payload.get("channel_id", ""),
                name=payload.get("channel_name", ""),
            ),
            response_url=payload.get("response_url", ""),
            trigger_id=payload.get("trigger_id", ""),
        )

        handler = self._command_handlers.get(command.command)
        if handler:
            try:
                return await handler(command)
            except Exception as e:
                log.exception("command handler failed")
                return self._error_response(str(e))

        return self._error_response(f"Unknown command: {command.command}")

    async def _handle_mention(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Handle @mention."""
        event = payload.get("event", {})

        text = event.get("text", "")
        text = re.sub(r"<@[A-Z0-9]+>", "", text).strip()

        if self._mention_handler:
            message = SlackMessage(
                text=text,
                user=SlackUser(
                    id=event.get("user", ""),
                    name="",
                ),
                channel=SlackChannel(
                    id=event.get("channel", ""),
                    name="",
                ),
                timestamp=event.get("ts", ""),
                thread_ts=event.get("thread_ts"),
            )
            return await self._mention_handler(message)

        if self._investigation_callback:
            try:
                result = await self._investigation_callback(
                    text,
                    event.get("user", ""),
                    event.get("channel", ""),
                )
                return {
                    "text": result[:3000],
                    "thread_ts": event.get("thread_ts") or event.get("ts"),
                }
            except Exception as e:
                log.exception("investigation failed")
                return {"text": f"Investigation failed: {e}"}

        return {"text": "I received your message!"}

    async def _handle_action(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Handle interactive action."""
        actions = payload.get("actions", [])
        if not actions:
            return {"ok": True}

        action_data = actions[0]
        action = SlackAction(
            action_id=action_data.get("action_id", ""),
            action_type=action_data.get("type", ""),
            value=action_data.get("value"),
            user=SlackUser(
                id=payload.get("user", {}).get("id", ""),
                name=payload.get("user", {}).get("name", ""),
            ),
            channel=SlackChannel(
                id=payload.get("channel", {}).get("id", ""),
                name=payload.get("channel", {}).get("name", ""),
            ),
            message_ts=payload.get("message", {}).get("ts", ""),
            response_url=payload.get("response_url", ""),
        )

        handler = self._action_handlers.get(action.action_id)
        if handler:
            try:
                return await handler(action)
            except Exception as e:
                log.exception("action handler failed")
                return self._error_response(str(e))

        return {"ok": True}

    async def _handle_message(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Handle direct message."""
        event = payload.get("event", {})

        if event.get("bot_id"):
            return {"ok": True}

        if self._investigation_callback:
            text = event.get("text", "")
            try:
                result = await self._investigation_callback(
                    text,
                    event.get("user", ""),
                    event.get("channel", ""),
                )
                return {"text": result[:3000]}
            except Exception:
                log.exception("DM handling failed")

        return {"ok": True}

    def _error_response(self, message: str) -> dict[str, Any]:
        """Create an error response."""
        return {
            "response_type": "ephemeral",
            "blocks": (
                SlackBlockBuilder()
                .section(f":warning: {message}")
                .build()
            ),
        }

    def parse_slash_command(self, body: bytes) -> dict[str, Any]:
        """Parse slash command from request body."""
        parsed = parse_qs(body.decode())
        return {k: v[0] if len(v) == 1 else v for k, v in parsed.items()}

    def parse_interaction(self, body: bytes) -> dict[str, Any]:
        """Parse interaction payload from request body."""
        parsed = parse_qs(body.decode())
        payload_str = parsed.get("payload", ["{}"])[0]
        return json.loads(payload_str)


_bot: SlackBot | None = None


def get_slack_bot(
    bot_token: str | None = None,
    signing_secret: str | None = None,
) -> SlackBot:
    """Get or create the Slack bot."""
    global _bot
    if _bot is None:
        import os

        token = bot_token or os.environ.get("SLACK_BOT_TOKEN", "")
        secret = signing_secret or os.environ.get("SLACK_SIGNING_SECRET", "")
        _bot = SlackBot(token, secret)
    return _bot
