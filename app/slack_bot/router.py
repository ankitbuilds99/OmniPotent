"""Slack Bot Router - @mention support and slash commands."""

from fastapi import APIRouter, Depends, HTTPException, Request, BackgroundTasks
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

logger = logging.getLogger(__name__)


class SlackConfig:
    """Slack app configuration - loaded from environment variables.

    Read via the properties below rather than the class attributes: the class
    attributes are bound at import time, so a secret exported after import (tests,
    lazily-loaded env files) would be silently missed.
    """

    @staticmethod
    def signing_secret() -> str:
        return os.getenv("SLACK_SIGNING_SECRET", "")

    @staticmethod
    def bot_token() -> str:
        return os.getenv("SLACK_BOT_TOKEN", "")

    SIGNING_SECRET: str = os.getenv("SLACK_SIGNING_SECRET", "")
    BOT_TOKEN: str = os.getenv("SLACK_BOT_TOKEN", "")
    APP_TOKEN: str = os.getenv("SLACK_APP_TOKEN", "")


def verify_slack_signature(
    body: bytes, timestamp: str, signature: str, signing_secret: str
) -> bool:
    """Verify Slack request signature."""
    if not signing_secret or not signature or not timestamp:
        return False

    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        return False

    if abs(time.time() - ts) > 60 * 5:
        return False

    sig_basestring = b"v0:" + timestamp.encode() + b":" + body
    computed = "v0=" + hmac.new(
        signing_secret.encode(),
        sig_basestring,
        hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(computed, signature)


async def enforce_slack_signature(request: Request) -> None:
    """Router-level gate: every Slack route must carry a valid Slack signature.

    Fails closed when SLACK_SIGNING_SECRET is unset — an unconfigured secret must
    not mean "accept everything" on routes that mutate incidents and escalations.
    """
    secret = SlackConfig.signing_secret()
    if not secret:
        logger.error("Rejecting Slack request: SLACK_SIGNING_SECRET is not configured")
        raise HTTPException(
            status_code=503, detail="Slack integration is not configured."
        )

    body = await request.body()
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")

    if not verify_slack_signature(body, timestamp, signature, secret):
        logger.warning("Rejected Slack request with invalid signature")
        raise HTTPException(status_code=401, detail="Invalid Slack signature")


# Every route on this router mutates incident/escalation state or triggers an
# investigation, so the signature gate is attached at the router level rather
# than per-handler — a new route cannot forget it.
router = APIRouter(
    prefix="/slack",
    tags=["slack"],
    dependencies=[Depends(enforce_slack_signature)],
)


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
                "Authorization": f"Bearer {SlackConfig.bot_token()}",
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
                "Authorization": f"Bearer {SlackConfig.bot_token()}",
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
                "Authorization": f"Bearer {SlackConfig.bot_token()}",
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


async def run_investigation(
    query: str, channel: str, thread_ts: str, message_ts: str
) -> None:
    """Run a real AI investigation and update Slack with the result.

    This used to `asyncio.sleep(3)` and then post a fixed, invented root cause —
    "Database connection pool exhaustion ... in payment-service v2.4.1" — at a
    hardcoded 87% confidence, with four invented pieces of supporting evidence
    and a recommendation to roll a specific service back a specific version.
    Posted into an incident channel, next to a green check mark reading
    "Verified", that is an instruction to take a destructive action based on
    nothing at all. It now runs the same investigation path as every other
    trigger, and renders the same structured report the production Slack
    integration renders.
    """
    from app.integrations import slack as slack_blocks

    thread_id = f"slack-{channel}-{thread_ts}"
    try:
        await update_slack_message(
            channel,
            message_ts,
            "Investigating...",
            build_investigation_blocks(query, "investigating"),
        )

        await add_reaction(channel, thread_ts, "eyes")

        # Imported here, not at module scope: app.api.main includes this router,
        # so a top-level import would be circular.
        from app.api.main import _get_session, _running_turn

        async with _running_turn(thread_id):
            session = await _get_session(thread_id)
            result = await session.ask(query)

        if result.status == "awaiting_approval":
            req = result.approval_request or {}
            detail = "\n".join(
                f"• `{a.get('tool')}`" + (" *(write)*" if a.get("write") else "")
                for a in req.get("actions", [])
            ) or req.get("message", "")
            blocks = slack_blocks.approval_blocks(thread_id, query, detail)
            text = "Approval needed"
            reaction = "hourglass"
        elif result.report:
            blocks = slack_blocks.report_blocks(query, result.report, result.final_text)
            text = "Investigation complete"
            reaction = "white_check_mark"
        else:
            blocks = slack_blocks.result_blocks(query, result.final_text)
            text = "Investigation complete"
            reaction = "white_check_mark"

        await update_slack_message(channel, message_ts, text, blocks)
        await add_reaction(channel, thread_ts, reaction)

    except Exception as e:
        logger.exception("Investigation failed")
        await update_slack_message(
            channel,
            message_ts,
            f"Investigation failed: {str(e)}",
            [{"type": "section", "text": {"type": "mrkdwn", "text": f":x: Error: {str(e)}"}}],
        )
        await add_reaction(channel, thread_ts, "x")


@router.post("/events")
async def slack_events(request: Request, background_tasks: BackgroundTasks):
    """Handle Slack Events API - mentions and messages."""
    # Signature already verified by the router-level enforce_slack_signature gate.
    body = await request.body()

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


_STATUS_EMOJI = {
    "operational": ":large_green_circle:",
    "degraded": ":large_yellow_circle:",
    "partial_outage": ":large_orange_circle:",
    "major_outage": ":red_circle:",
    "maintenance": ":wrench:",
    "unknown": ":white_circle:",
}


@router.post("/commands/status")
async def slash_status(request: Request):
    """Handle /status slash command - current system status.

    This used to answer from a hardcoded dict: "checkout" was permanently
    degraded at 2.3% errors and "api" permanently healthy at 45ms, whatever was
    actually happening. Reporting a real outage as healthy is the failure mode
    that matters here, so it now reads the status registry and reports an empty
    registry as empty.
    """
    form_data = await request.form()
    service_raw = form_data.get("text", "")
    service = str(service_raw).strip() if service_raw else ""

    from app.status_page.components import get_status_registry

    try:
        registry = get_status_registry()
        components = registry.list_components(showcase_only=True)
    except Exception as e:  # noqa: BLE001
        logger.exception("/status lookup failed")
        return _ephemeral(f":x: Could not read component status: {e}")

    if not components:
        return _ephemeral(
            ":grey_question: No status components are configured, so there is "
            "nothing to report. Register components on the status page first."
        )

    if service:
        match = next(
            (
                c for c in components
                if service.lower() in (c.name.lower(), c.component_id.lower())
            ),
            None,
        )
        if match is None:
            known = ", ".join(sorted(c.name for c in components)[:20])
            return _ephemeral(
                f":grey_question: No component called *{service}*. Known: {known}"
            )
        emoji = _STATUS_EMOJI.get(match.status.value, ":white_circle:")
        checked = (
            match.last_checked.isoformat() if match.last_checked else "never checked"
        )
        return {
            "response_type": "ephemeral",
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f"*{match.name}*\n{emoji} "
                            f"{match.status.value.replace('_', ' ').title()}\n"
                            f"• Last checked: {checked}"
                        ),
                    }
                }
            ]
        }

    overall = registry.get_overall_status()
    blocks: list[dict] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": ":bar_chart: System Status",
                "emoji": True,
            }
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"{_STATUS_EMOJI.get(overall.value, ':white_circle:')} "
                    f"*Overall: {overall.value.replace('_', ' ').title()}*"
                ),
            }
        },
    ]

    for comp in components:
        emoji = _STATUS_EMOJI.get(comp.status.value, ":white_circle:")
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"{emoji} *{comp.name}*: "
                    f"{comp.status.value.replace('_', ' ').title()}"
                ),
            }
        })

    return {"response_type": "ephemeral", "blocks": blocks}


