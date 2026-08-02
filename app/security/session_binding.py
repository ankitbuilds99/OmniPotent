"""Session binding with device fingerprinting.

Binds user sessions to device fingerprints to prevent session hijacking.
Sessions are invalidated if accessed from a different device.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("omnipotent.security.session_binding")


class BindingStrength(Enum):
    """How strictly to enforce device binding."""

    STRICT = "strict"  # Exact match required
    MODERATE = "moderate"  # Allow minor variations
    LOOSE = "loose"  # Only major changes trigger re-auth


@dataclass
class DeviceFingerprint:
    """Device fingerprint components."""

    user_agent: str
    accept_language: str
    screen_resolution: str | None = None
    timezone: str | None = None
    platform: str | None = None
    color_depth: int | None = None
    touch_support: bool | None = None
    webgl_vendor: str | None = None
    cpu_class: str | None = None

    def compute_hash(self, secret: str) -> str:
        """Compute HMAC hash of fingerprint components."""
        components = [
            self.user_agent,
            self.accept_language,
            self.screen_resolution or "",
            self.timezone or "",
            self.platform or "",
            str(self.color_depth or ""),
            str(self.touch_support or ""),
        ]
        data = "|".join(components).encode()
        return hmac.new(secret.encode(), data, hashlib.sha256).hexdigest()

    def compute_loose_hash(self, secret: str) -> str:
        """Compute hash of only stable components."""
        ua_parts = self.user_agent.split()
        browser_family = ua_parts[0] if ua_parts else ""
        components = [browser_family, self.platform or "", self.timezone or ""]
        data = "|".join(components).encode()
        return hmac.new(secret.encode(), data, hashlib.sha256).hexdigest()

    @classmethod
    def from_headers(cls, headers: dict[str, str]) -> DeviceFingerprint:
        """Extract fingerprint from HTTP headers."""
        return cls(
            user_agent=headers.get("user-agent", ""),
            accept_language=headers.get("accept-language", ""),
            platform=headers.get("sec-ch-ua-platform", "").strip('"'),
        )

    @classmethod
    def from_client_data(cls, data: dict[str, Any]) -> DeviceFingerprint:
        """Extract fingerprint from client-side collected data."""
        return cls(
            user_agent=data.get("userAgent", ""),
            accept_language=data.get("language", ""),
            screen_resolution=data.get("screenResolution"),
            timezone=data.get("timezone"),
            platform=data.get("platform"),
            color_depth=data.get("colorDepth"),
            touch_support=data.get("touchSupport"),
            webgl_vendor=data.get("webglVendor"),
            cpu_class=data.get("cpuClass"),
        )


@dataclass
class BoundSession:
    """Session bound to a device."""

    session_id: str
    user_id: str
    fingerprint_hash: str
    fingerprint_loose_hash: str
    created_at: datetime
    last_verified_at: datetime
    binding_strength: BindingStrength = BindingStrength.MODERATE
    verification_failures: int = 0
    max_failures: int = 3
    metadata: dict[str, Any] = field(default_factory=dict)

    def is_expired(self, max_age: timedelta = timedelta(hours=24)) -> bool:
        """Check if session has expired."""
        return datetime.now(timezone.utc) - self.created_at > max_age

    def needs_reverification(
        self, interval: timedelta = timedelta(minutes=15)
    ) -> bool:
        """Check if session needs re-verification."""
        return datetime.now(timezone.utc) - self.last_verified_at > interval


@dataclass
class BindingVerificationResult:
    """Result of device binding verification."""

    valid: bool
    reason: str
    confidence: float  # 0.0 to 1.0
    requires_reauthentication: bool = False
    anomaly_detected: bool = False


class SessionBindingManager:
    """Manages session-device binding."""

    def __init__(
        self,
        secret: str,
        default_strength: BindingStrength = BindingStrength.MODERATE,
        max_verification_failures: int = 3,
    ):
        self._secret = secret
        self._default_strength = default_strength
        self._max_failures = max_verification_failures
        self._sessions: dict[str, BoundSession] = {}
        self._user_sessions: dict[str, list[str]] = {}

    def create_session(
        self,
        session_id: str,
        user_id: str,
        fingerprint: DeviceFingerprint,
        strength: BindingStrength | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> BoundSession:
        """Create a new bound session."""
        now = datetime.now(timezone.utc)
        session = BoundSession(
            session_id=session_id,
            user_id=user_id,
            fingerprint_hash=fingerprint.compute_hash(self._secret),
            fingerprint_loose_hash=fingerprint.compute_loose_hash(self._secret),
            created_at=now,
            last_verified_at=now,
            binding_strength=strength or self._default_strength,
            max_failures=self._max_failures,
            metadata=metadata or {},
        )
        self._sessions[session_id] = session

        if user_id not in self._user_sessions:
            self._user_sessions[user_id] = []
        self._user_sessions[user_id].append(session_id)

        log.info(
            "created bound session",
            extra={
                "session_id": session_id,
                "user_id": user_id,
                "strength": session.binding_strength.value,
            },
        )
        return session

    def verify_session(
        self,
        session_id: str,
        fingerprint: DeviceFingerprint,
    ) -> BindingVerificationResult:
        """Verify session against device fingerprint."""
        session = self._sessions.get(session_id)
        if not session:
            return BindingVerificationResult(
                valid=False,
                reason="session_not_found",
                confidence=1.0,
                requires_reauthentication=True,
            )

        if session.is_expired():
            self.invalidate_session(session_id)
            return BindingVerificationResult(
                valid=False,
                reason="session_expired",
                confidence=1.0,
                requires_reauthentication=True,
            )

        current_hash = fingerprint.compute_hash(self._secret)
        current_loose_hash = fingerprint.compute_loose_hash(self._secret)

        if session.binding_strength == BindingStrength.STRICT:
            if current_hash != session.fingerprint_hash:
                session.verification_failures += 1
                if session.verification_failures >= session.max_failures:
                    self.invalidate_session(session_id)
                    return BindingVerificationResult(
                        valid=False,
                        reason="fingerprint_mismatch_max_failures",
                        confidence=1.0,
                        requires_reauthentication=True,
                        anomaly_detected=True,
                    )
                return BindingVerificationResult(
                    valid=False,
                    reason="fingerprint_mismatch",
                    confidence=0.9,
                    anomaly_detected=True,
                )

        elif session.binding_strength == BindingStrength.MODERATE:
            if current_hash == session.fingerprint_hash:
                confidence = 1.0
            elif current_loose_hash == session.fingerprint_loose_hash:
                confidence = 0.7
                session.fingerprint_hash = current_hash
            else:
                session.verification_failures += 1
                if session.verification_failures >= session.max_failures:
                    self.invalidate_session(session_id)
                    return BindingVerificationResult(
                        valid=False,
                        reason="fingerprint_drift_max_failures",
                        confidence=1.0,
                        requires_reauthentication=True,
                        anomaly_detected=True,
                    )
                return BindingVerificationResult(
                    valid=False,
                    reason="fingerprint_drift",
                    confidence=0.5,
                    anomaly_detected=True,
                )

        else:  # LOOSE
            if current_loose_hash == session.fingerprint_loose_hash:
                confidence = 0.6
            else:
                session.verification_failures += 1
                if session.verification_failures >= session.max_failures:
                    self.invalidate_session(session_id)
                    return BindingVerificationResult(
                        valid=False,
                        reason="device_changed",
                        confidence=1.0,
                        requires_reauthentication=True,
                        anomaly_detected=True,
                    )
                return BindingVerificationResult(
                    valid=False,
                    reason="device_changed",
                    confidence=0.8,
                    requires_reauthentication=True,
                    anomaly_detected=True,
                )
            confidence = 0.6

        session.last_verified_at = datetime.now(timezone.utc)
        session.verification_failures = 0

        return BindingVerificationResult(
            valid=True,
            reason="verified",
            confidence=confidence,
        )

    def invalidate_session(self, session_id: str) -> bool:
        """Invalidate a session."""
        session = self._sessions.pop(session_id, None)
        if session:
            user_sessions = self._user_sessions.get(session.user_id, [])
            if session_id in user_sessions:
                user_sessions.remove(session_id)
            log.info(
                "invalidated session",
                extra={"session_id": session_id, "user_id": session.user_id},
            )
            return True
        return False

    def invalidate_user_sessions(self, user_id: str) -> int:
        """Invalidate all sessions for a user."""
        session_ids = self._user_sessions.pop(user_id, [])
        for sid in session_ids:
            self._sessions.pop(sid, None)
        log.info(
            "invalidated all user sessions",
            extra={"user_id": user_id, "count": len(session_ids)},
        )
        return len(session_ids)

    def get_user_sessions(self, user_id: str) -> list[BoundSession]:
        """Get all active sessions for a user."""
        session_ids = self._user_sessions.get(user_id, [])
        return [
            self._sessions[sid]
            for sid in session_ids
            if sid in self._sessions
        ]

    def get_session(self, session_id: str) -> BoundSession | None:
        """Get a session by ID."""
        return self._sessions.get(session_id)

    def cleanup_expired(self) -> int:
        """Remove expired sessions."""
        expired = [
            sid for sid, session in self._sessions.items() if session.is_expired()
        ]
        for sid in expired:
            self.invalidate_session(sid)
        return len(expired)


_default_manager: SessionBindingManager | None = None


def get_session_binding_manager(
    secret: str | None = None,
) -> SessionBindingManager:
    """Get or create the default session binding manager."""
    global _default_manager
    if _default_manager is None:
        import os

        secret = secret or os.environ.get("SESSION_BINDING_SECRET", "dev-secret")
        _default_manager = SessionBindingManager(secret)
    return _default_manager
