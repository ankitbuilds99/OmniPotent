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

from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


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
        """
        super().__init__(app)
        self.enable_hsts = enable_hsts
        self.hsts_max_age = hsts_max_age
        self.frame_options = frame_options
        self.content_type_options = content_type_options
        self.referrer_policy = referrer_policy

        self.csp_policy = csp_policy or self._default_csp()
        self.permissions_policy = permissions_policy or self._default_permissions()

    def _default_csp(self) -> str:
        """Get default Content-Security-Policy."""
        return "; ".join([
            "default-src 'self'",
            "script-src 'self' 'unsafe-inline'",
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
            "font-src 'self' https://fonts.gstatic.com",
            "img-src 'self' data: https:",
            "connect-src 'self' https://api.anthropic.com https://api.openai.com",
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
