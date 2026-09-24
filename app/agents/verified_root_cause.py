"""Deterministic construction and verification of RootCauseAssessment records."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable
from uuid import UUID

from pydantic import Field

from app.contracts.common import DomainContract, RiskLevel, require_aware_datetime
from app.contracts.evidence import EvidenceDomain, EvidenceItem
from app.contracts.hypothesis import Hypothesis
from app.contracts.root_cause import (
    RootCauseAssessment,
    RootCauseReasonCode,
    RootCauseStatus,
)
from app.evidence.diversity import (
    EvidenceDiversityPolicy,
    assess_evidence_diversity,
)


class RootCauseAssessmentPolicy(DomainContract):
    """Explicit tenant/product policy inputs for verification thresholds."""

    required_evidence_domains: tuple[EvidenceDomain, ...] = Field(min_length=1)
    critical_contradiction_domains: tuple[EvidenceDomain, ...]
    maximum_evidence_age_seconds: float = Field(gt=0)
    minimum_hypothesis_confidence: float = Field(ge=0, le=1)
    minimum_evidence_confidence: float = Field(ge=0, le=1)
    minimum_evidence_completeness: float = Field(ge=0, le=1)
    diversity_policy: EvidenceDiversityPolicy


class VerifiedRootCauseAssessor:
    """Resolve IDs, assess evidence quality, and decide status without an LLM."""

    def __init__(self, policy: RootCauseAssessmentPolicy) -> None:
        self._policy = policy

    def assess(
        self,
        *,
        hypothesis: Hypothesis | None,
        evidence: Iterable[EvidenceItem],
        incident_id: str,
        tenant_id: str,
        risk: RiskLevel,
        assessed_at: datetime | None = None,
        unknowns: tuple[str, ...] = (),
        assumptions: tuple[str, ...] = (),
    ) -> RootCauseAssessment:
        at = assessed_at or datetime.now(timezone.utc)
        require_aware_datetime(at)
        supplied = tuple(evidence)
        reasons: list[RootCauseReasonCode] = []
        if hypothesis is None:
            reasons.append(RootCauseReasonCode.HYPOTHESIS_UNAVAILABLE)
        if hypothesis is not None and (
            hypothesis.incident_id not in (None, incident_id)
            or hypothesis.tenant_id not in (None, tenant_id)
        ):
            reasons.append(RootCauseReasonCode.EVIDENCE_SCOPE_MISMATCH)

        evidence_by_id = {item.evidence_id: item for item in supplied}
        if len(evidence_by_id) != len(supplied):
            reasons.append(RootCauseReasonCode.DUPLICATE_EVIDENCE)

        expected_support = set(hypothesis.supporting_evidence_ids if hypothesis else ())
        expected_contradictions = set(hypothesis.contradicting_evidence_ids if hypothesis else ())
        # EvidenceItem associations are the authoritative link when available.
        for item in supplied:
            if hypothesis and hypothesis.hypothesis_id in item.supports_hypothesis_ids:
                expected_support.add(item.evidence_id)
            if hypothesis and hypothesis.hypothesis_id in item.contradicts_hypothesis_ids:
                expected_contradictions.add(item.evidence_id)

        referenced_ids = expected_support | expected_contradictions
        if referenced_ids - evidence_by_id.keys():
            reasons.append(RootCauseReasonCode.EVIDENCE_REFERENCE_MISSING)

        scoped: dict[UUID, EvidenceItem] = {}
        scoped_records: list[EvidenceItem] = []
        stale_ids: set[UUID] = set()
        for item in supplied:
            evidence_id = item.evidence_id
            if evidence_id not in referenced_ids:
                continue
            if item.tenant_id != tenant_id or item.incident_id != incident_id:
                reasons.append(RootCauseReasonCode.EVIDENCE_SCOPE_MISMATCH)
                continue
            scoped_records.append(item)
            age = (at - item.observed_at).total_seconds()
            if age < 0 or age > self._policy.maximum_evidence_age_seconds:
                stale_ids.add(evidence_id)
                continue
            scoped[evidence_id] = item
        if stale_ids:
            reasons.append(RootCauseReasonCode.STALE_EVIDENCE)

        supporting_ids = tuple(sorted((expected_support & scoped.keys()), key=str))
        contradicting_ids = tuple(sorted((expected_contradictions & scoped.keys()), key=str))
        support_items = [scoped[item_id] for item_id in supporting_ids]
        # Count one artifact once for confidence/completeness. Diversity receives
        # all observations so duplicate/correlation penalties remain visible.
        distinct_support: dict[str, EvidenceItem] = {}
        for item in support_items:
            artifact = item.artifact_hash or item.digest or str(item.evidence_id)
            distinct_support.setdefault(artifact, item)

        present_domains = {item.domain for item in distinct_support.values() if item.domain is not None}
        missing_domains = set(self._policy.required_evidence_domains) - present_domains
        if missing_domains:
            reasons.append(RootCauseReasonCode.REQUIRED_EVIDENCE_MISSING)
        completeness = (
            len(set(self._policy.required_evidence_domains) & present_domains)
            / len(set(self._policy.required_evidence_domains))
        )

        diversity = assess_evidence_diversity(
            scoped_records,
            risk=risk,
            assessed_at=at,
            policy=self._policy.diversity_policy,
        )
        if diversity.independent_domain_count < diversity.minimum_required_domains:
            reasons.append(RootCauseReasonCode.EVIDENCE_DIVERSITY_INSUFFICIENT)
        if diversity.duplicate_ratio > 0:
            reasons.append(RootCauseReasonCode.DUPLICATE_EVIDENCE)

        confidences = [item.confidence for item in distinct_support.values() if item.confidence is not None]
        evidence_confidence = sum(confidences) / len(confidences) if confidences else 0.0
        unresolved_critical = any(
            scoped[evidence_id].domain in self._policy.critical_contradiction_domains
            for evidence_id in contradicting_ids
        )
        if contradicting_ids:
            reasons.append(RootCauseReasonCode.CONTRADICTION_PRESENT)
        if unresolved_critical:
            reasons.append(RootCauseReasonCode.CRITICAL_CONTRADICTION)

        complete_enough = completeness >= self._policy.minimum_evidence_completeness
        confidence_enough = (
            hypothesis is not None
            and hypothesis.confidence >= self._policy.minimum_hypothesis_confidence
            and evidence_confidence >= self._policy.minimum_evidence_confidence
        )
        diversity_enough = diversity.independent_domain_count >= diversity.minimum_required_domains
        refs_valid = not any(code in reasons for code in (
            RootCauseReasonCode.EVIDENCE_REFERENCE_MISSING,
            RootCauseReasonCode.EVIDENCE_SCOPE_MISMATCH,
            RootCauseReasonCode.STALE_EVIDENCE,
        ))

        if contradicting_ids and unresolved_critical:
            status = RootCauseStatus.CONTRADICTED
        elif not supporting_ids or not refs_valid or completeness == 0:
            status = RootCauseStatus.INSUFFICIENT_EVIDENCE
        elif complete_enough and confidence_enough and diversity_enough and not contradicting_ids:
            status = RootCauseStatus.VERIFIED
        elif contradicting_ids:
            status = RootCauseStatus.CONTRADICTED
        else:
            status = RootCauseStatus.PROBABLE

        if status == RootCauseStatus.VERIFIED:
            reasons.append(RootCauseReasonCode.VERIFIED)
        elif status == RootCauseStatus.PROBABLE:
            reasons.append(RootCauseReasonCode.PROBABLE)
        elif status == RootCauseStatus.INSUFFICIENT_EVIDENCE:
            reasons.append(RootCauseReasonCode.INSUFFICIENT_EVIDENCE)

        unresolved_unknowns = list(unknowns)
        unresolved_unknowns.extend(f"required evidence domain unavailable: {domain.value}" for domain in sorted(missing_domains, key=lambda item: item.value))
        unresolved_unknowns.extend(f"evidence reference unavailable: {item_id}" for item_id in sorted(referenced_ids - evidence_by_id.keys(), key=str))
        # Legacy evidence_ids must also include contradiction references because
        # existing ActionContract validation uses that aggregate field.
        all_evidence_ids = tuple(sorted(set(supporting_ids) | set(contradicting_ids), key=str))
        summary = hypothesis.claim if hypothesis else "Root cause could not be assessed without a hypothesis."
        return RootCauseAssessment(
            incident_id=incident_id,
            tenant_id=tenant_id,
            primary_hypothesis_id=hypothesis.hypothesis_id if hypothesis else None,
            root_cause_summary=summary,
            affected_entities=hypothesis.affected_entities if hypothesis else (),
            hypothesis_confidence=hypothesis.confidence if hypothesis else 0.0,
            evidence_confidence=evidence_confidence,
            evidence_completeness=completeness,
            evidence_diversity=diversity,
            supporting_evidence_ids=supporting_ids,
            contradicting_evidence_ids=contradicting_ids,
            unknowns=tuple(dict.fromkeys(unresolved_unknowns)),
            assumptions=assumptions,
            status=status,
            reason_codes=tuple(dict.fromkeys(reasons)),
            hypothesis_id=hypothesis.hypothesis_id if hypothesis else None,
            statement=summary,
            confidence=hypothesis.confidence if hypothesis else 0.0,
            evidence_ids=all_evidence_ids,
        )
