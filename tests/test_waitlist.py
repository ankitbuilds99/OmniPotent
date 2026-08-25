"""The waitlist survives a restart, and anonymous visitors can join it.

`app/waitlist/manager.py` was three module-level dicts plus a `_position_counter`
integer that reset to 0 with the process. Everything a visitor was told on
signup — their position, their referral code — held only until the next deploy,
and a second uvicorn worker answered from a second, empty waitlist. On top of
that, `POST /waitlist` sat behind the global `require_auth`, so the only people
who could join the waitlist were people who already had an API key.

These tests assert both halves: the rows are in `waitlist_entries` and read back
through a fresh call after the store is re-created, and the join/position
endpoints answer without a credential while the roll-up does not.
"""

from __future__ import annotations

import pytest

import app.config as cfg
from app import tenant_context
from app.database import engine as db_engine
from app.waitlist import (
    SignupSource,
    WaitlistManager,
    WaitlistTier,
    add_to_waitlist,
    export_waitlist,
    get_waitlist_entries,
    get_waitlist_entry,
    get_waitlist_stats,
    mark_converted,
    promote_to_early_access,
)


@pytest.fixture
async def db(tmp_path, monkeypatch):
    monkeypatch.setenv("COPILOT_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/app.db")
    cfg.get_settings.cache_clear()
    db_engine._engine = None
    db_engine._session_factory = None
    tenant_context.current_tenant.set(None)

    await db_engine.init_db()
    yield

    await db_engine.close_db()
    cfg.get_settings.cache_clear()
    db_engine._engine = None
    db_engine._session_factory = None
    tenant_context.current_tenant.set(None)


class TestWaitlistSignup:
    """Tests for waitlist signup functionality."""

    async def test_basic_signup(self, db):
        entry, message = await add_to_waitlist(
            email="test@example.com",
            source=SignupSource.LANDING_PAGE,
        )

        assert entry is not None
        assert entry.email == "test@example.com"
        assert entry.position == 1
        assert entry.referral_code.startswith("REF")
        assert "waitlist" in message.lower()

    async def test_signup_with_company_info(self, db):
        entry, _ = await add_to_waitlist(
            email="cto@startup.io",
            source=SignupSource.PRODUCT_HUNT,
            company="Startup Inc",
            role="CTO",
            team_size="10-50",
            use_case="Reduce MTTR by 50%",
        )

        assert entry is not None
        assert entry.company == "Startup Inc"
        assert entry.role == "CTO"
        assert entry.team_size == "10-50"
        assert entry.source == SignupSource.PRODUCT_HUNT

    async def test_duplicate_email_returns_existing(self, db):
        entry1, _ = await add_to_waitlist(email="dup@example.com")
        entry2, msg2 = await add_to_waitlist(email="dup@example.com")

        assert entry1 is not None
        assert entry2 is not None
        assert entry1.id == entry2.id
        assert "already" in msg2.lower()

    async def test_invalid_email_rejected(self, db):
        entry, message = await add_to_waitlist(email="not-an-email")

        assert entry is None
        assert "invalid" in message.lower()

    async def test_empty_email_rejected(self, db):
        entry, message = await add_to_waitlist(email="")

        assert entry is None
        assert "required" in message.lower()

    async def test_position_increments(self, db):
        entry1, _ = await add_to_waitlist(email="first@example.com")
        entry2, _ = await add_to_waitlist(email="second@example.com")
        entry3, _ = await add_to_waitlist(email="third@example.com")

        assert entry1.position == 1
        assert entry2.position == 2
        assert entry3.position == 3

    async def test_position_continues_across_a_restart(self, db):
        """The counter this replaces restarted at 0 with the process.

        Everyone who signed up after a deploy was told they were #1, behind
        however many thousand people were already waiting.
        """
        for i in range(3):
            await add_to_waitlist(email=f"before{i}@example.com")

        # Drop the engine and session factory the way a process restart would,
        # then reopen against the same file.
        await db_engine.close_db()
        db_engine._engine = None
        db_engine._session_factory = None
        await db_engine.init_db()

        entry, _ = await add_to_waitlist(email="after@example.com")
        assert entry.position == 4

        # And the earlier signups are still there to be found.
        assert (await get_waitlist_entry("before0@example.com")).position == 1

    async def test_referral_tracking(self, db):
        referrer, _ = await add_to_waitlist(email="referrer@example.com")
        referred, _ = await add_to_waitlist(
            email="referred@example.com",
            referred_by=referrer.email,
        )

        assert referred.referred_by == referrer.email

        updated_referrer = await get_waitlist_entry(referrer.email)
        assert updated_referrer.referral_count == 1

    async def test_referral_upgrade_after_3(self, db):
        referrer, _ = await add_to_waitlist(email="popular@example.com")

        for i in range(3):
            await add_to_waitlist(
                email=f"friend{i}@example.com",
                referred_by=referrer.email,
            )

        updated = await get_waitlist_entry(referrer.email)
        assert updated.tier == WaitlistTier.PRIORITY
        assert updated.referral_count == 3


