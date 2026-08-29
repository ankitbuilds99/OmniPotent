"""Production-grade metrics registry with Prometheus exposition.

Provides comprehensive metrics for:
- LLM calls (tokens, latency, cost by provider/model)
- MCP tool invocations (by tool, success/failure)
- Investigation lifecycle (start, complete, timeout, abort)
- Multi-tenant usage (per-org breakdowns)
- Error rates and types
- Queue depths and processing times
- Circuit breaker states
- Cache hit rates
"""

from __future__ import annotations

import logging
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum

from app import model_catalog

log = logging.getLogger("omnipotent.observability.metrics")

LATENCY_BUCKETS = (0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0)
TOKEN_BUCKETS = (100, 500, 1000, 2500, 5000, 10000, 25000, 50000, 100000)

# Models we've already complained about, so an unpriced model on a hot call path
# can't flood the log. Bounded by the number of distinct model ids seen.
_UNPRICED_WARNED: set[str] = set()


class MetricType(Enum):
    COUNTER = "counter"
    GAUGE = "gauge"
    HISTOGRAM = "histogram"
    SUMMARY = "summary"


@dataclass
class MetricValue:
    """A single metric with labels."""

    name: str
    type: MetricType
    help: str
    labels: dict[str, str] = field(default_factory=dict)
    value: float = 0.0
    buckets: dict[float, int] = field(default_factory=dict)
    sum: float = 0.0
    count: int = 0


