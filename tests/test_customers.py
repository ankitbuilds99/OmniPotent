"""Tests for customer and trial management.

Every test in this file used to be synchronous, because the module under test
kept customers, trials and usage in module-level dicts. It is now database-backed
(see app/customers/manager.py), so the API is async and each test gets its own
throwaway database.
"""

import asyncio

import pytest

from app.config import get_settings
from app.database.engine import close_db


@pytest.fixture(autouse=True)
async def isolated_db(tmp_path, monkeypatch):
    """Point the ORM at a fresh SQLite file per test.

    Also disposes the engine on both sides of the test. That is not tidiness:
    aiosqlite connections are bound to the event loop that opened them, pytest-
    asyncio gives each test its own loop, and a pooled connection surviving into
    the next test fails at use time rather than at reuse time.
    """
    monkeypatch.setenv("COPILOT_STATE_DIR", str(tmp_path))
    get_settings.cache_clear()
    await close_db()
    yield
    await close_db()
    get_settings.cache_clear()


class TestCustomerCreation:
    """Tests for customer creation."""

    async def test_create_customer(self):
        """Test basic customer creation."""
        from app.customers import create_customer

        customer, message = await create_customer(
            email="cust@startup.io",
            company="Startup Inc",
        )

        assert customer is not None
        assert customer.email == "cust@startup.io"
        assert customer.company == "Startup Inc"
        assert customer.id.startswith("cust_")
        assert "success" in message.lower()

    async def test_create_customer_with_details(self):
        """Test customer creation with full details."""
        from app.customers import create_customer, CustomerStatus

        customer, _ = await create_customer(
            email="detailed@company.com",
            company="Detailed Corp",
            name="John Smith",
            role="VP Engineering",
            team_size="50-100",
            industry="Fintech",
            use_case="Reduce incident response time",
        )

        assert customer.name == "John Smith"
        assert customer.role == "VP Engineering"
        assert customer.team_size == "50-100"
        assert customer.industry == "Fintech"
        assert customer.status == CustomerStatus.TRIAL

    async def test_duplicate_email_returns_existing(self):
        """Test that duplicate email returns existing customer."""
        from app.customers import create_customer

        cust1, msg1 = await create_customer("dup@example.com", "First Co")
        cust2, msg2 = await create_customer("dup@example.com", "Second Co")

        assert cust1.id == cust2.id
        assert "exists" in msg2.lower()

    async def test_invalid_email_rejected(self):
        """Test that invalid email is rejected."""
        from app.customers import create_customer

        customer, message = await create_customer("invalid", "Test Co")

        assert customer is None
        assert "email" in message.lower()


class TestTrialManagement:
    """Tests for trial management."""

    async def test_create_trial(self):
        """Test trial creation."""
        from app.customers import create_customer, create_trial, TrialStatus

        customer, _ = await create_customer("trial@test.com", "Trial Co")
        trial, message = await create_trial(customer.id)

        assert trial is not None
        assert trial.customer_id == customer.id
        assert trial.status == TrialStatus.ACTIVE
        assert trial.days_remaining > 0
        assert trial.investigations_allowed == 10

    async def test_trial_custom_duration(self):
        """Test trial with custom duration."""
        from app.customers import create_customer, create_trial

        customer, _ = await create_customer("custom@test.com", "Custom Co")
        trial, _ = await create_trial(
            customer.id,
            duration_days=30,
            investigations_allowed=20,
            tokens_allowed=200000,
        )

        assert trial.days_remaining <= 30
        assert trial.investigations_allowed == 20
        assert trial.tokens_allowed == 200000

    async def test_trial_for_nonexistent_customer(self):
        """Test trial creation for non-existent customer."""
        from app.customers import create_trial

        trial, message = await create_trial("cust_nonexistent")

        assert trial is None
        assert "not found" in message.lower()

    async def test_active_trial_check(self):
        """Test active trial detection."""
        from app.customers import create_customer, create_trial, get_active_trials

        customer, _ = await create_customer("active@test.com", "Active Co")
        await create_trial(customer.id)

        active = await get_active_trials()

        assert len(active) == 1
        assert active[0].customer_id == customer.id

    async def test_trial_usage_tracking(self):
        """Test trial usage percentage calculation."""
        from app.customers import (
            create_customer,
            create_trial,
            record_usage,
            get_trial,
        )

        customer, _ = await create_customer("usage@test.com", "Usage Co")
        trial, _ = await create_trial(customer.id, investigations_allowed=10)

        await record_usage(
            customer_id=customer.id,
            event_type="investigation",
            investigation_id="inv_001",
            tokens_used=5000,
        )

        updated_trial = await get_trial(trial.id)

        assert updated_trial.investigations_used == 1
        assert updated_trial.tokens_used == 5000
        assert updated_trial.usage_percent == 10.0

    async def test_expired_trial_is_recorded_as_expired_not_recomputed(self):
        """A trial past its window must be persisted as EXPIRED.

        `get_active_trials` performs the expiry sweep. When that state lived in a
        dict the transition happened once per process and was lost on restart, so
        a trial could resurrect as ACTIVE. The status has to survive the sweep.
        """
        from app.customers import create_customer, create_trial, get_active_trials, get_trial
        from app.customers.manager import TrialStatus

        customer, _ = await create_customer("expired@test.com", "Expired Co")
        trial, _ = await create_trial(customer.id, duration_days=-1)

        assert await get_active_trials() == []

        stored = await get_trial(trial.id)
        assert stored.status == TrialStatus.EXPIRED
        assert stored.is_active is False
        assert stored.days_remaining == 0


