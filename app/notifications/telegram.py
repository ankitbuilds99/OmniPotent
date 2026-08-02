"""Telegram notifications for OmniPotent.

Provides:
- Bot-based notifications
- Rich message formatting (Markdown)
- Inline keyboards for actions
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx

log = logging.getLogger("omnipotent.notifications.telegram")

TELEGRAM_API_BASE = "https://api.telegram.org/bot"


@dataclass
class InlineButton:
    """Telegram inline keyboard button."""
    text: str
    url: str | None = None
    callback_data: str | None = None

    def to_dict(self) -> dict[str, str]:
        data = {"text": self.text}
        if self.url:
            data["url"] = self.url
        elif self.callback_data:
            data["callback_data"] = self.callback_data
        return data


@dataclass
class InlineKeyboard:
    """Telegram inline keyboard."""
    rows: list[list[InlineButton]] = field(default_factory=list)

    def add_row(self, *buttons: InlineButton) -> InlineKeyboard:
        self.rows.append(list(buttons))
        return self

    def to_dict(self) -> dict[str, Any]:
        return {"inline_keyboard": [[btn.to_dict() for btn in row] for row in self.rows]}


@dataclass
class TelegramMessage:
    """Telegram message to send."""
    chat_id: str | int
    text: str
    parse_mode: str = "Markdown"
    disable_notification: bool = False
    reply_markup: InlineKeyboard | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "chat_id": self.chat_id,
            "text": self.text,
            "parse_mode": self.parse_mode,
            "disable_notification": self.disable_notification,
        }
        if self.reply_markup:
            data["reply_markup"] = self.reply_markup.to_dict()
        return data


class TelegramBot:
    """Telegram bot client for sending notifications."""

    def __init__(self, bot_token: str | None = None):
        self.bot_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN")
        self.default_chat_id = os.getenv("TELEGRAM_DEFAULT_CHAT_ID")

    @property
    def is_configured(self) -> bool:
        return bool(self.bot_token)

    @property
    def api_url(self) -> str:
        return f"{TELEGRAM_API_BASE}{self.bot_token}"

    async def send_message(self, message: TelegramMessage) -> dict[str, Any]:
        """Send a message."""
        if not self.is_configured:
            log.warning("Telegram bot not configured")
            return {"success": False, "error": "Telegram bot not configured"}

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.api_url}/sendMessage",
                    json=message.to_dict(),
                    timeout=10.0,
                )
                data = response.json()
                if data.get("ok"):
                    result = data.get("result", {})
                    log.info("Telegram message sent to %s", message.chat_id)
                    return {"success": True, "message_id": result.get("message_id")}
                else:
                    error = data.get("description", "Unknown error")
                    log.error("Telegram API error: %s", error)
                    return {"success": False, "error": error}
        except Exception as e:
            log.error("Failed to send Telegram message: %s", e)
            return {"success": False, "error": str(e)}

    def _escape_markdown(self, text: str) -> str:
        """Escape Markdown special characters."""
        for char in ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']:
            text = text.replace(char, f'\\{char}')
        return text

    async def send_incident_alert(
        self,
        incident_id: str,
        title: str,
        severity: str,
        description: str | None = None,
        chat_id: str | int | None = None,
    ) -> dict[str, Any]:
        """Send a formatted incident alert."""
        target_chat = chat_id or self.default_chat_id
        if not target_chat:
            return {"success": False, "error": "No chat_id provided"}

        severity_emoji = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🔵"}.get(severity.lower(), "⚪")

        lines = [
            f"{severity_emoji} *INCIDENT ALERT*",
            "",
            f"*{title}*",
            "",
            f"🆔 ID: `{incident_id}`",
            f"⚠️ Severity: *{severity.upper()}*",
        ]
        if description:
            lines.append(f"📝 {description}")
        lines.append(f"⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")

        text = "\n".join(lines)
        message = TelegramMessage(chat_id=target_chat, text=text)
        return await self.send_message(message)


_telegram_bot: TelegramBot | None = None


def get_telegram_bot() -> TelegramBot:
    """Get or create the Telegram bot singleton."""
    global _telegram_bot
    if _telegram_bot is None:
        _telegram_bot = TelegramBot()
    return _telegram_bot


async def send_telegram_alert(
    title: str,
    severity: str,
    incident_id: str = "unknown",
    description: str | None = None,
    chat_id: str | int | None = None,
) -> dict[str, Any]:
    """Send an incident alert to Telegram."""
    bot = get_telegram_bot()
    return await bot.send_incident_alert(
        incident_id=incident_id,
        title=title,
        severity=severity,
        description=description,
        chat_id=chat_id,
    )
