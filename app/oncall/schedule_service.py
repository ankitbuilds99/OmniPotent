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
from sqlalchemy import select, delete
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

log = logging.getLogger("omnipotent.oncall.schedule_service")


def _aware(value: datetime | None) -> datetime:
    """A timestamp read back from the database, as tz-aware UTC.

    Postgres round-trips `DateTime(timezone=True)` with its offset intact;
    SQLite has no timezone type and hands back a naive datetime for the same
    column. Any code that compares a stored boundary against a computed one has
    to normalise first or it raises "can't compare offset-naive and
    offset-aware datetimes" on whichever backend it was not developed against.
    """
    if value is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _rotation_period(rotation_type: str, length_days: int) -> timedelta:
    """How long one turn of the rotation lasts, in days."""
    return {
        "daily": timedelta(days=1),
        "weekly": timedelta(days=7),
        "biweekly": timedelta(days=14),
    }.get(rotation_type, timedelta(days=max(1, length_days)))


def _handoff(day: date, hour: int, minute: int, tz: Any) -> datetime:
    """The wall-clock handoff on `day`, as a real instant in `tz`.

    Shift boundaries have to be computed this way at *both* ends. The previous
    code localized the start and then did `shift_start + timedelta(days=7)` for
    the end: pytz freezes the UTC offset at localize time, so that addition
    keeps the pre-transition offset while the next shift's start is localized
    fresh at the post-transition one. Across the November fall-back the shift
    ended at 09:00-04:00 and the next began at 09:00-05:00, leaving an hour with
    nobody paged; in March the sign flips and two engineers are both primary for
    an hour. Deriving the end from the next rotation *date* makes end[n] and
    start[n+1] the same expression, so they agree by construction.

    `normalize()` handles the other trap: a handoff time can be one that does
    not exist — 02:30 on a spring-forward Sunday — and pytz otherwise returns a
    datetime for an instant that never occurred. Normalizing maps it to the
    first real moment at or after it.
    """
    naive = datetime.combine(day, time(hour, minute))
    return tz.normalize(tz.localize(naive, is_dst=False))


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
                OncallSchedule.is_active.is_(True),
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

        period = _rotation_period(rotation_type, length_days)

        while current_date <= end_date:
            next_rotation_start = rotation_start + period

            # The end of a shift is the wall-clock handoff on the *next*
            # rotation day, computed the same way that day's shift start will
            # be. Anything else drifts at a DST transition — see _handoff().
            shift_start = _handoff(rotation_start, handoff_hour, handoff_minute, tz)
            shift_end = _handoff(next_rotation_start, handoff_hour, handoff_minute, tz)

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

            rotation_start = next_rotation_start
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

    async def _member_emails(self, user_ids: set[UUID]) -> dict[UUID, str]:
        """Map member ids to their email. `oncall_*.user_id` FKs to `members.id`,
        which is the only place a responder has a human-readable identity."""
        if not user_ids:
            return {}
        from app.database.models import Member

        rows = await self.db.execute(select(Member.id, Member.email).where(Member.id.in_(user_ids)))
        return {row[0]: row[1] for row in rows.all()}

    async def get_team_participants(self, team_id: UUID) -> list[dict[str, Any]]:
        """The team roster, with each member's real email from `members`."""
        from app.oncall.models import OncallTeamMember

        rows = await self.db.execute(
            select(OncallTeamMember)
            .where(
                OncallTeamMember.team_id == team_id,
                OncallTeamMember.is_active.is_(True),
            )
            .order_by(OncallTeamMember.joined_at)
        )
        members = list(rows.scalars().all())
        emails = await self._member_emails({m.user_id for m in members})
        return [
            {
                "user_id": m.user_id,
                "user_email": emails.get(m.user_id),
                "role": m.role,
                "timezone": m.timezone,
                "skills": m.skills,
            }
            for m in members
        ]

    async def get_current_oncall(
        self,
        team_id: UUID,
        at_time: datetime | None = None,
    ) -> dict[str, Any]:
        """
        Get the current on-call user(s) for a team.

        Returns primary, secondary, and any shadows, plus the `shifts` rows they
        come from and the team `participants`. The shift/participant detail used
        to be missing entirely, so the UI's roster and responder list — which
        read exactly those two keys — were permanently empty and the schedule id
        it needs for an override was never sent.
        """
        if at_time is None:
            at_time = datetime.now(timezone.utc)

        schedules = await self.get_team_schedules(team_id)

        result: dict[str, Any] = {
            "primary": None,
            "secondary": None,
            "shadows": [],
            "schedules": [],
            "shifts": [],
            "participants": await self.get_team_participants(team_id),
            # Every active schedule, including ones nobody is currently on call
            # for. `schedules` only lists covered ones, so a team whose shifts
            # haven't been generated yet exposed no schedule id at all and the
            # UI had nothing to attach an override to.
            "team_schedules": [
                {
                    "id": s.id,
                    "name": s.name,
                    "layer": s.layer,
                    "timezone": s.timezone,
                    "rotation_type": (s.rotation_config or {}).get("type"),
                }
                for s in schedules
            ],
        }
        emails = {
            p["user_id"]: p["user_email"]
            for p in result["participants"]
            if p["user_email"] is not None
        }

        def _shift_entry(
            schedule: OncallSchedule,
            *,
            row_id: UUID,
            user_id: UUID,
            role: str,
            start: datetime,
            end: datetime,
            source: str,
            status: str,
        ) -> dict[str, Any]:
            return {
                "id": row_id,
                "schedule_id": schedule.id,
                "schedule_name": schedule.name,
                "layer": schedule.layer,
                "user_id": user_id,
                "user_email": emails.get(user_id),
                "role": role,
                "start_time": start,
                "end_time": end,
                "source": source,
                "status": status,
            }

        for schedule in schedules:
            override = await self._get_active_override(schedule.id, at_time)
            role = "primary" if schedule.layer == 1 else "secondary"

            if override:
                oncall_user_id = override.override_user_id
                source = "override"
                result["shifts"].append(
                    _shift_entry(
                        schedule,
                        row_id=override.id,
                        user_id=oncall_user_id,
                        role=role,
                        start=override.start_time,
                        end=override.end_time,
                        source="override",
                        status="active",
                    )
                )
            else:
                shift = await self._get_current_shift(schedule.id, at_time, "primary")
                if shift:
                    oncall_user_id = shift.user_id
                    source = "shift"
                    result["shifts"].append(
                        _shift_entry(
                            schedule,
                            row_id=shift.id,
                            user_id=oncall_user_id,
                            role=role,
                            start=shift.start_time,
                            end=shift.end_time,
                            source="shift",
                            status="active",
                        )
                    )
                else:
                    continue

            schedule_info = {
                "schedule_id": schedule.id,
                "schedule_name": schedule.name,
                "layer": schedule.layer,
                "user_id": oncall_user_id,
                "user_email": emails.get(oncall_user_id),
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
                result["shifts"].append(
                    _shift_entry(
                        schedule,
                        row_id=shadow_shift.id,
                        user_id=shadow_shift.user_id,
                        role="shadow",
                        start=shadow_shift.start_time,
                        end=shadow_shift.end_time,
                        source="shift",
                        status="active",
                    )
                )

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

        # _aware(): SQLite hands these columns back tz-naive while the window
        # below is built tz-aware, and the very first comparison then raises
        # TypeError — which is what made this endpoint a 500 on every call.
        coverage_periods: list[tuple[datetime, datetime]] = []
        for shift in shifts:
            coverage_periods.append((_aware(shift.start_time), _aware(shift.end_time)))
        for override in overrides:
            coverage_periods.append(
                (_aware(override.start_time), _aware(override.end_time))
            )

        coverage_periods.sort(key=lambda x: x[0])

        merged: list[tuple[datetime, datetime]] = []
        for start, end in coverage_periods:
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))

        window_start = datetime.combine(start_date, time.min, tzinfo=timezone.utc)
        window_end = datetime.combine(
            end_date + timedelta(days=1), time.min, tzinfo=timezone.utc
        )

        def _gap(gap_start: datetime, gap_end: datetime) -> dict[str, Any]:
            return {
                "gap_start": gap_start,
                "gap_end": gap_end,
                "gap_hours": round((gap_end - gap_start).total_seconds() / 3600, 2),
            }

        # No shifts at all means the whole window is uncovered. The old code
        # only emitted a trailing gap `if merged`, so a 24x7 schedule that had
        # never had shifts generated reported perfect coverage — the one case
        # where the answer matters most.
        if not merged:
            return [_gap(window_start, window_end)] if window_end > window_start else []

        gaps: list[dict[str, Any]] = []
        expected_start = window_start

        for coverage_start, coverage_end in merged:
            if coverage_start > expected_start:
                gaps.append(_gap(expected_start, min(coverage_start, window_end)))
            expected_start = max(expected_start, coverage_end)

        if expected_start < window_end:
            gaps.append(_gap(expected_start, window_end))

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
                OncallHolidayCalendar.is_default.is_(True),
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
