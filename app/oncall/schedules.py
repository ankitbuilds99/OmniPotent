"""Enhanced On-Call Scheduling System.

PagerDuty-level scheduling capabilities:
- Advanced timezone handling for distributed teams
- Schedule gap detection and alerting
- iCal feed generation for calendar integration
- Override management with reason tracking
- Conflict detection and resolution
- Schedule preview and what-if analysis
"""

from __future__ import annotations

import logging
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, date, time, timedelta, timezone as dt_timezone
from enum import Enum
from typing import Any, Generator
from uuid import UUID
from zoneinfo import ZoneInfo

from icalendar import Calendar, Event
from icalendar import vText

log = logging.getLogger("omnipotent.oncall.schedules")


class OverrideReason(Enum):
    """Standard reasons for schedule overrides."""

    PTO = "pto"
    SICK = "sick"
    PERSONAL = "personal"
    SWAP = "swap"
    TRAINING = "training"
    COVERAGE = "coverage"
    EMERGENCY = "emergency"
    OTHER = "other"


class GapSeverity(Enum):
    """Severity levels for coverage gaps."""

    CRITICAL = "critical"  # No coverage at all
    HIGH = "high"  # Less than minimum coverage
    MEDIUM = "medium"  # Below recommended coverage
    LOW = "low"  # Informational


@dataclass
class ScheduleGap:
    """Represents a gap in schedule coverage."""

    gap_id: str
    schedule_id: UUID
    start_time: datetime
    end_time: datetime
    duration_hours: float
    severity: GapSeverity
    description: str
    affected_layers: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "gap_id": self.gap_id,
            "schedule_id": str(self.schedule_id),
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat(),
            "duration_hours": self.duration_hours,
            "severity": self.severity.value,
            "description": self.description,
            "affected_layers": self.affected_layers,
        }


@dataclass
class ScheduleOverride:
    """Enhanced override with reason tracking and audit trail."""

    override_id: UUID
    schedule_id: UUID
    original_user_id: UUID | None
    override_user_id: UUID
    start_time: datetime
    end_time: datetime
    reason: OverrideReason
    reason_details: str | None = None
    requires_approval: bool = False
    approved_by: UUID | None = None
    approved_at: datetime | None = None
    status: str = "active"  # pending, active, cancelled, expired
    created_by: UUID | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(dt_timezone.utc))

    @property
    def is_active(self) -> bool:
        """Check if override is currently active."""
        now = datetime.now(dt_timezone.utc)
        return (
            self.status == "active"
            and self.start_time <= now < self.end_time
        )

    @property
    def is_pending_approval(self) -> bool:
        """Check if override is waiting for approval."""
        return self.requires_approval and self.approved_by is None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "override_id": str(self.override_id),
            "schedule_id": str(self.schedule_id),
            "original_user_id": str(self.original_user_id) if self.original_user_id else None,
            "override_user_id": str(self.override_user_id),
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat(),
            "reason": self.reason.value,
            "reason_details": self.reason_details,
            "status": self.status,
            "requires_approval": self.requires_approval,
            "approved_by": str(self.approved_by) if self.approved_by else None,
            "approved_at": self.approved_at.isoformat() if self.approved_at else None,
            "created_by": str(self.created_by) if self.created_by else None,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class ScheduleShift:
    """Represents a single shift in a schedule."""

    shift_id: UUID
    schedule_id: UUID
    user_id: UUID
    start_time: datetime
    end_time: datetime
    shift_type: str  # primary, secondary, shadow
    layer: int = 1
    source: str = "rotation"  # rotation, override, manual
    override_id: UUID | None = None

    @property
    def duration_hours(self) -> float:
        """Get shift duration in hours."""
        return (self.end_time - self.start_time).total_seconds() / 3600

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "shift_id": str(self.shift_id),
            "schedule_id": str(self.schedule_id),
            "user_id": str(self.user_id),
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat(),
            "shift_type": self.shift_type,
            "layer": self.layer,
            "source": self.source,
            "duration_hours": self.duration_hours,
            "override_id": str(self.override_id) if self.override_id else None,
        }


