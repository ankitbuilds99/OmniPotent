"""Microsoft Teams integration.

Provides:
- Incoming webhook notifications
- Adaptive Card messages
- Interactive message responses
- Bot framework integration
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import httpx

log = logging.getLogger("devcopilot.integrations.teams")


class CardStyle(Enum):
    """Adaptive card styles."""

    DEFAULT = "default"
    ACCENT = "accent"
    GOOD = "good"
    WARNING = "warning"
    ATTENTION = "attention"


@dataclass
class TeamsConfig:
    """Teams integration configuration."""

    webhook_url: str = ""
    bot_id: str = ""
    bot_password: str = ""
    tenant_id: str = ""

    @classmethod
    def from_env(cls) -> "TeamsConfig":
        """Load config from environment."""
        return cls(
            webhook_url=os.environ.get("TEAMS_WEBHOOK_URL", ""),
            bot_id=os.environ.get("TEAMS_BOT_ID", ""),
            bot_password=os.environ.get("TEAMS_BOT_PASSWORD", ""),
            tenant_id=os.environ.get("TEAMS_TENANT_ID", ""),
        )

    def is_configured(self) -> bool:
        """Check if webhook is configured."""
        return bool(self.webhook_url)

    def is_bot_configured(self) -> bool:
        """Check if bot is configured."""
        return bool(self.bot_id and self.bot_password)


@dataclass
class AdaptiveCard:
    """Microsoft Adaptive Card builder."""

    body: list[dict[str, Any]] = field(default_factory=list)
    actions: list[dict[str, Any]] = field(default_factory=list)
    version: str = "1.4"

    def add_text(
        self,
        text: str,
        size: str = "default",
        weight: str = "default",
        color: str = "default",
        wrap: bool = True,
    ) -> "AdaptiveCard":
        """Add text block."""
        self.body.append({
            "type": "TextBlock",
            "text": text,
            "size": size,
            "weight": weight,
            "color": color,
            "wrap": wrap,
        })
        return self

    def add_heading(self, text: str) -> "AdaptiveCard":
        """Add heading text."""
        return self.add_text(text, size="large", weight="bolder")

    def add_subheading(self, text: str) -> "AdaptiveCard":
        """Add subheading text."""
        return self.add_text(text, size="medium", weight="bolder")

    def add_facts(self, facts: list[tuple[str, str]]) -> "AdaptiveCard":
        """Add fact set (key-value pairs)."""
        self.body.append({
            "type": "FactSet",
            "facts": [{"title": k, "value": v} for k, v in facts],
        })
        return self

    def add_image(
        self,
        url: str,
        alt_text: str = "",
        size: str = "auto",
    ) -> "AdaptiveCard":
        """Add image."""
        self.body.append({
            "type": "Image",
            "url": url,
            "altText": alt_text,
            "size": size,
        })
        return self

    def add_column_set(self, columns: list[dict[str, Any]]) -> "AdaptiveCard":
        """Add column set for side-by-side content."""
        self.body.append({
            "type": "ColumnSet",
            "columns": columns,
        })
        return self

    def add_container(
        self,
        items: list[dict[str, Any]],
        style: CardStyle = CardStyle.DEFAULT,
    ) -> "AdaptiveCard":
        """Add container with items."""
        self.body.append({
            "type": "Container",
            "style": style.value,
            "items": items,
        })
        return self

    def add_action_button(
        self,
        title: str,
        url: str,
        style: str = "default",
    ) -> "AdaptiveCard":
        """Add URL action button."""
        self.actions.append({
            "type": "Action.OpenUrl",
            "title": title,
            "url": url,
            "style": style,
        })
        return self

    def add_submit_button(
        self,
        title: str,
        data: dict[str, Any],
        style: str = "default",
    ) -> "AdaptiveCard":
        """Add submit action button."""
        self.actions.append({
            "type": "Action.Submit",
            "title": title,
            "data": data,
            "style": style,
        })
        return self

    def add_input_text(
        self,
        input_id: str,
        label: str,
        placeholder: str = "",
        is_multiline: bool = False,
    ) -> "AdaptiveCard":
        """Add text input field."""
        self.body.append({
            "type": "Input.Text",
            "id": input_id,
            "label": label,
            "placeholder": placeholder,
            "isMultiline": is_multiline,
        })
        return self

    def add_input_choice(
        self,
        input_id: str,
        label: str,
        choices: list[tuple[str, str]],
        default_value: str = "",
    ) -> "AdaptiveCard":
        """Add choice input (dropdown)."""
        self.body.append({
            "type": "Input.ChoiceSet",
            "id": input_id,
            "label": label,
            "value": default_value,
            "choices": [{"title": t, "value": v} for t, v in choices],
        })
        return self

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary format."""
        card: dict[str, Any] = {
            "type": "AdaptiveCard",
            "version": self.version,
            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "body": self.body,
        }
        if self.actions:
            card["actions"] = self.actions
        return card

    def to_message(self, summary: str = "") -> dict[str, Any]:
        """Convert to Teams message format."""
        return {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "contentUrl": None,
                    "content": self.to_dict(),
                }
            ],
            "summary": summary or "DevOps Copilot Notification",
        }


