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
from typing import AsyncGenerator

from sqlalchemy import text
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
_schema_ready = False

# Serializes the first-caller schema creation. Keyed by the running loop rather
# than held as one module global: an asyncio.Lock binds to the loop that first
# awaits it and raises on every other one, and pytest-asyncio gives each test its
# own loop. Entries are dropped in close_db(), which every teardown path calls.
_schema_locks: dict[int, asyncio.Lock] = {}


def _schema_lock() -> asyncio.Lock:
    """The schema lock belonging to the currently running event loop."""
    key = id(asyncio.get_running_loop())
    lock = _schema_locks.get(key)
    if lock is None:
        lock = _schema_locks[key] = asyncio.Lock()
    return lock


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
        # No URL configured -> SQLite inside the one state directory every
        # subsystem shares (COPILOT_STATE_DIR), not a CWD-relative "data/" that
        # moves with however the process was launched. state_path() raises if the
        # directory can't be created: an unwritable state dir is a deployment
        # error, and silently relocating the database to /tmp used to discard
        # every row on the next restart with nothing but a log line to show.
        db_path = s.state_path("copilot.db")
        url = f"sqlite+aiosqlite:///{db_path}"

    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    elif url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif url.startswith("sqlite://") and not url.startswith("sqlite+"):
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


async def init_db() -> None:
    """Initialize database tables and run migrations.

    Called during application startup. Creates tables if they don't exist
    (dev mode) or verifies migration state (production).
    """
    from app.database.models import Base

    engine = get_engine()
    url = _get_database_url()
    is_sqlite = "sqlite" in url

    if is_sqlite:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        log.info("SQLite database initialized (tables created)")
    else:
        log.info("PostgreSQL database ready (use Alembic for migrations)")


async def ensure_schema() -> None:
    """Run :func:`init_db` at most once per engine.

    The API lifespan calls this before serving, so it is normally a no-op by the
    time a request arrives. It exists because callers that touch the ORM outside
    a request — tests, a worker, a script — otherwise hit tables that were never
    created: `init_db()` had no caller at all before this, so on the default
    (SQLite) configuration the entire ORM layer was unreachable.

    Locked, per event loop. `create_all(checkfirst=True)` is idempotent only
    against a *completed* creation: it issues a SELECT and then a CREATE, so two
    coroutines reaching a fresh database together both see no tables and both
    try to create them, and the loser raises "table ... already exists". The API
    lifespan makes that unreachable through a request, but anything that touches
    the ORM outside one — a worker, a script, a background task racing a poll —
    hits it on the first two concurrent calls. The lock is per-loop because an
    asyncio.Lock binds to the loop that first awaits it and raises on every
    other one (pytest-asyncio gives each test its own loop).
    """
    global _schema_ready

    if _schema_ready:
        return
    async with _schema_lock():
        # Re-checked inside the lock: the coroutine that waited here while
        # another one created the schema must not create it a second time.
        if _schema_ready:
            return
        await init_db()
        _schema_ready = True


async def close_db() -> None:
    """Close database connections gracefully.

    Called during application shutdown. Disposes the engine and clears
    the connection pool.
    """
    global _engine, _session_factory, _schema_ready

    if _engine is not None:
        await _engine.dispose()
        log.info("Database engine disposed")
        _engine = None
        _session_factory = None
    # The next engine may point somewhere else entirely (a test that repoints
    # COPILOT_DATABASE_URL), so "schema exists" does not survive a dispose.
    _schema_ready = False
    # Locks belong to loops that are being torn down with the engine; keeping
    # them would grow the dict once per test and hand a stale loop's lock to a
    # new loop that happened to reuse the same id.
    _schema_locks.clear()


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
