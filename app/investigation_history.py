"""Durable investigation history — the store behind GET /api/investigations.

The `investigations` table has existed in `app/database/models.py` since the
schema was written, with an org_id, a unique (org_id, thread_id) index, token
and latency columns and a feedback rating. Nothing read it and nothing wrote it.
The endpoint instead imported a module-level dict out of
`app/api/graphql/schema.py`, which imports `strawberry` — not a dependency — so
every request to the history page returned 500. This module is the missing half.

Recording is best-effort by design: an investigation that ran and answered must
not be turned into an error because the history row could not be written, so
every failure here is logged and swallowed. Reads surface their errors, since a
history page that silently shows nothing is worse than one that says it broke.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.tenant_context import storage_org_id

log = logging.getLogger("omnipotent.investigation_history")


def _org_id() -> str:
    """The tenant key rows are filed under.

    `investigations.org_id` is a plain string, not a UUID FK into
    `organizations` — see the model docstring. `app/dashboard/metrics.py`
    aggregates the same table under the id `app.api.main._history_org_id()`
    hands it, which is this. Deriving a UUID here instead put writes and reads
    under a key no other consumer of the table uses.
    """
    return storage_org_id()


def _as_iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


async def record(
    thread_id: str,
    query: str,
    *,
    status: str,
    summary: str | None = None,
    report: dict[str, Any] | None = None,
    latency_ms: int | None = None,
) -> None:
    """Insert or update this org's row for `thread_id`.

    A thread is one investigation across many turns, so later turns update the
    existing row rather than appending a second one — which is also what the
    unique (org_id, thread_id) index requires.

    Writes the narrative columns only. `tokens_used` is deliberately not one of
    them: `app.dashboard.metrics.record_investigation` accumulates it onto this
    same row, and it is the only writer that runs on *every* completion path
    (the two approve paths have no narrative write at all). While both
    accumulated, a three-turn conversation reported five turns' worth of tokens.
    """
    from app.database.engine import get_session
    from app.database.models import Investigation

    org = _org_id()

    try:
        async with get_session() as session:
            existing = (
                await session.execute(
                    select(Investigation).where(
                        Investigation.org_id == org,
                        Investigation.thread_id == thread_id,
                    )
                )
            ).scalar_one_or_none()

            completed = (
                datetime.now(timezone.utc) if status == "completed" else None
            )

            if existing is None:
                session.add(
                    Investigation(
                        org_id=org,
                        thread_id=thread_id,
                        status=status,
                        query=query,
                        summary=summary,
                        report=report,
                        latency_ms=latency_ms,
                        completed_at=completed,
                    )
                )
            else:
                existing.status = status
                existing.summary = summary or existing.summary
                existing.report = report or existing.report
                if latency_ms is not None:
                    existing.latency_ms = latency_ms
                if completed:
                    existing.completed_at = completed

            await session.commit()
    except Exception:  # noqa: BLE001 — history must never fail a chat turn
        # Swallowed, but never quietly: a schema or tenant-key mismatch here
        # empties the history page with no other symptom, so this stays at
        # error with the traceback and names the org the write was filed under.
        log.error(
            "could not record investigation history for thread %s (org=%s)",
            thread_id,
            org,
            exc_info=True,
        )


async def set_feedback(thread_id: str, rating: str, comment: str = "") -> bool:
    """Attach a thumbs-up/down to this org's investigation. False if absent."""
    from app.database.engine import get_session
    from app.database.models import Investigation

    try:
        async with get_session() as session:
            row = (
                await session.execute(
                    select(Investigation).where(
                        Investigation.org_id == _org_id(),
                        Investigation.thread_id == thread_id,
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                return False
            row.feedback_rating = rating
            row.feedback_comment = comment or None
            await session.commit()
            return True
    except Exception:  # noqa: BLE001
        log.exception("could not record feedback for thread %s", thread_id)
        return False


async def recent(
    status: str | None = None, limit: int = 50, offset: int = 0
) -> list[dict[str, Any]]:
    """This org's investigations, newest first."""
    from app.database.engine import get_session
    from app.database.models import Investigation

    async with get_session() as session:
        stmt = (
            select(Investigation)
            .where(Investigation.org_id == _org_id())
            .order_by(Investigation.created_at.desc())
        )
        if status:
            stmt = stmt.where(Investigation.status == status)

        rows = (
            (await session.execute(stmt.offset(max(0, offset)).limit(max(1, limit))))
            .scalars()
            .all()
        )

    return [
        {
            "id": str(row.id),
            "thread_id": row.thread_id,
            "query": row.query,
            "summary": row.summary,
            "status": row.status,
            "tokens_used": row.tokens_used,
            "latency_ms": row.latency_ms,
            "feedback_rating": row.feedback_rating,
            "created_at": _as_iso(row.created_at),
            "completed_at": _as_iso(row.completed_at),
            "report": row.report or {},
        }
        for row in rows
    ]