class TeamsClient:
    """Microsoft Teams client for sending notifications."""

    def __init__(self, config: TeamsConfig | None = None):
        self.config = config or TeamsConfig.from_env()

    async def send_webhook(self, payload: dict[str, Any]) -> bool:
        """Send message via incoming webhook."""
        if not self.config.is_configured():
            log.warning("Teams webhook not configured")
            return False

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    self.config.webhook_url,
                    json=payload,
                    timeout=30,
                )

            if resp.status_code == 200:
                log.info("Teams webhook message sent")
                return True
            else:
                log.error("Teams webhook error: %s %s", resp.status_code, resp.text)
                return False
        except Exception as e:
            log.error("Teams webhook failed: %s", e)
            return False

    async def send_text(self, text: str) -> bool:
        """Send simple text message."""
        return await self.send_webhook({"text": text})

    async def send_card(self, card: AdaptiveCard, summary: str = "") -> bool:
        """Send adaptive card message."""
        return await self.send_webhook(card.to_message(summary))

    async def send_alert(
        self,
        title: str,
        severity: str,
        message: str,
        service: str = "",
        dashboard_url: str = "",
        investigation_id: str = "",
    ) -> bool:
        """Send alert notification."""
        severity_colors = {
            "critical": "attention",
            "high": "warning",
            "medium": "accent",
            "low": "good",
        }
        color = severity_colors.get(severity.lower(), "default")

        card = AdaptiveCard()
        card.add_container(
            [
                {"type": "TextBlock", "text": f"🚨 Alert: {title}", "size": "large", "weight": "bolder"},
            ],
            CardStyle(color),
        )

        facts = [("Severity", severity.upper())]
        if service:
            facts.append(("Service", service))
        if investigation_id:
            facts.append(("Investigation", investigation_id))

        card.add_facts(facts)
        card.add_text(message, wrap=True)

        if dashboard_url:
            card.add_action_button("View Dashboard", dashboard_url, style="positive")

        return await self.send_card(card, f"Alert: {title}")

    async def send_investigation_update(
        self,
        investigation_id: str,
        status: str,
        summary: str,
        findings_count: int = 0,
        report_url: str = "",
    ) -> bool:
        """Send investigation status update."""
        status_emoji = {
            "started": "🔍",
            "in_progress": "⏳",
            "completed": "✅",
            "failed": "❌",
        }
        emoji = status_emoji.get(status.lower(), "📋")

        card = AdaptiveCard()
        card.add_heading(f"{emoji} Investigation {status.title()}")
        card.add_facts([
            ("Investigation ID", investigation_id),
            ("Status", status.upper()),
            ("Findings", str(findings_count)),
        ])

        if summary:
            card.add_text(summary, wrap=True)

        if report_url:
            card.add_action_button("View Full Report", report_url)

        return await self.send_card(card, f"Investigation {status}: {investigation_id}")

    async def send_approval_request(
        self,
        action: str,
        details: str,
        requester: str,
        approval_id: str,
        callback_url: str,
    ) -> bool:
        """Send approval request with interactive buttons."""
        card = AdaptiveCard()
        card.add_heading("⚠️ Approval Required")
        card.add_facts([
            ("Action", action),
            ("Requested By", requester),
            ("Approval ID", approval_id),
        ])
        card.add_text(details, wrap=True)

        card.add_submit_button(
            "Approve",
            {"action": "approve", "approval_id": approval_id, "callback_url": callback_url},
            style="positive",
        )
        card.add_submit_button(
            "Reject",
            {"action": "reject", "approval_id": approval_id, "callback_url": callback_url},
            style="destructive",
        )

        return await self.send_card(card, f"Approval required: {action}")

    async def send_postmortem_summary(
        self,
        incident_id: str,
        title: str,
        duration: str,
        impact: str,
        root_cause: str,
        action_items_count: int,
        postmortem_url: str = "",
    ) -> bool:
        """Send postmortem summary notification."""
        card = AdaptiveCard()
        card.add_heading(f"📝 Postmortem: {title}")
        card.add_facts([
            ("Incident ID", incident_id),
            ("Duration", duration),
            ("Impact", impact),
            ("Action Items", str(action_items_count)),
        ])
        card.add_subheading("Root Cause")
        card.add_text(root_cause[:500], wrap=True)

        if postmortem_url:
            card.add_action_button("View Full Postmortem", postmortem_url)

        return await self.send_card(card, f"Postmortem: {title}")

    async def send_runbook_execution(
        self,
        runbook_name: str,
        status: str,
        steps_completed: int,
        total_steps: int,
        duration: str = "",
        output_url: str = "",
    ) -> bool:
        """Send runbook execution status."""
        status_emoji = {
            "started": "▶️",
            "running": "⏳",
            "completed": "✅",
            "failed": "❌",
            "paused": "⏸️",
        }
        emoji = status_emoji.get(status.lower(), "📋")

        card = AdaptiveCard()
        card.add_heading(f"{emoji} Runbook: {runbook_name}")
        card.add_facts([
            ("Status", status.upper()),
            ("Progress", f"{steps_completed}/{total_steps} steps"),
        ])

        if duration:
            card.add_text(f"Duration: {duration}")

        if output_url:
            card.add_action_button("View Output", output_url)

        return await self.send_card(card, f"Runbook {status}: {runbook_name}")


_client: TeamsClient | None = None


def get_teams_client() -> TeamsClient:
    """Get the Teams client singleton."""
    global _client
    if _client is None:
        _client = TeamsClient()
    return _client


async def send_teams_alert(
    title: str,
    severity: str,
    message: str,
    **kwargs: Any,
) -> bool:
    """Send alert to Teams."""
    client = get_teams_client()
    return await client.send_alert(title, severity, message, **kwargs)


async def send_teams_message(text: str) -> bool:
    """Send simple text message to Teams."""
    client = get_teams_client()
    return await client.send_text(text)


async def send_teams_card(card: AdaptiveCard, summary: str = "") -> bool:
    """Send adaptive card to Teams."""
    client = get_teams_client()
    return await client.send_card(card, summary)
