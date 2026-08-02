"""Scaling metrics for KEDA/HPA custom metrics.

Exposes metrics that drive horizontal pod autoscaling:
- Active investigation count
- LLM request queue depth
- WebSocket connection count
- Memory pressure indicators
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field

log = logging.getLogger("omnipotent.scaling")


@dataclass
class ScalingMetrics:
    """Thread-safe scaling metrics collector."""

    _active_investigations: int = field(default=0, repr=False)
    _active_websockets: int = field(default=0, repr=False)
    _pending_llm_requests: int = field(default=0, repr=False)
    _request_timestamps: deque[float] = field(
        default_factory=lambda: deque(maxlen=1000), repr=False
    )
    _llm_latencies: deque[float] = field(
        default_factory=lambda: deque(maxlen=100), repr=False
    )
    _memory_pressure: float = field(default=0.0, repr=False)
    _circuit_breaker_open: bool = field(default=False, repr=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    async def investigation_started(self) -> None:
        """Record an investigation starting."""
        async with self._lock:
            self._active_investigations += 1

    async def investigation_ended(self) -> None:
        """Record an investigation ending."""
        async with self._lock:
            self._active_investigations = max(0, self._active_investigations - 1)

    async def websocket_connected(self) -> None:
        """Record a WebSocket connection."""
        async with self._lock:
            self._active_websockets += 1

    async def websocket_disconnected(self) -> None:
        """Record a WebSocket disconnection."""
        async with self._lock:
            self._active_websockets = max(0, self._active_websockets - 1)

    async def llm_request_queued(self) -> None:
        """Record an LLM request being queued."""
        async with self._lock:
            self._pending_llm_requests += 1

    async def llm_request_completed(self, latency_ms: float) -> None:
        """Record an LLM request completion."""
        async with self._lock:
            self._pending_llm_requests = max(0, self._pending_llm_requests - 1)
            self._llm_latencies.append(latency_ms)

    async def record_request(self) -> None:
        """Record an HTTP request."""
        async with self._lock:
            self._request_timestamps.append(time.time())

    async def set_memory_pressure(self, pressure: float) -> None:
        """Set current memory pressure (0.0-1.0)."""
        async with self._lock:
            self._memory_pressure = max(0.0, min(1.0, pressure))

    async def set_circuit_breaker_open(self, is_open: bool) -> None:
        """Set circuit breaker state."""
        async with self._lock:
            self._circuit_breaker_open = is_open

    async def get_metrics(self) -> dict:
        """Get all scaling metrics for Prometheus exposition."""
        async with self._lock:
            now = time.time()
            recent_requests = sum(1 for ts in self._request_timestamps if now - ts < 60)
            rps = recent_requests / 60.0

            latencies = sorted(self._llm_latencies) if self._llm_latencies else [0]
            p95_idx = int(len(latencies) * 0.95)
            llm_p95 = latencies[min(p95_idx, len(latencies) - 1)]

            return {
                "active_investigations": self._active_investigations,
                "active_websockets": self._active_websockets,
                "pending_llm_requests": self._pending_llm_requests,
                "requests_per_second": round(rps, 2),
                "llm_latency_p95_ms": round(llm_p95, 2),
                "memory_pressure": round(self._memory_pressure, 3),
                "circuit_breaker_open": 1 if self._circuit_breaker_open else 0,
                "scaling_pressure": self._calculate_pressure(),
            }

    def _calculate_pressure(self) -> int:
        """Calculate composite scaling pressure (0-100).

        Used for predictive scaling decisions.
        """
        pressure = 0
        pressure += min(40, self._active_investigations * 2)
        pressure += min(30, self._pending_llm_requests)
        pressure += int(self._memory_pressure * 20)
        if self._circuit_breaker_open:
            pressure += 10
        return min(100, pressure)


_metrics: ScalingMetrics | None = None


def get_scaling_metrics() -> ScalingMetrics:
    """Get the singleton scaling metrics instance."""
    global _metrics
    if _metrics is None:
        _metrics = ScalingMetrics()
    return _metrics


def reset_scaling_metrics() -> None:
    """Reset scaling metrics (for testing)."""
    global _metrics
    _metrics = None


async def expose_scaling_metrics() -> str:
    """Prometheus-format metrics for KEDA scraping."""
    m = await get_scaling_metrics().get_metrics()

    lines = [
        "# HELP copilot_active_investigations Current active investigation sessions",
        "# TYPE copilot_active_investigations gauge",
        f"copilot_active_investigations {m['active_investigations']}",
        "",
        "# HELP copilot_active_websockets Current WebSocket connections",
        "# TYPE copilot_active_websockets gauge",
        f"copilot_active_websockets {m['active_websockets']}",
        "",
        "# HELP copilot_llm_pending_requests Pending LLM API requests",
        "# TYPE copilot_llm_pending_requests gauge",
        f"copilot_llm_pending_requests {m['pending_llm_requests']}",
        "",
        "# HELP copilot_requests_per_second Request rate (60s window)",
        "# TYPE copilot_requests_per_second gauge",
        f"copilot_requests_per_second {m['requests_per_second']}",
        "",
        "# HELP copilot_llm_latency_p95_ms LLM P95 latency in milliseconds",
        "# TYPE copilot_llm_latency_p95_ms gauge",
        f"copilot_llm_latency_p95_ms {m['llm_latency_p95_ms']}",
        "",
        "# HELP copilot_scaling_pressure Composite scaling pressure (0-100)",
        "# TYPE copilot_scaling_pressure gauge",
        f"copilot_scaling_pressure {m['scaling_pressure']}",
        "",
        "# HELP copilot_memory_pressure Memory pressure (0-1)",
        "# TYPE copilot_memory_pressure gauge",
        f"copilot_memory_pressure {m['memory_pressure']}",
        "",
        "# HELP copilot_circuit_breaker_open Circuit breaker state",
        "# TYPE copilot_circuit_breaker_open gauge",
        f"copilot_circuit_breaker_open {m['circuit_breaker_open']}",
    ]

    return "\n".join(lines)
