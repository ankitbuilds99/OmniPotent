"""Dashboard metrics and ROI calculation.

Provides:
- Investigation success metrics
- MTTR (Mean Time To Resolution) tracking
- Cost savings calculation
- Usage analytics

Persistence
-----------
Investigation history lives in the `investigations` table (see
app/database/models.py), not in this process. It was previously a module-level
`_investigation_store: dict[str, list[InvestigationRecord]]`, which meant the
dashboard reported on whichever pod happened to serve the request and reset to
zero on every restart, deploy and scale-down.

`record_investigation` and `get_dashboard_metrics` are therefore coroutines.
The dataclasses below are detached read models: mutating one does not write
anything back.

Honesty constraints
-------------------
Two numbers here are reported to a user as fact and so may never be invented:

- `MTTRStats.improvement_percent` is only meaningful against a previous period
  that was actually measured. Where there is none, `comparison_available` is
  False and the improvement is not reported — it is not filled in from an
  assumed industry baseline.
- `ROIMetrics.estimated_cost_savings_usd` is derived from `time_saved_minutes`,
  which only a caller that measured a before/after can supply. It stays 0.0
  otherwise, which is why a fresh deployment shows no ROI rather than a
  flattering one.
"""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncGenerator
from enum import Enum

from sqlalchemy import Float, cast, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.engine import ensure_schema, get_session
from app.database.models import Investigation

log = logging.getLogger("omnipotent.dashboard.metrics")


class InvestigationOutcome(str, Enum):
    """Possible investigation outcomes."""

    ROOT_CAUSE_FOUND = "root_cause_found"
    PARTIAL_DIAGNOSIS = "partial_diagnosis"
    ESCALATED = "escalated"
    RESOLVED = "resolved"
    ABSTAINED = "abstained"
    FAILED = "failed"


@dataclass
class InvestigationRecord:
    """Record of a single investigation."""

    id: str
    started_at: datetime
    completed_at: datetime | None = None
    outcome: InvestigationOutcome = InvestigationOutcome.PARTIAL_DIAGNOSIS
    duration_seconds: float = 0.0
    tokens_used: int = 0
    severity: str = "unknown"
    service: str = ""
    hypotheses_generated: int = 0
    tools_used: list[str] = field(default_factory=list)
    user_feedback: str | None = None
    time_saved_minutes: float = 0.0


@dataclass
class MTTRStats:
    """Mean Time To Resolution statistics.

    `comparison_available` is the important field. MTTR "improvement" is a
    comparison against the previous period, and a first-ever period has no
    previous period to compare against. This used to be papered over with a
    45-minute default baseline: a brand-new deployment whose three
    investigations averaged 10 minutes was told MTTR had "improved by 78%"
    against a number nobody had measured, and that invented improvement was
    worth 25 of the 100 health-score points. When there is no measured previous
    period the comparison is now simply not made.
    """

    current_mttr_minutes: float = 0.0
    previous_mttr_minutes: float = 0.0
    improvement_percent: float = 0.0
    fastest_resolution_minutes: float = 0.0
    slowest_resolution_minutes: float = 0.0
    p50_minutes: float = 0.0
    p90_minutes: float = 0.0
    p99_minutes: float = 0.0
    sample_size: int = 0
    # False when `previous_mttr_minutes` was not measured from real prior-period
    # investigations. Consumers must not render an improvement figure when this
    # is False.
    comparison_available: bool = False

    @property
    def improved(self) -> bool:
        """Whether MTTR has measurably improved.

        False when there is nothing to compare against — "we cannot tell" is not
        the same as "no improvement", but it is much closer than "improved".
        """
        return self.comparison_available and self.improvement_percent > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "current_mttr_minutes": round(self.current_mttr_minutes, 1),
            "previous_mttr_minutes": round(self.previous_mttr_minutes, 1),
            "improvement_percent": (
                round(self.improvement_percent, 1) if self.comparison_available else None
            ),
            "comparison_available": self.comparison_available,
            "fastest_resolution_minutes": round(self.fastest_resolution_minutes, 1),
            "slowest_resolution_minutes": round(self.slowest_resolution_minutes, 1),
            "p50_minutes": round(self.p50_minutes, 1),
            "p90_minutes": round(self.p90_minutes, 1),
            "p99_minutes": round(self.p99_minutes, 1),
            "sample_size": self.sample_size,
            "improved": self.improved,
        }


