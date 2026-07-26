"""Cache module tests — backends, key generation, decorators, and stats."""

import asyncio
import json

import pytest

from app.cache import (
    CacheBackend,
    MemoryCache,
    NullCache,
    RedisCache,
    _make_key,
    cached_async,
    get_cache,
    reset_cache,
    TTL_CONFIG,
    TTL_MCP_SHORT,
)


# --- Key generation ---


def test_make_key_basic_types():
    """Key generation handles primitives and collections."""
    k1 = _make_key("prefix", (1, "a"), {"x": 2})
    k2 = _make_key("prefix", (1, "a"), {"x": 2})
    k3 = _make_key("prefix", (1, "b"), {"x": 2})

    assert k1 == k2, "same args should produce same key"
    assert k1 != k3, "different args should produce different key"
    assert k1.startswith("prefix:")


def test_make_key_dict_order_independent():
    """Dict arguments are sorted so key order doesn't matter."""
    k1 = _make_key("p", (), {"a": 1, "b": 2})
    k2 = _make_key("p", (), {"b": 2, "a": 1})
    assert k1 == k2


def test_make_key_non_serializable_falls_back_to_repr():
    """Non-JSON-serializable objects use repr fallback."""

    class Custom:
        pass

    obj = Custom()
    key = _make_key("prefix", (obj,), {})
    assert key.startswith("prefix:")
    assert len(key) == len("prefix:") + 16  # sha256[:16]


# --- NullCache ---


def test_null_cache_always_misses():
    cache = NullCache()

    async def run():
        # get always returns (False, None)
        hit, val = await cache.get("anything")
        assert hit is False
        assert val is None

        # set is a no-op
        await cache.set("key", {"data": 1}, 60)
        hit, val = await cache.get("key")
        assert hit is False

        # delete always returns False
        assert await cache.delete("key") is False

    asyncio.run(run())


def test_null_cache_stats():
    cache = NullCache()
    stats = cache.stats()
    assert stats["backend"] == "none"
    assert stats["enabled"] is False


# --- MemoryCache ---


def test_memory_cache_get_set():
    now = [1000.0]
    cache = MemoryCache(max_size=100, clock=lambda: now[0])

    async def run():
        # Miss before set
        hit, val = await cache.get("key")
        assert hit is False

        # Set and hit
        await cache.set("key", {"value": 42}, ttl=60)
        hit, val = await cache.get("key")
        assert hit is True
        assert val == {"value": 42}

    asyncio.run(run())


def test_memory_cache_ttl_expiry():
    now = [0.0]
    cache = MemoryCache(clock=lambda: now[0])

    async def run():
        await cache.set("key", "value", ttl=30)
        hit, _ = await cache.get("key")
        assert hit is True

        # Advance past TTL
        now[0] = 31
        hit, _ = await cache.get("key")
        assert hit is False  # expired

    asyncio.run(run())


def test_memory_cache_lru_eviction():
    cache = MemoryCache(max_size=2)

    async def run():
        await cache.set("a", 1, ttl=300)
        await cache.set("b", 2, ttl=300)
        await cache.set("c", 3, ttl=300)  # evicts "a" (oldest)

        hit_a, _ = await cache.get("a")
        hit_b, _ = await cache.get("b")
        hit_c, _ = await cache.get("c")

        assert hit_a is False  # evicted
        assert hit_b is True
        assert hit_c is True

    asyncio.run(run())


def test_memory_cache_lru_access_order():
    cache = MemoryCache(max_size=2)

    async def run():
        await cache.set("a", 1, ttl=300)
        await cache.set("b", 2, ttl=300)

        # Access "a" to make it recently used
        await cache.get("a")

        # Add "c" — should evict "b" (least recently used), not "a"
        await cache.set("c", 3, ttl=300)

        hit_a, _ = await cache.get("a")
        hit_b, _ = await cache.get("b")

        assert hit_a is True  # kept (recently accessed)
        assert hit_b is False  # evicted

    asyncio.run(run())