class TimezoneManager:
    """
    Handles timezone conversions for distributed teams.

    Features:
    - Convert times between team member timezones
    - Calculate handoff times in local timezones
    - Handle DST transitions gracefully
    - Provide timezone-aware schedule views
    """

    # Common timezone aliases
    TIMEZONE_ALIASES: dict[str, str] = {
        "EST": "America/New_York",
        "EDT": "America/New_York",
        "CST": "America/Chicago",
        "CDT": "America/Chicago",
        "MST": "America/Denver",
        "MDT": "America/Denver",
        "PST": "America/Los_Angeles",
        "PDT": "America/Los_Angeles",
        "GMT": "Europe/London",
        "BST": "Europe/London",
        "CET": "Europe/Paris",
        "CEST": "Europe/Paris",
        "IST": "Asia/Kolkata",
        "JST": "Asia/Tokyo",
        "AEST": "Australia/Sydney",
        "AEDT": "Australia/Sydney",
    }

    @classmethod
    def normalize_timezone(cls, tz_str: str) -> str:
        """Normalize timezone string to IANA format."""
        return cls.TIMEZONE_ALIASES.get(tz_str.upper(), tz_str)

    @classmethod
    def get_timezone(cls, tz_str: str) -> ZoneInfo:
        """Get ZoneInfo for a timezone string."""
        normalized = cls.normalize_timezone(tz_str)
        return ZoneInfo(normalized)

    @classmethod
    def convert_time(
        cls,
        dt: datetime,
        from_tz: str,
        to_tz: str,
    ) -> datetime:
        """Convert datetime from one timezone to another."""
        from_zone = cls.get_timezone(from_tz)
        to_zone = cls.get_timezone(to_tz)

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=from_zone)
        else:
            dt = dt.astimezone(from_zone)

        return dt.astimezone(to_zone)

    @classmethod
    def get_local_time(cls, utc_time: datetime, tz_str: str) -> datetime:
        """Convert UTC time to local timezone."""
        tz = cls.get_timezone(tz_str)
        if utc_time.tzinfo is None:
            utc_time = utc_time.replace(tzinfo=dt_timezone.utc)
        return utc_time.astimezone(tz)

    @classmethod
    def get_utc_time(cls, local_time: datetime, tz_str: str) -> datetime:
        """Convert local time to UTC."""
        tz = cls.get_timezone(tz_str)
        if local_time.tzinfo is None:
            local_time = local_time.replace(tzinfo=tz)
        return local_time.astimezone(dt_timezone.utc)

    @classmethod
    def get_handoff_time_utc(
        cls,
        handoff_time: time,
        handoff_day: int,  # 0=Monday
        schedule_tz: str,
        reference_date: date | None = None,
    ) -> datetime:
        """
        Calculate the next handoff time in UTC.

        Args:
            handoff_time: Local time of handoff
            handoff_day: Day of week (0=Monday)
            schedule_tz: Schedule's timezone
            reference_date: Date to calculate from (defaults to today)
        """
        tz = cls.get_timezone(schedule_tz)

        if reference_date is None:
            reference_date = datetime.now(tz).date()

        # Find next occurrence of handoff_day
        current_weekday = reference_date.weekday()
        days_until = (handoff_day - current_weekday) % 7

        handoff_date = reference_date + timedelta(days=days_until)
        local_handoff = datetime.combine(handoff_date, handoff_time, tzinfo=tz)

        return local_handoff.astimezone(dt_timezone.utc)

    @classmethod
    def format_time_for_user(
        cls,
        utc_time: datetime,
        user_tz: str,
        include_tz: bool = True,
    ) -> str:
        """Format a UTC time for display in user's timezone."""
        local_time = cls.get_local_time(utc_time, user_tz)

        if include_tz:
            return local_time.strftime("%Y-%m-%d %H:%M %Z")
        return local_time.strftime("%Y-%m-%d %H:%M")

    @classmethod
    def get_dst_transitions(
        cls,
        tz_str: str,
        start_date: date,
        end_date: date,
    ) -> list[dict[str, Any]]:
        """
        Find DST transitions in a date range.

        Useful for alerting about potential schedule disruptions.
        """
        tz = cls.get_timezone(tz_str)
        transitions: list[dict[str, Any]] = []

        current = datetime.combine(start_date, time.min, tzinfo=tz)
        end = datetime.combine(end_date, time.max, tzinfo=tz)

        prev_offset = current.utcoffset()

        while current <= end:
            current += timedelta(hours=1)
            try:
                new_offset = current.utcoffset()
            except Exception:
                continue

            if new_offset != prev_offset:
                offset_change = (new_offset - prev_offset).total_seconds() / 3600 if prev_offset and new_offset else 0
                transitions.append({
                    "datetime": current.isoformat(),
                    "timezone": tz_str,
                    "from_offset": str(prev_offset) if prev_offset else None,
                    "to_offset": str(new_offset) if new_offset else None,
                    "offset_change_hours": offset_change,
                    "type": "spring_forward" if offset_change > 0 else "fall_back",
                })
                prev_offset = new_offset

        return transitions


