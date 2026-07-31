"""3-Layer Safety Gates - Architecture 3 (Omnipotent) Implementation.

What NEITHER incident.io nor Resolve AI has:
1. CONFIDENCE GATE - Explicit 0.8 threshold with transparent scoring
2. POLICY GATE - Org rules, blast radius, injection detection
3. SANDBOX GATE - Test fix in isolation before approval

All gates pass → Auto-remediate
Any gate fails → Escalate to human with full context
"""

from app.safety_gates.confidence_gate import ConfidenceGate
from app.safety_gates.policy_gate import PolicyGate
from app.safety_gates.sandbox_gate import SandboxGate
from app.safety_gates.orchestrator import SafetyGateOrchestrator
from app.safety_gates.models import (
    GateResult,
    SafetyDecision,
    SafetyEvaluationResult,
)

__all__ = [
    "ConfidenceGate",
    "PolicyGate",
    "SandboxGate",
    "SafetyGateOrchestrator",
    "GateResult",
    "SafetyDecision",
    "SafetyEvaluationResult",
]
