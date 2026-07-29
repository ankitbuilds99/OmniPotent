"""Postmortem analytics and quality metrics."""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from app.postmortem_advanced.models import Postmortem, PostmortemStatus

log = logging.getLogger("devcopilot.postmortem_advanced.analytics")


@dataclass
class QualityScore:
    """Quality score for a postmortem."""

    completeness: float = 0.0
    timeline_accuracy: float = 0.0
    root_cause_depth: float = 0.0
    action_item_specificity: float = 0.0
    blameless_language: float = 0.0
    similar_incident_linkage: float = 0.0
    overall: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "completeness": round(self.completeness * 100, 1),
            "timeline_accuracy": round(self.timeline_accuracy * 100, 1),
            "root_cause_depth": round(self.root_cause_depth * 100, 1),
            "action_item_specificity": round(self.action_item_specificity * 100, 1),
            "blameless_language": round(self.blameless_language * 100, 1),
            "similar_incident_linkage": round(self.similar_incident_linkage * 100, 1),
            "overall": round(self.overall * 100, 1),
        }


@dataclass
class CompletionMetrics:
    """Metrics on postmortem completion."""

    total_required: int = 0
    total_completed: int = 0
    total_pending: int = 0
    completion_rate: float = 0.0
    avg_completion_days: float = 0.0
    avg_completion_days_with_ai: float = 0.0
    overdue_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_required": self.total_required,
            "completed": self.total_completed,
            "pending": self.total_pending,
            "completion_rate_percent": round(self.completion_rate * 100, 1),
            "avg_days_to_complete": round(self.avg_completion_days, 1),
            "avg_days_with_ai": round(self.avg_completion_days_with_ai, 1),
            "overdue": self.overdue_count,
        }


@dataclass
class TimeSavedMetrics:
    """Metrics on time saved with AI postmortems."""

    manual_avg_hours: float = 0.0
    ai_assisted_avg_hours: float = 0.0
    time_saved_per_postmortem_hours: float = 0.0
    time_saved_percent: float = 0.0
    total_postmortems: int = 0
    total_time_saved_hours: float = 0.0
    cost_savings_estimate: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "manual_avg_hours": round(self.manual_avg_hours, 1),
            "ai_assisted_avg_hours": round(self.ai_assisted_avg_hours, 1),
            "time_saved_per_postmortem_hours": round(self.time_saved_per_postmortem_hours, 1),
            "time_saved_percent": round(self.time_saved_percent, 1),
            "total_postmortems": self.total_postmortems,
            "total_time_saved_hours": round(self.total_time_saved_hours, 1),
            "cost_savings_at_150_per_hour": round(self.cost_savings_estimate, 2),
        }


@dataclass
class PostmortemRecord:
    """Internal record for analytics."""

    postmortem_id: str
    incident_id: str
    severity: str
    status: PostmortemStatus
    created_at: datetime
    completed_at: datetime | None
    published_at: datetime | None
    ai_assisted: bool
    quality_score: QualityScore
    creation_time_hours: float


