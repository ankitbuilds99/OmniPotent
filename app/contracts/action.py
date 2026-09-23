"""Versioned, immutable action request and safety envelope."""

from datetime import datetime
from uuid import UUID, uuid4

from pydantic import Field, JsonValue, field_validator, model_validator

from app.contracts.common import (
    ActionLifecycleState,
    ApprovalRequirement,
    DomainContract,
    Environment,
    ExecutionStrategy,
    RiskLevel,
    utc_now,
    require_aware_datetime,
)
from app.contracts.evidence import EvidenceReference
from app.contracts.root_cause import RootCauseAssessment


class ActionTarget(DomainContract):
    resource_type: str = Field(min_length=1, max_length=128)
    resource_id: str = Field(min_length=1, max_length=512)
    namespace: str | None = Field(default=None, max_length=253)
    provider: str | None = Field(default=None, max_length=128)
    region: str | None = Field(default=None, max_length=128)


class Precondition(DomainContract):
    condition_id: UUID = Field(default_factory=uuid4)
    description: str = Field(min_length=1, max_length=2000)
    required: bool = True
    evidence_ids: tuple[UUID, ...] = ()


class ExpectedOutcome(DomainContract):
    description: str = Field(min_length=1, max_length=4000)
    success_criteria: tuple[str, ...] = Field(min_length=1)
    observation_window_seconds: int = Field(gt=0)


class VerificationPredicate(DomainContract):
    predicate_id: UUID = Field(default_factory=uuid4)
    source: str = Field(min_length=1, max_length=128)
    metric_or_signal: str = Field(min_length=1, max_length=256)
    operator: str = Field(min_length=1, max_length=32)
    expected_value: JsonValue
    required: bool = True


class RollbackPlan(DomainContract):
    action_type: str = Field(min_length=1, max_length=128)
    target: ActionTarget
    reason: str = Field(min_length=1, max_length=2000)
    evidence_ids: tuple[UUID, ...] = ()


class BlastRadiusAssessment(DomainContract):
    affected_resource_count: int = Field(ge=0)
    affected_services: tuple[str, ...] = ()
    customer_impact_expected: bool
    estimated_affected_users: int | None = Field(default=None, ge=0)
    summary: str = Field(min_length=1, max_length=2000)


class ActionContract(DomainContract):
    action_id: UUID = Field(default_factory=uuid4)
    incident_id: str = Field(min_length=1, max_length=256)
    tenant_id: str = Field(min_length=1, max_length=256)
    created_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime

    action_type: str = Field(min_length=1, max_length=128)
    target: ActionTarget
    environment: Environment

    reason: str = Field(min_length=1, max_length=8000)
    root_cause: RootCauseAssessment | None = None
    evidence_ids: tuple[UUID, ...] = ()

    preconditions: tuple[Precondition, ...] = ()
    expected_outcome: ExpectedOutcome

    risk_level: RiskLevel
    blast_radius: BlastRadiusAssessment

    execution_strategy: ExecutionStrategy
    verification_predicates: tuple[VerificationPredicate, ...] = ()
    rollback_plan: RollbackPlan | None = None
    approval_requirement: ApprovalRequirement

    requested_capabilities: tuple[str, ...] = ()
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    # Explicit caller assertion prevents safety invariants depending on action
    # name conventions or a hardcoded mutation catalog.
    mutating: bool
    lifecycle_state: ActionLifecycleState = ActionLifecycleState.PROPOSED

    @field_validator("created_at", "expires_at")
    @classmethod
    def contract_times_are_aware(cls, value: datetime) -> datetime:
        return require_aware_datetime(value)

    @model_validator(mode="after")
    def validate_safety_invariants(self) -> "ActionContract":
        now = utc_now()
        if self.expires_at <= now:
            raise ValueError("action contract is expired")
        if self.expires_at <= self.created_at:
            raise ValueError("expires_at must be later than created_at")
        if self.mutating and self.rollback_plan is None:
            raise ValueError("mutating actions require a rollback plan")
        if self.mutating and not self.verification_predicates:
            raise ValueError("mutating actions require at least one verification predicate")
        if self.environment == Environment.PRODUCTION and self.mutating and not self.evidence_ids:
            raise ValueError("production mutations require evidence references")
        if self.execution_strategy == ExecutionStrategy.CANARY and not self.mutating:
            raise ValueError("canary execution strategy requires a mutating action")
        if self.root_cause is not None:
            missing = set(self.root_cause.evidence_ids) - set(self.evidence_ids)
            if missing:
                raise ValueError("root cause evidence must be included in action evidence_ids")
        return self