def _ephemeral(text: str) -> dict:
    """A plain ephemeral reply. Used for every "we don't know" answer, because
    the alternative — inventing a plausible one — is what this command used to do."""
    return {
        "response_type": "ephemeral",
        "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": text}}],
    }


async def _current_oncall(team_name: str = "") -> list[dict]:
    """Real rosters from the on-call schedules, newest-covering shift first.

    Returns one entry per team: {"team", "primary", "secondary"}, where the
    responder values are emails (or None when the rotation has nobody on call).
    """
    from sqlalchemy import select

    from app.database.engine import get_session
    from app.database.orgs import org_uuid
    from app.oncall.models import OncallTeam
    from app.oncall.schedule_service import ScheduleService

    rosters: list[dict] = []
    async with get_session() as session:
        query = select(OncallTeam).where(OncallTeam.org_id == org_uuid())
        if team_name:
            query = query.where(OncallTeam.name.ilike(team_name))
        teams = list((await session.execute(query)).scalars().all())

        service = ScheduleService(session)
        for team in teams:
            current = await service.get_current_oncall(team.id)
            emails = {
                p["user_id"]: p["user_email"]
                for p in current.get("participants", [])
            }
            rosters.append({
                "team": team.name,
                "primary": emails.get(current.get("primary")),
                "secondary": emails.get(current.get("secondary")),
            })
    return rosters


