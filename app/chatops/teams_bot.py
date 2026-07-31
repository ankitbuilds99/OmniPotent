"""Microsoft Teams Bot - Conversational incident management via Teams."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Awaitable

import httpx

from app.config import get_settings

log = logging.getLogger("devcopilot.chatops.teams")
_settings = get_settings()


@dataclass
class TeamsConfig:
    """Teams bot configuration."""
    app_id: str
    app_password: str
    tenant_id: str | None = None  # For single-tenant apps

    # Webhook URLs
    incoming_webhook_url: str | None = None

    # Channel mappings
    alerts_channel_id: str | None = None
    incidents_channel_id: str | None = None
    approvals_channel_id: str | None = None

    # Bot settings
    bot_name: str = "DevOps Copilot"


@dataclass
class TeamsMessage:
    """A Teams message."""
    channel_id: str
    text: str
    adaptive_card: dict[str, Any] | None = None
    reply_to_id: str | None = None
    importance: str = "normal"  # low, normal, high, urgent


@dataclass
class TeamsActivity:
    """A Teams activity (message, reaction, etc.)."""
    type: str
    id: str
    channel_id: str
    conversation_id: str
    from_id: str
    from_name: str
    text: str | None
    value: dict[str, Any] | None
    reply_to_id: str | None
    service_url: str
    raw_activity: dict[str, Any] = field(default_factory=dict)


class TeamsBot:
    """Microsoft Teams bot for DevOps incident management.

    Features:
    - Respond to @mentions and direct messages
    - Post alerts with Adaptive Cards
    - Handle approval workflows via action buttons
    - Thread conversations per incident
    """

    def __init__(self, config: TeamsConfig):
        """Initialize the Teams bot.

        Args:
            config: Teams configuration
        """
        self.config = config
        self._client = httpx.AsyncClient(timeout=_settings.http_timeout_default)
        self._access_token: str | None = None
        self._token_expires: datetime | None = None
        self._command_handlers: dict[str, Callable] = {}
        self._action_handlers: dict[str, Callable] = {}
        self._incident_conversations: dict[str, str] = {}  # incident_id -> conversation_id

        log.info("TeamsBot initialized: bot=%s", config.bot_name)

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()

    async def _get_access_token(self) -> str:
        """Get or refresh the access token."""
        if self._access_token and self._token_expires:
            if datetime.now(timezone.utc) < self._token_expires:
                return self._access_token

        # Get new token
        token_url = "https://login.microsoftonline.com/botframework.com/oauth2/v2.0/token"
        if self.config.tenant_id:
            token_url = f"https://login.microsoftonline.com/{self.config.tenant_id}/oauth2/v2.0/token"

        response = await self._client.post(
            token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": self.config.app_id,
                "client_secret": self.config.app_password,
                "scope": "https://api.botframework.com/.default",
            },
        )

        if response.status_code != 200:
            raise Exception(f"Failed to get token: {response.text}")

        data = response.json()
        self._access_token = data["access_token"]
        self._token_expires = datetime.now(timezone.utc)

        return self._access_token

    def register_command(
        self,
        command: str,
        handler: Callable[[TeamsActivity], Awaitable[str]],
    ) -> None:
        """Register a command handler.

        Args:
            command: Command name (e.g., "investigate")
            handler: Async function(activity) -> response
        """
        self._command_handlers[command.lower()] = handler
        log.info("Registered command: %s", command)

    def register_action(
        self,
        action_id: str,
        handler: Callable[[TeamsActivity], Awaitable[None]],
    ) -> None:
        """Register an action handler.

        Args:
            action_id: Action identifier
            handler: Async function(activity) -> None
        """
        self._action_handlers[action_id] = handler
        log.info("Registered action: %s", action_id)

    async def handle_activity(self, activity: dict[str, Any]) -> dict[str, Any] | None:
        """Handle an incoming activity.

        Args:
            activity: Teams activity payload

        Returns:
            Response activity or None
        """
        activity_type = activity.get("type", "")
        parsed = self._parse_activity(activity)

        if activity_type == "message":
            return await self._handle_message(parsed)
        elif activity_type == "invoke":
            return await self._handle_invoke(parsed)

        return None

    def _parse_activity(self, activity: dict[str, Any]) -> TeamsActivity:
        """Parse activity payload."""
        from_data = activity.get("from", {})
        conversation = activity.get("conversation", {})

        return TeamsActivity(
            type=activity.get("type", ""),
            id=activity.get("id", ""),
            channel_id=activity.get("channelId", ""),
            conversation_id=conversation.get("id", ""),
            from_id=from_data.get("id", ""),
            from_name=from_data.get("name", ""),
            text=activity.get("text"),
            value=activity.get("value"),
            reply_to_id=activity.get("replyToId"),
            service_url=activity.get("serviceUrl", ""),
            raw_activity=activity,
        )

    async def _handle_message(self, activity: TeamsActivity) -> dict[str, Any] | None:
        """Handle a message activity."""
        text = (activity.text or "").strip()

        # Remove @mention
        text = self._remove_mention(text)

        # Parse command
        parts = text.split(maxsplit=1)
        command = parts[0].lower() if parts else ""

        handler = self._command_handlers.get(command)
        if handler:
            try:
                response = await handler(activity)
                return await self._send_reply(activity, response)
            except Exception as e:
                log.exception("Command handler failed: %s", command)
                return await self._send_reply(activity, f"Error: {e}")

        # Default response
        return await self._send_reply(
            activity,
            f"Unknown command: {command}. Try: investigate, status, approve, help"
        )

    async def _handle_invoke(self, activity: TeamsActivity) -> dict[str, Any]:
        """Handle an invoke activity (button clicks, etc.)."""
        invoke_name = activity.raw_activity.get("name", "")

        if invoke_name == "adaptiveCard/action":
            action_data = activity.value or {}
            action_id = str(action_data.get("action", ""))

            handler = self._action_handlers.get(action_id)
            if handler:
                try:
                    await handler(activity)
                    return {"status": 200, "body": {"statusCode": 200}}
                except Exception as e:
                    log.exception("Action handler failed: %s", action_id)
                    return {"status": 500, "body": {"error": str(e)}}

        return {"status": 200}

    def _remove_mention(self, text: str) -> str:
        """Remove @mention from text."""
        import re
        return re.sub(r"<at>.*?</at>\s*", "", text).strip()

    async def _send_reply(
        self,
        activity: TeamsActivity,
        text: str,
        card: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send a reply to an activity."""
        token = await self._get_access_token()

        reply: dict[str, Any] = {
            "type": "message",
            "from": {"id": self.config.app_id},
            "conversation": {"id": activity.conversation_id},
            "recipient": {"id": activity.from_id},
            "text": text,
            "replyToId": activity.id,
        }

        if card:
            reply["attachments"] = [{
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": card,
            }]

        url = f"{activity.service_url}v3/conversations/{activity.conversation_id}/activities"

        response = await self._client.post(
            url,
            json=reply,
            headers={"Authorization": f"Bearer {token}"},
        )

        return response.json() if response.status_code == 200 else {"error": response.text}

    async def send_to_channel(
        self,
        channel_id: str,
        text: str,
        card: dict[str, Any] | None = None,
        service_url: str = "https://smba.trafficmanager.net/teams/",
    ) -> dict[str, Any]:
        """Send a message to a channel.

        Args:
            channel_id: Channel ID
            text: Message text
            card: Optional Adaptive Card
            service_url: Teams service URL

        Returns:
            Response data
        """
        token = await self._get_access_token()

        message: dict[str, Any] = {
            "type": "message",
            "text": text,
        }

        if card:
            message["attachments"] = [{
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": card,
            }]

        url = f"{service_url}v3/conversations/{channel_id}/activities"

        response = await self._client.post(
            url,
            json=message,
            headers={"Authorization": f"Bearer {token}"},
        )

        return response.json() if response.status_code in (200, 201) else {"error": response.text}

    # Adaptive Card builders

    def build_alert_card(
        self,
        alert_name: str,
        severity: str,
        service: str,
        description: str,
        alert_id: str,
    ) -> dict[str, Any]:
        """Build an alert Adaptive Card."""
        severity_color = {
            "critical": "attention",
            "high": "warning",
            "medium": "accent",
            "low": "good",
        }.get(severity.lower(), "default")

        return {
            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "type": "AdaptiveCard",
            "version": "1.4",
            "body": [
                {
                    "type": "TextBlock",
                    "text": f"🚨 {alert_name}",
                    "weight": "bolder",
                    "size": "large",
                    "color": severity_color,
                },
                {
                    "type": "FactSet",
                    "facts": [
                        {"title": "Service", "value": service},
                        {"title": "Severity", "value": severity.upper()},
                        {"title": "Alert ID", "value": alert_id},
                    ],
                },
                {
                    "type": "TextBlock",
                    "text": description,
                    "wrap": True,
                },
            ],
            "actions": [
                {
                    "type": "Action.Submit",
                    "title": "🔍 Investigate",
                    "data": {"action": "investigate", "alert_id": alert_id},
                },
                {
                    "type": "Action.Submit",
                    "title": "✅ Acknowledge",
                    "data": {"action": "acknowledge", "alert_id": alert_id},
                },
            ],
        }

    def build_approval_card(
        self,
        action_name: str,
        action_type: str,
        target_service: str,
        rationale: str,
        risk_level: str,
        approval_id: str,
    ) -> dict[str, Any]:
        """Build an approval request Adaptive Card."""
        return {
            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "type": "AdaptiveCard",
            "version": "1.4",
            "body": [
                {
                    "type": "TextBlock",
                    "text": "⚠️ Approval Required",
                    "weight": "bolder",
                    "size": "large",
                },
                {
                    "type": "FactSet",
                    "facts": [
                        {"title": "Action", "value": action_name},
                        {"title": "Type", "value": action_type},
                        {"title": "Target", "value": target_service},
                        {"title": "Risk Level", "value": risk_level.upper()},
                    ],
                },
                {
                    "type": "TextBlock",
                    "text": f"**Rationale:** {rationale}",
                    "wrap": True,
                },
            ],
            "actions": [
                {
                    "type": "Action.Submit",
                    "title": "✅ Approve",
                    "style": "positive",
                    "data": {"action": "approve", "approval_id": approval_id},
                },
                {
                    "type": "Action.Submit",
                    "title": "❌ Reject",
                    "style": "destructive",
                    "data": {"action": "reject", "approval_id": approval_id},
                },
            ],
        }

    # High-level methods

    async def post_alert(
        self,
        alert_name: str,
        severity: str,
        service: str,
        description: str,
        alert_id: str,
    ) -> dict[str, Any] | None:
        """Post an alert to the alerts channel."""
        if not self.config.alerts_channel_id:
            log.warning("No alerts channel configured")
            return None

        card = self.build_alert_card(
            alert_name, severity, service, description, alert_id
        )

        return await self.send_to_channel(
            self.config.alerts_channel_id,
            f"🚨 Alert: {alert_name} on {service}",
            card,
        )

    async def request_approval(
        self,
        action_name: str,
        action_type: str,
        target_service: str,
        rationale: str,
        risk_level: str,
        approval_id: str,
    ) -> dict[str, Any] | None:
        """Post an approval request."""
        if not self.config.approvals_channel_id:
            log.warning("No approvals channel configured")
            return None

        card = self.build_approval_card(
            action_name, action_type, target_service,
            rationale, risk_level, approval_id
        )

        return await self.send_to_channel(
            self.config.approvals_channel_id,
            f"⚠️ Approval needed: {action_name}",
            card,
        )


def create_teams_bot(
    app_id: str,
    app_password: str,
    **kwargs,
) -> TeamsBot:
    """Factory function to create a Teams bot."""
    config = TeamsConfig(
        app_id=app_id,
        app_password=app_password,
        **kwargs,
    )
    return TeamsBot(config)
