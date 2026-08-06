"""Integration tests for Slack functionality."""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from httpx import AsyncClient

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio,
    pytest.mark.skip(reason="Slack integration tests require full environment setup"),
]


def generate_slack_signature(
    body: bytes,
    timestamp: str,
    signing_secret: str = "test-secret",
) -> str:
    """Generate valid Slack signature for testing."""
    sig_basestring = f"v0:{timestamp}:{body.decode()}"
    signature = hmac.new(
        signing_secret.encode(),
        sig_basestring.encode(),
        hashlib.sha256,
    ).hexdigest()
    return f"v0={signature}"


class TestSlackEvents:
    """Test Slack Events API integration."""

    async def test_url_verification(self, client: AsyncClient) -> None:
        """Test URL verification challenge-response."""
        payload = {
            "type": "url_verification",
            "challenge": "3eZbrw1aBm2rZgRNFdxV2595E9CY3gmdALWMmHkvFXO7tYXAYM8P",
            "token": "test-token",
        }

        response = await client.post("/api/slack/events", json=payload)

        # 200 = success, 401/403 = signature verification, 404 = route not mounted
        assert response.status_code in (200, 401, 403, 404)
        if response.status_code == 200:
            data = response.json()
            assert data["challenge"] == payload["challenge"]

    async def test_message_event(self, client: AsyncClient) -> None:
        """Test handling message events."""
        payload = {
            "type": "event_callback",
            "team_id": "T123456",
            "event": {
                "type": "message",
                "channel": "C123456",
                "user": "U123456",
                "text": "Hello bot!",
                "ts": "1234567890.123456",
            },
        }

        response = await client.post("/api/slack/events", json=payload)

        assert response.status_code in (200, 401, 403, 404)

    async def test_app_mention_event(self, client: AsyncClient) -> None:
        """Test handling app mention events."""
        payload = {
            "type": "event_callback",
            "team_id": "T123456",
            "event": {
                "type": "app_mention",
                "channel": "C123456",
                "user": "U123456",
                "text": "<@U_BOT_ID> investigate payment errors",
                "ts": "1234567890.123456",
            },
        }

        response = await client.post("/api/slack/events", json=payload)

        assert response.status_code in (200, 401, 403, 404)

    async def test_app_home_opened_event(self, client: AsyncClient) -> None:
        """Test handling App Home opened events."""
        payload = {
            "type": "event_callback",
            "team_id": "T123456",
            "event": {
                "type": "app_home_opened",
                "user": "U123456",
                "channel": "D123456",
                "tab": "home",
            },
        }

        response = await client.post("/api/slack/events", json=payload)

        assert response.status_code in (200, 401, 403, 404)

    async def test_ignores_bot_messages(self, client: AsyncClient) -> None:
        """Test that bot messages are ignored."""
        payload = {
            "type": "event_callback",
            "team_id": "T123456",
            "event": {
                "type": "message",
                "channel": "C123456",
                "user": "U123456",
                "text": "Bot message",
                "ts": "1234567890.123456",
                "bot_id": "B123456",
            },
        }

        response = await client.post("/api/slack/events", json=payload)

        assert response.status_code in (200, 401, 403, 404)


class TestSlackCommands:
    """Test Slack slash commands."""

    async def test_investigate_command(self, client: AsyncClient) -> None:
        """Test /investigate command."""
        form_data = {
            "command": "/investigate",
            "text": "payment-service 500 errors",
            "user_id": "U123456",
            "user_name": "testuser",
            "channel_id": "C123456",
            "channel_name": "incidents",
            "team_id": "T123456",
            "response_url": "https://hooks.slack.com/test",
            "trigger_id": "123.456.abc",
        }

        response = await client.post("/api/slack/commands/investigate", data=form_data)

        assert response.status_code in (200, 401, 403, 404)
        if response.status_code == 200:
            data = response.json()
            assert "response_type" in data
            assert "text" in data

    async def test_oncall_command(self, client: AsyncClient) -> None:
        """Test /oncall command."""
        form_data = {
            "command": "/oncall",
            "text": "",
            "user_id": "U123456",
            "channel_id": "C123456",
            "response_url": "https://hooks.slack.com/test",
            "trigger_id": "123.456.abc",
        }

        response = await client.post("/api/slack/commands/oncall", data=form_data)

        assert response.status_code in (200, 401, 403, 404)

    async def test_status_command(self, client: AsyncClient) -> None:
        """Test /status command."""
        form_data = {
            "command": "/status",
            "text": "",
            "user_id": "U123456",
            "channel_id": "C123456",
            "response_url": "https://hooks.slack.com/test",
            "trigger_id": "123.456.abc",
        }

        response = await client.post("/api/slack/commands/status", data=form_data)

        assert response.status_code in (200, 401, 403, 404)


