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

from app.config import get_settings

router = APIRouter(prefix="/slack", tags=["slack"])
logger = logging.getLogger(__name__)

# Marker appended to every value that was fabricated rather than measured, so a
# screenshot of a demo can never be mistaken for a real incident response.
SIMULATED_SUFFIX = " (simulated)"


def _not_configured_response(subsystem: str, missing: str) -> dict:
    """Ephemeral Slack reply for a command whose backend is not wired up.

    Slack renders a non-2xx response as a generic failure with no explanation,
    so we answer 200 with an ephemeral message that names the missing piece.
    The point is that we never hand back invented data: the operator is told
    plainly that there is nothing behind this command in this deployment.
    """
    return {
        "response_type": "ephemeral",
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f":warning: *{subsystem} is not configured*\n"
                        f"{missing}\n"
                        "No data is being reported because none was collected."
                    ),
                },
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": "Set `COPILOT_DEMO_MODE=true` for a clearly-labelled simulated response.",
                    },
                ],
            },
        ],
    }


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
    simulated: bool = False,
) -> list:
    """Build Slack blocks for RCA results.

    ``simulated`` labels the card end to end: a reader scrolling past the header
    must be able to tell that the root cause and confidence below were invented.
    """
    confidence_emoji = ":large_green_circle:" if confidence >= 80 else ":large_yellow_circle:" if confidence >= 50 else ":red_circle:"

    evidence_text = "\n".join([f"• {e}" for e in evidence[:5]])

    header_text = ":white_check_mark: Investigation Complete"
    if simulated:
        header_text += SIMULATED_SUFFIX

    return [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": header_text,
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
                    "text": f"*Confidence:*\n{confidence_emoji} {confidence}%"
                    + (SIMULATED_SUFFIX if simulated else ""),
                },
                {
                    "type": "mrkdwn",
                    # "Verified" would be an outright lie for a fabricated card.
                    "text": "*Status:*\n:test_tube: Simulated - not a real finding"
                    if simulated
                    else "*Status:*\n:white_check_mark: Verified",
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

        # No RCA engine is wired into this router. Until one is, the only answer
        # we could produce is a fabricated one - and a made-up root cause posted
        # into an incident thread is worse than no answer at all, because
        # responders act on it. So we say we have nothing instead.
        if not get_settings().copilot_demo_mode:
            logger.warning(
                "Slack investigation requested but no RCA engine is configured for "
                "app.slack_bot.router; refusing to post a fabricated root cause"
            )
            await update_slack_message(
                channel,
                message_ts,
                "Investigation backend not configured",
                [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": (
                                ":warning: *Investigation backend not configured*\n"
                                f"*Query:* {query}\n"
                                "This deployment has no RCA engine wired to the Slack bot, "
                                "so no root cause was produced. Nothing was analysed."
                            ),
                        },
                    },
                    {
                        "type": "context",
                        "elements": [
                            {
                                "type": "mrkdwn",
                                "text": "Set `COPILOT_DEMO_MODE=true` for a clearly-labelled simulated investigation.",
                            },
                        ],
                    },
                ],
            )
            await add_reaction(channel, thread_ts, "warning")
            return

        # Demo mode: the canned investigation below is fiction, and every line of
        # it is labelled as such.
        logger.warning(
            "COPILOT_DEMO_MODE=true - posting a SIMULATED investigation result to channel %s",
            channel,
        )

        import asyncio
        await asyncio.sleep(3)

        result_blocks = build_rca_result_blocks(
            query=query,
            root_cause=(
                "Database connection pool exhaustion due to connection leak in "
                "payment-service v2.4.1" + SIMULATED_SUFFIX
            ),
            confidence=87,
            evidence=[
                "Error rate spiked 300% at 14:32 UTC" + SIMULATED_SUFFIX,
                "payment-service v2.4.1 deployed at 14:15 UTC" + SIMULATED_SUFFIX,
                "DB connection count at max (100/100)" + SIMULATED_SUFFIX,
                "Similar pattern in commit abc123 (connection leak)" + SIMULATED_SUFFIX,
            ],
            recommendation=(
                "Rollback payment-service to v2.4.0. The connection leak was introduced "
                "in the refactored database client." + SIMULATED_SUFFIX
            ),
            simulated=True,
        )

        await update_slack_message(
            channel, message_ts, "Investigation complete" + SIMULATED_SUFFIX, result_blocks
        )

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
                    "Hi! I'm OmniPotent. Mention me with a question like:\n"
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

    # There is no observability backend behind this command; the numbers below
    # are literals. Reporting "checkout: degraded, 230ms" while nothing was
    # measured is exactly how a responder ends up chasing a phantom.
    if not get_settings().copilot_demo_mode:
        logger.warning(
            "/status requested but no observability backend is configured for the "
            "Slack bot; refusing to report fabricated service health"
        )
        return _not_configured_response(
            "Service status",
            "No monitoring source is wired to the Slack `/status` command in this deployment.",
        )

    logger.warning("COPILOT_DEMO_MODE=true - returning SIMULATED service health for /status")

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
            "simulated": True,
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*{service.title()} Service Status*{SIMULATED_SUFFIX}\n{status_emoji} {s['status'].title()}\n• Latency: {s['latency']}\n• Error Rate: {s['error_rate']}",
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
                "text": ":bar_chart: System Status" + SIMULATED_SUFFIX,
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
                "text": f"{status_emoji} *{name}*: {s['status'].title()} | {s['latency']} | {s['error_rate']} errors{SIMULATED_SUFFIX}",
            }
        })

    return {"response_type": "ephemeral", "simulated": True, "blocks": blocks}


