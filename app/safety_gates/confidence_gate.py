"""Confidence Gate - Layer 1 of 3-Layer Safety Gates.

The explicit 0.8 threshold that Architecture 3 requires:
- Score >= 0.8 → Pass (allow auto-remediation)
- Score < 0.8 → Escalate (require human approval)

Transparent confidence scoring with factors breakdown.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from app.config import get_settings
from app.safety_gates.models import GateResult

log = logging.getLogger("devcopilot.safety_gates.confidence")


def _get_default_threshold() -> float:
    """Get the default confidence threshold from config."""
    return get_settings().safety_confidence_threshold


def _get_action_thresholds() -> dict[str, float]:
    """Get action-specific thresholds from config."""
    return get_settings().get_action_confidence_thresholds()


@dataclass
class ConfidenceFactor:
    """A factor contributing to the confidence score."""
    name: str
    score: float
    weight: float
    explanation: str


class ConfidenceGate:
    """Gate 1: Evaluates AI confidence in the proposed action.

    The 0.8 threshold is explicit and configurable per org/action type.
    Shows user exactly why confidence is what it is.
    """

    def __init__(
        self,
        default_threshold: float | None = None,
        org_thresholds: dict[str, float] | None = None,
        action_thresholds: dict[str, float] | None = None,
    ):
        """Initialize the confidence gate.

        Args:
            default_threshold: Default threshold (0.8 for Architecture 3)
            org_thresholds: Organization-specific threshold overrides
            action_thresholds: Action-specific thresholds
        """
        self.default_threshold = default_threshold or _get_default_threshold()
        self.org_thresholds = org_thresholds or {}
        self.action_thresholds = action_thresholds or _get_action_thresholds()

    def evaluate(
        self,
        action_type: str,
        action_details: dict[str, Any],
        investigation_result: dict[str, Any],
        organization_id: str | None = None,
    ) -> GateResult:
        """Evaluate confidence for a proposed action.

        Args:
            action_type: Type of action (rollback, scale, restart, etc.)
            action_details: Specific action parameters
            investigation_result: Result from parallel investigation
            organization_id: For org-specific thresholds

        Returns:
            GateResult with pass/fail and transparent reasoning
        """
        start_time = time.time()

        # Get threshold for this org/action
        threshold = self._get_threshold(organization_id, action_type)

        # Calculate confidence score with factors
        confidence_score, factors = self._calculate_confidence(
            investigation_result,
            action_type,
            action_details,
        )

        passed = confidence_score >= threshold

        # Build human-readable reasoning
        reasoning = self._build_reasoning(confidence_score, threshold, factors, passed)

        duration_ms = int((time.time() - start_time) * 1000)

        log.info(
            "Confidence gate: score=%.2f threshold=%.2f passed=%s action=%s",
            confidence_score, threshold, passed, action_type,
        )

        violations = []
        if not passed:
            violations.append({
                "type": "low_confidence",
                "score": confidence_score,
                "threshold": threshold,
                "message": f"Confidence {confidence_score:.0%} below threshold {threshold:.0%}",
                "action": "escalate",
            })

        return GateResult(
            gate_name="confidence",
            passed=passed,
            score=confidence_score,
            threshold=threshold,
            reasoning=reasoning,
            violations=violations,
            duration_ms=duration_ms,
        )

    def _get_threshold(
        self,
        organization_id: str | None,
        action_type: str,
    ) -> float:
        """Get confidence threshold for org/action type."""
        # Check org-specific override first
        if organization_id and organization_id in self.org_thresholds:
            return self.org_thresholds[organization_id]

        # Then action-specific threshold
        return self.action_thresholds.get(action_type, self.default_threshold)

    def _calculate_confidence(
        self,
        investigation_result: dict[str, Any],
        action_type: str,
        action_details: dict[str, Any],
    ) -> tuple[float, list[ConfidenceFactor]]:
        """Calculate overall confidence score with transparent factors.

        Factors:
        1. Investigation confidence (from parallel agents)
        2. Evidence quality (how many agents found corroborating evidence)
        3. Action-hypothesis alignment (does action address root cause?)
        4. Historical success rate (for similar actions)
        """
        factors = []

        # Factor 1: Investigation confidence (weight 40%)
        investigation_confidence = investigation_result.get("confidence_score", 0.5)
        factors.append(ConfidenceFactor(
            name="Investigation Confidence",
            score=investigation_confidence,
            weight=0.40,
            explanation=f"Parallel agents returned {investigation_confidence:.0%} confidence in root cause",
        ))

        # Factor 2: Evidence quality - agent agreement (weight 25%)
        specialist_results = investigation_result.get("specialist_results", {})
        agreeing_agents = sum(
            1 for r in specialist_results.values()
            if isinstance(r, dict) and r.get("confidence_score", 0) > 0.6
        )
        evidence_score = min(agreeing_agents / 4.0, 1.0)
        factors.append(ConfidenceFactor(
            name="Evidence Corroboration",
            score=evidence_score,
            weight=0.25,
            explanation=f"{agreeing_agents}/4 specialists found supporting evidence",
        ))

        # Factor 3: Action-hypothesis alignment (weight 25%)
        root_cause = investigation_result.get("root_cause_hypothesis", "")
        alignment_score = self._calculate_alignment(action_type, action_details, root_cause)
        factors.append(ConfidenceFactor(
            name="Action Alignment",
            score=alignment_score,
            weight=0.25,
            explanation=f"Action '{action_type}' alignment with root cause: {alignment_score:.0%}",
        ))

        # Factor 4: Historical success rate (weight 10%)
        historical_score = self._get_historical_success_rate(action_type, action_details)
        factors.append(ConfidenceFactor(
            name="Historical Success",
            score=historical_score,
            weight=0.10,
            explanation=f"Similar actions have {historical_score:.0%} success rate historically",
        ))

        # Weighted average
        total_confidence = sum(f.score * f.weight for f in factors)

        return total_confidence, factors

    def _calculate_alignment(
        self,
        action_type: str,
        action_details: dict[str, Any],
        root_cause: str,
    ) -> float:
        """Check if proposed action addresses the root cause."""
        if not root_cause:
            return 0.5  # Neutral if no root cause

        root_cause_lower = root_cause.lower()

        # Keywords that suggest action alignment
        alignments = {
            "rollback": ["deployment", "deploy", "code change", "commit", "release", "update"],
            "scale_up": ["capacity", "load", "traffic", "resource", "memory", "cpu", "oom"],
            "scale_down": ["over-provisioned", "cost", "unused", "idle"],
            "restart": ["stuck", "deadlock", "memory leak", "state", "corrupted", "hung"],
            "deploy_fix": ["bug", "error", "exception", "code", "fix", "patch"],
        }

        keywords = alignments.get(action_type, [])
        matches = sum(1 for k in keywords if k in root_cause_lower)

        if matches > 0:
            return min(0.5 + (matches * 0.15), 1.0)

        return 0.5  # Neutral if no match

    def _get_historical_success_rate(
        self,
        action_type: str,
        action_details: dict[str, Any],
    ) -> float:
        """Get historical success rate for similar actions.

        In production, this would query past remediations.
        For now, use reasonable defaults.
        """
        # Default success rates by action type
        success_rates = {
            "rollback": 0.85,  # Rollbacks usually work
            "scale_up": 0.90,  # Scaling up rarely fails
            "scale_down": 0.70,  # Can cause issues
            "restart": 0.75,  # Sometimes needs multiple restarts
            "deploy_fix": 0.65,  # Fixes don't always work first try
            "create_pr": 0.95,  # PRs rarely fail to create
        }

        return success_rates.get(action_type, 0.70)

    def _build_reasoning(
        self,
        score: float,
        threshold: float,
        factors: list[ConfidenceFactor],
        passed: bool,
    ) -> str:
        """Build human-readable reasoning with factor breakdown."""
        lines = [
            f"**Confidence Score: {score:.0%}** (threshold: {threshold:.0%})",
            "",
            "**Contributing Factors:**",
        ]

        # Sort by weight (highest first)
        for f in sorted(factors, key=lambda x: x.weight, reverse=True):
            icon = "✅" if f.score >= 0.7 else "⚠️" if f.score >= 0.5 else "❌"
            lines.append(f"  {icon} {f.name}: {f.score:.0%} (weight: {f.weight:.0%})")
            lines.append(f"     {f.explanation}")

        if not passed:
            lines.append("")
            lines.append("**⚠️ ESCALATION REQUIRED**")
            lines.append(f"Confidence {score:.0%} is below the {threshold:.0%} threshold.")
            lines.append("Human review required before proceeding.")

        return "\n".join(lines)
