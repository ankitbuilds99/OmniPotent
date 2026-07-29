"""On-Call Management Database Models.

Complete SQLAlchemy models for enterprise on-call management:
- Teams and team members
- Schedules and rotations
- Shifts and overrides
- Escalation policies
- Alerts and notifications
- Time-off and holidays
- Compensation tracking
- Metrics
"""

from __future__ import annotations

import uuid
from datetime import datetime, date, time
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Date,
    Time,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Numeric,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID, ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.models import Base


# ============================================================================
# TEAM MODELS
# ============================================================================

class OncallTeam(Base):
    """Teams/services that need on-call coverage."""

    __tablename__ = "oncall_teams"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    timezone: Mapped[str] = mapped_column(String(50), default="UTC", nullable=False)
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, default={
        "default_rotation_type": "weekly",
        "allow_self_coverage": True,
        "require_acknowledgment": True,
        "ack_timeout_minutes": 5,
        "auto_escalate": True,
    })
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    members: Mapped[list["OncallTeamMember"]] = relationship(
        back_populates="team", cascade="all, delete-orphan"
    )
    schedules: Mapped[list["OncallSchedule"]] = relationship(
        back_populates="team", cascade="all, delete-orphan"
    )
    escalation_policies: Mapped[list["OncallEscalationPolicy"]] = relationship(
        back_populates="team", cascade="all, delete-orphan"
    )
    alerts: Mapped[list["OncallAlert"]] = relationship(back_populates="team")

    __table_args__ = (
        UniqueConstraint("org_id", "slug", name="uq_oncall_team_slug"),
        Index("ix_oncall_teams_org", "org_id"),
    )


class OncallTeamMember(Base):
    """Team members with their on-call preferences."""

    __tablename__ = "oncall_team_members"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("oncall_teams.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("members.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(50), default="member", nullable=False)

    # Contact preferences (order matters for escalation)
    contact_methods: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=[
        {"type": "push", "delay_minutes": 0},
        {"type": "sms", "delay_minutes": 1},
        {"type": "phone", "delay_minutes": 3},
        {"type": "email", "delay_minutes": 5},
    ])

    # Availability preferences
    timezone: Mapped[str | None] = mapped_column(String(50))
    working_hours: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    # Skills for AI routing
    skills: Mapped[list[str]] = mapped_column(JSONB, default=[])

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    team: Mapped["OncallTeam"] = relationship(back_populates="members")

    __table_args__ = (
        UniqueConstraint("team_id", "user_id", name="uq_oncall_member"),
        Index("ix_oncall_members_team", "team_id"),
        Index("ix_oncall_members_user", "user_id"),
    )


# ============================================================================
# SCHEDULE MODELS
# ============================================================================

