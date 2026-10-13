"""Small cross-replica control channel for planning executions.

Redis is optional. With Redis configured, an API request can reach the worker
that owns an execution; without it, the in-process fallback preserves local
development behavior.
"""

from __future__ import annotations

from threading import RLock
from typing import Any

_lock = RLock()
_backend: Any = None
_local: dict[str, str] = {}


def configure_backend(backend: Any | None) -> None:
    global _backend
    with _lock:
        _backend = backend


def _key(execution_id: str) -> str:
    return f"omnipotent:execution-control:{execution_id}"


def request(execution_id: str, action: str) -> None:
    if action not in {"cancel", "pause"}:
        raise ValueError("unsupported execution control action")
    with _lock:
        if _backend is not None:
            _backend.set(_key(execution_id), action)
        _local[execution_id] = action


def requested(execution_id: str) -> str | None:
    with _lock:
        if _backend is not None:
            value = _backend.get(_key(execution_id))
            if value:
                return str(value)
        return _local.get(execution_id)


def clear(execution_id: str) -> None:
    with _lock:
        if _backend is not None:
            _backend.delete(_key(execution_id))
        _local.pop(execution_id, None)


def reset() -> None:
    global _backend
    with _lock:
        _backend = None
        _local.clear()
