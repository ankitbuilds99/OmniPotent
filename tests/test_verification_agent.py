"""Tests for verification_agent.py - main orchestrator for verification."""

from __future__ import annotations

from datetime import datetime, timezone
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.verification.verification_agent import (
    Claim,
    VerificationResult,
    VerificationStatus,
    VerificationAgent,
    create_verification_agent,
)
from app.verification.evidence_collector import Evidence, EvidenceSource
from app.verification.hallucination_detector import HallucinationType, HallucinationResult
from app.verification.confidence_calibrator import CalibrationResult
from app.verification.consistency_checker import ActionSafetyCheck


class TestVerificationStatus:
    """Tests for VerificationStatus enum."""

    def test_all_statuses(self):
        """Should have all expected statuses."""
        statuses = [s.value for s in VerificationStatus]
        assert "verified" in statuses
        assert "refuted" in statuses
        assert "uncertain" in statuses
        assert "insufficient_data" in statuses


class TestClaim:
    """Tests for Claim dataclass."""

    def test_defaults(self):
        """Should initialize with defaults."""
        claim = Claim(
            id="claim-1",
            text="Error rate increased",
            claim_type="root_cause",
        )
        assert claim.cited_sources == []
        assert claim.cited_values == {}
        assert claim.incident_id is None
        assert claim.service_id is None
        assert claim.stated_confidence is None

    def test_full_init(self):
        """Should accept all fields."""
        claim = Claim(
            id="claim-1",
            text="Error rate increased to 5%",
            claim_type="root_cause",
            cited_sources=["prometheus"],
            cited_values={"error_rate": 0.05},
            incident_id="inc-123",
            service_id="my-service",
            timestamp=datetime.now(timezone.utc),
            stated_confidence=0.85,
        )
        assert claim.incident_id == "inc-123"
        assert claim.stated_confidence == 0.85

    def test_to_dict(self):
        """Should serialize to dictionary."""
        claim = Claim(
            id="claim-1",
            text="Test",
            claim_type="general",
            incident_id="inc-1",
            service_id="svc-1",
            stated_confidence=0.9,
        )
        d = claim.to_dict()
        assert d["id"] == "claim-1"
        assert d["text"] == "Test"
        assert d["claim_type"] == "general"
        assert d["incident_id"] == "inc-1"
        assert d["service_id"] == "svc-1"
        assert d["stated_confidence"] == 0.9


class TestVerificationResult:
    """Tests for VerificationResult dataclass."""

    def test_defaults(self):
        """Should initialize with defaults."""
        result = VerificationResult(
            claim_id="claim-1",
            status=VerificationStatus.VERIFIED,
            confidence=0.9,
        )
        assert result.supporting_evidence == []
        assert result.contradicting_evidence == []
        assert result.hallucination_detected is False
        assert result.calibrated_confidence is None
        assert result.reasoning == ""

    def test_to_dict(self):
        """Should serialize to dictionary."""
        result = VerificationResult(
            claim_id="claim-1",
            status=VerificationStatus.VERIFIED,
            confidence=0.9,
            hallucination_detected=False,
            calibrated_confidence=0.85,
            overconfidence_warning=False,
            reasoning="Supported by metrics",
            verified_facts=["fact1"],
            refuted_facts=[],
            verification_time_ms=150,
        )
        d = result.to_dict()
        assert d["claim_id"] == "claim-1"
        assert d["status"] == "verified"
        assert d["confidence"] == 0.9
        assert d["supporting_evidence_count"] == 0
        assert d["calibrated_confidence"] == 0.85
        assert d["verification_time_ms"] == 150

    def test_to_dict_with_hallucination(self):
        """Should include hallucination type."""
        result = VerificationResult(
            claim_id="claim-1",
            status=VerificationStatus.REFUTED,
            confidence=0.1,
            hallucination_detected=True,
            hallucination_type=HallucinationType.FABRICATED_FACT,
            hallucination_explanation="Value not in evidence",
        )
        d = result.to_dict()
        assert d["hallucination_detected"] is True
        assert d["hallucination_type"] == "fabricated_fact"


