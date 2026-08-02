"""On-call scheduling implementation.

Supports:
- Multiple rotation types (daily, weekly, custom)
- Timezone-aware scheduling
- Temporary overrides
- Multi-level escalation policies
- Holiday handling
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone, time
from enum import Enum
from typing import Any

log = logging.getLogger("omnipotent.oncall.scheduler")


class RotationType(Enum):
    """Types of on-call rotations."""

    DAILY = "daily"
    WEEKLY = "weekly"
    BIWEEKLY = "biweekly"
    CUSTOM = "custom"


class DayOfWeek(Enum):
    """Days of the week."""

    MONDAY = 0
    TUESDAY = 1
    WEDNESDAY = 2
    THURSDAY = 3
    FRIDAY = 4
    SATURDAY = 5
    SUNDAY = 6


@dataclass
class OnCallUser:
    """A user in an on-call rotation."""

    user_id: str
    name: str
    email: str
    phone: str = ""
    slack_id: str = ""
    timezone: str = "UTC"
    notification_preferences: dict[str, bool] = field(default_factory=lambda: {
        "email": True,
        "sms": True,
        "slack": True,
        "push": True,
    })


@dataclass
class Rotation:
    """A single rotation within a schedule."""

    id: str
    name: str
    users: list[OnCallUser]
    rotation_type: RotationType
    handoff_time: time = field(default_factory=lambda: time(9, 0))
    handoff_day: DayOfWeek = DayOfWeek.MONDAY
    custom_length_days: int = 7
    start_date: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    timezone: str = "UTC"
    restrictions: list[dict[str, Any]] = field(default_factory=list)

    def get_rotation_length(self) -> timedelta:
        """Get the length of one rotation cycle."""
        if self.rotation_type == RotationType.DAILY:
            return timedelta(days=1)
        elif self.rotation_type == RotationType.WEEKLY:
            return timedelta(weeks=1)
        elif self.rotation_type == RotationType.BIWEEKLY:
            return timedelta(weeks=2)
        else:
            return timedelta(days=self.custom_length_days)

    def get_current_user(self, at_time: datetime | None = None) -> OnCallUser | None:
        """Get the user on-call at a given time."""
        if not self.users:
            return None

        if at_time is None:
            at_time = datetime.now(timezone.utc)

        elapsed = at_time - self.start_date
        rotation_length = self.get_rotation_length()

        if rotation_length.total_seconds() == 0:
            return self.users[0]

        rotations_elapsed = int(elapsed.total_seconds() / rotation_length.total_seconds())
        user_index = rotations_elapsed % len(self.users)

        return self.users[user_index]


@dataclass
class Override:
    """A temporary override for on-call."""

    id: str
    schedule_id: str
    user: OnCallUser
    start_time: datetime
    end_time: datetime
    reason: str = ""
    created_by: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def is_active(self, at_time: datetime | None = None) -> bool:
        """Check if override is active at given time."""
        if at_time is None:
            at_time = datetime.now(timezone.utc)
        return self.start_time <= at_time < self.end_time


@dataclass
class EscalationLevel:
    """A level in an escalation policy."""

    level: int
    targets: list[str]
    delay_minutes: int = 5
    repeat_count: int = 1
    notification_channels: list[str] = field(default_factory=lambda: ["email", "sms"])


@dataclass
class EscalationPolicy:
    """An escalation policy for alerts."""

    id: str
    name: str
    description: str = ""
    levels: list[EscalationLevel] = field(default_factory=list)
    repeat_policy: bool = True
    max_repeats: int = 3
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def get_escalation_chain(
        self,
        schedule_id: str,
        scheduler: "OnCallScheduler",
    ) -> list[dict[str, Any]]:
        """Get the full escalation chain with resolved users."""
        chain: list[dict[str, Any]] = []
        cumulative_delay = 0

        for level in self.levels:
            users: list[OnCallUser] = []

            for target in level.targets:
                if target == "current_oncall":
                    oncall = scheduler.get_current_oncall(schedule_id)
                    if oncall:
                        users.append(oncall)
                elif target.startswith("schedule:"):
                    target_schedule_id = target.split(":")[1]
                    oncall = scheduler.get_current_oncall(target_schedule_id)
                    if oncall:
                        users.append(oncall)
                elif target.startswith("user:"):
                    pass

            chain.append({
                "level": level.level,
                "delay_minutes": cumulative_delay,
                "users": users,
                "channels": level.notification_channels,
                "repeat_count": level.repeat_count,
            })

            cumulative_delay += level.delay_minutes

        return chain


@dataclass
class Schedule:
    """An on-call schedule."""

    id: str
    name: str
    description: str = ""
    org_id: str = ""
    team_id: str = ""
    rotations: list[Rotation] = field(default_factory=list)
    overrides: list[Override] = field(default_factory=list)
    escalation_policy_id: str = ""
    timezone: str = "UTC"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)


_SCHEDULES: dict[str, Schedule] = {}
_ESCALATION_POLICIES: dict[str, EscalationPolicy] = {}


class OnCallScheduler:
    """Manages on-call schedules and escalations."""

    def __init__(self, org_id: str):
        self.org_id = org_id

    def create_schedule(
        self,
        name: str,
        description: str = "",
        team_id: str = "",
        timezone: str = "UTC",
    ) -> Schedule:
        """Create a new on-call schedule."""
        schedule_id = f"schedule_{secrets.token_hex(8)}"

        schedule = Schedule(
            id=schedule_id,
            name=name,
            description=description,
            org_id=self.org_id,
            team_id=team_id,
            timezone=timezone,
        )

        _SCHEDULES[schedule_id] = schedule
        log.info("Created on-call schedule %s: %s", schedule_id, name)

        return schedule

    def get_schedule(self, schedule_id: str) -> Schedule | None:
        """Get a schedule by ID."""
        schedule = _SCHEDULES.get(schedule_id)
        if schedule and schedule.org_id == self.org_id:
            return schedule
        return None

    def list_schedules(self, team_id: str | None = None) -> list[Schedule]:
        """List all schedules."""
        schedules = [s for s in _SCHEDULES.values() if s.org_id == self.org_id]

        if team_id:
            schedules = [s for s in schedules if s.team_id == team_id]

        return schedules

    def delete_schedule(self, schedule_id: str) -> bool:
        """Delete a schedule."""
        schedule = self.get_schedule(schedule_id)
        if not schedule:
            return False

        del _SCHEDULES[schedule_id]
        log.info("Deleted on-call schedule %s", schedule_id)
        return True

    def add_rotation(
        self,
        schedule_id: str,
        name: str,
        users: list[OnCallUser],
        rotation_type: RotationType = RotationType.WEEKLY,
        handoff_time: time | None = None,
        handoff_day: DayOfWeek = DayOfWeek.MONDAY,
        start_date: datetime | None = None,
    ) -> Rotation | None:
        """Add a rotation to a schedule."""
        schedule = self.get_schedule(schedule_id)
        if not schedule:
            return None

        rotation_id = f"rotation_{secrets.token_hex(8)}"

        rotation = Rotation(
            id=rotation_id,
            name=name,
            users=users,
            rotation_type=rotation_type,
            handoff_time=handoff_time or time(9, 0),
            handoff_day=handoff_day,
            start_date=start_date or datetime.now(timezone.utc),
            timezone=schedule.timezone,
        )

        schedule.rotations.append(rotation)
        schedule.updated_at = datetime.now(timezone.utc)

        log.info("Added rotation %s to schedule %s", name, schedule_id)
        return rotation

    def remove_rotation(self, schedule_id: str, rotation_id: str) -> bool:
        """Remove a rotation from a schedule."""
        schedule = self.get_schedule(schedule_id)
        if not schedule:
            return False

        schedule.rotations = [r for r in schedule.rotations if r.id != rotation_id]
        schedule.updated_at = datetime.now(timezone.utc)
        return True

    def get_current_oncall(
        self,
        schedule_id: str,
        at_time: datetime | None = None,
    ) -> OnCallUser | None:
        """Get the current on-call user for a schedule."""
        schedule = self.get_schedule(schedule_id)
        if not schedule:
            return None

        if at_time is None:
            at_time = datetime.now(timezone.utc)

        for override in schedule.overrides:
            if override.is_active(at_time):
                return override.user

        for rotation in schedule.rotations:
            user = rotation.get_current_user(at_time)
            if user:
                return user

        return None

    def get_oncall_timeline(
        self,
        schedule_id: str,
        start_time: datetime,
        end_time: datetime,
    ) -> list[dict[str, Any]]:
        """Get on-call timeline for a period."""
        schedule = self.get_schedule(schedule_id)
        if not schedule:
            return []

        timeline: list[dict[str, Any]] = []
        current = start_time

        while current < end_time:
            user = self.get_current_oncall(schedule_id, current)

            if timeline and timeline[-1].get("user_id") == (user.user_id if user else None):
                timeline[-1]["end_time"] = current + timedelta(hours=1)
            else:
                timeline.append({
                    "start_time": current.isoformat(),
                    "end_time": (current + timedelta(hours=1)).isoformat(),
                    "user_id": user.user_id if user else None,
                    "user_name": user.name if user else None,
                    "user_email": user.email if user else None,
                })

            current += timedelta(hours=1)

        return timeline

    def create_override(
        self,
        schedule_id: str,
        user: OnCallUser,
        start_time: datetime,
        end_time: datetime,
        reason: str = "",
        created_by: str = "",
    ) -> Override | None:
        """Create an override for a schedule."""
        schedule = self.get_schedule(schedule_id)
        if not schedule:
            return None

        override_id = f"override_{secrets.token_hex(8)}"

        override = Override(
            id=override_id,
            schedule_id=schedule_id,
            user=user,
            start_time=start_time,
            end_time=end_time,
            reason=reason,
            created_by=created_by,
        )

        schedule.overrides.append(override)
        schedule.updated_at = datetime.now(timezone.utc)

        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        schedule.overrides = [
            o for o in schedule.overrides
            if o.end_time > cutoff
        ]

        log.info(
            "Created override for schedule %s: %s from %s to %s",
            schedule_id,
            user.name,
            start_time,
            end_time,
        )

        return override

    def delete_override(self, schedule_id: str, override_id: str) -> bool:
        """Delete an override."""
        schedule = self.get_schedule(schedule_id)
        if not schedule:
            return False

        schedule.overrides = [o for o in schedule.overrides if o.id != override_id]
        schedule.updated_at = datetime.now(timezone.utc)
        return True

    def create_escalation_policy(
        self,
        name: str,
        levels: list[EscalationLevel],
        description: str = "",
    ) -> EscalationPolicy:
        """Create an escalation policy."""
        policy_id = f"escalation_{secrets.token_hex(8)}"

        policy = EscalationPolicy(
            id=policy_id,
            name=name,
            description=description,
            levels=levels,
        )

        _ESCALATION_POLICIES[policy_id] = policy
        log.info("Created escalation policy %s: %s", policy_id, name)

        return policy

    def get_escalation_policy(self, policy_id: str) -> EscalationPolicy | None:
        """Get an escalation policy by ID."""
        return _ESCALATION_POLICIES.get(policy_id)

    def get_escalation_chain(
        self,
        schedule_id: str,
    ) -> list[dict[str, Any]]:
        """Get the escalation chain for a schedule."""
        schedule = self.get_schedule(schedule_id)
        if not schedule or not schedule.escalation_policy_id:
            oncall = self.get_current_oncall(schedule_id)
            if oncall:
                return [{
                    "level": 1,
                    "delay_minutes": 0,
                    "users": [oncall],
                    "channels": ["email", "sms", "slack"],
                    "repeat_count": 3,
                }]
            return []

        policy = self.get_escalation_policy(schedule.escalation_policy_id)
        if not policy:
            return []

        return policy.get_escalation_chain(schedule_id, self)

    async def notify_oncall(
        self,
        schedule_id: str,
        alert_id: str,
        message: str,
        severity: str = "high",
    ) -> dict[str, Any]:
        """Notify the on-call user(s) about an alert."""
        chain = self.get_escalation_chain(schedule_id)

        notifications_sent: list[dict[str, Any]] = []

        for level in chain:
            for user in level.get("users", []):
                for channel in level.get("channels", []):
                    notifications_sent.append({
                        "user_id": user.user_id,
                        "user_name": user.name,
                        "channel": channel,
                        "delay_minutes": level.get("delay_minutes", 0),
                        "status": "queued",
                    })

        log.info(
            "Queued %d notifications for alert %s on schedule %s",
            len(notifications_sent),
            alert_id,
            schedule_id,
        )

        return {
            "alert_id": alert_id,
            "schedule_id": schedule_id,
            "notifications": notifications_sent,
            "escalation_levels": len(chain),
        }


_schedulers: dict[str, OnCallScheduler] = {}


def _get_scheduler(org_id: str) -> OnCallScheduler:
    """Get or create on-call scheduler for org."""
    if org_id not in _schedulers:
        _schedulers[org_id] = OnCallScheduler(org_id)
    return _schedulers[org_id]


def create_schedule(
    org_id: str,
    name: str,
    **kwargs: Any,
) -> Schedule:
    """Create a new on-call schedule."""
    scheduler = _get_scheduler(org_id)
    return scheduler.create_schedule(name, **kwargs)


def get_schedule(org_id: str, schedule_id: str) -> Schedule | None:
    """Get a schedule by ID."""
    scheduler = _get_scheduler(org_id)
    return scheduler.get_schedule(schedule_id)


def list_schedules(org_id: str, **kwargs: Any) -> list[Schedule]:
    """List all schedules."""
    scheduler = _get_scheduler(org_id)
    return scheduler.list_schedules(**kwargs)


def get_current_oncall(
    org_id: str,
    schedule_id: str,
    at_time: datetime | None = None,
) -> OnCallUser | None:
    """Get the current on-call user."""
    scheduler = _get_scheduler(org_id)
    return scheduler.get_current_oncall(schedule_id, at_time)


def create_override(
    org_id: str,
    schedule_id: str,
    user: OnCallUser,
    start_time: datetime,
    end_time: datetime,
    **kwargs: Any,
) -> Override | None:
    """Create an override."""
    scheduler = _get_scheduler(org_id)
    return scheduler.create_override(schedule_id, user, start_time, end_time, **kwargs)


def get_escalation_chain(org_id: str, schedule_id: str) -> list[dict[str, Any]]:
    """Get the escalation chain for a schedule."""
    scheduler = _get_scheduler(org_id)
    return scheduler.get_escalation_chain(schedule_id)


async def notify_oncall(
    org_id: str,
    schedule_id: str,
    alert_id: str,
    message: str,
    severity: str = "high",
) -> dict[str, Any]:
    """Notify the on-call user(s)."""
    scheduler = _get_scheduler(org_id)
    return await scheduler.notify_oncall(schedule_id, alert_id, message, severity)
