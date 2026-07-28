"""Database layer with SQLAlchemy + Alembic migrations.

Production-grade database setup with:
- Async SQLAlchemy engine with connection pooling
- Alembic migrations for schema versioning
- Health checks for readiness probes
- Automatic retry and circuit breaker patterns
- Read replica routing for horizontal scaling
"""

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