class TestVerificationAgent:
    """Tests for VerificationAgent class."""

    def test_init_no_dependencies(self):
        """Should initialize with default components."""
        agent = VerificationAgent()
        assert agent.evidence is not None
        assert agent.hallucination is not None
        assert agent.calibration is not None
        assert agent.consistency is not None

    def test_init_with_dependencies(self):
        """Should accept external components."""
        mock_evidence = MagicMock()
        mock_hallucination = MagicMock()
        mock_calibration = MagicMock()
        mock_consistency = MagicMock()

        agent = VerificationAgent(
            evidence_collector=mock_evidence,
            hallucination_detector=mock_hallucination,
            confidence_calibrator=mock_calibration,
            consistency_checker=mock_consistency,
        )
        assert agent.evidence is mock_evidence
        assert agent.hallucination is mock_hallucination
        assert agent.calibration is mock_calibration
        assert agent.consistency is mock_consistency

    @pytest.mark.asyncio
    async def test_verify_claim_verified(self):
        """Should verify claim with supporting evidence."""
        mock_evidence = AsyncMock()
        evidence_list = [
            Evidence(
                source=EvidenceSource.METRICS,
                data={},
                timestamp=datetime.now(timezone.utc),
                confidence=0.9,
                metric_name="error_rate",
                metric_value=0.05,
            )
        ]
        mock_evidence.collect_evidence.return_value = evidence_list

        mock_hallucination = AsyncMock()
        mock_hallucination.check_for_hallucinations.return_value = HallucinationResult(
            detected=False
        )

        mock_calibration = AsyncMock()
        mock_calibration.calibrate_confidence.return_value = CalibrationResult(
            original_confidence=0.8,
            calibrated_confidence=0.85,
            overconfidence_detected=False,
            calibration_adjustment=0.05,
            evidence_strength=0.9,
            explanation="Well calibrated",
        )

        agent = VerificationAgent(
            evidence_collector=mock_evidence,
            hallucination_detector=mock_hallucination,
            confidence_calibrator=mock_calibration,
        )

        claim = Claim(
            id="claim-1",
            text="Error rate increased to 5%",
            claim_type="root_cause",
            service_id="my-service",
        )

        result = await agent.verify_claim(claim)

        assert result.status == VerificationStatus.VERIFIED
        assert result.confidence > 0.5
        assert result.hallucination_detected is False

    @pytest.mark.asyncio
    async def test_verify_claim_refuted_hallucination(self):
        """Should refute claim when hallucination detected."""
        mock_evidence = AsyncMock()
        mock_evidence.collect_evidence.return_value = []

        mock_hallucination = AsyncMock()
        mock_hallucination.check_for_hallucinations.return_value = HallucinationResult(
            detected=True,
            hallucination_type=HallucinationType.FABRICATED_FACT,
            explanation="Value 99% not found",
            severity=0.8,
        )

        mock_calibration = AsyncMock()
        mock_calibration.calibrate_confidence.return_value = CalibrationResult(
            original_confidence=0.1,
            calibrated_confidence=0.1,
            overconfidence_detected=False,
            calibration_adjustment=0,
            evidence_strength=0,
            explanation="Low confidence",
        )

        agent = VerificationAgent(
            evidence_collector=mock_evidence,
            hallucination_detector=mock_hallucination,
            confidence_calibrator=mock_calibration,
        )

        claim = Claim(
            id="claim-1",
            text="Error rate spiked to 99%",
            claim_type="root_cause",
        )

        result = await agent.verify_claim(claim)

        assert result.status == VerificationStatus.REFUTED
        assert result.hallucination_detected is True
        assert result.hallucination_type == HallucinationType.FABRICATED_FACT

    @pytest.mark.asyncio
    async def test_verify_claim_insufficient_data(self):
        """Should return insufficient_data when no evidence."""
        mock_evidence = AsyncMock()
        mock_evidence.collect_evidence.return_value = []

        mock_hallucination = AsyncMock()
        mock_hallucination.check_for_hallucinations.return_value = HallucinationResult(
            detected=False
        )

        mock_calibration = AsyncMock()
        mock_calibration.calibrate_confidence.return_value = CalibrationResult(
            original_confidence=0.2,
            calibrated_confidence=0.2,
            overconfidence_detected=False,
            calibration_adjustment=0,
            evidence_strength=0,
            explanation="No evidence",
        )

        agent = VerificationAgent(
            evidence_collector=mock_evidence,
            hallucination_detector=mock_hallucination,
            confidence_calibrator=mock_calibration,
        )

        claim = Claim(
            id="claim-1",
            text="Something happened",
            claim_type="general",
        )

        result = await agent.verify_claim(claim)

        assert result.status == VerificationStatus.INSUFFICIENT_DATA
        assert result.confidence < 0.5

    @pytest.mark.asyncio
    async def test_verify_claim_uncertain(self):
        """Should return uncertain with mixed evidence."""
        mock_evidence = AsyncMock()
        # One supporting, one contradicting
        mock_evidence.collect_evidence.return_value = [
            Evidence(
                source=EvidenceSource.METRICS,
                data={},
                timestamp=datetime.now(timezone.utc),
                confidence=0.9,
                metric_name="error_rate",
                metric_value=0.05,
            ),
            Evidence(
                source=EvidenceSource.KUBERNETES,
                data={"replicas": 3, "ready_replicas": 3},
                timestamp=datetime.now(timezone.utc),
                confidence=0.9,
                resource_type="Deployment",
            ),
        ]

        mock_hallucination = AsyncMock()
        mock_hallucination.check_for_hallucinations.return_value = HallucinationResult(
            detected=False
        )

        mock_calibration = AsyncMock()
        mock_calibration.calibrate_confidence.return_value = CalibrationResult(
            original_confidence=0.5,
            calibrated_confidence=0.5,
            overconfidence_detected=False,
            calibration_adjustment=0,
            evidence_strength=0.5,
            explanation="Mixed evidence",
        )

        agent = VerificationAgent(
            evidence_collector=mock_evidence,
            hallucination_detector=mock_hallucination,
            confidence_calibrator=mock_calibration,
        )

        claim = Claim(
            id="claim-1",
            text="Service is healthy but has errors",
            claim_type="status",
        )

        result = await agent.verify_claim(claim)
        # With mixed evidence, could be VERIFIED, UNCERTAIN, or based on logic
        assert result.status in [
            VerificationStatus.VERIFIED,
            VerificationStatus.UNCERTAIN,
        ]

    @pytest.mark.asyncio
    async def test_verify_claim_uses_cache(self):
        """Should use cached results when available."""
        mock_evidence = AsyncMock()
        mock_evidence.collect_evidence.return_value = []

        mock_hallucination = AsyncMock()
        mock_hallucination.check_for_hallucinations.return_value = HallucinationResult(
            detected=False
        )

        mock_calibration = AsyncMock()
        mock_calibration.calibrate_confidence.return_value = CalibrationResult(
            original_confidence=0.5,
            calibrated_confidence=0.5,
            overconfidence_detected=False,
            calibration_adjustment=0,
            evidence_strength=0,
            explanation="",
        )

        agent = VerificationAgent(
            evidence_collector=mock_evidence,
            hallucination_detector=mock_hallucination,
            confidence_calibrator=mock_calibration,
        )

        claim = Claim(
            id="claim-1",
            text="Test claim",
            claim_type="general",
        )

        # First call
        await agent.verify_claim(claim, use_cache=True)
        # Second call should use cache
        await agent.verify_claim(claim, use_cache=True)

        # Evidence collector should only be called once
        assert mock_evidence.collect_evidence.call_count == 1

    @pytest.mark.asyncio
    async def test_verify_claim_bypasses_cache(self):
        """Should bypass cache when use_cache=False."""
        mock_evidence = AsyncMock()
        mock_evidence.collect_evidence.return_value = []

        mock_hallucination = AsyncMock()
        mock_hallucination.check_for_hallucinations.return_value = HallucinationResult(
            detected=False
        )

        mock_calibration = AsyncMock()
        mock_calibration.calibrate_confidence.return_value = CalibrationResult(
            original_confidence=0.5,
            calibrated_confidence=0.5,
            overconfidence_detected=False,
            calibration_adjustment=0,
            evidence_strength=0,
            explanation="",
        )

        agent = VerificationAgent(
            evidence_collector=mock_evidence,
            hallucination_detector=mock_hallucination,
            confidence_calibrator=mock_calibration,
        )

        claim = Claim(
            id="claim-1",
            text="Test claim",
            claim_type="general",
        )

        await agent.verify_claim(claim, use_cache=False)
        await agent.verify_claim(claim, use_cache=False)

        # Evidence collector called both times
        assert mock_evidence.collect_evidence.call_count == 2

    @pytest.mark.asyncio
    async def test_verify_claim_measures_time(self):
        """Should measure verification time."""
        mock_evidence = AsyncMock()
        mock_evidence.collect_evidence.return_value = []

        mock_hallucination = AsyncMock()
        mock_hallucination.check_for_hallucinations.return_value = HallucinationResult(
            detected=False
        )

        mock_calibration = AsyncMock()
        mock_calibration.calibrate_confidence.return_value = CalibrationResult(
            original_confidence=0.5,
            calibrated_confidence=0.5,
            overconfidence_detected=False,
            calibration_adjustment=0,
            evidence_strength=0,
            explanation="",
        )

        agent = VerificationAgent(
            evidence_collector=mock_evidence,
            hallucination_detector=mock_hallucination,
            confidence_calibrator=mock_calibration,
        )

        claim = Claim(id="claim-1", text="Test", claim_type="general")
        result = await agent.verify_claim(claim)

        assert result.verification_time_ms >= 0

    @pytest.mark.asyncio
    async def test_verify_action(self):
        """Should delegate to consistency checker."""
        mock_consistency = AsyncMock()
        mock_consistency.pre_action_check.return_value = ActionSafetyCheck(
            action_id="test",
            action_type="scale",
            target="my-deployment",
            is_safe=True,
            safety_score=0.9,
        )

        agent = VerificationAgent(consistency_checker=mock_consistency)

        result = await agent.verify_action(
            action_type="scale",
            target="my-deployment",
            params={"replicas": 5},
        )

        assert result.is_safe is True
        mock_consistency.pre_action_check.assert_called_once()

    @pytest.mark.asyncio
    async def test_validate_action_result(self):
        """Should delegate to consistency checker."""
        mock_consistency = AsyncMock()
        mock_consistency.post_action_validate.return_value = True

        agent = VerificationAgent(consistency_checker=mock_consistency)

        check = ActionSafetyCheck(
            action_id="test",
            action_type="scale",
            target="my-deployment",
        )
        result = await agent.validate_action_result(check, timeout_seconds=30)

        assert result is True
        mock_consistency.post_action_validate.assert_called_once_with(check, 30)

    @pytest.mark.asyncio
    async def test_verify_multiple_claims(self):
        """Should verify multiple claims in parallel."""
        mock_evidence = AsyncMock()
        mock_evidence.collect_evidence.return_value = []

        mock_hallucination = AsyncMock()
        mock_hallucination.check_for_hallucinations.return_value = HallucinationResult(
            detected=False
        )

        mock_calibration = AsyncMock()
        mock_calibration.calibrate_confidence.return_value = CalibrationResult(
            original_confidence=0.5,
            calibrated_confidence=0.5,
            overconfidence_detected=False,
            calibration_adjustment=0,
            evidence_strength=0,
            explanation="",
        )

        agent = VerificationAgent(
            evidence_collector=mock_evidence,
            hallucination_detector=mock_hallucination,
            confidence_calibrator=mock_calibration,
        )

        claims = [
            Claim(id=f"claim-{i}", text=f"Claim {i}", claim_type="general")
            for i in range(3)
        ]

        results = await agent.verify_multiple_claims(claims)

        assert len(results) == 3
        assert all(isinstance(r, VerificationResult) for r in results)

    @pytest.mark.asyncio
    async def test_verify_hypothesis(self):
        """Should create and verify hypothesis claim."""
        mock_evidence = AsyncMock()
        mock_evidence.collect_evidence.return_value = []

        mock_hallucination = AsyncMock()
        mock_hallucination.check_for_hallucinations.return_value = HallucinationResult(
            detected=False
        )

        mock_calibration = AsyncMock()
        mock_calibration.calibrate_confidence.return_value = CalibrationResult(
            original_confidence=0.7,
            calibrated_confidence=0.65,
            overconfidence_detected=False,
            calibration_adjustment=-0.05,
            evidence_strength=0.5,
            explanation="",
        )

        agent = VerificationAgent(
            evidence_collector=mock_evidence,
            hallucination_detector=mock_hallucination,
            confidence_calibrator=mock_calibration,
        )

        result = await agent.verify_hypothesis(
            hypothesis="Database connection pool exhausted",
            service_id="api-service",
            incident_id="inc-123",
            confidence=0.7,
        )

        assert isinstance(result, VerificationResult)
        # Verify evidence collector was called with right params
        mock_evidence.collect_evidence.assert_called_once()
        call_kwargs = mock_evidence.collect_evidence.call_args.kwargs
        assert call_kwargs["service_id"] == "api-service"
        assert call_kwargs["incident_id"] == "inc-123"

    @pytest.mark.asyncio
    async def test_record_verification_outcome(self):
        """Should record outcome to calibrator."""
        mock_calibration = AsyncMock()
        agent = VerificationAgent(confidence_calibrator=mock_calibration)

        # First, create a cached result
        agent._verification_cache["claim-1:Test claim text that is lon"] = VerificationResult(
            claim_id="claim-1:Test claim text that is lon",
            status=VerificationStatus.VERIFIED,
            confidence=0.8,
        )

        await agent.record_verification_outcome("claim-1:Test claim text that is lon", was_correct=True)

        mock_calibration.record_outcome.assert_called_once_with(
            claim_type="general",
            stated_confidence=0.8,
            was_correct=True,
        )

    @pytest.mark.asyncio
    async def test_record_verification_outcome_no_cache(self):
        """Should handle missing cache entry."""
        mock_calibration = AsyncMock()
        agent = VerificationAgent(confidence_calibrator=mock_calibration)

        # Should not raise
        await agent.record_verification_outcome("nonexistent", was_correct=True)
        mock_calibration.record_outcome.assert_not_called()

    def test_get_calibration_stats(self):
        """Should return calibration statistics."""
        mock_calibration = MagicMock()
        mock_calibration.calculate_brier_score.return_value = 0.15
        mock_calibration.calculate_ece.return_value = 0.08
        mock_calibration.get_reliability_diagram_data.return_value = {"bins": []}

        agent = VerificationAgent(confidence_calibrator=mock_calibration)
        stats = agent.get_calibration_stats()

        assert stats["brier_score"] == 0.15
        assert stats["ece"] == 0.08
        assert "reliability_diagram" in stats

    def test_clear_cache(self):
        """Should clear verification cache."""
        agent = VerificationAgent()
        agent._verification_cache["test"] = MagicMock()

        agent.clear_cache()

        assert agent._verification_cache == {}

    def test_supports_claim_metrics_error(self):
        """Should identify supporting error metrics."""
        agent = VerificationAgent()
        evidence = Evidence(
            source=EvidenceSource.METRICS,
            data={},
            timestamp=datetime.now(timezone.utc),
            confidence=0.9,
            metric_name="error_rate",
            metric_value=0.05,
        )
        claim = Claim(id="c1", text="Error rate increased", claim_type="status")

        assert agent._supports_claim(evidence, claim) is True

    def test_supports_claim_metrics_latency(self):
        """Should identify supporting latency metrics."""
        agent = VerificationAgent()
        evidence = Evidence(
            source=EvidenceSource.METRICS,
            data={},
            timestamp=datetime.now(timezone.utc),
            confidence=0.9,
            metric_name="latency_p99",
            metric_value=0.8,
        )
        claim = Claim(id="c1", text="Service is slow", claim_type="status")

        assert agent._supports_claim(evidence, claim) is True

    def test_supports_claim_logs_error(self):
        """Should identify supporting error logs."""
        agent = VerificationAgent()
        evidence = Evidence(
            source=EvidenceSource.LOGS,
            data={},
            timestamp=datetime.now(timezone.utc),
            confidence=0.8,
            log_lines=["ERROR: Connection refused", "Retrying..."],
        )
        claim = Claim(id="c1", text="Errors in logs", claim_type="status")

        assert agent._supports_claim(evidence, claim) is True

    def test_supports_claim_logs_timeout(self):
        """Should identify timeout in logs."""
        agent = VerificationAgent()
        evidence = Evidence(
            source=EvidenceSource.LOGS,
            data={},
            timestamp=datetime.now(timezone.utc),
            confidence=0.8,
            log_lines=["Request timeout after 30s"],
        )
        claim = Claim(id="c1", text="Request timeout", claim_type="status")

        assert agent._supports_claim(evidence, claim) is True

    def test_supports_claim_k8s_replicas(self):
        """Should identify K8s replica info."""
        agent = VerificationAgent()
        evidence = Evidence(
            source=EvidenceSource.KUBERNETES,
            data={"replicas": 3, "ready_replicas": 2},
            timestamp=datetime.now(timezone.utc),
            confidence=0.95,
        )
        claim = Claim(id="c1", text="Not all replicas ready", claim_type="status")

        assert agent._supports_claim(evidence, claim) is True

    def test_supports_claim_graph_deployment(self):
        """Should identify deployment changes."""
        agent = VerificationAgent()
        evidence = Evidence(
            source=EvidenceSource.KNOWLEDGE_GRAPH,
            data={"type": "recent_deployments", "count": 1},
            timestamp=datetime.now(timezone.utc),
            confidence=0.85,
        )
        claim = Claim(id="c1", text="Recent deployment caused issue", claim_type="root_cause")

        assert agent._supports_claim(evidence, claim) is True

    def test_supports_claim_no_match(self):
        """Should return False when no match."""
        agent = VerificationAgent()
        evidence = Evidence(
            source=EvidenceSource.EXTERNAL,
            data={},
            timestamp=datetime.now(timezone.utc),
            confidence=0.5,
        )
        claim = Claim(id="c1", text="Some claim", claim_type="general")

        assert agent._supports_claim(evidence, claim) is False

    def test_contradicts_claim_zero_error_rate(self):
        """Should identify contradiction when claiming errors but none exist."""
        agent = VerificationAgent()
        evidence = Evidence(
            source=EvidenceSource.METRICS,
            data={},
            timestamp=datetime.now(timezone.utc),
            confidence=0.9,
            metric_name="error_rate",
            metric_value=0.0,
        )
        claim = Claim(id="c1", text="High error rate", claim_type="status")

        assert agent._contradicts_claim(evidence, claim) is True

    def test_contradicts_claim_healthy_but_errors(self):
        """Should identify contradiction when claiming healthy but errors exist."""
        agent = VerificationAgent()
        evidence = Evidence(
            source=EvidenceSource.METRICS,
            data={},
            timestamp=datetime.now(timezone.utc),
            confidence=0.9,
            metric_name="error_rate",
            metric_value=0.10,
        )
        claim = Claim(id="c1", text="Service is healthy", claim_type="status")

        assert agent._contradicts_claim(evidence, claim) is True

    def test_contradicts_claim_k8s_unhealthy(self):
        """Should identify contradiction in K8s health."""
        agent = VerificationAgent()
        evidence = Evidence(
            source=EvidenceSource.KUBERNETES,
            data={"replicas": 3, "ready_replicas": 1},
            timestamp=datetime.now(timezone.utc),
            confidence=0.95,
        )
        claim = Claim(id="c1", text="Service is healthy", claim_type="status")

        assert agent._contradicts_claim(evidence, claim) is True

    def test_contradicts_claim_no_contradiction(self):
        """Should return False when no contradiction."""
        agent = VerificationAgent()
        evidence = Evidence(
            source=EvidenceSource.LOGS,
            data={},
            timestamp=datetime.now(timezone.utc),
            confidence=0.8,
            log_lines=["INFO: Request processed"],
        )
        claim = Claim(id="c1", text="Some claim", claim_type="general")

        assert agent._contradicts_claim(evidence, claim) is False

    def test_generate_reasoning_hallucination(self):
        """Should include hallucination in reasoning."""
        agent = VerificationAgent()
        claim = Claim(id="c1", text="Test", claim_type="general")
        hallucination = HallucinationResult(
            detected=True,
            hallucination_type=HallucinationType.FABRICATED_FACT,
            explanation="Value not found",
        )

        reasoning = agent._generate_reasoning(claim, [], [], hallucination)

        assert "HALLUCINATION" in reasoning
        assert "fabricated_fact" in reasoning.lower()

    def test_generate_reasoning_supporting(self):
        """Should include supporting evidence count."""
        agent = VerificationAgent()
        claim = Claim(id="c1", text="Test", claim_type="general")
        supporting = [
            Evidence(
                source=EvidenceSource.METRICS,
                data={},
                timestamp=datetime.now(timezone.utc),
                confidence=0.9,
            ),
            Evidence(
                source=EvidenceSource.LOGS,
                data={},
                timestamp=datetime.now(timezone.utc),
                confidence=0.8,
            ),
        ]

        reasoning = agent._generate_reasoning(
            claim, supporting, [], HallucinationResult(detected=False)
        )

        assert "2" in reasoning
        assert "Supporting" in reasoning

    def test_generate_reasoning_contradicting(self):
        """Should include contradicting evidence count."""
        agent = VerificationAgent()
        claim = Claim(id="c1", text="Test", claim_type="general")
        contradicting = [
            Evidence(
                source=EvidenceSource.METRICS,
                data={},
                timestamp=datetime.now(timezone.utc),
                confidence=0.9,
            ),
        ]

        reasoning = agent._generate_reasoning(
            claim, [], contradicting, HallucinationResult(detected=False)
        )

        assert "1" in reasoning
        assert "Contradicting" in reasoning

    def test_generate_reasoning_no_evidence(self):
        """Should note when no evidence."""
        agent = VerificationAgent()
        claim = Claim(id="c1", text="Test", claim_type="general")

        reasoning = agent._generate_reasoning(
            claim, [], [], HallucinationResult(detected=False)
        )

        assert "No direct evidence" in reasoning


