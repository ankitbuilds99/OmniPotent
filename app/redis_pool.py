"""Redis connection pool with cluster support and health tracking.

Production-grade Redis connectivity:
- Connection pooling with automatic reconnection
- Cluster mode support for horizontal scaling
- Health checks and circuit breaking
- Separate pools for different workloads
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any, Set

log = logging.getLogger("omnipotent.redis_pool")


class RedisPoolType(Enum):
    """Workload-specific pool types with different tuning."""

    CACHE = "cache"
    SESSION = "session"
    PUBSUB = "pubsub"
    QUEUE = "queue"


@dataclass
class PoolConfig:
    """Pool configuration by workload type."""

    # Advisory only, and deliberately not passed to the pool: redis-py's
    # ConnectionPool is lazy and has no minimum/pre-warm concept, so there is
    # nothing to hand it. Kept because it is what REDIS_MIN_CONNECTIONS means to
    # an operator sizing their Redis (and it is reported by describe_pools), but
    # do not read it expecting connections to exist.
    min_connections: int
    max_connections: int
    connection_timeout: float
    socket_timeout: float
    retry_on_timeout: bool
    health_check_interval: float


@dataclass(frozen=True)
class WorkloadProfile:
    """How one workload deviates from the operator's Redis baseline.

    The baseline lives in settings (REDIS_MIN_CONNECTIONS, REDIS_MAX_CONNECTIONS,
    REDIS_CONNECTION_TIMEOUT, REDIS_SOCKET_TIMEOUT). Sizes and timeouts are stored
    here as *factors* of that baseline so that an operator who moves the baseline
    (bigger instance, slower network) moves every workload with it instead of
    having to discover four more numbers buried in this module.
    """

    min_conn_factor: float
    max_conn_factor: float
    connect_timeout_factor: float
    retry_on_timeout: bool
    health_check_interval: float
    # Multiplier on the baseline socket timeout for request/response workloads.
    socket_timeout_factor: float = 1.0
    # Absolute socket timeout in seconds for the two BLOCKING workloads, where the
    # request-path baseline is meaningless: a subscriber must never time out
    # (0.0 = block forever) and a queue consumer blocks for a whole poll interval.
    socket_timeout_s: float | None = None


WORKLOAD_PROFILES: dict[RedisPoolType, WorkloadProfile] = {
    # Cache reads are on the request path and have a cheap fallback (recompute),
    # so they fail fast — half the baseline — and never retry.
    RedisPoolType.CACHE: WorkloadProfile(
        min_conn_factor=1.0,
        max_conn_factor=1.0,
        connect_timeout_factor=0.5,
        socket_timeout_factor=0.5,
        retry_on_timeout=False,
        health_check_interval=30.0,
    ),
    # Sessions are read on every authenticated request: double the connections,
    # baseline timeouts, retry rather than log a user out on a blip.
    RedisPoolType.SESSION: WorkloadProfile(
        min_conn_factor=2.0,
        max_conn_factor=2.0,
        connect_timeout_factor=1.0,
        socket_timeout_factor=1.0,
        retry_on_timeout=True,
        health_check_interval=15.0,
    ),
    RedisPoolType.PUBSUB: WorkloadProfile(
        min_conn_factor=0.4,
        max_conn_factor=0.2,
        connect_timeout_factor=2.5,
        socket_timeout_s=0.0,
        retry_on_timeout=True,
        health_check_interval=60.0,
    ),
    RedisPoolType.QUEUE: WorkloadProfile(
        min_conn_factor=1.0,
        max_conn_factor=0.6,
        connect_timeout_factor=1.0,
        socket_timeout_s=30.0,
        retry_on_timeout=True,
        health_check_interval=30.0,
    ),
}


def pool_config(pool_type: RedisPoolType) -> PoolConfig:
    """Resolve the effective pool config for a workload from current settings.

    Called per pool creation rather than computed at import: an import-time read
    would freeze whatever the environment happened to be when this module was
    first imported, which silently ignores operator config loaded later (and
    breaks tests that patch it).
    """
    from app.config import get_settings

    s = get_settings()
    profile = WORKLOAD_PROFILES[pool_type]

    socket_timeout = (
        profile.socket_timeout_s
        if profile.socket_timeout_s is not None
        else s.redis_socket_timeout * profile.socket_timeout_factor
    )

    return PoolConfig(
        # At least one connection: a factor that rounds to 0 would make the pool
        # unusable rather than small.
        min_connections=max(1, round(s.redis_min_connections * profile.min_conn_factor)),
        max_connections=max(1, round(s.redis_max_connections * profile.max_conn_factor)),
        connection_timeout=s.redis_connection_timeout * profile.connect_timeout_factor,
        socket_timeout=socket_timeout,
        retry_on_timeout=profile.retry_on_timeout,
        health_check_interval=profile.health_check_interval,
    )


class _PoolConfigView(Mapping):
    """Mapping view kept for the historical ``POOL_CONFIGS[pool_type]`` API.

    Each lookup re-resolves from settings, so callers see the operator's current
    values rather than a snapshot taken at import time.
    """

    def __getitem__(self, key: RedisPoolType) -> PoolConfig:
        if key not in WORKLOAD_PROFILES:
            raise KeyError(key)
        return pool_config(key)

    def __iter__(self):
        return iter(WORKLOAD_PROFILES)

    def __len__(self) -> int:
        return len(WORKLOAD_PROFILES)


POOL_CONFIGS: Mapping[RedisPoolType, PoolConfig] = _PoolConfigView()


class RedisPoolManager:
    """Manages multiple Redis connection pools."""

    def __init__(self, url: str, cluster_mode: bool = False):
        self._url = url
        self._cluster_mode = cluster_mode
        self._pools: dict[RedisPoolType, Any] = {}
        self._health: dict[RedisPoolType, bool] = {}
        self._lock = asyncio.Lock()

    async def get_pool(self, pool_type: RedisPoolType) -> Any:
        """Get or create a connection pool for the specified workload."""
        async with self._lock:
            if pool_type not in self._pools:
                self._pools[pool_type] = await self._create_pool(pool_type)
                self._health[pool_type] = True
            return self._pools[pool_type]

    async def _create_pool(self, pool_type: RedisPoolType) -> Any:
        """Create a new connection pool with workload-specific tuning."""
        config = pool_config(pool_type)

        if self._cluster_mode:
            return await self._create_cluster_pool(config)

        try:
            import redis.asyncio as aioredis

            pool = aioredis.ConnectionPool.from_url(
                self._url,
                max_connections=config.max_connections,
                socket_timeout=config.socket_timeout or None,
                socket_connect_timeout=config.connection_timeout,
                retry_on_timeout=config.retry_on_timeout,
                health_check_interval=int(config.health_check_interval),
                encoding="utf-8",
                decode_responses=True,
            )

            log.info(
                "Created Redis pool: %s (max_conn=%d)",
                pool_type.value,
                config.max_connections,
            )

            return aioredis.Redis(connection_pool=pool)
        except ImportError:
            log.warning("redis package not installed, returning mock pool")
            return MockRedis()

    async def _create_cluster_pool(self, config: PoolConfig) -> Any:
        """Create a Redis Cluster client."""
        try:
            from redis.asyncio.cluster import RedisCluster

            return await RedisCluster.from_url(
                self._url,
                max_connections=config.max_connections,
                socket_timeout=config.socket_timeout or None,
                socket_connect_timeout=config.connection_timeout,
                retry_on_timeout=config.retry_on_timeout,
                health_check_interval=int(config.health_check_interval),
            )
        except ImportError:
            log.warning("redis package not installed, returning mock pool")
            return MockRedis()

    async def health_check(self) -> dict[str, Any]:
        """Check health of all pools."""
        results = {}

        for pool_type, pool in self._pools.items():
            try:
                await pool.ping()
                self._health[pool_type] = True
                try:
                    info = await pool.info("clients")
                    results[pool_type.value] = {
                        "healthy": True,
                        "connected_clients": info.get("connected_clients", 0),
                    }
                except Exception:
                    results[pool_type.value] = {"healthy": True}
            except Exception as e:
                self._health[pool_type] = False
                results[pool_type.value] = {
                    "healthy": False,
                    "error": str(e),
                }

        return results

    async def close_all(self) -> None:
        """Close all connection pools."""
        for pool_type, pool in self._pools.items():
            try:
                await pool.close()
                log.info("Closed Redis pool: %s", pool_type.value)
            except Exception as e:
                log.warning("Error closing pool %s: %s", pool_type.value, e)

        self._pools.clear()
        self._health.clear()


class MockRedis:
    """Mock Redis client for when redis is not installed.

    Provides in-memory storage for testing.
    """

    def __init__(self):
        self._data: dict[str, str] = {}
        self._sets: dict[str, set[str]] = {}

    async def ping(self) -> bool:
        return True

    async def get(self, key: str) -> str | None:
        return self._data.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> bool:
        self._data[key] = value
        return True

    async def setex(self, key: str, time: int, value: str) -> bool:
        self._data[key] = value
        return True

    async def delete(self, *keys: str) -> int:
        count = 0
        for key in keys:
            if key in self._data:
                del self._data[key]
                count += 1
        return count

    async def sadd(self, key: str, *values: str) -> int:
        if key not in self._sets:
            self._sets[key] = set()
        added = 0
        for v in values:
            if v not in self._sets[key]:
                self._sets[key].add(v)
                added += 1
        return added

    async def srem(self, key: str, *values: str) -> int:
        if key not in self._sets:
            return 0
        removed = 0
        for v in values:
            if v in self._sets[key]:
                self._sets[key].remove(v)
                removed += 1
        return removed

    async def smembers(self, key: str) -> Set[str]:
        return self._sets.get(key, set()).copy()

    async def expire(self, key: str, time: int) -> bool:
        return True

    async def info(self, section: str | None = None) -> dict:
        return {}

    async def close(self) -> None:
        self._data.clear()
        self._sets.clear()


_pool_manager: RedisPoolManager | None = None
_mock_redis: MockRedis | None = None


async def get_redis(pool_type: RedisPoolType = RedisPoolType.CACHE) -> Any:
    """Get a Redis client from the appropriate pool."""
    global _pool_manager, _mock_redis

    if _pool_manager is None:
        from app.config import get_settings

        settings = get_settings()

        url = getattr(settings, "copilot_redis_url", None)
        if not url:
            log.debug("COPILOT_REDIS_URL not configured, using mock")
            if _mock_redis is None:
                _mock_redis = MockRedis()
            return _mock_redis

        cluster_mode = getattr(settings, "copilot_redis_cluster_mode", False)
        _pool_manager = RedisPoolManager(url, cluster_mode)

    return await _pool_manager.get_pool(pool_type)


async def close_redis_pools() -> None:
    """Close all Redis pools (call on shutdown)."""
    global _pool_manager, _mock_redis
    if _pool_manager:
        await _pool_manager.close_all()
        _pool_manager = None
    if _mock_redis:
        await _mock_redis.close()
        _mock_redis = None


def reset_redis_pools() -> None:
    """Reset Redis pools (for testing)."""
    global _pool_manager, _mock_redis
    _pool_manager = None
    _mock_redis = None
