"""The customer CRM survives a restart, stays inside its org, and cannot invent MRR.

`app/customers/manager.py` was four module-level dicts — `_customer_store`,
`_email_index`, `_trial_store`, `_usage_store`. Every customer, trial and metered
usage event was lost on restart while the endpoints kept returning 200; the dicts
were global, so on a multi-tenant deployment every tenant's
`GET /customers/dashboard` listed every other tenant's customers and revenue; and
`convert_trial` wrote whatever `mrr_cents` the caller sent into the revenue
total, which made `POST /customers/convert` an API for making up MRR.

These tests cover all of it: durability across a simulated restart (including the
per-event usage log an invoice dispute is settled with), an `org_id` filter on
reads and writes, conversion pricing that comes from the catalog, and the
concurrency properties of the billing counters. Every test is async because the
module under test is now database-backed, and each test gets its own throwaway
SQLite database.
"""

from __future__ import annotations

import asyncio

import pytest

import app.config as cfg
from app import tenant_context
from app.customers import (
    CustomerManager,
    CustomerStatus,
    CustomerTier,
    TrialStatus,
    clear_customers,
    convert_trial,
    create_customer,
    create_trial,
    get_active_trials,
    get_customer,
    get_customer_stats,
    get_customer_usage,
    get_trial,
    record_usage,
)
from app.database import engine as db_engine


