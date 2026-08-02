"""Onboarding health score and activation tracking.

Tracks user activation progress and predicts conversion likelihood.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("omnipotent.onboarding.health")


class ActivationCheckStatus(str, Enum):
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    SKIPPED = "skipped"


@dataclass
class ActivationCheck:
    """A single activation checkpoint."""

    check_id: str
    name: str
    description: str
    points: int
    status: ActivationCheckStatus = ActivationCheckStatus.NOT_STARTED
    completed_at: datetime | None = None
    is_required: bool = True
    order: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.check_id,
            "name": self.name,
            "description": self.description,
            "points": self.points,
            "status": self.status.value,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "is_required": self.is_required,
        }


@dataclass
class ActivationChecklist:
    """Full activation checklist for a user."""

    user_id: str
    checks: list[ActivationCheck]
    total_points: int = 100
    earned_points: int = 0

    def get_score(self) -> int:
        """Get current activation score."""
        return self.earned_points

    def get_completion_percent(self) -> float:
        """Get completion percentage."""
        if self.total_points == 0:
            return 0.0
        return (self.earned_points / self.total_points) * 100

    def get_next_step(self) -> ActivationCheck | None:
        """Get next uncompleted step."""
        for check in sorted(self.checks, key=lambda c: c.order):
            if check.status == ActivationCheckStatus.NOT_STARTED:
                return check
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "score": self.earned_points,
            "total": self.total_points,
            "percent": round(self.get_completion_percent(), 1),
            "checks": [c.to_dict() for c in self.checks],
            "next_step": next_step.to_dict() if (next_step := self.get_next_step()) else None,
        }


@dataclass
class OnboardingHealthScore:
    """Health score for a user's onboarding."""

    user_id: str
    score: int
    status: str
    conversion_likelihood: float
    time_to_value_minutes: int | None
    risk_factors: list[str]
    suggested_nudge: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "score": self.score,
            "status": self.status,
            "conversion_likelihood": round(self.conversion_likelihood * 100, 1),
            "time_to_value_minutes": self.time_to_value_minutes,
            "risk_factors": self.risk_factors,
            "suggested_nudge": self.suggested_nudge,
        }


