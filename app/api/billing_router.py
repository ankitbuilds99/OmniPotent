"""Billing API router - exposes billing endpoints for the frontend."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.billing.api import BillingUnavailableError, UnknownMetricError
from app.billing.models import MetricType
# The billing store, Stripe service and BillingAPI live in `app.billing.runtime`
# so that the features gating on a plan flag (deduplication layers, dependency
# graph, cost budgets) read the same store these endpoints write to. Two stores
# would mean a customer who had just upgraded still looked unsubscribed to the
# gate.
#
# They keep their underscore-prefixed names here because `app/billing/feature_gate.py`
# resolves `_get_store` / `_current_org_id` off this module at call time, which is
# also how the tests point the gate at a fake store.
from app.billing.runtime import current_org_id as _current_org_id
from app.billing.runtime import get_billing_api as _get_billing_api
from app.billing.runtime import get_billing_store as _get_store  # noqa: F401
from app.billing.runtime import get_stripe_service as _get_stripe_service  # noqa: F401

log = logging.getLogger("omnipotent.api.billing")

router = APIRouter(prefix="/billing", tags=["Billing"])


def _require_perm(action: str) -> Any:
    """The app's RBAC dependency, imported lazily to avoid a circular import.

    `app.api.main` imports this router, so importing `require_perm` from it at
    module scope would be a cycle. Same shim `app/notifications/router.py` uses.
    """

    async def _dep() -> None:
        from app.api.main import require_perm

        await require_perm(action)()

    return _dep


# `manage_billing` was declared in the permission matrix (app/tenancy/models.py)
# and enforced on no route. The only app-wide dependency is `require_auth`, which
# proves who the caller is and says nothing about what they may do, so any key in
# the org — down to the lowest `viewer` rank — reached the money routes: cancel or
# downgrade the org's paid plan, or mint a Stripe customer-portal URL, which hands
# over the card on file, the invoice history and cancellation.
#
# The routes below now guarantee that the plan and the payment instrument can only
# be moved by an owner, and that the billing documents (who was charged what, and
# the card) need admin — the same bar `view_audit` sets on /audit, because both
# are administrative reads rather than part of plain "view". `require_perm`
# returns early when multi-tenancy is off, so single-tenant and offline demo
# deployments, which have no roles at all, are unaffected.
#
# Plain reads — /plans, /subscription, /usage/*, /features*, /upgrade-options,
# proration — stay open to a viewer on purpose: they expose no payment
# instrument, and gating them would blank the Billing page for most of an org.
_BILLING_OWNER = Depends(_require_perm("manage_billing"))
_BILLING_READER = Depends(_require_perm("view_audit"))


def _unavailable(exc: BillingUnavailableError) -> HTTPException:
    """Map an unconfigured-billing failure to 503 (never a silent success)."""
    return HTTPException(status_code=503, detail=str(exc))


def _bad_metric(exc: UnknownMetricError) -> HTTPException:
    """Map an unrecognised metric name to 400 — the caller's typo, not an outage."""
    return HTTPException(status_code=400, detail=str(exc))


def _bad_request(exc: ValueError) -> HTTPException:
    """Map a rejected billing operation to 400 with the reason.

    The Stripe service raises ValueError for everything it cannot do — unknown
    plan, no price configured for the requested cycle, no Stripe customer yet —
    and none of it was caught, so the client saw a bare 500 "An unexpected error
    occurred" and had no way to tell a typo from an outage.
    """
    return HTTPException(status_code=400, detail=str(exc))