@pytest.fixture(autouse=True)
async def db(tmp_path, monkeypatch):
    """A database per test, under tmp_path.

    The engine is a module global, so it is dropped on the way in as well as
    out — otherwise a test inherits the previous test's engine, still pointing
    at *that* test's tmp_path. Dropping it also invalidates the recorded schema
    (`ensure_schema` tracks the engine it created one for), and the per-loop
    schema locks go with it: the manager calls `ensure_schema()` on every session, and a lock
    left behind by a torn-down loop must not be handed to this test's loop.
    Disposing on both sides is not tidiness either: aiosqlite connections are
    bound to the event loop that opened them, pytest-asyncio gives each test its
    own loop, and a pooled connection surviving into the next test fails at use
    time rather than at reuse time.
    """
    monkeypatch.setenv("COPILOT_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("COPILOT_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/app.db")
    cfg.get_settings.cache_clear()
    db_engine._engine = None
    db_engine._session_factory = None
    db_engine._schema_locks.clear()
    tenant_context.current_tenant.set(None)

    await db_engine.init_db()
    yield

    await db_engine.close_db()
    cfg.get_settings.cache_clear()
    db_engine._engine = None
    db_engine._session_factory = None
    tenant_context.current_tenant.set(None)


async def _as(org: str, coro_factory):
    """Run one coroutine as `org`, the way a request carrying that tenant would."""
    token = tenant_context.set_tenant(tenant_context.TenantConfig(org_id=org))
    try:
        return await coro_factory()
    finally:
        tenant_context.reset_tenant(token)


async def _restart() -> None:
    """What a process restart looks like to this module.

    The engine is disposed and the settings cache dropped; the next call
    re-creates both from the environment and re-runs `ensure_schema()`.
    """
    await db_engine.close_db()
    cfg.get_settings.cache_clear()


class TestCustomerCreation:
    """Tests for customer creation."""

    async def test_create_customer(self, db):
        customer, message = await create_customer(
            email="cust@startup.io",
            company="Startup Inc",
        )

        assert customer is not None
        assert customer.email == "cust@startup.io"
        assert customer.company == "Startup Inc"
        assert customer.id.startswith("cust_")
        assert "success" in message.lower()

    async def test_create_customer_with_details(self, db):
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

    async def test_duplicate_email_returns_existing(self, db):
        cust1, _ = await create_customer("dup@example.com", "First Co")
        cust2, msg2 = await create_customer("dup@example.com", "Second Co")

        assert cust1.id == cust2.id
        assert "exists" in msg2.lower()

    async def test_invalid_email_rejected(self, db):
        customer, message = await create_customer("invalid", "Test Co")

        assert customer is None
        assert "email" in message.lower()

    async def test_customer_survives_a_restart(self, db):
        """The dict this replaces emptied on every deploy, silently."""
        created, _ = await create_customer("durable@test.com", "Durable Co")

        await db_engine.close_db()
        db_engine._engine = None
        db_engine._session_factory = None
        await db_engine.init_db()

        found = await get_customer(customer_id=created.id)
        assert found is not None
        assert found.company == "Durable Co"


class TestTenantIsolation:
    """One org's CRM is not another org's.

    The dicts were global and keyed only by customer id, so every read returned
    every tenant's rows: contact details, health scores and MRR.
    """

    async def test_one_orgs_customers_are_invisible_to_another(self, db):
        mine, _ = await _as("acme", lambda: create_customer("a@acme.test", "Acme"))
        theirs, _ = await _as("globex", lambda: create_customer("b@globex.test", "Globex"))

        assert await _as("acme", lambda: get_customer(customer_id=theirs.id)) is None
        assert await _as("globex", lambda: get_customer(customer_id=mine.id)) is None

        seen = await _as("acme", lambda: get_customer(customer_id=mine.id))
        assert seen.company == "Acme"

    async def test_stats_do_not_aggregate_across_orgs(self, db):
        acme, _ = await _as("acme", lambda: create_customer("a@acme.test", "Acme"))
        await _as("acme", lambda: create_trial(acme.id))
        await _as(
            "acme",
            lambda: convert_trial(acme.id, tier=CustomerTier.TEAM),
        )
        await _as("globex", lambda: create_customer("b@globex.test", "Globex"))

        acme_stats = await _as("acme", get_customer_stats)
        globex_stats = await _as("globex", get_customer_stats)

        assert acme_stats["total_customers"] == 1
        assert acme_stats["paying_customers"] == 1
        assert globex_stats["total_customers"] == 1
        assert globex_stats["paying_customers"] == 0
        assert globex_stats["mrr_usd"] == 0.0

    async def test_the_same_email_can_belong_to_two_orgs(self, db):
        a, _ = await _as("acme", lambda: create_customer("shared@test.com", "Acme"))
        b, _ = await _as("globex", lambda: create_customer("shared@test.com", "Globex"))

        assert a.id != b.id


class TestTrialManagement:
    """Tests for trial management."""

    async def test_create_trial(self, db):
        customer, _ = await create_customer("trial@test.com", "Trial Co")
        trial, _ = await create_trial(customer.id)

        assert trial is not None
        assert trial.customer_id == customer.id
        assert trial.status == TrialStatus.ACTIVE
        assert trial.days_remaining > 0
        assert trial.investigations_allowed == 10

    async def test_trial_custom_duration(self, db):
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

    async def test_trial_for_nonexistent_customer(self, db):
        trial, message = await create_trial("cust_nonexistent")

        assert trial is None
        assert "not found" in message.lower()

    async def test_active_trial_check(self, db):
        customer, _ = await create_customer("active@test.com", "Active Co")
        await create_trial(customer.id)

        active = await get_active_trials()

        assert len(active) == 1
        assert active[0].customer_id == customer.id

    async def test_expired_trials_are_marked_expired(self, db):
        """Expiry used to happen only if somebody listed the trials.

        A trial nobody looked at stayed `active` forever, including in the
        stats.
        """
        customer, _ = await create_customer("expired@test.com", "Expired Co")
        trial, _ = await create_trial(customer.id, duration_days=-1)

        assert await get_active_trials() == []

        after = await get_trial(trial.id)
        assert after.status == TrialStatus.EXPIRED

    async def test_expired_trial_is_recorded_as_expired_not_recomputed(self, db):
        """A trial past its window must be *persisted* as EXPIRED.

        `get_active_trials` performs the expiry sweep. When that state lived in a
        dict the transition happened once per process and was lost on restart, so
        a trial could resurrect as ACTIVE. The status has to survive the sweep,
        and the read model built from it has to agree with the stored status.
        """
        customer, _ = await create_customer("expired2@test.com", "Expired Co")
        trial, _ = await create_trial(customer.id, duration_days=-1)

        assert await get_active_trials() == []

        stored = await get_trial(trial.id)
        assert stored.status == TrialStatus.EXPIRED
        assert stored.is_active is False
        assert stored.days_remaining == 0

    async def test_trial_usage_tracking(self, db):
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


class TestTrialConversion:
    """Tests for trial to paid conversion."""

    async def test_convert_trial(self, db):
        customer, _ = await create_customer("convert@test.com", "Convert Co")
        await create_trial(customer.id)

        success, _ = await convert_trial(
            customer_id=customer.id,
            tier=CustomerTier.GROWTH,
        )

        assert success

        updated = await get_customer(customer_id=customer.id)
        assert updated.status == CustomerStatus.ACTIVE
        assert updated.tier == CustomerTier.GROWTH
        assert updated.is_paying

    async def test_mrr_comes_from_the_catalog_not_the_caller(self, db):
        """`convert_trial` used to store whatever `mrr_cents` it was handed.

        Anyone who could reach the endpoint could set the dashboard's revenue
        total to any number they liked.
        """
        from app.billing.plans import PRICING_PLANS

        customer, _ = await create_customer("priced@test.com", "Priced Co")
        await convert_trial(customer_id=customer.id, tier=CustomerTier.TEAM)

        updated = await get_customer(customer_id=customer.id)
        assert updated.mrr_cents == PRICING_PLANS["team"].monthly_price_cents

    async def test_annual_conversion_records_the_monthly_equivalent(self, db):
        from app.billing.plans import PRICING_PLANS

        customer, _ = await create_customer("annual@test.com", "Annual Co")
        await convert_trial(customer_id=customer.id, tier=CustomerTier.TEAM, annual=True)

        updated = await get_customer(customer_id=customer.id)
        assert updated.mrr_cents == PRICING_PLANS["team"].annual_price_cents // 12

    async def test_converting_to_a_free_tier_is_rejected(self, db):
        customer, _ = await create_customer("free@test.com", "Free Co")

        success, message = await convert_trial(
            customer_id=customer.id, tier=CustomerTier.STARTER
        )

        assert not success
        assert "no recurring price" in message.lower()

    async def test_enterprise_needs_a_contracted_amount(self, db):
        """Enterprise is quoted per deal, so there is no list price to read."""
        customer, _ = await create_customer("ent@test.com", "Ent Co")

        success, message = await convert_trial(
            customer_id=customer.id, tier=CustomerTier.ENTERPRISE
        )
        assert not success
        assert "contracted_mrr_cents" in message

        success, _ = await convert_trial(
            customer_id=customer.id,
            tier=CustomerTier.ENTERPRISE,
            contracted_mrr_cents=500000,
        )
        assert success
        assert (await get_customer(customer_id=customer.id)).mrr_cents == 500000

    async def test_conversion_closes_the_trial(self, db):
        customer, _ = await create_customer("closed@test.com", "Closed Co")
        trial, _ = await create_trial(customer.id)

        await convert_trial(customer_id=customer.id, tier=CustomerTier.TEAM)

        assert (await get_trial(trial.id)).status == TrialStatus.CONVERTED

    async def test_convert_nonexistent_customer(self, db):
        success, message = await convert_trial(
            customer_id="cust_ghost", tier=CustomerTier.TEAM
        )

        assert not success
        assert "not found" in message.lower()

    async def test_conversion_tags_are_persisted(self, db):
        """Conversion appends tags to a JSON column.

        Pinned because the natural spelling — `customer.tags.append(...)` — does
        not work: SQLAlchemy only marks a JSON column dirty on assignment, so an
        in-place append is silently discarded at commit.
        """
        customer, _ = await create_customer("tags@test.com", "Tag Co", source="landing")
        await convert_trial(customer.id, tier=CustomerTier.TEAM)

        updated = await get_customer(customer_id=customer.id)
        assert updated.tags == ["source:landing", "converted", "tier:team"]


class TestUsageTracking:
    """Tests for customer usage tracking."""

    async def test_record_usage(self, db):
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

    async def test_record_multiple_usage_events(self, db):
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

    async def test_record_usage_nonexistent_customer(self, db):
        success = await record_usage(
            customer_id="cust_ghost",
            event_type="investigation",
        )

        assert not success

    async def test_usage_cannot_be_recorded_against_another_orgs_customer(self, db):
        theirs, _ = await _as("globex", lambda: create_customer("b@globex.test", "Globex"))

        recorded = await _as(
            "acme",
            lambda: record_usage(customer_id=theirs.id, event_type="investigation"),
        )

        assert not recorded

    async def test_usage_events_are_kept_individually_not_just_summed(self, db):
        """The per-event log is what an invoice dispute is settled with.

        The counters on the customer row are a derived total; if only the total
        survived there would be no way to answer "which investigations was I
        charged for".
        """
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

    async def test_usage_log_is_scoped_to_the_calling_org(self, db):
        """The per-event log is a read like any other, so it carries the filter."""
        theirs, _ = await _as("globex", lambda: create_customer("b@globex.test", "Globex"))
        await _as(
            "globex",
            lambda: record_usage(theirs.id, "investigation", investigation_id="inv_g"),
        )

        assert await _as("globex", lambda: get_customer_usage(theirs.id)) != []
        assert await _as("acme", lambda: get_customer_usage(theirs.id)) == []

    async def test_a_stale_snapshot_cannot_clobber_the_billing_counters(self, db):
        """Returned customers are detached read models, not live rows.

        This is the invariant that makes the counters safe to increment in SQL:
        a caller holding an older copy has no write path back to the row, so a
        long-running request cannot roll `total_tokens_used` backwards.
        """
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

    async def test_concurrent_usage_records_do_not_lose_counts(self, db):
        """Ten overlapping usage events must total ten.

        Read-modify-write in Python would lose some of these; the counters are
        incremented with `col = col + n` in SQL precisely so they cannot.
        """
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

    async def test_baseline_health_score(self, db):
        customer, _ = await create_customer("health@test.com", "Health Co")

        assert customer.health_score >= 50

    async def test_health_score_increases_with_usage(self, db):
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

    async def test_empty_stats(self, db):
        stats = await get_customer_stats()

        assert stats["total_customers"] == 0
        assert stats["mrr_usd"] == 0.0

    async def test_stats_with_customers(self, db):
        from app.billing.plans import PRICING_PLANS

        cust1, _ = await create_customer("stat1@test.com", "Stats Co 1")
        cust2, _ = await create_customer("stat2@test.com", "Stats Co 2")

        await create_trial(cust1.id)
        await create_trial(cust2.id)

        await convert_trial(cust1.id, tier=CustomerTier.TEAM)

        stats = await get_customer_stats()

        assert stats["total_customers"] == 2
        assert stats["total_trials"] == 2
        assert stats["paying_customers"] == 1
        assert stats["mrr_usd"] == PRICING_PLANS["team"].monthly_price_cents / 100
        assert stats["by_tier"] == {"starter": 1, "team": 1}
        assert stats["conversion_rate"] == 0.5


class TestCustomerManager:
    """Tests for CustomerManager class."""

    async def test_manager_create_with_trial(self, db):
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

    async def test_manager_create_without_trial(self, db):
        manager = CustomerManager()
        result = await manager.create(
            email="notrial@test.com",
            company="No Trial Co",
            start_trial=False,
        )

        assert result["success"]
        assert "customer" in result
        assert "trial" not in result

    async def test_manager_dashboard(self, db):
        customer, _ = await create_customer("dash@test.com", "Dashboard Co")
        await create_trial(customer.id)

        manager = CustomerManager()
        dashboard = await manager.get_dashboard()

        assert "stats" in dashboard
        assert "active_trials" in dashboard
        assert "recent_customers" in dashboard
        assert len(dashboard["active_trials"]) == 1

    async def test_manager_dashboard_is_scoped_to_the_calling_org(self, db):
        await _as("acme", lambda: create_customer("a@acme.test", "Acme"))
        await _as("globex", lambda: create_customer("b@globex.test", "Globex"))

        dashboard = await _as("acme", CustomerManager().get_dashboard)

        companies = [c["company"] for c in dashboard["recent_customers"]]
        assert companies == ["Acme"]


class TestPersistence:
    """The reason this module was rewritten: state must outlive the process."""

    async def test_customers_and_usage_survive_a_restart(self, db):
        """Everything a bill is computed from must still be there after a restart.

        Before the move to the database this test could not pass by construction:
        `_customer_store` was a dict, so the customer, the trial and every usage
        record died with the process — and with a replica count above 1, died on
        every scale-down too.
        """
        customer, _ = await create_customer("durable@test.com", "Durable Co")
        trial, _ = await create_trial(customer.id)
        await record_usage(
            customer.id, "investigation", investigation_id="inv_x", tokens_used=4200
        )

        await _restart()

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

    async def test_lookup_by_email_survives_a_restart(self, db):
        """The email index was a dict; it is now a UNIQUE constraint + index."""
        created, _ = await create_customer("byemail@test.com", "Email Co")

        await _restart()

        found = await get_customer(email="ByEmail@Test.com")
        assert found is not None
        assert found.id == created.id

    async def test_duplicate_email_is_refused_by_the_database(self, db):
        """The uniqueness rule is enforced by the schema, not only by a pre-check.

        `create_customer` looks the email up before inserting, but two replicas
        can pass that check at the same instant. The constraint — unique on
        (org_id, email) — is what actually makes "one customer per email per
        org" true, so it is asserted directly rather than through the code path
        that avoids it.
        """
        from sqlalchemy.exc import IntegrityError

        from app.customers.manager import _session
        from app.database.growth_models import CustomerDB
        from app.database.orgs import org_uuid

        await create_customer("clash@test.com", "A")
        org = org_uuid()

        with pytest.raises(IntegrityError):
            async with _session() as session:
                session.add(
                    CustomerDB(
                        id="cust_two",
                        org_id=org,
                        email="clash@test.com",
                        company="B",
                    )
                )
                await session.commit()

    async def test_module_keeps_no_authoritative_state_in_process(self, db):
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

    async def test_clear_customers_removes_trials_and_usage_too(self, db):
        """A partial wipe would leave orphaned usage rows behind."""
        from sqlalchemy import func, select

        from app.customers.manager import _session
        from app.database.growth_models import CustomerUsageRecordDB, TrialDB

        customer, _ = await create_customer("wipe@test.com", "Wipe Co")
        await create_trial(customer.id)
        await record_usage(customer.id, "investigation", tokens_used=1)

        await clear_customers()

        assert (await get_customer_stats())["total_customers"] == 0
        async with _session() as session:
            assert (
                await session.scalar(select(func.count()).select_from(TrialDB))
            ) == 0
            assert (
                await session.scalar(
                    select(func.count()).select_from(CustomerUsageRecordDB)
                )
            ) == 0
