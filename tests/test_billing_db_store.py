"""Billing survives a restart, and one org's money is not another's.

Every /billing/* route ran against `InMemoryBillingStore`: subscriptions,
invoices, saved cards and usage counters were dicts on a process singleton. A
restart cancelled everyone's plan, and with more than one worker an org's plan
depended on which worker answered. These tests run the same contract against
`DatabaseBillingStore` — the one the router uses now — with a fresh store
instance standing in for a fresh process.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest

import app.config as cfg
from app import tenant_context
from app.billing.db_store import DatabaseBillingStore
from app.billing.models import (
    BillingCycle,
    FeatureOverride,
    Invoice,
    InvoiceStatus,
    MetricType,
    Organization,
    PaymentMethod,
    Subscription,
    SubscriptionStatus,
    UsageRecord,
    UsageSummary,
)
from app.database import engine as db_engine

ORG_A = uuid4()
ORG_B = uuid4()


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("COPILOT_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/app.db")
    cfg.get_settings.cache_clear()
    db_engine._engine = None
    db_engine._session_factory = None
    tenant_context.current_tenant.set(None)

    asyncio.run(db_engine.init_db())
    yield

    asyncio.run(db_engine.close_db())
    cfg.get_settings.cache_clear()
    db_engine._engine = None
    db_engine._session_factory = None
    tenant_context.current_tenant.set(None)


def _run(coro_factory):
    """One call on its own loop, as a separate request would make it."""
    return asyncio.run(coro_factory())


def _subscription(org_id, **kwargs) -> Subscription:
    now = datetime.now(timezone.utc)
    return Subscription(
        org_id=org_id,
        plan_id=kwargs.pop("plan_id", uuid4()),
        status=kwargs.pop("status", SubscriptionStatus.ACTIVE),
        billing_cycle=kwargs.pop("billing_cycle", BillingCycle.MONTHLY),
        current_period_start=now,
        current_period_end=now + timedelta(days=30),
        **kwargs,
    )


def test_a_subscription_outlives_the_process(db):
    sub = _subscription(ORG_A, stripe_subscription_id="sub_123", quantity=4)
    _run(lambda: DatabaseBillingStore().save_subscription(sub))

    # A brand new store object, as a fresh worker would build. The dict-backed
    # store returned None here — the customer's plan was simply gone.
    found = _run(lambda: DatabaseBillingStore().get_subscription(ORG_A))

    assert found is not None
    assert found.subscription_id == sub.subscription_id
    assert found.status is SubscriptionStatus.ACTIVE
    assert found.quantity == 4
    assert found.is_active


def test_one_orgs_subscription_is_invisible_to_another(db):
    store = DatabaseBillingStore()
    _run(lambda: store.save_subscription(_subscription(ORG_A)))

    assert _run(lambda: store.get_subscription(ORG_B)) is None


def test_changing_plan_updates_the_row_rather_than_adding_a_second(db):
    store = DatabaseBillingStore()
    first = _subscription(ORG_A, status=SubscriptionStatus.TRIALING)
    _run(lambda: store.save_subscription(first))

    upgraded = _run(lambda: store.get_subscription(ORG_A))
    upgraded.status = SubscriptionStatus.ACTIVE
    upgraded.billing_cycle = BillingCycle.ANNUAL
    _run(lambda: store.save_subscription(upgraded))

    current = _run(lambda: store.get_subscription(ORG_A))
    assert current.subscription_id == first.subscription_id
    assert current.status is SubscriptionStatus.ACTIVE
    assert current.billing_cycle is BillingCycle.ANNUAL


def test_lookup_by_stripe_id(db):
    """The webhook handler has only the Stripe id to go on."""
    store = DatabaseBillingStore()
    _run(lambda: store.save_subscription(_subscription(ORG_A, stripe_subscription_id="sub_xyz")))

    found = _run(lambda: store.get_subscription_by_stripe_id("sub_xyz"))
    assert found is not None and found.org_id == ORG_A


def test_an_empty_stripe_id_matches_nothing(db):
    """Subscriptions created without Stripe all carry "" — matching on it would
    hand the webhook an arbitrary org's subscription."""
    store = DatabaseBillingStore()
    _run(lambda: store.save_subscription(_subscription(ORG_A)))

    assert _run(lambda: store.get_subscription_by_stripe_id("")) is None


def test_organization_billing_details_round_trip(db):
    store = DatabaseBillingStore()
    org = Organization(
        org_id=ORG_A,
        name="Acme",
        slug="acme",
        stripe_customer_id="cus_1",
        billing_email="ap@acme.test",
        billing_address={"country": "US"},
        metadata={"referral": "hn"},
    )
    _run(lambda: store.save_organization(org))

    found = _run(lambda: DatabaseBillingStore().get_organization(ORG_A))
    assert found.stripe_customer_id == "cus_1"
    assert found.billing_address == {"country": "US"}
    assert found.metadata == {"referral": "hn"}


