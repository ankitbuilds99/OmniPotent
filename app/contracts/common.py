"""Shared, versioned primitives for OmniPotent domain contracts.

Contracts are immutable snapshots. New compatible fields can be added in later
minor schema revisions; consumers should branch on ``schema_version`` and must
not silently reinterpret an unknown major version.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter, field_validator, model_validator


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


class RiskLevel(StrEnum):
    INFORMATIONAL = "informational"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ApprovalRequirement(StrEnum):
    NONE = "none"
    HUMAN = "human"
    TWO_PERSON = "two_person"


class ExecutionStrategy(StrEnum):
    DRY_RUN = "dry_run"
    IMMEDIATE = "immediate"
    CANARY = "canary"
    STAGED = "staged"
    MANUAL = "manual"


class VerificationOutcome(StrEnum):
    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"
    INCONCLUSIVE = "inconclusive"
    SKIPPED = "skipped"


class TrustDecisionKind(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


class ActionLifecycleState(StrEnum):
    PROPOSED = "proposed"
    VALIDATED = "validated"
    AUTHORIZED = "authorized"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"
    EXPIRED = "expired"
    REJECTED = "rejected"


class TrustReasonCode(StrEnum):
    ALLOWED = "allowed"
    POLICY_DENIED = "policy_denied"
    APPROVAL_REQUIRED = "approval_required"
    CONTRACT_EXPIRED = "contract_expired"
    TENANT_MISMATCH = "tenant_mismatch"
    EVIDENCE_MISSING = "evidence_missing"
    TARGET_OUT_OF_SCOPE = "target_out_of_scope"
    POLICY_UNAVAILABLE = "policy_unavailable"


class DomainContract(BaseModel):
    """Base for immutable JSON-safe domain records."""

    model_config = ConfigDict(
        # Forward-compatible additive fields are retained as JSON values and
        # participate in canonical hashing. They cannot replace declared
        # fields; authorization code must branch on schema_version and must
        # never infer policy from an unrecognized extension.
        extra="allow",
        frozen=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    # Pydantic evaluates this inherited special annotation in subclasses'
    # modules; ``object`` keeps the declaration resolvable, while the validator
    # below limits each value to JSON-compatible data.
    __pydantic_extra__: dict[str, object] = Field(init=False)
    schema_version: str = Field(default="2.0.0", min_length=5, max_length=32)

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, value: str) -> str:
        # Semantic version format allows consumers to distinguish additive
        # revisions from incompatible major revisions without assuming a value.
        parts = value.split(".")
        if len(parts) != 3 or any(not part.isdigit() for part in parts):
            raise ValueError("schema_version must use numeric major.minor.patch format")
        return value

    @model_validator(mode="after")
    def validate_forward_extensions(self) -> "DomainContract":
        adapter: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)
        for name, value in (self.__pydantic_extra__ or {}).items():
            try:
                adapter.validate_python(value)
            except Exception as exc:
                raise ValueError(f"extension field {name!r} must contain JSON-compatible data") from exc
        return self

    @staticmethod
    def _jsonable(value: Any) -> Any:
        if isinstance(value, datetime):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("timestamps must be timezone-aware")
            return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        if isinstance(value, UUID):
            return str(value)
        if isinstance(value, StrEnum):
            return value.value
        if isinstance(value, BaseModel):
            return {key: DomainContract._jsonable(item) for key, item in value.model_dump().items()}
        if isinstance(value, dict):
            return {str(key): DomainContract._jsonable(item) for key, item in value.items()}
        if isinstance(value, (tuple, list)):
            return [DomainContract._jsonable(item) for item in value]
        return value

    def canonical_dict(self) -> dict[str, Any]:
        """Return an enum-normalized, UTC-normalized JSON representation."""
        return self._jsonable(self)

    def canonical_json(self) -> str:
        """Stable compact JSON used for hashes and signatures."""
        return json.dumps(
            self.canonical_dict(), sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False,
        )

    @property
    def canonical_digest(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


class ImmutableIdentifier(DomainContract):
    """Reusable immutable UUID identity for domain records."""

    id: UUID


def require_aware_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    return value


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


Metadata = dict[str, JsonValue]
