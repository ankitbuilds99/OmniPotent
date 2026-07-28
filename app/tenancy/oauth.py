"""OAuth and authentication utilities.

Provides:
- OAuth state token generation and validation (CSRF protection)
- Session token management
- Callback parameter validation
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
from typing import Any

log = logging.getLogger("devcopilot.tenancy.oauth")

STATE_EXPIRY_SECONDS = 600
SESSION_SECRET = os.environ.get("SESSION_SECRET", secrets.token_hex(32))


class OAuthError(Exception):
    """OAuth-related error."""

    def __init__(self, message: str, code: str = "oauth_error"):
        super().__init__(message)
        self.code = code


def _sign(data: str) -> str:
    """Create HMAC signature for data."""
    return hmac.new(
        SESSION_SECRET.encode(),
        data.encode(),
        hashlib.sha256
    ).hexdigest()[:16]


def generate_oauth_state(
    provider: str,
    redirect_uri: str = "",
    extra: dict[str, Any] | None = None,
) -> str:
    """Generate a signed, time-limited OAuth state token.

    Args:
        provider: OAuth provider name (e.g., "github", "slack")
        redirect_uri: Post-auth redirect destination
        extra: Additional data to include in state

    Returns:
        URL-safe state token string
    """
    nonce = secrets.token_urlsafe(16)
    timestamp = int(time.time())

    payload = {
        "provider": provider,
        "redirect_uri": redirect_uri,
        "nonce": nonce,
        "ts": timestamp,
        **(extra or {}),
    }

    payload_json = json.dumps(payload, separators=(",", ":"))
    payload_b64 = base64.urlsafe_b64encode(payload_json.encode()).decode()
    signature = _sign(payload_b64)

    return f"{payload_b64}.{signature}"


def _parse_state(state: str) -> dict[str, Any] | None:
    """Parse state token without validating signature or expiry."""
    if not state or "." not in state:
        return None

    try:
        payload_b64, _ = state.rsplit(".", 1)
        payload_json = base64.urlsafe_b64decode(payload_b64).decode()
        return json.loads(payload_json)
    except Exception:
        return None


def validate_oauth_state(state: str | None) -> tuple[bool, dict[str, Any] | None]:
    """Validate an OAuth state token.

    Checks:
    - Token is properly formatted
    - Signature is valid (HMAC)
    - Token hasn't expired

    Returns:
        Tuple of (is_valid, parsed_payload_or_None)
    """
    if not state or not isinstance(state, str):
        return False, None

    if "." not in state:
        return False, None

    try:
        payload_b64, signature = state.rsplit(".", 1)

        expected_sig = _sign(payload_b64)
        if not hmac.compare_digest(signature, expected_sig):
            log.warning("State token signature mismatch")
            return False, None

        payload_json = base64.urlsafe_b64decode(payload_b64).decode()
        payload = json.loads(payload_json)

        timestamp = payload.get("ts", 0)
        if time.time() - timestamp > STATE_EXPIRY_SECONDS:
            log.info("State token expired (age: %ds)", time.time() - timestamp)
            return False, None

        return True, payload

    except Exception as e:
        log.warning("Failed to validate state token: %s", e)
        return False, None


_DANGEROUS_PATTERNS = [
    r"<script",
    r"javascript:",
    r"data:",
    r"onclick",
    r"onerror",
    r"onload",
    r"'.*--",
    r";\s*DROP",
    r"UNION\s+SELECT",
    r"OR\s+1\s*=\s*1",
]


def _contains_injection(value: str) -> bool:
    """Check if value contains common injection patterns."""
    lower = value.lower()
    for pattern in _DANGEROUS_PATTERNS:
        if re.search(pattern, lower, re.IGNORECASE):
            return True
    return False


def validate_callback_params(state: str | None, code: str | None) -> None:
    """Validate OAuth callback parameters.

    Raises OAuthError if parameters are invalid or suspicious.
    """
    if not state:
        raise OAuthError("Missing state parameter", "missing_state")

    if not code:
        raise OAuthError("Missing authorization code", "missing_code")

    if _contains_injection(state):
        raise OAuthError("Invalid state parameter", "invalid_state")

    if _contains_injection(code):
        raise OAuthError("Invalid authorization code", "invalid_code")


def create_session_token(
    user_id: str,
    org_id: str,
    roles: list[str] | None = None,
    expiry_hours: int = 24,
    extra: dict[str, Any] | None = None,
) -> str:
    """Create a signed session token (JWT-like but simpler).

    Args:
        user_id: User identifier
        org_id: Organization identifier
        roles: User roles/permissions
        expiry_hours: Hours until expiration
        extra: Additional claims

    Returns:
        Signed token string
    """
    timestamp = int(time.time())
    expires = timestamp + (expiry_hours * 3600)

    payload = {
        "user_id": user_id,
        "org_id": org_id,
        "roles": roles or [],
        "iat": timestamp,
        "exp": expires,
        **(extra or {}),
    }

    payload_json = json.dumps(payload, separators=(",", ":"))
    payload_b64 = base64.urlsafe_b64encode(payload_json.encode()).decode()

    header = base64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').decode()
    signature = _sign(f"{header}.{payload_b64}")
    signature_b64 = base64.urlsafe_b64encode(signature.encode()).decode()

    return f"{header}.{payload_b64}.{signature_b64}"


def verify_session_token(token: str) -> dict[str, Any] | None:
    """Verify and decode a session token.

    Returns:
        Payload dict if valid, None otherwise
    """
    if not token or not isinstance(token, str):
        return None

    parts = token.split(".")
    if len(parts) != 3:
        return None

    header, payload_b64, signature_b64 = parts

    try:
        expected_sig = _sign(f"{header}.{payload_b64}")
        actual_sig = base64.urlsafe_b64decode(signature_b64).decode()

        if not hmac.compare_digest(expected_sig, actual_sig):
            return None

        payload_json = base64.urlsafe_b64decode(payload_b64).decode()
        payload = json.loads(payload_json)

        if time.time() > payload.get("exp", 0):
            return None

        return payload

    except Exception:
        return None
