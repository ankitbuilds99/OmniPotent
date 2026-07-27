"""Request metrics collection middleware.

Provides:
- Request count by endpoint and status
- Latency histograms
- Error rate tracking
- Active request gauge
- Integration with Prometheus
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

log = logging.getLogger("devcopilot.middleware.metrics")


@dataclass
class RequestMetrics:
    """Collected request metrics."""

    total_requests: int = 0
    total_errors: int = 0
    requests_by_status: dict[int, int] = field(default_factory=lambda: defaultdict(int))
    requests_by_endpoint: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    latency_sum_ms: float = 0.0
    latency_count: int = 0
    latency_buckets: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    active_requests: int = 0
    errors_by_type: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    @property
    def avg_latency_ms(self) -> float:
        """Average request latency in milliseconds."""
        if self.latency_count == 0:
            return 0.0
        return self.latency_sum_ms / self.latency_count

    @property
    def error_rate(self) -> float:
        """Error rate (5xx responses / total)."""
        if self.total_requests == 0:
            return 0.0
        return self.total_errors / self.total_requests

    def record_request(
        self,
        endpoint: str,
        status_code: int,
        duration_ms: float,
        error_type: str | None = None,
    ) -> None:
        """Record a completed request."""
        self.total_requests += 1
        self.requests_by_status[status_code] += 1
        self.requests_by_endpoint[endpoint] += 1
        self.latency_sum_ms += duration_ms
        self.latency_count += 1

        bucket = self._get_latency_bucket(duration_ms)
        self.latency_buckets[bucket] += 1

        if status_code >= 500:
            self.total_errors += 1
            if error_type:
                self.errors_by_type[error_type] += 1

    def _get_latency_bucket(self, duration_ms: float) -> str:
        """Get histogram bucket for latency."""
        if duration_ms < 50:
            return "0-50ms"
        elif duration_ms < 100:
            return "50-100ms"
        elif duration_ms < 250:
            return "100-250ms"
        elif duration_ms < 500:
            return "250-500ms"
        elif duration_ms < 1000:
            return "500-1000ms"
        elif duration_ms < 2500:
            return "1-2.5s"
        elif duration_ms < 5000:
            return "2.5-5s"
        else:
            return "5s+"

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "total_requests": self.total_requests,
            "total_errors": self.total_errors,
            "error_rate": round(self.error_rate, 4),
            "avg_latency_ms": round(self.avg_latency_ms, 2),
            "active_requests": self.active_requests,
            "requests_by_status": dict(self.requests_by_status),
            "requests_by_endpoint": dict(self.requests_by_endpoint),
            "latency_buckets": dict(self.latency_buckets),
            "errors_by_type": dict(self.errors_by_type),
        }

    def to_prometheus(self) -> str:
        """Export metrics in Prometheus format."""
        lines = []

        lines.append("# HELP http_requests_total Total HTTP requests")
        lines.append("# TYPE http_requests_total counter")
        lines.append(f"http_requests_total {self.total_requests}")

        lines.append("# HELP http_errors_total Total HTTP 5xx errors")
        lines.append("# TYPE http_errors_total counter")
        lines.append(f"http_errors_total {self.total_errors}")

        lines.append("# HELP http_request_duration_ms_avg Average request duration")
        lines.append("# TYPE http_request_duration_ms_avg gauge")
        lines.append(f"http_request_duration_ms_avg {round(self.avg_latency_ms, 2)}")

        lines.append("# HELP http_active_requests Current active requests")
        lines.append("# TYPE http_active_requests gauge")
        lines.append(f"http_active_requests {self.active_requests}")

        lines.append("# HELP http_requests_by_status Requests by HTTP status code")
        lines.append("# TYPE http_requests_by_status counter")
        for status, count in sorted(self.requests_by_status.items()):
            lines.append(f'http_requests_by_status{{status="{status}"}} {count}')

        lines.append("# HELP http_request_duration_bucket Request duration histogram")
        lines.append("# TYPE http_request_duration_bucket counter")
        for bucket, count in sorted(self.latency_buckets.items()):
            lines.append(f'http_request_duration_bucket{{le="{bucket}"}} {count}')

        return "\n".join(lines)

    def reset(self) -> None:
        """Reset all metrics (for testing)."""
        self.total_requests = 0
        self.total_errors = 0
        self.requests_by_status.clear()
        self.requests_by_endpoint.clear()
        self.latency_sum_ms = 0.0
        self.latency_count = 0
        self.latency_buckets.clear()
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
        start_time = time.monotonic()

        with _lock:
            _metrics.active_requests += 1

        error_type: str | None = None

        try:
            response = await call_next(request)

            duration_ms = (time.monotonic() - start_time) * 1000

            with _lock:
                _metrics.record_request(
                    endpoint=endpoint,
                    status_code=response.status_code,
                    duration_ms=duration_ms,
                )
                _metrics.active_requests -= 1

            return response

        except Exception as e:
            duration_ms = (time.monotonic() - start_time) * 1000
            error_type = type(e).__name__

            with _lock:
                _metrics.record_request(
                    endpoint=endpoint,
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
