"""Validation boundary for a separately deployed customer-side action runner."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol


@dataclass(frozen=True)
class RunnerCapability:
    token_id: str
    tenant_id: str
    cluster: str
    namespace: str
    resource_kind: str
    resource_name: str
    action: str
    expires_at: datetime
    action_digest: str


@dataclass(frozen=True)
class RunnerRequest:
    tenant_id: str
    cluster: str
    namespace: str
    resource_kind: str
    resource_name: str
    action: str
    action_digest: str


class RunnerExecutor(Protocol):
    """Customer-side executor injected by the deployment, never by the control plane."""

    def execute(self, request: RunnerRequest) -> dict: ...


def validate_capability(capability: RunnerCapability, request: RunnerRequest) -> tuple[bool, str]:
    """Fail closed before customer-side execution."""
    now = datetime.now(timezone.utc)
    if capability.expires_at <= now:
        return False, "capability_expired"
    for field in ("tenant_id", "cluster", "namespace", "resource_kind", "resource_name", "action", "action_digest"):
        if getattr(capability, field) != getattr(request, field):
            return False, f"{field}_mismatch"
    return True, "allowed"


def capability_from_payload(payload: dict) -> RunnerCapability:
    """Parse an untrusted wire payload without silently widening its scope."""
    expires_at = payload.get("expires_at")
    if not isinstance(expires_at, str):
        raise ValueError("expires_at must be an RFC3339 string")
    parsed = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("expires_at must include a timezone")
    fields = {
        "token_id", "tenant_id", "cluster", "namespace", "resource_kind",
        "resource_name", "action", "action_digest",
    }
    if set(payload) - fields - {"expires_at"}:
        raise ValueError("unknown capability fields")
    missing = fields - set(payload)
    if missing:
        raise ValueError(f"missing capability fields: {sorted(missing)}")
    if any(not isinstance(payload[field], str) or not payload[field] for field in fields):
        raise ValueError("capability scope fields must be non-empty strings")
    return RunnerCapability(expires_at=parsed, **{field: payload[field] for field in fields})
