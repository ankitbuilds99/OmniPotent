"""Session Management.

Manages user sessions with policies and limits.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("devcopilot.sso_advanced.session_management")


class SessionStatus(str, Enum):
    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"
    LOCKED = "locked"


@dataclass
class SessionPolicy:
    """Session policy for an organization."""

    policy_id: str
    org_id: str
    max_session_duration_hours: int = 24
    idle_timeout_minutes: int = 60
    max_concurrent_sessions: int = 5
    require_mfa: bool = False
    allowed_ip_ranges: list[str] = field(default_factory=list)
    block_tor: bool = True
    enforce_device_trust: bool = False
    session_binding: str = "ip"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.policy_id,
            "org_id": self.org_id,
            "max_session_duration_hours": self.max_session_duration_hours,
            "idle_timeout_minutes": self.idle_timeout_minutes,
            "max_concurrent_sessions": self.max_concurrent_sessions,
            "require_mfa": self.require_mfa,
            "allowed_ip_ranges": self.allowed_ip_ranges,
            "block_tor": self.block_tor,
            "enforce_device_trust": self.enforce_device_trust,
            "session_binding": self.session_binding,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


@dataclass
class UserSession:
    """A user session."""

    session_id: str
    org_id: str
    user_id: str
    user_email: str
    ip_address: str
    user_agent: str
    device_id: str | None
    status: SessionStatus = SessionStatus.ACTIVE
    mfa_verified: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_active: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime | None = None

    @property
    def is_active(self) -> bool:
        if self.status != SessionStatus.ACTIVE:
            return False
        if self.expires_at and datetime.now(timezone.utc) > self.expires_at:
            return False
        return True

    @property
    def duration_minutes(self) -> float:
        return (datetime.now(timezone.utc) - self.created_at).total_seconds() / 60

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.session_id,
            "org_id": self.org_id,
            "user_id": self.user_id,
            "email": self.user_email,
            "ip": self.ip_address,
            "user_agent": self.user_agent[:80] if self.user_agent else "",
            "device_id": self.device_id,
            "status": self.status.value,
            "mfa_verified": self.mfa_verified,
            "is_active": self.is_active,
            "duration_minutes": round(self.duration_minutes, 1),
            "created_at": self.created_at.isoformat(),
            "last_active": self.last_active.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
        }


@dataclass
class SessionValidationResult:
    """Result of session validation."""

    valid: bool
    session: UserSession | None
    reason: str
    requires_mfa: bool = False
    requires_reauthentication: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "session": self.session.to_dict() if self.session else None,
            "reason": self.reason,
            "requires_mfa": self.requires_mfa,
            "requires_reauthentication": self.requires_reauthentication,
        }


class SessionManager:
    """Manages user sessions."""

    def __init__(self) -> None:
        self._policies: dict[str, SessionPolicy] = {}
        self._sessions: dict[str, UserSession] = {}
        self._user_sessions: dict[str, dict[str, list[str]]] = {}

    def set_policy(
        self,
        org_id: str,
        max_session_duration_hours: int = 24,
        idle_timeout_minutes: int = 60,
        max_concurrent_sessions: int = 5,
        require_mfa: bool = False,
        allowed_ip_ranges: list[str] | None = None,
        block_tor: bool = True,
        enforce_device_trust: bool = False,
        session_binding: str = "ip",
    ) -> SessionPolicy:
        """Set session policy for an org."""
        policy_id = f"policy_{org_id}"

        policy = SessionPolicy(
            policy_id=policy_id,
            org_id=org_id,
            max_session_duration_hours=max_session_duration_hours,
            idle_timeout_minutes=idle_timeout_minutes,
            max_concurrent_sessions=max_concurrent_sessions,
            require_mfa=require_mfa,
            allowed_ip_ranges=allowed_ip_ranges or [],
            block_tor=block_tor,
            enforce_device_trust=enforce_device_trust,
            session_binding=session_binding,
        )

        self._policies[org_id] = policy

        log.info(
            "Set session policy: org=%s duration=%dh timeout=%dm max=%d",
            org_id,
            max_session_duration_hours,
            idle_timeout_minutes,
            max_concurrent_sessions,
        )

        return policy

    def get_policy(self, org_id: str) -> SessionPolicy:
        """Get session policy for an org, or create default."""
        if org_id not in self._policies:
            return self.set_policy(org_id)
        return self._policies[org_id]

    def create_session(
        self,
        org_id: str,
        user_id: str,
        user_email: str,
        ip_address: str,
        user_agent: str,
        device_id: str | None = None,
        mfa_verified: bool = False,
    ) -> tuple[UserSession | None, str]:
        """Create a new session for a user."""
        policy = self.get_policy(org_id)

        if policy.allowed_ip_ranges:
            if not self._is_ip_allowed(ip_address, policy.allowed_ip_ranges):
                return None, "IP address not in allowed range"

        user_sessions = self._get_user_sessions(org_id, user_id)
        active_sessions = [
            s for s in user_sessions if s.is_active
        ]

        if len(active_sessions) >= policy.max_concurrent_sessions:
            oldest = min(active_sessions, key=lambda s: s.created_at)
            self.revoke_session(oldest.session_id, "max sessions exceeded")

        session_id = f"sess_{org_id}_{user_id}_{datetime.now(timezone.utc).timestamp()}"

        expires_at = datetime.now(timezone.utc) + timedelta(
            hours=policy.max_session_duration_hours
        )

        session = UserSession(
            session_id=session_id,
            org_id=org_id,
            user_id=user_id,
            user_email=user_email,
            ip_address=ip_address,
            user_agent=user_agent,
            device_id=device_id,
            mfa_verified=mfa_verified,
            expires_at=expires_at,
        )

        self._sessions[session_id] = session

        if org_id not in self._user_sessions:
            self._user_sessions[org_id] = {}
        if user_id not in self._user_sessions[org_id]:
            self._user_sessions[org_id][user_id] = []
        self._user_sessions[org_id][user_id].append(session_id)

        log.info(
            "Created session: id=%s user=%s org=%s",
            session_id,
            user_email,
            org_id,
        )

        return session, "success"

    def validate_session(
        self,
        session_id: str,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> SessionValidationResult:
        """Validate a session."""
        session = self._sessions.get(session_id)

        if not session:
            return SessionValidationResult(
                valid=False,
                session=None,
                reason="Session not found",
            )

        if session.status == SessionStatus.REVOKED:
            return SessionValidationResult(
                valid=False,
                session=session,
                reason="Session has been revoked",
            )

        if session.status == SessionStatus.LOCKED:
            return SessionValidationResult(
                valid=False,
                session=session,
                reason="Session is locked",
                requires_reauthentication=True,
            )

        if session.expires_at and datetime.now(timezone.utc) > session.expires_at:
            session.status = SessionStatus.EXPIRED
            return SessionValidationResult(
                valid=False,
                session=session,
                reason="Session has expired",
                requires_reauthentication=True,
            )

        policy = self.get_policy(session.org_id)

        idle_cutoff = datetime.now(timezone.utc) - timedelta(
            minutes=policy.idle_timeout_minutes
        )
        if session.last_active < idle_cutoff:
            session.status = SessionStatus.EXPIRED
            return SessionValidationResult(
                valid=False,
                session=session,
                reason="Session idle timeout",
                requires_reauthentication=True,
            )

        if policy.session_binding == "ip" and ip_address:
            if session.ip_address != ip_address:
                return SessionValidationResult(
                    valid=False,
                    session=session,
                    reason="IP address mismatch",
                    requires_reauthentication=True,
                )

        if policy.require_mfa and not session.mfa_verified:
            return SessionValidationResult(
                valid=False,
                session=session,
                reason="MFA verification required",
                requires_mfa=True,
            )

        session.last_active = datetime.now(timezone.utc)

        return SessionValidationResult(
            valid=True,
            session=session,
            reason="Valid session",
        )

    def verify_mfa(self, session_id: str) -> bool:
        """Mark MFA as verified for a session."""
        session = self._sessions.get(session_id)
        if not session:
            return False

        session.mfa_verified = True
        session.last_active = datetime.now(timezone.utc)

        log.info("MFA verified: session=%s", session_id)

        return True

    def revoke_session(
        self,
        session_id: str,
        reason: str = "",
    ) -> bool:
        """Revoke a session."""
        session = self._sessions.get(session_id)
        if not session:
            return False

        session.status = SessionStatus.REVOKED

        log.info(
            "Session revoked: id=%s reason=%s",
            session_id,
            reason,
        )

        return True

    def revoke_all_sessions(
        self,
        org_id: str,
        user_id: str,
        reason: str = "",
    ) -> int:
        """Revoke all sessions for a user."""
        sessions = self._get_user_sessions(org_id, user_id)
        count = 0

        for session in sessions:
            if session.status == SessionStatus.ACTIVE:
                session.status = SessionStatus.REVOKED
                count += 1

        log.info(
            "Revoked all sessions: org=%s user=%s count=%d reason=%s",
            org_id,
            user_id,
            count,
            reason,
        )

        return count

    def lock_session(self, session_id: str) -> bool:
        """Lock a session (requires re-authentication)."""
        session = self._sessions.get(session_id)
        if not session:
            return False

        session.status = SessionStatus.LOCKED

        log.info("Session locked: id=%s", session_id)

        return True

    def unlock_session(self, session_id: str) -> bool:
        """Unlock a locked session."""
        session = self._sessions.get(session_id)
        if not session or session.status != SessionStatus.LOCKED:
            return False

        session.status = SessionStatus.ACTIVE
        session.last_active = datetime.now(timezone.utc)

        log.info("Session unlocked: id=%s", session_id)

        return True

    def get_session(self, session_id: str) -> UserSession | None:
        """Get a session by ID."""
        return self._sessions.get(session_id)

    def _get_user_sessions(
        self,
        org_id: str,
        user_id: str,
    ) -> list[UserSession]:
        """Get all sessions for a user."""
        session_ids = (
            self._user_sessions
            .get(org_id, {})
            .get(user_id, [])
        )

        return [
            self._sessions[sid]
            for sid in session_ids
            if sid in self._sessions
        ]

    def get_user_sessions(
        self,
        org_id: str,
        user_id: str,
        include_inactive: bool = False,
    ) -> list[UserSession]:
        """Get sessions for a user."""
        sessions = self._get_user_sessions(org_id, user_id)

        if not include_inactive:
            sessions = [s for s in sessions if s.is_active]

        return sessions

    def get_active_sessions(
        self,
        org_id: str,
    ) -> list[UserSession]:
        """Get all active sessions for an org."""
        return [
            s for s in self._sessions.values()
            if s.org_id == org_id and s.is_active
        ]

    def cleanup_expired(self) -> int:
        """Clean up expired sessions."""
        now = datetime.now(timezone.utc)
        count = 0

        for session in list(self._sessions.values()):
            if session.expires_at and now > session.expires_at:
                if session.status == SessionStatus.ACTIVE:
                    session.status = SessionStatus.EXPIRED
                    count += 1

        if count > 0:
            log.info("Cleaned up %d expired sessions", count)

        return count

    def _is_ip_allowed(
        self,
        ip_address: str,
        allowed_ranges: list[str],
    ) -> bool:
        """Check if IP is in allowed ranges."""
        for allowed in allowed_ranges:
            if "/" in allowed:
                if self._ip_in_cidr(ip_address, allowed):
                    return True
            else:
                if ip_address == allowed:
                    return True

        return False

    def _ip_in_cidr(self, ip: str, cidr: str) -> bool:
        """Check if IP is in CIDR range (simplified)."""
        if "/" not in cidr:
            return ip == cidr

        network, prefix = cidr.split("/")
        prefix_len = int(prefix)

        ip_parts = [int(p) for p in ip.split(".")]
        net_parts = [int(p) for p in network.split(".")]

        ip_int = sum(p << (24 - i * 8) for i, p in enumerate(ip_parts))
        net_int = sum(p << (24 - i * 8) for i, p in enumerate(net_parts))

        mask = (0xFFFFFFFF << (32 - prefix_len)) & 0xFFFFFFFF

        return (ip_int & mask) == (net_int & mask)

    def get_session_stats(self, org_id: str) -> dict[str, Any]:
        """Get session statistics for an org."""
        sessions = [
            s for s in self._sessions.values()
            if s.org_id == org_id
        ]

        active = sum(1 for s in sessions if s.is_active)
        by_status = {}
        for status in SessionStatus:
            by_status[status.value] = sum(
                1 for s in sessions if s.status == status
            )

        unique_users = len(set(s.user_id for s in sessions if s.is_active))
        unique_ips = len(set(s.ip_address for s in sessions if s.is_active))

        return {
            "total_sessions": len(sessions),
            "active_sessions": active,
            "by_status": by_status,
            "unique_active_users": unique_users,
            "unique_active_ips": unique_ips,
        }


_manager: SessionManager | None = None


def get_session_manager() -> SessionManager:
    """Get the default session manager."""
    global _manager
    if _manager is None:
        _manager = SessionManager()
    return _manager