def _responder(email: str | None) -> str:
    return email or "_nobody on call_"


@router.post("/commands/oncall")
async def slash_oncall(request: Request):
    """Handle /oncall slash command - who's on call.

    This used to answer from a hardcoded dict of invented people — "Alice Smith",
    "Bob Jones", "Charlie Brown" — for three teams that need not exist. During an
    incident that is worse than no answer at all: the responder pages a name that
    was never real while the actual on-call keeps sleeping. It now reads the
    schedules, and says so plainly when there is nothing to read.
    """
    form_data = await request.form()
    team_raw = form_data.get("text", "")
    team = str(team_raw).strip() if team_raw else ""

    try:
        rosters = await _current_oncall(team)
    except Exception as e:  # noqa: BLE001
        logger.exception("/oncall lookup failed")
        return _ephemeral(f":x: Could not read the on-call schedule: {e}")

    if not rosters:
        if team:
            return _ephemeral(
                f":grey_question: No on-call team called *{team}*. "
                "Run `/oncall` with no arguments to list the teams that exist."
            )
        return _ephemeral(
            ":grey_question: No on-call teams are configured yet. "
            "Create a team and a schedule first — there is nobody to page."
        )

    if team and len(rosters) == 1:
        r = rosters[0]
        return {
            "response_type": "ephemeral",
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f"*{r['team']} On-Call*\n"
                            f":bust_in_silhouette: Primary: {_responder(r['primary'])}\n"
                            f":busts_in_silhouette: Secondary: {_responder(r['secondary'])}"
                        ),
                    },
                }
            ],
        }

    blocks: list[dict] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": ":telephone_receiver: On-Call Schedule",
                "emoji": True,
            }
        }
    ]

    for r in rosters:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*{r['team']}*: {_responder(r['primary'])}"
                    f" (backup: {_responder(r['secondary'])})"
                ),
            }
        })

    return {"response_type": "ephemeral", "blocks": blocks}


_SEVERITY_ALIASES = {
    "p1": "critical", "sev1": "critical", "critical": "critical",
    "p2": "high", "sev2": "high", "high": "high",
    "p3": "medium", "sev3": "medium", "medium": "medium",
    "p4": "low", "sev4": "low", "low": "low",
}

# Highest first, so "escalate" is a step towards the front of this list.
_SEVERITY_ORDER = ["critical", "high", "medium", "low"]


def _short(incident_id) -> str:
    """The first block of the incident UUID — enough to say out loud in a channel."""
    return str(incident_id)[:8]


async def _target_incident(args: str):
    """The incident a subcommand applies to, plus the leftover arguments.

    Slack hands us a channel, not an incident, so the id may be given as the
    first word; without one we take the most recent incident that is not yet
    resolved. Returning None rather than picking something is the whole point:
    these handlers used to post ":white_check_mark: Incident Resolved" into the
    channel with no incident behind it at all, and a channel that believes an
    incident is closed stops working it.
    """
    from uuid import UUID

    from app.incidents.models import IncidentStatus
    from app.incidents.store import get_incident_store

    store = get_incident_store()
    first, _, rest = args.partition(" ")
    if first.strip():
        try:
            candidate = await store.get_incident(UUID(first.strip()))
        except (ValueError, AttributeError, TypeError):
            candidate = None
        if candidate is not None:
            return candidate, rest.strip()

    open_incidents = [
        i for i in await store.list_incidents(limit=25)
        if i.status != IncidentStatus.RESOLVED
    ]
    return (open_incidents[0] if open_incidents else None), args.strip()


