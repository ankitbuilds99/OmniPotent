"""Automated secret rotation with zero-downtime.

Provides:
- Scheduled rotation for API keys and secrets
- Dual-key grace periods
- Rotation audit trail
- Integration with external secret managers
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Awaitable, Callable

log = logging.getLogger("omnipotent.security.secret_rotation")


class RotationStatus(Enum):
    """Status of a rotation operation."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    GRACE_PERIOD = "grace_period"
    COMPLETED = "completed"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


@dataclass
class SecretMetadata:
    """Metadata for a managed secret."""

    name: str
    created_at: datetime
    expires_at: datetime | None
    last_rotated_at: datetime | None
    rotation_interval_days: int
    version: int = 1
    status: RotationStatus = RotationStatus.COMPLETED
    previous_version: int | None = None
    grace_period_ends: datetime | None = None


@dataclass
class RotationEvent:
    """Record of a rotation event."""

    secret_name: str
    timestamp: datetime
    old_version: int
    new_version: int
    status: RotationStatus
    initiated_by: str
    error: str = ""


class SecretRotationManager:
    """Manages automatic secret rotation with zero-downtime."""

    def __init__(
        self,
        default_rotation_days: int = 90,
        default_grace_period_hours: int = 24,
    ):
        self.default_rotation_days = default_rotation_days
        self.default_grace_period_hours = default_grace_period_hours

        self._secrets: dict[str, tuple[str, SecretMetadata]] = {}
        self._previous_secrets: dict[str, str] = {}
        self._events: list[RotationEvent] = []
        self._on_rotate: list[Callable[[str, str, str], Awaitable[None]]] = []
        self._on_expire: list[Callable[[str], Awaitable[None]]] = []

    def register_secret(
        self,
        name: str,
        initial_value: str,
        rotation_interval_days: int | None = None,
    ) -> SecretMetadata:
        """Register a secret for managed rotation."""
        now = datetime.now(timezone.utc)
        metadata = SecretMetadata(
            name=name,
            created_at=now,
            expires_at=now + timedelta(days=rotation_interval_days or self.default_rotation_days),
            last_rotated_at=None,
            rotation_interval_days=rotation_interval_days or self.default_rotation_days,
        )
        self._secrets[name] = (initial_value, metadata)
        log.info(
            "Registered secret for rotation: %s (interval: %d days)",
            name,
            metadata.rotation_interval_days,
        )
        return metadata

    def get_current(self, name: str) -> str | None:
        """Get the current value of a secret."""
        if name in self._secrets:
            return self._secrets[name][0]
        return None

    def validate(self, name: str, value: str) -> bool:
        """Validate a secret value (current or previous during grace period)."""
        if name not in self._secrets:
            return False

        current, metadata = self._secrets[name]

        if secrets.compare_digest(value, current):
            return True

        if metadata.status == RotationStatus.GRACE_PERIOD:
            if name in self._previous_secrets:
                if secrets.compare_digest(value, self._previous_secrets[name]):
                    return True

        return False

    async def rotate(
        self,
        name: str,
        new_value: str | None = None,
        initiated_by: str = "system",
    ) -> tuple[bool, str]:
        """Rotate a secret to a new value."""
        if name not in self._secrets:
            return False, f"Secret not found: {name}"

        current_value, metadata = self._secrets[name]

        if new_value is None:
            new_value = secrets.token_urlsafe(32)

        now = datetime.now(timezone.utc)
        new_version = metadata.version + 1

        self._previous_secrets[name] = current_value

        metadata.previous_version = metadata.version
        metadata.version = new_version
        metadata.last_rotated_at = now
        metadata.expires_at = now + timedelta(days=metadata.rotation_interval_days)
        metadata.status = RotationStatus.GRACE_PERIOD
        metadata.grace_period_ends = now + timedelta(hours=self.default_grace_period_hours)

        self._secrets[name] = (new_value, metadata)

        event = RotationEvent(
            secret_name=name,
            timestamp=now,
            old_version=metadata.previous_version,
            new_version=new_version,
            status=RotationStatus.GRACE_PERIOD,
            initiated_by=initiated_by,
        )
        self._events.append(event)

        log.info(
            "Rotated secret %s: v%d -> v%d (grace period until %s)",
            name,
            metadata.previous_version,
            new_version,
            metadata.grace_period_ends,
        )

        for callback in self._on_rotate:
            try:
                await callback(name, current_value, new_value)
            except Exception as e:
                log.error("Rotation callback failed for %s: %s", name, e)

        return True, new_value

    async def end_grace_period(self, name: str) -> bool:
        """End the grace period and invalidate the old secret."""
        if name not in self._secrets:
            return False

        _, metadata = self._secrets[name]

        if metadata.status != RotationStatus.GRACE_PERIOD:
            return False

        self._previous_secrets.pop(name, None)

        metadata.status = RotationStatus.COMPLETED
        metadata.previous_version = None
        metadata.grace_period_ends = None

        log.info("Grace period ended for secret %s", name)
        return True

    async def rollback(self, name: str, reason: str = "") -> bool:
        """Rollback to the previous secret value."""
        if name not in self._secrets:
            return False

        if name not in self._previous_secrets:
            log.warning("Cannot rollback %s: no previous value", name)
            return False

        _, metadata = self._secrets[name]
        previous_value = self._previous_secrets[name]

        metadata.version = metadata.previous_version or metadata.version - 1
        metadata.status = RotationStatus.ROLLED_BACK
        metadata.previous_version = None

        self._secrets[name] = (previous_value, metadata)
        del self._previous_secrets[name]

        event = RotationEvent(
            secret_name=name,
            timestamp=datetime.now(timezone.utc),
            old_version=metadata.version + 1,
            new_version=metadata.version,
            status=RotationStatus.ROLLED_BACK,
            initiated_by="system",
            error=reason,
        )
        self._events.append(event)

        log.warning("Rolled back secret %s: %s", name, reason)
        return True

    def needs_rotation(self, name: str) -> bool:
        """Check if a secret needs rotation."""
        if name not in self._secrets:
            return False

        _, metadata = self._secrets[name]

        if metadata.expires_at is None:
            return False

        return datetime.now(timezone.utc) >= metadata.expires_at

    def get_expiring_soon(self, within_days: int = 7) -> list[str]:
        """Get secrets expiring within the specified number of days."""
        threshold = datetime.now(timezone.utc) + timedelta(days=within_days)

        expiring = []
        for name, (_, metadata) in self._secrets.items():
            if metadata.expires_at and metadata.expires_at <= threshold:
                expiring.append(name)

        return expiring

    async def check_and_rotate(self) -> list[str]:
        """Check all secrets and rotate those that need it."""
        rotated = []

        for name in list(self._secrets.keys()):
            _, metadata = self._secrets[name]
            if metadata.status == RotationStatus.GRACE_PERIOD:
                if (
                    metadata.grace_period_ends
                    and datetime.now(timezone.utc) >= metadata.grace_period_ends
                ):
                    await self.end_grace_period(name)

            if self.needs_rotation(name):
                success, _ = await self.rotate(name)
                if success:
                    rotated.append(name)

        return rotated

    def on_rotate(
        self,
        callback: Callable[[str, str, str], Awaitable[None]],
    ) -> None:
        """Register a callback for rotation events."""
        self._on_rotate.append(callback)

    def get_events(
        self,
        name: str | None = None,
        limit: int = 100,
    ) -> list[RotationEvent]:
        """Get rotation history."""
        events = self._events
        if name:
            events = [e for e in events if e.secret_name == name]
        return events[-limit:]


_manager: SecretRotationManager | None = None


def get_rotation_manager() -> SecretRotationManager:
    """Get the global secret rotation manager."""
    global _manager
    if _manager is None:
        _manager = SecretRotationManager()
    return _manager
