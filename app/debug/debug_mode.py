"""Debug mode for detailed request/response logging and diagnostics.

Provides detailed visibility into request processing, LLM calls,
tool executions, and performance metrics for debugging.
"""

from __future__ import annotations

import functools
import logging
import time
import traceback
import uuid
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, AsyncGenerator, Callable, Generator, TypeVar

log = logging.getLogger("omnipotent.debug")

F = TypeVar("F", bound=Callable[..., Any])


class DebugLevel(Enum):
    """Debug verbosity levels."""

    OFF = 0
    BASIC = 1  # Request/response summary
    DETAILED = 2  # Include headers, timing
    VERBOSE = 3  # Include payloads, internal state
    TRACE = 4  # Everything including stack traces


@dataclass
class DebugSpan:
    """A span in the debug trace."""

    span_id: str
    name: str
    started_at: datetime
    ended_at: datetime | None = None
    parent_id: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    status: str = "ok"
    error: str | None = None

    @property
    def duration_ms(self) -> float | None:
        """Get span duration in milliseconds."""
        if self.ended_at:
            return (self.ended_at - self.started_at).total_seconds() * 1000
        return None

    def add_event(self, name: str, attributes: dict[str, Any] | None = None) -> None:
        """Add an event to the span."""
        self.events.append(
            {
                "name": name,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "attributes": attributes or {},
            }
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "span_id": self.span_id,
            "name": self.name,
            "parent_id": self.parent_id,
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "duration_ms": self.duration_ms,
            "attributes": self.attributes,
            "events": self.events,
            "status": self.status,
            "error": self.error,
        }


@dataclass
class DebugContext:
    """Context for a debug session."""

    request_id: str
    level: DebugLevel
    started_at: datetime
    spans: list[DebugSpan] = field(default_factory=list)
    current_span_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    logs: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "request_id": self.request_id,
            "level": self.level.name,
            "started_at": self.started_at.isoformat(),
            "duration_ms": (
                datetime.now(timezone.utc) - self.started_at
            ).total_seconds()
            * 1000,
            "metadata": self.metadata,
            "spans": [s.to_dict() for s in self.spans],
            "logs": self.logs,
        }


class DebugSession:
    """Manages debug mode for a request."""

    _current: DebugContext | None = None

    def __init__(
        self,
        level: DebugLevel = DebugLevel.BASIC,
        request_id: str | None = None,
    ):
        self.context = DebugContext(
            request_id=request_id or str(uuid.uuid4()),
            level=level,
            started_at=datetime.now(timezone.utc),
        )

    def __enter__(self) -> DebugSession:
        DebugSession._current = self.context
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        DebugSession._current = None

    async def __aenter__(self) -> DebugSession:
        DebugSession._current = self.context
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        DebugSession._current = None

    @classmethod
    def current(cls) -> DebugContext | None:
        """Get current debug context."""
        return cls._current

    @contextmanager
    def span(
        self,
        name: str,
        attributes: dict[str, Any] | None = None,
    ) -> Generator[DebugSpan, None, None]:
        """Create a debug span."""
        span = DebugSpan(
            span_id=str(uuid.uuid4()),
            name=name,
            started_at=datetime.now(timezone.utc),
            parent_id=self.context.current_span_id,
            attributes=attributes or {},
        )
        self.context.spans.append(span)
        previous_span = self.context.current_span_id
        self.context.current_span_id = span.span_id

        try:
            yield span
        except Exception as e:
            span.status = "error"
            span.error = str(e)
            if self.context.level.value >= DebugLevel.TRACE.value:
                span.attributes["traceback"] = traceback.format_exc()
            raise
        finally:
            span.ended_at = datetime.now(timezone.utc)
            self.context.current_span_id = previous_span

    @asynccontextmanager
    async def aspan(
        self,
        name: str,
        attributes: dict[str, Any] | None = None,
    ) -> AsyncGenerator[DebugSpan, None]:
        """Create an async debug span."""
        with self.span(name, attributes) as s:
            yield s

    def log(
        self,
        level: str,
        message: str,
        **kwargs: Any,
    ) -> None:
        """Add a log entry."""
        if self.context.level.value >= DebugLevel.DETAILED.value:
            self.context.logs.append(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "level": level,
                    "message": message,
                    "data": kwargs,
                }
            )

    def set_metadata(self, key: str, value: Any) -> None:
        """Set metadata on the debug context."""
        self.context.metadata[key] = value

    def get_trace(self) -> dict[str, Any]:
        """Get the full debug trace."""
        return self.context.to_dict()


def debug_enabled() -> bool:
    """Check if debug mode is enabled for current request."""
    return DebugSession.current() is not None


def get_debug_level() -> DebugLevel:
    """Get current debug level."""
    ctx = DebugSession.current()
    return ctx.level if ctx else DebugLevel.OFF


def debug_log(
    message: str,
    level: str = "info",
    min_level: DebugLevel = DebugLevel.BASIC,
    **kwargs: Any,
) -> None:
    """Log a debug message if debug mode is enabled."""
    ctx = DebugSession.current()
    if ctx and ctx.level.value >= min_level.value:
        ctx.logs.append(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "level": level,
                "message": message,
                "data": kwargs,
            }
        )


