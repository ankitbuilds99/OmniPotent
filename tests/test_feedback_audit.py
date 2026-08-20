"""Feedback capture + the queryable audit trail.

The audit assertions are async now: the trail lives in `audit_events` rather
than a process-local deque, and `record()` queues rather than writing inline, so
each read flushes first. See tests/test_audit_chain_persistence.py for what the
move was actually for.
"""

import asyncio

import pytest

from app import audit, config as cfg, feedback
from app.database.engine import close_db


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
    yield
    asyncio.run(close_db())
    cfg.get_settings.cache_clear()


async def test_audit_records_are_queryable_newest_first():
    await audit.clear()
    audit.record("approval.decided", thread="t1", approved=True)
    audit.record("security.prompt_injection_detected", tool="search_logs")
    events = await audit.recent(limit=10)
    assert events[0]["event"] == "security.prompt_injection_detected"
    assert events[1]["event"] == "approval.decided"
    assert "ts" in events[0]


async def test_audit_filter_by_prefix():
    await audit.clear()
    audit.record("approval.decided", thread="t1", approved=False)
    audit.record("feedback.submitted", thread="t1", rating="up")
    only = await audit.recent(event_prefix="feedback")
    assert len(only) == 1
    assert only[0]["event"] == "feedback.submitted"


async def test_feedback_records_valid_rating(monkeypatch):
    monkeypatch.setattr(feedback, "_LOG_PATH", "")  # don't write a file in tests
    await audit.clear()
    entry = feedback.record_feedback("web-1", "down", comment="missed the cache layer", question="why slow?")
    assert entry["rating"] == "down"
    assert entry["thread_id"] == "web-1"
    assert entry["comment"] == "missed the cache layer"
    # also emits an audit event
    assert any(e["event"] == "feedback.submitted" for e in await audit.recent())


def test_feedback_rejects_bad_rating(monkeypatch):
    monkeypatch.setattr(feedback, "_LOG_PATH", "")
    with pytest.raises(ValueError):
        feedback.record_feedback("web-1", "meh")


# --- tamper-evident audit chain ------------------------------------------- #
async def test_audit_chain_verifies_when_intact():
    await audit.clear()
    audit.record("a")
    audit.record("b", x=1)
    audit.record("c")
    res = await audit.verify_all_chains()
    assert res["valid"] is True
    assert res["count"] == 3


async def test_audit_chain_detects_tampering():
    await audit.clear()
    audit.record("a")
    audit.record("b", x=1)
    audit.record("c")
    await audit.flush()

    # Tamper with the middle entry where it now lives: edit the stored row's
    # payload, leaving its hash alone. That is the shape of a real tamper --
    # somebody with database access changing what an event says.
    from sqlalchemy import select

    from app.database.engine import get_session
    from app.database.models import AuditEventRecord

    async with get_session() as session:
        row = (
            await session.execute(
                select(AuditEventRecord).where(AuditEventRecord.seq == 1)
            )
        ).scalar_one()
        row.payload = {"x": 999}
        await session.commit()

    res = await audit.verify_org_chain("-")
    assert res["valid"] is False
    assert res["broken_at"] == 1
