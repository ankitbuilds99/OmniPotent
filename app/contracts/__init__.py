"""Canonical, versioned OmniPotent V2 domain contracts.

This package defines interfaces only. Existing APIs and production execution
are deliberately not wired to these models in this change.
"""

from app.contracts.action import (
    ActionContract,
    ActionTarget,
    BlastRadiusAssessment,
    ExpectedOutcome,
    Precondition,
    RollbackPlan,
    VerificationPredicate,
)
from app.contracts.common import (
    ActionLifecycleState,
    ApprovalRequirement,
    Environment,
    ExecutionStrategy,
    RiskLevel,
    TrustDecisionKind,
    TrustReasonCode,
    VerificationOutcome,
)
from app.contracts.evidence import EvidenceDomain, EvidenceItem, EvidenceReference, EvidenceTrustLevel
from app.contracts.hypothesis import Hypothesis
from app.contracts.receipt import ExecutionReceipt
from app.contracts.root_cause import RootCauseAssessment, RootCauseStatus
from app.contracts.trust import TrustDecision
from app.contracts.verification import ExecutionResult, PredicateResult, VerificationResult

__all__ = [
    "ActionContract", "ActionLifecycleState", "ActionTarget", "ApprovalRequirement",
    "BlastRadiusAssessment", "Environment", "EvidenceDomain", "EvidenceItem", "EvidenceReference", "EvidenceTrustLevel",
    "ExecutionReceipt", "ExecutionResult", "ExecutionStrategy", "ExpectedOutcome",
    "Hypothesis", "Precondition", "PredicateResult", "RiskLevel", "RollbackPlan",
    "RootCauseAssessment", "RootCauseStatus", "TrustDecision", "TrustDecisionKind",
    "TrustReasonCode", "VerificationOutcome", "VerificationPredicate", "VerificationResult",
]
