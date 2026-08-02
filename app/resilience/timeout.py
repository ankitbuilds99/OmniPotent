"""Adaptive timeout management based on latency percentiles.

Provides dynamic timeout adjustment based on observed latency distributions,
helping balance responsiveness with reliability.
"""

from __future__ import annotations

import asyncio
import bisect
import logging
import time
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from typing import AsyncIterator, Callable, Iterator, TypeVar

log = logging.getLogger("omnipotent.resilience.timeout")

T = TypeVar("T")


class TimeoutError(Exception):
    """Raised when an operation times out."""

    def __init__(
        self,
        message: str,
        timeout_seconds: float,
        operation: str | None = None,
    ):
        super().__init__(message)
        self.timeout_seconds = timeout_seconds
        self.operation = operation


@dataclass
class LatencyBucket:
    """A bucket for tracking latency samples."""

    samples: list[float] = field(default_factory=list)
    max_samples: int = 1000

    def add(self, latency_ms: float) -> None:
        """Add a latency sample."""
        if len(self.samples) >= self.max_samples:
            self.samples.pop(0)
        bisect.insort(self.samples, latency_ms)

    def percentile(self, p: float) -> float:
        """Get the p-th percentile (0.0 to 1.0)."""
        if not self.samples:
            return 0.0
        idx = int(len(self.samples) * p)
        idx = min(idx, len(self.samples) - 1)
        return self.samples[idx]

    def clear(self) -> None:
        """Clear all samples."""
        self.samples.clear()


@dataclass
class AdaptiveTimeoutConfig:
    """Configuration for adaptive timeout.

    Attributes:
        initial_timeout_ms: Starting timeout before enough data collected
        min_timeout_ms: Minimum timeout to use
        max_timeout_ms: Maximum timeout to use
        percentile: Latency percentile to base timeout on (e.g., 0.99 for p99)
        multiplier: Multiply percentile by this for headroom
        min_samples: Minimum samples before adapting
        name: Name for logging/identification
    """

    initial_timeout_ms: float = 5000.0
    min_timeout_ms: float = 100.0
    max_timeout_ms: float = 30000.0
    percentile: float = 0.99
    multiplier: float = 1.5
    min_samples: int = 10
    name: str = "default"


@dataclass
class TimeoutStats:
    """Statistics for timeout tracking."""

    total_calls: int = 0
    timeouts: int = 0
    successes: int = 0
    current_timeout_ms: float = 0.0
    min_latency_ms: float = float("inf")
    max_latency_ms: float = 0.0
    total_latency_ms: float = 0.0

    @property
    def timeout_rate(self) -> float:
        """Calculate timeout rate (0.0 to 1.0)."""
        if self.total_calls == 0:
            return 0.0
        return self.timeouts / self.total_calls

    @property
    def avg_latency_ms(self) -> float:
        """Calculate average latency in milliseconds."""
        if self.successes == 0:
            return 0.0
        return self.total_latency_ms / self.successes


class AdaptiveTimeout:
    """Adaptive timeout that adjusts based on observed latency.

    Tracks latency samples and computes timeout based on a configurable
    percentile (e.g., p99) with a multiplier for headroom.

    Usage:
        timeout = AdaptiveTimeout(AdaptiveTimeoutConfig(percentile=0.99))

        async with timeout.execute("fetch_data"):
            await fetch_data()

        # Or get current timeout for external use
        current_timeout = timeout.get_timeout_seconds()
    """

    def __init__(
        self,
        config: AdaptiveTimeoutConfig,
        clock_fn: Callable[[], float] = time.monotonic,
    ):
        self.config = config
        self._clock = clock_fn
        self._latency_bucket = LatencyBucket()
        self._stats = TimeoutStats(current_timeout_ms=config.initial_timeout_ms)
        self._current_timeout_ms = config.initial_timeout_ms

    @property
    def stats(self) -> TimeoutStats:
        """Get current statistics."""
        return self._stats

    def get_timeout_ms(self) -> float:
        """Get current timeout in milliseconds."""
        return self._current_timeout_ms

    def get_timeout_seconds(self) -> float:
        """Get current timeout in seconds."""
        return self._current_timeout_ms / 1000.0

    def _compute_timeout(self) -> float:
        """Compute timeout based on latency percentile."""
        if len(self._latency_bucket.samples) < self.config.min_samples:
            return self.config.initial_timeout_ms

        percentile_latency = self._latency_bucket.percentile(self.config.percentile)
        computed = percentile_latency * self.config.multiplier

        return max(
            self.config.min_timeout_ms,
            min(computed, self.config.max_timeout_ms),
        )

    def record_latency(self, latency_ms: float) -> None:
        """Record a latency sample and update timeout.

        Args:
            latency_ms: Latency in milliseconds
        """
        self._latency_bucket.add(latency_ms)
        self._stats.total_calls += 1
        self._stats.successes += 1
        self._stats.total_latency_ms += latency_ms
        self._stats.min_latency_ms = min(self._stats.min_latency_ms, latency_ms)
        self._stats.max_latency_ms = max(self._stats.max_latency_ms, latency_ms)

        old_timeout = self._current_timeout_ms
        self._current_timeout_ms = self._compute_timeout()
        self._stats.current_timeout_ms = self._current_timeout_ms

        if abs(old_timeout - self._current_timeout_ms) > 100:
            log.debug(
                "Timeout '%s' adjusted: %.0fms -> %.0fms (p%.0f: %.0fms)",
                self.config.name,
                old_timeout,
                self._current_timeout_ms,
                self.config.percentile * 100,
                self._latency_bucket.percentile(self.config.percentile),
            )

    def record_timeout(self) -> None:
        """Record a timeout event."""
        self._stats.total_calls += 1
        self._stats.timeouts += 1

    @asynccontextmanager
    async def execute(self, operation: str | None = None) -> AsyncIterator[None]:
        """Execute an operation with adaptive timeout.

        Yields:
            None when timeout is not exceeded

        Raises:
            TimeoutError: If the operation exceeds the timeout
        """
        timeout_seconds = self.get_timeout_seconds()
        start = self._clock()

        try:
            async with asyncio.timeout(timeout_seconds):
                yield
        except asyncio.TimeoutError:
            self.record_timeout()
            raise TimeoutError(
                f"Operation timed out after {timeout_seconds:.2f}s",
                timeout_seconds=timeout_seconds,
                operation=operation,
            )

        latency_ms = (self._clock() - start) * 1000
        self.record_latency(latency_ms)

    @contextmanager
    def execute_sync(self, operation: str | None = None) -> Iterator[None]:
        """Synchronous execution context that records latency.

        Note: This does not enforce timeout (Python sync code can't be
        interrupted). Use async version for actual timeout enforcement.

        Yields:
            None (latency is recorded on exit)
        """
        start = self._clock()

        try:
            yield
        finally:
            latency_ms = (self._clock() - start) * 1000
            self.record_latency(latency_ms)

    def reset(self) -> None:
        """Reset statistics and latency samples."""
        self._latency_bucket.clear()
        self._stats = TimeoutStats(current_timeout_ms=self.config.initial_timeout_ms)
        self._current_timeout_ms = self.config.initial_timeout_ms
        log.debug("Reset timeout '%s'", self.config.name)


