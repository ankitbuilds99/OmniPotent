"""Hedged requests for tail latency reduction.

Launches backup requests after a delay to reduce p99 latency.
The first successful response wins; extras are cancelled.
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any, Callable, TypeVar

log = logging.getLogger("omnipotent.resilience.hedging")

T = TypeVar("T")


@dataclass
class HedgingConfig:
    """Configuration for hedged requests.

    Attributes:
        hedge_delay_ms: Delay before launching hedge request
        max_hedges: Maximum number of hedge requests (total calls = 1 + max_hedges)
        percentile_based: If True, hedge_delay_ms is based on latency percentile
        percentile: Latency percentile for dynamic hedge delay
        min_delay_ms: Minimum hedge delay
        max_delay_ms: Maximum hedge delay
        name: Name for logging/identification
    """

    hedge_delay_ms: float = 100.0
    max_hedges: int = 1
    percentile_based: bool = False
    percentile: float = 0.50
    min_delay_ms: float = 10.0
    max_delay_ms: float = 1000.0
    name: str = "default"


@dataclass
class HedgingStats:
    """Statistics for hedged requests."""

    total_calls: int = 0
    primary_wins: int = 0
    hedge_wins: int = 0
    total_hedges_launched: int = 0
    total_hedges_cancelled: int = 0
    total_failures: int = 0
    total_latency_ms: float = 0.0
    latencies: list[float] = field(default_factory=list)

    @property
    def hedge_win_rate(self) -> float:
        """Calculate rate of hedge wins (0.0 to 1.0)."""
        successful = self.primary_wins + self.hedge_wins
        if successful == 0:
            return 0.0
        return self.hedge_wins / successful

    @property
    def avg_latency_ms(self) -> float:
        """Calculate average latency in milliseconds."""
        successful = self.primary_wins + self.hedge_wins
        if successful == 0:
            return 0.0
        return self.total_latency_ms / successful

    @property
    def p50_latency_ms(self) -> float:
        """Get p50 latency."""
        if not self.latencies:
            return 0.0
        sorted_latencies = sorted(self.latencies)
        idx = len(sorted_latencies) // 2
        return sorted_latencies[idx]

    @property
    def p99_latency_ms(self) -> float:
        """Get p99 latency."""
        if not self.latencies:
            return 0.0
        sorted_latencies = sorted(self.latencies)
        idx = int(len(sorted_latencies) * 0.99)
        idx = min(idx, len(sorted_latencies) - 1)
        return sorted_latencies[idx]


class HedgedRequests:
    """Hedged request executor for tail latency reduction.

    Launches a primary request and, after a delay, launches hedge requests.
    The first successful response wins; all others are cancelled.

    Usage:
        hedger = HedgedRequests(HedgingConfig(hedge_delay_ms=50))

        result = await hedger.execute(
            lambda: call_service(request),
            lambda: call_service(request),  # hedge
        )
    """

    def __init__(
        self,
        config: HedgingConfig,
        clock_fn: Callable[[], float] = time.monotonic,
    ):
        self.config = config
        self._clock = clock_fn
        self._stats = HedgingStats()
        self._latency_samples: list[float] = []
        self._max_samples = 1000

    @property
    def stats(self) -> HedgingStats:
        """Get current statistics."""
        return self._stats

    def _get_hedge_delay_ms(self) -> float:
        """Get current hedge delay in milliseconds."""
        if not self.config.percentile_based or not self._latency_samples:
            return self.config.hedge_delay_ms

        sorted_samples = sorted(self._latency_samples)
        idx = int(len(sorted_samples) * self.config.percentile)
        idx = min(idx, len(sorted_samples) - 1)
        percentile_latency = sorted_samples[idx]

        return max(
            self.config.min_delay_ms,
            min(percentile_latency, self.config.max_delay_ms),
        )

    def _record_latency(self, latency_ms: float) -> None:
        """Record a latency sample."""
        if len(self._latency_samples) >= self._max_samples:
            self._latency_samples.pop(0)
        self._latency_samples.append(latency_ms)

        if len(self._stats.latencies) >= self._max_samples:
            self._stats.latencies.pop(0)
        self._stats.latencies.append(latency_ms)
        self._stats.total_latency_ms += latency_ms

    async def execute(
        self,
        primary_fn: Callable[[], Any],
        *hedge_fns: Callable[[], Any],
    ) -> Any:
        """Execute with hedged requests.

        Args:
            primary_fn: Primary request function
            *hedge_fns: Hedge request functions (max: config.max_hedges)

        Returns:
            Result from the first successful request

        Raises:
            Exception: If all requests fail (raises the primary exception)
        """
        self._stats.total_calls += 1
        start = self._clock()

        hedge_fns_list = list(hedge_fns)[: self.config.max_hedges]
        hedge_delay_ms = self._get_hedge_delay_ms()
        hedge_delay_seconds = hedge_delay_ms / 1000.0

        tasks: list[asyncio.Task[Any]] = []
        result_holder: dict[str, Any] = {}
        primary_exception: BaseException | None = None
        pending_count = 1 + len(hedge_fns_list)
        done_event = asyncio.Event()

        async def run_with_index(
            fn: Callable[[], Any],
            index: int,
            delay: float = 0,
        ) -> None:
            nonlocal primary_exception, pending_count

            if delay > 0:
                await asyncio.sleep(delay)

            if "value" in result_holder:
                pending_count -= 1
                if pending_count == 0:
                    done_event.set()
                return

            try:
                result = fn()
                if asyncio.iscoroutine(result):
                    result = await result

                if "value" not in result_holder:
                    result_holder["value"] = result
                    result_holder["index"] = index
                    done_event.set()

            except Exception as exc:
                if index == 0:
                    primary_exception = exc
                log.debug("Request %d failed: %s", index, exc)
            finally:
                pending_count -= 1
                if pending_count == 0:
                    done_event.set()

        primary_task = asyncio.create_task(run_with_index(primary_fn, 0))
        tasks.append(primary_task)

        for i, hedge_fn in enumerate(hedge_fns_list):
            delay = hedge_delay_seconds * (i + 1)
            hedge_task = asyncio.create_task(run_with_index(hedge_fn, i + 1, delay))
            tasks.append(hedge_task)
            self._stats.total_hedges_launched += 1

        await done_event.wait()

        for task in tasks:
            if not task.done():
                task.cancel()
                self._stats.total_hedges_cancelled += 1
                with suppress(asyncio.CancelledError):
                    await task

        latency_ms = (self._clock() - start) * 1000

        if "value" not in result_holder:
            self._stats.total_failures += 1
            if primary_exception:
                raise primary_exception
            raise RuntimeError("All hedged requests failed")

        winner_index = result_holder["index"]
        if winner_index == 0:
            self._stats.primary_wins += 1
            log.debug(
                "Primary won (latency: %.2fms, hedge_delay: %.2fms)",
                latency_ms,
                hedge_delay_ms,
            )
        else:
            self._stats.hedge_wins += 1
            log.debug(
                "Hedge %d won (latency: %.2fms, hedge_delay: %.2fms)",
                winner_index,
                latency_ms,
                hedge_delay_ms,
            )

        self._record_latency(latency_ms)

        return result_holder["value"]

    async def execute_same(
        self,
        fn: Callable[[], Any],
        hedge_count: int | None = None,
    ) -> Any:
        """Execute with the same function for primary and hedges.

        Args:
            fn: Function to execute (used for primary and all hedges)
            hedge_count: Number of hedge requests (defaults to config.max_hedges)

        Returns:
            Result from the first successful request
        """
        count = hedge_count if hedge_count is not None else self.config.max_hedges
        hedge_fns = [fn] * count
        return await self.execute(fn, *hedge_fns)

    def reset(self) -> None:
        """Reset statistics."""
        self._stats = HedgingStats()
        self._latency_samples.clear()
        log.debug("Reset hedging '%s'", self.config.name)


_hedgers: dict[str, HedgedRequests] = {}


def get_hedger(name: str) -> HedgedRequests | None:
    """Get a hedger by name."""
    return _hedgers.get(name)


def create_hedger(
    name: str,
    hedge_delay_ms: float = 100.0,
    max_hedges: int = 1,
    percentile_based: bool = False,
) -> HedgedRequests:
    """Create or get a hedger by name."""
    if name not in _hedgers:
        config = HedgingConfig(
            name=name,
            hedge_delay_ms=hedge_delay_ms,
            max_hedges=max_hedges,
            percentile_based=percentile_based,
        )
        _hedgers[name] = HedgedRequests(config)
    return _hedgers[name]


def reset_all_hedgers() -> None:
    """Reset all hedgers (for testing)."""
    for hedger in _hedgers.values():
        hedger.reset()
    _hedgers.clear()
