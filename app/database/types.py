"""Column types that work on both Postgres and SQLite.

The ORM models were written against `sqlalchemy.dialects.postgresql`, so
`Base.metadata.create_all` could not render them on SQLite — it raised
"Compiler ... can't render element of type JSONB" on the very first table.
Since the default `copilot_database_url` falls back to SQLite, that made
`init_db()` unrunnable, so the tables were never created and every DB-backed
feature (on-call in particular) answered 500 against a table that did not exist.

These keep the Postgres types on Postgres — JSONB stays JSONB, UUID stays a
native uuid column — and fall back to portable equivalents elsewhere.
"""

from __future__ import annotations

from sqlalchemy import JSON, Uuid
from sqlalchemy.dialects.postgresql import JSONB as _PostgresJSONB

# A single shared instance is fine: SQLAlchemy copies the type per column.
JSONB = _PostgresJSONB().with_variant(JSON(), "sqlite")


def UUID(as_uuid: bool = True) -> Uuid:
    """UUID column, native on Postgres and CHAR(32) elsewhere.

    Callable rather than a constant because the models spell it
    `UUID(as_uuid=True)`, matching `postgresql.UUID`'s signature.
    """
    return Uuid(as_uuid=as_uuid)
