"""Response caching layer with pluggable backends.

A caching layer for API responses and expensive operations, with two backends:

  - MemoryCache: in-memory with TTL + LRU eviction (default; single-instance only).
  - RedisCache: shared across replicas (production choice). Selected when
    COPILOT_REDIS_URL is set.

The system works without caching (COPILOT_CACHE_BACKEND=none). The backends fail
open: a Redis outage never blocks a request, it just misses the cache.
"""

from __future__ import annotations

import asyncio
import functools
import hashlib
import json
import logging
import time
from abc import ABC, abstractmethod
from collections import OrderedDict
from typing import Any, Callable, TypeVar, ParamSpec

log = logging.getLogger("omnipotent.cache")

P = ParamSpec("P")
T = TypeVar("T")


def _make_key(prefix: str, args: tuple, kwargs: dict) -> str:
    """Generate a cache key from a prefix and function arguments.

    Serializes arguments to a stable JSON string and hashes it. This handles
    most common argument types (primitives, dicts, lists). Non-serializable
    arguments are repr'd as a fallback.
    """
    def _serialize(obj: Any) -> Any:
        if isinstance(obj, (str, int, float, bool, type(None))):
            return obj
        if isinstance(obj, (list, tuple)):
            return [_serialize(x) for x in obj]
        if isinstance(obj, dict):
            return {str(k): _serialize(v) for k, v in sorted(obj.items())}
        # Fallback for non-JSON types (Pydantic models, custom objects)
        return repr(obj)

    try:
        payload = json.dumps({"args": _serialize(args), "kwargs": _serialize(kwargs)}, sort_keys=True)
    except (TypeError, ValueError):
        # Ultimate fallback: hash the repr
        payload = repr((args, kwargs))

    h = hashlib.sha256(payload.encode()).hexdigest()[:16]
    return f"{prefix}:{h}"


class CacheBackend(ABC):
    """Abstract cache backend. Implementations must be async-compatible."""

    @abstractmethod
    async def get(self, key: str) -> tuple[bool, Any]:
        """Retrieve a cached value. Returns (hit, value)."""
        ...

    @abstractmethod
    async def set(self, key: str, value: Any, ttl: int) -> None:
        """Store a value with TTL in seconds."""
        ...

    @abstractmethod
    async def delete(self, key: str) -> bool:
        """Delete a key. Returns True if it existed."""
        ...

    async def get_or_set(self, key: str, factory: Callable[[], Any], ttl: int) -> Any:
        """Get cached value or compute + store it. `factory` may be sync or async."""
        hit, value = await self.get(key)
        if hit:
            return value
        result = factory()
        if asyncio.iscoroutine(result):
            result = await result
        await self.set(key, result, ttl)
        return result

    @abstractmethod
    def stats(self) -> dict[str, Any]:
        """Return cache statistics."""
        ...


class NullCache(CacheBackend):
    """No-op cache (COPILOT_CACHE_BACKEND=none). Always misses."""

    async def get(self, key: str) -> tuple[bool, Any]:
        return (False, None)

    async def set(self, key: str, value: Any, ttl: int) -> None:
        pass

    async def delete(self, key: str) -> bool:
        return False

    def stats(self) -> dict[str, Any]:
        return {"backend": "none", "enabled": False}


class MemoryCache(CacheBackend):
    """In-memory LRU cache with TTL. Thread-safe for single-process use.

    Entries are stored as (value, expires_at) in an OrderedDict. The LRU policy
    moves accessed items to the end; eviction removes from the front.
    """

    def __init__(
        self,
        max_size: int = 1000,
        clock: Callable[[], float] = time.time,
    ):
        self._store: OrderedDict[str, tuple[Any, float]] = OrderedDict()
        self._max_size = max_size
        self._clock = clock
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    async def get(self, key: str) -> tuple[bool, Any]:
        entry = self._store.get(key)
        if entry is None:
            self._misses += 1
            return (False, None)
        value, expires_at = entry
        if self._clock() > expires_at:
            # Expired: remove and report miss
            del self._store[key]
            self._misses += 1
            return (False, None)
        # LRU: move to end
        self._store.move_to_end(key)
        self._hits += 1
        return (True, value)

    async def set(self, key: str, value: Any, ttl: int) -> None:
        expires_at = self._clock() + ttl
        # Remove old entry if it exists (so move_to_end works correctly)
        self._store.pop(key, None)
        self._store[key] = (value, expires_at)
        # Evict oldest entries if over capacity
        while len(self._store) > self._max_size:
            self._store.popitem(last=False)
            self._evictions += 1

    async def delete(self, key: str) -> bool:
        try:
            del self._store[key]
            return True
        except KeyError:
            return False

    def clear(self) -> None:
        """Remove all entries (useful for tests and the reset endpoint)."""
        self._store.clear()

    def _gc(self) -> int:
        """Remove expired entries and return the count removed."""
        now = self._clock()
        expired = [k for k, (_, exp) in self._store.items() if now > exp]
        for k in expired:
            del self._store[k]
        return len(expired)

    def stats(self) -> dict[str, Any]:
        # Run a quick GC to get accurate size
        expired = self._gc()
        total = self._hits + self._misses
        return {
            "backend": "memory",
            "enabled": True,
            "size": len(self._store),
            "max_size": self._max_size,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(self._hits / total, 3) if total else 0.0,
            "evictions": self._evictions,
            "expired_cleaned": expired,
        }


