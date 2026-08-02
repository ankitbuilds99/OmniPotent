"""Email notification provider.

Supports multiple backends:
- SMTP (default, works with any email server)
- SendGrid (transactional email service)
- AWS SES (Simple Email Service)
"""

from __future__ import annotations

import logging
import os
import smtplib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

log = logging.getLogger("omnipotent.notifications.email")


@dataclass
class EmailMessage:
    """Email message structure."""

    to: list[str]
    subject: str
    body_text: str
    body_html: str = ""
    from_email: str = ""
    from_name: str = "DevOps Copilot"
    reply_to: str = ""
    cc: list[str] | None = None
    bcc: list[str] | None = None
    attachments: list[tuple[str, bytes, str]] | None = None


class EmailProvider(ABC):
    """Abstract email provider interface."""

    @abstractmethod
    async def send(self, message: EmailMessage) -> bool:
        """Send an email message."""
        pass

    @abstractmethod
    def is_configured(self) -> bool:
        """Check if the provider is properly configured."""
        pass


class SMTPProvider(EmailProvider):
    """SMTP email provider."""

    def __init__(
        self,
        host: str = "",
        port: int = 587,
        username: str = "",
        password: str = "",
        use_tls: bool = True,
        from_email: str = "",
    ):
        self.host = host or os.environ.get("SMTP_HOST", "")
        self.port = port or int(os.environ.get("SMTP_PORT", "587"))
        self.username = username or os.environ.get("SMTP_USERNAME", "")
        self.password = password or os.environ.get("SMTP_PASSWORD", "")
        self.use_tls = use_tls
        self.from_email = from_email or os.environ.get("SMTP_FROM_EMAIL", "")

    def is_configured(self) -> bool:
        return bool(self.host and self.username and self.password)

    async def send(self, message: EmailMessage) -> bool:
        if not self.is_configured():
            log.warning("SMTP not configured")
            return False

        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = message.subject
            msg["From"] = f"{message.from_name} <{message.from_email or self.from_email}>"
            msg["To"] = ", ".join(message.to)

            if message.reply_to:
                msg["Reply-To"] = message.reply_to
            if message.cc:
                msg["Cc"] = ", ".join(message.cc)

            msg.attach(MIMEText(message.body_text, "plain"))
            if message.body_html:
                msg.attach(MIMEText(message.body_html, "html"))

            with smtplib.SMTP(self.host, self.port) as server:
                if self.use_tls:
                    server.starttls()
                server.login(self.username, self.password)
                recipients = message.to + (message.cc or []) + (message.bcc or [])
                server.sendmail(self.from_email, recipients, msg.as_string())

            log.info("Email sent to %s: %s", message.to, message.subject)
            return True
        except Exception as e:
            log.error("SMTP send failed: %s", e)
            return False


class SendGridProvider(EmailProvider):
    """SendGrid email provider."""

    def __init__(self, api_key: str = "", from_email: str = ""):
        self.api_key = api_key or os.environ.get("SENDGRID_API_KEY", "")
        self.from_email = from_email or os.environ.get("SENDGRID_FROM_EMAIL", "")

    def is_configured(self) -> bool:
        return bool(self.api_key and self.from_email)

    async def send(self, message: EmailMessage) -> bool:
        if not self.is_configured():
            log.warning("SendGrid not configured")
            return False

        try:
            import httpx

            content_list: list[dict[str, str]] = [{"type": "text/plain", "value": message.body_text}]
            if message.body_html:
                content_list.append({"type": "text/html", "value": message.body_html})

            payload: dict[str, Any] = {
                "personalizations": [{"to": [{"email": e} for e in message.to]}],
                "from": {
                    "email": message.from_email or self.from_email,
                    "name": message.from_name,
                },
                "subject": message.subject,
                "content": content_list,
            }

            if message.reply_to:
                payload["reply_to"] = {"email": message.reply_to}

            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    "https://api.sendgrid.com/v3/mail/send",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=30,
                )

            if resp.status_code in (200, 202):
                log.info("SendGrid email sent to %s", message.to)
                return True
            else:
                log.error("SendGrid error: %s", resp.text)
                return False
        except Exception as e:
            log.error("SendGrid send failed: %s", e)
            return False


