"""Fail-closed validation for deployment image/source identity."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40,64}$")


@dataclass(frozen=True)
class DeploymentIdentity:
    service: str
    image_digest: str
    commit_sha: str
    source: str
    verified: bool
    reasons: tuple[str, ...] = ()


def validate_deployment_identity(
    evidence: dict[str, Any],
    *,
    expected_service: str | None = None,
    expected_digest: str | None = None,
    expected_commit: str | None = None,
) -> DeploymentIdentity:
    """Validate an observed deployment identity without inferring missing data.

    ``image`` may be a full OCI reference ending in ``@sha256:...`` or a bare
    digest. Tags (including ``:latest``) are deliberately not accepted as
    immutable deployment identity.
    """
    service = str(evidence.get("service") or "").strip()
    image = str(evidence.get("image") or evidence.get("image_ref") or "").strip()
    digest = image.rsplit("@", 1)[-1] if "@" in image else image
    commit = str(evidence.get("commit_sha") or evidence.get("git_sha") or "").lower().strip()
    source = str(evidence.get("source") or "").strip()
    reasons: list[str] = []
    if not service:
        reasons.append("service_missing")
    if not _DIGEST.fullmatch(digest):
        reasons.append("immutable_image_digest_missing")
    if not _COMMIT.fullmatch(commit):
        reasons.append("commit_sha_invalid")
    if not source:
        reasons.append("identity_source_missing")
    if expected_service and service != expected_service:
        reasons.append("service_mismatch")
    if expected_digest and digest != expected_digest:
        reasons.append("image_digest_mismatch")
    if expected_commit and commit != expected_commit.lower():
        reasons.append("commit_sha_mismatch")
    return DeploymentIdentity(
        service=service,
        image_digest=digest,
        commit_sha=commit,
        source=source,
        verified=not reasons,
        reasons=tuple(reasons),
    )

