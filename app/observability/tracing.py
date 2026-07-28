"""Full distributed tracing with OpenTelemetry.

Provides:
- Automatic trace context propagation
- Spans for all key operations (LLM, MCP, DB)
- Baggage for tenant/user context
- Integration with Jaeger/Zipkin/OTLP backends
- Sampling strategies
"""

from __future__ import annotations

import asyncio
import functools
import logging
import os
from contextlib import contextmanager
from typing import Any, Callable, Iterator, TypeVar

log = logging.getLogger("devcopilot.observability.tracing")

_INITIALIZED = False
_TRACER = None
_ENABLED = False

T = TypeVar("T")


def init_tracing(
    service_name: str = "devops-copilot",
    sample_rate: float = 1.0,
) -> bool:
    """Initialize OpenTelemetry tracing.

    Enabled when OTEL_EXPORTER_OTLP_ENDPOINT is set.

    Args:
        service_name: Service name for traces
        sample_rate: Fraction of traces to sample (0.0-1.0)

    Returns:
        True if tracing was enabled
    """
    global _INITIALIZED, _TRACER, _ENABLED

    if _INITIALIZED:
        return _ENABLED

    _INITIALIZED = True
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()

    if not endpoint:
        log.debug("OTEL_EXPORTER_OTLP_ENDPOINT not set, tracing disabled")
        return False

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import SERVICE_NAME, Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.trace.sampling import TraceIdRatioBased

        resource = Resource.create(
            {
                SERVICE_NAME: service_name,
                "service.version": os.environ.get("APP_VERSION", "unknown"),
                "deployment.environment": os.environ.get(
                    "COPILOT_ENV", "development"
                ),
            }
        )

        sampler = TraceIdRatioBased(sample_rate)
        provider = TracerProvider(resource=resource, sampler=sampler)

        otlp_exporter = OTLPSpanExporter(endpoint=endpoint)
        provider.add_span_processor(BatchSpanProcessor(otlp_exporter))

        trace.set_tracer_provider(provider)

        _TRACER = trace.get_tracer("devops-copilot", "1.0.0")
        _ENABLED = True

        log.info(
            "OpenTelemetry tracing enabled: endpoint=%s sample_rate=%.2f",
            endpoint,
            sample_rate,
        )

        _auto_instrument()

        return True

    except ImportError as e:
        log.warning("OpenTelemetry not installed: %s", e)
        return False
    except Exception as e:
        log.error("Failed to initialize tracing: %s", e)
        return False


def _auto_instrument() -> None:
    """Auto-instrument common libraries."""
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor().instrument()
        log.debug("Instrumented FastAPI")
    except ImportError:
        pass

    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        HTTPXClientInstrumentor().instrument()
        log.debug("Instrumented httpx")
    except ImportError:
        pass

    try:
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

        SQLAlchemyInstrumentor().instrument()
        log.debug("Instrumented SQLAlchemy")
    except ImportError:
        pass

    try:
        from opentelemetry.instrumentation.redis import RedisInstrumentor

        RedisInstrumentor().instrument()
        log.debug("Instrumented Redis")
    except ImportError:
        pass


def is_enabled() -> bool:
    """Check if tracing is enabled."""
    return _ENABLED


def get_tracer() -> Any:
    """Get the OpenTelemetry tracer."""
    return _TRACER


@contextmanager
def span(
    name: str,
    attributes: dict[str, Any] | None = None,
    kind: str = "internal",
) -> Iterator[Any]:
    """Create a traced span.

    Usage:
        with span("fetch_logs", {"service": "api"}) as s:
            s.set_attribute("log_count", 100)
            # ... do work

    Args:
        name: Span name
        attributes: Initial span attributes
        kind: Span kind (internal, server, client, producer, consumer)
    """
    if not _ENABLED or _TRACER is None:
        yield None
        return

    from opentelemetry.trace import SpanKind

    kind_map = {
        "internal": SpanKind.INTERNAL,
        "server": SpanKind.SERVER,
        "client": SpanKind.CLIENT,
        "producer": SpanKind.PRODUCER,
        "consumer": SpanKind.CONSUMER,
    }

    with _TRACER.start_as_current_span(
        name,
        kind=kind_map.get(kind, SpanKind.INTERNAL),
    ) as s:
        if attributes:
            for k, v in attributes.items():
                try:
                    s.set_attribute(k, _coerce_attribute(v))
                except Exception:
                    pass
        yield s


def _coerce_attribute(value: Any) -> Any:
    """Coerce a value to an acceptable span attribute type."""
    if isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, (list, tuple)):
        return [_coerce_attribute(v) for v in value]
    return str(value)