class TestTrialConversion:
    """Tests for trial to paid conversion."""

    async def test_convert_trial(self):
        """Test converting trial to paid."""
        from app.customers import (
            create_customer,
            create_trial,
            convert_trial,
            get_customer,
            CustomerTier,
            CustomerStatus,
        )

        customer, _ = await create_customer("convert@test.com", "Convert Co")
        await create_trial(customer.id)

        success, message = await convert_trial(
            customer_id=customer.id,
            tier=CustomerTier.PRO,
            mrr_cents=49900,
        )

        assert success

        updated = await get_customer(customer_id=customer.id)
        assert updated.status == CustomerStatus.ACTIVE
        assert updated.tier == CustomerTier.PRO
        assert updated.mrr_cents == 49900
        assert updated.is_paying

    async def test_convert_nonexistent_customer(self):
        """Test converting non-existent customer."""
        from app.customers import convert_trial

        success, message = await convert_trial(customer_id="cust_ghost")

        assert not success
        assert "not found" in message.lower()

    async def test_conversion_tags_are_persisted(self):
        """Conversion appends tags to a JSON column.

        Pinned because the natural spelling — `customer.tags.append(...)` — does
        not work: SQLAlchemy only marks a JSON column dirty on assignment, so an
        in-place append is silently discarded at commit.
        """
        from app.customers import create_customer, convert_trial, get_customer, CustomerTier

        customer, _ = await create_customer("tags@test.com", "Tag Co", source="landing")
        await convert_trial(customer.id, tier=CustomerTier.STARTER, mrr_cents=9900)

        updated = await get_customer(customer_id=customer.id)
        assert updated.tags == ["source:landing", "converted", "tier:starter"]


