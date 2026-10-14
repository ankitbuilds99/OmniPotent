"""Real integration tests for the API.

These tests hit actual endpoints without mocking the core functionality,
ensuring the full stack works together. They use the TestClient which
runs the app in-process, so no external server is needed.

For tests that would hit real LLM APIs, we use COPILOT_REPLAY_MODE=replay
with pre-recorded cassettes, or skip if no cassette exists.
"""

import pytest
from fastapi.testclient import TestClient

import app.api.main as api
import app.config as cfg
from app import runtime
from app import tenant_context


@pytest.fixture(autouse=True)
def reset_state():
    """Reset all state between tests."""
    cfg.get_settings.cache_clear()
    api._RL.clear()
    api._reset_limiter()
    runtime.reset()
    yield
    cfg.get_settings.cache_clear()
    api._RL.clear()
    api._reset_limiter()
    runtime.reset()


@pytest.fixture
def client():
    """Create a test client."""
    return TestClient(api.app)


class TestHealthEndpoints:
    """Integration tests for health and readiness probes."""

    def test_healthz_returns_session_count(self, client):
        """Health endpoint returns valid JSON with session info."""
        r = client.get("/healthz")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert "active_sessions" in data
        assert isinstance(data["active_sessions"], int)

    def test_readyz_checks_llm_config(self, client):
        """Readiness probe validates LLM configuration."""
        r = client.get("/readyz")
        assert r.status_code == 200
        data = r.json()
        assert "status" in data
        assert "reasons" in data
        assert isinstance(data["reasons"], list)

    def test_metrics_slo_endpoint(self, client):
        """SLO metrics endpoint returns Prometheus format."""
        r = client.get("/metrics/slo")
        assert r.status_code == 200
        assert "text/plain" in r.headers["content-type"]


class TestConfigEndpoints:
    """Integration tests for configuration endpoints."""

    def test_config_returns_full_config(self, client):
        """Config endpoint returns all expected fields."""
        r = client.get("/config")
        assert r.status_code == 200
        data = r.json()

        required_fields = ["provider", "model", "fast_model", "servers", "github", "sources"]
        for field in required_fields:
            assert field in data, f"Missing field: {field}"

        assert isinstance(data["servers"], list)
        assert len(data["servers"]) > 0

        assert "connected" in data["github"]
        assert "repo_path" in data["sources"]

    def test_frontend_config_returns_supabase_settings(self, client):
        """Frontend config endpoint returns Supabase settings."""
        r = client.get("/frontend-config")
        assert r.status_code == 200
        data = r.json()

        assert "supabase_url" in data
        assert "supabase_anon_key" in data
        assert "api_url" in data

    def test_github_status_endpoint(self, client):
        """GitHub status endpoint returns connection info."""
        r = client.get("/github/status")
        assert r.status_code == 200
        data = r.json()

        assert "connected" in data
        assert "mode" in data
        assert data["mode"] in ["live", "offline"]

    def test_me_endpoint_returns_identity(self, client):
        """Me endpoint returns current user identity."""
        r = client.get("/me")
        assert r.status_code == 200
        data = r.json()

        assert "multi_tenant" in data
        assert "authenticated" in data
        assert "role" in data


class TestAuthProviders:
    """Integration tests for auth provider endpoints."""

    def test_auth_providers_list(self, client):
        """Auth providers endpoint returns available providers."""
        r = client.get("/auth/providers")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list)


class TestChatValidation:
    """Integration tests for chat endpoint validation."""

    def test_chat_rejects_empty_message(self, client):
        """Chat endpoint rejects empty messages."""
        r = client.post("/chat", json={"thread_id": "test", "message": ""})
        assert r.status_code == 400

    def test_chat_rejects_whitespace_message(self, client):
        """Chat endpoint rejects whitespace-only messages."""
        r = client.post("/chat", json={"thread_id": "test", "message": "   \n\t  "})
        assert r.status_code == 400

    def test_chat_rejects_oversized_message(self, client, monkeypatch):
        """Chat endpoint rejects messages exceeding size limit."""
        monkeypatch.setenv("COPILOT_MAX_MESSAGE_CHARS", "100")
        cfg.get_settings.cache_clear()

        r = client.post("/chat", json={
            "thread_id": "test",
            "message": "x" * 200
        })
        assert r.status_code == 413

    def test_chat_requires_thread_id(self, client):
        """Chat endpoint requires thread_id."""
        r = client.post("/chat", json={"message": "test"})
        assert r.status_code == 422  # Pydantic validation error


