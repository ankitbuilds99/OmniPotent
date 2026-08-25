"""A status page subscription that is actually delivered, or is refused.

The subscribe endpoint answered 201 "Please check your email to verify" and
then did nothing: the handler carried a `# TODO: Send verification email` and
the verification token — generated in the store and never returned to anyone —
stayed on the server. Since `notify_subscribers` skips `verified=False`, every
address collected through the public form was inert forever, and the response
said the opposite.

These tests drive the whole loop over HTTP against a real SMTP server (a
throwaway one, on a socket, in a thread — the production path is unmodified
`SMTPProvider`): subscribe, read the link out of the message that arrived,
open it, and check the now-verified subscriber is the one an incident reaches.
They also pin the two honest refusals: no email provider configured is a 503
that names the configuration, and a verification email that did not leave the
building is a 502 with nothing stored, not a 201.
"""

from __future__ import annotations

import asyncio
import email
import re
import socket
import socketserver
import threading
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import app.api.main as api
import app.config as cfg
import app.notifications.email as email_mod
from app import runtime, tenant_context
from app.database import engine as db_engine
from app.notifications.email import SMTPProvider
from app.status_page.models import Subscriber, SubscriptionType
from app.status_page.notifications import NotificationPayload, StatusNotifier


# --- a real (if minimal) SMTP server -----------------------------------------


class _SMTPHandler(socketserver.StreamRequestHandler):
    """Enough of RFC 5321 for smtplib to complete a session."""

    def handle(self) -> None:  # noqa: C901 - a protocol loop is a protocol loop
        self.wfile.write(b"220 test.local ESMTP\r\n")
        in_data = False
        body: list[bytes] = []
        while True:
            line = self.rfile.readline()
            if not line:
                return
            if in_data:
                if line.strip() == b".":
                    in_data = False
                    self.server.messages.append(b"".join(body).decode("utf-8", "replace"))
                    body = []
                    self.wfile.write(b"250 2.0.0 OK\r\n")
                else:
                    body.append(line)
                continue

            command = line.decode("utf-8", "replace").strip()
            verb = command.upper()
            if verb.startswith("EHLO"):
                self.wfile.write(b"250-test.local\r\n250 AUTH PLAIN LOGIN\r\n")
            elif verb.startswith("HELO"):
                self.wfile.write(b"250 test.local\r\n")
            elif verb.startswith("AUTH"):
                self.server.authenticated = True
                self.wfile.write(b"235 2.7.0 Authentication successful\r\n")
            elif verb.startswith("RCPT"):
                self.server.recipients.append(command.split(":", 1)[-1].strip())
                self.wfile.write(b"250 2.1.5 OK\r\n")
            elif verb == "DATA":
                in_data = True
                self.wfile.write(b"354 End data with <CR><LF>.<CR><LF>\r\n")
            elif verb == "QUIT":
                self.wfile.write(b"221 2.0.0 Bye\r\n")
                return
            else:  # MAIL FROM, RSET, NOOP
                self.wfile.write(b"250 2.0.0 OK\r\n")


class _SMTPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.messages: list[str] = []
        self.recipients: list[str] = []
        self.authenticated = False


