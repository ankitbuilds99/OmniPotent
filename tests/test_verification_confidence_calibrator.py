"""Tests for confidence_calibrator.py - confidence calibration and scoring."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock

from app.verification.confidence_calibrator import (
    CalibrationBin,
    CalibrationResult,
    ConfidenceCalibrator,
    PlattScaling,
    IsotonicCalibration,
)


class TestCalibrationBin:
    def test_accuracy_with_predictions(self):
        bin_ = CalibrationBin(bin_index=5, lower_bound=0.5, upper_bound=0.6, predictions=100, correct=75)
        assert bin_.accuracy == 0.75

    def test_accuracy_no_predictions(self):
        """An empty bin has no observed accuracy — it must not report one.

        This used to return the bin midpoint (0.55), which made the reliability
        diagram draw a perfect 45-degree line over zero recorded outcomes:
        accuracy == confidence in every bin, i.e. "flawlessly calibrated"
        rendered from no data at all.
        """
        bin_ = CalibrationBin(bin_index=5, lower_bound=0.5, upper_bound=0.6, predictions=0, correct=0)
        assert bin_.accuracy is None

    def test_mean_confidence(self):
        bin_ = CalibrationBin(bin_index=3, lower_bound=0.3, upper_bound=0.4)
        assert bin_.mean_confidence == 0.35

    def test_to_dict(self):
        bin_ = CalibrationBin(bin_index=2, lower_bound=0.2, upper_bound=0.3, predictions=50, correct=40)
        result = bin_.to_dict()
        assert result["bin"] == 2
        assert result["accuracy"] == 0.8


class TestCalibrationResult:
    def test_to_dict(self):
        result = CalibrationResult(
            original_confidence=0.9, calibrated_confidence=0.75, overconfidence_detected=True,
            calibration_adjustment=-0.15, evidence_strength=0.6, explanation="Test"
        )
        d = result.to_dict()
        assert d["original_confidence"] == 0.9


class TestConfidenceCalibrator:
    def test_init_default_bins(self):
        cal = ConfidenceCalibrator(num_bins=10)
        assert "default" in cal._bins
        assert len(cal._bins["default"]) == 10

    @pytest.mark.asyncio
    async def test_calibrate_confidence_clamps_input(self):
        cal = ConfidenceCalibrator()
        result = await cal.calibrate_confidence(claim_type="test", stated_confidence=1.5, evidence_strength=-0.5)
        assert 0.0 <= result.calibrated_confidence <= 1.0

    @pytest.mark.asyncio
    async def test_calibrate_confidence_new_claim_type(self):
        cal = ConfidenceCalibrator()
        await cal.calibrate_confidence(claim_type="new_type", stated_confidence=0.8, evidence_strength=0.7)
        assert "new_type" in cal._bins

    @pytest.mark.asyncio
    async def test_record_outcome_updates_bin(self):
        cal = ConfidenceCalibrator()
        await cal.record_outcome("default", 0.75, True)
        assert cal._bins["default"][7].predictions == 1

    @pytest.mark.asyncio
    async def test_record_outcome_with_redis(self):
        mock_redis = AsyncMock()
        mock_redis.smembers.return_value = set()
        mock_redis.hgetall.return_value = {}
        cal = ConfidenceCalibrator(redis_client=mock_redis)
        await cal.record_outcome("default", 0.5, True)
        mock_redis.hset.assert_called()

    def test_get_calibration_curve(self):
        cal = ConfidenceCalibrator()
        curve = cal.get_calibration_curve("default")
        assert len(curve) == 10

    def test_get_calibration_curve_unknown_type(self):
        cal = ConfidenceCalibrator()
        curve = cal.get_calibration_curve("nonexistent")
        assert curve == []

    def test_calculate_brier_score_no_predictions(self):
        cal = ConfidenceCalibrator()
        score = cal.calculate_brier_score("default")
        assert score is None

    def test_calculate_ece_no_predictions(self):
        cal = ConfidenceCalibrator()
        ece = cal.calculate_ece("default")
        assert ece is None


class TestPlattScaling:
    def test_init(self):
        ps = PlattScaling()
        assert ps._fitted is False

    def test_fit_insufficient_data(self):
        ps = PlattScaling()
        ps.fit([0.5, 0.6, 0.7], [True, True, False])
        assert ps._fitted is False

    def test_fit_success(self):
        ps = PlattScaling()
        ps.fit([0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95], [False]*4 + [True]*6)
        assert ps._fitted is True

    def test_calibrate_unfitted(self):
        ps = PlattScaling()
        assert ps.calibrate(0.7) == 0.7

    def test_sigmoid(self):
        assert PlattScaling._sigmoid(0) == 0.5


class TestIsotonicCalibration:
    def test_init(self):
        ic = IsotonicCalibration()
        assert ic._fitted is False

    def test_fit_insufficient_data(self):
        ic = IsotonicCalibration()
        ic.fit([0.5, 0.6, 0.7], [True, True, False])
        assert ic._fitted is False

    def test_fit_success(self):
        ic = IsotonicCalibration()
        ic.fit([0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95], [False]*4 + [True]*6)
        assert ic._fitted is True

    def test_calibrate_unfitted(self):
        ic = IsotonicCalibration()
        assert ic.calibrate(0.7) == 0.7


class _InMemoryRedis:
    """A hash+set store with the four commands the calibrator uses.

    A test double at the network boundary only. Production code never gets a
    substitute like this: with no COPILOT_REDIS_URL the calibrator keeps its
    counts in process memory and GET /calibration reports persistent=false.
    """

    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, str]] = {}
        self.sets: dict[str, set[str]] = {}

    async def hset(self, key: str, mapping: dict[str, object]) -> None:
        self.hashes.setdefault(key, {}).update(
            {k: str(v) for k, v in mapping.items()}
        )

    async def hgetall(self, key: str) -> dict[bytes, bytes]:
        return {
            k.encode(): v.encode() for k, v in self.hashes.get(key, {}).items()
        }

    async def sadd(self, key: str, *members: str) -> None:
        self.sets.setdefault(key, set()).update(members)

    async def smembers(self, key: str) -> set[bytes]:
        return {m.encode() for m in self.sets.get(key, set())}


class TestCalibrationPersistence:
    """Recorded outcomes have to survive a restart when Redis is configured."""

    def test_reports_where_its_counts_live(self):
        assert ConfidenceCalibrator().is_persistent is False
        assert "COPILOT_REDIS_URL" in ConfidenceCalibrator().storage_description

        with_redis = ConfidenceCalibrator(redis_client=_InMemoryRedis())
        assert with_redis.is_persistent is True
        assert "redis" in with_redis.storage_description

    @pytest.mark.asyncio
    async def test_outcomes_survive_a_restart(self):
        redis = _InMemoryRedis()

        first = ConfidenceCalibrator(redis_client=redis)
        await first.record_outcome("root_cause", 0.75, True)
        await first.record_outcome("root_cause", 0.75, False)
        assert first.total_predictions() == 2

        # A brand new process, same Redis.
        second = ConfidenceCalibrator(redis_client=redis)
        assert second.total_predictions() == 0  # nothing loaded yet
        await second.ensure_loaded()

        assert second.total_predictions() == 2
        assert second.total_predictions("root_cause") == 2
        assert second._bins["root_cause"][7].correct == 1
        assert second.calculate_brier_score() is not None

    @pytest.mark.asyncio
    async def test_first_write_after_restart_does_not_clobber_history(self):
        redis = _InMemoryRedis()

        first = ConfidenceCalibrator(redis_client=redis)
        for _ in range(5):
            await first.record_outcome("default", 0.75, True)

        second = ConfidenceCalibrator(redis_client=redis)
        await second.record_outcome("default", 0.75, False)

        assert second.total_predictions() == 6
        assert second._bins["default"][7].correct == 5
