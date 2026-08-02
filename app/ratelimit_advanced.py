"""Advanced distributed rate limiting with multiple algorithms.

Provides:
- Sliding window log (precise, higher memory)
- Sliding window counter (efficient approximation)
- Token bucket (burst-friendly)
- Leaky bucket (smooth rate)
- Adaptive rate limiting (adjusts based on system load)
- Multi-tier limits (per-user, per-tenant, global)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Protocol

log = logging.getLogger("omnipotent.ratelimit.advanced")


class RateLimitAlgorithm(Enum):
    FIXED_WINDOW = "fixed_window"
    SLIDING_WINDOW_LOG = "sliding_window_log"
    SLIDING_WINDOW_COUNTER = "sliding_window_counter"
    TOKEN_BUCKET = "token_bucket"
    LEAKY_BUCKET = "leaky_bucket"


@dataclass
class RateLimitResult:
    """Result of a rate limit check."""

    allowed: bool
    limit: int
    remaining: int
    reset_at: float
    retry_after: float | None = None

    def headers(self) -> dict[str, str]:
        headers = {
            "X-RateLimit-Limit": str(self.limit),
            "X-RateLimit-Remaining": str(max(0, self.remaining)),
            "X-RateLimit-Reset": str(int(self.reset_at)),
        }
        if self.retry_after is not None:
            headers["Retry-After"] = str(int(self.retry_after))
        return headers


@dataclass
class RateLimitTier:
    """Configuration for a rate limit tier."""

    name: str
    requests_per_window: int
    window_seconds: int
    burst_allowance: int = 0


class RateLimiter(Protocol):
    async def check(self, key: str, limit: int, window: int) -> RateLimitResult: ...
    async def reset(self, key: str) -> None: ...


SLIDING_WINDOW_LOG_SCRIPT = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local cutoff = now - window

redis.call('ZREMRANGEBYSCORE', key, '-inf', cutoff)
local count = redis.call('ZCARD', key)

if count < limit then
    redis.call('ZADD', key, now, now .. '-' .. math.random())
    redis.call('EXPIRE', key, window)
    return {1, limit - count - 1, now + window}
else
    local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
    local retry_after = oldest[2] and (oldest[2] + window - now) or window
    return {0, 0, now + window, retry_after}
end
"""

SLIDING_WINDOW_COUNTER_SCRIPT = """
local current_key = KEYS[1]
local previous_key = KEYS[2]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])

local current_window = math.floor(now / window)
local current_count = tonumber(redis.call('GET', current_key) or '0')
local previous_count = tonumber(redis.call('GET', previous_key) or '0')

local elapsed_in_current = now % window
local weight = (window - elapsed_in_current) / window
local weighted_count = current_count + (previous_count * weight)

if weighted_count < limit then
    redis.call('INCR', current_key)
    redis.call('EXPIRE', current_key, window * 2)
    return {1, math.floor(limit - weighted_count - 1), (current_window + 1) * window}
else
    return {0, 0, (current_window + 1) * window, window - elapsed_in_current}
end
"""

TOKEN_BUCKET_SCRIPT = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local capacity = tonumber(ARGV[2])
local refill_rate = tonumber(ARGV[3])
local requested = tonumber(ARGV[4])

local bucket = redis.call('HMGET', key, 'tokens', 'last_refill')
local tokens = tonumber(bucket[1]) or capacity
local last_refill = tonumber(bucket[2]) or now

local elapsed = now - last_refill
local refill = elapsed * refill_rate
tokens = math.min(capacity, tokens + refill)

if tokens >= requested then
    tokens = tokens - requested
    redis.call('HMSET', key, 'tokens', tokens, 'last_refill', now)
    redis.call('EXPIRE', key, math.ceil(capacity / refill_rate) * 2)
    return {1, math.floor(tokens), now + ((capacity - tokens) / refill_rate)}
else
    local wait_time = (requested - tokens) / refill_rate
    return {0, math.floor(tokens), now + wait_time, wait_time}
