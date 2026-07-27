"""Health check endpoints for API v1."""

from __future__ import annotations

from fastapi import APIRouter

from app.health import get_health_checker

router = APIRouter(tags=["Health"])


@router.get("/health")
async def health() -> dict:
    """Liveness probe - is the service alive?"""
    alive, details = await get_health_checker().liveness()
    return {"status": "healthy" if alive else "unhealthy", **details}


@router.get("/health/ready")
async def ready() -> dict:
    """Readiness probe - can the service handle requests?"""
    ready, details = await get_health_checker().readiness()
    return details


@router.get("/health/startup")
async def startup() -> dict:
    """Startup probe - has initialization completed?"""
    started, details = await get_health_checker().startup()
    return details