@router.post("/commands/oncall")
async def slash_oncall(request: Request):
    """Handle /oncall slash command - who's on call."""
    form_data = await request.form()
    team_raw = form_data.get("text", "")
    team = str(team_raw).strip() if team_raw else ""

    # No scheduling provider (PagerDuty/Opsgenie) is wired to this command, so
    # the rota below is invented. Naming a person who is not actually on call is
    # the worst possible failure mode here: the real on-call never gets paged.
    if not get_settings().copilot_demo_mode:
        logger.warning(
            "/oncall requested but no on-call schedule provider is configured for the "
            "Slack bot; refusing to name fabricated responders"
        )
        return _not_configured_response(
            "On-call schedule",
            "No on-call provider (PagerDuty/Opsgenie) is wired to the Slack `/oncall` command "
            "in this deployment.",
        )

    logger.warning("COPILOT_DEMO_MODE=true - returning a SIMULATED on-call rota for /oncall")

    oncall_data = {
        "platform": {"primary": "Alice Smith", "secondary": "Bob Jones", "escalation": "#platform-oncall"},
        "infra": {"primary": "Charlie Brown", "secondary": "Diana Prince", "escalation": "#infra-oncall"},
        "security": {"primary": "Eve Wilson", "secondary": "Frank Miller", "escalation": "#security-oncall"},
    }

    if team and team in oncall_data:
        o = oncall_data[team]
        return {
            "response_type": "ephemeral",
            "simulated": True,
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*{team.title()} On-Call*{SIMULATED_SUFFIX}\n:bust_in_silhouette: Primary: {o['primary']}{SIMULATED_SUFFIX}\n:busts_in_silhouette: Secondary: {o['secondary']}{SIMULATED_SUFFIX}\n:mega: Escalation: {o['escalation']}",
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
                "text": ":telephone_receiver: On-Call Schedule" + SIMULATED_SUFFIX,
                "emoji": True,
            }
        }
    ]

    for name, o in oncall_data.items():
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*{name.title()}*: {o['primary']} (backup: {o['secondary']}){SIMULATED_SUFFIX}",
            }
        })

    return {"response_type": "ephemeral", "simulated": True, "blocks": blocks}


