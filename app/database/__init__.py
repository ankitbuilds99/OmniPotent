"""Database layer with SQLAlchemy + Alembic migrations.

Production-grade database setup with:
- Async SQLAlchemy engine with connection pooling
- Alembic migrations for schema versioning
- Health checks for readiness probes
- Automatic retry and circuit breaker patterns
- Read replica routing for horizontal scaling

Note: Requires the 'postgres' extra to be installed:
    pip install -e ".[postgres]"
"""

from __future__ import annotations

from typing import TYPE_CHECKING

# Lazy imports to avoid ImportError when sqlalchemy is not installed
_HAS_SQLALCHEMY = False
try:
    import sqlalchemy  # noqa: F401
    _HAS_SQLALCHEMY = True
except ImportError:
    pass


def _require_sqlalchemy():
    if not _HAS_SQLALCHEMY:
        raise ImportError(
            "SQLAlchemy is required for database features. "
            "Install with: pip install -e '.[postgres]'"
        )


if TYPE_CHECKING or _HAS_SQLALCHEMY:
    from app.database.engine import (
        get_engine,
        get_session,
        init_db,
        close_db,
        check_db_health,
        DatabaseHealth,
    )
    from app.database.models import Base
    from app.database.routing import (
        DatabaseRouter,
        ReplicaHealth,
        get_db_router,
        reset_db_router,
    )

    __all__ = [
        "get_engine",
        "get_session",
        "init_db",
        "close_db",
        "check_db_health",
        "DatabaseHealth",
        "Base",
        "DatabaseRouter",
        "ReplicaHealth",
        "get_db_router",
        "reset_db_router",
    ]
else:
    __all__ = []
