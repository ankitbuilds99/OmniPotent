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

from app.database.orgs import ensure_org_row, org_uuid

log = logging.getLogger("omnipotent.investigation_history")


def _as_iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


async def record(
    thread_id: str,
    query: str,
    *,
    status: str,
    summary: str | None = None,
    report: dict[str, Any] | None = None,
    tokens_used: int = 0,
    latency_ms: int | None = None,
) -> None:
    """Insert or update this org's row for `thread_id`.

    A thread is one investigation across many turns, so later turns update the
    existing row rather than appending a second one — which is also what the
    unique (org_id, thread_id) index requires.
    """
    from app.database.engine import get_session
    from app.database.models import Investigation

    try:
        async with get_session() as session:
            org = await ensure_org_row(session)
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
                        tokens_used=tokens_used,
                        latency_ms=latency_ms,
                        completed_at=completed,
                    )
                )
            else:
                existing.status = status
                existing.summary = summary or existing.summary
                existing.report = report or existing.report
                # Cumulative: the column is the cost of the whole investigation,
                # not of whichever turn happened to finish last.
                existing.tokens_used = (existing.tokens_used or 0) + tokens_used
                if latency_ms is not None:
                    existing.latency_ms = latency_ms
                if completed:
                    existing.completed_at = completed

            await session.commit()
    except Exception:  # noqa: BLE001 — history must never fail a chat turn
        log.exception("could not record investigation history for thread %s", thread_id)


async def set_feedback(thread_id: str, rating: str, comment: str = "") -> bool:
    """Attach a thumbs-up/down to this org's investigation. False if absent."""
    from app.database.engine import get_session
    from app.database.models import Investigation

    try:
        async with get_session() as session:
            row = (
                await session.execute(
                    select(Investigation).where(
                        Investigation.org_id == org_uuid(),
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
            .where(Investigation.org_id == org_uuid())
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