class OnboardingHealth:
    """Tracks onboarding health and predicts conversion."""

    def __init__(self) -> None:
        self._checklists: dict[str, ActivationChecklist] = {}
        self._signup_times: dict[str, datetime] = {}
        self._first_value_times: dict[str, datetime] = {}

        self._default_checks = [
            ActivationCheck("account_created", "Account Created", "Sign up complete", 10, order=1),
            ActivationCheck("profile_completed", "Profile Completed", "Added role and team info", 10, order=2),
            ActivationCheck("demo_completed", "Demo Completed", "Tried demo investigation", 15, order=3, is_required=False),
            ActivationCheck("first_integration", "First Integration", "Connected first tool", 20, order=4),
            ActivationCheck("first_investigation", "First Investigation", "Ran first AI investigation", 25, order=5),
            ActivationCheck("teammate_invited", "Teammate Invited", "Invited a team member", 10, order=6, is_required=False),
            ActivationCheck("second_investigation", "Second Investigation", "Ran second investigation", 10, order=7),
        ]

    def create_checklist(self, user_id: str) -> ActivationChecklist:
        """Create a new activation checklist for a user."""
        checks = [
            ActivationCheck(
                c.check_id, c.name, c.description, c.points,
                order=c.order, is_required=c.is_required
            )
            for c in self._default_checks
        ]

        checklist = ActivationChecklist(
            user_id=user_id,
            checks=checks,
            total_points=sum(c.points for c in checks),
        )

        self._checklists[user_id] = checklist
        self._signup_times[user_id] = datetime.now(timezone.utc)

        return checklist

    def get_checklist(self, user_id: str) -> ActivationChecklist:
        """Get user's activation checklist."""
        if user_id not in self._checklists:
            return self.create_checklist(user_id)
        return self._checklists[user_id]

    def complete_check(
        self,
        user_id: str,
        check_id: str,
    ) -> ActivationChecklist:
        """Mark a check as completed."""
        checklist = self.get_checklist(user_id)

        for check in checklist.checks:
            if check.check_id == check_id and check.status != ActivationCheckStatus.COMPLETED:
                check.status = ActivationCheckStatus.COMPLETED
                check.completed_at = datetime.now(timezone.utc)
                checklist.earned_points += check.points

                if check_id == "first_investigation":
                    self._first_value_times[user_id] = datetime.now(timezone.utc)

                log.info("Activation check completed: user=%s check=%s", user_id, check_id)
                break

        return checklist

    def skip_check(self, user_id: str, check_id: str) -> ActivationChecklist:
        """Skip a check."""
        checklist = self.get_checklist(user_id)

        for check in checklist.checks:
            if check.check_id == check_id and not check.is_required:
                check.status = ActivationCheckStatus.SKIPPED
                break

        return checklist

    def get_health_score(self, user_id: str) -> OnboardingHealthScore:
        """Calculate onboarding health score."""
        checklist = self.get_checklist(user_id)
        score = checklist.earned_points

        status = self._determine_status(user_id, score)
        likelihood = self._calculate_conversion_likelihood(user_id, checklist)
        risk_factors = self._identify_risk_factors(user_id, checklist)
        nudge = self._generate_nudge(user_id, checklist, risk_factors)

        time_to_value = None
        if user_id in self._first_value_times and user_id in self._signup_times:
            delta = self._first_value_times[user_id] - self._signup_times[user_id]
            time_to_value = int(delta.total_seconds() / 60)

        return OnboardingHealthScore(
            user_id=user_id,
            score=score,
            status=status,
            conversion_likelihood=likelihood,
            time_to_value_minutes=time_to_value,
            risk_factors=risk_factors,
            suggested_nudge=nudge,
        )

    def _determine_status(self, user_id: str, score: int) -> str:
        """Determine onboarding status."""
        signup_time = self._signup_times.get(user_id)
        if not signup_time:
            return "UNKNOWN"

        hours_since_signup = (datetime.now(timezone.utc) - signup_time).total_seconds() / 3600

        if score >= 80:
            return "ACTIVATED"
        elif score >= 50:
            return "ON_TRACK"
        elif hours_since_signup < 24:
            return "JUST_STARTED"
        elif score >= 20:
            return "NEEDS_ATTENTION"
        else:
            return "AT_RISK"

    def _calculate_conversion_likelihood(
        self,
        user_id: str,
        checklist: ActivationChecklist,
    ) -> float:
        """Calculate likelihood of converting to active user."""
        score = 0.0

        completion_percent = checklist.get_completion_percent()
        score += completion_percent * 0.005

        first_inv = next(
            (c for c in checklist.checks if c.check_id == "first_investigation"),
            None
        )
        if first_inv and first_inv.status == ActivationCheckStatus.COMPLETED:
            score += 0.25

        integration = next(
            (c for c in checklist.checks if c.check_id == "first_integration"),
            None
        )
        if integration and integration.status == ActivationCheckStatus.COMPLETED:
            score += 0.15

        teammate = next(
            (c for c in checklist.checks if c.check_id == "teammate_invited"),
            None
        )
        if teammate and teammate.status == ActivationCheckStatus.COMPLETED:
            score += 0.1

        signup_time = self._signup_times.get(user_id)
        if signup_time:
            hours = (datetime.now(timezone.utc) - signup_time).total_seconds() / 3600
            if hours < 1 and completion_percent >= 50:
                score += 0.1
            elif hours > 72 and completion_percent < 30:
                score -= 0.2

        return max(0.0, min(1.0, score))

    def _identify_risk_factors(
        self,
        user_id: str,
        checklist: ActivationChecklist,
    ) -> list[str]:
        """Identify risk factors for user dropping off."""
        risks = []

        signup_time = self._signup_times.get(user_id)
        if signup_time:
            hours = (datetime.now(timezone.utc) - signup_time).total_seconds() / 3600

            if hours > 24 and checklist.earned_points < 20:
                risks.append("Stalled after 24 hours with low progress")

            if hours > 168 and user_id not in self._first_value_times:
                risks.append("No first value after 7 days")

        integration = next(
            (c for c in checklist.checks if c.check_id == "first_integration"),
            None
        )
        if integration and integration.status == ActivationCheckStatus.NOT_STARTED:
            risks.append("No integration connected yet")

        first_inv = next(
            (c for c in checklist.checks if c.check_id == "first_investigation"),
            None
        )
        if first_inv and first_inv.status == ActivationCheckStatus.NOT_STARTED:
            risks.append("No investigation run yet")

        return risks

    def _generate_nudge(
        self,
        user_id: str,
        checklist: ActivationChecklist,
        risk_factors: list[str],
    ) -> str:
        """Generate suggested nudge for user."""
        next_step = checklist.get_next_step()

        if not next_step:
            return "Invite a teammate to earn bonus investigations"

        if next_step.check_id == "first_integration":
            return "Connect your first integration to start investigating"
        elif next_step.check_id == "first_investigation":
            return "Try your first AI investigation - takes under 2 minutes"
        elif next_step.check_id == "demo_completed":
            return "Try the demo to see Copilot in action"
        elif next_step.check_id == "teammate_invited":
            return "Invite a teammate to earn +5 investigations"

        if "No integration connected" in risk_factors:
            return "Need help connecting? Chat with us!"

        return f"Complete: {next_step.name}"

    def get_time_to_value_stats(self) -> dict[str, Any]:
        """Get aggregate time-to-value statistics."""
        times = []
        for user_id in self._first_value_times:
            if user_id in self._signup_times:
                delta = self._first_value_times[user_id] - self._signup_times[user_id]
                times.append(delta.total_seconds() / 60)

        if not times:
            return {"count": 0}

        return {
            "count": len(times),
            "avg_minutes": sum(times) / len(times),
            "median_minutes": sorted(times)[len(times) // 2],
            "under_5_min_percent": len([t for t in times if t <= 5]) / len(times) * 100,
            "under_15_min_percent": len([t for t in times if t <= 15]) / len(times) * 100,
        }


_health: OnboardingHealth | None = None


def get_onboarding_health() -> OnboardingHealth:
    """Get the default onboarding health tracker."""
    global _health
    if _health is None:
        _health = OnboardingHealth()
    return _health