@dataclass
class ROIMetrics:
    """Return on Investment metrics."""

    time_saved_hours: float = 0.0
    investigations_completed: int = 0
    avg_time_saved_per_investigation_minutes: float = 0.0
    estimated_cost_savings_usd: float = 0.0
    engineer_hours_recovered: float = 0.0
    downtime_prevented_minutes: float = 0.0
    incidents_auto_resolved: int = 0
    escalations_prevented: int = 0

    hourly_engineer_cost_usd: float = 150.0
    downtime_cost_per_minute_usd: float = 500.0

    def calculate_savings(self) -> None:
        """Calculate cost savings based on time saved."""
        self.estimated_cost_savings_usd = (
            self.time_saved_hours * self.hourly_engineer_cost_usd
            + self.downtime_prevented_minutes * self.downtime_cost_per_minute_usd
        )

        if self.investigations_completed > 0:
            self.avg_time_saved_per_investigation_minutes = (
                self.time_saved_hours * 60 / self.investigations_completed
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "time_saved_hours": round(self.time_saved_hours, 1),
            "investigations_completed": self.investigations_completed,
            "avg_time_saved_per_investigation_minutes": round(
                self.avg_time_saved_per_investigation_minutes, 1
            ),
            "estimated_cost_savings_usd": round(self.estimated_cost_savings_usd, 2),
            "engineer_hours_recovered": round(self.engineer_hours_recovered, 1),
            "downtime_prevented_minutes": round(self.downtime_prevented_minutes, 1),
            "incidents_auto_resolved": self.incidents_auto_resolved,
            "escalations_prevented": self.escalations_prevented,
        }


@dataclass
class UsageStats:
    """Usage statistics."""

    total_investigations: int = 0
    investigations_this_period: int = 0
    tokens_used: int = 0
    tokens_used_this_period: int = 0
    active_users: int = 0
    investigations_by_severity: dict[str, int] = field(default_factory=dict)
    investigations_by_service: dict[str, int] = field(default_factory=dict)
    investigations_by_outcome: dict[str, int] = field(default_factory=dict)
    top_tools_used: list[tuple[str, int]] = field(default_factory=list)
    avg_investigation_duration_seconds: float = 0.0
    avg_tokens_per_investigation: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_investigations": self.total_investigations,
            "investigations_this_period": self.investigations_this_period,
            "tokens_used": self.tokens_used,
            "tokens_used_this_period": self.tokens_used_this_period,
            "active_users": self.active_users,
            "investigations_by_severity": self.investigations_by_severity,
            "investigations_by_service": self.investigations_by_service,
            "investigations_by_outcome": self.investigations_by_outcome,
            "top_tools_used": self.top_tools_used,
            "avg_investigation_duration_seconds": round(
                self.avg_investigation_duration_seconds, 1
            ),
            "avg_tokens_per_investigation": round(self.avg_tokens_per_investigation, 0),
        }


@dataclass
class InvestigationSummary:
    """Summary of recent investigations."""

    total: int = 0
    successful: int = 0
    partial: int = 0
    failed: int = 0
    abstained: int = 0

    success_rate: float = 0.0
    abstention_rate: float = 0.0

    avg_duration_seconds: float = 0.0
    avg_hypotheses: float = 0.0

    recent_investigations: list[dict[str, Any]] = field(default_factory=list)

    def calculate_rates(self) -> None:
        """Calculate success and abstention rates."""
        if self.total > 0:
            self.success_rate = self.successful / self.total
            self.abstention_rate = self.abstained / self.total

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "successful": self.successful,
            "partial": self.partial,
            "failed": self.failed,
            "abstained": self.abstained,
            "success_rate": round(self.success_rate, 3),
            "abstention_rate": round(self.abstention_rate, 3),
            "avg_duration_seconds": round(self.avg_duration_seconds, 1),
            "avg_hypotheses": round(self.avg_hypotheses, 1),
            "recent_investigations": self.recent_investigations[:10],
        }


