"""Notification system for multi-channel alerts.

Supports:
- Email (SMTP, SendGrid, AWS SES)
- Push notifications (FCM, APNs)
- SMS (Twilio)
- Voice Calls (Twilio)
- WhatsApp (Twilio)
- Discord (Webhooks)
- Telegram (Bot API)
- Webhook callbacks
"""

from app.notifications.email import (
    EmailProvider,
    SMTPProvider,
    SendGridProvider,
    SESProvider,
    get_email_provider,
    send_email,
    send_investigation_complete,
    send_alert_notification,
    send_approval_request,
    send_welcome_email,
)
from app.notifications.push import (
    PushProvider,
    FCMProvider,
    APNsProvider,
    get_push_provider,
    send_push_notification,
    register_device,
    unregister_device,
)
from app.notifications.sms import (
    TwilioProvider,
    SMSMessage,
    VoiceCall,
    WhatsAppMessage,
    get_twilio_provider,
    send_sms,
    make_voice_call,
    send_whatsapp,
)
from app.notifications.discord import (
    DiscordWebhook,
    DiscordEmbed,
    DiscordMessage,
    DiscordColor,
    get_discord_webhook,
    send_discord_alert,
)
from app.notifications.telegram import (
    TelegramBot,
    TelegramMessage,
    InlineButton,
    InlineKeyboard,
    get_telegram_bot,
    send_telegram_alert,
)
from app.notifications.dispatcher import (
    NotificationDispatcher,
    NotificationChannel,
    NotificationPriority,
    get_dispatcher,
    notify,
)

__all__ = [
    # Email
    "EmailProvider",
    "SMTPProvider",
    "SendGridProvider",
    "SESProvider",
    "get_email_provider",
    "send_email",
    "send_investigation_complete",
    "send_alert_notification",
    "send_approval_request",
    "send_welcome_email",
    # Push
    "PushProvider",
    "FCMProvider",
    "APNsProvider",
    "get_push_provider",
    "send_push_notification",
    "register_device",
    "unregister_device",
    # SMS/Voice/WhatsApp
    "TwilioProvider",
    "SMSMessage",
    "VoiceCall",
    "WhatsAppMessage",
    "get_twilio_provider",
    "send_sms",
    "make_voice_call",
    "send_whatsapp",
    # Discord
    "DiscordWebhook",
    "DiscordEmbed",
    "DiscordMessage",
    "DiscordColor",
    "get_discord_webhook",
    "send_discord_alert",
    # Telegram
    "TelegramBot",
    "TelegramMessage",
    "InlineButton",
    "InlineKeyboard",
    "get_telegram_bot",
    "send_telegram_alert",
    # Dispatcher
    "NotificationDispatcher",
    "NotificationChannel",
    "NotificationPriority",
    "get_dispatcher",
    "notify",
]
