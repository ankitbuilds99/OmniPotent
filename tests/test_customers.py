"""The customer CRM survives a restart, stays inside its org, and cannot invent MRR.

`app/customers/manager.py` was four module-level dicts. Every customer, trial and
metered usage event was lost on restart while the endpoints kept returning 200;
the dicts were global, so on a multi-tenant deployment every tenant's
`GET /customers/dashboard` listed every other tenant's customers and revenue; and
`convert_trial` wrote whatever `mrr_cents` the caller sent into the revenue
total, which made `POST /customers/convert` an API for making up MRR.

These tests cover all three: durability across a simulated restart, an `org_id`
filter on reads and writes, and conversion pricing that comes from the catalog.
"""

from __future__ import annotations

import pytest

import app.config as cfg
from app import tenant_context
from app.customers import (
    CustomerManager,
    CustomerStatus,
    CustomerTier,
    TrialStatus,
    convert_trial,
    create_customer,
    create_trial,
    get_active_trials,
    get_customer,
    get_customer_stats,
    get_trial,
    record_usage,
)
from app.database import engine as db_engine


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


async def _as(org: str, coro_factory):
    """Run one coroutine as `org`, the way a request carrying that tenant would."""
    token = tenant_context.set_tenant(tenant_context.TenantConfig(org_id=org))
    try:
        return await coro_factory()
    finally:
        tenant_context.reset_tenant(token)


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
