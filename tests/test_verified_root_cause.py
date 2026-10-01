from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.agents.verified_root_cause import (
    RootCauseAssessmentPolicy,
    VerifiedRootCauseAssessor,
)
from app.contracts.common import RiskLevel
from app.contracts.evidence import EvidenceDomain, EvidenceItem
from app.contracts.hypothesis import Hypothesis
from app.contracts.root_cause import RootCauseReasonCode, RootCauseStatus
from app.evidence.diversity import EvidenceDiversityPolicy


def _policy(**overrides):
    values = {
        "required_evidence_domains": (EvidenceDomain.TELEMETRY_LOG, EvidenceDomain.TELEMETRY_METRIC),
        "critical_contradiction_domains": (EvidenceDomain.SECURITY,),
        "maximum_evidence_age_seconds": 120,
        "minimum_hypothesis_confidence": 0.7,
        "minimum_evidence_confidence": 0.7,
        "minimum_evidence_completeness": 1.0,
        "diversity_policy": EvidenceDiversityPolicy(
            minimum_domains_by_risk={RiskLevel.LOW: 2},
            correlation_window_seconds=30,
            freshness_horizon_seconds=120,
        ),
    }
    values.update(overrides)
    return RootCauseAssessmentPolicy(**values)


def _evidence(hypothesis_id, domain, source, *, contradicts=False, observed_at=None, artifact_hash=None, confidence=0.9):
    now = datetime.now(timezone.utc)
    return EvidenceItem(
        source=source,
        evidence_type="observed_signal",
        observed_at=observed_at or now,
        payload={"signal": source},
        tenant_id="tenant-1",
        incident_id="incident-1",
        domain=domain,
        source_identity=source,
        entity_type="service",
        entity_id="checkout",
        service="checkout",
        confidence=confidence,
        artifact_hash=artifact_hash,
        supports_hypothesis_ids=() if contradicts else (hypothesis_id,),
        contradicts_hypothesis_ids=(hypothesis_id,) if contradicts else (),
    )


def _hypothesis(evidence, *, confidence=0.9):
    hypothesis_id = evidence[0].supports_hypothesis_ids[0] if evidence[0].supports_hypothesis_ids else evidence[0].contradicts_hypothesis_ids[0]
    support_ids = tuple(item.evidence_id for item in evidence if hypothesis_id in item.supports_hypothesis_ids)
    contradiction_ids = tuple(item.evidence_id for item in evidence if hypothesis_id in item.contradicts_hypothesis_ids)
    return Hypothesis(
        hypothesis_id=hypothesis_id,
        tenant_id="tenant-1",
        incident_id="incident-1",
        claim="Connection pool saturation caused elevated latency",
        affected_entities=("checkout", "database"),
        confidence=confidence,
        supporting_evidence_ids=support_ids,
        contradicting_evidence_ids=contradiction_ids,
    )


def _assess(evidence, **kwargs):
    hypothesis = _hypothesis(evidence, confidence=kwargs.pop("hypothesis_confidence", 0.9))
    assessed_at = kwargs.pop("assessed_at", datetime.now(timezone.utc))
    policy = kwargs.pop("policy", _policy())
    return VerifiedRootCauseAssessor(policy).assess(
        hypothesis=hypothesis,
        evidence=evidence,
        incident_id="incident-1",
        tenant_id="tenant-1",
        risk=RiskLevel.LOW,
        assessed_at=assessed_at,
        **kwargs,
    )


def _two_domain_evidence():
    hypothesis_id = uuid4()
    return [
        _evidence(hypothesis_id, EvidenceDomain.TELEMETRY_LOG, "log-store"),
        _evidence(hypothesis_id, EvidenceDomain.TELEMETRY_METRIC, "metrics-store"),
    ]


def test_verified_assessment_is_derived_from_real_scoped_evidence_items():
    evidence = _two_domain_evidence()
    assessment = _assess(evidence)
    assert assessment.status == RootCauseStatus.VERIFIED
    assert assessment.incident_id == "incident-1"
    assert assessment.tenant_id == "tenant-1"
    assert assessment.primary_hypothesis_id == evidence[0].supports_hypothesis_ids[0]
    assert set(assessment.supporting_evidence_ids) == {item.evidence_id for item in evidence}
    assert assessment.evidence_completeness == 1
    assert assessment.evidence_diversity.independent_domain_count == 2
    assert RootCauseReasonCode.VERIFIED in assessment.reason_codes
    assert set(assessment.supporting_evidence_ids).issubset({item.evidence_id for item in evidence})


