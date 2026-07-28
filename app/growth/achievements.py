"""Achievement and gamification system.

Tracks user achievements, streaks, and rewards for engagement.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("devcopilot.growth.achievements")


class AchievementStatus(str, Enum):
    LOCKED = "locked"
    IN_PROGRESS = "in_progress"
    EARNED = "earned"


class AchievementCategory(str, Enum):
    INVESTIGATION = "investigation"
    SPEED = "speed"
    INTEGRATION = "integration"
    TEAM = "team"
    PREVENTION = "prevention"
    AUTOMATION = "automation"
    INFLUENCE = "influence"
    MASTERY = "mastery"


@dataclass
class Achievement:
    """An achievement definition."""

    achievement_id: str
    name: str
    description: str
    category: AchievementCategory
    icon: str
    points: int = 10
    requirement_count: int = 1
    reward_type: str = ""
    reward_value: int = 0
    is_hidden: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.achievement_id,
            "name": self.name,
            "description": self.description,
            "category": self.category.value,
            "icon": self.icon,
            "points": self.points,
            "requirement": self.requirement_count,
            "reward_type": self.reward_type,
            "reward_value": self.reward_value,
        }


@dataclass
class UserAchievement:
    """User's progress on an achievement."""

    achievement_id: str
    status: AchievementStatus = AchievementStatus.LOCKED
    progress: int = 0
    earned_at: datetime | None = None
    notified: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "achievement_id": self.achievement_id,
            "status": self.status.value,
            "progress": self.progress,
            "earned_at": self.earned_at.isoformat() if self.earned_at else None,
        }


@dataclass
class WeeklyStreak:
    """User's weekly usage streak."""

    user_id: str
    current_streak: int = 0
    longest_streak: int = 0
    last_active_week: int = 0
    weeks_active: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "current_streak": self.current_streak,
            "longest_streak": self.longest_streak,
            "last_active_week": self.last_active_week,
        }


@dataclass
class UserAchievements:
    """All achievements for a user."""

    user_id: str
    achievements: dict[str, UserAchievement] = field(default_factory=dict)
    total_points: int = 0
    streak: WeeklyStreak | None = None

    def earned_count(self) -> int:
        return sum(
            1 for a in self.achievements.values()
            if a.status == AchievementStatus.EARNED
        )

    def to_dict(self) -> dict[str, Any]:
        earned = [
            a.to_dict() for a in self.achievements.values()
            if a.status == AchievementStatus.EARNED
        ]
        in_progress = [
            a.to_dict() for a in self.achievements.values()
            if a.status == AchievementStatus.IN_PROGRESS
        ]
        locked = [
            a.to_dict() for a in self.achievements.values()
            if a.status == AchievementStatus.LOCKED
        ]

        return {
            "user_id": self.user_id,
            "earned": earned,
            "in_progress": in_progress,
            "locked": locked,
            "total_earned": len(earned),
            "total_points": self.total_points,
            "streak": self.streak.to_dict() if self.streak else None,
        }