class TestApprovalValidation:
    """Integration tests for approval endpoint validation."""

    def test_approve_requires_thread_id(self, client):
        """Approve endpoint requires thread_id."""
        r = client.post("/approve", json={"approved": True})
        assert r.status_code == 422

    def test_approve_nonexistent_thread(self, client):
        """Approve endpoint returns 404 for nonexistent thread."""
        r = client.post("/approve", json={
            "thread_id": "nonexistent-thread-12345",
            "approved": True
        })
        assert r.status_code == 404


class TestFeedbackEndpoint:
    """Integration tests for feedback endpoint."""

    def test_feedback_accepts_valid_rating(self, client):
        """Feedback endpoint accepts valid up/down ratings."""
        r = client.post("/feedback", json={
            "thread_id": "test-thread",
            "rating": "up",
            "comment": "Great investigation!"
        })
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "recorded"

    def test_feedback_rejects_invalid_rating(self, client):
        """Feedback endpoint rejects invalid ratings."""
        r = client.post("/feedback", json={
            "thread_id": "test-thread",
            "rating": "invalid"
        })
        assert r.status_code == 400


class TestAuditEndpoints:
    """Integration tests for audit endpoints."""

    def test_audit_returns_events(self, client):
        """Audit endpoint returns event list."""
        r = client.get("/audit")
        assert r.status_code == 200
        data = r.json()
        assert "events" in data
        assert isinstance(data["events"], list)

    def test_audit_verify_returns_integrity(self, client):
        """Audit verify endpoint returns chain integrity."""
        r = client.get("/audit/verify")
        assert r.status_code == 200
        data = r.json()
        assert "valid" in data
        assert isinstance(data["valid"], bool)

    def test_audit_respects_limit(self, client):
        """Audit endpoint respects limit parameter."""
        r = client.get("/audit?limit=5")
        assert r.status_code == 200
        data = r.json()
        assert len(data["events"]) <= 5


class TestRateLimiting:
    """Integration tests for rate limiting middleware."""

    def test_rate_limit_enforced(self, client, monkeypatch):
        """Rate limiter blocks excessive requests."""
        monkeypatch.setenv("COPILOT_RATE_LIMIT_PER_MIN", "3")
        cfg.get_settings.cache_clear()
        api._RL.clear()
        api._reset_limiter()

        responses = []
        for _ in range(5):
            r = client.post("/feedback", json={
                "thread_id": "test",
                "rating": "up"
            })
            responses.append(r.status_code)

        assert 429 in responses, "Rate limit should trigger 429"

    def test_rate_limit_allows_normal_traffic(self, client, monkeypatch):
        """Rate limiter allows normal request volume."""
        monkeypatch.setenv("COPILOT_RATE_LIMIT_PER_MIN", "100")
        cfg.get_settings.cache_clear()
        api._RL.clear()
        api._reset_limiter()

        for _ in range(5):
            r = client.get("/healthz")
            assert r.status_code == 200


class TestAuthentication:
    """Integration tests for authentication."""

    def test_auth_required_when_token_set(self, client, monkeypatch):
        """Protected endpoints require auth when token is configured."""
        monkeypatch.setenv("COPILOT_API_TOKEN", "test-secret-token")
        cfg.get_settings.cache_clear()

        # Without token - should fail
        r = client.get("/config")
        assert r.status_code == 401

        # With correct token - should work
        r = client.get("/config", headers={"Authorization": "Bearer test-secret-token"})
        assert r.status_code == 200

        # With wrong token - should fail
        r = client.get("/config", headers={"Authorization": "Bearer wrong-token"})
        assert r.status_code == 401

    def test_healthz_bypasses_auth(self, client, monkeypatch):
        """Health endpoint bypasses authentication."""
        monkeypatch.setenv("COPILOT_API_TOKEN", "test-secret-token")
        cfg.get_settings.cache_clear()

        r = client.get("/healthz")
        assert r.status_code == 200

    def test_frontend_config_bypasses_auth(self, client, monkeypatch):
        """Frontend config endpoint bypasses authentication."""
        monkeypatch.setenv("COPILOT_API_TOKEN", "test-secret-token")
        cfg.get_settings.cache_clear()

        r = client.get("/frontend-config")
        assert r.status_code == 200


