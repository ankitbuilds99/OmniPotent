"""Priority-based load shedding for overload protection.

Provides mechanisms to shed load under pressure by rejecting
lower-priority requests to protect capacity for critical traffic.
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import IntEnum
from typing import AsyncIterator, Callable

log = logging.getLogger("omnipotent.resilience.load_shedding")


class Priority(IntEnum):
    """Request priority levels (higher number = higher priority)."""

    LOW = 1
    NORMAL = 5
    HIGH = 8
    CRITICAL = 10


@dataclass
class LoadSheddingConfig:
    """Configuration for load shedding.

    Attributes:
        capacity: Maximum concurrent requests at full capacity
        shed_threshold: Load factor (0.0-1.0) at which to start shedding
        critical_threshold: Load factor above which only CRITICAL passes
        recovery_threshold: Load factor below which to stop shedding
        min_priority_at_full: Minimum priority allowed when at capacity
        name: Name for logging/identification
    """

    capacity: int = 100
    shed_threshold: float = 0.7
    critical_threshold: float = 0.9
    recovery_threshold: float = 0.5
    min_priority_at_full: Priority = Priority.HIGH
    name: str = "default"


@dataclass
class LoadSheddingStats:
    """Statistics for load shedding."""

    total_requests: int = 0
    admitted_requests: int = 0
    shed_requests: int = 0
    current_load: int = 0
    max_load_seen: int = 0
    shed_by_priority: dict[int, int] = field(default_factory=lambda: {p.value: 0 for p in Priority})

    @property
    def shed_rate(self) -> float:
        """Calculate shed rate (0.0 to 1.0)."""
        if self.total_requests == 0:
            return 0.0
        return self.shed_requests / self.total_requests

    @property
    def admission_rate(self) -> float:
        """Calculate admission rate (0.0 to 1.0)."""
        if self.total_requests == 0:
            return 1.0
        return self.admitted_requests / self.total_requests


class LoadShedError(Exception):
    """Raised when a request is shed due to overload."""

    def __init__(
        self,
        message: str,
        priority: Priority,
        current_load: int,
        capacity: int,
    ):
        super().__init__(message)
        self.priority = priority
        self.current_load = current_load
        self.capacity = capacity


class LoadShedder:
    """Priority-based load shedder.

    Tracks current load and sheds lower-priority requests when
    load exceeds configured thresholds.

    Usage:
        shedder = LoadShedder(LoadSheddingConfig(capacity=100))

        async with shedder.admit(Priority.NORMAL):
            await handle_request()

        # Or check admission without context manager
        if shedder.should_admit(Priority.LOW):
            await handle_request()
    """

    def __init__(
        self,
        config: LoadSheddingConfig,
        clock_fn: Callable[[], float] = time.monotonic,
    ):
        self.config = config
        self._clock = clock_fn
        self._stats = LoadSheddingStats()
        self._current_load = 0
        self._is_shedding = False
        self._lock = asyncio.Lock()

    @property
    def stats(self) -> LoadSheddingStats:
        """Get current statistics."""
        return self._stats

    @property
    def current_load(self) -> int:
        """Get current load (number of active requests)."""
        return self._current_load

    @property
    def load_factor(self) -> float:
        """Get current load factor (0.0 to 1.0)."""
        return self._current_load / self.config.capacity

    @property
    def is_shedding(self) -> bool:
        """Check if currently shedding load."""
        return self._is_shedding

    def _compute_min_priority(self) -> Priority:
        """Compute minimum priority to admit based on current load."""
        load_factor = self.load_factor

        if load_factor >= self.config.critical_threshold:
            return Priority.CRITICAL
        elif load_factor >= self.config.shed_threshold:
            range_size = self.config.critical_threshold - self.config.shed_threshold
            if range_size <= 0:
                return Priority.HIGH

            position = (load_factor - self.config.shed_threshold) / range_size

            if position < 0.33:
                return Priority.NORMAL
            elif position < 0.66:
                return Priority.HIGH
            else:
                return Priority.HIGH

        return Priority.LOW

    def should_admit(self, priority: Priority) -> bool:
        """Check if a request with given priority should be admitted.

        Args:
            priority: Request priority

        Returns:
            True if request should be admitted
        """
        if self._current_load >= self.config.capacity:
            return priority >= self.config.min_priority_at_full

        min_priority = self._compute_min_priority()
        return priority >= min_priority

    def _update_shedding_state(self) -> None:
        """Update the shedding state based on current load."""
        load_factor = self.load_factor

        if not self._is_shedding and load_factor >= self.config.shed_threshold:
            self._is_shedding = True
            log.info(
                "Load shedder '%s' activated (load: %d/%d, %.1f%%)",
                self.config.name,
                self._current_load,
                self.config.capacity,
                load_factor * 100,
            )
        elif self._is_shedding and load_factor < self.config.recovery_threshold:
            self._is_shedding = False
            log.info(
                "Load shedder '%s' deactivated (load: %d/%d, %.1f%%)",
                self.config.name,
                self._current_load,
                self.config.capacity,
                load_factor * 100,
            )

    @asynccontextmanager
    async def admit(self, priority: Priority = Priority.NORMAL) -> AsyncIterator[None]:
        """Attempt to admit a request.

        Args:
            priority: Request priority

        Yields:
            None if admitted

        Raises:
            LoadShedError: If request is shed
        """
        self._stats.total_requests += 1

        if not self.should_admit(priority):
            self._stats.shed_requests += 1
            self._stats.shed_by_priority[priority.value] += 1
            log.debug(
                "Shed request (priority: %s, load: %d/%d)",
                priority.name,
                self._current_load,
                self.config.capacity,
            )
            raise LoadShedError(
                f"Request shed (priority: {priority.name})",
                priority=priority,
                current_load=self._current_load,
                capacity=self.config.capacity,
            )

        async with self._lock:
            self._current_load += 1
            self._stats.current_load = self._current_load
            self._stats.max_load_seen = max(
                self._stats.max_load_seen,
                self._current_load,
            )
            self._stats.admitted_requests += 1
            self._update_shedding_state()

        try:
            yield
        finally:
            async with self._lock:
                self._current_load -= 1
                self._stats.current_load = self._current_load
                self._update_shedding_state()

    def reset(self) -> None:
        """Reset statistics and state."""
        self._stats = LoadSheddingStats()
        self._current_load = 0
        self._is_shedding = False
        log.debug("Reset load shedder '%s'", self.config.name)


@dataclass
class AdaptiveLoadSheddingConfig:
    """Configuration for adaptive load shedding based on latency.

    Attributes:
        capacity: Maximum concurrent requests
        target_latency_ms: Target p99 latency to maintain
        max_latency_ms: Latency above which aggressive shedding starts
        window_size: Number of latency samples to track
        name: Name for logging/identification
    """

    capacity: int = 100
    target_latency_ms: float = 500.0
    max_latency_ms: float = 2000.0
    window_size: int = 100
    name: str = "default"


class AdaptiveLoadShedder:
    """Load shedder that adapts based on observed latency.

    Starts shedding when latency exceeds targets, recovering
    when latency improves.
    """

    def __init__(
        self,
        config: AdaptiveLoadSheddingConfig,
        clock_fn: Callable[[], float] = time.monotonic,
    ):
        self.config = config
        self._clock = clock_fn
        self._latencies: list[float] = []
        self._stats = LoadSheddingStats()
        self._current_load = 0
        self._shed_factor = 0.0
        self._lock = asyncio.Lock()

    @property
    def stats(self) -> LoadSheddingStats:
        """Get current statistics."""
        return self._stats

    @property
    def shed_factor(self) -> float:
        """Get current shed factor (0.0 = no shedding, 1.0 = max shedding)."""
        return self._shed_factor

    def _compute_p99_latency(self) -> float:
        """Compute p99 latency from samples."""
        if not self._latencies:
            return 0.0
        sorted_latencies = sorted(self._latencies)
        idx = int(len(sorted_latencies) * 0.99)
        idx = min(idx, len(sorted_latencies) - 1)
        return sorted_latencies[idx]

    def _update_shed_factor(self) -> None:
        """Update shed factor based on latency."""
        p99 = self._compute_p99_latency()

        if p99 <= self.config.target_latency_ms:
            self._shed_factor = max(0.0, self._shed_factor - 0.1)
        elif p99 >= self.config.max_latency_ms:
            self._shed_factor = min(1.0, self._shed_factor + 0.2)
        else:
            range_size = self.config.max_latency_ms - self.config.target_latency_ms
            position = (p99 - self.config.target_latency_ms) / range_size
            target_factor = position * 0.8
            self._shed_factor += (target_factor - self._shed_factor) * 0.1

    def should_admit(self, priority: Priority) -> bool:
        """Check if a request should be admitted based on priority and load."""
        if self._shed_factor <= 0:
            return True

        priority_threshold = Priority.LOW.value + (
            (Priority.CRITICAL.value - Priority.LOW.value) * self._shed_factor
        )

        return priority.value >= priority_threshold

    def record_latency(self, latency_ms: float) -> None:
        """Record a latency sample."""
        if len(self._latencies) >= self.config.window_size:
            self._latencies.pop(0)
        self._latencies.append(latency_ms)
        self._update_shed_factor()

    @asynccontextmanager
    async def admit(self, priority: Priority = Priority.NORMAL) -> AsyncIterator[None]:
        """Attempt to admit a request."""
        self._stats.total_requests += 1

        if not self.should_admit(priority):
            self._stats.shed_requests += 1
            self._stats.shed_by_priority[priority.value] += 1
            raise LoadShedError(
                f"Request shed (priority: {priority.name}, shed_factor: {self._shed_factor:.2f})",
                priority=priority,
                current_load=self._current_load,
                capacity=self.config.capacity,
            )

        async with self._lock:
            self._current_load += 1
            self._stats.current_load = self._current_load
            self._stats.max_load_seen = max(
                self._stats.max_load_seen,
                self._current_load,
            )
            self._stats.admitted_requests += 1

        start = self._clock()
        try:
            yield
        finally:
            latency_ms = (self._clock() - start) * 1000
            self.record_latency(latency_ms)

            async with self._lock:
                self._current_load -= 1
                self._stats.current_load = self._current_load

    def reset(self) -> None:
        """Reset statistics and state."""
        self._stats = LoadSheddingStats()
        self._latencies.clear()
        self._current_load = 0
        self._shed_factor = 0.0
        log.debug("Reset adaptive load shedder '%s'", self.config.name)


_load_shedders: dict[str, LoadShedder] = {}
_adaptive_shedders: dict[str, AdaptiveLoadShedder] = {}


def get_load_shedder(name: str) -> LoadShedder | None:
    """Get a load shedder by name."""
    return _load_shedders.get(name)


def create_load_shedder(
    name: str,
    capacity: int = 100,
    shed_threshold: float = 0.7,
    critical_threshold: float = 0.9,
) -> LoadShedder:
    """Create or get a load shedder by name."""
    if name not in _load_shedders:
        config = LoadSheddingConfig(
            name=name,
            capacity=capacity,
            shed_threshold=shed_threshold,
            critical_threshold=critical_threshold,
        )
        _load_shedders[name] = LoadShedder(config)
    return _load_shedders[name]


def create_adaptive_shedder(
    name: str,
    capacity: int = 100,
    target_latency_ms: float = 500.0,
    max_latency_ms: float = 2000.0,
) -> AdaptiveLoadShedder:
    """Create or get an adaptive load shedder by name."""
    if name not in _adaptive_shedders:
        config = AdaptiveLoadSheddingConfig(
            name=name,
            capacity=capacity,
            target_latency_ms=target_latency_ms,
            max_latency_ms=max_latency_ms,
        )
        _adaptive_shedders[name] = AdaptiveLoadShedder(config)
    return _adaptive_shedders[name]


def reset_all_shedders() -> None:
    """Reset all load shedders (for testing)."""
    for load_shedder in _load_shedders.values():
        load_shedder.reset()
    for adaptive_shedder in _adaptive_shedders.values():
        adaptive_shedder.reset()
    _load_shedders.clear()
    _adaptive_shedders.clear()
