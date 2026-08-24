"""SLO/SLI tracking implementation.

Implements Google SRE-style SLO tracking with:
- Multiple SLI types (availability, latency, throughput, etc.)
- Rolling and calendar-based windows
- Error budget calculations
- Multi-window burn rate alerts

Definitions live in `slos` and measurements in `sli_measurements`. They were two
module-level dicts, `_SLOS` and `_SLI_DATA`: a restart deleted every objective a
customer had defined along with the entire measurement series behind it, so the
error budget reset to "no data" and the burn-rate alerts went quiet exactly when
the deploy that caused the burn happened. With more than one worker each process
had its own set, so an SLO existed only for the worker that answered the request
that created it.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from sqlalchemy import delete, select

from app.database.orgs import ensure_org, org_uuid_for

log = logging.getLogger("omnipotent.slo.tracker")


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


# The fields `update_slo` is allowed to write. The key comes from the caller and
# now reaches a column; the dict version fed it to setattr() behind a hasattr()
# guard, which also let a caller overwrite `id`, `org_id` and `created_at`.
_UPDATABLE_FIELDS = frozenset(
    {
        "name",
        "description",
        "service",
        "target",
        "team_id",
        "owner",
        "labels",
        "alert_burn_rate_threshold",
        "alert_channels",
    }
)


def _as_utc(value: datetime) -> datetime:
    """Normalize a timestamp to UTC before it is stored.

    SQLAlchemy's SQLite DATETIME writes the wall clock and drops the offset, so
    a measurement timestamped 09:00+05:30 would come back as 09:00 UTC — four
    and a half hours in the future, inside the wrong burn-rate window. Naive
    input is taken as UTC, which is what the old in-memory comparisons assumed.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _utc(value: datetime) -> datetime:
    """Read a timestamp back as UTC-aware.

    SQLite has no timezone type, so a `DateTime(timezone=True)` column round
    trips to a naive datetime, and every comparison here is against
    `datetime.now(timezone.utc)` — naive-vs-aware is a TypeError, i.e. a 500 on
    every error-budget and burn-rate request under the default database URL.
    """
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