@pytest.fixture
def smtp():
    server = _SMTPServer(("127.0.0.1", 0), _SMTPHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _use_smtp(monkeypatch, host: str, port: int) -> None:
    """Point the real SMTPProvider at a host, as SMTP_* env vars would."""
    monkeypatch.setattr(
        email_mod,
        "_provider",
        SMTPProvider(
            host=host,
            port=port,
            username="status",
            password="secret",
            use_tls=False,
            from_email="status@test.local",
        ),
    )


@pytest.fixture
def no_email_provider(monkeypatch):
    """A deployment that never configured email."""
    for var in (
        "SENDGRID_API_KEY",
        "AWS_SES_ENABLED",
        "SMTP_HOST",
        "SMTP_USERNAME",
        "SMTP_PASSWORD",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(email_mod, "_provider", None)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("COPILOT_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/subs.db")
    cfg.get_settings.cache_clear()
    tenant_context.current_tenant.set(None)
    api._RL.clear()
    api._reset_limiter()
    runtime.reset()
    db_engine._engine = None
    db_engine._session_factory = None

    with TestClient(api.app) as test_client:
        yield test_client

    cfg.get_settings.cache_clear()
    tenant_context.current_tenant.set(None)
    api._RL.clear()
    api._reset_limiter()
    runtime.reset()
    db_engine._engine = None
    db_engine._session_factory = None


_LINK = re.compile(r"https?://[^\s\"<>]*/api/status/subscribe/verify\?[^\s\"<>]+")


def _verification_link(message: str) -> str:
    """The link as a subscriber's mail client would see it.

    Read out of the text/plain alternative of the real MIME message that came
    down the socket, decoded — not scraped off the wire — so the test is
    reading what was actually delivered.
    """
    parsed = email.message_from_string(message)
    for part in parsed.walk():
        if part.get_content_type() != "text/plain":
            continue
        payload = part.get_payload(decode=True)
        assert payload is not None
        links = _LINK.findall(payload.decode(part.get_content_charset() or "utf-8"))
        assert links, f"no verification link in the delivered message:\n{message}"
        return links[0]
    raise AssertionError(f"no text/plain part in the delivered message:\n{message}")


def _closed_port() -> int:
    """A port with nothing listening on it."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


# --- subscribe ---------------------------------------------------------------


def test_the_verification_link_is_emailed_and_verifies_the_subscription(
    client, smtp, monkeypatch
):
    """The whole loop, end to end.

    Before this, the POST returned 201 "check your email", no mail was ever
    sent, and the token existed only in the database — so `verify` could only
    ever be reached by guessing 32 random bytes.
    """
    _use_smtp(monkeypatch, *smtp.server_address)

    created = client.post("/api/status/subscribe", json={"email": "sre@example.com"})
    assert created.status_code == 201, created.text
    assert created.json()["verified"] is False

    assert len(smtp.messages) == 1, "no verification email was delivered"
    assert smtp.recipients == ["<sre@example.com>"]
    assert "Confirm your status page subscription" in smtp.messages[0]

    verified = client.get(_verification_link(smtp.messages[0]))
    assert verified.status_code == 200, verified.text
    assert verified.json()["status"] == "verified"

    # The subscription is now verified as far as the API is concerned...
    again = client.post("/api/status/subscribe", json={"email": "sre@example.com"})
    assert again.json()["verified"] is True

    # ...and the link is single use.
    assert client.get(_verification_link(smtp.messages[0])).status_code == 400


def test_the_verification_link_prefers_the_configured_status_page_url(
    client, smtp, monkeypatch
):
    """The Host header is client-controlled and this link carries a token."""
    _use_smtp(monkeypatch, *smtp.server_address)
    monkeypatch.setenv("STATUS_PAGE_URL", "https://status.example.com")
    cfg.get_settings.cache_clear()

    created = client.post(
        "/api/status/subscribe",
        json={"email": "sre@example.com"},
        headers={"Host": "attacker.example"},
    )
    assert created.status_code == 201, created.text

    link = _verification_link(smtp.messages[0])
    assert link.startswith("https://status.example.com/api/status/subscribe/verify?")
    assert "attacker.example" not in link
    assert client.get(link).status_code == 200


def test_only_the_verified_subscriber_is_told_about_an_incident(client, smtp, monkeypatch):
    """Verification is not decoration: it is the gate on every later notification."""
    _use_smtp(monkeypatch, *smtp.server_address)

    client.post("/api/status/subscribe", json={"email": "verified@example.com"})
    client.post("/api/status/subscribe", json={"email": "pending@example.com"})
    assert len(smtp.messages) == 2

    verify_first = _verification_link(smtp.messages[0])
    assert client.get(verify_first).status_code == 200
    smtp.messages.clear()
    smtp.recipients.clear()

    incident = client.post(
        "/api/status/incidents",
        json={"title": "Elevated error rate", "message": "Investigating", "severity": "major"},
    )
    assert incident.status_code == 201, incident.text

    assert smtp.recipients == ["<verified@example.com>"]
    assert "[Incident] Elevated error rate" in smtp.messages[0]


def test_email_subscriptions_are_refused_when_no_provider_is_configured(
    client, smtp, monkeypatch, no_email_provider
):
    """503 naming the configuration, not 201 and a promise of an email."""
    refused = client.post("/api/status/subscribe", json={"email": "sre@example.com"})

    assert refused.status_code == 503, refused.text
    detail = refused.json()["detail"]
    assert "no email provider is configured" in detail
    assert "SMTP_HOST" in detail and "SENDGRID_API_KEY" in detail
    assert smtp.messages == []

    # Nothing was stored either: with a provider in place the same address is a
    # fresh subscription rather than "already subscribed".
    _use_smtp(monkeypatch, *smtp.server_address)
    accepted = client.post("/api/status/subscribe", json={"email": "sre@example.com"})
    assert accepted.status_code == 201
    assert "Check your email" in accepted.json()["message"]
    assert len(smtp.messages) == 1


def test_webhook_subscriptions_still_work_without_an_email_provider(
    client, no_email_provider
):
    """The refusal is scoped to the channel that needs the mail transport."""
    created = client.post(
        "/api/status/subscribe", json={"webhook_url": "https://example.com/hook"}
    )

    assert created.status_code == 201, created.text
    assert created.json()["verified"] is True


def test_a_subscription_whose_email_bounced_is_not_kept(client, smtp, monkeypatch):
    """The token only leaves the server in that email.

    If it did not go out, the row can never be verified and never notified, so
    keeping it means storing a customer's address for nothing while telling
    them it worked.
    """
    _use_smtp(monkeypatch, "127.0.0.1", _closed_port())

    failed = client.post("/api/status/subscribe", json={"email": "sre@example.com"})
    assert failed.status_code == 502, failed.text
    assert "could not be delivered" in failed.json()["detail"]

    _use_smtp(monkeypatch, *smtp.server_address)
    retried = client.post("/api/status/subscribe", json={"email": "sre@example.com"})
    assert retried.status_code == 201
    assert "Check your email" in retried.json()["message"]
    assert len(smtp.messages) == 1


def test_an_unverified_subscription_is_not_told_to_check_a_mail_it_will_not_get(
    client, smtp, monkeypatch
):
    _use_smtp(monkeypatch, *smtp.server_address)
    client.post("/api/status/subscribe", json={"email": "sre@example.com"})

    repeat = client.post("/api/status/subscribe", json={"email": "sre@example.com"})

    assert repeat.status_code == 201
    assert repeat.json()["message"] == (
        "Already subscribed, but not verified yet. Open the verification "
        "link already emailed to this address."
    )
    # And no second email: the form is public, so it cannot be a way to mail an
    # address over and over.
    assert len(smtp.messages) == 1


# --- what the notifier reports -----------------------------------------------


def _subscriber(sub_id: str, target: str, verified: bool) -> Subscriber:
    return Subscriber(
        id=sub_id,
        subscription_type=SubscriptionType.EMAIL,
        target=target,
        verified=verified,
        created_at=datetime.now(timezone.utc),
    )


def _payload() -> NotificationPayload:
    return NotificationPayload(
        event_type="incident_created",
        title="Elevated error rate",
        message="Investigating",
    )


def test_a_delivery_result_belongs_to_the_subscriber_it_was_sent_to(smtp, monkeypatch):
    """Skipped subscribers used to shift every result onto the wrong id.

    The send list is filtered (unverified, muted preferences, component
    filters) but the results were zipped against the unfiltered list, so with
    one skipped subscriber ahead of it a delivered email was recorded against
    the subscriber that was never contacted.
    """
    _use_smtp(monkeypatch, *smtp.server_address)
    subscribers = [
        _subscriber("sub_skipped", "pending@example.com", verified=False),
        _subscriber("sub_sent", "verified@example.com", verified=True),
    ]

    result = asyncio.run(StatusNotifier().notify_subscribers(subscribers, _payload()))

    assert result == {"success": ["sub_sent"], "failed": []}
    assert smtp.recipients == ["<verified@example.com>"]


def test_an_email_that_was_never_sent_is_not_reported_as_delivered(no_email_provider):
    """`_send_email` discarded the provider's answer and always returned True."""
    subscribers = [_subscriber("sub_sent", "verified@example.com", verified=True)]

    result = asyncio.run(StatusNotifier().notify_subscribers(subscribers, _payload()))

    assert result == {"success": [], "failed": ["sub_sent"]}
