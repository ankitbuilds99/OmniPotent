"""Waitlist management for early access signups.

Tracks signups, manages tiers, and provides analytics for
understanding conversion funnel and user acquisition.

This was three module-level dicts — `_waitlist_store`, `_email_index` and a
`_position_counter` integer. Everything a visitor was told when they signed up
(their position, their referral code) was true only until the process recycled,
and a second uvicorn worker answered from a second, empty waitlist: two people
could be handed position #1, and one of them would find themselves off the list
entirely after the next deploy. It is the `waitlist_entries` table now, and
`position` is allocated under a unique constraint so a concurrent signup cannot
duplicate one.

The list is global, not per-org: someone joining a waitlist has no account yet.
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

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

log = logging.getLogger("omnipotent.waitlist")


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


# How many times to re-pick a position when a concurrent signup takes the one we
# were about to claim. Each retry re-reads MAX(position), so this only has to
# outlast a burst, not a sustained race.
_POSITION_ATTEMPTS = 5


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


def _utc(value: datetime | None) -> Any:
    """Read a timestamp back as UTC-aware.

    SQLite has no timezone type, so a `DateTime(timezone=True)` column round
    trips to a naive datetime; everything written here is UTC. Without this the
    "signups today" comparison in `get_waitlist_stats` raises TypeError on the
    default database.
    """
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def _enum(cls: Any, value: str, fallback: Any) -> Any:
    """Read a stored enum value back, tolerating one written by an older build."""
    try:
        return cls(value)
    except ValueError:
        return fallback


def _to_entry(row: Any) -> WaitlistEntry:
    return WaitlistEntry(
        id=row.id,
        email=row.email,
        created_at=_utc(row.created_at),
        tier=_enum(WaitlistTier, row.tier, WaitlistTier.STANDARD),
        source=_enum(SignupSource, row.source, SignupSource.OTHER),
        company=row.company,
        role=row.role,
        team_size=row.team_size,
        use_case=row.use_case,
        referral_code=row.referral_code,
        referred_by=row.referred_by,
        referral_count=row.referral_count,
        position=row.position,
        invited_at=_utc(row.invited_at),
        converted_at=_utc(row.converted_at),
        utm_source=row.utm_source,
        utm_medium=row.utm_medium,
        utm_campaign=row.utm_campaign,
        ip_hash=row.ip_hash,
        user_agent=row.user_agent,
        notes=row.notes,
        tags=list(row.tags or []),
    )


async def _row_for_email(session: Any, email: str) -> Any:
    from app.database.growth_models import WaitlistEntryDB

    return (
        await session.execute(
            select(WaitlistEntryDB).where(WaitlistEntryDB.email == email)
        )
    ).scalar_one_or_none()


async def _credit_referrer(session: Any, referred_by: str) -> None:
    """Give the referrer their +1, and the tier bump it may have earned."""
    referrer = await _row_for_email(session, referred_by.strip().lower())
    if referrer is None:
        return
    referrer.referral_count += 1
    if referrer.referral_count >= 3 and referrer.tier == WaitlistTier.STANDARD.value:
        referrer.tier = WaitlistTier.PRIORITY.value
        referrer.tags = [*(referrer.tags or []), "referral_upgrade"]


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
    from app.database.engine import get_session
    from app.database.growth_models import WaitlistEntryDB

    email = email.strip().lower()

    if not email:
        return None, "Email is required"

    if not _validate_email(email):
        return None, "Invalid email format"

    for _ in range(_POSITION_ATTEMPTS):
        async with get_session() as session:
            existing = await _row_for_email(session, email)
            if existing is not None:
                return _to_entry(existing), "Already on waitlist"

            # The counter this replaces restarted at 0 with the process, so the
            # first signup after a deploy was told it was #1 behind however many
            # thousand people were already on the list.
            next_position = (
                await session.execute(select(func.max(WaitlistEntryDB.position)))
            ).scalar() or 0

            row = WaitlistEntryDB(
                id=_generate_id(),
                email=email,
                position=next_position + 1,
                tier=WaitlistTier.STANDARD.value,
                source=source.value,
                company=company,
                role=role,
                team_size=team_size,
                use_case=use_case,
                referral_code=_generate_referral_code(email),
                referred_by=referred_by.strip().lower(),
                utm_source=utm_source,
                utm_medium=utm_medium,
                utm_campaign=utm_campaign,
                ip_hash=_hash_ip(ip_address) if ip_address else "",
                user_agent=user_agent[:200] if user_agent else "",
                tags=[],
            )
            session.add(row)
            if referred_by:
                await _credit_referrer(session, referred_by)

            try:
                await session.commit()
            except IntegrityError:
                # Either the email or the position was taken between the read
                # and the write. Both are settled by looping: the email lookup
                # at the top returns the winner's entry, and MAX(position) is
                # re-read.
                await session.rollback()
                continue

            entry = _to_entry(row)
            log.info(
                "Waitlist signup: position=%d source=%s company=%s",
                entry.position,
                source.value,
                company or "(none)",
            )
            return entry, "Successfully joined waitlist"

    log.warning("waitlist signup gave up after %d position collisions", _POSITION_ATTEMPTS)
    return None, "Could not join the waitlist right now — please try again."


async def get_waitlist_entry(email: str) -> WaitlistEntry | None:
    """Get waitlist entry by email."""
    from app.database.engine import get_session

    async with get_session() as session:
        row = await _row_for_email(session, email.strip().lower())
        return _to_entry(row) if row else None


async def get_waitlist_entries(
    tier: WaitlistTier | None = None,
    source: SignupSource | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[WaitlistEntry]:
    """Get waitlist entries with optional filtering."""
    from app.database.engine import get_session
    from app.database.growth_models import WaitlistEntryDB

    stmt = select(WaitlistEntryDB)
    if tier:
        stmt = stmt.where(WaitlistEntryDB.tier == tier.value)
    if source:
        stmt = stmt.where(WaitlistEntryDB.source == source.value)
    # Ordering, limit and offset are the database's job now; the dict version
    # loaded every row into memory to sort and slice it.
    stmt = stmt.order_by(WaitlistEntryDB.position).offset(offset).limit(limit)

    async with get_session() as session:
        rows = (await session.execute(stmt)).scalars().all()
        return [_to_entry(r) for r in rows]


async def promote_to_early_access(
    email: str,
    tier: WaitlistTier = WaitlistTier.EARLY_ACCESS,
) -> tuple[bool, str]:
    """Promote a waitlist entry to early access."""
    from app.database.engine import get_session

    async with get_session() as session:
        row = await _row_for_email(session, email.strip().lower())
        if row is None:
            return False, "Email not found in waitlist"
        if row.tier == WaitlistTier.FOUNDING_CUSTOMER.value:
            return False, "Already a founding customer"

        row.tier = tier.value
        row.invited_at = datetime.now(timezone.utc)
        row.tags = [*(row.tags or []), f"promoted_to_{tier.value}"]
        await session.commit()

        log.info("Promoted %s to %s", _to_entry(row)._mask_email(), tier.value)
        return True, f"Promoted to {tier.value}"


async def mark_converted(email: str) -> tuple[bool, str]:
    """Mark a waitlist entry as converted (became customer)."""
    from app.database.engine import get_session

    async with get_session() as session:
        row = await _row_for_email(session, email.strip().lower())
        if row is None:
            return False, "Email not found in waitlist"

        row.converted_at = datetime.now(timezone.utc)
        row.tier = WaitlistTier.FOUNDING_CUSTOMER.value
        row.tags = [*(row.tags or []), "converted"]
        await session.commit()

        log.info("Conversion: %s", _to_entry(row)._mask_email())
        return True, "Marked as converted"


async def get_waitlist_stats() -> dict[str, Any]:
    """Get waitlist statistics for dashboard."""
    entries = await get_waitlist_entries(limit=100_000)

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
        "conversion_rate": converted / total,
        "referral_rate": referred / total,
        "conversions": converted,
        "signups_today": signups_today,
        "signups_this_week": signups_this_week,
        "avg_position": sum(e.position for e in entries) / total,
    }


async def export_waitlist(format: str = "csv") -> str:
    """Export waitlist data.

    Args:
        format: 'csv' or 'json'

    Returns:
        Exported data as string
    """
    entries = await get_waitlist_entries(limit=10000)

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
        from app.database.engine import get_session
        from app.database.growth_models import WaitlistEntryDB

        async with get_session() as session:
            row = await _row_for_email(session, email.strip().lower())
            if row is None:
                return {
                    "found": False,
                    "error": "Email not found in waitlist",
                }
            entry = _to_entry(row)
            total = (
                await session.execute(select(func.count()).select_from(WaitlistEntryDB))
            ).scalar() or 0

        return {
            "found": True,
            "position": entry.position,
            "total": total,
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
    from app.database.engine import get_session
    from app.database.growth_models import WaitlistEntryDB

    async with get_session() as session:
        await session.execute(delete(WaitlistEntryDB))
        await session.commit()
