"""Legacy runbook compatibility and canonical action normalization tests."""

from datetime import timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.contracts import (
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
from app.contracts.common import utc_now
from app.runbooks.models import DEFAULT_RUNBOOKS, Runbook, RunbookStep, StepType
from app.runbooks.normalizer import (
    ActionDefinition,
    CanonicalActionKind,
    NormalizationContext,
    NormalizationError,
    RunbookNormalizer,
    SafetyGateRequirements,
)


def context(**overrides):
    values = {
        "tenant_id": "tenant-test",
        "incident_id": "incident-test",
        "environment": Environment.PRODUCTION,
        "evidence_ids": (uuid4(),),
        "created_at": utc_now(),
        "expires_at": utc_now() + timedelta(minutes=5),
    }
    values.update(overrides)
    return NormalizationContext(**values)


def definition(step_type: StepType, action: str, *, mutating=True):
    target = ActionTarget(resource_type="configured-resource", resource_id="configured-target")
    return ActionDefinition(
        step_type=step_type,
        legacy_action=action,
        action_type=f"configured.{action}",
        mutating=mutating,
        resource_type="configured-resource",
        target_id_parameter="resource_id",
        namespace_parameter="namespace",
        risk_level=RiskLevel.MEDIUM,
        approval_requirement=ApprovalRequirement.HUMAN,
        execution_strategy=ExecutionStrategy.DRY_RUN,
        reason_template="Configured reason from the trusted capability definition",
        expected_outcome=ExpectedOutcome(
            description="Configured expected result",
            success_criteria=("configured criterion",),
            observation_window_seconds=30,
        ),
        blast_radius=BlastRadiusAssessment(
            affected_resource_count=1,
            affected_services=("configured-service",),
            customer_impact_expected=False,
            summary="Configured blast radius",
        ),
        verification_predicates=(
            VerificationPredicate(
                source="configured-source",
                metric_or_signal="configured-signal",
                operator="equals",
                expected_value="healthy",
            ),
        ) if mutating else (),
        rollback_plan=RollbackPlan(
            action_type="configured.rollback",
            target=target,
            reason="Configured rollback reason",
        ) if mutating else None,
        requested_capabilities=("configured.capability",),
        executor_capability="configured.executor",
    )


def step(step_type: StepType, action: str, **params):
    return RunbookStep(
        id="step-1",
        name="Configured step",
        description="legacy description",
        step_type=step_type,
        action=action,
        params=params,
        requires_approval=True,
    )


@pytest.mark.parametrize("legacy", DEFAULT_RUNBOOKS)
def test_existing_serialized_default_runbooks_still_load(legacy):
    loaded = Runbook.from_dict(legacy)
    assert loaded.id == legacy["id"]
    assert [s.step_type.value for s in loaded.steps] == [s["step_type"] for s in legacy["steps"]]
    serialized = loaded.to_dict()["steps"]
    for original, restored in zip(legacy["steps"], serialized, strict=True):
        assert {key: restored[key] for key in original} == original


@pytest.mark.parametrize("step_type", list(StepType))
def test_legacy_step_vocabulary_is_preserved_on_model_round_trip(step_type):
    raw = {
        "id": "legacy-step",
        "name": "Legacy step",
        "step_type": step_type.value,
        "action": "legacy-action",
        "params": {"resource_id": "resource-a"},
    }
    loaded = RunbookStep.from_dict(raw)
    assert loaded.to_dict()["step_type"] == step_type.value


@pytest.mark.parametrize("step_type", [StepType.KUBERNETES, StepType.TERRAFORM, StepType.HTTP, StepType.SLACK, StepType.CONDITION])
def test_each_configured_mutating_legacy_type_normalizes_to_action_contract(step_type):
    action = "configured-action"
    configured_step = step(step_type, action, resource_id="target-a", namespace="team-a")
    normalizer = RunbookNormalizer({(step_type, action): definition(step_type, action)})

    normalized = normalizer.normalize("runbook-a", configured_step, context())

    assert normalized.kind == CanonicalActionKind.MUTATING
    assert normalized.action_contract is not None
    assert normalized.action_contract.tenant_id == "tenant-test"
    assert normalized.action_contract.target.resource_id == "target-a"
    assert normalized.action_contract.target.namespace == "team-a"
    assert normalized.action_contract.rollback_plan.target == normalized.action_contract.target
    assert normalized.safety_gates == SafetyGateRequirements()
    assert normalized.executor_capability == "configured.executor"


def test_wait_normalizes_as_control_without_execution_contract():
    normalized = RunbookNormalizer({}).normalize(
        "runbook-a", step(StepType.WAIT, "sleep", duration=20), context()
    )
    assert normalized.kind == CanonicalActionKind.CONTROL
    assert normalized.action_contract is None


def test_read_only_registered_step_does_not_create_mutation_contract():
    action = "read-resource"
    normalizer = RunbookNormalizer({(StepType.HTTP, action): definition(StepType.HTTP, action, mutating=False)})
    normalized = normalizer.normalize(
        "runbook-a", step(StepType.HTTP, action, resource_id="target-a"), context()
    )
    assert normalized.kind == CanonicalActionKind.READ_ONLY
    assert normalized.action_contract is None


@pytest.mark.parametrize("raw_type", ["not-a-type", "ai_action", "script", "approval", "notification"])
def test_invalid_and_ai_generated_step_types_are_rejected(raw_type):
    with pytest.raises(NormalizationError, match="unsupported runbook step type"):
        RunbookNormalizer({}).normalize_type(raw_type)
    with pytest.raises(ValueError):
        RunbookStep.from_dict({"id": "s", "name": "S", "step_type": raw_type, "action": "do"})


def test_unregistered_actions_fail_closed_and_command_steps_are_never_capabilities():
    normalizer = RunbookNormalizer({})
    with pytest.raises(NormalizationError, match="no trusted action definition"):
        normalizer.normalize("rb", step(StepType.KUBERNETES, "unknown", resource_id="x"), context())
    with pytest.raises(NormalizationError, match="shell commands"):
        normalizer.normalize("rb", step(StepType.COMMAND, "kubectl apply", resource_id="x"), context())


def test_production_mutation_requires_evidence_and_resolved_target():
    action = "configured-action"
    normalizer = RunbookNormalizer({(StepType.KUBERNETES, action): definition(StepType.KUBERNETES, action)})
    with pytest.raises(NormalizationError, match="evidence"):
        normalizer.normalize("rb", step(StepType.KUBERNETES, action, resource_id="x"), context(evidence_ids=()))
    with pytest.raises(NormalizationError, match="target"):
        normalizer.normalize("rb", step(StepType.KUBERNETES, action), context())
    with pytest.raises(NormalizationError, match="unresolved"):
        normalizer.normalize("rb", step(StepType.KUBERNETES, action, resource_id="${service}"), context())


@pytest.mark.parametrize("change, message", [
    ({"rollback_plan": None}, "rollback plan"),
    ({"verification_predicates": ()}, "verification predicates"),
])
def test_incomplete_trusted_mutation_definition_is_rejected(change, message):
    action = "configured-action"
    configured = definition(StepType.KUBERNETES, action).model_copy(update=change)
    normalizer = RunbookNormalizer({(StepType.KUBERNETES, action): configured})
    with pytest.raises(NormalizationError, match=message):
        normalizer.normalize("rb", step(StepType.KUBERNETES, action, resource_id="x"), context())


def test_step_cannot_weaken_approval_in_trusted_definition():
    action = "configured-action"
    configured = definition(StepType.KUBERNETES, action).model_copy(
        update={"approval_requirement": ApprovalRequirement.NONE}
    )
    normalizer = RunbookNormalizer({(StepType.KUBERNETES, action): configured})
    with pytest.raises(NormalizationError, match="cannot be weakened"):
        normalizer.normalize("rb", step(StepType.KUBERNETES, action, resource_id="x"), context())


def test_mutating_action_requires_approval_even_if_definition_omits_it():
    action = "configured-action"
    configured = definition(StepType.KUBERNETES, action).model_copy(
        update={"approval_requirement": ApprovalRequirement.NONE}
    )
    normalizer = RunbookNormalizer({(StepType.KUBERNETES, action): configured})
    # The runbook step itself doesn't request approval, so the mandatory
    # mutating-action invariant is what rejects this definition.
    unguarded = RunbookStep(
        id="s", name="s", description="", step_type=StepType.KUBERNETES,
        action=action, params={"resource_id": "x"}, requires_approval=False,
    )
    with pytest.raises(NormalizationError, match="require an approval gate"):
        normalizer.normalize("rb", unguarded, context())


def test_context_requires_tenant_and_aware_timestamps():
    with pytest.raises(ValidationError):
        context(tenant_id="")
    with pytest.raises(ValidationError):
        context(created_at=utc_now().replace(tzinfo=None))
