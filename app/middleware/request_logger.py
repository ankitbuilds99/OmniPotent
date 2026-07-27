"""Structured request logging middleware.

Provides:
- JSON-formatted request/response logs
- Performance timing
- Request body sampling (for debugging)
- PII redaction
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.middleware.tracing import get_trace_context

log = logging.getLogger("devcopilot.middleware.request_logger")


SENSITIVE_HEADERS = frozenset({
    "authorization",
    "x-api-key",
    "cookie",
    "set-cookie",
    "x-auth-token",
})

SENSITIVE_FIELDS = frozenset({
    "password",
    "token",
    "secret",
    "api_key",
    "apikey",
    "credential",
    "auth",
})


@dataclass
class RequestLog:
    """Structured request log entry."""

    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    method: str = ""
    path: str = ""
    query_string: str = ""
    status_code: int = 0
    duration_ms: float = 0.0
    request_size: int = 0
    response_size: int = 0
    client_ip: str = ""
    user_agent: str = ""
    trace_id: str = ""
    span_id: str = ""
    user_id: str = ""
    org_id: str = ""
    error: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON logging."""
        data = {
            "timestamp": self.timestamp.isoformat(),
            "method": self.method,
            "path": self.path,
            "status_code": self.status_code,
            "duration_ms": round(self.duration_ms, 2),
            "request_size": self.request_size,
            "response_size": self.response_size,
            "client_ip": self.client_ip,
            "trace_id": self.trace_id,
        }

        if self.query_string:
            data["query_string"] = self.query_string
        if self.user_agent:
            data["user_agent"] = self.user_agent
        if self.span_id:
            data["span_id"] = self.span_id
        if self.user_id:
            data["user_id"] = self.user_id
        if self.org_id:
            data["org_id"] = self.org_id
        if self.error:
            data["error"] = self.error
        if self.extra:
            data.update(self.extra)

        return data


def _redact_headers(headers: dict[str, str]) -> dict[str, str]:
    """Redact sensitive header values."""
    result = {}
    for key, value in headers.items():
        if key.lower() in SENSITIVE_HEADERS:
            result[key] = "[REDACTED]"
        else:
            result[key] = value
    return result


def _redact_body(body: dict[str, Any]) -> dict[str, Any]:
    """Redact sensitive fields from request body."""
    result: dict[str, Any] = {}
    for key, value in body.items():
        key_lower = key.lower()
        if any(s in key_lower for s in SENSITIVE_FIELDS):
            result[key] = "[REDACTED]"
        elif isinstance(value, dict):
            result[key] = _redact_body(value)
        elif isinstance(value, str) and len(value) > 100:
            result[key] = value[:50] + "...[truncated]"
        else:
            result[key] = value
    return result


class RequestLoggerMiddleware(BaseHTTPMiddleware):
    """Middleware that logs all requests in structured format.

    Features:
    - JSON log format for aggregation
    - Performance timing
    - Optional request body logging (with redaction)
    - Integration with tracing
    """

    def __init__(
        self,
        app: Any,
        log_request_body: bool = False,
        log_response_body: bool = False,
        exclude_paths: set[str] | None = None,
        slow_request_threshold_ms: float = 1000.0,
    ):
        """Initialize request logger.

        Args:
            app: The ASGI application
            log_request_body: Whether to log request bodies
            log_response_body: Whether to log response bodies
            exclude_paths: Paths to exclude from logging
            slow_request_threshold_ms: Threshold for slow request warnings
        """
        super().__init__(app)
        self.log_request_body = log_request_body
        self.log_response_body = log_response_body
        self.exclude_paths = exclude_paths or {"/healthz", "/readyz", "/metrics"}
        self.slow_request_threshold_ms = slow_request_threshold_ms

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        """Process request with logging."""
        if request.url.path in self.exclude_paths:
            return await call_next(request)

        start_time = time.monotonic()

        request_log = RequestLog(
            method=request.method,
            path=request.url.path,
            query_string=str(request.url.query) if request.url.query else "",
            client_ip=request.client.host if request.client else "",
            user_agent=request.headers.get("user-agent", "")[:200],
        )

        content_length = request.headers.get("content-length")
        if content_length and content_length.isdigit():
            request_log.request_size = int(content_length)

        ctx = get_trace_context()
        if ctx:
            request_log.trace_id = ctx.trace_id
            request_log.span_id = ctx.span_id

        try:
            from app import tenant_context
            cfg = tenant_context.get_tenant()
            if cfg:
                request_log.org_id = cfg.org_id
            actor = tenant_context.get_actor()
            if actor:
                request_log.user_id = actor
        except Exception:
            pass

        try:
            response = await call_next(request)

            request_log.status_code = response.status_code
            request_log.duration_ms = (time.monotonic() - start_time) * 1000

            content_length = response.headers.get("content-length")
            if content_length and content_length.isdigit():
                request_log.response_size = int(content_length)

            log_level = logging.INFO
            if response.status_code >= 500:
                log_level = logging.ERROR
            elif response.status_code >= 400:
                log_level = logging.WARNING

            if request_log.duration_ms > self.slow_request_threshold_ms:
                request_log.extra["slow_request"] = True
                log_level = max(log_level, logging.WARNING)

            log.log(
                log_level,
                "%s %s %d %.2fms",
                request.method,
                request.url.path,
                response.status_code,
                request_log.duration_ms,
                extra={"request_log": request_log.to_dict()},
            )

            return response

        except Exception as e:
            request_log.status_code = 500
            request_log.duration_ms = (time.monotonic() - start_time) * 1000
            request_log.error = f"{type(e).__name__}: {str(e)[:200]}"

            log.error(
                "%s %s failed: %s",
                request.method,
                request.url.path,
                type(e).__name__,
                extra={"request_log": request_log.to_dict()},
                exc_info=True,
            )
            raise
