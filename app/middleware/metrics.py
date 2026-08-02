"""Request metrics collection middleware.

Provides:
- Request count by endpoint and status
- Latency histograms with P50/P95/P99 percentiles
- Error rate tracking
- Active request gauge
- Integration with Prometheus
- Request ID tracking for correlation
"""

from __future__ import annotations

import bisect
import logging
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

log = logging.getLogger("omnipotent.middleware.metrics")

# Histogram buckets for latency (in milliseconds)
LATENCY_BUCKETS_MS = (10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000, 30000)


@dataclass
class LatencyTracker:
    """Tracks latency values for percentile calculation."""

    values: list[float] = field(default_factory=list)
    max_samples: int = 10000

    def add(self, value_ms: float) -> None:
        """Add a latency sample, maintaining sorted order."""
        if len(self.values) >= self.max_samples:
            self.values.pop(0)
        bisect.insort(self.values, value_ms)

    def percentile(self, p: float) -> float:
        """Calculate percentile (0-100)."""
        if not self.values:
            return 0.0
        idx = int(len(self.values) * p / 100)
        idx = min(idx, len(self.values) - 1)
        return self.values[idx]

    @property
    def p50(self) -> float:
        return self.percentile(50)

    @property
    def p95(self) -> float:
        return self.percentile(95)

    @property
    def p99(self) -> float:
        return self.percentile(99)


