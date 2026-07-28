"""Tests for advanced rate limiting algorithms."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.ratelimit_advanced import (
    AdaptiveRateLimiter,
    LeakyBucketLimiter,
    MultiTierRateLimiter,
    RateLimitAlgorithm,
    RateLimitResult,
    RateLimitTier,
    SlidingWindowCounterLimiter,
    SlidingWindowLogLimiter,
    TokenBucketLimiter,
    create_rate_limiter,
)


class MockRedisClient:
    """Mock Redis client for testing."""

    def __init__(self):
        self._data: dict[str, str] = {}
        self._zsets: dict[str, list[tuple[float, str]]] = {}
        self._hashes: dict[str, dict[str, str]] = {}
        self._ttls: dict[str, float] = {}

    async def incr(self, key: str) -> int:
        val = int(self._data.get(key, "0")) + 1
        self._data[key] = str(val)
        return val

    async def get(self, key: str) -> str | None:
        return self._data.get(key)

    async def set(self, key: str, value: str) -> None:
        self._data[key] = value

    async def expire(self, key: str, seconds: int) -> None:
        self._ttls[key] = time.time() + seconds

    async def delete(self, *keys: str) -> None:
        for key in keys:
            self._data.pop(key, None)
            self._zsets.pop(key, None)
            self._hashes.pop(key, None)

    async def zadd(self, key: str, score: float, member: str) -> None:
        if key not in self._zsets:
            self._zsets[key] = []
        self._zsets[key].append((score, member))

    async def zremrangebyscore(
        self, key: str, min_score: str, max_score: float
    ) -> None:
        if key in self._zsets:
            self._zsets[key] = [
                (s, m) for s, m in self._zsets[key] if s > max_score
            ]

    async def zcard(self, key: str) -> int:
        return len(self._zsets.get(key, []))

    async def zrange(
        self, key: str, start: int, end: int, withscores: bool = False
    ) -> list:
        zset = self._zsets.get(key, [])
        if withscores:
            return zset[start : end + 1] if end >= 0 else zset[start:]
        return (
            [m for _, m in zset[start : end + 1]]
            if end >= 0
            else [m for _, m in zset[start:]]
        )

    async def hmget(self, key: str, *fields: str) -> list:
        h = self._hashes.get(key, {})
        return [h.get(f) for f in fields]

    async def hmset(self, key: str, **mapping: str) -> None:
        if key not in self._hashes:
            self._hashes[key] = {}
        self._hashes[key].update(mapping)

    async def info(self, section: str) -> dict:
        return {"instantaneous_ops_per_sec": 1000}

    def register_script(self, script: str) -> MagicMock:
        mock_script = MagicMock()
        return mock_script


@pytest.fixture
def mock_redis():
    return MockRedisClient()


@pytest.fixture
def fixed_clock():
    current_time = [1000.0]

    def clock():
        return current_time[0]

    def advance(seconds: float):
        current_time[0] += seconds

    clock.advance = advance
    return clock


class TestRateLimitResult:
    def test_headers_generation(self):
        result = RateLimitResult(
            allowed=True,
            limit=100,
            remaining=50,
            reset_at=1700000000.0,
        )

        headers = result.headers()

        assert headers["X-RateLimit-Limit"] == "100"
        assert headers["X-RateLimit-Remaining"] == "50"
        assert headers["X-RateLimit-Reset"] == "1700000000"
        assert "Retry-After" not in headers

    def test_headers_with_retry_after(self):
        result = RateLimitResult(
            allowed=False,
            limit=100,
            remaining=0,
            reset_at=1700000000.0,
            retry_after=30.0,
        )

        headers = result.headers()

        assert headers["Retry-After"] == "30"

    def test_remaining_never_negative(self):
        result = RateLimitResult(
            allowed=False,
            limit=100,
            remaining=-5,
            reset_at=1700000000.0,
        )

        headers = result.headers()

        assert headers["X-RateLimit-Remaining"] == "0"


class TestSlidingWindowLogLimiter:
    @pytest.mark.asyncio
    async def test_allows_under_limit(self, mock_redis, fixed_clock):
        limiter = SlidingWindowLogLimiter(mock_redis, fixed_clock)

        mock_script = AsyncMock(return_value=[1, 9, 1060.0])
        limiter._script = mock_script

        result = await limiter.check("user:123", limit=10, window=60)

        assert result.allowed is True
        assert result.remaining == 9

    @pytest.mark.asyncio
    async def test_blocks_over_limit(self, mock_redis, fixed_clock):
        limiter = SlidingWindowLogLimiter(mock_redis, fixed_clock)

        mock_script = AsyncMock(return_value=[0, 0, 1060.0, 30.0])
        limiter._script = mock_script

        result = await limiter.check("user:123", limit=10, window=60)

        assert result.allowed is False
        assert result.remaining == 0
        assert result.retry_after == 30.0

    @pytest.mark.asyncio
    async def test_reset_clears_key(self, mock_redis):
        limiter = SlidingWindowLogLimiter(mock_redis)
        mock_redis._zsets["rl:swl:user:123"] = [(1000.0, "req1")]

        await limiter.reset("user:123")

        assert "rl:swl:user:123" not in mock_redis._zsets


class TestSlidingWindowCounterLimiter:
    @pytest.mark.asyncio
    async def test_weighted_count_calculation(self, mock_redis, fixed_clock):
        limiter = SlidingWindowCounterLimiter(mock_redis, fixed_clock)

        mock_script = AsyncMock(return_value=[1, 85, 1020.0])
        limiter._script = mock_script

        result = await limiter.check("user:123", limit=100, window=60)

        assert result.allowed is True


class TestTokenBucketLimiter:
    @pytest.mark.asyncio
    async def test_allows_burst(self, mock_redis, fixed_clock):
        limiter = TokenBucketLimiter(mock_redis, fixed_clock)

        mock_script = AsyncMock(return_value=[1, 9, 1010.0])
        limiter._script = mock_script

        result = await limiter.check("user:123", capacity=10, window=60, requested=1)

        assert result.allowed is True
        assert result.remaining == 9

    @pytest.mark.asyncio
    async def test_blocks_when_empty(self, mock_redis, fixed_clock):
        limiter = TokenBucketLimiter(mock_redis, fixed_clock)

        mock_script = AsyncMock(return_value=[0, 0, 1060.0, 6.0])
        limiter._script = mock_script

        result = await limiter.check("user:123", capacity=10, window=60, requested=1)

        assert result.allowed is False
        assert result.retry_after == 6.0


class TestLeakyBucketLimiter:
    @pytest.mark.asyncio
    async def test_smooth_rate(self, mock_redis, fixed_clock):
        limiter = LeakyBucketLimiter(mock_redis, fixed_clock)

        mock_script = AsyncMock(return_value=[1, 9, 1006.0])
        limiter._script = mock_script

        result = await limiter.check("user:123", capacity=10, window=60)

        assert result.allowed is True


class TestAdaptiveRateLimiter:
    @pytest.mark.asyncio
    async def test_reduces_limit_under_load(self, mock_redis, fixed_clock):
        base_limiter = SlidingWindowCounterLimiter(mock_redis, fixed_clock)
        base_limiter._script = AsyncMock(return_value=[1, 40, 1060.0])

        mock_redis.info = AsyncMock(
            return_value={"instantaneous_ops_per_sec": 90000}
        )

        adaptive = AdaptiveRateLimiter(
            mock_redis,
            base_limiter,
            fixed_clock,
            load_threshold=0.8,
            reduction_factor=0.5,
        )

        result = await adaptive.check("user:123", limit=100, window=60)

        assert result.allowed is True

    @pytest.mark.asyncio
    async def test_normal_limit_under_low_load(self, mock_redis, fixed_clock):
        base_limiter = SlidingWindowCounterLimiter(mock_redis, fixed_clock)
        base_limiter._script = AsyncMock(return_value=[1, 90, 1060.0])

        mock_redis.info = AsyncMock(return_value={"instantaneous_ops_per_sec": 1000})

        adaptive = AdaptiveRateLimiter(
            mock_redis,
            base_limiter,
            fixed_clock,
            load_threshold=0.8,
            reduction_factor=0.5,
        )

        result = await adaptive.check("user:123", limit=100, window=60)

        assert result.allowed is True


class TestMultiTierRateLimiter:
    @pytest.mark.asyncio
    async def test_tier_limits(self, mock_redis, fixed_clock):
        limiter = MultiTierRateLimiter(
            mock_redis,
            fixed_clock,
            RateLimitAlgorithm.SLIDING_WINDOW_COUNTER,
        )

        assert limiter.tiers["free"].requests_per_window == 100
        assert limiter.tiers["pro"].requests_per_window == 1000
        assert limiter.tiers["enterprise"].requests_per_window == 10000

    def test_tier_dataclass(self):
        tier = RateLimitTier(
            name="custom",
            requests_per_window=500,
            window_seconds=3600,
            burst_allowance=50,
        )

        assert tier.name == "custom"
        assert tier.burst_allowance == 50


class TestFactoryFunction:
    def test_create_sliding_window_log(self, mock_redis):
        limiter = create_rate_limiter(
            mock_redis,
            algorithm=RateLimitAlgorithm.SLIDING_WINDOW_LOG,
        )

        assert isinstance(limiter, SlidingWindowLogLimiter)

    def test_create_token_bucket(self, mock_redis):
        limiter = create_rate_limiter(
            mock_redis,
            algorithm=RateLimitAlgorithm.TOKEN_BUCKET,
        )

        assert isinstance(limiter, TokenBucketLimiter)

    def test_create_adaptive(self, mock_redis):
        limiter = create_rate_limiter(
            mock_redis,
            algorithm=RateLimitAlgorithm.SLIDING_WINDOW_COUNTER,
            adaptive=True,
            load_threshold=0.7,
        )

        assert isinstance(limiter, AdaptiveRateLimiter)
