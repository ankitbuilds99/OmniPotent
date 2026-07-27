"""Waitlist management for early access signups.

Tracks signups, manages tiers, and provides analytics for
understanding conversion funnel and user acquisition.
"""

from __future__ import annotations

import csv
import hashlib
import io
import logging
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("devcopilot.waitlist")


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


_waitlist_store: dict[str, WaitlistEntry] = {}
_email_index: dict[str, str] = {}
_position_counter: int = 0


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


def add_to_waitlist(
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
    global _position_counter

    email = email.strip().lower()

    if not email:
        return None, "Email is required"

    if not _validate_email(email):
        return None, "Invalid email format"

    if email in _email_index:
        existing_id = _email_index[email]
        existing = _waitlist_store.get(existing_id)
        if existing:
            return existing, "Already on waitlist"

    _position_counter += 1

    entry = WaitlistEntry(
        id=_generate_id(),
        email=email,
        created_at=datetime.now(timezone.utc),
        source=source,
        company=company,
        role=role,
        team_size=team_size,
        use_case=use_case,
        referral_code=_generate_referral_code(email),
        referred_by=referred_by,
        position=_position_counter,
        utm_source=utm_source,
        utm_medium=utm_medium,
        utm_campaign=utm_campaign,
        ip_hash=_hash_ip(ip_address) if ip_address else "",
        user_agent=user_agent[:200] if user_agent else "",
    )

    if referred_by and referred_by in _email_index:
        referrer_id = _email_index[referred_by]
        referrer = _waitlist_store.get(referrer_id)
        if referrer:
            referrer.referral_count += 1
            if referrer.referral_count >= 3 and referrer.tier == WaitlistTier.STANDARD:
                referrer.tier = WaitlistTier.PRIORITY
                referrer.tags.append("referral_upgrade")

    _waitlist_store[entry.id] = entry
    _email_index[email] = entry.id

    log.info(
        "Waitlist signup: position=%d source=%s company=%s",
        entry.position,
        source.value,
        company or "(none)",
    )

    return entry, "Successfully joined waitlist"


def get_waitlist_entry(email: str) -> WaitlistEntry | None:
    """Get waitlist entry by email."""
    email = email.strip().lower()
    entry_id = _email_index.get(email)
    if entry_id:
        return _waitlist_store.get(entry_id)
    return None


def get_waitlist_entries(
    tier: WaitlistTier | None = None,
    source: SignupSource | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[WaitlistEntry]:
    """Get waitlist entries with optional filtering."""
    entries = list(_waitlist_store.values())

    if tier:
        entries = [e for e in entries if e.tier == tier]

    if source:
        entries = [e for e in entries if e.source == source]

    entries.sort(key=lambda e: e.position)

    return entries[offset:offset + limit]


def promote_to_early_access(
    email: str,
    tier: WaitlistTier = WaitlistTier.EARLY_ACCESS,
) -> tuple[bool, str]:
    """Promote a waitlist entry to early access."""
    entry = get_waitlist_entry(email)
    if not entry:
        return False, "Email not found in waitlist"

    if entry.tier == WaitlistTier.FOUNDING_CUSTOMER:
        return False, "Already a founding customer"

    entry.tier = tier
    entry.invited_at = datetime.now(timezone.utc)
    entry.tags.append(f"promoted_to_{tier.value}")

    log.info("Promoted %s to %s", entry._mask_email(), tier.value)

    return True, f"Promoted to {tier.value}"


def mark_converted(email: str) -> tuple[bool, str]:
    """Mark a waitlist entry as converted (became customer)."""
    entry = get_waitlist_entry(email)
    if not entry:
        return False, "Email not found in waitlist"

    entry.converted_at = datetime.now(timezone.utc)
    entry.tier = WaitlistTier.FOUNDING_CUSTOMER
    entry.tags.append("converted")

    log.info("Conversion: %s", entry._mask_email())

    return True, "Marked as converted"


def get_waitlist_stats() -> dict[str, Any]:
    """Get waitlist statistics for dashboard."""
    entries = list(_waitlist_store.values())

    if not entries:
        return {
            "total_signups": 0,
            "by_tier": {},
            "by_source": {},
            "conversion_rate": 0.0,
            "referral_rate": 0.0,
            "signups_today": 0,
            "signups_this_week": 0,
        }

    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    by_tier: dict[str, int] = {}
    by_source: dict[str, int] = {}
    converted = 0
    referred = 0
    signups_today = 0
    signups_this_week = 0

    for entry in entries:
        by_tier[entry.tier.value] = by_tier.get(entry.tier.value, 0) + 1
        by_source[entry.source.value] = by_source.get(entry.source.value, 0) + 1

        if entry.converted_at:
            converted += 1

        if entry.referred_by:
            referred += 1

        if entry.created_at >= today_start:
            signups_today += 1

        days_diff = (now - entry.created_at).days
        if days_diff <= 7:
            signups_this_week += 1

    total = len(entries)

    return {
        "total_signups": total,
        "by_tier": by_tier,
        "by_source": by_source,
        "conversion_rate": converted / total if total > 0 else 0.0,
        "referral_rate": referred / total if total > 0 else 0.0,
        "conversions": converted,
        "signups_today": signups_today,
        "signups_this_week": signups_this_week,
        "avg_position": sum(e.position for e in entries) / total if total > 0 else 0,
    }


def export_waitlist(format: str = "csv") -> str:
    """Export waitlist data.

    Args:
        format: 'csv' or 'json'

    Returns:
        Exported data as string
    """
    entries = get_waitlist_entries(limit=10000)

    if format == "json":
        import json
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

    def signup(
        self,
        email: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Process a signup request."""
        entry, message = add_to_waitlist(email, **kwargs)

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

    def get_position(self, email: str) -> dict[str, Any]:
        """Get waitlist position for an email."""
        entry = get_waitlist_entry(email)

        if not entry:
            return {
                "found": False,
                "error": "Email not found in waitlist",
            }

        total = len(_waitlist_store)

        return {
            "found": True,
            "position": entry.position,
            "total": total,
            "tier": entry.tier.value,
            "referral_code": entry.referral_code,
            "referral_count": entry.referral_count,
            "invited": entry.invited_at is not None,
        }

    def stats(self) -> dict[str, Any]:
        """Get waitlist statistics."""
        return get_waitlist_stats()


def clear_waitlist() -> None:
    """Clear all waitlist data (for testing)."""
    global _position_counter
    _waitlist_store.clear()
    _email_index.clear()
    _position_counter = 0
