"""Request coalescing to deduplicate identical concurrent requests.

When multiple identical requests arrive simultaneously, only one is executed
and the result is shared with all waiting callers. This reduces load on
expensive operations like LLM calls and database queries.
"""

from __future__ import annotations

import asyncio
import functools
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Generic, TypeVar

log = logging.getLogger("omnipotent.performance.coalescing")

T = TypeVar("T")


@dataclass
class CoalescedRequest(Generic[T]):
    """A coalesced request with multiple waiters."""

    key: str
    created_at: float
    future: asyncio.Future[T]
    waiter_count: int = 1
    completed_at: float | None = None
    result: T | None = None
    error: Exception | None = None


@dataclass
class CoalescingStats:
    """Statistics for request coalescing."""

    total_requests: int = 0
    coalesced_requests: int = 0
    executed_requests: int = 0
    cache_hits: int = 0
    errors: int = 0
    total_saved_time_ms: float = 0.0

    @property
    def coalescing_ratio(self) -> float:
        """Ratio of coalesced to total requests."""
        if self.total_requests == 0:
            return 0.0
        return self.coalesced_requests / self.total_requests

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "total_requests": self.total_requests,
            "coalesced_requests": self.coalesced_requests,
            "executed_requests": self.executed_requests,
            "cache_hits": self.cache_hits,
            "errors": self.errors,
            "coalescing_ratio": round(self.coalescing_ratio, 3),
            "total_saved_time_ms": round(self.total_saved_time_ms, 2),
        }


class RequestCoalescer(Generic[T]):
    """Coalesces identical concurrent requests."""

    def __init__(
        self,
        name: str = "default",
        max_coalesce_time_ms: float = 50.0,
        result_ttl_ms: float = 100.0,
        max_waiters: int = 100,
    ):
        self.name = name
        self._max_coalesce_time = max_coalesce_time_ms / 1000.0
        self._result_ttl = result_ttl_ms / 1000.0
        self._max_waiters = max_waiters
        self._pending: dict[str, CoalescedRequest[T]] = {}
        self._recent_results: dict[str, tuple[float, T]] = {}
        self._stats = CoalescingStats()
        self._lock = asyncio.Lock()

    async def execute(
        self,
        key: str,
        func: Callable[[], Coroutine[Any, Any, T]],
    ) -> T:
        """Execute a request, coalescing with identical pending requests."""
        self._stats.total_requests += 1

        async with self._lock:
            if key in self._recent_results:
                timestamp, result = self._recent_results[key]
                if time.time() - timestamp < self._result_ttl:
                    self._stats.cache_hits += 1
                    log.debug(
                        "request cache hit",
                        extra={"coalescer": self.name, "key": key[:32]},
                    )
                    return result
                else:
                    del self._recent_results[key]

            if key in self._pending:
                pending = self._pending[key]
                if pending.waiter_count < self._max_waiters:
                    pending.waiter_count += 1
                    self._stats.coalesced_requests += 1
                    log.debug(
                        "request coalesced",
                        extra={
                            "coalescer": self.name,
                            "key": key[:32],
                            "waiters": pending.waiter_count,
                        },
                    )
                    future = pending.future
                else:
                    log.warning(
                        "max waiters reached, executing separately",
                        extra={"coalescer": self.name, "key": key[:32]},
                    )
                    future = None
            else:
                future = None

        if future is not None:
            try:
                return await future
            except Exception:
                raise

        loop = asyncio.get_event_loop()
        new_future: asyncio.Future[T] = loop.create_future()
        request = CoalescedRequest(
            key=key,
            created_at=time.time(),
            future=new_future,
        )

        async with self._lock:
            if key in self._pending:
                pending = self._pending[key]
                pending.waiter_count += 1
                self._stats.coalesced_requests += 1
                return await pending.future

            self._pending[key] = request

        start_time = time.time()
        try:
            await asyncio.sleep(self._max_coalesce_time)

            self._stats.executed_requests += 1
            result = await func()

            request.result = result
            request.completed_at = time.time()

            execution_time = request.completed_at - start_time
            saved_time = execution_time * (request.waiter_count - 1)
            self._stats.total_saved_time_ms += saved_time * 1000

            async with self._lock:
                self._recent_results[key] = (time.time(), result)
                self._cleanup_recent_results()

            new_future.set_result(result)

            log.info(
                "request executed",
                extra={
                    "coalescer": self.name,
                    "key": key[:32],
                    "waiters": request.waiter_count,
                    "duration_ms": round(execution_time * 1000, 2),
                    "saved_ms": round(saved_time * 1000, 2),
                },
            )

            return result

        except Exception as e:
            request.error = e
            self._stats.errors += 1
            new_future.set_exception(e)
            raise

        finally:
            async with self._lock:
                self._pending.pop(key, None)

    def _cleanup_recent_results(self) -> None:
        """Remove expired results from cache."""
        now = time.time()
        expired = [
            k
            for k, (ts, _) in self._recent_results.items()
            if now - ts > self._result_ttl
        ]
        for k in expired:
            del self._recent_results[k]

    def get_stats(self) -> CoalescingStats:
        """Get coalescing statistics."""
        return self._stats

    def reset_stats(self) -> None:
        """Reset statistics."""
        self._stats = CoalescingStats()


