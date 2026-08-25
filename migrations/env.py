"""Alembic migration environment configuration.

Supports both sync (CLI) and async (runtime) migrations with:
- Automatic URL detection from environment
- Model auto-import for autogenerate
- Transactional DDL for safe migrations
"""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.database.engine import _ensure_sqlite_directory
from app.database.models import Base

# Importing the model modules is what puts their tables on Base.metadata;
# autogenerate only sees what has been imported. Without this the 17 oncall_*
# tables were absent from every generated revision — and would have been
# dropped by one, since autogenerate reads absence as "delete this table".
import app.database.auth_models  # noqa: E402,F401
import app.database.team_models  # noqa: E402,F401
import app.database.growth_models  # noqa: E402,F401
import app.oncall.models  # noqa: E402,F401
import app.model_router.models  # noqa: E402,F401
import app.notifications.models  # noqa: E402,F401

config = context.config

if config.config_file_name is not None:
    # disable_existing_loggers defaults to True, which silences every logger
    # already configured — including the app's own. That is invisible from the
    # `alembic` CLI and very visible anywhere env.py is imported in-process.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def get_url() -> str:
    """Get database URL from environment or config."""
    # DATABASE_URL is what Render, Heroku and docker-compose inject; falling
    # back to it stops `alembic upgrade` from silently migrating the local
    # SQLite file while the app talks to Postgres.
    url = os.environ.get("COPILOT_DATABASE_URL", "") or os.environ.get("DATABASE_URL", "")
    if not url:
        url = config.get_main_option("sqlalchemy.url", "sqlite:///data/copilot.db")

    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    elif url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif url.startswith("sqlite://") and "+aiosqlite" not in url:
        url = url.replace("sqlite://", "sqlite+aiosqlite://", 1)

    if url.startswith("sqlite"):
        # SQLite does not create the directory holding the database file; it just
        # fails with "unable to open database file". The documented default is
        # `sqlite:///data/copilot.db` and `data/` is untracked, so on a fresh
        # clone `alembic upgrade head` died before running a single revision.
        # The app's own startup path already does this (app/database/engine.py);
        # reuse it so the two agree on how a URL maps to a filesystem path.
        _ensure_sqlite_directory(url)

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
