"""Emergency bypass for IdP outages.

Provides break-glass access when the identity provider is unavailable:
- Pre-registered emergency codes
- Time-limited access
- Full audit trail
- Security notifications
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum, auto
from typing import Any

log = logging.getLogger("devcopilot.sso.emergency_bypass")


class BypassReason(Enum):
    """Reasons for emergency bypass."""

    IDP_UNREACHABLE = auto()
    IDP_TIMEOUT = auto()
    CERTIFICATE_EXPIRED = auto()
    SCHEDULED_MAINTENANCE = auto()
    CRITICAL_INCIDENT = auto()
    MANUAL_OVERRIDE = auto()


class BypassStatus(Enum):
    """Status of a bypass attempt."""

    SUCCESS = "success"
    INVALID_CODE = "invalid_code"
    CODE_EXPIRED = "code_expired"
    CODE_ALREADY_USED = "code_already_used"
    USER_NOT_REGISTERED = "user_not_registered"
    BYPASS_DISABLED = "bypass_disabled"
    RATE_LIMITED = "rate_limited"


@dataclass
class EmergencyCode:
    """An emergency access code."""

    code_hash: str
    user_email: str
    created_at: datetime
    expires_at: datetime
    max_uses: int = 1
    used_count: int = 0
    last_used_at: datetime | None = None
    created_by: str | None = None
    note: str | None = None

    @property
    def is_expired(self) -> bool:
        return datetime.now(timezone.utc) > self.expires_at

    @property
    def is_exhausted(self) -> bool:
        return self.used_count >= self.max_uses

    @property
    def is_valid(self) -> bool:
        return not self.is_expired and not self.is_exhausted


@dataclass
class BypassAttempt:
    """Record of a bypass attempt."""

    id: str
    user_email: str
    timestamp: datetime
    status: BypassStatus
    reason: BypassReason
    ip_address: str
    user_agent: str
    code_hint: str
    session_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class BypassSession:
    """An active emergency bypass session."""

    session_id: str
    user_email: str
    created_at: datetime
    expires_at: datetime
    reason: BypassReason
    ip_address: str
    permissions: list[str] = field(default_factory=list)
    is_active: bool = True

    @property
    def is_expired(self) -> bool:
        return datetime.now(timezone.utc) > self.expires_at


class EmergencyBypassManager:
    """Manages emergency bypass access."""

    def __init__(
        self,
        secret_key: str,
        default_code_validity_hours: int = 24,
        default_session_validity_minutes: int = 60,
        max_attempts_per_hour: int = 5,
        enabled: bool = True,
    ):
        self._secret_key = secret_key.encode()
        self.default_code_validity = timedelta(hours=default_code_validity_hours)
        self.default_session_validity = timedelta(minutes=default_session_validity_minutes)
        self.max_attempts_per_hour = max_attempts_per_hour
        self.enabled = enabled

        self._codes: dict[str, EmergencyCode] = {}
        self._sessions: dict[str, BypassSession] = {}
        self._attempts: list[BypassAttempt] = []
        self._attempt_counter: int = 0
        self._notification_handlers: list[Any] = []

    def generate_code(
        self,
        user_email: str,
        validity: timedelta | None = None,
        max_uses: int = 1,
        created_by: str | None = None,
        note: str | None = None,
    ) -> str:
        """Generate a new emergency code for a user."""
        code = secrets.token_urlsafe(16)
        code_hash = self._hash_code(code)

        now = datetime.now(timezone.utc)
        validity = validity or self.default_code_validity

        emergency_code = EmergencyCode(
            code_hash=code_hash,
            user_email=user_email,
            created_at=now,
            expires_at=now + validity,
            max_uses=max_uses,
            created_by=created_by,
            note=note,
        )

        self._codes[code_hash] = emergency_code

        log.info(
            "emergency code generated",
            extra={
                "user_email": user_email,
                "expires_at": emergency_code.expires_at.isoformat(),
                "created_by": created_by,
            },
        )

        return code

    def verify_code(
        self,
        user_email: str,
        code: str,
        reason: BypassReason,
        ip_address: str,
        user_agent: str,
    ) -> tuple[BypassStatus, BypassSession | None]:
        """Verify an emergency code and create a session if valid."""
        self._attempt_counter += 1
        attempt_id = f"attempt_{self._attempt_counter}"
        code_hint = code[:4] + "****" if len(code) > 4 else "****"

        if not self.enabled:
            self._record_attempt(
                attempt_id, user_email, BypassStatus.BYPASS_DISABLED,
                reason, ip_address, user_agent, code_hint,
            )
            return BypassStatus.BYPASS_DISABLED, None

        if self._is_rate_limited(user_email, ip_address):
            self._record_attempt(
                attempt_id, user_email, BypassStatus.RATE_LIMITED,
                reason, ip_address, user_agent, code_hint,
            )
            return BypassStatus.RATE_LIMITED, None

        code_hash = self._hash_code(code)
        emergency_code = self._codes.get(code_hash)

        if not emergency_code:
            self._record_attempt(
                attempt_id, user_email, BypassStatus.INVALID_CODE,
                reason, ip_address, user_agent, code_hint,
            )
            self._notify_failed_attempt(user_email, ip_address, "invalid_code")
            return BypassStatus.INVALID_CODE, None

        if emergency_code.user_email != user_email:
            self._record_attempt(
                attempt_id, user_email, BypassStatus.USER_NOT_REGISTERED,
                reason, ip_address, user_agent, code_hint,
            )
            self._notify_failed_attempt(user_email, ip_address, "user_mismatch")
            return BypassStatus.USER_NOT_REGISTERED, None

        if emergency_code.is_expired:
            self._record_attempt(
                attempt_id, user_email, BypassStatus.CODE_EXPIRED,
                reason, ip_address, user_agent, code_hint,
            )
            return BypassStatus.CODE_EXPIRED, None

        if emergency_code.is_exhausted:
            self._record_attempt(
                attempt_id, user_email, BypassStatus.CODE_ALREADY_USED,
                reason, ip_address, user_agent, code_hint,
            )
            return BypassStatus.CODE_ALREADY_USED, None

        emergency_code.used_count += 1
        emergency_code.last_used_at = datetime.now(timezone.utc)

        session = self._create_session(user_email, reason, ip_address)

        self._record_attempt(
            attempt_id, user_email, BypassStatus.SUCCESS,
            reason, ip_address, user_agent, code_hint, session.session_id,
        )

        self._notify_successful_bypass(user_email, session, reason)

        log.warning(
            "emergency bypass activated",
            extra={
                "user_email": user_email,
                "session_id": session.session_id,
                "reason": reason.name,
                "ip_address": ip_address,
            },
        )

        return BypassStatus.SUCCESS, session

    def _create_session(
        self,
        user_email: str,
        reason: BypassReason,
        ip_address: str,
    ) -> BypassSession:
        """Create an emergency bypass session."""
        session_id = secrets.token_urlsafe(24)
        now = datetime.now(timezone.utc)

        session = BypassSession(
            session_id=session_id,
            user_email=user_email,
            created_at=now,
            expires_at=now + self.default_session_validity,
            reason=reason,
            ip_address=ip_address,
            permissions=["VIEW_INCIDENTS", "INVESTIGATE", "EXECUTE_ACTIONS"],
        )

        self._sessions[session_id] = session
        return session

    def validate_session(self, session_id: str) -> BypassSession | None:
        """Validate an emergency bypass session."""
        session = self._sessions.get(session_id)
        if not session:
            return None
        if not session.is_active:
            return None
        if session.is_expired:
            session.is_active = False
            return None
        return session

    def end_session(self, session_id: str) -> bool:
        """End an emergency bypass session."""
        session = self._sessions.get(session_id)
        if session:
            session.is_active = False
            log.info(
                "emergency session ended",
                extra={"session_id": session_id, "user_email": session.user_email},
            )
            return True
        return False

    def revoke_code(self, user_email: str) -> int:
        """Revoke all emergency codes for a user."""
        revoked = 0
        to_remove = []
        for code_hash, code in self._codes.items():
            if code.user_email == user_email:
                to_remove.append(code_hash)
                revoked += 1

        for code_hash in to_remove:
            del self._codes[code_hash]

        if revoked > 0:
            log.info(
                "emergency codes revoked",
                extra={"user_email": user_email, "count": revoked},
            )

        return revoked

    def list_codes(self, user_email: str) -> list[dict[str, Any]]:
        """List all emergency codes for a user (without actual codes)."""
        codes = []
        for code in self._codes.values():
            if code.user_email == user_email:
                codes.append({
                    "created_at": code.created_at.isoformat(),
                    "expires_at": code.expires_at.isoformat(),
                    "max_uses": code.max_uses,
                    "used_count": code.used_count,
                    "is_valid": code.is_valid,
                    "created_by": code.created_by,
                    "note": code.note,
                })
        return codes

    def list_active_sessions(self) -> list[dict[str, Any]]:
        """List all active emergency bypass sessions."""
        sessions = []
        for session in self._sessions.values():
            if session.is_active and not session.is_expired:
                sessions.append({
                    "session_id": session.session_id,
                    "user_email": session.user_email,
                    "created_at": session.created_at.isoformat(),
                    "expires_at": session.expires_at.isoformat(),
                    "reason": session.reason.name,
                    "ip_address": session.ip_address,
                })
        return sessions

    def get_audit_log(
        self,
        user_email: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Get audit log of bypass attempts."""
        attempts = self._attempts
        if user_email:
            attempts = [a for a in attempts if a.user_email == user_email]

        attempts = sorted(attempts, key=lambda a: a.timestamp, reverse=True)[:limit]

        return [
            {
                "id": a.id,
                "user_email": a.user_email,
                "timestamp": a.timestamp.isoformat(),
                "status": a.status.value,
                "reason": a.reason.name,
                "ip_address": a.ip_address,
                "code_hint": a.code_hint,
                "session_id": a.session_id,
            }
            for a in attempts
        ]

    def _hash_code(self, code: str) -> str:
        """Hash an emergency code."""
        return hmac.new(self._secret_key, code.encode(), hashlib.sha256).hexdigest()

    def _is_rate_limited(self, user_email: str, ip_address: str) -> bool:
        """Check if user/IP is rate limited."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
        recent = [
            a for a in self._attempts
            if a.timestamp > cutoff and (a.user_email == user_email or a.ip_address == ip_address)
        ]
        return len(recent) >= self.max_attempts_per_hour

    def _record_attempt(
        self,
        attempt_id: str,
        user_email: str,
        status: BypassStatus,
        reason: BypassReason,
        ip_address: str,
        user_agent: str,
        code_hint: str,
        session_id: str | None = None,
    ) -> None:
        """Record a bypass attempt."""
        attempt = BypassAttempt(
            id=attempt_id,
            user_email=user_email,
            timestamp=datetime.now(timezone.utc),
            status=status,
            reason=reason,
            ip_address=ip_address,
            user_agent=user_agent,
            code_hint=code_hint,
            session_id=session_id,
        )
        self._attempts.append(attempt)

        if len(self._attempts) > 10000:
            self._attempts = self._attempts[-5000:]

    def _notify_failed_attempt(
        self,
        user_email: str,
        ip_address: str,
        failure_type: str,
    ) -> None:
        """Notify security of failed bypass attempt."""
        log.warning(
            "emergency bypass failed",
            extra={
                "user_email": user_email,
                "ip_address": ip_address,
                "failure_type": failure_type,
            },
        )
        for handler in self._notification_handlers:
            try:
                handler("bypass_failed", {
                    "user_email": user_email,
                    "ip_address": ip_address,
                    "failure_type": failure_type,
                })
            except Exception as e:
                log.error("notification handler failed", extra={"error": str(e)})

    def _notify_successful_bypass(
        self,
        user_email: str,
        session: BypassSession,
        reason: BypassReason,
    ) -> None:
        """Notify security of successful bypass."""
        for handler in self._notification_handlers:
            try:
                handler("bypass_success", {
                    "user_email": user_email,
                    "session_id": session.session_id,
                    "reason": reason.name,
                    "ip_address": session.ip_address,
                })
            except Exception as e:
                log.error("notification handler failed", extra={"error": str(e)})

    def add_notification_handler(self, handler: Any) -> None:
        """Add a notification handler for security events."""
        self._notification_handlers.append(handler)

    def check_idp_health(self, idp_id: str) -> tuple[bool, str | None]:
        """Check if an IdP is healthy (placeholder for actual health check)."""
        return True, None


_bypass: EmergencyBypassManager | None = None


def get_emergency_bypass() -> EmergencyBypassManager:
    """Get the default emergency bypass manager."""
    global _bypass
    if _bypass is None:
        import os
        secret = os.environ.get("COPILOT_EMERGENCY_SECRET", "default-emergency-secret")
        _bypass = EmergencyBypassManager(secret_key=secret)
    return _bypass
