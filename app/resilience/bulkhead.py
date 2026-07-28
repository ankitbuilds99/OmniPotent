"""Bulkhead isolation pattern for resource protection.

Provides semaphore-based isolation to prevent resource exhaustion
and limit concurrent access to protected resources.
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from threading import Semaphore
from typing import Any, AsyncIterator, Callable, Iterator, TypeVar

log = logging.getLogger("devcopilot.resilience.bulkhead")

T = TypeVar("T")


class BulkheadFullError(Exception):
    """Raised when the bulkhead is at capacity and cannot accept more requests."""

    def __init__(
        self,
        message: str,
        bulkhead_name: str,
        current_count: int,
        max_concurrent: int,
    ):
        super().__init__(message)
        self.bulkhead_name = bulkhead_name
        self.current_count = current_count
        self.max_concurrent = max_concurrent


@dataclass
class BulkheadStats:
    """Statistics for a bulkhead."""

    total_acquired: int = 0
    total_rejected: int = 0
    total_released: int = 0
    current_count: int = 0
    max_concurrent_seen: int = 0
    total_wait_time_ms: float = 0.0

    @property
    def rejection_rate(self) -> float:
        """Calculate rejection rate (0.0 to 1.0)."""
        total = self.total_acquired + self.total_rejected
        if total == 0:
            return 0.0
        return self.total_rejected / total

    @property
    def avg_wait_time_ms(self) -> float:
        """Calculate average wait time in milliseconds."""
        if self.total_acquired == 0:
            return 0.0
        return self.total_wait_time_ms / self.total_acquired


@dataclass
class BulkheadConfig:
    """Configuration for a bulkhead.

    Attributes:
        max_concurrent: Maximum concurrent executions
        max_wait_seconds: Maximum time to wait for a slot (0 = no wait)
        name: Name for logging/identification
    """

    max_concurrent: int = 10
    max_wait_seconds: float = 0.0
    name: str = "default"


class Bulkhead:
    """Semaphore-based bulkhead for limiting concurrent access.

    Protects resources by limiting the number of concurrent executions.
    When the limit is reached, requests either wait or are rejected.

    Usage:
        bulkhead = Bulkhead(BulkheadConfig(max_concurrent=5))

        # Context manager usage
        async with bulkhead.acquire():
            await do_work()

        # Decorator usage
        @bulkhead
        async def protected_function():
            await do_work()
    """

    def __init__(
        self,
        config: BulkheadConfig,
        clock_fn: Callable[[], float] = time.monotonic,
    ):
        self.config = config
        self._clock = clock_fn
        self._semaphore = asyncio.Semaphore(config.max_concurrent)
        self._sync_semaphore = Semaphore(config.max_concurrent)
        self._stats = BulkheadStats()
        self._current_count = 0
        self._lock = asyncio.Lock()

    @property
    def stats(self) -> BulkheadStats:
        """Get current statistics."""
        return self._stats

    @property
    def available_slots(self) -> int:
        """Get number of available slots."""
        return self.config.max_concurrent - self._current_count

    @property
    def is_full(self) -> bool:
        """Check if bulkhead is at capacity."""
        return self._current_count >= self.config.max_concurrent

    async def _try_acquire(self, timeout: float | None = None) -> bool:
        """Try to acquire a slot with optional timeout."""
        if timeout is None or timeout <= 0:
            return self._semaphore.locked() is False and await self._semaphore.acquire()

        try:
            await asyncio.wait_for(
                self._semaphore.acquire(),
                timeout=timeout,
            )
            return True
        except asyncio.TimeoutError:
            return False

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[None]:
        """Acquire a slot in the bulkhead.

        Yields:
            None when slot is acquired

        Raises:
            BulkheadFullError: If bulkhead is full and max_wait_seconds exceeded
        """
        start = self._clock()

        if self.config.max_wait_seconds > 0:
            try:
                await asyncio.wait_for(
                    self._semaphore.acquire(),
                    timeout=self.config.max_wait_seconds,
                )
            except asyncio.TimeoutError:
                self._stats.total_rejected += 1
                raise BulkheadFullError(
                    f"Bulkhead '{self.config.name}' is full",
                    bulkhead_name=self.config.name,
                    current_count=self._current_count,
                    max_concurrent=self.config.max_concurrent,
                )
        else:
            if self._semaphore.locked():
                self._stats.total_rejected += 1
                raise BulkheadFullError(
                    f"Bulkhead '{self.config.name}' is full",
                    bulkhead_name=self.config.name,
                    current_count=self._current_count,
                    max_concurrent=self.config.max_concurrent,
                )
            await self._semaphore.acquire()

        wait_time_ms = (self._clock() - start) * 1000
        self._stats.total_acquired += 1
        self._stats.total_wait_time_ms += wait_time_ms
        self._current_count += 1
        self._stats.current_count = self._current_count
        self._stats.max_concurrent_seen = max(
            self._stats.max_concurrent_seen,
            self._current_count,
        )

        try:
            yield
        finally:
            self._semaphore.release()
            self._current_count -= 1
            self._stats.current_count = self._current_count
            self._stats.total_released += 1

    @contextmanager
    def acquire_sync(self) -> Iterator[None]:
        """Synchronous version of acquire().

        Yields:
            None when slot is acquired

        Raises:
            BulkheadFullError: If bulkhead is full and max_wait_seconds exceeded
        """
        start = self._clock()

        acquired = self._sync_semaphore.acquire(
            blocking=self.config.max_wait_seconds > 0,
            timeout=self.config.max_wait_seconds if self.config.max_wait_seconds > 0 else None,
        )

        if not acquired:
            self._stats.total_rejected += 1
            raise BulkheadFullError(
                f"Bulkhead '{self.config.name}' is full",
                bulkhead_name=self.config.name,
                current_count=self._current_count,
                max_concurrent=self.config.max_concurrent,
            )

        wait_time_ms = (self._clock() - start) * 1000
        self._stats.total_acquired += 1
        self._stats.total_wait_time_ms += wait_time_ms
        self._current_count += 1
        self._stats.current_count = self._current_count
        self._stats.max_concurrent_seen = max(
            self._stats.max_concurrent_seen,
            self._current_count,
        )

        try:
            yield
        finally:
            self._sync_semaphore.release()
            self._current_count -= 1
            self._stats.current_count = self._current_count
            self._stats.total_released += 1

    def __call__(self, func: Callable[..., T]) -> Callable[..., T]:
        """Decorator to wrap a function with bulkhead protection.

        Example:
            bulkhead = Bulkhead(BulkheadConfig(max_concurrent=5))

            @bulkhead
            async def protected_function():
                ...
        """
        import functools

        if asyncio.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                async with self.acquire():
                    return await func(*args, **kwargs)

            return async_wrapper  # type: ignore[return-value]
        else:

            @functools.wraps(func)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                with self.acquire_sync():
                    return func(*args, **kwargs)

            return sync_wrapper  # type: ignore[return-value]

    def reset(self) -> None:
        """Reset statistics."""
        self._stats = BulkheadStats()
        log.debug("Reset bulkhead '%s'", self.config.name)


_bulkheads: dict[str, Bulkhead] = {}


def get_bulkhead(name: str) -> Bulkhead | None:
    """Get a bulkhead by name."""
    return _bulkheads.get(name)


def create_bulkhead(
    name: str,
    max_concurrent: int = 10,
    max_wait_seconds: float = 0.0,
) -> Bulkhead:
    """Create or get a bulkhead by name."""
    if name not in _bulkheads:
        config = BulkheadConfig(
            name=name,
            max_concurrent=max_concurrent,
            max_wait_seconds=max_wait_seconds,
        )
        _bulkheads[name] = Bulkhead(config)
    return _bulkheads[name]


def reset_all_bulkheads() -> None:
    """Reset all bulkheads (for testing)."""
    for bulkhead in _bulkheads.values():
        bulkhead.reset()
    _bulkheads.clear()