@router.post("/commands/incident")
async def slash_incident(request: Request, background_tasks: BackgroundTasks):
    """Handle /incident slash command - create, update, resolve incidents."""
    form_data = await request.form()
    text_raw = form_data.get("text", "")
    text = str(text_raw).strip() if text_raw else ""
    user_id = str(form_data.get("user_id", ""))
    _ = form_data.get("user_name", "")  # Available for future use
    _ = form_data.get("channel_id", "")  # Available for future use
    _ = form_data.get("trigger_id", "")  # Available for future use
    _ = form_data.get("response_url", "")  # Available for future use

    parts = text.split(maxsplit=1)
    subcommand = parts[0].lower() if parts else "help"
    args = parts[1] if len(parts) > 1 else ""

    if subcommand == "create":
        if not args:
            # Open modal for incident creation
            return {
                "response_type": "ephemeral",
                "text": "Opening incident creation form...",
            }

        # Quick create with title
        from datetime import datetime, timezone
        incident_id = f"INC-{int(datetime.now(timezone.utc).timestamp()) % 100000}"

        from app.slack_bot.messages import get_message_builder, IncidentData
        builder = get_message_builder()
        incident = IncidentData(
            incident_id=incident_id,
            title=args,
            severity="P2",
            status="open",
            services=[],
            responders=[user_id],
            started_at=datetime.now(timezone.utc),
        )

        blocks = builder.incident_card(incident, show_actions=True)

        return {
            "response_type": "in_channel",
            "blocks": blocks,
        }

    elif subcommand == "update":
        return {
            "response_type": "in_channel",
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f":speech_balloon: *Status Update* from <@{user_id}>:\n\n{args}",
                    },
                },
            ],
        }

    elif subcommand == "resolve":
        return {
            "response_type": "in_channel",
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f":white_check_mark: *Incident Resolved* by <@{user_id}>\n\n{args or 'No resolution notes provided.'}",
                    },
                },
            ],
        }

    elif subcommand == "escalate":
        return {
            "response_type": "in_channel",
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f":arrow_up: *Incident Escalated* by <@{user_id}>\n\n{args or 'Escalating to next tier.'}",
                    },
                },
            ],
        }

    elif subcommand == "severity":
        severity = args.upper() if args else "P2"
        return {
            "response_type": "in_channel",
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f":chart_with_upwards_trend: *Severity Updated* to {severity} by <@{user_id}>",
                    },
                },
            ],
        }

    else:
        return {
            "response_type": "ephemeral",
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            "*`/incident` Commands:*\n\n"
                            "* `/incident create <title>` - Create a new incident\n"
                            "* `/incident update <message>` - Post a status update\n"
                            "* `/incident resolve [notes]` - Resolve the incident\n"
                            "* `/incident escalate [reason]` - Escalate to next tier\n"
                            "* `/incident severity <P1|P2|P3|P4>` - Change severity"
                        ),
                    },
                },
            ],
        }


@router.post("/commands/warroom")
async def slash_warroom(request: Request, background_tasks: BackgroundTasks):
    """Handle /warroom slash command - create and manage war rooms."""
    form_data = await request.form()
    text_raw = form_data.get("text", "")
    text = str(text_raw).strip() if text_raw else ""
    user_id = str(form_data.get("user_id", ""))
    _ = form_data.get("channel_id", "")  # Available for future use

    parts = text.split(maxsplit=1)
    subcommand = parts[0].lower() if parts else "create"
    args = parts[1] if len(parts) > 1 else ""

    if subcommand == "create":
        from datetime import datetime, timezone
        incident_id = f"INC-{int(datetime.now(timezone.utc).timestamp()) % 100000}"
        channel_name = f"inc-{incident_id.lower()}-warroom"

        return {
            "response_type": "in_channel",
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f":mega: *War Room Created*\n\n"
                            f"*Channel:* #{channel_name}\n"
                            f"*Created by:* <@{user_id}>\n"
                            f"*Incident:* {args or incident_id}"
                        ),
                    },
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Join War Room", "emoji": True},
                            "action_id": f"join_warroom_{incident_id}",
                            "style": "primary",
                        },
                    ],
                },
            ],
        }

    elif subcommand == "list":
        from app.slack_bot.war_room import get_war_room_manager
        manager = get_war_room_manager()
        active_rooms = manager.get_active_war_rooms()

        if not active_rooms:
            return {
                "response_type": "ephemeral",
                "text": "No active war rooms.",
            }

        rooms_text = "\n".join(
            f"* #{wr.channel_name} - {wr.severity} {wr.title} ({wr.status.value})"
            for wr in active_rooms[:10]
        )

        return {
            "response_type": "ephemeral",
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Active War Rooms ({len(active_rooms)}):*\n\n{rooms_text}",
                    },
                },
            ],
        }

    else:
        return {
            "response_type": "ephemeral",
            "text": "Usage: `/warroom create [title]` or `/warroom list`",
        }