class TestModelConfiguration:
    """Integration tests for model configuration endpoint."""

    def test_model_configure_rejects_invalid_provider(self, client):
        """Model configure endpoint rejects invalid providers."""
        r = client.post("/model/configure", json={"provider": "invalid-provider"})
        assert r.status_code == 400
        assert "provider" in r.json()["detail"].lower()

    def test_model_configure_accepts_valid_providers(self, client):
        """Model configure endpoint accepts valid provider names."""
        valid_providers = ["anthropic", "openai", "gemini", "groq", "deepseek"]

        for provider in valid_providers:
            r = client.post("/model/configure", json={
                "provider": provider,
                "api_key": ""  # Empty key will fail, but provider validation passes
            })
            # Either 400 (no key) or 200 (if env has key) - not 400 for invalid provider
            assert r.status_code in [200, 400]
            if r.status_code == 400:
                assert "provider" not in r.json()["detail"].lower() or "key" in r.json()["detail"].lower()


class TestGitHubIntegration:
    """Integration tests for GitHub integration endpoints."""

    def test_github_connect_validates_input(self, client):
        """GitHub connect validates token and repo format."""
        # Missing token
        r = client.post("/github/connect", json={"token": "", "repo": "owner/repo"})
        assert r.status_code == 400

        # Invalid repo format
        r = client.post("/github/connect", json={"token": "test", "repo": "invalid"})
        assert r.status_code == 400

    def test_github_disconnect_works(self, client):
        """GitHub disconnect endpoint works."""
        r = client.post("/github/disconnect")
        assert r.status_code == 200
        data = r.json()
        assert data["connected"] is False


class TestIntegrationStatus:
    """`/integrations/status` answers live-vs-demo for every catalog connector.

    The sidebar badges every MCP server from this, so a connector missing from
    the response cannot be classified and gets shown as unknown. It used to
    cover five of the eleven, and the sidebar filled the gap by hardcoding
    "connected" on all of them.
    """

    def test_answers_for_every_catalog_connector_with_a_live_mode(self, client):
        """Nothing in the catalog is left unclassifiable except local-only servers."""
        r = client.get("/integrations/status")
        assert r.status_code == 200
        status = r.json()

        # repo and memory read local state; they have no credentials and no
        # live/demo distinction to report.
        local_only = {"repo", "memory"}
        catalog = {s["name"] for s in api.MCP_CATALOG} - local_only
        assert catalog <= set(status), f"unclassifiable connectors: {catalog - set(status)}"

    def test_reports_false_when_no_credentials_are_configured(self, client, monkeypatch):
        """False means "serving bundled fixtures", which is the default state."""
        # Do not let the developer's real .env turn this fixture into a live
        # deployment. Production still loads .env; this test needs an explicit
        # empty deployment configuration so it can verify the fail-closed path.
        monkeypatch.setitem(cfg.Settings.model_config, "env_file", None)
        for var in (
            "PAGERDUTY_API_TOKEN", "DD_API_KEY", "DD_APP_KEY", "SENTRY_API_TOKEN",
            "PROMETHEUS_URL",
            "KUBE_CONFIG_PATH", "TRACES_API_URL", "DEPLOYS_API_URL",
            "ARGOCD_SERVER_URL", "ARGOCD_AUTH_TOKEN",
            "TERRAFORM_CLOUD_TOKEN", "TERRAFORM_ORGANIZATION", "GRAFANA_URL",
        ):
            monkeypatch.delenv(var, raising=False)
        cfg.get_settings.cache_clear()

        status = client.get("/integrations/status").json()
        boolean_status = {k: v for k, v in status.items() if isinstance(v, bool)}
        assert not any(boolean_status.values()), f"claimed live with no credentials: {status}"

    def test_reports_true_only_for_the_connector_that_was_configured(self, client, monkeypatch):
        """One credential must not light up the rest."""
        monkeypatch.setitem(cfg.Settings.model_config, "env_file", None)
        monkeypatch.setenv("SENTRY_API_TOKEN", "sntrys_test")
        cfg.get_settings.cache_clear()

        status = client.get("/integrations/status").json()
        assert status["sentry"] is True
        assert [k for k, v in status.items() if isinstance(v, bool) and v] == ["sentry"]

    def test_reports_prometheus_when_url_is_configured(self, client, monkeypatch):
        monkeypatch.setitem(cfg.Settings.model_config, "env_file", None)
        monkeypatch.setenv("PROMETHEUS_URL", "http://127.0.0.1:9090")
        cfg.get_settings.cache_clear()

        status = client.get("/integrations/status").json()
        assert status["prometheus"] is True

    def test_a_connector_needing_two_settings_is_not_live_on_one(self, client, monkeypatch):
        """Terraform needs both a token and an org; half of it queries nothing."""
        monkeypatch.setitem(cfg.Settings.model_config, "env_file", None)
        monkeypatch.setenv("TERRAFORM_CLOUD_TOKEN", "tf-token")
        monkeypatch.delenv("TERRAFORM_ORGANIZATION", raising=False)
        cfg.get_settings.cache_clear()
        assert client.get("/integrations/status").json()["terraform"] is False

        monkeypatch.setenv("TERRAFORM_ORGANIZATION", "acme")
        cfg.get_settings.cache_clear()
        assert client.get("/integrations/status").json()["terraform"] is True

    def test_tenant_status_never_reads_host_connector_credentials(self, client, monkeypatch):
        """A tenant request cannot inherit integrations configured on the host."""
        monkeypatch.setitem(cfg.Settings.model_config, "env_file", None)
        monkeypatch.setenv("SENTRY_API_TOKEN", "host-only-test-value")
        cfg.get_settings.cache_clear()
        token = tenant_context.set_tenant(
            tenant_context.TenantConfig(
                org_id="org-a",
                role="owner",
                integration_secrets={},
            )
        )
        try:
            status = client.get("/integrations/status")
        finally:
            tenant_context.reset_tenant(token)

        assert status.status_code == 200
        assert status.json()["sentry"] is False


