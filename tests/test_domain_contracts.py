"""Unit tests for the canonical V2 domain-contract layer."""

from datetime import timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.contracts import (
    ActionContract,
    ActionLifecycleState,
    ActionTarget,
    ApprovalRequirement,
    BlastRadiusAssessment,
    Environment,
    EvidenceItem,
    EvidenceReference,
    ExecutionReceipt,
    ExecutionResult,
    ExecutionStrategy,
    ExpectedOutcome,
    Hypothesis,
    Precondition,
    PredicateResult,
    RiskLevel,
    RollbackPlan,
    RootCauseAssessment,
    RootCauseStatus,
    TrustDecision,
    TrustDecisionKind,
    TrustReasonCode,
    VerificationOutcome,
    VerificationPredicate,
    VerificationResult,
)
from app.contracts.common import utc_now


def valid_action(**overrides):
    evidence_id = uuid4()
    target = ActionTarget(resource_type="deployment", resource_id="checkout", namespace="prod")
    values = {
        "incident_id": "incident-1",
        "tenant_id": "tenant-1",
        "created_at": utc_now(),
        "expires_at": utc_now() + timedelta(minutes=5),
        "action_type": "restart",
        "target": target,
        "environment": Environment.PRODUCTION,
        "reason": "Restart based on observed incident evidence",
        "evidence_ids": (evidence_id,),
        "expected_outcome": ExpectedOutcome(
            description="Service returns to healthy state",
            success_criteria=("error rate returns below the configured SLO",),
            observation_window_seconds=60,
        ),
        "risk_level": RiskLevel.MEDIUM,
        "blast_radius": BlastRadiusAssessment(
            affected_resource_count=1,
            affected_services=("checkout",),
            customer_impact_expected=False,
            summary="One deployment",
        ),
        "execution_strategy": ExecutionStrategy.DRY_RUN,
        "verification_predicates": (
            VerificationPredicate(
                source="metrics",
                metric_or_signal="error_rate",
                operator="less_than",
                expected_value=0.01,
            ),
        ),
        "rollback_plan": RollbackPlan(
            action_type="restore_previous_revision",
            target=target,
            reason="Restore previous deployment revision",
        ),
        "approval_requirement": ApprovalRequirement.HUMAN,
        "requested_capabilities": ("deployment.restart",),
        "metadata": {"change_ticket": "CHG-1", "attempt": 1},
        "mutating": True,
    }
    values.update(overrides)
    if values.get("root_cause") is not None:
        values["evidence_ids"] = values["root_cause"].evidence_ids
    return ActionContract(**values)


def test_contracts_round_trip_as_stable_json_and_hash():
    action = valid_action()
    encoded = action.canonical_json()
    decoded = ActionContract.model_validate_json(encoded)
    assert decoded == action
    assert decoded.schema_version == "2.0.0"
    assert decoded.canonical_digest == action.canonical_digest
    assert action.canonical_json() == ActionContract.model_validate_json(encoded).canonical_json()

    evidence = EvidenceItem(source="metrics", evidence_type="measurement", payload={"rate": 0.0})
    assert EvidenceItem.model_validate_json(evidence.model_dump_json()) == evidence
    assert EvidenceReference(evidence_id=evidence.evidence_id, source="metrics")
    hypothesis = Hypothesis(statement="Dependency timeout", confidence=0.7)
    assert Hypothesis.model_validate_json(hypothesis.model_dump_json()) == hypothesis


