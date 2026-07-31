"""Redis-backed session and rate limiting for production deployments."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from app.config import get_settings

log = logging.getLogger("devcopilot.redis_sessions")

_redis_client = None


def get_redis_client():
    """Get or create Redis client singleton."""
    global _redis_client
    if _redis_client is not None:
        return _redis_client

    settings = get_settings()
    redis_url = settings.copilot_redis_url

    if not redis_url:
        log.info("Redis not configured, using in-memory fallback")
        return None

    try:
        import redis

        _redis_client = redis.from_url(
            redis_url,
            decode_responses=True,
            socket_timeout=5,
            socket_connect_timeout=5,
            retry_on_timeout=True,
        )
        _redis_client.ping()
        log.info("Connected to Redis")
        return _redis_client
    except Exception as e:
        log.warning("Failed to connect to Redis: %s", e)
        return None


@dataclass
class RateLimitResult:
    allowed: bool
    remaining: int
    reset_at: datetime
    retry_after: int | None = None


class RedisRateLimiter:
    """Token bucket rate limiter backed by Redis."""

    def __init__(
        self,
        key_prefix: str = "ratelimit",
        requests_per_minute: int = 60,
        burst_size: int = 10,
    ):
        self.key_prefix = key_prefix
        self.requests_per_minute = requests_per_minute
        self.burst_size = burst_size
        self.refill_rate = requests_per_minute / 60.0

    def _get_key(self, identifier: str) -> str:
        return f"{self.key_prefix}:{identifier}"

    def check(self, identifier: str, cost: int = 1) -> RateLimitResult:
        """Check and consume rate limit tokens."""
        client = get_redis_client()
        if not client:
            return RateLimitResult(
                allowed=True,
                remaining=self.burst_size,
                reset_at=datetime.utcnow() + timedelta(minutes=1),
            )

        key = self._get_key(identifier)
        now = time.time()

        pipe = client.pipeline()
        pipe.hgetall(key)
        result = pipe.execute()[0]

        if not result:
            tokens = self.burst_size
            last_refill = now
        else:
            tokens = float(result.get("tokens", self.burst_size))
            last_refill = float(result.get("last_refill", now))

        elapsed = now - last_refill
        tokens = min(self.burst_size, tokens + elapsed * self.refill_rate)

        if tokens >= cost:
            tokens -= cost
            allowed = True
            retry_after = None
        else:
            allowed = False
            retry_after = int((cost - tokens) / self.refill_rate) + 1

        pipe = client.pipeline()
        pipe.hset(key, mapping={"tokens": tokens, "last_refill": now})
        pipe.expire(key, 120)
        pipe.execute()

        reset_at = datetime.utcnow() + timedelta(
            seconds=int((self.burst_size - tokens) / self.refill_rate)
        )

        return RateLimitResult(
            allowed=allowed,
            remaining=int(tokens),
            reset_at=reset_at,
            retry_after=retry_after,
        )


class RedisSessionStore:
    """Redis-backed session store for distributed deployments."""

    def __init__(self, key_prefix: str = "session", ttl_seconds: int = 3600):
        self.key_prefix = key_prefix
        self.ttl_seconds = ttl_seconds

    def _get_key(self, session_id: str) -> str:
        return f"{self.key_prefix}:{session_id}"

    def get(self, session_id: str) -> dict[str, Any] | None:
        """Get session data."""
        client = get_redis_client()
        if not client:
            return None

        data = client.get(self._get_key(session_id))
        if data:
            return json.loads(data)
        return None

    def set(self, session_id: str, data: dict[str, Any]) -> bool:
        """Set session data."""
        client = get_redis_client()
        if not client:
            return False

        return bool(
            client.setex(
                self._get_key(session_id),
                self.ttl_seconds,
                json.dumps(data),
            )
        )

    def delete(self, session_id: str) -> bool:
        """Delete session."""
        client = get_redis_client()
        if not client:
            return False
        return bool(client.delete(self._get_key(session_id)))

    def touch(self, session_id: str) -> bool:
        """Refresh session TTL."""
        client = get_redis_client()
        if not client:
            return False
        return bool(client.expire(self._get_key(session_id), self.ttl_seconds))


class RedisCache:
    """Simple Redis cache for expensive operations."""

    def __init__(self, key_prefix: str = "cache"):
        self.key_prefix = key_prefix

    def _get_key(self, key: str) -> str:
        return f"{self.key_prefix}:{key}"

    def get(self, key: str) -> Any | None:
        """Get cached value."""
        client = get_redis_client()
        if not client:
            return None

        data = client.get(self._get_key(key))
        if data:
            return json.loads(data)
        return None

    def set(self, key: str, value: Any, ttl_seconds: int = 300) -> bool:
        """Set cached value."""
        client = get_redis_client()
        if not client:
            return False

        return bool(
            client.setex(self._get_key(key), ttl_seconds, json.dumps(value))
        )

    def delete(self, key: str) -> bool:
        """Delete cached value."""
        client = get_redis_client()
        if not client:
            return False
        return bool(client.delete(self._get_key(key)))

    def increment(self, key: str, amount: int = 1, ttl_seconds: int = 3600) -> int:
        """Increment counter."""
        client = get_redis_client()
        if not client:
            return amount

        full_key = self._get_key(key)
        pipe = client.pipeline()
        pipe.incrby(full_key, amount)
        pipe.expire(full_key, ttl_seconds)
        result = pipe.execute()
        return result[0]


_rate_limiter: RedisRateLimiter | None = None
_session_store: RedisSessionStore | None = None
_cache: RedisCache | None = None


def get_rate_limiter() -> RedisRateLimiter:
    global _rate_limiter
    if _rate_limiter is None:
        settings = get_settings()
        _rate_limiter = RedisRateLimiter(
            requests_per_minute=getattr(settings, "copilot_rate_limit_rpm", 60),
            burst_size=getattr(settings, "copilot_rate_limit_burst", 10),
        )
    return _rate_limiter


def get_session_store() -> RedisSessionStore:
    global _session_store
    if _session_store is None:
        _session_store = RedisSessionStore()
    return _session_store


def get_cache() -> RedisCache:
    global _cache
    if _cache is None:
        _cache = RedisCache()
    return _cache
