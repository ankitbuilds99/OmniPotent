"""Fail-closed production pilot release gate."""

from __future__ import annotations

from dataclasses import dataclass
from typing import FrozenSet

from app.artifact_signing import verify_artifact_signatures


@dataclass(frozen=True)
class ReleaseEvidence:
    outstanding_p0: int
    critical_safety_violations: int
    audit_integrity_verified: bool
    restore_drill_verified: bool
    reviewer_signatures: int
    incident_fallback_documented: bool
    required_artifacts: FrozenSet[str] = frozenset()
    verified_artifacts: FrozenSet[str] = frozenset()
    artifact_digests_valid: bool = True
    artifact_signatures_valid: bool = False
    artifact_digests: tuple[tuple[str, str], ...] = ()
    artifact_signatures: tuple[tuple[str, str], ...] = ()
    artifact_signing_public_key: str = ""
    benchmark_corpus_ready: bool = True


@dataclass(frozen=True)
class ReleaseDecision:
    allowed: bool
    reasons: tuple[str, ...]


def evaluate_release(evidence: ReleaseEvidence, *, required_signatures: int = 2) -> ReleaseDecision:
    reasons: list[str] = []
    if evidence.outstanding_p0:
        reasons.append("outstanding_p0_requirements")
    if evidence.critical_safety_violations:
        reasons.append("critical_safety_violations")
    if not evidence.audit_integrity_verified:
        reasons.append("audit_integrity_unverified")
    if not evidence.restore_drill_verified:
        reasons.append("restore_drill_unverified")
    if evidence.reviewer_signatures < required_signatures:
        reasons.append("insufficient_reviewer_signatures")
    if not evidence.incident_fallback_documented:
        reasons.append("incident_fallback_missing")
    if not evidence.benchmark_corpus_ready:
        reasons.append("benchmark_corpus_not_release_ready")
    missing_artifacts = evidence.required_artifacts - evidence.verified_artifacts
    if missing_artifacts:
        reasons.append("required_release_artifacts_missing")
    if not evidence.artifact_digests_valid:
        reasons.append("release_artifact_digest_invalid")
    # Signature verification is evaluated once the artifact set is complete;
    # incomplete coverage gets the more useful missing-artifacts reason above.
    signatures_valid = evidence.artifact_signatures_valid
    if evidence.artifact_digests and evidence.artifact_signatures and evidence.artifact_signing_public_key:
        signatures_valid = verify_artifact_signatures(
            dict(evidence.artifact_digests), dict(evidence.artifact_signatures),
            evidence.artifact_signing_public_key,
        )
    if (evidence.required_artifacts
            and evidence.required_artifacts <= evidence.verified_artifacts
            and not signatures_valid):
        reasons.append("release_artifact_signature_invalid")
    return ReleaseDecision(not reasons, tuple(reasons))