class OncallSchedule(Base):
    """Master schedule definition."""

    __tablename__ = "oncall_schedules"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("oncall_teams.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    timezone: Mapped[str] = mapped_column(String(50), default="UTC", nullable=False)

    # Schedule type
    schedule_type: Mapped[str] = mapped_column(String(50), default="rotation", nullable=False)

    # Rotation settings
    rotation_config: Mapped[dict[str, Any]] = mapped_column(JSONB, default={
        "type": "weekly",
        "handoff_time": "09:00",
        "handoff_day": "monday",
        "length_days": 7,
    })

    # Coverage requirements
    coverage_type: Mapped[str] = mapped_column(String(50), default="24x7", nullable=False)
    coverage_hours: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    # Layer (primary=1, secondary=2, tertiary=3)
    layer: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    effective_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    effective_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    team: Mapped["OncallTeam"] = relationship(back_populates="schedules")
    participants: Mapped[list["OncallRotationParticipant"]] = relationship(
        back_populates="schedule", cascade="all, delete-orphan", order_by="OncallRotationParticipant.position"
    )
    shifts: Mapped[list["OncallShift"]] = relationship(
        back_populates="schedule", cascade="all, delete-orphan"
    )
    overrides: Mapped[list["OncallOverride"]] = relationship(
        back_populates="schedule", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_oncall_schedules_team", "team_id"),
        Index("ix_oncall_schedules_active", "is_active"),
    )


class OncallRotationParticipant(Base):
    """Rotation participants (ordered)."""

    __tablename__ = "oncall_rotation_participants"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    schedule_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("oncall_schedules.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("members.id", ondelete="CASCADE"), nullable=False
    )

    # Position in rotation
    position: Mapped[int] = mapped_column(Integer, nullable=False)

    # Participation settings
    participation_type: Mapped[str] = mapped_column(String(50), default="full", nullable=False)
    participation_percentage: Mapped[int] = mapped_column(Integer, default=100, nullable=False)

    # Skip settings
    skip_next: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    effective_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    effective_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    schedule: Mapped["OncallSchedule"] = relationship(back_populates="participants")

    __table_args__ = (
        UniqueConstraint("schedule_id", "position", name="uq_rotation_position"),
        Index("ix_oncall_participants_schedule", "schedule_id"),
    )


class OncallShift(Base):
    """Actual shift assignments (generated from rotation or manual)."""

    __tablename__ = "oncall_shifts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    schedule_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("oncall_schedules.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("members.id"), nullable=False
    )

    # Shift timing
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Shift type: primary, secondary, shadow, override, swap
    shift_type: Mapped[str] = mapped_column(String(50), default="primary", nullable=False)

    # Source: rotation, manual, override, swap
    source: Mapped[str] = mapped_column(String(50), default="rotation", nullable=False)
    source_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))

    # Status: scheduled, active, completed, swapped, cancelled
    status: Mapped[str] = mapped_column(String(50), default="scheduled", nullable=False)

    # For shadow rotations (training)
    shadow_of: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("oncall_shifts.id")
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    schedule: Mapped["OncallSchedule"] = relationship(back_populates="shifts")
    shadow_shift: Mapped["OncallShift | None"] = relationship(remote_side=[id])

    __table_args__ = (
        Index("ix_oncall_shifts_schedule_time", "schedule_id", "start_time", "end_time"),
        Index("ix_oncall_shifts_user_time", "user_id", "start_time", "end_time"),
        Index("ix_oncall_shifts_status", "status"),
    )


# ============================================================================
# OVERRIDE & SWAP MODELS
# ============================================================================

class OncallOverride(Base):
    """Schedule overrides (temporary coverage changes)."""

    __tablename__ = "oncall_overrides"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    schedule_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("oncall_schedules.id", ondelete="CASCADE"), nullable=False
    )

    # Who is being replaced
    original_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("members.id")
    )

    # Who is covering
    override_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("members.id"), nullable=False
    )

    # Override period
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Reason: pto, sick, swap, training, other
    reason: Mapped[str | None] = mapped_column(String(50))
    notes: Mapped[str | None] = mapped_column(Text)

    # Approval
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("members.id")
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Status: pending, active, cancelled
    status: Mapped[str] = mapped_column(String(50), default="active", nullable=False)

    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("members.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    schedule: Mapped["OncallSchedule"] = relationship(back_populates="overrides")

    __table_args__ = (
        Index("ix_oncall_overrides_schedule_time", "schedule_id", "start_time", "end_time"),
        Index("ix_oncall_overrides_status", "status"),
    )


class OncallSwapRequest(Base):
    """Shift swap requests."""

    __tablename__ = "oncall_swap_requests"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # Requestor's shift
    from_shift_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("oncall_shifts.id"), nullable=False
    )
    from_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("members.id"), nullable=False
    )

    # Target (can be specific user or open request)
    to_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("members.id")
    )
    to_shift_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("oncall_shifts.id")
    )

    # Timing
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Status: pending, accepted, declined, expired, cancelled
    status: Mapped[str] = mapped_column(String(50), default="pending", nullable=False)

    # Response
    responded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("members.id")
    )
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    reason: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_oncall_swaps_from_shift", "from_shift_id"),
        Index("ix_oncall_swaps_status", "status"),
    )


# ============================================================================
# ESCALATION MODELS
# ============================================================================