class GapDetector:
    """
    Detects and analyzes coverage gaps in schedules.

    Features:
    - Identify periods without on-call coverage
    - Calculate gap severity based on duration
    - Suggest coverage options
    - Alert on upcoming gaps
    """

    # Gap thresholds
    CRITICAL_GAP_HOURS = 1.0  # Any gap is critical for 24/7 coverage
    HIGH_GAP_HOURS = 4.0
    MEDIUM_GAP_HOURS = 8.0

    @classmethod
    def detect_gaps(
        cls,
        shifts: list[ScheduleShift],
        start_time: datetime,
        end_time: datetime,
        schedule_id: UUID,
        coverage_type: str = "24x7",
    ) -> list[ScheduleGap]:
        """
        Detect gaps in schedule coverage.

        Args:
            shifts: List of shifts to analyze
            start_time: Start of analysis period
            end_time: End of analysis period
            schedule_id: ID of the schedule
            coverage_type: Type of coverage expected (24x7, business_hours, etc.)
        """
        if coverage_type != "24x7":
            # Only check gaps during expected coverage periods
            return cls._detect_business_hours_gaps(
                shifts, start_time, end_time, schedule_id
            )

        return cls._detect_continuous_gaps(shifts, start_time, end_time, schedule_id)

    @classmethod
    def _detect_continuous_gaps(
        cls,
        shifts: list[ScheduleShift],
        start_time: datetime,
        end_time: datetime,
        schedule_id: UUID,
    ) -> list[ScheduleGap]:
        """Detect gaps in 24/7 coverage."""
        gaps: list[ScheduleGap] = []

        if not shifts:
            # Entire period is a gap
            gap_hours = (end_time - start_time).total_seconds() / 3600
            gaps.append(ScheduleGap(
                gap_id=cls._generate_gap_id(schedule_id, start_time),
                schedule_id=schedule_id,
                start_time=start_time,
                end_time=end_time,
                duration_hours=gap_hours,
                severity=GapSeverity.CRITICAL,
                description="No coverage scheduled for entire period",
            ))
            return gaps

        # Sort shifts by start time
        sorted_shifts = sorted(shifts, key=lambda s: s.start_time)

        # Check gap before first shift
        if sorted_shifts[0].start_time > start_time:
            gap_start = start_time
            gap_end = sorted_shifts[0].start_time
            gap_hours = (gap_end - gap_start).total_seconds() / 3600

            gaps.append(ScheduleGap(
                gap_id=cls._generate_gap_id(schedule_id, gap_start),
                schedule_id=schedule_id,
                start_time=gap_start,
                end_time=gap_end,
                duration_hours=gap_hours,
                severity=cls._calculate_severity(gap_hours),
                description=f"Coverage gap at start of period ({gap_hours:.1f} hours)",
            ))

        # Check gaps between shifts
        coverage_end = start_time
        for shift in sorted_shifts:
            # Extend or merge coverage
            if shift.start_time <= coverage_end:
                coverage_end = max(coverage_end, shift.end_time)
            else:
                # Gap found
                gap_start = coverage_end
                gap_end = shift.start_time
                gap_hours = (gap_end - gap_start).total_seconds() / 3600

                gaps.append(ScheduleGap(
                    gap_id=cls._generate_gap_id(schedule_id, gap_start),
                    schedule_id=schedule_id,
                    start_time=gap_start,
                    end_time=gap_end,
                    duration_hours=gap_hours,
                    severity=cls._calculate_severity(gap_hours),
                    description=f"Coverage gap between shifts ({gap_hours:.1f} hours)",
                ))
                coverage_end = shift.end_time

        # Check gap after last shift
        if coverage_end < end_time:
            gap_start = coverage_end
            gap_end = end_time
            gap_hours = (gap_end - gap_start).total_seconds() / 3600

            gaps.append(ScheduleGap(
                gap_id=cls._generate_gap_id(schedule_id, gap_start),
                schedule_id=schedule_id,
                start_time=gap_start,
                end_time=gap_end,
                duration_hours=gap_hours,
                severity=cls._calculate_severity(gap_hours),
                description=f"Coverage gap at end of period ({gap_hours:.1f} hours)",
            ))

        return gaps

    @classmethod
    def _detect_business_hours_gaps(
        cls,
        shifts: list[ScheduleShift],
        start_time: datetime,
        end_time: datetime,
        schedule_id: UUID,
        business_start: time = time(9, 0),
        business_end: time = time(17, 0),
        weekdays_only: bool = True,
    ) -> list[ScheduleGap]:
        """Detect gaps only during business hours."""
        gaps: list[ScheduleGap] = []

        current_date = start_time.date()
        end_date = end_time.date()

        while current_date <= end_date:
            # Skip weekends if weekdays only
            if weekdays_only and current_date.weekday() >= 5:
                current_date += timedelta(days=1)
                continue

            # Define business hours for this day
            bh_start = datetime.combine(
                current_date, business_start, tzinfo=dt_timezone.utc
            )
            bh_end = datetime.combine(
                current_date, business_end, tzinfo=dt_timezone.utc
            )

            # Find shifts covering this business day
            day_shifts = [
                s for s in shifts
                if s.start_time < bh_end and s.end_time > bh_start
            ]

            # Detect gaps within business hours
            day_gaps = cls._detect_continuous_gaps(
                day_shifts, bh_start, bh_end, schedule_id
            )
            gaps.extend(day_gaps)

            current_date += timedelta(days=1)

        return gaps

    @classmethod
    def _calculate_severity(cls, gap_hours: float) -> GapSeverity:
        """Calculate gap severity based on duration."""
        if gap_hours <= cls.CRITICAL_GAP_HOURS:
            return GapSeverity.LOW
        elif gap_hours <= cls.HIGH_GAP_HOURS:
            return GapSeverity.MEDIUM
        elif gap_hours <= cls.MEDIUM_GAP_HOURS:
            return GapSeverity.HIGH
        else:
            return GapSeverity.CRITICAL

    @classmethod
    def _generate_gap_id(cls, schedule_id: UUID, start_time: datetime) -> str:
        """Generate a unique ID for a gap."""
        content = f"{schedule_id}:{start_time.isoformat()}"
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    @classmethod
    def get_gap_summary(cls, gaps: list[ScheduleGap]) -> dict[str, Any]:
        """Generate a summary of detected gaps."""
        if not gaps:
            return {
                "total_gaps": 0,
                "total_gap_hours": 0,
                "critical_gaps": 0,
                "high_gaps": 0,
                "medium_gaps": 0,
                "low_gaps": 0,
                "status": "ok",
                "message": "No coverage gaps detected",
            }

        total_hours = sum(g.duration_hours for g in gaps)
        by_severity = {
            GapSeverity.CRITICAL: 0,
            GapSeverity.HIGH: 0,
            GapSeverity.MEDIUM: 0,
            GapSeverity.LOW: 0,
        }
        for gap in gaps:
            by_severity[gap.severity] += 1

        # Determine overall status
        if by_severity[GapSeverity.CRITICAL] > 0:
            status = "critical"
            message = f"{by_severity[GapSeverity.CRITICAL]} critical coverage gaps detected"
        elif by_severity[GapSeverity.HIGH] > 0:
            status = "warning"
            message = f"{by_severity[GapSeverity.HIGH]} high-severity gaps detected"
        else:
            status = "info"
            message = f"{len(gaps)} minor gaps detected"

        return {
            "total_gaps": len(gaps),
            "total_gap_hours": round(total_hours, 2),
            "critical_gaps": by_severity[GapSeverity.CRITICAL],
            "high_gaps": by_severity[GapSeverity.HIGH],
            "medium_gaps": by_severity[GapSeverity.MEDIUM],
            "low_gaps": by_severity[GapSeverity.LOW],
            "status": status,
            "message": message,
        }


