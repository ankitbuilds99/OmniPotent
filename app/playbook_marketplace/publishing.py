"""Playbook Publishing.

Manages playbook publishing, verification, and revenue sharing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("devcopilot.playbook_marketplace.publishing")


class PublisherStatus(str, Enum):
    PENDING = "pending"
    VERIFIED = "verified"
    SUSPENDED = "suspended"


@dataclass
class Publisher:
    """A playbook publisher."""

    publisher_id: str
    name: str
    email: str
    bio: str = ""
    website: str = ""
    status: PublisherStatus = PublisherStatus.PENDING
    total_playbooks: int = 0
    total_installs: int = 0
    total_revenue: float = 0.0
    revenue_share_percent: float = 70.0
    joined_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    verified_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.publisher_id,
            "name": self.name,
            "email": self.email,
            "bio": self.bio,
            "website": self.website,
            "status": self.status.value,
            "playbooks": self.total_playbooks,
            "installs": self.total_installs,
            "revenue": round(self.total_revenue, 2),
            "revenue_share": self.revenue_share_percent,
            "verified": self.status == PublisherStatus.VERIFIED,
            "joined_at": self.joined_at.isoformat(),
        }


@dataclass
class RevenueRecord:
    """Revenue record for a premium playbook."""

    record_id: str
    publisher_id: str
    playbook_id: str
    org_id: str
    amount: float
    publisher_share: float
    period_start: datetime
    period_end: datetime
    paid: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.record_id,
            "playbook_id": self.playbook_id,
            "amount": round(self.amount, 2),
            "publisher_share": round(self.publisher_share, 2),
            "period": f"{self.period_start.date()} - {self.period_end.date()}",
            "paid": self.paid,
        }


class PublishingManager:
    """Manages playbook publishing and revenue."""

    def __init__(self) -> None:
        self._publishers: dict[str, Publisher] = {}
        self._revenue: dict[str, list[RevenueRecord]] = {}
        self._playbook_publishers: dict[str, str] = {}

    def register_publisher(
        self,
        user_id: str,
        name: str,
        email: str,
        bio: str = "",
    ) -> Publisher:
        """Register a new publisher."""
        publisher = Publisher(
            publisher_id=user_id,
            name=name,
            email=email,
            bio=bio,
        )

        self._publishers[user_id] = publisher

        log.info("Registered publisher: id=%s name=%s", user_id, name)

        return publisher

    def get_publisher(self, publisher_id: str) -> Publisher | None:
        """Get a publisher by ID."""
        return self._publishers.get(publisher_id)

    def apply_for_verification(self, publisher_id: str) -> dict[str, Any]:
        """Apply for verified publisher status."""
        publisher = self._publishers.get(publisher_id)
        if not publisher:
            return {"error": "Publisher not found"}

        if publisher.status == PublisherStatus.VERIFIED:
            return {"error": "Already verified"}

        requirements = []
        if publisher.total_playbooks < 1:
            requirements.append("Publish at least 1 playbook")
        if publisher.total_installs < 10:
            requirements.append("Get at least 10 total installs")

        if requirements:
            return {
                "eligible": False,
                "requirements": requirements,
            }

        return {
            "eligible": True,
            "submitted": True,
            "estimated_days": 5,
        }

    def verify_publisher(self, publisher_id: str) -> dict[str, Any]:
        """Verify a publisher (admin action)."""
        publisher = self._publishers.get(publisher_id)
        if not publisher:
            return {"error": "Publisher not found"}

        publisher.status = PublisherStatus.VERIFIED
        publisher.verified_at = datetime.now(timezone.utc)

        log.info("Verified publisher: id=%s", publisher_id)

        return {
            "success": True,
            "publisher": publisher.to_dict(),
        }

    def assign_playbook(self, playbook_id: str, publisher_id: str) -> None:
        """Assign a playbook to a publisher."""
        self._playbook_publishers[playbook_id] = publisher_id

        publisher = self._publishers.get(publisher_id)
        if publisher:
            publisher.total_playbooks += 1

    def record_install(self, playbook_id: str) -> None:
        """Record an install for revenue tracking."""
        publisher_id = self._playbook_publishers.get(playbook_id)
        if publisher_id:
            publisher = self._publishers.get(publisher_id)
            if publisher:
                publisher.total_installs += 1

    def record_revenue(
        self,
        playbook_id: str,
        org_id: str,
        amount: float,
        period_start: datetime,
        period_end: datetime,
    ) -> RevenueRecord | None:
        """Record revenue for a premium playbook."""
        publisher_id = self._playbook_publishers.get(playbook_id)
        if not publisher_id:
            return None

        publisher = self._publishers.get(publisher_id)
        if not publisher:
            return None

        publisher_share = amount * (publisher.revenue_share_percent / 100)

        record = RevenueRecord(
            record_id=f"rev_{datetime.now(timezone.utc).timestamp()}",
            publisher_id=publisher_id,
            playbook_id=playbook_id,
            org_id=org_id,
            amount=amount,
            publisher_share=publisher_share,
            period_start=period_start,
            period_end=period_end,
        )

        if publisher_id not in self._revenue:
            self._revenue[publisher_id] = []
        self._revenue[publisher_id].append(record)

        publisher.total_revenue += publisher_share

        log.info(
            "Recorded revenue: playbook=%s amount=%.2f share=%.2f",
            playbook_id,
            amount,
            publisher_share,
        )

        return record

    def get_revenue_report(
        self,
        publisher_id: str,
        limit: int = 12,
    ) -> dict[str, Any]:
        """Get revenue report for a publisher."""
        publisher = self._publishers.get(publisher_id)
        if not publisher:
            return {"error": "Publisher not found"}

        records = self._revenue.get(publisher_id, [])
        records.sort(key=lambda r: r.period_start, reverse=True)

        pending = sum(r.publisher_share for r in records if not r.paid)
        paid = sum(r.publisher_share for r in records if r.paid)

        return {
            "publisher": publisher.to_dict(),
            "total_earned": round(publisher.total_revenue, 2),
            "pending_payout": round(pending, 2),
            "total_paid": round(paid, 2),
            "recent_records": [r.to_dict() for r in records[:limit]],
        }

    def get_leaderboard(self, limit: int = 10) -> list[dict[str, Any]]:
        """Get publisher leaderboard."""
        publishers = list(self._publishers.values())
        publishers.sort(key=lambda p: p.total_installs, reverse=True)

        return [
            {
                "rank": i + 1,
                **p.to_dict(),
            }
            for i, p in enumerate(publishers[:limit])
        ]


_manager: PublishingManager | None = None


def get_publishing_manager() -> PublishingManager:
    """Get the default publishing manager."""
    global _manager
    if _manager is None:
        _manager = PublishingManager()
    return _manager
