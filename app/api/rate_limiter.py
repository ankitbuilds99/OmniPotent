"""Rate limiting middleware and utilities."""

from __future__ import annotations

import time
import hashlib
import secrets
from dataclasses import dataclass, field
from typing import Callable
from datetime import datetime, timedelta

from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from app.config import get_settings


@dataclass
class RateLimitBucket:
    """Token bucket for rate limiting."""
    tokens: float
    last_update: float
    max_tokens: int
    refill_rate: float  # tokens per second

    def consume(self, tokens: int = 1) -> bool:
        """Try to consume tokens. Returns True if allowed."""
        now = time.time()
        elapsed = now - self.last_update
        self.tokens = min(self.max_tokens, self.tokens + elapsed * self.refill_rate)
        self.last_update = now

        if self.tokens >= tokens:
            self.tokens -= tokens
            return True
        return False

    @property
    def retry_after(self) -> int:
        """Seconds until a token is available."""
        if self.tokens >= 1:
            return 0
        return int((1 - self.tokens) / self.refill_rate) + 1


class RateLimiter:
    """In-memory rate limiter with multiple tiers."""

    def __init__(self) -> None:
        self._buckets: dict[str, RateLimitBucket] = {}
        self._blocked: dict[str, float] = {}  # IP -> block until timestamp

    def _get_bucket(
        self, key: str, max_requests: int, window_seconds: int
    ) -> RateLimitBucket:
        """Get or create a rate limit bucket."""
        if key not in self._buckets:
            self._buckets[key] = RateLimitBucket(
                tokens=max_requests,
                last_update=time.time(),
                max_tokens=max_requests,
                refill_rate=max_requests / window_seconds,
            )
        return self._buckets[key]

    def is_blocked(self, key: str) -> bool:
        """Check if a key is temporarily blocked."""
        if key in self._blocked:
            if time.time() < self._blocked[key]:
                return True
            del self._blocked[key]
        return False

    def block(self, key: str, seconds: int) -> None:
        """Block a key for a period."""
        self._blocked[key] = time.time() + seconds

    def check(
        self,
        key: str,
        max_requests: int = 60,
        window_seconds: int = 60,
        tokens: int = 1,
    ) -> tuple[bool, int]:
        """Check if request is allowed. Returns (allowed, retry_after)."""
        if self.is_blocked(key):
            return False, 60

        bucket = self._get_bucket(key, max_requests, window_seconds)
        allowed = bucket.consume(tokens)
        return allowed, bucket.retry_after

    def cleanup(self, max_age: int = 3600) -> int:
        """Remove stale buckets. Returns count removed."""
        cutoff = time.time() - max_age
        stale = [k for k, v in self._buckets.items() if v.last_update < cutoff]
        for k in stale:
            del self._buckets[k]

        # Also clean up expired blocks
        expired = [k for k, v in self._blocked.items() if v < time.time()]
        for k in expired:
            del self._blocked[k]

        return len(stale)


# Global limiter instance
_limiter: RateLimiter | None = None


def get_rate_limiter() -> RateLimiter:
    """Get the global rate limiter."""
    global _limiter
    if _limiter is None:
        _limiter = RateLimiter()
    return _limiter


class RateLimitMiddleware(BaseHTTPMiddleware):
    """FastAPI middleware for rate limiting."""

    def __init__(
        self,
        app,
        requests_per_minute: int = 60,
        burst_limit: int = 10,
        exclude_paths: list[str] | None = None,
    ):
        super().__init__(app)
        self.requests_per_minute = requests_per_minute
        self.burst_limit = burst_limit
        self.exclude_paths = exclude_paths or ["/healthz", "/metrics", "/favicon.ico"]
        self.limiter = get_rate_limiter()

    def _get_client_key(self, request: Request) -> str:
        """Get rate limit key for client."""
        s = get_settings()

        # Check for API key first
        api_key = request.headers.get("X-API-Key")
        if api_key:
            return f"api:{hashlib.sha256(api_key.encode()).hexdigest()[:16]}"

        # Use IP address
        if s.copilot_trust_proxy:
            forwarded = request.headers.get("X-Forwarded-For")
            if forwarded:
                return f"ip:{forwarded.split(',')[0].strip()}"

        client = request.client
        if client:
            return f"ip:{client.host}"
        return "ip:unknown"

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Skip rate limiting for excluded paths
        if request.url.path in self.exclude_paths:
            return await call_next(request)

        # Skip for OPTIONS (CORS preflight)
        if request.method == "OPTIONS":
            return await call_next(request)

        client_key = self._get_client_key(request)

        # Check rate limit
        allowed, retry_after = self.limiter.check(
            client_key,
            max_requests=self.requests_per_minute,
            window_seconds=60,
        )

        if not allowed:
            return Response(
                content='{"error": "Rate limit exceeded", "retry_after": ' + str(retry_after) + '}',
                status_code=429,
                headers={
                    "Retry-After": str(retry_after),
                    "X-RateLimit-Limit": str(self.requests_per_minute),
                    "X-RateLimit-Remaining": "0",
                    "Content-Type": "application/json",
                },
            )

        # Add rate limit headers
        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(self.requests_per_minute)
        return response


