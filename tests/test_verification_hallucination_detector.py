"""Tests for hallucination_detector.py - detecting AI hallucinations."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.verification.hallucination_detector import (
    HallucinationType,
    HallucinationResult,
    HallucinationDetector,
    ConfidenceOverconfidenceChecker,
)
from app.verification.evidence_collector import Evidence, EvidenceSource


class TestHallucinationType:
    """Tests for HallucinationType enum."""

    def test_all_types(self):
        """Should have all expected hallucination types."""
        types = [t.value for t in HallucinationType]
        assert "fabricated_fact" in types
        assert "wrong_attribution" in types
        assert "temporal_error" in types
        assert "logical_fallacy" in types
        assert "overconfidence" in types
        assert "conflation" in types
        assert "unsupported_claim" in types


class TestHallucinationResult:
    """Tests for HallucinationResult dataclass."""

    def test_defaults(self):
        """Should initialize with defaults."""
        result = HallucinationResult(detected=False)
        assert result.hallucination_type is None
        assert result.explanation == ""
        assert result.severity == 0.0
        assert result.affected_claims == []

    def test_to_dict_no_hallucination(self):
        """Should serialize when no hallucination."""
        result = HallucinationResult(detected=False)
        d = result.to_dict()
        assert d["detected"] is False
        assert d["type"] is None

    def test_to_dict_with_hallucination(self):
        """Should serialize hallucination details."""
        result = HallucinationResult(
            detected=True,
            hallucination_type=HallucinationType.FABRICATED_FACT,
            explanation="Value 99% not found in evidence",
            severity=0.8,
            affected_claims=["claim1"],
        )
        d = result.to_dict()
        assert d["detected"] is True
        assert d["type"] == "fabricated_fact"
        assert d["severity"] == 0.8
        assert len(d["affected_claims"]) == 1


class TestHallucinationDetector:
    """Tests for HallucinationDetector class."""

    def test_init_no_llm(self):
        """Should initialize without LLM."""
        detector = HallucinationDetector()
        assert detector.llm is None
        assert detector._fact_cache == {}

    def test_init_with_llm(self):
        """Should accept LLM client."""
        mock_llm = MagicMock()
        detector = HallucinationDetector(llm_client=mock_llm)
        assert detector.llm is mock_llm

    @pytest.mark.asyncio
    async def test_check_for_hallucinations_no_issues(self):
        """Should return not detected when claim is valid."""
        detector = HallucinationDetector()
        evidence = [
            Evidence(
                source=EvidenceSource.METRICS,
                data={"result": [{"value": [0, "0.05"]}]},
                timestamp=datetime.now(timezone.utc),
                confidence=0.9,
                metric_name="error_rate",
                metric_value=0.05,
            )
        ]
        result = await detector.check_for_hallucinations(
            claim_text="Error rate is 5%",
            evidence=evidence,
            cited_values={"error_rate": 0.05},
        )
        assert result.detected is False

    @pytest.mark.asyncio
    async def test_check_for_hallucinations_fabricated_numeric(self):
        """Should detect fabricated numeric claims."""
        detector = HallucinationDetector()
        evidence = [
            Evidence(
                source=EvidenceSource.METRICS,
                data={},
                timestamp=datetime.now(timezone.utc),
                confidence=0.9,
                metric_name="error_rate",
                metric_value=0.01,
            )
        ]
        result = await detector.check_for_hallucinations(
            claim_text="Error rate spiked to 99%",
            evidence=evidence,
        )
        assert result.detected is True
        assert result.hallucination_type == HallucinationType.FABRICATED_FACT
        assert "99" in result.explanation

    @pytest.mark.asyncio
    async def test_check_for_hallucinations_wrong_cited_value(self):
        """Should detect wrong cited values."""
        detector = HallucinationDetector()
        evidence = [
            Evidence(
                source=EvidenceSource.METRICS,
                data={},
                timestamp=datetime.now(timezone.utc),
                confidence=0.9,
                metric_name="latency_p99",
                metric_value=100,  # 100ms
            )
        ]
        result = await detector.check_for_hallucinations(
            claim_text="Latency is high",
            evidence=evidence,
            cited_values={"latency": 500},  # Claimed 500ms but evidence shows 100ms
        )
        assert result.detected is True
        assert result.hallucination_type == HallucinationType.FABRICATED_FACT

    @pytest.mark.asyncio
    async def test_check_for_hallucinations_wrong_source(self):
        """Should detect wrong source attribution."""
        detector = HallucinationDetector()
        evidence = [
            Evidence(
                source=EvidenceSource.LOGS,
                data={},
                timestamp=datetime.now(timezone.utc),
                confidence=0.8,
            )
        ]
        result = await detector.check_for_hallucinations(
            claim_text="According to metrics...",
            evidence=evidence,
            cited_sources=["prometheus"],
        )
        assert result.detected is True
        assert result.hallucination_type == HallucinationType.WRONG_ATTRIBUTION

    @pytest.mark.asyncio
    async def test_check_for_hallucinations_source_found(self):
        """Should accept valid source citations."""
        detector = HallucinationDetector()
        evidence = [
            Evidence(
                source=EvidenceSource.METRICS,
                data={},
                timestamp=datetime.now(timezone.utc),
                confidence=0.9,
                metric_name="error_rate",
            )
        ]
        result = await detector.check_for_hallucinations(
            claim_text="Error rate from metrics",
            evidence=evidence,
            cited_sources=["metrics"],
        )
        assert result.detected is False

    @pytest.mark.asyncio
    async def test_check_for_hallucinations_temporal_error(self):
        """Should detect temporal errors."""
        detector = HallucinationDetector()
        # Evidence from an hour ago
        evidence = [
            Evidence(
                source=EvidenceSource.LOGS,
                data={},
                timestamp=datetime.now(timezone.utc) - timedelta(hours=5),
                confidence=0.8,
            )
        ]
        result = await detector.check_for_hallucinations(
            claim_text="The error occurred 2 minutes ago",
            evidence=evidence,
        )
        assert result.detected is True
        assert result.hallucination_type == HallucinationType.TEMPORAL_ERROR

    @pytest.mark.asyncio
    async def test_check_for_hallucinations_temporal_valid(self):
        """Should accept valid temporal claims."""
        detector = HallucinationDetector()
        evidence = [
            Evidence(
                source=EvidenceSource.LOGS,
                data={},
                timestamp=datetime.now(timezone.utc) - timedelta(minutes=5),
                confidence=0.8,
            )
        ]
        result = await detector.check_for_hallucinations(
            claim_text="The event happened 5 minutes ago",
            evidence=evidence,
        )
        assert result.detected is False

    @pytest.mark.asyncio
    async def test_check_for_hallucinations_logical_fallacy_with_llm(self):
        """Should detect logical fallacies using LLM."""
        mock_llm = AsyncMock()
        mock_llm.complete.return_value = '{"has_fallacy": true, "fallacy_type": "circular", "explanation": "Circular reasoning detected"}'

        detector = HallucinationDetector(llm_client=mock_llm)
        evidence = []  # No other checks needed
        result = await detector.check_for_hallucinations(
            claim_text="The system is slow because it performs slowly",
            evidence=evidence,
        )
        assert result.detected is True
        assert result.hallucination_type == HallucinationType.LOGICAL_FALLACY

    @pytest.mark.asyncio
    async def test_check_for_hallucinations_logical_no_fallacy(self):
        """Should pass when no logical fallacy found."""
        mock_llm = AsyncMock()
        mock_llm.complete.return_value = '{"has_fallacy": false}'

        detector = HallucinationDetector(llm_client=mock_llm)
        result = await detector.check_for_hallucinations(
            claim_text="High traffic caused increased latency",
            evidence=[],
        )
        assert result.detected is False

    @pytest.mark.asyncio
    async def test_check_for_hallucinations_llm_error(self):
        """Should handle LLM errors gracefully."""
        mock_llm = AsyncMock()
        mock_llm.complete.side_effect = Exception("LLM unavailable")

        detector = HallucinationDetector(llm_client=mock_llm)
        result = await detector.check_for_hallucinations(
            claim_text="Test claim",
            evidence=[],
        )
        # Should not crash, return no hallucination
        assert result.detected is False

    @pytest.mark.asyncio
    async def test_check_for_hallucinations_llm_invalid_json(self):
        """Should handle invalid LLM JSON."""
        mock_llm = AsyncMock()
        mock_llm.complete.return_value = "This is not JSON"

        detector = HallucinationDetector(llm_client=mock_llm)
        result = await detector.check_for_hallucinations(
            claim_text="Test",
            evidence=[],
        )
        assert result.detected is False

    @pytest.mark.asyncio
    async def test_check_fact_grounding_value_in_data(self):
        """Should find values in evidence data."""
        detector = HallucinationDetector()
        evidence = [
            Evidence(
                source=EvidenceSource.LOGS,
                data={"count": 150},
                timestamp=datetime.now(timezone.utc),
                confidence=0.8,
            )
        ]
        result = await detector._check_fact_grounding(
            "There were 150 errors",
            evidence,
            {},
        )
        assert result.detected is False

    def test_extract_numeric_claims_ms(self):
        """Should extract millisecond values."""
        detector = HallucinationDetector()
        claims = detector._extract_numeric_claims("Latency increased to 250ms")
        assert any(v == 250 and "ms" in u for v, u, _ in claims)

    def test_extract_numeric_claims_percent(self):
        """Should extract percentage values."""
        detector = HallucinationDetector()
        claims = detector._extract_numeric_claims("Error rate is 5%")
        assert any(v == 5 and "%" in u for v, u, _ in claims)

    def test_extract_numeric_claims_bytes(self):
        """Should extract byte values."""
        detector = HallucinationDetector()
        claims = detector._extract_numeric_claims("Memory usage 512 MB")
        assert any(v == 512 and "MB" in u for v, u, _ in claims)

    def test_extract_numeric_claims_rps(self):
        """Should extract requests per second."""
        detector = HallucinationDetector()
        claims = detector._extract_numeric_claims("Processing 1000 requests/s")
        assert any(v == 1000 for v, u, _ in claims)

    def test_extract_numeric_claims_context(self):
        """Should capture surrounding context."""
        detector = HallucinationDetector()
        claims = detector._extract_numeric_claims(
            "The error rate suddenly spiked to 15% during the incident"
        )
        assert len(claims) > 0
        _, _, context = claims[0]
        assert "error" in context.lower() or "spike" in context.lower()

    def test_value_in_evidence_metric_exact(self):
        """Should match exact metric values."""
        detector = HallucinationDetector()
        ev = Evidence(
            source=EvidenceSource.METRICS,
            data={},
            timestamp=datetime.now(timezone.utc),
            confidence=0.9,
            metric_value=0.05,
        )
        assert detector._value_in_evidence(0.05, "%", ev) is True

    def test_value_in_evidence_metric_tolerance(self):
        """Should match within 10% tolerance."""
        detector = HallucinationDetector()
        ev = Evidence(
            source=EvidenceSource.METRICS,
            data={},
            timestamp=datetime.now(timezone.utc),
            confidence=0.9,
            metric_value=100,
        )
        assert detector._value_in_evidence(105, "ms", ev) is True  # 5% off
        assert detector._value_in_evidence(150, "ms", ev) is False  # 50% off

    def test_value_in_evidence_percent_conversion(self):
        """Should handle percent conversion (0.05 = 5%)."""
        detector = HallucinationDetector()
        ev = Evidence(
            source=EvidenceSource.METRICS,
            data={},
            timestamp=datetime.now(timezone.utc),
            confidence=0.9,
            metric_value=0.05,
        )
        assert detector._value_in_evidence(5, "%", ev) is True

    def test_value_in_evidence_data_json(self):
        """Should find value in evidence data JSON."""
        detector = HallucinationDetector()
        ev = Evidence(
            source=EvidenceSource.LOGS,
            data={"error_count": 42, "nested": {"value": 123}},
            timestamp=datetime.now(timezone.utc),
            confidence=0.8,
        )
        assert detector._value_in_evidence(42, "errors", ev) is True
        assert detector._value_in_evidence(123, "", ev) is True

    def test_extract_time_references_clock_time(self):
        """Should extract clock times."""
        detector = HallucinationDetector()
        refs = detector._extract_time_references("Error at 14:30 PM caused outage")
        assert len(refs) > 0
        assert "14:30" in refs[0][0]

    def test_extract_time_references_relative(self):
        """Should extract relative time references."""
        detector = HallucinationDetector()
        refs = detector._extract_time_references("Issue started 30 minutes ago")
        assert len(refs) > 0
        assert "minutes" in refs[0][0].lower()

    def test_extract_time_references_yesterday(self):
        """Should extract 'yesterday'."""
        detector = HallucinationDetector()
        refs = detector._extract_time_references("This happened yesterday afternoon")
        assert len(refs) > 0
        assert "yesterday" in refs[0][0].lower()

    def test_extract_time_references_iso(self):
        """Should extract ISO dates."""
        detector = HallucinationDetector()
        refs = detector._extract_time_references("Deployed on 2024-01-15T10:30")
        assert len(refs) > 0
        assert "2024-01-15" in refs[0][0]

    def test_parse_time_reference_minutes(self):
        """Should parse 'N minutes ago'."""
        detector = HallucinationDetector()
        now = datetime.now(timezone.utc)
        result = detector._parse_time_reference("30 minutes ago")
        assert result is not None
        assert (now - result).total_seconds() < 35 * 60

    def test_parse_time_reference_hours(self):
        """Should parse 'N hours ago'."""
        detector = HallucinationDetector()
        now = datetime.now(timezone.utc)
        result = detector._parse_time_reference("2 hours ago")
        assert result is not None
        assert 1.5 * 60 * 60 < (now - result).total_seconds() < 2.5 * 60 * 60

    def test_parse_time_reference_days(self):
        """Should parse 'N days ago'."""
        detector = HallucinationDetector()
        now = datetime.now(timezone.utc)
        result = detector._parse_time_reference("3 days ago")
        assert result is not None
        assert 2.5 * 24 * 60 * 60 < (now - result).total_seconds() < 3.5 * 24 * 60 * 60

    def test_parse_time_reference_yesterday(self):
        """Should parse 'yesterday'."""
        detector = HallucinationDetector()
        result = detector._parse_time_reference("yesterday")
        assert result is not None
        diff = datetime.now(timezone.utc) - result
        assert 0.5 * 24 * 60 * 60 < diff.total_seconds() < 1.5 * 24 * 60 * 60

    def test_parse_time_reference_today(self):
        """Should parse 'today'."""
        detector = HallucinationDetector()
        result = detector._parse_time_reference("today")
        assert result is not None
        diff = datetime.now(timezone.utc) - result
        assert diff.total_seconds() < 60  # Within a minute

    def test_parse_time_reference_iso_datetime(self):
        """Should parse ISO datetime."""
        detector = HallucinationDetector()
        result = detector._parse_time_reference("2024-01-15T10:30")
        assert result is not None
        assert result.year == 2024
        assert result.month == 1
        assert result.day == 15

    def test_parse_time_reference_iso_date(self):
        """Should parse ISO date."""
        detector = HallucinationDetector()
        result = detector._parse_time_reference("2024-01-15")
        assert result is not None
        assert result.year == 2024

    def test_parse_time_reference_invalid(self):
        """Should return None for invalid time reference."""
        detector = HallucinationDetector()
        result = detector._parse_time_reference("some random text")
        assert result is None

    def test_time_matches_within_tolerance(self):
        """Should match times within 30-minute tolerance."""
        detector = HallucinationDetector()
        now = datetime.now(timezone.utc)
        assert detector._time_matches(now, now) is True
        assert detector._time_matches(now, now + timedelta(minutes=15)) is True
        assert detector._time_matches(now, now - timedelta(minutes=15)) is True

    def test_time_matches_outside_tolerance(self):
        """Should not match times outside tolerance."""
        detector = HallucinationDetector()
        now = datetime.now(timezone.utc)
        assert detector._time_matches(now, now + timedelta(hours=1)) is False
        assert detector._time_matches(now, now - timedelta(hours=1)) is False


class TestConfidenceOverconfidenceChecker:
    """Tests for ConfidenceOverconfidenceChecker class."""

    def test_init_no_history(self):
        """Should initialize without history."""
        checker = ConfidenceOverconfidenceChecker()
        assert checker.historical_accuracy == {}

    def test_init_with_history(self):
        """Should accept historical accuracy."""
        history = {"root_cause": 0.75, "general": 0.80}
        checker = ConfidenceOverconfidenceChecker(historical_accuracy=history)
        assert checker.historical_accuracy == history

    def test_check_overconfidence_insufficient_evidence(self):
        """Should detect overconfidence with insufficient evidence."""
        checker = ConfidenceOverconfidenceChecker()
        is_overconfident, msg = checker.check_overconfidence(
            stated_confidence=0.95,
            evidence_count=1,  # Too few for 95% confidence
            evidence_quality=0.8,
        )
        assert is_overconfident is True
        assert "evidence" in msg.lower()

    def test_check_overconfidence_sufficient_evidence(self):
        """Should not flag when evidence is sufficient."""
        checker = ConfidenceOverconfidenceChecker()
        is_overconfident, msg = checker.check_overconfidence(
            stated_confidence=0.8,
            evidence_count=5,
            evidence_quality=0.9,
        )
        assert is_overconfident is False
        assert msg == ""

    def test_check_overconfidence_low_quality_evidence(self):
        """Should detect overconfidence with low quality evidence."""
        checker = ConfidenceOverconfidenceChecker()
        is_overconfident, msg = checker.check_overconfidence(
            stated_confidence=0.9,
            evidence_count=10,
            evidence_quality=0.3,  # Low quality
        )
        assert is_overconfident is True
        assert "quality" in msg.lower()

    def test_check_overconfidence_exceeds_historical(self):
        """Should detect when confidence exceeds historical accuracy."""
        checker = ConfidenceOverconfidenceChecker(
            historical_accuracy={"root_cause": 0.6}
        )
        is_overconfident, msg = checker.check_overconfidence(
            stated_confidence=0.9,  # Much higher than 0.6 historical
            evidence_count=10,
            evidence_quality=0.8,
            claim_type="root_cause",
        )
        assert is_overconfident is True
        assert "historical" in msg.lower()

    def test_check_overconfidence_within_historical(self):
        """Should accept confidence within historical range."""
        checker = ConfidenceOverconfidenceChecker(
            historical_accuracy={"root_cause": 0.8}
        )
        is_overconfident, msg = checker.check_overconfidence(
            stated_confidence=0.85,  # Slightly above but within tolerance
            evidence_count=5,
            evidence_quality=0.8,
            claim_type="root_cause",
        )
        assert is_overconfident is False

    def test_check_overconfidence_thresholds(self):
        """Should check different confidence thresholds."""
        checker = ConfidenceOverconfidenceChecker()

        # 90% confidence needs 5 evidence pieces
        is_over, _ = checker.check_overconfidence(0.9, 4, 0.8)
        assert is_over is True
        is_over, _ = checker.check_overconfidence(0.9, 5, 0.8)
        assert is_over is False

        # 80% confidence needs 3 evidence pieces
        is_over, _ = checker.check_overconfidence(0.8, 2, 0.8)
        assert is_over is True
        is_over, _ = checker.check_overconfidence(0.8, 3, 0.8)
        assert is_over is False

        # 70% confidence needs 2 evidence pieces
        is_over, _ = checker.check_overconfidence(0.7, 1, 0.8)
        assert is_over is True
        is_over, _ = checker.check_overconfidence(0.7, 2, 0.8)
        assert is_over is False

        # 60% confidence needs 1 evidence piece
        is_over, _ = checker.check_overconfidence(0.6, 0, 0.8)
        assert is_over is True
        is_over, _ = checker.check_overconfidence(0.6, 1, 0.8)
        assert is_over is False

    def test_check_overconfidence_low_confidence(self):
        """Should not flag low confidence levels."""
        checker = ConfidenceOverconfidenceChecker()
        is_overconfident, msg = checker.check_overconfidence(
            stated_confidence=0.5,
            evidence_count=0,
            evidence_quality=0.3,
        )
        assert is_overconfident is False
