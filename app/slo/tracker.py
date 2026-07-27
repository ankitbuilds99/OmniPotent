"""SLO/SLI tracking implementation.

Implements Google SRE-style SLO tracking with:
- Multiple SLI types (availability, latency, throughput, etc.)
- Rolling and calendar-based windows
- Error budget calculations
- Multi-window burn rate alerts
"""

from __future__ import annotations

import logging
import secrets
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("devcopilot.slo.tracker")


class SLIType(Enum):
    """Types of Service Level Indicators."""

    AVAILABILITY = "availability"
    LATENCY = "latency"
    THROUGHPUT = "throughput"
    ERROR_RATE = "error_rate"
    SATURATION = "saturation"
    FRESHNESS = "freshness"
    CORRECTNESS = "correctness"
    CUSTOM = "custom"


class SLOWindow(Enum):
    """SLO measurement windows."""

    ROLLING_1H = "rolling_1h"
    ROLLING_6H = "rolling_6h"
    ROLLING_24H = "rolling_24h"
    ROLLING_7D = "rolling_7d"
    ROLLING_28D = "rolling_28d"
    ROLLING_30D = "rolling_30d"
    CALENDAR_WEEK = "calendar_week"
    CALENDAR_MONTH = "calendar_month"
    CALENDAR_QUARTER = "calendar_quarter"


class SLOStatus(Enum):
    """SLO compliance status."""

    HEALTHY = "healthy"
    AT_RISK = "at_risk"
    BREACHED = "breached"
    NO_DATA = "no_data"


@dataclass
class SLI:
    """A Service Level Indicator measurement."""

    id: str
    slo_id: str
    timestamp: datetime
    value: float
    good_count: int = 0
    total_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SLO:
    """A Service Level Objective definition."""

    id: str
    name: str
    description: str
    service: str
    sli_type: SLIType
    target: float
    window: SLOWindow
    org_id: str = ""
    team_id: str = ""
    owner: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    labels: dict[str, str] = field(default_factory=dict)
    alert_burn_rate_threshold: float = 1.0
    alert_channels: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def get_window_duration(self) -> timedelta:
        """Get the duration of the SLO window."""
        durations = {
            SLOWindow.ROLLING_1H: timedelta(hours=1),
            SLOWindow.ROLLING_6H: timedelta(hours=6),
            SLOWindow.ROLLING_24H: timedelta(days=1),
            SLOWindow.ROLLING_7D: timedelta(days=7),
            SLOWindow.ROLLING_28D: timedelta(days=28),
            SLOWindow.ROLLING_30D: timedelta(days=30),
            SLOWindow.CALENDAR_WEEK: timedelta(days=7),
            SLOWindow.CALENDAR_MONTH: timedelta(days=30),
            SLOWindow.CALENDAR_QUARTER: timedelta(days=90),
        }
        return durations.get(self.window, timedelta(days=30))


@dataclass
class ErrorBudget:
    """Error budget status for an SLO."""

    slo_id: str
    total_budget: float
    consumed: float
    remaining: float
    remaining_percent: float
    burn_rate: float
    projected_exhaustion: datetime | None
    status: SLOStatus
    window_start: datetime
    window_end: datetime
    good_count: int = 0
    total_count: int = 0
    current_sli: float = 0.0


@dataclass
class BurnRate:
    """Multi-window burn rate calculation."""

    slo_id: str
    short_window: str
    short_window_burn_rate: float
    long_window: str
    long_window_burn_rate: float
    is_alerting: bool
    severity: str
    message: str
    calculated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class SLOReport:
    """SLO compliance report."""

    slo_id: str
    slo_name: str
    service: str
    period_start: datetime
    period_end: datetime
    target: float
    achieved: float
    compliant: bool
    error_budget_consumed: float
    total_events: int
    good_events: int
    bad_events: int
    downtime_minutes: int
    incidents: list[dict[str, Any]] = field(default_factory=list)
    daily_values: list[dict[str, Any]] = field(default_factory=list)


_SLOS: dict[str, SLO] = {}
_SLI_DATA: dict[str, list[SLI]] = defaultdict(list)


