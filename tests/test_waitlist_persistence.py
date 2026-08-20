"""The waitlist is durable, positions never repeat, and referrals actually land.

Two defects are covered here, because the waitlist had both and fixing either
alone would still have lost something real:

  * `_waitlist_store` / `_email_index` were module-level dicts and
    `_position_counter` was a module-level int. Those dicts held leads — real
    email addresses submitted through the public landing page — and were emptied
    on every restart, deploy and scale-down. The counter reset with them, so the
    first signup after a deploy was handed position 1, which an earlier signup
    had already been promised;
  * `POST /waitlist` passed `req.referral_code` — a code like `REF1A2B3C4D` — as
    `referred_by`, which `add_to_waitlist` resolved against an index keyed by
    *email*. It never matched, so no referral made through the public API was
    ever credited and the "3 referrals earns priority" rule could not fire.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import config as cfg
from app import runtime
from app.api import main as api
from app.database.engine import close_db, get_session
from app.database.models import WaitlistSignup
from app.waitlist import manager, store


@pytest.fixture(autouse=True)
def db(tmp_path, monkeypatch):
    """A database per test, under tmp_path.

    The engine is a module global, so it is disposed on the way in as well as
    out — otherwise a test inherits the previous test's engine, still pointing
    at *that* test's tmp_path.
    """
    monkeypatch.setenv("COPILOT_STATE_DIR", str(tmp_path))
    cfg.get_settings.cache_clear()
    asyncio.run(close_db())
    yield
    asyncio.run(close_db())
    cfg.get_settings.cache_clear()


@pytest.fixture
def client(db):
    api._RL.clear()
    runtime.reset()
    yield TestClient(api.app)
    # Closed here too: TestClient serves on its own event loop, and aiosqlite
    # connections are bound to the loop that created them.
    asyncio.run(close_db())
    api._RL.clear()


def _restart() -> None:
    """Simulate a process restart against the same database file.

    Disposes the engine and clears the settings cache, exactly as a redeploy
    would; the file under COPILOT_STATE_DIR survives, which is the entire point
    of this slice.
    """
    asyncio.run(close_db())
    cfg.get_settings.cache_clear()


# --------------------------------------------------------------------------- #
# Durability — the thing the dicts could not do
# --------------------------------------------------------------------------- #
def test_signup_outlives_the_process():
    """A lead is still there after a restart."""
    entry, _ = asyncio.run(manager.add_to_waitlist("survivor@example.com",
                                                   company="Still Here Inc"))
    assert entry is not None

    _restart()

    found = asyncio.run(manager.get_waitlist_entry("survivor@example.com"))
    assert found is not None
    assert found.id == entry.id
    assert found.company == "Still Here Inc"
    assert found.position == entry.position


def test_positions_do_not_restart_at_one_after_a_restart():
    """The bug `_position_counter` had: it reset to 0 and reissued position 1."""
    for i in range(3):
        asyncio.run(manager.add_to_waitlist(f"early{i}@example.com"))

    _restart()

    later, _ = asyncio.run(manager.add_to_waitlist("after-deploy@example.com"))
    assert later is not None
    assert later.position == 4

    positions = [e.position for e in asyncio.run(manager.get_waitlist_entries())]
    assert positions == [1, 2, 3, 4]
    assert len(set(positions)) == len(positions)


def test_no_module_level_stores_remain():
    """The dicts are gone, not merely shadowed by the table."""
    for name in ("_waitlist_store", "_email_index", "_position_counter"):
        assert not hasattr(manager, name), f"{name} is still a module global"


# --------------------------------------------------------------------------- #
# Positions are unique, including under contention
# --------------------------------------------------------------------------- #
def test_concurrent_signups_get_distinct_positions():
    async def run() -> list[int]:
        results = await asyncio.gather(
            *(manager.add_to_waitlist(f"rush{i}@example.com") for i in range(5))
        )
        return [entry.position for entry, _ in results if entry is not None]

    positions = asyncio.run(run())
    assert len(positions) == 5
    assert sorted(positions) == [1, 2, 3, 4, 5]


def test_a_contended_position_is_retried_not_duplicated(monkeypatch):
    """If the position read never advances, the insert fails rather than collide."""
    asyncio.run(manager.add_to_waitlist("holder@example.com"))

    async def always_one() -> int:
        return 1

    monkeypatch.setattr(store, "next_position", always_one)

    entry, message = asyncio.run(manager.add_to_waitlist("loser@example.com"))
    assert entry is None
    assert "try again" in message.lower()

    # And nothing was written: one row, one position.
    entries = asyncio.run(manager.get_waitlist_entries())
    assert [e.email for e in entries] == ["holder@example.com"]


def test_duplicate_email_consumes_no_position():
    first, _ = asyncio.run(manager.add_to_waitlist("once@example.com"))
    again, message = asyncio.run(manager.add_to_waitlist("once@example.com"))

    assert again is not None
    assert again.id == first.id
    assert again.position == first.position
    assert "already" in message.lower()

    nxt, _ = asyncio.run(manager.add_to_waitlist("second@example.com"))
    assert nxt.position == 2


def test_email_is_normalised_before_lookup():
    first, _ = asyncio.run(manager.add_to_waitlist("  Mixed.Case@Example.COM  "))
    assert first.email == "mixed.case@example.com"

    again, message = asyncio.run(manager.add_to_waitlist("MIXED.CASE@example.com"))
    assert again.id == first.id
    assert "already" in message.lower()


# --------------------------------------------------------------------------- #
# Referrals — the code path the public API actually uses
# --------------------------------------------------------------------------- #
def test_referral_by_code_credits_the_referrer():
    """The live bug: a referral code was resolved against an email-keyed index."""
    referrer, _ = asyncio.run(manager.add_to_waitlist("host@example.com"))
    referred, _ = asyncio.run(
        manager.add_to_waitlist("guest@example.com", referred_by=referrer.referral_code)
    )

    assert referred.referred_by == referrer.email

    updated = asyncio.run(manager.get_waitlist_entry(referrer.email))
    assert updated.referral_count == 1


def test_referral_code_is_case_insensitive():
    referrer, _ = asyncio.run(manager.add_to_waitlist("host2@example.com"))
    asyncio.run(
        manager.add_to_waitlist(
            "guest2@example.com", referred_by=referrer.referral_code.lower()
        )
    )

    updated = asyncio.run(manager.get_waitlist_entry(referrer.email))
    assert updated.referral_count == 1


def test_referral_by_email_still_works():
    """Admin paths pass an email; both forms resolve."""
    referrer, _ = asyncio.run(manager.add_to_waitlist("host3@example.com"))
    asyncio.run(
        manager.add_to_waitlist("guest3@example.com", referred_by="host3@example.com")
    )

    updated = asyncio.run(manager.get_waitlist_entry("host3@example.com"))
    assert updated.referral_count == 1


def test_unknown_referrer_is_dropped_not_counted():
    """An unresolvable referral must not inflate the referral rate."""
    asyncio.run(manager.add_to_waitlist("solo@example.com", referred_by="REFNOTREAL"))

    entry = asyncio.run(manager.get_waitlist_entry("solo@example.com"))
    assert entry.referred_by == ""

    stats = asyncio.run(manager.get_waitlist_stats())
    assert stats["referral_rate"] == 0.0


def test_referral_upgrade_persists_across_a_restart():
    referrer, _ = asyncio.run(manager.add_to_waitlist("magnet@example.com"))
    for i in range(3):
        asyncio.run(
            manager.add_to_waitlist(
                f"pal{i}@example.com", referred_by=referrer.referral_code
            )
        )

    _restart()

    updated = asyncio.run(manager.get_waitlist_entry("magnet@example.com"))
    assert updated.referral_count == 3
    assert updated.tier == manager.WaitlistTier.PRIORITY
    # The tag list is a JSON column: it is only saved if it was reassigned, not
    # appended to in place.
    assert "referral_upgrade" in updated.tags


def test_third_referral_is_what_triggers_the_upgrade():
    referrer, _ = asyncio.run(manager.add_to_waitlist("nearly@example.com"))
    for i in range(2):
        asyncio.run(
            manager.add_to_waitlist(
                f"two{i}@example.com", referred_by=referrer.referral_code
            )
        )

    still_standard = asyncio.run(manager.get_waitlist_entry(referrer.email))
    assert still_standard.tier == manager.WaitlistTier.STANDARD
    assert "referral_upgrade" not in still_standard.tags

    asyncio.run(
        manager.add_to_waitlist("three@example.com", referred_by=referrer.referral_code)
    )
    upgraded = asyncio.run(manager.get_waitlist_entry(referrer.email))
    assert upgraded.tier == manager.WaitlistTier.PRIORITY


def test_a_promoted_referrer_is_not_demoted_to_priority():
    """Only STANDARD is bumped; an early-access referrer keeps their tier."""
    referrer, _ = asyncio.run(manager.add_to_waitlist("vip@example.com"))
    asyncio.run(manager.promote_to_early_access("vip@example.com"))

    for i in range(3):
        asyncio.run(
            manager.add_to_waitlist(
                f"vipfriend{i}@example.com", referred_by=referrer.referral_code
            )
        )

    updated = asyncio.run(manager.get_waitlist_entry("vip@example.com"))
    assert updated.tier == manager.WaitlistTier.EARLY_ACCESS
    assert updated.referral_count == 3


# --------------------------------------------------------------------------- #
# Tier changes are written, not just held
# --------------------------------------------------------------------------- #
def test_promotion_persists_across_a_restart():
    asyncio.run(manager.add_to_waitlist("invitee@example.com"))
    ok, _ = asyncio.run(manager.promote_to_early_access("invitee@example.com"))
    assert ok

    _restart()

    entry = asyncio.run(manager.get_waitlist_entry("invitee@example.com"))
    assert entry.tier == manager.WaitlistTier.EARLY_ACCESS
    assert entry.invited_at is not None
    assert entry.invited_at.utcoffset() == timedelta(0)
    assert "promoted_to_early_access" in entry.tags


def test_conversion_persists_and_shows_in_stats():
    asyncio.run(manager.add_to_waitlist("buyer@example.com"))
    asyncio.run(manager.add_to_waitlist("browser@example.com"))
    ok, _ = asyncio.run(manager.mark_converted("buyer@example.com"))
    assert ok

    _restart()

    entry = asyncio.run(manager.get_waitlist_entry("buyer@example.com"))
    assert entry.tier == manager.WaitlistTier.FOUNDING_CUSTOMER
    assert entry.converted_at is not None
    assert "converted" in entry.tags

    stats = asyncio.run(manager.get_waitlist_stats())
    assert stats["conversions"] == 1
    assert stats["conversion_rate"] == 0.5


def test_a_founding_customer_is_not_promoted_again():
    asyncio.run(manager.add_to_waitlist("closed@example.com"))
    asyncio.run(manager.mark_converted("closed@example.com"))

    ok, message = asyncio.run(manager.promote_to_early_access("closed@example.com"))
    assert not ok
    assert "founding customer" in message.lower()


# --------------------------------------------------------------------------- #
# Reads: filtering, ordering, counting
# --------------------------------------------------------------------------- #
def test_entries_are_returned_in_position_order():
    for i in range(5):
        asyncio.run(manager.add_to_waitlist(f"ordered{i}@example.com"))

    entries = asyncio.run(manager.get_waitlist_entries())
    assert [e.position for e in entries] == [1, 2, 3, 4, 5]


def test_filters_and_paging_are_applied_in_sql():
    from app.waitlist import SignupSource

    asyncio.run(manager.add_to_waitlist("l1@example.com", source=SignupSource.LANDING_PAGE))
    asyncio.run(manager.add_to_waitlist("l2@example.com", source=SignupSource.LANDING_PAGE))
    asyncio.run(manager.add_to_waitlist("ph@example.com", source=SignupSource.PRODUCT_HUNT))

    landing = asyncio.run(manager.get_waitlist_entries(source=SignupSource.LANDING_PAGE))
    assert [e.email for e in landing] == ["l1@example.com", "l2@example.com"]

    page2 = asyncio.run(manager.get_waitlist_entries(limit=1, offset=1))
    assert [e.email for e in page2] == ["l2@example.com"]

    priority = asyncio.run(
        manager.get_waitlist_entries(tier=manager.WaitlistTier.PRIORITY)
    )
    assert priority == []


def test_stats_count_recency_windows():
    asyncio.run(manager.add_to_waitlist("fresh@example.com"))

    async def backdate() -> None:
        await store.update(
            "fresh@example.com",
            created_at=datetime.now(timezone.utc) - timedelta(days=30),
        )

    stats_before = asyncio.run(manager.get_waitlist_stats())
    assert stats_before["signups_today"] == 1
    assert stats_before["signups_this_week"] == 1

    asyncio.run(backdate())

    stats_after = asyncio.run(manager.get_waitlist_stats())
    assert stats_after["total_signups"] == 1
    assert stats_after["signups_today"] == 0
    assert stats_after["signups_this_week"] == 0


def test_stats_average_position_is_computed_over_every_row():
    for i in range(4):
        asyncio.run(manager.add_to_waitlist(f"avg{i}@example.com"))

    stats = asyncio.run(manager.get_waitlist_stats())
    assert stats["avg_position"] == 2.5


def test_an_unreadable_tier_does_not_kill_the_listing():
    """A row written by an older build must not take down every read."""
    asyncio.run(manager.add_to_waitlist("legacy@example.com"))

    async def corrupt() -> None:
        await store.update("legacy@example.com", tier="tier_from_the_future")

    asyncio.run(corrupt())

    entries = asyncio.run(manager.get_waitlist_entries())
    assert len(entries) == 1
    assert entries[0].tier == manager.WaitlistTier.STANDARD


def test_export_masks_the_address_in_json_but_not_in_csv():
    """`to_dict` masks for the dashboard; CSV is the deliberate operator export."""
    import json

    asyncio.run(manager.add_to_waitlist("someone@example.com", company="Acme"))

    parsed = json.loads(asyncio.run(manager.export_waitlist(format="json")))
    assert parsed[0]["email"] == "s*****e@example.com"

    csv_data = asyncio.run(manager.export_waitlist(format="csv"))
    assert "someone@example.com" in csv_data


def test_created_at_reads_back_as_utc():
    asyncio.run(manager.add_to_waitlist("tz@example.com"))

    _restart()

    entry = asyncio.run(manager.get_waitlist_entry("tz@example.com"))
    assert entry.created_at.utcoffset() == timedelta(0)


def test_the_raw_ip_is_never_stored():
    asyncio.run(
        manager.add_to_waitlist("privacy@example.com", ip_address="203.0.113.7")
    )

    async def read_row() -> WaitlistSignup:
        from sqlalchemy import select

        async with get_session() as session:
            result = await session.execute(
                select(WaitlistSignup).where(
                    WaitlistSignup.email == "privacy@example.com"
                )
            )
            return result.scalars().one()

    row = asyncio.run(read_row())
    assert "203.0.113.7" not in row.ip_hash
    assert len(row.ip_hash) == 16


# --------------------------------------------------------------------------- #
# End to end, through the routes the landing page calls
# --------------------------------------------------------------------------- #
def test_api_signup_then_position(client):
    signup = client.post("/waitlist", json={"email": "api@example.com"})
    assert signup.status_code == 200, signup.text
    body = signup.json()
    assert body["position"] == 1
    assert body["referral_code"].startswith("REF")

    position = client.get("/waitlist/position", params={"email": "api@example.com"})
    assert position.status_code == 200
    assert position.json()["position"] == 1
    assert position.json()["total"] == 1


def test_api_referral_code_credits_the_referrer(client):
    """The end-to-end regression for the referral bug."""
    host = client.post("/waitlist", json={"email": "apihost@example.com"}).json()

    guest = client.post(
        "/waitlist",
        json={"email": "apiguest@example.com", "referral_code": host["referral_code"]},
    )
    assert guest.status_code == 200, guest.text

    position = client.get("/waitlist/position", params={"email": "apihost@example.com"})
    assert position.json()["referral_count"] == 1


def test_api_position_is_404_for_an_unknown_email(client):
    response = client.get("/waitlist/position", params={"email": "nobody@example.com"})
    assert response.status_code == 404


def test_api_rejects_an_invalid_email(client):
    response = client.post("/waitlist", json={"email": "nope"})
    assert response.status_code == 400
    assert "invalid" in response.json()["detail"].lower()


def test_api_stats_reflect_the_table(client):
    client.post("/waitlist", json={"email": "s1@example.com", "source": "product_hunt"})
    client.post("/waitlist", json={"email": "s2@example.com"})

    stats = client.get("/waitlist/stats")
    assert stats.status_code == 200
    body = stats.json()
    assert body["total_signups"] == 2
    assert body["by_source"]["product_hunt"] == 1
    assert body["by_source"]["landing_page"] == 1


def test_api_signup_survives_a_restart(client):
    client.post("/waitlist", json={"email": "durable@example.com"})

    asyncio.run(close_db())
    cfg.get_settings.cache_clear()

    position = client.get("/waitlist/position", params={"email": "durable@example.com"})
    assert position.status_code == 200
    assert position.json()["position"] == 1
