"""Billing API router - exposes billing endpoints for the frontend."""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.billing import (
    BillingAPI,
    DatabaseBillingStore,
    StripeBillingService,
)
from app.billing.api import BillingUnavailableError
from app.config import get_settings
from app.database.orgs import org_uuid

log = logging.getLogger("omnipotent.api.billing")

router = APIRouter(prefix="/billing", tags=["Billing"])


def _unavailable(exc: BillingUnavailableError) -> HTTPException:
    """Map an unconfigured-billing failure to 503 (never a silent success)."""
    return HTTPException(status_code=503, detail=str(exc))

# Billing service singletons (initialized lazily). The store holds no state of
# its own — every row it reads and writes is in the `billing_*` tables — so one
# instance for the process is enough.
_BILLING_STORE: DatabaseBillingStore | None = None
_STRIPE_SERVICE: StripeBillingService | None = None
_BILLING_API: BillingAPI | None = None


def _get_store() -> DatabaseBillingStore:
    global _BILLING_STORE
    if _BILLING_STORE is None:
        _BILLING_STORE = DatabaseBillingStore()
    return _BILLING_STORE


def _get_stripe_service() -> StripeBillingService | None:
    global _STRIPE_SERVICE
    s = get_settings()
    if _STRIPE_SERVICE is None and s.stripe_api_key:
        try:
            import stripe
            stripe.api_key = s.stripe_api_key
            _STRIPE_SERVICE = StripeBillingService(
                store=_get_store(),
                stripe_client=stripe,
                webhook_secret=s.stripe_webhook_secret or "",
                frontend_url=s.frontend_url or "http://localhost:3000",
            )
        except Exception as e:
            log.warning("Failed to initialize Stripe: %s", e)
    return _STRIPE_SERVICE


def _get_billing_api() -> BillingAPI:
    global _BILLING_API
    if _BILLING_API is None:
        _BILLING_API = BillingAPI(
            store=_get_store(),
            stripe_service=_get_stripe_service(),
        )
    return _BILLING_API


def _current_org_id() -> UUID:
    """The org that owns this request's billing rows.

    Tenant org ids are opaque strings; this read `UUID(cfg.org_id)` directly and
    raised ValueError — a 500 on every /billing/* route — for any tenant whose id
    was not already a UUID. `org_uuid()` is the same derivation the rest of the
    DB-backed features use, so billing rows land under the same org as the
    incidents and runbooks beside them.
    """
    return org_uuid()


# Request/Response models
class CheckoutRequest(BaseModel):
    plan_name: str
    billing_cycle: str = "monthly"
    email: str | None = None
    success_url: str | None = None
    cancel_url: str | None = None


class PortalRequest(BaseModel):
    return_url: str | None = None


class ChangePlanRequest(BaseModel):
    plan_name: str
    billing_cycle: str | None = None


class CancelRequest(BaseModel):
    reason: str = ""
    immediate: bool = False
    feedback: str = ""


class TrackUsageRequest(BaseModel):
    metric_type: str
    metadata: dict[str, Any] | None = None


# Plan endpoints
@router.get("/plans")
async def list_plans(include_enterprise: bool = False) -> list[dict[str, Any]]:
    """List all available pricing plans."""
    api = _get_billing_api()
    org_id = _current_org_id()
    return await api.list_plans(org_id, include_enterprise)


@router.get("/plans/comparison")
async def get_plan_comparison() -> list[dict[str, Any]]:
    """Get plan feature comparison matrix."""
    api = _get_billing_api()
    return await api.get_plan_comparison()


# Subscription endpoints
@router.get("/subscription")
async def get_subscription() -> dict[str, Any] | None:
    """Get current subscription details."""
    api = _get_billing_api()
    org_id = _current_org_id()
    return await api.get_subscription(org_id)


@router.post("/subscription/change")
async def change_subscription(req: ChangePlanRequest) -> dict[str, Any]:
    """Change subscription plan."""
    api = _get_billing_api()
    org_id = _current_org_id()
    try:
        return await api.change_plan(org_id, req.plan_name, req.billing_cycle)
    except BillingUnavailableError as e:
        raise _unavailable(e) from e


@router.post("/subscription/cancel")
async def cancel_subscription(req: CancelRequest) -> dict[str, Any]:
    """Cancel subscription."""
    api = _get_billing_api()
    org_id = _current_org_id()
    try:
        return await api.cancel_subscription(org_id, req.reason, req.immediate, req.feedback)
    except BillingUnavailableError as e:
        raise _unavailable(e) from e


@router.post("/subscription/reactivate")
async def reactivate_subscription() -> dict[str, Any]:
    """Reactivate canceled subscription."""
    api = _get_billing_api()
    org_id = _current_org_id()
    try:
        return await api.reactivate_subscription(org_id)
    except BillingUnavailableError as e:
        raise _unavailable(e) from e