@router.post("/commands/postmortem")
async def slash_postmortem(request: Request):
    """Handle /postmortem slash command - generate and manage postmortems."""
    form_data = await request.form()
    text_raw = form_data.get("text", "")
    text = str(text_raw).strip() if text_raw else ""
    user_id = str(form_data.get("user_id", ""))

    parts = text.split(maxsplit=1)
    subcommand = parts[0].lower() if parts else "generate"
    args = parts[1] if len(parts) > 1 else ""

    if subcommand == "generate":
        incident_id = args or "current"
        return {
            "response_type": "in_channel",
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f":page_facing_up: *Generating Postmortem* for {incident_id}...\n\n"
                            f"Requested by <@{user_id}>"
                        ),
                    },
                },
                {
                    "type": "context",
                    "elements": [
                        {"type": "mrkdwn", "text": "This may take a few moments. You'll be notified when ready."},
                    ],
                },
            ],
        }

    elif subcommand == "actions":
        # The list below is a canned example, not a query against postmortem
        # records - a team could otherwise believe follow-up work is tracked
        # (and one item already "done") when nothing of the sort exists.
        if not get_settings().copilot_demo_mode:
            logger.warning(
                "/postmortem actions requested but no postmortem action-item store is "
                "configured for the Slack bot; refusing to list fabricated action items"
            )
            return _not_configured_response(
                "Postmortem action item tracking",
                "No postmortem store is wired to the Slack `/postmortem` command in this deployment.",
            )

        logger.warning(
            "COPILOT_DEMO_MODE=true - returning SIMULATED postmortem action items"
        )
        return {
            "response_type": "ephemeral",
            "simulated": True,
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f"*Action Items from Recent Incidents:*{SIMULATED_SUFFIX}\n\n"
                            "1. :white_large_square: Add connection pool monitoring\n"
                            "2. :white_large_square: Implement circuit breaker for payment-service\n"
                            "3. :white_check_mark: Add pre-deploy health checks\n"
                            "4. :white_large_square: Update runbook for DB failover"
                        ),
                    },
                },
            ],
        }

    elif subcommand == "share":
        return {
            "response_type": "ephemeral",
            "text": f"Sharing postmortem for {args}. Choose destination channel.",
        }

    else:
        return {
            "response_type": "ephemeral",
            "text": "Usage: `/postmortem generate [incident_id]` | `/postmortem actions` | `/postmortem share <incident_id>`",
        }


@router.post("/commands/stakeholder")
async def slash_stakeholder(request: Request):
    """Handle /stakeholder slash command - post stakeholder updates."""
    form_data = await request.form()
    text_raw = form_data.get("text", "")
    text = str(text_raw).strip() if text_raw else ""
    user_id = str(form_data.get("user_id", ""))

    if not text:
        return {
            "response_type": "ephemeral",
            "text": "Usage: `/stakeholder <update message>` - Posts an update to stakeholder channels.",
        }

    from datetime import datetime, timezone

    return {
        "response_type": "in_channel",
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": ":mega: Stakeholder Update",
                    "emoji": True,
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": text,
                },
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": (
                            f"Posted by <@{user_id}> at "
                            f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
                        ),
                    },
                ],
            },
        ],
    }


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
                    "text": ":robot_face: OmniPotent Help",
                    "emoji": True,
                }
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*Incident Management:*",
                }
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        "* `/incident create <title>` - Create a new incident\n"
                        "* `/incident update <message>` - Post status update\n"
                        "* `/incident resolve [notes]` - Resolve incident\n"
                        "* `/incident escalate [reason]` - Escalate incident\n"
                        "* `/warroom create [title]` - Create war room\n"
                        "* `/stakeholder <message>` - Post stakeholder update"
                    ),
                }
            },
            {"type": "divider"},
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*Investigation & Monitoring:*",
                }
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        "* `/rca <query>` - Start AI investigation\n"
                        "* `/status [service]` - Check system status\n"
                        "* `/oncall [team]` - See who's on call\n"
                        "* `/postmortem generate` - Generate postmortem"
                    ),
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
                    "text": (
                        "* `@OmniPotent why is X slow?`\n"
                        "* `@OmniPotent investigate Y`\n"
                        "* `@OmniPotent what changed recently?`"
                    ),
                }
            },
            {"type": "divider"},
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        "*Interactive Features:*\n"
                        "Use buttons on incident cards for quick actions like Acknowledge, Escalate, Resolve, and Snooze."
                    ),
                }
            },
        ]
    }


