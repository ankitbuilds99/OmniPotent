"""Integration tests for Slack bot functionality."""

from __future__ import annotations

import json
from urllib.parse import urlencode

import pytest
from httpx import AsyncClient

from tests.integration.conftest import slack_headers

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


class TestSlackWebhooks:
    """The Slack routes, at the paths the router is actually mounted on.

    These used to post to `/slack/events`, `/slack/commands` and
    `/slack/interactions`. The router is mounted at `/api/slack` and has no
    generic `commands` route, so all three 405'd against the SPA catch-all —
    and the assertions allowed 404, so two of them would have passed against
    nothing at all. Nobody noticed because CI never ran this directory.

    They also sent no signature. Every route here carries a router-wide
    `enforce_slack_signature` dependency that fails closed, so an unsigned
    request is rejected by design; the tests sign like Slack does, and one
    asserts that skipping the signature is refused.
    """

    async def test_unsigned_request_is_refused(self, client: AsyncClient, slack_secret: str):
        """The signature gate is load-bearing: no signature, no handler."""
        response = await client.post(
            "/api/slack/events",
            json={"type": "url_verification", "challenge": "c"},
        )
        assert response.status_code == 401

    async def test_slack_is_unreachable_when_no_secret_is_configured(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ):
        """An unset secret must not degrade to "accept everything"."""
        import app.config as cfg

        monkeypatch.delenv("SLACK_SIGNING_SECRET", raising=False)
        cfg.get_settings.cache_clear()
        try:
            response = await client.post(
                "/api/slack/events",
                json={"type": "url_verification", "challenge": "c"},
            )
            assert response.status_code == 503
        finally:
            cfg.get_settings.cache_clear()

    async def test_slack_events_url_verification(self, client: AsyncClient, slack_secret: str):
        """Slack URL verification challenge should be echoed back."""
        body = json.dumps(
            {
                "type": "url_verification",
                "token": "test_token",
                "challenge": "test_challenge_string",
            }
        ).encode()

        response = await client.post(
            "/api/slack/events",
            content=body,
            headers={"Content-Type": "application/json", **slack_headers(body)},
        )
        assert response.status_code == 200
        assert response.json().get("challenge") == "test_challenge_string"

    async def test_slack_events_message(self, client: AsyncClient, slack_secret: str):
        """Slack message events should be accepted."""
        body = json.dumps(
            {
                "type": "event_callback",
                "event": {
                    "type": "message",
                    "channel": "C12345",
                    "user": "U12345",
                    "text": "Hello bot",
                    "ts": "1234567890.123456",
                },
                "team_id": "T12345",
                "event_id": "Ev12345",
            }
        ).encode()

        response = await client.post(
            "/api/slack/events",
            content=body,
            headers={"Content-Type": "application/json", **slack_headers(body)},
        )
        assert response.status_code == 200

    async def test_slack_commands(self, client: AsyncClient, slack_secret: str):
        """Slash commands are mounted per command, not on one generic route."""
        body = urlencode(
            {
                "command": "/copilot",
                "text": "help",
                "user_id": "U12345",
                "channel_id": "C12345",
                "response_url": "https://hooks.slack.com/commands/test",
                "trigger_id": "12345.67890",
            }
        ).encode()

        response = await client.post(
            "/api/slack/commands/help",
            content=body,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                **slack_headers(body),
            },
        )
        assert response.status_code == 200

    async def test_slack_interactions(self, client: AsyncClient, slack_secret: str):
        """Slack interactive components should be handled."""
        payload = json.dumps({
            "type": "block_actions",
            "user": {"id": "U12345", "name": "testuser"},
            "channel": {"id": "C12345"},
            "actions": [
                {
                    "action_id": "investigate_INC123",
                    "type": "button",
                    "value": "clicked",
                }
            ],
            "response_url": "https://hooks.slack.com/actions/test",
            "trigger_id": "12345.67890",
        })
        body = urlencode({"payload": payload}).encode()

        response = await client.post(
            "/api/slack/interactivity",
            content=body,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                **slack_headers(body),
            },
        )
        assert response.status_code == 200