def debug_span(name: str, **attrs: Any) -> Callable[[F], F]:
    """Decorator to wrap a function in a debug span."""

    def decorator(func: F) -> F:
        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            ctx = DebugSession.current()
            if not ctx:
                return func(*args, **kwargs)

            span = DebugSpan(
                span_id=str(uuid.uuid4()),
                name=name,
                started_at=datetime.now(timezone.utc),
                parent_id=ctx.current_span_id,
                attributes=attrs.copy(),
            )
            ctx.spans.append(span)
            previous = ctx.current_span_id
            ctx.current_span_id = span.span_id

            try:
                result = func(*args, **kwargs)
                return result
            except Exception as e:
                span.status = "error"
                span.error = str(e)
                raise
            finally:
                span.ended_at = datetime.now(timezone.utc)
                ctx.current_span_id = previous

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            ctx = DebugSession.current()
            if not ctx:
                return await func(*args, **kwargs)

            span = DebugSpan(
                span_id=str(uuid.uuid4()),
                name=name,
                started_at=datetime.now(timezone.utc),
                parent_id=ctx.current_span_id,
                attributes=attrs.copy(),
            )
            ctx.spans.append(span)
            previous = ctx.current_span_id
            ctx.current_span_id = span.span_id

            try:
                result = await func(*args, **kwargs)
                return result
            except Exception as e:
                span.status = "error"
                span.error = str(e)
                raise
            finally:
                span.ended_at = datetime.now(timezone.utc)
                ctx.current_span_id = previous

        import asyncio

        if asyncio.iscoroutinefunction(func):
            return async_wrapper  # type: ignore
        return sync_wrapper  # type: ignore

    return decorator


@dataclass
class DebugTimer:
    """Simple timer for measuring durations."""

    name: str
    start_time: float = field(default_factory=time.perf_counter)
    checkpoints: list[tuple[str, float]] = field(default_factory=list)

    def checkpoint(self, name: str) -> float:
        """Record a checkpoint and return elapsed time."""
        elapsed = time.perf_counter() - self.start_time
        self.checkpoints.append((name, elapsed))
        return elapsed

    def elapsed(self) -> float:
        """Get total elapsed time."""
        return time.perf_counter() - self.start_time

    def summary(self) -> dict[str, Any]:
        """Get timing summary."""
        return {
            "name": self.name,
            "total_ms": self.elapsed() * 1000,
            "checkpoints": [
                {"name": n, "elapsed_ms": t * 1000} for n, t in self.checkpoints
            ],
        }


class DebugMiddleware:
    """ASGI middleware for debug mode."""

    def __init__(
        self,
        app: Any,
        header_name: str = "X-Debug-Mode",
        query_param: str = "debug",
    ):
        self.app = app
        self.header_name = header_name.lower()
        self.query_param = query_param

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[..., Any],
        send: Callable[..., Any],
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        debug_level = self._get_debug_level(scope)

        if debug_level == DebugLevel.OFF:
            await self.app(scope, receive, send)
            return

        request_id = self._get_request_id(scope)

        async with DebugSession(level=debug_level, request_id=request_id):
            debug_log(
                "request_started",
                method=scope.get("method"),
                path=scope.get("path"),
                query=scope.get("query_string", b"").decode(),
            )

            response_started = False
            response_headers: list[tuple[bytes, bytes]] = []

            async def send_wrapper(message: dict[str, Any]) -> None:
                nonlocal response_started, response_headers
                if message["type"] == "http.response.start":
                    response_started = True
                    response_headers = list(message.get("headers", []))
                    ctx = DebugSession.current()
                    if ctx:
                        trace = ctx.to_dict()
                        response_headers.append(
                            (b"x-debug-request-id", request_id.encode())
                        )
                        response_headers.append(
                            (
                                b"x-debug-duration-ms",
                                str(int(trace["duration_ms"])).encode(),
                            )
                        )
                    message = {**message, "headers": response_headers}
                await send(message)

            try:
                await self.app(scope, receive, send_wrapper)
            finally:
                debug_log("request_completed")

    def _get_debug_level(self, scope: dict[str, Any]) -> DebugLevel:
        """Extract debug level from request."""
        headers = dict(scope.get("headers", []))
        header_value = headers.get(self.header_name.encode(), b"").decode()

        if header_value:
            try:
                return DebugLevel[header_value.upper()]
            except KeyError:
                if header_value.lower() in ("1", "true", "yes"):
                    return DebugLevel.BASIC

        query_string = scope.get("query_string", b"").decode()
        for param in query_string.split("&"):
            if "=" in param:
                key, value = param.split("=", 1)
                if key == self.query_param:
                    try:
                        return DebugLevel[value.upper()]
                    except KeyError:
                        if value.lower() in ("1", "true", "yes"):
                            return DebugLevel.BASIC

        return DebugLevel.OFF

    def _get_request_id(self, scope: dict[str, Any]) -> str:
        """Get or generate request ID."""
        headers = dict(scope.get("headers", []))
        request_id = headers.get(b"x-request-id", b"").decode()
        return request_id or str(uuid.uuid4())