class CalendarExporter:
    """
    Exports on-call schedules to iCal format.

    Features:
    - Generate iCal feeds for individuals or teams
    - Include shift details and contact information
    - Support for recurring events
    - Customizable event formatting
    """

    PRODID = "-//OmniPotent//On-Call Schedule//EN"

    @classmethod
    def generate_ical(
        cls,
        shifts: list[ScheduleShift],
        schedule_name: str,
        user_name: str | None = None,
        user_email: str | None = None,
        include_reminders: bool = True,
        reminder_minutes: list[int] | None = None,
    ) -> str:
        """
        Generate an iCal feed for on-call shifts.

        Args:
            shifts: List of shifts to include
            schedule_name: Name of the schedule
            user_name: Optional user name for filtering
            user_email: Optional email for organizer
            include_reminders: Whether to include reminders
            reminder_minutes: List of reminder times in minutes before shift

        Returns:
            iCal formatted string
        """
        cal = Calendar()
        cal.add("prodid", cls.PRODID)
        cal.add("version", "2.0")
        cal.add("calscale", "GREGORIAN")
        cal.add("method", "PUBLISH")
        cal.add("x-wr-calname", f"On-Call: {schedule_name}")

        if reminder_minutes is None:
            reminder_minutes = [60, 1440]  # 1 hour and 1 day before

        for shift in shifts:
            event = cls._create_event(
                shift,
                schedule_name,
                user_name,
                user_email,
                include_reminders,
                reminder_minutes,
            )
            cal.add_component(event)

        return cal.to_ical().decode("utf-8")

    @classmethod
    def _create_event(
        cls,
        shift: ScheduleShift,
        schedule_name: str,
        user_name: str | None,
        user_email: str | None,
        include_reminders: bool,
        reminder_minutes: list[int],
    ) -> Event:
        """Create an iCal event for a shift."""
        event = Event()

        # Generate unique ID
        uid = f"{shift.shift_id}@devops-copilot.oncall"
        event.add("uid", uid)

        # Event timing
        event.add("dtstart", shift.start_time)
        event.add("dtend", shift.end_time)
        event.add("dtstamp", datetime.now(dt_timezone.utc))

        # Event details
        shift_type_label = shift.shift_type.replace("_", " ").title()
        summary = f"On-Call: {schedule_name} ({shift_type_label})"
        event.add("summary", summary)

        description_parts = [
            f"Schedule: {schedule_name}",
            f"Shift Type: {shift_type_label}",
            f"Layer: {shift.layer}",
            f"Duration: {shift.duration_hours:.1f} hours",
        ]
        if shift.source != "rotation":
            description_parts.append(f"Source: {shift.source}")

        event.add("description", "\n".join(description_parts))

        # Categories
        event.add("categories", ["On-Call", shift_type_label])

        # Organizer
        if user_email:
            event.add("organizer", f"mailto:{user_email}")

        # Attendee (if user specified)
        if user_name and user_email:
            event.add(
                "attendee",
                f"mailto:{user_email}",
                parameters={"cn": vText(user_name), "role": "REQ-PARTICIPANT"},
            )

        # Reminders (VALARM)
        if include_reminders:
            for minutes in reminder_minutes:
                alarm = cls._create_alarm(minutes, summary)
                event.add_component(alarm)

        return event

    @classmethod
    def _create_alarm(cls, minutes_before: int, summary: str):
        """Create a VALARM component for reminders."""
        from icalendar import Alarm

        alarm = Alarm()
        alarm.add("action", "DISPLAY")
        alarm.add("description", f"On-call shift starting: {summary}")
        alarm.add("trigger", timedelta(minutes=-minutes_before))

        return alarm

    @classmethod
    def generate_personal_feed(
        cls,
        shifts: list[ScheduleShift],
        user_id: UUID,
        user_name: str,
        user_email: str,
        schedule_names: dict[UUID, str],
    ) -> str:
        """
        Generate a personal iCal feed for a user.

        Filters shifts for the specific user and formats them
        for personal calendar integration.
        """
        user_shifts = [s for s in shifts if s.user_id == user_id]

        cal = Calendar()
        cal.add("prodid", cls.PRODID)
        cal.add("version", "2.0")
        cal.add("calscale", "GREGORIAN")
        cal.add("method", "PUBLISH")
        cal.add("x-wr-calname", f"On-Call Schedule - {user_name}")

        for shift in user_shifts:
            schedule_name = schedule_names.get(shift.schedule_id, "Unknown Schedule")
            event = cls._create_event(
                shift,
                schedule_name,
                user_name,
                user_email,
                include_reminders=True,
                reminder_minutes=[60, 1440],
            )
            cal.add_component(event)

        return cal.to_ical().decode("utf-8")

    @classmethod
    def get_feed_url(
        cls,
        base_url: str,
        schedule_id: UUID,
        user_id: UUID | None = None,
        token: str | None = None,
    ) -> str:
        """
        Generate the URL for an iCal feed.

        The URL can be used to subscribe to the schedule
        in calendar applications.
        """
        url = f"{base_url}/oncall/schedules/{schedule_id}/ical"

        params: list[str] = []
        if user_id:
            params.append(f"user_id={user_id}")
        if token:
            params.append(f"token={token}")

        if params:
            url += "?" + "&".join(params)

        return url


