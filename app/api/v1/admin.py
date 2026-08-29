"""Admin endpoints for API v1 (multi-tenant)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.api.main import (
    CreateApiKeyRequest,
    AddMemberRequest,
    admin_org as main_admin_org,
    admin_list_api_keys as main_list_api_keys,
    admin_create_api_key as main_create_api_key,
    admin_add_member as main_add_member,
    usage as main_usage,
    require_perm,
)

router: APIRouter = APIRouter(prefix="/admin", tags=["Admin"])

# The root handlers run as plain functions here, so the RBAC dependencies that
# guard the root routes do not fire for a /v1 request. Each one is restated
# below; otherwise the /v1 mirror would let any authenticated seat mint API keys
# and add members.


@router.get("/org", dependencies=[Depends(require_perm("view"))])
async def get_org() -> dict[str, Any]:
    """Get organization details."""
    return await main_admin_org()


@router.get("/api-keys", dependencies=[Depends(require_perm("manage_api_keys"))])
async def list_api_keys() -> dict[str, Any]:
    """List organization API keys."""
    return await main_list_api_keys()


@router.post("/api-keys", dependencies=[Depends(require_perm("manage_api_keys"))])
async def create_api_key(req: CreateApiKeyRequest) -> dict[str, Any]:
    """Create a new API key."""
    return await main_create_api_key(req)


@router.post("/members", dependencies=[Depends(require_perm("manage_members"))])
async def add_member(req: AddMemberRequest) -> dict[str, Any]:
    """Add a member to the organization."""
    return await main_add_member(req)


@router.get("/usage")
async def get_usage() -> dict[str, Any]:
    """Get usage statistics."""
    return await main_usage()
