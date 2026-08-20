"""Alembic migration environment configuration.

Supports both sync (CLI) and async (runtime) migrations with:
- Automatic URL detection from environment
- Model auto-import for autogenerate
- Transactional DDL for safe migrations
"""

from __future__ import annotations

import asyncio
import logging
import os
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.database.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

log = logging.getLogger("alembic.env")

target_metadata = Base.metadata


def _redact(url: str) -> str:
    """Drop any `user:password@` from a URL so the rest can be logged."""
    scheme, _, rest = url.partition("://")
    if not rest:
        return url
    _, at, hostpart = rest.rpartition("@")
    return f"{scheme}://{hostpart}" if at else url


def _ensure_sqlite_parent(url: str) -> None:
    """Create the directory a SQLite file lives in, if it is missing.

    `app/database/engine.py` learned this in 2717e94: a relative
    `sqlite:///data/copilot.db` fails with the singularly unhelpful "unable to
    open database file" when `data/` is absent -- which it is in every fresh
    clone and every CI checkout, because `data/` is gitignored and was never
    tracked.
    """
    prefix = "sqlite+aiosqlite:///"
    if not url.startswith(prefix):
        return

    parent = Path(url[len(prefix) :]).parent
    if parent in (Path(""), Path(".")):
        return
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        log.warning("Cannot create %s for the SQLite database", parent)


def get_url() -> str:
    """Resolve the database URL, honouring the same names the app honours.

    `app/config.py` accepts `COPILOT_DATABASE_URL` *or* a bare `DATABASE_URL`,
    because every managed platform injects the latter (Render, Heroku, Helm,
    docker-compose). Alembic read only the first, so on those platforms the app
    talked to Postgres while migrations went somewhere else entirely -- the very
    split that comment in `config.py` exists to prevent.

    It is not hypothetical. Every alembic step in
    `.github/workflows/database-migrations.yml` passes the URL as
    `DATABASE_URL`, including *Migrate Production*: that job would have pointed
    at a throwaway SQLite file on the runner rather than at production, and the
    Slack step would have announced it as a success.
    """
    url = os.environ.get("COPILOT_DATABASE_URL", "")
    source = "COPILOT_DATABASE_URL"
    if not url:
        url = os.environ.get("DATABASE_URL", "")
        source = "DATABASE_URL"
    if not url:
        url = config.get_main_option("sqlalchemy.url", "sqlite:///data/copilot.db")
        source = "alembic.ini"

    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    elif url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif url.startswith("sqlite://") and "+aiosqlite" not in url:
        url = url.replace("sqlite://", "sqlite+aiosqlite://", 1)

    _ensure_sqlite_parent(url)
    # Say out loud what is about to be migrated. A migration run against the
    # wrong database is the failure this module is most able to cause and least
    # able to undo, so it should never be something you have to infer.
    log.info("Migrating %s (url from %s)", _redact(url), source)
    return url


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL and not an Engine,
    though an Engine is acceptable here as well. By skipping the Engine
    creation we don't even need a DBAPI to be available.
    """
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Run migrations with a live connection."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations in 'online' mode with async engine."""
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_url()

    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