async def record_ai_investigation(tokens: int = 0, idem: str = "") -> None:
    """Mirror a completed investigation into the billing meter.

    The Billing page reads `billing_usage_records` / `billing_usage_summaries`,
    and until this existed the only writer was the hand-callable POST
    /billing/track — which no part of the product or the frontend ever called.
    So every counter and every bar on the Usage tab read zero forever, while the
    enforced meter at GET /usage counted the same investigations correctly.

    Best-effort by design: accounting must not fail a request that already
    succeeded. The caller is the completion hook, which is equally forgiving of
    its other meters.
    """
    meta: dict[str, Any] = {"source": "investigation"}
    if tokens:
        meta["tokens"] = tokens
    if idem:
        meta["idempotency_key"] = idem
    try:
        await _get_billing_api().track_usage(
            _current_org_id(), MetricType.AI_INVESTIGATION.value, meta
        )
    except Exception:  # noqa: BLE001 — billing accounting must never break a request
        log.warning("billing usage record failed", exc_info=True)


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


@router.post("/subscription/change", dependencies=[_BILLING_OWNER])
async def change_subscription(req: ChangePlanRequest) -> dict[str, Any]:
    """Change subscription plan."""
    api = _get_billing_api()
    org_id = _current_org_id()
    try:
        return await api.change_plan(org_id, req.plan_name, req.billing_cycle)
    except BillingUnavailableError as e:
        raise _unavailable(e) from e
    except ValueError as e:
        raise _bad_request(e) from e


@router.post("/subscription/cancel", dependencies=[_BILLING_OWNER])
async def cancel_subscription(req: CancelRequest) -> dict[str, Any]:
    """Cancel subscription."""
    api = _get_billing_api()
    org_id = _current_org_id()
    try:
        return await api.cancel_subscription(org_id, req.reason, req.immediate, req.feedback)
    except BillingUnavailableError as e:
        raise _unavailable(e) from e
    except ValueError as e:
        raise _bad_request(e) from e


@router.post("/subscription/reactivate", dependencies=[_BILLING_OWNER])
async def reactivate_subscription() -> dict[str, Any]:
    """Reactivate canceled subscription."""
    api = _get_billing_api()
    org_id = _current_org_id()
    try:
        return await api.reactivate_subscription(org_id)
    except BillingUnavailableError as e:
        raise _unavailable(e) from e
    except ValueError as e:
        raise _bad_request(e) from e


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
    try:
        return await api.check_usage_limit(org_id, metric, increment)
    except UnknownMetricError as e:
        raise _bad_metric(e) from e


# Checkout and portal endpoints
@router.post("/checkout", dependencies=[_BILLING_OWNER])
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
    except ValueError as e:
        raise _bad_request(e) from e


@router.post("/portal", dependencies=[_BILLING_OWNER])
async def create_portal(req: PortalRequest) -> dict[str, Any]:
    """Create a Stripe customer portal session."""
    api = _get_billing_api()
    org_id = _current_org_id()
    try:
        return await api.create_portal(org_id, req.return_url)
    except BillingUnavailableError as e:
        raise _unavailable(e) from e
    except ValueError as e:
        raise _bad_request(e) from e


# Invoice and payment method endpoints
@router.get("/invoices", dependencies=[_BILLING_READER])
async def get_invoices(limit: int = 10) -> list[dict[str, Any]]:
    """Get invoice history."""
    api = _get_billing_api()
    org_id = _current_org_id()
    return await api.get_invoices(org_id, limit)


@router.get("/payment-methods", dependencies=[_BILLING_READER])
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
@router.post("/track", dependencies=[_BILLING_OWNER])
async def track_usage(req: TrackUsageRequest) -> dict[str, Any]:
    """Track a usage event."""
    api = _get_billing_api()
    org_id = _current_org_id()
    try:
        return await api.track_usage(org_id, req.metric_type, req.metadata)
    except UnknownMetricError as e:
        raise _bad_metric(e) from e


# Stripe's webhook receiver is `POST /webhooks/stripe` (app/api/main.py). There
# was a second one here, at /billing/webhooks/stripe. It could never fire:
# `require_auth` only exempts paths under `/webhooks/`, and Stripe sends a
# `Stripe-Signature` header and no `Authorization` header, so every real delivery
# was rejected with 401 before reaching the handler. A duplicate receiver that
# looks configured but never runs is worse than none at all — plan changes would
# silently depend on whichever URL was pasted into the Stripe dashboard — so it
# is gone rather than moved.
