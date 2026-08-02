"""Analytics for deduplication effectiveness and alert quality."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from app.deduplication.models import (
    DeduplicationDecision,
    Decision,
    AlertQualityScore,
    FatigueMetrics,
)

log = logging.getLogger("omnipotent.deduplication.analytics")


@dataclass
class LayerReductionStats:
    """Statistics for a single deduplication layer."""

    layer_name: str
    alerts_before: int = 0
    alerts_after: int = 0
    reduction_count: int = 0
    reduction_percentage: float = 0.0

    def calculate(self) -> None:
        """Calculate reduction percentage."""
        if self.alerts_before > 0:
            self.reduction_count = self.alerts_before - self.alerts_after
            self.reduction_percentage = self.reduction_count / self.alerts_before * 100


@dataclass
class NoiseReductionReport:
    """Comprehensive noise reduction report."""

    period_start: datetime = field(default_factory=datetime.utcnow)
    period_end: datetime = field(default_factory=datetime.utcnow)
    raw_alerts: int = 0
    deduplicated_alerts: int = 0
    final_actionable: int = 0
    overall_reduction_percentage: float = 0.0
    layer_stats: list[LayerReductionStats] = field(default_factory=list)


class NoiseReductionTracker:
    """Track noise reduction metrics over time."""

    def __init__(self):
        self._raw_alert_count: int = 0
        self._layer_counts: dict[str, int] = {
            "fingerprint": 0,
            "semantic": 0,
            "temporal": 0,
            "topology": 0,
            "ml": 0,
            "suppression": 0,
        }
        self._decision_counts: dict[Decision, int] = {
            Decision.NEW_INCIDENT: 0,
            Decision.ADD_TO_EXISTING: 0,
            Decision.SUPPRESS: 0,
            Decision.FLAPPING: 0,
        }
        self._period_start: datetime = datetime.utcnow()

    def record_alert(self, decision: DeduplicationDecision) -> None:
        """Record an alert and its deduplication decision."""
        self._raw_alert_count += 1
        self._decision_counts[decision.decision] = (
            self._decision_counts.get(decision.decision, 0) + 1
        )

        for layer in decision.layers_applied:
            if layer in self._layer_counts:
                self._layer_counts[layer] += 1

    def get_reduction_report(self) -> NoiseReductionReport:
        """Generate noise reduction report."""
        total = self._raw_alert_count
        deduplicated = self._decision_counts.get(Decision.ADD_TO_EXISTING, 0)
        suppressed = self._decision_counts.get(Decision.SUPPRESS, 0) + self._decision_counts.get(Decision.FLAPPING, 0)
        actionable = self._decision_counts.get(Decision.NEW_INCIDENT, 0)

        layer_stats = []
        running_total = total

        for layer_name in [
            "fingerprint",
            "semantic",
            "temporal",
            "topology",
            "ml",
            "suppression",
        ]:
            count = self._layer_counts.get(layer_name, 0)
            after = running_total - count
            stats = LayerReductionStats(
                layer_name=layer_name,
                alerts_before=running_total,
                alerts_after=after,
            )
            stats.calculate()
            layer_stats.append(stats)
            running_total = after

        overall_reduction = 0.0
        if total > 0:
            overall_reduction = (deduplicated + suppressed) / total * 100

        return NoiseReductionReport(
            period_start=self._period_start,
            period_end=datetime.utcnow(),
            raw_alerts=total,
            deduplicated_alerts=deduplicated,
            final_actionable=actionable,
            overall_reduction_percentage=overall_reduction,
            layer_stats=layer_stats,
        )

    def reset(self) -> None:
        """Reset tracking for new period."""
        self._raw_alert_count = 0
        self._layer_counts = {k: 0 for k in self._layer_counts}
        self._decision_counts = {k: 0 for k in self._decision_counts}
        self._period_start = datetime.utcnow()


@dataclass
class AlertActionRecord:
    """Record of action taken on an alert."""

    alert_fingerprint: str
    alert_name: str = ""
    service: str = ""
    timestamp: datetime = field(default_factory=datetime.utcnow)
    was_actioned: bool = False
    was_ignored: bool = False
    was_auto_resolved: bool = False
    time_to_action_seconds: float | None = None
    action_type: str = ""


class AlertQualityAnalyzer:
    """Analyze and score alert quality."""

    def __init__(self):
        self._action_records: dict[str, list[AlertActionRecord]] = {}
        self._quality_scores: dict[str, AlertQualityScore] = {}

    def record_action(
        self,
        fingerprint: str,
        alert_name: str = "",
        service: str = "",
        was_actioned: bool = False,
        was_ignored: bool = False,
        was_auto_resolved: bool = False,
        time_to_action_seconds: float | None = None,
        action_type: str = "",
    ) -> None:
        """Record an action taken on an alert."""
        record = AlertActionRecord(
            alert_fingerprint=fingerprint,
            alert_name=alert_name,
            service=service,
            was_actioned=was_actioned,
            was_ignored=was_ignored,
            was_auto_resolved=was_auto_resolved,
            time_to_action_seconds=time_to_action_seconds,
            action_type=action_type,
        )

        if fingerprint not in self._action_records:
            self._action_records[fingerprint] = []
        self._action_records[fingerprint].append(record)

    def calculate_quality_score(
        self,
        fingerprint: str,
    ) -> AlertQualityScore:
        """Calculate quality score for an alert type."""
        records = self._action_records.get(fingerprint, [])

        if not records:
            return AlertQualityScore(alert_fingerprint=fingerprint)

        total = len(records)
        actioned = sum(1 for r in records if r.was_actioned)
        ignored = sum(1 for r in records if r.was_ignored)
        auto_resolved = sum(1 for r in records if r.was_auto_resolved)

        action_times = [
            r.time_to_action_seconds
            for r in records
            if r.time_to_action_seconds is not None
        ]
        avg_time_to_action = (
            sum(action_times) / len(action_times) if action_times else 0.0
        )

        actionability = actioned / total if total > 0 else 0.0
        snr = actioned / (actioned + ignored) if (actioned + ignored) > 0 else 0.0

        score = AlertQualityScore(
            alert_fingerprint=fingerprint,
            alert_name=records[0].alert_name if records else "",
            service=records[0].service if records else "",
            actionability_score=actionability,
            signal_to_noise_ratio=snr,
            time_to_action_avg_seconds=avg_time_to_action,
            total_fires=total,
            total_actioned=actioned,
            total_ignored=ignored,
            total_auto_resolved=auto_resolved,
        )

        score.calculate_overall()
        score.recommendations = self._generate_recommendations(score)

        self._quality_scores[fingerprint] = score
        return score

    def _generate_recommendations(
        self,
        score: AlertQualityScore,
    ) -> list[str]:
        """Generate recommendations for improving alert quality."""
        recommendations = []

        if score.actionability_score < 0.3:
            recommendations.append(
                "Low actionability: Consider removing or adjusting this alert"
            )

        if score.signal_to_noise_ratio < 0.5:
            recommendations.append(
                "High noise ratio: Consider increasing threshold or adding filters"
            )

        if score.time_to_action_avg_seconds > 600:
            recommendations.append(
                "Slow response time: May indicate alert fatigue or low priority"
            )

        if score.flapping_rate > 0.3:
            recommendations.append(
                "High flapping rate: Add hysteresis or increase threshold"
            )

        if score.correlation_rate > 0.7:
            recommendations.append(
                "Often grouped with others: May be a symptom, not root cause"
            )

        return recommendations

    def get_top_alerts(
        self,
        limit: int = 10,
        sort_by: str = "overall_score",
    ) -> list[AlertQualityScore]:
        """Get top quality alerts."""
        scores = list(self._quality_scores.values())

        if sort_by == "overall_score":
            scores.sort(key=lambda s: s.overall_score, reverse=True)
        elif sort_by == "actionability":
            scores.sort(key=lambda s: s.actionability_score, reverse=True)
        elif sort_by == "fires":
            scores.sort(key=lambda s: s.total_fires, reverse=True)

        return scores[:limit]

    def get_noisy_alerts(
        self,
        limit: int = 10,
    ) -> list[AlertQualityScore]:
        """Get noisiest (lowest quality) alerts."""
        scores = list(self._quality_scores.values())
        scores.sort(key=lambda s: s.overall_score)
        return scores[:limit]

    def get_quality_distribution(self) -> dict[str, int]:
        """Get distribution of alert quality scores."""
        distribution = {
            "excellent": 0,
            "good": 0,
            "fair": 0,
            "poor": 0,
            "critical": 0,
        }

        for score in self._quality_scores.values():
            if score.overall_score >= 80:
                distribution["excellent"] += 1
            elif score.overall_score >= 60:
                distribution["good"] += 1
            elif score.overall_score >= 40:
                distribution["fair"] += 1
            elif score.overall_score >= 20:
                distribution["poor"] += 1
            else:
                distribution["critical"] += 1

        return distribution


@dataclass
class OnCallShift:
    """On-call shift period."""

    user: str
    start_time: datetime
    end_time: datetime
    alerts_received: int = 0
    pages_received: int = 0
    acknowledged: int = 0
    ignored: int = 0


class FatigueAnalyzer:
    """Analyze alert fatigue for on-call teams."""

    def __init__(self):
        self._shifts: list[OnCallShift] = []
        self._daily_metrics: dict[str, FatigueMetrics] = {}

    def record_shift(
        self,
        user: str,
        start_time: datetime,
        end_time: datetime,
        alerts_received: int,
        pages_received: int,
        acknowledged: int,
        ignored: int,
    ) -> None:
        """Record an on-call shift."""
        shift = OnCallShift(
            user=user,
            start_time=start_time,
            end_time=end_time,
            alerts_received=alerts_received,
            pages_received=pages_received,
            acknowledged=acknowledged,
            ignored=ignored,
        )
        self._shifts.append(shift)

    def calculate_fatigue_metrics(
        self,
        period_start: datetime,
        period_end: datetime,
        team: str | None = None,
    ) -> FatigueMetrics:
        """Calculate fatigue metrics for a period."""
        relevant_shifts = [
            s for s in self._shifts
            if s.start_time >= period_start and s.end_time <= period_end
        ]

        if not relevant_shifts:
            return FatigueMetrics(
                period_start=period_start,
                period_end=period_end,
                team=team,
            )

        total_alerts = sum(s.alerts_received for s in relevant_shifts)
        total_pages = sum(s.pages_received for s in relevant_shifts)
        total_acknowledged = sum(s.acknowledged for s in relevant_shifts)
        total_ignored = sum(s.ignored for s in relevant_shifts)

        oncall_days = len(relevant_shifts)

        night_pages = 0
        weekend_pages = 0
        for shift in relevant_shifts:
            if shift.start_time.hour >= 23 or shift.start_time.hour < 7:
                night_pages += shift.pages_received
            if shift.start_time.weekday() >= 5:
                weekend_pages += shift.pages_received

        metrics = FatigueMetrics(
            period_start=period_start,
            period_end=period_end,
            team=team,
            alerts_per_oncall_day=total_alerts / oncall_days if oncall_days > 0 else 0,
            pages_per_oncall_day=total_pages / oncall_days if oncall_days > 0 else 0,
            acknowledged_within_5min_pct=(
                total_acknowledged / total_alerts if total_alerts > 0 else 0
            ),
            ignored_dismissed_pct=total_ignored / total_alerts if total_alerts > 0 else 0,
            night_pages_pct=night_pages / total_pages if total_pages > 0 else 0,
            weekend_pages_pct=weekend_pages / total_pages if total_pages > 0 else 0,
            total_alerts=total_alerts,
            total_pages=total_pages,
            total_acknowledged=total_acknowledged,
            total_ignored=total_ignored,
        )

        metrics.calculate_fatigue_score()
        metrics.trend = self._calculate_trend(period_start, metrics.fatigue_score)

        return metrics

    def _calculate_trend(
        self,
        current_period_start: datetime,
        current_score: float,
    ) -> str:
        """Calculate trend compared to previous period."""
        period_length = timedelta(days=30)
        prev_start = current_period_start - period_length
        prev_end = current_period_start

        prev_metrics = self.calculate_fatigue_metrics(prev_start, prev_end)

        if prev_metrics.fatigue_score == 0:
            return "stable"

        change = (current_score - prev_metrics.fatigue_score) / prev_metrics.fatigue_score

        if change > 0.1:
            return "worsening"
        elif change < -0.1:
            return "improving"
        else:
            return "stable"

    def get_user_fatigue_report(
        self,
        user: str,
        days: int = 30,
    ) -> dict[str, Any]:
        """Get fatigue report for a specific user."""
        period_end = datetime.utcnow()
        period_start = period_end - timedelta(days=days)

        user_shifts = [
            s for s in self._shifts
            if s.user == user
            and s.start_time >= period_start
            and s.end_time <= period_end
        ]

        if not user_shifts:
            return {"user": user, "shifts": 0, "fatigue_indicators": []}

        total_alerts = sum(s.alerts_received for s in user_shifts)
        total_pages = sum(s.pages_received for s in user_shifts)

        fatigue_indicators = []
        if total_pages / len(user_shifts) > 5:
            fatigue_indicators.append("High pages per shift")
        if total_alerts / len(user_shifts) > 20:
            fatigue_indicators.append("High alerts per shift")

        return {
            "user": user,
            "shifts": len(user_shifts),
            "total_alerts": total_alerts,
            "total_pages": total_pages,
            "avg_pages_per_shift": total_pages / len(user_shifts),
            "fatigue_indicators": fatigue_indicators,
        }


class DeduplicationAnalytics:
    """Main analytics class combining all analytics components."""

    def __init__(self):
        self._noise_tracker = NoiseReductionTracker()
        self._quality_analyzer = AlertQualityAnalyzer()
        self._fatigue_analyzer = FatigueAnalyzer()

    def record_decision(self, decision: DeduplicationDecision) -> None:
        """Record a deduplication decision."""
        self._noise_tracker.record_alert(decision)

    def record_alert_action(
        self,
        fingerprint: str,
        alert_name: str = "",
        service: str = "",
        was_actioned: bool = False,
        was_ignored: bool = False,
        time_to_action_seconds: float | None = None,
    ) -> None:
        """Record an action taken on an alert."""
        self._quality_analyzer.record_action(
            fingerprint=fingerprint,
            alert_name=alert_name,
            service=service,
            was_actioned=was_actioned,
            was_ignored=was_ignored,
            time_to_action_seconds=time_to_action_seconds,
        )

    def record_oncall_shift(
        self,
        user: str,
        start_time: datetime,
        end_time: datetime,
        alerts_received: int,
        pages_received: int,
        acknowledged: int,
        ignored: int,
    ) -> None:
        """Record an on-call shift."""
        self._fatigue_analyzer.record_shift(
            user=user,
            start_time=start_time,
            end_time=end_time,
            alerts_received=alerts_received,
            pages_received=pages_received,
            acknowledged=acknowledged,
            ignored=ignored,
        )

    def get_noise_reduction_report(self) -> NoiseReductionReport:
        """Get noise reduction report."""
        return self._noise_tracker.get_reduction_report()

    def get_alert_quality_score(
        self,
        fingerprint: str,
    ) -> AlertQualityScore:
        """Get quality score for an alert type."""
        return self._quality_analyzer.calculate_quality_score(fingerprint)

    def get_top_quality_alerts(self, limit: int = 10) -> list[AlertQualityScore]:
        """Get highest quality alerts."""
        return self._quality_analyzer.get_top_alerts(limit)

    def get_noisy_alerts(self, limit: int = 10) -> list[AlertQualityScore]:
        """Get noisiest alerts."""
        return self._quality_analyzer.get_noisy_alerts(limit)

    def get_fatigue_metrics(
        self,
        period_start: datetime,
        period_end: datetime,
        team: str | None = None,
    ) -> FatigueMetrics:
        """Get fatigue metrics."""
        return self._fatigue_analyzer.calculate_fatigue_metrics(
            period_start, period_end, team
        )

    def get_comprehensive_report(self) -> dict[str, Any]:
        """Get comprehensive analytics report."""
        noise_report = self._noise_tracker.get_reduction_report()
        quality_dist = self._quality_analyzer.get_quality_distribution()

        now = datetime.utcnow()
        fatigue = self._fatigue_analyzer.calculate_fatigue_metrics(
            now - timedelta(days=30), now
        )

        return {
            "noise_reduction": {
                "raw_alerts": noise_report.raw_alerts,
                "final_actionable": noise_report.final_actionable,
                "reduction_percentage": noise_report.overall_reduction_percentage,
                "layer_breakdown": [
                    {
                        "layer": s.layer_name,
                        "reduction_pct": s.reduction_percentage,
                        "count": s.reduction_count,
                    }
                    for s in noise_report.layer_stats
                ],
            },
            "alert_quality": {
                "distribution": quality_dist,
                "top_alerts": [
                    s.to_dict() for s in self._quality_analyzer.get_top_alerts(5)
                ],
                "noisy_alerts": [
                    s.to_dict() for s in self._quality_analyzer.get_noisy_alerts(5)
                ],
            },
            "fatigue": fatigue.to_dict(),
        }

    def reset_noise_tracking(self) -> None:
        """Reset noise tracking for new period."""
        self._noise_tracker.reset()
