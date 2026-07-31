"""Email notification service for team invites and alerts."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.config import get_settings

log = logging.getLogger("devcopilot.email")


@dataclass
class EmailMessage:
    to: str
    subject: str
    html_body: str
    text_body: str | None = None


class EmailService:
    """Email service with multiple provider support."""

    def __init__(self):
        self.settings = get_settings()
        self._client = None

    def _get_client(self):
        """Get email client based on configuration."""
        if self._client:
            return self._client

        provider = getattr(self.settings, "email_provider", "smtp")

        if provider == "ses":
            return self._get_ses_client()
        elif provider == "sendgrid":
            return self._get_sendgrid_client()
        elif provider == "resend":
            return self._get_resend_client()
        else:
            return self._get_smtp_client()

    def _get_ses_client(self):
        """Get AWS SES client."""
        try:
            import boto3
            return boto3.client("ses", region_name=self.settings.aws_region)
        except Exception as e:
            log.error("Failed to create SES client: %s", e)
            return None

    def _get_sendgrid_client(self):
        """Get SendGrid client."""
        try:
            from sendgrid import SendGridAPIClient
            api_key = getattr(self.settings, "sendgrid_api_key", "")
            return SendGridAPIClient(api_key)
        except Exception as e:
            log.error("Failed to create SendGrid client: %s", e)
            return None

    def _get_resend_client(self):
        """Get Resend client."""
        try:
            import resend
            resend.api_key = getattr(self.settings, "resend_api_key", "")
            return resend
        except Exception as e:
            log.error("Failed to create Resend client: %s", e)
            return None

    def _get_smtp_client(self):
        """Get SMTP client."""
        return "smtp"

    async def send(self, message: EmailMessage) -> bool:
        """Send an email."""
        provider = getattr(self.settings, "email_provider", "smtp")

        try:
            if provider == "ses":
                return await self._send_ses(message)
            elif provider == "sendgrid":
                return await self._send_sendgrid(message)
            elif provider == "resend":
                return await self._send_resend(message)
            else:
                return await self._send_smtp(message)
        except Exception as e:
            log.error("Failed to send email: %s", e)
            return False

    async def _send_ses(self, message: EmailMessage) -> bool:
        """Send via AWS SES."""
        client = self._get_ses_client()
        if not client:
            return False

        try:
            from_email = getattr(self.settings, "email_from", "noreply@devops-copilot.io")
            client.send_email(
                Source=from_email,
                Destination={"ToAddresses": [message.to]},
                Message={
                    "Subject": {"Data": message.subject},
                    "Body": {
                        "Html": {"Data": message.html_body},
                        "Text": {"Data": message.text_body or message.subject},
                    },
                },
            )
            log.info("Email sent via SES to %s", message.to)
            return True
        except Exception as e:
            log.error("SES send failed: %s", e)
            return False

    async def _send_sendgrid(self, message: EmailMessage) -> bool:
        """Send via SendGrid."""
        client = self._get_sendgrid_client()
        if not client:
            return False

        try:
            from sendgrid.helpers.mail import Mail

            from_email = getattr(self.settings, "email_from", "noreply@devops-copilot.io")
            mail = Mail(
                from_email=from_email,
                to_emails=message.to,
                subject=message.subject,
                html_content=message.html_body,
            )
            client.send(mail)
            log.info("Email sent via SendGrid to %s", message.to)
            return True
        except Exception as e:
            log.error("SendGrid send failed: %s", e)
            return False

    async def _send_resend(self, message: EmailMessage) -> bool:
        """Send via Resend."""
        client = self._get_resend_client()
        if not client:
            return False

        try:
            from_email = getattr(self.settings, "email_from", "noreply@devops-copilot.io")
            client.Emails.send({
                "from": from_email,
                "to": message.to,
                "subject": message.subject,
                "html": message.html_body,
            })
            log.info("Email sent via Resend to %s", message.to)
            return True
        except Exception as e:
            log.error("Resend send failed: %s", e)
            return False

    async def _send_smtp(self, message: EmailMessage) -> bool:
        """Send via SMTP."""
        import smtplib

        smtp_host = getattr(self.settings, "smtp_host", "localhost")
        smtp_port = getattr(self.settings, "smtp_port", 587)
        smtp_user = getattr(self.settings, "smtp_user", "")
        smtp_pass = getattr(self.settings, "smtp_pass", "")
        from_email = getattr(self.settings, "email_from", "noreply@devops-copilot.io")

        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = message.subject
            msg["From"] = from_email
            msg["To"] = message.to

            if message.text_body:
                msg.attach(MIMEText(message.text_body, "plain"))
            msg.attach(MIMEText(message.html_body, "html"))

            with smtplib.SMTP(smtp_host, smtp_port) as server:
                if smtp_user and smtp_pass:
                    server.starttls()
                    server.login(smtp_user, smtp_pass)
                server.send_message(msg)

            log.info("Email sent via SMTP to %s", message.to)
            return True
        except Exception as e:
            log.error("SMTP send failed: %s", e)
            return False


_email_service: EmailService | None = None


def get_email_service() -> EmailService:
    global _email_service
    if _email_service is None:
        _email_service = EmailService()
    return _email_service


async def send_team_invite(
    email: str,
    team_name: str,
    inviter_name: str,
    invite_link: str,
    role: str,
) -> bool:
    """Send team invitation email."""
    service = get_email_service()

    html_body = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }}
            .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
            .header {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 30px; border-radius: 8px 8px 0 0; }}
            .content {{ background: #f8f9fa; padding: 30px; border-radius: 0 0 8px 8px; }}
            .button {{ display: inline-block; background: #667eea; color: white; padding: 12px 24px; text-decoration: none; border-radius: 6px; margin: 20px 0; }}
            .footer {{ color: #6c757d; font-size: 12px; margin-top: 20px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>You're invited to join {team_name}</h1>
            </div>
            <div class="content">
                <p>Hi there,</p>
                <p><strong>{inviter_name}</strong> has invited you to join <strong>{team_name}</strong> on DevOps Copilot as a <strong>{role}</strong>.</p>
                <p>DevOps Copilot is an AI-powered platform for incident investigation and resolution.</p>
                <a href="{invite_link}" class="button">Accept Invitation</a>
                <p class="footer">
                    This invitation will expire in 7 days.<br>
                    If you didn't expect this invitation, you can safely ignore this email.
                </p>
            </div>
        </div>
    </body>
    </html>
    """

    text_body = f"""
    You're invited to join {team_name}

    {inviter_name} has invited you to join {team_name} on DevOps Copilot as a {role}.

    Accept the invitation: {invite_link}

    This invitation will expire in 7 days.
    """

    message = EmailMessage(
        to=email,
        subject=f"You're invited to join {team_name} on DevOps Copilot",
        html_body=html_body,
        text_body=text_body,
    )

    return await service.send(message)


