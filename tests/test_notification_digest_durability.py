"""Regression tests for durable notification retry semantics."""

import pytest


@pytest.mark.asyncio
async def test_failed_digest_delivery_is_retained_for_retry(monkeypatch):
    from app.notifications import delivery

    class Entry:
        id = "entry-1"
        event = "incident_created"
        title = "Incident"
        body = "failure"
        channels = ["webhook"]
        data = {}
        created_at = delivery.datetime.now(delivery.timezone.utc)

    async def preferences():
        return {"digest_mode": "hourly", "channels": []}
    async def fan_out(*args):
        return {"status": "failed", "results": []}
    monkeypatch.setattr(delivery, "get_preferences", preferences)
    monkeypatch.setattr(delivery, "_fan_out", fan_out)
    async def load_entries():
        return [Entry()]
    monkeypatch.setattr(delivery, "_load_digest_entries", load_entries)
    deleted = []
    async def delete_entries(entries):
        deleted.extend(entries)
    monkeypatch.setattr(delivery, "_delete_digest_entries", delete_entries)

    # The test uses the real implementation's failure branch; provider/database
    # adapters are patched only to isolate the outbox decision.
    result = await delivery.flush_digest(delivery.datetime(2026, 10, 14, 9, tzinfo=delivery.timezone.utc))
    assert result["status"] == "failed"
    assert result["retained_for_retry"] == 1
    assert deleted == []
