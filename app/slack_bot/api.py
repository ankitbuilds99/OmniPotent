"""Slack API Endpoints.

FastAPI router for handling Slack webhooks:
- Event subscriptions
- Slash commands
- Interactive components
- OAuth flow
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.slack_bot.client import get_slack_client, SlackClient

log = logging.getLogger("omnipotent.slack.api")

router = APIRouter(prefix="/slack", tags=["slack"])


async def get_client() -> SlackClient:
    """Dependency to get Slack client."""
    return get_slack_client()


async def verify_slack_request(
    request: Request,
    x_slack_request_timestamp: str = Header(...),
    x_slack_signature: str = Header(...),
) -> bytes:
    """Verify Slack request signature."""
    body = await request.body()
    client = get_slack_client()

    if not client.verify_signature(body, x_slack_request_timestamp, x_slack_signature):
        raise HTTPException(status_code=401, detail="Invalid signature")

    return body


@router.post("/events", response_model=None)
async def slack_events(
    request: Request,
    background_tasks: BackgroundTasks,
    client: SlackClient = Depends(get_client),
) -> Any:
    """Handle Slack Events API."""
    body = await request.body()

    try:
        timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
        signature = request.headers.get("X-Slack-Signature", "")

        if timestamp and signature:
            if not client.verify_signature(body, timestamp, signature):
                raise HTTPException(status_code=401, detail="Invalid signature")
    except Exception:
        pass

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    event_type = payload.get("type")

    if event_type == "url_verification":
        return {"challenge": payload.get("challenge")}

    if event_type == "event_callback":
        event = payload.get("event", {})
        background_tasks.add_task(_handle_event, event, payload)
        return {"ok": True}

    return {"ok": True}


async def _handle_event(event: dict[str, Any], payload: dict[str, Any]) -> None:
    """Process Slack event in background."""
    event_type = event.get("type")

    log.info("Processing event: %s", event_type)

    try:
        if event_type == "message":
            await _handle_message_event(event)
        elif event_type == "app_mention":
            await _handle_mention_event(event)
        elif event_type == "app_home_opened":
            await _handle_app_home_event(event)
        elif event_type == "reaction_added":
            await _handle_reaction_event(event)
        elif event_type == "channel_created":
            await _handle_channel_created(event)
    except Exception as e:
        log.error("Error handling event %s: %s", event_type, e)


async def _handle_message_event(event: dict[str, Any]) -> None:
    """Handle message event."""
    if event.get("bot_id"):
        return

    channel = event.get("channel", "")
    text = event.get("text", "")
    user = event.get("user", "")
    thread_ts = event.get("thread_ts")

    log.debug("Message from %s in %s: %s", user, channel, text[:50])


async def _handle_mention_event(event: dict[str, Any]) -> None:
    """Handle @mention event."""
    channel = event.get("channel", "")
    text = event.get("text", "")
    user = event.get("user", "")
    ts = event.get("ts", "")

    client = get_slack_client()

    await client.send_message(
        channel=channel,
        text=f"Hey <@{user}>! I received your message. How can I help?",
        thread_ts=ts,
    )


async def _handle_app_home_event(event: dict[str, Any]) -> None:
    """Handle App Home opened event."""
    user_id = event.get("user", "")

    client = get_slack_client()

    view = {
        "type": "home",
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*Welcome to OmniPotent!*\n\nYour AI-powered DevOps assistant.",
                },
            },
            {"type": "divider"},
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*Quick Actions*",
                },
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Start Investigation"},
                        "action_id": "start_investigation",
                        "style": "primary",
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "View Incidents"},
                        "action_id": "view_incidents",
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "On-Call Status"},
                        "action_id": "oncall_status",
                    },
                ],
            },
        ],
    }

    await client.publish_home(user_id, view)


async def _handle_reaction_event(event: dict[str, Any]) -> None:
    """Handle reaction added event."""
    reaction = event.get("reaction", "")
    user = event.get("user", "")
    item = event.get("item", {})

    log.debug("Reaction %s by %s on %s", reaction, user, item)


async def _handle_channel_created(event: dict[str, Any]) -> None:
    """Handle channel created event."""
    channel = event.get("channel", {})
    channel_name = channel.get("name", "")

    log.info("Channel created: %s", channel_name)


@router.post("/commands/{command}", response_model=None)
async def slack_commands(
    command: str,
    request: Request,
    background_tasks: BackgroundTasks,
    client: SlackClient = Depends(get_client),
) -> dict[str, Any]:
    """Handle Slack slash commands."""
    form_data = await request.form()

    cmd_text = form_data.get("text", "")
    user_id = form_data.get("user_id", "")
    channel_id = form_data.get("channel_id", "")
    response_url = form_data.get("response_url", "")
    trigger_id = form_data.get("trigger_id", "")

    log.info("Command /%s from %s: %s", command, user_id, cmd_text)

    background_tasks.add_task(
        _process_command,
        command,
        str(cmd_text),
        str(user_id),
        str(channel_id),
        str(response_url),
        str(trigger_id),
    )

    return {
        "response_type": "ephemeral",
        "text": f"Processing `/{command} {cmd_text}`...",
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
    client = get_slack_client()

    try:
        if command == "investigate":
            result = f"Starting investigation for: {text or 'current incident'}"
        elif command == "oncall":
            result = "On-call: @alice (primary), @bob (secondary)"
        elif command == "status":
            result = "All systems operational"
        elif command == "incident":
            result = f"Creating incident: {text}"
        else:
            result = f"Unknown command: /{command}"

        await client.respond_to_url(
            response_url,
            text=result,
            response_type="in_channel" if command in ("status",) else "ephemeral",
        )

    except Exception as e:
        log.error("Command error: %s", e)
        await client.respond_to_url(response_url, text=f"Error: {e}")


@router.post("/interactions", response_model=None)
async def slack_interactions(
    request: Request,
    background_tasks: BackgroundTasks,
) -> Any:
    """Handle Slack interactive components."""
    form_data = await request.form()
    payload_str = form_data.get("payload", "")

    if not payload_str:
        raise HTTPException(status_code=400, detail="Missing payload")

    try:
        payload = json.loads(str(payload_str))
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid payload JSON")

    interaction_type = payload.get("type")

    if interaction_type == "block_actions":
        background_tasks.add_task(_handle_block_actions, payload)
        return JSONResponse(content={})

    elif interaction_type == "view_submission":
        return await _handle_view_submission(payload)

    elif interaction_type == "view_closed":
        return JSONResponse(content={})

    elif interaction_type == "shortcut":
        background_tasks.add_task(_handle_shortcut, payload)
        return JSONResponse(content={})

    elif interaction_type == "message_action":
        background_tasks.add_task(_handle_message_action, payload)
        return JSONResponse(content={})

    return JSONResponse(content={})


async def _handle_block_actions(payload: dict[str, Any]) -> None:
    """Handle block action interactions."""
    actions = payload.get("actions", [])
    user = payload.get("user", {})
    response_url = payload.get("response_url")

    client = get_slack_client()

    for action in actions:
        action_id = action.get("action_id", "")

        log.info("Block action: %s by %s", action_id, user.get("id"))

        if action_id == "start_investigation":
            if response_url:
                await client.respond_to_url(
                    response_url,
                    text="Investigation started! I'll analyze the incident and report back.",
                    replace_original=False,
                )

        elif action_id == "view_incidents":
            if response_url:
                await client.respond_to_url(
                    response_url,
                    text="No active incidents. All systems operational!",
                )

        elif action_id == "oncall_status":
            if response_url:
                await client.respond_to_url(
                    response_url,
                    text="*On-Call Schedule*\nPrimary: @alice\nSecondary: @bob",
                )


async def _handle_view_submission(payload: dict[str, Any]) -> dict[str, Any]:
    """Handle modal submission."""
    view = payload.get("view", {})
    callback_id = view.get("callback_id", "")
    values = view.get("state", {}).get("values", {})
    user = payload.get("user", {})

    log.info("View submission: %s by %s", callback_id, user.get("id"))

    if callback_id == "incident_form":
        return {"response_action": "clear"}

    return {}


async def _handle_shortcut(payload: dict[str, Any]) -> None:
    """Handle global/message shortcuts."""
    callback_id = payload.get("callback_id", "")
    trigger_id = payload.get("trigger_id", "")
    user = payload.get("user", {})

    log.info("Shortcut: %s by %s", callback_id, user.get("id"))

    client = get_slack_client()

    if callback_id == "create_incident":
        view = {
            "type": "modal",
            "callback_id": "incident_form",
            "title": {"type": "plain_text", "text": "Create Incident"},
            "submit": {"type": "plain_text", "text": "Create"},
            "close": {"type": "plain_text", "text": "Cancel"},
            "blocks": [
                {
                    "type": "input",
                    "block_id": "title_block",
                    "label": {"type": "plain_text", "text": "Title"},
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "title",
                        "placeholder": {"type": "plain_text", "text": "What's happening?"},
                    },
                },
                {
                    "type": "input",
                    "block_id": "severity_block",
                    "label": {"type": "plain_text", "text": "Severity"},
                    "element": {
                        "type": "static_select",
                        "action_id": "severity",
                        "options": [
                            {"text": {"type": "plain_text", "text": "Critical"}, "value": "critical"},
                            {"text": {"type": "plain_text", "text": "High"}, "value": "high"},
                            {"text": {"type": "plain_text", "text": "Medium"}, "value": "medium"},
                            {"text": {"type": "plain_text", "text": "Low"}, "value": "low"},
                        ],
                    },
                },
            ],
        }

        await client.open_modal(trigger_id, view)


async def _handle_message_action(payload: dict[str, Any]) -> None:
    """Handle message action (context menu)."""
    callback_id = payload.get("callback_id", "")
    message = payload.get("message", {})
    user = payload.get("user", {})

    log.info("Message action: %s by %s", callback_id, user.get("id"))


@router.get("/oauth/authorize")
async def slack_oauth_authorize(
    client: SlackClient = Depends(get_client),
) -> RedirectResponse:
    """Initiate Slack OAuth flow."""
    import os

    client_id = os.environ.get("SLACK_CLIENT_ID", "")
    redirect_uri = os.environ.get("SLACK_OAUTH_REDIRECT_URI", "")
    scopes = "app_mentions:read,channels:history,channels:read,chat:write,commands,users:read"

    url = (
        f"https://slack.com/oauth/v2/authorize"
        f"?client_id={client_id}"
        f"&scope={scopes}"
        f"&redirect_uri={redirect_uri}"
    )

    return RedirectResponse(url=url)


@router.get("/oauth/callback")
async def slack_oauth_callback(
    code: str,
    client: SlackClient = Depends(get_client),
) -> dict[str, Any]:
    """Handle Slack OAuth callback."""
    import os

    redirect_uri = os.environ.get("SLACK_OAUTH_REDIRECT_URI")

    try:
        response = await client.oauth_v2_access(code, redirect_uri)

        team_id = response.data.get("team", {}).get("id")
        team_name = response.data.get("team", {}).get("name")

        log.info("OAuth success: %s (%s)", team_name, team_id)

        return {
            "ok": True,
            "team_id": team_id,
            "team_name": team_name,
            "message": f"Successfully connected to {team_name}!",
        }

    except Exception as e:
        log.error("OAuth error: %s", e)
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/health")
async def slack_health() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "ok", "service": "slack"}
