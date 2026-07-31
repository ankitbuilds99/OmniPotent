"""Data models for the 3-layer safety gate system."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID


class SafetyDecision(str, Enum):
    """Final decision from the safety gate system."""
    AUTO_APPROVED = "auto_approved"
    ESCALATED = "escalated"
    BLOCKED = "blocked"


@dataclass
class GateResult:
    """Result from a single safety gate."""
    gate_name: str
    passed: bool
    score: float | None = None
    threshold: float | None = None
    reasoning: str = ""
    violations: list[dict[str, Any]] = field(default_factory=list)
    duration_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate_name": self.gate_name,
            "passed": self.passed,
            "score": self.score,
            "threshold": self.threshold,
            "reasoning": self.reasoning,
            "violations": self.violations,
            "duration_ms": self.duration_ms,
        }


@dataclass
class SafetyEvaluationResult:
    """Combined result from all 3 safety gates."""
    evaluation_id: UUID
    action_type: str
    action_details: dict[str, Any]

    # Individual gate results
    confidence_gate: GateResult
    policy_gate: GateResult
    sandbox_gate: GateResult

    # Overall decision
    all_gates_passed: bool
    final_decision: SafetyDecision
    escalation_reason: str | None = None

    # Human approval context
    requires_human_approval: bool = False
    approval_context: dict[str, Any] = field(default_factory=dict)

    # Timestamps
    evaluated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "evaluation_id": str(self.evaluation_id),
            "action_type": self.action_type,
            "action_details": self.action_details,
            "confidence_gate": self.confidence_gate.to_dict(),
            "policy_gate": self.policy_gate.to_dict(),
            "sandbox_gate": self.sandbox_gate.to_dict(),
            "all_gates_passed": self.all_gates_passed,
            "final_decision": self.final_decision.value,
            "escalation_reason": self.escalation_reason,
            "requires_human_approval": self.requires_human_approval,
            "approval_context": self.approval_context,
            "evaluated_at": self.evaluated_at.isoformat(),
        }
