"""Slack Bot Router - @mention support and slash commands."""

from fastapi import APIRouter, Request, BackgroundTasks
from pydantic import BaseModel
from typing import Optional
import hmac
import hashlib
import time
import json
import re
import logging
import httpx
import os

router = APIRouter(prefix="/slack", tags=["slack"])
logger = logging.getLogger(__name__)


class SlackConfig:
    """Slack app configuration - loaded from environment variables."""

    SIGNING_SECRET: str = os.getenv("SLACK_SIGNING_SECRET", "")
    BOT_TOKEN: str = os.getenv("SLACK_BOT_TOKEN", "")
    APP_TOKEN: str = os.getenv("SLACK_APP_TOKEN", "")


def verify_slack_signature(
    body: bytes, timestamp: str, signature: str, signing_secret: str
) -> bool:
    """Verify Slack request signature."""
    if abs(time.time() - int(timestamp)) > 60 * 5:
        return False

    sig_basestring = f"v0:{timestamp}:{body.decode()}"
    computed = "v0=" + hmac.new(
        signing_secret.encode(),
        sig_basestring.encode(),
        hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(computed, signature)


class SlackEventPayload(BaseModel):
    """Slack Events API payload."""

    type: str
    challenge: Optional[str] = None
    token: Optional[str] = None
    team_id: Optional[str] = None
    event: Optional[dict] = None
    event_id: Optional[str] = None
    event_time: Optional[int] = None


class SlackCommandPayload(BaseModel):
    """Slack slash command payload."""

    command: str
    text: str
    response_url: str
    trigger_id: str
    user_id: str
    user_name: str
    channel_id: str
    channel_name: str
    team_id: str
    team_domain: str


async def send_slack_message(
    channel: str, text: str, thread_ts: Optional[str] = None, blocks: Optional[list[dict]] = None
) -> dict:
    """Send a message to Slack."""
    async with httpx.AsyncClient() as client:
        payload: dict[str, str | list[dict]] = {
            "channel": channel,
            "text": text,
        }
        if thread_ts:
            payload["thread_ts"] = thread_ts
        if blocks:
            payload["blocks"] = blocks

        response = await client.post(
            "https://slack.com/api/chat.postMessage",
            headers={
                "Authorization": f"Bearer {SlackConfig.BOT_TOKEN}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        return response.json()


async def update_slack_message(
    channel: str, ts: str, text: str, blocks: Optional[list[dict]] = None
) -> dict:
    """Update an existing Slack message."""
    async with httpx.AsyncClient() as client:
        payload: dict[str, str | list[dict]] = {
            "channel": channel,
            "ts": ts,
            "text": text,
        }
        if blocks:
            payload["blocks"] = blocks

        response = await client.post(
            "https://slack.com/api/chat.update",
            headers={
                "Authorization": f"Bearer {SlackConfig.BOT_TOKEN}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        return response.json()


async def add_reaction(channel: str, ts: str, emoji: str) -> dict:
    """Add a reaction to a message."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://slack.com/api/reactions.add",
            headers={
                "Authorization": f"Bearer {SlackConfig.BOT_TOKEN}",
                "Content-Type": "application/json",
            },
            json={
                "channel": channel,
                "timestamp": ts,
                "name": emoji,
            },
        )
        return response.json()


def parse_mention_query(text: str, bot_user_id: str) -> Optional[str]:
    """Extract the query from a bot mention."""
    mention_pattern = f"<@{bot_user_id}>"
    text = text.replace(mention_pattern, "").strip()
    return text if text else None


def build_investigation_blocks(query: str, status: str = "starting") -> list:
    """Build Slack blocks for investigation progress."""
    status_emoji = {
        "starting": ":hourglass:",
        "investigating": ":mag:",
        "analyzing": ":brain:",
        "complete": ":white_check_mark:",
        "error": ":x:",
    }

    emoji = status_emoji.get(status, ":question:")

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"{emoji} AI Investigation",
                "emoji": True,
            }
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Query:* {query}",
            }
        },
        {"type": "divider"},
    ]

    if status == "starting":
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": ":hourglass: Starting investigation...",
            }
        })
    elif status == "investigating":
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": ":mag: Querying observability tools...\n:rocket: Checking recent deployments...\n:memo: Analyzing logs...",
            }
        })

    return blocks


def build_rca_result_blocks(
    query: str,
    root_cause: str,
    confidence: int,
    evidence: list,
    recommendation: str,
) -> list:
    """Build Slack blocks for RCA results."""
    confidence_emoji = ":large_green_circle:" if confidence >= 80 else ":large_yellow_circle:" if confidence >= 50 else ":red_circle:"

    evidence_text = "\n".join([f"• {e}" for e in evidence[:5]])

    return [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": ":white_check_mark: Investigation Complete",
                "emoji": True,
            }
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Query:* {query}",
            }
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Root Cause:*\n{root_cause}",
            }
        },
        {
            "type": "section",
            "fields": [
                {
                    "type": "mrkdwn",
                    "text": f"*Confidence:*\n{confidence_emoji} {confidence}%",
                },
                {
                    "type": "mrkdwn",
                    "text": "*Status:*\n:white_check_mark: Verified",
                },
            ]
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Evidence:*\n{evidence_text}",
            }
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Recommendation:*\n{recommendation}",
            }
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": ":thumbsup: Helpful",
                        "emoji": True,
                    },
                    "action_id": "feedback_helpful",
                    "value": "helpful",
                },
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": ":thumbsdown: Not Helpful",
                        "emoji": True,
                    },
                    "action_id": "feedback_not_helpful",
                    "value": "not_helpful",
                },
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": ":page_facing_up: View Full Report",
                        "emoji": True,
                    },
                    "action_id": "view_full_report",
                    "url": "https://omnipotent.example.com/investigation/123",
                },
            ]
        },
    ]


async def run_investigation(
    query: str, channel: str, thread_ts: str, message_ts: str
) -> None:
    """Run AI investigation and update Slack with results."""
    try:
        await update_slack_message(
            channel,
            message_ts,
            "Investigating...",
            build_investigation_blocks(query, "investigating"),
        )

        await add_reaction(channel, thread_ts, "eyes")

        # Simulated investigation - in production, this would call the real RCA engine
        import asyncio
        await asyncio.sleep(3)

        # Mock results
        result_blocks = build_rca_result_blocks(
            query=query,
            root_cause="Database connection pool exhaustion due to connection leak in payment-service v2.4.1",
            confidence=87,
            evidence=[
                "Error rate spiked 300% at 14:32 UTC",
                "payment-service v2.4.1 deployed at 14:15 UTC",
                "DB connection count at max (100/100)",
                "Similar pattern in commit abc123 (connection leak)",
            ],
            recommendation="Rollback payment-service to v2.4.0. The connection leak was introduced in the refactored database client.",
        )

        await update_slack_message(channel, message_ts, "Investigation complete", result_blocks)

        await add_reaction(channel, thread_ts, "white_check_mark")

    except Exception as e:
        logger.error(f"Investigation failed: {e}")
        await update_slack_message(
            channel,
            message_ts,
            f"Investigation failed: {str(e)}",
            [{"type": "section", "text": {"type": "mrkdwn", "text": f":x: Error: {str(e)}"}}],
        )


@router.post("/events")
async def slack_events(request: Request, background_tasks: BackgroundTasks):
    """Handle Slack Events API - mentions and messages."""
    body = await request.body()

    # Verify signature in production
    # timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    # signature = request.headers.get("X-Slack-Signature", "")
    # if not verify_slack_signature(body, timestamp, signature, SlackConfig.SIGNING_SECRET):
    #     raise HTTPException(status_code=401, detail="Invalid signature")

    payload = SlackEventPayload(**json.loads(body))

    # URL verification challenge
    if payload.type == "url_verification":
        return {"challenge": payload.challenge}

    if payload.type == "event_callback" and payload.event:
        event = payload.event
        event_type = event.get("type")

        # Handle app mentions: @omnipotent investigate X
        if event_type == "app_mention":
            text = event.get("text", "")
            channel: str = event.get("channel", "")
            thread_ts: str = event.get("thread_ts") or event.get("ts", "")
            _user = event.get("user")

            bot_user_id = "U123456"  # Would be fetched from auth.test
            query = parse_mention_query(text, bot_user_id)

            if query and channel:
                # Send initial response
                response = await send_slack_message(
                    channel,
                    "Starting investigation...",
                    thread_ts,
                    build_investigation_blocks(query, "starting"),
                )

                if response.get("ok"):
                    message_ts: str = response["ts"]
                    background_tasks.add_task(
                        run_investigation, query, channel, thread_ts, message_ts
                    )
            elif channel:
                await send_slack_message(
                    channel,
                    "Hi! I'm DevOps Copilot. Mention me with a question like:\n"
                    "• `@omnipotent why is checkout slow?`\n"
                    "• `@omnipotent investigate payment failures`\n"
                    "• `@omnipotent what changed in the last hour?`",
                    thread_ts,
                )

        # Handle messages in monitored channels (auto-investigation)
        elif event_type == "message":
            subtype = event.get("subtype")

            # Skip bot messages and edits
            if subtype in ["bot_message", "message_changed", "message_deleted"]:
                return {"ok": True}

            text = event.get("text", "").lower()
            channel = event.get("channel", "")

            # Check for alert patterns
            alert_patterns = [
                r"\[alert\]",
                r"\[critical\]",
                r"\[incident\]",
                r"pagerduty",
                r"opsgenie",
                r"🚨",
                r"error rate",
                r"latency spike",
            ]

            is_alert = any(re.search(pattern, text, re.IGNORECASE) for pattern in alert_patterns)

            if is_alert and channel:
                # Auto-investigation for alerts
                thread_ts = event.get("ts", "")
                response = await send_slack_message(
                    channel,
                    ":robot_face: I detected an alert. Starting auto-investigation...",
                    thread_ts,
                    build_investigation_blocks(text[:100], "starting"),
                )

                if response.get("ok"):
                    message_ts = response["ts"]
                    background_tasks.add_task(
                        run_investigation, text, channel, thread_ts, message_ts
                    )

    return {"ok": True}


@router.post("/commands/rca")
async def slash_rca(request: Request, background_tasks: BackgroundTasks):
    """Handle /rca slash command."""
    _ = background_tasks  # Will be used for async investigation
    form_data = await request.form()

    command_text = form_data.get("text", "")
    user_name = form_data.get("user_name", "")

    if not command_text:
        return {
            "response_type": "ephemeral",
            "text": "Please provide a query. Example: `/rca why is checkout slow?`",
        }

    # Immediate acknowledgment
    return {
        "response_type": "in_channel",
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f":mag: *{user_name}* started an investigation:\n> {command_text}",
                }
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": ":hourglass: Results will appear in a thread...",
                }
            }
        ]
    }


@router.post("/commands/status")
async def slash_status(request: Request):
    """Handle /status slash command - current system status."""
    form_data = await request.form()
    service_raw = form_data.get("text", "")
    service = str(service_raw).strip() if service_raw else ""

    # Mock status data
    services = {
        "api": {"status": "healthy", "latency": "45ms", "error_rate": "0.01%"},
        "checkout": {"status": "degraded", "latency": "230ms", "error_rate": "2.3%"},
        "payment": {"status": "healthy", "latency": "120ms", "error_rate": "0.05%"},
        "database": {"status": "healthy", "latency": "12ms", "error_rate": "0%"},
    }

    if service and service in services:
        s = services[service]
        status_emoji = ":large_green_circle:" if s["status"] == "healthy" else ":large_yellow_circle:"
        return {
            "response_type": "ephemeral",
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*{service.title()} Service Status*\n{status_emoji} {s['status'].title()}\n• Latency: {s['latency']}\n• Error Rate: {s['error_rate']}",
                    }
                }
            ]
        }

    # All services overview
    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": ":bar_chart: System Status",
                "emoji": True,
            }
        }
    ]

    for name, s in services.items():
        status_emoji = ":large_green_circle:" if s["status"] == "healthy" else ":large_yellow_circle:"
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"{status_emoji} *{name}*: {s['status'].title()} | {s['latency']} | {s['error_rate']} errors",
            }
        })

    return {"response_type": "ephemeral", "blocks": blocks}


@router.post("/commands/oncall")
async def slash_oncall(request: Request):
    """Handle /oncall slash command - who's on call."""
    form_data = await request.form()
    team_raw = form_data.get("text", "")
    team = str(team_raw).strip() if team_raw else ""

    # Mock on-call data
    oncall_data = {
        "platform": {"primary": "Alice Smith", "secondary": "Bob Jones", "escalation": "#platform-oncall"},
        "infra": {"primary": "Charlie Brown", "secondary": "Diana Prince", "escalation": "#infra-oncall"},
        "security": {"primary": "Eve Wilson", "secondary": "Frank Miller", "escalation": "#security-oncall"},
    }

    if team and team in oncall_data:
        o = oncall_data[team]
        return {
            "response_type": "ephemeral",
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*{team.title()} On-Call*\n:bust_in_silhouette: Primary: {o['primary']}\n:busts_in_silhouette: Secondary: {o['secondary']}\n:mega: Escalation: {o['escalation']}",
                    }
                }
            ]
        }

    # All teams
    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": ":telephone_receiver: On-Call Schedule",
                "emoji": True,
            }
        }
    ]

    for name, o in oncall_data.items():
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*{name.title()}*: {o['primary']} (backup: {o['secondary']})",
            }
        })

    return {"response_type": "ephemeral", "blocks": blocks}


