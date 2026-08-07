"""Slack API Endpoints.

FastAPI router for handling Slack webhooks:
- Event subscriptions
- Slash commands
- Interactive components
- OAuth flow
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, Response
from pydantic import BaseModel

log = logging.getLogger("omnipotent.slack_bot.api")

router = APIRouter(prefix="/slack", tags=["slack"])


class SlackChallenge(BaseModel):
    """Slack URL verification challenge."""

    type: str
    token: str
    challenge: str


async def verify_slack_signature(
    request: Request,
    x_slack_signature: str = Header(..., alias="X-Slack-Signature"),
    x_slack_request_timestamp: str = Header(..., alias="X-Slack-Request-Timestamp"),
) -> bytes:
    """Verify Slack request signature."""
    import os

    signing_secret = os.getenv("SLACK_SIGNING_SECRET", "")
    if not signing_secret:
        log.warning("SLACK_SIGNING_SECRET not configured")
        raise HTTPException(status_code=500, detail="Slack signing secret not configured")

    timestamp = int(x_slack_request_timestamp)
    if abs(time.time() - timestamp) > 60 * 5:
        raise HTTPException(status_code=403, detail="Request timestamp too old")

    body = await request.body()

    sig_basestring = f"v0:{x_slack_request_timestamp}:{body.decode('utf-8')}"
    computed_signature = (
        "v0="
        + hmac.new(
            signing_secret.encode(),
            sig_basestring.encode(),
            hashlib.sha256,
        ).hexdigest()
    )

    if not hmac.compare_digest(computed_signature, x_slack_signature):
        raise HTTPException(status_code=403, detail="Invalid signature")

    return body


@router.post("/events", response_model=None)
async def slack_events(
    request: Request,
    background_tasks: BackgroundTasks,
    x_slack_signature: str = Header(None, alias="X-Slack-Signature"),
    x_slack_request_timestamp: str = Header(None, alias="X-Slack-Request-Timestamp"),
) -> dict[str, Any] | Response:
    """Handle Slack event subscriptions."""
    body = await request.body()
    payload = json.loads(body)

    if payload.get("type") == "url_verification":
        return {"challenge": payload.get("challenge")}

    if x_slack_signature and x_slack_request_timestamp:
        await verify_slack_signature(request, x_slack_signature, x_slack_request_timestamp)

    event_type = payload.get("type")

    if event_type == "event_callback":
        event = payload.get("event", {})
        event_subtype = event.get("type")

        log.info(
            "Slack event received: type=%s subtype=%s",
            event_type,
            event_subtype,
        )

        background_tasks.add_task(_process_event, event, payload)

        return {"ok": True}

    return {"ok": True}


async def _process_event(event: dict[str, Any], payload: dict[str, Any]) -> None:
    """Process Slack event in background."""
    from app.slack_bot.handlers import get_event_handler

    try:
        handler = get_event_handler()
        await handler.handle(event)
    except Exception as e:
        log.exception("Error processing Slack event: %s", e)


@router.post("/commands")
async def slack_commands(
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    """Handle Slack slash commands."""
    form_data = await request.form()

    command = form_data.get("command", "")
    text = form_data.get("text", "")
    user_id = form_data.get("user_id", "")
    channel_id = form_data.get("channel_id", "")
    response_url = form_data.get("response_url", "")
    trigger_id = form_data.get("trigger_id", "")

    log.info(
        "Slack command received: command=%s text=%s user=%s channel=%s",
        command,
        text[:50] if text else "",
        user_id,
        channel_id,
    )

    background_tasks.add_task(
        _process_command,
        command,
        text,
        user_id,
        channel_id,
        response_url,
        trigger_id,
    )

    return {
        "response_type": "ephemeral",
        "text": "Processing your request...",
    }


async def _process_command(
    command: str,
    text: str,
    user_id: str,
    channel_id: str,
    response_url: str,
    trigger_id: str,
) -> None:
    """Process slash command in background."""
    from app.slack_bot.client import get_slack_client
    from app.slack_bot.core import get_slack_bot

    try:
        bot = get_slack_bot()
        response = await bot.handle_command(
            command=command,
            text=text,
            user_id=user_id,
            channel_id=channel_id,
            response_url=response_url,
        )

        client = await get_slack_client()
        await client.respond_to_interaction(
            response_url=response_url,
            text=response.text,
            blocks=response.blocks if response.blocks else None,
            response_type="ephemeral" if response.ephemeral else "in_channel",
        )

    except Exception as e:
        log.exception("Error processing Slack command: %s", e)

        try:
            client = await get_slack_client()
            await client.respond_to_interaction(
                response_url=response_url,
                text=f"Error processing command: {str(e)}",
                response_type="ephemeral",
            )
        except Exception:
            pass


@router.post("/interactions", response_model=None)
async def slack_interactions(
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict[str, Any] | Response:
    """Handle Slack interactive components."""
    form_data = await request.form()
    payload_str = form_data.get("payload", "")

    if not payload_str:
        raise HTTPException(status_code=400, detail="Missing payload")

    payload = json.loads(payload_str)
    interaction_type = payload.get("type")

    log.info("Slack interaction received: type=%s", interaction_type)

    if interaction_type == "view_submission":
        result = await _process_view_submission(payload)
        if result:
            return result
        return Response(status_code=200)

    background_tasks.add_task(_process_interaction, payload)

    return Response(status_code=200)


async def _process_interaction(payload: dict[str, Any]) -> None:
    """Process interaction in background."""
    from app.slack_bot.client import get_slack_client
    from app.slack_bot.handlers import get_interaction_handler

    try:
        handler = get_interaction_handler()
        response = await handler.handle(payload)

        if response:
            response_url = payload.get("response_url")
            if response_url:
                client = await get_slack_client()
                await client.respond_to_interaction(
                    response_url=response_url,
                    text=response.text,
                    blocks=response.blocks if response.blocks else None,
                    replace_original=True,
                )

    except Exception as e:
        log.exception("Error processing Slack interaction: %s", e)


async def _process_view_submission(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Process view submission synchronously (must return within 3s)."""
    from app.slack_bot.handlers import get_interaction_handler

    try:
        handler = get_interaction_handler()
        result = await handler.handle(payload)

        if isinstance(result, dict) and result.get("response_action"):
            return result

        return None

    except Exception as e:
        log.exception("Error processing view submission: %s", e)
        return {
            "response_action": "errors",
            "errors": {"general": str(e)},
        }


