"""On-call compensation tracking.

Tracks:
- On-call hours per engineer
- Incident response time
- Weekend/holiday shifts
- Compensation calculations
- PTO accrual
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any

log = logging.getLogger("omnipotent.oncall.compensation")


class ShiftType(Enum):
    """Types of on-call shifts."""

    WEEKDAY = "weekday"
    WEEKEND = "weekend"
    HOLIDAY = "holiday"
    OVERNIGHT = "overnight"


@dataclass
class OnCallHours:
    """Record of on-call hours."""

    record_id: str
    engineer_id: str
    engineer_name: str
    start_time: datetime
    end_time: datetime
    shift_type: ShiftType
    incidents_handled: int = 0
    pages_received: int = 0
    total_response_minutes: int = 0

    @property
    def duration_hours(self) -> float:
        return (self.end_time - self.start_time).total_seconds() / 3600

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "engineer_id": self.engineer_id,
            "engineer_name": self.engineer_name,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat(),
            "duration_hours": round(self.duration_hours, 2),
            "shift_type": self.shift_type.value,
            "incidents_handled": self.incidents_handled,
            "pages_received": self.pages_received,
        }


@dataclass
class CompensationPolicy:
    """Compensation policy for on-call work."""

    policy_id: str
    name: str
    base_rate_per_hour: Decimal
    weekend_multiplier: float = 1.5
    holiday_multiplier: float = 2.0
    overnight_multiplier: float = 1.25
    incident_bonus: Decimal = Decimal("25.00")
    pto_hours_per_oncall_week: float = 0.5
    max_weekly_hours: int = 40
    overtime_multiplier: float = 1.5

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "name": self.name,
            "base_rate_per_hour": str(self.base_rate_per_hour),
            "weekend_multiplier": self.weekend_multiplier,
            "holiday_multiplier": self.holiday_multiplier,
            "overnight_multiplier": self.overnight_multiplier,
            "incident_bonus": str(self.incident_bonus),
            "pto_hours_per_oncall_week": self.pto_hours_per_oncall_week,
            "max_weekly_hours": self.max_weekly_hours,
        }


@dataclass
class CompensationSummary:
    """Summary of compensation for a period."""

    engineer_id: str
    engineer_name: str
    period_start: datetime
    period_end: datetime
    total_hours: float
    weekday_hours: float
    weekend_hours: float
    holiday_hours: float
    overnight_hours: float
    incidents_handled: int
    pages_received: int
    base_pay: Decimal
    overtime_pay: Decimal
    incident_bonus: Decimal
    total_pay: Decimal
    pto_earned: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "engineer_id": self.engineer_id,
            "engineer_name": self.engineer_name,
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "total_hours": round(self.total_hours, 2),
            "weekday_hours": round(self.weekday_hours, 2),
            "weekend_hours": round(self.weekend_hours, 2),
            "holiday_hours": round(self.holiday_hours, 2),
            "overnight_hours": round(self.overnight_hours, 2),
            "incidents_handled": self.incidents_handled,
            "pages_received": self.pages_received,
            "base_pay": str(self.base_pay),
            "overtime_pay": str(self.overtime_pay),
            "incident_bonus": str(self.incident_bonus),
            "total_pay": str(self.total_pay),
            "pto_earned": round(self.pto_earned, 2),
        }


class CompensationTracker:
    """Tracks and calculates on-call compensation."""

    def __init__(
        self,
        default_policy: CompensationPolicy | None = None,
    ) -> None:
        self._policies: dict[str, CompensationPolicy] = {}
        self._records: list[OnCallHours] = []
        self._record_counter = 0
        self._holidays: set[str] = set()

        if default_policy:
            self._policies["default"] = default_policy
        else:
            self._policies["default"] = CompensationPolicy(
                policy_id="default",
                name="Standard On-Call Policy",
                base_rate_per_hour=Decimal("10.00"),
                weekend_multiplier=1.5,
                holiday_multiplier=2.0,
                overnight_multiplier=1.25,
                incident_bonus=Decimal("25.00"),
                pto_hours_per_oncall_week=0.5,
            )

    def set_policy(self, policy: CompensationPolicy) -> None:
        """Set a compensation policy."""
        self._policies[policy.policy_id] = policy

    def get_policy(self, policy_id: str = "default") -> CompensationPolicy:
        """Get a compensation policy."""
        return self._policies.get(policy_id, self._policies["default"])

    def add_holiday(self, date: str) -> None:
        """Add a holiday date (YYYY-MM-DD format)."""
        self._holidays.add(date)

    def record_shift(
        self,
        engineer_id: str,
        engineer_name: str,
        start_time: datetime,
        end_time: datetime,
        incidents_handled: int = 0,
        pages_received: int = 0,
        response_minutes: int = 0,
    ) -> OnCallHours:
        """Record an on-call shift."""
        self._record_counter += 1
        record_id = f"oncall_{self._record_counter}"

        shift_type = self._determine_shift_type(start_time, end_time)

        record = OnCallHours(
            record_id=record_id,
            engineer_id=engineer_id,
            engineer_name=engineer_name,
            start_time=start_time,
            end_time=end_time,
            shift_type=shift_type,
            incidents_handled=incidents_handled,
            pages_received=pages_received,
            total_response_minutes=response_minutes,
        )

        self._records.append(record)

        log.info(
            "shift recorded",
            extra={
                "record_id": record_id,
                "engineer_id": engineer_id,
                "hours": record.duration_hours,
                "type": shift_type.value,
            },
        )

        return record

    def _determine_shift_type(
        self,
        start_time: datetime,
        end_time: datetime,
    ) -> ShiftType:
        """Determine the type of shift."""
        date_str = start_time.strftime("%Y-%m-%d")
        if date_str in self._holidays:
            return ShiftType.HOLIDAY

        if start_time.weekday() >= 5:
            return ShiftType.WEEKEND

        if start_time.hour >= 22 or start_time.hour < 6:
            return ShiftType.OVERNIGHT

        return ShiftType.WEEKDAY

    def calculate_compensation(
        self,
        engineer_id: str,
        period_start: datetime,
        period_end: datetime,
        policy_id: str = "default",
    ) -> CompensationSummary:
        """Calculate compensation for an engineer over a period."""
        policy = self.get_policy(policy_id)

        records = [
            r for r in self._records
            if r.engineer_id == engineer_id
            and r.start_time >= period_start
            and r.end_time <= period_end
        ]

        weekday_hours = sum(
            r.duration_hours for r in records if r.shift_type == ShiftType.WEEKDAY
        )
        weekend_hours = sum(
            r.duration_hours for r in records if r.shift_type == ShiftType.WEEKEND
        )
        holiday_hours = sum(
            r.duration_hours for r in records if r.shift_type == ShiftType.HOLIDAY
        )
        overnight_hours = sum(
            r.duration_hours for r in records if r.shift_type == ShiftType.OVERNIGHT
        )

        total_hours = weekday_hours + weekend_hours + holiday_hours + overnight_hours
        incidents = sum(r.incidents_handled for r in records)
        pages = sum(r.pages_received for r in records)

        base_pay = (
            Decimal(str(weekday_hours)) * policy.base_rate_per_hour
            + Decimal(str(weekend_hours)) * policy.base_rate_per_hour * Decimal(str(policy.weekend_multiplier))
            + Decimal(str(holiday_hours)) * policy.base_rate_per_hour * Decimal(str(policy.holiday_multiplier))
            + Decimal(str(overnight_hours)) * policy.base_rate_per_hour * Decimal(str(policy.overnight_multiplier))
        )

        overtime_hours = max(0, total_hours - policy.max_weekly_hours)
        overtime_pay = Decimal(str(overtime_hours)) * policy.base_rate_per_hour * Decimal(str(policy.overtime_multiplier))

        incident_bonus = Decimal(incidents) * policy.incident_bonus

        total_pay = base_pay + overtime_pay + incident_bonus

        weeks = (period_end - period_start).days / 7
        pto_earned = weeks * policy.pto_hours_per_oncall_week

        engineer_name = records[0].engineer_name if records else f"Engineer {engineer_id}"

        return CompensationSummary(
            engineer_id=engineer_id,
            engineer_name=engineer_name,
            period_start=period_start,
            period_end=period_end,
            total_hours=total_hours,
            weekday_hours=weekday_hours,
            weekend_hours=weekend_hours,
            holiday_hours=holiday_hours,
            overnight_hours=overnight_hours,
            incidents_handled=incidents,
            pages_received=pages,
            base_pay=base_pay,
            overtime_pay=overtime_pay,
            incident_bonus=incident_bonus,
            total_pay=total_pay,
            pto_earned=pto_earned,
        )

    def get_team_summary(
        self,
        engineer_ids: list[str],
        period_start: datetime,
        period_end: datetime,
        policy_id: str = "default",
    ) -> list[CompensationSummary]:
        """Get compensation summary for a team."""
        summaries = []
        for eng_id in engineer_ids:
            summary = self.calculate_compensation(eng_id, period_start, period_end, policy_id)
            summaries.append(summary)
        return sorted(summaries, key=lambda s: s.total_pay, reverse=True)

    def get_monthly_report(
        self,
        year: int,
        month: int,
        policy_id: str = "default",
    ) -> dict[str, Any]:
        """Generate monthly compensation report."""
        from calendar import monthrange

        start = datetime(year, month, 1, tzinfo=timezone.utc)
        _, last_day = monthrange(year, month)
        end = datetime(year, month, last_day, 23, 59, 59, tzinfo=timezone.utc)

        engineer_ids = list(set(r.engineer_id for r in self._records))
        summaries = self.get_team_summary(engineer_ids, start, end, policy_id)

        total_pay = sum(s.total_pay for s in summaries)
        total_hours = sum(s.total_hours for s in summaries)
        total_incidents = sum(s.incidents_handled for s in summaries)
        total_pto = sum(s.pto_earned for s in summaries)

        return {
            "period": f"{year}-{month:02d}",
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "engineer_count": len(summaries),
            "total_pay": str(total_pay),
            "total_hours": round(total_hours, 2),
            "total_incidents": total_incidents,
            "total_pto_earned": round(total_pto, 2),
            "summaries": [s.to_dict() for s in summaries],
        }

    def export_for_payroll(
        self,
        period_start: datetime,
        period_end: datetime,
    ) -> list[dict[str, Any]]:
        """Export data for payroll processing."""
        engineer_ids = list(set(r.engineer_id for r in self._records))
        summaries = self.get_team_summary(engineer_ids, period_start, period_end)

        return [
            {
                "employee_id": s.engineer_id,
                "employee_name": s.engineer_name,
                "period_start": s.period_start.strftime("%Y-%m-%d"),
                "period_end": s.period_end.strftime("%Y-%m-%d"),
                "regular_hours": round(min(s.total_hours, 40), 2),
                "overtime_hours": round(max(0, s.total_hours - 40), 2),
                "total_compensation": str(s.total_pay),
                "pto_accrued": round(s.pto_earned, 2),
                "incidents": s.incidents_handled,
            }
            for s in summaries
        ]


_tracker: CompensationTracker | None = None


def get_compensation_tracker() -> CompensationTracker:
    """Get the default compensation tracker."""
    global _tracker
    if _tracker is None:
        _tracker = CompensationTracker()
    return _tracker
