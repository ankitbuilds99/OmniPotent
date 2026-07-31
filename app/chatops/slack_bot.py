"""Slack Bot - Conversational incident management via Slack."""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable
from enum import Enum

import httpx

log = logging.getLogger("devcopilot.chatops.slack")


class SlackMessageType(str, Enum):
    """Types of Slack messages."""
    ALERT = "alert"
    INVESTIGATION = "investigation"
    APPROVAL_REQUEST = "approval_request"
    STATUS_UPDATE = "status_update"
    POSTMORTEM = "postmortem"


@dataclass
class SlackConfig:
    """Slack bot configuration."""
    bot_token: str
    signing_secret: str
    app_token: str | None = None  # For Socket Mode

    # Channel mappings
    alerts_channel: str = "#alerts"
    incidents_channel: str = "#incidents"
    approvals_channel: str = "#approvals"

    # Bot settings
    bot_name: str = "DevOps Copilot"
    bot_emoji: str = ":robot_face:"

    # Features
    enable_threads: bool = True
    enable_reactions: bool = True
    enable_ephemeral: bool = True


@dataclass
class SlackMessage:
    """A Slack message."""
    channel: str
    text: str
    blocks: list[dict[str, Any]] | None = None
    attachments: list[dict[str, Any]] | None = None
    thread_ts: str | None = None
    reply_broadcast: bool = False
    unfurl_links: bool = False
    unfurl_media: bool = True
    metadata: dict[str, Any] | None = None


@dataclass
class SlackInteraction:
    """A Slack interaction (button click, modal submit, etc.)."""
    type: str  # block_actions, view_submission, shortcut
    user_id: str
    user_name: str
    channel_id: str | None
    action_id: str | None
    action_value: str | None
    trigger_id: str | None
    response_url: str | None
    raw_payload: dict[str, Any] = field(default_factory=dict)


