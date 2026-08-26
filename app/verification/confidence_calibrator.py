"""Confidence Calibration for AI Predictions.

Calibrates and validates confidence scores using:
- Historical accuracy tracking
- Platt scaling
- Isotonic regression
- Brier score calculation
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any

log = logging.getLogger("omnipotent.verification.confidence_calibrator")

# Bin set that accumulates every recorded outcome regardless of claim type, so
# the calibration endpoint can report overall Brier/ECE.
OVERALL_CLAIM_TYPE = "default"

# Redis set recording which claim types have persisted bins, so a restarted
# process can restore all of them instead of only the ones it happens to ask for.
CLAIM_TYPES_KEY = "calibration:claim_types"


@dataclass
class CalibrationBin:
    """A calibration bin for tracking accuracy at confidence levels."""
    bin_index: int
    lower_bound: float
    upper_bound: float
    predictions: int = 0
    correct: int = 0

    @property
    def accuracy(self) -> float | None:
        """Observed accuracy, or None when nothing has been recorded.

        Returning the bin midpoint for an empty bin made every reliability
        diagram draw a perfect 45-degree line over zero samples, which reads as
        "this system is perfectly calibrated" when the truth is "no outcome has
        ever been recorded".
        """
        if self.predictions == 0:
            return None
        return self.correct / self.predictions

    @property
    def mean_confidence(self) -> float:
        return (self.lower_bound + self.upper_bound) / 2

    def to_dict(self) -> dict[str, Any]:
        return {
            "bin": self.bin_index,
            "range": f"{self.lower_bound:.0%}-{self.upper_bound:.0%}",
            "predictions": self.predictions,
            "correct": self.correct,
            "accuracy": self.accuracy,
        }


@dataclass
class CalibrationResult:
    """Result of confidence calibration."""
    original_confidence: float
    calibrated_confidence: float
    overconfidence_detected: bool
    calibration_adjustment: float
    evidence_strength: float
    explanation: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "original_confidence": self.original_confidence,
            "calibrated_confidence": self.calibrated_confidence,
            "overconfidence_detected": self.overconfidence_detected,
            "calibration_adjustment": self.calibration_adjustment,
            "evidence_strength": self.evidence_strength,
            "explanation": self.explanation,
        }


class ConfidenceCalibrator:
    """Calibrates and validates confidence scores.

    Uses historical accuracy to adjust stated confidence levels.
    Implements proper scoring rules and calibration methods.
    """

    def __init__(
        self,
        redis_client: Any = None,
        num_bins: int = 10,
    ):
        self.redis = redis_client
        self.num_bins = num_bins
        self._bins: dict[str, list[CalibrationBin]] = {}
        self._loaded = False
        self._initialize_bins(OVERALL_CLAIM_TYPE)

    @property
    def is_persistent(self) -> bool:
        """Whether recorded outcomes survive a restart / are shared by workers."""
        return self.redis is not None

    @property
    def storage_description(self) -> str:
        """Where recorded outcomes live, in words a reader can act on."""
        if self.redis is not None:
            return "redis (keys: calibration:<claim_type>:bin:<n>)"
        return (
            "process memory: recorded outcomes are lost on restart and are not "
            "shared between workers. Set COPILOT_REDIS_URL to persist them."
        )

    async def ensure_loaded(self) -> None:
        """Restore persisted bins from Redis, once, if Redis is configured."""
        if self.redis is None or self._loaded:
            return
        self._loaded = True

        claim_types = {OVERALL_CLAIM_TYPE}
        try:
            members = await self.redis.smembers(CLAIM_TYPES_KEY)
            for member in members or []:
                claim_types.add(
                    member.decode() if isinstance(member, bytes) else str(member)
                )
        except Exception as e:
            log.debug("Failed to list persisted calibration claim types: %s", e)

        for claim_type in sorted(claim_types):
            if claim_type not in self._bins:
                self._initialize_bins(claim_type)
            await self._load_calibration_data(claim_type)

    def _initialize_bins(self, claim_type: str) -> None:
        """Initialize calibration bins for a claim type."""
        bins = []
        for i in range(self.num_bins):
            lower = i / self.num_bins
            upper = (i + 1) / self.num_bins
            bins.append(CalibrationBin(
                bin_index=i,
                lower_bound=lower,
                upper_bound=upper,
            ))
        self._bins[claim_type] = bins

    async def calibrate_confidence(
        self,
        claim_type: str,
        stated_confidence: float,
        evidence_strength: float,
    ) -> CalibrationResult:
        """Calibrate a confidence score based on historical accuracy.

        Args:
            claim_type: Type of claim being made
            stated_confidence: The AI's stated confidence (0-1)
            evidence_strength: Quality/quantity of evidence (0-1)

        Returns:
            CalibrationResult with calibrated confidence and metadata
        """
        stated_confidence = max(0.0, min(1.0, stated_confidence))
        evidence_strength = max(0.0, min(1.0, evidence_strength))

        await self.ensure_loaded()

        if claim_type not in self._bins:
            self._initialize_bins(claim_type)
            await self._load_calibration_data(claim_type)

        bin_idx = min(int(stated_confidence * self.num_bins), self.num_bins - 1)
        calibration_bin = self._bins[claim_type][bin_idx]
        historical_accuracy = calibration_bin.accuracy

        if historical_accuracy is None:
            # No outcome has ever been recorded in this band. Blending in a
            # made-up "historical accuracy" would dress a guess up as evidence,
            # so the historical term is dropped and its weight redistributed.
            calibrated = max(0.0, min(1.0, stated_confidence * 0.55 + evidence_strength * 0.45))
            return CalibrationResult(
                original_confidence=stated_confidence,
                calibrated_confidence=calibrated,
                overconfidence_detected=False,
                calibration_adjustment=calibrated - stated_confidence,
                evidence_strength=evidence_strength,
                explanation=(
                    "No recorded outcomes for this confidence band yet; calibrated "
                    "from evidence strength alone (no historical accuracy available)"
                ),
            )

        calibrated = (
            stated_confidence * 0.4 +
            historical_accuracy * 0.3 +
            evidence_strength * 0.3
        )

        calibrated = max(0.0, min(1.0, calibrated))

        overconfident = stated_confidence > (historical_accuracy + 0.1)
        adjustment = calibrated - stated_confidence

        if overconfident:
            explanation = (
                f"Stated confidence {stated_confidence:.0%} exceeds "
                f"historical accuracy {historical_accuracy:.0%}"
            )
        elif adjustment > 0.1:
            explanation = f"Evidence strength {evidence_strength:.0%} supports higher confidence"
        elif adjustment < -0.1:
            explanation = f"Historical accuracy {historical_accuracy:.0%} suggests lower confidence"
        else:
            explanation = "Confidence is well-calibrated"

        return CalibrationResult(
            original_confidence=stated_confidence,
            calibrated_confidence=calibrated,
            overconfidence_detected=overconfident,
            calibration_adjustment=adjustment,
            evidence_strength=evidence_strength,
            explanation=explanation,
        )

    async def record_outcome(
        self,
        claim_type: str,
        stated_confidence: float,
        was_correct: bool,
    ) -> None:
        """Record an outcome for calibration improvement.

        Args:
            claim_type: Type of claim that was made
            stated_confidence: The confidence that was stated
            was_correct: Whether the prediction was correct
        """
        stated_confidence = max(0.0, min(1.0, stated_confidence))

        # Load before incrementing: otherwise the first outcome after a restart
        # would write predictions=1 over whatever Redis already held.
        await self.ensure_loaded()

        self._record_in_bins(claim_type, stated_confidence, was_correct)
        if self.redis:
            await self._save_calibration_data(claim_type)

        # OVERALL_CLAIM_TYPE doubles as the cross-type aggregate so that
        # GET /calibration has something truthful to report without the caller
        # having to know every claim_type the agent has ever seen.
        if claim_type != OVERALL_CLAIM_TYPE:
            self._record_in_bins(OVERALL_CLAIM_TYPE, stated_confidence, was_correct)
            if self.redis:
                await self._save_calibration_data(OVERALL_CLAIM_TYPE)

    def _record_in_bins(
        self,
        claim_type: str,
        stated_confidence: float,
        was_correct: bool,
    ) -> None:
        if claim_type not in self._bins:
            self._initialize_bins(claim_type)

        bin_idx = min(int(stated_confidence * self.num_bins), self.num_bins - 1)
        calibration_bin = self._bins[claim_type][bin_idx]

        calibration_bin.predictions += 1
        if was_correct:
            calibration_bin.correct += 1

    def total_predictions(self, claim_type: str = OVERALL_CLAIM_TYPE) -> int:
        """How many outcomes have actually been recorded for a claim type."""
        return sum(b.predictions for b in self._bins.get(claim_type, []))

    def known_claim_types(self) -> list[str]:
        """Claim types that have at least one bin (recorded or calibrated)."""
        return sorted(self._bins)

    async def _load_calibration_data(self, claim_type: str) -> None:
        """Load calibration data from Redis."""
        if not self.redis:
            return

        for i in range(self.num_bins):
            key = f"calibration:{claim_type}:bin:{i}"
            try:
                data = await self.redis.hgetall(key)
                if data:
                    # Clients differ on decode_responses, so accept either.
                    fields = {
                        (k.decode() if isinstance(k, bytes) else str(k)): v
                        for k, v in data.items()
                    }
                    self._bins[claim_type][i].predictions = int(fields.get("predictions", 0))
                    self._bins[claim_type][i].correct = int(fields.get("correct", 0))
            except Exception as e:
                log.debug("Failed to load calibration data: %s", e)

    async def _save_calibration_data(self, claim_type: str) -> None:
        """Save calibration data to Redis."""
        if not self.redis:
            return

        try:
            await self.redis.sadd(CLAIM_TYPES_KEY, claim_type)
        except Exception as e:
            log.debug("Failed to register calibration claim type: %s", e)

        for i, cal_bin in enumerate(self._bins[claim_type]):
            key = f"calibration:{claim_type}:bin:{i}"
            try:
                await self.redis.hset(key, mapping={
                    "predictions": cal_bin.predictions,
                    "correct": cal_bin.correct,
                })
            except Exception as e:
                log.debug("Failed to save calibration data: %s", e)

    def get_calibration_curve(self, claim_type: str = "default") -> list[dict[str, Any]]:
        """Get the calibration curve for a claim type."""
        if claim_type not in self._bins:
            return []

        return [cal_bin.to_dict() for cal_bin in self._bins[claim_type]]

    def calculate_brier_score(self, claim_type: str = "default") -> float | None:
        """Calculate Brier score for calibration quality.

        Lower is better. 0 = perfect, 0.25 = random guessing.
        """
        if claim_type not in self._bins:
            return None

        total_predictions = 0
        brier_sum = 0.0

        for cal_bin in self._bins[claim_type]:
            if cal_bin.predictions == 0:
                continue

            mean_confidence = cal_bin.mean_confidence
            accuracy = cal_bin.accuracy
            if accuracy is None:
                continue

            brier_sum += cal_bin.predictions * (mean_confidence - accuracy) ** 2
            total_predictions += cal_bin.predictions

        if total_predictions == 0:
            return None

        return brier_sum / total_predictions

    def calculate_ece(self, claim_type: str = "default") -> float | None:
        """Calculate Expected Calibration Error.

        Lower is better. 0 = perfectly calibrated.
        """
        if claim_type not in self._bins:
            return None

        total_predictions = sum(b.predictions for b in self._bins[claim_type])
        if total_predictions == 0:
            return None

        ece = 0.0
        for cal_bin in self._bins[claim_type]:
            if cal_bin.predictions == 0:
                continue

            accuracy = cal_bin.accuracy
            if accuracy is None:
                continue

            weight = cal_bin.predictions / total_predictions
            calibration_error = abs(accuracy - cal_bin.mean_confidence)
            ece += weight * calibration_error

        return ece

    def get_reliability_diagram_data(
        self,
        claim_type: str = "default",
    ) -> dict[str, Any]:
        """Get data for plotting a reliability diagram."""
        if claim_type not in self._bins:
            return {"bins": [], "perfect_calibration": []}

        bins_data = []
        for cal_bin in self._bins[claim_type]:
            bins_data.append({
                "confidence": cal_bin.mean_confidence,
                "accuracy": cal_bin.accuracy,
                "samples": cal_bin.predictions,
            })

        return {
            "bins": bins_data,
            # Only meaningful once something has been recorded; a client that
            # plots this against zero-sample bins is drawing a line over no data.
            "total_samples": sum(b["samples"] for b in bins_data),
            "perfect_calibration": [{"x": i / 10, "y": i / 10} for i in range(11)],
            "brier_score": self.calculate_brier_score(claim_type),
            "ece": self.calculate_ece(claim_type),
        }


class PlattScaling:
    """Platt scaling for probability calibration.

    Fits a sigmoid function to calibrate probabilities.
    """

    def __init__(self):
        self.a: float = 0.0
        self.b: float = 0.0
        self._fitted = False

    def fit(
        self,
        predictions: list[float],
        outcomes: list[bool],
    ) -> None:
        """Fit Platt scaling parameters.

        Uses simple gradient descent for fitting.
        """
        if len(predictions) != len(outcomes) or len(predictions) < 10:
            return

        self.a = 1.0
        self.b = 0.0

        learning_rate = 0.01
        for _ in range(1000):
            grad_a = 0.0
            grad_b = 0.0

            for pred, outcome in zip(predictions, outcomes):
                calibrated = self._sigmoid(self.a * pred + self.b)
                error = calibrated - (1.0 if outcome else 0.0)
                grad_a += error * pred
                grad_b += error

            self.a -= learning_rate * grad_a / len(predictions)
            self.b -= learning_rate * grad_b / len(predictions)

        self._fitted = True

    def calibrate(self, probability: float) -> float:
        """Calibrate a probability using fitted parameters."""
        if not self._fitted:
            return probability

        return self._sigmoid(self.a * probability + self.b)

    @staticmethod
    def _sigmoid(x: float) -> float:
        """Numerically stable sigmoid function."""
        if x >= 0:
            return 1 / (1 + math.exp(-x))
        else:
            exp_x = math.exp(x)
            return exp_x / (1 + exp_x)


class IsotonicCalibration:
    """Isotonic regression for probability calibration.

    Non-parametric calibration method.
    """

    def __init__(self):
        self._calibration_map: list[tuple[float, float]] = []
        self._fitted = False

    def fit(
        self,
        predictions: list[float],
        outcomes: list[bool],
    ) -> None:
        """Fit isotonic calibration.

        Uses pool adjacent violators algorithm.
        """
        if len(predictions) != len(outcomes) or len(predictions) < 10:
            return

        data = sorted(zip(predictions, outcomes))
        n = len(data)

        blocks: list[list[int]] = [[i] for i in range(n)]
        weights: list[int] = [1] * n
        values: list[float] = [1.0 if d[1] else 0.0 for d in data]

        changed = True
        while changed:
            changed = False
            i = 0
            while i < len(blocks) - 1:
                val_i = values[i]
                val_j = values[i + 1]

                if val_i > val_j:
                    new_val = (val_i * weights[i] + val_j * weights[i + 1]) / (
                        weights[i] + weights[i + 1]
                    )
                    values[i] = new_val
                    weights[i] = weights[i] + weights[i + 1]
                    blocks[i].extend(blocks[i + 1])
                    del blocks[i + 1]
                    del values[i + 1]
                    del weights[i + 1]
                    changed = True
                else:
                    i += 1

        self._calibration_map = []
        for block, val in zip(blocks, values):
            for idx in block:
                self._calibration_map.append((data[idx][0], val))

        self._calibration_map.sort()
        self._fitted = True

    def calibrate(self, probability: float) -> float:
        """Calibrate a probability using fitted isotonic regression."""
        if not self._fitted or not self._calibration_map:
            return probability

        if probability <= self._calibration_map[0][0]:
            return self._calibration_map[0][1]
        if probability >= self._calibration_map[-1][0]:
            return self._calibration_map[-1][1]

        for i in range(len(self._calibration_map) - 1):
            x1, y1 = self._calibration_map[i]
            x2, y2 = self._calibration_map[i + 1]

            if x1 <= probability <= x2:
                if x2 == x1:
                    return y1
                t = (probability - x1) / (x2 - x1)
                return y1 + t * (y2 - y1)

        return probability
