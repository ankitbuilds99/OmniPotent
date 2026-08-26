"""Database-backed storage for the hash-chained audit trail.

Replaces `app.audit_legacy._BUFFER`, a `deque(maxlen=2000)` that backed both
GET /audit and GET /audit/verify. See migration 009 for why that buffer was
wrong in both directions -- falsely alarming past 2000 events on a single pod,
falsely reassuring below it on any deployment with more than one replica.

Two things make a serial hash chain safe to append to from several processes:

*   The chain's position is explicit. `seq` counts from 0 within an org, and
    `(org_id, seq)` is unique, so two appends racing for the same position
    cannot both win -- the loser re-reads the head and retries.
*   The hash is computed against the row that is actually its predecessor in
    the table, not against a process-local `_LAST_HASH` that each replica kept
    its own copy of.

The canonical form the hash is computed over is unchanged from the in-memory
implementation, so `verify_chain` in `app.audit_legacy` still verifies a list of
entries read from here, and the JSONL sink stays byte-compatible.
"""

from __future__ import annotations

import asyncio
import logging
import random
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

from sqlalchemy import delete, desc, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.engine import ensure_schema, get_session
from app.database.models import AuditEventRecord

log = logging.getLogger("omnipotent.audit.store")

# Attempts to claim a chain position before giving up. Each retry re-reads the
# head, so this tolerates far more concurrency than the number suggests: it
# bounds consecutive losses by *this* writer, not total contention.
#
# It was 8 with no backoff, which lost. Every loser of a race woke immediately
# and raced again, so N writers stayed in lockstep and re-collided; six
# concurrent writers exhausted eight attempts often enough to fail a test once
# in three runs. The backoff below breaks the lockstep, and exhaustion now
# raises instead of dropping, so the number only decides how long we try before
# handing the entries back -- not whether they survive.
_APPEND_RETRIES = 25
_BACKOFF_BASE_SECONDS = 0.002
_BACKOFF_CAP_SECONDS = 0.05


def _backoff_delay(attempt: int) -> float:
    """Exponential backoff with full jitter, capped.

    The jitter is the part that matters: without it every writer that lost a
    race retries at the same instant as the others that lost it, which is the
    same collision again with the same participants.
    """
    ceiling = min(_BACKOFF_BASE_SECONDS * (2**attempt), _BACKOFF_CAP_SECONDS)
    return random.uniform(0, ceiling)


class ContendedAppend(RuntimeError):
    """Raised when an entry could not claim a chain position in time.

    Carries the entries that *did* land, so a caller can requeue only the
    remainder. Requeueing the whole batch would write the successful ones a
    second time, and a duplicated audit event is its own kind of lie.
    """

    def __init__(
        self, written: list[dict[str, Any]], unwritten: list[dict[str, Any]]
    ) -> None:
        super().__init__(
            f"{len(unwritten)} audit event(s) could not claim a chain position "
            f"after {_APPEND_RETRIES} attempts"
        )
        self.written = written
        self.unwritten = unwritten

# Fields that live in their own columns; everything else the caller passed goes
# to `payload`. Keeping the split explicit is what lets an entry be reassembled
# exactly as it was hashed.
_FIXED = ("ts", "event", "request_id", "org_id", "actor")


@asynccontextmanager
async def _session() -> AsyncGenerator[AsyncSession, None]:
    await ensure_schema()
    async with get_session() as session:
        yield session


def to_entry(row: AuditEventRecord) -> dict[str, Any]:
    """Rebuild the entry dict exactly as it was hashed.

    Key order is irrelevant -- `_canonical` sorts keys -- but membership is not:
    an extra or missing key changes the canonical form and would make every
    row from here on verify as broken.
    """
    entry: dict[str, Any] = {
        "ts": row.ts,
        "event": row.event,
        "request_id": row.request_id,
        "org_id": row.org_id,
        "actor": row.actor,
        **(row.payload or {}),
    }
    entry["prev_hash"] = row.prev_hash
    entry["hash"] = row.hash
    return entry