def _no_incident() -> dict:
    return _ephemeral(
        ":grey_question: There is no open incident to act on. Pass an incident "
        "id as the first argument, or declare one with `/incident create "
        "<title>`."
    )


def _incident_line(incident, headline: str, user_id: str) -> dict:
    """One in-channel block describing what was actually recorded."""
    return {
        "response_type": "in_channel",
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"{headline}\n\n"
                        f"*{incident.title}* (`{_short(incident.id)}`)\n"
                        f"• Severity: {incident.severity.value} "
                        f"• Status: {incident.status.value}"
                    ),
                },
            },
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": f"Recorded by <@{user_id}>"},
                ],
            },
        ],
    }


@router.post("/commands/incident")
async def slash_incident(request: Request, background_tasks: BackgroundTasks):
    """Handle /incident slash command - create, update, resolve incidents.

    Every subcommand here used to be a message and nothing else. `create`
    invented an id from the clock (`INC-{timestamp % 100000}`) and stored
    nothing, so the incident it announced could not be looked up, updated or
    resolved by anything afterwards. `update`, `resolve`, `escalate` and
    `severity` posted a confident line into the channel without an incident to
    apply it to — a channel reading "Incident Resolved" while the incident is
    open and still paging is worse than no command at all.

    Each subcommand now goes through the incident store and reports what the
    store did, including when it did nothing.
    """
    form_data = await request.form()
    text_raw = form_data.get("text", "")
    text = str(text_raw).strip() if text_raw else ""
    user_id = str(form_data.get("user_id", ""))
    user_name = str(form_data.get("user_name", "") or user_id)

    parts = text.split(maxsplit=1)
    subcommand = parts[0].lower() if parts else "help"
    args = parts[1] if len(parts) > 1 else ""

    from app.incidents.store import get_incident_store

    store = get_incident_store()

    if subcommand == "create":
        if not args:
            return _ephemeral(
                "Usage: `/incident create <title>` — the title is what the "
                "incident gets recorded under."
            )

        from app.incidents.models import IncidentSeverity

        try:
            incident = await store.create_incident(
                title=args,
                severity=IncidentSeverity.HIGH,
                service="unspecified",
                description=f"Declared from Slack by {user_name}",
            )
        except Exception as e:  # noqa: BLE001 — reported, not swallowed
            logger.exception("/incident create failed")
            return _ephemeral(f":x: Could not create the incident: {e}")

        return {
            "response_type": "in_channel",
            "blocks": [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": ":rotating_light: Incident Declared",
                        "emoji": True,
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f"*{incident.title}*\n"
                            f"• ID: `{incident.id}`\n"
                            f"• Severity: {incident.severity.value} "
                            f"(change with `/incident severity <P1-P4>`)\n"
                            f"• Service: {incident.service} — set it on the "
                            f"incident page if it matters for routing"
                        ),
                    },
                },
                {
                    "type": "context",
                    "elements": [
                        {"type": "mrkdwn", "text": f"Declared by <@{user_id}>"},
                    ],
                },
            ],
        }

    if subcommand in {"update", "resolve", "escalate", "severity", "ack", "acknowledge"}:
        incident, rest = await _target_incident(args)
        if incident is None:
            return _no_incident()

        try:
            if subcommand == "update":
                if not rest:
                    return _ephemeral("Usage: `/incident update [id] <message>`")
                updated = await store.add_timeline_note(
                    incident.id, rest, user=user_name, kind="status_update"
                )
                return _incident_line(
                    updated or incident,
                    f":speech_balloon: *Status update recorded*\n\n{rest}",
                    user_id,
                )

            if subcommand in {"ack", "acknowledge"}:
                updated = await store.acknowledge_incident(incident.id, user_name)
                return _incident_line(
                    updated or incident, ":eyes: *Acknowledged*", user_id
                )

            if subcommand == "resolve":
                updated = await store.resolve_incident(
                    incident.id, user_name, notes=rest or None
                )
                headline = ":white_check_mark: *Incident Resolved*"
                if rest:
                    headline += f"\n\n{rest}"
                return _incident_line(updated or incident, headline, user_id)

            if subcommand == "severity":
                wanted = _SEVERITY_ALIASES.get(rest.strip().lower())
                if not wanted:
                    return _ephemeral(
                        f":grey_question: Unknown severity {rest.strip()!r}. Use "
                        f"one of P1, P2, P3, P4 (or critical/high/medium/low)."
                    )
                updated = await store.update_incident(
                    incident.id, {"severity": wanted}
                )
                await store.add_timeline_note(
                    incident.id,
                    f"Severity set to {wanted} by {user_name}",
                    user=user_name,
                    kind="severity_change",
                )
                return _incident_line(
                    updated or incident,
                    f":chart_with_upwards_trend: *Severity set to {wanted}*",
                    user_id,
                )

            # escalate: one step up the severity ladder, and say so when there
            # is no step left rather than claiming an escalation happened.
            current = incident.severity.value
            index = _SEVERITY_ORDER.index(current) if current in _SEVERITY_ORDER else 1
            if index == 0:
                await store.add_timeline_note(
                    incident.id,
                    f"Escalation requested by {user_name}"
                    + (f": {rest}" if rest else ""),
                    user=user_name,
                    kind="escalation",
                )
                return _incident_line(
                    incident,
                    ":arrow_up: *Escalation noted* — already at the highest "
                    "severity, so the record is unchanged.",
                    user_id,
                )
            raised = _SEVERITY_ORDER[index - 1]
            updated = await store.update_incident(incident.id, {"severity": raised})
            await store.add_timeline_note(
                incident.id,
                f"Escalated to {raised} by {user_name}"
                + (f": {rest}" if rest else ""),
                user=user_name,
                kind="escalation",
            )
            return _incident_line(
                updated or incident,
                f":arrow_up: *Escalated* {current} → {raised}",
                user_id,
            )
        except Exception as e:  # noqa: BLE001 — reported, not swallowed
            logger.exception("/incident %s failed", subcommand)
            return _ephemeral(f":x: `/incident {subcommand}` failed: {e}")

    return {
        "response_type": "ephemeral",
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        "*`/incident` Commands:*\n\n"
                        "* `/incident create <title>` - Declare a new incident\n"
                        "* `/incident update [id] <message>` - Add to the timeline\n"
                        "* `/incident ack [id]` - Acknowledge\n"
                        "* `/incident resolve [id] [notes]` - Resolve\n"
                        "* `/incident escalate [id] [reason]` - Raise severity one level\n"
                        "* `/incident severity [id] <P1|P2|P3|P4>` - Set severity\n\n"
                        "_Without an id, these apply to the most recent "
                        "unresolved incident._"
                    ),
                },
            },
        ],
    }


