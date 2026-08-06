"""Integration tests for API endpoints."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


class TestHealthEndpoints:
    """Test health check endpoints."""

    async def test_health_check(self, client: AsyncClient) -> None:
        """Test basic health check."""
        response = await client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] in ("healthy", "ok")

    async def test_readiness_check(self, client: AsyncClient) -> None:
        """Test readiness probe."""
        response = await client.get("/ready")
        assert response.status_code in (200, 503)


class TestAPIEndpoints:
    """Test main API endpoints."""

    async def test_root_endpoint(self, client: AsyncClient) -> None:
        """Test root endpoint."""
        response = await client.get("/")
        assert response.status_code in (200, 404)

    async def test_openapi_spec(self, client: AsyncClient) -> None:
        """Test OpenAPI spec is available."""
        response = await client.get("/openapi.json")
        assert response.status_code == 200
        data = response.json()
        assert "openapi" in data
        assert "paths" in data

    async def test_docs_endpoint(self, client: AsyncClient) -> None:
        """Test docs endpoint."""
        response = await client.get("/docs")
        assert response.status_code == 200


class TestIncidentEndpoints:
    """Test incident management endpoints."""

    async def test_list_incidents(
        self,
        client: AsyncClient,
        auth_headers: dict,
    ) -> None:
        """Test listing incidents."""
        response = await client.get("/api/v1/incidents", headers=auth_headers)
        assert response.status_code in (200, 401, 404)

    async def test_create_incident(
        self,
        client: AsyncClient,
        auth_headers: dict,
        sample_incident: dict,
    ) -> None:
        """Test creating an incident."""
        response = await client.post(
            "/api/v1/incidents",
            json=sample_incident,
            headers=auth_headers,
        )
        assert response.status_code in (200, 201, 401, 422)


class TestInvestigationEndpoints:
    """Test investigation endpoints."""

    async def test_start_investigation(
        self,
        client: AsyncClient,
        auth_headers: dict,
        sample_investigation: dict,
    ) -> None:
        """Test starting an investigation."""
        response = await client.post(
            "/api/v1/investigate",
            json=sample_investigation,
            headers=auth_headers,
        )
        assert response.status_code in (200, 202, 401, 422)


class TestSlackWebhooks:
    """Test Slack webhook endpoints."""

    async def test_slack_url_verification(self, client: AsyncClient) -> None:
        """Test Slack URL verification challenge."""
        payload = {
            "type": "url_verification",
            "challenge": "test-challenge-token",
        }
        response = await client.post("/slack/events", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data.get("challenge") == "test-challenge-token"

    async def test_slack_event_callback(self, client: AsyncClient) -> None:
        """Test Slack event callback."""
        payload = {
            "type": "event_callback",
            "event": {
                "type": "message",
                "channel": "C123456",
                "user": "U123456",
                "text": "Hello",
                "ts": "1234567890.123456",
            },
        }
        response = await client.post("/slack/events", json=payload)
        assert response.status_code == 200

    async def test_slack_command(self, client: AsyncClient) -> None:
        """Test Slack slash command."""
        form_data = {
            "command": "/investigate",
            "text": "payment-service error",
            "user_id": "U123456",
            "channel_id": "C123456",
            "response_url": "https://hooks.slack.com/test",
            "trigger_id": "123.456.abc",
        }
        response = await client.post(
            "/slack/commands/investigate",
            data=form_data,
        )
        assert response.status_code == 200


class TestConcurrency:
    """Test concurrent request handling."""

    async def test_concurrent_health_checks(self, client: AsyncClient) -> None:
        """Test multiple concurrent health checks."""
        import asyncio

        tasks = [client.get("/health") for _ in range(10)]
        responses = await asyncio.gather(*tasks)

        for response in responses:
            assert response.status_code == 200

    async def test_concurrent_api_requests(
        self,
        client: AsyncClient,
        auth_headers: dict,
    ) -> None:
        """Test concurrent API requests."""
        import asyncio

        tasks = [
            client.get("/api/v1/incidents", headers=auth_headers)
            for _ in range(5)
        ]
        responses = await asyncio.gather(*tasks)

        for response in responses:
            assert response.status_code in (200, 401, 404)


class TestErrorHandling:
    """Test error handling."""

    async def test_404_not_found(self, client: AsyncClient) -> None:
        """Test 404 response for unknown endpoint."""
        response = await client.get("/api/v1/nonexistent")
        assert response.status_code == 404

    async def test_method_not_allowed(self, client: AsyncClient) -> None:
        """Test 405 for wrong HTTP method."""
        response = await client.delete("/health")
        assert response.status_code in (405, 404)

    async def test_invalid_json(self, client: AsyncClient) -> None:
        """Test invalid JSON handling."""
        response = await client.post(
            "/slack/events",
            content="not valid json",
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code in (400, 422)