@dataclass
class DashboardMetrics:
    """Complete dashboard metrics."""

    org_id: str = ""
    generated_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    period_start: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc) - timedelta(days=30)
    )
    period_end: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    mttr: MTTRStats = field(default_factory=MTTRStats)
    roi: ROIMetrics = field(default_factory=ROIMetrics)
    usage: UsageStats = field(default_factory=UsageStats)
    summary: InvestigationSummary = field(default_factory=InvestigationSummary)

    health_score: float = 0.0
    # Points that were actually available to be scored. The MTTR band (25 points)
    # can only be judged against a measured previous period, so on a first period
    # the maximum is 75, not 100 — reporting 60/100 when 25 points were
    # unscoreable reads as "poor" rather than "not enough data yet".
    health_score_max: float = 100.0

    def calculate_health_score(self) -> None:
        """Calculate overall health score, out of `health_score_max`."""
        score = 0.0
        available = 100.0

        if self.summary.success_rate > 0.8:
            score += 30
        elif self.summary.success_rate > 0.6:
            score += 20
        elif self.summary.success_rate > 0.4:
            score += 10

        # Only score the MTTR trend when there is a measured previous period.
        # Scoring it otherwise awarded 15-25 points off an assumed baseline —
        # points every new deployment got for free. See MTTRStats.
        if self.mttr.comparison_available:
            if self.mttr.improved:
                score += 25
            elif self.mttr.improvement_percent > -10:
                score += 15
        else:
            available -= 25

        if self.summary.abstention_rate < 0.1:
            score += 20
        elif self.summary.abstention_rate < 0.2:
            score += 10

        if self.usage.investigations_this_period > 10:
            score += 15
        elif self.usage.investigations_this_period > 5:
            score += 10

        if self.roi.time_saved_hours > 0:
            score += 10

        self.health_score = min(available, score)
        self.health_score_max = available

    def to_dict(self) -> dict[str, Any]:
        return {
            "org_id": self.org_id,
            "generated_at": self.generated_at.isoformat(),
            "period": {
                "start": self.period_start.isoformat(),
                "end": self.period_end.isoformat(),
            },
            "health_score": round(self.health_score, 0),
            "health_score_max": round(self.health_score_max, 0),
            "mttr": self.mttr.to_dict(),
            "roi": self.roi.to_dict(),
            "usage": self.usage.to_dict(),
            "summary": self.summary.to_dict(),
        }


@asynccontextmanager
async def _session() -> AsyncGenerator[AsyncSession, None]:
    """A session against the configured database, schema guaranteed to exist."""
    await ensure_schema()
    async with get_session() as session:
        yield session


def _aware(dt: datetime) -> datetime:
    """Re-attach UTC to a datetime SQLite handed back naive.

    SQLite has no timezone-aware type: SQLAlchemy writes the UTC wall clock and
    reads it back with tzinfo stripped. Comparing that to an aware `now()` raises
    TypeError, so every datetime leaving the database goes through here.
    """
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _to_record(row: Investigation) -> InvestigationRecord:
    """Map a row to the detached read model callers see."""
    try:
        outcome = InvestigationOutcome(row.outcome)
    except ValueError:
        # An outcome written by a newer version, or hand-edited. Don't crash the
        # whole dashboard over one row.
        log.warning("unknown investigation outcome %r on %s", row.outcome, row.id)
        outcome = InvestigationOutcome.PARTIAL_DIAGNOSIS

    return InvestigationRecord(
        id=row.thread_id,
        started_at=_aware(row.started_at),
        completed_at=_aware(row.completed_at) if row.completed_at else None,
        outcome=outcome,
        duration_seconds=row.duration_seconds,
        tokens_used=row.tokens_used,
        severity=row.severity,
        service=row.service,
        hypotheses_generated=row.hypotheses_generated,
        tools_used=list(row.tools_used or []),
        user_feedback=row.feedback_comment,
        time_saved_minutes=row.time_saved_minutes,
    )