@router.post("/interactivity")
async def slack_interactivity(request: Request, background_tasks: BackgroundTasks):
    """Handle Slack interactive components (buttons, modals)."""
    form_data = await request.form()
    payload_raw = form_data.get("payload", "{}")
    payload_str = str(payload_raw) if payload_raw else "{}"
    payload = json.loads(payload_str)

    action_type = payload.get("type")
    user = payload.get("user", {})
    user_id = user.get("id", "")
    channel = payload.get("channel", {})
    channel_id = channel.get("id", "")
    trigger_id = payload.get("trigger_id", "")
    response_url = payload.get("response_url", "")

    if action_type == "block_actions":
        actions = payload.get("actions", [])
        for action in actions:
            action_id = action.get("action_id", "")
            _ = action.get("value", "")  # Available for action-specific handlers

            # Incident action handlers
            if action_id.startswith("ack_") or action_id.startswith("acknowledge_"):
                incident_id = action_id.split("_", 1)[1]
                return await _handle_incident_action(
                    "acknowledge", incident_id, user_id, channel_id, response_url
                )

            elif action_id.startswith("escalate_"):
                incident_id = action_id.replace("escalate_", "")
                # Open escalation modal
                from app.slack_bot.interactions import get_incident_action_handler
                handler = get_incident_action_handler()
                return {
                    "response_action": "open_modal",
                    "view": handler.get_escalation_modal(incident_id, trigger_id),
                }

            elif action_id.startswith("resolve_"):
                incident_id = action_id.replace("resolve_", "")
                from app.slack_bot.interactions import get_incident_action_handler
                handler = get_incident_action_handler()
                return {
                    "response_action": "open_modal",
                    "view": handler.get_resolution_modal(incident_id, trigger_id),
                }

            elif action_id.startswith("snooze_"):
                incident_id = action_id.replace("snooze_", "")
                from app.slack_bot.interactions import get_incident_action_handler
                handler = get_incident_action_handler()
                return {
                    "response_action": "open_modal",
                    "view": handler.get_snooze_options_modal(incident_id, trigger_id),
                }

            elif action_id.startswith("warroom_") or action_id.startswith("create_war_room_"):
                incident_id = action_id.split("_")[-1]
                return await _handle_incident_action(
                    "create_war_room", incident_id, user_id, channel_id, response_url
                )

            elif action_id.startswith("update_") or action_id.startswith("post_update_"):
                incident_id = action_id.split("_")[-1]
                return {
                    "response_action": "open_modal",
                    "view": _get_status_update_modal(incident_id, trigger_id),
                }

            elif action_id.startswith("investigate_"):
                incident_id = action_id.replace("investigate_", "")
                from app.slack_bot.blocks import get_block_builder
                builder = get_block_builder()
                blocks = builder.investigation_started(
                    query=f"Investigating incident {incident_id}",
                    user_id=user_id,
                )
                return {"blocks": blocks, "response_type": "in_channel"}

            elif action_id.startswith("fix_"):
                incident_id = action_id.replace("fix_", "")
                return {"text": f":hammer_and_wrench: Generating fix PR for {incident_id}..."}

            elif action_id.startswith("rollback_"):
                incident_id = action_id.replace("rollback_", "")
                from app.slack_bot.blocks import get_block_builder
                builder = get_block_builder()
                blocks = builder.approval_card(
                    approval_id=f"rollback_{incident_id}",
                    action_type="Rollback Deployment",
                    description=f"Rollback to previous version for {incident_id}",
                    details={"incident_id": incident_id},
                    risk_level="medium",
                    requested_by=user_id,
                )
                return {"blocks": blocks}

            # Feedback handlers
            elif action_id == "feedback_helpful":
                logger.info(f"Positive feedback from user {user_id}")
                return {"text": "Thanks for the feedback! :thumbsup:"}

            elif action_id == "feedback_not_helpful":
                return {
                    "response_action": "open_modal",
                    "view": {
                        "type": "modal",
                        "callback_id": "feedback_modal",
                        "title": {"type": "plain_text", "text": "Improve AI Response"},
                        "submit": {"type": "plain_text", "text": "Submit"},
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
                                "label": {"type": "plain_text", "text": "Your Feedback"},
                            }
                        ],
                    },
                }

            # Overflow menu actions
            elif action_id.startswith("more_"):
                selected = action.get("selected_option", {}).get("value", "")
                if selected.startswith("add_responder_"):
                    incident_id = selected.replace("add_responder_", "")
                    return {
                        "response_action": "open_modal",
                        "view": _get_add_responder_modal(incident_id, trigger_id),
                    }
                elif selected.startswith("severity_"):
                    incident_id = selected.replace("severity_", "")
                    return {
                        "response_action": "open_modal",
                        "view": _get_severity_modal(incident_id, trigger_id),
                    }
                elif selected.startswith("timeline_"):
                    incident_id = selected.replace("timeline_", "")
                    from app.slack_bot.blocks import get_block_builder
                    builder = get_block_builder()
                    blocks = builder.timeline_card(
                        incident_id=incident_id,
                        events=[
                            {"time": "Now", "event": "Timeline requested"},
                        ],
                    )
                    return {"blocks": blocks, "response_type": "ephemeral"}

    elif action_type == "view_submission":
        callback_id = payload.get("view", {}).get("callback_id", "")
        values = payload.get("view", {}).get("state", {}).get("values", {})

        if callback_id == "feedback_modal":
            feedback = values.get("feedback_input", {}).get("feedback_text", {}).get("value", "")
            logger.info(f"Received feedback from {user_id}: {feedback}")
            return {"response_action": "clear"}

        elif callback_id.startswith("escalate_submit_"):
            incident_id = callback_id.replace("escalate_submit_", "")
            level = values.get("escalation_level", {}).get("level_select", {}).get("selected_option", {}).get("value", "L2")
            team = values.get("escalation_team", {}).get("team_select", {}).get("selected_option", {}).get("value", "on-call")
            reason = values.get("escalation_reason", {}).get("reason_input", {}).get("value", "")

            from app.slack_bot.interactions import get_incident_action_handler
            handler = get_incident_action_handler()
            result = await handler.handle_action(
                action_id=f"escalate_{incident_id}",
                incident_id=incident_id,
                user_id=user_id,
                channel_id=channel_id,
                action_type="escalate",
                action_value=None,
            )
            result.details.update({"level": level, "team": team, "reason": reason})

            if response_url:
                background_tasks.add_task(_send_response, response_url, result.blocks)

            return {"response_action": "clear"}

        elif callback_id.startswith("resolve_submit_"):
            incident_id = callback_id.replace("resolve_submit_", "")
            resolution_type = values.get("resolution_type", {}).get("type_select", {}).get("selected_option", {}).get("value", "fixed")
            _ = values.get("resolution_summary", {}).get("summary_input", {}).get("value", "")

            from app.slack_bot.interactions import get_incident_action_handler
            handler = get_incident_action_handler()
            await handler.handle_action(
                action_id=f"resolve_{incident_id}",
                incident_id=incident_id,
                user_id=user_id,
                channel_id=channel_id,
                action_type="resolve",
                action_value=resolution_type,
            )

            return {"response_action": "clear"}

        elif callback_id.startswith("snooze_submit_"):
            incident_id = callback_id.replace("snooze_submit_", "")
            snooze_duration = values.get("snooze_duration", {}).get("duration_select", {}).get("selected_option", {}).get("value", "30")

            from app.slack_bot.interactions import get_incident_action_handler
            handler = get_incident_action_handler()
            await handler.handle_action(
                action_id=f"snooze_{incident_id}",
                incident_id=incident_id,
                user_id=user_id,
                channel_id=channel_id,
                action_type="snooze",
                action_value=snooze_duration,
            )

            return {"response_action": "clear"}

        elif callback_id.startswith("status_update_"):
            incident_id = callback_id.replace("status_update_", "")
            _ = values.get("update_type", {}).get("type_select", {}).get("selected_option", {}).get("value", "update")
            status_update_text = values.get("update_text", {}).get("text_input", {}).get("value", "")

            from app.slack_bot.interactions import get_incident_action_handler
            handler = get_incident_action_handler()
            await handler.handle_action(
                action_id=f"update_{incident_id}",
                incident_id=incident_id,
                user_id=user_id,
                channel_id=channel_id,
                action_type="post_update",
                action_value=status_update_text,
            )

            return {"response_action": "clear"}

    elif action_type == "shortcut":
        callback_id = payload.get("callback_id", "")

        if callback_id == "create_incident":
            return {
                "response_action": "open_modal",
                "view": _get_create_incident_modal(trigger_id),
            }

        elif callback_id == "quick_investigate":
            return {
                "response_action": "open_modal",
                "view": _get_quick_investigate_modal(trigger_id),
            }

    return {"ok": True}