end
"""

LEAKY_BUCKET_SCRIPT = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local capacity = tonumber(ARGV[2])
local leak_rate = tonumber(ARGV[3])

local bucket = redis.call('HMGET', key, 'level', 'last_leak')
local level = tonumber(bucket[1]) or 0
local last_leak = tonumber(bucket[2]) or now

local elapsed = now - last_leak
local leaked = elapsed * leak_rate
level = math.max(0, level - leaked)

if level < capacity then
    level = level + 1
    redis.call('HMSET', key, 'level', level, 'last_leak', now)
    redis.call('EXPIRE', key, math.ceil(capacity / leak_rate) * 2)
    return {1, math.floor(capacity - level), now + (level / leak_rate)}
else
    local wait_time = 1 / leak_rate
    return {0, 0, now + wait_time, wait_time}
end
"""


class SlidingWindowLogLimiter:
    """Precise sliding window using sorted set of timestamps."""

    def __init__(self, client: Any, clock: Callable[[], float] = time.time):
        self._redis = client
        self._clock = clock
        self._script: Any = None

    async def _get_script(self) -> Any:
        if self._script is None:
            self._script = self._redis.register_script(SLIDING_WINDOW_LOG_SCRIPT)
        return self._script

    async def check(self, key: str, limit: int, window: int) -> RateLimitResult:
        now = self._clock()
        rkey = f"rl:swl:{key}"

        try:
            script = await self._get_script()
            result = await script(keys=[rkey], args=[now, window, limit])
            allowed, remaining, reset_at = result[0], result[1], result[2]
            retry_after = result[3] if len(result) > 3 else None

            return RateLimitResult(
                allowed=bool(allowed),
                limit=limit,
                remaining=int(remaining),
                reset_at=float(reset_at),
                retry_after=float(retry_after) if retry_after else None,
            )
        except Exception:
            log.warning("sliding window log limiter unavailable", exc_info=True)
            return RateLimitResult(
                allowed=True, limit=limit, remaining=limit, reset_at=now + window
            )

    async def reset(self, key: str) -> None:
        await self._redis.delete(f"rl:swl:{key}")


