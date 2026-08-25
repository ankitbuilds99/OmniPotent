"""Async SQLAlchemy engine with connection pooling and health checks.

Production-ready database connectivity:
- Async engine with configurable pool size
- Session factory with automatic cleanup
- Health checks for readiness probes
- Graceful startup/shutdown
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncGenerator, cast

from sqlalchemy import text
from sqlalchemy.engine import CursorResult, Result
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings

log = logging.getLogger("omnipotent.database")

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


@dataclass
class DatabaseHealth:
    """Database health check result."""

    healthy: bool
    latency_ms: float
    connection_pool_size: int
    connection_pool_checked_out: int
    last_check: datetime
    error: str | None = None


def _get_database_url() -> str:
    """Get async database URL from settings."""
    s = get_settings()
    url = s.copilot_database_url

    if not url:
        url = "sqlite+aiosqlite:///data/copilot.db"

    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    elif url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif url.startswith("sqlite://"):
        url = url.replace("sqlite://", "sqlite+aiosqlite://", 1)

    return url


def get_engine() -> AsyncEngine:
    """Get the database engine, creating if needed."""
    global _engine

    if _engine is None:
        url = _get_database_url()
        s = get_settings()

        is_sqlite = "sqlite" in url

        if is_sqlite:
            _engine = create_async_engine(
                url,
                echo=s.copilot_db_echo,
                connect_args={"check_same_thread": False},
            )
        else:
            _engine = create_async_engine(
                url,
                echo=s.copilot_db_echo,
                pool_size=s.copilot_db_pool_size,
                max_overflow=s.copilot_db_pool_overflow,
                pool_timeout=s.copilot_db_pool_timeout,
                pool_recycle=s.copilot_db_pool_recycle,
                pool_pre_ping=True,
            )

        log.info(
            "Database engine created: %s (pool_size=%d)",
            "sqlite" if is_sqlite else "postgresql",
            s.copilot_db_pool_size,
        )

    return _engine


def _get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Get the session factory, creating if needed."""
    global _session_factory

    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )

    return _session_factory


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Get an async database session with automatic cleanup.

    Usage:
        async with get_session() as session:
            result = await session.execute(...)
            await session.commit()
    """
    factory = _get_session_factory()
    session = factory()
    try:
        yield session
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def session_dependency() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding a session.

    `get_session` is an @asynccontextmanager, which FastAPI cannot consume as a
    dependency — it tries to iterate the returned context-manager object and
    raises "'_AsyncGeneratorContextManager' object is not an async iterator".
    The on-call router used it directly, so every /oncall/* endpoint answered
    500. FastAPI wants a bare async generator, which is what this is.
    """
    async with get_session() as session:
        yield session


def rows_affected(result: Result[Any]) -> int:
    """How many rows an UPDATE or DELETE actually touched.

    `AsyncSession.execute` is annotated as returning the read-side `Result`,
    which has no `rowcount`; a DML statement produces a `CursorResult` at
    runtime, which does. The cast lives here rather than at each call site so
    the reason is written down once.

    Callers use this to tell "no such row" apart from "row updated" without a
    prior SELECT, which is what makes the update atomic.
    """
    return cast("CursorResult[Any]", result).rowcount


def _ensure_sqlite_directory(url: str) -> None:
    """Create the directory holding a file-backed SQLite database.

    The default URL is `sqlite+aiosqlite:///data/copilot.db`; SQLite will not
    create `data/` itself, it just fails to open the file.
    """
    path = url.split("///", 1)[-1].split("?", 1)[0]
    if not path or path == ":memory:":
        return
    parent = Path(path).parent
    if str(parent) not in ("", "."):
        parent.mkdir(parents=True, exist_ok=True)


async def init_db() -> None:
    """Initialize database tables and run migrations.

    Called during application startup. Creates tables if they don't exist
    (dev mode) or verifies migration state (production).
    """
    # Importing the model modules is what registers their tables on the shared
    # metadata; create_all only creates what has been imported.
    from app.database.models import Base
    import app.database.auth_models  # noqa: F401
    import app.database.team_models  # noqa: F401
    import app.database.growth_models  # noqa: F401
    import app.oncall.models  # noqa: F401
    import app.model_router.models  # noqa: F401
    import app.notifications.models  # noqa: F401

    engine = get_engine()
    url = _get_database_url()
    is_sqlite = "sqlite" in url

    if is_sqlite:
        _ensure_sqlite_directory(url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        log.info("SQLite database initialized (%d tables)", len(Base.metadata.tables))
    else:
        log.info("PostgreSQL database ready (use Alembic for migrations)")


async def close_db() -> None:
    """Close database connections gracefully.

    Called during application shutdown. Disposes the engine and clears
    the connection pool.
    """
    global _engine, _session_factory

    if _engine is not None:
        await _engine.dispose()
        log.info("Database engine disposed")
        _engine = None
        _session_factory = None


async def check_db_health() -> DatabaseHealth:
    """Check database health for readiness probes.

    Returns:
        DatabaseHealth with connection status and pool metrics.
    """
    engine = get_engine()
    start = asyncio.get_event_loop().time()

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

        latency = (asyncio.get_event_loop().time() - start) * 1000

        pool = engine.pool
        pool_size = getattr(pool, "size", lambda: 0)()
        checked_out = getattr(pool, "checkedout", lambda: 0)()

        return DatabaseHealth(
            healthy=True,
            latency_ms=round(latency, 2),
            connection_pool_size=pool_size,
            connection_pool_checked_out=checked_out,
            last_check=datetime.now(),
        )

    except Exception as e:
        latency = (asyncio.get_event_loop().time() - start) * 1000
        log.error("Database health check failed: %s", e)

        return DatabaseHealth(
            healthy=False,
            latency_ms=round(latency, 2),
            connection_pool_size=0,
            connection_pool_checked_out=0,
            last_check=datetime.now(),
            error=str(e),
        )
