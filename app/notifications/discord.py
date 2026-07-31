"""Discord notifications for DevOps-Copilot.

Provides:
- Webhook-based notifications
- Rich embeds for incidents
- Severity-based coloring
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import httpx

log = logging.getLogger("devcopilot.notifications.discord")


class DiscordColor(int, Enum):
    """Discord embed colors for different severities."""
    CRITICAL = 0xDC3545
    HIGH = 0xFD7E14
    MEDIUM = 0xFFC107
    LOW = 0x17A2B8
    INFO = 0x6C757D
    SUCCESS = 0x28A745
    DEFAULT = 0x7289DA


@dataclass
class DiscordEmbed:
    """Discord rich embed."""
    title: str
    description: str | None = None
    color: int = DiscordColor.DEFAULT
    url: str | None = None
    timestamp: datetime | None = None
    footer: dict[str, str] | None = None
    fields: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"title": self.title}
        if self.description:
            data["description"] = self.description
        if self.color:
            data["color"] = self.color
        if self.url:
            data["url"] = self.url
        if self.timestamp:
            data["timestamp"] = self.timestamp.isoformat()
        if self.footer:
            data["footer"] = self.footer
        if self.fields:
            data["fields"] = self.fields
        return data

    def add_field(self, name: str, value: str, inline: bool = False) -> DiscordEmbed:
        self.fields.append({"name": name, "value": value, "inline": inline})
        return self


@dataclass
class DiscordMessage:
    """Discord message to send."""
    content: str | None = None
    embeds: list[DiscordEmbed] = field(default_factory=list)
    username: str | None = None
    avatar_url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {}
        if self.content:
            data["content"] = self.content
        if self.embeds:
            data["embeds"] = [e.to_dict() for e in self.embeds]
        if self.username:
            data["username"] = self.username
        if self.avatar_url:
            data["avatar_url"] = self.avatar_url
        return data


class DiscordWebhook:
    """Discord webhook client for sending notifications."""

    def __init__(self, webhook_url: str | None = None):
        self.webhook_url = webhook_url or os.getenv("DISCORD_WEBHOOK_URL")
        self.default_username = "DevOps-Copilot"

    @property
    def is_configured(self) -> bool:
        return bool(self.webhook_url)

    async def send(self, message: DiscordMessage) -> dict[str, Any]:
        """Send a message via webhook."""
        if not self.is_configured:
            log.warning("Discord webhook not configured")
            return {"success": False, "error": "Discord webhook not configured"}

        try:
            payload = message.to_dict()
            if not payload.get("username"):
                payload["username"] = self.default_username

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    self.webhook_url,
                    json=payload,
                    params={"wait": "true"},
                    timeout=10.0,
                )
                if response.status_code in (200, 204):
                    log.info("Discord message sent successfully")
                    return {"success": True}
                else:
                    log.error("Discord webhook error: %s", response.status_code)
                    return {"success": False, "error": f"HTTP {response.status_code}"}
        except Exception as e:
            log.error("Failed to send Discord message: %s", e)
            return {"success": False, "error": str(e)}

    async def send_incident_alert(
        self,
        incident_id: str,
        title: str,
        severity: str,
        description: str | None = None,
        affected_services: list[str] | None = None,
    ) -> dict[str, Any]:
        """Send a formatted incident alert."""
        color = {
            "critical": DiscordColor.CRITICAL,
            "high": DiscordColor.HIGH,
            "medium": DiscordColor.MEDIUM,
            "low": DiscordColor.LOW,
        }.get(severity.lower(), DiscordColor.DEFAULT)

        severity_emoji = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🔵"}.get(severity.lower(), "⚪")

        embed = DiscordEmbed(
            title=f"{severity_emoji} {title}",
            description=description,
            color=color,
            timestamp=datetime.now(timezone.utc),
            footer={"text": f"Incident {incident_id}"},
        )
        embed.add_field("Severity", severity.upper(), inline=True)
        embed.add_field("Status", "TRIGGERED", inline=True)
        if affected_services:
            embed.add_field("Affected Services", ", ".join(affected_services), inline=False)

        return await self.send(DiscordMessage(embeds=[embed]))


_discord_webhook: DiscordWebhook | None = None


def get_discord_webhook() -> DiscordWebhook:
    """Get or create the Discord webhook singleton."""
    global _discord_webhook
    if _discord_webhook is None:
        _discord_webhook = DiscordWebhook()
    return _discord_webhook


async def send_discord_alert(
    title: str,
    severity: str,
    incident_id: str = "unknown",
    description: str | None = None,
) -> dict[str, Any]:
    """Send an incident alert to Discord."""
    webhook = get_discord_webhook()
    return await webhook.send_incident_alert(
        incident_id=incident_id,
        title=title,
        severity=severity,
        description=description,
    )
