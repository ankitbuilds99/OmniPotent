"""Tenant domain model: orgs, roles/RBAC, plans/quotas, API keys, usage.

Pure data + policy (no I/O), so the permission matrix and quota logic are
unit-testable in isolation. The store (store.py) persists these.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

log = logging.getLogger("omnipotent.tenancy.models")

# --- Roles & RBAC ---------------------------------------------------------- #
# Ranked least → most privileged. A permission requires a minimum role.
ROLE_RANK: dict[str, int] = {"viewer": 0, "responder": 1, "admin": 2, "owner": 3}
ROLES = tuple(ROLE_RANK)

# action -> minimum role that may perform it.
_PERMISSIONS: dict[str, str] = {
    "view": "viewer",                 # read investigations / config
    "run_investigation": "responder", # start a chat/investigation
    "approve_action": "responder",    # approve/reject a write the agent proposes
    "manage_integrations": "admin",   # set Datadog/GitHub/etc. credentials
    "manage_members": "admin",        # invite/remove members, change roles
    "manage_api_keys": "admin",       # issue/revoke API keys
    # The trail names API-key ids, the actors that used them and every approval
    # decision — reading it is an administrative act, not part of plain "view".
    "view_audit": "admin",            # read /audit + verify the hash chain
    "manage_billing": "owner",        # change plan / billing
    "delete_org": "owner",
}
ACTIONS = tuple(_PERMISSIONS)


def can(role: str, action: str) -> bool:
    """True if `role` is allowed to perform `action`."""
    need = _PERMISSIONS.get(action)
    if need is None:
        return False
    return ROLE_RANK.get(role, -1) >= ROLE_RANK[need]


def normalize_role(role: str) -> str:
    r = (role or "").strip().lower()
    return r if r in ROLE_RANK else "viewer"


# --- Plans & quotas -------------------------------------------------------- #
# -1 == unlimited.
#
# This table is the *vocabulary* of `orgs.plan`, not just a list of limits:
# `normalize_plan` turns anything absent from it into "free", so a plan value
# with no bracket here cannot be stored at all. It held three brackets while the
# catalog (app/billing/plans.py) sold six tiers, so Growth and Business — the two
# tiers whose whole pitch is the features above Team — had nowhere to land. They
# were translated to "team" on the way in and the org record then had no way to
# say which of the two the customer had bought, which is what
# `FeatureGateService._plan_from_tenant_context` reads: every Growth and Business
# entitlement was withheld from customers paying $699 and $1,499 a month for
# exactly those entitlements, and the meter capped them at Team's 1000
# investigations when both tiers sell unlimited.
#
# The numbers come from the catalog entries of the same name: seats is
# `max_users`, and unlimited investigations/integrations is what those two tiers
# advertise (`max_ai_investigations_monthly=None`, `limit_integrations=None`).
# The catalog prices no API-key allowance, so those keep the seats-to-keys ratio
# the Team bracket already sets rather than inventing an entitlement.
#
# Scale still maps onto "enterprise" (app/billing/plans.py): that collapse only
# ever grants more than was bought, so it is not this failure, and narrowing it
# would start refusing seats that are allowed today.
PLAN_QUOTAS: dict[str, dict[str, int]] = {
    "free": {"investigations_per_month": 50, "seats": 3, "integrations": 2, "api_keys": 2},
    "team": {"investigations_per_month": 1000, "seats": 25, "integrations": 10, "api_keys": 20},
    "growth": {"investigations_per_month": -1, "seats": 25, "integrations": -1, "api_keys": 20},
    "business": {"investigations_per_month": -1, "seats": 50, "integrations": -1, "api_keys": 40},
    "enterprise": {"investigations_per_month": -1, "seats": -1, "integrations": -1, "api_keys": -1},
}
PLANS = tuple(PLAN_QUOTAS)


def normalize_plan(plan: str) -> str:
    """The stored plan value for `plan`, defaulting to free.

    The default is a downgrade, so it is said out loud: an org whose plan value
    is not in the table is metered exactly like one that never paid, and the only
    difference between "the operator typed 'Growth ' with a trailing space" and
    "this customer is on the free plan" used to be invisible.
    """
    p = (plan or "").strip().lower()
    if p in PLAN_QUOTAS:
        return p
    if p:
        log.warning(
            "Plan %r is not one this deployment enforces (%s); treating it as "
            "free, which caps the org at the free tier's quotas",
            plan,
            ", ".join(PLAN_QUOTAS),
        )
    return "free"


def quota(plan: str, key: str) -> int:
    """The limit for `key` under `plan` (-1 = unlimited)."""
    return PLAN_QUOTAS.get(normalize_plan(plan), PLAN_QUOTAS["free"]).get(key, 0)


def within_quota(plan: str, key: str, current: int) -> bool:
    """True if one more unit of `key` is allowed given `current` usage/count."""
    limit = quota(plan, key)
    return limit < 0 or current < limit


# --- Records --------------------------------------------------------------- #
@dataclass
class Org:
    id: str
    name: str
    plan: str = "free"
    created_at: str = ""
    stripe_customer_id: str = ""


@dataclass
class User:
    id: str
    email: str
    created_at: str = ""


@dataclass
class Membership:
    org_id: str
    user_id: str
    role: str = "viewer"


@dataclass
class ApiKey:
    id: str
    org_id: str
    prefix: str          # public, used to look the key up
    name: str = ""
    role: str = "responder"
    created_at: str = ""
    last_used_at: str = ""
    revoked_at: str = ""
    expires_at: str = ""

    @property
    def active(self) -> bool:
        return not self.revoked_at


@dataclass
class UsageRecord:
    org_id: str
    kind: str            # e.g. "investigation", "tokens"
    amount: int = 1
    ts: str = ""
    meta: dict = field(default_factory=dict)