class SESProvider(EmailProvider):
    """AWS SES email provider."""

    def __init__(
        self,
        region: str = "",
        access_key: str = "",
        secret_key: str = "",
        from_email: str = "",
    ):
        self.region = region or os.environ.get("AWS_REGION", "us-east-1")
        self.access_key = access_key or os.environ.get("AWS_ACCESS_KEY_ID", "")
        self.secret_key = secret_key or os.environ.get("AWS_SECRET_ACCESS_KEY", "")
        self.from_email = from_email or os.environ.get("SES_FROM_EMAIL", "")

    def is_configured(self) -> bool:
        return bool(self.access_key and self.secret_key and self.from_email)

    async def send(self, message: EmailMessage) -> bool:
        if not self.is_configured():
            log.warning("AWS SES not configured")
            return False

        try:
            import boto3

            client = boto3.client(
                "ses",
                region_name=self.region,
                aws_access_key_id=self.access_key,
                aws_secret_access_key=self.secret_key,
            )

            body: dict[str, Any] = {"Text": {"Data": message.body_text}}
            if message.body_html:
                body["Html"] = {"Data": message.body_html}

            client.send_email(
                Source=f"{message.from_name} <{message.from_email or self.from_email}>",
                Destination={
                    "ToAddresses": message.to,
                    "CcAddresses": message.cc or [],
                    "BccAddresses": message.bcc or [],
                },
                Message={
                    "Subject": {"Data": message.subject},
                    "Body": body,
                },
            )

            log.info("SES email sent to %s", message.to)
            return True
        except Exception as e:
            log.error("SES send failed: %s", e)
            return False


_provider: EmailProvider | None = None


def get_email_provider() -> EmailProvider:
    """Get the configured email provider."""
    global _provider

    if _provider is not None:
        return _provider

    if os.environ.get("SENDGRID_API_KEY"):
        _provider = SendGridProvider()
    elif os.environ.get("AWS_SES_ENABLED"):
        _provider = SESProvider()
    else:
        _provider = SMTPProvider()

    return _provider


async def send_email(
    to: str | list[str],
    subject: str,
    body_text: str,
    body_html: str = "",
    **kwargs: Any,
) -> bool:
    """Send an email using the configured provider."""
    if isinstance(to, str):
        to = [to]

    message = EmailMessage(
        to=to,
        subject=subject,
        body_text=body_text,
        body_html=body_html,
        **kwargs,
    )

    provider = get_email_provider()
    return await provider.send(message)


async def send_investigation_complete(
    to: str,
    investigation_id: str,
    summary: str,
    severity: str,
    report_url: str,
) -> bool:
    """Send investigation completion notification."""
    severity_colors = {
        "critical": "#dc2626",
        "high": "#ea580c",
        "medium": "#ca8a04",
        "low": "#16a34a",
    }
    color = severity_colors.get(severity.lower(), "#6b7280")

    body_text = f"""
Investigation Complete: {investigation_id}

Summary: {summary}

Severity: {severity.upper()}

View the full report: {report_url}

---
DevOps Copilot
"""

    body_html = f"""
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 20px; border-radius: 8px 8px 0 0; }}
        .content {{ background: #f9fafb; padding: 20px; border: 1px solid #e5e7eb; }}
        .severity {{ display: inline-block; padding: 4px 12px; border-radius: 4px; color: white; font-weight: bold; background: {color}; }}
        .btn {{ display: inline-block; padding: 12px 24px; background: #4f46e5; color: white; text-decoration: none; border-radius: 6px; margin-top: 16px; }}
        .footer {{ padding: 16px; text-align: center; color: #6b7280; font-size: 12px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1 style="margin: 0;">Investigation Complete</h1>
            <p style="margin: 8px 0 0 0; opacity: 0.9;">{investigation_id}</p>
        </div>
        <div class="content">
            <h2 style="margin-top: 0;">Summary</h2>
            <p>{summary}</p>
            <p><strong>Severity:</strong> <span class="severity">{severity.upper()}</span></p>
            <a href="{report_url}" class="btn">View Full Report</a>
        </div>
        <div class="footer">
            <p>Sent by DevOps Copilot</p>
        </div>
    </div>
</body>
</html>
"""

    return await send_email(
        to=to,
        subject=f"[{severity.upper()}] Investigation Complete: {investigation_id}",
        body_text=body_text,
        body_html=body_html,
    )


