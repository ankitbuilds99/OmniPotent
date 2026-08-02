"""Change Window Manager.

Manages change windows for controlling when changes can be made:
- Scheduled maintenance windows
- Change freeze periods
- Emergency override handling
- Recurring windows (weekly, monthly)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any
import hashlib

log = logging.getLogger("devcopilot.policy_engine.change_windows")


class WindowType(str, Enum):
    """Types of change windows."""
    MAINTENANCE = "maintenance"  # Allowed to make changes
    FREEZE = "freeze"  # Changes blocked
    RESTRICTED = "restricted"  # Some changes allowed with approval
    EMERGENCY = "emergency"  # Emergency override active


class RecurrencePattern(str, Enum):
    """Recurrence patterns for windows."""
    NONE = "none"
    DAILY = "daily"
    WEEKLY = "weekly"
    BIWEEKLY = "biweekly"
    MONTHLY = "monthly"


@dataclass
class ChangeWindow:
    """A change window definition."""
    id: str
    name: str
    window_type: WindowType
    start_time: datetime
    end_time: datetime

    # Recurrence
    recurrence: RecurrencePattern = RecurrencePattern.NONE
    recurrence_day: int | None = None  # Day of week (0=Mon) or month (1-31)

    # Scope
    environments: list[str] = field(default_factory=lambda: ["*"])  # ["*"] = all
    services: list[str] = field(default_factory=lambda: ["*"])
    teams: list[str] = field(default_factory=lambda: ["*"])

    # Controls
    allowed_action_types: list[str] = field(default_factory=list)
    blocked_action_types: list[str] = field(default_factory=list)
    max_risk_score: float = 1.0  # 0-1
    requires_approval: bool = False

    # Metadata
    reason: str = ""
    created_by: str = ""
    approved_by: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def is_active(self, at_time: datetime | None = None) -> bool:
        """Check if window is currently active."""
        now = at_time or datetime.now(timezone.utc)

        if self.recurrence == RecurrencePattern.NONE:
            return self.start_time <= now <= self.end_time

        # Handle recurring windows
        return self._is_recurring_active(now)

    def _is_recurring_active(self, now: datetime) -> bool:
        """Check if a recurring window is active."""
        # Get the duration of the window
        duration = self.end_time - self.start_time

        if self.recurrence == RecurrencePattern.DAILY:
            # Check if current time of day is within window
            window_start_time = self.start_time.time()
            window_end_time = self.end_time.time()
            current_time = now.time()

            if window_start_time <= window_end_time:
                return window_start_time <= current_time <= window_end_time
            else:
                # Window spans midnight
                return current_time >= window_start_time or current_time <= window_end_time

        elif self.recurrence == RecurrencePattern.WEEKLY:
            # Check if current day of week and time matches
            if self.recurrence_day is not None and now.weekday() != self.recurrence_day:
                return False

            window_start_time = self.start_time.time()
            window_end_time = self.end_time.time()
            current_time = now.time()
            return window_start_time <= current_time <= window_end_time

        elif self.recurrence == RecurrencePattern.MONTHLY:
            # Check if current day of month and time matches
            if self.recurrence_day is not None and now.day != self.recurrence_day:
                return False

            window_start_time = self.start_time.time()
            window_end_time = self.end_time.time()
            current_time = now.time()
            return window_start_time <= current_time <= window_end_time

        return False

    def applies_to(
        self,
        environment: str | None = None,
        service: str | None = None,
        team: str | None = None,
    ) -> bool:
        """Check if window applies to given scope."""
        if environment and "*" not in self.environments:
            if environment not in self.environments:
                return False

        if service and "*" not in self.services:
            if service not in self.services:
                return False

        if team and "*" not in self.teams:
            if team not in self.teams:
                return False

        return True

    def allows_action(
        self,
        action_type: str,
        risk_score: float = 0.0,
    ) -> tuple[bool, str]:
        """Check if an action is allowed during this window.

        Returns:
            Tuple of (allowed, reason)
        """
        # Freeze windows block everything except emergencies
        if self.window_type == WindowType.FREEZE:
            return False, f"Change freeze active: {self.reason or self.name}"

        # Check blocked action types
        if action_type in self.blocked_action_types:
            return False, f"Action type '{action_type}' is blocked during this window"

        # Check allowed action types (if specified)
        if self.allowed_action_types and action_type not in self.allowed_action_types:
            return False, f"Action type '{action_type}' is not in allowed list for this window"

        # Check risk score
        if risk_score > self.max_risk_score:
            return False, f"Risk score {risk_score:.2f} exceeds maximum {self.max_risk_score:.2f} for this window"

        # Restricted windows require approval
        if self.window_type == WindowType.RESTRICTED and self.requires_approval:
            return True, "Allowed with approval required"

        return True, "Allowed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "window_type": self.window_type.value,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat(),
            "recurrence": self.recurrence.value,
            "recurrence_day": self.recurrence_day,
            "environments": self.environments,
            "services": self.services,
            "teams": self.teams,
            "allowed_action_types": self.allowed_action_types,
            "blocked_action_types": self.blocked_action_types,
            "max_risk_score": self.max_risk_score,
            "requires_approval": self.requires_approval,
            "reason": self.reason,
            "created_by": self.created_by,
            "is_active": self.is_active(),
        }


@dataclass
class WindowCheckResult:
    """Result of checking change windows."""
    allowed: bool
    active_windows: list[ChangeWindow]
    blocking_window: ChangeWindow | None
    reason: str
    requires_approval: bool
    override_available: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "active_windows": [w.to_dict() for w in self.active_windows],
            "blocking_window": self.blocking_window.to_dict() if self.blocking_window else None,
            "reason": self.reason,
            "requires_approval": self.requires_approval,
            "override_available": self.override_available,
        }


class ChangeWindowManager:
    """Manages change windows and evaluates change requests.

    Features:
    - Multiple window types (maintenance, freeze, restricted)
    - Recurring windows
    - Scope-based filtering
    - Emergency overrides
    - Audit logging
    """

    def __init__(self, redis_client: Any = None):
        self.redis = redis_client
        self._windows: dict[str, ChangeWindow] = {}
        self._emergency_overrides: dict[str, datetime] = {}  # scope -> expiry
        self._window_counter = 0

        # Load default windows
        self._create_default_windows()

    def _create_default_windows(self) -> None:
        """Create default change windows."""
        now = datetime.now(timezone.utc)

        # Weekly maintenance window (Sunday 2-6 AM UTC)
        self.create_window(
            name="Weekly Maintenance",
            window_type=WindowType.MAINTENANCE,
            start_time=now.replace(hour=2, minute=0, second=0, microsecond=0),
            end_time=now.replace(hour=6, minute=0, second=0, microsecond=0),
            recurrence=RecurrencePattern.WEEKLY,
            recurrence_day=6,  # Sunday
            reason="Scheduled weekly maintenance window",
        )

        # Business hours restricted window (weekdays 9 AM - 5 PM UTC)
        self.create_window(
            name="Business Hours Caution",
            window_type=WindowType.RESTRICTED,
            start_time=now.replace(hour=9, minute=0, second=0, microsecond=0),
            end_time=now.replace(hour=17, minute=0, second=0, microsecond=0),
            recurrence=RecurrencePattern.DAILY,
            max_risk_score=0.5,
            requires_approval=True,
            reason="Higher scrutiny during business hours",
        )

    def create_window(
        self,
        name: str,
        window_type: WindowType,
        start_time: datetime,
        end_time: datetime,
        recurrence: RecurrencePattern = RecurrencePattern.NONE,
        recurrence_day: int | None = None,
        environments: list[str] | None = None,
        services: list[str] | None = None,
        teams: list[str] | None = None,
        allowed_action_types: list[str] | None = None,
        blocked_action_types: list[str] | None = None,
        max_risk_score: float = 1.0,
        requires_approval: bool = False,
        reason: str = "",
        created_by: str = "system",
    ) -> ChangeWindow:
        """Create a new change window."""
        self._window_counter += 1
        window_id = f"window_{self._window_counter}"

        window = ChangeWindow(
            id=window_id,
            name=name,
            window_type=window_type,
            start_time=start_time,
            end_time=end_time,
            recurrence=recurrence,
            recurrence_day=recurrence_day,
            environments=environments or ["*"],
            services=services or ["*"],
            teams=teams or ["*"],
            allowed_action_types=allowed_action_types or [],
            blocked_action_types=blocked_action_types or [],
            max_risk_score=max_risk_score,
            requires_approval=requires_approval,
            reason=reason,
            created_by=created_by,
        )

        self._windows[window_id] = window
        log.info("Created change window: %s (%s)", name, window_type.value)

        return window

    def create_freeze(
        self,
        name: str,
        start_time: datetime,
        end_time: datetime,
        reason: str,
        environments: list[str] | None = None,
        services: list[str] | None = None,
        created_by: str = "system",
        approved_by: str = "",
    ) -> ChangeWindow:
        """Create a change freeze window."""
        self._window_counter += 1
        window_id = f"freeze_{self._window_counter}"

        window = ChangeWindow(
            id=window_id,
            name=name,
            window_type=WindowType.FREEZE,
            start_time=start_time,
            end_time=end_time,
            environments=environments or ["*"],
            services=services or ["*"],
            reason=reason,
            created_by=created_by,
            approved_by=approved_by,
        )

        self._windows[window_id] = window
        log.warning(
            "Created change freeze: %s (%s to %s) - %s",
            name, start_time, end_time, reason
        )

        return window

    def check_change_allowed(
        self,
        action_type: str,
        risk_score: float = 0.0,
        environment: str | None = None,
        service: str | None = None,
        team: str | None = None,
        at_time: datetime | None = None,
    ) -> WindowCheckResult:
        """Check if a change is allowed given current windows.

        Args:
            action_type: Type of action being performed
            risk_score: Risk score of the action (0-1)
            environment: Target environment
            service: Target service
            team: Team performing the action
            at_time: Time to check (defaults to now)

        Returns:
            WindowCheckResult with decision and details
        """
        now = at_time or datetime.now(timezone.utc)

        # Check for emergency override
        override_key = self._get_override_key(environment, service)
        if override_key in self._emergency_overrides:
            if self._emergency_overrides[override_key] > now:
                return WindowCheckResult(
                    allowed=True,
                    active_windows=[],
                    blocking_window=None,
                    reason="Emergency override active",
                    requires_approval=False,
                    override_available=False,
                )

        # Find all active windows that apply
        active_windows = []
        blocking_window = None
        requires_approval = False

        for window in self._windows.values():
            if not window.is_active(now):
                continue

            if not window.applies_to(environment, service, team):
                continue

            active_windows.append(window)

            # Check if this window allows the action
            allowed, reason = window.allows_action(action_type, risk_score)

            if not allowed:
                blocking_window = window
                break

            if window.requires_approval:
                requires_approval = True

        # Determine final result
        if blocking_window:
            return WindowCheckResult(
                allowed=False,
                active_windows=active_windows,
                blocking_window=blocking_window,
                reason=f"Blocked by {blocking_window.name}: {blocking_window.reason}",
                requires_approval=False,
                override_available=True,  # Emergency override is available
            )

        if not active_windows:
            # No active windows - default allow with caution
            return WindowCheckResult(
                allowed=True,
                active_windows=[],
                blocking_window=None,
                reason="No active windows - default policy applies",
                requires_approval=False,
                override_available=False,
            )

        # Allowed by active windows
        maintenance_window = next(
            (w for w in active_windows if w.window_type == WindowType.MAINTENANCE),
            None
        )

        if maintenance_window:
            reason = f"Within maintenance window: {maintenance_window.name}"
        else:
            reason = "Allowed by current change window policy"

        return WindowCheckResult(
            allowed=True,
            active_windows=active_windows,
            blocking_window=None,
            reason=reason,
            requires_approval=requires_approval,
            override_available=False,
        )

    def activate_emergency_override(
        self,
        duration_minutes: int = 60,
        environment: str | None = None,
        service: str | None = None,
        reason: str = "",
        activated_by: str = "",
    ) -> str:
        """Activate an emergency override.

        Args:
            duration_minutes: How long the override lasts
            environment: Scope to environment (None = all)
            service: Scope to service (None = all)
            reason: Reason for override
            activated_by: Who activated it

        Returns:
            Override ID
        """
        override_key = self._get_override_key(environment, service)
        expiry = datetime.now(timezone.utc) + timedelta(minutes=duration_minutes)

        self._emergency_overrides[override_key] = expiry

        override_id = hashlib.sha256(
            f"{override_key}:{expiry.isoformat()}".encode()
        ).hexdigest()[:12]

        log.warning(
            "EMERGENCY OVERRIDE ACTIVATED: %s for %d minutes by %s - %s",
            override_key, duration_minutes, activated_by, reason
        )

        return override_id

    def deactivate_emergency_override(
        self,
        environment: str | None = None,
        service: str | None = None,
    ) -> bool:
        """Deactivate an emergency override."""
        override_key = self._get_override_key(environment, service)

        if override_key in self._emergency_overrides:
            del self._emergency_overrides[override_key]
            log.info("Emergency override deactivated: %s", override_key)
            return True

        return False

    def _get_override_key(
        self,
        environment: str | None,
        service: str | None,
    ) -> str:
        """Generate a key for override scope."""
        env = environment or "*"
        svc = service or "*"
        return f"{env}:{svc}"

    def get_window(self, window_id: str) -> ChangeWindow | None:
        """Get a window by ID."""
        return self._windows.get(window_id)

    def list_windows(
        self,
        active_only: bool = False,
        window_type: WindowType | None = None,
    ) -> list[ChangeWindow]:
        """List change windows."""
        windows = list(self._windows.values())

        if active_only:
            now = datetime.now(timezone.utc)
            windows = [w for w in windows if w.is_active(now)]

        if window_type:
            windows = [w for w in windows if w.window_type == window_type]

        return windows

    def delete_window(self, window_id: str) -> bool:
        """Delete a window."""
        if window_id in self._windows:
            del self._windows[window_id]
            log.info("Deleted change window: %s", window_id)
            return True
        return False

    def get_upcoming_windows(
        self,
        hours: int = 24,
        window_type: WindowType | None = None,
    ) -> list[ChangeWindow]:
        """Get windows starting in the next N hours."""
        now = datetime.now(timezone.utc)
        end = now + timedelta(hours=hours)

        upcoming = []
        for window in self._windows.values():
            if window_type and window.window_type != window_type:
                continue

            # For non-recurring, check start time
            if window.recurrence == RecurrencePattern.NONE:
                if now <= window.start_time <= end:
                    upcoming.append(window)
            else:
                # For recurring, it will be active within the period
                upcoming.append(window)

        return upcoming

    def get_stats(self) -> dict[str, Any]:
        """Get manager statistics."""
        now = datetime.now(timezone.utc)
        active = [w for w in self._windows.values() if w.is_active(now)]

        return {
            "total_windows": len(self._windows),
            "active_windows": len(active),
            "active_freezes": len([w for w in active if w.window_type == WindowType.FREEZE]),
            "active_maintenance": len([w for w in active if w.window_type == WindowType.MAINTENANCE]),
            "emergency_overrides_active": len([
                k for k, v in self._emergency_overrides.items()
                if v > now
            ]),
        }


def create_change_window_manager(redis_client: Any = None) -> ChangeWindowManager:
    """Factory function to create a change window manager."""
    return ChangeWindowManager(redis_client=redis_client)
