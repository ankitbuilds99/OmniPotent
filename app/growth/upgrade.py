"""Intelligent upgrade timing engine.

Determines the optimal moment to prompt for upgrades based on
user behavior, value delivered, and conversion psychology.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("devcopilot.growth.upgrade")


class UpgradeMomentType(str, Enum):
    GOOD = "good"
    BAD = "bad"
    NEUTRAL = "neutral"


class UpgradeContext(str, Enum):
    POST_INVESTIGATION = "post_investigation"
    AT_LIMIT = "at_limit"
    VALUE_MILESTONE = "value_milestone"
    TEAM_GROWING = "team_growing"
    FEATURE_BLOCKED = "feature_blocked"
    DURING_INCIDENT = "during_incident"
    FIRST_LOGIN = "first_login"
    INVESTIGATION_FAILED = "investigation_failed"
    JUST_DISMISSED = "just_dismissed"
    INTEGRATION_LIMIT = "integration_limit"


@dataclass
class UpgradeMoment:
    """A potential upgrade prompt moment."""

    context: UpgradeContext
    moment_type: UpgradeMomentType
    score: float
    reason: str
    recommended_action: str
    cooldown_hours: int = 24

    def to_dict(self) -> dict[str, Any]:
        return {
            "context": self.context.value,
            "type": self.moment_type.value,
            "score": round(self.score, 2),
            "reason": self.reason,
            "action": self.recommended_action,
            "cooldown_hours": self.cooldown_hours,
        }


@dataclass
class UserUpgradeState:
    """User's upgrade prompt state."""

    user_id: str
    org_id: str
    tier: str
    last_prompt_at: datetime | None = None
    prompts_shown: int = 0
    prompts_dismissed: int = 0
    last_dismissed_at: datetime | None = None
    value_delivered: float = 0.0
    investigations_run: int = 0
    team_size: int = 1
    integrations_connected: int = 0
    is_during_incident: bool = False
    last_investigation_at: datetime | None = None
    last_investigation_success: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "tier": self.tier,
            "prompts_shown": self.prompts_shown,
            "prompts_dismissed": self.prompts_dismissed,
            "value_delivered": self.value_delivered,
            "investigations_run": self.investigations_run,
        }


@dataclass
class UpgradeRecommendation:
    """Recommendation for upgrade prompt."""

    should_prompt: bool
    moment: UpgradeMoment | None
    message: str
    cta_text: str
    urgency: str
    show_value: bool = True
    value_message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "should_prompt": self.should_prompt,
            "moment": self.moment.to_dict() if self.moment else None,
            "message": self.message,
            "cta_text": self.cta_text,
            "urgency": self.urgency,
            "show_value": self.show_value,
            "value_message": self.value_message,
        }