async def send_incident_notification(
    email: str,
    incident_title: str,
    severity: str,
    service: str,
    incident_link: str,
) -> bool:
    """Send incident notification email."""
    service_obj = get_email_service()

    severity_colors = {
        "critical": "#dc3545",
        "high": "#fd7e14",
        "medium": "#ffc107",
        "low": "#28a745",
    }
    color = severity_colors.get(severity.lower(), "#6c757d")

    html_body = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }}
            .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
            .header {{ background: {color}; color: white; padding: 20px; border-radius: 8px 8px 0 0; }}
            .content {{ background: #f8f9fa; padding: 20px; border-radius: 0 0 8px 8px; }}
            .badge {{ display: inline-block; padding: 4px 12px; border-radius: 4px; font-size: 12px; font-weight: bold; text-transform: uppercase; }}
            .button {{ display: inline-block; background: #667eea; color: white; padding: 10px 20px; text-decoration: none; border-radius: 6px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <span class="badge" style="background: white; color: {color};">{severity.upper()}</span>
                <h2 style="margin: 10px 0 0 0;">{incident_title}</h2>
            </div>
            <div class="content">
                <p><strong>Service:</strong> {service}</p>
                <p>An incident has been triggered and requires attention.</p>
                <a href="{incident_link}" class="button">View Incident</a>
            </div>
        </div>
    </body>
    </html>
    """

    message = EmailMessage(
        to=email,
        subject=f"[{severity.upper()}] {incident_title}",
        html_body=html_body,
        text_body=f"[{severity.upper()}] {incident_title}\n\nService: {service}\n\nView: {incident_link}",
    )

    return await service_obj.send(message)
