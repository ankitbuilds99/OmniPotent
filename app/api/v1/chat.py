"""Chat/Investigation endpoints for API v1."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.api.main import (
    ChatRequest,
    ChatResponse,
    ApproveRequest,
    chat as main_chat,
    chat_stream as main_stream,
    approve as main_approve,
    approve_stream as main_approve_stream,
)

router = APIRouter(prefix="/chat", tags=["Chat"])


@router.post("", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    """Start or continue an investigation."""
    return await main_chat(req)


@router.post("/stream")
async def chat_stream(req: ChatRequest) -> Any:
    """Stream an investigation with real-time progress (SSE)."""
    return await main_stream(req)


@router.post("/approve", response_model=ChatResponse)
async def approve(req: ApproveRequest) -> ChatResponse:
    """Approve or reject a pending action."""
    return await main_approve(req)


@router.post("/approve/stream")
async def approve_stream(req: ApproveRequest) -> Any:
    """Stream approval continuation (SSE)."""
    return await main_approve_stream(req)