class OncallEscalationPolicy(Base):
    """Escalation policies."""

    __tablename__ = "oncall_escalation_policies"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("oncall_teams.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    # When to use this policy
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    conditions: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    # Repeat settings
    repeat_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    repeat_limit: Mapped[int] = mapped_column(Integer, default=3, nullable=False)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    team: Mapped["OncallTeam"] = relationship(back_populates="escalation_policies")
    steps: Mapped[list["OncallEscalationStep"]] = relationship(
        back_populates="policy", cascade="all, delete-orphan", order_by="OncallEscalationStep.step_number"
    )

    __table_args__ = (
        Index("ix_oncall_policies_team", "team_id"),
    )


class OncallEscalationStep(Base):
    """Escalation steps."""

    __tablename__ = "oncall_escalation_steps"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    policy_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("oncall_escalation_policies.id", ondelete="CASCADE"), nullable=False
    )

    step_number: Mapped[int] = mapped_column(Integer, nullable=False)

    # Timing
    delay_minutes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Targets: [{"type": "schedule", "schedule_id": "...", "layer": "primary"}, ...]
    targets: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)

    # Notification settings
    notification_channels: Mapped[list[str]] = mapped_column(JSONB, default=["push", "sms", "phone"])

    # Conditions to skip this step
    skip_if: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    policy: Mapped["OncallEscalationPolicy"] = relationship(back_populates="steps")

    __table_args__ = (
        UniqueConstraint("policy_id", "step_number", name="uq_escalation_step"),
    )


# ============================================================================
# ALERT MODELS
# ============================================================================

class OncallAlert(Base):
    """Incoming alerts."""

    __tablename__ = "oncall_alerts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False
    )
    team_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("oncall_teams.id")
    )

    # Alert details
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(String(50), default="warning", nullable=False)

    # Source
    source: Mapped[str | None] = mapped_column(String(100))
    source_id: Mapped[str | None] = mapped_column(String(255))
    source_url: Mapped[str | None] = mapped_column(Text)

    # Deduplication
    dedup_key: Mapped[str | None] = mapped_column(String(500))

    # Status: triggered, acknowledged, resolved, suppressed
    status: Mapped[str] = mapped_column(String(50), default="triggered", nullable=False)

    # Timing
    triggered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("members.id")
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("members.id")
    )

    # Escalation tracking
    escalation_policy_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("oncall_escalation_policies.id")
    )
    current_escalation_step: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    escalation_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # AI analysis
    ai_analysis: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    ai_suggested_responder: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("members.id")
    )
    ai_routing_reason: Mapped[str | None] = mapped_column(Text)

    # Metadata
    tags: Mapped[list[str]] = mapped_column(JSONB, default=[])
    custom_fields: Mapped[dict[str, Any]] = mapped_column(JSONB, default={})
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    # Link to incident
    incident_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    team: Mapped["OncallTeam | None"] = relationship(back_populates="alerts")
    notifications: Mapped[list["OncallNotification"]] = relationship(
        back_populates="alert", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_oncall_alerts_org_status", "org_id", "status"),
        Index("ix_oncall_alerts_team_time", "team_id", "triggered_at"),
        Index("ix_oncall_alerts_dedup", "dedup_key"),
    )


class OncallNotification(Base):
    """Alert notifications sent."""

    __tablename__ = "oncall_notifications"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    alert_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("oncall_alerts.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("members.id"), nullable=False
    )

    # Notification details
    channel: Mapped[str] = mapped_column(String(50), nullable=False)
    contact_value: Mapped[str | None] = mapped_column(String(255))

    # Escalation context
    escalation_step: Mapped[int | None] = mapped_column(Integer)
    attempt_number: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    # Status: pending, sent, delivered, failed, acknowledged
    status: Mapped[str] = mapped_column(String(50), default="pending", nullable=False)

    # Timing
    scheduled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Provider response
    provider: Mapped[str | None] = mapped_column(String(50))
    provider_id: Mapped[str | None] = mapped_column(String(255))
    provider_response: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    # Failure info
    error_message: Mapped[str | None] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    alert: Mapped["OncallAlert"] = relationship(back_populates="notifications")

    __table_args__ = (
        Index("ix_oncall_notifications_alert", "alert_id"),
        Index("ix_oncall_notifications_user_pending", "user_id", "status"),
    )


# ============================================================================
# TIME OFF & HOLIDAY MODELS
# ============================================================================

class OncallHolidayCalendar(Base):
    """Holiday calendars."""

    __tablename__ = "oncall_holiday_calendars"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    country_code: Mapped[str | None] = mapped_column(String(10))
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    holidays: Mapped[list["OncallHoliday"]] = relationship(
        back_populates="calendar", cascade="all, delete-orphan"
    )