async def record_investigation(
    org_id: str,
    investigation_id: str,
    started_at: datetime,
    completed_at: datetime,
    outcome: InvestigationOutcome,
    tokens_used: int = 0,
    severity: str = "unknown",
    service: str = "",
    hypotheses_generated: int = 0,
    tools_used: list[str] | None = None,
    time_saved_minutes: float = 0.0,
) -> InvestigationRecord:
    """Record a completed investigation.

    Upserts on (org_id, investigation_id): an investigation is a thread, and a
    thread can complete several turns. Each completion updates the one row rather
    than appending another, so a ten-turn conversation counts as one
    investigation. The previous dict-backed version appended per turn, which
    inflated every total, every "investigations completed" figure, and the ROI
    derived from them.

    Token counts accumulate across turns; duration and outcome describe the
    investigation as a whole and are taken from the latest completion.
    """
    duration = (completed_at - started_at).total_seconds()

    async with _session() as session:
        existing = (
            await session.execute(
                select(Investigation).where(
                    Investigation.org_id == org_id,
                    Investigation.thread_id == investigation_id,
                )
            )
        ).scalar_one_or_none()

        if existing is None:
            row = Investigation(
                id=uuid.uuid4(),
                org_id=org_id,
                thread_id=investigation_id,
                status="completed",
                started_at=started_at,
                completed_at=completed_at,
                outcome=outcome.value,
                duration_seconds=duration,
                tokens_used=tokens_used,
                severity=severity,
                service=service,
                hypotheses_generated=hypotheses_generated,
                tools_used=list(tools_used or []),
                time_saved_minutes=time_saved_minutes,
            )
            session.add(row)
        else:
            row = existing
            row.status = "completed"
            # Earliest start wins: the investigation began when its first turn
            # began, and that is what MTTR is measured from.
            if started_at < _aware(row.started_at):
                row.started_at = started_at
            row.completed_at = completed_at
            row.outcome = outcome.value
            row.duration_seconds = (completed_at - _aware(row.started_at)).total_seconds()
            row.tokens_used = row.tokens_used + tokens_used
            row.hypotheses_generated = row.hypotheses_generated + hypotheses_generated
            row.time_saved_minutes = row.time_saved_minutes + time_saved_minutes
            if severity != "unknown":
                row.severity = severity
            if service:
                row.service = service
            if tools_used:
                # Reassigned, not appended to: SQLAlchemy only marks a JSON column
                # dirty on assignment, so in-place mutation is silently discarded.
                merged = list(row.tools_used or [])
                merged.extend(t for t in tools_used if t not in merged)
                row.tools_used = merged

        await session.commit()
        await session.refresh(row)
        return _to_record(row)


async def get_dashboard_metrics(
    org_id: str,
    period_days: int = 30,
    baseline_mttr_minutes: float | None = None,
) -> DashboardMetrics:
    """Get dashboard metrics for an organization.

    Args:
        org_id: Organization ID
        period_days: Number of days to analyze
        baseline_mttr_minutes: An externally supplied MTTR baseline to compare
            against when there is no previous period of real data. Defaults to
            None, meaning no comparison is made. Pass a number only if it was
            actually measured for this organization — it is reported to the user
            as their MTTR improvement.

    Returns:
        DashboardMetrics with all calculated values
    """
    now = datetime.now(timezone.utc)
    period_start = now - timedelta(days=period_days)
    prev_period_start = period_start - timedelta(days=period_days)

    async with _session() as session:
        # Only the current period is loaded as rows — percentiles, per-service
        # breakdowns and the recent list all need individual records. Lifetime
        # figures are aggregated in SQL instead of loading every investigation
        # the org has ever run, and the previous period only needs its mean.
        current_period = [
            _to_record(row)
            for row in (
                await session.execute(
                    select(Investigation)
                    .where(
                        Investigation.org_id == org_id,
                        Investigation.started_at >= period_start,
                    )
                    .order_by(Investigation.started_at.desc())
                )
            ).scalars()
        ]

        prev_count, prev_mean = (
            await session.execute(
                select(
                    func.count(Investigation.id),
                    func.avg(Investigation.duration_seconds),
                ).where(
                    Investigation.org_id == org_id,
                    Investigation.started_at >= prev_period_start,
                    Investigation.started_at < period_start,
                )
            )
        ).one()

        lifetime_count, lifetime_tokens, lifetime_mean_duration = (
            await session.execute(
                select(
                    func.count(Investigation.id),
                    func.coalesce(func.sum(Investigation.tokens_used), 0),
                    func.coalesce(
                        func.avg(cast(Investigation.duration_seconds, Float)), 0.0
                    ),
                ).where(Investigation.org_id == org_id)
            )
        ).one()

    metrics = DashboardMetrics(
        org_id=org_id,
        period_start=period_start,
        period_end=now,
    )

    metrics.summary = _calculate_summary(current_period)
    metrics.mttr = _calculate_mttr(
        current_period,
        previous_mean_seconds=float(prev_mean) if prev_count else None,
        previous_sample_size=int(prev_count),
        baseline=baseline_mttr_minutes,
    )
    metrics.roi = _calculate_roi(current_period)
    metrics.usage = _calculate_usage(
        current_period,
        lifetime_count=int(lifetime_count),
        lifetime_tokens=int(lifetime_tokens),
        lifetime_mean_duration=float(lifetime_mean_duration or 0.0),
    )

    metrics.calculate_health_score()

    return metrics


