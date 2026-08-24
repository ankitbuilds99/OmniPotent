"""Tests for the security headers middleware, and for it being switched on.

Every test below the first class checks the middleware object in isolation,
which is how it passed for as long as it did while being wired into nothing:
`app.add_middleware(SecurityHeadersMiddleware)` appeared nowhere, so the API
served every response with no CSP, no X-Frame-Options and no HSTS while these
tests stayed green.
"""

import pytest
from fastapi.testclient import TestClient

import app.api.main as api
import app.config as cfg


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("COPILOT_ENV", "development")
    cfg.get_settings.cache_clear()
    yield TestClient(api.app)
    cfg.get_settings.cache_clear()


class TestHeadersAreActuallySent:
    """The part the unit tests below cannot see: is the middleware installed?"""

    def test_a_real_response_carries_the_headers(self, client):
        response = client.get("/health")

        assert response.headers["X-Frame-Options"] == "DENY"
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert "default-src 'self'" in response.headers["Content-Security-Policy"]
        assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
        assert "camera=()" in response.headers["Permissions-Policy"]

    @pytest.mark.parametrize(
        "method,path,expected", [("GET", "/api/nope", 404), ("POST", "/api/investigate", 405)]
    )
    def test_error_responses_carry_them_too(self, client, method, path, expected):
        """It is added last, so it is outermost and stamps responses it never routed.

        A 404 for an unmatched path and a 405 for a wrong verb are produced by
        the router, below every other middleware; clickjacking protection that
        only covered 200s would be worth very little.
        """
        response = client.request(method, path, json={} if method == "POST" else None)

        assert response.status_code == expected
        assert response.headers["X-Frame-Options"] == "DENY"

    def test_hsts_is_off_outside_production(self, client):
        """A year-long HTTPS pin on localhost would break every developer.

        HSTS is remembered by the browser per host, not per response, so
        sending it once from a dev server is enough to make plain http to
        that host unreachable until max-age runs out.
        """
        assert "Strict-Transport-Security" not in client.get("/health").headers


class TestSecurityHeaders:
    """Tests for security headers middleware."""

    def test_headers_middleware_init(self):
        """Test SecurityHeadersMiddleware initialization."""
        from app.security.headers import SecurityHeadersMiddleware

        class MockApp:
            pass

        middleware = SecurityHeadersMiddleware(MockApp(), enable_hsts=True)

        assert middleware.enable_hsts is True
        assert middleware.frame_options == "DENY"
        assert middleware.content_type_options == "nosniff"
        assert "default-src 'self'" in middleware.csp_policy

    def test_default_csp_policy(self):
        """Test default CSP policy contains required directives."""
        from app.security.headers import SecurityHeadersMiddleware

        class MockApp:
            pass

        middleware = SecurityHeadersMiddleware(MockApp())
        csp = middleware.csp_policy

        assert "default-src 'self'" in csp
        assert "script-src" in csp
        assert "frame-ancestors 'none'" in csp

    def test_default_permissions_policy(self):
        """Test default permissions policy."""
        from app.security.headers import SecurityHeadersMiddleware

        class MockApp:
            pass

        middleware = SecurityHeadersMiddleware(MockApp())
        perms = middleware.permissions_policy

        assert "camera=()" in perms
        assert "microphone=()" in perms
        assert "geolocation=()" in perms

    def test_custom_csp(self):
        """Test custom CSP policy."""
        from app.security.headers import SecurityHeadersMiddleware

        class MockApp:
            pass

        custom_csp = "default-src 'none'; script-src 'self'"
        middleware = SecurityHeadersMiddleware(MockApp(), csp_policy=custom_csp)

        assert middleware.csp_policy == custom_csp
