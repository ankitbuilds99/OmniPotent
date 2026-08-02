"""WebSocket authentication with full tenant validation.

Validates JWT/API keys for WebSocket connections with:
- Token extraction from subprotocol (secure, not logged)
- Multi-tenant resolution
- Brute-force protection
- Connection-level permissions
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import time
from dataclasses import dataclass
from typing import Any

from fastapi import WebSocket

from app.config import get_settings

log = logging.getLogger("omnipotent.security.websocket_auth")

_FAILED_ATTEMPTS: dict[str, list[float]] = {}
_FAILED_ATTEMPTS_LOCK = asyncio.Lock()
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_WINDOW_SECONDS = 300


@dataclass
class WebSocketAuthResult:
    """Result of WebSocket authentication."""

    authenticated: bool
    tenant_config: Any | None = None
    actor: Any | None = None
    error_code: int = 0
    error_reason: str = ""


async def _check_brute_force(client_ip: str) -> bool:
    """Check if IP is locked out due to too many failed attempts."""
    async with _FAILED_ATTEMPTS_LOCK:
        attempts = _FAILED_ATTEMPTS.get(client_ip, [])
        cutoff = time.time() - LOCKOUT_WINDOW_SECONDS
        attempts = [t for t in attempts if t > cutoff]
        _FAILED_ATTEMPTS[client_ip] = attempts

        if len(attempts) >= MAX_FAILED_ATTEMPTS:
            log.warning("WebSocket auth blocked due to brute-force: ip=%s", client_ip)
            return True
        return False


async def _record_failed_attempt(client_ip: str) -> None:
    """Record a failed authentication attempt."""
    async with _FAILED_ATTEMPTS_LOCK:
        if client_ip not in _FAILED_ATTEMPTS:
            _FAILED_ATTEMPTS[client_ip] = []
        _FAILED_ATTEMPTS[client_ip].append(time.time())
        if len(_FAILED_ATTEMPTS) > 10000:
            oldest_ip = min(
                _FAILED_ATTEMPTS.keys(),
                key=lambda k: _FAILED_ATTEMPTS[k][0] if _FAILED_ATTEMPTS[k] else 0,
            )
            del _FAILED_ATTEMPTS[oldest_ip]


def extract_token_from_subprotocol(websocket: WebSocket) -> str | None:
    """Extract auth token from WebSocket subprotocol.

    Secure token transport - NOT in URL query (which gets logged).
    Client connects with: new WebSocket(url, ["copilot-v1", "auth-<token>"])
    """
    subprotocol_header = websocket.headers.get("sec-websocket-protocol", "")
    if not subprotocol_header:
        return None

    protocols = [p.strip() for p in subprotocol_header.split(",")]
    for proto in protocols:
        if proto.startswith("auth-"):
            token = proto[5:]
            if len(token) < 10 or len(token) > 500:
                return None
            if any(c in token for c in "<>{}[]"):
                log.warning("Suspicious characters in WebSocket auth token")
                return None
            return token
    return None


def extract_token_from_query(websocket: WebSocket) -> str | None:
    """Fallback: extract token from query params (less secure)."""
    token = websocket.query_params.get("token", "")
    if token:
        log.warning("WebSocket auth via query param (less secure than subprotocol)")
        return token
    return None


async def authenticate_websocket(
    websocket: WebSocket,
    require_auth: bool = True,
) -> WebSocketAuthResult:
    """Authenticate a WebSocket connection.

    Args:
        websocket: The WebSocket connection
        require_auth: If True, reject unauthenticated connections

    Returns:
        WebSocketAuthResult with authentication status and tenant info
    """
    settings = get_settings()
    client_ip = websocket.client.host if websocket.client else "unknown"

    if await _check_brute_force(client_ip):
        return WebSocketAuthResult(
            authenticated=False,
            error_code=4003,
            error_reason="Too many failed attempts. Try again later.",
        )

    token = extract_token_from_subprotocol(websocket)
    if not token:
        token = extract_token_from_query(websocket)

    if not token:
        if not require_auth:
            return WebSocketAuthResult(authenticated=True)
        return WebSocketAuthResult(
            authenticated=False,
            error_code=4001,
            error_reason="Authentication required",
        )

    if settings.copilot_multi_tenant:
        from app.tenancy import auth as tenant_auth

        if token.startswith("dcp_"):
            resolved = await tenant_auth.resolve(token)
        elif token.count(".") == 2:
            resolved = await tenant_auth.resolve_jwt(token)
        else:
            await _record_failed_attempt(client_ip)
            return WebSocketAuthResult(
                authenticated=False,
                error_code=4001,
                error_reason="Invalid token format",
            )

        if resolved is None:
            await _record_failed_attempt(client_ip)
            log.warning("WebSocket auth failed: invalid token from ip=%s", client_ip)
            return WebSocketAuthResult(
                authenticated=False,
                error_code=4001,
                error_reason="Invalid or revoked credential",
            )

        cfg, actor = resolved
        return WebSocketAuthResult(
            authenticated=True,
            tenant_config=cfg,
            actor=actor,
        )

    expected = settings.copilot_api_token
    if expected and not hmac.compare_digest(token, expected):
        await _record_failed_attempt(client_ip)
        return WebSocketAuthResult(
            authenticated=False,
            error_code=4001,
            error_reason="Invalid API token",
        )

    return WebSocketAuthResult(authenticated=True)


async def validate_websocket_message(
    message: dict,
    tenant_config: Any | None,
) -> tuple[bool, str]:
    """Validate an inbound WebSocket message.

    Returns:
        Tuple of (is_valid, error_message)
    """
    settings = get_settings()
    msg_type = message.get("type", "")

    if tenant_config:
        from app.tenancy.models import can

        if msg_type == "chat" and not can(tenant_config.role, "run_investigation"):
            return False, "Insufficient permissions to run investigations"
        if msg_type == "approval" and not can(tenant_config.role, "approve_action"):
            return False, "Insufficient permissions to approve actions"

    if msg_type == "chat":
        message_text = message.get("message", "")
        if not message_text or not message_text.strip():
            return False, "Message cannot be empty"
        max_chars = getattr(settings, "copilot_max_message_chars", 50000)
        if len(message_text) > max_chars:
            return False, "Message too long"

    return True, ""


def get_accepted_subprotocol(websocket: WebSocket) -> str:
    """Get the subprotocol to accept for the connection."""
    subprotocol_header = websocket.headers.get("sec-websocket-protocol", "")
    protocols = [p.strip() for p in subprotocol_header.split(",") if p.strip()]

    if "copilot-v1" in protocols:
        return "copilot-v1"
    for proto in protocols:
        if not proto.startswith("auth-"):
            return proto
    return ""
