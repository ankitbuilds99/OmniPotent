"""Tests for scaling infrastructure.

Covers: scaling metrics, Redis pools, session store, database routing.
"""

from __future__ import annotations

import time

import pytest

from app.redis_pool import (
    MockRedis,
    RedisPoolManager,
    RedisPoolType,
    reset_redis_pools,
)
from app.session_store import (
    DistributedSessionStore,
    InvestigationState,
    SessionData,
    reset_session_store,
)

# Database routing requires SQLAlchemy (optional dependency)
try:
    from app.database.routing import (
        DatabaseRouter,
        ReplicaHealth,
        reset_db_router,
    )
    HAS_SQLALCHEMY = True
except ImportError:
    HAS_SQLALCHEMY = False
    DatabaseRouter = None
    ReplicaHealth = None
    reset_db_router = None


class TestRedisPool:
    """Test Redis connection pooling."""

    def setup_method(self):
        reset_redis_pools()

    @pytest.mark.asyncio
    async def test_mock_redis_operations(self):
        """Test mock Redis client."""
        redis = MockRedis()

        assert await redis.ping() is True
        assert await redis.get("key") is None
        assert await redis.set("key", "value") is True
        assert await redis.get("key") == "value"
        assert await redis.setex("key2", 60, "value2") is True
        assert await redis.get("key2") == "value2"
        assert await redis.delete("key") == 1
        assert await redis.get("key") is None
        assert await redis.sadd("set", "val1", "val2") == 2
        assert await redis.smembers("set") == {"val1", "val2"}
        assert await redis.srem("set", "val1") == 1
        assert await redis.smembers("set") == {"val2"}

    @pytest.mark.asyncio
    async def test_pool_manager_creates_pools(self):
        """Test pool manager creates pools lazily."""
        manager = RedisPoolManager("redis://localhost:6379")

        pool = await manager.get_pool(RedisPoolType.CACHE)
        assert pool is not None

        pool2 = await manager.get_pool(RedisPoolType.CACHE)
        assert pool is pool2

    @pytest.mark.asyncio
    async def test_pool_configs(self):
        """Test different pool configurations exist."""
        from app.redis_pool import POOL_CONFIGS

        assert RedisPoolType.CACHE in POOL_CONFIGS
        assert RedisPoolType.SESSION in POOL_CONFIGS
        assert RedisPoolType.PUBSUB in POOL_CONFIGS
        assert RedisPoolType.QUEUE in POOL_CONFIGS

        cache_config = POOL_CONFIGS[RedisPoolType.CACHE]
        assert cache_config.retry_on_timeout is False

        session_config = POOL_CONFIGS[RedisPoolType.SESSION]
        assert session_config.retry_on_timeout is True


class TestSessionStore:
    """Test distributed session store."""

    def setup_method(self):
        reset_session_store()
        reset_redis_pools()

    @pytest.mark.asyncio
    async def test_create_session(self):
        """Test session creation."""
        store = DistributedSessionStore()

        session = SessionData(
            session_id="sess-123",
            user_id="user-456",
            org_id="org-789",
        )

        session_id = await store.create_session(session)
        assert session_id == "sess-123"

    @pytest.mark.asyncio
    async def test_get_session_from_cache(self):
        """Test session retrieval from local cache."""
        store = DistributedSessionStore()

        session = SessionData(
            session_id="sess-123",
            user_id="user-456",
            org_id="org-789",
        )
        await store.create_session(session)

        retrieved = await store.get_session("sess-123")
        assert retrieved is not None
        assert retrieved.user_id == "user-456"

    @pytest.mark.asyncio
    async def test_update_session(self):
        """Test session update."""
        store = DistributedSessionStore()

        session = SessionData(
            session_id="sess-123",
            user_id="user-456",
            org_id="org-789",
        )
        await store.create_session(session)

        updated = await store.update_session(
            "sess-123",
            {"investigation_id": "inv-999"},
        )
        assert updated is True

        retrieved = await store.get_session("sess-123")
        assert retrieved is not None
        assert retrieved.investigation_id == "inv-999"

    @pytest.mark.asyncio
    async def test_delete_session(self):
        """Test session deletion."""
        store = DistributedSessionStore()

        session = SessionData(
            session_id="sess-123",
            user_id="user-456",
            org_id="org-789",
        )
        await store.create_session(session)

        deleted = await store.delete_session("sess-123")
        assert deleted is True

        retrieved = await store.get_session("sess-123")
        assert retrieved is None

    @pytest.mark.asyncio
    async def test_investigation_state(self):
        """Test investigation state storage."""
        store = DistributedSessionStore()

        state = InvestigationState(
            investigation_id="inv-123",
            session_id="sess-456",
            status="running",
            current_node="analyze",
        )
        await store.store_investigation(state)

        retrieved = await store.get_investigation("inv-123")
        assert retrieved is not None
        assert retrieved.status == "running"
        assert retrieved.current_node == "analyze"

    def test_session_data_serialization(self):
        """Test SessionData JSON serialization."""
        session = SessionData(
            session_id="sess-123",
            user_id="user-456",
            org_id="org-789",
            metadata={"key": "value"},
        )

        json_str = session.to_json()
        restored = SessionData.from_json(json_str)

        assert restored.session_id == session.session_id
        assert restored.user_id == session.user_id
        assert restored.metadata == {"key": "value"}

    def test_investigation_state_serialization(self):
        """Test InvestigationState JSON serialization."""
        state = InvestigationState(
            investigation_id="inv-123",
            session_id="sess-456",
            status="completed",
            findings=[{"issue": "test"}],
        )

        json_str = state.to_json()
        restored = InvestigationState.from_json(json_str)

        assert restored.investigation_id == state.investigation_id
        assert restored.status == "completed"
        assert restored.findings == [{"issue": "test"}]

    @pytest.mark.asyncio
    async def test_clear_local_cache(self):
        """Test clearing local cache."""
        store = DistributedSessionStore()

        session = SessionData(
            session_id="sess-123",
            user_id="user-456",
            org_id="org-789",
        )
        await store.create_session(session)

        assert "sess-123" in store._local_cache

        store.clear_local_cache()

        assert "sess-123" not in store._local_cache


@pytest.mark.skipif(not HAS_SQLALCHEMY, reason="SQLAlchemy not installed")
class TestDatabaseRouter:
    """Test database routing."""

    def setup_method(self):
        reset_db_router()

    def test_replica_health_dataclass(self):
        """Test ReplicaHealth dataclass."""
        health = ReplicaHealth(
            url="postgresql://replica1",
            healthy=True,
            latency_ms=5.0,
            last_check=time.time(),
        )
        assert health.healthy is True
        assert health.latency_ms == 5.0

    def test_async_url_conversion(self):
        """Test URL conversion to async."""
        router = DatabaseRouter()

        url1 = router._async_url("postgresql://localhost/db")
        assert url1 == "postgresql+asyncpg://localhost/db"

        url2 = router._async_url("postgres://localhost/db")
        assert url2 == "postgresql+asyncpg://localhost/db"

        url3 = router._async_url("sqlite:///test.db")
        assert url3 == "sqlite:///test.db"