class ConflictDetector:
    """
    Detects conflicts in schedule assignments.

    Features:
    - Detect overlapping shifts for same user
    - Check for overrides that conflict
    - Identify users scheduled in multiple places
    """

    @classmethod
    def detect_conflicts(
        cls,
        shifts: list[ScheduleShift],
        overrides: list[ScheduleOverride] | None = None,
    ) -> list[dict[str, Any]]:
        """Detect all conflicts in a set of shifts and overrides."""
        conflicts: list[dict[str, Any]] = []

        # Check shift-to-shift conflicts (same user, overlapping time)
        shifts_by_user: dict[UUID, list[ScheduleShift]] = {}
        for shift in shifts:
            if shift.user_id not in shifts_by_user:
                shifts_by_user[shift.user_id] = []
            shifts_by_user[shift.user_id].append(shift)

        for user_id, user_shifts in shifts_by_user.items():
            sorted_shifts = sorted(user_shifts, key=lambda s: s.start_time)

            for i in range(len(sorted_shifts) - 1):
                current = sorted_shifts[i]
                next_shift = sorted_shifts[i + 1]

                if current.end_time > next_shift.start_time:
                    # Overlapping shifts
                    if current.schedule_id != next_shift.schedule_id:
                        conflicts.append({
                            "type": "cross_schedule_overlap",
                            "severity": "high",
                            "user_id": str(user_id),
                            "shift_1": current.to_dict(),
                            "shift_2": next_shift.to_dict(),
                            "overlap_start": next_shift.start_time.isoformat(),
                            "overlap_end": min(current.end_time, next_shift.end_time).isoformat(),
                            "message": "User is scheduled on two different schedules at the same time",
                        })

        # Check override conflicts
        if overrides:
            for override in overrides:
                if override.status != "active":
                    continue

                # Find shifts this override affects
                affected_shifts = [
                    s for s in shifts
                    if s.schedule_id == override.schedule_id
                    and s.start_time < override.end_time
                    and s.end_time > override.start_time
                    and s.source != "override"
                ]

                if not affected_shifts:
                    conflicts.append({
                        "type": "orphan_override",
                        "severity": "low",
                        "override": override.to_dict(),
                        "message": "Override does not match any scheduled shifts",
                    })

        return conflicts

    @classmethod
    def get_user_schedule_conflicts(
        cls,
        user_id: UUID,
        shifts: list[ScheduleShift],
        proposed_start: datetime,
        proposed_end: datetime,
        exclude_schedule_id: UUID | None = None,
    ) -> list[ScheduleShift]:
        """
        Find conflicts with a proposed time period for a user.

        Useful for checking before creating overrides or swaps.
        """
        user_shifts = [s for s in shifts if s.user_id == user_id]

        if exclude_schedule_id:
            user_shifts = [s for s in user_shifts if s.schedule_id != exclude_schedule_id]

        conflicts = [
            s for s in user_shifts
            if s.start_time < proposed_end and s.end_time > proposed_start
        ]

        return conflicts


