"""Notification system for multi-channel alerts.

Supports:
- Email (SMTP, SendGrid, AWS SES)
- Push notifications (FCM, APNs)
- SMS (Twilio)
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
from app.notifications.dispatcher import (
    NotificationDispatcher,
    NotificationChannel,
    NotificationPriority,
    get_dispatcher,
    notify,
)

__all__ = [
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
    "PushProvider",
    "FCMProvider",
    "APNsProvider",
    "get_push_provider",
    "send_push_notification",
    "register_device",
    "unregister_device",
    "NotificationDispatcher",
    "NotificationChannel",
    "NotificationPriority",
    "get_dispatcher",
    "notify",
]