async def _handle_incident_action(
    action_type: str,
    incident_id: str,
    user_id: str,
    channel_id: str,
    response_url: str,
) -> dict:
    """Handle an incident action."""
    from app.slack_bot.interactions import get_incident_action_handler

    handler = get_incident_action_handler()
    result = await handler.handle_action(
        action_id=f"{action_type}_{incident_id}",
        incident_id=incident_id,
        user_id=user_id,
        channel_id=channel_id,
        action_type=action_type,
    )

    if result.blocks:
        return {"blocks": result.blocks}
    return {"text": result.message}


async def _send_response(response_url: str, blocks: list[dict]) -> None:
    """Send a response to Slack via response_url."""
    async with httpx.AsyncClient() as client:
        await client.post(
            response_url,
            json={"blocks": blocks, "response_type": "in_channel"},
        )


def _get_status_update_modal(incident_id: str, trigger_id: str) -> dict:
    """Get status update modal."""
    return {
        "type": "modal",
        "callback_id": f"status_update_{incident_id}",
        "title": {"type": "plain_text", "text": "Post Status Update"},
        "submit": {"type": "plain_text", "text": "Post"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "input",
                "block_id": "update_type",
                "label": {"type": "plain_text", "text": "Update Type"},
                "element": {
                    "type": "static_select",
                    "action_id": "type_select",
                    "options": [
                        {"text": {"type": "plain_text", "text": "Progress Update"}, "value": "progress"},
                        {"text": {"type": "plain_text", "text": "Investigation Update"}, "value": "investigating"},
                        {"text": {"type": "plain_text", "text": "Root Cause Identified"}, "value": "identified"},
                        {"text": {"type": "plain_text", "text": "Mitigation Applied"}, "value": "mitigation"},
                        {"text": {"type": "plain_text", "text": "Monitoring"}, "value": "monitoring"},
                    ],
                },
            },
            {
                "type": "input",
                "block_id": "update_text",
                "label": {"type": "plain_text", "text": "Update Message"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "text_input",
                    "multiline": True,
                    "placeholder": {"type": "plain_text", "text": "What's the current status?"},
                },
            },
            {
                "type": "input",
                "block_id": "stakeholder_update",
                "optional": True,
                "label": {"type": "plain_text", "text": "Post to Stakeholder Channel"},
                "element": {
                    "type": "checkboxes",
                    "action_id": "stakeholder_check",
                    "options": [
                        {"text": {"type": "plain_text", "text": "Also post to stakeholder channel"}, "value": "yes"},
                    ],
                },
            },
        ],
    }