async def _create_slack_channel(name: str, invite: str = "") -> tuple[str, str]:
    """Create a Slack channel and return `(channel_id, error)`.

    A war room whose channel does not exist is the failure this guards: the old
    handler announced "*War Room Created*  *Channel:* #inc-...-warroom" without
    calling Slack at all, so responders searched for a channel that was never
    there while the incident ran.
    """
    token = SlackConfig.bot_token()
    if not token:
        return "", (
            "SLACK_BOT_TOKEN is not configured, so the bot cannot create a "
            "channel"
        )

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            response = await client.post(
                "https://slack.com/api/conversations.create",
                headers=headers,
                json={"name": name[:80]},
            )
            payload = response.json()
        except Exception as e:  # noqa: BLE001 — surfaced to the channel
            return "", str(e)

        if not payload.get("ok"):
            return "", str(payload.get("error") or "conversations.create failed")

        channel_id = str(payload.get("channel", {}).get("id", ""))
        if channel_id and invite:
            try:
                await client.post(
                    "https://slack.com/api/conversations.invite",
                    headers=headers,
                    json={"channel": channel_id, "users": invite},
                )
            except Exception:  # noqa: BLE001 — the room exists; the invite is a nicety
                logger.warning("Could not invite %s to %s", invite, channel_id)
        return channel_id, ""