async def send_alert_notification(
    to: str,
    alert_name: str,
    service: str,
    severity: str,
    message: str,
    dashboard_url: str,
) -> bool:
    """Send alert notification email."""
    body_text = f"""
Alert: {alert_name}

Service: {service}
Severity: {severity}

{message}

View dashboard: {dashboard_url}

---
DevOps Copilot
"""

    body_html = f"""
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }}
        .alert-box {{ background: #fef2f2; border-left: 4px solid #dc2626; padding: 16px; margin: 16px 0; }}
    </style>
</head>
<body>
    <h1>🚨 Alert: {alert_name}</h1>
    <div class="alert-box">
        <p><strong>Service:</strong> {service}</p>
        <p><strong>Severity:</strong> {severity.upper()}</p>
        <p>{message}</p>
    </div>
    <a href="{dashboard_url}">View Dashboard</a>
</body>
</html>
"""

    return await send_email(
        to=to,
        subject=f"[ALERT] {alert_name} - {service}",
        body_text=body_text,
        body_html=body_html,
    )


async def send_approval_request(
    to: str,
    action: str,
    details: str,
    requester: str,
    approve_url: str,
    reject_url: str,
) -> bool:
    """Send approval request email."""
    body_text = f"""
Approval Required: {action}

Requested by: {requester}

Details:
{details}

To approve: {approve_url}
To reject: {reject_url}

---
DevOps Copilot
"""

    body_html = f"""
<!DOCTYPE html>
<html>
<body>
    <h1>⚠️ Approval Required</h1>
    <h2>{action}</h2>
    <p><strong>Requested by:</strong> {requester}</p>
    <pre style="background: #f3f4f6; padding: 16px; border-radius: 8px;">{details}</pre>
    <div style="margin-top: 20px;">
        <a href="{approve_url}" style="background: #16a34a; color: white; padding: 12px 24px; text-decoration: none; border-radius: 6px; margin-right: 8px;">Approve</a>
        <a href="{reject_url}" style="background: #dc2626; color: white; padding: 12px 24px; text-decoration: none; border-radius: 6px;">Reject</a>
    </div>
</body>
</html>
"""

    return await send_email(
        to=to,
        subject=f"[ACTION REQUIRED] Approval needed: {action}",
        body_text=body_text,
        body_html=body_html,
    )


async def send_welcome_email(to: str, name: str, login_url: str) -> bool:
    """Send welcome email to new users."""
    body_text = f"""
Welcome to DevOps Copilot, {name}!

Your account has been created successfully.

Get started: {login_url}

What you can do:
- Investigate incidents with AI assistance
- Get root cause analysis in minutes
- Automate runbooks and remediation

Need help? Check out our documentation or reach out to support.

---
The DevOps Copilot Team
"""

    body_html = f"""
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f9fafb; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .card {{ background: white; border-radius: 12px; padding: 32px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }}
        .btn {{ display: inline-block; padding: 14px 28px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; text-decoration: none; border-radius: 8px; font-weight: bold; }}
        .features {{ background: #f3f4f6; padding: 20px; border-radius: 8px; margin: 20px 0; }}
        .feature {{ display: flex; align-items: center; margin: 12px 0; }}
        .feature-icon {{ margin-right: 12px; font-size: 24px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="card">
            <h1 style="color: #1f2937;">Welcome to DevOps Copilot! 🎉</h1>
            <p style="font-size: 18px; color: #4b5563;">Hi {name},</p>
            <p>Your account is ready. Start investigating incidents with AI-powered assistance.</p>

            <div style="text-align: center; margin: 32px 0;">
                <a href="{login_url}" class="btn">Get Started</a>
            </div>

            <div class="features">
                <h3 style="margin-top: 0;">What you can do:</h3>
                <div class="feature"><span class="feature-icon">🔍</span> Investigate incidents with AI</div>
                <div class="feature"><span class="feature-icon">📊</span> Get root cause analysis in minutes</div>
                <div class="feature"><span class="feature-icon">⚡</span> Automate runbooks and remediation</div>
            </div>
        </div>
    </div>
</body>
</html>
"""

    return await send_email(
        to=to,
        subject="Welcome to DevOps Copilot! 🚀",
        body_text=body_text,
        body_html=body_html,
    )
