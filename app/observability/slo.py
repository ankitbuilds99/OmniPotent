"""SLO (Service Level Objective) tracking and burn rate calculation.

Tracks:
- Investigation success rate SLO (e.g., 95% target)
- Latency SLO (e.g., p99 < 60s)
- Token budget SLO
- Error budget consumption
- Multi-window burn rates for alerting
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

log = logging.getLogger("omnipotent.observability.slo")


@dataclass
class SLODefinition:
    """Definition of a Service Level Objective."""

    name: str
    target: float
    window_seconds: int
    metric_type: str
    description: str = ""


@dataclass
class SLOStatus:
    """Current status of an SLO."""

    name: str
    target: float
    current: float
    budget_remaining: float
    burn_rate_1h: float
    burn_rate_6h: float
    burn_rate_24h: float
    is_breaching: bool
    window_seconds: int
    last_updated: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class SLOTracker:
    """Tracks SLO compliance and error budget burn rates."""

    def __init__(self, window_seconds: int = 86400) -> None:
        self._lock = threading.Lock()
        self._window = window_seconds
        self._observations: deque[tuple[float, int, int]] = deque()
        self._slos: dict[str, SLODefinition] = {}
        self._slo_data: dict[str, deque[tuple[float, float]]] = {}

    def define_slo(
        self,
        name: str,
        target: float,
        window_seconds: int = 86400,
        metric_type: str = "ratio",
        description: str = "",
    ) -> None:
        """Define a new SLO."""
        with self._lock:
            self._slos[name] = SLODefinition(
                name=name,
                target=target,
                window_seconds=window_seconds,
                metric_type=metric_type,
                description=description,
            )
            self._slo_data[name] = deque()

    def record(
        self,
        slo_name: str,
        value: float,
        timestamp: float | None = None,
    ) -> None:
        """Record an observation for an SLO.

        For ratio SLOs: 1.0 = good, 0.0 = bad
        For latency SLOs: the actual latency value
        For count SLOs: the count value
        """
        ts = timestamp or time.time()

        with self._lock:
            if slo_name not in self._slo_data:
                self._slo_data[slo_name] = deque()

            self._slo_data[slo_name].append((ts, value))

            slo = self._slos.get(slo_name)
            if slo:
                cutoff = ts - slo.window_seconds
                while (
                    self._slo_data[slo_name]
                    and self._slo_data[slo_name][0][0] < cutoff
                ):
                    self._slo_data[slo_name].popleft()

    def get_status(self, slo_name: str) -> SLOStatus | None:
        """Get current status of an SLO."""
        with self._lock:
            slo = self._slos.get(slo_name)
            if not slo:
                return None

            data = list(self._slo_data.get(slo_name, []))

        if not data:
            return SLOStatus(
                name=slo_name,
                target=slo.target,
                current=1.0,
                budget_remaining=1.0,
                burn_rate_1h=0.0,
                burn_rate_6h=0.0,
                burn_rate_24h=0.0,
                is_breaching=False,
                window_seconds=slo.window_seconds,
            )

        now = time.time()

        if slo.metric_type == "ratio":
            current = self._calc_ratio(data)
        elif slo.metric_type == "latency_p99":
            current = self._calc_percentile(data, 0.99)
        else:
            current = sum(v for _, v in data)

        if slo.metric_type == "ratio":
            error_budget = 1.0 - slo.target
            actual_error_rate = 1.0 - current
            if error_budget > 0:
                budget_remaining = max(0, 1.0 - (actual_error_rate / error_budget))
            else:
                budget_remaining = 1.0
        else:
            budget_remaining = 1.0 if current <= slo.target else 0.0

        burn_rate_1h = self._calc_burn_rate(
            data, now, 3600, slo.target, slo.metric_type
        )
        burn_rate_6h = self._calc_burn_rate(
            data, now, 21600, slo.target, slo.metric_type
        )
        burn_rate_24h = self._calc_burn_rate(
            data, now, 86400, slo.target, slo.metric_type
        )

        is_breaching = budget_remaining <= 0 or burn_rate_1h > 14.4

        return SLOStatus(
            name=slo_name,
            target=slo.target,
            current=current,
            budget_remaining=budget_remaining,
            burn_rate_1h=burn_rate_1h,
            burn_rate_6h=burn_rate_6h,
            burn_rate_24h=burn_rate_24h,
            is_breaching=is_breaching,
            window_seconds=slo.window_seconds,
        )

    def _calc_ratio(self, data: list[tuple[float, float]]) -> float:
        """Calculate success ratio from observations."""
        if not data:
            return 1.0
        good = sum(1 for _, v in data if v >= 1.0)
        return good / len(data)

    def _calc_percentile(self, data: list[tuple[float, float]], p: float) -> float:
        """Calculate percentile from observations."""
        if not data:
            return 0.0
        values = sorted(v for _, v in data)
        idx = int(len(values) * p)
        return values[min(idx, len(values) - 1)]

    def _calc_burn_rate(
        self,
        data: list[tuple[float, float]],
        now: float,
        window: int,
        target: float,
        metric_type: str,
    ) -> float:
        """Calculate burn rate for a time window.

        Burn rate = actual error rate / allowed error rate
        A burn rate of 1.0 means consuming budget at exactly the expected rate.
        A burn rate of 2.0 means consuming budget 2x faster than allowed.
        """
        cutoff = now - window
        window_data = [(t, v) for t, v in data if t >= cutoff]

        if not window_data:
            return 0.0

        if metric_type == "ratio":
            actual_ratio = self._calc_ratio(window_data)
            actual_error_rate = 1.0 - actual_ratio
            allowed_error_rate = 1.0 - target

            if allowed_error_rate <= 0:
                return float("inf") if actual_error_rate > 0 else 0.0

            return actual_error_rate / allowed_error_rate

        actual = self._calc_percentile(window_data, 0.99)
        if target <= 0:
            return 0.0
        return actual / target

    def get_all_statuses(self) -> list[SLOStatus]:
        """Get status of all defined SLOs."""
        statuses = []
        with self._lock:
            slo_names = list(self._slos.keys())

        for name in slo_names:
            status = self.get_status(name)
            if status:
                statuses.append(status)

        return statuses

    def render_prometheus(self) -> str:
        """Render SLO metrics in Prometheus format."""
        lines = []

        lines.append("# HELP slo_target SLO target value")
        lines.append("# TYPE slo_target gauge")

        lines.append("# HELP slo_current Current SLO value")
        lines.append("# TYPE slo_current gauge")

        lines.append("# HELP slo_budget_remaining Remaining error budget (0-1)")
        lines.append("# TYPE slo_budget_remaining gauge")

        lines.append("# HELP slo_burn_rate_1h 1-hour burn rate")
        lines.append("# TYPE slo_burn_rate_1h gauge")

        lines.append("# HELP slo_burn_rate_6h 6-hour burn rate")
        lines.append("# TYPE slo_burn_rate_6h gauge")

        lines.append("# HELP slo_burn_rate_24h 24-hour burn rate")
        lines.append("# TYPE slo_burn_rate_24h gauge")

        lines.append("# HELP slo_is_breaching Whether SLO is currently breaching")
        lines.append("# TYPE slo_is_breaching gauge")

        for status in self.get_all_statuses():
            name = status.name.replace("-", "_")
            lines.append(f'slo_target{{slo="{name}"}} {status.target}')
            lines.append(f'slo_current{{slo="{name}"}} {status.current}')
            lines.append(f'slo_budget_remaining{{slo="{name}"}} {status.budget_remaining}')
            lines.append(f'slo_burn_rate_1h{{slo="{name}"}} {status.burn_rate_1h}')
            lines.append(f'slo_burn_rate_6h{{slo="{name}"}} {status.burn_rate_6h}')
            lines.append(f'slo_burn_rate_24h{{slo="{name}"}} {status.burn_rate_24h}')
            lines.append(
                f'slo_is_breaching{{slo="{name}"}} {1 if status.is_breaching else 0}'
            )

        return "\n".join(lines) + "\n"

    def reset(self) -> None:
        """Reset all SLO data (for testing)."""
        with self._lock:
            self._slo_data.clear()
            for name in self._slos:
                self._slo_data[name] = deque()


_tracker = SLOTracker()


def _init_default_slos() -> None:
    """Define default SLOs for the copilot."""
    _tracker.define_slo(
        "investigation_success_rate",
        target=0.95,
        window_seconds=86400,
        metric_type="ratio",
        description="95% of investigations should succeed",
    )

    _tracker.define_slo(
        "investigation_latency_p99",
        target=60.0,
        window_seconds=86400,
        metric_type="latency_p99",
        description="p99 investigation latency should be under 60s",
    )

    _tracker.define_slo(
        "llm_success_rate",
        target=0.99,
        window_seconds=3600,
        metric_type="ratio",
        description="99% of LLM calls should succeed",
    )

    _tracker.define_slo(
        "mcp_tool_success_rate",
        target=0.95,
        window_seconds=3600,
        metric_type="ratio",
        description="95% of MCP tool calls should succeed",
    )


_init_default_slos()


def get_slo_tracker() -> SLOTracker:
    """Get the global SLO tracker."""
    return _tracker


def record_investigation_slo(success: bool, latency_seconds: float) -> None:
    """Record an investigation for SLO tracking."""
    _tracker.record("investigation_success_rate", 1.0 if success else 0.0)
    _tracker.record("investigation_latency_p99", latency_seconds)


def record_llm_slo(success: bool) -> None:
    """Record an LLM call for SLO tracking."""
    _tracker.record("llm_success_rate", 1.0 if success else 0.0)


def record_mcp_tool_slo(success: bool) -> None:
    """Record an MCP tool call for SLO tracking."""
    _tracker.record("mcp_tool_success_rate", 1.0 if success else 0.0)