def _get_add_responder_modal(incident_id: str, trigger_id: str) -> dict:
    """Get add responder modal."""
    return {
        "type": "modal",
        "callback_id": f"add_responder_{incident_id}",
        "title": {"type": "plain_text", "text": "Add Responder"},
        "submit": {"type": "plain_text", "text": "Add"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "input",
                "block_id": "responder",
                "label": {"type": "plain_text", "text": "Select Responder"},
                "element": {
                    "type": "users_select",
                    "action_id": "user_select",
                    "placeholder": {"type": "plain_text", "text": "Choose a user"},
                },
            },
            {
                "type": "input",
                "block_id": "role",
                "optional": True,
                "label": {"type": "plain_text", "text": "Role (optional)"},
                "element": {
                    "type": "static_select",
                    "action_id": "role_select",
                    "options": [
                        {"text": {"type": "plain_text", "text": "Responder"}, "value": "responder"},
                        {"text": {"type": "plain_text", "text": "Incident Commander"}, "value": "commander"},
                        {"text": {"type": "plain_text", "text": "Communications Lead"}, "value": "comms"},
                        {"text": {"type": "plain_text", "text": "Subject Matter Expert"}, "value": "sme"},
                    ],
                },
            },
        ],
    }


def _get_severity_modal(incident_id: str, trigger_id: str) -> dict:
    """Get severity update modal."""
    return {
        "type": "modal",
        "callback_id": f"severity_update_{incident_id}",
        "title": {"type": "plain_text", "text": "Update Severity"},
        "submit": {"type": "plain_text", "text": "Update"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "input",
                "block_id": "severity",
                "label": {"type": "plain_text", "text": "New Severity"},
                "element": {
                    "type": "static_select",
                    "action_id": "severity_select",
                    "options": [
                        {"text": {"type": "plain_text", "text": "P1 - Critical"}, "value": "P1"},
                        {"text": {"type": "plain_text", "text": "P2 - High"}, "value": "P2"},
                        {"text": {"type": "plain_text", "text": "P3 - Medium"}, "value": "P3"},
                        {"text": {"type": "plain_text", "text": "P4 - Low"}, "value": "P4"},
                    ],
                },
            },
            {
                "type": "input",
                "block_id": "reason",
                "optional": True,
                "label": {"type": "plain_text", "text": "Reason for Change"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "reason_input",
                    "placeholder": {"type": "plain_text", "text": "Why is severity changing?"},
                },
            },
        ],
    }