class TestMetricsEndpoints:
    """Integration tests for metrics endpoints."""

    def test_metrics_returns_data(self, client):
        """Metrics endpoint returns service data."""
        r = client.get("/metrics")
        assert r.status_code == 200
        data = r.json()
        assert "services" in data

    def test_cache_stats_endpoint(self, client):
        """Cache stats endpoint returns statistics."""
        r = client.get("/cache/stats")
        assert r.status_code == 200
        data = r.json()
        # Should have standard cache metrics
        assert isinstance(data, dict)


class TestUsageEndpoint:
    """Integration tests for usage endpoint."""

    def test_usage_returns_single_tenant_info(self, client):
        """Usage endpoint returns single-tenant info by default."""
        r = client.get("/usage")
        assert r.status_code == 200
        data = r.json()
        assert data["multi_tenant"] is False


class TestRequestIdTracking:
    """Integration tests for request ID tracking."""

    def test_request_id_in_response(self, client):
        """All responses include x-request-id header."""
        endpoints = ["/healthz", "/config", "/metrics", "/audit"]

        for endpoint in endpoints:
            r = client.get(endpoint)
            assert "x-request-id" in r.headers, f"Missing x-request-id in {endpoint}"

    def test_custom_request_id_echoed(self, client):
        """Custom request ID is echoed in response."""
        custom_id = "my-custom-request-id-12345"
        r = client.get("/healthz", headers={"x-request-id": custom_id})
        assert r.headers["x-request-id"] == custom_id


class TestCORSHeaders:
    """Integration tests for CORS configuration."""

    def test_cors_allows_localhost(self, client):
        """CORS allows localhost origin for development."""
        r = client.options("/config", headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET"
        })
        # OPTIONS should work
        assert r.status_code in [200, 204, 405]


class TestWebSocketEndpoint:
    """Integration tests for WebSocket endpoint availability."""

    def test_websocket_endpoint_exists(self, client):
        """WebSocket endpoint is registered."""
        # Can't test actual WebSocket with TestClient, but can verify route exists
        # by checking the health endpoint which reports WebSocket stats
        r = client.get("/healthz")
        assert r.status_code == 200
        data = r.json()
        assert "websocket" in data
        assert "enabled" in data["websocket"]