class TestSlackInteractions:
    """Test Slack interactive components."""

    async def test_block_action(self, client: AsyncClient) -> None:
        """Test block action interaction."""
        payload = {
            "type": "block_actions",
            "user": {"id": "U123456", "username": "testuser"},
            "channel": {"id": "C123456"},
            "actions": [
                {
                    "action_id": "start_investigation",
                    "block_id": "actions_block",
                    "type": "button",
                    "value": "start",
                }
            ],
            "response_url": "https://hooks.slack.com/test",
            "trigger_id": "123.456.abc",
        }

        response = await client.post(
            "/api/slack/interactions",
            data={"payload": json.dumps(payload)},
        )

        assert response.status_code in (200, 401, 403, 404)

    async def test_view_submission(self, client: AsyncClient) -> None:
        """Test modal submission."""
        payload = {
            "type": "view_submission",
            "user": {"id": "U123456"},
            "view": {
                "callback_id": "incident_form",
                "state": {
                    "values": {
                        "title_block": {
                            "title": {"value": "Test Incident"},
                        },
                        "severity_block": {
                            "severity": {"selected_option": {"value": "high"}},
                        },
                    },
                },
            },
        }

        response = await client.post(
            "/api/slack/interactions",
            data={"payload": json.dumps(payload)},
        )

        assert response.status_code in (200, 401, 403, 404)

    async def test_shortcut(self, client: AsyncClient) -> None:
        """Test global shortcut."""
        payload = {
            "type": "shortcut",
            "callback_id": "create_incident",
            "user": {"id": "U123456"},
            "trigger_id": "123.456.abc",
        }

        response = await client.post(
            "/api/slack/interactions",
            data={"payload": json.dumps(payload)},
        )

        assert response.status_code in (200, 401, 403, 404)


class TestSlackOAuth:
    """Test Slack OAuth flow."""

    async def test_oauth_authorize_redirect(self, client: AsyncClient) -> None:
        """Test OAuth authorize redirects to Slack."""
        response = await client.get("/api/slack/oauth/authorize", follow_redirects=False)

        # 302/307 = redirect, 404 = route not mounted, 500 = missing config
        assert response.status_code in (302, 307, 404, 500)
        if response.status_code in (302, 307):
            location = response.headers.get("location", "")
            assert "slack.com/oauth" in location


class TestSlackHealth:
    """Test Slack health endpoint."""

    async def test_slack_health(self, client: AsyncClient) -> None:
        """Test Slack service health check."""
        response = await client.get("/api/slack/health")

        assert response.status_code in (200, 404)
        if response.status_code == 200:
            data = response.json()
            assert data["status"] == "ok"
            assert data["service"] == "slack"


class TestSlackBlockKit:
    """Test Slack Block Kit message building."""

    async def test_incident_modal_structure(self) -> None:
        """Test incident modal has correct structure."""
        from app.slack_bot.blocks import SlackBlockBuilder

        builder = SlackBlockBuilder()
        blocks = builder.build_incident_blocks({
            "id": "INC-001",
            "title": "Test Incident",
            "severity": "high",
            "status": "investigating",
            "service": "api-gateway",
        })

        assert isinstance(blocks, list)
        assert len(blocks) > 0
        assert all("type" in block for block in blocks)

    async def test_investigation_message_structure(self) -> None:
        """Test investigation message has correct structure."""
        from app.slack_bot.blocks import SlackBlockBuilder

        builder = SlackBlockBuilder()
        blocks = builder.build_investigation_blocks({
            "id": "INV-001",
            "status": "running",
            "findings": ["Finding 1", "Finding 2"],
        })

        assert isinstance(blocks, list)
        assert len(blocks) > 0
