"""Chat/Investigation endpoints for API v1."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.api.main import (
    ChatRequest,
    ChatResponse,
    ApproveRequest,
    chat as main_chat,
    chat_stream as main_stream,
    approve as main_approve,
    approve_stream as main_approve_stream,
    quota_gate,
    require_perm,
)

router = APIRouter(prefix="/chat", tags=["Chat"])

# These handlers call the root handlers as plain functions, which means the
# dependencies declared on the root *routes* never run for a /v1 request. Every
# one of them is therefore restated here. Left off, mounting this router would
# have handed a `viewer` seat an investigation endpoint with no quota check —
# a quieter version of the hole that made the root routes carry these in the
# first place. Any change to the root route's gates has to be mirrored here.
_INVESTIGATE = [Depends(require_perm("run_investigation")), Depends(quota_gate)]
_APPROVE = [Depends(require_perm("approve_action"))]


@router.post("", response_model=ChatResponse, dependencies=_INVESTIGATE)
async def chat(req: ChatRequest) -> ChatResponse:
    """Start or continue an investigation."""
    return await main_chat(req)


@router.post("/stream", dependencies=_INVESTIGATE)
async def chat_stream(req: ChatRequest) -> Any:
    """Stream an investigation with real-time progress (SSE)."""
    return await main_stream(req)


@router.post("/approve", response_model=ChatResponse, dependencies=_APPROVE)
async def approve(req: ApproveRequest) -> ChatResponse:
    """Approve or reject a pending action."""
    return await main_approve(req)


@router.post("/approve/stream", dependencies=_APPROVE)
async def approve_stream(req: ApproveRequest) -> Any:
    """Stream approval continuation (SSE)."""
    return await main_approve_stream(req)