def test_invoices_are_scoped_and_newest_first(db):
    store = DatabaseBillingStore()
    old = Invoice(
        org_id=ORG_A,
        amount_cents=2000,
        status=InvoiceStatus.PAID,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    new = Invoice(
        org_id=ORG_A,
        amount_cents=9900,
        status=InvoiceStatus.OPEN,
        line_items=[{"description": "team plan", "amount": 9900}],
        created_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
    )
    _run(lambda: store.save_invoice(old))
    _run(lambda: store.save_invoice(new))
    _run(lambda: store.save_invoice(Invoice(org_id=ORG_B, amount_cents=100)))

    mine = _run(lambda: store.get_invoices(ORG_A))
    assert [i.amount_cents for i in mine] == [9900, 2000]
    assert mine[0].line_items == [{"description": "team plan", "amount": 9900}]
    assert mine[0].amount == 99.0
    assert mine[1].is_paid

    assert _run(lambda: store.get_invoices(ORG_A, limit=1))[0].amount_cents == 9900


def test_payment_methods_are_saved_updated_and_deleted(db):
    store = DatabaseBillingStore()
    pm = PaymentMethod(
        org_id=ORG_A, card_brand="visa", card_last4="4242", card_exp_year=2030,
        card_exp_month=6, is_default=True,
    )
    _run(lambda: store.save_payment_method(pm))

    saved = _run(lambda: DatabaseBillingStore().get_payment_methods(ORG_A))
    assert [p.card_last4 for p in saved] == ["4242"]
    assert saved[0].is_default

    # Saving the same id again replaces it rather than duplicating the card.
    pm.card_last4 = "1881"
    _run(lambda: store.save_payment_method(pm))
    assert [p.card_last4 for p in _run(lambda: store.get_payment_methods(ORG_A))] == ["1881"]

    _run(lambda: store.delete_payment_method(pm.payment_method_id))
    assert _run(lambda: store.get_payment_methods(ORG_A)) == []


def test_usage_records_filter_by_metric_and_window(db):
    store = DatabaseBillingStore()
    for metric, day in (
        (MetricType.AI_INVESTIGATION, date(2026, 3, 5)),
        (MetricType.AI_INVESTIGATION, date(2026, 4, 5)),
        (MetricType.API_CALL, date(2026, 3, 6)),
    ):
        _run(
            lambda m=metric, d=day: store.create_usage_record(
                UsageRecord(org_id=ORG_A, metric_type=m, period_start=d, period_end=d)
            )
        )
    _run(
        lambda: store.create_usage_record(
            UsageRecord(org_id=ORG_B, period_start=date(2026, 3, 5), period_end=date(2026, 3, 5))
        )
    )

    march = _run(
        lambda: store.get_usage_records(
            ORG_A, MetricType.AI_INVESTIGATION, date(2026, 3, 1), date(2026, 3, 31)
        )
    )
    assert len(march) == 1
    assert march[0].period_start == date(2026, 3, 5)

    everything = _run(
        lambda: store.get_usage_records(ORG_A, None, date(2026, 1, 1), date(2026, 12, 31))
    )
    assert len(everything) == 3


def test_incrementing_a_summary_creates_it_then_accumulates(db):
    store = DatabaseBillingStore()

    _run(lambda: store.increment_summary_field(ORG_A, "2026-03", "ai_investigations_count", 3))
    _run(lambda: store.increment_summary_field(ORG_A, "2026-03", "ai_investigations_count", 2))

    summary = _run(lambda: DatabaseBillingStore().get_usage_summary(ORG_A, "2026-03"))
    assert summary.ai_investigations_count == 5
    # The other counters default rather than coming back as NULL.
    assert summary.api_calls_count == 0

    # A different month is a different row, and a different org sees neither.
    assert _run(lambda: store.get_usage_summary(ORG_A, "2026-04")) is None
    assert _run(lambda: store.get_usage_summary(ORG_B, "2026-03")) is None


def test_concurrent_increments_are_not_lost(db):
    """Ten requests each adding one must leave ten, not two.

    The counter used to be read into Python, added to, and written back, with
    an `await` in the gap — so two overlapping requests both read the same old
    value and the second overwrote the first. Ten concurrent `+1`s landed as
    `+2`. These counters back `check_usage_limit` and metered billing, so the
    lost ones are quota that never trips and usage nobody is charged for.
    """
    store = DatabaseBillingStore()

    async def hammer():
        # The row does not exist yet: the first arrivals race to create it too.
        await asyncio.gather(
            *(
                store.increment_summary_field(ORG_A, "2026-05", "api_calls_count", 1)
                for _ in range(10)
            )
        )

    asyncio.run(hammer())

    summary = _run(lambda: DatabaseBillingStore().get_usage_summary(ORG_A, "2026-05"))
    assert summary.api_calls_count == 10


def test_concurrent_increments_to_different_counters_do_not_clobber_each_other(db):
    """Two counters on one row, bumped at once, both survive."""
    store = DatabaseBillingStore()

    async def hammer():
        await asyncio.gather(
            *(
                store.increment_summary_field(ORG_A, "2026-05", field, 1)
                for field in ("api_calls_count", "alerts_processed_count")
                for _ in range(5)
            )
        )

    asyncio.run(hammer())

    summary = _run(lambda: DatabaseBillingStore().get_usage_summary(ORG_A, "2026-05"))
    assert summary.api_calls_count == 5
    assert summary.alerts_processed_count == 5


def test_increment_rejects_a_field_that_is_not_a_counter(db):
    """The field name comes from the caller and reaches a column; the in-memory
    version fed it straight to setattr()."""
    store = DatabaseBillingStore()

    with pytest.raises(ValueError):
        _run(lambda: store.increment_summary_field(ORG_A, "2026-03", "org_id", 1))


def test_upserting_a_summary_replaces_the_month(db):
    store = DatabaseBillingStore()
    _run(lambda: store.increment_summary_field(ORG_A, "2026-03", "api_calls_count", 7))

    _run(
        lambda: store.upsert_usage_summary(
            UsageSummary(org_id=ORG_A, year_month="2026-03", api_calls_count=50)
        )
    )

    assert _run(lambda: store.get_usage_summary(ORG_A, "2026-03")).api_calls_count == 50


def test_usage_history_is_newest_first_and_capped(db):
    store = DatabaseBillingStore()
    for month in ("2026-01", "2026-02", "2026-03"):
        _run(lambda m=month: store.increment_summary_field(ORG_A, m, "api_calls_count", 1))

    history = _run(lambda: store.get_usage_history(ORG_A, months=2))
    assert [s.year_month for s in history] == ["2026-03", "2026-02"]
    assert _run(lambda: store.get_usage_history(ORG_B, months=12)) == []


def test_feature_overrides_are_per_org_and_replace_by_key(db):
    store = DatabaseBillingStore()
    _run(
        lambda: store.save_feature_override(
            FeatureOverride(org_id=ORG_A, feature_key="sso", enabled=True, reason="deal")
        )
    )
    # Same key again is the same deal renegotiated, not a second override.
    _run(
        lambda: store.save_feature_override(
            FeatureOverride(org_id=ORG_A, feature_key="sso", enabled=False, reason="lapsed")
        )
    )

    overrides = _run(lambda: DatabaseBillingStore().get_feature_overrides(ORG_A))
    assert len(overrides) == 1
    assert overrides[0].enabled is False
    assert overrides[0].reason == "lapsed"

    assert _run(lambda: store.get_feature_overrides(ORG_B)) == []

    _run(lambda: store.delete_feature_override(ORG_A, "sso"))
    assert _run(lambda: store.get_feature_overrides(ORG_A)) == []


def test_an_expired_override_is_still_stored_but_not_valid(db):
    store = DatabaseBillingStore()
    _run(
        lambda: store.save_feature_override(
            FeatureOverride(
                org_id=ORG_A,
                feature_key="sso",
                expires_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
            )
        )
    )

    override = _run(lambda: store.get_feature_overrides(ORG_A))[0]
    assert override.is_valid is False


def test_plans_are_still_served_from_code(db):
    """Plans have no per-org state — they are defined in app/billing/plans.py."""
    store = DatabaseBillingStore()

    assert _run(lambda: store.get_plan_by_name("team")) is not None
    assert _run(lambda: store.get_all_plans())
    team = _run(lambda: store.get_plan_by_name("team"))
    assert _run(lambda: store.get_plan(team.plan_id)).name == "team"


def test_a_plan_id_means_the_same_thing_in_every_process():
    """The stored plan_id has to survive a restart.

    Plan ids were `uuid4()` evaluated at import. The plan objects are in
    memory, but the subscription's plan_id is a column: it was written with
    one run's random id and, on the next process, matched nothing.
    """
    import subprocess
    import sys

    read_id = "from app.billing.plans import PRICING_PLANS; print(PRICING_PLANS['business'].plan_id)"
    first = subprocess.run(
        [sys.executable, "-c", read_id], capture_output=True, text=True, check=True
    )
    second = subprocess.run(
        [sys.executable, "-c", read_id], capture_output=True, text=True, check=True
    )

    assert first.stdout.strip() == second.stdout.strip()


def test_a_paid_subscription_still_resolves_to_its_plan_after_a_restart(db):
    """The whole point of storing plan_id: reading it back gets the plan.

    A fresh store instance stands in for a fresh process. This returned None
    before — and both `BillingAPI.get_subscription` and
    `FeatureGateService.get_organization_plan` read that as "no plan" and hand
    back the free tier, so a paying customer lost every gated feature.
    """
    from app.billing.plans import PRICING_PLANS

    business = PRICING_PLANS["business"]
    _run(
        lambda: DatabaseBillingStore().save_subscription(
            _subscription(ORG_A, plan_id=business.plan_id)
        )
    )

    restarted = DatabaseBillingStore()
    stored = _run(lambda: restarted.get_subscription(ORG_A))
    plan = _run(lambda: restarted.get_plan(stored.plan_id))

    assert plan is not None
    assert plan.name == "business"
    assert plan.features.sso_saml is True
