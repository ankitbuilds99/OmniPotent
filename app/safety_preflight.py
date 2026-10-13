"""Deterministic change-risk preflight for typed infrastructure actions."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ResourceSnapshot:
    uid: str
    resource_version: str
    namespace: str
    name: str
    kind: str
    controller: str | None = None
    current_revision: int | None = None
    available_revisions: tuple[int, ...] = ()
    schema_compatible: bool = True


@dataclass(frozen=True)
class ActionIntent:
    uid: str
    resource_version: str
    namespace: str
    name: str
    kind: str
    target_revision: int
    expected_controller: str | None = None


@dataclass(frozen=True)
class PreflightResult:
    allowed: bool
    reasons: tuple[str, ...] = field(default_factory=tuple)


def validate_rollback_preflight(intent: ActionIntent, current: ResourceSnapshot) -> PreflightResult:
    """Reject stale, out-of-scope, controller-conflicting or incompatible writes."""
    reasons: list[str] = []
    if intent.uid != current.uid:
        reasons.append("resource_uid_changed")
    if intent.resource_version != current.resource_version:
        reasons.append("resource_version_changed")
    if (intent.namespace, intent.name, intent.kind) != (current.namespace, current.name, current.kind):
        reasons.append("target_scope_changed")
    if intent.expected_controller and current.controller != intent.expected_controller:
        reasons.append("controller_ownership_changed")
    if intent.target_revision not in current.available_revisions:
        reasons.append("target_revision_unavailable")
    if not current.schema_compatible:
        reasons.append("schema_incompatible")
    return PreflightResult(not reasons, tuple(reasons))
