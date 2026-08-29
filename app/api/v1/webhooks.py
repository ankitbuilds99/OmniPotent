"""Webhook endpoints for API v1."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

router: APIRouter = APIRouter(prefix="/webhooks", tags=["Webhooks"])


@router.post("/github")
async def github_webhook(request: Request) -> dict[str, Any]:
    """Handle GitHub webhook events."""
    from app.api.main import github_webhook as main_webhook

    return await main_webhook(request)


@router.post("/gitlab")
async def gitlab_webhook(request: Request) -> dict[str, Any]:
    """Handle GitLab webhook events."""
    from app.api.main import gitlab_webhook as main_webhook

    return await main_webhook(request)


@router.post("/pagerduty")
async def pagerduty_webhook(request: Request) -> dict[str, Any]:
    """Handle PagerDuty webhook events."""
    from app.api.main import pagerduty_webhook as main_webhook

    return await main_webhook(request)


@router.post("/stripe")
async def stripe_webhook(request: Request) -> dict[str, Any]:
    """Handle Stripe webhook events."""
    from app.api.main import stripe_webhook as main_webhook

    return await main_webhook(request)
