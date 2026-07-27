"""Admin endpoints for API v1 (multi-tenant)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.api.main import (
    CreateApiKeyRequest,
    AddMemberRequest,
    admin_org as main_admin_org,
    admin_list_api_keys as main_list_api_keys,
    admin_create_api_key as main_create_api_key,
    admin_list_members as main_list_members,
    admin_add_member as main_add_member,
    usage as main_usage,
)

router = APIRouter(prefix="/admin", tags=["Admin"])


@router.get("/org")
async def get_org() -> dict[str, Any]:
    """Get organization details."""
    return await main_admin_org()


@router.get("/api-keys")
async def list_api_keys() -> dict[str, Any]:
    """List organization API keys."""
    return await main_list_api_keys()


@router.post("/api-keys")
async def create_api_key(req: CreateApiKeyRequest) -> dict[str, Any]:
    """Create a new API key."""
    return await main_create_api_key(req)


@router.get("/members")
async def list_members() -> dict[str, Any]:
    """List organization members."""
    return await main_list_members()


@router.post("/members")
async def add_member(req: AddMemberRequest) -> dict[str, Any]:
    """Add a member to the organization."""
    return await main_add_member(req)


@router.get("/usage")
async def get_usage() -> dict[str, Any]:
    """Get usage statistics."""
    return await main_usage()
