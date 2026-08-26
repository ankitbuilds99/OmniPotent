"""Default channel handlers for the notification dispatcher.

`NotificationDispatcher` delivers through pluggable handler slots — `slack_handler`,
`sms_handler`, `teams_handler` and so on — that callers are expected to populate.
Nothing ever populated them, so six channels (SLACK, WEBHOOK, SMS, VOICE, WHATSAPP,
TEAMS) returned "<channel> not configured" for every notification regardless of how
the deployment was configured. Teams was the sharpest case: a user with a valid
`teams_webhook_url` in their preferences still got "Teams handler not set", because
the guard accepted the URL but the delivery branch required the absent handler.

This module supplies those handlers by adapting senders that already existed and
were already tested — `app/notifications/sms.py`, `app/slack_bot/client.py` and
`app/integrations/teams.py` — to the signatures the dispatcher calls them with.

Two conventions matter here and are easy to get wrong:

1. **Return types differ per channel.** The dispatcher uses SLACK, WEBHOOK and SMS
   results directly as booleans (`success = await self.slack_handler(...)`) but
   reads VOICE, WHATSAPP and TEAMS results as mappings (`result.get("success")`).
   An adapter returning the wrong shape does not raise — a dict is truthy, so a
   *failed* send would be recorded as delivered. Each adapter below returns the
   exact shape its branch consumes, and the tests pin that.

2. **Fail closed.** Missing credentials must produce an unambiguous delivery
   failure, never a silent success and never a fabricated destination. Handlers
   also never raise: the dispatcher catches broadly and would otherwise record the
   stack-trace text as the delivery error, hiding the real reason.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("omnipotent.notifications.handlers")

__all__ = ["install_default_handlers"]


async def _slack_send(user_id: str, text: str) -> bool:
    """Deliver a Slack DM. Returns bool — the SLACK branch uses it as one.

    `chat.postMessage` accepts a user ID in the `channel` field and opens the DM
    itself, so the user ID from preferences can be passed straight through.
    """
    try:
        from app.slack_bot.client import get_slack_client

        client = await get_slack_client()
        resp = await client.send_message(channel=user_id, text=text)
        return bool(getattr(resp, "ok", False))
    except Exception as exc:
        # get_slack_client() raises SlackAPIError when SLACK_BOT_TOKEN is unset,
        # which is the common path on an unconfigured deployment rather than a bug.
        log.warning("Slack delivery failed: %s", exc)
        return False


async def _webhook_send(
    notification_id: str,
    title: str,
    body: str,
    data: dict[str, Any] | None = None,
) -> bool:
    """POST the notification to the configured outbound webhook. Returns bool.

    There is no per-user webhook URL in NotificationPreferences, so the target is
    deployment-level configuration. With no URL set this reports failure rather
    than success-with-nowhere-to-go.
    """
    try:
        import httpx

        from app.config import get_settings

        url = get_settings().copilot_notification_webhook_url
        if not url:
            log.warning(
                "Webhook notification requested but COPILOT_NOTIFICATION_WEBHOOK_URL "
                "is not set; refusing to report success"
            )
            return False

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                url,
                json={
                    "notification_id": notification_id,
                    "title": title,
                    "body": body,
                    "data": data or {},
                },
                timeout=30,
            )
        return 200 <= resp.status_code < 300
    except Exception as exc:
        log.warning("Webhook delivery failed: %s", exc)
        return False


async def _sms_send(phone: str, message: str) -> bool:
    """Send an SMS. Returns bool — the SMS branch uses it as one.

    Note the asymmetry with voice/WhatsApp below: the underlying Twilio helpers
    all return dicts, but the dispatcher consumes this one as a boolean, so the
    dict is reduced here rather than passed through.
    """
    try:
        from app.notifications.sms import send_sms

        result = await send_sms(to=phone, body=message)
        return bool(result.get("success", False))
    except Exception as exc:
        log.warning("SMS delivery failed: %s", exc)
        return False


async def _voice_send(phone: str, message: str) -> dict[str, Any]:
    """Place a voice call. Returns a mapping — the VOICE branch reads .get()."""
    try:
        from app.notifications.sms import make_voice_call

        return await make_voice_call(to=phone, message=message)
    except Exception as exc:
        log.warning("Voice delivery failed: %s", exc)
        return {"success": False, "error": str(exc)}


async def _whatsapp_send(phone: str, message: str) -> dict[str, Any]:
    """Send a WhatsApp message. Returns a mapping — WHATSAPP reads .get()."""
    try:
        from app.notifications.sms import send_whatsapp

        return await send_whatsapp(to=phone, body=message)
    except Exception as exc:
        log.warning("WhatsApp delivery failed: %s", exc)
        return {"success": False, "error": str(exc)}


async def _teams_send(
    webhook_url: str | None,
    title: str,
    body: str,
) -> dict[str, Any]:
    """Post to Microsoft Teams. Returns a mapping — the TEAMS branch reads .get().

    The per-user `teams_webhook_url` from preferences wins when present; otherwise
    the deployment-level `TEAMS_WEBHOOK_URL` picked up by TeamsConfig.from_env()
    applies. This is the case that previously failed even when correctly set up.
    """
    try:
        from app.integrations.teams import TeamsClient, TeamsConfig

        config = TeamsConfig.from_env()
        if webhook_url:
            config.webhook_url = webhook_url
        if not config.is_configured():
            return {
                "success": False,
                "error": "Teams webhook not configured (set TEAMS_WEBHOOK_URL)",
            }

        ok = await TeamsClient(config).send_text(f"**{title}**\n\n{body}")
        return {"success": ok, "error": None if ok else "Teams webhook post failed"}
    except Exception as exc:
        log.warning("Teams delivery failed: %s", exc)
        return {"success": False, "error": str(exc)}


def install_default_handlers(dispatcher: Any) -> None:
    """Attach the default handlers to `dispatcher`.

    Only unset slots are filled, so a caller that installed its own handler before
    the dispatcher was first requested keeps it. Discord and Telegram are left
    alone deliberately: their delivery branches call their sender modules directly
    and ignore the handler slot entirely, so populating those slots would suggest
    a wiring that does not exist.
    """
    if dispatcher.slack_handler is None:
        dispatcher.set_slack_handler(_slack_send)
    if dispatcher.webhook_handler is None:
        dispatcher.set_webhook_handler(_webhook_send)
    if dispatcher.sms_handler is None:
        dispatcher.set_sms_handler(_sms_send)
    if dispatcher.voice_handler is None:
        dispatcher.set_voice_handler(_voice_send)
    if dispatcher.whatsapp_handler is None:
        dispatcher.set_whatsapp_handler(_whatsapp_send)
    if dispatcher.teams_handler is None:
        dispatcher.set_teams_handler(_teams_send)
