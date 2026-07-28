"""SLA tracking for the status page.

Tracks:
- SLA targets and compliance
- Uptime budgets
- Real-time burn-down
- Breach predictions
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from typing import Any

log = logging.getLogger("devcopilot.status_page.sla")


class SLAPeriod(Enum):
    """SLA measurement period."""

    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    ANNUAL = "annual"


@dataclass
class SLATarget:
    """SLA target definition."""

    sla_id: str
    name: str
    target_uptime: Decimal
    period: SLAPeriod
    components: list[str]
    exclude_maintenance: bool = True
    credit_tiers: list[tuple[Decimal, Decimal]] = field(default_factory=list)

    @property
    def allowed_downtime_minutes(self) -> Decimal:
        """Calculate allowed downtime in minutes."""
        period_minutes = {
            SLAPeriod.DAILY: Decimal("1440"),
            SLAPeriod.WEEKLY: Decimal("10080"),
            SLAPeriod.MONTHLY: Decimal("43200"),
            SLAPeriod.QUARTERLY: Decimal("129600"),
            SLAPeriod.ANNUAL: Decimal("525600"),
        }.get(self.period, Decimal("43200"))

        return period_minutes * (Decimal("100") - self.target_uptime) / Decimal("100")

    def to_dict(self) -> dict[str, Any]:
        return {
            "sla_id": self.sla_id,
            "name": self.name,
            "target_uptime": str(self.target_uptime),
            "period": self.period.value,
            "components": self.components,
            "allowed_downtime_minutes": str(self.allowed_downtime_minutes),
        }


@dataclass
class DowntimeEvent:
    """A recorded downtime event."""

    event_id: str
    component_id: str
    started_at: datetime
    ended_at: datetime | None = None
    is_maintenance: bool = False
    incident_id: str | None = None

    @property
    def duration_minutes(self) -> float:
        end = self.ended_at or datetime.now(timezone.utc)
        return (end - self.started_at).total_seconds() / 60


@dataclass
class SLAStatus:
    """Current SLA status."""

    sla: SLATarget
    period_start: datetime
    period_end: datetime
    current_uptime: Decimal
    downtime_minutes: Decimal
    budget_remaining_minutes: Decimal
    budget_used_percent: Decimal
    is_breached: bool
    predicted_breach: bool
    predicted_breach_at: datetime | None = None
    events: list[DowntimeEvent] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sla_id": self.sla.sla_id,
            "sla_name": self.sla.name,
            "target_uptime": str(self.sla.target_uptime),
            "current_uptime": str(round(self.current_uptime, 4)),
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "downtime_minutes": str(round(self.downtime_minutes, 2)),
            "budget_remaining_minutes": str(round(self.budget_remaining_minutes, 2)),
            "budget_used_percent": str(round(self.budget_used_percent, 2)),
            "is_breached": self.is_breached,
            "predicted_breach": self.predicted_breach,
            "predicted_breach_at": self.predicted_breach_at.isoformat() if self.predicted_breach_at else None,
        }


class SLATracker:
    """Tracks SLA compliance and budget."""

    def __init__(self) -> None:
        self._slas: dict[str, SLATarget] = {}
        self._events: list[DowntimeEvent] = []
        self._event_counter = 0
        self._setup_default_slas()

    def _setup_default_slas(self) -> None:
        """Set up default SLA targets."""
        slas = [
            SLATarget(
                sla_id="api_monthly",
                name="API Monthly Uptime",
                target_uptime=Decimal("99.9"),
                period=SLAPeriod.MONTHLY,
                components=["api", "dashboard"],
                credit_tiers=[
                    (Decimal("99.9"), Decimal("0")),
                    (Decimal("99.0"), Decimal("10")),
                    (Decimal("95.0"), Decimal("25")),
                    (Decimal("0"), Decimal("50")),
                ],
            ),
            SLATarget(
                sla_id="payments_monthly",
                name="Payment Processing Monthly",
                target_uptime=Decimal("99.99"),
                period=SLAPeriod.MONTHLY,
                components=["payments", "checkout"],
                credit_tiers=[
                    (Decimal("99.99"), Decimal("0")),
                    (Decimal("99.9"), Decimal("10")),
                    (Decimal("99.0"), Decimal("25")),
                    (Decimal("0"), Decimal("100")),
                ],
            ),
        ]

        for sla in slas:
            self._slas[sla.sla_id] = sla

    def register_sla(self, sla: SLATarget) -> None:
        """Register an SLA target."""
        self._slas[sla.sla_id] = sla

    def record_downtime_start(
        self,
        component_id: str,
        is_maintenance: bool = False,
        incident_id: str | None = None,
    ) -> DowntimeEvent:
        """Record the start of downtime."""
        self._event_counter += 1
        event_id = f"dt_{self._event_counter}"

        event = DowntimeEvent(
            event_id=event_id,
            component_id=component_id,
            started_at=datetime.now(timezone.utc),
            is_maintenance=is_maintenance,
            incident_id=incident_id,
        )

        self._events.append(event)

        log.info(
            "downtime started",
            extra={
                "event_id": event_id,
                "component_id": component_id,
                "is_maintenance": is_maintenance,
            },
        )

        return event

    def record_downtime_end(self, event_id: str) -> DowntimeEvent | None:
        """Record the end of downtime."""
        for event in self._events:
            if event.event_id == event_id and event.ended_at is None:
                event.ended_at = datetime.now(timezone.utc)
                log.info(
                    "downtime ended",
                    extra={
                        "event_id": event_id,
                        "duration_minutes": event.duration_minutes,
                    },
                )
                return event
        return None

    def get_sla_status(self, sla_id: str) -> SLAStatus | None:
        """Get current SLA status."""
        sla = self._slas.get(sla_id)
        if not sla:
            return None

        now = datetime.now(timezone.utc)
        period_start, period_end = self._get_period_bounds(sla.period, now)

        relevant_events = [
            e for e in self._events
            if e.component_id in sla.components
            and e.started_at >= period_start
            and (not sla.exclude_maintenance or not e.is_maintenance)
        ]

        downtime_minutes = Decimal(0)
        for event in relevant_events:
            end = event.ended_at or now
            if end > period_end:
                end = period_end
            duration = (end - max(event.started_at, period_start)).total_seconds() / 60
            downtime_minutes += Decimal(str(duration))

        total_minutes = Decimal(str((min(now, period_end) - period_start).total_seconds() / 60))
        if total_minutes > 0:
            current_uptime = (total_minutes - downtime_minutes) / total_minutes * Decimal("100")
        else:
            current_uptime = Decimal("100")

        budget_total = sla.allowed_downtime_minutes
        budget_remaining = budget_total - downtime_minutes
        budget_used_percent = (downtime_minutes / budget_total * Decimal("100")) if budget_total > 0 else Decimal("0")

        is_breached = current_uptime < sla.target_uptime
        predicted_breach, predicted_at = self._predict_breach(
            sla, period_start, period_end, downtime_minutes, now
        )

        return SLAStatus(
            sla=sla,
            period_start=period_start,
            period_end=period_end,
            current_uptime=current_uptime,
            downtime_minutes=downtime_minutes,
            budget_remaining_minutes=max(Decimal("0"), budget_remaining),
            budget_used_percent=min(Decimal("100"), budget_used_percent),
            is_breached=is_breached,
            predicted_breach=predicted_breach,
            predicted_breach_at=predicted_at,
            events=relevant_events,
        )

    def _get_period_bounds(
        self,
        period: SLAPeriod,
        reference: datetime,
    ) -> tuple[datetime, datetime]:
        """Get start and end of the SLA period."""
        if period == SLAPeriod.DAILY:
            start = reference.replace(hour=0, minute=0, second=0, microsecond=0)
            end = start + timedelta(days=1)
        elif period == SLAPeriod.WEEKLY:
            start = reference - timedelta(days=reference.weekday())
            start = start.replace(hour=0, minute=0, second=0, microsecond=0)
            end = start + timedelta(days=7)
        elif period == SLAPeriod.MONTHLY:
            start = reference.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            if reference.month == 12:
                end = start.replace(year=reference.year + 1, month=1)
            else:
                end = start.replace(month=reference.month + 1)
        elif period == SLAPeriod.QUARTERLY:
            quarter = (reference.month - 1) // 3
            start = reference.replace(month=quarter * 3 + 1, day=1, hour=0, minute=0, second=0, microsecond=0)
            end_month = start.month + 3
            if end_month > 12:
                end = start.replace(year=start.year + 1, month=end_month - 12)
            else:
                end = start.replace(month=end_month)
        else:
            start = reference.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
            end = start.replace(year=start.year + 1)

        return start, end

    def _predict_breach(
        self,
        sla: SLATarget,
        period_start: datetime,
        period_end: datetime,
        current_downtime: Decimal,
        now: datetime,
    ) -> tuple[bool, datetime | None]:
        """Predict if SLA will be breached."""
        budget = sla.allowed_downtime_minutes
        remaining = budget - current_downtime

        if remaining <= 0:
            return True, now

        elapsed = Decimal(str((now - period_start).total_seconds() / 60))
        remaining_period = Decimal(str((period_end - now).total_seconds() / 60))

        if elapsed > 0:
            downtime_rate = current_downtime / elapsed
            projected_additional = downtime_rate * remaining_period
            projected_total = current_downtime + projected_additional

            if projected_total > budget:
                minutes_until_breach = remaining / downtime_rate if downtime_rate > 0 else Decimal("0")
                breach_at = now + timedelta(minutes=float(minutes_until_breach))
                return True, breach_at

        return False, None

    def get_all_sla_status(self) -> list[SLAStatus]:
        """Get status for all SLAs."""
        statuses = []
        for sla_id in self._slas:
            status = self.get_sla_status(sla_id)
            if status:
                statuses.append(status)
        return statuses

    def get_credit_due(self, sla_id: str) -> Decimal:
        """Calculate credit due based on SLA performance."""
        status = self.get_sla_status(sla_id)
        if not status or not status.sla.credit_tiers:
            return Decimal("0")

        for threshold, credit in status.sla.credit_tiers:
            if status.current_uptime >= threshold:
                return credit

        return status.sla.credit_tiers[-1][1] if status.sla.credit_tiers else Decimal("0")

    def get_dashboard_data(self) -> dict[str, Any]:
        """Get SLA dashboard data."""
        statuses = self.get_all_sla_status()

        breached = [s for s in statuses if s.is_breached]
        at_risk = [s for s in statuses if s.predicted_breach and not s.is_breached]

        return {
            "total_slas": len(statuses),
            "breached_count": len(breached),
            "at_risk_count": len(at_risk),
            "slas": [s.to_dict() for s in statuses],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }


_tracker: SLATracker | None = None


def get_sla_tracker() -> SLATracker:
    """Get the default SLA tracker."""
    global _tracker
    if _tracker is None:
        _tracker = SLATracker()
    return _tracker