def test_memory_cache_delete():
    cache = MemoryCache()

    async def run():
        await cache.set("key", "value", ttl=300)
        assert await cache.delete("key") is True
        hit, _ = await cache.get("key")
        assert hit is False

        # Delete non-existent key
        assert await cache.delete("nonexistent") is False

    asyncio.run(run())


def test_memory_cache_clear():
    cache = MemoryCache()

    async def run():
        await cache.set("a", 1, ttl=300)
        await cache.set("b", 2, ttl=300)
        cache.clear()

        hit_a, _ = await cache.get("a")
        hit_b, _ = await cache.get("b")
        assert hit_a is False
        assert hit_b is False

    asyncio.run(run())


def test_memory_cache_stats():
    cache = MemoryCache(max_size=2)

    async def run():
        # Generate some activity
        await cache.get("miss1")  # miss
        await cache.set("a", 1, ttl=300)
        await cache.get("a")  # hit
        await cache.get("miss2")  # miss
        await cache.set("b", 2, ttl=300)
        await cache.set("c", 3, ttl=300)  # evicts "a"

        stats = cache.stats()
        assert stats["backend"] == "memory"
        assert stats["enabled"] is True
        assert stats["hits"] == 1
        assert stats["misses"] == 2
        assert stats["evictions"] == 1
        assert stats["size"] == 2
        assert stats["max_size"] == 2
        assert stats["hit_rate"] == pytest.approx(1 / 3, rel=0.01)

    asyncio.run(run())


def test_memory_cache_get_or_set():
    cache = MemoryCache()
    calls = [0]

    async def run():
        def factory():
            calls[0] += 1
            return {"computed": True}

        # First call computes
        result = await cache.get_or_set("key", factory, ttl=300)
        assert result == {"computed": True}
        assert calls[0] == 1

        # Second call hits cache
        result = await cache.get_or_set("key", factory, ttl=300)
        assert result == {"computed": True}
        assert calls[0] == 1  # factory not called again

    asyncio.run(run())


def test_memory_cache_get_or_set_async_factory():
    cache = MemoryCache()
    calls = [0]

    async def run():
        async def async_factory():
            calls[0] += 1
            return {"async": True}

        result = await cache.get_or_set("key", async_factory, ttl=300)
        assert result == {"async": True}
        assert calls[0] == 1

        # Cached
        result = await cache.get_or_set("key", async_factory, ttl=300)
        assert calls[0] == 1

    asyncio.run(run())


# --- RedisCache (with fake client) ---


class _FakeRedis:
    """Minimal async redis stand-in for testing."""

    def __init__(self, fail: bool = False):
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int] = {}
        self._fail = fail

    async def get(self, key: str) -> str | None:
        if self._fail:
            raise RuntimeError("redis down")
        return self.store.get(key)

    async def setex(self, key: str, ttl: int, value: str) -> None:
        if self._fail:
            raise RuntimeError("redis down")
        self.store[key] = value
        self.ttls[key] = ttl

    async def delete(self, key: str) -> int:
        if self._fail:
            raise RuntimeError("redis down")
        if key in self.store:
            del self.store[key]
            return 1
        return 0


def test_redis_cache_get_set():
    fake = _FakeRedis()
    cache = RedisCache(fake, prefix="test")

    async def run():
        # Miss
        hit, val = await cache.get("key")
        assert hit is False

        # Set and hit
        await cache.set("key", {"data": [1, 2, 3]}, ttl=120)
        hit, val = await cache.get("key")
        assert hit is True
        assert val == {"data": [1, 2, 3]}

        # Verify TTL was set
        assert fake.ttls["test:key"] == 120

    asyncio.run(run())


def test_redis_cache_delete():
    fake = _FakeRedis()
    cache = RedisCache(fake, prefix="test")

    async def run():
        await cache.set("key", "value", ttl=60)
        assert await cache.delete("key") is True
        hit, _ = await cache.get("key")
        assert hit is False

        assert await cache.delete("nonexistent") is False

    asyncio.run(run())


