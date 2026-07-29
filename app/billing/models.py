"""Billing data models - pricing plans, subscriptions, usage tracking, invoices."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta, timezone
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

log = logging.getLogger("devcopilot.billing.models")


class PlanTier(str, Enum):
    """Plan tier hierarchy for feature gating."""

    COMMUNITY = "community"
    STARTER = "starter"
    TEAM = "team"
    GROWTH = "growth"
    BUSINESS = "business"
    SCALE = "scale"
    ENTERPRISE = "enterprise"


class BillingCycle(str, Enum):
    """Billing cycle options."""

    MONTHLY = "monthly"
    ANNUAL = "annual"


class SubscriptionStatus(str, Enum):
    """Subscription status states."""

    TRIALING = "trialing"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELED = "canceled"
    PAUSED = "paused"
    UNPAID = "unpaid"
    INCOMPLETE = "incomplete"


class InvoiceStatus(str, Enum):
    """Invoice status states."""

    DRAFT = "draft"
    OPEN = "open"
    PAID = "paid"
    VOID = "void"
    UNCOLLECTIBLE = "uncollectible"


class MetricType(str, Enum):
    """Usage metric types."""

    AI_INVESTIGATION = "ai_investigation"
    ALERT_PROCESSED = "alert_processed"
    API_CALL = "api_call"
    POSTMORTEM_GENERATED = "postmortem_generated"
    AUTO_FIX_PR = "auto_fix_pr"
    SIMILAR_SEARCH = "similar_search"
    STORAGE_BYTES = "storage_bytes"


@dataclass
class PlanFeatures:
    """Feature flags and limits for a pricing plan."""

    ai_investigation: bool = True
    slack_integration: bool = True
    basic_deduplication: bool = True
    github_integration: bool = False
    multi_llm: bool = False
    auto_fix_pr: bool = False
    ai_postmortem: bool = False
    similar_incident_search: bool = False
    advanced_deduplication: bool = False
    mtta_mttr_dashboard: bool = False
    api_access: bool = False
    cascade_detection: bool = False
    service_dependency_map: bool = False
    pattern_detection: bool = False
    scheduled_reports: bool = False
    sso_saml: bool = False
    rbac: bool = False
    audit_logs: bool = False
    soc2_compliance: bool = False
    custom_dashboards: bool = False
    servicenow_integration: bool = False
    priority_support: bool = False
    self_hosted: bool = False
    dedicated_support_manager: bool = False
    quarterly_business_reviews: bool = False
    dedicated_infrastructure: bool = False
    hipaa_compliance: bool = False
    air_gapped_deployment: bool = False
    custom_data_residency: bool = False
    bring_your_own_llm: bool = False
    custom_sla: bool = False
    phone_support_24_7: bool = False
    onsite_training: bool = False

    limit_history_days: int | None = 14
    limit_llm_providers: int | None = 1
    limit_integrations: int | None = 2
    limit_support_response_hours: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON storage."""
        return {
            "ai_investigation": self.ai_investigation,
            "slack_integration": self.slack_integration,
            "basic_deduplication": self.basic_deduplication,
            "github_integration": self.github_integration,
            "multi_llm": self.multi_llm,
            "auto_fix_pr": self.auto_fix_pr,
            "ai_postmortem": self.ai_postmortem,
            "similar_incident_search": self.similar_incident_search,
            "advanced_deduplication": self.advanced_deduplication,
            "mtta_mttr_dashboard": self.mtta_mttr_dashboard,
            "api_access": self.api_access,
            "cascade_detection": self.cascade_detection,
            "service_dependency_map": self.service_dependency_map,
            "pattern_detection": self.pattern_detection,
            "scheduled_reports": self.scheduled_reports,
            "sso_saml": self.sso_saml,
            "rbac": self.rbac,
            "audit_logs": self.audit_logs,
            "soc2_compliance": self.soc2_compliance,
            "custom_dashboards": self.custom_dashboards,
            "servicenow_integration": self.servicenow_integration,
            "priority_support": self.priority_support,
            "self_hosted": self.self_hosted,
            "dedicated_support_manager": self.dedicated_support_manager,
            "quarterly_business_reviews": self.quarterly_business_reviews,
            "dedicated_infrastructure": self.dedicated_infrastructure,
            "hipaa_compliance": self.hipaa_compliance,
            "air_gapped_deployment": self.air_gapped_deployment,
            "custom_data_residency": self.custom_data_residency,
            "bring_your_own_llm": self.bring_your_own_llm,
            "custom_sla": self.custom_sla,
            "phone_support_24_7": self.phone_support_24_7,
            "onsite_training": self.onsite_training,
            "limit_history_days": self.limit_history_days,
            "limit_llm_providers": self.limit_llm_providers,
            "limit_integrations": self.limit_integrations,
            "limit_support_response_hours": self.limit_support_response_hours,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlanFeatures:
        """Create from dictionary."""
        return cls(
            ai_investigation=data.get("ai_investigation", True),
            slack_integration=data.get("slack_integration", True),
            basic_deduplication=data.get("basic_deduplication", True),
            github_integration=data.get("github_integration", False),
            multi_llm=data.get("multi_llm", False),
            auto_fix_pr=data.get("auto_fix_pr", False),
            ai_postmortem=data.get("ai_postmortem", False),
            similar_incident_search=data.get("similar_incident_search", False),
            advanced_deduplication=data.get("advanced_deduplication", False),
            mtta_mttr_dashboard=data.get("mtta_mttr_dashboard", False),
            api_access=data.get("api_access", False),
            cascade_detection=data.get("cascade_detection", False),
            service_dependency_map=data.get("service_dependency_map", False),
            pattern_detection=data.get("pattern_detection", False),
            scheduled_reports=data.get("scheduled_reports", False),
            sso_saml=data.get("sso_saml", False),
            rbac=data.get("rbac", False),
            audit_logs=data.get("audit_logs", False),
            soc2_compliance=data.get("soc2_compliance", False),
            custom_dashboards=data.get("custom_dashboards", False),
            servicenow_integration=data.get("servicenow_integration", False),
            priority_support=data.get("priority_support", False),
            self_hosted=data.get("self_hosted", False),
            dedicated_support_manager=data.get("dedicated_support_manager", False),
            quarterly_business_reviews=data.get("quarterly_business_reviews", False),
            dedicated_infrastructure=data.get("dedicated_infrastructure", False),
            hipaa_compliance=data.get("hipaa_compliance", False),
            air_gapped_deployment=data.get("air_gapped_deployment", False),
            custom_data_residency=data.get("custom_data_residency", False),
            bring_your_own_llm=data.get("bring_your_own_llm", False),
            custom_sla=data.get("custom_sla", False),
            phone_support_24_7=data.get("phone_support_24_7", False),
            onsite_training=data.get("onsite_training", False),
            limit_history_days=data.get("limit_history_days", 14),
            limit_llm_providers=data.get("limit_llm_providers", 1),
            limit_integrations=data.get("limit_integrations", 2),
            limit_support_response_hours=data.get("limit_support_response_hours"),
        )

    def has_feature(self, feature_key: str) -> bool:
        """Check if a feature is enabled."""
        return getattr(self, feature_key, False)

    def get_limit(self, limit_key: str) -> int | None:
        """Get a limit value."""
        key = f"limit_{limit_key}" if not limit_key.startswith("limit_") else limit_key
        return getattr(self, key, None)


@dataclass
class PricingPlan:
    """Pricing plan definition."""

    plan_id: UUID = field(default_factory=uuid4)
    name: str = ""
    display_name: str = ""
    description: str = ""
    tier: PlanTier = PlanTier.COMMUNITY
    monthly_price_cents: int = 0
    annual_price_cents: int = 0
    max_users: int = 2
    max_ai_investigations_monthly: int | None = 25
    max_alerts_monthly: int | None = 250
    features: PlanFeatures = field(default_factory=PlanFeatures)
    stripe_monthly_price_id: str = ""
    stripe_annual_price_id: str = ""
    stripe_product_id: str = ""
    is_active: bool = True
    is_public: bool = True
    sort_order: int = 0
    trial_days: int = 14
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def monthly_price(self) -> float:
        """Get monthly price in dollars."""
        return self.monthly_price_cents / 100

    @property
    def annual_price(self) -> float:
        """Get annual price in dollars."""
        return self.annual_price_cents / 100

    @property
    def annual_monthly_price(self) -> float:
        """Get monthly price when billed annually."""
        return (self.annual_price_cents / 12) / 100

    @property
    def annual_savings_percent(self) -> float:
        """Calculate annual savings percentage."""
        if self.monthly_price_cents == 0:
            return 0.0
        yearly_monthly = self.monthly_price_cents * 12
        if yearly_monthly == 0:
            return 0.0
        return ((yearly_monthly - self.annual_price_cents) / yearly_monthly) * 100

    @property
    def is_unlimited_users(self) -> bool:
        """Check if users are unlimited."""
        return self.max_users >= 999999

    @property
    def is_unlimited_investigations(self) -> bool:
        """Check if investigations are unlimited."""
        return self.max_ai_investigations_monthly is None

    @property
    def is_unlimited_alerts(self) -> bool:
        """Check if alerts are unlimited."""
        return self.max_alerts_monthly is None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "plan_id": str(self.plan_id),
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "tier": self.tier.value,
            "monthly_price_cents": self.monthly_price_cents,
            "annual_price_cents": self.annual_price_cents,
            "monthly_price": self.monthly_price,
            "annual_price": self.annual_price,
            "annual_monthly_price": self.annual_monthly_price,
            "annual_savings_percent": round(self.annual_savings_percent, 1),
            "max_users": self.max_users,
            "max_ai_investigations_monthly": self.max_ai_investigations_monthly,
            "max_alerts_monthly": self.max_alerts_monthly,
            "features": self.features.to_dict(),
            "is_active": self.is_active,
            "is_public": self.is_public,
            "sort_order": self.sort_order,
            "trial_days": self.trial_days,
        }


