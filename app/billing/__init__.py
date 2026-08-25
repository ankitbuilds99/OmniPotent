"""
Advanced Billing and Subscription Management System.

Provides:
- Multi-tier pricing plans with feature gating
- Usage tracking and limits enforcement
- Stripe integration for payments
- Subscription lifecycle management
- Invoice and payment method management
- Usage analytics and forecasting

Components:
- models: Core data models (PricingPlan, Subscription, Invoice, etc.)
- plans: Pricing plan definitions and management
- feature_gate: Feature access control based on plan tier
- usage_tracker: Track and report usage metrics
- stripe_integration: Stripe billing operations
- store: Data storage implementations
- api: API handlers for billing operations
"""

from app.billing.models import (
    PlanTier,
    BillingCycle,
    SubscriptionStatus,
    InvoiceStatus,
    MetricType,
    PlanFeatures,
    PricingPlan,
    Organization,
    Subscription,
    UsageRecord,
    UsageSummary,
    Invoice,
    FeatureOverride,
    PaymentMethod,
)

from app.billing.plans import (
    PRICING_PLANS,
    PLAN_TIER_HIERARCHY,
    FEATURE_TO_MIN_TIER,
    FEATURE_DISPLAY_NAMES,
    PlanManager,
)

from app.billing.feature_gate import (
    FeatureGateService,
    FeatureCheckResult,
    UsageLimitResult,
    require_feature,
    check_usage_limit,
    FeatureGate,
    UsageGate,
)

from app.billing.usage_tracker import (
    UsageTracker,
    UsageReporter,
    UsageStats,
)

from app.billing.stripe_integration import (
    StripeBillingService,
    CheckoutResult,
    PortalResult,
    WebhookResult,
)

from app.billing.webhooks import (
    verify_stripe_signature,
)

from app.billing.store import (
    InMemoryBillingStore,
    FakeStripeClient,
)

from app.billing.db_store import (
    DatabaseBillingStore,
)

from app.billing.api import (
    BillingAPI,
    BillingUnavailableError,
    PlanResponse,
    SubscriptionResponse,
    UnknownMetricError,
    UsageResponse,
)

from app.billing.subscriptions import (
    Plan,
    PlanInterval,
    PLANS,
    SubscriptionManager,
)

from app.billing.webhooks import (
    plan_from_stripe_event,
    UnknownPlan,
    sync_usage_to_stripe,
    get_client,
    StripeClient as LegacyStripeClient,
)

__all__ = [
    # Models
    "PlanTier",
    "BillingCycle",
    "SubscriptionStatus",
    "InvoiceStatus",
    "MetricType",
    "PlanFeatures",
    "PricingPlan",
    "Organization",
    "Subscription",
    "UsageRecord",
    "UsageSummary",
    "Invoice",
    "FeatureOverride",
    "PaymentMethod",
    # Plans
    "PRICING_PLANS",
    "PLAN_TIER_HIERARCHY",
    "FEATURE_TO_MIN_TIER",
    "FEATURE_DISPLAY_NAMES",
    "PlanManager",
    # Feature Gate
    "FeatureGateService",
    "FeatureCheckResult",
    "UsageLimitResult",
    "require_feature",
    "check_usage_limit",
    "FeatureGate",
    "UsageGate",
    # Usage Tracker
    "UsageTracker",
    "UsageReporter",
    "UsageStats",
    # Stripe Integration
    "StripeBillingService",
    "CheckoutResult",
    "PortalResult",
    "WebhookResult",
    "verify_stripe_signature",
    # Store
    "DatabaseBillingStore",
    "InMemoryBillingStore",
    "FakeStripeClient",
    # API
    "BillingAPI",
    "BillingUnavailableError",
    "PlanResponse",
    "SubscriptionResponse",
    "UnknownMetricError",
    "UsageResponse",
    # Legacy (from subscriptions.py)
    "Plan",
    "PlanInterval",
    "PLANS",
    "SubscriptionManager",
    # Legacy (from webhooks.py)
    "plan_from_stripe_event",
    "UnknownPlan",
    "sync_usage_to_stripe",
    "get_client",
    "LegacyStripeClient",
]