class SlidingWindowCounterLimiter:
    """Efficient sliding window approximation using two counters."""

    def __init__(self, client: Any, clock: Callable[[], float] = time.time):
        self._redis = client
        self._clock = clock
        self._script: Any = None

    async def _get_script(self) -> Any:
        if self._script is None:
            self._script = self._redis.register_script(SLIDING_WINDOW_COUNTER_SCRIPT)
        return self._script

    async def check(self, key: str, limit: int, window: int) -> RateLimitResult:
        now = self._clock()
        current_window = int(now // window)
        current_key = f"rl:swc:{key}:{current_window}"
        previous_key = f"rl:swc:{key}:{current_window - 1}"

        try:
            script = await self._get_script()
            result = await script(
                keys=[current_key, previous_key], args=[now, window, limit]
            )
            allowed, remaining, reset_at = result[0], result[1], result[2]
            retry_after = result[3] if len(result) > 3 else None

            return RateLimitResult(
                allowed=bool(allowed),
                limit=limit,
                remaining=int(remaining),
                reset_at=float(reset_at),
                retry_after=float(retry_after) if retry_after else None,
            )
        except Exception:
            log.warning("sliding window counter limiter unavailable", exc_info=True)
            return RateLimitResult(
                allowed=True, limit=limit, remaining=limit, reset_at=now + window
            )

    async def reset(self, key: str) -> None:
        now = self._clock()
        current_window = int(now // 60)
        await self._redis.delete(
            f"rl:swc:{key}:{current_window}", f"rl:swc:{key}:{current_window - 1}"
        )


class TokenBucketLimiter:
    """Token bucket for burst-friendly rate limiting."""

    def __init__(
        self,
        client: Any,
        clock: Callable[[], float] = time.time,
        default_refill_rate: float = 1.0,
    ):
        self._redis = client
        self._clock = clock
        self._refill_rate = default_refill_rate
        self._script: Any = None

    async def _get_script(self) -> Any:
        if self._script is None:
            self._script = self._redis.register_script(TOKEN_BUCKET_SCRIPT)
        return self._script

    async def check(
        self,
        key: str,
        capacity: int,
        window: int,
        requested: int = 1,
    ) -> RateLimitResult:
        now = self._clock()
        rkey = f"rl:tb:{key}"
        refill_rate = capacity / window

        try:
            script = await self._get_script()
            result = await script(
                keys=[rkey], args=[now, capacity, refill_rate, requested]
            )
            allowed, remaining, reset_at = result[0], result[1], result[2]
            retry_after = result[3] if len(result) > 3 else None

            return RateLimitResult(
                allowed=bool(allowed),
                limit=capacity,
                remaining=int(remaining),
                reset_at=float(reset_at),
                retry_after=float(retry_after) if retry_after else None,
            )
        except Exception:
            log.warning("token bucket limiter unavailable", exc_info=True)
            return RateLimitResult(
                allowed=True, limit=capacity, remaining=capacity, reset_at=now + window
            )

    async def reset(self, key: str) -> None:
        await self._redis.delete(f"rl:tb:{key}")


class LeakyBucketLimiter:
    """Leaky bucket for smooth, consistent rate limiting."""

    def __init__(self, client: Any, clock: Callable[[], float] = time.time):
        self._redis = client
        self._clock = clock
        self._script: Any = None

    async def _get_script(self) -> Any:
        if self._script is None:
            self._script = self._redis.register_script(LEAKY_BUCKET_SCRIPT)
        return self._script

    async def check(self, key: str, capacity: int, window: int) -> RateLimitResult:
        now = self._clock()
        rkey = f"rl:lb:{key}"
        leak_rate = capacity / window

        try:
            script = await self._get_script()
            result = await script(keys=[rkey], args=[now, capacity, leak_rate])
            allowed, remaining, reset_at = result[0], result[1], result[2]
            retry_after = result[3] if len(result) > 3 else None

            return RateLimitResult(
                allowed=bool(allowed),
                limit=capacity,
                remaining=int(remaining),
                reset_at=float(reset_at),
                retry_after=float(retry_after) if retry_after else None,
            )
        except Exception:
            log.warning("leaky bucket limiter unavailable", exc_info=True)
            return RateLimitResult(
                allowed=True, limit=capacity, remaining=capacity, reset_at=now + window
            )

    async def reset(self, key: str) -> None:
        await self._redis.delete(f"rl:lb:{key}")


class AdaptiveRateLimiter:
    """Adaptive rate limiting that adjusts based on system load."""

    def __init__(
        self,
        client: Any,
        base_limiter: RateLimiter,
        clock: Callable[[], float] = time.time,
        load_threshold: float = 0.8,
        reduction_factor: float = 0.5,
    ):
        self._redis = client
        self._base = base_limiter
        self._clock = clock
        self._load_threshold = load_threshold
        self._reduction_factor = reduction_factor

    async def _get_system_load(self) -> float:
        try:
            info = await self._redis.info("stats")
            ops_per_sec = info.get("instantaneous_ops_per_sec", 0)
            max_ops = 100000
            return min(1.0, ops_per_sec / max_ops)
        except Exception:
            return 0.0

    async def check(self, key: str, limit: int, window: int) -> RateLimitResult:
        load = await self._get_system_load()

        if load > self._load_threshold:
            adjusted_limit = int(limit * self._reduction_factor)
            log.info(
                "adaptive rate limit reduced: %d -> %d (load: %.2f)",
                limit,
                adjusted_limit,
                load,
            )
        else:
            adjusted_limit = limit

        return await self._base.check(key, adjusted_limit, window)

    async def reset(self, key: str) -> None:
        await self._base.reset(key)


class MultiTierRateLimiter:
    """Multi-tier rate limiting: per-user, per-tenant, global."""

    _limiter: (
        SlidingWindowLogLimiter
        | SlidingWindowCounterLimiter
        | TokenBucketLimiter
        | LeakyBucketLimiter
    )

    def __init__(
        self,
        client: Any,
        clock: Callable[[], float] = time.time,
        algorithm: RateLimitAlgorithm = RateLimitAlgorithm.SLIDING_WINDOW_COUNTER,
    ):
        self._redis = client
        self._clock = clock

        if algorithm == RateLimitAlgorithm.SLIDING_WINDOW_LOG:
            self._limiter = SlidingWindowLogLimiter(client, clock)
        elif algorithm == RateLimitAlgorithm.TOKEN_BUCKET:
            self._limiter = TokenBucketLimiter(client, clock)
        elif algorithm == RateLimitAlgorithm.LEAKY_BUCKET:
            self._limiter = LeakyBucketLimiter(client, clock)
        else:
            self._limiter = SlidingWindowCounterLimiter(client, clock)

        self.tiers: dict[str, RateLimitTier] = {
            "free": RateLimitTier("free", 100, 3600, 10),
            "starter": RateLimitTier("starter", 500, 3600, 50),
            "pro": RateLimitTier("pro", 1000, 3600, 100),
            "enterprise": RateLimitTier("enterprise", 10000, 3600, 1000),
        }

        self.global_limits = RateLimitTier("global", 100000, 60, 10000)

    async def check(
        self,
        user_id: str,
        tenant_id: str,
        tier: str = "free",
        endpoint: str = "default",
    ) -> RateLimitResult:
        user_tier = self.tiers.get(tier, self.tiers["free"])

        user_key = f"user:{user_id}:{endpoint}"
        user_result = await self._limiter.check(
            user_key, user_tier.requests_per_window, user_tier.window_seconds
        )
        if not user_result.allowed:
            return user_result

        tenant_key = f"tenant:{tenant_id}:{endpoint}"
        tenant_limit = user_tier.requests_per_window * 10
        tenant_result = await self._limiter.check(
            tenant_key, tenant_limit, user_tier.window_seconds
        )
        if not tenant_result.allowed:
            return tenant_result

        global_key = f"global:{endpoint}"
        global_result = await self._limiter.check(
            global_key,
            self.global_limits.requests_per_window,
            self.global_limits.window_seconds,
        )

        return global_result

    async def reset_user(self, user_id: str, endpoint: str = "default") -> None:
        await self._limiter.reset(f"user:{user_id}:{endpoint}")

    async def reset_tenant(self, tenant_id: str, endpoint: str = "default") -> None:
        await self._limiter.reset(f"tenant:{tenant_id}:{endpoint}")


def create_rate_limiter(
    client: Any,
    algorithm: RateLimitAlgorithm = RateLimitAlgorithm.SLIDING_WINDOW_COUNTER,
    adaptive: bool = False,
    load_threshold: float = 0.8,
) -> RateLimiter:
    """Factory function to create a rate limiter."""
    clock = time.time

    limiter: (
        SlidingWindowLogLimiter
        | SlidingWindowCounterLimiter
        | TokenBucketLimiter
        | LeakyBucketLimiter
    )
    if algorithm == RateLimitAlgorithm.SLIDING_WINDOW_LOG:
        limiter = SlidingWindowLogLimiter(client, clock)
    elif algorithm == RateLimitAlgorithm.TOKEN_BUCKET:
        limiter = TokenBucketLimiter(client, clock)
    elif algorithm == RateLimitAlgorithm.LEAKY_BUCKET:
        limiter = LeakyBucketLimiter(client, clock)
    else:
        limiter = SlidingWindowCounterLimiter(client, clock)

    if adaptive:
        return AdaptiveRateLimiter(
            client, limiter, clock, load_threshold=load_threshold
        )

    return limiter