class OncallHoliday(Base):
    """Holidays."""

    __tablename__ = "oncall_holidays"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    calendar_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("oncall_holiday_calendars.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    holiday_date: Mapped[date] = mapped_column(Date, nullable=False)
    is_recurring: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    affects_coverage: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    calendar: Mapped["OncallHolidayCalendar"] = relationship(back_populates="holidays")


class OncallTimeOff(Base):
    """User time off."""

    __tablename__ = "oncall_time_off"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("members.id", ondelete="CASCADE"), nullable=False
    )

    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)

    # Type: pto, sick, personal, other
    time_off_type: Mapped[str] = mapped_column(String(50), default="pto", nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)

    # Approval
    status: Mapped[str] = mapped_column(String(50), default="approved", nullable=False)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("members.id")
    )

    # Coverage handled?
    coverage_arranged: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    coverage_override_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("oncall_overrides.id")
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_oncall_time_off_user_dates", "user_id", "start_date", "end_date"),
    )


# ============================================================================
# COMPENSATION MODELS
# ============================================================================

class OncallCompensationRule(Base):
    """Compensation rules."""

    __tablename__ = "oncall_compensation_rules"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    team_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("oncall_teams.id")
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)

    # Rate type: hourly, daily, weekly, per_incident
    rate_type: Mapped[str] = mapped_column(String(50), nullable=False)

    # Base rates
    base_rate: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(10), default="USD", nullable=False)

    # Multipliers
    weekend_multiplier: Mapped[float] = mapped_column(Numeric(4, 2), default=1.5, nullable=False)
    holiday_multiplier: Mapped[float] = mapped_column(Numeric(4, 2), default=2.0, nullable=False)
    night_multiplier: Mapped[float] = mapped_column(Numeric(4, 2), default=1.25, nullable=False)
    night_hours_start: Mapped[time] = mapped_column(Time, default=time(22, 0), nullable=False)
    night_hours_end: Mapped[time] = mapped_column(Time, default=time(6, 0), nullable=False)

    # Per-incident bonus
    incident_bonus: Mapped[float] = mapped_column(Numeric(10, 2), default=0, nullable=False)
    critical_incident_bonus: Mapped[float] = mapped_column(Numeric(10, 2), default=0, nullable=False)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_until: Mapped[date | None] = mapped_column(Date)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class OncallCompensationRecord(Base):
    """Calculated compensation records."""

    __tablename__ = "oncall_compensation_records"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("members.id"), nullable=False
    )

    # Period
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)

    # Breakdown
    regular_hours: Mapped[float] = mapped_column(Numeric(10, 2), default=0, nullable=False)
    weekend_hours: Mapped[float] = mapped_column(Numeric(10, 2), default=0, nullable=False)
    holiday_hours: Mapped[float] = mapped_column(Numeric(10, 2), default=0, nullable=False)
    night_hours: Mapped[float] = mapped_column(Numeric(10, 2), default=0, nullable=False)

    incidents_handled: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    critical_incidents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Calculated amounts
    regular_amount: Mapped[float] = mapped_column(Numeric(10, 2), default=0, nullable=False)
    weekend_amount: Mapped[float] = mapped_column(Numeric(10, 2), default=0, nullable=False)
    holiday_amount: Mapped[float] = mapped_column(Numeric(10, 2), default=0, nullable=False)
    night_amount: Mapped[float] = mapped_column(Numeric(10, 2), default=0, nullable=False)
    incident_amount: Mapped[float] = mapped_column(Numeric(10, 2), default=0, nullable=False)

    total_amount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(10), default="USD", nullable=False)

    # Status: pending, approved, paid
    status: Mapped[str] = mapped_column(String(50), default="pending", nullable=False)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("members.id")
    )
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ============================================================================
# METRICS MODELS
# ============================================================================

class OncallMetrics(Base):
    """On-call metrics (hourly aggregates)."""

    __tablename__ = "oncall_metrics"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("oncall_teams.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("members.id")
    )

    # Time bucket
    hour_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Metrics
    alerts_received: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    alerts_acknowledged: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    alerts_resolved: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    alerts_escalated: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    avg_ack_time_seconds: Mapped[int | None] = mapped_column(Integer)
    avg_resolve_time_seconds: Mapped[int | None] = mapped_column(Integer)

    notifications_sent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    notifications_failed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("team_id", "user_id", "hour_start", name="uq_oncall_metrics"),
        Index("ix_oncall_metrics_team_hour", "team_id", "hour_start"),
    )
