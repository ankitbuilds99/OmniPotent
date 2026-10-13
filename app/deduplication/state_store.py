"""Shared state for restart-safe alert deduplication.

The correlation engines are intentionally synchronous, so this adapter uses the
standard synchronous Redis client.  Redis is optional for local development;
when configured, snapshots are written atomically and a new worker can rebuild
the in-memory indexes before accepting alerts.
"""

from __future__ import annotations

import json
import logging
from contextlib import nullcontext
from typing import Any, Protocol

log = logging.getLogger("omnipotent.deduplication.state")


class DedupStateStore(Protocol):
    def load(self) -> dict[str, Any] | None: ...
    def save(self, state: dict[str, Any]) -> None: ...
    def lock(self): ...


class RedisDedupStateStore:
    """Atomic JSON snapshot store backed by a shared Redis key."""

    def __init__(self, url: str, key: str = "omnipotent:dedup:v1") -> None:
        if not url.strip():
            raise ValueError("a Redis URL is required for shared deduplication")
        try:
            import redis
        except ImportError as exc:  # pragma: no cover - dependency is optional
            raise RuntimeError("redis package is required for shared deduplication") from exc
        self._client = redis.Redis.from_url(url, decode_responses=True)
        self._key = key

    def load(self) -> dict[str, Any] | None:
        raw = self._client.get(self._key)
        if not raw:
            return None
        try:
            value = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("deduplication state is not valid JSON") from exc
        if not isinstance(value, dict):
            raise RuntimeError("deduplication state must be a JSON object")
        return value

    def save(self, state: dict[str, Any]) -> None:
        payload = json.dumps(state, separators=(",", ":"), sort_keys=True)
        # SET is atomic; readers see either the old complete snapshot or the new
        # complete snapshot, never a partially written index.
        self._client.set(self._key, payload)

    def lock(self):
        """Serialize read/decide/write transactions for this tenant key."""
        # Snapshot writes are proportional to the retained alert window. Give a
        # recovering worker enough time to wait for a predecessor, while keeping
        # the lease finite so a crashed worker cannot wedge a tenant forever.
        return self._client.lock(
            f"{self._key}:lock", timeout=120, blocking_timeout=60
        )


def local_lock():
    """Context manager used by process-local stores and tests."""
    return nullcontext()

