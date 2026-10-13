"""Structured execution and verification outputs."""

from datetime import datetime
from uuid import UUID, uuid4

from pydantic import Field, field_validator

from app.contracts.common import (
    ActionLifecycleState,
    DomainContract,
    ExecutionResultState,
    VerificationOutcome,
    require_aware_datetime,
    utc_now,
)


class ExecutionResult(DomainContract):
    execution_id: UUID = Field(default_factory=uuid4)
    action_id: UUID
    # Accept legacy lifecycle values while exposing distinct provider-result
    # states for new runners and reconciliation paths.
    state: ActionLifecycleState | ExecutionResultState
    started_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None
    simulated: bool
    dry_run: bool
    executor: str = Field(min_length=1, max_length=256)
    output_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    reason_code: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("started_at", "completed_at")
    @classmethod
    def execution_times_are_aware(cls, value: datetime | None) -> datetime | None:
        return require_aware_datetime(value) if value is not None else None

    @field_validator("completed_at")
    @classmethod
    def completion_after_start(cls, value: datetime | None, info):
        started = info.data.get("started_at")
        if value is not None and started is not None and value < started:
            raise ValueError("completed_at must not precede started_at")
        return value


class PredicateResult(DomainContract):
    predicate_id: UUID
    outcome: VerificationOutcome
    evidence_ids: tuple[UUID, ...] = ()
    reason_code: str | None = Field(default=None, min_length=1, max_length=128)


class VerificationResult(DomainContract):
    verification_id: UUID = Field(default_factory=uuid4)
    action_id: UUID
    outcome: VerificationOutcome
    predicate_results: tuple[PredicateResult, ...] = ()
    verified_at: datetime = Field(default_factory=utc_now)
    causal_attribution: bool = False
    reason_code: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("verified_at")
    @classmethod
    def verification_time_is_aware(cls, value: datetime) -> datetime:
        return require_aware_datetime(value)