class RedisCache(CacheBackend):
    """Redis-backed cache for multi-replica deployments.

    Values are JSON-serialized. Fails open on Redis errors: a cache outage
    becomes a cache miss, never a request failure.
    """

    def __init__(self, client: Any, prefix: str = "cache"):
        self._redis = client
        self._prefix = prefix
        self._hits = 0
        self._misses = 0
        self._errors = 0

    def _key(self, key: str) -> str:
        return f"{self._prefix}:{key}"

    async def get(self, key: str) -> tuple[bool, Any]:
        try:
            raw = await self._redis.get(self._key(key))
            if raw is None:
                self._misses += 1
                return (False, None)
            value = json.loads(raw)
            self._hits += 1
            return (True, value)
        except Exception:  # noqa: BLE001 — fail open
            log.warning("redis cache get failed; treating as miss", exc_info=True)
            self._errors += 1
            self._misses += 1
            return (False, None)

    async def set(self, key: str, value: Any, ttl: int) -> None:
        try:
            raw = json.dumps(value)
            await self._redis.setex(self._key(key), ttl, raw)
        except Exception:  # noqa: BLE001 — fail open
            log.warning("redis cache set failed; continuing", exc_info=True)
            self._errors += 1

    async def delete(self, key: str) -> bool:
        try:
            result = await self._redis.delete(self._key(key))
            return bool(result)
        except Exception:  # noqa: BLE001 — fail open
            log.warning("redis cache delete failed; continuing", exc_info=True)
            self._errors += 1
            return False

    def stats(self) -> dict[str, Any]:
        total = self._hits + self._misses
        return {
            "backend": "redis",
            "enabled": True,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(self._hits / total, 3) if total else 0.0,
            "errors": self._errors,
        }


# --- Singleton cache instance ---

_cache: CacheBackend | None = None


def get_cache() -> CacheBackend:
    """Return the configured cache backend (lazy-initialized singleton).

    Reads COPILOT_CACHE_BACKEND from settings:
      - "none": NullCache (caching disabled)
      - "memory": MemoryCache (default)
      - "redis": RedisCache (if COPILOT_REDIS_URL is also set)
    """
    global _cache
    if _cache is not None:
        return _cache

    from app.config import get_settings
    settings = get_settings()

    backend = settings.copilot_cache_backend.lower()

    if backend == "none":
        log.info("cache: disabled (COPILOT_CACHE_BACKEND=none)")
        _cache = NullCache()
        return _cache

    if backend == "redis" and settings.copilot_redis_url.strip():
        try:
            import redis.asyncio as aioredis
            client = aioredis.from_url(
                settings.copilot_redis_url,
                encoding="utf-8",
                decode_responses=True,
            )
            log.info("cache: redis (%s)", settings.copilot_redis_url.split("@")[-1])
            _cache = RedisCache(client)
            return _cache
        except Exception:  # noqa: BLE001 — fall back to memory
            log.warning("redis cache unavailable; falling back to memory", exc_info=True)

    # Default: in-memory
    max_size = settings.copilot_cache_max_size
    log.info("cache: memory (max_size=%d)", max_size)
    _cache = MemoryCache(max_size=max_size)
    return _cache


def reset_cache() -> None:
    """Reset the cache singleton (for tests or after config change)."""
    global _cache
    if isinstance(_cache, MemoryCache):
        _cache.clear()
    _cache = None


# --- Decorators ---


def cached(ttl: int = 300, prefix: str | None = None) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """Decorator for caching sync function results (called from sync context).

    NOTE: For functions called from async contexts, use @cached_async instead.
    This decorator uses asyncio.run() internally, which cannot be called from
    within an already-running event loop.

    Args:
        ttl: Time-to-live in seconds (default 300 = 5 minutes).
        prefix: Cache key prefix (defaults to function name).
    """
    def decorator(fn: Callable[P, T]) -> Callable[P, T]:
        key_prefix = prefix or fn.__name__

        @functools.wraps(fn)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            cache = get_cache()
            key = _make_key(key_prefix, args, kwargs)

            async def _do():
                hit, value = await cache.get(key)
                if hit:
                    return value
                result = fn(*args, **kwargs)
                await cache.set(key, result, ttl)
                return result

            # Try to get the running loop, if any
            try:
                asyncio.get_running_loop()
                # We're inside an async context - this decorator can't work here
                raise RuntimeError(
                    "@cached decorator cannot be used on sync functions called from async "
                    "contexts. Use @cached_async instead, or call the function outside "
                    "the async context."
                )
            except RuntimeError:
                # No running loop - safe to use asyncio.run()
                return asyncio.run(_do())

        return wrapper

    return decorator


def cached_async(ttl: int = 300, prefix: str | None = None) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """Decorator for caching async function results.

    Args:
        ttl: Time-to-live in seconds (default 300 = 5 minutes).
        prefix: Cache key prefix (defaults to function name).
    """
    def decorator(fn: Callable[P, T]) -> Callable[P, T]:
        key_prefix = prefix or fn.__name__

        @functools.wraps(fn)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            cache = get_cache()
            key = _make_key(key_prefix, args, kwargs)
            hit, value = await cache.get(key)
            if hit:
                return value
            result = await fn(*args, **kwargs)  # type: ignore[misc]
            await cache.set(key, result, ttl)
            return result

        return wrapper  # type: ignore[return-value]

    return decorator


# --- TTL constants for different operations ---

# MCP tool result caching: short TTL because state changes frequently
TTL_MCP_SHORT = 30       # logs, metrics, pod status (30 seconds)
TTL_MCP_MEDIUM = 120     # service lists, trace queries (2 minutes)
TTL_MCP_LONG = 300       # incident memory, static configs (5 minutes)

# API endpoint caching
TTL_CONFIG = 300         # /config endpoint (5 minutes)
TTL_STATIC = 600         # truly static data (10 minutes)