class SLOTracker:
    """Tracks SLOs and calculates compliance."""

    def __init__(self, org_id: str):
        self.org_id = org_id

    def create_slo(
        self,
        name: str,
        service: str,
        sli_type: SLIType,
        target: float,
        window: SLOWindow = SLOWindow.ROLLING_30D,
        description: str = "",
        team_id: str = "",
        owner: str = "",
        labels: dict[str, str] | None = None,
        alert_burn_rate_threshold: float = 1.0,
    ) -> SLO:
        """Create a new SLO."""
        slo_id = f"slo_{secrets.token_hex(8)}"

        slo = SLO(
            id=slo_id,
            name=name,
            description=description,
            service=service,
            sli_type=sli_type,
            target=target,
            window=window,
            org_id=self.org_id,
            team_id=team_id,
            owner=owner,
            labels=labels or {},
            alert_burn_rate_threshold=alert_burn_rate_threshold,
        )

        _SLOS[slo_id] = slo
        log.info("Created SLO %s: %s for service %s", slo_id, name, service)

        return slo

    def get_slo(self, slo_id: str) -> SLO | None:
        """Get an SLO by ID."""
        slo = _SLOS.get(slo_id)
        if slo and slo.org_id == self.org_id:
            return slo
        return None

    def list_slos(
        self,
        service: str | None = None,
        team_id: str | None = None,
    ) -> list[SLO]:
        """List all SLOs."""
        slos = [s for s in _SLOS.values() if s.org_id == self.org_id]

        if service:
            slos = [s for s in slos if s.service == service]

        if team_id:
            slos = [s for s in slos if s.team_id == team_id]

        return slos

    def update_slo(self, slo_id: str, **updates: Any) -> SLO | None:
        """Update an SLO."""
        slo = self.get_slo(slo_id)
        if not slo:
            return None

        for key, value in updates.items():
            if hasattr(slo, key):
                setattr(slo, key, value)

        slo.updated_at = datetime.now(timezone.utc)
        return slo

    def delete_slo(self, slo_id: str) -> bool:
        """Delete an SLO."""
        slo = self.get_slo(slo_id)
        if not slo:
            return False

        del _SLOS[slo_id]
        if slo_id in _SLI_DATA:
            del _SLI_DATA[slo_id]

        log.info("Deleted SLO %s", slo_id)
        return True

    def record_sli(
        self,
        slo_id: str,
        good_count: int,
        total_count: int,
        timestamp: datetime | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SLI | None:
        """Record an SLI measurement."""
        slo = self.get_slo(slo_id)
        if not slo:
            return None

        if timestamp is None:
            timestamp = datetime.now(timezone.utc)

        value = good_count / total_count if total_count > 0 else 1.0

        sli = SLI(
            id=f"sli_{secrets.token_hex(8)}",
            slo_id=slo_id,
            timestamp=timestamp,
            value=value,
            good_count=good_count,
            total_count=total_count,
            metadata=metadata or {},
        )

        _SLI_DATA[slo_id].append(sli)

        max_age = slo.get_window_duration() * 2
        cutoff = datetime.now(timezone.utc) - max_age
        _SLI_DATA[slo_id] = [
            s for s in _SLI_DATA[slo_id] if s.timestamp > cutoff
        ]

        return sli

    def get_sli_data(
        self,
        slo_id: str,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> list[SLI]:
        """Get SLI measurements for an SLO."""
        data = _SLI_DATA.get(slo_id, [])

        if start_time:
            data = [s for s in data if s.timestamp >= start_time]

        if end_time:
            data = [s for s in data if s.timestamp <= end_time]

        return sorted(data, key=lambda s: s.timestamp)

    def calculate_error_budget(self, slo_id: str) -> ErrorBudget | None:
        """Calculate error budget for an SLO."""
        slo = self.get_slo(slo_id)
        if not slo:
            return None

        now = datetime.now(timezone.utc)
        window_duration = slo.get_window_duration()
        window_start = now - window_duration
        window_end = now

        data = self.get_sli_data(slo_id, window_start, window_end)

        if not data:
            return ErrorBudget(
                slo_id=slo_id,
                total_budget=1 - slo.target,
                consumed=0,
                remaining=1 - slo.target,
                remaining_percent=100.0,
                burn_rate=0,
                projected_exhaustion=None,
                status=SLOStatus.NO_DATA,
                window_start=window_start,
                window_end=window_end,
            )

        total_good = sum(s.good_count for s in data)
        total_count = sum(s.total_count for s in data)
        current_sli = total_good / total_count if total_count > 0 else 1.0

        total_budget = 1 - slo.target
        consumed = max(0, slo.target - current_sli)
        remaining = max(0, total_budget - consumed)
        remaining_percent = (remaining / total_budget * 100) if total_budget > 0 else 100

        window_elapsed = (now - window_start).total_seconds()
        window_total = window_duration.total_seconds()
        expected_consumption = (window_elapsed / window_total) * total_budget if window_total > 0 else 0
        burn_rate = consumed / expected_consumption if expected_consumption > 0 else 0

        projected_exhaustion = None
        if burn_rate > 0 and remaining > 0:
            time_to_exhaust = (remaining / (consumed / window_elapsed)) if consumed > 0 else float("inf")
            if time_to_exhaust < float("inf"):
                projected_exhaustion = now + timedelta(seconds=time_to_exhaust)

        if remaining_percent <= 0:
            status = SLOStatus.BREACHED
        elif remaining_percent < 20 or burn_rate > slo.alert_burn_rate_threshold:
            status = SLOStatus.AT_RISK
        else:
            status = SLOStatus.HEALTHY

        return ErrorBudget(
            slo_id=slo_id,
            total_budget=total_budget,
            consumed=consumed,
            remaining=remaining,
            remaining_percent=remaining_percent,
            burn_rate=burn_rate,
            projected_exhaustion=projected_exhaustion,
            status=status,
            window_start=window_start,
            window_end=window_end,
            good_count=total_good,
            total_count=total_count,
            current_sli=current_sli,
        )

    def calculate_burn_rate(
        self,
        slo_id: str,
        short_window: timedelta = timedelta(hours=1),
        long_window: timedelta = timedelta(hours=6),
    ) -> BurnRate | None:
        """Calculate multi-window burn rate for alerting."""
        slo = self.get_slo(slo_id)
        if not slo:
            return None

        now = datetime.now(timezone.utc)

        short_data = self.get_sli_data(slo_id, now - short_window, now)
        long_data = self.get_sli_data(slo_id, now - long_window, now)

        def calc_burn(data: list[SLI], window: timedelta) -> float:
            if not data:
                return 0.0
            total_good = sum(s.good_count for s in data)
            total_count = sum(s.total_count for s in data)
            if total_count == 0:
                return 0.0

            current_sli = total_good / total_count
            error_rate = 1 - current_sli
            allowed_error = 1 - slo.target

            slo_window = slo.get_window_duration()
            time_fraction = window.total_seconds() / slo_window.total_seconds()
            expected_error = allowed_error * time_fraction

            return error_rate / expected_error if expected_error > 0 else 0

        short_burn = calc_burn(short_data, short_window)
        long_burn = calc_burn(long_data, long_window)

        is_alerting = False
        severity = "none"
        message = "SLO is healthy"

        if short_burn > 14.4 and long_burn > 14.4:
            is_alerting = True
            severity = "critical"
            message = f"Critical: 2% of error budget consumed in 1 hour (burn rate: {short_burn:.1f}x)"
        elif short_burn > 6 and long_burn > 6:
            is_alerting = True
            severity = "high"
            message = f"High: 5% of error budget consumed in 6 hours (burn rate: {long_burn:.1f}x)"
        elif short_burn > 3 and long_burn > 1:
            is_alerting = True
            severity = "medium"
            message = f"Medium: Elevated burn rate detected ({long_burn:.1f}x)"
        elif long_burn > 1:
            severity = "low"
            message = f"Low: Slightly elevated burn rate ({long_burn:.1f}x)"

        return BurnRate(
            slo_id=slo_id,
            short_window=f"{int(short_window.total_seconds() / 3600)}h",
            short_window_burn_rate=short_burn,
            long_window=f"{int(long_window.total_seconds() / 3600)}h",
            long_window_burn_rate=long_burn,
            is_alerting=is_alerting,
            severity=severity,
            message=message,
        )

    def get_slo_status(self, slo_id: str) -> dict[str, Any] | None:
        """Get comprehensive SLO status."""
        slo = self.get_slo(slo_id)
        if not slo:
            return None

        error_budget = self.calculate_error_budget(slo_id)
        burn_rate = self.calculate_burn_rate(slo_id)

        return {
            "slo": {
                "id": slo.id,
                "name": slo.name,
                "service": slo.service,
                "target": slo.target,
                "window": slo.window.value,
                "sli_type": slo.sli_type.value,
            },
            "error_budget": {
                "total": error_budget.total_budget if error_budget else 0,
                "consumed": error_budget.consumed if error_budget else 0,
                "remaining": error_budget.remaining if error_budget else 0,
                "remaining_percent": error_budget.remaining_percent if error_budget else 0,
                "status": error_budget.status.value if error_budget else "no_data",
            },
            "burn_rate": {
                "current": burn_rate.long_window_burn_rate if burn_rate else 0,
                "is_alerting": burn_rate.is_alerting if burn_rate else False,
                "severity": burn_rate.severity if burn_rate else "none",
            },
            "current_sli": error_budget.current_sli if error_budget else 0,
            "total_events": error_budget.total_count if error_budget else 0,
            "good_events": error_budget.good_count if error_budget else 0,
        }

    def generate_report(
        self,
        slo_id: str,
        start_time: datetime,
        end_time: datetime,
    ) -> SLOReport | None:
        """Generate an SLO compliance report."""
        slo = self.get_slo(slo_id)
        if not slo:
            return None

        data = self.get_sli_data(slo_id, start_time, end_time)

        total_good = sum(s.good_count for s in data)
        total_count = sum(s.total_count for s in data)
        achieved = total_good / total_count if total_count > 0 else 1.0
        compliant = achieved >= slo.target

        error_budget_consumed = max(0, (slo.target - achieved) / (1 - slo.target)) if slo.target < 1 else 0

        bad_events = total_count - total_good
        downtime_minutes = int(bad_events * 60 / (total_count / ((end_time - start_time).total_seconds() / 60))) if total_count > 0 else 0

        daily_values: list[dict[str, Any]] = []
        current = start_time
        while current < end_time:
            day_end = min(current + timedelta(days=1), end_time)
            day_data = [s for s in data if current <= s.timestamp < day_end]

            day_good = sum(s.good_count for s in day_data)
            day_total = sum(s.total_count for s in day_data)
            day_value = day_good / day_total if day_total > 0 else None

            daily_values.append({
                "date": current.date().isoformat(),
                "value": day_value,
                "good": day_good,
                "total": day_total,
                "compliant": day_value is not None and day_value >= slo.target,
            })
            current = day_end

        return SLOReport(
            slo_id=slo_id,
            slo_name=slo.name,
            service=slo.service,
            period_start=start_time,
            period_end=end_time,
            target=slo.target,
            achieved=achieved,
            compliant=compliant,
            error_budget_consumed=error_budget_consumed,
            total_events=total_count,
            good_events=total_good,
            bad_events=bad_events,
            downtime_minutes=downtime_minutes,
            daily_values=daily_values,
        )


_trackers: dict[str, SLOTracker] = {}


def _get_tracker(org_id: str) -> SLOTracker:
    """Get or create SLO tracker for org."""
    if org_id not in _trackers:
        _trackers[org_id] = SLOTracker(org_id)
    return _trackers[org_id]


def create_slo(
    org_id: str,
    name: str,
    service: str,
    sli_type: SLIType,
    target: float,
    **kwargs: Any,
) -> SLO:
    """Create a new SLO."""
    tracker = _get_tracker(org_id)
    return tracker.create_slo(name, service, sli_type, target, **kwargs)


def get_slo(org_id: str, slo_id: str) -> SLO | None:
    """Get an SLO by ID."""
    tracker = _get_tracker(org_id)
    return tracker.get_slo(slo_id)


def list_slos(org_id: str, **kwargs: Any) -> list[SLO]:
    """List all SLOs."""
    tracker = _get_tracker(org_id)
    return tracker.list_slos(**kwargs)


def record_sli(
    org_id: str,
    slo_id: str,
    good_count: int,
    total_count: int,
    **kwargs: Any,
) -> SLI | None:
    """Record an SLI measurement."""
    tracker = _get_tracker(org_id)
    return tracker.record_sli(slo_id, good_count, total_count, **kwargs)


def calculate_error_budget(org_id: str, slo_id: str) -> ErrorBudget | None:
    """Calculate error budget for an SLO."""
    tracker = _get_tracker(org_id)
    return tracker.calculate_error_budget(slo_id)


def get_slo_status(org_id: str, slo_id: str) -> dict[str, Any] | None:
    """Get comprehensive SLO status."""
    tracker = _get_tracker(org_id)
    return tracker.get_slo_status(slo_id)


def get_burn_rate(org_id: str, slo_id: str) -> BurnRate | None:
    """Get burn rate for an SLO."""
    tracker = _get_tracker(org_id)
    return tracker.calculate_burn_rate(slo_id)


def get_slo_report(
    org_id: str,
    slo_id: str,
    start_time: datetime,
    end_time: datetime,
) -> SLOReport | None:
    """Generate an SLO report."""
    tracker = _get_tracker(org_id)
    return tracker.generate_report(slo_id, start_time, end_time)