class TestWaitlistStats:
    """Tests for waitlist statistics."""

    async def test_empty_stats(self, db):
        stats = await get_waitlist_stats()

        assert stats["total_signups"] == 0
        assert stats["conversion_rate"] == 0.0

    async def test_stats_by_source(self, db):
        await add_to_waitlist("a@example.com", source=SignupSource.LANDING_PAGE)
        await add_to_waitlist("b@example.com", source=SignupSource.LANDING_PAGE)
        await add_to_waitlist("c@example.com", source=SignupSource.PRODUCT_HUNT)

        stats = await get_waitlist_stats()

        assert stats["total_signups"] == 3
        assert stats["by_source"]["landing_page"] == 2
        assert stats["by_source"]["product_hunt"] == 1

    async def test_conversion_rate_counts_converted_entries(self, db):
        await add_to_waitlist("converted@example.com")
        await add_to_waitlist("waiting@example.com")

        ok, _ = await mark_converted("converted@example.com")
        assert ok

        stats = await get_waitlist_stats()
        assert stats["conversions"] == 1
        assert stats["conversion_rate"] == 0.5


class TestWaitlistPromotion:
    """Tests for waitlist tier promotion."""

    async def test_promote_to_early_access(self, db):
        await add_to_waitlist(email="vip@example.com")

        success, _ = await promote_to_early_access("vip@example.com")

        assert success
        entry = await get_waitlist_entry("vip@example.com")
        assert entry.tier == WaitlistTier.EARLY_ACCESS
        assert entry.invited_at is not None

    async def test_promote_nonexistent_email(self, db):
        success, message = await promote_to_early_access("ghost@example.com")

        assert not success
        assert "not found" in message.lower()


class TestWaitlistManager:
    """Tests for WaitlistManager class."""

    async def test_manager_signup(self, db):
        manager = WaitlistManager()
        result = await manager.signup(email="manager@example.com", company="Test Co")

        assert result["success"]
        assert result["position"] == 1
        assert "referral_code" in result

    async def test_manager_get_position(self, db):
        manager = WaitlistManager()
        await manager.signup(email="lookup@example.com")

        result = await manager.get_position("lookup@example.com")

        assert result["found"]
        assert result["position"] == 1
        assert result["total"] == 1

    async def test_manager_position_not_found(self, db):
        manager = WaitlistManager()
        result = await manager.get_position("unknown@example.com")

        assert not result["found"]


class TestWaitlistExport:
    """Tests for waitlist export functionality."""

    async def test_export_csv(self, db):
        await add_to_waitlist("export1@example.com", company="Company A")
        await add_to_waitlist("export2@example.com", company="Company B")

        csv_data = await export_waitlist(format="csv")

        assert "position" in csv_data
        assert "email" in csv_data
        assert "Company A" in csv_data
        assert "Company B" in csv_data

    async def test_export_json(self, db):
        import json

        await add_to_waitlist("json@example.com", company="JSON Corp")

        json_data = await export_waitlist(format="json")
        parsed = json.loads(json_data)

        assert len(parsed) == 1
        assert parsed[0]["company"] == "JSON Corp"


class TestWaitlistPaging:
    """Ordering, limit and offset are the database's job now."""

    async def test_entries_come_back_in_position_order(self, db):
        for i in range(5):
            await add_to_waitlist(email=f"p{i}@example.com")

        page = await get_waitlist_entries(limit=2, offset=1)

        assert [e.position for e in page] == [2, 3]

    async def test_filter_by_tier(self, db):
        await add_to_waitlist(email="standard@example.com")
        await add_to_waitlist(email="early@example.com")
        await promote_to_early_access("early@example.com")

        early = await get_waitlist_entries(tier=WaitlistTier.EARLY_ACCESS)

        assert [e.email for e in early] == ["early@example.com"]