@router.get("/subscription/proration")
async def calculate_proration(plan: str) -> dict[str, Any]:
    """Calculate proration for plan change."""
    api = _get_billing_api()
    org_id = _current_org_id()
    return await api.calculate_proration(org_id, plan)


# Usage endpoints
@router.get("/usage")
async def get_usage() -> dict[str, Any]:
    """Get current usage and limits."""
    api = _get_billing_api()
    org_id = _current_org_id()
    return await api.get_usage(org_id)


@router.get("/usage/stats")
async def get_usage_stats(year_month: str | None = None) -> dict[str, Any]:
    """Get usage statistics for a month."""
    api = _get_billing_api()
    org_id = _current_org_id()
    return await api.get_usage_stats(org_id, year_month)


@router.get("/usage/history")
async def get_usage_history(months: int = 12) -> list[dict[str, Any]]:
    """Get usage history."""
    api = _get_billing_api()
    org_id = _current_org_id()
    return await api.get_usage_history(org_id, months)


@router.get("/usage/report")
async def get_usage_report(year_month: str | None = None) -> dict[str, Any]:
    """Get detailed usage report."""
    api = _get_billing_api()
    org_id = _current_org_id()
    return await api.get_usage_report(org_id, year_month)


@router.get("/usage/forecast")
async def get_usage_forecast(months_ahead: int = 3) -> dict[str, Any]:
    """Get usage forecast."""
    api = _get_billing_api()
    org_id = _current_org_id()
    return await api.get_usage_forecast(org_id, months_ahead)


@router.get("/usage/limit")
async def check_usage_limit(metric: str, increment: int = 1) -> dict[str, Any]:
    """Check if within usage limits."""
    api = _get_billing_api()
    org_id = _current_org_id()
    return await api.check_usage_limit(org_id, metric, increment)


# Checkout and portal endpoints
@router.post("/checkout")
async def create_checkout(req: CheckoutRequest) -> dict[str, Any]:
    """Create a Stripe checkout session."""
    api = _get_billing_api()
    org_id = _current_org_id()
    try:
        return await api.create_checkout(
            org_id,
            req.plan_name,
            req.billing_cycle,
            req.email,
            req.success_url,
            req.cancel_url,
        )
    except BillingUnavailableError as e:
        raise _unavailable(e) from e


@router.post("/portal")
async def create_portal(req: PortalRequest) -> dict[str, Any]:
    """Create a Stripe customer portal session."""
    api = _get_billing_api()
    org_id = _current_org_id()
    try:
        return await api.create_portal(org_id, req.return_url)
    except BillingUnavailableError as e:
        raise _unavailable(e) from e


# Invoice and payment method endpoints
@router.get("/invoices")
async def get_invoices(limit: int = 10) -> list[dict[str, Any]]:
    """Get invoice history."""
    api = _get_billing_api()
    org_id = _current_org_id()
    return await api.get_invoices(org_id, limit)


@router.get("/payment-methods")
async def get_payment_methods() -> list[dict[str, Any]]:
    """Get payment methods."""
    api = _get_billing_api()
    org_id = _current_org_id()
    return await api.get_payment_methods(org_id)


# Feature gating endpoints
@router.get("/features/{feature_key}")
async def check_feature(feature_key: str) -> dict[str, Any]:
    """Check if a feature is available."""
    api = _get_billing_api()
    org_id = _current_org_id()
    return await api.check_feature(org_id, feature_key)


@router.get("/features")
async def get_feature_status(features: str | None = None) -> dict[str, dict[str, Any]]:
    """Get status of multiple features."""
    api = _get_billing_api()
    org_id = _current_org_id()
    feature_list = features.split(",") if features else None
    return await api.get_feature_status(org_id, feature_list)


@router.get("/upgrade-options")
async def get_upgrade_options() -> list[dict[str, Any]]:
    """Get available upgrade options."""
    api = _get_billing_api()
    org_id = _current_org_id()
    return await api.get_upgrade_options(org_id)


# Usage tracking endpoint
@router.post("/track")
async def track_usage(req: TrackUsageRequest) -> dict[str, Any]:
    """Track a usage event."""
    api = _get_billing_api()
    org_id = _current_org_id()
    return await api.track_usage(org_id, req.metric_type, req.metadata)


# Webhook endpoint
@router.post("/webhooks/stripe")
async def handle_stripe_webhook(request: Request) -> dict[str, Any]:
    """Handle Stripe webhook events."""
    api = _get_billing_api()
    payload = await request.body()
    signature = request.headers.get("stripe-signature", "")
    try:
        return await api.handle_webhook(payload, signature)
    except BillingUnavailableError as e:
        raise _unavailable(e) from e
