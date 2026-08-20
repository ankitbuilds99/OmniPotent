"""Process-wide billing singletons, shared by every feature that gates on a plan.

Until now the billing store, Stripe service and `BillingAPI` were private module
globals inside `app/api/billing_router.py`. Any other module that wanted to check a
plan flag had to build its own `InMemoryBillingStore`, which would be a *different*
store: subscriptions written through the billing endpoints would be invisible to the
gate, so a customer who had just upgraded would still be refused the feature they
paid for. Keeping one instance here removes that possibility by construction.

`billing_router` now imports these rather than defining its own, so there is exactly
one store per process.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from app import tenant_context
from app.billing.api import BillingAPI
from app.billing.store import InMemoryBillingStore
from app.billing.stripe_integration import StripeBillingService
from app.config import get_settings

log = logging.getLogger("omnipotent.billing.runtime")

__all__ = [
    "current_org_id",
    "enabled_features",
    "get_billing_api",
    "get_billing_store",
    "get_stripe_service",
]

# Org used when no tenant is bound to the request — single-tenant deployments.
SINGLE_TENANT_ORG_ID = UUID("00000000-0000-0000-0000-000000000000")

_BILLING_STORE: InMemoryBillingStore | None = None
_STRIPE_SERVICE: StripeBillingService | None = None
_BILLING_API: BillingAPI | None = None


def get_billing_store() -> InMemoryBillingStore:
    """The one billing store for this process."""
    global _BILLING_STORE
    if _BILLING_STORE is None:
        _BILLING_STORE = InMemoryBillingStore()
    return _BILLING_STORE


def get_stripe_service() -> StripeBillingService | None:
    """The Stripe service, or None when no API key is configured."""
    global _STRIPE_SERVICE
    s = get_settings()
    if _STRIPE_SERVICE is None and s.stripe_api_key:
        try:
            import stripe

            stripe.api_key = s.stripe_api_key
            _STRIPE_SERVICE = StripeBillingService(
                store=get_billing_store(),
                stripe_client=stripe,
                webhook_secret=s.stripe_webhook_secret or "",
                frontend_url=s.frontend_url or "http://localhost:3000",
            )
        except Exception as e:
            log.warning("Failed to initialize Stripe: %s", e)
    return _STRIPE_SERVICE


def get_billing_api() -> BillingAPI:
    """The one BillingAPI for this process."""
    global _BILLING_API
    if _BILLING_API is None:
        _BILLING_API = BillingAPI(
            store=get_billing_store(),
            stripe_service=get_stripe_service(),
        )
    return _BILLING_API


def current_org_id() -> UUID:
    """Resolve the organization for the current request from tenant context."""
    cfg = tenant_context.get_tenant()
    if cfg is None:
        return SINGLE_TENANT_ORG_ID
    return UUID(cfg.org_id)


async def enabled_features(
    feature_keys: list[str],
    org_id: UUID | None = None,
) -> dict[str, bool]:
    """Resolve several plan flags at once for `org_id` (default: current tenant).

    Returns `{feature_key: bool}`. An organization with no active subscription falls
    back to the free Starter plan, so unknown or unsubscribed orgs get the free
    tier's answer rather than an error.

    Gating decisions must not take an endpoint down: if the billing store is
    unreachable this logs and reports every requested flag as enabled. The
    alternative — failing closed — would turn a billing outage into a loss of the
    core alerting path, which is a worse failure than briefly serving a paid layer
    for free. Callers that gate something genuinely restricted (data access, not
    processing depth) should check access explicitly instead of using this helper.
    """
    org = org_id if org_id is not None else current_org_id()
    try:
        status: dict[str, dict[str, Any]] = await get_billing_api().get_feature_status(
            org, feature_keys
        )
    except Exception as exc:
        log.warning(
            "Feature status lookup failed for org %s (%s); treating %s as enabled",
            org,
            exc,
            ", ".join(feature_keys),
        )
        return {key: True for key in feature_keys}

    return {key: bool(status.get(key, {}).get("enabled", False)) for key in feature_keys}
