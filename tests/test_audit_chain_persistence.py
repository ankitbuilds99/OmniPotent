"""The audit trail survives, spans replicas, and only claims what it can prove.

`app/audit_legacy.py` kept every audit event in `_BUFFER`, a
`deque(maxlen=2000)`, and the running chain hash in a module-level `_LAST_HASH`.
Both backed GET /audit and GET /audit/verify, and the combination was wrong in
two opposite directions:

  * past 2000 events, on a single pod, verification failed permanently. The
    deque had evicted the genesis entry, so the oldest survivor's `prev_hash` no
    longer matched the empty string verification starts from, and the endpoint
    answered `{"valid": false, "broken_at": 0}` forever. A tamper-evidence
    control that cries wolf permanently is worse than none at all: the first
    real detection reads as the same known false alarm;

  * below 2000 events, on more than one replica, it answered `valid: true`
    about one pod's fraction of the trail, because each replica kept its own
    buffer and its own running hash.

Migration 009_audit moves the events to `audit_events`, one chain per org,
position held by a unique `(org_id, seq)` so several processes can append to a
strictly serial chain at once.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections import deque
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, OperationalError

from app import audit_legacy as audit
from app import config as cfg, tenant_context
from app.audit import store
from app.database.engine import close_db, get_session
from app.database.models import AuditEventRecord


@pytest.fixture(autouse=True)
def db(tmp_path, monkeypatch):
    """A database per test, under tmp_path.

    The engine is a module global, so it is disposed on the way in as well as
    out -- otherwise a test inherits the previous test's engine, still pointing
    at *that* test's tmp_path.
    """
    monkeypatch.setenv("COPILOT_STATE_DIR", str(tmp_path))
    cfg.get_settings.cache_clear()
    asyncio.run(close_db())
    audit._PENDING.clear()
    yield
    asyncio.run(close_db())
    cfg.get_settings.cache_clear()


async def _restart() -> None:
    """Simulate a process restart against the same database file.

    Disposes the engine and clears the settings cache, exactly as a redeploy
    would; the file under COPILOT_STATE_DIR survives, which is the entire point.
    Also clears the hand-off queue, because a real restart loses it.
    """
    await audit.flush()
    await close_db()
    audit._PENDING.clear()
    cfg.get_settings.cache_clear()


# --- the bug that needed no second replica -------------------------------- #


async def test_verification_holds_past_the_old_buffer_bound():
    """The regression that motivated the whole slice.

    `_BUFFER` was `deque(maxlen=2000)`. At the 2001st event it evicted the
    genesis entry, and from then on `verify_chain` -- which starts from
    `prev_hash == ""` -- reported the trail as broken at index 0 on a single
    pod, with nothing wrong. 2500 events here is past that edge by a clear
    margin.
    """
    await audit.clear()
    for i in range(2500):
        audit.record("bulk.event", n=i)
    await audit.flush()

    verdict = await audit.verify_org_chain("-")
    assert verdict["valid"] is True, "the chain must verify past 2000 events"
    assert verdict["count"] == 2500, "and it must not have silently dropped any"


async def test_nothing_is_evicted():
    """The count is the count. The deque quietly capped it at 2000."""
    await audit.clear()
    for i in range(2100):
        audit.record("bulk.event", n=i)
    await audit.flush()
    assert await store.count("-") == 2100


# --- durability ------------------------------------------------------------ #


async def test_events_survive_a_restart():
    await audit.clear()
    audit.record("approval.decided", thread="t1", approved=True)
    await _restart()

    events = await audit.recent(limit=10)
    assert [e["event"] for e in events] == ["approval.decided"]
    assert events[0]["approved"] is True


async def test_the_chain_continues_across_a_restart():
    """The link across the restart boundary is the one a per-process
    `_LAST_HASH` could not make: after a restart it was "" again, so the next
    event started a second chain that verification would reject."""
    await audit.clear()
    audit.record("first")
    await _restart()
    audit.record("second")
    await audit.flush()

    verdict = await audit.verify_org_chain("-")
    assert verdict["valid"] is True
    assert verdict["count"] == 2

    chain = await store.chain("-")
    assert chain[1]["prev_hash"] == chain[0]["hash"], "the second event must fold in the first"


async def test_sequence_numbers_are_dense_and_start_at_zero():
    await audit.clear()
    for i in range(5):
        audit.record("e", n=i)
    await audit.flush()

    async with get_session() as session:
        seqs = (
            await session.execute(
                select(AuditEventRecord.seq).order_by(AuditEventRecord.seq)
            )
        ).scalars().all()
    assert list(seqs) == [0, 1, 2, 3, 4]


# --- tamper evidence ------------------------------------------------------- #


async def test_editing_a_stored_event_is_detected():
    await audit.clear()
    audit.record("a")
    audit.record("b", x=1)
    audit.record("c")
    await audit.flush()

    async with get_session() as session:
        row = (
            await session.execute(
                select(AuditEventRecord).where(AuditEventRecord.seq == 1)
            )
        ).scalar_one()
        row.payload = {"x": 999}
        await session.commit()

    verdict = await audit.verify_org_chain("-")
    assert verdict["valid"] is False
    assert verdict["broken_at"] == 1
    assert verdict["event"] == "b"


async def test_deleting_a_stored_event_is_detected():
    """The deque could not detect this at all: a deletion just looked like the
    buffer having wrapped, which is also what a legitimate eviction looked
    like."""
    await audit.clear()
    audit.record("a")
    audit.record("b")
    audit.record("c")
    await audit.flush()

    async with get_session() as session:
        row = (
            await session.execute(
                select(AuditEventRecord).where(AuditEventRecord.seq == 1)
            )
        ).scalar_one()
        await session.delete(row)
        await session.commit()

    verdict = await audit.verify_org_chain("-")
    assert verdict["valid"] is False
    assert verdict["broken_at"] == 1, "the entry after the hole no longer folds in its predecessor"


async def test_an_intact_chain_verifies():
    await audit.clear()
    for i in range(50):
        audit.record("e", n=i)
    await audit.flush()
    assert (await audit.verify_org_chain("-"))["valid"] is True


# --- multi-tenant ---------------------------------------------------------- #


async def test_chains_are_per_org_and_verified_separately(monkeypatch):
    await audit.clear()
    monkeypatch.setattr(tenant_context, "tenant_id", lambda: "org_a")
    audit.record("a.one")
    audit.record("a.two")
    monkeypatch.setattr(tenant_context, "tenant_id", lambda: "org_b")
    audit.record("b.one")
    await audit.flush()

    assert (await audit.verify_org_chain("org_a"))["count"] == 2
    assert (await audit.verify_org_chain("org_b"))["count"] == 1

    summary = await audit.verify_all_chains()
    assert summary["valid"] is True
    assert summary["orgs_checked"] == 2
    assert summary["count"] == 3


async def test_one_orgs_events_are_not_visible_to_another(monkeypatch):
    """The buffer was not tenant-scoped at all: GET /audit returned every org's
    events to whoever asked."""
    await audit.clear()
    monkeypatch.setattr(tenant_context, "tenant_id", lambda: "org_a")
    audit.record("a.secret", detail="only for a")
    monkeypatch.setattr(tenant_context, "tenant_id", lambda: "org_b")
    audit.record("b.own")
    await audit.flush()

    visible = await audit.recent(limit=100)
    assert [e["event"] for e in visible] == ["b.own"]


async def test_a_broken_chain_names_the_org_it_belongs_to(monkeypatch):
    """A single valid flag over a multi-tenant deployment would hide which
    tenant's trail is the broken one."""
    await audit.clear()
    monkeypatch.setattr(tenant_context, "tenant_id", lambda: "org_a")
    audit.record("a.one")
    audit.record("a.two")
    monkeypatch.setattr(tenant_context, "tenant_id", lambda: "org_b")
    audit.record("b.one")
    audit.record("b.two")
    await audit.flush()

    async with get_session() as session:
        row = (
            await session.execute(
                select(AuditEventRecord).where(
                    AuditEventRecord.org_id == "org_b", AuditEventRecord.seq == 1
                )
            )
        ).scalar_one()
        row.payload = {"tampered": True}
        await session.commit()

    summary = await audit.verify_all_chains()
    assert summary["valid"] is False
    assert summary["broken_orgs"] == ["org_b"]


