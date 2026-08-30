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
        "method,path,expected", [("GET", "/api/nope", 404), ("POST", "/healthz", 405)]
    )
    def test_error_responses_carry_them_too(self, client, method, path, expected):
        """It is added last, so it is outermost and stamps responses it never routed.

        A 404 for an unmatched path and a 405 for a wrong verb are produced by
        the router, below every other middleware; clickjacking protection that
        only covered 200s would be worth very little.

        The 405 comes from `/healthz`, a real GET-only route, rather than from a
        path no route declares. `POST /api/investigate` answered 405 only where
        `frontend/dist` had been built: the SPA mount then matched the path for
        GET and the router reported the verb as wrong. Without a build — CI, a
        fresh clone — there was no mount, nothing matched, and the answer was a
        404 that had nothing to do with the method.
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


class TestConnectSrcFollowsConfiguration:
    """The policy has to allow the hosts this deployment hands the SPA.

    /frontend-config gives the browser a Supabase address to authenticate
    against while connect-src named only 'self' and the two model APIs. The
    browser blocked every call to it: sign-in failed with "Failed to fetch", a
    stored session could not be refreshed, and the login form sat disabled
    behind a spinner with the reason visible only in the developer console.
    """

    @pytest.mark.parametrize(
        "url,expected",
        [
            ("https://abc.supabase.co", ("https://abc.supabase.co", "wss://abc.supabase.co")),
            # Ports belong to the origin; dropping one would not match.
            ("http://localhost:54321", ("http://localhost:54321", "ws://localhost:54321")),
            # A path is not part of a CSP source expression.
            ("https://abc.supabase.co/auth/v1", ("https://abc.supabase.co", "wss://abc.supabase.co")),
            # "Not configured", and things no browser would connect to anyway.
            ("", ()),
            ("   ", ()),
            ("abc.supabase.co", ()),
            ("file:///etc/passwd", ()),
        ],
    )
    def test_sources_derived_from_a_url(self, url, expected):
        from app.security.headers import csp_connect_sources

        assert csp_connect_sources(url) == expected

    def test_duplicates_collapse(self):
        from app.security.headers import csp_connect_sources

        assert csp_connect_sources("https://a.co", "https://a.co/x") == (
            "https://a.co",
            "wss://a.co",
        )

    def test_extra_sources_join_the_defaults(self):
        from app.security.headers import SecurityHeadersMiddleware

        class MockApp:
            pass

        middleware = SecurityHeadersMiddleware(
            MockApp(), extra_connect_src=("https://abc.supabase.co",)
        )

        # connect-src is derived from the configured provider (anthropic in the
        # test env), so only that provider's origin appears alongside the extras.
        assert (
            "connect-src 'self' https://api.anthropic.com "
            "https://abc.supabase.co" in middleware.csp_policy
        )

    def test_the_app_wires_the_configured_auth_host_into_the_policy(self):
        """The wiring, not just the helper.

        Asserted against the installed middleware rather than a served response,
        because the app builds its middleware stack once at import: rebuilding it
        for a different setting means reloading the module, and every other test
        holds a reference to the app that reload replaces.
        """
        from app.security.headers import SecurityHeadersMiddleware, csp_connect_sources

        installed = [
            mw for mw in api.app.user_middleware if mw.cls is SecurityHeadersMiddleware
        ]
        assert len(installed) == 1, "security headers middleware is not installed exactly once"

        expected = csp_connect_sources(cfg.get_settings().supabase_url)
        assert tuple(installed[0].kwargs.get("extra_connect_src", ())) == expected

    def test_a_configured_host_reaches_the_served_header(self, monkeypatch):
        """The same construction the app performs, exercised through a response."""
        monkeypatch.setenv("SUPABASE_URL", "https://abc.supabase.co")
        cfg.get_settings.cache_clear()
        try:
            from fastapi import FastAPI

            from app.security.headers import SecurityHeadersMiddleware, csp_connect_sources

            probe = FastAPI()

            @probe.get("/ping")
            async def ping() -> dict:
                return {"ok": True}

            probe.add_middleware(
                SecurityHeadersMiddleware,
                extra_connect_src=csp_connect_sources(cfg.get_settings().supabase_url),
            )

            csp = TestClient(probe).get("/ping").headers["Content-Security-Policy"]

            connect_src = next(
                part for part in csp.split("; ") if part.startswith("connect-src ")
            )
            assert "https://abc.supabase.co" in connect_src.split()
            assert "wss://abc.supabase.co" in connect_src.split()
        finally:
            cfg.get_settings.cache_clear()