@router.post("/commands/warroom")
async def slash_warroom(request: Request, background_tasks: BackgroundTasks):
    """Handle /warroom slash command - create and manage war rooms.

    `create` used to announce a channel it had not created, against an incident
    id it had invented from the clock, with a "Join War Room" button wired to an
    action id nothing handled. `list` then read the war room manager, which of
    course had never heard of it — so the command contradicted itself one line
    later.
    """
    form_data = await request.form()
    text_raw = form_data.get("text", "")
    text = str(text_raw).strip() if text_raw else ""
    user_id = str(form_data.get("user_id", ""))

    parts = text.split(maxsplit=1)
    subcommand = parts[0].lower() if parts else "create"
    args = parts[1] if len(parts) > 1 else ""

    from app.slack_bot.war_room import get_war_room_manager

    manager = get_war_room_manager()

    if subcommand == "create":
        incident, rest = await _target_incident(args)
        if incident is None:
            return _ephemeral(
                ":grey_question: A war room needs an incident. Declare one with "
                "`/incident create <title>` first, or pass an incident id."
            )

        existing = manager.get_by_incident(str(incident.id))
        if existing is not None:
            return _ephemeral(
                f":information_source: <#{existing.channel_id}> is already the "
                f"war room for `{_short(incident.id)}`."
            )

        title = rest or incident.title
        name = manager._generate_channel_name(_short(incident.id), title)
        channel_id, error = await _create_slack_channel(name, invite=user_id)
        if error:
            return _ephemeral(
                f":x: Could not create the war room channel: {error}. No channel "
                f"was created — the incident is still at `{_short(incident.id)}`."
            )

        war_room = await manager.create_war_room(
            incident_id=str(incident.id),
            org_id=str(getattr(incident, "org_id", "")) or "default",
            title=title,
            severity=incident.severity.value,
            description=incident.description or "",
            channel_id=channel_id,
            channel_name=name,
        )

        return {
            "response_type": "in_channel",
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f":mega: *War Room Created*\n\n"
                            f"*Channel:* <#{war_room.channel_id}>\n"
                            f"*Incident:* {incident.title} "
                            f"(`{_short(incident.id)}`)\n"
                            f"*Created by:* <@{user_id}>"
                        ),
                    },
                },
            ],
        }

    if subcommand == "list":
        active_rooms = manager.get_active_war_rooms()

        if not active_rooms:
            return _ephemeral("No active war rooms.")

        rooms_text = "\n".join(
            f"* <#{wr.channel_id}> - {wr.severity} {wr.title} ({wr.status.value})"
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

    return _ephemeral("Usage: `/warroom create [incident-id] [title]` or `/warroom list`")


async def _postmortem_for(incident) -> tuple[dict | None, str]:
    """Generate the postmortem for `incident`, or return why it could not be.

    There is no template fallback on purpose: the generator refuses without an
    LLM rather than emitting a canned document, and a canned document presented
    as analysis is what this command used to post.
    """
    from app.postmortems import (
        PostmortemGenerationFailed,
        PostmortemUnavailable,
        generate_postmortem,
    )

    try:
        return await generate_postmortem(incident), ""
    except PostmortemUnavailable as e:
        return None, str(e)
    except PostmortemGenerationFailed as e:
        return None, f"the model did not return a usable postmortem: {e}"
    except Exception as e:  # noqa: BLE001 — reported into the channel
        logger.exception("postmortem generation failed for %s", incident.id)
        return None, str(e)


async def _run_postmortem(incident, response_url: str, want_actions: bool) -> None:
    """Generate in the background and deliver via `response_url`.

    Slack kills a slash command that has not answered in three seconds, and a
    postmortem is several model calls, so the work cannot happen inline. The old
    handler solved that by saying "You'll be notified when ready" and doing
    nothing at all.
    """
    document, error = await _postmortem_for(incident)
    if error or document is None:
        await _send_response(
            response_url,
            [{
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f":x: Could not generate the postmortem for "
                        f"`{_short(incident.id)}`: {error}"
                    ),
                },
            }],
        )
        return

    if want_actions:
        items = document.get("action_items") or []
        if not items:
            body = (
                f":page_facing_up: The postmortem for *{incident.title}* "
                f"proposes no action items."
            )
        else:
            lines = "\n".join(
                f"{i + 1}. :white_large_square: [{item.get('priority', 'P2')}] "
                f"{item.get('title', '')}"
                + (f" — {item['owner']}" if item.get("owner") else "")
                for i, item in enumerate(items)
            )
            body = (
                f"*Action items from {incident.title} "
                f"(`{_short(incident.id)}`):*\n\n{lines}"
            )
        await _send_response(
            response_url,
            [{"type": "section", "text": {"type": "mrkdwn", "text": body}}],
        )
        return

    sections = document.get("sections") or []
    blocks: list[dict] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"Postmortem: {incident.title}"[:150],
                "emoji": True,
            },
        }
    ]
    # Slack rejects a message with more than 50 blocks and truncates any single
    # text field past 3000 characters, so cap both rather than post nothing.
    for section in sections[:20]:
        content = str(section.get("content", "")).strip()
        if not content:
            continue
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*{section.get('title', '')}*\n{content}"[:2900],
            },
        })
    blocks.append({
        "type": "context",
        "elements": [{
            "type": "mrkdwn",
            "text": f"Draft, generated from incident `{incident.id}`.",
        }],
    })
    await _send_response(response_url, blocks)


