"""Evidence records and references shared across investigation and execution."""

from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field, JsonValue, field_validator, model_validator

from app.contracts.common import DomainContract, require_aware_datetime, utc_now


class EvidenceTrustLevel(StrEnum):
    UNVERIFIED = "unverified"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class EvidenceDomain(StrEnum):
    TELEMETRY_LOG = "telemetry_log"
    TELEMETRY_METRIC = "telemetry_metric"
    TELEMETRY_TRACE = "telemetry_trace"
    DEPLOYMENT = "deployment"
    SOURCE_CODE = "source_code"
    INFRASTRUCTURE = "infrastructure"
    DEPENDENCY = "dependency"
    CONFIGURATION = "configuration"
    SLO = "slo"
    BUSINESS_IMPACT = "business_impact"
    HISTORICAL_INCIDENT = "historical_incident"
    SYNTHETIC_TEST = "synthetic_test"
    SECURITY = "security"
    HUMAN_CONFIRMATION = "human_confirmation"


class EvidenceItem(DomainContract):
    evidence_id: UUID = Field(default_factory=uuid4)
    source: str = Field(min_length=1, max_length=128)
    evidence_type: str = Field(min_length=1, max_length=128)
    observed_at: datetime = Field(default_factory=utc_now)
    payload: JsonValue
    digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    confidence: float | None = Field(default=None, ge=0, le=1)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    # V2 evidence envelope. Optional here to keep historical callers and
    # serialized contracts readable; app.evidence.normalizer requires the
    # tenant/source context before an item may enter the V2 store.
    tenant_id: str | None = Field(default=None, min_length=1, max_length=256)
    incident_id: str | None = Field(default=None, min_length=1, max_length=256)
    source_type: str | None = Field(default=None, min_length=1, max_length=128)
    domain: EvidenceDomain | None = None
    source_identity: str | None = Field(default=None, min_length=1, max_length=512)
    tool_name: str | None = Field(default=None, max_length=256)
    tool_version: str | None = Field(default=None, max_length=128)
    connector_version: str | None = Field(default=None, max_length=128)
    query_or_operation: str | None = Field(default=None, max_length=2048)
    observation_window_start: datetime | None = None
    observation_window_end: datetime | None = None
    freshness_seconds: float | None = Field(default=None, ge=0)
    entity_type: str | None = Field(default=None, max_length=128)
    entity_id: str | None = Field(default=None, max_length=512)
    service: str | None = Field(default=None, max_length=512)
    summary: str | None = Field(default=None, max_length=4096)
    artifact_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    artifact_reference: str | None = Field(default=None, max_length=2048)
    trust_level: EvidenceTrustLevel = EvidenceTrustLevel.UNVERIFIED
    supports_hypothesis_ids: tuple[UUID, ...] = ()
    contradicts_hypothesis_ids: tuple[UUID, ...] = ()
    instruction_allowed: Literal[False] = False

    @field_validator("observed_at")
    @classmethod
    def observed_time_is_aware(cls, value: datetime) -> datetime:
        return require_aware_datetime(value)

    @field_validator("observation_window_start", "observation_window_end")
    @classmethod
    def window_times_are_aware(cls, value: datetime | None) -> datetime | None:
        return require_aware_datetime(value) if value is not None else None

    @model_validator(mode="after")
    def observation_window_is_ordered(self) -> "EvidenceItem":
        if (
            self.observation_window_start is not None
            and self.observation_window_end is not None
            and self.observation_window_start > self.observation_window_end
        ):
            raise ValueError("observation window start must not be after its end")
        return self


class EvidenceReference(DomainContract):
    evidence_id: UUID
    source: str = Field(min_length=1, max_length=128)
    digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    relation: Literal["supports", "contradicts", "context"] = "context"
