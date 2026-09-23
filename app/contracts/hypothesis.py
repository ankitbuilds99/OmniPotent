"""Typed diagnosis hypotheses with explicit evidence linkage."""

from uuid import UUID, uuid4

from pydantic import Field

from app.contracts.common import DomainContract


class Hypothesis(DomainContract):
    hypothesis_id: UUID = Field(default_factory=uuid4)
    statement: str = Field(min_length=1, max_length=4000)
    confidence: float = Field(ge=0, le=1)
    evidence_ids: tuple[UUID, ...] = ()
    alternatives: tuple[str, ...] = ()
    rationale: str = Field(default="", max_length=8000)