def _get_create_incident_modal(trigger_id: str) -> dict:
    """Get create incident modal."""
    return {
        "type": "modal",
        "callback_id": "create_incident_submit",
        "title": {"type": "plain_text", "text": "Create Incident"},
        "submit": {"type": "plain_text", "text": "Create"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "input",
                "block_id": "title",
                "label": {"type": "plain_text", "text": "Incident Title"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "title_input",
                    "placeholder": {"type": "plain_text", "text": "Brief description of the incident"},
                },
            },
            {
                "type": "input",
                "block_id": "severity",
                "label": {"type": "plain_text", "text": "Severity"},
                "element": {
                    "type": "static_select",
                    "action_id": "severity_select",
                    "options": [
                        {"text": {"type": "plain_text", "text": "P1 - Critical"}, "value": "P1"},
                        {"text": {"type": "plain_text", "text": "P2 - High"}, "value": "P2"},
                        {"text": {"type": "plain_text", "text": "P3 - Medium"}, "value": "P3"},
                        {"text": {"type": "plain_text", "text": "P4 - Low"}, "value": "P4"},
                    ],
                    "initial_option": {"text": {"type": "plain_text", "text": "P2 - High"}, "value": "P2"},
                },
            },
            {
                "type": "input",
                "block_id": "description",
                "optional": True,
                "label": {"type": "plain_text", "text": "Description"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "description_input",
                    "multiline": True,
                    "placeholder": {"type": "plain_text", "text": "What's happening? What's the impact?"},
                },
            },
            {
                "type": "input",
                "block_id": "services",
                "optional": True,
                "label": {"type": "plain_text", "text": "Affected Services"},
                "element": {
                    "type": "multi_static_select",
                    "action_id": "services_select",
                    "placeholder": {"type": "plain_text", "text": "Select services"},
                    "options": [
                        {"text": {"type": "plain_text", "text": "API"}, "value": "api"},
                        {"text": {"type": "plain_text", "text": "Web"}, "value": "web"},
                        {"text": {"type": "plain_text", "text": "Database"}, "value": "database"},
                        {"text": {"type": "plain_text", "text": "Payment"}, "value": "payment"},
                        {"text": {"type": "plain_text", "text": "Authentication"}, "value": "auth"},
                    ],
                },
            },
            {
                "type": "input",
                "block_id": "war_room",
                "optional": True,
                "label": {"type": "plain_text", "text": "Create War Room"},
                "element": {
                    "type": "checkboxes",
                    "action_id": "warroom_check",
                    "options": [
                        {"text": {"type": "plain_text", "text": "Create dedicated incident channel"}, "value": "yes"},
                    ],
                },
            },
        ],
    }


def _get_quick_investigate_modal(trigger_id: str) -> dict:
    """Get quick investigate modal."""
    return {
        "type": "modal",
        "callback_id": "quick_investigate_submit",
        "title": {"type": "plain_text", "text": "Quick Investigate"},
        "submit": {"type": "plain_text", "text": "Investigate"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "input",
                "block_id": "query",
                "label": {"type": "plain_text", "text": "What's the issue?"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "query_input",
                    "multiline": True,
                    "placeholder": {"type": "plain_text", "text": "e.g., checkout-api is returning 500 errors"},
                },
            },
            {
                "type": "input",
                "block_id": "timerange",
                "optional": True,
                "label": {"type": "plain_text", "text": "Time Range"},
                "element": {
                    "type": "static_select",
                    "action_id": "timerange_select",
                    "options": [
                        {"text": {"type": "plain_text", "text": "Last 15 minutes"}, "value": "15m"},
                        {"text": {"type": "plain_text", "text": "Last 1 hour"}, "value": "1h"},
                        {"text": {"type": "plain_text", "text": "Last 6 hours"}, "value": "6h"},
                        {"text": {"type": "plain_text", "text": "Last 24 hours"}, "value": "24h"},
                    ],
                    "initial_option": {"text": {"type": "plain_text", "text": "Last 1 hour"}, "value": "1h"},
                },
            },
        ],
    }
