"""Security headers middleware for FastAPI.

Adds security headers to all responses:
- Content-Security-Policy
- X-Content-Type-Options
- X-Frame-Options
- X-XSS-Protection
- Strict-Transport-Security (HSTS)
- Referrer-Policy
- Permissions-Policy
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from urllib.parse import urlsplit

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# API origin per LLM provider (the deepseek one matches app/llm.py's
# _DEEPSEEK_BASE_URL). connect-src names only the provider(s) this deployment
# is configured for — hardcoding two vendors' origins both blocked the other
# providers and allowlisted APIs the deployment never calls.
_PROVIDER_API_ORIGINS: dict[str, str] = {
    "anthropic": "https://api.anthropic.com",
    "openai": "https://api.openai.com",
    "gemini": "https://generativelanguage.googleapis.com",
    "groq": "https://api.groq.com",
    "deepseek": "https://api.deepseek.com",
}


def _provider_connect_sources() -> tuple[str, ...]:
    """API origins for the configured primary (and fallback) LLM providers."""
    from app.config import get_settings

    settings = get_settings()
    sources: list[str] = []
    for provider in (settings.copilot_provider, settings.copilot_fallback_provider):
        origin = _PROVIDER_API_ORIGINS.get((provider or "").strip().lower())
        if origin and origin not in sources:
            sources.append(origin)
    return tuple(sources)


def csp_connect_sources(*urls: str) -> tuple[str, ...]:
    """The ``connect-src`` entries a browser needs to reach ``urls``.

    A service the SPA is configured to call has to be named in the policy or the
    browser blocks the request outright. Each URL contributes its own origin plus
    the WebSocket origin on the same host, since clients such as Supabase open
    both. Blank, relative and non-HTTP values are dropped: they either mean "not
    configured" or are already covered by ``'self'``.

    Args:
        *urls: Absolute URLs of services the frontend connects to.

    Returns:
        tuple[str, ...]: Deduplicated CSP source expressions, in argument order.
    """
    sources: list[str] = []
    for url in urls:
        parts = urlsplit((url or "").strip())
        if parts.scheme not in ("http", "https") or not parts.netloc:
            continue
        ws_scheme = "wss" if parts.scheme == "https" else "ws"
        for source in (f"{parts.scheme}://{parts.netloc}", f"{ws_scheme}://{parts.netloc}"):
            if source not in sources:
                sources.append(source)
    return tuple(sources)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Middleware that adds security headers to all responses."""

    def __init__(
        self,
        app: Any,
        enable_hsts: bool = False,
        hsts_max_age: int = 31536000,
        frame_options: str = "DENY",
        content_type_options: str = "nosniff",
        referrer_policy: str = "strict-origin-when-cross-origin",
        csp_policy: str | None = None,
        permissions_policy: str | None = None,
        extra_connect_src: Sequence[str] | None = None,
    ):
        """Initialize security headers middleware.

        Args:
            app: The ASGI application
            enable_hsts: Enable Strict-Transport-Security (only for HTTPS)
            hsts_max_age: HSTS max-age in seconds (default: 1 year)
            frame_options: X-Frame-Options value (DENY, SAMEORIGIN, or None to disable)
            content_type_options: X-Content-Type-Options value
            referrer_policy: Referrer-Policy value
            csp_policy: Content-Security-Policy value (None for default)
            permissions_policy: Permissions-Policy value (None for default)
            extra_connect_src: Additional connect-src sources to allow, for
                services this deployment tells the frontend to call directly.
                Ignored when csp_policy is given, which replaces the policy whole.
        """
        super().__init__(app)
        self.enable_hsts = enable_hsts
        self.hsts_max_age = hsts_max_age
        self.frame_options = frame_options
        self.content_type_options = content_type_options
        self.referrer_policy = referrer_policy
        self.extra_connect_src = tuple(extra_connect_src or ())

        self.csp_policy = csp_policy or self._default_csp()
        self.permissions_policy = permissions_policy or self._default_permissions()

    def _default_csp(self) -> str:
        """Get default Content-Security-Policy."""
        # connect-src is the one directive that has to follow the deployment's
        # configuration. Anything the SPA is handed an address for — the auth
        # host in /frontend-config, say — is unreachable unless it is named here,
        # and the browser reports that only to its own console.
        connect_src = " ".join([
            "'self'",
            *_provider_connect_sources(),
            *self.extra_connect_src,
        ])
        return "; ".join([
            "default-src 'self'",
            "script-src 'self' 'unsafe-inline'",
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
            "font-src 'self' https://fonts.gstatic.com",
            "img-src 'self' data: https:",
            f"connect-src {connect_src}",
            "frame-ancestors 'none'",
            "base-uri 'self'",
            "form-action 'self'",
        ])

    def _default_permissions(self) -> str:
        """Get default Permissions-Policy."""
        return ", ".join([
            "accelerometer=()",
            "camera=()",
            "geolocation=()",
            "gyroscope=()",
            "magnetometer=()",
            "microphone=()",
            "payment=()",
            "usb=()",
        ])

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        """Process request and add security headers to response."""
        response = await call_next(request)

        if self.content_type_options:
            response.headers["X-Content-Type-Options"] = self.content_type_options

        if self.frame_options:
            response.headers["X-Frame-Options"] = self.frame_options

        response.headers["X-XSS-Protection"] = "1; mode=block"

        if self.referrer_policy:
            response.headers["Referrer-Policy"] = self.referrer_policy

        if self.csp_policy:
            response.headers["Content-Security-Policy"] = self.csp_policy

        if self.permissions_policy:
            response.headers["Permissions-Policy"] = self.permissions_policy

        if self.enable_hsts:
            response.headers["Strict-Transport-Security"] = (
                f"max-age={self.hsts_max_age}; includeSubDomains"
            )

        return response
