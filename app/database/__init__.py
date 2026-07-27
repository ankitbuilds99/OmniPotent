"""Database layer with SQLAlchemy + Alembic migrations.

Production-grade database setup with:
- Async SQLAlchemy engine with connection pooling
- Alembic migrations for schema versioning
- Health checks for readiness probes
- Automatic retry and circuit breaker patterns
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

__all__ = [
    "get_engine",
    "get_session",
    "init_db",
    "close_db",
    "check_db_health",
    "DatabaseHealth",
    "Base",
]
