"""Referral and viral growth system.

Features:
- Referral link generation and tracking
- Two-sided rewards (referrer and referee)
- Viral coefficient calculation
- Referral campaign management
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Protocol

log = logging.getLogger("devcopilot.growth.referral")


class RewardType(str, Enum):
    CREDIT = "credit"
    DISCOUNT = "discount"
    TRIAL_EXTENSION = "trial_extension"
    FEATURE_UNLOCK = "feature_unlock"


class ReferralStatus(str, Enum):
    PENDING = "pending"
    SIGNED_UP = "signed_up"
    ACTIVATED = "activated"
    CONVERTED = "converted"
    EXPIRED = "expired"


@dataclass
class ReferralReward:
    """Reward configuration for referrals."""

    id: str
    name: str
    reward_type: RewardType
    value: int

    referrer_reward_type: RewardType = RewardType.CREDIT
    referrer_value: int = 0
    referee_reward_type: RewardType = RewardType.CREDIT
    referee_value: int = 0

    requires_conversion: bool = True
    max_rewards_per_user: int = 10

    valid_from: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    valid_until: datetime | None = None

    @property
    def is_active(self) -> bool:
        now = datetime.now(timezone.utc)
        if now < self.valid_from:
            return False
        if self.valid_until and now > self.valid_until:
            return False
        return True


@dataclass
class ReferralLink:
    """A referral link with tracking."""

    id: str
    code: str
    referrer_id: str
    referrer_org_id: str

    campaign_id: str = "default"
    reward_id: str = ""

    clicks: int = 0
    signups: int = 0
    activations: int = 0
    conversions: int = 0

    rewards_earned_cents: int = 0
    rewards_paid_cents: int = 0

    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime | None = None

    @property
    def is_active(self) -> bool:
        if self.expires_at and datetime.now(timezone.utc) > self.expires_at:
            return False
        return True

    @property
    def signup_rate(self) -> float:
        if self.clicks == 0:
            return 0.0
        return self.signups / self.clicks

    @property
    def conversion_rate(self) -> float:
        if self.signups == 0:
            return 0.0
        return self.conversions / self.signups

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "code": self.code,
            "url": f"https://app.devcopilot.io/r/{self.code}",
            "clicks": self.clicks,
            "signups": self.signups,
            "activations": self.activations,
            "conversions": self.conversions,
            "signup_rate": self.signup_rate,
            "conversion_rate": self.conversion_rate,
            "rewards_earned": self.rewards_earned_cents / 100,
            "is_active": self.is_active,
        }


@dataclass
class Referral:
    """Individual referral record."""

    id: str
    link_id: str
    referrer_id: str
    referee_id: str

    status: ReferralStatus = ReferralStatus.PENDING

    referrer_rewarded: bool = False
    referee_rewarded: bool = False

    signed_up_at: datetime | None = None
    activated_at: datetime | None = None
    converted_at: datetime | None = None

    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class ReferralStore(Protocol):
    """Protocol for referral data storage."""

    async def create_link(self, link: ReferralLink) -> str: ...
    async def get_link(self, link_id: str) -> ReferralLink | None: ...
    async def get_link_by_code(self, code: str) -> ReferralLink | None: ...
    async def update_link(self, link: ReferralLink) -> None: ...
    async def get_user_links(self, user_id: str) -> list[ReferralLink]: ...

    async def create_referral(self, referral: Referral) -> str: ...
    async def get_referral(self, referral_id: str) -> Referral | None: ...
    async def update_referral(self, referral: Referral) -> None: ...
    async def get_referrals_by_link(self, link_id: str) -> list[Referral]: ...
    async def get_referrals_by_referrer(self, user_id: str) -> list[Referral]: ...

    async def get_reward(self, reward_id: str) -> ReferralReward | None: ...
    async def apply_credit(self, user_id: str, amount_cents: int, reason: str) -> None: ...

    async def get_total_users(self) -> int: ...
    async def get_referred_users_count(self, since: datetime) -> int: ...
    async def get_referrers_count(self, since: datetime) -> int: ...


class ReferralManager:
    """Manages referral program."""

    def __init__(self, store: ReferralStore):
        self._store = store

    async def create_referral_link(
        self,
        user_id: str,
        org_id: str,
        campaign_id: str = "default",
        expires_days: int | None = None,
    ) -> ReferralLink:
        """Create a new referral link for a user."""
        code = self._generate_code(user_id)
        link_id = f"ref_{code}"

        expires_at = None
        if expires_days:
            expires_at = datetime.now(timezone.utc) + timedelta(days=expires_days)

        link = ReferralLink(
            id=link_id,
            code=code,
            referrer_id=user_id,
            referrer_org_id=org_id,
            campaign_id=campaign_id,
            expires_at=expires_at,
        )

        await self._store.create_link(link)

        log.info("Created referral link %s for user %s", code, user_id)
        return link

    def _generate_code(self, user_id: str) -> str:
        """Generate a unique referral code."""
        random_part = secrets.token_urlsafe(4)
        hash_part = hashlib.sha256(user_id.encode()).hexdigest()[:4]
        return f"{random_part}{hash_part}".upper()

    async def track_click(self, code: str) -> ReferralLink | None:
        """Track a click on a referral link."""
        link = await self._store.get_link_by_code(code)
        if not link or not link.is_active:
            return None

        link.clicks += 1
        await self._store.update_link(link)
        return link

    async def track_signup(
        self,
        code: str,
        referee_id: str,
    ) -> Referral | None:
        """Track a signup from a referral link."""
        link = await self._store.get_link_by_code(code)
        if not link or not link.is_active:
            return None

        now = datetime.now(timezone.utc)
        referral = Referral(
            id=f"ref_{referee_id}",
            link_id=link.id,
            referrer_id=link.referrer_id,
            referee_id=referee_id,
            status=ReferralStatus.SIGNED_UP,
            signed_up_at=now,
        )

        await self._store.create_referral(referral)

        link.signups += 1
        await self._store.update_link(link)

        log.info(
            "Referral signup: link=%s referrer=%s referee=%s",
            code,
            link.referrer_id,
            referee_id,
        )
        return referral

    async def track_activation(self, referee_id: str) -> Referral | None:
        """Track when a referred user activates."""
        referrals = await self._store.get_referrals_by_link("")

        referral = None
        for r in referrals:
            if r.referee_id == referee_id:
                referral = r
                break

        if not referral:
            return None

        now = datetime.now(timezone.utc)
        referral.status = ReferralStatus.ACTIVATED
        referral.activated_at = now
        await self._store.update_referral(referral)

        link = await self._store.get_link(referral.link_id)
        if link:
            link.activations += 1
            await self._store.update_link(link)

        log.info("Referral activated: referee=%s", referee_id)
        return referral

    async def track_conversion(
        self,
        referee_id: str,
    ) -> Referral | None:
        """Track when a referred user converts to paid."""
        referrals = await self._store.get_referrals_by_referrer("")
        referral = None
        for r in referrals:
            if r.referee_id == referee_id:
                referral = r
                break

        if not referral:
            return None

        now = datetime.now(timezone.utc)
        referral.status = ReferralStatus.CONVERTED
        referral.converted_at = now
        await self._store.update_referral(referral)

        link = await self._store.get_link(referral.link_id)
        if link:
            link.conversions += 1
            await self._store.update_link(link)

            await self._process_rewards(link, referral)

        log.info(
            "Referral converted: referee=%s referrer=%s",
            referee_id,
            referral.referrer_id,
        )
        return referral

    async def _process_rewards(
        self,
        link: ReferralLink,
        referral: Referral,
    ) -> None:
        """Process rewards for both referrer and referee."""
        if not link.reward_id:
            reward_amount = 1000
        else:
            reward = await self._store.get_reward(link.reward_id)
            reward_amount = reward.referrer_value if reward else 1000

        if not referral.referrer_rewarded:
            await self._store.apply_credit(
                referral.referrer_id,
                reward_amount,
                f"Referral reward for {referral.referee_id}",
            )
            referral.referrer_rewarded = True
            link.rewards_earned_cents += reward_amount

        if not referral.referee_rewarded:
            referee_reward = reward_amount // 2
            await self._store.apply_credit(
                referral.referee_id,
                referee_reward,
                "Welcome reward from referral",
            )
            referral.referee_rewarded = True

        await self._store.update_referral(referral)
        await self._store.update_link(link)

    async def get_user_referrals(
        self,
        user_id: str,
    ) -> dict[str, Any]:
        """Get referral statistics for a user."""
        links = await self._store.get_user_links(user_id)

        total_clicks = sum(link.clicks for link in links)
        total_signups = sum(link.signups for link in links)
        total_conversions = sum(link.conversions for link in links)
        total_earned = sum(link.rewards_earned_cents for link in links)

        return {
            "links": [link.to_dict() for link in links],
            "totals": {
                "clicks": total_clicks,
                "signups": total_signups,
                "conversions": total_conversions,
                "rewards_earned": total_earned / 100,
            },
            "conversion_rate": (
                total_conversions / total_signups if total_signups > 0 else 0.0
            ),
        }

    async def calculate_viral_coefficient(
        self,
        days: int = 30,
    ) -> dict[str, float]:
        """Calculate viral coefficient (K-factor)."""
        since = datetime.now(timezone.utc) - timedelta(days=days)

        total_users = await self._store.get_total_users()
        referred = await self._store.get_referred_users_count(since)
        referrers = await self._store.get_referrers_count(since)

        if referrers == 0:
            invites_per_user = 0.0
        else:
            invites_per_user = referred / referrers

        conversion_rate = referred / total_users if total_users > 0 else 0.0
        k_factor = invites_per_user * conversion_rate

        return {
            "k_factor": k_factor,
            "invites_per_user": invites_per_user,
            "conversion_rate": conversion_rate,
            "is_viral": k_factor > 1.0,
            "period_days": days,
            "total_users": total_users,
            "referred_users": referred,
            "active_referrers": referrers,
        }

    async def get_leaderboard(
        self,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Get top referrers leaderboard."""
        all_links = await self._store.get_user_links("")

        user_stats: dict[str, dict[str, Any]] = {}
        for link in all_links:
            if link.referrer_id not in user_stats:
                user_stats[link.referrer_id] = {
                    "user_id": link.referrer_id,
                    "conversions": 0,
                    "earnings_cents": 0,
                }
            user_stats[link.referrer_id]["conversions"] += link.conversions
            user_stats[link.referrer_id]["earnings_cents"] += link.rewards_earned_cents

        sorted_users = sorted(
            user_stats.values(),
            key=lambda x: x["conversions"],
            reverse=True,
        )

        return [
            {
                "rank": i + 1,
                "user_id": u["user_id"],
                "conversions": u["conversions"],
                "earnings": u["earnings_cents"] / 100,
            }
            for i, u in enumerate(sorted_users[:limit])
        ]