def test_partial_required_evidence_is_probable_and_carries_gap_reason():
    hypothesis_id = uuid4()
    evidence = [_evidence(hypothesis_id, EvidenceDomain.TELEMETRY_LOG, "log-store")]
    assessment = _assess(evidence)
    assert assessment.status == RootCauseStatus.PROBABLE
    assert assessment.evidence_completeness == 0.5
    assert RootCauseReasonCode.REQUIRED_EVIDENCE_MISSING in assessment.reason_codes
    assert any("telemetry_metric" in unknown for unknown in assessment.unknowns)


def test_no_evidence_produces_insufficient_assessment_instead_of_failure_or_guess():
    assessment = VerifiedRootCauseAssessor(_policy()).assess(
        hypothesis=None,
        evidence=(),
        incident_id="incident-1",
        tenant_id="tenant-1",
        risk=RiskLevel.LOW,
    )
    assert assessment.status == RootCauseStatus.INSUFFICIENT_EVIDENCE
    assert assessment.supporting_evidence_ids == ()
    assert RootCauseReasonCode.HYPOTHESIS_UNAVAILABLE in assessment.reason_codes


def test_critical_conflicting_evidence_blocks_verification():
    evidence = _two_domain_evidence()
    hypothesis_id = evidence[0].supports_hypothesis_ids[0]
    evidence.append(_evidence(hypothesis_id, EvidenceDomain.SECURITY, "security-review", contradicts=True))
    assessment = _assess(evidence)
    assert assessment.status == RootCauseStatus.CONTRADICTED
    assert assessment.contradicting_evidence_ids == (evidence[-1].evidence_id,)
    assert RootCauseReasonCode.CRITICAL_CONTRADICTION in assessment.reason_codes


def test_stale_evidence_cannot_satisfy_required_domains_or_be_referenced_as_support():
    hypothesis_id = uuid4()
    stale_at = datetime.now(timezone.utc) - timedelta(minutes=10)
    evidence = [
        _evidence(hypothesis_id, EvidenceDomain.TELEMETRY_LOG, "log-store", observed_at=stale_at),
        _evidence(hypothesis_id, EvidenceDomain.TELEMETRY_METRIC, "metrics-store", observed_at=stale_at),
    ]
    assessment = _assess(evidence)
    assert assessment.status == RootCauseStatus.INSUFFICIENT_EVIDENCE
    assert assessment.supporting_evidence_ids == ()
    assert RootCauseReasonCode.STALE_EVIDENCE in assessment.reason_codes


def test_duplicate_artifacts_do_not_satisfy_independent_diversity_threshold():
    hypothesis_id = uuid4()
    repeated_hash = "a" * 64
    evidence = [
        _evidence(hypothesis_id, EvidenceDomain.TELEMETRY_LOG, "log-store", artifact_hash=repeated_hash),
        _evidence(hypothesis_id, EvidenceDomain.TELEMETRY_LOG, "log-store", artifact_hash=repeated_hash),
    ]
    assessment = _assess(
        evidence,
        policy=_policy(
            required_evidence_domains=(EvidenceDomain.TELEMETRY_LOG,),
            diversity_policy=EvidenceDiversityPolicy(
                minimum_domains_by_risk={RiskLevel.LOW: 2},
                correlation_window_seconds=30,
                freshness_horizon_seconds=120,
            ),
        ),
    )
    assert assessment.status == RootCauseStatus.PROBABLE
    assert assessment.evidence_diversity.independent_domain_count == 1
    assert assessment.evidence_diversity.duplicate_ratio > 0
    assert RootCauseReasonCode.EVIDENCE_DIVERSITY_INSUFFICIENT in assessment.reason_codes
    assert RootCauseReasonCode.DUPLICATE_EVIDENCE in assessment.reason_codes


def test_verified_model_rejects_blocking_reasons_even_if_status_is_claimed():
    evidence = _two_domain_evidence()
    assessment = _assess(evidence)
    values = assessment.model_dump()
    values["reason_codes"] = [RootCauseReasonCode.STALE_EVIDENCE]
    with pytest.raises(ValidationError, match="blocking reason"):
        type(assessment).model_validate(values)
