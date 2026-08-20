"""Waitlist management for early access signups.

Tracks signups, manages tiers, and provides analytics for
understanding conversion funnel and user acquisition.

State lives in Postgres (`app/waitlist/store.py`), not in this module. It used
to live in three module-level globals, which meant every restart, deploy and
scale-down threw away the leads, and `_position_counter` restarted at 0 so the
next signup was handed a position an earlier one already held. Every function
here is therefore async; there is no in-process copy of the waitlist to read.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from app.database.models import WaitlistSignup
from app.waitlist import store

log = logging.getLogger("omnipotent.waitlist")

# Referrals needed before a standard signup is bumped up the queue.
REFERRALS_FOR_PRIORITY = 3


class WaitlistTier(str, Enum):
    """Waitlist tier for prioritization."""

    STANDARD = "standard"
    PRIORITY = "priority"
    EARLY_ACCESS = "early_access"
    BETA_TESTER = "beta_tester"
    FOUNDING_CUSTOMER = "founding_customer"


class SignupSource(str, Enum):
    """Source of the signup."""

    LANDING_PAGE = "landing_page"
    PRODUCT_HUNT = "product_hunt"
    HACKER_NEWS = "hacker_news"
    TWITTER = "twitter"
    LINKEDIN = "linkedin"
    GITHUB = "github"
    REFERRAL = "referral"
    DIRECT = "direct"
    OTHER = "other"


@dataclass
class WaitlistEntry:
    """A waitlist signup entry."""

    id: str
    email: str
    created_at: datetime
    tier: WaitlistTier = WaitlistTier.STANDARD
    source: SignupSource = SignupSource.LANDING_PAGE

    company: str = ""
    role: str = ""
    team_size: str = ""
    use_case: str = ""

    referral_code: str = ""
    referred_by: str = ""
    referral_count: int = 0

    position: int = 0
    invited_at: datetime | None = None
    converted_at: datetime | None = None

    utm_source: str = ""
    utm_medium: str = ""
    utm_campaign: str = ""

    ip_hash: str = ""
    user_agent: str = ""

    notes: str = ""
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "email": self._mask_email(),
            "created_at": self.created_at.isoformat(),
            "tier": self.tier.value,
            "source": self.source.value,
            "company": self.company,
            "role": self.role,
            "team_size": self.team_size,
            "position": self.position,
            "referral_code": self.referral_code,
            "referral_count": self.referral_count,
            "invited_at": self.invited_at.isoformat() if self.invited_at else None,
            "converted_at": self.converted_at.isoformat() if self.converted_at else None,
            "tags": self.tags,
        }

    def _mask_email(self) -> str:
        """Mask email for privacy in logs/exports."""
        if "@" not in self.email:
            return "***"
        local, domain = self.email.split("@", 1)
        if len(local) <= 2:
            masked_local = "*" * len(local)
        else:
            masked_local = local[0] + "*" * (len(local) - 2) + local[-1]
        return f"{masked_local}@{domain}"


def _generate_id() -> str:
    """Generate unique waitlist entry ID."""
    return f"wl_{secrets.token_hex(8)}"


def _generate_referral_code(email: str) -> str:
    """Generate unique referral code from email."""
    h = hashlib.sha256(email.lower().encode()).hexdigest()[:8]
    return f"REF{h.upper()}"


def _validate_email(email: str) -> bool:
    """Basic email validation."""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(pattern, email))


def _hash_ip(ip: str) -> str:
    """Hash IP for privacy-preserving duplicate detection."""
    return hashlib.sha256(ip.encode()).hexdigest()[:16]


def _to_entry(row: WaitlistSignup) -> WaitlistEntry:
    """Build the domain object from a stored row.

    Tier and source are read back defensively: they are plain strings in the
    table, and a row written by an older build (or by hand) must not take down
    a listing because its tier no longer names a member of the enum.
    """
    try:
        tier = WaitlistTier(row.tier)
    except ValueError:
        log.warning("Waitlist row %s has unknown tier %r; reading as standard",
                    row.id, row.tier)
        tier = WaitlistTier.STANDARD
    try:
        source = SignupSource(row.source)
    except ValueError:
        log.warning("Waitlist row %s has unknown source %r; reading as other",
                    row.id, row.source)
        source = SignupSource.OTHER

    created_at = store.utc(row.created_at)
    return WaitlistEntry(
        id=row.id,
        email=row.email,
        created_at=created_at or datetime.now(timezone.utc),
        tier=tier,
        source=source,
        company=row.company,
        role=row.role,
        team_size=row.team_size,
        use_case=row.use_case,
        referral_code=row.referral_code,
        referred_by=row.referred_by,
        referral_count=row.referral_count,
        position=row.position,
        invited_at=store.utc(row.invited_at),
        converted_at=store.utc(row.converted_at),
        utm_source=row.utm_source,
        utm_medium=row.utm_medium,
        utm_campaign=row.utm_campaign,
        ip_hash=row.ip_hash,
        user_agent=row.user_agent,
        notes=row.notes,
        tags=list(row.tags or []),
    )


async def _resolve_referrer(referred_by: str) -> WaitlistSignup | None:
    """Find the signup that referred someone, by referral code or by email.

    The signup API sends a referral *code* (`REF1A2B3C4D`); the admin paths and
    the tests send an email. Both are accepted, code first, because the code is
    the thing that actually travels in a share link.
    """
    value = referred_by.strip()
    if not value:
        return None

    referrer = await store.get_by_referral_code(value.upper())
    if referrer is None:
        referrer = await store.get_by_email(value.lower())
    return referrer


async def _credit_referrer(referrer: WaitlistSignup) -> None:
    """Record one more referral, promoting the referrer once they hit the bar."""
    changes: dict[str, Any] = {"referral_count": referrer.referral_count + 1}

    if (
        changes["referral_count"] >= REFERRALS_FOR_PRIORITY
        and referrer.tier == WaitlistTier.STANDARD.value
    ):
        changes["tier"] = WaitlistTier.PRIORITY.value
        # Reassigned, not appended to: a JSON column is only marked dirty on
        # assignment, so an in-place append would be dropped on commit.
        changes["tags"] = [*(referrer.tags or []), "referral_upgrade"]

    await store.update(referrer.email, **changes)


async def add_to_waitlist(
    email: str,
    source: SignupSource = SignupSource.LANDING_PAGE,
    company: str = "",
    role: str = "",
    team_size: str = "",
    use_case: str = "",
    referred_by: str = "",
    utm_source: str = "",
    utm_medium: str = "",
    utm_campaign: str = "",
    ip_address: str = "",
    user_agent: str = "",
) -> tuple[WaitlistEntry | None, str]:
    """Add email to waitlist.

    Returns:
        Tuple of (entry, message). Entry is None if failed.
    """
    email = email.strip().lower()

    if not email:
        return None, "Email is required"

    if not _validate_email(email):
        return None, "Invalid email format"

    existing = await store.get_by_email(email)
    if existing is not None:
        return _to_entry(existing), "Already on waitlist"

    # Resolved before the insert so that only a referral that actually points at
    # a real signup is recorded. An unresolvable value is dropped rather than
    # stored, which keeps `referral_rate` equal to the referrals that were
    # genuinely credited instead of counting attribution that never landed.
    referrer = await _resolve_referrer(referred_by)
    if referred_by and referrer is None:
        log.info("Waitlist signup cited an unknown referrer; recording no referral")

    row, failure = await store.insert(
        id=_generate_id(),
        email=email,
        created_at=datetime.now(timezone.utc),
        tier=WaitlistTier.STANDARD.value,
        source=source.value,
        company=company,
        role=role,
        team_size=team_size,
        use_case=use_case,
        referral_code=_generate_referral_code(email),
        referred_by=referrer.email if referrer else "",
        referral_count=0,
        utm_source=utm_source,
        utm_medium=utm_medium,
        utm_campaign=utm_campaign,
        ip_hash=_hash_ip(ip_address) if ip_address else "",
        user_agent=user_agent[:200] if user_agent else "",
        notes="",
        tags=[],
    )

    if row is None:
        if failure == "duplicate_email":
            # Lost a race with a concurrent signup for the same address; the
            # other one won and this is the same answer that caller would have
            # got a moment earlier.
            duplicate = await store.get_by_email(email)
            if duplicate is not None:
                return _to_entry(duplicate), "Already on waitlist"
        return None, "Could not join waitlist, please try again"

    if referrer is not None:
        await _credit_referrer(referrer)

    log.info(
        "Waitlist signup: position=%d source=%s company=%s",
        row.position,
        source.value,
        company or "(none)",
    )

    return _to_entry(row), "Successfully joined waitlist"


async def get_waitlist_entry(email: str) -> WaitlistEntry | None:
    """Get waitlist entry by email."""
    row = await store.get_by_email(email.strip().lower())
    return _to_entry(row) if row else None


async def get_waitlist_entries(
    tier: WaitlistTier | None = None,
    source: SignupSource | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[WaitlistEntry]:
    """Get waitlist entries with optional filtering."""
    rows = await store.list_entries(
        tier=tier.value if tier else None,
        source=source.value if source else None,
        limit=limit,
        offset=offset,
    )
    return [_to_entry(row) for row in rows]


async def promote_to_early_access(
    email: str,
    tier: WaitlistTier = WaitlistTier.EARLY_ACCESS,
) -> tuple[bool, str]:
    """Promote a waitlist entry to early access."""
    row = await store.get_by_email(email.strip().lower())
    if not row:
        return False, "Email not found in waitlist"

    if row.tier == WaitlistTier.FOUNDING_CUSTOMER.value:
        return False, "Already a founding customer"

    updated = await store.update(
        row.email,
        tier=tier.value,
        invited_at=datetime.now(timezone.utc),
        tags=[*(row.tags or []), f"promoted_to_{tier.value}"],
    )
    if updated is None:
        return False, "Email not found in waitlist"

    log.info("Promoted %s to %s", _to_entry(updated)._mask_email(), tier.value)

    return True, f"Promoted to {tier.value}"


async def mark_converted(email: str) -> tuple[bool, str]:
    """Mark a waitlist entry as converted (became customer)."""
    row = await store.get_by_email(email.strip().lower())
    if not row:
        return False, "Email not found in waitlist"

    updated = await store.update(
        row.email,
        converted_at=datetime.now(timezone.utc),
        tier=WaitlistTier.FOUNDING_CUSTOMER.value,
        tags=[*(row.tags or []), "converted"],
    )
    if updated is None:
        return False, "Email not found in waitlist"

    log.info("Conversion: %s", _to_entry(updated)._mask_email())

    return True, "Marked as converted"


async def get_waitlist_stats() -> dict[str, Any]:
    """Get waitlist statistics for dashboard."""
    return await store.stats()


async def export_waitlist(format: str = "csv") -> str:
    """Export waitlist data.

    Args:
        format: 'csv' or 'json'

    Returns:
        Exported data as string
    """
    entries = await get_waitlist_entries(limit=10000)

    if format == "json":
        return json.dumps([e.to_dict() for e in entries], indent=2)

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "position", "email", "company", "role", "team_size",
        "source", "tier", "created_at", "referral_code", "referral_count",
    ])

    for entry in entries:
        writer.writerow([
            entry.position,
            entry.email,
            entry.company,
            entry.role,
            entry.team_size,
            entry.source.value,
            entry.tier.value,
            entry.created_at.isoformat(),
            entry.referral_code,
            entry.referral_count,
        ])

    return output.getvalue()


class WaitlistManager:
    """High-level waitlist manager for API integration."""

    def __init__(self) -> None:
        pass

    async def signup(
        self,
        email: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Process a signup request."""
        entry, message = await add_to_waitlist(email, **kwargs)

        if entry is None:
            return {
                "success": False,
                "error": message,
            }

        return {
            "success": True,
            "message": message,
            "position": entry.position,
            "referral_code": entry.referral_code,
            "tier": entry.tier.value,
        }

    async def get_position(self, email: str) -> dict[str, Any]:
        """Get waitlist position for an email."""
        entry = await get_waitlist_entry(email)

        if not entry:
            return {
                "found": False,
                "error": "Email not found in waitlist",
            }

        return {
            "found": True,
            "position": entry.position,
            "total": await store.count(),
            "tier": entry.tier.value,
            "referral_code": entry.referral_code,
            "referral_count": entry.referral_count,
            "invited": entry.invited_at is not None,
        }

    async def stats(self) -> dict[str, Any]:
        """Get waitlist statistics."""
        return await get_waitlist_stats()


async def clear_waitlist() -> None:
    """Clear all waitlist data (for testing)."""
    await store.clear()
