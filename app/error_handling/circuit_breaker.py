"""Circuit breaker pattern implementation.

Prevents cascading failures by tracking error rates and temporarily
stopping calls to failing services.

States:
- CLOSED: Normal operation, calls pass through
- OPEN: Failures exceeded threshold, calls fail immediately
- HALF_OPEN: Testing if service recovered
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, TypeVar

log = logging.getLogger("devcopilot.error_handling.circuit_breaker")

F = TypeVar("F", bound=Callable[..., Any])


class CircuitState(Enum):
    """Circuit breaker states."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(Exception):
    """Raised when the circuit is open and calls are blocked."""

    def __init__(
        self,
        message: str = "Circuit breaker is open",
        service: str | None = None,
        reset_time: float | None = None,
    ):
        super().__init__(message)
        self.service = service
        self.reset_time = reset_time


@dataclass
class CircuitStats:
    """Statistics for a circuit breaker."""

    total_calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    rejected_calls: int = 0
    last_failure_time: float | None = None
    last_success_time: float | None = None
    consecutive_failures: int = 0
    consecutive_successes: int = 0


class CircuitBreaker:
    """Circuit breaker for protecting against cascading failures.

    Usage:
        breaker = CircuitBreaker(name="datadog", failure_threshold=5)

        @breaker
        async def call_datadog():
            return await datadog_client.query(...)

        # Or manual usage:
        async with breaker:
            result = await call_datadog()
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        success_threshold: int = 3,
        timeout: float = 60.0,
        half_open_max_calls: int = 3,
        failure_rate_threshold: float | None = None,
        window_size: int = 100,
        excluded_exceptions: tuple[type[Exception], ...] = (),
        on_state_change: Callable[[CircuitState, CircuitState], None] | None = None,
    ):
        """Initialize the circuit breaker.

        Args:
            name: Name for logging/identification
            failure_threshold: Consecutive failures to trip circuit
            success_threshold: Consecutive successes to close circuit
            timeout: Seconds to wait before half-open (testing)
            half_open_max_calls: Max concurrent calls in half-open state
            failure_rate_threshold: Optional failure rate (0-1) to trip circuit
            window_size: Rolling window size for failure rate calculation
            excluded_exceptions: Exceptions that don't count as failures
            on_state_change: Callback when state changes
        """
        self.name = name
        self.failure_threshold = failure_threshold
        self.success_threshold = success_threshold
        self.timeout = timeout
        self.half_open_max_calls = half_open_max_calls
        self.failure_rate_threshold = failure_rate_threshold
        self.window_size = window_size
        self.excluded_exceptions = excluded_exceptions
        self.on_state_change = on_state_change

        self._state = CircuitState.CLOSED
        self._stats = CircuitStats()
        self._last_failure_time: float = 0
        self._half_open_calls = 0
        self._lock = asyncio.Lock()

        self._outcomes: deque[bool] = deque(maxlen=window_size)

    @property
    def state(self) -> CircuitState:
        """Current circuit state."""
        return self._state

    @property
    def stats(self) -> CircuitStats:
        """Current statistics."""
        return self._stats

    def _set_state(self, new_state: CircuitState) -> None:
        """Change state with callback."""
        if new_state != self._state:
            old_state = self._state
            self._state = new_state
            log.info(
                "Circuit breaker '%s' state change: %s -> %s",
                self.name,
                old_state.value,
                new_state.value,
            )
            if self.on_state_change:
                self.on_state_change(old_state, new_state)

    def _should_trip(self) -> bool:
        """Check if circuit should trip to OPEN."""
        if self._stats.consecutive_failures >= self.failure_threshold:
            return True

        if self.failure_rate_threshold and len(self._outcomes) >= self.window_size:
            failures = sum(1 for o in self._outcomes if not o)
            rate = failures / len(self._outcomes)
            if rate >= self.failure_rate_threshold:
                return True

        return False

    def _should_attempt_reset(self) -> bool:
        """Check if enough time has passed to try half-open."""
        return time.monotonic() - self._last_failure_time >= self.timeout

    def _record_success(self) -> None:
        """Record a successful call."""
        self._stats.total_calls += 1
        self._stats.successful_calls += 1
        self._stats.last_success_time = time.monotonic()
        self._stats.consecutive_failures = 0
        self._stats.consecutive_successes += 1
        self._outcomes.append(True)

        if self._state == CircuitState.HALF_OPEN:
            if self._stats.consecutive_successes >= self.success_threshold:
                self._set_state(CircuitState.CLOSED)
                self._half_open_calls = 0

    def _record_failure(self, exception: Exception) -> None:
        """Record a failed call."""
        if isinstance(exception, self.excluded_exceptions):
            return

        self._stats.total_calls += 1
        self._stats.failed_calls += 1
        self._stats.last_failure_time = time.monotonic()
        self._stats.consecutive_successes = 0
        self._stats.consecutive_failures += 1
        self._last_failure_time = time.monotonic()
        self._outcomes.append(False)

        if self._state == CircuitState.HALF_OPEN:
            self._set_state(CircuitState.OPEN)
            self._half_open_calls = 0
        elif self._state == CircuitState.CLOSED and self._should_trip():
            self._set_state(CircuitState.OPEN)

    async def _check_state(self) -> None:
        """Check and potentially update circuit state."""
        async with self._lock:
            if self._state == CircuitState.OPEN:
                if self._should_attempt_reset():
                    self._set_state(CircuitState.HALF_OPEN)
                    self._stats.consecutive_successes = 0

    def _can_execute(self) -> bool:
        """Check if a call can proceed."""
        if self._state == CircuitState.CLOSED:
            return True

        if self._state == CircuitState.HALF_OPEN:
            return self._half_open_calls < self.half_open_max_calls

        return False

    async def __aenter__(self) -> "CircuitBreaker":
        """Async context manager entry."""
        await self._check_state()

        if not self._can_execute():
            self._stats.rejected_calls += 1
            reset_time = self._last_failure_time + self.timeout
            raise CircuitOpenError(
                f"Circuit breaker '{self.name}' is open",
                service=self.name,
                reset_time=reset_time,
            )

        if self._state == CircuitState.HALF_OPEN:
            self._half_open_calls += 1

        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        if self._state == CircuitState.HALF_OPEN:
            self._half_open_calls = max(0, self._half_open_calls - 1)

        if exc_val is None:
            self._record_success()
        elif isinstance(exc_val, Exception):
            self._record_failure(exc_val)

    def __call__(self, func: F) -> F:
        """Decorator to wrap a function with circuit breaker.

        Example:
            @circuit_breaker
            async def call_service():
                ...
        """
        import functools

        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            async with self:
                return await func(*args, **kwargs)

        return wrapper  # type: ignore

    def reset(self) -> None:
        """Manually reset the circuit to closed state."""
        self._set_state(CircuitState.CLOSED)
        self._stats = CircuitStats()
        self._outcomes.clear()
        self._half_open_calls = 0
        log.info("Circuit breaker '%s' manually reset", self.name)

    def force_open(self) -> None:
        """Manually force the circuit open."""
        self._set_state(CircuitState.OPEN)
        self._last_failure_time = time.monotonic()
        log.info("Circuit breaker '%s' manually opened", self.name)


_circuit_breakers: dict[str, CircuitBreaker] = {}


def get_circuit_breaker(
    name: str,
    **kwargs: Any,
) -> CircuitBreaker:
    """Get or create a circuit breaker by name.

    This allows sharing circuit breakers across different parts
    of the application for the same service.
    """
    if name not in _circuit_breakers:
        _circuit_breakers[name] = CircuitBreaker(name=name, **kwargs)
    return _circuit_breakers[name]


def reset_all_circuit_breakers() -> None:
    """Reset all circuit breakers (for testing)."""
    for breaker in _circuit_breakers.values():
        breaker.reset()
