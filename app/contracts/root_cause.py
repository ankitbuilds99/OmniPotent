"""Assessment of a candidate root cause and its supporting evidence."""

from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import Field

from app.contracts.common import DomainContract


class RootCauseStatus(StrEnum):
    CONFIRMED = "confirmed"
    LIKELY = "likely"
    UNCONFIRMED = "unconfirmed"
    REFUTED = "refuted"


class RootCauseAssessment(DomainContract):
    assessment_id: UUID = Field(default_factory=uuid4)
    status: RootCauseStatus
    hypothesis_id: UUID
    statement: str = Field(min_length=1, max_length=4000)
    confidence: float = Field(ge=0, le=1)
    evidence_ids: tuple[UUID, ...] = Field(min_length=1)
    rationale: str = Field(default="", max_length=8000)