class MetricsRegistry:
    """Thread-safe metrics registry with Prometheus exposition."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._metrics: dict[str, dict[str, MetricValue]] = defaultdict(dict)
        self._help: dict[str, str] = {}
        self._types: dict[str, MetricType] = {}

    def _label_key(self, labels: dict[str, str]) -> str:
        """Create a unique key from labels."""
        if not labels:
            return ""
        return ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))

    def register(
        self,
        name: str,
        metric_type: MetricType,
        help_text: str,
    ) -> None:
        """Register a new metric."""
        with self._lock:
            self._help[name] = help_text
            self._types[name] = metric_type

    def counter_inc(
        self,
        name: str,
        value: float = 1.0,
        labels: dict[str, str] | None = None,
    ) -> None:
        """Increment a counter."""
        labels = labels or {}
        key = self._label_key(labels)
        with self._lock:
            if name not in self._metrics:
                self._metrics[name] = {}
            if key not in self._metrics[name]:
                self._metrics[name][key] = MetricValue(
                    name=name,
                    type=MetricType.COUNTER,
                    help=self._help.get(name, ""),
                    labels=labels,
                )
            self._metrics[name][key].value += value

    def gauge_set(
        self,
        name: str,
        value: float,
        labels: dict[str, str] | None = None,
    ) -> None:
        """Set a gauge value."""
        labels = labels or {}
        key = self._label_key(labels)
        with self._lock:
            if name not in self._metrics:
                self._metrics[name] = {}
            if key not in self._metrics[name]:
                self._metrics[name][key] = MetricValue(
                    name=name,
                    type=MetricType.GAUGE,
                    help=self._help.get(name, ""),
                    labels=labels,
                )
            self._metrics[name][key].value = value

    def gauge_inc(
        self,
        name: str,
        value: float = 1.0,
        labels: dict[str, str] | None = None,
    ) -> None:
        """Increment a gauge."""
        labels = labels or {}
        key = self._label_key(labels)
        with self._lock:
            if name not in self._metrics:
                self._metrics[name] = {}
            if key not in self._metrics[name]:
                self._metrics[name][key] = MetricValue(
                    name=name,
                    type=MetricType.GAUGE,
                    help=self._help.get(name, ""),
                    labels=labels,
                )
            self._metrics[name][key].value += value

    def histogram_observe(
        self,
        name: str,
        value: float,
        labels: dict[str, str] | None = None,
        buckets: tuple[float, ...] = LATENCY_BUCKETS,
    ) -> None:
        """Record a histogram observation."""
        labels = labels or {}
        key = self._label_key(labels)
        with self._lock:
            if name not in self._metrics:
                self._metrics[name] = {}
            if key not in self._metrics[name]:
                self._metrics[name][key] = MetricValue(
                    name=name,
                    type=MetricType.HISTOGRAM,
                    help=self._help.get(name, ""),
                    labels=labels,
                    buckets={b: 0 for b in buckets},
                )
            metric = self._metrics[name][key]
            metric.sum += value
            metric.count += 1
            for bucket in buckets:
                if value <= bucket:
                    metric.buckets[bucket] = metric.buckets.get(bucket, 0) + 1

    def get_value(
        self,
        name: str,
        labels: dict[str, str] | None = None,
    ) -> float | None:
        """Get current value of a metric."""
        labels = labels or {}
        key = self._label_key(labels)
        with self._lock:
            if name in self._metrics and key in self._metrics[name]:
                return self._metrics[name][key].value
        return None

    def render_prometheus(self) -> str:
        """Render all metrics in Prometheus exposition format."""
        lines: list[str] = []

        with self._lock:
            for name, labeled_metrics in sorted(self._metrics.items()):
                metric_type = self._types.get(name, MetricType.GAUGE)
                help_text = self._help.get(name, "")

                lines.append(f"# HELP {name} {help_text}")
                lines.append(f"# TYPE {name} {metric_type.value}")

                for key, metric in labeled_metrics.items():
                    label_str = f"{{{key}}}" if key else ""

                    if metric.type == MetricType.HISTOGRAM:
                        for bucket, count in sorted(metric.buckets.items()):
                            bucket_labels = f"{key}," if key else ""
                            lines.append(
                                f'{name}_bucket{{{bucket_labels}le="{bucket}"}} {count}'
                            )
                        inf_count = metric.count
                        bucket_labels = f"{key}," if key else ""
                        lines.append(
                            f'{name}_bucket{{{bucket_labels}le="+Inf"}} {inf_count}'
                        )
                        lines.append(f"{name}_sum{label_str} {metric.sum}")
                        lines.append(f"{name}_count{label_str} {metric.count}")
                    else:
                        lines.append(f"{name}{label_str} {metric.value}")

        return "\n".join(lines) + "\n"

    def reset(self) -> None:
        """Reset all metrics (for testing)."""
        with self._lock:
            self._metrics.clear()


_registry = MetricsRegistry()


def _init_metrics() -> None:
    """Initialize all metric definitions."""
    r = _registry

    r.register(
        "copilot_llm_calls_total",
        MetricType.COUNTER,
        "Total LLM API calls",
    )
    r.register(
        "copilot_llm_tokens_input_total",
        MetricType.COUNTER,
        "Total input/prompt tokens sent to LLM",
    )
    r.register(
        "copilot_llm_tokens_output_total",
        MetricType.COUNTER,
        "Total output/completion tokens from LLM",
    )
    r.register(
        "copilot_llm_latency_seconds",
        MetricType.HISTOGRAM,
        "LLM call latency in seconds",
    )
    r.register(
        "copilot_llm_errors_total",
        MetricType.COUNTER,
        "Total LLM API errors",
    )
    r.register(
        "copilot_llm_cost_dollars",
        MetricType.COUNTER,
        "Estimated LLM cost in USD (excludes models with no verified price -- "
        "see copilot_llm_calls_unpriced_total)",
    )
    r.register(
        "copilot_llm_calls_unpriced_total",
        MetricType.COUNTER,
        "LLM calls omitted from copilot_llm_cost_dollars because the model has "
        "no verified price in the catalog. Non-zero means cost is understated.",
    )
    r.register(
        "copilot_llm_cache_hits_total",
        MetricType.COUNTER,
        "LLM prompt cache hits",
    )

    r.register(
        "copilot_mcp_tool_calls_total",
        MetricType.COUNTER,
        "Total MCP tool invocations",
    )
    r.register(
        "copilot_mcp_tool_latency_seconds",
        MetricType.HISTOGRAM,
        "MCP tool call latency in seconds",
    )
    r.register(
        "copilot_mcp_tool_errors_total",
        MetricType.COUNTER,
        "MCP tool call errors, labelled by failure kind (tool_error, rpc_error, "
        "timeout, transport). Only timeout and transport mean the server itself "
        "is in trouble; the other two are a working server answering with an error.",
    )

    r.register(
        "copilot_investigations_active",
        MetricType.GAUGE,
        "Currently active investigations",
    )
    r.register(
        "copilot_investigations_started_total",
        MetricType.COUNTER,
        "Total investigations started",
    )
    r.register(
        "copilot_investigations_completed_total",
        MetricType.COUNTER,
        "Total investigations completed",
    )
    r.register(
        "copilot_investigations_duration_seconds",
        MetricType.HISTOGRAM,
        "Investigation duration in seconds",
    )
    r.register(
        "copilot_investigations_iterations_total",
        MetricType.COUNTER,
        "Total agent iterations across all investigations",
    )

    r.register(
        "copilot_approvals_pending",
        MetricType.GAUGE,
        "Investigations awaiting approval",
    )
    r.register(
        "copilot_approvals_total",
        MetricType.COUNTER,
        "Total approval decisions",
    )
    r.register(
        "copilot_approvals_latency_seconds",
        MetricType.HISTOGRAM,
        "Time between approval request and decision",
    )

    r.register(
        "copilot_tenant_investigations_total",
        MetricType.COUNTER,
        "Investigations by tenant",
    )
    r.register(
        "copilot_tenant_tokens_total",
        MetricType.COUNTER,
        "Tokens consumed by tenant",
    )
    r.register(
        "copilot_tenant_quota_remaining",
        MetricType.GAUGE,
        "Remaining quota for tenant",
    )

    r.register(
        "copilot_circuit_breaker_state",
        MetricType.GAUGE,
        "Circuit breaker state (0=closed, 1=half-open, 2=open)",
    )
    r.register(
        "copilot_circuit_breaker_failures_total",
        MetricType.COUNTER,
        "Circuit breaker failure count",
    )

    r.register(
        "copilot_job_queue_depth",
        MetricType.GAUGE,
        "Jobs waiting in queue",
    )
    r.register(
        "copilot_job_queue_latency_seconds",
        MetricType.HISTOGRAM,
        "Time from enqueue to processing start",
    )
    r.register(
        "copilot_job_queue_processed_total",
        MetricType.COUNTER,
        "Total jobs processed",
    )

    r.register(
        "copilot_websocket_connections_active",
        MetricType.GAUGE,
        "Active WebSocket connections",
    )
    r.register(
        "copilot_websocket_messages_total",
        MetricType.COUNTER,
        "Total WebSocket messages sent/received",
    )

    r.register(
        "copilot_cache_hits_total",
        MetricType.COUNTER,
        "Cache hits",
    )
    r.register(
        "copilot_cache_misses_total",
        MetricType.COUNTER,
        "Cache misses",
    )
    r.register(
        "copilot_cache_size",
        MetricType.GAUGE,
        "Current cache size",
    )

    r.register(
        "copilot_auth_failures_total",
        MetricType.COUNTER,
        "Authentication failures",
    )
    r.register(
        "copilot_rate_limit_hits_total",
        MetricType.COUNTER,
        "Rate limit rejections",
    )
    r.register(
        "copilot_injection_attempts_total",
        MetricType.COUNTER,
        "Detected prompt injection attempts",
    )


_init_metrics()


def get_metrics_registry() -> MetricsRegistry:
    """Get the global metrics registry."""
    return _registry


def record_llm_call(
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    latency_seconds: float,
    cached: bool = False,
    error: str | None = None,
) -> None:
    """Record an LLM call with all dimensions."""
    labels = {"provider": provider, "model": model}

    _registry.counter_inc("copilot_llm_calls_total", labels=labels)
    _registry.counter_inc(
        "copilot_llm_tokens_input_total", input_tokens, labels=labels
    )
    _registry.counter_inc(
        "copilot_llm_tokens_output_total", output_tokens, labels=labels
    )
    _registry.histogram_observe(
        "copilot_llm_latency_seconds", latency_seconds, labels=labels
    )

    if cached:
        _registry.counter_inc("copilot_llm_cache_hits_total", labels=labels)

    if error:
        _registry.counter_inc(
            "copilot_llm_errors_total", labels={**labels, "error": error}
        )

    # None = we have no verified price for this model. Recording a guess would
    # quietly corrupt every cost dashboard built on this counter, so skip it; the
    # token counters above still tell you the call happened and how big it was.
    #
    # But a skipped counter and a genuinely-free call are the same picture on a
    # dashboard: absent series reads as zero, and the warning that explains it is
    # in the logs, which is not where anyone looking at a cost graph is. So the
    # gap is counted here, in the same system, next to the number it undercuts.
    # Alert on it: `copilot_llm_calls_unpriced_total > 0` means the cost graph is
    # understated, and its `model` label names the row to add to the catalog.
    cost = _estimate_llm_cost(provider, model, input_tokens, output_tokens)
    if cost is not None:
        _registry.counter_inc("copilot_llm_cost_dollars", cost, labels=labels)
    else:
        _registry.counter_inc("copilot_llm_calls_unpriced_total", labels=labels)


def _estimate_llm_cost(
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> float | None:
    """Estimate LLM cost in USD, or None when the model has no verified price.

    Delegates to the model catalog. The table this replaced matched with
    ``m in model``, so ``claude-opus-4-8`` hit the ``claude-opus-4`` row and
    over-reported cost threefold, and anything unmatched fell back to an invented
    (1.0, 3.0) rate. ``provider`` is kept in the signature for the call site's
    labels but is no longer part of the lookup — the catalog owns the mapping from
    model id to provider, so the two can't disagree.
    """
    cost = model_catalog.estimate_cost_usd(model, input_tokens, output_tokens)
    if cost is None and model not in _UNPRICED_WARNED:
        _UNPRICED_WARNED.add(model)
        log.warning(
            "no verified price for model %r (provider %r); "
            "cost metric skipped for this model. Add it to app/model_catalog.py.",
            model,
            provider,
        )
    return cost


# `error` becomes a Prometheus label, and this registry never evicts a series, so
# its value set has to stay small. The call site (app/mcp/client.py) classifies
# every failure into a closed set of four kinds before calling; this is the
# backstop for everyone else, because the value that must never arrive here is an
# exception *message* — MCP failures quote what the model asked for, so a path or
# a query would become a permanent new time series per distinct request.
#
# Admitting anything that "reads like an identifier" was too weak to be that
# backstop. `[A-Za-z0-9_.-]` up to 48 characters also describes a UUID, a git
# branch name and a request id, each of which still minted one permanent series
# per distinct value — the exact failure this is here to prevent, so the bound
# held only because the one in-tree caller was already careful.
#
# Two things are admitted now, and nothing else. The four classified kinds, which
# alert rules group by and which must survive verbatim; and a bare Python
# exception class name, which `record_mcp_tool_call` documents as accepted and
# which is drawn from the finite set of classes in a program rather than from
# request data. Requiring CamelCase with no separator is what tells `TimeoutError`
# apart from `550e8400-e29b-41d4-a716-446655440000`, `release-2026.08` and
# `req_9f2c`: an identifier built out of a value carries a separator, a class name
# does not.
_ERROR_LABEL_MAX_LEN = 48
_ERROR_LABEL_KINDS = frozenset({"tool_error", "rpc_error", "timeout", "transport"})
_ERROR_LABEL_UNCLASSIFIED = "unclassified"


def _looks_like_an_exception_class(error: str) -> bool:
    """`ConnectionTimeout` yes, `550e8400-e29b-41d4-...` no.

    A leading capital and letters-and-digits only. Separators are what values get
    joined with, so refusing them is what keeps the set finite.
    """
    return (
        len(error) <= _ERROR_LABEL_MAX_LEN
        and error[0].isupper()
        and error.isalnum()
        and error.isascii()
    )


def _bounded_error_label(error: str | None) -> str:
    """An `error` label value safe to mint a time series for.

    Anything outside the admitted set is counted under "unclassified" rather than
    dropped: the failure still shows up, and the bucket name says the detail was
    withheld instead of implying there was none.
    """
    if not error:
        # Absent, not zero: something failed and the caller said nothing about
        # what. "unknown" is the long-standing value for that and alert rules
        # group on it.
        return "unknown"
    if error in _ERROR_LABEL_KINDS or _looks_like_an_exception_class(error):
        return error
    return _ERROR_LABEL_UNCLASSIFIED


def record_mcp_tool_call(
    server: str,
    tool: str,
    latency_seconds: float,
    success: bool = True,
    error: str | None = None,
) -> None:
    """Record an MCP tool invocation.

    `error` is a metric label, not a message: pass a failure *kind* (see
    `app.mcp.client._ERROR_KINDS`) or an exception class name. Free-form text is
    bucketed as "unclassified" rather than being turned into its own series.
    """
    labels = {"server": server, "tool": tool}
    status = "success" if success else "error"

    _registry.counter_inc(
        "copilot_mcp_tool_calls_total", labels={**labels, "status": status}
    )
    _registry.histogram_observe(
        "copilot_mcp_tool_latency_seconds", latency_seconds, labels=labels
    )

    if not success:
        _registry.counter_inc(
            "copilot_mcp_tool_errors_total",
            labels={**labels, "error": _bounded_error_label(error)},
        )


def record_investigation_start(org_id: str = "") -> None:
    """Record investigation started."""
    _registry.counter_inc("copilot_investigations_started_total")
    _registry.gauge_inc("copilot_investigations_active")

    if org_id:
        _registry.counter_inc(
            "copilot_tenant_investigations_total", labels={"org_id": org_id}
        )


def record_investigation_complete(
    duration_seconds: float,
    status: str,
    tokens_used: int = 0,
    org_id: str = "",
) -> None:
    """Record investigation completed."""
    _registry.counter_inc(
        "copilot_investigations_completed_total", labels={"status": status}
    )
    _registry.gauge_inc("copilot_investigations_active", -1)
    _registry.histogram_observe(
        "copilot_investigations_duration_seconds",
        duration_seconds,
        labels={"status": status},
    )

    if org_id and tokens_used:
        _registry.counter_inc(
            "copilot_tenant_tokens_total", tokens_used, labels={"org_id": org_id}
        )


def record_approval(approved: bool, latency_seconds: float) -> None:
    """Record an approval decision."""
    _registry.counter_inc(
        "copilot_approvals_total", labels={"approved": str(approved).lower()}
    )
    _registry.gauge_inc("copilot_approvals_pending", -1)
    _registry.histogram_observe("copilot_approvals_latency_seconds", latency_seconds)


def record_circuit_breaker_state(provider: str, state: str) -> None:
    """Record circuit breaker state change."""
    state_value = {"closed": 0, "half_open": 1, "open": 2}.get(state, 0)
    _registry.gauge_set(
        "copilot_circuit_breaker_state", state_value, labels={"provider": provider}
    )


def record_injection_attempt(pattern: str, source: str) -> None:
    """Record a detected prompt injection attempt."""
    _registry.counter_inc(
        "copilot_injection_attempts_total", labels={"pattern": pattern, "source": source}
    )


def record_websocket_connection(delta: int = 1) -> None:
    """Record WebSocket connection change."""
    _registry.gauge_inc("copilot_websocket_connections_active", delta)


def record_websocket_message(direction: str = "received") -> None:
    """Record WebSocket message."""
    _registry.counter_inc(
        "copilot_websocket_messages_total", labels={"direction": direction}
    )


def record_cache_access(hit: bool) -> None:
    """Record cache access."""
    if hit:
        _registry.counter_inc("copilot_cache_hits_total")
    else:
        _registry.counter_inc("copilot_cache_misses_total")


def record_auth_failure(reason: str) -> None:
    """Record authentication failure."""
    _registry.counter_inc("copilot_auth_failures_total", labels={"reason": reason})


def record_rate_limit(endpoint: str) -> None:
    """Record rate limit hit."""
    _registry.counter_inc("copilot_rate_limit_hits_total", labels={"endpoint": endpoint})
