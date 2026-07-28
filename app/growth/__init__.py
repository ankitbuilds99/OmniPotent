"""Growth and referral module.

Provides:
- Referral link generation
- Reward tracking
- Viral coefficient calculation
"""

from app.growth.referral import (
    ReferralLink,
    ReferralReward,
    RewardType,
    ReferralManager,
)

__all__ = [
    "ReferralLink",
    "ReferralReward",
    "RewardType",
    "ReferralManager",
]
