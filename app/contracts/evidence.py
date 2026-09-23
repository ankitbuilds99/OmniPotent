"""Evidence records and references shared across investigation and execution."""

from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field, JsonValue, field_validator

from app.contracts.common import DomainContract, require_aware_datetime, utc_now


class EvidenceItem(DomainContract):
    evidence_id: UUID = Field(default_factory=uuid4)
    source: str = Field(min_length=1, max_length=128)
    evidence_type: str = Field(min_length=1, max_length=128)
    observed_at: datetime = Field(default_factory=utc_now)
    payload: JsonValue
    digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    confidence: float | None = Field(default=None, ge=0, le=1)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("observed_at")
    @classmethod
    def observed_time_is_aware(cls, value: datetime) -> datetime:
        return require_aware_datetime(value)


class EvidenceReference(DomainContract):
    evidence_id: UUID
    source: str = Field(min_length=1, max_length=128)
    digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    relation: Literal["supports", "contradicts", "context"] = "context"
