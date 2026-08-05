"""Rotation Templates for On-Call Scheduling.

Provides PagerDuty-level rotation patterns:
- Weekly rotations with configurable handoff
- Bi-weekly rotations
- Follow-the-sun (timezone-based handoffs)
- Custom patterns with restrictions
- Partial-day rotations (business hours only)
- Weekend-specific rotations
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, date, time, timedelta, timezone as dt_timezone
from enum import Enum
from typing import Any, Iterator
from uuid import UUID

from zoneinfo import ZoneInfo

log = logging.getLogger("omnipotent.oncall.rotations")


class RotationPatternType(Enum):
    """Supported rotation pattern types."""

    WEEKLY = "weekly"
    BIWEEKLY = "biweekly"
    DAILY = "daily"
    CUSTOM = "custom"
    FOLLOW_THE_SUN = "follow_the_sun"
    BUSINESS_HOURS = "business_hours"
    WEEKEND = "weekend"
    INVERSE = "inverse"  # Cover when another rotation is not active


class HandoffDay(Enum):
    """Days of the week for handoff."""

    MONDAY = 0
    TUESDAY = 1
    WEDNESDAY = 2
    THURSDAY = 3
    FRIDAY = 4
    SATURDAY = 5
    SUNDAY = 6

    @classmethod
    def from_string(cls, day: str) -> "HandoffDay":
        """Convert string to HandoffDay."""
        return cls[day.upper()]


@dataclass
class TimeRestriction:
    """
    Time-based restriction for when a rotation is active.

    Supports:
    - Specific days of week
    - Time ranges (e.g., 9am-5pm)
    - Date ranges (effective from/until)
    """

    days_of_week: list[int] | None = None  # 0=Monday, 6=Sunday; None=all days
    start_time: time | None = None  # None = midnight
    end_time: time | None = None  # None = midnight next day
    effective_from: date | None = None
    effective_until: date | None = None

    def is_active_at(self, dt: datetime, tz: ZoneInfo) -> bool:
        """Check if restriction allows activity at given time."""
        # Convert to local time
        local_dt = dt.astimezone(tz)
        local_date = local_dt.date()
        local_time = local_dt.time()

        # Check date range
        if self.effective_from and local_date < self.effective_from:
            return False
        if self.effective_until and local_date > self.effective_until:
            return False

        # Check day of week
        if self.days_of_week is not None:
            if local_dt.weekday() not in self.days_of_week:
                return False

        # Check time range
        if self.start_time and self.end_time:
            if self.start_time <= self.end_time:
                # Normal range (e.g., 9am-5pm)
                if not (self.start_time <= local_time < self.end_time):
                    return False
            else:
                # Overnight range (e.g., 10pm-6am)
                if not (local_time >= self.start_time or local_time < self.end_time):
                    return False

        return True


@dataclass
class RotationLayer:
    """
    A layer within a rotation pattern.

    PagerDuty-style layers allow multiple overlapping rotations
    where higher priority layers override lower ones.
    """

    layer_id: str
    name: str
    priority: int  # 1 = highest priority
    participants: list[UUID]
    pattern_type: RotationPatternType
    handoff_time: time = field(default_factory=lambda: time(9, 0))
    handoff_day: HandoffDay = HandoffDay.MONDAY
    rotation_length_days: int = 7
    timezone: str = "UTC"
    restrictions: list[TimeRestriction] = field(default_factory=list)

    def get_layer_tz(self) -> ZoneInfo:
        """Get layer timezone as ZoneInfo."""
        return ZoneInfo(self.timezone)


@dataclass
class FollowTheSunRegion:
    """
    A region in a follow-the-sun rotation.

    Each region has its own participants and active hours.
    Coverage seamlessly transitions between regions.
    """

    region_id: str
    name: str
    timezone: str
    participants: list[UUID]
    active_hours_start: time
    active_hours_end: time

    def is_active_at(self, dt: datetime) -> bool:
        """Check if this region is active at the given UTC time."""
        tz = ZoneInfo(self.timezone)
        local_dt = dt.astimezone(tz)
        local_time = local_dt.time()

        if self.active_hours_start <= self.active_hours_end:
            return self.active_hours_start <= local_time < self.active_hours_end
        else:
            # Overnight coverage
            return local_time >= self.active_hours_start or local_time < self.active_hours_end

    def get_participant_for_date(self, target_date: date) -> UUID:
        """Get the participant on-call for a specific date."""
        if not self.participants:
            raise ValueError(f"No participants in region {self.name}")

        # Simple daily rotation within region
        day_of_year = target_date.timetuple().tm_yday
        idx = day_of_year % len(self.participants)
        return self.participants[idx]


@dataclass
class RotationTemplate:
    """
    A complete rotation template that can generate shift assignments.

    Templates support:
    - Multiple layers with different patterns
    - Follow-the-sun configurations
    - Business hours / weekend coverage
    - Custom restrictions
    """

    template_id: str
    name: str
    description: str
    pattern_type: RotationPatternType
    timezone: str = "UTC"

    # For simple rotations
    participants: list[UUID] = field(default_factory=list)
    handoff_time: time = field(default_factory=lambda: time(9, 0))
    handoff_day: HandoffDay = HandoffDay.MONDAY
    rotation_length_days: int = 7

    # For layered rotations
    layers: list[RotationLayer] = field(default_factory=list)

    # For follow-the-sun
    regions: list[FollowTheSunRegion] = field(default_factory=list)

    # Restrictions
    restrictions: list[TimeRestriction] = field(default_factory=list)

    # Metadata
    created_at: datetime = field(default_factory=lambda: datetime.now(dt_timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(dt_timezone.utc))

    def get_oncall_at(self, at_time: datetime) -> UUID | None:
        """
        Get the on-call user at a specific time.

        For layered rotations, returns the user from the highest-priority
        active layer. For follow-the-sun, returns the user from the
        currently active region.
        """
        if self.pattern_type == RotationPatternType.FOLLOW_THE_SUN:
            return self._get_follow_the_sun_oncall(at_time)

        if self.layers:
            return self._get_layered_oncall(at_time)

        return self._get_simple_oncall(at_time)

    def _get_simple_oncall(self, at_time: datetime) -> UUID | None:
        """Get on-call for simple rotation patterns."""
        if not self.participants:
            return None

        # Check restrictions
        tz = ZoneInfo(self.timezone)
        for restriction in self.restrictions:
            if not restriction.is_active_at(at_time, tz):
                return None

        # Calculate which rotation period we're in
        rotation_length = timedelta(days=self.rotation_length_days)

        # Find the epoch (first rotation start)
        # Default to start of current year, aligned to handoff day
        epoch_year = at_time.year
        epoch_date = date(epoch_year, 1, 1)

        # Adjust to first occurrence of handoff day
        while epoch_date.weekday() != self.handoff_day.value:
            epoch_date += timedelta(days=1)

        epoch = datetime.combine(
            epoch_date,
            self.handoff_time,
            tzinfo=ZoneInfo(self.timezone)
        )

        # Handle case where at_time is before epoch
        while epoch > at_time:
            epoch -= timedelta(days=365)
            # Realign to handoff day
            epoch_date = epoch.date()
            while epoch_date.weekday() != self.handoff_day.value:
                epoch_date += timedelta(days=1)
            epoch = datetime.combine(epoch_date, self.handoff_time, tzinfo=ZoneInfo(self.timezone))

        elapsed = at_time - epoch
        rotations_elapsed = int(elapsed.total_seconds() / rotation_length.total_seconds())
        participant_idx = rotations_elapsed % len(self.participants)

        return self.participants[participant_idx]

    def _get_layered_oncall(self, at_time: datetime) -> UUID | None:
        """Get on-call considering multiple layers (highest priority wins)."""
        # Sort layers by priority (1 = highest)
        sorted_layers = sorted(self.layers, key=lambda layer: layer.priority)

        for layer in sorted_layers:
            tz = layer.get_layer_tz()

            # Check if layer is active (restrictions)
            is_active = True
            for restriction in layer.restrictions:
                if not restriction.is_active_at(at_time, tz):
                    is_active = False
                    break

            if not is_active:
                continue

            # Calculate on-call for this layer
            if not layer.participants:
                continue

            rotation_length = timedelta(days=layer.rotation_length_days)
            epoch_date = date(at_time.year, 1, 1)
            while epoch_date.weekday() != layer.handoff_day.value:
                epoch_date += timedelta(days=1)

            epoch = datetime.combine(epoch_date, layer.handoff_time, tzinfo=tz)

            # Adjust epoch if needed
            while epoch > at_time:
                epoch -= timedelta(days=365)
                epoch_date = epoch.date()
                while epoch_date.weekday() != layer.handoff_day.value:
                    epoch_date += timedelta(days=1)
                epoch = datetime.combine(epoch_date, layer.handoff_time, tzinfo=tz)

            elapsed = at_time - epoch
            rotations_elapsed = int(elapsed.total_seconds() / rotation_length.total_seconds())
            participant_idx = rotations_elapsed % len(layer.participants)

            return layer.participants[participant_idx]

        return None

    def _get_follow_the_sun_oncall(self, at_time: datetime) -> UUID | None:
        """Get on-call for follow-the-sun rotation."""
        if not self.regions:
            return None

        for region in self.regions:
            if region.is_active_at(at_time):
                return region.get_participant_for_date(at_time.date())

        # If no region is active, return from the region whose
        # active hours are closest to starting
        return self.regions[0].get_participant_for_date(at_time.date())

    def generate_shifts(
        self,
        start_date: date,
        end_date: date,
    ) -> Iterator[dict[str, Any]]:
        """
        Generate shift assignments for a date range.

        Yields dicts with:
        - user_id: UUID
        - start_time: datetime
        - end_time: datetime
        - shift_type: str
        - layer: str | None
        - region: str | None
        """
        if self.pattern_type == RotationPatternType.FOLLOW_THE_SUN:
            yield from self._generate_follow_the_sun_shifts(start_date, end_date)
        elif self.layers:
            yield from self._generate_layered_shifts(start_date, end_date)
        else:
            yield from self._generate_simple_shifts(start_date, end_date)

    def _generate_simple_shifts(
        self,
        start_date: date,
        end_date: date,
    ) -> Iterator[dict[str, Any]]:
        """Generate shifts for simple rotation patterns."""
        if not self.participants:
            return

        tz = ZoneInfo(self.timezone)
        rotation_length = timedelta(days=self.rotation_length_days)

        # Find start of rotation period containing start_date
        current = datetime.combine(start_date, self.handoff_time, tzinfo=tz)

        # Align to handoff day
        days_since_handoff = (current.weekday() - self.handoff_day.value) % 7
        rotation_start = current - timedelta(days=days_since_handoff)

        # Calculate participant index at rotation_start
        epoch_date = date(rotation_start.year, 1, 1)
        while epoch_date.weekday() != self.handoff_day.value:
            epoch_date += timedelta(days=1)
        epoch = datetime.combine(epoch_date, self.handoff_time, tzinfo=tz)

        while epoch > rotation_start:
            epoch -= timedelta(days=365)

        elapsed = rotation_start - epoch
        rotations_since_epoch = int(elapsed.total_seconds() / rotation_length.total_seconds())
        participant_idx = rotations_since_epoch % len(self.participants)

        current = rotation_start
        end_dt = datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=tz)

        while current < end_dt:
            shift_end = current + rotation_length

            yield {
                "user_id": self.participants[participant_idx],
                "start_time": current,
                "end_time": shift_end,
                "shift_type": "primary",
                "layer": None,
                "region": None,
            }

            participant_idx = (participant_idx + 1) % len(self.participants)
            current = shift_end

    def _generate_layered_shifts(
        self,
        start_date: date,
        end_date: date,
    ) -> Iterator[dict[str, Any]]:
        """Generate shifts for layered rotations."""
        for layer in sorted(self.layers, key=lambda lay: lay.priority):
            if not layer.participants:
                continue

            tz = layer.get_layer_tz()
            rotation_length = timedelta(days=layer.rotation_length_days)

            current = datetime.combine(start_date, layer.handoff_time, tzinfo=tz)
            days_since_handoff = (current.weekday() - layer.handoff_day.value) % 7
            rotation_start = current - timedelta(days=days_since_handoff)

            epoch_date = date(rotation_start.year, 1, 1)
            while epoch_date.weekday() != layer.handoff_day.value:
                epoch_date += timedelta(days=1)
            epoch = datetime.combine(epoch_date, layer.handoff_time, tzinfo=tz)

            while epoch > rotation_start:
                epoch -= timedelta(days=365)

            elapsed = rotation_start - epoch
            rotations_since_epoch = int(elapsed.total_seconds() / rotation_length.total_seconds())
            participant_idx = rotations_since_epoch % len(layer.participants)

            current = rotation_start
            end_dt = datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=tz)

            while current < end_dt:
                shift_end = current + rotation_length

                # Check restrictions
                is_active = True
                for restriction in layer.restrictions:
                    if not restriction.is_active_at(current, tz):
                        is_active = False
                        break

                if is_active:
                    yield {
                        "user_id": layer.participants[participant_idx],
                        "start_time": current,
                        "end_time": shift_end,
                        "shift_type": "primary",
                        "layer": layer.name,
                        "region": None,
                    }

                participant_idx = (participant_idx + 1) % len(layer.participants)
                current = shift_end

    def _generate_follow_the_sun_shifts(
        self,
        start_date: date,
        end_date: date,
    ) -> Iterator[dict[str, Any]]:
        """Generate shifts for follow-the-sun rotations."""
        current_date = start_date

        while current_date <= end_date:
            for region in self.regions:
                tz = ZoneInfo(region.timezone)

                # Create shift for this region's active hours
                local_start = datetime.combine(
                    current_date,
                    region.active_hours_start,
                    tzinfo=tz
                )

                if region.active_hours_start <= region.active_hours_end:
                    local_end = datetime.combine(
                        current_date,
                        region.active_hours_end,
                        tzinfo=tz
                    )
                else:
                    # Overnight shift
                    local_end = datetime.combine(
                        current_date + timedelta(days=1),
                        region.active_hours_end,
                        tzinfo=tz
                    )

                yield {
                    "user_id": region.get_participant_for_date(current_date),
                    "start_time": local_start.astimezone(dt_timezone.utc),
                    "end_time": local_end.astimezone(dt_timezone.utc),
                    "shift_type": "primary",
                    "layer": None,
                    "region": region.name,
                }

            current_date += timedelta(days=1)


# ============================================================================
# TEMPLATE FACTORY FUNCTIONS
# ============================================================================

def create_weekly_rotation(
    name: str,
    participants: list[UUID],
    timezone: str = "UTC",
    handoff_day: str = "monday",
    handoff_time_str: str = "09:00",
) -> RotationTemplate:
    """Create a simple weekly rotation template."""
    hour, minute = map(int, handoff_time_str.split(":"))

    return RotationTemplate(
        template_id=f"template_{name.lower().replace(' ', '_')}",
        name=name,
        description=f"Weekly rotation with handoff on {handoff_day} at {handoff_time_str}",
        pattern_type=RotationPatternType.WEEKLY,
        timezone=timezone,
        participants=participants,
        handoff_time=time(hour, minute),
        handoff_day=HandoffDay.from_string(handoff_day),
        rotation_length_days=7,
    )


def create_biweekly_rotation(
    name: str,
    participants: list[UUID],
    timezone: str = "UTC",
    handoff_day: str = "monday",
    handoff_time_str: str = "09:00",
) -> RotationTemplate:
    """Create a bi-weekly (every 2 weeks) rotation template."""
    hour, minute = map(int, handoff_time_str.split(":"))

    return RotationTemplate(
        template_id=f"template_{name.lower().replace(' ', '_')}",
        name=name,
        description=f"Bi-weekly rotation with handoff on {handoff_day} at {handoff_time_str}",
        pattern_type=RotationPatternType.BIWEEKLY,
        timezone=timezone,
        participants=participants,
        handoff_time=time(hour, minute),
        handoff_day=HandoffDay.from_string(handoff_day),
        rotation_length_days=14,
    )


def create_follow_the_sun_rotation(
    name: str,
    regions: list[dict[str, Any]],
) -> RotationTemplate:
    """
    Create a follow-the-sun rotation template.

    regions format:
    [
        {
            "name": "Americas",
            "timezone": "America/New_York",
            "participants": [user_id1, user_id2],
            "active_hours": {"start": "09:00", "end": "17:00"}
        },
        ...
    ]
    """
    fts_regions: list[FollowTheSunRegion] = []

    for idx, region in enumerate(regions):
        start_h, start_m = map(int, region["active_hours"]["start"].split(":"))
        end_h, end_m = map(int, region["active_hours"]["end"].split(":"))

        fts_regions.append(FollowTheSunRegion(
            region_id=f"region_{idx}",
            name=region["name"],
            timezone=region["timezone"],
            participants=region["participants"],
            active_hours_start=time(start_h, start_m),
            active_hours_end=time(end_h, end_m),
        ))

    return RotationTemplate(
        template_id=f"template_{name.lower().replace(' ', '_')}",
        name=name,
        description=f"Follow-the-sun rotation across {len(regions)} regions",
        pattern_type=RotationPatternType.FOLLOW_THE_SUN,
        timezone="UTC",  # FTS uses region timezones
        regions=fts_regions,
    )


def create_business_hours_rotation(
    name: str,
    participants: list[UUID],
    timezone: str = "UTC",
    start_time_str: str = "09:00",
    end_time_str: str = "17:00",
    weekdays_only: bool = True,
) -> RotationTemplate:
    """Create a rotation that only covers business hours."""
    start_h, start_m = map(int, start_time_str.split(":"))
    end_h, end_m = map(int, end_time_str.split(":"))

    days = [0, 1, 2, 3, 4] if weekdays_only else None  # Mon-Fri

    return RotationTemplate(
        template_id=f"template_{name.lower().replace(' ', '_')}",
        name=name,
        description=f"Business hours rotation ({start_time_str}-{end_time_str})",
        pattern_type=RotationPatternType.BUSINESS_HOURS,
        timezone=timezone,
        participants=participants,
        handoff_time=time(start_h, start_m),
        handoff_day=HandoffDay.MONDAY,
        rotation_length_days=7,
        restrictions=[
            TimeRestriction(
                days_of_week=days,
                start_time=time(start_h, start_m),
                end_time=time(end_h, end_m),
            )
        ],
    )


def create_weekend_rotation(
    name: str,
    participants: list[UUID],
    timezone: str = "UTC",
) -> RotationTemplate:
    """Create a rotation that only covers weekends."""
    return RotationTemplate(
        template_id=f"template_{name.lower().replace(' ', '_')}",
        name=name,
        description="Weekend-only rotation (Saturday-Sunday)",
        pattern_type=RotationPatternType.WEEKEND,
        timezone=timezone,
        participants=participants,
        handoff_time=time(0, 0),
        handoff_day=HandoffDay.SATURDAY,
        rotation_length_days=7,  # Rotate weekly but only active on weekends
        restrictions=[
            TimeRestriction(
                days_of_week=[5, 6],  # Saturday, Sunday
            )
        ],
    )


def create_custom_rotation(
    name: str,
    participants: list[UUID],
    rotation_length_days: int,
    timezone: str = "UTC",
    handoff_day: str = "monday",
    handoff_time_str: str = "09:00",
    restrictions: list[dict[str, Any]] | None = None,
) -> RotationTemplate:
    """
    Create a custom rotation with arbitrary length.

    restrictions format:
    [
        {
            "days_of_week": [0, 1, 2, 3, 4],  # Optional
            "start_time": "09:00",  # Optional
            "end_time": "17:00",  # Optional
            "effective_from": "2024-01-01",  # Optional
            "effective_until": "2024-12-31",  # Optional
        }
    ]
    """
    hour, minute = map(int, handoff_time_str.split(":"))

    parsed_restrictions: list[TimeRestriction] = []
    if restrictions:
        for r in restrictions:
            start_time = None
            end_time = None
            if "start_time" in r:
                sh, sm = map(int, r["start_time"].split(":"))
                start_time = time(sh, sm)
            if "end_time" in r:
                eh, em = map(int, r["end_time"].split(":"))
                end_time = time(eh, em)

            effective_from = None
            effective_until = None
            if "effective_from" in r:
                effective_from = date.fromisoformat(r["effective_from"])
            if "effective_until" in r:
                effective_until = date.fromisoformat(r["effective_until"])

            parsed_restrictions.append(TimeRestriction(
                days_of_week=r.get("days_of_week"),
                start_time=start_time,
                end_time=end_time,
                effective_from=effective_from,
                effective_until=effective_until,
            ))

    return RotationTemplate(
        template_id=f"template_{name.lower().replace(' ', '_')}",
        name=name,
        description=f"Custom {rotation_length_days}-day rotation",
        pattern_type=RotationPatternType.CUSTOM,
        timezone=timezone,
        participants=participants,
        handoff_time=time(hour, minute),
        handoff_day=HandoffDay.from_string(handoff_day),
        rotation_length_days=rotation_length_days,
        restrictions=parsed_restrictions,
    )


def create_layered_rotation(
    name: str,
    layers: list[dict[str, Any]],
    timezone: str = "UTC",
) -> RotationTemplate:
    """
    Create a multi-layer rotation (PagerDuty-style).

    layers format:
    [
        {
            "name": "Primary",
            "priority": 1,
            "participants": [user_id1, user_id2],
            "pattern_type": "weekly",
            "handoff_day": "monday",
            "handoff_time": "09:00",
            "restrictions": [...]  # Optional
        },
        {
            "name": "Weekend Override",
            "priority": 0,  # Higher priority than primary
            "participants": [user_id3],
            "pattern_type": "weekly",
            "restrictions": [{"days_of_week": [5, 6]}]
        }
    ]
    """
    parsed_layers: list[RotationLayer] = []

    for idx, layer in enumerate(layers):
        h, m = map(int, layer.get("handoff_time", "09:00").split(":"))

        restrictions: list[TimeRestriction] = []
        for r in layer.get("restrictions", []):
            start_time = None
            end_time = None
            if "start_time" in r:
                sh, sm = map(int, r["start_time"].split(":"))
                start_time = time(sh, sm)
            if "end_time" in r:
                eh, em = map(int, r["end_time"].split(":"))
                end_time = time(eh, em)

            restrictions.append(TimeRestriction(
                days_of_week=r.get("days_of_week"),
                start_time=start_time,
                end_time=end_time,
            ))

        pattern_type_str = layer.get("pattern_type", "weekly")
        pattern_type = RotationPatternType(pattern_type_str)

        length_days = {
            RotationPatternType.DAILY: 1,
            RotationPatternType.WEEKLY: 7,
            RotationPatternType.BIWEEKLY: 14,
        }.get(pattern_type, layer.get("rotation_length_days", 7))

        parsed_layers.append(RotationLayer(
            layer_id=f"layer_{idx}",
            name=layer["name"],
            priority=layer.get("priority", idx + 1),
            participants=layer["participants"],
            pattern_type=pattern_type,
            handoff_time=time(h, m),
            handoff_day=HandoffDay.from_string(layer.get("handoff_day", "monday")),
            rotation_length_days=length_days,
            timezone=layer.get("timezone", timezone),
            restrictions=restrictions,
        ))

    return RotationTemplate(
        template_id=f"template_{name.lower().replace(' ', '_')}",
        name=name,
        description=f"Layered rotation with {len(layers)} layers",
        pattern_type=RotationPatternType.CUSTOM,
        timezone=timezone,
        layers=parsed_layers,
    )