class TestSlackClient:
    """Test Slack client functionality (mocked)."""

    async def test_slack_client_initialization(self):
        """Slack client should initialize without errors."""
        from app.slack_bot.client import SlackClientConfig, SlackClient

        config = SlackClientConfig(
            bot_token="xoxb-test-token",
            signing_secret="test_secret",
        )
        client = SlackClient(config)
        assert client.config.bot_token == "xoxb-test-token"

    async def test_signature_verification(self):
        """Signature verification should work correctly."""
        import hashlib
        import hmac
        import time

        from app.slack_bot.client import SlackClientConfig, SlackClient

        secret = "test_signing_secret"
        config = SlackClientConfig(
            bot_token="xoxb-test",
            signing_secret=secret,
        )
        client = SlackClient(config)

        # Create valid signature
        timestamp = str(int(time.time()))
        body = b'{"test": "data"}'
        sig_basestring = f"v0:{timestamp}:{body.decode()}"
        signature = "v0=" + hmac.new(
            secret.encode(),
            sig_basestring.encode(),
            hashlib.sha256,
        ).hexdigest()

        assert client.verify_signature(signature, timestamp, body) is True

    async def test_signature_verification_invalid(self):
        """Invalid signatures should be rejected."""
        from app.slack_bot.client import SlackClientConfig, SlackClient

        config = SlackClientConfig(
            bot_token="xoxb-test",
            signing_secret="test_secret",
        )
        client = SlackClient(config)

        # Invalid signature
        assert client.verify_signature(
            "v0=invalid",
            str(int(__import__('time').time())),
            b'{"test": "data"}',
        ) is False


class TestSlackBlockBuilder:
    """Test Slack Block Kit builder."""

    async def test_investigation_card(self):
        """Build investigation card blocks."""
        from app.slack_bot.blocks import get_block_builder

        builder = get_block_builder()
        blocks = builder.investigation_card(
            incident_id="INC-123",
            title="Test Incident",
            status="Investigating",
            duration="5m",
            severity="P2",
            responders=["user1", "user2"],
            services=["checkout-api"],
            root_cause="Connection leak",
            confidence=0.85,
            evidence_count=5,
        )

        assert len(blocks) > 0
        assert any(b["type"] == "header" for b in blocks)

    async def test_approval_card(self):
        """Build approval request card."""
        from app.slack_bot.blocks import get_block_builder

        builder = get_block_builder()
        blocks = builder.approval_card(
            approval_id="apr_123",
            action_type="Rollback",
            description="Rollback checkout-api to v1.2.3",
            details={"service": "checkout-api", "version": "v1.2.3"},
            risk_level="medium",
            requested_by="user123",
        )

        assert len(blocks) > 0
        # Should have approve/reject buttons
        actions = [b for b in blocks if b["type"] == "actions"]
        assert len(actions) > 0

    async def test_timeline_card(self):
        """Build timeline card."""
        from app.slack_bot.blocks import get_block_builder

        builder = get_block_builder()
        blocks = builder.timeline_card(
            incident_id="INC-123",
            events=[
                {"time": "14:00", "event": "Alert fired"},
                {"time": "14:05", "event": "Investigation started"},
                {"time": "14:10", "event": "Root cause identified"},
            ],
        )

        assert len(blocks) > 0


class TestWarRoom:
    """Test war room management."""

    async def test_create_war_room(self):
        """Create a war room."""
        from app.slack_bot.war_room import get_war_room_manager

        manager = get_war_room_manager()
        war_room = await manager.create_war_room(
            incident_id="INC-123",
            org_id="org_test",
            title="Test Incident",
            severity="P2",
            services=["checkout-api"],
        )

        assert war_room.incident_id == "INC-123"
        assert war_room.severity == "P2"
        assert "checkout-api" in war_room.services

    async def test_war_room_lookup(self):
        """Look up war room by incident."""
        from app.slack_bot.war_room import get_war_room_manager

        manager = get_war_room_manager()

        # Create first
        await manager.create_war_room(
            incident_id="INC-456",
            org_id="org_test",
            title="Another Incident",
            severity="P1",
        )

        # Look up
        found = manager.get_by_incident("INC-456")
        assert found is not None
        assert found.incident_id == "INC-456"