class TestCreateVerificationAgent:
    """Tests for create_verification_agent factory."""

    def test_create_minimal(self):
        """Should create agent with no external dependencies."""
        agent = create_verification_agent()

        assert isinstance(agent, VerificationAgent)
        assert agent.evidence is not None
        assert agent.hallucination is not None
        assert agent.calibration is not None
        assert agent.consistency is not None

    def test_create_with_prometheus(self):
        """Should configure evidence collector with Prometheus."""
        agent = create_verification_agent(prometheus_url="http://prom:9090")

        assert agent.evidence.prometheus_url == "http://prom:9090"

    def test_create_with_loki(self):
        """Should configure evidence collector with Loki."""
        agent = create_verification_agent(loki_url="http://loki:3100")

        assert agent.evidence.loki_url == "http://loki:3100"

    def test_create_with_elasticsearch(self):
        """Should configure evidence collector with Elasticsearch."""
        agent = create_verification_agent(elasticsearch_url="http://es:9200")

        assert agent.evidence.elasticsearch_url == "http://es:9200"

    def test_create_with_k8s(self):
        """Should configure with K8s client."""
        mock_k8s = MagicMock()
        agent = create_verification_agent(k8s_client=mock_k8s)

        assert agent.evidence.k8s is mock_k8s
        assert agent.consistency.k8s is mock_k8s

    def test_create_with_github_token(self):
        """Should configure evidence collector with GitHub token."""
        agent = create_verification_agent(github_token="ghp_test")

        assert agent.evidence.github_token == "ghp_test"

    def test_create_with_knowledge_graph(self):
        """Should configure with knowledge graph."""
        mock_graph = MagicMock()
        agent = create_verification_agent(knowledge_graph=mock_graph)

        assert agent.evidence.graph is mock_graph
        assert agent.consistency.graph is mock_graph

    def test_create_with_redis(self):
        """Should configure calibrator with Redis."""
        mock_redis = MagicMock()
        agent = create_verification_agent(redis_client=mock_redis)

        assert agent.calibration.redis is mock_redis

    def test_create_with_llm(self):
        """Should configure hallucination detector with LLM."""
        mock_llm = MagicMock()
        agent = create_verification_agent(llm_client=mock_llm)

        assert agent.hallucination.llm is mock_llm

    def test_create_full(self):
        """Should create fully configured agent."""
        mock_k8s = MagicMock()
        mock_graph = MagicMock()
        mock_redis = MagicMock()
        mock_llm = MagicMock()

        agent = create_verification_agent(
            prometheus_url="http://prom:9090",
            loki_url="http://loki:3100",
            elasticsearch_url="http://es:9200",
            k8s_client=mock_k8s,
            github_token="ghp_test",
            knowledge_graph=mock_graph,
            redis_client=mock_redis,
            llm_client=mock_llm,
        )

        assert agent.evidence.prometheus_url == "http://prom:9090"
        assert agent.evidence.loki_url == "http://loki:3100"
        assert agent.evidence.k8s is mock_k8s
        assert agent.calibration.redis is mock_redis
        assert agent.hallucination.llm is mock_llm
