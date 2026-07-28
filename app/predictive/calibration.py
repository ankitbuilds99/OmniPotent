"""Confidence calibration for prediction accuracy.

Tracks prediction outcomes to improve confidence scoring.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("devcopilot.predictive.calibration")


class PredictionOutcome(Enum):
    """Outcome of a prediction."""

    TRUE_POSITIVE = "true_positive"
    FALSE_POSITIVE = "false_positive"
    TRUE_NEGATIVE = "true_negative"
    FALSE_NEGATIVE = "false_negative"
    PREVENTED = "prevented"
    PENDING = "pending"


@dataclass
class PredictionRecord:
    """Record of a prediction and its outcome."""

    prediction_id: str
    predicted_at: datetime
    predicted_confidence: float
    expected_incident_time: datetime
    incident_type: str
    affected_services: list[str]
    outcome: PredictionOutcome = PredictionOutcome.PENDING
    actual_incident_id: str | None = None
    outcome_recorded_at: datetime | None = None
    notes: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "prediction_id": self.prediction_id,
            "predicted_at": self.predicted_at.isoformat(),
            "predicted_confidence": self.predicted_confidence,
            "expected_incident_time": self.expected_incident_time.isoformat(),
            "incident_type": self.incident_type,
            "affected_services": self.affected_services,
            "outcome": self.outcome.value,
            "actual_incident_id": self.actual_incident_id,
            "outcome_recorded_at": (
                self.outcome_recorded_at.isoformat()
                if self.outcome_recorded_at
                else None
            ),
        }


@dataclass
class CalibrationBucket:
    """A bucket for calibration metrics."""

    confidence_range: tuple[float, float]
    predictions: int = 0
    actual_occurrences: int = 0

    @property
    def actual_rate(self) -> float:
        if self.predictions == 0:
            return 0.0
        return self.actual_occurrences / self.predictions

    @property
    def expected_rate(self) -> float:
        return (self.confidence_range[0] + self.confidence_range[1]) / 2

    @property
    def calibration_error(self) -> float:
        return abs(self.actual_rate - self.expected_rate)

    @property
    def status(self) -> str:
        error = self.calibration_error
        if error < 0.05:
            return "well_calibrated"
        elif self.actual_rate > self.expected_rate:
            return "underconfident"
        else:
            return "overconfident"

    def to_dict(self) -> dict[str, Any]:
        return {
            "confidence_range": list(self.confidence_range),
            "predictions": self.predictions,
            "actual_occurrences": self.actual_occurrences,
            "actual_rate": round(self.actual_rate, 3),
            "expected_rate": round(self.expected_rate, 3),
            "calibration_error": round(self.calibration_error, 3),
            "status": self.status,
        }


@dataclass
class CalibrationMetrics:
    """Overall calibration metrics."""

    total_predictions: int
    true_positives: int
    false_positives: int
    true_negatives: int
    false_negatives: int
    prevented: int
    pending: int
    buckets: list[CalibrationBucket]
    brier_score: float
    expected_calibration_error: float
    accuracy: float
    precision: float
    recall: float
    f1_score: float
    calculated_at: datetime

    @property
    def is_well_calibrated(self) -> bool:
        return self.expected_calibration_error < 0.1

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_predictions": self.total_predictions,
            "true_positives": self.true_positives,
            "false_positives": self.false_positives,
            "true_negatives": self.true_negatives,
            "false_negatives": self.false_negatives,
            "prevented": self.prevented,
            "pending": self.pending,
            "brier_score": round(self.brier_score, 4),
            "expected_calibration_error": round(self.expected_calibration_error, 4),
            "accuracy": round(self.accuracy, 3),
            "precision": round(self.precision, 3),
            "recall": round(self.recall, 3),
            "f1_score": round(self.f1_score, 3),
            "is_well_calibrated": self.is_well_calibrated,
            "buckets": [b.to_dict() for b in self.buckets],
            "calculated_at": self.calculated_at.isoformat(),
        }


@dataclass
class MissedIncidentAnalysis:
    """Analysis of why an incident was missed."""

    incident_id: str
    incident_type: str
    affected_services: list[str]
    occurred_at: datetime
    reason: str
    fix_implemented: bool
    now_detectable: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "incident_id": self.incident_id,
            "incident_type": self.incident_type,
            "affected_services": self.affected_services,
            "occurred_at": self.occurred_at.isoformat(),
            "reason": self.reason,
            "fix_implemented": self.fix_implemented,
            "now_detectable": self.now_detectable,
        }


class ConfidenceCalibrator:
    """Calibrates prediction confidence based on historical accuracy."""

    def __init__(self) -> None:
        self._records: dict[str, PredictionRecord] = {}
        self._missed_incidents: list[MissedIncidentAnalysis] = []
        self._bucket_ranges = [
            (0.0, 0.6),
            (0.6, 0.7),
            (0.7, 0.8),
            (0.8, 0.9),
            (0.9, 1.0),
        ]

    def record_prediction(
        self,
        prediction_id: str,
        confidence: float,
        expected_time: datetime,
        incident_type: str,
        affected_services: list[str],
    ) -> PredictionRecord:
        """Record a new prediction for tracking."""
        record = PredictionRecord(
            prediction_id=prediction_id,
            predicted_at=datetime.now(timezone.utc),
            predicted_confidence=confidence,
            expected_incident_time=expected_time,
            incident_type=incident_type,
            affected_services=affected_services,
        )

        self._records[prediction_id] = record

        log.info(
            "prediction recorded for calibration",
            extra={
                "prediction_id": prediction_id,
                "confidence": confidence,
            },
        )

        return record

    def record_outcome(
        self,
        prediction_id: str,
        outcome: PredictionOutcome,
        actual_incident_id: str | None = None,
        notes: str | None = None,
    ) -> bool:
        """Record the outcome of a prediction."""
        record = self._records.get(prediction_id)
        if not record:
            return False

        record.outcome = outcome
        record.actual_incident_id = actual_incident_id
        record.outcome_recorded_at = datetime.now(timezone.utc)
        record.notes = notes

        log.info(
            "prediction outcome recorded",
            extra={
                "prediction_id": prediction_id,
                "outcome": outcome.value,
                "actual_incident_id": actual_incident_id,
            },
        )

        return True

    def record_missed_incident(
        self,
        incident_id: str,
        incident_type: str,
        affected_services: list[str],
        occurred_at: datetime,
        reason: str,
    ) -> MissedIncidentAnalysis:
        """Record an incident that was not predicted."""
        analysis = MissedIncidentAnalysis(
            incident_id=incident_id,
            incident_type=incident_type,
            affected_services=affected_services,
            occurred_at=occurred_at,
            reason=reason,
            fix_implemented=False,
            now_detectable=False,
        )

        self._missed_incidents.append(analysis)

        self.record_prediction(
            prediction_id=f"missed_{incident_id}",
            confidence=0.0,
            expected_time=occurred_at,
            incident_type=incident_type,
            affected_services=affected_services,
        )
        self.record_outcome(
            prediction_id=f"missed_{incident_id}",
            outcome=PredictionOutcome.FALSE_NEGATIVE,
            actual_incident_id=incident_id,
            notes=reason,
        )

        return analysis

    def mark_detection_fixed(
        self,
        incident_id: str,
        now_detectable: bool = True,
    ) -> bool:
        """Mark that detection for a missed incident type has been fixed."""
        for analysis in self._missed_incidents:
            if analysis.incident_id == incident_id:
                analysis.fix_implemented = True
                analysis.now_detectable = now_detectable
                return True
        return False

    def calculate_metrics(
        self,
        days: int = 30,
    ) -> CalibrationMetrics:
        """Calculate calibration metrics for recent predictions."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        relevant = [
            r for r in self._records.values()
            if r.predicted_at >= cutoff and r.outcome != PredictionOutcome.PENDING
        ]

        true_positives = sum(
            1 for r in relevant if r.outcome == PredictionOutcome.TRUE_POSITIVE
        )
        false_positives = sum(
            1 for r in relevant if r.outcome == PredictionOutcome.FALSE_POSITIVE
        )
        true_negatives = sum(
            1 for r in relevant if r.outcome == PredictionOutcome.TRUE_NEGATIVE
        )
        false_negatives = sum(
            1 for r in relevant if r.outcome == PredictionOutcome.FALSE_NEGATIVE
        )
        prevented = sum(
            1 for r in relevant if r.outcome == PredictionOutcome.PREVENTED
        )
        pending = sum(
            1 for r in self._records.values()
            if r.predicted_at >= cutoff and r.outcome == PredictionOutcome.PENDING
        )

        buckets = self._calculate_buckets(relevant)
        brier = self._calculate_brier_score(relevant)
        ece = self._calculate_ece(buckets)

        total = true_positives + false_positives + true_negatives + false_negatives
        accuracy = (true_positives + true_negatives) / total if total > 0 else 0.0

        precision = (
            true_positives / (true_positives + false_positives)
            if (true_positives + false_positives) > 0
            else 0.0
        )
        recall = (
            true_positives / (true_positives + false_negatives)
            if (true_positives + false_negatives) > 0
            else 0.0
        )
        f1 = (
            2 * precision * recall / (precision + recall)
            if (precision + recall) > 0
            else 0.0
        )

        return CalibrationMetrics(
            total_predictions=len(relevant),
            true_positives=true_positives,
            false_positives=false_positives,
            true_negatives=true_negatives,
            false_negatives=false_negatives,
            prevented=prevented,
            pending=pending,
            buckets=buckets,
            brier_score=brier,
            expected_calibration_error=ece,
            accuracy=accuracy,
            precision=precision,
            recall=recall,
            f1_score=f1,
            calculated_at=datetime.now(timezone.utc),
        )

    def _calculate_buckets(
        self,
        records: list[PredictionRecord],
    ) -> list[CalibrationBucket]:
        """Calculate calibration buckets."""
        buckets = []

        for low, high in self._bucket_ranges:
            bucket_records = [
                r for r in records
                if low <= r.predicted_confidence < high
            ]

            predictions = len(bucket_records)
            actual = sum(
                1 for r in bucket_records
                if r.outcome in (
                    PredictionOutcome.TRUE_POSITIVE,
                    PredictionOutcome.PREVENTED,
                )
            )

            buckets.append(
                CalibrationBucket(
                    confidence_range=(low, high),
                    predictions=predictions,
                    actual_occurrences=actual,
                )
            )

        return buckets

    def _calculate_brier_score(
        self,
        records: list[PredictionRecord],
    ) -> float:
        """Calculate Brier score (lower is better)."""
        if not records:
            return 0.0

        total = 0.0
        for record in records:
            actual = 1.0 if record.outcome in (
                PredictionOutcome.TRUE_POSITIVE,
                PredictionOutcome.PREVENTED,
            ) else 0.0
            total += (record.predicted_confidence - actual) ** 2

        return total / len(records)

    def _calculate_ece(
        self,
        buckets: list[CalibrationBucket],
    ) -> float:
        """Calculate Expected Calibration Error."""
        total_predictions = sum(b.predictions for b in buckets)
        if total_predictions == 0:
            return 0.0

        ece = sum(
            (b.predictions / total_predictions) * b.calibration_error
            for b in buckets
            if b.predictions > 0
        )

        return ece

    def adjust_confidence(
        self,
        raw_confidence: float,
        incident_type: str,
    ) -> float:
        """Adjust confidence based on historical calibration."""
        relevant = [
            r for r in self._records.values()
            if r.incident_type == incident_type
            and r.outcome != PredictionOutcome.PENDING
        ]

        if len(relevant) < 10:
            return raw_confidence

        bucket = None
        for low, high in self._bucket_ranges:
            if low <= raw_confidence < high:
                bucket_records = [
                    r for r in relevant if low <= r.predicted_confidence < high
                ]
                if len(bucket_records) >= 5:
                    actual_rate = sum(
                        1 for r in bucket_records
                        if r.outcome == PredictionOutcome.TRUE_POSITIVE
                    ) / len(bucket_records)

                    expected_rate = (low + high) / 2
                    adjustment = actual_rate - expected_rate
                    adjusted = raw_confidence + (adjustment * 0.5)
                    return max(0.0, min(1.0, adjusted))

        return raw_confidence

    def get_missed_incidents(
        self,
        fixed_only: bool = False,
    ) -> list[MissedIncidentAnalysis]:
        """Get list of missed incidents."""
        if fixed_only:
            return [m for m in self._missed_incidents if m.fix_implemented]
        return self._missed_incidents.copy()

    def get_calibration_summary(self) -> dict[str, Any]:
        """Get a summary of calibration status."""
        metrics = self.calculate_metrics()

        return {
            "is_well_calibrated": metrics.is_well_calibrated,
            "accuracy": metrics.accuracy,
            "precision": metrics.precision,
            "recall": metrics.recall,
            "f1_score": metrics.f1_score,
            "brier_score": metrics.brier_score,
            "ece": metrics.expected_calibration_error,
            "total_predictions": metrics.total_predictions,
            "missed_incidents": len(self._missed_incidents),
            "missed_now_detectable": sum(
                1 for m in self._missed_incidents if m.now_detectable
            ),
        }

    def get_dashboard_data(self) -> dict[str, Any]:
        """Get data for the calibration dashboard."""
        metrics = self.calculate_metrics()
        missed = self.get_missed_incidents()

        return {
            "metrics": metrics.to_dict(),
            "missed_incidents": [m.to_dict() for m in missed[-5:]],
            "interpretation": self._generate_interpretation(metrics),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    def _generate_interpretation(
        self,
        metrics: CalibrationMetrics,
    ) -> str:
        """Generate human-readable interpretation of metrics."""
        if metrics.total_predictions < 10:
            return "Not enough predictions yet for reliable calibration analysis."

        if metrics.is_well_calibrated:
            if metrics.precision >= 0.9:
                return (
                    f"Predictions are well-calibrated and highly accurate. "
                    f"When we predict with 90% confidence, incidents occur "
                    f"{metrics.buckets[-1].actual_rate*100:.0f}% of the time."
                )
            return "Predictions are well-calibrated. Confidence scores are reliable."

        overconfident = [b for b in metrics.buckets if b.status == "overconfident"]
        if overconfident:
            return (
                "Predictions tend to be overconfident. Consider lowering "
                "confidence thresholds for alerting."
            )

        return "Predictions may be underconfident. Consider raising alert thresholds."


_calibrator: ConfidenceCalibrator | None = None


def get_confidence_calibrator() -> ConfidenceCalibrator:
    """Get the default confidence calibrator."""
    global _calibrator
    if _calibrator is None:
        _calibrator = ConfidenceCalibrator()
    return _calibrator
