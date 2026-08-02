"""Advanced backoff strategies with jitter.

Provides decorrelated jitter (AWS recommended), equal jitter, full jitter,
and adaptive backoff based on error patterns.
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable

log = logging.getLogger("omnipotent.resilience.backoff")


class JitterStrategy(Enum):
    """Jitter strategies for exponential backoff."""

    NONE = "none"
    FULL = "full"
    EQUAL = "equal"
    DECORRELATED = "decorrelated"


@dataclass
class BackoffConfig:
    """Configuration for backoff behavior.

    Attributes:
        base_delay: Initial delay in seconds
        max_delay: Maximum delay cap in seconds
        multiplier: Exponential multiplier (default 2.0)
        jitter: Jitter strategy to use
    """

    base_delay: float = 1.0
    max_delay: float = 60.0
    multiplier: float = 2.0
    jitter: JitterStrategy = JitterStrategy.DECORRELATED


def compute_delay_no_jitter(attempt: int, config: BackoffConfig) -> float:
    """Compute delay without jitter: base * multiplier^attempt, capped at max_delay."""
    delay = config.base_delay * (config.multiplier ** attempt)
    return min(delay, config.max_delay)


def compute_delay_full_jitter(attempt: int, config: BackoffConfig) -> float:
    """Full jitter: random between 0 and exponential delay.

    sleep = random_between(0, min(cap, base * 2^attempt))

    Provides best spread but can result in very short sleeps.
    """
    exp_delay = compute_delay_no_jitter(attempt, config)
    return random.uniform(0, exp_delay)


def compute_delay_equal_jitter(attempt: int, config: BackoffConfig) -> float:
    """Equal jitter: half exponential + random half.

    sleep = temp / 2 + random_between(0, temp / 2)
    where temp = min(cap, base * 2^attempt)

    Provides a guaranteed minimum delay while still adding randomness.
    """
    exp_delay = compute_delay_no_jitter(attempt, config)
    half = exp_delay / 2
    return half + random.uniform(0, half)


@dataclass
class DecorrelatedState:
    """State for decorrelated jitter algorithm."""

    last_delay: float = 0.0


def compute_delay_decorrelated(
    attempt: int,
    config: BackoffConfig,
    state: DecorrelatedState,
) -> float:
    """Decorrelated jitter (AWS recommended).

    sleep = min(cap, random_between(base, sleep * 3))

    Each delay is based on the previous delay rather than the attempt number,
    providing better spread and avoiding thundering herd.
    """
    if attempt == 0 or state.last_delay == 0:
        delay = config.base_delay
    else:
        delay = random.uniform(config.base_delay, state.last_delay * 3)

    capped = min(delay, config.max_delay)
    state.last_delay = capped
    return capped


def compute_delay(
    attempt: int,
    config: BackoffConfig,
    decorrelated_state: DecorrelatedState | None = None,
) -> float:
    """Compute delay based on the configured jitter strategy.

    Args:
        attempt: Current attempt number (0-indexed)
        config: Backoff configuration
        decorrelated_state: State for decorrelated jitter (required if using DECORRELATED)

    Returns:
        Delay in seconds
    """
    if config.jitter == JitterStrategy.NONE:
        return compute_delay_no_jitter(attempt, config)
    elif config.jitter == JitterStrategy.FULL:
        return compute_delay_full_jitter(attempt, config)
    elif config.jitter == JitterStrategy.EQUAL:
        return compute_delay_equal_jitter(attempt, config)
    elif config.jitter == JitterStrategy.DECORRELATED:
        if decorrelated_state is None:
            decorrelated_state = DecorrelatedState()
        return compute_delay_decorrelated(attempt, config, decorrelated_state)
    else:
        return compute_delay_no_jitter(attempt, config)


@dataclass
class AdaptiveBackoffState:
    """State for adaptive backoff that learns from error patterns.

    Tracks recent error rates and adjusts backoff aggressiveness accordingly.
    """

    window_successes: int = 0
    window_failures: int = 0
    current_multiplier: float = 1.0
    min_multiplier: float = 0.5
    max_multiplier: float = 4.0
    adjustment_rate: float = 0.1


def compute_adaptive_delay(
    attempt: int,
    config: BackoffConfig,
    adaptive_state: AdaptiveBackoffState,
    decorrelated_state: DecorrelatedState | None = None,
) -> float:
    """Compute delay with adaptive multiplier based on error patterns.

    If error rate is high, increase the multiplier (back off more aggressively).
    If error rate is low, decrease the multiplier (retry more quickly).
    """
    base_delay = compute_delay(attempt, config, decorrelated_state)
    return base_delay * adaptive_state.current_multiplier


def record_adaptive_outcome(state: AdaptiveBackoffState, success: bool) -> None:
    """Record an outcome and adjust the adaptive multiplier.

    Args:
        state: Adaptive backoff state to update
        success: Whether the operation succeeded
    """
    if success:
        state.window_successes += 1
        state.current_multiplier = max(
            state.min_multiplier,
            state.current_multiplier - state.adjustment_rate,
        )
    else:
        state.window_failures += 1
        state.current_multiplier = min(
            state.max_multiplier,
            state.current_multiplier + state.adjustment_rate,
        )


@dataclass
class RetryContext:
    """Context passed to retry callbacks."""

    attempt: int
    delay: float
    exception: BaseException
    elapsed_seconds: float


def retry_with_backoff(
    fn: Callable[[], object],
    config: BackoffConfig,
    max_attempts: int = 5,
    retryable: Callable[[BaseException], bool] | None = None,
    on_retry: Callable[[RetryContext], None] | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    clock_fn: Callable[[], float] = time.monotonic,
) -> object:
    """Execute a function with configurable backoff.

    Args:
        fn: Function to execute
        config: Backoff configuration
        max_attempts: Maximum number of attempts
        retryable: Predicate to determine if exception is retryable
        on_retry: Callback invoked before each retry
        sleep_fn: Sleep function (injectable for testing)
        clock_fn: Clock function (injectable for testing)

    Returns:
        Result of fn()

    Raises:
        The last exception if all attempts fail or exception is not retryable
    """
    decorrelated_state = DecorrelatedState()
    start_time = clock_fn()
    last_exc: BaseException | None = None

    for attempt in range(max_attempts):
        try:
            return fn()
        except BaseException as exc:
            last_exc = exc

            is_last = attempt >= max_attempts - 1
            should_retry = retryable(exc) if retryable else True

            if is_last or not should_retry:
                raise

            delay = compute_delay(attempt, config, decorrelated_state)
            elapsed = clock_fn() - start_time

            if on_retry:
                ctx = RetryContext(
                    attempt=attempt,
                    delay=delay,
                    exception=exc,
                    elapsed_seconds=elapsed,
                )
                on_retry(ctx)

            log.debug(
                "Retry %d/%d after %.2fs (elapsed: %.2fs): %s",
                attempt + 1,
                max_attempts,
                delay,
                elapsed,
                exc,
            )
            sleep_fn(delay)

    assert last_exc is not None
    raise last_exc


async def async_retry_with_backoff(
    fn: Callable[[], object],
    config: BackoffConfig,
    max_attempts: int = 5,
    retryable: Callable[[BaseException], bool] | None = None,
    on_retry: Callable[[RetryContext], None] | None = None,
    clock_fn: Callable[[], float] = time.monotonic,
) -> object:
    """Async version of retry_with_backoff.

    Args:
        fn: Async function to execute
        config: Backoff configuration
        max_attempts: Maximum number of attempts
        retryable: Predicate to determine if exception is retryable
        on_retry: Callback invoked before each retry
        clock_fn: Clock function (injectable for testing)

    Returns:
        Result of fn()

    Raises:
        The last exception if all attempts fail or exception is not retryable
    """
    import asyncio

    decorrelated_state = DecorrelatedState()
    start_time = clock_fn()
    last_exc: BaseException | None = None

    for attempt in range(max_attempts):
        try:
            result = fn()
            if asyncio.iscoroutine(result):
                return await result
            return result
        except BaseException as exc:
            last_exc = exc

            is_last = attempt >= max_attempts - 1
            should_retry = retryable(exc) if retryable else True

            if is_last or not should_retry:
                raise

            delay = compute_delay(attempt, config, decorrelated_state)
            elapsed = clock_fn() - start_time

            if on_retry:
                ctx = RetryContext(
                    attempt=attempt,
                    delay=delay,
                    exception=exc,
                    elapsed_seconds=elapsed,
                )
                on_retry(ctx)

            log.debug(
                "Retry %d/%d after %.2fs (elapsed: %.2fs): %s",
                attempt + 1,
                max_attempts,
                delay,
                elapsed,
                exc,
            )
            await asyncio.sleep(delay)

    assert last_exc is not None
    raise last_exc