def test_nested_domain_contracts_round_trip():
    evidence_id = uuid4()
    hypothesis = Hypothesis(
        statement="A recent deployment caused elevated errors",
        confidence=0.8,
        evidence_ids=(evidence_id,),
    )
    root_cause = RootCauseAssessment(
        status=RootCauseStatus.LIKELY,
        hypothesis_id=hypothesis.hypothesis_id,
        statement=hypothesis.statement,
        confidence=hypothesis.confidence,
        evidence_ids=(evidence_id,),
    )
    action = valid_action(root_cause=root_cause)
    trust = TrustDecision(
        action_id=action.action_id,
        tenant_id=action.tenant_id,
        decision=TrustDecisionKind.REQUIRE_APPROVAL,
        reason_codes=(TrustReasonCode.APPROVAL_REQUIRED,),
        policy_version="policy-7",
        expires_at=utc_now() + timedelta(minutes=1),
    )
    execution = ExecutionResult(
        action_id=action.action_id,
        state=ActionLifecycleState.EXECUTING,
        simulated=True,
        dry_run=True,
        executor="local-simulator",
    )
    verification = VerificationResult(
        action_id=action.action_id,
        outcome=VerificationOutcome.PENDING,
        predicate_results=(
            PredicateResult(
                predicate_id=action.verification_predicates[0].predicate_id,
                outcome=VerificationOutcome.PENDING,
            ),
        ),
    )
    receipt = ExecutionReceipt(
        tenant_id=action.tenant_id,
        action_contract=action,
        trust_decision=trust,
        execution_result=execution,
        verification_result=verification,
    )
    restored = ExecutionReceipt.model_validate_json(receipt.model_dump_json())
    assert restored == receipt
    assert len(restored.canonical_digest) == 64


def test_models_are_immutable_and_ids_are_uuid_typed():
    action = valid_action()
    with pytest.raises(ValidationError):
        action.tenant_id = "other-tenant"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        ActionTarget(resource_type="deployment", resource_id=" ")


@pytest.mark.parametrize(
    "override, message",
    [
        ({"tenant_id": ""}, "tenant_id"),
        ({"target": None}, "target"),
        ({"risk_level": "catastrophic-ish"}, "risk_level"),
        ({"execution_strategy": "instant_magic"}, "execution_strategy"),
        ({"rollback_plan": None}, "rollback plan"),
        ({"verification_predicates": ()}, "verification predicate"),
        ({"evidence_ids": ()}, "production mutations require evidence"),
        ({"expires_at": utc_now() - timedelta(seconds=1)}, "expired"),
    ],
)
def test_invalid_production_contracts_are_rejected(override, message):
    with pytest.raises(ValidationError, match=message):
        valid_action(**override)


def test_read_only_contract_does_not_require_mutation_safeguards():
    action = valid_action(
        mutating=False,
        rollback_plan=None,
        verification_predicates=(),
        environment=Environment.STAGING,
        evidence_ids=(),
    )
    assert action.mutating is False


def test_naive_timestamps_fail_and_json_extensions_round_trip():
    with pytest.raises(ValidationError, match="timezone-aware"):
        EvidenceItem(
            source="metrics",
            evidence_type="sample",
            observed_at=utc_now().replace(tzinfo=None),
            payload={"value": 1},
        )
    action = valid_action(extension_field={"future": ["value", 2]})
    restored = ActionContract.model_validate_json(action.canonical_json())
    assert restored.model_dump()["extension_field"] == {"future": ["value", 2]}
    assert restored.canonical_digest == action.canonical_digest


def test_execution_receipt_rejects_cross_action_or_cross_tenant_binding():
    action = valid_action()
    decision = TrustDecision(
        action_id=uuid4(),
        tenant_id=action.tenant_id,
        decision=TrustDecisionKind.ALLOW,
        reason_codes=(TrustReasonCode.ALLOWED,),
        policy_version="policy-1",
        expires_at=utc_now() + timedelta(minutes=1),
    )
    with pytest.raises(ValidationError, match="bind to the receipt action"):
        ExecutionReceipt(
            tenant_id=action.tenant_id,
            action_contract=action,
            trust_decision=decision,
            execution_result=ExecutionResult(
                action_id=action.action_id,
                state=ActionLifecycleState.EXECUTING,
                simulated=True,
                dry_run=True,
                executor="test",
            ),
            verification_result=VerificationResult(
                action_id=action.action_id,
                outcome=VerificationOutcome.PENDING,
            ),
        )
