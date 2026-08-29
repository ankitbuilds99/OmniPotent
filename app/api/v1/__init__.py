"""API v1 - Stable release.

This module contains the v1 API endpoints. The v1 API is the current
stable release and should be used for production integrations.

Breaking changes will NOT be made to v1. New features may be added
in a backwards-compatible manner.

Import order matters: reach this package through ``app.api.main``, never the
other way round. ``.chat``, ``.config`` and ``.admin`` below delegate to the
root handlers and import them from ``app.api.main`` at module scope, while
``app.api.main`` includes ``router`` at the end of its own module body. That is
the ordinary FastAPI shape and it works in one direction only -- importing
``app.api.v1`` (or one of its submodules) as the entry point re-enters this
half-built ``__init__`` from main's include line and raises "cannot import name
'router' from partially initialized module". If you hit that, import
``app.api.main`` first; the served ASGI app lives there, so every real process
already does.
"""

from fastapi import APIRouter

from app.api.v1 import chat, health, config, admin, webhooks, decision, policy, digital_twin

router = APIRouter(prefix="/v1", tags=["v1"])

router.include_router(health.router)
router.include_router(chat.router)
router.include_router(config.router)
router.include_router(admin.router)
router.include_router(webhooks.router)
router.include_router(decision.router)
router.include_router(policy.router)
router.include_router(digital_twin.router)

__all__ = ["router"]