# --- concurrency ----------------------------------------------------------- #


async def test_concurrent_appends_produce_one_unbroken_chain():
    """The point of the unique `(org_id, seq)` constraint.

    A hash chain is strictly serial, so several writers appending at once must
    still land at distinct positions each folding in the one before. Without the
    constraint two writers read the same head and both write `seq`, producing
    two entries claiming the same predecessor.
    """
    await audit.clear()
    batches = [[{"ts": "t", "event": f"w{w}.{i}", "org_id": "-", "actor": None,
                 "request_id": None} for i in range(5)] for w in range(6)]
    await asyncio.gather(*(store.append(b) for b in batches))

    assert await store.count("-") == 30
    verdict = await audit.verify_org_chain("-")
    assert verdict["valid"] is True, "every concurrent append must fold into the chain"
    assert verdict["count"] == 30


async def test_concurrent_appends_take_distinct_positions():
    await audit.clear()
    batches = [[{"ts": "t", "event": f"w{w}", "org_id": "-", "actor": None,
                 "request_id": None}] for w in range(12)]
    await asyncio.gather(*(store.append(b) for b in batches))

    async with get_session() as session:
        total = (await session.execute(select(func.count()).select_from(AuditEventRecord))).scalar_one()
        distinct = (
            await session.execute(select(func.count(func.distinct(AuditEventRecord.seq))))
        ).scalar_one()
    assert total == 12
    assert distinct == 12, "two events must never share a chain position"


