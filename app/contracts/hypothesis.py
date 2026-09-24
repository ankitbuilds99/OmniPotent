"""Typed competing diagnosis hypotheses with explicit evidence linkage."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import Field, field_validator, model_validator

from app.contracts.common import DomainContract, require_aware_datetime, utc_now


class HypothesisStatus(StrEnum):
    PROPOSED = "proposed"
    SUPPORTED = "supported"
    CONTRADICTED = "contradicted"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    REFUTED = "refuted"


class Hypothesis(DomainContract):
    hypothesis_id: UUID = Field(default_factory=uuid4)
    # `statement` and `evidence_ids` retain compatibility with the original V2
    # contract; active diagnosis uses claim + the explicit evidence partitions.
    claim: str = Field(default="", max_length=4000)
    statement: str | None = Field(default=None, max_length=4000)
    incident_id: str | None = Field(default=None, min_length=1, max_length=256)
    tenant_id: str | None = Field(default=None, min_length=1, max_length=256)
    affected_entities: tuple[str, ...] = ()
    confidence: float = Field(ge=0, le=1)
    evidence_ids: tuple[UUID, ...] = ()
    supporting_evidence_ids: tuple[UUID, ...] = ()
    contradicting_evidence_ids: tuple[UUID, ...] = ()
    missing_evidence: tuple[str, ...] = ()
    status: HypothesisStatus = HypothesisStatus.PROPOSED
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    alternatives: tuple[str, ...] = ()
    rationale: str = Field(default="", max_length=8000)

    @field_validator("created_at", "updated_at")
    @classmethod
    def timestamps_are_aware(cls, value: datetime) -> datetime:
        return require_aware_datetime(value)

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_and_v2_fields(cls, value):
        if isinstance(value, dict):
            data = dict(value)
            claim = data.get("claim") or data.get("statement")
            if claim:
                data["claim"] = claim
                data.setdefault("statement", claim)
            if not data.get("supporting_evidence_ids") and data.get("evidence_ids"):
                data["supporting_evidence_ids"] = data["evidence_ids"]
            if not data.get("evidence_ids") and data.get("supporting_evidence_ids"):
                data["evidence_ids"] = data["supporting_evidence_ids"]
            return data
        return value

    @model_validator(mode="after")
    def claim_is_present_and_timestamps_ordered(self) -> "Hypothesis":
        if not self.claim and not self.statement:
            raise ValueError("claim is required")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not precede created_at")
        return self
