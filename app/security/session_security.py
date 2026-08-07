"""Advanced session security with idle timeout and concurrent session limits.

Provides:
- Idle session timeout (activity-based expiration)
- Concurrent session limits per user
- Session binding to IP/User-Agent
- Forced re-authentication for sensitive operations
- Device/location tracking
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

log = logging.getLogger("omnipotent.security.session")


class SessionTimeoutStrategy(str, Enum):
    """Session timeout strategies."""

    SLIDING = "sliding"
    ABSOLUTE = "absolute"
    HYBRID = "hybrid"


@dataclass
class SessionSecurityConfig:
    """Per-role session security configuration."""

    idle_timeout_seconds: int = 1800
    absolute_timeout_seconds: int = 86400
    max_concurrent_sessions: int = 5
    bind_to_ip: bool = False
    bind_to_user_agent: bool = False
    require_reauth_for_sensitive: bool = True
    timeout_strategy: SessionTimeoutStrategy = SessionTimeoutStrategy.SLIDING


ROLE_SECURITY_CONFIGS: dict[str, SessionSecurityConfig] = {
    "viewer": SessionSecurityConfig(
        idle_timeout_seconds=900,
        max_concurrent_sessions=3,
    ),
    "responder": SessionSecurityConfig(
        idle_timeout_seconds=1800,
        max_concurrent_sessions=5,
    ),
    "admin": SessionSecurityConfig(
        idle_timeout_seconds=3600,
        max_concurrent_sessions=3,
        bind_to_ip=True,
        require_reauth_for_sensitive=True,
    ),
    "owner": SessionSecurityConfig(
        idle_timeout_seconds=7200,
        max_concurrent_sessions=2,
        bind_to_ip=True,
        bind_to_user_agent=True,
        require_reauth_for_sensitive=True,
    ),
}


@dataclass
class SecureSession:
    """Enhanced session with security metadata."""

    session_id: str
    user_id: str
    org_id: str
    role: str
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    absolute_expiry: float = 0.0
    ip_address: str = ""
    user_agent_hash: str = ""
    device_fingerprint: str = ""
    location: str = ""
    is_sensitive_authenticated: bool = False
    sensitive_auth_at: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.absolute_expiry:
            config = ROLE_SECURITY_CONFIGS.get(self.role, SessionSecurityConfig())
            self.absolute_expiry = self.created_at + config.absolute_timeout_seconds


@dataclass
class SessionValidationResult:
    """Result of session validation."""

    valid: bool
    reason: str = ""
    session: SecureSession | None = None
    requires_reauth: bool = False


SENSITIVE_OPERATIONS = frozenset(
    {
        "manage_integrations",
        "manage_members",
        "delete_org",
        "rotate_api_key",
        "update_billing",
        "manage_secrets",
        "disable_mfa",
        "change_password",
    }
)

SENSITIVE_AUTH_WINDOW = 300


def generate_secure_session_id() -> str:
    """Generate cryptographically secure session ID."""
    return secrets.token_urlsafe(32)


def hash_user_agent(user_agent: str) -> str:
    """Hash user agent for storage."""
    return hashlib.sha256(user_agent.encode()).hexdigest()[:16]


class SecureSessionStore:
    """Redis-backed secure session store with concurrent limits."""

    SESSION_PREFIX = "secure_session:"
    USER_SESSIONS_PREFIX = "user_sessions_z:"
    SESSION_META_PREFIX = "session_meta:"
    LIMIT_OVERRIDE_PREFIX = "session_limit_override:"

    def __init__(self, redis_client: Any = None):
        self._redis = redis_client

    async def _get_redis(self) -> Any:
        if self._redis:
            return self._redis
        from app.redis_sessions import get_redis_client

        return get_redis_client()

    async def create_session(
        self,
        user_id: str,
        org_id: str,
        role: str,
        ip_address: str = "",
        user_agent: str = "",
        device_fingerprint: str = "",
        location: str = "",
        device_info: dict | None = None,
    ) -> tuple[SecureSession, list[str]]:
        """
        Create a new secure session, enforcing concurrent limits.

        Returns:
            (session, list of invalidated session IDs)
        """
        session = SecureSession(
            session_id=generate_secure_session_id(),
            user_id=user_id,
            org_id=org_id,
            role=role,
            ip_address=ip_address,
            user_agent_hash=hash_user_agent(user_agent) if user_agent else "",
            device_fingerprint=device_fingerprint,
            location=location,
        )

        invalidated = await self._enforce_concurrent_limit(session, device_info)

        await self._store_session(session)

        self._audit_log(
            "session.created",
            session_id=session.session_id,
            user_id=user_id,
            ip=ip_address,
        )

        return session, invalidated

    async def _enforce_concurrent_limit(
        self,
        session: SecureSession,
        device_info: dict | None = None,
    ) -> list[str]:
        """Enforce concurrent session limit, evicting oldest sessions."""
        redis = await self._get_redis()
        if not redis:
            return []

        config = ROLE_SECURITY_CONFIGS.get(session.role, SessionSecurityConfig())
        max_sessions = await self._get_session_limit(session.user_id, config)

        zset_key = f"{self.USER_SESSIONS_PREFIX}{session.user_id}"
        invalidated: list[str] = []

        script = """
        local zset_key = KEYS[1]
        local max_sessions = tonumber(ARGV[1])
        local new_session_id = ARGV[2]
        local new_score = tonumber(ARGV[3])

        local count = redis.call('ZCARD', zset_key)
        local evicted = {}

        while count >= max_sessions do
            local oldest = redis.call('ZRANGE', zset_key, 0, 0)
            if #oldest > 0 then
                redis.call('ZREM', zset_key, oldest[1])
                table.insert(evicted, oldest[1])
                count = count - 1
            else
                break
            end
        end

        redis.call('ZADD', zset_key, new_score, new_session_id)
        return evicted
        """

        evicted_ids = redis.eval(
            script,
            1,
            zset_key,
            max_sessions,
            session.session_id,
            session.created_at,
        )

        if device_info:
            meta_key = f"{self.SESSION_META_PREFIX}{session.session_id}"
            redis.hset(
                meta_key,
                mapping={
                    "device_type": device_info.get("device_type", "unknown"),
                    "browser": device_info.get("browser", "unknown"),
                    "os": device_info.get("os", "unknown"),
                    "ip": session.ip_address,
                    "location": session.location,
                    "created_at": str(session.created_at),
                },
            )
            redis.expire(meta_key, config.absolute_timeout_seconds)

        for old_session_id in evicted_ids or []:
            await self._cleanup_evicted_session(old_session_id, session.user_id)
            invalidated.append(old_session_id)

        return invalidated

    async def _get_session_limit(
        self,
        user_id: str,
        config: SessionSecurityConfig,
    ) -> int:
        """Get session limit, checking for admin overrides."""
        redis = await self._get_redis()
        if not redis:
            return config.max_concurrent_sessions

        override_key = f"{self.LIMIT_OVERRIDE_PREFIX}{user_id}"
        override = redis.hget(override_key, "limit")
        if override:
            return int(override)

        return config.max_concurrent_sessions

    async def _cleanup_evicted_session(self, session_id: str, user_id: str) -> None:
        """Remove session data and notify."""
        redis = await self._get_redis()
        if not redis:
            return

        redis.delete(f"{self.SESSION_PREFIX}{session_id}")
        redis.delete(f"{self.SESSION_META_PREFIX}{session_id}")

        redis.publish(f"session_invalidated:{user_id}", session_id)

        self._audit_log(
            "session.evicted",
            session_id=session_id,
            user_id=user_id,
            reason="concurrent_limit",
        )

    async def _store_session(self, session: SecureSession) -> None:
        """Store session data in Redis."""
        redis = await self._get_redis()
        if not redis:
            return

        config = ROLE_SECURITY_CONFIGS.get(session.role, SessionSecurityConfig())
        key = f"{self.SESSION_PREFIX}{session.session_id}"

        redis.hset(
            key,
            mapping={
                "session_id": session.session_id,
                "user_id": session.user_id,
                "org_id": session.org_id,
                "role": session.role,
                "created_at": str(session.created_at),
                "last_activity": str(session.last_activity),
                "absolute_expiry": str(session.absolute_expiry),
                "ip_address": session.ip_address,
                "user_agent_hash": session.user_agent_hash,
                "device_fingerprint": session.device_fingerprint,
                "is_sensitive_authenticated": "1" if session.is_sensitive_authenticated else "0",
                "sensitive_auth_at": str(session.sensitive_auth_at),
            },
        )
        redis.expire(key, config.idle_timeout_seconds)

    async def get_session(self, session_id: str) -> SecureSession | None:
        """Retrieve a session by ID."""
        redis = await self._get_redis()
        if not redis:
            return None

        key = f"{self.SESSION_PREFIX}{session_id}"
        data = redis.hgetall(key)

        if not data:
            return None

        return SecureSession(
            session_id=data.get("session_id", session_id),
            user_id=data.get("user_id", ""),
            org_id=data.get("org_id", ""),
            role=data.get("role", "viewer"),
            created_at=float(data.get("created_at", 0)),
            last_activity=float(data.get("last_activity", 0)),
            absolute_expiry=float(data.get("absolute_expiry", 0)),
            ip_address=data.get("ip_address", ""),
            user_agent_hash=data.get("user_agent_hash", ""),
            device_fingerprint=data.get("device_fingerprint", ""),
            is_sensitive_authenticated=data.get("is_sensitive_authenticated") == "1",
            sensitive_auth_at=float(data.get("sensitive_auth_at", 0)),
        )

    async def validate_session(
        self,
        session_id: str,
        request_ip: str = "",
        request_user_agent: str = "",
        operation: str = "",
    ) -> SessionValidationResult:
        """
        Validate a session against security policies.

        Checks:
        - Session exists
        - Idle timeout not exceeded
        - Absolute timeout not exceeded
        - IP/User-Agent binding
        - Sensitive operation re-auth
        """
        session = await self.get_session(session_id)
        if not session:
            return SessionValidationResult(
                valid=False,
                reason="session_not_found",
            )

        config = ROLE_SECURITY_CONFIGS.get(session.role, SessionSecurityConfig())
        now = time.time()

        if now > session.absolute_expiry:
            await self.invalidate_session(session_id, "absolute_timeout")
            return SessionValidationResult(
                valid=False,
                reason="absolute_timeout",
            )

        idle_seconds = now - session.last_activity
        if idle_seconds > config.idle_timeout_seconds:
            await self.invalidate_session(session_id, "idle_timeout")
            return SessionValidationResult(
                valid=False,
                reason="idle_timeout",
            )

        if config.bind_to_ip and session.ip_address:
            if session.ip_address != request_ip:
                self._audit_log(
                    "session.ip_mismatch",
                    session_id=session_id,
                    expected=session.ip_address,
                    actual=request_ip,
                )
                return SessionValidationResult(
                    valid=False,
                    reason="ip_binding_violation",
                )

        if config.bind_to_user_agent and session.user_agent_hash:
            request_ua_hash = hash_user_agent(request_user_agent)
            if session.user_agent_hash != request_ua_hash:
                return SessionValidationResult(
                    valid=False,
                    reason="user_agent_binding_violation",
                )

        requires_reauth = False
        if operation in SENSITIVE_OPERATIONS:
            if config.require_reauth_for_sensitive:
                if not session.is_sensitive_authenticated:
                    requires_reauth = True
                else:
                    elapsed = now - session.sensitive_auth_at
                    if elapsed > SENSITIVE_AUTH_WINDOW:
                        requires_reauth = True

        return SessionValidationResult(
            valid=True,
            session=session,
            requires_reauth=requires_reauth,
        )

    async def touch_session(self, session_id: str) -> bool:
        """Update last activity and refresh TTL."""
        redis = await self._get_redis()
        if not redis:
            return False

        session = await self.get_session(session_id)
        if not session:
            return False

        config = ROLE_SECURITY_CONFIGS.get(session.role, SessionSecurityConfig())

        if config.timeout_strategy in (
            SessionTimeoutStrategy.SLIDING,
            SessionTimeoutStrategy.HYBRID,
        ):
            key = f"{self.SESSION_PREFIX}{session_id}"
            redis.hset(key, "last_activity", str(time.time()))
            redis.expire(key, config.idle_timeout_seconds)

        return True

    async def mark_sensitive_authenticated(self, session_id: str) -> bool:
        """Mark session as recently re-authenticated for sensitive ops."""
        redis = await self._get_redis()
        if not redis:
            return False

        key = f"{self.SESSION_PREFIX}{session_id}"
        now = time.time()

        redis.hset(
            key,
            mapping={
                "is_sensitive_authenticated": "1",
                "sensitive_auth_at": str(now),
            },
        )

        self._audit_log(
            "session.sensitive_reauth",
            session_id=session_id,
        )

        return True

    async def invalidate_session(
        self,
        session_id: str,
        reason: str = "manual",
    ) -> bool:
        """Invalidate a session."""
        redis = await self._get_redis()
        if not redis:
            return False

        session = await self.get_session(session_id)
        if session:
            zset_key = f"{self.USER_SESSIONS_PREFIX}{session.user_id}"
            redis.zrem(zset_key, session_id)

        redis.delete(f"{self.SESSION_PREFIX}{session_id}")
        redis.delete(f"{self.SESSION_META_PREFIX}{session_id}")

        self._audit_log(
            "session.invalidated",
            session_id=session_id,
            reason=reason,
        )

        return True

    async def regenerate_session_id(
        self,
        old_session_id: str,
    ) -> str | None:
        """
        Regenerate session ID after privilege escalation.
        Prevents session fixation attacks.
        """
        session = await self.get_session(old_session_id)
        if not session:
            return None

        new_session_id = generate_secure_session_id()

        redis = await self._get_redis()
        if redis:
            old_key = f"{self.SESSION_PREFIX}{old_session_id}"
            new_key = f"{self.SESSION_PREFIX}{new_session_id}"
            ttl = redis.ttl(old_key)

            data = redis.hgetall(old_key)
            if data:
                data["session_id"] = new_session_id
                redis.hset(new_key, mapping=data)
                if ttl > 0:
                    redis.expire(new_key, ttl)

            zset_key = f"{self.USER_SESSIONS_PREFIX}{session.user_id}"
            score = redis.zscore(zset_key, old_session_id)
            if score:
                redis.zrem(zset_key, old_session_id)
                redis.zadd(zset_key, {new_session_id: score})

            redis.delete(old_key)

        self._audit_log(
            "session.regenerated",
            old_session_id=old_session_id,
            new_session_id=new_session_id,
        )

        return new_session_id

    async def list_user_sessions(self, user_id: str) -> list[dict]:
        """List all active sessions for a user."""
        redis = await self._get_redis()
        if not redis:
            return []

        zset_key = f"{self.USER_SESSIONS_PREFIX}{user_id}"
        sessions = redis.zrange(zset_key, 0, -1, withscores=True)

        result = []
        for session_id, created_at in sessions or []:
            session = await self.get_session(session_id)
            if session:
                meta_key = f"{self.SESSION_META_PREFIX}{session_id}"
                meta = redis.hgetall(meta_key) or {}

                result.append(
                    {
                        "session_id": session_id,
                        "created_at": created_at,
                        "last_activity": session.last_activity,
                        "ip_address": session.ip_address,
                        "device_type": meta.get("device_type", "unknown"),
                        "browser": meta.get("browser", "unknown"),
                        "os": meta.get("os", "unknown"),
                        "location": meta.get("location", ""),
                    }
                )

        return result

    async def invalidate_all_user_sessions(
        self,
        user_id: str,
        except_session_id: str | None = None,
    ) -> int:
        """Invalidate all sessions for a user (logout everywhere)."""
        redis = await self._get_redis()
        if not redis:
            return 0

        zset_key = f"{self.USER_SESSIONS_PREFIX}{user_id}"
        session_ids = redis.zrange(zset_key, 0, -1)

        count = 0
        for session_id in session_ids or []:
            if session_id == except_session_id:
                continue
            await self.invalidate_session(session_id, "logout_all")
            count += 1

        self._audit_log(
            "session.logout_all",
            user_id=user_id,
            sessions_invalidated=count,
        )

        return count

    async def admin_set_session_limit(
        self,
        user_id: str,
        new_limit: int,
        admin_user_id: str,
        reason: str,
    ) -> bool:
        """Admin override for session limit."""
        redis = await self._get_redis()
        if not redis:
            return False

        override_key = f"{self.LIMIT_OVERRIDE_PREFIX}{user_id}"

        redis.hset(
            override_key,
            mapping={
                "limit": str(new_limit),
                "set_by": admin_user_id,
                "reason": reason,
                "set_at": str(time.time()),
            },
        )
        redis.expire(override_key, 86400 * 30)

        self._audit_log(
            "session.limit_override",
            target_user=user_id,
            new_limit=new_limit,
            admin=admin_user_id,
            reason=reason,
        )

        return True

    def _audit_log(self, event: str, **kwargs: Any) -> None:
        """Log session-related audit events."""
        try:
            from app import audit

            audit.record(event, **kwargs)
        except Exception:
            log.info("Session audit: %s %s", event, kwargs)


_session_store: SecureSessionStore | None = None


def get_secure_session_store() -> SecureSessionStore:
    """Get the singleton secure session store instance."""
    global _session_store
    if _session_store is None:
        _session_store = SecureSessionStore()
    return _session_store
