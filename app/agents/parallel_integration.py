"""Integration layer between parallel investigation and safety gates.

This module connects the Architecture 3 components:
- ParallelInvestigationCoordinator (4 specialists in parallel)
- SafetyGateOrchestrator (3-layer safety gates)
- Existing OrchestratorAgent (for handoffs and routing)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from app.agents.parallel_coordinator import (
    ParallelInvestigationCoordinator,
    ParallelInvestigationResult,
)
from app.agents.protocol import SharedContext
from app.safety_gates.orchestrator import SafetyGateOrchestrator
from app.safety_gates.models import SafetyDecision, SafetyEvaluationResult

log = logging.getLogger("devcopilot.agents.parallel_integration")


@dataclass
class Arch3Config:
    """Configuration for Architecture 3 investigation mode."""
    # Parallel investigation
    parallel_timeout_seconds: float = 180.0
    min_confidence_threshold: float = 0.8
    model: str = "claude-sonnet-4-20250514"

    # Safety gates
    enable_sandbox_gate: bool = True
    sandbox_environment: str = "local"  # local | kubernetes

    # Autonomy
    auto_apply_safe_fixes: bool = False
    max_auto_apply_severity: str = "low"  # low | medium | high


@dataclass
class Arch3Result:
    """Result from Architecture 3 parallel investigation."""
    result_id: UUID
    incident_summary: str
    root_cause: str | None
    confidence: float
    severity: str

    # Parallel investigation results
    investigation: ParallelInvestigationResult
    specialist_count: int
    consensus_reached: bool

    # Safety evaluation (if action proposed)
    safety_evaluation: SafetyEvaluationResult | None = None
    action_approved: bool = False
    requires_human_approval: bool = False

    # Performance
    total_duration_ms: int = 0
    parallel_savings_ms: int = 0  # Estimated time saved vs sequential

    def to_dict(self) -> dict[str, Any]:
        return {
            "result_id": str(self.result_id),
            "incident_summary": self.incident_summary,
            "root_cause": self.root_cause,
            "confidence": self.confidence,
            "severity": self.severity,
            "investigation": self.investigation.to_dict(),
            "specialist_count": self.specialist_count,
            "consensus_reached": self.consensus_reached,
            "safety_evaluation": self.safety_evaluation.to_dict() if self.safety_evaluation else None,
            "action_approved": self.action_approved,
            "requires_human_approval": self.requires_human_approval,
            "total_duration_ms": self.total_duration_ms,
            "parallel_savings_ms": self.parallel_savings_ms,
        }


class Architecture3Coordinator:
    """Coordinates Architecture 3 (Omnipotent) investigation flow.

    This is the main entry point for parallel investigation with safety gates.
    It orchestrates:
    1. Parallel investigation across 4 specialists
    2. Result synthesis and consensus building
    3. Safety gate evaluation for proposed actions
    4. Human-in-the-loop for risky actions
    """

    def __init__(self, config: Arch3Config | None = None):
        """Initialize the Architecture 3 coordinator.

        Args:
            config: Architecture 3 configuration
        """
        self.config = config or Arch3Config()

        # Initialize components
        self._parallel = ParallelInvestigationCoordinator(
            model=self.config.model,
        )

        self._safety = SafetyGateOrchestrator(
            confidence_threshold=self.config.min_confidence_threshold,
            sandbox_enabled=self.config.enable_sandbox_gate,
            sandbox_environment=self.config.sandbox_environment,
        )

        log.info(
            "Architecture3Coordinator initialized: "
            "model=%s, safety_gates=%s, auto_apply=%s",
            self.config.model,
            self.config.enable_sandbox_gate,
            self.config.auto_apply_safe_fixes,
        )

    async def investigate(
        self,
        incident: dict[str, Any],
        context: SharedContext | None = None,
    ) -> Arch3Result:
        """Run a parallel investigation for an incident.

        Args:
            incident: Incident details (from PagerDuty, alert, etc.)
            context: Optional shared context from prior investigation

        Returns:
            Arch3Result with findings and recommendations
        """
        start_time = datetime.now(timezone.utc)
        result_id = uuid4()

        log.info("Starting Arch3 investigation: id=%s incident=%s", result_id, incident.get("id"))

        # Phase 1: Parallel Investigation
        investigation = await self._parallel.investigate(
            incident=incident,
            timeout_seconds=self.config.parallel_timeout_seconds,
        )

        # Estimate time saved vs sequential execution
        sequential_time_ms = sum(
            sr.duration_ms for sr in investigation.specialists
        )
        parallel_savings_ms = max(0, sequential_time_ms - investigation.total_duration_ms)

        # Build consensus assessment
        consensus_reached = investigation.consensus_confidence >= self.config.min_confidence_threshold

        # Phase 2: Safety Evaluation (if action proposed)
        safety_evaluation = None
        action_approved = False
        requires_human = False

        proposed_action = self._extract_proposed_action(investigation)
        if proposed_action:
            safety_evaluation = await self._evaluate_safety(
                action=proposed_action,
                investigation=investigation,
            )

            if safety_evaluation.final_decision == SafetyDecision.AUTO_APPROVED:
                action_approved = True
                if self.config.auto_apply_safe_fixes:
                    await self._apply_action(proposed_action, investigation)
            elif safety_evaluation.final_decision == SafetyDecision.ESCALATED:
                requires_human = True
            # BLOCKED = action_approved stays False

        duration_ms = int((datetime.now(timezone.utc) - start_time).total_seconds() * 1000)

        return Arch3Result(
            result_id=result_id,
            incident_summary=incident.get("title", incident.get("summary", "Unknown incident")),
            root_cause=investigation.root_cause,
            confidence=investigation.confidence,
            severity=investigation.severity,
            investigation=investigation,
            specialist_count=len(investigation.specialists),
            consensus_reached=consensus_reached,
            safety_evaluation=safety_evaluation,
            action_approved=action_approved,
            requires_human_approval=requires_human,
            total_duration_ms=duration_ms,
            parallel_savings_ms=parallel_savings_ms,
        )

    def _extract_proposed_action(
        self,
        investigation: ParallelInvestigationResult,
    ) -> dict[str, Any] | None:
        """Extract a proposed remediation action from investigation results.

        Looks for high-confidence fix recommendations.
        """
        if not investigation.recommended_actions:
            return None

        for action in investigation.recommended_actions:
            # Only propose actions with sufficient confidence
            if action.get("confidence", 0) >= self.config.min_confidence_threshold:
                return action

        return None

    async def _evaluate_safety(
        self,
        action: dict[str, Any],
        investigation: ParallelInvestigationResult,
    ) -> SafetyEvaluationResult:
        """Evaluate an action through the 3-layer safety gate system."""
        return await self._safety.evaluate(
            action_type=action.get("type", "unknown"),
            action_details=action,
            investigation_context={
                "root_cause": investigation.root_cause,
                "confidence": investigation.confidence,
                "evidence_count": len(investigation.evidence),
                "specialist_count": len(investigation.specialists),
                "consensus_confidence": investigation.consensus_confidence,
            },
        )

    async def _apply_action(
        self,
        action: dict[str, Any],
        investigation: ParallelInvestigationResult,
    ) -> bool:
        """Apply an auto-approved action.

        Only called when:
        1. Safety gates passed (AUTO_APPROVED)
        2. auto_apply_safe_fixes is enabled
        3. Action severity <= max_auto_apply_severity
        """
        severity = action.get("severity", "high")
        severity_order = {"low": 0, "medium": 1, "high": 2}

        max_severity = self.config.max_auto_apply_severity
        if severity_order.get(severity, 2) > severity_order.get(max_severity, 0):
            log.info("Action severity %s exceeds auto-apply max %s, skipping", severity, max_severity)
            return False

        log.info("Auto-applying action: %s", action.get("type"))
        # In production: execute the action via appropriate MCP tools
        return True

    async def continue_after_approval(
        self,
        result: Arch3Result,
        approved: bool,
        reason: str = "",
    ) -> dict[str, Any]:
        """Continue investigation after human approval decision.

        Args:
            result: The investigation result awaiting approval
            approved: Whether the human approved
            reason: Optional reason for the decision

        Returns:
            Execution result
        """
        if not result.requires_human_approval:
            return {"status": "error", "message": "No approval was required"}

        if not approved:
            log.info("Human rejected action: %s", reason)
            return {
                "status": "rejected",
                "message": f"Action rejected by human: {reason or 'No reason provided'}",
            }

        # Execute the approved action
        proposed_action = self._extract_proposed_action(result.investigation)
        if proposed_action:
            success = await self._apply_action(proposed_action, result.investigation)
            return {
                "status": "executed" if success else "failed",
                "action": proposed_action,
            }

        return {"status": "error", "message": "No action to execute"}


async def run_arch3_investigation(
    incident: dict[str, Any],
    config: Arch3Config | None = None,
) -> Arch3Result:
    """Convenience function to run a parallel investigation.

    This is the main entry point for Architecture 3 investigations.

    Args:
        incident: Incident details
        config: Optional configuration

    Returns:
        Investigation result with safety evaluation
    """
    coordinator = Architecture3Coordinator(config=config)
    return await coordinator.investigate(incident)