@dataclass
class RequestMetrics:
    """Collected request metrics."""

    total_requests: int = 0
    total_errors: int = 0
    requests_by_status: dict[int, int] = field(default_factory=lambda: defaultdict(int))
    requests_by_endpoint: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    requests_by_method: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    latency_sum_ms: float = 0.0
    latency_count: int = 0
    latency_buckets: dict[float, int] = field(default_factory=lambda: defaultdict(int))
    latency_tracker: LatencyTracker = field(default_factory=LatencyTracker)
    latency_by_endpoint: dict[str, LatencyTracker] = field(default_factory=lambda: defaultdict(LatencyTracker))
    active_requests: int = 0
    errors_by_type: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    @property
    def avg_latency_ms(self) -> float:
        """Average request latency in milliseconds."""
        if self.latency_count == 0:
            return 0.0
        return self.latency_sum_ms / self.latency_count

    @property
    def p50_latency_ms(self) -> float:
        """P50 (median) latency in milliseconds."""
        return self.latency_tracker.p50

    @property
    def p95_latency_ms(self) -> float:
        """P95 latency in milliseconds."""
        return self.latency_tracker.p95

    @property
    def p99_latency_ms(self) -> float:
        """P99 latency in milliseconds."""
        return self.latency_tracker.p99

    @property
    def error_rate(self) -> float:
        """Error rate (5xx responses / total)."""
        if self.total_requests == 0:
            return 0.0
        return self.total_errors / self.total_requests

    def record_request(
        self,
        endpoint: str,
        method: str,
        status_code: int,
        duration_ms: float,
        error_type: str | None = None,
    ) -> None:
        """Record a completed request."""
        self.total_requests += 1
        self.requests_by_status[status_code] += 1
        self.requests_by_endpoint[endpoint] += 1
        self.requests_by_method[method] += 1
        self.latency_sum_ms += duration_ms
        self.latency_count += 1

        # Track percentiles
        self.latency_tracker.add(duration_ms)
        if endpoint not in self.latency_by_endpoint:
            self.latency_by_endpoint[endpoint] = LatencyTracker()
        self.latency_by_endpoint[endpoint].add(duration_ms)

        # Histogram buckets
        for bucket in LATENCY_BUCKETS_MS:
            if duration_ms <= bucket:
                self.latency_buckets[bucket] = self.latency_buckets.get(bucket, 0) + 1

        if status_code >= 500:
            self.total_errors += 1
            if error_type:
                self.errors_by_type[error_type] += 1
        elif status_code >= 400:
            self.errors_by_type[f"http_{status_code}"] = self.errors_by_type.get(f"http_{status_code}", 0) + 1

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "total_requests": self.total_requests,
            "total_errors": self.total_errors,
            "error_rate": round(self.error_rate, 4),
            "avg_latency_ms": round(self.avg_latency_ms, 2),
            "p50_latency_ms": round(self.p50_latency_ms, 2),
            "p95_latency_ms": round(self.p95_latency_ms, 2),
            "p99_latency_ms": round(self.p99_latency_ms, 2),
            "active_requests": self.active_requests,
            "requests_by_status": dict(self.requests_by_status),
            "requests_by_method": dict(self.requests_by_method),
            "requests_by_endpoint": dict(self.requests_by_endpoint),
            "latency_buckets": {str(k): v for k, v in sorted(self.latency_buckets.items())},
            "errors_by_type": dict(self.errors_by_type),
            "endpoints": {
                ep: {
                    "count": self.requests_by_endpoint.get(ep, 0),
                    "p50_ms": round(tracker.p50, 2),
                    "p95_ms": round(tracker.p95, 2),
                    "p99_ms": round(tracker.p99, 2),
                }
                for ep, tracker in self.latency_by_endpoint.items()
            },
        }

    def to_prometheus(self) -> str:
        """Export metrics in Prometheus format."""
        lines = []

        # Total requests counter
        lines.append("# HELP omnipotent_http_requests_total Total HTTP requests")
        lines.append("# TYPE omnipotent_http_requests_total counter")
        for status, count in sorted(self.requests_by_status.items()):
            lines.append(f'omnipotent_http_requests_total{{status="{status}"}} {count}')

        # Requests by method
        lines.append("# HELP omnipotent_http_requests_by_method HTTP requests by method")
        lines.append("# TYPE omnipotent_http_requests_by_method counter")
        for method, count in sorted(self.requests_by_method.items()):
            lines.append(f'omnipotent_http_requests_by_method{{method="{method}"}} {count}')

        # Error counter
        lines.append("# HELP omnipotent_http_errors_total Total HTTP errors")
        lines.append("# TYPE omnipotent_http_errors_total counter")
        lines.append(f"omnipotent_http_errors_total {self.total_errors}")
        for error_type, count in sorted(self.errors_by_type.items()):
            lines.append(f'omnipotent_http_errors_total{{type="{error_type}"}} {count}')

        # Latency percentiles (gauges)
        lines.append("# HELP omnipotent_http_request_duration_ms Request duration percentiles")
        lines.append("# TYPE omnipotent_http_request_duration_ms gauge")
        lines.append(f'omnipotent_http_request_duration_ms{{quantile="0.5"}} {round(self.p50_latency_ms, 2)}')
        lines.append(f'omnipotent_http_request_duration_ms{{quantile="0.95"}} {round(self.p95_latency_ms, 2)}')
        lines.append(f'omnipotent_http_request_duration_ms{{quantile="0.99"}} {round(self.p99_latency_ms, 2)}')

        # Latency histogram
        lines.append("# HELP omnipotent_http_request_duration_seconds_bucket Request duration histogram")
        lines.append("# TYPE omnipotent_http_request_duration_seconds_bucket histogram")
        cumulative = 0
        for bucket in sorted(LATENCY_BUCKETS_MS):
            cumulative += self.latency_buckets.get(bucket, 0)
            bucket_seconds = bucket / 1000.0
            lines.append(f'omnipotent_http_request_duration_seconds_bucket{{le="{bucket_seconds}"}} {cumulative}')
        lines.append(f'omnipotent_http_request_duration_seconds_bucket{{le="+Inf"}} {self.latency_count}')
        lines.append(f"omnipotent_http_request_duration_seconds_sum {round(self.latency_sum_ms / 1000.0, 4)}")
        lines.append(f"omnipotent_http_request_duration_seconds_count {self.latency_count}")

        # Active requests
        lines.append("# HELP omnipotent_http_active_requests Currently active requests")
        lines.append("# TYPE omnipotent_http_active_requests gauge")
        lines.append(f"omnipotent_http_active_requests {self.active_requests}")

        # Error rate (derived metric)
        lines.append("# HELP omnipotent_http_error_rate Current error rate")
        lines.append("# TYPE omnipotent_http_error_rate gauge")
        lines.append(f"omnipotent_http_error_rate {round(self.error_rate, 4)}")

        # Per-endpoint latencies
        lines.append("# HELP omnipotent_http_endpoint_duration_ms Per-endpoint request duration")
        lines.append("# TYPE omnipotent_http_endpoint_duration_ms gauge")
        for endpoint, tracker in self.latency_by_endpoint.items():
            safe_ep = endpoint.replace('"', '\\"')
            lines.append(f'omnipotent_http_endpoint_duration_ms{{endpoint="{safe_ep}",quantile="0.5"}} {round(tracker.p50, 2)}')
            lines.append(f'omnipotent_http_endpoint_duration_ms{{endpoint="{safe_ep}",quantile="0.95"}} {round(tracker.p95, 2)}')
            lines.append(f'omnipotent_http_endpoint_duration_ms{{endpoint="{safe_ep}",quantile="0.99"}} {round(tracker.p99, 2)}')

        return "\n".join(lines)

    def reset(self) -> None:
        """Reset all metrics (for testing)."""
        self.total_requests = 0
        self.total_errors = 0
        self.requests_by_status.clear()
        self.requests_by_endpoint.clear()
        self.requests_by_method.clear()
        self.latency_sum_ms = 0.0
        self.latency_count = 0
        self.latency_buckets.clear()
        self.latency_tracker = LatencyTracker()
        self.latency_by_endpoint.clear()
        self.active_requests = 0
        self.errors_by_type.clear()