# API Key management
@dataclass
class APIKey:
    """API key with metadata."""
    key_id: str
    key_hash: str
    name: str
    owner_id: str
    created_at: datetime
    expires_at: datetime | None = None
    rate_limit: int = 1000  # requests per minute
    scopes: list[str] = field(default_factory=lambda: ["read", "write"])
    last_used: datetime | None = None
    is_active: bool = True


class APIKeyManager:
    """Manage API keys for programmatic access."""

    def __init__(self) -> None:
        self._keys: dict[str, APIKey] = {}  # key_hash -> APIKey
        self._key_ids: dict[str, str] = {}  # key_id -> key_hash

    def create_key(
        self,
        name: str,
        owner_id: str,
        rate_limit: int = 1000,
        scopes: list[str] | None = None,
        expires_in_days: int | None = None,
    ) -> tuple[str, APIKey]:
        """Create a new API key. Returns (raw_key, api_key_object)."""
        # Generate secure random key
        raw_key = f"dcp_{secrets.token_urlsafe(32)}"
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        key_id = secrets.token_hex(8)

        expires_at = None
        if expires_in_days:
            expires_at = datetime.utcnow() + timedelta(days=expires_in_days)

        api_key = APIKey(
            key_id=key_id,
            key_hash=key_hash,
            name=name,
            owner_id=owner_id,
            created_at=datetime.utcnow(),
            expires_at=expires_at,
            rate_limit=rate_limit,
            scopes=scopes or ["read", "write"],
        )

        self._keys[key_hash] = api_key
        self._key_ids[key_id] = key_hash

        return raw_key, api_key

    def validate_key(self, raw_key: str) -> APIKey | None:
        """Validate an API key and return its metadata."""
        if not raw_key.startswith("dcp_"):
            return None

        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        api_key = self._keys.get(key_hash)

        if not api_key:
            return None

        if not api_key.is_active:
            return None

        if api_key.expires_at and api_key.expires_at < datetime.utcnow():
            return None

        # Update last used
        api_key.last_used = datetime.utcnow()
        return api_key

    def revoke_key(self, key_id: str) -> bool:
        """Revoke an API key."""
        key_hash = self._key_ids.get(key_id)
        if not key_hash:
            return False

        api_key = self._keys.get(key_hash)
        if api_key:
            api_key.is_active = False
            return True
        return False

    def list_keys(self, owner_id: str) -> list[dict]:
        """List all API keys for an owner."""
        return [
            {
                "key_id": k.key_id,
                "name": k.name,
                "created_at": k.created_at.isoformat(),
                "expires_at": k.expires_at.isoformat() if k.expires_at else None,
                "rate_limit": k.rate_limit,
                "scopes": k.scopes,
                "last_used": k.last_used.isoformat() if k.last_used else None,
                "is_active": k.is_active,
            }
            for k in self._keys.values()
            if k.owner_id == owner_id
        ]

    def get_key_by_id(self, key_id: str) -> APIKey | None:
        """Get API key by ID."""
        key_hash = self._key_ids.get(key_id)
        if key_hash:
            return self._keys.get(key_hash)
        return None


# Global API key manager
_key_manager: APIKeyManager | None = None


def get_api_key_manager() -> APIKeyManager:
    """Get the global API key manager."""
    global _key_manager
    if _key_manager is None:
        _key_manager = APIKeyManager()
    return _key_manager


# Dependency for FastAPI
async def require_api_key(request: Request) -> APIKey:
    """FastAPI dependency to require valid API key."""
    api_key = request.headers.get("X-API-Key")
    if not api_key:
        raise HTTPException(status_code=401, detail="API key required")

    manager = get_api_key_manager()
    key = manager.validate_key(api_key)
    if not key:
        raise HTTPException(status_code=401, detail="Invalid or expired API key")

    return key


async def optional_api_key(request: Request) -> APIKey | None:
    """FastAPI dependency for optional API key."""
    api_key = request.headers.get("X-API-Key")
    if not api_key:
        return None

    manager = get_api_key_manager()
    return manager.validate_key(api_key)
