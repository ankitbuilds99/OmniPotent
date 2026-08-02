"""SMS, Voice Call, and WhatsApp notifications via Twilio.

Provides:
- SMS text messages
- Voice calls with TTS
- WhatsApp messages
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from enum import Enum
from typing import Any

log = logging.getLogger("omnipotent.notifications.sms")


class CallStatus(str, Enum):
    """Voice call status."""
    QUEUED = "queued"
    RINGING = "ringing"
    IN_PROGRESS = "in-progress"
    COMPLETED = "completed"
    FAILED = "failed"
    BUSY = "busy"
    NO_ANSWER = "no-answer"


@dataclass
class SMSMessage:
    """SMS message to send."""
    to: str
    body: str
    from_number: str | None = None


@dataclass
class VoiceCall:
    """Voice call configuration."""
    to: str
    message: str
    from_number: str | None = None
    voice: str = "alice"
    language: str = "en-US"
    loop: int = 1


@dataclass
class WhatsAppMessage:
    """WhatsApp message to send."""
    to: str
    body: str
    from_number: str | None = None
    media_url: str | None = None


class TwilioProvider:
    """Twilio provider for SMS, Voice, and WhatsApp."""

    def __init__(self):
        self.account_sid = os.getenv("TWILIO_ACCOUNT_SID")
        self.auth_token = os.getenv("TWILIO_AUTH_TOKEN")
        self.phone_number = os.getenv("TWILIO_PHONE_NUMBER")
        self.whatsapp_number = os.getenv("TWILIO_WHATSAPP_NUMBER")
        self.client = None

        if self.account_sid and self.auth_token:
            try:
                from twilio.rest import Client
                self.client = Client(self.account_sid, self.auth_token)
                log.info("Twilio client initialized")
            except ImportError:
                log.warning("twilio package not installed")
            except Exception as e:
                log.error("Failed to initialize Twilio: %s", e)

    @property
    def is_configured(self) -> bool:
        return self.client is not None and self.phone_number is not None

    async def send_sms(self, message: SMSMessage) -> dict[str, Any]:
        """Send SMS message."""
        if not self.is_configured:
            log.warning("Twilio not configured for SMS")
            return {"success": False, "error": "Twilio not configured"}

        try:
            from_number = message.from_number or self.phone_number
            result = self.client.messages.create(
                body=message.body,
                from_=from_number,
                to=message.to,
            )
            log.info("SMS sent to %s, SID: %s", message.to, result.sid)
            return {"success": True, "message_sid": result.sid, "status": result.status}
        except Exception as e:
            log.error("Failed to send SMS to %s: %s", message.to, e)
            return {"success": False, "error": str(e)}

    async def make_voice_call(self, call: VoiceCall) -> dict[str, Any]:
        """Make a voice call with text-to-speech."""
        if not self.is_configured:
            log.warning("Twilio not configured for voice calls")
            return {"success": False, "error": "Twilio not configured"}

        try:
            from_number = call.from_number or self.phone_number
            twiml = f"""
            <Response>
                <Say voice="{call.voice}" language="{call.language}" loop="{call.loop}">
                    {call.message}
                </Say>
            </Response>
            """
            result = self.client.calls.create(
                twiml=twiml,
                from_=from_number,
                to=call.to,
            )
            log.info("Voice call initiated to %s, SID: %s", call.to, result.sid)
            return {"success": True, "call_sid": result.sid, "status": result.status}
        except Exception as e:
            log.error("Failed to make voice call to %s: %s", call.to, e)
            return {"success": False, "error": str(e)}

    async def send_whatsapp(self, message: WhatsAppMessage) -> dict[str, Any]:
        """Send WhatsApp message."""
        if not self.client:
            log.warning("Twilio not configured for WhatsApp")
            return {"success": False, "error": "Twilio not configured"}

        whatsapp_from = message.from_number or self.whatsapp_number
        if not whatsapp_from:
            return {"success": False, "error": "WhatsApp number not configured"}

        try:
            params: dict[str, Any] = {
                "body": message.body,
                "from_": f"whatsapp:{whatsapp_from}",
                "to": f"whatsapp:{message.to}",
            }
            if message.media_url:
                params["media_url"] = [message.media_url]

            result = self.client.messages.create(**params)
            log.info("WhatsApp sent to %s, SID: %s", message.to, result.sid)
            return {"success": True, "message_sid": result.sid, "status": result.status}
        except Exception as e:
            log.error("Failed to send WhatsApp to %s: %s", message.to, e)
            return {"success": False, "error": str(e)}


_twilio_provider: TwilioProvider | None = None


def get_twilio_provider() -> TwilioProvider:
    """Get or create the Twilio provider singleton."""
    global _twilio_provider
    if _twilio_provider is None:
        _twilio_provider = TwilioProvider()
    return _twilio_provider


async def send_sms(to: str, body: str) -> dict[str, Any]:
    """Send an SMS message."""
    provider = get_twilio_provider()
    return await provider.send_sms(SMSMessage(to=to, body=body))


async def make_voice_call(to: str, message: str) -> dict[str, Any]:
    """Make a voice call with TTS."""
    provider = get_twilio_provider()
    return await provider.make_voice_call(VoiceCall(to=to, message=message))


async def send_whatsapp(to: str, body: str, media_url: str | None = None) -> dict[str, Any]:
    """Send a WhatsApp message."""
    provider = get_twilio_provider()
    return await provider.send_whatsapp(WhatsAppMessage(to=to, body=body, media_url=media_url))
