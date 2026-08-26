"""Externalized session store for horizontal scaling.

Moves session state from in-memory to Redis, enabling:
- Horizontal pod scaling without session loss
- Graceful pod termination without disruption
- Session migration during rolling deployments
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from app.config import get_settings
from app.redis_pool import RedisPoolType, get_redis

log = logging.getLogger("omnipotent.session_store")

# Redis expiry (seconds) for session and investigation keys. 24h is the fallback,
# not a rule: a session must survive a rolling deploy and a long incident, but
# holding it forever leaks state for every user who never logs out.
SESSION_TTL_DEFAULT = 86_400
SESSION_PREFIX = "session:"
INVESTIGATION_PREFIX = "investigation:"


def _session_ttl() -> int:
    """TTL to stamp on session/investigation keys.

    Read per call (get_settings is cached) instead of frozen at import time, so
    an operator override applies without a code change. getattr keeps this
    working while app/config.py has no copilot_session_ttl field yet — the
    fallback is the historical 24h, so behaviour is unchanged until it does.
    """
    ttl = getattr(get_settings(), "copilot_session_ttl", SESSION_TTL_DEFAULT)
    return int(ttl) if ttl else SESSION_TTL_DEFAULT


@dataclass
class SessionData:
    """Serializable session state."""

    session_id: str
    user_id: str
    org_id: str
    tenant_id: str | None = None
    created_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)
    investigation_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, data: str) -> "SessionData":
        return cls(**json.loads(data))


@dataclass
class InvestigationState:
    """Serializable investigation state for LangGraph checkpoints."""

    investigation_id: str
    session_id: str
    status: str
    current_node: str | None = None
    plan: str | None = None
    findings: list[dict] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    checkpoint_data: dict = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, data: str) -> "InvestigationState":
        return cls(**json.loads(data))


class DistributedSessionStore:
    """Redis-backed session store for horizontal scaling."""

    def __init__(self):
        self._local_cache: dict[str, SessionData] = {}
        self._cache_ttl: dict[str, float] = {}
        self._cache_duration = 30.0

    async def _get_redis(self) -> Any:
        return await get_redis(RedisPoolType.SESSION)

    async def create_session(self, session_data: SessionData) -> str:
        """Create a new session in Redis."""
        redis = await self._get_redis()
        key = f"{SESSION_PREFIX}{session_data.session_id}"
        ttl = _session_ttl()  # one value for both keys, so they expire together

        await redis.setex(key, ttl, session_data.to_json())

        user_key = f"user_sessions:{session_data.user_id}"
        await redis.sadd(user_key, session_data.session_id)
        await redis.expire(user_key, ttl)

        self._local_cache[session_data.session_id] = session_data
        self._cache_ttl[session_data.session_id] = time.time() + self._cache_duration

        log.debug(
            "Created session %s for user %s",
            session_data.session_id,
            session_data.user_id,
        )
        return session_data.session_id

    async def get_session(self, session_id: str) -> SessionData | None:
        """Get session from local cache or Redis."""
        if session_id in self._local_cache:
            if time.time() < self._cache_ttl.get(session_id, 0):
                return self._local_cache[session_id]
            else:
                del self._local_cache[session_id]
                self._cache_ttl.pop(session_id, None)

        redis = await self._get_redis()
        key = f"{SESSION_PREFIX}{session_id}"
        data = await redis.get(key)

        if not data:
            return None

        session = SessionData.from_json(data)

        self._local_cache[session_id] = session
        self._cache_ttl[session_id] = time.time() + self._cache_duration

        return session

    async def update_session(self, session_id: str, updates: dict[str, Any]) -> bool:
        """Update session fields atomically."""
        session = await self.get_session(session_id)
        if not session:
            return False

        for key, value in updates.items():
            if hasattr(session, key):
                setattr(session, key, value)
        session.last_active = time.time()

        redis = await self._get_redis()
        key = f"{SESSION_PREFIX}{session_id}"
        await redis.setex(key, _session_ttl(), session.to_json())

        self._local_cache[session_id] = session
        self._cache_ttl[session_id] = time.time() + self._cache_duration

        return True

    async def delete_session(self, session_id: str) -> bool:
        """Delete a session."""
        session = await self.get_session(session_id)
        if not session:
            return False

        redis = await self._get_redis()
        key = f"{SESSION_PREFIX}{session_id}"
        await redis.delete(key)

        user_key = f"user_sessions:{session.user_id}"
        await redis.srem(user_key, session_id)

        self._local_cache.pop(session_id, None)
        self._cache_ttl.pop(session_id, None)

        return True

    async def store_investigation(self, state: InvestigationState) -> None:
        """Store investigation state for recovery after pod migration."""
        redis = await self._get_redis()
        key = f"{INVESTIGATION_PREFIX}{state.investigation_id}"

        state.updated_at = time.time()
        ttl = _session_ttl()
        await redis.setex(key, ttl, state.to_json())

        session_key = f"session_investigations:{state.session_id}"
        await redis.sadd(session_key, state.investigation_id)
        await redis.expire(session_key, ttl)

    async def get_investigation(self, investigation_id: str) -> InvestigationState | None:
        """Retrieve investigation state."""
        redis = await self._get_redis()
        key = f"{INVESTIGATION_PREFIX}{investigation_id}"
        data = await redis.get(key)

        if not data:
            return None

        return InvestigationState.from_json(data)

    async def list_user_sessions(self, user_id: str) -> list[str]:
        """List all active sessions for a user."""
        redis = await self._get_redis()
        user_key = f"user_sessions:{user_id}"
        return list(await redis.smembers(user_key))

    def clear_local_cache(self) -> None:
        """Clear local cache (for testing or memory pressure)."""
        self._local_cache.clear()
        self._cache_ttl.clear()


_session_store: DistributedSessionStore | None = None


def get_session_store() -> DistributedSessionStore:
    """Get the singleton session store."""
    global _session_store
    if _session_store is None:
        _session_store = DistributedSessionStore()
    return _session_store


def reset_session_store() -> None:
    """Reset session store (for testing)."""
    global _session_store
    _session_store = None
