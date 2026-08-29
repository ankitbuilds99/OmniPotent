"""Configuration endpoints for API v1."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.api.main import (
    GithubConnectRequest,
    ModelConfigRequest,
    config as main_config,
    github_status as main_github_status,
    github_connect as main_connect,
    github_disconnect as main_disconnect,
    model_configure as main_model_configure,
    reset_config as main_reset,
    require_perm,
)

router = APIRouter(prefix="/config", tags=["Config"])

# The root handlers are called as plain functions, so their routes' RBAC
# dependencies do not run for a /v1 request and are restated here. These write
# integration credentials and rewrite the deployment's model configuration;
# without this the /v1 mirror would be the unguarded way in.
_MANAGE = [Depends(require_perm("manage_integrations"))]


@router.get("")
async def get_config() -> dict[str, Any]:
    """Get the current agent configuration."""
    return await main_config()


@router.get("/github/status")
async def github_status() -> dict[str, Any]:
    """Get GitHub connection status."""
    return await main_github_status()


@router.post("/github/connect", dependencies=_MANAGE)
async def github_connect(req: GithubConnectRequest) -> dict[str, Any]:
    """Connect to a GitHub repository."""
    return await main_connect(req)


@router.post("/github/disconnect", dependencies=_MANAGE)
async def github_disconnect() -> dict[str, Any]:
    """Disconnect from GitHub."""
    return await main_disconnect()


@router.post("/model", dependencies=_MANAGE)
async def configure_model(req: ModelConfigRequest) -> dict[str, Any]:
    """Configure the LLM provider and model."""
    return await main_model_configure(req)


@router.post("/reset", dependencies=_MANAGE)
async def reset_config() -> dict[str, Any]:
    """Reset configuration to defaults."""
    return await main_reset()