_metrics = RequestMetrics()
_lock = threading.Lock()


def get_request_metrics() -> RequestMetrics:
    """Get the global request metrics instance."""
    return _metrics


def reset_request_metrics() -> None:
    """Reset global metrics (for testing)."""
    with _lock:
        _metrics.reset()


class MetricsMiddleware(BaseHTTPMiddleware):
    """Middleware that collects request metrics.

    Features:
    - Request counting by endpoint and status
    - Latency histograms
    - Active request tracking
    - Error classification
    """

    def __init__(
        self,
        app: Any,
        exclude_paths: set[str] | None = None,
    ):
        """Initialize metrics middleware.

        Args:
            app: The ASGI application
            exclude_paths: Paths to exclude from metrics
        """
        super().__init__(app)
        self.exclude_paths = exclude_paths or {"/healthz", "/readyz", "/metrics"}

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        """Process request with metrics collection."""
        if request.url.path in self.exclude_paths:
            return await call_next(request)

        endpoint = self._normalize_endpoint(request.url.path)
        method = request.method
        start_time = time.monotonic()

        # Generate or extract request ID for correlation
        request_id = request.headers.get("X-Request-ID", str(uuid4()))
        request.state.request_id = request_id

        with _lock:
            _metrics.active_requests += 1

        error_type: str | None = None

        try:
            response = await call_next(request)

            duration_ms = (time.monotonic() - start_time) * 1000

            with _lock:
                _metrics.record_request(
                    endpoint=endpoint,
                    method=method,
                    status_code=response.status_code,
                    duration_ms=duration_ms,
                )
                _metrics.active_requests -= 1

            # Add response headers for observability
            response.headers["X-Request-ID"] = request_id
            response.headers["X-Response-Time"] = f"{duration_ms:.2f}ms"

            return response

        except Exception as e:
            duration_ms = (time.monotonic() - start_time) * 1000
            error_type = type(e).__name__

            with _lock:
                _metrics.record_request(
                    endpoint=endpoint,
                    method=method,
                    status_code=500,
                    duration_ms=duration_ms,
                    error_type=error_type,
                )
                _metrics.active_requests -= 1

            raise

    def _normalize_endpoint(self, path: str) -> str:
        """Normalize endpoint path for grouping.

        Removes variable path segments to group similar endpoints.
        """
        parts = path.strip("/").split("/")
        normalized = []

        for part in parts:
            if part.isdigit():
                normalized.append("{id}")
            elif len(part) > 20 and "-" in part:
                normalized.append("{uuid}")
            else:
                normalized.append(part)

        return "/" + "/".join(normalized)