class SlackBot:
    """Slack bot for DevOps incident management.

    Features:
    - Receive and respond to slash commands
    - Post alerts and investigation updates
    - Handle approval workflows via buttons
    - Thread conversations per incident
    - Interactive modals for complex inputs
    """

    def __init__(self, config: SlackConfig):
        """Initialize the Slack bot.

        Args:
            config: Slack configuration
        """
        self.config = config
        self._client = httpx.AsyncClient(
            base_url="https://slack.com/api",
            headers={
                "Authorization": f"Bearer {config.bot_token}",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )
        self._command_handlers: dict[str, Callable] = {}
        self._action_handlers: dict[str, Callable] = {}
        self._incident_threads: dict[str, str] = {}  # incident_id -> thread_ts

        log.info("SlackBot initialized: bot=%s", config.bot_name)

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()

    def register_command(
        self,
        command: str,
        handler: Callable[[str, str, str], Awaitable[str]],
    ) -> None:
        """Register a slash command handler.

        Args:
            command: Command name (e.g., "/investigate")
            handler: Async function(user_id, channel_id, text) -> response
        """
        self._command_handlers[command] = handler
        log.info("Registered command: %s", command)

    def register_action(
        self,
        action_id: str,
        handler: Callable[[SlackInteraction], Awaitable[None]],
    ) -> None:
        """Register an interactive action handler.

        Args:
            action_id: Action identifier
            handler: Async function(interaction) -> None
        """
        self._action_handlers[action_id] = handler
        log.info("Registered action: %s", action_id)

    def verify_signature(
        self,
        body: bytes,
        timestamp: str,
        signature: str,
    ) -> bool:
        """Verify Slack request signature.

        Args:
            body: Raw request body
            timestamp: X-Slack-Request-Timestamp header
            signature: X-Slack-Signature header

        Returns:
            True if signature is valid
        """
        if abs(time.time() - int(timestamp)) > 60 * 5:
            return False  # Request too old

        sig_basestring = f"v0:{timestamp}:{body.decode()}"
        my_signature = "v0=" + hmac.new(
            self.config.signing_secret.encode(),
            sig_basestring.encode(),
            hashlib.sha256,
        ).hexdigest()

        return hmac.compare_digest(my_signature, signature)

    async def handle_command(
        self,
        command: str,
        user_id: str,
        channel_id: str,
        text: str,
    ) -> str:
        """Handle a slash command.

        Args:
            command: Command name
            user_id: User who invoked
            channel_id: Channel where invoked
            text: Command arguments

        Returns:
            Response text
        """
        handler = self._command_handlers.get(command)
        if not handler:
            return f"Unknown command: {command}"

        try:
            return await handler(user_id, channel_id, text)
        except Exception as e:
            log.exception("Command handler failed: %s", command)
            return f"Error: {e}"

    async def handle_interaction(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        """Handle an interactive component interaction.

        Args:
            payload: Slack interaction payload

        Returns:
            Response payload or None
        """
        interaction = self._parse_interaction(payload)

        if interaction.action_id:
            handler = self._action_handlers.get(interaction.action_id)
            if handler:
                try:
                    await handler(interaction)
                except Exception as e:
                    log.exception("Action handler failed: %s", interaction.action_id)
                    return {"text": f"Error: {e}"}

        return None

    def _parse_interaction(self, payload: dict[str, Any]) -> SlackInteraction:
        """Parse interaction payload."""
        user = payload.get("user", {})
        channel = payload.get("channel", {})
        actions = payload.get("actions", [{}])
        action = actions[0] if actions else {}

        return SlackInteraction(
            type=payload.get("type", ""),
            user_id=user.get("id", ""),
            user_name=user.get("name", ""),
            channel_id=channel.get("id"),
            action_id=action.get("action_id"),
            action_value=action.get("value"),
            trigger_id=payload.get("trigger_id"),
            response_url=payload.get("response_url"),
            raw_payload=payload,
        )

    async def post_message(self, message: SlackMessage) -> dict[str, Any]:
        """Post a message to Slack.

        Args:
            message: Message to post

        Returns:
            Slack API response
        """
        payload = {
            "channel": message.channel,
            "text": message.text,
            "unfurl_links": message.unfurl_links,
            "unfurl_media": message.unfurl_media,
        }

        if message.blocks:
            payload["blocks"] = message.blocks
        if message.attachments:
            payload["attachments"] = message.attachments
        if message.thread_ts:
            payload["thread_ts"] = message.thread_ts
            if message.reply_broadcast:
                payload["reply_broadcast"] = True
        if message.metadata:
            payload["metadata"] = message.metadata

        response = await self._client.post("/chat.postMessage", json=payload)
        result = response.json()

        if not result.get("ok"):
            log.error("Failed to post message: %s", result.get("error"))

        return result

    async def update_message(
        self,
        channel: str,
        ts: str,
        text: str,
        blocks: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Update an existing message.

        Args:
            channel: Channel ID
            ts: Message timestamp
            text: New text
            blocks: New blocks

        Returns:
            Slack API response
        """
        payload = {"channel": channel, "ts": ts, "text": text}
        if blocks:
            payload["blocks"] = blocks

        response = await self._client.post("/chat.update", json=payload)
        return response.json()

    async def add_reaction(self, channel: str, ts: str, emoji: str) -> bool:
        """Add a reaction to a message.

        Args:
            channel: Channel ID
            ts: Message timestamp
            emoji: Emoji name (without colons)

        Returns:
            True if successful
        """
        if not self.config.enable_reactions:
            return True

        response = await self._client.post(
            "/reactions.add",
            json={"channel": channel, "timestamp": ts, "name": emoji},
        )
        return response.json().get("ok", False)

    async def open_modal(
        self,
        trigger_id: str,
        view: dict[str, Any],
    ) -> dict[str, Any]:
        """Open a modal dialog.

        Args:
            trigger_id: Trigger ID from interaction
            view: Modal view definition

        Returns:
            Slack API response
        """
        response = await self._client.post(
            "/views.open",
            json={"trigger_id": trigger_id, "view": view},
        )
        return response.json()

    # High-level incident management methods

    async def post_alert(
        self,
        alert_name: str,
        severity: str,
        service: str,
        description: str,
        alert_id: str | None = None,
    ) -> str | None:
        """Post an alert notification.

        Args:
            alert_name: Name of the alert
            severity: Alert severity (critical, high, medium, low)
            service: Affected service
            description: Alert description
            alert_id: Optional alert ID for threading

        Returns:
            Message timestamp if successful
        """
        severity_emoji = {
            "critical": ":red_circle:",
            "high": ":large_orange_circle:",
            "medium": ":large_yellow_circle:",
            "low": ":large_blue_circle:",
        }.get(severity.lower(), ":white_circle:")

        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": f"{severity_emoji} {alert_name}"}
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Service:*\n{service}"},
                    {"type": "mrkdwn", "text": f"*Severity:*\n{severity.upper()}"},
                ]
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Description:*\n{description}"}
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "🔍 Investigate"},
                        "style": "primary",
                        "action_id": "investigate_alert",
                        "value": alert_id or alert_name,
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "✅ Acknowledge"},
                        "action_id": "acknowledge_alert",
                        "value": alert_id or alert_name,
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "🔇 Silence"},
                        "action_id": "silence_alert",
                        "value": alert_id or alert_name,
                    },
                ]
            },
        ]

        message = SlackMessage(
            channel=self.config.alerts_channel,
            text=f"{severity_emoji} Alert: {alert_name} on {service}",
            blocks=blocks,
        )

        result = await self.post_message(message)
        return result.get("ts")

    async def post_investigation_update(
        self,
        incident_id: str,
        status: str,
        findings: str,
        confidence: float | None = None,
        suggested_actions: list[str] | None = None,
    ) -> str | None:
        """Post an investigation status update.

        Args:
            incident_id: Incident ID for threading
            status: Investigation status
            findings: Current findings
            confidence: Confidence score (0-1)
            suggested_actions: List of suggested actions

        Returns:
            Message timestamp if successful
        """
        thread_ts = self._incident_threads.get(incident_id)

        fields = [
            {"type": "mrkdwn", "text": f"*Status:*\n{status}"},
        ]
        if confidence is not None:
            fields.append({
                "type": "mrkdwn",
                "text": f"*Confidence:*\n{confidence:.0%}"
            })

        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "🔍 Investigation Update"}
            },
            {"type": "section", "fields": fields},
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Findings:*\n{findings}"}
            },
        ]

        if suggested_actions:
            actions_text = "\n".join(f"• {a}" for a in suggested_actions)
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Suggested Actions:*\n{actions_text}"}
            })

        message = SlackMessage(
            channel=self.config.incidents_channel,
            text=f"Investigation update for {incident_id}: {status}",
            blocks=blocks,
            thread_ts=thread_ts,
        )

        result = await self.post_message(message)

        # Store thread for future updates
        if not thread_ts and result.get("ok"):
            self._incident_threads[incident_id] = result.get("ts", "")

        return result.get("ts")

    async def request_approval(
        self,
        action_name: str,
        action_type: str,
        target_service: str,
        rationale: str,
        risk_level: str,
        approval_id: str,
        incident_id: str | None = None,
    ) -> str | None:
        """Request approval for an action.

        Args:
            action_name: Name of the action
            action_type: Type (rollback, restart, scale, etc.)
            target_service: Service to act on
            rationale: Why this action is recommended
            risk_level: low, medium, high
            approval_id: Unique approval request ID
            incident_id: Related incident ID

        Returns:
            Message timestamp if successful
        """
        risk_emoji = {
            "low": ":large_green_circle:",
            "medium": ":large_yellow_circle:",
            "high": ":red_circle:",
        }.get(risk_level.lower(), ":white_circle:")

        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "⚠️ Approval Required"}
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Action:*\n{action_name}"},
                    {"type": "mrkdwn", "text": f"*Type:*\n{action_type}"},
                    {"type": "mrkdwn", "text": f"*Target:*\n{target_service}"},
                    {"type": "mrkdwn", "text": f"*Risk:*\n{risk_emoji} {risk_level.upper()}"},
                ]
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Rationale:*\n{rationale}"}
            },
            {"type": "divider"},
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "✅ Approve"},
                        "style": "primary",
                        "action_id": "approve_action",
                        "value": approval_id,
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "❌ Reject"},
                        "style": "danger",
                        "action_id": "reject_action",
                        "value": approval_id,
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "📋 Details"},
                        "action_id": "action_details",
                        "value": approval_id,
                    },
                ]
            },
        ]

        thread_ts = self._incident_threads.get(incident_id) if incident_id else None

        message = SlackMessage(
            channel=self.config.approvals_channel,
            text=f"⚠️ Approval needed: {action_name} on {target_service}",
            blocks=blocks,
            thread_ts=thread_ts,
        )

        result = await self.post_message(message)
        return result.get("ts")

    async def post_resolution(
        self,
        incident_id: str,
        root_cause: str,
        resolution: str,
        duration_minutes: int,
        postmortem_url: str | None = None,
    ) -> str | None:
        """Post incident resolution notification.

        Args:
            incident_id: Incident ID
            root_cause: Root cause summary
            resolution: How it was resolved
            duration_minutes: Incident duration
            postmortem_url: Link to postmortem

        Returns:
            Message timestamp if successful
        """
        thread_ts = self._incident_threads.get(incident_id)

        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "✅ Incident Resolved"}
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Duration:*\n{duration_minutes} minutes"},
                    {"type": "mrkdwn", "text": f"*Incident ID:*\n{incident_id}"},
                ]
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Root Cause:*\n{root_cause}"}
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Resolution:*\n{resolution}"}
            },
        ]

        if postmortem_url:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"📝 <{postmortem_url}|View Postmortem>"
                }
            })

        message = SlackMessage(
            channel=self.config.incidents_channel,
            text=f"✅ Incident {incident_id} resolved after {duration_minutes} minutes",
            blocks=blocks,
            thread_ts=thread_ts,
            reply_broadcast=True,  # Also post to main channel
        )

        result = await self.post_message(message)

        # Add resolved reaction to original message
        if thread_ts:
            await self.add_reaction(
                self.config.incidents_channel,
                thread_ts,
                "white_check_mark",
            )

        return result.get("ts")


def create_slack_bot(
    bot_token: str,
    signing_secret: str,
    **kwargs,
) -> SlackBot:
    """Factory function to create a Slack bot."""
    config = SlackConfig(
        bot_token=bot_token,
        signing_secret=signing_secret,
        **kwargs,
    )
    return SlackBot(config)
