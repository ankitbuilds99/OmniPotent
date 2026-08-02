"""Verification Agent for AI Safety.

Ensures AI-generated analysis and actions are:
1. Grounded in real evidence
2. Free from hallucinations
3. Properly calibrated confidence
4. Safe to execute
"""

from app.verification.confidence_calibrator import (
    CalibrationBin,
    ConfidenceCalibrator,
)
from app.verification.consistency_checker import (
    ActionSafetyCheck,
    DataConsistencyChecker,
)
from app.verification.evidence_collector import (
    EvidenceCollector,
    EvidenceSource,
)
from app.verification.hallucination_detector import (
    HallucinationDetector,
    HallucinationType,
)
from app.verification.verification_agent import (
    Claim,
    Evidence,
    VerificationAgent,
    VerificationResult,
    VerificationStatus,
)

__all__ = [
    "VerificationAgent",
    "Claim",
    "Evidence",
    "VerificationResult",
    "VerificationStatus",
    "HallucinationDetector",
    "HallucinationType",
    "ConfidenceCalibrator",
    "CalibrationBin",
    "DataConsistencyChecker",
    "ActionSafetyCheck",
    "EvidenceCollector",
    "EvidenceSource",
]