@dataclass
class Organization:
    """Organization with billing information."""

    org_id: UUID = field(default_factory=uuid4)
    name: str = ""
    slug: str = ""
    owner_id: UUID | None = None
    stripe_customer_id: str = ""
    billing_email: str = ""
    billing_address: dict[str, Any] = field(default_factory=dict)
    tax_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "org_id": str(self.org_id),
            "name": self.name,
            "slug": self.slug,
            "stripe_customer_id": self.stripe_customer_id,
            "billing_email": self.billing_email,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class Subscription:
    """Customer subscription state."""

    subscription_id: UUID = field(default_factory=uuid4)
    org_id: UUID = field(default_factory=uuid4)
    plan_id: UUID = field(default_factory=uuid4)
    status: SubscriptionStatus = SubscriptionStatus.TRIALING
    billing_cycle: BillingCycle = BillingCycle.MONTHLY
    stripe_subscription_id: str = ""
    stripe_customer_id: str = ""
    current_period_start: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    current_period_end: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc) + timedelta(days=30)
    )
    trial_start: datetime | None = None
    trial_end: datetime | None = None
    canceled_at: datetime | None = None
    cancel_at_period_end: bool = False
    cancel_reason: str = ""
    pause_collection: dict[str, Any] | None = None
    quantity: int = 1
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def is_active(self) -> bool:
        """Check if subscription is active."""
        return self.status in (SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIALING)

    @property
    def is_trialing(self) -> bool:
        """Check if in trial period."""
        if self.status != SubscriptionStatus.TRIALING:
            return False
        if self.trial_end and self.trial_end > datetime.now(timezone.utc):
            return True
        return False

    @property
    def days_until_renewal(self) -> int:
        """Days until subscription renews."""
        delta = self.current_period_end - datetime.now(timezone.utc)
        return max(0, delta.days)

    @property
    def days_remaining_trial(self) -> int | None:
        """Days remaining in trial."""
        if not self.is_trialing or not self.trial_end:
            return None
        delta = self.trial_end - datetime.now(timezone.utc)
        return max(0, delta.days)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "subscription_id": str(self.subscription_id),
            "org_id": str(self.org_id),
            "plan_id": str(self.plan_id),
            "status": self.status.value,
            "billing_cycle": self.billing_cycle.value,
            "is_active": self.is_active,
            "is_trialing": self.is_trialing,
            "current_period_start": self.current_period_start.isoformat(),
            "current_period_end": self.current_period_end.isoformat(),
            "days_until_renewal": self.days_until_renewal,
            "days_remaining_trial": self.days_remaining_trial,
            "cancel_at_period_end": self.cancel_at_period_end,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class UsageRecord:
    """Individual usage event record."""

    record_id: UUID = field(default_factory=uuid4)
    org_id: UUID = field(default_factory=uuid4)
    metric_type: MetricType = MetricType.AI_INVESTIGATION
    quantity: int = 1
    period_start: date = field(default_factory=date.today)
    period_end: date = field(default_factory=date.today)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "record_id": str(self.record_id),
            "org_id": str(self.org_id),
            "metric_type": self.metric_type.value,
            "quantity": self.quantity,
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class UsageSummary:
    """Monthly usage summary for quick lookups."""

    summary_id: UUID = field(default_factory=uuid4)
    org_id: UUID = field(default_factory=uuid4)
    year_month: str = ""
    ai_investigations_count: int = 0
    alerts_processed_count: int = 0
    api_calls_count: int = 0
    postmortems_generated_count: int = 0
    auto_fix_prs_count: int = 0
    similar_searches_count: int = 0
    active_users_count: int = 0
    storage_bytes_used: int = 0
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "summary_id": str(self.summary_id),
            "org_id": str(self.org_id),
            "year_month": self.year_month,
            "ai_investigations_count": self.ai_investigations_count,
            "alerts_processed_count": self.alerts_processed_count,
            "api_calls_count": self.api_calls_count,
            "postmortems_generated_count": self.postmortems_generated_count,
            "auto_fix_prs_count": self.auto_fix_prs_count,
            "similar_searches_count": self.similar_searches_count,
            "active_users_count": self.active_users_count,
            "storage_bytes_used": self.storage_bytes_used,
        }