@router.post("/commands/postmortem")
async def slash_postmortem(request: Request, background_tasks: BackgroundTasks):
    """Handle /postmortem slash command - generate and manage postmortems.

    `generate` promised "You'll be notified when ready" and started nothing.
    `actions` was the worse one: it listed four invented action items under
    "Action Items from Recent Incidents", one of them naming a specific service
    to put a circuit breaker in front of. Follow-up work assigned off that list
    would have been work on an incident that never happened.
    """
    form_data = await request.form()
    text_raw = form_data.get("text", "")
    text = str(text_raw).strip() if text_raw else ""
    response_url = str(form_data.get("response_url", ""))

    parts = text.split(maxsplit=1)
    subcommand = parts[0].lower() if parts else "generate"
    args = parts[1] if len(parts) > 1 else ""

    if subcommand in {"generate", "actions"}:
        incident, _rest = await _target_incident(args)
        if incident is None:
            from app.incidents.store import get_incident_store

            recent = await get_incident_store().list_incidents(limit=1)
            incident = recent[0] if recent else None
        if incident is None:
            return _ephemeral(
                ":grey_question: There are no incidents on record, so there is "
                "nothing to write a postmortem about."
            )
        if not response_url:
            return _ephemeral(":x: Slack sent no response_url; cannot deliver the result.")

        background_tasks.add_task(
            _run_postmortem, incident, response_url, subcommand == "actions"
        )
        what = "action items" if subcommand == "actions" else "postmortem"
        return _ephemeral(
            f":hourglass: Generating the {what} for *{incident.title}* "
            f"(`{_short(incident.id)}`). This takes a few model calls; the "
            f"result will replace this message."
        )

    if subcommand == "share":
        return _ephemeral(
            "Sharing a postmortem to another channel is not wired up yet. "
            "`/postmortem generate [incident-id]` posts the draft here, and the "
            "full document is at `/api/incidents/<id>/postmortem`."
        )

    return _ephemeral(
        "Usage: `/postmortem generate [incident-id]` | "
        "`/postmortem actions [incident-id]`"
    )


@router.post("/commands/stakeholder")
async def slash_stakeholder(request: Request):
    """Handle /stakeholder slash command - post stakeholder updates.

    Its own usage text said it "Posts an update to stakeholder channels", and it
    posted into whichever channel the command was typed in. The update is real
    either way, so the fix is to name the destination truthfully and to record
    the update on the incident timeline, which is where a stakeholder update
    belongs when the postmortem is written.
    """
    form_data = await request.form()
    text_raw = form_data.get("text", "")
    text = str(text_raw).strip() if text_raw else ""
    user_id = str(form_data.get("user_id", ""))
    user_name = str(form_data.get("user_name", "") or user_id)

    if not text:
        return _ephemeral(
            "Usage: `/stakeholder <update message>` — posts the update in this "
            "channel and records it on the current incident's timeline."
        )

    from datetime import datetime, timezone

    incident, _rest = await _target_incident("")
    if incident is not None:
        from app.incidents.store import get_incident_store

        try:
            await get_incident_store().add_timeline_note(
                incident.id, text, user=user_name, kind="stakeholder_update"
            )
        except Exception:  # noqa: BLE001 — the post still goes out
            logger.exception("Could not record the stakeholder update on %s", incident.id)

    context = f"Posted by <@{user_id}> at {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
    if incident is not None:
        context += f" • recorded on incident `{_short(incident.id)}`"

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
                "text": {"type": "mrkdwn", "text": text},
            },
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": context}],
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