class UpgradeTimingEngine:
    """Determines optimal upgrade prompt timing."""

    def __init__(self) -> None:
        self._user_states: dict[str, UserUpgradeState] = {}
        self._prompt_history: dict[str, list[datetime]] = {}

        self._good_moments = {
            UpgradeContext.POST_INVESTIGATION: UpgradeMoment(
                UpgradeContext.POST_INVESTIGATION,
                UpgradeMomentType.GOOD,
                0.8,
                "User just completed successful investigation",
                "Show value and suggest Pro for unlimited",
                cooldown_hours=48,
            ),
            UpgradeContext.AT_LIMIT: UpgradeMoment(
                UpgradeContext.AT_LIMIT,
                UpgradeMomentType.GOOD,
                0.9,
                "User hit usage limit during active work",
                "Show urgent upgrade with immediate benefit",
                cooldown_hours=1,
            ),
            UpgradeContext.VALUE_MILESTONE: UpgradeMoment(
                UpgradeContext.VALUE_MILESTONE,
                UpgradeMomentType.GOOD,
                0.85,
                "User reached significant value milestone",
                "Highlight ROI and suggest upgrade",
                cooldown_hours=72,
            ),
            UpgradeContext.TEAM_GROWING: UpgradeMoment(
                UpgradeContext.TEAM_GROWING,
                UpgradeMomentType.GOOD,
                0.75,
                "User is adding team members",
                "Suggest Pro for larger team limits",
                cooldown_hours=168,
            ),
            UpgradeContext.INTEGRATION_LIMIT: UpgradeMoment(
                UpgradeContext.INTEGRATION_LIMIT,
                UpgradeMomentType.GOOD,
                0.7,
                "User trying to connect 6th integration",
                "Suggest Pro for more integrations",
                cooldown_hours=24,
            ),
        }

        self._bad_moments = {
            UpgradeContext.DURING_INCIDENT: UpgradeMoment(
                UpgradeContext.DURING_INCIDENT,
                UpgradeMomentType.BAD,
                0.1,
                "User is dealing with active incident",
                "Do not interrupt - they're stressed",
                cooldown_hours=0,
            ),
            UpgradeContext.FIRST_LOGIN: UpgradeMoment(
                UpgradeContext.FIRST_LOGIN,
                UpgradeMomentType.BAD,
                0.05,
                "User hasn't seen value yet",
                "Let them experience the product first",
                cooldown_hours=0,
            ),
            UpgradeContext.INVESTIGATION_FAILED: UpgradeMoment(
                UpgradeContext.INVESTIGATION_FAILED,
                UpgradeMomentType.BAD,
                0.1,
                "Investigation just failed",
                "User is frustrated - not the time",
                cooldown_hours=0,
            ),
            UpgradeContext.JUST_DISMISSED: UpgradeMoment(
                UpgradeContext.JUST_DISMISSED,
                UpgradeMomentType.BAD,
                0.0,
                "Just dismissed upgrade prompt",
                "Respect their decision",
                cooldown_hours=24,
            ),
        }

        self._value_thresholds = [500, 1000, 2500, 5000, 10000]

    def update_user_state(
        self,
        user_id: str,
        org_id: str,
        tier: str,
        **kwargs: Any,
    ) -> UserUpgradeState:
        """Update or create user upgrade state."""
        if user_id not in self._user_states:
            self._user_states[user_id] = UserUpgradeState(
                user_id=user_id,
                org_id=org_id,
                tier=tier,
            )

        state = self._user_states[user_id]
        state.tier = tier

        for key, value in kwargs.items():
            if hasattr(state, key):
                setattr(state, key, value)

        return state

    def record_investigation(
        self,
        user_id: str,
        success: bool,
        value_added: float = 0.0,
    ) -> None:
        """Record an investigation completion."""
        if user_id not in self._user_states:
            return

        state = self._user_states[user_id]
        state.investigations_run += 1
        state.last_investigation_at = datetime.now(timezone.utc)
        state.last_investigation_success = success
        state.value_delivered += value_added

    def record_prompt_shown(self, user_id: str) -> None:
        """Record that an upgrade prompt was shown."""
        if user_id not in self._user_states:
            return

        state = self._user_states[user_id]
        state.prompts_shown += 1
        state.last_prompt_at = datetime.now(timezone.utc)

        if user_id not in self._prompt_history:
            self._prompt_history[user_id] = []
        self._prompt_history[user_id].append(datetime.now(timezone.utc))

    def record_prompt_dismissed(self, user_id: str) -> None:
        """Record that user dismissed upgrade prompt."""
        if user_id not in self._user_states:
            return

        state = self._user_states[user_id]
        state.prompts_dismissed += 1
        state.last_dismissed_at = datetime.now(timezone.utc)

    def should_show_upgrade(
        self,
        user_id: str,
        context: UpgradeContext | None = None,
    ) -> UpgradeRecommendation:
        """Determine if upgrade prompt should be shown."""
        state = self._user_states.get(user_id)

        if not state:
            return UpgradeRecommendation(
                should_prompt=False,
                moment=None,
                message="",
                cta_text="",
                urgency="none",
            )

        if state.tier != "free":
            return UpgradeRecommendation(
                should_prompt=False,
                moment=None,
                message="Already on paid tier",
                cta_text="",
                urgency="none",
            )

        if context and context in self._bad_moments:
            moment = self._bad_moments[context]
            return UpgradeRecommendation(
                should_prompt=False,
                moment=moment,
                message=moment.reason,
                cta_text="",
                urgency="none",
            )

        if not self._check_cooldown(state):
            return UpgradeRecommendation(
                should_prompt=False,
                moment=None,
                message="In cooldown period",
                cta_text="",
                urgency="none",
            )

        if self._prompts_today(user_id) >= 1:
            return UpgradeRecommendation(
                should_prompt=False,
                moment=None,
                message="Already prompted today",
                cta_text="",
                urgency="none",
            )

        best_moment = self._evaluate_moment(state, context)

        if not best_moment or best_moment.score < 0.5:
            return UpgradeRecommendation(
                should_prompt=False,
                moment=best_moment,
                message="Not a good moment",
                cta_text="",
                urgency="none",
            )

        return self._build_recommendation(state, best_moment)

    def _check_cooldown(self, state: UserUpgradeState) -> bool:
        """Check if user is in cooldown period."""
        if state.last_dismissed_at:
            cooldown = timedelta(hours=24)
            if datetime.now(timezone.utc) - state.last_dismissed_at < cooldown:
                return False

        if state.last_prompt_at:
            min_gap = timedelta(hours=4)
            if datetime.now(timezone.utc) - state.last_prompt_at < min_gap:
                return False

        return True

    def _prompts_today(self, user_id: str) -> int:
        """Count prompts shown today."""
        history = self._prompt_history.get(user_id, [])
        today = datetime.now(timezone.utc).date()
        return sum(1 for dt in history if dt.date() == today)

    def _evaluate_moment(
        self,
        state: UserUpgradeState,
        context: UpgradeContext | None,
    ) -> UpgradeMoment | None:
        """Evaluate current moment for upgrade prompt."""
        if context and context in self._good_moments:
            return self._good_moments[context]

        best_moment = None
        best_score = 0.0

        for threshold in self._value_thresholds:
            if state.value_delivered >= threshold:
                moment = self._good_moments[UpgradeContext.VALUE_MILESTONE]
                if moment.score > best_score:
                    best_moment = moment
                    best_score = moment.score

        if state.team_size >= 3:
            moment = self._good_moments[UpgradeContext.TEAM_GROWING]
            if moment.score > best_score:
                best_moment = moment
                best_score = moment.score

        if (
            state.last_investigation_at
            and state.last_investigation_success
            and datetime.now(timezone.utc) - state.last_investigation_at < timedelta(minutes=5)
        ):
            moment = self._good_moments[UpgradeContext.POST_INVESTIGATION]
            if moment.score > best_score:
                best_moment = moment
                best_score = moment.score

        return best_moment

    def _build_recommendation(
        self,
        state: UserUpgradeState,
        moment: UpgradeMoment,
    ) -> UpgradeRecommendation:
        """Build upgrade recommendation."""
        if moment.context == UpgradeContext.AT_LIMIT:
            urgency = "high"
            message = "You've reached your investigation limit. Upgrade now to continue."
            cta = "Upgrade Now"
        elif moment.context == UpgradeContext.VALUE_MILESTONE:
            urgency = "medium"
            message = f"You've saved ${state.value_delivered:,.0f}! Pro pays for itself instantly."
            cta = "See Pro Benefits"
        elif moment.context == UpgradeContext.POST_INVESTIGATION:
            urgency = "low"
            message = "Great investigation! Want unlimited access?"
            cta = "Upgrade to Pro"
        else:
            urgency = "low"
            message = "Upgrade to unlock more features"
            cta = "View Plans"

        value_message = ""
        if state.value_delivered > 0:
            pro_value = state.value_delivered * 4
            value_message = f"Pro would deliver ${pro_value:,.0f}/month in value"

        return UpgradeRecommendation(
            should_prompt=True,
            moment=moment,
            message=message,
            cta_text=cta,
            urgency=urgency,
            show_value=state.value_delivered > 100,
            value_message=value_message,
        )

    def get_user_status(self, user_id: str) -> dict[str, Any]:
        """Get user's upgrade status for admin view."""
        state = self._user_states.get(user_id)
        if not state:
            return {"found": False}

        last_prompt = state.last_prompt_at.isoformat() if state.last_prompt_at else None
        next_eligible = None
        if state.last_prompt_at:
            next_eligible = (state.last_prompt_at + timedelta(hours=4)).isoformat()

        return {
            "found": True,
            "state": state.to_dict(),
            "last_prompt": last_prompt,
            "next_eligible": next_eligible,
            "prompts_today": self._prompts_today(user_id),
            "conversion_likelihood": self._estimate_conversion(state),
            "recommended_action": self._get_recommended_action(state),
        }

    def _estimate_conversion(self, state: UserUpgradeState) -> float:
        """Estimate likelihood of conversion."""
        score = 0.0

        if state.value_delivered >= 1000:
            score += 0.3
        elif state.value_delivered >= 500:
            score += 0.2
        elif state.value_delivered >= 100:
            score += 0.1

        if state.investigations_run >= 8:
            score += 0.2
        elif state.investigations_run >= 5:
            score += 0.1

        if state.team_size >= 3:
            score += 0.2
        elif state.team_size >= 2:
            score += 0.1

        if state.integrations_connected >= 4:
            score += 0.15

        if state.prompts_dismissed > 3:
            score -= 0.2

        return max(0.0, min(1.0, score))

    def _get_recommended_action(self, state: UserUpgradeState) -> str:
        """Get recommended action for this user."""
        likelihood = self._estimate_conversion(state)

        if likelihood >= 0.7:
            return "High conversion likelihood - prompt after next investigation"
        elif likelihood >= 0.5:
            return "Medium likelihood - wait for value milestone"
        elif likelihood >= 0.3:
            return "Low likelihood - focus on delivering more value"
        else:
            return "Very low likelihood - no action recommended"


_engine: UpgradeTimingEngine | None = None


def get_upgrade_timing_engine() -> UpgradeTimingEngine:
    """Get the default upgrade timing engine."""
    global _engine
    if _engine is None:
        _engine = UpgradeTimingEngine()
    return _engine
