"""Verification Agent - Main Orchestrator.

Ensures AI-generated analysis and actions are:
1. Grounded in real evidence
2. Free from hallucinations
3. Properly calibrated confidence
4. Safe to execute

This is the critical safety layer before any autonomous action.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from app.verification.confidence_calibrator import (
    ConfidenceCalibrator,
)
from app.verification.consistency_checker import (
    ActionSafetyCheck,
    DataConsistencyChecker,
)
from app.verification.evidence_collector import Evidence, EvidenceCollector, EvidenceSource
from app.verification.hallucination_detector import (
    HallucinationDetector,
    HallucinationResult,
    HallucinationType,
)

log = logging.getLogger("omnipotent.verification.agent")


class VerificationStatus(str, Enum):
    VERIFIED = "verified"
    REFUTED = "refuted"
    UNCERTAIN = "uncertain"
    INSUFFICIENT_DATA = "insufficient_data"


@dataclass
class Claim:
    """A claim that needs verification."""
    id: str
    text: str
    claim_type: str

    cited_sources: list[str] = field(default_factory=list)
    cited_values: dict[str, Any] = field(default_factory=dict)

    incident_id: str | None = None
    service_id: str | None = None
    timestamp: datetime | None = None

    stated_confidence: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "claim_type": self.claim_type,
            "incident_id": self.incident_id,
            "service_id": self.service_id,
            "stated_confidence": self.stated_confidence,
        }


@dataclass
class VerificationResult:
    """Result of verifying a claim."""
    claim_id: str
    status: VerificationStatus
    confidence: float

    supporting_evidence: list[Evidence] = field(default_factory=list)
    contradicting_evidence: list[Evidence] = field(default_factory=list)

    hallucination_detected: bool = False
    hallucination_type: HallucinationType | None = None
    hallucination_explanation: str | None = None

    calibrated_confidence: float | None = None
    overconfidence_warning: bool = False

    reasoning: str = ""
    verified_facts: list[str] = field(default_factory=list)
    refuted_facts: list[str] = field(default_factory=list)

    verification_time_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "status": self.status.value,
            "confidence": self.confidence,
            "supporting_evidence_count": len(self.supporting_evidence),
            "contradicting_evidence_count": len(self.contradicting_evidence),
            "hallucination_detected": self.hallucination_detected,
            "hallucination_type": (
                self.hallucination_type.value if self.hallucination_type else None
            ),
            "calibrated_confidence": self.calibrated_confidence,
            "overconfidence_warning": self.overconfidence_warning,
            "reasoning": self.reasoning,
            "verified_facts": self.verified_facts,
            "refuted_facts": self.refuted_facts,
            "verification_time_ms": self.verification_time_ms,
        }


class VerificationAgent:
    """Main verification agent that orchestrates all checks.

    Provides a comprehensive safety layer for AI-generated content
    and actions in DevOps automation.
    """

    def __init__(
        self,
        evidence_collector: EvidenceCollector | None = None,
        hallucination_detector: HallucinationDetector | None = None,
        confidence_calibrator: ConfidenceCalibrator | None = None,
        consistency_checker: DataConsistencyChecker | None = None,
        llm_client: Any = None,
    ):
        self.evidence = evidence_collector or EvidenceCollector()
        self.hallucination = hallucination_detector or HallucinationDetector(llm_client)
        self.calibration = confidence_calibrator or ConfidenceCalibrator()
        self.consistency = consistency_checker or DataConsistencyChecker()

        self._verification_cache: dict[str, VerificationResult] = {}
        self._cache_ttl_seconds = 300

    async def verify_claim(
        self,
        claim: Claim,
        time_window_minutes: int = 60,
        use_cache: bool = True,
    ) -> VerificationResult:
        """Verify a claim with full evidence checking.

        Args:
            claim: The claim to verify
            time_window_minutes: Time window for evidence collection
            use_cache: Whether to use cached results

        Returns:
            VerificationResult with status, evidence, and reasoning
        """
        start_time = datetime.now(timezone.utc)

        cache_key = f"{claim.id}:{claim.text[:50]}"
        if use_cache and cache_key in self._verification_cache:
            cached = self._verification_cache[cache_key]
            log.debug("Returning cached verification for %s", claim.id)
            return cached

        evidence = await self.evidence.collect_evidence(
            claim_text=claim.text,
            service_id=claim.service_id,
            incident_id=claim.incident_id,
            time_window_minutes=time_window_minutes,
        )

        hallucination_result = await self.hallucination.check_for_hallucinations(
            claim_text=claim.text,
            evidence=evidence,
            cited_sources=claim.cited_sources,
            cited_values=claim.cited_values,
        )

        supporting = [e for e in evidence if self._supports_claim(e, claim)]
        contradicting = [e for e in evidence if self._contradicts_claim(e, claim)]

        if hallucination_result.detected:
            status = VerificationStatus.REFUTED
            confidence = 0.1
        elif len(contradicting) > len(supporting):
            status = VerificationStatus.REFUTED
            confidence = 0.3
        elif len(supporting) > 0 and len(contradicting) == 0:
            status = VerificationStatus.VERIFIED
            confidence = min(0.9, 0.5 + len(supporting) * 0.1)
        elif len(evidence) == 0:
            status = VerificationStatus.INSUFFICIENT_DATA
            confidence = 0.2
        else:
            status = VerificationStatus.UNCERTAIN
            confidence = 0.5

        evidence_strength = len(supporting) / max(len(evidence), 1)
        calibration_result = await self.calibration.calibrate_confidence(
            claim_type=claim.claim_type,
            stated_confidence=claim.stated_confidence or confidence,
            evidence_strength=evidence_strength,
        )

        end_time = datetime.now(timezone.utc)
        verification_time_ms = int((end_time - start_time).total_seconds() * 1000)

        result = VerificationResult(
            claim_id=claim.id,
            status=status,
            confidence=confidence,
            supporting_evidence=supporting,
            contradicting_evidence=contradicting,
            hallucination_detected=hallucination_result.detected,
            hallucination_type=hallucination_result.hallucination_type,
            hallucination_explanation=hallucination_result.explanation,
            calibrated_confidence=calibration_result.calibrated_confidence,
            overconfidence_warning=calibration_result.overconfidence_detected,
            reasoning=self._generate_reasoning(
                claim, supporting, contradicting, hallucination_result
            ),
            verification_time_ms=verification_time_ms,
        )

        self._verification_cache[cache_key] = result

        return result

    async def verify_action(
        self,
        action_type: str,
        target: str,
        params: dict[str, Any],
    ) -> ActionSafetyCheck:
        """Verify an action is safe to execute.

        Args:
            action_type: Type of action (scale, restart, rollback, etc.)
            target: Target resource (deployment name, service, etc.)
            params: Action parameters

        Returns:
            ActionSafetyCheck with safety assessment
        """
        return await self.consistency.pre_action_check(action_type, target, params)

    async def validate_action_result(
        self,
        check: ActionSafetyCheck,
        timeout_seconds: int = 60,
    ) -> bool:
        """Validate action completed successfully.

        Args:
            check: The safety check from before the action
            timeout_seconds: How long to wait for validation

        Returns:
            True if action succeeded as expected
        """
        return await self.consistency.post_action_validate(check, timeout_seconds)

    async def verify_multiple_claims(
        self,
        claims: list[Claim],
        time_window_minutes: int = 60,
    ) -> list[VerificationResult]:
        """Verify multiple claims in parallel.

        Args:
            claims: List of claims to verify
            time_window_minutes: Time window for evidence collection

        Returns:
            List of verification results
        """
        tasks = [
            self.verify_claim(claim, time_window_minutes)
            for claim in claims
        ]

        return await asyncio.gather(*tasks)

    async def verify_hypothesis(
        self,
        hypothesis: str,
        service_id: str,
        incident_id: str | None = None,
        confidence: float = 0.5,
    ) -> VerificationResult:
        """Verify a root cause hypothesis.

        Convenience method for verifying RCA hypotheses.

        Args:
            hypothesis: The hypothesis text
            service_id: Affected service
            incident_id: Related incident ID
            confidence: Stated confidence in the hypothesis

        Returns:
            VerificationResult
        """
        claim = Claim(
            id=f"hyp_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
            text=hypothesis,
            claim_type="root_cause",
            service_id=service_id,
            incident_id=incident_id,
            stated_confidence=confidence,
        )

        return await self.verify_claim(claim)

    def _supports_claim(self, evidence: Evidence, claim: Claim) -> bool:
        """Check if evidence supports the claim."""
        claim_lower = claim.text.lower()

        if evidence.source == EvidenceSource.METRICS:
            if evidence.metric_value is not None:
                if "error" in claim_lower and evidence.metric_value > 0:
                    if "error_rate" in (evidence.metric_name or "").lower():
                        return True

                if "latency" in claim_lower or "slow" in claim_lower:
                    if "latency" in (evidence.metric_name or "").lower():
                        if evidence.metric_value > 0.5:
                            return True

        if evidence.source == EvidenceSource.LOGS:
            if evidence.log_lines:
                for line in evidence.log_lines:
                    line_lower = line.lower()
                    if "error" in claim_lower and "error" in line_lower:
                        return True
                    if "timeout" in claim_lower and "timeout" in line_lower:
                        return True
                    if "exception" in claim_lower and "exception" in line_lower:
                        return True

        if evidence.source == EvidenceSource.KUBERNETES:
            if isinstance(evidence.data, dict):
                if "replica" in claim_lower:
                    if "replicas" in evidence.data or "ready_replicas" in evidence.data:
                        return True

        if evidence.source == EvidenceSource.KNOWLEDGE_GRAPH:
            if isinstance(evidence.data, dict):
                if "deployment" in claim_lower and evidence.data.get("type") == "recent_deployments":
                    return True
                if "change" in claim_lower and evidence.data.get("type") == "correlated_changes":
                    return True

        return False

    def _contradicts_claim(self, evidence: Evidence, claim: Claim) -> bool:
        """Check if evidence contradicts the claim."""
        claim_lower = claim.text.lower()

        if evidence.source == EvidenceSource.METRICS:
            if evidence.metric_value is not None:
                if "error" in claim_lower and "no error" not in claim_lower:
                    if "error_rate" in (evidence.metric_name or "").lower():
                        if evidence.metric_value == 0:
                            return True

                if "healthy" in claim_lower or "working" in claim_lower:
                    if "error_rate" in (evidence.metric_name or "").lower():
                        if evidence.metric_value > 0.05:
                            return True

        if evidence.source == EvidenceSource.KUBERNETES:
            if isinstance(evidence.data, dict):
                if "healthy" in claim_lower:
                    ready = evidence.data.get("ready_replicas", 0)
                    desired = evidence.data.get("replicas", 0)
                    if ready < desired:
                        return True

        return False

    def _generate_reasoning(
        self,
        claim: Claim,
        supporting: list[Evidence],
        contradicting: list[Evidence],
        hallucination: HallucinationResult,
    ) -> str:
        """Generate human-readable reasoning."""
        parts: list[str] = []

        if hallucination.detected:
            parts.append(
                f"HALLUCINATION DETECTED ({hallucination.hallucination_type.value if hallucination.hallucination_type else 'unknown'}): "
                f"{hallucination.explanation}"
            )

        if supporting:
            sources = set(e.source.value for e in supporting)
            parts.append(
                f"Supporting evidence from {len(supporting)} source(s): {', '.join(sources)}"
            )

        if contradicting:
            sources = set(e.source.value for e in contradicting)
            parts.append(
                f"Contradicting evidence from {len(contradicting)} source(s): {', '.join(sources)}"
            )

        if not supporting and not contradicting:
            parts.append("No direct evidence found to verify or refute this claim.")

        return " | ".join(parts)

    async def record_verification_outcome(
        self,
        claim_id: str,
        was_correct: bool,
    ) -> None:
        """Record the actual outcome of a verified claim.

        Used to improve calibration over time.

        Args:
            claim_id: The claim that was verified
            was_correct: Whether the claim turned out to be correct
        """
        cached = self._verification_cache.get(claim_id)
        if cached:
            await self.calibration.record_outcome(
                claim_type="general",
                stated_confidence=cached.confidence,
                was_correct=was_correct,
            )

    def get_calibration_stats(self) -> dict[str, Any]:
        """Get calibration statistics."""
        return {
            "brier_score": self.calibration.calculate_brier_score(),
            "ece": self.calibration.calculate_ece(),
            "reliability_diagram": self.calibration.get_reliability_diagram_data(),
        }

    def clear_cache(self) -> None:
        """Clear the verification cache."""
        self._verification_cache.clear()


def create_verification_agent(
    prometheus_url: str | None = None,
    loki_url: str | None = None,
    elasticsearch_url: str | None = None,
    k8s_client: Any = None,
    github_token: str | None = None,
    knowledge_graph: Any = None,
    redis_client: Any = None,
    llm_client: Any = None,
) -> VerificationAgent:
    """Create a fully configured verification agent.

    Factory function to create a verification agent with all
    components properly configured.

    Args:
        prometheus_url: URL of Prometheus server
        loki_url: URL of Loki log server
        elasticsearch_url: URL of Elasticsearch
        k8s_client: Kubernetes client instance
        github_token: GitHub API token
        knowledge_graph: Knowledge graph instance
        redis_client: Redis client for caching/calibration
        llm_client: LLM client for hallucination detection

    Returns:
        Configured VerificationAgent
    """
    evidence_collector = EvidenceCollector(
        prometheus_url=prometheus_url,
        loki_url=loki_url,
        elasticsearch_url=elasticsearch_url,
        k8s_client=k8s_client,
        github_token=github_token,
        knowledge_graph=knowledge_graph,
    )

    hallucination_detector = HallucinationDetector(llm_client=llm_client)

    confidence_calibrator = ConfidenceCalibrator(redis_client=redis_client)

    consistency_checker = DataConsistencyChecker(
        k8s_client=k8s_client,
        prometheus_url=prometheus_url,
        knowledge_graph=knowledge_graph,
    )

    return VerificationAgent(
        evidence_collector=evidence_collector,
        hallucination_detector=hallucination_detector,
        confidence_calibrator=confidence_calibrator,
        consistency_checker=consistency_checker,
        llm_client=llm_client,
    )
