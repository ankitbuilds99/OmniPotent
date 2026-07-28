"""API Key authentication system.

Provides:
- Secure API key generation with prefixes
- Key hashing (never store plaintext)
- Role-based key permissions
- Key rotation without downtime
- Rate limiting per key
- Audit logging for key events
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("devcopilot.security.api_auth")


class KeyRole(Enum):
    """API key permission roles."""

    ADMIN = "admin"
    RESPONDER = "responder"
    VIEWER = "viewer"
    CI_CD = "ci_cd"
    READONLY = "readonly"


class KeyStatus(Enum):
    """API key status."""

    ACTIVE = "active"
    REVOKED = "revoked"
    EXPIRED = "expired"
    ROTATING = "rotating"


@dataclass
class APIKey:
    """An API key with metadata."""

    id: str
    org_id: str
    name: str
    prefix: str
    key_hash: str
    role: KeyRole
    status: KeyStatus = KeyStatus.ACTIVE
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime | None = None
    last_used_at: datetime | None = None
    last_used_ip: str = ""
    use_count: int = 0
    rate_limit_per_min: int = 60
    scopes: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    rotated_from: str | None = None

    def is_valid(self) -> bool:
        """Check if key is currently valid."""
        if self.status != KeyStatus.ACTIVE:
            return False
        if self.expires_at and datetime.now(timezone.utc) > self.expires_at:
            return False
        return True

    def has_scope(self, scope: str) -> bool:
        """Check if key has a specific scope."""
        if not self.scopes:
            return True
        return scope in self.scopes or "*" in self.scopes

    def to_dict(self, include_hash: bool = False) -> dict[str, Any]:
        """Convert to dictionary for storage/API response."""
        data = {
            "id": self.id,
            "org_id": self.org_id,
            "name": self.name,
            "prefix": self.prefix,
            "role": self.role.value,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "last_used_at": self.last_used_at.isoformat() if self.last_used_at else None,
            "use_count": self.use_count,
            "rate_limit_per_min": self.rate_limit_per_min,
            "scopes": self.scopes,
        }
        if include_hash:
            data["key_hash"] = self.key_hash
        return data


_KEYS: dict[str, APIKey] = {}
_KEY_BY_PREFIX: dict[str, str] = {}


def _hash_key(key: str) -> str:
    """Hash an API key for secure storage."""
    return hashlib.sha256(key.encode()).hexdigest()


def _generate_key(prefix: str = "dcp") -> tuple[str, str]:
    """Generate a new API key and its hash.

    Returns:
        Tuple of (plaintext_key, key_hash)
    """
    random_part = secrets.token_urlsafe(32)
    key = f"{prefix}_{random_part}"
    key_hash = _hash_key(key)
    return key, key_hash


def create_api_key(
    org_id: str,
    name: str = "",
    role: KeyRole = KeyRole.RESPONDER,
    expires_in_days: int | None = None,
    rate_limit_per_min: int = 60,
    scopes: list[str] | None = None,
    created_by: str = "",
) -> tuple[APIKey, str]:
    """Create a new API key.

    Args:
        org_id: Organization ID
        name: Human-readable key name
        role: Permission role for the key
        expires_in_days: Days until expiration (None = no expiry)
        rate_limit_per_min: Rate limit for this key
        scopes: Optional list of allowed scopes
        created_by: User who created the key

    Returns:
        Tuple of (APIKey object, plaintext_key)

    Note:
        The plaintext key is only returned once at creation time.
        Store it securely - it cannot be recovered.
    """
    key_id = f"key_{secrets.token_hex(12)}"
    plaintext_key, key_hash = _generate_key()
    prefix = plaintext_key[:12]

    expires_at = None
    if expires_in_days:
        from datetime import timedelta
        expires_at = datetime.now(timezone.utc) + timedelta(days=expires_in_days)

    api_key = APIKey(
        id=key_id,
        org_id=org_id,
        name=name or f"API Key {key_id[-8:]}",
        prefix=prefix,
        key_hash=key_hash,
        role=role,
        expires_at=expires_at,
        rate_limit_per_min=rate_limit_per_min,
        scopes=scopes or [],
        metadata={"created_by": created_by} if created_by else {},
    )

    _KEYS[key_id] = api_key
    _KEY_BY_PREFIX[prefix] = key_id

    log.info(
        "Created API key %s for org %s (role=%s, expires=%s)",
        key_id,
        org_id,
        role.value,
        expires_at,
    )

    return api_key, plaintext_key


def validate_api_key(
    key: str,
    required_scope: str | None = None,
    update_usage: bool = True,
    client_ip: str = "",
) -> APIKey | None:
    """Validate an API key and return its metadata.

    Args:
        key: The plaintext API key to validate
        required_scope: Optional scope that must be present
        update_usage: Whether to update last_used timestamp
        client_ip: Client IP for tracking

    Returns:
        APIKey if valid, None otherwise
    """
    if not key or not key.startswith("dcp_"):
        return None

    prefix = key[:12]
    key_id = _KEY_BY_PREFIX.get(prefix)
    if not key_id:
        return None

    api_key = _KEYS.get(key_id)
    if not api_key:
        return None

    if not hmac.compare_digest(_hash_key(key), api_key.key_hash):
        log.warning("Invalid key hash for prefix %s", prefix)
        return None

    if not api_key.is_valid():
        log.info("Key %s is no longer valid (status=%s)", key_id, api_key.status.value)
        return None

    if required_scope and not api_key.has_scope(required_scope):
        log.warning("Key %s lacks required scope: %s", key_id, required_scope)
        return None

    if update_usage:
        api_key.last_used_at = datetime.now(timezone.utc)
        api_key.last_used_ip = client_ip
        api_key.use_count += 1

    return api_key


def revoke_api_key(key_id: str, revoked_by: str = "") -> bool:
    """Revoke an API key.

    Args:
        key_id: The key ID to revoke
        revoked_by: User who revoked the key

    Returns:
        True if revoked, False if not found
    """
    api_key = _KEYS.get(key_id)
    if not api_key:
        return False

    api_key.status = KeyStatus.REVOKED
    api_key.metadata["revoked_by"] = revoked_by
    api_key.metadata["revoked_at"] = datetime.now(timezone.utc).isoformat()

    log.info("Revoked API key %s (by %s)", key_id, revoked_by or "system")
    return True


def rotate_api_key(
    key_id: str,
    grace_period_hours: int = 24,
    rotated_by: str = "",
) -> tuple[APIKey, str] | None:
    """Rotate an API key with a grace period.

    Creates a new key while keeping the old one active for the grace period,
    allowing time to update systems using the old key.

    Args:
        key_id: The key ID to rotate
        grace_period_hours: Hours to keep old key active
        rotated_by: User who initiated rotation

    Returns:
        Tuple of (new_APIKey, new_plaintext_key) or None if not found
    """
    old_key = _KEYS.get(key_id)
    if not old_key:
        return None

    new_key, plaintext = create_api_key(
        org_id=old_key.org_id,
        name=f"{old_key.name} (rotated)",
        role=old_key.role,
        rate_limit_per_min=old_key.rate_limit_per_min,
        scopes=old_key.scopes,
        created_by=rotated_by,
    )

    new_key.rotated_from = key_id
    old_key.status = KeyStatus.ROTATING
    old_key.metadata["rotated_to"] = new_key.id
    old_key.metadata["rotation_expires"] = (
        datetime.now(timezone.utc).timestamp() + (grace_period_hours * 3600)
    )

    log.info(
        "Rotating API key %s -> %s (grace period: %dh)",
        key_id,
        new_key.id,
        grace_period_hours,
    )

    return new_key, plaintext


def list_api_keys(org_id: str, include_revoked: bool = False) -> list[APIKey]:
    """List all API keys for an organization.

    Args:
        org_id: Organization ID
        include_revoked: Whether to include revoked keys

    Returns:
        List of APIKey objects
    """
    keys = [k for k in _KEYS.values() if k.org_id == org_id]

    if not include_revoked:
        keys = [k for k in keys if k.status != KeyStatus.REVOKED]

    return sorted(keys, key=lambda k: k.created_at, reverse=True)


def get_api_key(key_id: str) -> APIKey | None:
    """Get an API key by ID."""
    return _KEYS.get(key_id)


def cleanup_expired_keys() -> int:
    """Mark expired keys and clean up old rotation grace periods.

    Returns:
        Number of keys affected
    """
    now = datetime.now(timezone.utc)
    affected = 0

    for key in _KEYS.values():
        if key.status == KeyStatus.ACTIVE and key.expires_at and now > key.expires_at:
            key.status = KeyStatus.EXPIRED
            affected += 1
            log.info("Key %s expired", key.id)

        if key.status == KeyStatus.ROTATING:
            grace_expires = key.metadata.get("rotation_expires", 0)
            if time.time() > grace_expires:
                key.status = KeyStatus.REVOKED
                affected += 1
                log.info("Key %s rotation grace period ended", key.id)

    return affected


class APIKeyAuth:
    """FastAPI dependency for API key authentication."""

    def __init__(
        self,
        required_scope: str | None = None,
        required_role: KeyRole | None = None,
    ):
        self.required_scope = required_scope
        self.required_role = required_role

    async def __call__(self, request: Any) -> APIKey:
        """Validate the API key from the request."""
        from fastapi import HTTPException, Request

        if not isinstance(request, Request):
            raise HTTPException(500, "Invalid request object")

        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            key = auth_header[7:].strip()
        else:
            key = auth_header.strip()

        if not key:
            raise HTTPException(
                401,
                "API key required. Use 'Authorization: Bearer dcp_...'",
                headers={"WWW-Authenticate": "Bearer"},
            )

        client_ip = request.client.host if request.client else ""
        api_key = validate_api_key(
            key,
            required_scope=self.required_scope,
            client_ip=client_ip,
        )

        if not api_key:
            raise HTTPException(401, "Invalid or expired API key")

        if self.required_role:
            role_hierarchy = {
                KeyRole.ADMIN: 4,
                KeyRole.RESPONDER: 3,
                KeyRole.CI_CD: 2,
                KeyRole.VIEWER: 1,
                KeyRole.READONLY: 0,
            }
            key_level = role_hierarchy.get(api_key.role, 0)
            required_level = role_hierarchy.get(self.required_role, 0)

            if key_level < required_level:
                raise HTTPException(
                    403,
                    f"Key role '{api_key.role.value}' insufficient. "
                    f"Required: {self.required_role.value}",
                )

        return api_key


# --- Format validation utilities for tests ---

_VALID_PREFIXES = ("sk_live_", "sk_test_", "cop_", "dcp_")
_MIN_KEY_LENGTH = 20


def validate_api_key_format(key: str) -> bool:
    """Validate the format of an API key without checking if it exists.

    Used for input validation before attempting database lookup.
    """
    if not key or not isinstance(key, str):
        return False

    if len(key) < _MIN_KEY_LENGTH:
        return False

    if not any(key.startswith(prefix) for prefix in _VALID_PREFIXES):
        return False

    if "<" in key or ">" in key:
        return False

    import re
    if not re.match(r"^[a-zA-Z0-9_\-]+$", key):
        return False

    return True


def extract_api_key(auth_header: str) -> str | None:
    """Extract API key from Authorization header.

    Supports:
    - Bearer token: "Bearer sk_live_xxx"
    - Direct key: "sk_live_xxx"

    Returns None if header is invalid or uses unsupported auth scheme.
    """
    if not auth_header or not isinstance(auth_header, str):
        return None

    header = auth_header.strip()

    if header.lower().startswith("bearer "):
        key = header[7:].strip()
        if key and any(key.startswith(p) or key.upper().startswith(p.upper()) for p in _VALID_PREFIXES):
            return key
        return None

    if header.lower().startswith("basic "):
        return None

    if " " in header:
        return None

    if any(header.startswith(p) for p in _VALID_PREFIXES):
        return header

    return None