@router.post("/commands/help")
async def slash_help(request: Request):
    """Handle /omnipotent-help slash command."""
    return {
        "response_type": "ephemeral",
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": ":robot_face: DevOps Copilot Help",
                    "emoji": True,
                }
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*Slash Commands:*",
                }
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "• `/rca [query]` - Start an AI investigation\n• `/status [service]` - Check system status\n• `/oncall [team]` - See who's on call\n• `/omnipotent-help` - Show this help",
                }
            },
            {"type": "divider"},
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*Mentions:*",
                }
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "• `@DevOps Copilot why is X slow?`\n• `@DevOps Copilot investigate Y`\n• `@DevOps Copilot what changed recently?`",
                }
            },
            {"type": "divider"},
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*Auto-Investigation:*\nI automatically investigate alerts posted in monitored channels. Look for the :robot_face: reply!",
                }
            },
        ]
    }


@router.post("/interactivity")
async def slack_interactivity(request: Request):
    """Handle Slack interactive components (buttons, modals)."""
    form_data = await request.form()
    payload_raw = form_data.get("payload", "{}")
    payload_str = str(payload_raw) if payload_raw else "{}"
    payload = json.loads(payload_str)

    action_type = payload.get("type")

    if action_type == "block_actions":
        actions = payload.get("actions", [])
        for action in actions:
            action_id = action.get("action_id")

            if action_id == "feedback_helpful":
                # Log positive feedback
                return {"text": "Thanks for the feedback! :thumbsup:"}

            elif action_id == "feedback_not_helpful":
                # Log negative feedback and open modal for details
                return {
                    "response_action": "open_modal",
                    "view": {
                        "type": "modal",
                        "callback_id": "feedback_modal",
                        "title": {
                            "type": "plain_text",
                            "text": "Improve AI Response",
                        },
                        "submit": {
                            "type": "plain_text",
                            "text": "Submit",
                        },
                        "blocks": [
                            {
                                "type": "input",
                                "block_id": "feedback_input",
                                "element": {
                                    "type": "plain_text_input",
                                    "action_id": "feedback_text",
                                    "multiline": True,
                                    "placeholder": {
                                        "type": "plain_text",
                                        "text": "What was wrong or missing?",
                                    },
                                },
                                "label": {
                                    "type": "plain_text",
                                    "text": "Your Feedback",
                                },
                            }
                        ],
                    },
                }

    elif action_type == "view_submission":
        callback_id = payload.get("view", {}).get("callback_id")

        if callback_id == "feedback_modal":
            values = payload.get("view", {}).get("state", {}).get("values", {})
            feedback = values.get("feedback_input", {}).get("feedback_text", {}).get("value", "")
            # Store feedback for model improvement
            logger.info(f"Received feedback: {feedback}")
            return {"response_action": "clear"}

    return {"ok": True}