async def get_investigation_series(
    org_id: str,
    period_days: int = 30,
) -> list[tuple[datetime, float, int]]:
    """Raw (started_at, duration_seconds, tokens_used) for trend bucketing.

    Three columns rather than whole rows: the caller buckets these into days and
    discards everything else, so selecting the report JSON and per-row text would
    be reading a period of history to throw most of it away.

    Ordered oldest-first, which is the order the trend analyzer wants.
    """
    period_start = datetime.now(timezone.utc) - timedelta(days=period_days)

    async with _session() as session:
        rows = (
            await session.execute(
                select(
                    Investigation.started_at,
                    Investigation.duration_seconds,
                    Investigation.tokens_used,
                )
                .where(
                    Investigation.org_id == org_id,
                    Investigation.started_at >= period_start,
                )
                .order_by(Investigation.started_at)
            )
        ).all()

    return [(_aware(started), float(duration), int(tokens)) for started, duration, tokens in rows]


async def clear_investigations(org_id: str | None = None) -> None:
    """Delete recorded investigations. Test/support helper.

    With no org_id this empties the table, so it is deliberately explicit rather
    than something a request path would reach for.
    """
    async with _session() as session:
        stmt = delete(Investigation)
        if org_id is not None:
            stmt = stmt.where(Investigation.org_id == org_id)
        await session.execute(stmt)
        await session.commit()


def _calculate_summary(investigations: list[InvestigationRecord]) -> InvestigationSummary:
    """Calculate investigation summary."""
    summary = InvestigationSummary(total=len(investigations))

    if not investigations:
        return summary

    total_duration = 0.0
    total_hypotheses = 0

    for inv in investigations:
        if inv.outcome == InvestigationOutcome.ROOT_CAUSE_FOUND:
            summary.successful += 1
        elif inv.outcome == InvestigationOutcome.RESOLVED:
            summary.successful += 1
        elif inv.outcome == InvestigationOutcome.PARTIAL_DIAGNOSIS:
            summary.partial += 1
        elif inv.outcome == InvestigationOutcome.ABSTAINED:
            summary.abstained += 1
        else:
            summary.failed += 1

        total_duration += inv.duration_seconds
        total_hypotheses += inv.hypotheses_generated

    summary.avg_duration_seconds = total_duration / len(investigations)
    summary.avg_hypotheses = total_hypotheses / len(investigations)
    summary.calculate_rates()

    summary.recent_investigations = [
        {
            "id": inv.id,
            "started_at": inv.started_at.isoformat(),
            "outcome": inv.outcome.value,
            "duration_seconds": inv.duration_seconds,
            "service": inv.service,
            "severity": inv.severity,
        }
        for inv in sorted(investigations, key=lambda x: x.started_at, reverse=True)[:10]
    ]

    return summary


