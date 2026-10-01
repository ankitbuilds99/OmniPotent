"""Evidence-layer typed models and deterministic artifact helpers."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter, field_validator

from app.contracts.common import DomainContract, require_aware_datetime
from app.contracts.evidence import EvidenceDomain, EvidenceTrustLevel
class EvidenceSourceType(StrEnum):
    LOGS = "logs"
    METRICS = "metrics"
    TRACES = "traces"
    DEPLOYMENTS = "deployments"
    GITHUB = "github"
    KUBERNETES = "kubernetes"
    CLOUD_PROVIDER = "cloud_provider"
    INCIDENT_SYSTEM = "incident_system"
    MCP_TOOL = "mcp_tool"
    RUNBOOK = "runbook"
    SYNTHETIC_TEST = "synthetic_test"
    OTHER = "other"


class EvidenceTrustStatus(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    STALE = "stale"
    UNVERIFIED = "unverified"


class EvidenceReasonCode(StrEnum):
    TENANT_MISMATCH = "tenant_mismatch"
    INCIDENT_MISMATCH = "incident_mismatch"
    HASH_MISSING = "artifact_hash_missing"
    HASH_MISMATCH = "artifact_hash_mismatch"
    PROVENANCE_MISSING = "provenance_missing"
    PROVENANCE_MISMATCH = "provenance_mismatch"
    STALE = "evidence_stale"
    FUTURE_OBSERVATION = "future_observation"
    SOURCE_UNVERIFIED = "source_unverified"
    FRESH = "evidence_fresh"


class EvidenceContext(BaseModel):
    """Trusted request context supplied by the caller, never by tool output."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)
    tenant_id: str = Field(min_length=1, max_length=256)
    incident_id: str = Field(min_length=1, max_length=256)
    now: datetime

    @field_validator("tenant_id")
    @classmethod
    def tenant_must_be_resolved(cls, value: str) -> str:
        if value in {"-", "unknown"}:
            raise ValueError("tenant_id must be resolved")
        return value

    @field_validator("now")
    @classmethod
    def now_is_aware(cls, value: datetime) -> datetime:
        return require_aware_datetime(value)


class EvidenceSource(BaseModel):
    """Trusted connector/source description; callers inject it per integration."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)
    source: str = Field(min_length=1, max_length=128)
    source_type: EvidenceSourceType
    domain: EvidenceDomain | None = None
    source_identity: str = Field(min_length=1, max_length=512)
    tool_name: str | None = Field(default=None, max_length=256)
    tool_version: str | None = Field(default=None, max_length=128)
    connector_version: str | None = Field(default=None, max_length=128)


class FreshnessPolicy(BaseModel):
    """Optional stale threshold. There is deliberately no implicit TTL."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    maximum_age_seconds: float = Field(gt=0)


class EvidenceTrustAssessment(DomainContract):
    evidence_id: str
    status: EvidenceTrustStatus
    trust_level: EvidenceTrustLevel
    reason_codes: tuple[EvidenceReasonCode, ...]
    assessed_at: datetime
    provenance_digest: str | None = None

    @field_validator("assessed_at")
    @classmethod
    def assessed_time_is_aware(cls, value: datetime) -> datetime:
        return require_aware_datetime(value)


def canonical_artifact_json(payload: JsonValue) -> str:
    """Stable compact JSON of a normalized JSON payload."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def artifact_hash(payload: JsonValue) -> str:
    """SHA-256 of canonical normalized artifact data."""
    return hashlib.sha256(canonical_artifact_json(payload).encode("utf-8")).hexdigest()


def json_value(value: Any) -> JsonValue:
    """Validate and normalize a connector value as JSON-safe data."""
    adapter: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)
    return adapter.validate_python(value)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