class SLOTracker:
    """Tracks SLOs and calculates compliance."""

    def __init__(self, org_id: str):
        self.org_id = org_id
        self._org = org_uuid_for(org_id)

    # --- row <-> dataclass ----------------------------------------------------

    def _to_slo(self, row: Any) -> SLO:
        return SLO(
            id=row.slo_id,
            name=row.name,
            description=row.description,
            service=row.service,
            sli_type=SLIType(row.sli_type),
            target=row.target,
            window=SLOWindow(row.window),
            org_id=self.org_id,
            team_id=row.team_id,
            owner=row.owner,
            created_at=_utc(row.created_at),
            updated_at=_utc(row.updated_at),
            labels=dict(row.labels or {}),
            alert_burn_rate_threshold=row.alert_burn_rate_threshold,
            alert_channels=list(row.alert_channels or []),
            metadata=dict(row.meta or {}),
        )

    @staticmethod
    def _to_sli(row: Any) -> SLI:
        return SLI(
            id=row.sli_id,
            slo_id=row.slo_id,
            timestamp=_utc(row.timestamp),
            value=row.value,
            good_count=row.good_count,
            total_count=row.total_count,
            metadata=dict(row.meta or {}),
        )

    def _select_slo(self, slo_id: str) -> Any:
        from app.database.models import SLO as SLORow

        return select(SLORow).where(
            SLORow.org_id == self._org, SLORow.slo_id == slo_id
        )

    # --- SLO definitions ------------------------------------------------------

    async def create_slo(
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
        from app.database.engine import get_session
        from app.database.models import SLO as SLORow

        slo_id = f"slo_{secrets.token_hex(8)}"
        now = datetime.now(timezone.utc)

        async with get_session() as session:
            await ensure_org(session, self._org)
            session.add(
                SLORow(
                    org_id=self._org,
                    slo_id=slo_id,
                    name=name,
                    description=description,
                    service=service,
                    sli_type=sli_type.value,
                    target=target,
                    window=window.value,
                    team_id=team_id,
                    owner=owner,
                    labels=dict(labels or {}),
                    alert_burn_rate_threshold=alert_burn_rate_threshold,
                    alert_channels=[],
                    meta={},
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.commit()

        log.info("Created SLO %s: %s for service %s", slo_id, name, service)

        return SLO(
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
            created_at=now,
            updated_at=now,
            labels=dict(labels or {}),
            alert_burn_rate_threshold=alert_burn_rate_threshold,
        )

    async def get_slo(self, slo_id: str) -> SLO | None:
        """Get an SLO by ID."""
        from app.database.engine import get_session

        async with get_session() as session:
            row = (await session.execute(self._select_slo(slo_id))).scalar_one_or_none()
            return self._to_slo(row) if row else None

    async def list_slos(
        self,
        service: str | None = None,
        team_id: str | None = None,
    ) -> list[SLO]:
        """List all SLOs."""
        from app.database.engine import get_session
        from app.database.models import SLO as SLORow

        stmt = (
            select(SLORow)
            .where(SLORow.org_id == self._org)
            # `slo_id` breaks ties: several SLOs can be created in the same
            # second and the frontend renders them in the order it receives.
            .order_by(SLORow.created_at, SLORow.slo_id)
        )
        if service:
            stmt = stmt.where(SLORow.service == service)
        if team_id:
            stmt = stmt.where(SLORow.team_id == team_id)

        async with get_session() as session:
            rows = (await session.execute(stmt)).scalars().all()
            return [self._to_slo(row) for row in rows]

    async def update_slo(self, slo_id: str, **updates: Any) -> SLO | None:
        """Update an SLO. Fields outside `_UPDATABLE_FIELDS` are ignored."""
        from app.database.engine import get_session

        async with get_session() as session:
            row = (await session.execute(self._select_slo(slo_id))).scalar_one_or_none()
            if row is None:
                return None

            for key, value in updates.items():
                if key in _UPDATABLE_FIELDS:
                    setattr(row, key, value)

            row.updated_at = datetime.now(timezone.utc)
            await session.commit()
            return self._to_slo(row)

    async def delete_slo(self, slo_id: str) -> bool:
        """Delete an SLO and the measurement series behind it."""
        from app.database.engine import get_session
        from app.database.models import SLIMeasurement

        async with get_session() as session:
            row = (await session.execute(self._select_slo(slo_id))).scalar_one_or_none()
            if row is None:
                return False

            await session.execute(
                delete(SLIMeasurement).where(
                    SLIMeasurement.org_id == self._org,
                    SLIMeasurement.slo_id == slo_id,
                )
            )
            await session.delete(row)
            await session.commit()

        log.info("Deleted SLO %s", slo_id)
        return True

    # --- measurements ---------------------------------------------------------

    async def record_sli(
        self,
        slo_id: str,
        good_count: int,
        total_count: int,
        timestamp: datetime | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SLI | None:
        """Record an SLI measurement."""
        from app.database.engine import get_session
        from app.database.models import SLIMeasurement

        slo = await self.get_slo(slo_id)
        if not slo:
            return None

        stamp = _as_utc(timestamp) if timestamp is not None else datetime.now(timezone.utc)
        value = good_count / total_count if total_count > 0 else 1.0
        sli_id = f"sli_{secrets.token_hex(8)}"

        async with get_session() as session:
            await ensure_org(session, self._org)
            session.add(
                SLIMeasurement(
                    org_id=self._org,
                    slo_id=slo_id,
                    sli_id=sli_id,
                    timestamp=stamp,
                    value=value,
                    good_count=good_count,
                    total_count=total_count,
                    meta=dict(metadata or {}),
                )
            )
            # Retention, as the dict version had: twice the SLO window is the
            # most any calculation here looks back. It matters more now that the
            # rows outlive the process.
            cutoff = datetime.now(timezone.utc) - slo.get_window_duration() * 2
            await session.execute(
                delete(SLIMeasurement).where(
                    SLIMeasurement.org_id == self._org,
                    SLIMeasurement.slo_id == slo_id,
                    SLIMeasurement.timestamp <= cutoff,
                )
            )
            await session.commit()

        return SLI(
            id=sli_id,
            slo_id=slo_id,
            timestamp=stamp,
            value=value,
            good_count=good_count,
            total_count=total_count,
            metadata=dict(metadata or {}),
        )

    async def get_sli_data(
        self,
        slo_id: str,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> list[SLI]:
        """Get SLI measurements for an SLO."""
        from app.database.engine import get_session
        from app.database.models import SLIMeasurement

        # Via get_slo, not the measurement table directly: an id-only lookup
        # returned another tenant's measurement series to anyone holding the id.
        if await self.get_slo(slo_id) is None:
            return []

        stmt = (
            select(SLIMeasurement)
            .where(
                SLIMeasurement.org_id == self._org,
                SLIMeasurement.slo_id == slo_id,
            )
            .order_by(SLIMeasurement.timestamp)
        )
        if start_time:
            stmt = stmt.where(SLIMeasurement.timestamp >= _as_utc(start_time))
        if end_time:
            stmt = stmt.where(SLIMeasurement.timestamp <= _as_utc(end_time))

        async with get_session() as session:
            rows = (await session.execute(stmt)).scalars().all()
            return [self._to_sli(row) for row in rows]

    # --- calculations ---------------------------------------------------------

    async def calculate_error_budget(self, slo_id: str) -> ErrorBudget | None:
        """Calculate error budget for an SLO."""
        slo = await self.get_slo(slo_id)
        if not slo:
            return None

        now = datetime.now(timezone.utc)
        window_duration = slo.get_window_duration()
        window_start = now - window_duration
        window_end = now

        data = await self.get_sli_data(slo_id, window_start, window_end)

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

    async def calculate_burn_rate(
        self,
        slo_id: str,
        short_window: timedelta = timedelta(hours=1),
        long_window: timedelta = timedelta(hours=6),
    ) -> BurnRate | None:
        """Calculate multi-window burn rate for alerting."""
        slo = await self.get_slo(slo_id)
        if not slo:
            return None

        now = datetime.now(timezone.utc)

        short_data = await self.get_sli_data(slo_id, now - short_window, now)
        long_data = await self.get_sli_data(slo_id, now - long_window, now)

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

    async def get_slo_status(self, slo_id: str) -> dict[str, Any] | None:
        """Get comprehensive SLO status."""
        slo = await self.get_slo(slo_id)
        if not slo:
            return None

        error_budget = await self.calculate_error_budget(slo_id)
        burn_rate = await self.calculate_burn_rate(slo_id)

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

    async def generate_report(
        self,
        slo_id: str,
        start_time: datetime,
        end_time: datetime,
    ) -> SLOReport | None:
        """Generate an SLO compliance report."""
        slo = await self.get_slo(slo_id)
        if not slo:
            return None

        start_time = _as_utc(start_time)
        end_time = _as_utc(end_time)

        data = await self.get_sli_data(slo_id, start_time, end_time)

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


# `SLOTracker` holds nothing but the org id now — its state is in the database —
# so these build one per call rather than caching instances in a module dict.


async def create_slo(
    org_id: str,
    name: str,
    service: str,
    sli_type: SLIType,
    target: float,
    **kwargs: Any,
) -> SLO:
    """Create a new SLO."""
    return await SLOTracker(org_id).create_slo(name, service, sli_type, target, **kwargs)


async def get_slo(org_id: str, slo_id: str) -> SLO | None:
    """Get an SLO by ID."""
    return await SLOTracker(org_id).get_slo(slo_id)


async def list_slos(org_id: str, **kwargs: Any) -> list[SLO]:
    """List all SLOs."""
    return await SLOTracker(org_id).list_slos(**kwargs)


async def record_sli(
    org_id: str,
    slo_id: str,
    good_count: int,
    total_count: int,
    **kwargs: Any,
) -> SLI | None:
    """Record an SLI measurement."""
    return await SLOTracker(org_id).record_sli(slo_id, good_count, total_count, **kwargs)


async def calculate_error_budget(org_id: str, slo_id: str) -> ErrorBudget | None:
    """Calculate error budget for an SLO."""
    return await SLOTracker(org_id).calculate_error_budget(slo_id)


async def get_slo_status(org_id: str, slo_id: str) -> dict[str, Any] | None:
    """Get comprehensive SLO status."""
    return await SLOTracker(org_id).get_slo_status(slo_id)


async def get_burn_rate(org_id: str, slo_id: str) -> BurnRate | None:
    """Get burn rate for an SLO."""
    return await SLOTracker(org_id).calculate_burn_rate(slo_id)


async def get_slo_report(
    org_id: str,
    slo_id: str,
    start_time: datetime,
    end_time: datetime,
) -> SLOReport | None:
    """Generate an SLO report."""
    return await SLOTracker(org_id).generate_report(slo_id, start_time, end_time)
