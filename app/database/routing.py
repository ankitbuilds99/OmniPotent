"""Database routing for read replica support.

Enables horizontal database scaling:
- Write queries → primary
- Read queries → load-balanced replicas
- Automatic failover on replica failure
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, AsyncGenerator, Literal

log = logging.getLogger("devcopilot.database.routing")

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class ReplicaHealth:
    """Health status of a read replica."""

    url: str
    healthy: bool
    latency_ms: float
    last_check: float


class DatabaseRouter:
    """Routes queries to primary or read replicas."""

    def __init__(self):
        self._primary_engine = None
        self._replica_engines: list = []
        self._replica_health: dict[str, ReplicaHealth] = {}
        self._replica_index = 0
        self._lock = asyncio.Lock()
        self._initialized = False

    async def initialize(self) -> None:
        """Initialize database connections."""
        if self._initialized:
            return

        try:
            from sqlalchemy.ext.asyncio import create_async_engine
        except ImportError:
            log.warning("SQLAlchemy async not available")
            self._initialized = True
            return

        from app.config import get_settings

        settings = get_settings()

        primary_url = getattr(settings, "copilot_database_url", "")
        if not primary_url:
            log.debug("No database URL configured")
            self._initialized = True
            return

        primary_url = self._async_url(primary_url)
        pool_size = getattr(settings, "copilot_db_pool_size", 5)
        pool_overflow = getattr(settings, "copilot_db_pool_overflow", 10)

        self._primary_engine = create_async_engine(
            primary_url,
            pool_size=pool_size,
            max_overflow=pool_overflow,
            pool_pre_ping=True,
        )

        replica_urls = getattr(settings, "copilot_db_replica_urls", "")
        if replica_urls:
            for url in replica_urls.split(","):
                url = url.strip()
                if url:
                    engine = create_async_engine(
                        self._async_url(url),
                        pool_size=max(1, pool_size // 2),
                        max_overflow=max(1, pool_overflow // 2),
                        pool_pre_ping=True,
                    )
                    self._replica_engines.append(engine)
                    self._replica_health[url] = ReplicaHealth(
                        url=url,
                        healthy=True,
                        latency_ms=0,
                        last_check=0,
                    )

        log.info(
            "Database router initialized: primary + %d replicas",
            len(self._replica_engines),
        )
        self._initialized = True

    def _async_url(self, url: str) -> str:
        """Convert sync URL to async."""
        if url.startswith("postgresql://"):
            return url.replace("postgresql://", "postgresql+asyncpg://", 1)
        if url.startswith("postgres://"):
            return url.replace("postgres://", "postgresql+asyncpg://", 1)
        return url

    def _get_session_factory(self, mode: Literal["write", "read"]):
        """Get session factory for the specified mode."""
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

        if mode == "write" or not self._replica_engines:
            engine = self._primary_engine
        else:
            engine = self._select_replica()

        return async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )

    def _select_replica(self):
        """Select a healthy replica (round-robin with health check)."""
        if not self._replica_engines:
            return self._primary_engine

        for _ in range(len(self._replica_engines)):
            self._replica_index = (self._replica_index + 1) % len(
                self._replica_engines
            )
            engine = self._replica_engines[self._replica_index]

            url = str(engine.url)
            health = self._replica_health.get(url)
            if health and health.healthy:
                return engine

        log.warning("All read replicas unhealthy, falling back to primary")
        return self._primary_engine

    @asynccontextmanager
    async def session(
        self,
        mode: Literal["write", "read"] = "read",
    ) -> AsyncGenerator["AsyncSession", None]:
        """Get a database session with automatic routing.

        Usage:
            async with router.session("read") as session:
                result = await session.execute(select(...))

            async with router.session("write") as session:
                session.add(obj)
                await session.commit()
        """
        if not self._initialized:
            await self.initialize()

        if self._primary_engine is None:
            raise RuntimeError("Database not configured")

        factory = self._get_session_factory(mode)
        session = factory()

        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    async def health_check(self) -> dict:
        """Check health of all database connections."""
        from sqlalchemy import text

        results: dict = {"primary": {"healthy": False}, "replicas": []}

        if not self._initialized:
            await self.initialize()

        if self._primary_engine is None:
            return results

        try:
            start = time.time()
            async with self._primary_engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            results["primary"] = {
                "healthy": True,
                "latency_ms": round((time.time() - start) * 1000, 2),
            }
        except Exception as e:
            results["primary"] = {"healthy": False, "error": str(e)}

        for i, engine in enumerate(self._replica_engines):
            url = str(engine.url)
            try:
                start = time.time()
                async with engine.connect() as conn:
                    await conn.execute(text("SELECT 1"))
                latency = (time.time() - start) * 1000

                self._replica_health[url] = ReplicaHealth(
                    url=url,
                    healthy=True,
                    latency_ms=latency,
                    last_check=time.time(),
                )
                results["replicas"].append(
                    {
                        "index": i,
                        "healthy": True,
                        "latency_ms": round(latency, 2),
                    }
                )
            except Exception as e:
                self._replica_health[url] = ReplicaHealth(
                    url=url,
                    healthy=False,
                    latency_ms=0,
                    last_check=time.time(),
                )
                results["replicas"].append(
                    {
                        "index": i,
                        "healthy": False,
                        "error": str(e),
                    }
                )

        return results

    async def close(self) -> None:
        """Close all database connections."""
        if self._primary_engine:
            await self._primary_engine.dispose()

        for engine in self._replica_engines:
            await engine.dispose()

        self._initialized = False
        log.info("Database router closed")


_router: DatabaseRouter | None = None


async def get_db_router() -> DatabaseRouter:
    """Get or create the database router."""
    global _router
    if _router is None:
        _router = DatabaseRouter()
        await _router.initialize()
    return _router


def reset_db_router() -> None:
    """Reset database router (for testing)."""
    global _router
    _router = None
