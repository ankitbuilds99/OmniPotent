"""Request signing for webhook and API integrity.

Provides:
- HMAC-SHA256 request signing
- Timestamp-based replay protection
- Request body integrity verification
- Idempotency key handling
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from dataclasses import dataclass
from typing import Any

log = logging.getLogger("devcopilot.security.request_signing")

REPLAY_WINDOW = 300
_SEEN_SIGNATURES: dict[str, float] = {}
_MAX_SEEN_SIZE = 100000


@dataclass
class SignatureValidation:
    """Result of signature validation."""

    valid: bool
    error: str = ""
    timestamp: int = 0


def compute_signature(
    payload: bytes,
    secret: str,
    timestamp: int | None = None,
    algorithm: str = "sha256",
) -> tuple[str, int]:
    """Compute HMAC signature for a payload."""
    ts = timestamp or int(time.time())
    signed_payload = f"{ts}.".encode() + payload

    if algorithm == "sha512":
        sig = hmac.new(secret.encode(), signed_payload, hashlib.sha512).hexdigest()
    else:
        sig = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()

    return sig, ts


def validate_signature(
    payload: bytes,
    signature: str,
    timestamp: int,
    secret: str,
    algorithm: str = "sha256",
    max_age: int = REPLAY_WINDOW,
) -> SignatureValidation:
    """Validate a signed request."""
    now = int(time.time())

    if abs(now - timestamp) > max_age:
        return SignatureValidation(
            valid=False,
            error=f"Request timestamp too old or in future (age: {now - timestamp}s)",
            timestamp=timestamp,
        )

    expected_sig, _ = compute_signature(payload, secret, timestamp, algorithm)

    if not hmac.compare_digest(signature, expected_sig):
        return SignatureValidation(
            valid=False,
            error="Signature mismatch",
            timestamp=timestamp,
        )

    sig_key = f"{timestamp}:{signature[:32]}"
    if sig_key in _SEEN_SIGNATURES:
        return SignatureValidation(
            valid=False,
            error="Duplicate request (replay detected)",
            timestamp=timestamp,
        )

    _SEEN_SIGNATURES[sig_key] = time.time()

    if len(_SEEN_SIGNATURES) > _MAX_SEEN_SIZE:
        cutoff = time.time() - max_age
        to_delete = [k for k, v in _SEEN_SIGNATURES.items() if v < cutoff]
        for k in to_delete:
            del _SEEN_SIGNATURES[k]

    return SignatureValidation(valid=True, timestamp=timestamp)


def parse_signature_header(
    header: str,
    header_format: str = "stripe",
) -> dict[str, Any]:
    """Parse a signature header."""
    result: dict[str, Any] = {"timestamp": 0, "signature": "", "version": ""}

    if header_format == "stripe":
        parts = dict(p.split("=", 1) for p in header.split(",") if "=" in p)
        result["timestamp"] = int(parts.get("t", 0))
        result["signature"] = parts.get("v1", "")
        result["version"] = "v1"

    elif header_format == "github":
        if header.startswith("sha256="):
            result["signature"] = header[7:]
            result["version"] = "sha256"
        elif header.startswith("sha1="):
            result["signature"] = header[5:]
            result["version"] = "sha1"

    elif header_format == "slack":
        slack_parts = header.split(":")
        if len(slack_parts) == 3:
            result["version"] = slack_parts[0]
            result["timestamp"] = int(slack_parts[1])
            result["signature"] = slack_parts[2]

    elif header_format == "custom":
        if "." in header:
            ts, sig = header.split(".", 1)
            result["timestamp"] = int(ts)
            result["signature"] = sig

    return result


class IdempotencyTracker:
    """Track idempotency keys to prevent duplicate processing."""

    def __init__(self, ttl_seconds: int = 86400):
        self._keys: dict[str, tuple[float, Any]] = {}
        self._ttl = ttl_seconds

    def check_and_set(
        self,
        key: str,
        result: Any = None,
    ) -> tuple[bool, Any]:
        """Check if key was seen; if not, record it."""
        self._prune()

        if key in self._keys:
            _, cached = self._keys[key]
            return True, cached

        self._keys[key] = (time.time(), result)
        return False, None

    def update_result(self, key: str, result: Any) -> None:
        """Update the cached result for a key."""
        if key in self._keys:
            ts, _ = self._keys[key]
            self._keys[key] = (ts, result)

    def _prune(self) -> None:
        """Remove expired keys."""
        if len(self._keys) > 50000:
            cutoff = time.time() - self._ttl
            self._keys = {k: v for k, v in self._keys.items() if v[0] > cutoff}


_idempotency = IdempotencyTracker()


def check_idempotency(key: str) -> tuple[bool, Any]:
    """Check if a request with this idempotency key was already processed."""
    return _idempotency.check_and_set(key)


def record_idempotency_result(key: str, result: Any) -> None:
    """Record the result of processing an idempotent request."""
    _idempotency.update_result(key, result)