class TestUsageTracking:
    """Tests for customer usage tracking."""

    async def test_record_usage(self):
        """Test recording customer usage."""
        from app.customers import create_customer, record_usage, get_customer

        customer, _ = await create_customer("track@test.com", "Track Co")

        success = await record_usage(
            customer_id=customer.id,
            event_type="investigation",
            investigation_id="inv_123",
            tokens_used=1500,
            duration_seconds=120.5,
        )

        assert success

        updated = await get_customer(customer_id=customer.id)
        assert updated.total_investigations == 1
        assert updated.total_tokens_used == 1500
        assert updated.last_active_at is not None

    async def test_record_multiple_usage_events(self):
        """Test recording multiple usage events."""
        from app.customers import create_customer, record_usage, get_customer

        customer, _ = await create_customer("multi@test.com", "Multi Co")

        for i in range(5):
            await record_usage(
                customer_id=customer.id,
                event_type="investigation",
                investigation_id=f"inv_{i}",
                tokens_used=1000,
            )

        updated = await get_customer(customer_id=customer.id)
        assert updated.total_investigations == 5
        assert updated.total_tokens_used == 5000

    async def test_record_usage_nonexistent_customer(self):
        """Test recording usage for non-existent customer."""
        from app.customers import record_usage

        success = await record_usage(
            customer_id="cust_ghost",
            event_type="investigation",
        )

        assert not success

    async def test_usage_events_are_kept_individually_not_just_summed(self):
        """The per-event log is what an invoice dispute is settled with.

        The counters on the customer row are a derived total; if only the total
        survived there would be no way to answer "which investigations was I
        charged for".
        """
        from app.customers import create_customer, record_usage, get_customer_usage

        customer, _ = await create_customer("audit@test.com", "Audit Co")
        await record_usage(customer.id, "investigation", investigation_id="inv_a", tokens_used=10)
        await record_usage(customer.id, "investigation", investigation_id="inv_b", tokens_used=20)
        await record_usage(customer.id, "export", investigation_id="", region="eu")

        events = await get_customer_usage(customer.id)

        assert len(events) == 3
        assert {e.investigation_id for e in events} == {"inv_a", "inv_b", ""}
        assert sum(e.tokens_used for e in events) == 30
        export = next(e for e in events if e.event_type == "export")
        assert export.metadata == {"region": "eu"}

    async def test_a_stale_snapshot_cannot_clobber_the_billing_counters(self):
        """Returned customers are detached read models, not live rows.

        This is the invariant that makes the counters safe to increment in SQL:
        a caller holding an older copy has no write path back to the row, so a
        long-running request cannot roll `total_tokens_used` backwards.
        """
        from app.customers import create_customer, record_usage, get_customer

        customer, _ = await create_customer("stale@test.com", "Stale Co")
        await record_usage(customer.id, "investigation", tokens_used=100)

        # `customer` predates the usage above. Mutating it must write nothing.
        customer.total_tokens_used = 999_999
        customer.total_investigations = 0
        customer.company = "Renamed By A Stale Copy"

        fresh = await get_customer(customer_id=customer.id)
        assert fresh.total_tokens_used == 100
        assert fresh.total_investigations == 1
        assert fresh.company == "Stale Co"

    async def test_concurrent_usage_records_do_not_lose_counts(self):
        """Ten overlapping usage events must total ten.

        Read-modify-write in Python would lose some of these; the counters are
        incremented with `col = col + n` in SQL precisely so they cannot.
        """
        from app.customers import create_customer, record_usage, get_customer

        customer, _ = await create_customer("race@test.com", "Race Co")

        results = await asyncio.gather(
            *(
                record_usage(customer.id, "investigation", tokens_used=100)
                for _ in range(10)
            )
        )
        assert all(results)

        updated = await get_customer(customer_id=customer.id)
        assert updated.total_investigations == 10
        assert updated.total_tokens_used == 1000


class TestHealthScore:
    """Tests for customer health score calculation."""

    async def test_baseline_health_score(self):
        """Test baseline health score for new customer."""
        from app.customers import create_customer

        customer, _ = await create_customer("health@test.com", "Health Co")

        assert customer.health_score >= 50

    async def test_health_score_increases_with_usage(self):
        """Test health score increases with usage."""
        from app.customers import create_customer, record_usage, get_customer

        customer, _ = await create_customer("active@test.com", "Active Co")
        initial_score = customer.health_score

        for i in range(5):
            await record_usage(
                customer_id=customer.id,
                event_type="investigation",
                investigation_id=f"inv_{i}",
            )

        updated = await get_customer(customer_id=customer.id)
        assert updated.health_score > initial_score


class TestCustomerStats:
    """Tests for customer statistics."""

    async def test_empty_stats(self):
        """Test stats with no customers."""
        from app.customers import get_customer_stats

        stats = await get_customer_stats()

        assert stats["total_customers"] == 0
        assert stats["mrr_usd"] == 0.0

    async def test_stats_with_customers(self):
        """Test stats calculation."""
        from app.customers import (
            create_customer,
            create_trial,
            convert_trial,
            get_customer_stats,
            CustomerTier,
        )

        cust1, _ = await create_customer("stat1@test.com", "Stats Co 1")
        cust2, _ = await create_customer("stat2@test.com", "Stats Co 2")

        await create_trial(cust1.id)
        await create_trial(cust2.id)

        await convert_trial(cust1.id, tier=CustomerTier.STARTER, mrr_cents=9900)

        stats = await get_customer_stats()

        assert stats["total_customers"] == 2
        assert stats["total_trials"] == 2
        assert stats["paying_customers"] == 1
        assert stats["mrr_usd"] == 99.0
        assert stats["by_tier"] == {"free": 1, "starter": 1}
        assert stats["conversion_rate"] == 0.5


class TestCustomerManager:
    """Tests for CustomerManager class."""

    async def test_manager_create_with_trial(self):
        """Test manager create with auto trial."""
        from app.customers import CustomerManager

        manager = CustomerManager()
        result = await manager.create(
            email="mgr@test.com",
            company="Manager Co",
            start_trial=True,
        )

        assert result["success"]
        assert "customer" in result
        assert "trial" in result
        assert result["trial"]["days_remaining"] > 0

    async def test_manager_create_without_trial(self):
        """Test manager create without trial."""
        from app.customers import CustomerManager

        manager = CustomerManager()
        result = await manager.create(
            email="notrial@test.com",
            company="No Trial Co",
            start_trial=False,
        )

        assert result["success"]
        assert "customer" in result
        assert "trial" not in result

    async def test_manager_dashboard(self):
        """Test manager dashboard data."""
        from app.customers import CustomerManager, create_customer, create_trial

        customer, _ = await create_customer("dash@test.com", "Dashboard Co")
        await create_trial(customer.id)

        manager = CustomerManager()
        dashboard = await manager.get_dashboard()

        assert "stats" in dashboard
        assert "active_trials" in dashboard
        assert "recent_customers" in dashboard
        assert len(dashboard["active_trials"]) == 1


