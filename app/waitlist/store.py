"""Database-backed storage for waitlist signups.

Replaces the three module-level globals in `app/waitlist/manager.py`
(`_waitlist_store`, `_email_index`, `_position_counter`). Those held leads —
real email addresses submitted by real people — in a dict emptied on every
restart, deploy and scale-down, and `_position_counter` reset to 0 along with
it, so signups arriving after a deploy were issued positions that earlier
signups already held.

Not tenant-scoped, unlike the other stores added in this effort: a signup
happens before the person has an organization, so there is nothing to scope it
to.

This module deals in ORM rows; the mapping to and from `WaitlistEntry` lives in
`manager.py`, which owns the enums and the email masking.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncGenerator

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.engine import ensure_schema, get_session
from app.database.models import WaitlistSignup

log = logging.getLogger("omnipotent.waitlist.store")

# How many times to retry a signup whose position was claimed by a concurrent
# insert between reading MAX(position) and writing. Each attempt re-reads the
# maximum, so a handful covers far more contention than a signup form will ever
# produce.
_POSITION_RETRIES = 5


@asynccontextmanager
async def _session() -> AsyncGenerator[AsyncSession, None]:
    await ensure_schema()
    async with get_session() as session:
        yield session


def utc(value: datetime | None) -> datetime | None:
    """Re-attach UTC to a value read back from SQLite, which drops the offset."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


async def next_position() -> int:
    """The next waitlist position, derived from the table rather than a counter.

    The counter this replaces lived in module scope and reset to 0 on restart,
    so position 1 was handed out again to whoever signed up after a deploy.
    """
    async with _session() as session:
        current = (
            await session.execute(select(func.max(WaitlistSignup.position)))
        ).scalar()
        return int(current or 0) + 1


async def insert(**fields: Any) -> tuple[WaitlistSignup | None, str]:
    """Insert one signup, assigning its position, retrying on a lost race.

    Returns `(row, "")` on success and `(None, reason)` otherwise. A duplicate
    email is a real answer for the caller — the person is already on the list —
    so it is reported distinctly from a duplicate position, which is nobody's
    fault and is simply retried against a freshly-read maximum.
    """
    email = fields["email"]

    for attempt in range(_POSITION_RETRIES):
        row = WaitlistSignup(position=await next_position(), **fields)
        async with _session() as session:
            session.add(row)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                if await get_by_email(email) is not None:
                    return None, "duplicate_email"
                log.info(
                    "Waitlist position %d was taken concurrently; retrying (%d/%d)",
                    row.position,
                    attempt + 1,
                    _POSITION_RETRIES,
                )
                continue
            return row, ""

    log.error(
        "Gave up assigning a waitlist position after %d attempts", _POSITION_RETRIES
    )
    return None, "position_contention"


async def get_by_email(email: str) -> WaitlistSignup | None:
    async with _session() as session:
        result = await session.execute(
            select(WaitlistSignup).where(WaitlistSignup.email == email)
        )
        return result.scalars().first()


async def get_by_referral_code(code: str) -> WaitlistSignup | None:
    """Look a signup up by the code it hands out to people it refers.

    Needed because the signup API sends a referral *code* as `referred_by`;
    before this existed that value was resolved against an email-keyed index,
    matched nothing, and every referral was silently dropped.
    """
    async with _session() as session:
        result = await session.execute(
            select(WaitlistSignup).where(WaitlistSignup.referral_code == code)
        )
        return result.scalars().first()


async def update(email: str, **changes: Any) -> WaitlistSignup | None:
    """Apply field changes to one signup, returning the updated row or None.

    Loads through the session rather than taking a detached row so that `tags`
    is reassigned on a live instance: SQLAlchemy only marks a JSON column dirty
    on assignment, and an in-place `append` to a detached list goes nowhere.
    """
    async with _session() as session:
        result = await session.execute(
            select(WaitlistSignup).where(WaitlistSignup.email == email)
        )
        row = result.scalars().first()
        if row is None:
            return None
        for key, value in changes.items():
            setattr(row, key, value)
        await session.commit()
        return row


async def list_entries(
    *,
    tier: str | None = None,
    source: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[WaitlistSignup]:
    """List signups in position order — the order the waitlist is served in."""
    stmt = select(WaitlistSignup)
    if tier is not None:
        stmt = stmt.where(WaitlistSignup.tier == tier)
    if source is not None:
        stmt = stmt.where(WaitlistSignup.source == source)
    stmt = stmt.order_by(WaitlistSignup.position.asc()).limit(limit).offset(offset)

    async with _session() as session:
        return list((await session.execute(stmt)).scalars().all())


async def count() -> int:
    async with _session() as session:
        total = (
            await session.execute(select(func.count()).select_from(WaitlistSignup))
        ).scalar()
        return int(total or 0)


async def stats() -> dict[str, Any]:
    """Aggregate the whole waitlist.

    Counted in SQL rather than by loading every row: the waitlist is the one
    table here that is meant to grow without bound, and this is the number a
    dashboard polls.
    """
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = now - timedelta(days=7)

    async def _count(session: AsyncSession, *where: Any) -> int:
        stmt = select(func.count()).select_from(WaitlistSignup)
        for clause in where:
            stmt = stmt.where(clause)
        return int((await session.execute(stmt)).scalar() or 0)

    async with _session() as session:
        total = await _count(session)
        if total == 0:
            return {
                "total_signups": 0,
                "by_tier": {},
                "by_source": {},
                "conversion_rate": 0.0,
                "referral_rate": 0.0,
                "conversions": 0,
                "signups_today": 0,
                "signups_this_week": 0,
                "avg_position": 0,
            }

        by_tier = {
            tier: int(n)
            for tier, n in (
                await session.execute(
                    select(WaitlistSignup.tier, func.count()).group_by(
                        WaitlistSignup.tier
                    )
                )
            ).all()
        }
        by_source = {
            source: int(n)
            for source, n in (
                await session.execute(
                    select(WaitlistSignup.source, func.count()).group_by(
                        WaitlistSignup.source
                    )
                )
            ).all()
        }
        conversions = await _count(session, WaitlistSignup.converted_at.isnot(None))
        referred = await _count(session, WaitlistSignup.referred_by != "")
        signups_today = await _count(session, WaitlistSignup.created_at >= today_start)
        signups_this_week = await _count(
            session, WaitlistSignup.created_at >= week_start
        )
        avg_position = (
            await session.execute(select(func.avg(WaitlistSignup.position)))
        ).scalar()

    return {
        "total_signups": total,
        "by_tier": by_tier,
        "by_source": by_source,
        "conversion_rate": conversions / total,
        "referral_rate": referred / total,
        "conversions": conversions,
        "signups_today": signups_today,
        "signups_this_week": signups_this_week,
        "avg_position": float(avg_position or 0),
    }


async def clear() -> None:
    """Delete every signup. For tests and for a deliberate reset."""
    async with _session() as session:
        await session.execute(delete(WaitlistSignup))
        await session.commit()
