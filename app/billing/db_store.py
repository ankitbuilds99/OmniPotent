"""Durable billing storage.

`InMemoryBillingStore` is what every `/billing/*` route used to run against: an
org's subscription, its invoices, its saved cards and its usage counters were
process memory. A restart cancelled everyone's plan, and with more than one
worker a customer's plan flipped between requests depending on which process
answered. Nothing here was ever written to a table — `app/billing/` shipped
without one.

This is the same async interface, backed by the `billing_*` tables, so the
in-memory store stays exactly where it belongs: unit tests.

Every method takes the org explicitly (that is the interface the callers
already use), so isolation is the `org_id` filter on each statement rather than
anything read from the request context.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select, update

from app.billing.models import (
    BillingCycle,
    FeatureOverride,
    Invoice,
    InvoiceStatus,
    MetricType,
    Organization,
    PaymentMethod,
    PricingPlan,
    Subscription,
    SubscriptionStatus,
    UsageRecord,
    UsageSummary,
)
from app.billing.plans import PRICING_PLANS
from app.database.orgs import ensure_org

log = logging.getLogger("omnipotent.billing.db_store")

# The columns `increment_summary_field` is allowed to touch. It takes a field
# name from the caller, and the in-memory version fed that straight into
# setattr() — here it would be a column name interpolated into a statement.
_SUMMARY_FIELDS = frozenset(
    {
        "ai_investigations_count",
        "alerts_processed_count",
        "api_calls_count",
        "postmortems_generated_count",
        "auto_fix_prs_count",
        "similar_searches_count",
        "active_users_count",
        "storage_bytes_used",
    }
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(value: Any) -> Any:
    """Read a timestamp back as UTC-aware.

    SQLite has no timezone type, so a `DateTime(timezone=True)` column round
    trips to a *naive* datetime. `Subscription.is_trialing`,
    `days_until_renewal` and `FeatureOverride.is_valid` all compare against
    `datetime.now(timezone.utc)`, and naive-vs-aware is a TypeError — a 500 on
    GET /billing/subscription under the default database URL.
    """
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def _as_date(value: Any) -> date:
    """SQLite hands back a `date`; a naive round-trip can yield a datetime."""
    return value.date() if isinstance(value, datetime) else value


class DatabaseBillingStore:
    """The billing store the API runs against in production."""

    def __init__(self) -> None:
        # Plans are code, not data: they are defined in app/billing/plans.py and
        # have no per-org state, so they stay in memory.
        self._plans: dict[str, PricingPlan] = dict(PRICING_PLANS)

    # --- organizations --------------------------------------------------------

    async def get_organization(self, org_id: UUID) -> Organization | None:
        from app.database.engine import get_session
        from app.database.models import BillingOrganization

        async with get_session() as session:
            row = (
                await session.execute(
                    select(BillingOrganization).where(BillingOrganization.org_id == org_id)
                )
            ).scalar_one_or_none()
            return self._to_org(row) if row else None

    async def save_organization(self, org: Organization) -> None:
        from app.database.engine import get_session
        from app.database.models import BillingOrganization

        async with get_session() as session:
            await ensure_org(session, org.org_id)
            row = (
                await session.execute(
                    select(BillingOrganization).where(
                        BillingOrganization.org_id == org.org_id
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                row = BillingOrganization(org_id=org.org_id)
                session.add(row)

            row.name = org.name
            row.slug = org.slug
            row.owner_id = org.owner_id
            row.stripe_customer_id = org.stripe_customer_id
            row.billing_email = org.billing_email
            row.tax_id = org.tax_id
            row.billing_address = dict(org.billing_address)
            row.meta = dict(org.metadata)
            row.updated_at = _now()
            await session.commit()

    @staticmethod
    def _to_org(row: Any) -> Organization:
        return Organization(
            org_id=row.org_id,
            name=row.name,
            slug=row.slug,
            owner_id=row.owner_id,
            stripe_customer_id=row.stripe_customer_id,
            billing_email=row.billing_email,
            billing_address=row.billing_address or {},
            tax_id=row.tax_id,
            metadata=row.meta or {},
            created_at=_utc(row.created_at),
            updated_at=_utc(row.updated_at),
        )

    # --- subscriptions --------------------------------------------------------

    async def get_subscription(self, org_id: UUID) -> Subscription | None:
        from app.database.engine import get_session
        from app.database.models import BillingSubscription

        async with get_session() as session:
            row = (
                await session.execute(
                    select(BillingSubscription).where(BillingSubscription.org_id == org_id)
                )
            ).scalar_one_or_none()
            return self._to_subscription(row) if row else None

    async def get_subscription_by_stripe_id(self, stripe_id: str) -> Subscription | None:
        from app.database.engine import get_session
        from app.database.models import BillingSubscription

        if not stripe_id:
            # Every subscription without a Stripe id shares the empty string;
            # matching on it would hand back an arbitrary org's row.
            return None

        async with get_session() as session:
            row = (
                await session.execute(
                    select(BillingSubscription).where(
                        BillingSubscription.stripe_subscription_id == stripe_id
                    )
                )
            ).scalar_one_or_none()
            return self._to_subscription(row) if row else None

    async def save_subscription(self, sub: Subscription) -> None:
        from app.database.engine import get_session
        from app.database.models import BillingSubscription

        async with get_session() as session:
            await ensure_org(session, sub.org_id)
            # Keyed by org, not by subscription id: an org has one subscription,
            # and a plan change reuses the row rather than adding a second.
            row = (
                await session.execute(
                    select(BillingSubscription).where(
                        BillingSubscription.org_id == sub.org_id
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                row = BillingSubscription(
                    subscription_id=sub.subscription_id, org_id=sub.org_id
                )
                session.add(row)

            row.plan_id = sub.plan_id
            row.status = sub.status.value
            row.billing_cycle = sub.billing_cycle.value
            row.stripe_subscription_id = sub.stripe_subscription_id
            row.stripe_customer_id = sub.stripe_customer_id
            row.current_period_start = sub.current_period_start
            row.current_period_end = sub.current_period_end
            row.trial_start = sub.trial_start
            row.trial_end = sub.trial_end
            row.canceled_at = sub.canceled_at
            row.cancel_at_period_end = sub.cancel_at_period_end
            row.cancel_reason = sub.cancel_reason
            row.pause_collection = sub.pause_collection
            row.quantity = sub.quantity
            row.meta = dict(sub.metadata)
            row.updated_at = _now()
            await session.commit()

    @staticmethod
    def _to_subscription(row: Any) -> Subscription:
        return Subscription(
            subscription_id=row.subscription_id,
            org_id=row.org_id,
            plan_id=row.plan_id,
            status=SubscriptionStatus(row.status),
            billing_cycle=BillingCycle(row.billing_cycle),
            stripe_subscription_id=row.stripe_subscription_id,
            stripe_customer_id=row.stripe_customer_id,
            current_period_start=_utc(row.current_period_start),
            current_period_end=_utc(row.current_period_end),
            trial_start=_utc(row.trial_start),
            trial_end=_utc(row.trial_end),
            canceled_at=_utc(row.canceled_at),
            cancel_at_period_end=row.cancel_at_period_end,
            cancel_reason=row.cancel_reason,
            pause_collection=row.pause_collection,
            quantity=row.quantity,
            metadata=row.meta or {},
            created_at=_utc(row.created_at),
            updated_at=_utc(row.updated_at),
        )

    # --- plans ----------------------------------------------------------------

    async def get_plan(self, plan_id: UUID) -> PricingPlan | None:
        for plan in self._plans.values():
            if plan.plan_id == plan_id:
                return plan
        return None

    async def get_plan_by_name(self, name: str) -> PricingPlan | None:
        return self._plans.get(name)

    async def get_all_plans(self) -> list[PricingPlan]:
        return list(self._plans.values())

    # --- invoices -------------------------------------------------------------

    async def save_invoice(self, invoice: Invoice) -> None:
        from app.database.engine import get_session
        from app.database.models import BillingInvoice

        async with get_session() as session:
            await ensure_org(session, invoice.org_id)
            row = (
                await session.execute(
                    select(BillingInvoice).where(
                        BillingInvoice.invoice_id == invoice.invoice_id
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                row = BillingInvoice(
                    invoice_id=invoice.invoice_id,
                    org_id=invoice.org_id,
                    created_at=invoice.created_at,
                )
                session.add(row)

            row.subscription_id = invoice.subscription_id
            row.stripe_invoice_id = invoice.stripe_invoice_id
            row.invoice_number = invoice.invoice_number
            row.amount_cents = invoice.amount_cents
            row.amount_paid_cents = invoice.amount_paid_cents
            row.currency = invoice.currency
            row.status = invoice.status.value
            row.invoice_pdf_url = invoice.invoice_pdf_url
            row.hosted_invoice_url = invoice.hosted_invoice_url
            row.due_date = invoice.due_date
            row.paid_at = invoice.paid_at
            row.period_start = invoice.period_start
            row.period_end = invoice.period_end
            row.line_items = list(invoice.line_items)
            row.meta = dict(invoice.metadata)
            await session.commit()

    async def get_invoices(self, org_id: UUID, limit: int = 10) -> list[Invoice]:
        from app.database.engine import get_session
        from app.database.models import BillingInvoice

        async with get_session() as session:
            rows = (
                (
                    await session.execute(
                        select(BillingInvoice)
                        .where(BillingInvoice.org_id == org_id)
                        .order_by(BillingInvoice.created_at.desc())
                        .limit(max(1, limit))
                    )
                )
                .scalars()
                .all()
            )
            return [self._to_invoice(row) for row in rows]

    @staticmethod
    def _to_invoice(row: Any) -> Invoice:
        return Invoice(
            invoice_id=row.invoice_id,
            org_id=row.org_id,
            subscription_id=row.subscription_id,
            stripe_invoice_id=row.stripe_invoice_id,
            invoice_number=row.invoice_number,
            amount_cents=row.amount_cents,
            amount_paid_cents=row.amount_paid_cents,
            currency=row.currency,
            status=InvoiceStatus(row.status),
            invoice_pdf_url=row.invoice_pdf_url,
            hosted_invoice_url=row.hosted_invoice_url,
            due_date=_utc(row.due_date),
            paid_at=_utc(row.paid_at),
            period_start=_utc(row.period_start),
            period_end=_utc(row.period_end),
            line_items=row.line_items or [],
            metadata=row.meta or {},
            created_at=_utc(row.created_at),
        )

    # --- payment methods ------------------------------------------------------

    async def get_payment_methods(self, org_id: UUID) -> list[PaymentMethod]:
        from app.database.engine import get_session
        from app.database.models import BillingPaymentMethod

        async with get_session() as session:
            rows = (
                (
                    await session.execute(
                        select(BillingPaymentMethod)
                        .where(BillingPaymentMethod.org_id == org_id)
                        .order_by(BillingPaymentMethod.created_at)
                    )
                )
                .scalars()
                .all()
            )
            return [self._to_payment_method(row) for row in rows]

    async def save_payment_method(self, pm: PaymentMethod) -> None:
        from app.database.engine import get_session
        from app.database.models import BillingPaymentMethod

        async with get_session() as session:
            await ensure_org(session, pm.org_id)
            row = (
                await session.execute(
                    select(BillingPaymentMethod).where(
                        BillingPaymentMethod.payment_method_id == pm.payment_method_id
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                row = BillingPaymentMethod(
                    payment_method_id=pm.payment_method_id,
                    org_id=pm.org_id,
                    created_at=pm.created_at,
                )
                session.add(row)

            row.stripe_payment_method_id = pm.stripe_payment_method_id
            row.type = pm.type
            row.card_brand = pm.card_brand
            row.card_last4 = pm.card_last4
            row.card_exp_month = pm.card_exp_month
            row.card_exp_year = pm.card_exp_year
            row.is_default = pm.is_default
            row.billing_details = dict(pm.billing_details)
            await session.commit()

    async def delete_payment_method(self, pm_id: UUID) -> None:
        from app.database.engine import get_session
        from app.database.models import BillingPaymentMethod

        async with get_session() as session:
            row = (
                await session.execute(
                    select(BillingPaymentMethod).where(
                        BillingPaymentMethod.payment_method_id == pm_id
                    )
                )
            ).scalar_one_or_none()
            if row is not None:
                await session.delete(row)
                await session.commit()

    @staticmethod
    def _to_payment_method(row: Any) -> PaymentMethod:
        return PaymentMethod(
            payment_method_id=row.payment_method_id,
            org_id=row.org_id,
            stripe_payment_method_id=row.stripe_payment_method_id,
            type=row.type,
            card_brand=row.card_brand,
            card_last4=row.card_last4,
            card_exp_month=row.card_exp_month,
            card_exp_year=row.card_exp_year,
            is_default=row.is_default,
            billing_details=row.billing_details or {},
            created_at=_utc(row.created_at),
        )

    # --- usage ----------------------------------------------------------------

    async def create_usage_record(self, record: UsageRecord) -> None:
        from app.database.engine import get_session
        from app.database.models import BillingUsageRecord

        async with get_session() as session:
            await ensure_org(session, record.org_id)
            session.add(
                BillingUsageRecord(
                    record_id=record.record_id,
                    org_id=record.org_id,
                    metric_type=record.metric_type.value,
                    quantity=record.quantity,
                    period_start=record.period_start,
                    period_end=record.period_end,
                    meta=dict(record.metadata),
                    created_at=record.created_at,
                )
            )
            await session.commit()

    async def get_usage_records(
        self,
        org_id: UUID,
        metric_type: MetricType | None,
        start_date: date,
        end_date: date,
    ) -> list[UsageRecord]:
        from app.database.engine import get_session
        from app.database.models import BillingUsageRecord

        async with get_session() as session:
            stmt = select(BillingUsageRecord).where(
                BillingUsageRecord.org_id == org_id,
                BillingUsageRecord.period_start >= start_date,
                BillingUsageRecord.period_end <= end_date,
            )
            if metric_type:
                stmt = stmt.where(BillingUsageRecord.metric_type == metric_type.value)

            rows = (
                (await session.execute(stmt.order_by(BillingUsageRecord.created_at)))
                .scalars()
                .all()
            )
            return [
                UsageRecord(
                    record_id=row.record_id,
                    org_id=row.org_id,
                    metric_type=MetricType(row.metric_type),
                    quantity=row.quantity,
                    period_start=_as_date(row.period_start),
                    period_end=_as_date(row.period_end),
                    metadata=row.meta or {},
                    created_at=_utc(row.created_at),
                )
                for row in rows
            ]

    async def get_usage_summary(
        self, org_id: UUID, year_month: str
    ) -> UsageSummary | None:
        from app.database.engine import get_session

        async with get_session() as session:
            row = await self._summary_row(session, org_id, year_month)
            return self._to_summary(row) if row else None

    async def upsert_usage_summary(self, summary: UsageSummary) -> None:
        from app.database.engine import get_session
        from app.database.models import BillingUsageSummary

        async with get_session() as session:
            await ensure_org(session, summary.org_id)
            row = await self._summary_row(session, summary.org_id, summary.year_month)
            if row is None:
                row = BillingUsageSummary(
                    summary_id=summary.summary_id,
                    org_id=summary.org_id,
                    year_month=summary.year_month,
                )
                session.add(row)

            for field in _SUMMARY_FIELDS:
                setattr(row, field, getattr(summary, field))
            row.updated_at = _now()
            await session.commit()

    async def increment_summary_field(
        self, org_id: UUID, year_month: str, field: str, increment: int
    ) -> None:
        """Add to one month's counter, without reading it first.

        The obvious version — load the row, `setattr(row, field, old + n)`,
        commit — is a read-modify-write with an await between the read and the
        write, so two concurrent requests both read the same old value and the
        second overwrites the first. Ten concurrent `+1`s landed as `+2`. The
        in-memory store it replaced had no await in that gap, so making the
        counters durable is exactly what opened the window. These numbers back
        `check_usage_limit` and metered billing: lost increments mean quotas
        that never trip and usage that is never charged.

        `UPDATE ... SET col = col + n` computes the new value in the database,
        so concurrent statements serialize on the row instead of racing. The
        insert only runs when the month has no row yet, and loses a race to
        the unique index on (org_id, year_month) rather than to whoever
        committed last.
        """
        from sqlalchemy.exc import IntegrityError

        from app.database.engine import get_session, rows_affected
        from app.database.models import BillingUsageSummary

        if field not in _SUMMARY_FIELDS:
            raise ValueError(f"not a usage summary field: {field}")

        column = getattr(BillingUsageSummary, field)
        bump = (
            update(BillingUsageSummary)
            .where(
                BillingUsageSummary.org_id == org_id,
                BillingUsageSummary.year_month == year_month,
            )
            .values({column: column + increment, BillingUsageSummary.updated_at: _now()})
        )

        async with get_session() as session:
            await ensure_org(session, org_id)
            result = await session.execute(bump)
            if rows_affected(result):
                await session.commit()
                return

            # No row for this month yet.
            session.add(
                BillingUsageSummary(
                    summary_id=uuid4(),
                    org_id=org_id,
                    year_month=year_month,
                    **{name: (increment if name == field else 0) for name in _SUMMARY_FIELDS},
                    updated_at=_now(),
                )
            )
            try:
                await session.commit()
            except IntegrityError:
                # Another worker created the month between the UPDATE and the
                # INSERT. Its row is there now, so the increment is an update
                # after all — and this one must not be dropped.
                await session.rollback()
                await session.execute(bump)
                await session.commit()

    async def get_usage_history(self, org_id: UUID, months: int) -> list[UsageSummary]:
        from app.database.engine import get_session
        from app.database.models import BillingUsageSummary

        async with get_session() as session:
            rows = (
                (
                    await session.execute(
                        select(BillingUsageSummary)
                        .where(BillingUsageSummary.org_id == org_id)
                        .order_by(BillingUsageSummary.year_month.desc())
                        .limit(max(1, months))
                    )
                )
                .scalars()
                .all()
            )
            return [self._to_summary(row) for row in rows]

    @staticmethod
    async def _summary_row(session: Any, org_id: UUID, year_month: str) -> Any:
        from app.database.models import BillingUsageSummary

        return (
            await session.execute(
                select(BillingUsageSummary).where(
                    BillingUsageSummary.org_id == org_id,
                    BillingUsageSummary.year_month == year_month,
                )
            )
        ).scalar_one_or_none()

    @staticmethod
    def _to_summary(row: Any) -> UsageSummary:
        return UsageSummary(
            summary_id=row.summary_id,
            org_id=row.org_id,
            year_month=row.year_month,
            ai_investigations_count=row.ai_investigations_count,
            alerts_processed_count=row.alerts_processed_count,
            api_calls_count=row.api_calls_count,
            postmortems_generated_count=row.postmortems_generated_count,
            auto_fix_prs_count=row.auto_fix_prs_count,
            similar_searches_count=row.similar_searches_count,
            active_users_count=row.active_users_count,
            storage_bytes_used=row.storage_bytes_used,
            updated_at=_utc(row.updated_at),
        )

    # --- feature overrides ----------------------------------------------------

    async def get_feature_overrides(self, org_id: UUID) -> list[FeatureOverride]:
        from app.database.engine import get_session
        from app.database.models import BillingFeatureOverride

        async with get_session() as session:
            rows = (
                (
                    await session.execute(
                        select(BillingFeatureOverride)
                        .where(BillingFeatureOverride.org_id == org_id)
                        .order_by(BillingFeatureOverride.created_at)
                    )
                )
                .scalars()
                .all()
            )
            return [
                FeatureOverride(
                    override_id=row.override_id,
                    org_id=row.org_id,
                    feature_key=row.feature_key,
                    enabled=row.enabled,
                    limit_value=row.limit_value,
                    expires_at=_utc(row.expires_at),
                    reason=row.reason,
                    created_by=row.created_by,
                    created_at=_utc(row.created_at),
                )
                for row in rows
            ]

    async def save_feature_override(self, override: FeatureOverride) -> None:
        from app.database.engine import get_session
        from app.database.models import BillingFeatureOverride

        async with get_session() as session:
            await ensure_org(session, override.org_id)
            # One override per (org, feature): saving the same key twice replaces
            # the deal rather than stacking two of them.
            row = (
                await session.execute(
                    select(BillingFeatureOverride).where(
                        BillingFeatureOverride.org_id == override.org_id,
                        BillingFeatureOverride.feature_key == override.feature_key,
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                row = BillingFeatureOverride(
                    override_id=override.override_id,
                    org_id=override.org_id,
                    feature_key=override.feature_key,
                    created_at=override.created_at,
                )
                session.add(row)

            row.enabled = override.enabled
            row.limit_value = override.limit_value
            row.expires_at = override.expires_at
            row.reason = override.reason
            row.created_by = override.created_by
            await session.commit()

    async def delete_feature_override(self, org_id: UUID, feature_key: str) -> None:
        from app.database.engine import get_session
        from app.database.models import BillingFeatureOverride

        async with get_session() as session:
            row = (
                await session.execute(
                    select(BillingFeatureOverride).where(
                        BillingFeatureOverride.org_id == org_id,
                        BillingFeatureOverride.feature_key == feature_key,
                    )
                )
            ).scalar_one_or_none()
            if row is not None:
                await session.delete(row)
                await session.commit()