@router.post("/options")
async def slack_options(request: Request) -> dict[str, Any]:
    """Handle Slack options loading (for dynamic selects)."""
    form_data = await request.form()
    payload_str = form_data.get("payload", "")

    if not payload_str:
        raise HTTPException(status_code=400, detail="Missing payload")

    payload = json.loads(payload_str)
    action_id = payload.get("action_id", "")
    value = payload.get("value", "")

    log.info("Slack options request: action_id=%s value=%s", action_id, value)

    options = await _get_options(action_id, value, payload)

    return {"options": options}


async def _get_options(
    action_id: str,
    value: str,
    payload: dict[str, Any],
) -> list[dict[str, Any]]:
    """Get options for dynamic selects."""
    if action_id == "service_select":
        services = ["checkout-api", "billing-service", "user-service", "payment-service"]
        return [
            {"text": {"type": "plain_text", "text": s}, "value": s}
            for s in services
            if value.lower() in s.lower()
        ]

    elif action_id == "team_select":
        teams = ["platform", "payments", "frontend", "infrastructure", "data"]
        return [
            {"text": {"type": "plain_text", "text": t}, "value": t}
            for t in teams
            if value.lower() in t.lower()
        ]

    elif action_id == "incident_select":
        from app.slack_bot.core import get_slack_bot

        return [
            {"text": {"type": "plain_text", "text": "INC-1234"}, "value": "INC-1234"},
            {"text": {"type": "plain_text", "text": "INC-1235"}, "value": "INC-1235"},
        ]

    return []


@router.get("/oauth/callback")
async def slack_oauth_callback(
    code: str,
    state: str | None = None,
) -> dict[str, Any]:
    """Handle Slack OAuth callback."""
    import os
    import httpx

    client_id = os.getenv("SLACK_CLIENT_ID", "")
    client_secret = os.getenv("SLACK_CLIENT_SECRET", "")

    if not client_id or not client_secret:
        raise HTTPException(status_code=500, detail="OAuth not configured")

    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://slack.com/api/oauth.v2.access",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
            },
        )

        result = response.json()

        if not result.get("ok"):
            log.error("OAuth error: %s", result.get("error"))
            raise HTTPException(status_code=400, detail=result.get("error"))

        team_id = result.get("team", {}).get("id")
        team_name = result.get("team", {}).get("name")
        access_token = result.get("access_token")
        bot_user_id = result.get("bot_user_id")

        log.info(
            "Slack OAuth success: team=%s (%s) bot=%s",
            team_name,
            team_id,
            bot_user_id,
        )

        return {
            "success": True,
            "team_id": team_id,
            "team_name": team_name,
            "message": f"Successfully installed OmniPotent to {team_name}!",
        }


@router.get("/install")
async def slack_install() -> dict[str, Any]:
    """Get Slack installation URL."""
    import os

    client_id = os.getenv("SLACK_CLIENT_ID", "")

    if not client_id:
        raise HTTPException(status_code=500, detail="SLACK_CLIENT_ID not configured")

    scopes = [
        "channels:history",
        "channels:join",
        "channels:manage",
        "channels:read",
        "chat:write",
        "chat:write.public",
        "commands",
        "files:read",
        "files:write",
        "groups:history",
        "groups:read",
        "groups:write",
        "im:history",
        "im:read",
        "im:write",
        "mpim:history",
        "mpim:read",
        "mpim:write",
        "pins:read",
        "pins:write",
        "reactions:read",
        "reactions:write",
        "users:read",
        "users:read.email",
    ]

    redirect_uri = os.getenv("SLACK_REDIRECT_URI", "")

    install_url = (
        f"https://slack.com/oauth/v2/authorize"
        f"?client_id={client_id}"
        f"&scope={','.join(scopes)}"
    )

    if redirect_uri:
        install_url += f"&redirect_uri={redirect_uri}"

    return {"install_url": install_url}


def include_router(app: Any) -> None:
    """Include Slack router in FastAPI app."""
    app.include_router(router)