def split_entry(entry: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Split a built entry into its fixed columns and its payload."""
    fixed = {k: entry.get(k) for k in _FIXED}
    payload = {
        k: v for k, v in entry.items() if k not in _FIXED and k not in ("hash", "prev_hash")
    }
    return fixed, payload


async def append(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Append entries to their org's chain, hashing each against its predecessor.

    Entries are grouped by org because each org has its own chain. Within a
    group they are appended in the order given.

    Returns the entries as they were actually written, chain fields included --
    the caller needs those for the JSONL sink, which must record the hash that
    landed rather than one computed before the predecessor was known.

    A writer that loses a race for a position backs off and retries. One that
    exhausts its retries raises `ContendedAppend` carrying what landed and what
    did not, so the caller requeues the remainder. It must not drop: a dropped
    event never claims a `seq`, so the surviving chain stays dense and still
    verifies as intact -- the trail would be missing entries and
    `/audit/verify` would answer `valid: true` about it, which is the exact
    failure this table was introduced to end. Bounding the backlog is the
    queue's job, and `app.audit_legacy._PENDING` already does it with a counter.

    Entries are appended in the order given, so a requeued remainder keeps its
    original order.
    """
    if not entries:
        return []

    # Import here: app.audit_legacy imports nothing from this module, and going
    # the other way at module scope would make the cycle real.
    from app.audit_legacy import chain_hash

    written: list[dict[str, Any]] = []

    for index, entry in enumerate(entries):
        org_id = str(entry.get("org_id") or "-")
        for attempt in range(_APPEND_RETRIES):
            try:
                async with _session() as session:
                    head = (
                        await session.execute(
                            select(AuditEventRecord)
                            .where(AuditEventRecord.org_id == org_id)
                            .order_by(desc(AuditEventRecord.seq))
                            .limit(1)
                        )
                    ).scalar_one_or_none()

                    seq = 0 if head is None else head.seq + 1
                    prev_hash = "" if head is None else head.hash

                    # Recomputed here rather than trusted from the caller:
                    # the predecessor is only known now, and the whole point
                    # of the chain is that each link folds in the one before
                    # it as it actually landed.
                    body = {k: v for k, v in entry.items() if k not in ("hash", "prev_hash")}
                    body["prev_hash"] = prev_hash
                    digest = chain_hash(body, prev_hash)

                    fixed, payload = split_entry(body)
                    session.add(
                        AuditEventRecord(
                            org_id=org_id,
                            seq=seq,
                            ts=str(fixed.get("ts") or ""),
                            event=str(fixed.get("event") or ""),
                            request_id=fixed.get("request_id"),
                            actor=fixed.get("actor"),
                            payload=payload,
                            prev_hash=prev_hash,
                            hash=digest,
                        )
                    )
                    await session.commit()
                body["hash"] = digest
                written.append(body)
                break
            except IntegrityError:
                # Somebody else took this position between the read and the
                # write. Back off, then re-read the head and chain onto
                # whatever landed.
                await asyncio.sleep(_backoff_delay(attempt))
                continue
        else:
            log.error(
                "could not place audit event %s for org %s after %d contended "
                "attempts; handing back %d unwritten event(s)",
                entry.get("event"),
                org_id,
                _APPEND_RETRIES,
                len(entries) - index,
            )
            raise ContendedAppend(written, list(entries[index:]))

    return written


async def chain(org_id: str) -> list[dict[str, Any]]:
    """The whole chain for an org, oldest first -- the order it was hashed in."""
    async with _session() as session:
        rows = (
            await session.execute(
                select(AuditEventRecord)
                .where(AuditEventRecord.org_id == org_id)
                .order_by(AuditEventRecord.seq)
            )
        ).scalars().all()
    return [to_entry(r) for r in rows]


async def recent(
    org_id: str, limit: int = 100, event_prefix: str = ""
) -> list[dict[str, Any]]:
    """The most recent events for an org, newest first.

    Filtering and limiting happen in SQL. The buffer version pulled everything
    into memory and sliced, which was fine for 2000 entries and is not for a
    table that never evicts.
    """
    async with _session() as session:
        stmt = select(AuditEventRecord).where(AuditEventRecord.org_id == org_id)
        if event_prefix:
            stmt = stmt.where(AuditEventRecord.event.startswith(event_prefix))
        rows = (
            await session.execute(stmt.order_by(desc(AuditEventRecord.seq)).limit(limit))
        ).scalars().all()
    return [to_entry(r) for r in rows]


async def orgs() -> list[str]:
    """Every org with at least one event, so /audit/verify can check them all."""
    async with _session() as session:
        rows = (
            await session.execute(
                select(AuditEventRecord.org_id).distinct().order_by(AuditEventRecord.org_id)
            )
        ).scalars().all()
    return list(rows)


async def count(org_id: str | None = None) -> int:
    """How many events are stored, for an org or in total."""
    async with _session() as session:
        stmt = select(func.count()).select_from(AuditEventRecord)
        if org_id is not None:
            stmt = stmt.where(AuditEventRecord.org_id == org_id)
        return int((await session.execute(stmt)).scalar_one())


async def clear() -> None:
    """Drop every stored event (tests)."""
    async with _session() as session:
        await session.execute(delete(AuditEventRecord))
        await session.commit()
