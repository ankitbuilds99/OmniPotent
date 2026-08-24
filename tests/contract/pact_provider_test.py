"""Contract tests for API consumers.

Uses Pact for consumer-driven contract testing:
- Validates API responses match consumer expectations
- Catches breaking changes before deployment
- Documents API behavior as executable contracts
"""

from __future__ import annotations

import pytest


class TestPactProvider:
    """Pact provider verification tests."""

    @pytest.fixture
    def provider_states(self):
        """Provider state setup handlers."""
        return {
            "a healthy system": self._setup_healthy_system,
            "an active investigation": self._setup_active_investigation,
            "a user with valid credentials": self._setup_valid_user,
            "rate limit exceeded": self._setup_rate_limited,
        }

    def _setup_healthy_system(self):
        """Setup for healthy system state."""
        pass  # Default state

    def _setup_active_investigation(self):
        """Setup an active investigation for testing."""
        pass

    def _setup_valid_user(self):
        """Setup valid user credentials."""
        pass

    def _setup_rate_limited(self):
        """Setup rate-limited state."""
        pass

    @pytest.mark.contract
    @pytest.mark.skip(reason="Requires pact-python and running server")
    def test_verify_frontend_contract(self, provider_states):
        """Verify contracts with the frontend consumer."""
        from pact import Verifier

        verifier = Verifier(
            provider="omnipotent-api",
            provider_base_url="http://localhost:8000",
        )

        output, _ = verifier.verify_pacts(
            pact_url="tests/contract/pacts/frontend-omnipotent-api.json",
            provider_states_setup_url="http://localhost:8000/_pact/provider-states",
        )

        assert output == 0, "Contract verification failed"

    @pytest.mark.contract
    @pytest.mark.skip(reason="Requires pact-python and running server")
    def test_verify_slack_integration_contract(self, provider_states):
        """Verify contracts with Slack integration consumer."""
        from pact import Verifier

        verifier = Verifier(
            provider="omnipotent-api",
            provider_base_url="http://localhost:8000",
        )

        output, _ = verifier.verify_pacts(
            pact_url="tests/contract/pacts/slack-integration-omnipotent-api.json",
        )

        assert output == 0, "Slack contract verification failed"


class TestContractCompliance:
    """Tests for API contract compliance without Pact."""

    @pytest.mark.asyncio
    async def test_healthz_response_format(self):
        """Test /healthz returns expected format."""
        from fastapi.testclient import TestClient
        from app.api.main import app

        client = TestClient(app)
        response = client.get("/healthz")

        assert response.status_code == 200
        data = response.json()
        # The actual API returns "status": "ok"
        assert "status" in data
        assert data["status"] in ("ok", "healthy", "degraded", "unhealthy")

    @pytest.mark.asyncio
    async def test_readyz_response_format(self):
        """Test /readyz returns expected format."""
        from fastapi.testclient import TestClient
        from app.api.main import app

        client = TestClient(app)
        response = client.get("/readyz")

        assert response.status_code in (200, 503)
        data = response.json()
        assert "ready" in data or "status" in data

    @pytest.mark.asyncio
    async def test_config_response_format(self):
        """Test /config returns expected format."""
        from fastapi.testclient import TestClient
        from app.api.main import app

        client = TestClient(app)
        response = client.get("/config")

        assert response.status_code == 200
        data = response.json()
        assert "provider" in data

    @pytest.mark.asyncio
    async def test_error_response_format(self):
        """Test error responses follow RFC 7807 Problem Details."""
        from fastapi.testclient import TestClient
        from app.api.main import app

        client = TestClient(app)
        # Must be an API path. Unknown paths that are *not* API paths now fall
        # through to the SPA fallback and serve index.html, so that a deep link
        # like /dashboard reaches the client-side router instead of 404ing.
        response = client.get("/api/nonexistent")

        assert response.status_code == 404
        data = response.json()
        assert "detail" in data

    @pytest.mark.asyncio
    async def test_rate_limit_headers(self):
        """Test rate limit headers format if present."""
        from fastapi.testclient import TestClient
        from app.api.main import app

        client = TestClient(app)
        response = client.get("/config")

        # Rate limit headers are optional but should be valid if present
        headers = response.headers
        if "X-RateLimit-Limit" in headers:
            assert headers["X-RateLimit-Limit"].isdigit()
        if "X-RateLimit-Remaining" in headers:
            assert headers["X-RateLimit-Remaining"].isdigit()
        # This test is informational - doesn't fail if headers are missing
        assert response.status_code == 200
