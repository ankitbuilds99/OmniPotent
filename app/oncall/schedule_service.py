"""Advanced On-Call Schedule Service.

Enterprise-grade schedule management with:
- Daily/Weekly/Biweekly/Custom rotations
- Follow-the-sun scheduling
- Primary/Secondary/Shadow rotations
- Handoff time configuration
- Holiday-aware scheduling
- Time-off auto-routing
- Gap detection
"""

from __future__ import annotations

import logging
from datetime import datetime, date, timedelta, time, timezone
from typing import Any
from collections import defaultdict
from uuid import UUID

import pytz
from sqlalchemy import select, delete, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.oncall.models import (
    OncallTeam,
    OncallSchedule,
    OncallRotationParticipant,
    OncallShift,
    OncallOverride,
    OncallTimeOff,
    OncallHoliday,
    OncallHolidayCalendar,
)

log = logging.getLogger("devcopilot.oncall.schedule_service")


class ScheduleService:
    """Manages on-call schedules, rotations, and shift generation."""

    def __init__(self, db: AsyncSession):
        self.db = db

    # =========================================================================
    # SCHEDULE CRUD
    # =========================================================================

    async def create_schedule(
        self,
        team_id: UUID,
        name: str,
        timezone_str: str = "UTC",
        rotation_type: str = "weekly",
        handoff_time: str = "09:00",
        handoff_day: str = "monday",
        length_days: int = 7,
        coverage_type: str = "24x7",
        coverage_hours: dict[str, Any] | None = None,
        layer: int = 1,
        participants: list[UUID] | None = None,
    ) -> OncallSchedule:
        """Create a new on-call schedule."""
        schedule = OncallSchedule(
            team_id=team_id,
            name=name,
            timezone=timezone_str,
            schedule_type="rotation",
            rotation_config={
                "type": rotation_type,
                "handoff_time": handoff_time,
                "handoff_day": handoff_day,
                "length_days": length_days,
            },
            coverage_type=coverage_type,
            coverage_hours=coverage_hours,
            layer=layer,
        )

        self.db.add(schedule)
        await self.db.flush()

        if participants:
            for position, user_id in enumerate(participants):
                participant = OncallRotationParticipant(
                    schedule_id=schedule.id,
                    user_id=user_id,
                    position=position,
                )
                self.db.add(participant)

        await self.db.commit()
        await self.db.refresh(schedule)

        log.info("Created schedule %s for team %s", schedule.id, team_id)
        return schedule

    async def get_schedule(self, schedule_id: UUID) -> OncallSchedule | None:
        """Get schedule with participants."""
        result = await self.db.execute(
            select(OncallSchedule)
            .options(selectinload(OncallSchedule.participants))
            .where(OncallSchedule.id == schedule_id)
        )
        return result.scalar_one_or_none()

    async def get_team_schedules(self, team_id: UUID) -> list[OncallSchedule]:
        """Get all schedules for a team."""
        result = await self.db.execute(
            select(OncallSchedule)
            .options(selectinload(OncallSchedule.participants))
            .where(
                OncallSchedule.team_id == team_id,
                OncallSchedule.is_active == True,
            )
            .order_by(OncallSchedule.layer)
        )
        return list(result.scalars().all())

    async def update_rotation_order(
        self,
        schedule_id: UUID,
        participant_order: list[UUID],
    ) -> None:
        """Update the order of participants in a rotation (drag-and-drop reorder)."""
        await self.db.execute(
            delete(OncallRotationParticipant)
            .where(OncallRotationParticipant.schedule_id == schedule_id)
        )

        for position, user_id in enumerate(participant_order):
            participant = OncallRotationParticipant(
                schedule_id=schedule_id,
                user_id=user_id,
                position=position,
            )
            self.db.add(participant)

        await self.db.commit()
        log.info("Reordered rotation for schedule %s", schedule_id)

    # =========================================================================
    # SHIFT GENERATION
    # =========================================================================

    async def generate_shifts(
        self,
        schedule_id: UUID,
        start_date: date,
        end_date: date,
        regenerate: bool = False,
    ) -> list[OncallShift]:
        """
        Generate shifts for a schedule within a date range.

        This is the core rotation algorithm that creates actual shift assignments
        from the rotation configuration.
        """
        schedule = await self.get_schedule(schedule_id)
        if not schedule:
            raise ValueError(f"Schedule {schedule_id} not found")

        if not schedule.participants:
            raise ValueError("Schedule has no participants")

        if regenerate:
            await self._clear_future_shifts(schedule_id, start_date)

        config = schedule.rotation_config
        rotation_type = config.get("type", "weekly")
        handoff_time_str = config.get("handoff_time", "09:00")
        handoff_day = config.get("handoff_day", "monday")
        length_days = config.get("length_days", 7)

        handoff_hour, handoff_minute = map(int, handoff_time_str.split(":"))

        tz = pytz.timezone(schedule.timezone)

        participants = sorted(schedule.participants, key=lambda p: p.position)
        participant_count = len(participants)

        time_off_map = await self._get_time_off_map(
            [p.user_id for p in participants],
            start_date,
            end_date,
        )

        shifts: list[OncallShift] = []
        current_date = start_date
        participant_index = 0

        rotation_start = self._find_rotation_start(
            current_date,
            rotation_type,
            handoff_day,
            handoff_hour,
            handoff_minute,
            tz,
        )

        while current_date <= end_date:
            shift_start = tz.localize(
                datetime.combine(rotation_start, time(handoff_hour, handoff_minute))
            )

            if rotation_type == "daily":
                shift_end = shift_start + timedelta(days=1)
            elif rotation_type == "weekly":
                shift_end = shift_start + timedelta(days=7)
            elif rotation_type == "biweekly":
                shift_end = shift_start + timedelta(days=14)
            else:
                shift_end = shift_start + timedelta(days=length_days)

            current_participant = participants[participant_index % participant_count]
            assigned_user_id = current_participant.user_id

            if self._is_user_off(assigned_user_id, shift_start.date(), shift_end.date(), time_off_map):
                assigned_user_id = await self._find_coverage(
                    participants,
                    participant_index,
                    shift_start.date(),
                    shift_end.date(),
                    time_off_map,
                )

            if shift_start.date() <= end_date and shift_end.date() >= start_date:
                shift = OncallShift(
                    schedule_id=schedule_id,
                    user_id=assigned_user_id,
                    start_time=shift_start,
                    end_time=shift_end,
                    shift_type="primary",
                    source="rotation",
                    status="scheduled",
                )
                self.db.add(shift)
                shifts.append(shift)

            participant_index += 1

            if rotation_type == "daily":
                rotation_start += timedelta(days=1)
            elif rotation_type == "weekly":
                rotation_start += timedelta(days=7)
            elif rotation_type == "biweekly":
                rotation_start += timedelta(days=14)
            else:
                rotation_start += timedelta(days=length_days)

            current_date = rotation_start

        await self.db.commit()
        log.info("Generated %d shifts for schedule %s", len(shifts), schedule_id)
        return shifts

    async def generate_follow_the_sun_shifts(
        self,
        schedule_id: UUID,
        start_date: date,
        end_date: date,
        regions: list[dict[str, Any]],
    ) -> list[OncallShift]:
        """
        Generate follow-the-sun shifts where coverage follows working hours across timezones.

        regions = [
            {"timezone": "America/New_York", "users": [user1, user2], "hours": {"start": 9, "end": 17}},
            {"timezone": "Europe/London", "users": [user3, user4], "hours": {"start": 9, "end": 17}},
            {"timezone": "Asia/Tokyo", "users": [user5, user6], "hours": {"start": 9, "end": 17}},
        ]
        """
        shifts: list[OncallShift] = []
        current_date = start_date

        while current_date <= end_date:
            for region in regions:
                tz = pytz.timezone(region["timezone"])
                users = region["users"]
                start_hour = region["hours"]["start"]
                end_hour = region["hours"]["end"]

                shift_start = tz.localize(
                    datetime.combine(current_date, time(start_hour, 0))
                )
                shift_end = tz.localize(
                    datetime.combine(current_date, time(end_hour, 0))
                )

                day_of_year = current_date.timetuple().tm_yday
                user_index = day_of_year % len(users)
                assigned_user = users[user_index]

                shift = OncallShift(
                    schedule_id=schedule_id,
                    user_id=assigned_user,
                    start_time=shift_start,
                    end_time=shift_end,
                    shift_type="primary",
                    source="rotation",
                    status="scheduled",
                )
                self.db.add(shift)
                shifts.append(shift)

            current_date += timedelta(days=1)

        await self.db.commit()
        log.info("Generated %d follow-the-sun shifts for schedule %s", len(shifts), schedule_id)
        return shifts

    async def generate_shadow_shifts(
        self,
        schedule_id: UUID,
        shadow_user_id: UUID,
        start_date: date,
        end_date: date,
    ) -> list[OncallShift]:
        """
        Generate shadow shifts for training a new team member.
        Shadow shifts mirror primary shifts but don't receive alerts.
        """
        result = await self.db.execute(
            select(OncallShift)
            .where(
                OncallShift.schedule_id == schedule_id,
                OncallShift.shift_type == "primary",
                OncallShift.start_time >= datetime.combine(start_date, time.min),
                OncallShift.end_time <= datetime.combine(end_date, time.max),
            )
        )
        primary_shifts = result.scalars().all()

        shadow_shifts: list[OncallShift] = []
        for primary in primary_shifts:
            shadow = OncallShift(
                schedule_id=schedule_id,
                user_id=shadow_user_id,
                start_time=primary.start_time,
                end_time=primary.end_time,
                shift_type="shadow",
                source="rotation",
                status="scheduled",
                shadow_of=primary.id,
            )
            self.db.add(shadow)
            shadow_shifts.append(shadow)

        await self.db.commit()
        log.info("Generated %d shadow shifts for user %s", len(shadow_shifts), shadow_user_id)
        return shadow_shifts

    # =========================================================================
    # CURRENT ON-CALL
    # =========================================================================

    async def get_current_oncall(
        self,
        team_id: UUID,
        at_time: datetime | None = None,
    ) -> dict[str, Any]:
        """
        Get the current on-call user(s) for a team.
        Returns primary, secondary, and any shadows.
        """
        if at_time is None:
            at_time = datetime.now(timezone.utc)

        schedules = await self.get_team_schedules(team_id)

        result: dict[str, Any] = {
            "primary": None,
            "secondary": None,
            "shadows": [],
            "schedules": [],
        }

        for schedule in schedules:
            override = await self._get_active_override(schedule.id, at_time)

            if override:
                oncall_user_id = override.override_user_id
                source = "override"
            else:
                shift = await self._get_current_shift(schedule.id, at_time, "primary")
                if shift:
                    oncall_user_id = shift.user_id
                    source = "shift"
                else:
                    continue

            schedule_info = {
                "schedule_id": schedule.id,
                "schedule_name": schedule.name,
                "layer": schedule.layer,
                "user_id": oncall_user_id,
                "source": source,
            }

            result["schedules"].append(schedule_info)

            if schedule.layer == 1:
                result["primary"] = oncall_user_id
            elif schedule.layer == 2:
                result["secondary"] = oncall_user_id

            shadow_shift = await self._get_current_shift(schedule.id, at_time, "shadow")
            if shadow_shift:
                result["shadows"].append(shadow_shift.user_id)

        return result

    async def get_oncall_at_time(
        self,
        schedule_id: UUID,
        at_time: datetime,
    ) -> UUID | None:
        """Get the on-call user for a specific schedule at a specific time."""
        override = await self._get_active_override(schedule_id, at_time)
        if override:
            return override.override_user_id

        shift = await self._get_current_shift(schedule_id, at_time, "primary")
        return shift.user_id if shift else None

    # =========================================================================
    # GAP DETECTION
    # =========================================================================

    async def detect_gaps(
        self,
        schedule_id: UUID,
        start_date: date,
        end_date: date,
    ) -> list[dict[str, Any]]:
        """
        Detect gaps in schedule coverage.
        Returns list of time periods without on-call coverage.
        """
        schedule = await self.get_schedule(schedule_id)
        if not schedule:
            return []

        if schedule.coverage_type != "24x7":
            return []

        result = await self.db.execute(
            select(OncallShift)
            .where(
                OncallShift.schedule_id == schedule_id,
                OncallShift.shift_type == "primary",
                OncallShift.status.in_(["scheduled", "active"]),
                OncallShift.start_time >= datetime.combine(start_date, time.min),
                OncallShift.end_time <= datetime.combine(end_date + timedelta(days=1), time.max),
            )
            .order_by(OncallShift.start_time)
        )
        shifts = list(result.scalars().all())

        override_result = await self.db.execute(
            select(OncallOverride)
            .where(
                OncallOverride.schedule_id == schedule_id,
                OncallOverride.status == "active",
                OncallOverride.start_time >= datetime.combine(start_date, time.min),
                OncallOverride.end_time <= datetime.combine(end_date + timedelta(days=1), time.max),
            )
        )
        overrides = list(override_result.scalars().all())

        coverage_periods: list[tuple[datetime, datetime]] = []
        for shift in shifts:
            coverage_periods.append((shift.start_time, shift.end_time))
        for override in overrides:
            coverage_periods.append((override.start_time, override.end_time))

        coverage_periods.sort(key=lambda x: x[0])

        merged: list[tuple[datetime, datetime]] = []
        for start, end in coverage_periods:
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))

        gaps: list[dict[str, Any]] = []
        expected_start = datetime.combine(start_date, time.min, tzinfo=timezone.utc)

        for coverage_start, coverage_end in merged:
            if coverage_start > expected_start:
                gap_hours = (coverage_start - expected_start).total_seconds() / 3600
                gaps.append({
                    "gap_start": expected_start,
                    "gap_end": coverage_start,
                    "gap_hours": round(gap_hours, 2),
                })
            expected_start = coverage_end

        expected_end = datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=timezone.utc)
        if merged and merged[-1][1] < expected_end:
            gap_hours = (expected_end - merged[-1][1]).total_seconds() / 3600
            gaps.append({
                "gap_start": merged[-1][1],
                "gap_end": expected_end,
                "gap_hours": round(gap_hours, 2),
            })

        return gaps

    # =========================================================================
    # HELPERS
    # =========================================================================

    def _find_rotation_start(
        self,
        current_date: date,
        rotation_type: str,
        handoff_day: str,
        handoff_hour: int,
        handoff_minute: int,
        tz: Any,
    ) -> date:
        """Find the start of the current/next rotation period."""
        day_map = {
            "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
            "friday": 4, "saturday": 5, "sunday": 6
        }

        if rotation_type == "daily":
            return current_date

        target_weekday = day_map.get(handoff_day.lower(), 0)
        days_since_handoff = (current_date.weekday() - target_weekday) % 7

        rotation_start = current_date - timedelta(days=days_since_handoff)
        return rotation_start

    async def _clear_future_shifts(self, schedule_id: UUID, from_date: date) -> None:
        """Clear shifts from a date onwards."""
        await self.db.execute(
            delete(OncallShift)
            .where(
                OncallShift.schedule_id == schedule_id,
                OncallShift.start_time >= datetime.combine(from_date, time.min),
                OncallShift.source == "rotation",
            )
        )

    async def _get_time_off_map(
        self,
        user_ids: list[UUID],
        start_date: date,
        end_date: date,
    ) -> dict[UUID, list[tuple[date, date]]]:
        """Get time-off periods for users."""
        result = await self.db.execute(
            select(OncallTimeOff)
            .where(
                OncallTimeOff.user_id.in_(user_ids),
                OncallTimeOff.status == "approved",
                OncallTimeOff.start_date <= end_date,
                OncallTimeOff.end_date >= start_date,
            )
        )
        time_offs = result.scalars().all()

        time_off_map: dict[UUID, list[tuple[date, date]]] = defaultdict(list)
        for to in time_offs:
            time_off_map[to.user_id].append((to.start_date, to.end_date))

        return dict(time_off_map)

    def _is_user_off(
        self,
        user_id: UUID,
        shift_start: date,
        shift_end: date,
        time_off_map: dict[UUID, list[tuple[date, date]]],
    ) -> bool:
        """Check if user has approved time off during shift."""
        if user_id not in time_off_map:
            return False

        for off_start, off_end in time_off_map[user_id]:
            if off_start <= shift_end and off_end >= shift_start:
                return True

        return False

    async def _find_coverage(
        self,
        participants: list[OncallRotationParticipant],
        original_index: int,
        shift_start: date,
        shift_end: date,
        time_off_map: dict[UUID, list[tuple[date, date]]],
    ) -> UUID:
        """Find a replacement when the scheduled person is on PTO."""
        participant_count = len(participants)

        for offset in range(1, participant_count):
            candidate_index = (original_index + offset) % participant_count
            candidate = participants[candidate_index]

            if not self._is_user_off(candidate.user_id, shift_start, shift_end, time_off_map):
                return candidate.user_id

        return participants[original_index % participant_count].user_id

    async def _get_active_override(
        self,
        schedule_id: UUID,
        at_time: datetime,
    ) -> OncallOverride | None:
        """Get active override at a given time."""
        result = await self.db.execute(
            select(OncallOverride)
            .where(
                OncallOverride.schedule_id == schedule_id,
                OncallOverride.status == "active",
                OncallOverride.start_time <= at_time,
                OncallOverride.end_time > at_time,
            )
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def _get_current_shift(
        self,
        schedule_id: UUID,
        at_time: datetime,
        shift_type: str,
    ) -> OncallShift | None:
        """Get current shift at a given time."""
        result = await self.db.execute(
            select(OncallShift)
            .where(
                OncallShift.schedule_id == schedule_id,
                OncallShift.shift_type == shift_type,
                OncallShift.status.in_(["scheduled", "active"]),
                OncallShift.start_time <= at_time,
                OncallShift.end_time > at_time,
            )
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def _get_holidays(
        self,
        team_id: UUID,
        start_date: date,
        end_date: date,
    ) -> list[date]:
        """Get holidays for the team's organization."""
        result = await self.db.execute(
            select(OncallTeam).where(OncallTeam.id == team_id)
        )
        team = result.scalar_one_or_none()
        if not team:
            return []

        cal_result = await self.db.execute(
            select(OncallHolidayCalendar)
            .where(
                OncallHolidayCalendar.org_id == team.org_id,
                OncallHolidayCalendar.is_default == True,
            )
        )
        calendar = cal_result.scalar_one_or_none()
        if not calendar:
            return []

        holiday_result = await self.db.execute(
            select(OncallHoliday)
            .where(
                OncallHoliday.calendar_id == calendar.id,
                OncallHoliday.holiday_date >= start_date,
                OncallHoliday.holiday_date <= end_date,
            )
        )
        holidays = holiday_result.scalars().all()

        return [h.holiday_date for h in holidays]


# Singleton instance
_schedule_service: ScheduleService | None = None


async def get_schedule_service(db: AsyncSession) -> ScheduleService:
    """Get or create schedule service instance."""
    return ScheduleService(db)
