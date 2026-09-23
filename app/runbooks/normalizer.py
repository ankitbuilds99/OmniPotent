"""Compatibility boundary from legacy runbook steps to V2 action contracts.

This module does not execute steps or make authorization decisions. A caller
must provide a trusted capability registry and separately run policy,
approval, simulation, and audit before any eventual execution.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Mapping
from uuid import UUID

from pydantic import Field, JsonValue, field_validator, model_validator

from app.contracts import (
    ActionContract,
    ActionTarget,
    ApprovalRequirement,
    BlastRadiusAssessment,
    Environment,
    ExecutionStrategy,
    ExpectedOutcome,
    RiskLevel,
    RollbackPlan,
    VerificationPredicate,
)
from app.contracts.common import DomainContract
from app.runbooks.models import Runbook, RunbookStep, StepType


class NormalizationError(ValueError):
    """A legacy step cannot safely be represented as a canonical action."""


class CanonicalActionKind(StrEnum):
    CONTROL = "control"
    READ_ONLY = "read_only"
    MUTATING = "mutating"


class SafetyGateRequirements(DomainContract):
    """Mandatory pipeline stages for a normalized runbook operation."""

    policy: bool = True
    approval: bool = True
    simulation: bool = True
    audit: bool = True


class NormalizationContext(DomainContract):
    """Request-scoped identity and evidence needed to construct a contract."""

    tenant_id: str = Field(min_length=1, max_length=256)
    incident_id: str = Field(min_length=1, max_length=256)
    environment: Environment
    evidence_ids: tuple[UUID, ...] = ()
    created_at: datetime
    expires_at: datetime

    @field_validator("created_at", "expires_at")
    @classmethod
    def timestamps_are_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("normalization timestamps must be timezone-aware")
        return value


class ActionDefinition(DomainContract):
    """Trusted, injected definition for one legacy step capability.

    The action's safety envelope is configuration owned by the integration or
    policy layer. The normalizer intentionally has no built-in action names or
    target conventions.
    """

    step_type: StepType
    legacy_action: str = Field(min_length=1, max_length=256)
    action_type: str = Field(min_length=1, max_length=128)
    mutating: bool
    resource_type: str = Field(min_length=1, max_length=128)
    target_id_parameter: str = Field(min_length=1, max_length=128)
    namespace_parameter: str | None = Field(default=None, max_length=128)
    provider: str | None = Field(default=None, max_length=128)
    region: str | None = Field(default=None, max_length=128)
    risk_level: RiskLevel
    approval_requirement: ApprovalRequirement
    execution_strategy: ExecutionStrategy
    reason_template: str = Field(min_length=1, max_length=8000)
    expected_outcome: ExpectedOutcome
    blast_radius: BlastRadiusAssessment
    verification_predicates: tuple[VerificationPredicate, ...] = ()
    rollback_plan: RollbackPlan | None = None
    requested_capabilities: tuple[str, ...] = ()
    executor_capability: str = Field(min_length=1, max_length=256)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class CanonicalAction(DomainContract):
    """Normalized runbook operation; mutating entries carry an ActionContract."""

    runbook_id: str = Field(min_length=1, max_length=256)
    step_id: str = Field(min_length=1, max_length=256)
    kind: CanonicalActionKind
    action_contract: ActionContract | None = None
    safety_gates: SafetyGateRequirements = Field(default_factory=SafetyGateRequirements)
    executor_capability: str | None = None

    @model_validator(mode="after")
    def validate_contract_presence(self) -> "CanonicalAction":
        if self.kind == CanonicalActionKind.MUTATING and self.action_contract is None:
            raise ValueError("mutating canonical actions require an ActionContract")
        if self.kind != CanonicalActionKind.MUTATING and self.action_contract is not None:
            raise ValueError("only mutating canonical actions may carry an ActionContract")
        if not all((self.safety_gates.policy, self.safety_gates.approval,
                    self.safety_gates.simulation, self.safety_gates.audit)):
            raise ValueError("runbook normalization cannot disable mandatory safety gates")
        return self


class RunbookNormalizer:
    """Normalize steps against a caller-supplied, trusted capability registry."""

    def __init__(self, definitions: Mapping[tuple[StepType, str], ActionDefinition]):
        self._definitions = dict(definitions)
        for key, definition in self._definitions.items():
            if key != (definition.step_type, definition.legacy_action):
                raise ValueError("capability registry key does not match its action definition")

    def normalize(
        self,
        runbook_id: str,
        step: RunbookStep,
        context: NormalizationContext,
    ) -> CanonicalAction:
        if step.step_type == StepType.WAIT:
            return CanonicalAction(
                runbook_id=runbook_id,
                step_id=step.id,
                kind=CanonicalActionKind.CONTROL,
                safety_gates=SafetyGateRequirements(),
            )

        # Free-form shell commands are deliberately never considered a
        # capability, even when a caller accidentally registers one.
        if step.step_type == StepType.COMMAND:
            raise NormalizationError("legacy shell commands cannot be normalized into production actions")

        definition = self._definitions.get((step.step_type, step.action))
        if definition is None:
            raise NormalizationError(
                f"no trusted action definition for {step.step_type.value}:{step.action}"
            )
        if step.requires_approval and definition.approval_requirement == ApprovalRequirement.NONE:
            raise NormalizationError("runbook approval requirements cannot be weakened by an action definition")

        target_id = step.params.get(definition.target_id_parameter)
        if not isinstance(target_id, str) or not target_id.strip():
            raise NormalizationError("action target parameter is missing or invalid")
        if "${" in target_id or "{{" in target_id:
            raise NormalizationError("action target contains an unresolved runbook variable")
        namespace = None
        if definition.namespace_parameter:
            candidate = step.params.get(definition.namespace_parameter)
            if candidate is not None and not isinstance(candidate, str):
                raise NormalizationError("action namespace parameter is invalid")
            namespace = candidate

        target = ActionTarget(
            resource_type=definition.resource_type,
            resource_id=target_id,
            namespace=namespace,
            provider=definition.provider,
            region=definition.region,
        )
        kind = CanonicalActionKind.MUTATING if definition.mutating else CanonicalActionKind.READ_ONLY
        contract = None
        if definition.mutating:
            if definition.approval_requirement == ApprovalRequirement.NONE:
                raise NormalizationError("mutating runbook actions require an approval gate")
            if definition.rollback_plan is None:
                raise NormalizationError("mutating action definition requires a rollback plan")
            if not definition.verification_predicates:
                raise NormalizationError("mutating action definition requires verification predicates")
            if context.environment == Environment.PRODUCTION and not context.evidence_ids:
                raise NormalizationError("production mutations require evidence references")
            rollback = definition.rollback_plan.model_copy(update={"target": target})
            contract = ActionContract(
                incident_id=context.incident_id,
                tenant_id=context.tenant_id,
                created_at=context.created_at,
                expires_at=context.expires_at,
                action_type=definition.action_type,
                target=target,
                environment=context.environment,
                reason=definition.reason_template,
                evidence_ids=tuple(context.evidence_ids),
                expected_outcome=definition.expected_outcome,
                risk_level=definition.risk_level,
                blast_radius=definition.blast_radius,
                execution_strategy=definition.execution_strategy,
                verification_predicates=definition.verification_predicates,
                rollback_plan=rollback,
                approval_requirement=definition.approval_requirement,
                requested_capabilities=definition.requested_capabilities or (definition.executor_capability,),
                metadata=definition.metadata,
                mutating=True,
            )

        return CanonicalAction(
            runbook_id=runbook_id,
            step_id=step.id,
            kind=kind,
            action_contract=contract,
            executor_capability=definition.executor_capability,
        )

    def normalize_runbook(
        self, runbook: Runbook, context: NormalizationContext
    ) -> tuple[CanonicalAction, ...]:
        """Normalize all steps in order; any unsafe step rejects the whole plan."""
        return tuple(
            self.normalize(runbook.id, step, context)
            for step in runbook.steps
        )

    def normalize_type(self, step_type: str) -> StepType:
        """Validate raw legacy/UI input without coercing unknown values."""
        try:
            return StepType(step_type)
        except ValueError as exc:
            raise NormalizationError(f"unsupported runbook step type: {step_type}") from exc
