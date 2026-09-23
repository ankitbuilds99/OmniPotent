"""Deterministic evidence integrity, tenant, provenance, and freshness checks."""

from __future__ import annotations

from datetime import datetime

from app.contracts.evidence import EvidenceItem
from app.evidence.models import (
    EvidenceReasonCode,
    EvidenceTrustAssessment,
    EvidenceTrustLevel,
    EvidenceTrustStatus,
    FreshnessPolicy,
    artifact_hash,
)
from app.evidence.provenance import EvidenceProvenance, provenance_matches


def assess_evidence(
    item: EvidenceItem,
    *,
    expected_tenant_id: str,
    expected_incident_id: str,
    assessed_at: datetime,
    provenance: EvidenceProvenance | None,
    freshness_policy: FreshnessPolicy | None = None,
    source_verified: bool = False,
) -> EvidenceTrustAssessment:
    """Assess evidence and return machine-readable reasons; never authorizes actions."""
    reasons: list[EvidenceReasonCode] = []
    rejected = False
    stale = False
    if item.tenant_id != expected_tenant_id:
        reasons.append(EvidenceReasonCode.TENANT_MISMATCH)
        rejected = True
    if item.incident_id != expected_incident_id:
        reasons.append(EvidenceReasonCode.INCIDENT_MISMATCH)
        rejected = True
    if not item.artifact_hash:
        reasons.append(EvidenceReasonCode.HASH_MISSING)
        rejected = True
    elif item.artifact_hash != artifact_hash(item.payload):
        reasons.append(EvidenceReasonCode.HASH_MISMATCH)
        rejected = True
    if provenance is None:
        reasons.append(EvidenceReasonCode.PROVENANCE_MISSING)
        rejected = True
    elif not provenance_matches(item, provenance):
        reasons.append(EvidenceReasonCode.PROVENANCE_MISMATCH)
        rejected = True
    if item.observed_at > assessed_at:
        reasons.append(EvidenceReasonCode.FUTURE_OBSERVATION)
        rejected = True
    if freshness_policy is not None and (assessed_at - item.observed_at).total_seconds() > freshness_policy.maximum_age_seconds:
        reasons.append(EvidenceReasonCode.STALE)
        stale = True
    if source_verified:
        trust_level = EvidenceTrustLevel.MEDIUM
    else:
        trust_level = EvidenceTrustLevel.UNVERIFIED
        reasons.append(EvidenceReasonCode.SOURCE_UNVERIFIED)
    if not rejected and not stale:
        reasons.append(EvidenceReasonCode.FRESH)
        status = EvidenceTrustStatus.ACCEPTED if source_verified else EvidenceTrustStatus.UNVERIFIED
    elif rejected:
        status = EvidenceTrustStatus.REJECTED
    else:
        status = EvidenceTrustStatus.STALE
    return EvidenceTrustAssessment(
        evidence_id=str(item.evidence_id),
        status=status,
        trust_level=trust_level,
        reason_codes=tuple(dict.fromkeys(reasons)),
        assessed_at=assessed_at,
        provenance_digest=provenance.provenance_digest if provenance else None,
    )
