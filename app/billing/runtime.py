"""Process-wide billing singletons, shared by every feature that gates on a plan.

The billing store, Stripe service and `BillingAPI` used to be private module globals
inside `app/api/billing_router.py`. Any other module that wanted to check a plan flag
had to build its own store, which would be a *different* store: subscriptions written
through the billing endpoints would be invisible to the gate, so a customer who had
just upgraded would still be refused the feature they paid for. Keeping one instance
here removes that possibility by construction.

`billing_router` re-exports these rather than defining its own, so there is exactly
one store per process — and it is `DatabaseBillingStore`, not the in-memory one: a
plan held in process memory is cancelled by a restart and flips between requests once
there is more than one worker.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from app.billing.api import BillingAPI
from app.billing.db_store import DatabaseBillingStore
from app.billing.stripe_client import make_stripe_client
from app.billing.stripe_integration import StripeBillingService
from app.config import get_settings
from app.database.orgs import org_uuid

log = logging.getLogger("omnipotent.billing.runtime")

__all__ = [
    "current_org_id",
    "enabled_features",
    "get_billing_api",
    "get_billing_store",
    "get_stripe_service",
]

_BILLING_STORE: DatabaseBillingStore | None = None
_STRIPE_SERVICE: StripeBillingService | None = None
_BILLING_API: BillingAPI | None = None
# Which Stripe service `_BILLING_API` was built with, so `get_billing_api` can
# tell "built without one" from "built with the one that exists" without reaching
# into BillingAPI's internals.
_BILLING_API_STRIPE: StripeBillingService | None = None


def get_billing_store() -> DatabaseBillingStore:
    """The one billing store for this process.

    It holds no state of its own — every row it reads and writes is in the
    `billing_*` tables — so a single instance is enough.
    """
    global _BILLING_STORE
    if _BILLING_STORE is None:
        _BILLING_STORE = DatabaseBillingStore()
    return _BILLING_STORE


def get_stripe_service() -> StripeBillingService | None:
    """The live Stripe service, or None when billing is not configured.

    This used to pass the `stripe` module itself as the client. The module has no
    `create_customer` / `create_checkout_session` / `construct_webhook_event`, so a
    deployment with the SDK installed got an AttributeError — a 500 — on the first
    checkout. `make_stripe_client` returns the adapter that actually implements the
    protocol the service calls, or None so callers can report an honest 503.
    """
    global _STRIPE_SERVICE
    s = get_settings()
    if _STRIPE_SERVICE is None and s.stripe_api_key:
        client = make_stripe_client(s.stripe_api_key)
        if client is not None:
            _STRIPE_SERVICE = StripeBillingService(
                store=get_billing_store(),
                stripe_client=client,
                webhook_secret=s.stripe_webhook_secret or "",
                frontend_url=s.frontend_url or "http://localhost:3000",
            )
    return _STRIPE_SERVICE


def get_billing_api() -> BillingAPI:
    """The one BillingAPI for this process, rebuilt if Stripe arrives later.

    `BillingAPI` takes the Stripe service once, at construction, and refuses
    every Stripe-backed operation with `BillingUnavailableError` while it holds
    None. This memoised whatever `get_stripe_service()` answered on the very
    first call, and that call is not always made after the configuration is
    readable: a health check, a feature-flag lookup or an import-time warm-up
    that lands before STRIPE_API_KEY is in the environment pinned a Stripe-less
    BillingAPI for the life of the process, so `POST /webhooks/stripe` reported
    `billing_store: not_configured` for every real delivery on a deployment that
    was configured perfectly well — and `billing_subscriptions` stayed empty
    while customers were charged.

    `get_stripe_service()` keeps retrying until it succeeds, so asking it every
    time costs one attribute read once it has, and the BillingAPI is rebuilt only
    on the transition. Rebuilding is safe because it holds no state of its own:
    the store is the shared singleton and the gate, tracker and reporter it
    builds are all derived from it.
    """
    global _BILLING_API, _BILLING_API_STRIPE
    service = get_stripe_service()
    if _BILLING_API is None or (service is not None and service is not _BILLING_API_STRIPE):
        _BILLING_API = BillingAPI(store=get_billing_store(), stripe_service=service)
        _BILLING_API_STRIPE = service
    return _BILLING_API


def current_org_id() -> UUID:
    """The org that owns this request's billing rows.

    Tenant org ids are opaque strings; this read `UUID(cfg.org_id)` directly and
    raised ValueError — a 500 on every plan gate and every /billing/* route — for
    any tenant whose id was not already a UUID. `org_uuid()` is the same derivation
    the rest of the DB-backed features use, so billing rows land under the same org
    as the incidents and runbooks beside them.
    """
    return org_uuid()


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
