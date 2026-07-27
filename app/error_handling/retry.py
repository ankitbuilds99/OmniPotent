"""Retry utilities with exponential backoff.

Provides decorators and functions for retrying failed operations
with configurable backoff, jitter, and exception filtering.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import random
import time
from dataclasses import dataclass
from typing import Any, Callable, TypeVar

from app.error_handling.exceptions import CopilotError

log = logging.getLogger("devcopilot.error_handling.retry")

F = TypeVar("F", bound=Callable[..., Any])


@dataclass
class RetryConfig:
    """Configuration for retry behavior.

    Attributes:
        max_attempts: Maximum number of attempts (including first try)
        initial_delay: Initial delay between retries in seconds
        max_delay: Maximum delay between retries
        exponential_base: Base for exponential backoff (delay *= base)
        jitter: Whether to add random jitter to delays
        jitter_factor: Maximum jitter as fraction of delay
        retry_on: Exception types to retry on (default: all retryable)
        dont_retry_on: Exception types to never retry
        on_retry: Callback function called before each retry
    """

    max_attempts: int = 3
    initial_delay: float = 1.0
    max_delay: float = 60.0
    exponential_base: float = 2.0
    jitter: bool = True
    jitter_factor: float = 0.25
    retry_on: tuple[type[Exception], ...] = (Exception,)
    dont_retry_on: tuple[type[Exception], ...] = ()
    on_retry: Callable[[int, Exception, float], None] | None = None

    def calculate_delay(self, attempt: int) -> float:
        """Calculate delay for a given attempt number."""
        delay = self.initial_delay * (self.exponential_base ** (attempt - 1))
        delay = min(delay, self.max_delay)

        if self.jitter:
            jitter_range = delay * self.jitter_factor
            delay += random.uniform(-jitter_range, jitter_range)

        return max(0.1, delay)

    def should_retry(self, exception: Exception) -> bool:
        """Check if an exception should trigger a retry."""
        if isinstance(exception, self.dont_retry_on):
            return False

        if isinstance(exception, CopilotError):
            return exception.retryable

        return isinstance(exception, self.retry_on)


def with_retry(
    config: RetryConfig | None = None,
    max_attempts: int | None = None,
    initial_delay: float | None = None,
    max_delay: float | None = None,
    retry_on: tuple[type[Exception], ...] | None = None,
) -> Callable[[F], F]:
    """Decorator for retrying synchronous functions.

    Args:
        config: Full RetryConfig object (overrides other args)
        max_attempts: Override max attempts
        initial_delay: Override initial delay
        max_delay: Override max delay
        retry_on: Override retry exception types

    Returns:
        Decorated function with retry behavior

    Example:
        @with_retry(max_attempts=3, initial_delay=1.0)
        def fetch_data():
            return requests.get(url).json()
    """
    if config is None:
        config = RetryConfig()

    if max_attempts is not None:
        config = RetryConfig(
            max_attempts=max_attempts,
            initial_delay=config.initial_delay,
            max_delay=config.max_delay,
            exponential_base=config.exponential_base,
            jitter=config.jitter,
            jitter_factor=config.jitter_factor,
            retry_on=config.retry_on,
            dont_retry_on=config.dont_retry_on,
            on_retry=config.on_retry,
        )
    if initial_delay is not None:
        config = RetryConfig(
            max_attempts=config.max_attempts,
            initial_delay=initial_delay,
            max_delay=config.max_delay,
            exponential_base=config.exponential_base,
            jitter=config.jitter,
            jitter_factor=config.jitter_factor,
            retry_on=config.retry_on,
            dont_retry_on=config.dont_retry_on,
            on_retry=config.on_retry,
        )
    if max_delay is not None:
        config = RetryConfig(
            max_attempts=config.max_attempts,
            initial_delay=config.initial_delay,
            max_delay=max_delay,
            exponential_base=config.exponential_base,
            jitter=config.jitter,
            jitter_factor=config.jitter_factor,
            retry_on=config.retry_on,
            dont_retry_on=config.dont_retry_on,
            on_retry=config.on_retry,
        )
    if retry_on is not None:
        config = RetryConfig(
            max_attempts=config.max_attempts,
            initial_delay=config.initial_delay,
            max_delay=config.max_delay,
            exponential_base=config.exponential_base,
            jitter=config.jitter,
            jitter_factor=config.jitter_factor,
            retry_on=retry_on,
            dont_retry_on=config.dont_retry_on,
            on_retry=config.on_retry,
        )

    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exception: Exception | None = None

            for attempt in range(1, config.max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exception = e

                    if not config.should_retry(e) or attempt == config.max_attempts:
                        raise

                    delay = config.calculate_delay(attempt)

                    log.warning(
                        "Retry %d/%d for %s after error: %s (delay: %.2fs)",
                        attempt,
                        config.max_attempts,
                        func.__name__,
                        str(e)[:200],
                        delay,
                    )

                    if config.on_retry:
                        config.on_retry(attempt, e, delay)

                    time.sleep(delay)

            if last_exception:
                raise last_exception

        return wrapper  # type: ignore

    return decorator


def async_with_retry(
    config: RetryConfig | None = None,
    max_attempts: int | None = None,
    initial_delay: float | None = None,
    max_delay: float | None = None,
    retry_on: tuple[type[Exception], ...] | None = None,
) -> Callable[[F], F]:
    """Decorator for retrying async functions.

    Same parameters as with_retry, but for async functions.

    Example:
        @async_with_retry(max_attempts=3)
        async def fetch_data():
            async with httpx.AsyncClient() as client:
                return await client.get(url)
    """
    if config is None:
        config = RetryConfig()

    if max_attempts is not None:
        config = RetryConfig(
            max_attempts=max_attempts,
            initial_delay=config.initial_delay,
            max_delay=config.max_delay,
            exponential_base=config.exponential_base,
            jitter=config.jitter,
            jitter_factor=config.jitter_factor,
            retry_on=config.retry_on,
            dont_retry_on=config.dont_retry_on,
            on_retry=config.on_retry,
        )
    if initial_delay is not None:
        config = RetryConfig(
            max_attempts=config.max_attempts,
            initial_delay=initial_delay,
            max_delay=config.max_delay,
            exponential_base=config.exponential_base,
            jitter=config.jitter,
            jitter_factor=config.jitter_factor,
            retry_on=config.retry_on,
            dont_retry_on=config.dont_retry_on,
            on_retry=config.on_retry,
        )
    if max_delay is not None:
        config = RetryConfig(
            max_attempts=config.max_attempts,
            initial_delay=config.initial_delay,
            max_delay=max_delay,
            exponential_base=config.exponential_base,
            jitter=config.jitter,
            jitter_factor=config.jitter_factor,
            retry_on=config.retry_on,
            dont_retry_on=config.dont_retry_on,
            on_retry=config.on_retry,
        )
    if retry_on is not None:
        config = RetryConfig(
            max_attempts=config.max_attempts,
            initial_delay=config.initial_delay,
            max_delay=config.max_delay,
            exponential_base=config.exponential_base,
            jitter=config.jitter,
            jitter_factor=config.jitter_factor,
            retry_on=retry_on,
            dont_retry_on=config.dont_retry_on,
            on_retry=config.on_retry,
        )

    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exception: Exception | None = None

            for attempt in range(1, config.max_attempts + 1):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    last_exception = e

                    if not config.should_retry(e) or attempt == config.max_attempts:
                        raise

                    delay = config.calculate_delay(attempt)

                    log.warning(
                        "Retry %d/%d for %s after error: %s (delay: %.2fs)",
                        attempt,
                        config.max_attempts,
                        func.__name__,
                        str(e)[:200],
                        delay,
                    )

                    if config.on_retry:
                        config.on_retry(attempt, e, delay)

                    await asyncio.sleep(delay)

            if last_exception:
                raise last_exception

        return wrapper  # type: ignore

    return decorator


async def retry_async(
    func: Callable[..., Any],
    *args: Any,
    config: RetryConfig | None = None,
    **kwargs: Any,
) -> Any:
    """Retry an async function call with the given config.

    Useful when you can't use decorators (e.g., lambda or inline).

    Example:
        result = await retry_async(
            client.search_logs,
            query="error",
            config=RetryConfig(max_attempts=3),
        )
    """
    if config is None:
        config = RetryConfig()

    last_exception: Exception | None = None

    for attempt in range(1, config.max_attempts + 1):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            last_exception = e

            if not config.should_retry(e) or attempt == config.max_attempts:
                raise

            delay = config.calculate_delay(attempt)

            log.warning(
                "Retry %d/%d for %s after error: %s (delay: %.2fs)",
                attempt,
                config.max_attempts,
                getattr(func, "__name__", str(func)),
                str(e)[:200],
                delay,
            )

            if config.on_retry:
                config.on_retry(attempt, e, delay)

            await asyncio.sleep(delay)

    if last_exception:
        raise last_exception