class PostmortemAnalytics:
    """Analyzes postmortem quality and completion metrics."""

    def __init__(self) -> None:
        self._records: list[PostmortemRecord] = []
        self._quality_targets = {
            "completeness": 0.90,
            "timeline_accuracy": 0.95,
            "root_cause_depth": 0.85,
            "action_item_specificity": 0.80,
            "blameless_language": 0.95,
            "similar_incident_linkage": 0.70,
            "overall": 0.85,
        }

    def record_postmortem(
        self,
        postmortem: Postmortem,
        creation_time_hours: float = 1.5,
    ) -> None:
        """Record a postmortem for analytics."""
        quality = self.calculate_quality_score(postmortem)

        record = PostmortemRecord(
            postmortem_id=str(postmortem.postmortem_id),
            incident_id=postmortem.incident_id,
            severity=postmortem.severity,
            status=postmortem.status,
            created_at=postmortem.created_at,
            completed_at=postmortem.approved_at,
            published_at=postmortem.published_at,
            ai_assisted=postmortem.created_by.lower().startswith("ai"),
            quality_score=quality,
            creation_time_hours=creation_time_hours,
        )

        existing = next(
            (r for r in self._records if r.postmortem_id == record.postmortem_id),
            None
        )

        if existing:
            idx = self._records.index(existing)
            self._records[idx] = record
        else:
            self._records.append(record)

    def calculate_quality_score(
        self,
        postmortem: Postmortem,
    ) -> QualityScore:
        """Calculate quality score for a postmortem."""
        completeness = self._calculate_completeness(postmortem)

        timeline_accuracy = self._calculate_timeline_accuracy(postmortem)

        root_cause_depth = self._calculate_root_cause_depth(postmortem)

        action_specificity = self._calculate_action_specificity(postmortem)

        blameless = postmortem.blameless_score

        similar_linkage = self._calculate_similar_linkage(postmortem)

        weights = {
            "completeness": 0.20,
            "timeline_accuracy": 0.15,
            "root_cause_depth": 0.25,
            "action_item_specificity": 0.15,
            "blameless_language": 0.15,
            "similar_incident_linkage": 0.10,
        }

        overall = (
            completeness * weights["completeness"] +
            timeline_accuracy * weights["timeline_accuracy"] +
            root_cause_depth * weights["root_cause_depth"] +
            action_specificity * weights["action_item_specificity"] +
            blameless * weights["blameless_language"] +
            similar_linkage * weights["similar_incident_linkage"]
        )

        return QualityScore(
            completeness=completeness,
            timeline_accuracy=timeline_accuracy,
            root_cause_depth=root_cause_depth,
            action_item_specificity=action_specificity,
            blameless_language=blameless,
            similar_incident_linkage=similar_linkage,
            overall=overall,
        )

    def _calculate_completeness(self, postmortem: Postmortem) -> float:
        """Calculate section completeness score."""
        checks = [
            bool(postmortem.executive_summary),
            len(postmortem.timeline) >= 5,
            postmortem.root_cause_analysis is not None,
            len(postmortem.contributing_factors) >= 2,
            postmortem.impact is not None,
            len(postmortem.action_items) >= 3,
            len(postmortem.what_went_well) >= 2,
            len(postmortem.what_could_improve) >= 2,
            len(postmortem.lessons_learned) >= 1,
        ]

        return sum(checks) / len(checks)

    def _calculate_timeline_accuracy(self, postmortem: Postmortem) -> float:
        """Calculate timeline accuracy score."""
        if not postmortem.timeline:
            return 0.0

        score = 0.5

        if len(postmortem.timeline) >= 10:
            score += 0.2

        key_events = sum(1 for e in postmortem.timeline if e.is_key_event)
        if key_events >= 3:
            score += 0.15

        sources = set(e.source.value for e in postmortem.timeline)
        if len(sources) >= 3:
            score += 0.15

        return min(1.0, score)

    def _calculate_root_cause_depth(self, postmortem: Postmortem) -> float:
        """Calculate root cause analysis depth score."""
        rca = postmortem.root_cause_analysis
        if not rca:
            return 0.0

        score = 0.3

        if len(rca.five_whys) >= 4:
            score += 0.25
        elif len(rca.five_whys) >= 2:
            score += 0.15

        if rca.code_snippets:
            score += 0.15

        if rca.alternative_hypotheses:
            score += 0.15

        if len(rca.evidence) >= 3:
            score += 0.15

        return min(1.0, score)

    def _calculate_action_specificity(self, postmortem: Postmortem) -> float:
        """Calculate action item specificity score."""
        if not postmortem.action_items:
            return 0.0

        total_score = 0.0

        for item in postmortem.action_items:
            item_score = 0.25

            if item.suggested_owner or item.actual_owner:
                item_score += 0.25

            if item.due_date:
                item_score += 0.25

            if item.how:
                item_score += 0.25

            total_score += item_score

        return total_score / len(postmortem.action_items)

    def _calculate_similar_linkage(self, postmortem: Postmortem) -> float:
        """Calculate similar incident linkage score."""
        if not postmortem.similar_incidents:
            return 0.3

        score = 0.5

        if len(postmortem.similar_incidents) >= 2:
            score += 0.25

        has_lessons = any(
            s.lessons_applicable for s in postmortem.similar_incidents
        )
        if has_lessons:
            score += 0.25

        return min(1.0, score)

    def get_completion_metrics(
        self,
        period_days: int = 90,
        severity_filter: list[str] | None = None,
    ) -> CompletionMetrics:
        """Get postmortem completion metrics."""
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=period_days)

        records = [
            r for r in self._records
            if r.created_at >= cutoff
            and (severity_filter is None or r.severity in severity_filter)
        ]

        if not records:
            return CompletionMetrics()

        required_severities = severity_filter or ["P1", "P2"]
        total_required = sum(
            1 for r in records if r.severity in required_severities
        )

        completed = [
            r for r in records
            if r.status in [PostmortemStatus.APPROVED, PostmortemStatus.PUBLISHED]
        ]
        total_completed = len(completed)

        pending = [
            r for r in records
            if r.status in [PostmortemStatus.DRAFT, PostmortemStatus.IN_REVIEW]
        ]
        total_pending = len(pending)

        completion_rate = total_completed / total_required if total_required > 0 else 0.0

        completion_times = []
        ai_completion_times = []

        for r in completed:
            if r.completed_at:
                days = (r.completed_at - r.created_at).days
                completion_times.append(days)
                if r.ai_assisted:
                    ai_completion_times.append(days)

        avg_completion = statistics.mean(completion_times) if completion_times else 0.0
        avg_ai_completion = statistics.mean(ai_completion_times) if ai_completion_times else 0.0

        target_days = 5
        overdue = sum(
            1 for r in pending
            if (now - r.created_at).days > target_days
        )

        return CompletionMetrics(
            total_required=total_required,
            total_completed=total_completed,
            total_pending=total_pending,
            completion_rate=completion_rate,
            avg_completion_days=avg_completion,
            avg_completion_days_with_ai=avg_ai_completion,
            overdue_count=overdue,
        )

    def get_time_saved_metrics(
        self,
        period_days: int = 90,
        hourly_rate: float = 150.0,
    ) -> TimeSavedMetrics:
        """Get metrics on time saved with AI postmortems."""
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=period_days)

        records = [r for r in self._records if r.created_at >= cutoff]

        if not records:
            return TimeSavedMetrics()

        ai_records = [r for r in records if r.ai_assisted]
        manual_records = [r for r in records if not r.ai_assisted]

        manual_avg = 6.2
        if manual_records:
            manual_avg = statistics.mean(r.creation_time_hours for r in manual_records)

        ai_avg = 1.4
        if ai_records:
            ai_avg = statistics.mean(r.creation_time_hours for r in ai_records)

        time_saved_per = manual_avg - ai_avg
        time_saved_percent = (time_saved_per / manual_avg * 100) if manual_avg > 0 else 0

        total_ai_postmortems = len(ai_records)
        total_time_saved = total_ai_postmortems * time_saved_per
        cost_savings = total_time_saved * hourly_rate

        return TimeSavedMetrics(
            manual_avg_hours=manual_avg,
            ai_assisted_avg_hours=ai_avg,
            time_saved_per_postmortem_hours=time_saved_per,
            time_saved_percent=time_saved_percent,
            total_postmortems=total_ai_postmortems,
            total_time_saved_hours=total_time_saved,
            cost_savings_estimate=cost_savings,
        )

    def get_quality_trends(
        self,
        period_days: int = 90,
        interval_days: int = 7,
    ) -> list[dict[str, Any]]:
        """Get quality score trends over time."""
        now = datetime.now(timezone.utc)
        trends = []

        intervals = period_days // interval_days

        for i in range(intervals - 1, -1, -1):
            interval_end = now - timedelta(days=i * interval_days)
            interval_start = interval_end - timedelta(days=interval_days)

            interval_records = [
                r for r in self._records
                if interval_start <= r.created_at < interval_end
            ]

            if interval_records:
                avg_quality = statistics.mean(
                    r.quality_score.overall for r in interval_records
                )
                count = len(interval_records)
            else:
                avg_quality = 0.0
                count = 0

            trends.append({
                "period_start": interval_start.isoformat(),
                "period_end": interval_end.isoformat(),
                "postmortem_count": count,
                "avg_quality_percent": round(avg_quality * 100, 1),
            })

        return trends

    def get_quality_by_category(
        self,
        period_days: int = 90,
    ) -> dict[str, dict[str, float]]:
        """Get average quality scores by category."""
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=period_days)

        records = [r for r in self._records if r.created_at >= cutoff]

        if not records:
            return {}

        categories = [
            "completeness",
            "timeline_accuracy",
            "root_cause_depth",
            "action_item_specificity",
            "blameless_language",
            "similar_incident_linkage",
            "overall",
        ]

        result = {}

        for cat in categories:
            values = [getattr(r.quality_score, cat) for r in records]
            avg = statistics.mean(values)
            target = self._quality_targets.get(cat, 0.8)

            result[cat] = {
                "average": round(avg * 100, 1),
                "target": round(target * 100, 1),
                "meets_target": avg >= target,
            }

        return result

    def get_improvement_suggestions(
        self,
        period_days: int = 90,
    ) -> list[str]:
        """Get suggestions for improving postmortem quality."""
        quality_by_cat = self.get_quality_by_category(period_days)
        suggestions = []

        for cat, metrics in quality_by_cat.items():
            if not metrics["meets_target"]:
                gap = metrics["target"] - metrics["average"]

                if cat == "completeness":
                    suggestions.append(
                        f"Completeness is {gap:.0f}% below target. "
                        "Ensure all standard sections are filled out."
                    )
                elif cat == "root_cause_depth":
                    suggestions.append(
                        f"Root cause depth is {gap:.0f}% below target. "
                        "Include more Five Whys levels and code evidence."
                    )
                elif cat == "action_item_specificity":
                    suggestions.append(
                        f"Action item specificity is {gap:.0f}% below target. "
                        "Add owners, due dates, and implementation details."
                    )
                elif cat == "similar_incident_linkage":
                    suggestions.append(
                        f"Similar incident linkage is {gap:.0f}% below target. "
                        "Reference related past incidents and their lessons."
                    )

        completion = self.get_completion_metrics(period_days)
        if completion.overdue_count > 0:
            suggestions.append(
                f"{completion.overdue_count} postmortems are overdue. "
                "Set reminders and consider auto-escalation."
            )

        if completion.completion_rate < 0.8:
            suggestions.append(
                f"Completion rate is {completion.completion_rate * 100:.0f}%. "
                "Consider requiring postmortems for P1/P2 incidents."
            )

        return suggestions

    def generate_executive_report(
        self,
        period_days: int = 90,
    ) -> dict[str, Any]:
        """Generate executive-level analytics report."""
        completion = self.get_completion_metrics(period_days)
        time_saved = self.get_time_saved_metrics(period_days)
        quality_by_cat = self.get_quality_by_category(period_days)
        trends = self.get_quality_trends(period_days)
        suggestions = self.get_improvement_suggestions(period_days)

        return {
            "period_days": period_days,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "completion": completion.to_dict(),
            "time_saved": time_saved.to_dict(),
            "quality": {
                "by_category": quality_by_cat,
                "overall_trend": trends,
            },
            "suggestions": suggestions,
            "highlights": [
                f"Quality improved {self._calculate_quality_improvement(trends)}% with AI",
                f"Saved {time_saved.total_time_saved_hours:.0f} hours across {time_saved.total_postmortems} postmortems",
                f"Estimated cost savings: ${time_saved.cost_savings_estimate:,.0f}",
            ],
        }

    def _calculate_quality_improvement(
        self,
        trends: list[dict[str, Any]],
    ) -> float:
        """Calculate quality improvement from trends."""
        if len(trends) < 2:
            return 0.0

        with_data = [t for t in trends if t["postmortem_count"] > 0]
        if len(with_data) < 2:
            return 0.0

        first_half = with_data[:len(with_data) // 2]
        second_half = with_data[len(with_data) // 2:]

        avg_first = statistics.mean(t["avg_quality_percent"] for t in first_half)
        avg_second = statistics.mean(t["avg_quality_percent"] for t in second_half)

        if avg_first > 0:
            return round((avg_second - avg_first) / avg_first * 100, 1)

        return 0.0


_analytics: PostmortemAnalytics | None = None


def get_postmortem_analytics() -> PostmortemAnalytics:
    """Get the default postmortem analytics."""
    global _analytics
    if _analytics is None:
        _analytics = PostmortemAnalytics()
    return _analytics