class AchievementTracker:
    """Tracks user achievements and rewards."""

    def __init__(self) -> None:
        self._definitions: dict[str, Achievement] = {}
        self._user_achievements: dict[str, UserAchievements] = {}
        self._init_default_achievements()

    def _init_default_achievements(self) -> None:
        """Initialize default achievements."""
        achievements = [
            Achievement(
                "first_investigation",
                "First Investigation",
                "Ran your first AI investigation",
                AchievementCategory.INVESTIGATION,
                "magnifying_glass",
                points=10,
            ),
            Achievement(
                "speed_demon",
                "Speed Demon",
                "Resolved incident in under 15 minutes",
                AchievementCategory.SPEED,
                "lightning",
                points=25,
            ),
            Achievement(
                "connected",
                "Connected",
                "Linked 3+ integrations",
                AchievementCategory.INTEGRATION,
                "link",
                points=15,
                requirement_count=3,
            ),
            Achievement(
                "team_player",
                "Team Player",
                "Invited first teammate",
                AchievementCategory.TEAM,
                "people",
                points=20,
                reward_type="bonus_investigations",
                reward_value=5,
            ),
            Achievement(
                "pattern_finder",
                "Pattern Finder",
                "Discovered a recurring incident pattern",
                AchievementCategory.INVESTIGATION,
                "chart",
                points=30,
            ),
            Achievement(
                "prevention_pro",
                "Prevention Pro",
                "Prevented incident with prediction",
                AchievementCategory.PREVENTION,
                "shield",
                points=50,
            ),
            Achievement(
                "power_user",
                "Power User",
                "50 investigations total",
                AchievementCategory.MASTERY,
                "star",
                points=100,
                requirement_count=50,
            ),
            Achievement(
                "influencer",
                "Influencer",
                "5 teammates joined from your invites",
                AchievementCategory.INFLUENCE,
                "megaphone",
                points=75,
                requirement_count=5,
                reward_type="bonus_investigations",
                reward_value=25,
            ),
            Achievement(
                "runbook_runner",
                "Runbook Runner",
                "Executed 10 automated runbooks",
                AchievementCategory.AUTOMATION,
                "play",
                points=40,
                requirement_count=10,
            ),
            Achievement(
                "data_master",
                "Data Master",
                "Connected all 5 free integrations",
                AchievementCategory.INTEGRATION,
                "database",
                points=35,
                requirement_count=5,
            ),
            Achievement(
                "incident_champion",
                "Incident Champion",
                "Resolved 100 incidents",
                AchievementCategory.MASTERY,
                "trophy",
                points=200,
                requirement_count=100,
            ),
            Achievement(
                "night_owl",
                "Night Owl",
                "Resolved incident between midnight and 5am",
                AchievementCategory.SPEED,
                "moon",
                points=15,
                is_hidden=True,
            ),
        ]

        for achievement in achievements:
            self._definitions[achievement.achievement_id] = achievement

    def get_achievement(self, achievement_id: str) -> Achievement | None:
        """Get an achievement definition."""
        return self._definitions.get(achievement_id)

    def get_all_achievements(self) -> list[Achievement]:
        """Get all achievement definitions."""
        return [a for a in self._definitions.values() if not a.is_hidden]

    def get_user_achievements(self, user_id: str) -> UserAchievements:
        """Get user's achievements."""
        if user_id not in self._user_achievements:
            self._user_achievements[user_id] = UserAchievements(
                user_id=user_id,
                streak=WeeklyStreak(user_id=user_id),
            )
            for aid in self._definitions:
                self._user_achievements[user_id].achievements[aid] = UserAchievement(
                    achievement_id=aid
                )

        return self._user_achievements[user_id]

    def record_progress(
        self,
        user_id: str,
        achievement_id: str,
        progress: int = 1,
    ) -> UserAchievement | None:
        """Record progress toward an achievement."""
        definition = self._definitions.get(achievement_id)
        if not definition:
            return None

        user_achs = self.get_user_achievements(user_id)
        user_ach = user_achs.achievements.get(achievement_id)

        if not user_ach:
            user_ach = UserAchievement(achievement_id=achievement_id)
            user_achs.achievements[achievement_id] = user_ach

        if user_ach.status == AchievementStatus.EARNED:
            return user_ach

        user_ach.progress += progress

        if user_ach.progress >= definition.requirement_count:
            return self._earn_achievement(user_id, achievement_id)

        if user_ach.status == AchievementStatus.LOCKED:
            user_ach.status = AchievementStatus.IN_PROGRESS

        return user_ach

    def _earn_achievement(
        self,
        user_id: str,
        achievement_id: str,
    ) -> UserAchievement | None:
        """Mark achievement as earned and grant rewards."""
        definition = self._definitions.get(achievement_id)
        user_achs = self.get_user_achievements(user_id)
        user_ach = user_achs.achievements.get(achievement_id)

        if not definition or not user_ach:
            return None

        user_ach.status = AchievementStatus.EARNED
        user_ach.earned_at = datetime.now(timezone.utc)
        user_ach.progress = definition.requirement_count

        user_achs.total_points += definition.points

        log.info(
            "Achievement earned: user=%s achievement=%s points=%d",
            user_id,
            achievement_id,
            definition.points,
        )

        return user_ach

    def check_investigation(
        self,
        user_id: str,
        duration_minutes: int,
        is_recurring_pattern: bool = False,
    ) -> list[UserAchievement]:
        """Check achievements after investigation."""
        earned = []

        result = self.record_progress(user_id, "first_investigation")
        if result and result.status == AchievementStatus.EARNED:
            earned.append(result)

        result = self.record_progress(user_id, "power_user")
        if result and result.status == AchievementStatus.EARNED:
            earned.append(result)

        result = self.record_progress(user_id, "incident_champion")
        if result and result.status == AchievementStatus.EARNED:
            earned.append(result)

        if duration_minutes <= 15:
            result = self.record_progress(user_id, "speed_demon")
            if result and result.status == AchievementStatus.EARNED:
                earned.append(result)

        if is_recurring_pattern:
            result = self.record_progress(user_id, "pattern_finder")
            if result and result.status == AchievementStatus.EARNED:
                earned.append(result)

        now = datetime.now(timezone.utc)
        if 0 <= now.hour < 5:
            result = self.record_progress(user_id, "night_owl")
            if result and result.status == AchievementStatus.EARNED:
                earned.append(result)

        return earned

    def check_integration(self, user_id: str, integration_count: int) -> list[UserAchievement]:
        """Check integration achievements."""
        earned = []

        user_achs = self.get_user_achievements(user_id)
        connected = user_achs.achievements.get("connected")
        if connected:
            connected.progress = integration_count
            if integration_count >= 3 and connected.status != AchievementStatus.EARNED:
                result = self._earn_achievement(user_id, "connected")
                if result:
                    earned.append(result)

        data_master = user_achs.achievements.get("data_master")
        if data_master:
            data_master.progress = integration_count
            if integration_count >= 5 and data_master.status != AchievementStatus.EARNED:
                result = self._earn_achievement(user_id, "data_master")
                if result:
                    earned.append(result)

        return earned

    def check_team_invite(self, user_id: str, invite_count: int) -> list[UserAchievement]:
        """Check team invite achievements."""
        earned = []

        if invite_count >= 1:
            result = self.record_progress(user_id, "team_player")
            if result and result.status == AchievementStatus.EARNED:
                earned.append(result)

        user_achs = self.get_user_achievements(user_id)
        influencer = user_achs.achievements.get("influencer")
        if influencer:
            influencer.progress = invite_count
            if invite_count >= 5 and influencer.status != AchievementStatus.EARNED:
                result = self._earn_achievement(user_id, "influencer")
                if result:
                    earned.append(result)

        return earned

    def check_runbook(self, user_id: str) -> list[UserAchievement]:
        """Check runbook achievements."""
        earned = []

        result = self.record_progress(user_id, "runbook_runner")
        if result and result.status == AchievementStatus.EARNED:
            earned.append(result)

        return earned

    def check_prevention(self, user_id: str) -> list[UserAchievement]:
        """Check prevention achievements."""
        earned = []

        result = self.record_progress(user_id, "prevention_pro")
        if result and result.status == AchievementStatus.EARNED:
            earned.append(result)

        return earned

    def record_activity(self, user_id: str) -> WeeklyStreak:
        """Record weekly activity for streak."""
        user_achs = self.get_user_achievements(user_id)
        streak = user_achs.streak

        if not streak:
            streak = WeeklyStreak(user_id=user_id)
            user_achs.streak = streak

        now = datetime.now(timezone.utc)
        current_week = now.isocalendar()[1]

        if current_week == streak.last_active_week:
            return streak

        if current_week == streak.last_active_week + 1:
            streak.current_streak += 1
        elif current_week > streak.last_active_week + 1:
            streak.current_streak = 1

        streak.last_active_week = current_week

        if current_week not in streak.weeks_active:
            streak.weeks_active.append(current_week)

        if streak.current_streak > streak.longest_streak:
            streak.longest_streak = streak.current_streak

        log.info(
            "Streak updated: user=%s streak=%d",
            user_id,
            streak.current_streak,
        )

        return streak

    def get_streak_reward(self, user_id: str) -> dict[str, Any]:
        """Get streak reward info."""
        user_achs = self.get_user_achievements(user_id)
        streak = user_achs.streak

        if not streak:
            return {"has_reward": False}

        rewards = {
            4: {"type": "badge", "value": "4-week streak badge"},
            8: {"type": "feature", "value": "Unlock 1 Pro feature for 1 week"},
            12: {"type": "bonus", "value": "10 bonus investigations"},
        }

        next_milestone = None
        for weeks in sorted(rewards.keys()):
            if streak.current_streak < weeks:
                next_milestone = weeks
                break

        return {
            "current_streak": streak.current_streak,
            "longest_streak": streak.longest_streak,
            "next_milestone": next_milestone,
            "next_reward": rewards.get(next_milestone) if next_milestone else None,
            "weeks_until_reward": (next_milestone - streak.current_streak) if next_milestone else None,
        }

    def get_leaderboard(self, limit: int = 10) -> list[dict[str, Any]]:
        """Get achievements leaderboard."""
        users: list[dict[str, Any]] = []
        for user_id, user_achs in self._user_achievements.items():
            users.append({
                "user_id": user_id,
                "points": user_achs.total_points,
                "earned_count": user_achs.earned_count(),
                "streak": user_achs.streak.current_streak if user_achs.streak else 0,
            })

        users.sort(key=lambda x: x["points"], reverse=True)

        return [
            {"rank": i + 1, **u}
            for i, u in enumerate(users[:limit])
        ]


_tracker: AchievementTracker | None = None


def get_achievement_tracker() -> AchievementTracker:
    """Get the default achievement tracker."""
    global _tracker
    if _tracker is None:
        _tracker = AchievementTracker()
    return _tracker
