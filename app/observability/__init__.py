"""Advanced observability infrastructure for OmniPotent.

Provides:
- Comprehensive Prometheus metrics registry
- OpenTelemetry distributed tracing
- SLO tracking with error budget burn rates
- LLM and MCP tool instrumentation
- Logging and third-party APM integration (LangSmith, Datadog, Sentry)
"""

from app.observability_core import (
    _JsonFormatter,
    configure_logging,
    init,
    request_id_var,
    setup_datadog_apm,
    setup_langsmith,
    setup_sentry,
)
from app.observability.metrics import (
    MetricsRegistry,
    MetricType,
    get_metrics_registry,
    record_approval,
    record_circuit_breaker_state,
    record_injection_attempt,
    record_investigation_complete,
    record_investigation_start,
    record_llm_call,
    record_mcp_tool_call,
)
from app.observability.slo import (
    SLODefinition,
    SLOStatus,
    SLOTracker,
    get_slo_tracker,
    record_investigation_slo,
    record_llm_slo,
)
from app.observability.tracing import (
    add_span_attributes,
    get_baggage,
    get_trace_context,
    get_tracer,
    init_tracing,
    investigation_span,
    is_enabled,
    llm_span,
    mcp_span,
    record_exception,
    set_baggage,
    span,
    traced,
)

__all__ = [
    # Legacy logging/APM
    "_JsonFormatter",
    "configure_logging",
    "init",
    "request_id_var",
    "setup_datadog_apm",
    "setup_langsmith",
    "setup_sentry",
    # Metrics
    "MetricsRegistry",
    "MetricType",
    "get_metrics_registry",
    "record_llm_call",
    "record_mcp_tool_call",
    "record_investigation_start",
    "record_investigation_complete",
    "record_approval",
    "record_circuit_breaker_state",
    "record_injection_attempt",
    # Tracing
    "init_tracing",
    "is_enabled",
    "get_tracer",
    "span",
    "traced",
    "add_span_attributes",
    "record_exception",
    "get_trace_context",
    "set_baggage",
    "get_baggage",
    "llm_span",
    "mcp_span",
    "investigation_span",
    # SLO
    "SLODefinition",
    "SLOStatus",
    "SLOTracker",
    "get_slo_tracker",
    "record_investigation_slo",
    "record_llm_slo",
]