class TestPersistence:
    """The reason this module was rewritten: state must outlive the process."""

    async def test_customers_and_usage_survive_a_restart(self):
        """Everything a bill is computed from must still be there after a restart.

        Disposing the engine and clearing the settings cache is what a process
        restart looks like to this module. Before the move to the database this
        test could not pass by construction: `_customer_store` was a dict, so the
        customer, the trial and every usage record died with the process — and
        with a replica count above 1, died on every scale-down too.
        """
        from app.customers import (
            create_customer,
            create_trial,
            record_usage,
            get_customer,
            get_trial,
            get_customer_usage,
        )

        customer, _ = await create_customer("durable@test.com", "Durable Co")
        trial, _ = await create_trial(customer.id)
        await record_usage(customer.id, "investigation", investigation_id="inv_x", tokens_used=4200)

        await close_db()
        get_settings.cache_clear()

        restored = await get_customer(customer_id=customer.id)
        assert restored is not None
        assert restored.email == "durable@test.com"
        assert restored.total_investigations == 1
        assert restored.total_tokens_used == 4200

        restored_trial = await get_trial(trial.id)
        assert restored_trial is not None
        assert restored_trial.tokens_used == 4200
        assert restored_trial.is_active

        events = await get_customer_usage(customer.id)
        assert [e.investigation_id for e in events] == ["inv_x"]

    async def test_lookup_by_email_survives_a_restart(self):
        """The email index was a dict; it is now a UNIQUE constraint + index."""
        from app.customers import create_customer, get_customer

        created, _ = await create_customer("byemail@test.com", "Email Co")

        await close_db()
        get_settings.cache_clear()

        found = await get_customer(email="ByEmail@Test.com")
        assert found is not None
        assert found.id == created.id

    async def test_duplicate_email_is_refused_by_the_database(self):
        """The uniqueness rule is enforced by the schema, not only by a pre-check.

        `create_customer` looks the email up before inserting, but two replicas
        can pass that check at the same instant. The constraint is what actually
        makes "one customer per email" true, so it is asserted directly rather
        than through the code path that avoids it.
        """
        from sqlalchemy.exc import IntegrityError

        from app.customers.manager import _session
        from app.database.models import CRMCustomer

        async with _session() as session:
            session.add(CRMCustomer(id="cust_one", email="clash@test.com", company="A"))
            await session.commit()

        with pytest.raises(IntegrityError):
            async with _session() as session:
                session.add(CRMCustomer(id="cust_two", email="clash@test.com", company="B"))
                await session.commit()

    async def test_module_keeps_no_authoritative_state_in_process(self):
        """Guard against the dicts coming back.

        `_customer_store`, `_email_index`, `_trial_store` and `_usage_store` are
        the four stores that pinned the Helm chart to a single replica. A module
        attribute reintroducing any of them is the regression this catches.
        """
        from app.customers import manager

        assert not [
            name
            for name in vars(manager)
            if name in {"_customer_store", "_email_index", "_trial_store", "_usage_store"}
        ]
        assert not [
            name
            for name, value in vars(manager).items()
            if isinstance(value, (dict, list, set)) and not name.startswith("__")
        ]

    async def test_clear_customers_removes_trials_and_usage_too(self):
        """A partial wipe would leave orphaned usage rows behind."""
        from app.customers import create_customer, create_trial, record_usage, get_customer_stats
        from app.customers.manager import clear_customers, _session
        from app.database.models import CRMTrial, CRMUsageEvent
        from sqlalchemy import func, select

        customer, _ = await create_customer("wipe@test.com", "Wipe Co")
        await create_trial(customer.id)
        await record_usage(customer.id, "investigation", tokens_used=1)

        await clear_customers()

        assert (await get_customer_stats())["total_customers"] == 0
        async with _session() as session:
            assert await session.scalar(select(func.count()).select_from(CRMTrial)) == 0
            assert await session.scalar(select(func.count()).select_from(CRMUsageEvent)) == 0