@dataclass
class FixedTimeoutConfig:
    """Configuration for fixed timeout."""

    timeout_ms: float = 5000.0
    name: str = "default"


class FixedTimeout:
    """Fixed timeout wrapper for consistent interface.

    Usage:
        timeout = FixedTimeout(FixedTimeoutConfig(timeout_ms=5000))

        async with timeout.execute("fetch_data"):
            await fetch_data()
    """

    def __init__(
        self,
        config: FixedTimeoutConfig,
        clock_fn: Callable[[], float] = time.monotonic,
    ):
        self.config = config
        self._clock = clock_fn
        self._stats = TimeoutStats(current_timeout_ms=config.timeout_ms)

    @property
    def stats(self) -> TimeoutStats:
        """Get current statistics."""
        return self._stats

    def get_timeout_ms(self) -> float:
        """Get timeout in milliseconds."""
        return self.config.timeout_ms

    def get_timeout_seconds(self) -> float:
        """Get timeout in seconds."""
        return self.config.timeout_ms / 1000.0

    @asynccontextmanager
    async def execute(self, operation: str | None = None) -> AsyncIterator[None]:
        """Execute an operation with fixed timeout."""
        timeout_seconds = self.get_timeout_seconds()
        start = self._clock()

        try:
            async with asyncio.timeout(timeout_seconds):
                yield
        except asyncio.TimeoutError:
            self._stats.total_calls += 1
            self._stats.timeouts += 1
            raise TimeoutError(
                f"Operation timed out after {timeout_seconds:.2f}s",
                timeout_seconds=timeout_seconds,
                operation=operation,
            )

        latency_ms = (self._clock() - start) * 1000
        self._stats.total_calls += 1
        self._stats.successes += 1
        self._stats.total_latency_ms += latency_ms
        self._stats.min_latency_ms = min(self._stats.min_latency_ms, latency_ms)
        self._stats.max_latency_ms = max(self._stats.max_latency_ms, latency_ms)

    def reset(self) -> None:
        """Reset statistics."""
        self._stats = TimeoutStats(current_timeout_ms=self.config.timeout_ms)


_adaptive_timeouts: dict[str, AdaptiveTimeout] = {}
_fixed_timeouts: dict[str, FixedTimeout] = {}


def get_adaptive_timeout(name: str) -> AdaptiveTimeout | None:
    """Get an adaptive timeout by name."""
    return _adaptive_timeouts.get(name)


def create_adaptive_timeout(
    name: str,
    initial_timeout_ms: float = 5000.0,
    percentile: float = 0.99,
    multiplier: float = 1.5,
    min_timeout_ms: float = 100.0,
    max_timeout_ms: float = 30000.0,
) -> AdaptiveTimeout:
    """Create or get an adaptive timeout by name."""
    if name not in _adaptive_timeouts:
        config = AdaptiveTimeoutConfig(
            name=name,
            initial_timeout_ms=initial_timeout_ms,
            percentile=percentile,
            multiplier=multiplier,
            min_timeout_ms=min_timeout_ms,
            max_timeout_ms=max_timeout_ms,
        )
        _adaptive_timeouts[name] = AdaptiveTimeout(config)
    return _adaptive_timeouts[name]


def create_fixed_timeout(
    name: str,
    timeout_ms: float = 5000.0,
) -> FixedTimeout:
    """Create or get a fixed timeout by name."""
    if name not in _fixed_timeouts:
        config = FixedTimeoutConfig(
            name=name,
            timeout_ms=timeout_ms,
        )
        _fixed_timeouts[name] = FixedTimeout(config)
    return _fixed_timeouts[name]


def reset_all_timeouts() -> None:
    """Reset all timeouts (for testing)."""
    for adaptive in _adaptive_timeouts.values():
        adaptive.reset()
    for fixed in _fixed_timeouts.values():
        fixed.reset()
    _adaptive_timeouts.clear()
    _fixed_timeouts.clear()
