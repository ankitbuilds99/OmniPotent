"""API v1 - Stable release.

This module contains the v1 API endpoints. The v1 API is the current
stable release and should be used for production integrations.

Breaking changes will NOT be made to v1. New features may be added
in a backwards-compatible manner.
"""

from fastapi import APIRouter

from app.api.v1 import chat, health, config, admin, webhooks

router = APIRouter(prefix="/v1", tags=["v1"])

router.include_router(health.router)
router.include_router(chat.router)
router.include_router(config.router)
router.include_router(admin.router)
router.include_router(webhooks.router)

__all__ = ["router"]
