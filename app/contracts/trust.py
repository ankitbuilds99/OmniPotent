"""Deterministic trust evaluation output with machine-readable reasons."""

from datetime import datetime
from uuid import UUID, uuid4

from pydantic import Field, field_validator

from app.contracts.common import DomainContract, TrustDecisionKind, TrustReasonCode, utc_now, require_aware_datetime


class TrustDecision(DomainContract):
    decision_id: UUID = Field(default_factory=uuid4)
    action_id: UUID
    tenant_id: str = Field(min_length=1, max_length=256)
    decision: TrustDecisionKind
    reason_codes: tuple[TrustReasonCode, ...] = Field(min_length=1)
    policy_version: str = Field(min_length=1, max_length=128)
    evaluated_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime

    @field_validator("evaluated_at", "expires_at")
    @classmethod
    def trust_times_are_aware(cls, value: datetime) -> datetime:
        return require_aware_datetime(value)

    @property
    def allowed(self) -> bool:
        return self.decision == TrustDecisionKind.ALLOW