def _calculate_mttr(
    current: list[InvestigationRecord],
    previous_mean_seconds: float | None,
    previous_sample_size: int,
    baseline: float | None,
) -> MTTRStats:
    """Calculate MTTR statistics.

    `previous_mean_seconds` is None when the previous period contained no
    investigations. In that case the comparison is made only if the caller
    supplied a `baseline` it actually measured; otherwise no improvement figure
    is produced. See MTTRStats for why this is not defaulted.
    """
    stats = MTTRStats()

    if not current:
        return stats

    durations = sorted([inv.duration_seconds / 60 for inv in current])
    stats.sample_size = len(durations)
    stats.current_mttr_minutes = sum(durations) / len(durations)
    stats.fastest_resolution_minutes = min(durations)
    stats.slowest_resolution_minutes = max(durations)

    stats.p50_minutes = durations[len(durations) // 2]
    stats.p90_minutes = durations[min(int(len(durations) * 0.9), len(durations) - 1)]
    stats.p99_minutes = durations[min(int(len(durations) * 0.99), len(durations) - 1)]

    if previous_mean_seconds is not None and previous_sample_size > 0:
        stats.previous_mttr_minutes = previous_mean_seconds / 60
        stats.comparison_available = True
    elif baseline is not None:
        stats.previous_mttr_minutes = baseline
        stats.comparison_available = True

    if stats.comparison_available and stats.previous_mttr_minutes > 0:
        stats.improvement_percent = (
            (stats.previous_mttr_minutes - stats.current_mttr_minutes)
            / stats.previous_mttr_minutes
            * 100
        )

    return stats


def _calculate_roi(investigations: list[InvestigationRecord]) -> ROIMetrics:
    """Calculate ROI metrics."""
    roi = ROIMetrics()

    if not investigations:
        return roi

    roi.investigations_completed = len(investigations)

    total_time_saved_minutes = sum(inv.time_saved_minutes for inv in investigations)
    roi.time_saved_hours = total_time_saved_minutes / 60
    roi.engineer_hours_recovered = roi.time_saved_hours

    roi.incidents_auto_resolved = sum(
        1 for inv in investigations
        if inv.outcome == InvestigationOutcome.RESOLVED
    )

    roi.escalations_prevented = sum(
        1 for inv in investigations
        if inv.outcome in (
            InvestigationOutcome.ROOT_CAUSE_FOUND,
            InvestigationOutcome.RESOLVED,
        )
    )

    roi.calculate_savings()

    return roi


def _calculate_usage(
    current_period: list[InvestigationRecord],
    lifetime_count: int,
    lifetime_tokens: int,
    lifetime_mean_duration: float,
) -> UsageStats:
    """Calculate usage statistics.

    Lifetime figures arrive pre-aggregated from SQL rather than as records. They
    are unbounded by any period, so loading them as rows to sum in Python would
    mean pulling an organization's entire history into memory to produce three
    numbers.
    """
    usage = UsageStats()

    usage.total_investigations = lifetime_count
    usage.investigations_this_period = len(current_period)

    usage.tokens_used = lifetime_tokens
    usage.tokens_used_this_period = sum(inv.tokens_used for inv in current_period)

    if lifetime_count:
        usage.avg_tokens_per_investigation = lifetime_tokens / lifetime_count
        usage.avg_investigation_duration_seconds = lifetime_mean_duration

    for inv in current_period:
        usage.investigations_by_severity[inv.severity] = (
            usage.investigations_by_severity.get(inv.severity, 0) + 1
        )
        if inv.service:
            usage.investigations_by_service[inv.service] = (
                usage.investigations_by_service.get(inv.service, 0) + 1
            )
        usage.investigations_by_outcome[inv.outcome.value] = (
            usage.investigations_by_outcome.get(inv.outcome.value, 0) + 1
        )

    tool_counts: dict[str, int] = {}
    for inv in current_period:
        for tool in inv.tools_used:
            tool_counts[tool] = tool_counts.get(tool, 0) + 1

    usage.top_tools_used = sorted(
        tool_counts.items(),
        key=lambda x: x[1],
        reverse=True,
    )[:10]

    return usage


def calculate_roi(
    time_saved_minutes: float,
    incidents_resolved: int = 0,
    downtime_prevented_minutes: float = 0.0,
    hourly_engineer_cost: float = 150.0,
    downtime_cost_per_minute: float = 500.0,
) -> dict[str, Any]:
    """Calculate ROI for given metrics.

    Args:
        time_saved_minutes: Total engineer time saved
        incidents_resolved: Number of incidents auto-resolved
        downtime_prevented_minutes: Downtime prevented
        hourly_engineer_cost: Hourly cost of engineer time
        downtime_cost_per_minute: Cost per minute of downtime

    Returns:
        Dictionary with ROI calculations
    """
    time_saved_hours = time_saved_minutes / 60
    engineer_savings = time_saved_hours * hourly_engineer_cost
    downtime_savings = downtime_prevented_minutes * downtime_cost_per_minute
    total_savings = engineer_savings + downtime_savings

    return {
        "time_saved_hours": round(time_saved_hours, 1),
        "engineer_savings_usd": round(engineer_savings, 2),
        "downtime_savings_usd": round(downtime_savings, 2),
        "total_savings_usd": round(total_savings, 2),
        "incidents_resolved": incidents_resolved,
    }