async def test_exhausting_retries_hands_the_events_back_rather_than_dropping(monkeypatch):
    """The append path may fail. It may not lose an event while failing.

    This is the defect the first cut of `append()` shipped with: after a fixed
    number of contended attempts it logged at ERROR and moved on. A dropped
    event never claims a `seq`, so the entries around it stay dense and keep
    folding into one another -- the chain still verifies as `valid: true` while
    silently missing entries, which is the precise failure `audit_events` was
    introduced to end. Losing the event to a *log line* is not better than
    losing it to a deque.
    """
    await audit.clear()
    monkeypatch.setattr(store, "_APPEND_RETRIES", 3)
    monkeypatch.setattr(store, "_BACKOFF_BASE_SECONDS", 0.0)

    @asynccontextmanager
    async def always_loses_the_race():
        class _Contended:
            async def execute(self, *args, **kwargs):
                raise IntegrityError("forced", (), Exception("position taken"))

        yield _Contended()

    monkeypatch.setattr(store, "_session", always_loses_the_race)

    entries = [
        {"ts": "t", "event": "never.lands", "org_id": "-", "actor": None, "request_id": None}
    ]
    with pytest.raises(store.ContendedAppend) as raised:
        await store.append(entries)

    assert raised.value.written == []
    assert [e["event"] for e in raised.value.unwritten] == ["never.lands"]


async def test_a_writer_that_loses_the_write_lock_retries_instead_of_raising(monkeypatch):
    """The other way a writer loses a race, and the one that was not caught.

    SQLite serialises writers behind one database-wide write lock, so a writer
    that cannot take it before the busy timeout gets `OperationalError:
    database is locked` rather than the `IntegrityError` that a lost `(org_id,
    seq)` race produces. Only the latter was retried, so this escaped
    `append()` uncaught -- past the `ContendedAppend` requeue path, taking the
    unwritten remainder of the batch with it. Six concurrent writers hit it in
    roughly a quarter of runs.
    """
    await audit.clear()
    monkeypatch.setattr(store, "_BACKOFF_BASE_SECONDS", 0.0)

    real_session = store._session
    attempts = {"n": 0}

    @asynccontextmanager
    async def locked_for_the_first_two_attempts():
        attempts["n"] += 1
        if attempts["n"] <= 2:

            class _Locked:
                async def execute(self, *args, **kwargs):
                    raise OperationalError(
                        "INSERT", (), sqlite3.OperationalError("database is locked")
                    )

            yield _Locked()
            return
        async with real_session() as session:
            yield session

    monkeypatch.setattr(store, "_session", locked_for_the_first_two_attempts)

    written = await store.append(
        [{"ts": "t", "event": "waited.its.turn", "org_id": "-", "actor": None,
          "request_id": None}]
    )

    assert [e["event"] for e in written] == ["waited.its.turn"]
    assert attempts["n"] == 3, "the two locked attempts must have been retried, not swallowed"
    assert await store.count("-") == 1


async def test_a_broken_database_fails_immediately_rather_than_retrying(monkeypatch):
    """The retry is for contention, not for every OperationalError.

    A missing table is not going to appear on the third attempt. Blanket-
    retrying would turn an instant, legible failure into twenty-five rounds of
    backoff ending in a `ContendedAppend` that names the wrong problem.
    """
    await audit.clear()
    monkeypatch.setattr(store, "_BACKOFF_BASE_SECONDS", 0.0)

    attempts = {"n": 0}

    @asynccontextmanager
    async def no_such_table():
        attempts["n"] += 1

        class _Broken:
            async def execute(self, *args, **kwargs):
                raise OperationalError(
                    "SELECT", (), sqlite3.OperationalError("no such table: audit_events")
                )

        yield _Broken()

    monkeypatch.setattr(store, "_session", no_such_table)

    with pytest.raises(OperationalError):
        await store.append(
            [{"ts": "t", "event": "never", "org_id": "-", "actor": None, "request_id": None}]
        )

    assert attempts["n"] == 1


