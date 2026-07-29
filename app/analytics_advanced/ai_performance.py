"""AI Performance Analytics.

Tracks AI investigation quality, confidence calibration, and fix metrics.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("devcopilot.analytics_advanced.ai_performance")


class AIActionType(str, Enum):
    INVESTIGATE = "investigate"
    DIAGNOSE = "diagnose"
    FIX = "fix"
    VERIFY = "verify"


class Feedback(str, Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    MODIFIED = "modified"
    TIMEOUT = "timeout"


@dataclass
class AIAction:
    """Record of an AI action."""

    action_id: str
    incident_id: str
    action_type: AIActionType
    started_at: datetime
    completed_at: datetime | None = None
    confidence_score: float = 0.0
    was_correct: bool | None = None
    feedback: Feedback | None = None
    hypothesis_rank: int = 1
    caused_regression: bool = False
    reverted_within_24h: bool = False

    @property
    def duration_seconds(self) -> float | None:
        if self.completed_at and self.started_at:
            return (self.completed_at - self.started_at).total_seconds()
        return None


@dataclass
class ConfidenceCalibration:
    """Calibration metrics for AI confidence scores."""

    confidence_bucket: str
    predicted_accuracy: float
    actual_accuracy: float
    sample_count: int
    is_well_calibrated: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "bucket": self.confidence_bucket,
            "predicted": round(self.predicted_accuracy, 2),
            "actual": round(self.actual_accuracy, 2),
            "samples": self.sample_count,
            "calibrated": self.is_well_calibrated,
        }


@dataclass
class FixQuality:
    """Fix quality metrics."""

    total_fixes: int
    accepted_rate: float
    resolved_issue_rate: float
    regression_rate: float
    reverted_rate: float
    rejection_reasons: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_fixes": self.total_fixes,
            "rates": {
                "accepted": round(self.accepted_rate * 100, 1),
                "resolved_issue": round(self.resolved_issue_rate * 100, 1),
                "caused_regression": round(self.regression_rate * 100, 1),
                "reverted_within_24h": round(self.reverted_rate * 100, 1),
            },
            "rejection_reasons": {
                k: round(v * 100, 1) for k, v in self.rejection_reasons.items()
            },
        }


@dataclass
class AIMetrics:
    """Aggregated AI performance metrics."""

    period_start: datetime
    period_end: datetime
    total_actions: int
    investigation_success_rate: float
    root_cause_accuracy: float
    first_hypothesis_correct_rate: float
    top3_correct_rate: float
    human_correction_rate: float
    auto_fix_acceptance_rate: float
    time_saved_hours: float
    escalation_rate: float
    confidence_calibration: list[ConfidenceCalibration]
    fix_quality: FixQuality
    weekly_trend: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "period": {
                "start": self.period_start.isoformat(),
                "end": self.period_end.isoformat(),
            },
            "summary": {
                "total_actions": self.total_actions,
                "investigation_success_rate": round(self.investigation_success_rate * 100, 1),
                "root_cause_accuracy": round(self.root_cause_accuracy * 100, 1),
                "auto_fix_acceptance": round(self.auto_fix_acceptance_rate * 100, 1),
                "time_saved_hours": round(self.time_saved_hours, 1),
                "escalation_rate": round(self.escalation_rate * 100, 1),
            },
            "accuracy": {
                "first_hypothesis_correct": round(self.first_hypothesis_correct_rate * 100, 1),
                "top3_correct": round(self.top3_correct_rate * 100, 1),
                "human_correction_rate": round(self.human_correction_rate * 100, 1),
            },
            "calibration": [c.to_dict() for c in self.confidence_calibration],
            "fix_quality": self.fix_quality.to_dict(),
            "weekly_trend": self.weekly_trend,
        }


class AIPerformanceTracker:
    """Tracks AI performance metrics."""

    def __init__(self) -> None:
        self._actions: list[AIAction] = []
        self._rejection_reasons: dict[str, int] = {
            "too_risky": 0,
            "prefer_different_approach": 0,
            "doesnt_address_root_cause": 0,
            "other": 0,
        }

    def record_action(self, action: AIAction) -> None:
        """Record an AI action."""
        self._actions.append(action)

        if len(self._actions) > 50000:
            cutoff = datetime.now(timezone.utc) - timedelta(days=365)
            self._actions = [a for a in self._actions if a.started_at >= cutoff]

    def record_rejection_reason(self, reason: str) -> None:
        """Record a fix rejection reason."""
        if reason in self._rejection_reasons:
            self._rejection_reasons[reason] += 1
        else:
            self._rejection_reasons["other"] += 1

    def update_feedback(
        self,
        action_id: str,
        was_correct: bool,
        feedback: Feedback,
    ) -> None:
        """Update feedback for an action."""
        for action in self._actions:
            if action.action_id == action_id:
                action.was_correct = was_correct
                action.feedback = feedback
                break

    def calculate_metrics(
        self,
        period_days: int = 30,
    ) -> AIMetrics:
        """Calculate AI performance metrics."""
        now = datetime.now(timezone.utc)
        period_start = now - timedelta(days=period_days)

        actions = [a for a in self._actions if a.started_at >= period_start]

        if not actions:
            return self._empty_metrics(period_start, now)

        investigations = [
            a for a in actions if a.action_type == AIActionType.INVESTIGATE
        ]
        diagnoses = [
            a for a in actions if a.action_type == AIActionType.DIAGNOSE
        ]
        fixes = [
            a for a in actions if a.action_type == AIActionType.FIX
        ]

        investigation_success = self._calculate_success_rate(investigations)
        root_cause_accuracy = self._calculate_accuracy(diagnoses)
        first_hypothesis_rate = self._calculate_first_hypothesis_rate(diagnoses)
        top3_rate = self._calculate_top3_rate(diagnoses)
        correction_rate = self._calculate_correction_rate(actions)
        fix_acceptance = self._calculate_acceptance_rate(fixes)

        time_saved = self._estimate_time_saved(actions)
        escalation_rate = self._calculate_escalation_rate(actions)

        calibration = self._calculate_calibration(actions)
        fix_quality = self._calculate_fix_quality(fixes)

        weekly_trend = self._calculate_weekly_trend(period_days)

        return AIMetrics(
            period_start=period_start,
            period_end=now,
            total_actions=len(actions),
            investigation_success_rate=investigation_success,
            root_cause_accuracy=root_cause_accuracy,
            first_hypothesis_correct_rate=first_hypothesis_rate,
            top3_correct_rate=top3_rate,
            human_correction_rate=correction_rate,
            auto_fix_acceptance_rate=fix_acceptance,
            time_saved_hours=time_saved,
            escalation_rate=escalation_rate,
            confidence_calibration=calibration,
            fix_quality=fix_quality,
            weekly_trend=weekly_trend,
        )

    def _calculate_success_rate(self, actions: list[AIAction]) -> float:
        """Calculate success rate for actions."""
        if not actions:
            return 0.0

        successful = sum(
            1 for a in actions
            if a.was_correct is True or a.feedback == Feedback.ACCEPTED
        )

        return successful / len(actions)

    def _calculate_accuracy(self, actions: list[AIAction]) -> float:
        """Calculate accuracy for diagnoses."""
        validated = [a for a in actions if a.was_correct is not None]

        if not validated:
            return 0.0

        correct = sum(1 for a in validated if a.was_correct)
        return correct / len(validated)

    def _calculate_first_hypothesis_rate(self, actions: list[AIAction]) -> float:
        """Calculate rate of first hypothesis being correct."""
        validated = [
            a for a in actions
            if a.was_correct is not None and a.hypothesis_rank == 1
        ]

        if not validated:
            return 0.0

        correct = sum(1 for a in validated if a.was_correct)
        return correct / len(validated)

    def _calculate_top3_rate(self, actions: list[AIAction]) -> float:
        """Calculate rate of correct answer in top 3 hypotheses."""
        by_incident: dict[str, list[AIAction]] = {}

        for action in actions:
            if action.incident_id not in by_incident:
                by_incident[action.incident_id] = []
            by_incident[action.incident_id].append(action)

        if not by_incident:
            return 0.0

        correct_in_top3 = 0
        for incident_actions in by_incident.values():
            top3 = sorted(incident_actions, key=lambda a: a.hypothesis_rank)[:3]
            if any(a.was_correct for a in top3):
                correct_in_top3 += 1

        return correct_in_top3 / len(by_incident)

    def _calculate_correction_rate(self, actions: list[AIAction]) -> float:
        """Calculate human correction rate."""
        with_feedback = [a for a in actions if a.feedback is not None]

        if not with_feedback:
            return 0.0

        corrected = sum(
            1 for a in with_feedback
            if a.feedback in (Feedback.REJECTED, Feedback.MODIFIED)
        )

        return corrected / len(with_feedback)

    def _calculate_acceptance_rate(self, actions: list[AIAction]) -> float:
        """Calculate fix acceptance rate."""
        with_feedback = [a for a in actions if a.feedback is not None]

        if not with_feedback:
            return 0.0

        accepted = sum(
            1 for a in with_feedback if a.feedback == Feedback.ACCEPTED
        )

        return accepted / len(with_feedback)

    def _estimate_time_saved(self, actions: list[AIAction]) -> float:
        """Estimate time saved by AI actions."""
        time_saved_minutes = 0.0

        for action in actions:
            if action.action_type == AIActionType.INVESTIGATE:
                time_saved_minutes += 120
            elif action.action_type == AIActionType.DIAGNOSE:
                if action.was_correct:
                    time_saved_minutes += 90
            elif action.action_type == AIActionType.FIX:
                if action.feedback == Feedback.ACCEPTED:
                    time_saved_minutes += 30
            elif action.action_type == AIActionType.VERIFY:
                time_saved_minutes += 15

        return time_saved_minutes / 60

    def _calculate_escalation_rate(self, actions: list[AIAction]) -> float:
        """Calculate rate of escalations to humans."""
        if not actions:
            return 0.0

        escalated = sum(
            1 for a in actions
            if a.feedback == Feedback.TIMEOUT or (
                a.was_correct is False and a.feedback == Feedback.REJECTED
            )
        )

        return escalated / len(actions)

    def _calculate_calibration(
        self,
        actions: list[AIAction],
    ) -> list[ConfidenceCalibration]:
        """Calculate confidence calibration."""
        buckets = [
            (0.0, 0.5, "0-50%"),
            (0.5, 0.7, "50-70%"),
            (0.7, 0.9, "70-90%"),
            (0.9, 1.01, "90-100%"),
        ]

        calibration = []

        for low, high, label in buckets:
            bucket_actions = [
                a for a in actions
                if low <= a.confidence_score < high and a.was_correct is not None
            ]

            if not bucket_actions:
                continue

            predicted = (low + high) / 2
            actual = sum(1 for a in bucket_actions if a.was_correct) / len(bucket_actions)

            is_calibrated = abs(predicted - actual) < 0.1

            calibration.append(ConfidenceCalibration(
                confidence_bucket=label,
                predicted_accuracy=predicted,
                actual_accuracy=actual,
                sample_count=len(bucket_actions),
                is_well_calibrated=is_calibrated,
            ))

        return calibration

    def _calculate_fix_quality(self, fixes: list[AIAction]) -> FixQuality:
        """Calculate fix quality metrics."""
        if not fixes:
            return FixQuality(
                total_fixes=0,
                accepted_rate=0.0,
                resolved_issue_rate=0.0,
                regression_rate=0.0,
                reverted_rate=0.0,
                rejection_reasons={},
            )

        accepted = sum(1 for f in fixes if f.feedback == Feedback.ACCEPTED)
        resolved = sum(1 for f in fixes if f.was_correct)
        regressions = sum(1 for f in fixes if f.caused_regression)
        reverted = sum(1 for f in fixes if f.reverted_within_24h)

        total_rejections = sum(self._rejection_reasons.values())
        rejection_rates = {}
        if total_rejections > 0:
            rejection_rates = {
                k: v / total_rejections
                for k, v in self._rejection_reasons.items()
            }

        return FixQuality(
            total_fixes=len(fixes),
            accepted_rate=accepted / len(fixes),
            resolved_issue_rate=resolved / len(fixes) if fixes else 0,
            regression_rate=regressions / len(fixes),
            reverted_rate=reverted / len(fixes),
            rejection_reasons=rejection_rates,
        )

    def _calculate_weekly_trend(self, period_days: int) -> list[dict[str, Any]]:
        """Calculate weekly trend."""
        weeks = period_days // 7
        trend = []
        now = datetime.now(timezone.utc)

        for i in range(weeks - 1, -1, -1):
            week_end = now - timedelta(days=7 * i)
            week_start = week_end - timedelta(days=7)

            week_actions = [
                a for a in self._actions
                if week_start <= a.started_at < week_end
            ]

            validated = [a for a in week_actions if a.was_correct is not None]
            accuracy = (
                sum(1 for a in validated if a.was_correct) / len(validated)
                if validated else 0
            )

            trend.append({
                "week": f"Week {weeks - i}",
                "accuracy": round(accuracy * 100, 1),
                "actions": len(week_actions),
            })

        return trend

    def _empty_metrics(
        self,
        start: datetime,
        end: datetime,
    ) -> AIMetrics:
        """Return empty metrics."""
        return AIMetrics(
            period_start=start,
            period_end=end,
            total_actions=0,
            investigation_success_rate=0.0,
            root_cause_accuracy=0.0,
            first_hypothesis_correct_rate=0.0,
            top3_correct_rate=0.0,
            human_correction_rate=0.0,
            auto_fix_acceptance_rate=0.0,
            time_saved_hours=0.0,
            escalation_rate=0.0,
            confidence_calibration=[],
            fix_quality=FixQuality(
                total_fixes=0,
                accepted_rate=0.0,
                resolved_issue_rate=0.0,
                regression_rate=0.0,
                reverted_rate=0.0,
                rejection_reasons={},
            ),
            weekly_trend=[],
        )

    def get_improvement_suggestions(
        self,
        metrics: AIMetrics,
    ) -> list[str]:
        """Get suggestions for improving AI performance."""
        suggestions = []

        if metrics.first_hypothesis_correct_rate < 0.7:
            suggestions.append(
                "First hypothesis accuracy is below 70%. Consider reviewing "
                "training data for common failure patterns."
            )

        if metrics.auto_fix_acceptance_rate < 0.75:
            suggestions.append(
                "Fix acceptance rate is low. Analyze rejection reasons and "
                "improve fix generation for 'prefer different approach' cases."
            )

        if metrics.escalation_rate > 0.15:
            suggestions.append(
                "Escalation rate is high. Review timeout cases and improve "
                "confidence calibration."
            )

        for cal in metrics.confidence_calibration:
            if not cal.is_well_calibrated:
                suggestions.append(
                    f"Confidence bucket {cal.confidence_bucket} is poorly calibrated "
                    f"(predicted {cal.predicted_accuracy:.0%} vs actual {cal.actual_accuracy:.0%}). "
                    "Consider recalibrating confidence scoring."
                )

        if metrics.fix_quality.regression_rate > 0.05:
            suggestions.append(
                "Regression rate is above 5%. Add more test coverage to "
                "auto-generated fixes."
            )

        return suggestions


_tracker: AIPerformanceTracker | None = None


def get_ai_tracker() -> AIPerformanceTracker:
    """Get the default AI performance tracker."""
    global _tracker
    if _tracker is None:
        _tracker = AIPerformanceTracker()
    return _tracker
