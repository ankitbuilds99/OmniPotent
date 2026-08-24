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

from typing import Any

from sqlalchemy import JSON, Uuid
from sqlalchemy.dialects.postgresql import JSONB as _PostgresJSONB

# A single shared instance is fine: SQLAlchemy copies the type per column.
JSONB = _PostgresJSONB().with_variant(JSON(), "sqlite")


def UUID(as_uuid: bool = True) -> Uuid[Any]:
    """UUID column, native on Postgres and CHAR(32) elsewhere.

    Callable rather than a constant because the models spell it
    `UUID(as_uuid=True)`, matching `postgresql.UUID`'s signature.
    """
    # Branching rather than passing `as_uuid` straight through: Uuid's
    # overloads are keyed on Literal[True]/Literal[False], because the flag
    # decides whether the column yields uuid.UUID or str. A plain bool matches
    # neither, so forwarding it fails to type-check.
    return Uuid(as_uuid=True) if as_uuid else Uuid(as_uuid=False)
