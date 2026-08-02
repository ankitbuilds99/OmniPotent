"""Request tracing middleware with correlation IDs.

Provides end-to-end request tracing for:
- Correlating logs across services
- Performance monitoring
- Debugging production issues
- Distributed tracing integration
"""

from __future__ import annotations

import logging
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

log = logging.getLogger("omnipotent.middleware.tracing")

_trace_context: ContextVar["TraceContext | None"] = ContextVar(
    "trace_context", default=None
)


@dataclass
class TraceContext:
    """Context for request tracing."""

    trace_id: str
    span_id: str
    parent_span_id: str | None = None
    start_time: float = field(default_factory=time.monotonic)
    start_timestamp: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    attributes: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_request(cls, request: Request) -> "TraceContext":
        """Create trace context from incoming request headers."""
        trace_id = (
            request.headers.get("x-trace-id")
            or request.headers.get("x-request-id")
            or uuid.uuid4().hex
        )

        parent_span_id = request.headers.get("x-span-id")
        span_id = uuid.uuid4().hex[:16]

        return cls(
            trace_id=trace_id,
            span_id=span_id,
            parent_span_id=parent_span_id,
        )

    def child_span(self) -> "TraceContext":
        """Create a child span for nested operations."""
        return TraceContext(
            trace_id=self.trace_id,
            span_id=uuid.uuid4().hex[:16],
            parent_span_id=self.span_id,
        )

    @property
    def duration_ms(self) -> float:
        """Get duration since span start in milliseconds."""
        return (time.monotonic() - self.start_time) * 1000

    def set_attribute(self, key: str, value: Any) -> None:
        """Set a trace attribute."""
        self.attributes[key] = value

    def to_headers(self) -> dict[str, str]:
        """Convert to headers for outgoing requests."""
        headers = {
            "x-trace-id": self.trace_id,
            "x-span-id": self.span_id,
        }
        if self.parent_span_id:
            headers["x-parent-span-id"] = self.parent_span_id
        return headers

    def to_log_dict(self) -> dict[str, Any]:
        """Convert to dictionary for structured logging."""
        return {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "duration_ms": round(self.duration_ms, 2),
            **self.attributes,
        }


def get_trace_context() -> TraceContext | None:
    """Get the current trace context."""
    return _trace_context.get()


def set_trace_context(ctx: TraceContext | None) -> None:
    """Set the current trace context."""
    _trace_context.set(ctx)


def get_trace_id() -> str:
    """Get the current trace ID or generate a new one."""
    ctx = get_trace_context()
    return ctx.trace_id if ctx else uuid.uuid4().hex


class TracingMiddleware(BaseHTTPMiddleware):
    """Middleware that adds request tracing.

    Features:
    - Generates or propagates trace IDs
    - Creates span IDs for each request
    - Adds trace context to response headers
    - Integrates with structured logging
    """

    def __init__(
        self,
        app: Any,
        service_name: str = "omnipotent",
        propagate_headers: bool = True,
        sample_rate: float = 1.0,
    ):
        """Initialize tracing middleware.

        Args:
            app: The ASGI application
            service_name: Name of this service for tracing
            propagate_headers: Whether to add trace headers to response
            sample_rate: Fraction of requests to trace (0.0-1.0)
        """
        super().__init__(app)
        self.service_name = service_name
        self.propagate_headers = propagate_headers
        self.sample_rate = sample_rate

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        """Process request with tracing context."""
        ctx = TraceContext.from_request(request)

        ctx.set_attribute("service", self.service_name)
        ctx.set_attribute("http.method", request.method)
        ctx.set_attribute("http.url", str(request.url.path))
        ctx.set_attribute("http.host", request.headers.get("host", ""))

        if request.client:
            ctx.set_attribute("client.ip", request.client.host)

        token = _trace_context.set(ctx)

        try:
            response = await call_next(request)

            ctx.set_attribute("http.status_code", response.status_code)

            if self.propagate_headers:
                response.headers["x-trace-id"] = ctx.trace_id
                response.headers["x-span-id"] = ctx.span_id
                response.headers["x-request-duration-ms"] = str(
                    round(ctx.duration_ms, 2)
                )

            log.info(
                "Request completed",
                extra={
                    "trace": ctx.to_log_dict(),
                    "status_code": response.status_code,
                },
            )

            return response

        except Exception as e:
            ctx.set_attribute("error", True)
            ctx.set_attribute("error.type", type(e).__name__)
            ctx.set_attribute("error.message", str(e)[:500])

            log.error(
                "Request failed",
                extra={"trace": ctx.to_log_dict()},
                exc_info=True,
            )
            raise

        finally:
            _trace_context.reset(token)


def trace_operation(name: str) -> Any:
    """Decorator to trace a function as a child span.

    Example:
        @trace_operation("fetch_logs")
        async def fetch_logs(query: str):
            ...
    """
    import functools

    def decorator(func: Any) -> Any:
        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            parent = get_trace_context()
            if parent:
                child = parent.child_span()
                child.set_attribute("operation", name)
                token = _trace_context.set(child)
                try:
                    result = await func(*args, **kwargs)
                    child.set_attribute("success", True)
                    return result
                except Exception as e:
                    child.set_attribute("error", str(e)[:200])
                    raise
                finally:
                    log.debug(
                        "Operation %s completed in %.2fms",
                        name,
                        child.duration_ms,
                        extra={"trace": child.to_log_dict()},
                    )
                    _trace_context.reset(token)
            else:
                return await func(*args, **kwargs)

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            parent = get_trace_context()
            if parent:
                child = parent.child_span()
                child.set_attribute("operation", name)
                token = _trace_context.set(child)
                try:
                    result = func(*args, **kwargs)
                    child.set_attribute("success", True)
                    return result
                except Exception as e:
                    child.set_attribute("error", str(e)[:200])
                    raise
                finally:
                    log.debug(
                        "Operation %s completed in %.2fms",
                        name,
                        child.duration_ms,
                        extra={"trace": child.to_log_dict()},
                    )
                    _trace_context.reset(token)
            else:
                return func(*args, **kwargs)

        import asyncio

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator
