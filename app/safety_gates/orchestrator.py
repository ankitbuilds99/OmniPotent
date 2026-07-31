"""Safety Gate Orchestrator - Runs all 3 gates in sequence.

Flow:
1. Run Confidence Gate (0.8 threshold)
2. Run Policy Gate (blast radius, injection, rules)
3. Run Sandbox Gate (test in isolation)
4. Return combined decision

All pass → AUTO_APPROVED (can auto-remediate)
Any fail → ESCALATED or BLOCKED (needs human)
"""

from __future__ import annotations

import logging
import time
from typing import Any
from uuid import UUID, uuid4

from app.safety_gates.confidence_gate import ConfidenceGate
from app.safety_gates.policy_gate import PolicyGate
from app.safety_gates.sandbox_gate import SandboxGate
from app.safety_gates.models import (
    GateResult,
    SafetyDecision,
    SafetyEvaluationResult,
)

log = logging.getLogger("devcopilot.safety_gates.orchestrator")


class SafetyGateOrchestrator:
    """Orchestrates all 3 safety gates in sequence.

    This is the main entry point for the safety system.
    It runs each gate and aggregates the results into a final decision.
    """

    def __init__(
        self,
        confidence_gate: ConfidenceGate | None = None,
        policy_gate: PolicyGate | None = None,
        sandbox_gate: SandboxGate | None = None,
        sandbox_enabled: bool = True,
    ):
        """Initialize the orchestrator with gates.

        Args:
            confidence_gate: Custom confidence gate (or use default)
            policy_gate: Custom policy gate (or use default)
            sandbox_gate: Custom sandbox gate (or use default)
            sandbox_enabled: Whether to run sandbox verification
        """
        self.confidence_gate = confidence_gate or ConfidenceGate()
        self.policy_gate = policy_gate or PolicyGate()
        self.sandbox_gate = sandbox_gate or SandboxGate(enabled=sandbox_enabled)

    async def evaluate(
        self,
        incident_id: UUID,
        organization_id: UUID,
        action_type: str,
        action_details: dict[str, Any],
        investigation_result: dict[str, Any],
        target_service: str | None = None,
        target_environment: str = "production",
    ) -> SafetyEvaluationResult:
        """Run all 3 safety gates and return combined result.

        Args:
            incident_id: The incident being remediated
            organization_id: Tenant ID
            action_type: Type of action (rollback, scale, restart, etc.)
            action_details: Specific action parameters
            investigation_result: Result from parallel investigation
            target_service: Service being acted on
            target_environment: Environment (production, staging, etc.)

        Returns:
            SafetyEvaluationResult with final decision
        """
        evaluation_id = uuid4()
        start_time = time.time()

        log.info(
            "Starting safety evaluation: eval_id=%s incident=%s action=%s",
            evaluation_id, incident_id, action_type,
        )

        # Gate 1: Confidence
        confidence_result = self.confidence_gate.evaluate(
            action_type=action_type,
            action_details=action_details,
            investigation_result=investigation_result,
            organization_id=str(organization_id),
        )

        # Gate 2: Policy (always run, even if confidence fails)
        policy_result = self.policy_gate.evaluate(
            action_type=action_type,
            action_details=action_details,
            organization_id=str(organization_id),
            target_service=target_service,
            target_environment=target_environment,
        )

        # Gate 3: Sandbox (only if confidence and policy pass)
        if confidence_result.passed and policy_result.passed:
            sandbox_result = await self.sandbox_gate.evaluate(
                action_type=action_type,
                action_details=action_details,
                organization_id=str(organization_id),
            )
        else:
            # Skip sandbox if earlier gates failed
            sandbox_result = GateResult(
                gate_name="sandbox",
                passed=False,
                reasoning="Skipped (earlier gate failed)",
                violations=[],
            )

        # Determine final decision
        all_gates_passed = (
            confidence_result.passed and
            policy_result.passed and
            sandbox_result.passed
        )

        # Check for blocking violations
        all_violations = (
            confidence_result.violations +
            policy_result.violations +
            sandbox_result.violations
        )
        has_blocking = any(v.get("action") == "block" for v in all_violations)

        if has_blocking:
            final_decision = SafetyDecision.BLOCKED
            escalation_reason = self._get_blocking_reason(all_violations)
        elif all_gates_passed:
            final_decision = SafetyDecision.AUTO_APPROVED
            escalation_reason = None
        else:
            final_decision = SafetyDecision.ESCALATED
            escalation_reason = self._build_escalation_reason(
                confidence_result, policy_result, sandbox_result
            )

        duration_ms = int((time.time() - start_time) * 1000)

        log.info(
            "Safety evaluation complete: eval_id=%s decision=%s duration=%dms",
            evaluation_id, final_decision.value, duration_ms,
        )

        # Build approval context for human review
        approval_context = self._build_approval_context(
            action_type=action_type,
            action_details=action_details,
            target_service=target_service,
            target_environment=target_environment,
            investigation_result=investigation_result,
            confidence_result=confidence_result,
            policy_result=policy_result,
            sandbox_result=sandbox_result,
        )

        return SafetyEvaluationResult(
            evaluation_id=evaluation_id,
            action_type=action_type,
            action_details=action_details,
            confidence_gate=confidence_result,
            policy_gate=policy_result,
            sandbox_gate=sandbox_result,
            all_gates_passed=all_gates_passed,
            final_decision=final_decision,
            escalation_reason=escalation_reason,
            requires_human_approval=final_decision != SafetyDecision.AUTO_APPROVED,
            approval_context=approval_context,
        )

    def _get_blocking_reason(self, violations: list[dict[str, Any]]) -> str:
        """Get the reason for blocking."""
        blocking = [v for v in violations if v.get("action") == "block"]
        if blocking:
            return "; ".join(v.get("message", "Blocked") for v in blocking[:3])
        return "Action blocked due to policy violations"

    def _build_escalation_reason(
        self,
        confidence_result: GateResult,
        policy_result: GateResult,
        sandbox_result: GateResult,
    ) -> str:
        """Build explanation for why escalation is needed."""
        reasons = []

        if not confidence_result.passed:
            reasons.append(
                f"Confidence ({confidence_result.score:.0%}) below threshold ({confidence_result.threshold:.0%})"
            )

        if not policy_result.passed:
            violation_types = [v["type"] for v in policy_result.violations]
            reasons.append(f"Policy violations: {', '.join(violation_types)}")

        if not sandbox_result.passed and "Skipped" not in sandbox_result.reasoning:
            reasons.append("Sandbox verification failed")

        return "; ".join(reasons)

    def _build_approval_context(
        self,
        action_type: str,
        action_details: dict[str, Any],
        target_service: str | None,
        target_environment: str,
        investigation_result: dict[str, Any],
        confidence_result: GateResult,
        policy_result: GateResult,
        sandbox_result: GateResult,
    ) -> dict[str, Any]:
        """Build context for human approval UI."""
        return {
            "action": {
                "type": action_type,
                "details": action_details,
                "target_service": target_service,
                "target_environment": target_environment,
            },
            "investigation": {
                "root_cause": investigation_result.get("root_cause_hypothesis", ""),
                "summary": investigation_result.get("summary", ""),
                "confidence": investigation_result.get("confidence_score", 0),
            },
            "gates": {
                "confidence": {
                    "passed": confidence_result.passed,
                    "score": confidence_result.score,
                    "threshold": confidence_result.threshold,
                    "reasoning": confidence_result.reasoning,
                },
                "policy": {
                    "passed": policy_result.passed,
                    "violations": len(policy_result.violations),
                    "reasoning": policy_result.reasoning,
                },
                "sandbox": {
                    "passed": sandbox_result.passed,
                    "score": sandbox_result.score,
                    "reasoning": sandbox_result.reasoning,
                },
            },
        }


# Convenience function for integration
async def evaluate_action_safety(
    incident_id: UUID,
    organization_id: UUID,
    action_type: str,
    action_details: dict[str, Any],
    investigation_result: dict[str, Any],
    target_service: str | None = None,
    target_environment: str = "production",
    sandbox_enabled: bool = True,
) -> SafetyEvaluationResult:
    """Evaluate action through all 3 safety gates.

    This is the main entry point for safety evaluation.

    Args:
        incident_id: Incident being remediated
        organization_id: Tenant ID
        action_type: Type of action
        action_details: Action parameters
        investigation_result: Result from investigation
        target_service: Service being acted on
        target_environment: Environment
        sandbox_enabled: Whether to run sandbox

    Returns:
        SafetyEvaluationResult with decision
    """
    orchestrator = SafetyGateOrchestrator(sandbox_enabled=sandbox_enabled)

    return await orchestrator.evaluate(
        incident_id=incident_id,
        organization_id=organization_id,
        action_type=action_type,
        action_details=action_details,
        investigation_result=investigation_result,
        target_service=target_service,
        target_environment=target_environment,
    )
