"""Versioned, evidence-backed root-cause assessment contract."""

from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from app.contracts.common import DomainContract
from app.evidence.diversity import EvidenceDiversityAssessment


class RootCauseStatus(StrEnum):
    VERIFIED = "verified"
    PROBABLE = "probable"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    CONTRADICTED = "contradicted"

    # Compatibility aliases for callers of the first contract revision.
    CONFIRMED = "verified"
    LIKELY = "probable"
    UNCONFIRMED = "insufficient_evidence"
    REFUTED = "contradicted"


class RootCauseReasonCode(StrEnum):
    VERIFIED = "root_cause_verified"
    PROBABLE = "root_cause_probable"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    REQUIRED_EVIDENCE_MISSING = "required_evidence_missing"
    EVIDENCE_REFERENCE_MISSING = "evidence_reference_missing"
    EVIDENCE_SCOPE_MISMATCH = "evidence_scope_mismatch"
    STALE_EVIDENCE = "stale_evidence"
    EVIDENCE_DIVERSITY_INSUFFICIENT = "evidence_diversity_insufficient"
    CRITICAL_CONTRADICTION = "critical_contradiction_unresolved"
    CONTRADICTION_PRESENT = "contradiction_present"
    DUPLICATE_EVIDENCE = "duplicate_evidence_reduced_quality"
    HYPOTHESIS_UNAVAILABLE = "primary_hypothesis_unavailable"


class RootCauseAssessment(DomainContract):
    """A root-cause conclusion with traceable evidence and quality measures.

    The legacy fields remain accepted during migration. New assessments should
    be created by ``VerifiedRootCauseAssessor``; that factory resolves every
    referenced evidence id and enforces tenant/incident scope before assigning
    VERIFIED status.
    """

    assessment_id: UUID = Field(default_factory=uuid4)
    incident_id: str | None = Field(default=None, min_length=1, max_length=256)
    tenant_id: str | None = Field(default=None, min_length=1, max_length=256)
    primary_hypothesis_id: UUID | None = None
    root_cause_summary: str | None = Field(default=None, min_length=1, max_length=4000)
    affected_entities: tuple[str, ...] = ()
    hypothesis_confidence: float | None = Field(default=None, ge=0, le=1)
    evidence_confidence: float | None = Field(default=None, ge=0, le=1)
    evidence_completeness: float | None = Field(default=None, ge=0, le=1)
    evidence_diversity: EvidenceDiversityAssessment | None = None
    supporting_evidence_ids: tuple[UUID, ...] = ()
    contradicting_evidence_ids: tuple[UUID, ...] = ()
    unknowns: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()
    status: RootCauseStatus
    reason_codes: tuple[RootCauseReasonCode, ...] = ()

    # Legacy V1/V2-transition fields. Keep these until the API/schema migration.
    hypothesis_id: UUID | None = None
    statement: str | None = Field(default=None, max_length=4000)
    confidence: float | None = Field(default=None, ge=0, le=1)
    evidence_ids: tuple[UUID, ...] = ()
    rationale: str = Field(default="", max_length=8000)

    @model_validator(mode="before")
    @classmethod
    def map_legacy_fields(cls, value):
        if not isinstance(value, dict):
            return value
        data = dict(value)
        if data.get("primary_hypothesis_id") is None:
            data["primary_hypothesis_id"] = data.get("hypothesis_id")
        if data.get("hypothesis_id") is None:
            data["hypothesis_id"] = data.get("primary_hypothesis_id")
        if data.get("root_cause_summary") is None:
            data["root_cause_summary"] = data.get("statement")
        if data.get("statement") is None:
            data["statement"] = data.get("root_cause_summary")
        if data.get("hypothesis_confidence") is None:
            data["hypothesis_confidence"] = data.get("confidence")
        if data.get("confidence") is None:
            data["confidence"] = data.get("hypothesis_confidence")
        if not data.get("supporting_evidence_ids") and data.get("evidence_ids"):
            data["supporting_evidence_ids"] = data["evidence_ids"]
        if not data.get("evidence_ids") and data.get("supporting_evidence_ids"):
            data["evidence_ids"] = data["supporting_evidence_ids"]
        return data

    @model_validator(mode="after")
    def validate_v2_record(self) -> "RootCauseAssessment":
        v2_fields = (
            self.incident_id, self.tenant_id, self.evidence_completeness,
            self.evidence_diversity,
        )
        if any(value is not None for value in v2_fields):
            missing = [
                name for name, value in (
                    ("incident_id", self.incident_id),
                    ("tenant_id", self.tenant_id),
                    ("primary_hypothesis_id", self.primary_hypothesis_id if self.status != RootCauseStatus.INSUFFICIENT_EVIDENCE else True),
                    ("root_cause_summary", self.root_cause_summary),
                    ("evidence_completeness", self.evidence_completeness),
                    ("evidence_diversity", self.evidence_diversity),
                ) if value is None
            ]
            if missing:
                raise ValueError(f"V2 root cause assessment is missing fields: {', '.join(missing)}")
            if self.status in {RootCauseStatus.VERIFIED, RootCauseStatus.PROBABLE} and not self.supporting_evidence_ids:
                raise ValueError("verified/probable V2 assessment must reference supporting evidence")
            if self.status == RootCauseStatus.CONTRADICTED and not self.contradicting_evidence_ids:
                raise ValueError("contradicted V2 assessment must reference contradicting evidence")
            if self.status == RootCauseStatus.VERIFIED:
                blocking = {
                    RootCauseReasonCode.REQUIRED_EVIDENCE_MISSING,
                    RootCauseReasonCode.EVIDENCE_REFERENCE_MISSING,
                    RootCauseReasonCode.EVIDENCE_SCOPE_MISMATCH,
                    RootCauseReasonCode.STALE_EVIDENCE,
                    RootCauseReasonCode.EVIDENCE_DIVERSITY_INSUFFICIENT,
                    RootCauseReasonCode.CRITICAL_CONTRADICTION,
                }
                if blocking.intersection(self.reason_codes):
                    raise ValueError("VERIFIED assessment contains unresolved blocking reason codes")
        return self