@dataclass
class Invoice:
    """Invoice record."""

    invoice_id: UUID = field(default_factory=uuid4)
    org_id: UUID = field(default_factory=uuid4)
    subscription_id: UUID | None = None
    stripe_invoice_id: str = ""
    invoice_number: str = ""
    amount_cents: int = 0
    amount_paid_cents: int = 0
    currency: str = "usd"
    status: InvoiceStatus = InvoiceStatus.DRAFT
    invoice_pdf_url: str = ""
    hosted_invoice_url: str = ""
    due_date: datetime | None = None
    paid_at: datetime | None = None
    period_start: datetime | None = None
    period_end: datetime | None = None
    line_items: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def amount(self) -> float:
        """Get amount in dollars."""
        return self.amount_cents / 100

    @property
    def amount_paid(self) -> float:
        """Get amount paid in dollars."""
        return self.amount_paid_cents / 100

    @property
    def is_paid(self) -> bool:
        """Check if invoice is paid."""
        return self.status == InvoiceStatus.PAID

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "invoice_id": str(self.invoice_id),
            "org_id": str(self.org_id),
            "stripe_invoice_id": self.stripe_invoice_id,
            "invoice_number": self.invoice_number,
            "amount": self.amount,
            "amount_paid": self.amount_paid,
            "currency": self.currency,
            "status": self.status.value,
            "invoice_pdf_url": self.invoice_pdf_url,
            "hosted_invoice_url": self.hosted_invoice_url,
            "due_date": self.due_date.isoformat() if self.due_date else None,
            "paid_at": self.paid_at.isoformat() if self.paid_at else None,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class FeatureOverride:
    """Feature override for custom deals."""

    override_id: UUID = field(default_factory=uuid4)
    org_id: UUID = field(default_factory=uuid4)
    feature_key: str = ""
    enabled: bool = True
    limit_value: int | None = None
    expires_at: datetime | None = None
    reason: str = ""
    created_by: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def is_valid(self) -> bool:
        """Check if override is still valid."""
        if self.expires_at and self.expires_at < datetime.now(timezone.utc):
            return False
        return True

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "override_id": str(self.override_id),
            "org_id": str(self.org_id),
            "feature_key": self.feature_key,
            "enabled": self.enabled,
            "limit_value": self.limit_value,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "reason": self.reason,
            "is_valid": self.is_valid,
        }


@dataclass
class PaymentMethod:
    """Payment method information."""

    payment_method_id: UUID = field(default_factory=uuid4)
    org_id: UUID = field(default_factory=uuid4)
    stripe_payment_method_id: str = ""
    type: str = "card"
    card_brand: str = ""
    card_last4: str = ""
    card_exp_month: int = 0
    card_exp_year: int = 0
    is_default: bool = False
    billing_details: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def is_expired(self) -> bool:
        """Check if card is expired."""
        now = datetime.now(timezone.utc)
        if self.card_exp_year < now.year:
            return True
        if self.card_exp_year == now.year and self.card_exp_month < now.month:
            return True
        return False

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary (safe for frontend)."""
        return {
            "payment_method_id": str(self.payment_method_id),
            "type": self.type,
            "card_brand": self.card_brand,
            "card_last4": self.card_last4,
            "card_exp_month": self.card_exp_month,
            "card_exp_year": self.card_exp_year,
            "is_default": self.is_default,
            "is_expired": self.is_expired,
        }