def compute_request_key(*args: Any, **kwargs: Any) -> str:
    """Compute a hash key for request arguments."""
    key_data = json.dumps(
        {"args": args, "kwargs": kwargs},
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(key_data.encode()).hexdigest()


def coalesce(
    coalescer: RequestCoalescer[T] | None = None,
    key_func: Callable[..., str] | None = None,
    name: str | None = None,
) -> Callable[
    [Callable[..., Coroutine[Any, Any, T]]], Callable[..., Coroutine[Any, Any, T]]
]:
    """Decorator to add request coalescing to an async function."""

    def decorator(
        func: Callable[..., Coroutine[Any, Any, T]],
    ) -> Callable[..., Coroutine[Any, Any, T]]:
        nonlocal coalescer
        if coalescer is None:
            coalescer = RequestCoalescer(name=name or func.__name__)

        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            if key_func:
                key = key_func(*args, **kwargs)
            else:
                key = compute_request_key(*args, **kwargs)

            return await coalescer.execute(key, lambda: func(*args, **kwargs))

        wrapper._coalescer = coalescer  # type: ignore
        return wrapper

    return decorator


@dataclass
class BatchRequest(Generic[T]):
    """A request in a batch."""

    key: str
    args: tuple[Any, ...]
    kwargs: dict[str, Any]
    future: asyncio.Future[T]
    submitted_at: float = field(default_factory=time.time)


class BatchCoalescer(Generic[T]):
    """Batches multiple requests together for efficient processing."""

    def __init__(
        self,
        batch_func: Callable[[list[tuple[Any, ...]]], Coroutine[Any, Any, list[T]]],
        name: str = "batch",
        max_batch_size: int = 100,
        max_wait_ms: float = 10.0,
    ):
        self.name = name
        self._batch_func = batch_func
        self._max_batch_size = max_batch_size
        self._max_wait = max_wait_ms / 1000.0
        self._pending: list[BatchRequest[T]] = []
        self._lock = asyncio.Lock()
        self._flush_task: asyncio.Task[None] | None = None
        self._stats = CoalescingStats()

    async def submit(self, *args: Any, **kwargs: Any) -> T:
        """Submit a request to the batch."""
        self._stats.total_requests += 1

        loop = asyncio.get_event_loop()
        future: asyncio.Future[T] = loop.create_future()

        request = BatchRequest(
            key=compute_request_key(*args, **kwargs),
            args=args,
            kwargs=kwargs,
            future=future,
        )

        async with self._lock:
            self._pending.append(request)

            if len(self._pending) >= self._max_batch_size:
                await self._flush()
            elif self._flush_task is None:
                self._flush_task = asyncio.create_task(self._delayed_flush())

        return await future

    async def _delayed_flush(self) -> None:
        """Flush after max wait time."""
        await asyncio.sleep(self._max_wait)
        async with self._lock:
            await self._flush()
            self._flush_task = None

    async def _flush(self) -> None:
        """Execute the batch."""
        if not self._pending:
            return

        batch = self._pending
        self._pending = []

        self._stats.executed_requests += 1
        self._stats.coalesced_requests += len(batch) - 1

        try:
            args_list = [r.args for r in batch]
            results = await self._batch_func(args_list)

            for request, result in zip(batch, results, strict=False):
                if not request.future.done():
                    request.future.set_result(result)

            log.info(
                "batch executed",
                extra={
                    "coalescer": self.name,
                    "batch_size": len(batch),
                },
            )

        except Exception as e:
            self._stats.errors += 1
            for request in batch:
                if not request.future.done():
                    request.future.set_exception(e)
            raise

    def get_stats(self) -> CoalescingStats:
        """Get batch statistics."""
        return self._stats


_coalescers: dict[str, RequestCoalescer[Any]] = {}


def get_coalescer(
    name: str,
    **kwargs: Any,
) -> RequestCoalescer[Any]:
    """Get or create a named coalescer."""
    if name not in _coalescers:
        _coalescers[name] = RequestCoalescer(name=name, **kwargs)
    return _coalescers[name]


def get_all_stats() -> dict[str, dict[str, Any]]:
    """Get stats for all coalescers."""
    return {name: c.get_stats().to_dict() for name, c in _coalescers.items()}