def traced(
    name: str | None = None,
    attributes: dict[str, Any] | None = None,
    record_args: bool = False,
    record_result: bool = False,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator to trace a function.

    Usage:
        @traced("fetch_user", {"component": "auth"})
        async def fetch_user(user_id: str) -> User:
            ...

    Args:
        name: Span name (defaults to function name)
        attributes: Static attributes to add to span
        record_args: Whether to record function arguments
        record_result: Whether to record return value
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        span_name = name or func.__name__

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            if not _ENABLED:
                return await func(*args, **kwargs)

            span_attrs = dict(attributes or {})
            span_attrs["function"] = func.__name__

            if record_args:
                span_attrs["args"] = str(args)[:500]
                span_attrs["kwargs"] = str(kwargs)[:500]

            with span(span_name, span_attrs) as s:
                try:
                    result = await func(*args, **kwargs)
                    if record_result and s:
                        s.set_attribute("result", str(result)[:500])
                    return result
                except Exception as e:
                    if s:
                        s.set_attribute("error", True)
                        s.set_attribute("error.type", type(e).__name__)
                        s.set_attribute("error.message", str(e)[:500])
                        s.record_exception(e)
                    raise

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            if not _ENABLED:
                return func(*args, **kwargs)

            span_attrs = dict(attributes or {})
            span_attrs["function"] = func.__name__

            if record_args:
                span_attrs["args"] = str(args)[:500]
                span_attrs["kwargs"] = str(kwargs)[:500]

            with span(span_name, span_attrs) as s:
                try:
                    result = func(*args, **kwargs)
                    if record_result and s:
                        s.set_attribute("result", str(result)[:500])
                    return result
                except Exception as e:
                    if s:
                        s.set_attribute("error", True)
                        s.set_attribute("error.type", type(e).__name__)
                        s.set_attribute("error.message", str(e)[:500])
                        s.record_exception(e)
                    raise

        if asyncio.iscoroutinefunction(func):
            return async_wrapper  # type: ignore[return-value]
        return sync_wrapper  # type: ignore[return-value]

    return decorator


def add_span_attributes(**attributes: Any) -> None:
    """Add attributes to the current span.

    Usage:
        add_span_attributes(user_id="123", action="approve")
    """
    if not _ENABLED:
        return

    from opentelemetry import trace

    current_span = trace.get_current_span()

    if current_span and current_span.is_recording():
        for k, v in attributes.items():
            try:
                current_span.set_attribute(k, _coerce_attribute(v))
            except Exception:
                pass


def record_exception(
    exception: Exception, attributes: dict[str, Any] | None = None
) -> None:
    """Record an exception in the current span."""
    if not _ENABLED:
        return

    from opentelemetry import trace

    current_span = trace.get_current_span()

    if current_span and current_span.is_recording():
        current_span.record_exception(exception)
        current_span.set_attribute("error", True)
        if attributes:
            for k, v in attributes.items():
                current_span.set_attribute(k, _coerce_attribute(v))


def get_trace_context() -> dict[str, str]:
    """Get current trace context for propagation.

    Returns:
        Dict with traceparent and tracestate headers
    """
    if not _ENABLED:
        return {}

    from opentelemetry.propagate import inject

    headers: dict[str, str] = {}
    inject(headers)
    return headers


def set_baggage(key: str, value: str) -> None:
    """Set baggage that propagates across service boundaries.

    Usage:
        set_baggage("tenant_id", "org_123")
    """
    if not _ENABLED:
        return

    try:
        from opentelemetry import baggage, context

        ctx = baggage.set_baggage(key, value)
        context.attach(ctx)
    except Exception:
        pass


def get_baggage(key: str) -> str | None:
    """Get a baggage value."""
    if not _ENABLED:
        return None

    try:
        from opentelemetry import baggage

        result = baggage.get_baggage(key)
        return str(result) if result is not None else None
    except Exception:
        return None


@contextmanager
def llm_span(
    provider: str,
    model: str,
    operation: str = "chat",
) -> Iterator[Any]:
    """Create a span for an LLM call."""
    with span(
        f"llm.{operation}",
        {
            "llm.provider": provider,
            "llm.model": model,
            "llm.operation": operation,
        },
        kind="client",
    ) as s:
        yield s


@contextmanager
def mcp_span(
    server: str,
    tool: str,
) -> Iterator[Any]:
    """Create a span for an MCP tool call."""
    with span(
        f"mcp.{server}.{tool}",
        {
            "mcp.server": server,
            "mcp.tool": tool,
        },
        kind="client",
    ) as s:
        yield s


@contextmanager
def investigation_span(
    thread_id: str,
    org_id: str = "",
) -> Iterator[Any]:
    """Create a span for an investigation."""
    with span(
        "investigation",
        {
            "investigation.thread_id": thread_id,
            "investigation.org_id": org_id,
        },
        kind="server",
    ) as s:
        yield s


def _reset_for_tests() -> None:
    """Reset tracing state for tests."""
    global _INITIALIZED, _TRACER, _ENABLED
    _INITIALIZED, _TRACER, _ENABLED = False, None, False
