"""Independent mutation kill switch.

This control is deliberately outside the LLM/session path. Deployments may
install a shared Redis-compatible backend; the in-process registry remains the
deterministic fallback for development and single-replica mode.
"""

from __future__ import annotations

from threading import RLock
from typing import Any

_lock = RLock()
_global_frozen = False
_tenants: set[str] = set()
_backend: Any = None


def configure_backend(backend: Any | None) -> None:
    """Install a Redis-compatible backend for multi-replica freeze state."""
    global _backend
    with _lock:
        _backend = backend


def _key(scope: str) -> str:
    return f"omnipotent:mutation-freeze:{scope}"


def freeze(*, tenant_id: str | None = None) -> None:
    global _global_frozen
    with _lock:
        if _backend is not None:
            if tenant_id:
                _backend.sadd(_key("tenants"), tenant_id)
            else:
                _backend.set(_key("global"), "1")
        if tenant_id:
            _tenants.add(tenant_id)
        else:
            _global_frozen = True


def unfreeze(*, tenant_id: str | None = None) -> None:
    global _global_frozen
    with _lock:
        if _backend is not None:
            if tenant_id:
                _backend.srem(_key("tenants"), tenant_id)
            else:
                _backend.delete(_key("global"))
        if tenant_id:
            _tenants.discard(tenant_id)
        else:
            _global_frozen = False


def is_frozen(tenant_id: str) -> bool:
    with _lock:
        shared = False
        if _backend is not None:
            shared = bool(_backend.exists(_key("global")) or _backend.sismember(_key("tenants"), tenant_id))
        return _global_frozen or tenant_id in _tenants or shared


def reset() -> None:
    """Test/support reset; production callers should use explicit unfreeze."""
    global _global_frozen, _backend
    with _lock:
        _backend = None
        _global_frozen = False
        _tenants.clear()
