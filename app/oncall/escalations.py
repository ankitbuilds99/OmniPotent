"""Enhanced Escalation Policies for On-Call Management.

PagerDuty-level escalation capabilities:
- Multi-level escalation with configurable timeouts
- Multiple target types (schedules, users, teams, webhooks, Slack)
- Conditional escalation based on alert properties
- Escalation chaining and fallbacks
- Time-based routing (business hours vs after-hours)
- Urgency-based escalation paths
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone as dt_timezone
from enum import Enum
from typing import Any, Callable
from uuid import UUID

from zoneinfo import ZoneInfo

log = logging.getLogger("omnipotent.oncall.escalations")


class TargetType(Enum):
    """Types of escalation targets."""

    SCHEDULE = "schedule"
    USER = "user"
    TEAM = "team"
    SLACK_CHANNEL = "slack_channel"
    SLACK_USER = "slack_user"
    WEBHOOK = "webhook"
    EMAIL_GROUP = "email_group"
    PAGERDUTY = "pagerduty"  # Integration
    OPSGENIE = "opsgenie"  # Integration
    CUSTOM = "custom"


class EscalationUrgency(Enum):
    """Urgency levels affecting escalation behavior."""

    LOW = "low"
    HIGH = "high"

    @classmethod
    def from_severity(cls, severity: str) -> "EscalationUrgency":
        """Map alert severity to urgency."""
        high_severities = {"critical", "high", "p1", "p2", "sev1", "sev2"}
        return cls.HIGH if severity.lower() in high_severities else cls.LOW


class NotificationChannel(Enum):
    """Notification channels for escalations."""

    PUSH = "push"
    SMS = "sms"
    PHONE = "phone"
    EMAIL = "email"
    SLACK = "slack"
    TEAMS = "teams"
    WEBHOOK = "webhook"


@dataclass
class EscalationTarget:
    """
    A target to notify during escalation.

    Targets can be individual users, schedules (current on-call),
    teams (all members), or external systems (Slack, webhooks).
    """

    target_type: TargetType
    target_id: str
    target_name: str | None = None

    # For schedules: which layer to target
    schedule_layer: str | None = None  # "primary", "secondary", "all"

    # For teams: notification strategy
    team_notify_strategy: str = "all"  # "all", "random", "round_robin"

    # For webhooks
    webhook_url: str | None = None
    webhook_headers: dict[str, str] = field(default_factory=dict)
    webhook_payload_template: str | None = None

    # For Slack
    slack_channel_id: str | None = None
    slack_mention_users: bool = True

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "target_type": self.target_type.value,
            "target_id": self.target_id,
            "target_name": self.target_name,
            "schedule_layer": self.schedule_layer,
            "team_notify_strategy": self.team_notify_strategy,
        }


@dataclass
class EscalationStep:
    """
    A step in an escalation policy.

    Each step has:
    - Delay before execution (relative to previous step)
    - One or more targets to notify
    - Notification channels to use
    - Optional conditions to skip
    """

    step_number: int
    delay_minutes: int
    targets: list[EscalationTarget]
    notification_channels: list[NotificationChannel] = field(
        default_factory=lambda: [
            NotificationChannel.PUSH,
            NotificationChannel.SMS,
        ]
    )

    # Conditions to skip this step
    skip_if_acknowledged: bool = True
    skip_if_resolved: bool = True
    skip_conditions: list[dict[str, Any]] = field(default_factory=list)

    # Repeat this step before moving to next
    repeat_count: int = 1
    repeat_delay_minutes: int = 5

    # Human-readable description
    description: str | None = None

    def should_skip(self, alert_status: str, alert_data: dict[str, Any]) -> bool:
        """Check if this step should be skipped."""
        if self.skip_if_acknowledged and alert_status == "acknowledged":
            return True
        if self.skip_if_resolved and alert_status == "resolved":
            return True

        for condition in self.skip_conditions:
            if self._evaluate_condition(condition, alert_data):
                return True

        return False

    def _evaluate_condition(
        self,
        condition: dict[str, Any],
        alert_data: dict[str, Any],
    ) -> bool:
        """Evaluate a skip condition against alert data."""
        field_name = condition.get("field")
        operator = condition.get("operator", "equals")
        value = condition.get("value")

        if not field_name:
            return False

        actual_value = alert_data.get(field_name)

        if operator == "equals":
            return actual_value == value
        elif operator == "not_equals":
            return actual_value != value
        elif operator == "contains":
            return value in (actual_value or "")
        elif operator == "in":
            return actual_value in (value or [])
        elif operator == "not_in":
            return actual_value not in (value or [])

        return False

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "step_number": self.step_number,
            "delay_minutes": self.delay_minutes,
            "targets": [t.to_dict() for t in self.targets],
            "notification_channels": [c.value for c in self.notification_channels],
            "repeat_count": self.repeat_count,
            "repeat_delay_minutes": self.repeat_delay_minutes,
            "description": self.description,
        }


@dataclass
class TimeBasedRule:
    """
    Time-based routing rule for escalation.

    Allows different escalation behavior during:
    - Business hours vs after-hours
    - Weekdays vs weekends
    - Specific time ranges
    """

    rule_id: str
    name: str
    timezone: str = "UTC"

    # Time restrictions
    days_of_week: list[int] | None = None  # 0=Monday, 6=Sunday; None=all
    start_time: time | None = None
    end_time: time | None = None

    # Override urgency for this time period
    override_urgency: EscalationUrgency | None = None

    # Use different escalation steps
    steps: list[EscalationStep] | None = None

    # Skip notification channels during this period
    disabled_channels: list[NotificationChannel] = field(default_factory=list)

    def is_active_at(self, dt: datetime) -> bool:
        """Check if this rule is active at the given time."""
        tz = ZoneInfo(self.timezone)
        local_dt = dt.astimezone(tz)

        # Check day of week
        if self.days_of_week is not None:
            if local_dt.weekday() not in self.days_of_week:
                return False

        # Check time range
        if self.start_time and self.end_time:
            local_time = local_dt.time()
            if self.start_time <= self.end_time:
                if not (self.start_time <= local_time < self.end_time):
                    return False
            else:
                # Overnight range
                if not (local_time >= self.start_time or local_time < self.end_time):
                    return False

        return True

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "rule_id": self.rule_id,
            "name": self.name,
            "timezone": self.timezone,
            "days_of_week": self.days_of_week,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "override_urgency": self.override_urgency.value if self.override_urgency else None,
        }


@dataclass
class MatchCondition:
    """Condition for matching alerts to escalation policies."""

    field: str
    operator: str  # equals, not_equals, contains, regex, in, not_in
    value: Any

    def matches(self, alert_data: dict[str, Any]) -> bool:
        """Check if alert data matches this condition."""
        actual = alert_data.get(self.field)

        if self.operator == "equals":
            return actual == self.value
        elif self.operator == "not_equals":
            return actual != self.value
        elif self.operator == "contains":
            return self.value in str(actual or "")
        elif self.operator == "regex":
            import re
            return bool(re.search(self.value, str(actual or "")))
        elif self.operator == "in":
            return actual in (self.value or [])
        elif self.operator == "not_in":
            return actual not in (self.value or [])
        elif self.operator == "exists":
            return actual is not None
        elif self.operator == "not_exists":
            return actual is None

        return False


@dataclass
class EscalationPolicy:
    """
    Complete escalation policy definition.

    Features:
    - Multiple escalation steps with configurable delays
    - Urgency-based escalation paths
    - Time-based routing rules
    - Alert matching conditions
    - Repeat/loop behavior
    - Fallback policies
    """

    policy_id: UUID
    name: str
    description: str = ""
    team_id: UUID | None = None

    # Default escalation steps
    steps: list[EscalationStep] = field(default_factory=list)

    # Urgency-specific steps (override defaults)
    high_urgency_steps: list[EscalationStep] | None = None
    low_urgency_steps: list[EscalationStep] | None = None

    # Time-based routing rules
    time_rules: list[TimeBasedRule] = field(default_factory=list)

    # Alert matching conditions
    match_conditions: list[MatchCondition] = field(default_factory=list)
    match_mode: str = "all"  # "all" = AND, "any" = OR

    # Repeat behavior
    repeat_enabled: bool = True
    repeat_limit: int = 3
    repeat_delay_minutes: int = 0  # 0 = immediate

    # Fallback
    fallback_policy_id: UUID | None = None

    # Policy settings
    is_default: bool = False
    is_active: bool = True
    priority: int = 0  # Higher priority = matched first

    # Metadata
    created_at: datetime = field(default_factory=lambda: datetime.now(dt_timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(dt_timezone.utc))

    def matches_alert(self, alert_data: dict[str, Any]) -> bool:
        """Check if this policy matches an alert."""
        if not self.match_conditions:
            return True  # No conditions = matches all

        results = [c.matches(alert_data) for c in self.match_conditions]

        if self.match_mode == "all":
            return all(results)
        else:  # "any"
            return any(results)

    def get_steps_for_alert(
        self,
        urgency: EscalationUrgency,
        at_time: datetime | None = None,
    ) -> list[EscalationStep]:
        """
        Get the appropriate escalation steps for an alert.

        Considers:
        - Alert urgency
        - Time-based routing rules
        """
        if at_time is None:
            at_time = datetime.now(dt_timezone.utc)

        # Check time-based rules first
        for rule in self.time_rules:
            if rule.is_active_at(at_time):
                if rule.steps:
                    return rule.steps
                # Rule may just modify urgency
                if rule.override_urgency:
                    urgency = rule.override_urgency

        # Return urgency-specific steps if defined
        if urgency == EscalationUrgency.HIGH and self.high_urgency_steps:
            return self.high_urgency_steps
        elif urgency == EscalationUrgency.LOW and self.low_urgency_steps:
            return self.low_urgency_steps

        return self.steps

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "policy_id": str(self.policy_id),
            "name": self.name,
            "description": self.description,
            "team_id": str(self.team_id) if self.team_id else None,
            "steps": [s.to_dict() for s in self.steps],
            "time_rules": [r.to_dict() for r in self.time_rules],
            "repeat_enabled": self.repeat_enabled,
            "repeat_limit": self.repeat_limit,
            "is_default": self.is_default,
            "is_active": self.is_active,
            "priority": self.priority,
        }


@dataclass
class EscalationState:
    """
    Tracks the current state of an escalation.

    Used to manage in-flight escalations:
    - Current step
    - Repeat count
    - Notification history
    """

    state_id: str
    alert_id: UUID
    policy_id: UUID
    urgency: EscalationUrgency

    # Current position
    current_step: int = 0
    current_repeat: int = 0
    total_repeats: int = 0

    # Status
    status: str = "active"  # active, acknowledged, resolved, exhausted, suppressed

    # Timing
    started_at: datetime = field(default_factory=lambda: datetime.now(dt_timezone.utc))
    last_step_at: datetime | None = None
    next_step_at: datetime | None = None

    # History
    notifications_sent: list[dict[str, Any]] = field(default_factory=list)

    def advance_step(self, steps: list[EscalationStep]) -> bool:
        """
        Advance to the next step in the escalation.

        Returns True if there's a next step, False if exhausted.
        """
        current_step_config = steps[self.current_step] if self.current_step < len(steps) else None

        # Check if we should repeat current step
        if current_step_config and self.current_repeat < current_step_config.repeat_count - 1:
            self.current_repeat += 1
            self.last_step_at = datetime.now(dt_timezone.utc)
            self.next_step_at = self.last_step_at + timedelta(
                minutes=current_step_config.repeat_delay_minutes
            )
            return True

        # Move to next step
        self.current_step += 1
        self.current_repeat = 0

        if self.current_step < len(steps):
            next_step = steps[self.current_step]
            self.last_step_at = datetime.now(dt_timezone.utc)
            self.next_step_at = self.last_step_at + timedelta(minutes=next_step.delay_minutes)
            return True

        return False

    def record_notification(
        self,
        target: EscalationTarget,
        channel: NotificationChannel,
        success: bool,
        error: str | None = None,
    ) -> None:
        """Record a notification attempt."""
        self.notifications_sent.append({
            "step": self.current_step,
            "repeat": self.current_repeat,
            "target_type": target.target_type.value,
            "target_id": target.target_id,
            "channel": channel.value,
            "success": success,
            "error": error,
            "sent_at": datetime.now(dt_timezone.utc).isoformat(),
        })

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "state_id": self.state_id,
            "alert_id": str(self.alert_id),
            "policy_id": str(self.policy_id),
            "urgency": self.urgency.value,
            "current_step": self.current_step,
            "current_repeat": self.current_repeat,
            "total_repeats": self.total_repeats,
            "status": self.status,
            "started_at": self.started_at.isoformat(),
            "last_step_at": self.last_step_at.isoformat() if self.last_step_at else None,
            "next_step_at": self.next_step_at.isoformat() if self.next_step_at else None,
            "notifications_sent_count": len(self.notifications_sent),
        }


class EscalationPolicyBuilder:
    """
    Fluent builder for creating escalation policies.

    Example:
        policy = (
            EscalationPolicyBuilder("Critical Alerts Policy")
            .add_step(
                delay_minutes=0,
                targets=[
                    EscalationTarget(TargetType.SCHEDULE, "schedule-id", schedule_layer="primary")
                ],
                channels=[NotificationChannel.PUSH, NotificationChannel.SMS]
            )
            .add_step(
                delay_minutes=5,
                targets=[
                    EscalationTarget(TargetType.SCHEDULE, "schedule-id", schedule_layer="secondary")
                ],
                channels=[NotificationChannel.PHONE]
            )
            .add_step(
                delay_minutes=10,
                targets=[
                    EscalationTarget(TargetType.TEAM, "team-id")
                ]
            )
            .with_repeat(enabled=True, limit=2)
            .for_high_urgency()
            .build()
        )
    """

    def __init__(self, name: str, policy_id: UUID | None = None):
        self.name = name
        self.policy_id = policy_id or UUID("00000000-0000-0000-0000-000000000000")
        self.description = ""
        self.team_id: UUID | None = None
        self.steps: list[EscalationStep] = []
        self.high_urgency_steps: list[EscalationStep] | None = None
        self.low_urgency_steps: list[EscalationStep] | None = None
        self.time_rules: list[TimeBasedRule] = []
        self.match_conditions: list[MatchCondition] = []
        self.match_mode = "all"
        self.repeat_enabled = True
        self.repeat_limit = 3
        self.is_default = False
        self._building_high_urgency = False
        self._building_low_urgency = False

    def with_description(self, description: str) -> "EscalationPolicyBuilder":
        """Set policy description."""
        self.description = description
        return self

    def for_team(self, team_id: UUID) -> "EscalationPolicyBuilder":
        """Set the team this policy belongs to."""
        self.team_id = team_id
        return self

    def add_step(
        self,
        delay_minutes: int,
        targets: list[EscalationTarget],
        channels: list[NotificationChannel] | None = None,
        repeat_count: int = 1,
        repeat_delay_minutes: int = 5,
        description: str | None = None,
    ) -> "EscalationPolicyBuilder":
        """Add an escalation step."""
        if channels is None:
            channels = [NotificationChannel.PUSH, NotificationChannel.SMS]

        step_list = self.steps
        if self._building_high_urgency:
            if self.high_urgency_steps is None:
                self.high_urgency_steps = []
            step_list = self.high_urgency_steps
        elif self._building_low_urgency:
            if self.low_urgency_steps is None:
                self.low_urgency_steps = []
            step_list = self.low_urgency_steps

        step = EscalationStep(
            step_number=len(step_list),
            delay_minutes=delay_minutes,
            targets=targets,
            notification_channels=channels,
            repeat_count=repeat_count,
            repeat_delay_minutes=repeat_delay_minutes,
            description=description,
        )
        step_list.append(step)
        return self

    def add_schedule_step(
        self,
        schedule_id: str,
        delay_minutes: int,
        layer: str = "primary",
        channels: list[NotificationChannel] | None = None,
    ) -> "EscalationPolicyBuilder":
        """Convenience method to add a schedule target step."""
        target = EscalationTarget(
            target_type=TargetType.SCHEDULE,
            target_id=schedule_id,
            schedule_layer=layer,
        )
        return self.add_step(
            delay_minutes=delay_minutes,
            targets=[target],
            channels=channels,
        )

    def add_user_step(
        self,
        user_id: str,
        user_name: str,
        delay_minutes: int,
        channels: list[NotificationChannel] | None = None,
    ) -> "EscalationPolicyBuilder":
        """Convenience method to add a user target step."""
        target = EscalationTarget(
            target_type=TargetType.USER,
            target_id=user_id,
            target_name=user_name,
        )
        return self.add_step(
            delay_minutes=delay_minutes,
            targets=[target],
            channels=channels,
        )

    def add_team_step(
        self,
        team_id: str,
        delay_minutes: int,
        notify_strategy: str = "all",
        channels: list[NotificationChannel] | None = None,
    ) -> "EscalationPolicyBuilder":
        """Convenience method to add a team target step."""
        target = EscalationTarget(
            target_type=TargetType.TEAM,
            target_id=team_id,
            team_notify_strategy=notify_strategy,
        )
        return self.add_step(
            delay_minutes=delay_minutes,
            targets=[target],
            channels=channels,
        )

    def add_slack_step(
        self,
        channel_id: str,
        delay_minutes: int,
        mention_users: bool = True,
    ) -> "EscalationPolicyBuilder":
        """Convenience method to add a Slack channel step."""
        target = EscalationTarget(
            target_type=TargetType.SLACK_CHANNEL,
            target_id=channel_id,
            slack_channel_id=channel_id,
            slack_mention_users=mention_users,
        )
        return self.add_step(
            delay_minutes=delay_minutes,
            targets=[target],
            channels=[NotificationChannel.SLACK],
        )

    def add_webhook_step(
        self,
        webhook_url: str,
        delay_minutes: int,
        headers: dict[str, str] | None = None,
    ) -> "EscalationPolicyBuilder":
        """Convenience method to add a webhook step."""
        target = EscalationTarget(
            target_type=TargetType.WEBHOOK,
            target_id=webhook_url,
            webhook_url=webhook_url,
            webhook_headers=headers or {},
        )
        return self.add_step(
            delay_minutes=delay_minutes,
            targets=[target],
            channels=[NotificationChannel.WEBHOOK],
        )

    def for_high_urgency(self) -> "EscalationPolicyBuilder":
        """Start defining high-urgency specific steps."""
        self._building_high_urgency = True
        self._building_low_urgency = False
        return self

    def for_low_urgency(self) -> "EscalationPolicyBuilder":
        """Start defining low-urgency specific steps."""
        self._building_high_urgency = False
        self._building_low_urgency = True
        return self

    def for_default_urgency(self) -> "EscalationPolicyBuilder":
        """Go back to defining default steps."""
        self._building_high_urgency = False
        self._building_low_urgency = False
        return self

    def add_time_rule(
        self,
        name: str,
        rule_id: str,
        timezone: str = "UTC",
        days_of_week: list[int] | None = None,
        start_time: time | None = None,
        end_time: time | None = None,
        override_urgency: EscalationUrgency | None = None,
        disabled_channels: list[NotificationChannel] | None = None,
    ) -> "EscalationPolicyBuilder":
        """Add a time-based routing rule."""
        rule = TimeBasedRule(
            rule_id=rule_id,
            name=name,
            timezone=timezone,
            days_of_week=days_of_week,
            start_time=start_time,
            end_time=end_time,
            override_urgency=override_urgency,
            disabled_channels=disabled_channels or [],
        )
        self.time_rules.append(rule)
        return self

    def add_business_hours_rule(
        self,
        timezone: str = "UTC",
        start_hour: int = 9,
        end_hour: int = 17,
        override_urgency: EscalationUrgency | None = None,
    ) -> "EscalationPolicyBuilder":
        """Add a business hours rule (Mon-Fri, 9-5)."""
        return self.add_time_rule(
            name="Business Hours",
            rule_id="business_hours",
            timezone=timezone,
            days_of_week=[0, 1, 2, 3, 4],  # Mon-Fri
            start_time=time(start_hour, 0),
            end_time=time(end_hour, 0),
            override_urgency=override_urgency,
        )

    def add_after_hours_rule(
        self,
        timezone: str = "UTC",
        start_hour: int = 17,
        end_hour: int = 9,
        override_urgency: EscalationUrgency | None = None,
        disabled_channels: list[NotificationChannel] | None = None,
    ) -> "EscalationPolicyBuilder":
        """Add an after-hours rule."""
        return self.add_time_rule(
            name="After Hours",
            rule_id="after_hours",
            timezone=timezone,
            days_of_week=None,  # All days
            start_time=time(start_hour, 0),
            end_time=time(end_hour, 0),
            override_urgency=override_urgency,
            disabled_channels=disabled_channels,
        )

    def match_severity(self, severities: list[str]) -> "EscalationPolicyBuilder":
        """Only match alerts with specific severities."""
        self.match_conditions.append(MatchCondition(
            field="severity",
            operator="in",
            value=severities,
        ))
        return self

    def match_service(self, services: list[str]) -> "EscalationPolicyBuilder":
        """Only match alerts from specific services."""
        self.match_conditions.append(MatchCondition(
            field="source",
            operator="in",
            value=services,
        ))
        return self

    def match_tags(self, tags: list[str], mode: str = "any") -> "EscalationPolicyBuilder":
        """Only match alerts with specific tags."""
        for tag in tags:
            self.match_conditions.append(MatchCondition(
                field="tags",
                operator="contains",
                value=tag,
            ))
        if mode == "any":
            self.match_mode = "any"
        return self

    def with_repeat(
        self,
        enabled: bool = True,
        limit: int = 3,
        delay_minutes: int = 0,
    ) -> "EscalationPolicyBuilder":
        """Configure repeat behavior."""
        self.repeat_enabled = enabled
        self.repeat_limit = limit
        return self

    def as_default(self) -> "EscalationPolicyBuilder":
        """Mark this as the default policy."""
        self.is_default = True
        return self

    def build(self) -> EscalationPolicy:
        """Build the escalation policy."""
        return EscalationPolicy(
            policy_id=self.policy_id,
            name=self.name,
            description=self.description,
            team_id=self.team_id,
            steps=self.steps,
            high_urgency_steps=self.high_urgency_steps,
            low_urgency_steps=self.low_urgency_steps,
            time_rules=self.time_rules,
            match_conditions=self.match_conditions,
            match_mode=self.match_mode,
            repeat_enabled=self.repeat_enabled,
            repeat_limit=self.repeat_limit,
            is_default=self.is_default,
        )


# ============================================================================
# FACTORY FUNCTIONS FOR COMMON POLICIES
# ============================================================================

def create_simple_policy(
    name: str,
    schedule_id: str,
    team_id: UUID | None = None,
    policy_id: UUID | None = None,
) -> EscalationPolicy:
    """
    Create a simple escalation policy.

    Steps:
    1. Notify primary on-call immediately
    2. After 5 min, notify secondary
    3. After 10 min, notify entire team
    """
    return (
        EscalationPolicyBuilder(name, policy_id)
        .for_team(team_id) if team_id else EscalationPolicyBuilder(name, policy_id)
    ).add_schedule_step(schedule_id, delay_minutes=0, layer="primary").add_schedule_step(
        schedule_id, delay_minutes=5, layer="secondary"
    ).add_team_step(
        str(team_id) if team_id else "team", delay_minutes=10
    ).with_repeat(enabled=True, limit=2).as_default().build()


def create_critical_alerts_policy(
    name: str,
    schedule_id: str,
    slack_channel_id: str,
    team_id: UUID | None = None,
    policy_id: UUID | None = None,
) -> EscalationPolicy:
    """
    Create a policy for critical alerts.

    Features:
    - Immediate phone call to primary
    - Multiple notification channels
    - Slack notification
    - Faster escalation
    """
    builder = EscalationPolicyBuilder(name, policy_id)
    if team_id:
        builder.for_team(team_id)

    return (
        builder
        .with_description("Aggressive escalation for critical alerts")
        .match_severity(["critical", "p1", "sev1"])
        .add_step(
            delay_minutes=0,
            targets=[
                EscalationTarget(TargetType.SCHEDULE, schedule_id, schedule_layer="primary")
            ],
            channels=[
                NotificationChannel.PUSH,
                NotificationChannel.SMS,
                NotificationChannel.PHONE,
            ],
            description="Immediately notify primary on-call via all channels",
        )
        .add_slack_step(slack_channel_id, delay_minutes=1)
        .add_step(
            delay_minutes=3,
            targets=[
                EscalationTarget(TargetType.SCHEDULE, schedule_id, schedule_layer="secondary"),
                EscalationTarget(TargetType.SCHEDULE, schedule_id, schedule_layer="primary"),
            ],
            channels=[NotificationChannel.PHONE],
            description="Call both primary and secondary",
        )
        .add_team_step(str(team_id) if team_id else "team", delay_minutes=5)
        .with_repeat(enabled=True, limit=3)
        .build()
    )


def create_business_hours_policy(
    name: str,
    schedule_id: str,
    timezone: str = "UTC",
    team_id: UUID | None = None,
    policy_id: UUID | None = None,
) -> EscalationPolicy:
    """
    Create a policy with different behavior for business hours.

    During business hours: slack first, then escalate
    After hours: immediate phone call
    """
    builder = EscalationPolicyBuilder(name, policy_id)
    if team_id:
        builder.for_team(team_id)

    return (
        builder
        .with_description("Different escalation paths for business hours vs after hours")
        # Default steps (used during business hours)
        .add_step(
            delay_minutes=0,
            targets=[
                EscalationTarget(TargetType.SCHEDULE, schedule_id, schedule_layer="primary")
            ],
            channels=[NotificationChannel.SLACK, NotificationChannel.PUSH],
            description="Slack and push during business hours",
        )
        .add_step(
            delay_minutes=10,
            targets=[
                EscalationTarget(TargetType.SCHEDULE, schedule_id, schedule_layer="primary")
            ],
            channels=[NotificationChannel.SMS, NotificationChannel.PHONE],
            description="Escalate to SMS/phone if no response",
        )
        # After hours: more aggressive
        .add_business_hours_rule(timezone=timezone)
        .add_after_hours_rule(
            timezone=timezone,
            override_urgency=EscalationUrgency.HIGH,  # Treat all alerts as high urgency
        )
        # High urgency steps (used after hours due to override)
        .for_high_urgency()
        .add_step(
            delay_minutes=0,
            targets=[
                EscalationTarget(TargetType.SCHEDULE, schedule_id, schedule_layer="primary")
            ],
            channels=[NotificationChannel.PHONE, NotificationChannel.SMS],
            description="Immediate phone call after hours",
        )
        .add_step(
            delay_minutes=5,
            targets=[
                EscalationTarget(TargetType.SCHEDULE, schedule_id, schedule_layer="secondary")
            ],
            channels=[NotificationChannel.PHONE],
            description="Escalate to secondary quickly",
        )
        .build()
    )