async def test_a_partial_append_requeues_only_what_did_not_land(monkeypatch):
    """No loss, and no duplication either.

    `flush()` used to take the whole batch off the queue and treat a short
    result as success, so anything `append()` failed to place was already gone.
    Requeueing the *whole* batch would be the opposite error: the entries that
    did land would be written a second time, and a duplicated audit event is
    its own kind of lie.
    """
    await audit.clear()
    audit.record("landed.one")
    audit.record("stuck.two")
    audit.record("stuck.three")

    real_append = store.append

    async def only_the_first_lands(entries):
        written = await real_append(entries[:1])
        raise store.ContendedAppend(written, list(entries[1:]))

    monkeypatch.setattr(store, "append", only_the_first_lands)
    assert await audit.flush() == 1, "the one that landed counts as written"
    assert [e["event"] for e in audit._PENDING] == ["stuck.two", "stuck.three"], (
        "only the remainder goes back, in its original order"
    )

    monkeypatch.undo()
    assert await audit.flush() == 2
    assert [e["event"] for e in await store.chain("-")] == [
        "landed.one",
        "stuck.two",
        "stuck.three",
    ], "nothing lost, nothing written twice"


async def test_a_requeue_that_does_not_fit_is_counted_not_silent(monkeypatch):
    """Sustained backpressure is allowed to shed events. It is not allowed to
    hide that it did -- that is what `dropped_count()` is for."""
    await audit.clear()
    monkeypatch.setattr(audit, "_PENDING", deque(maxlen=3))
    monkeypatch.setattr(audit, "_dropped", 0)

    audit.record("a")
    audit.record("b")
    audit.record("c")

    async def fails_while_more_arrives(entries):
        # The queue keeps filling while the write is in flight, so the batch no
        # longer fits when it comes back.
        audit.record("late.one")
        audit.record("late.two")
        raise RuntimeError("database is down")

    monkeypatch.setattr(store, "append", fails_while_more_arrives)

    assert await audit.flush() == 0
    assert len(audit._PENDING) == 3, "the queue stays bounded"
    assert audit.dropped_count() == 2, "and says exactly how much it shed"


# --- the hand-off queue is not a store ------------------------------------- #


async def test_recording_does_not_block_on_the_database():
    """`record()` stays synchronous on purpose: five of its call sites are in
    sync functions, and a synchronous write from the other 22 would block the
    event loop. So it queues, and the read path flushes."""
    await audit.clear()
    audit.record("queued.event")
    assert await store.count("-") == 0, "record() must not have written inline"
    assert await audit.recent(limit=10), "reading must flush what is queued"


async def test_flush_is_idempotent():
    await audit.clear()
    audit.record("once")
    assert await audit.flush() == 1
    assert await audit.flush() == 0
    assert await store.count("-") == 1


async def test_a_failed_write_requeues_rather_than_losing_events(monkeypatch):
    """A transient database error must not lose audit events -- that would be a
    worse failure than the one this module was rewritten to fix."""
    await audit.clear()
    audit.record("important.one")
    audit.record("important.two")

    async def boom(entries):
        raise RuntimeError("database is down")

    monkeypatch.setattr(store, "append", boom)
    assert await audit.flush() == 0
    assert len(audit._PENDING) == 2, "the events must still be queued"

    monkeypatch.undo()
    assert await audit.flush() == 2
    assert [e["event"] for e in await store.chain("-")] == [
        "important.one",
        "important.two",
    ], "and they must keep their original order"


async def test_queue_overflow_is_counted_not_silent():
    """The queue is bounded so a stalled drain costs fixed memory. Dropping an
    audit event is exactly the thing that must not happen quietly."""
    await audit.clear()
    maxlen = audit._PENDING.maxlen
    assert maxlen is not None
    for i in range(maxlen + 5):
        audit.record("flood", n=i)
    assert audit.dropped_count() == 5


async def test_no_in_memory_event_store_remains():
    """`_BUFFER` is gone, not merely bypassed."""
    import inspect
    import re

    source = inspect.getsource(audit)
    # An *assignment*, not a mention: the module docstring explains what the
    # buffer was and why it went.
    assert not re.search(r"^_BUFFER\s*[:=]", source, re.M)
    assert not hasattr(audit, "_BUFFER")
    assert not hasattr(audit, "_LAST_HASH"), "the per-process chain head is gone too"


async def test_clear_empties_both_the_queue_and_the_table():
    audit.record("x")
    await audit.flush()
    audit.record("y")
    await audit.clear()
    assert len(audit._PENDING) == 0
    assert await store.count() == 0