def test_redis_cache_fails_open_on_error():
    """Redis errors result in cache misses, not request failures."""
    fake = _FakeRedis(fail=True)
    cache = RedisCache(fake)

    async def run():
        # Get fails open (returns miss)
        hit, val = await cache.get("key")
        assert hit is False

        # Set fails silently
        await cache.set("key", "value", ttl=60)  # no exception

        # Delete fails silently
        result = await cache.delete("key")
        assert result is False

    asyncio.run(run())


def test_redis_cache_stats():
    fake = _FakeRedis()
    cache = RedisCache(fake)

    async def run():
        await cache.get("miss")
        await cache.set("hit", 1, ttl=60)
        await cache.get("hit")

        stats = cache.stats()
        assert stats["backend"] == "redis"
        assert stats["enabled"] is True
        assert stats["hits"] == 1
        assert stats["misses"] == 1
        assert stats["hit_rate"] == 0.5

    asyncio.run(run())


# --- @cached_async decorator ---


def test_cached_async_decorator():
    """The async decorator caches function results."""
    calls = [0]

    # Use a fresh cache for each test
    reset_cache()

    @cached_async(ttl=60, prefix="test_fn")
    async def expensive_call(x: int, y: str = "default") -> dict:
        calls[0] += 1
        return {"x": x, "y": y, "computed": True}

    async def run():
        # First call computes
        result = await expensive_call(1, y="a")
        assert result == {"x": 1, "y": "a", "computed": True}
        assert calls[0] == 1

        # Same args hits cache
        result = await expensive_call(1, y="a")
        assert result == {"x": 1, "y": "a", "computed": True}
        assert calls[0] == 1

        # Different args computes again
        result = await expensive_call(2, y="b")
        assert result == {"x": 2, "y": "b", "computed": True}
        assert calls[0] == 2

    asyncio.run(run())
    reset_cache()


def test_cached_sync_decorator():
    """The sync decorator caches function results when called from sync context."""
    from app.cache import cached

    calls = [0]
    reset_cache()

    @cached(ttl=60, prefix="test_sync_fn")
    def sync_expensive_call(x: int) -> dict:
        calls[0] += 1
        return {"x": x, "computed": True}

    # First call computes
    result = sync_expensive_call(1)
    assert result == {"x": 1, "computed": True}
    assert calls[0] == 1

    # Same args hits cache
    result = sync_expensive_call(1)
    assert result == {"x": 1, "computed": True}
    assert calls[0] == 1

    # Different args computes again
    result = sync_expensive_call(2)
    assert result == {"x": 2, "computed": True}
    assert calls[0] == 2

    reset_cache()


# --- TTL constants ---


def test_ttl_constants_defined():
    """TTL constants are defined with sensible values."""
    assert TTL_MCP_SHORT == 30
    assert TTL_CONFIG == 300


# --- get_cache factory ---


def test_get_cache_returns_memory_by_default(monkeypatch):
    """Without Redis URL, get_cache returns MemoryCache."""
    reset_cache()

    # Mock settings
    class MockSettings:
        copilot_cache_backend = "memory"
        copilot_redis_url = ""
        copilot_cache_max_size = 500

    monkeypatch.setattr("app.config.get_settings", lambda: MockSettings())

    cache = get_cache()
    assert isinstance(cache, MemoryCache)
    assert cache._max_size == 500

    reset_cache()


def test_get_cache_returns_null_when_disabled(monkeypatch):
    """COPILOT_CACHE_BACKEND=none returns NullCache."""
    reset_cache()

    class MockSettings:
        copilot_cache_backend = "none"
        copilot_redis_url = ""
        copilot_cache_max_size = 1000

    monkeypatch.setattr("app.config.get_settings", lambda: MockSettings())

    cache = get_cache()
    assert isinstance(cache, NullCache)

    reset_cache()