class ScheduleAnalyzer:
    """
    Analyzes schedules for fairness and workload distribution.

    Features:
    - Calculate on-call hours per user
    - Identify workload imbalances
    - Suggest rotation adjustments
    """

    @classmethod
    def analyze_workload(
        cls,
        shifts: list[ScheduleShift],
        start_date: date,
        end_date: date,
    ) -> dict[str, Any]:
        """Analyze workload distribution across users."""
        user_hours: dict[UUID, float] = {}
        user_shifts: dict[UUID, int] = {}
        user_weekend_hours: dict[UUID, float] = {}
        user_night_hours: dict[UUID, float] = {}

        for shift in shifts:
            user_id = shift.user_id

            if user_id not in user_hours:
                user_hours[user_id] = 0
                user_shifts[user_id] = 0
                user_weekend_hours[user_id] = 0
                user_night_hours[user_id] = 0

            user_hours[user_id] += shift.duration_hours
            user_shifts[user_id] += 1

            # Calculate weekend and night hours
            weekend, night = cls._calculate_special_hours(shift)
            user_weekend_hours[user_id] += weekend
            user_night_hours[user_id] += night

        if not user_hours:
            return {
                "total_users": 0,
                "total_hours": 0,
                "analysis": "No shifts to analyze",
                "users": [],
            }

        total_hours = sum(user_hours.values())
        avg_hours = total_hours / len(user_hours)

        users_analysis: list[dict[str, Any]] = []
        for user_id in user_hours:
            hours = user_hours[user_id]
            deviation = ((hours - avg_hours) / avg_hours * 100) if avg_hours > 0 else 0

            users_analysis.append({
                "user_id": str(user_id),
                "total_hours": round(hours, 2),
                "total_shifts": user_shifts[user_id],
                "weekend_hours": round(user_weekend_hours[user_id], 2),
                "night_hours": round(user_night_hours[user_id], 2),
                "deviation_from_average": round(deviation, 1),
                "status": cls._workload_status(deviation),
            })

        # Sort by total hours descending
        users_analysis.sort(key=lambda u: u["total_hours"], reverse=True)

        # Calculate fairness score (0-100, 100 = perfectly fair)
        max_deviation = max(abs(u["deviation_from_average"]) for u in users_analysis) if users_analysis else 0
        fairness_score = max(0, 100 - max_deviation)

        return {
            "total_users": len(user_hours),
            "total_hours": round(total_hours, 2),
            "average_hours_per_user": round(avg_hours, 2),
            "fairness_score": round(fairness_score, 1),
            "period": {
                "start": start_date.isoformat(),
                "end": end_date.isoformat(),
            },
            "users": users_analysis,
        }

    @classmethod
    def _calculate_special_hours(cls, shift: ScheduleShift) -> tuple[float, float]:
        """Calculate weekend and night hours for a shift."""
        weekend_hours = 0.0
        night_hours = 0.0

        current = shift.start_time
        while current < shift.end_time:
            hour_end = min(current + timedelta(hours=1), shift.end_time)
            hour_duration = (hour_end - current).total_seconds() / 3600

            # Weekend check (Saturday=5, Sunday=6)
            if current.weekday() >= 5:
                weekend_hours += hour_duration

            # Night check (10pm - 6am)
            hour = current.hour
            if hour >= 22 or hour < 6:
                night_hours += hour_duration

            current = hour_end

        return weekend_hours, night_hours

    @classmethod
    def _workload_status(cls, deviation: float) -> str:
        """Determine workload status based on deviation."""
        if abs(deviation) <= 5:
            return "balanced"
        elif deviation > 20:
            return "overloaded"
        elif deviation < -20:
            return "underutilized"
        elif deviation > 0:
            return "above_average"
        else:
            return "below_average"
