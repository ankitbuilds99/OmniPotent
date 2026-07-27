"""Slack MCP server — ChatOps integration for incident management.

Provides Slack integration for posting updates, creating channels,
and enabling ChatOps workflows during incident response.

Tools:
  - send_message:           Send message to a channel
  - post_incident_update:   Post formatted incident update
  - create_channel:         Create a new channel
  - list_channels:          List available channels
  - get_channel_history:    Get recent messages from a channel
  - add_reaction:           Add emoji reaction to a message
  - invite_to_channel:      Invite users to a channel
  - set_channel_topic:      Set channel topic/description

Run standalone:
    SLACK_BOT_TOKEN=xoxb-... python -m app.mcp.servers.slack.server
"""

from __future__ import annotations

import json
import os
from datetime import datetime

from mcp.server.fastmcp import FastMCP

SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "").strip()
SLACK_DEFAULT_CHANNEL = os.environ.get("SLACK_DEFAULT_CHANNEL", "#incidents").strip()
OFFLINE = not SLACK_BOT_TOKEN

mcp = FastMCP("slack")

# Demo fixtures for offline mode
DEMO_CHANNELS: list[dict[str, object]] = [
    {
        "id": "C01INCIDENT",
        "name": "incidents",
        "is_private": False,
        "topic": "Active incident coordination",
        "purpose": "Post all incident updates here",
        "num_members": 45,
        "created": "2025-01-15T10:00:00Z",
    },
    {
        "id": "C02ONCALL",
        "name": "oncall",
        "is_private": False,
        "topic": "On-call coordination",
        "purpose": "On-call handoffs and escalations",
        "num_members": 12,
        "created": "2025-01-15T10:00:00Z",
    },
    {
        "id": "C03ALERTS",
        "name": "alerts",
        "is_private": False,
        "topic": "Automated alerts",
        "purpose": "PagerDuty, Datadog, and other alert notifications",
        "num_members": 78,
        "created": "2025-01-15T10:00:00Z",
    },
    {
        "id": "C04DEPLOYS",
        "name": "deploys",
        "is_private": False,
        "topic": "Deployment notifications",
        "purpose": "Track all production deployments",
        "num_members": 34,
        "created": "2025-02-01T09:00:00Z",
    },
    {
        "id": "C05SRE",
        "name": "sre-team",
        "is_private": True,
        "topic": "SRE team discussions",
        "purpose": "Internal SRE coordination",
        "num_members": 8,
        "created": "2025-01-20T14:00:00Z",
    },
]

DEMO_MESSAGES: list[dict[str, object]] = [
    {
        "ts": "1719500400.000100",
        "user": "U01ALICE",
        "username": "alice",
        "text": "🚨 INCIDENT: High error rate on checkout-svc",
        "channel": "C01INCIDENT",
        "timestamp": "2026-06-27T14:00:00Z",
    },
    {
        "ts": "1719500460.000200",
        "user": "U02BOB",
        "username": "bob",
        "text": "Looking into it now. Seeing 500s in the logs.",
        "channel": "C01INCIDENT",
        "timestamp": "2026-06-27T14:01:00Z",
    },
    {
        "ts": "1719500520.000300",
        "user": "U01ALICE",
        "username": "alice",
        "text": "Found it - bad config in the latest deploy. Rolling back.",
        "channel": "C01INCIDENT",
        "timestamp": "2026-06-27T14:02:00Z",
    },
    {
        "ts": "1719500700.000400",
        "user": "U01ALICE",
        "username": "alice",
        "text": "✅ Rollback complete. Error rate back to normal.",
        "channel": "C01INCIDENT",
        "timestamp": "2026-06-27T14:05:00Z",
    },
]

DEMO_USERS: dict[str, dict[str, str]] = {
    "U01ALICE": {"name": "alice", "real_name": "Alice Smith", "email": "alice@acme.com"},
    "U02BOB": {"name": "bob", "real_name": "Bob Jones", "email": "bob@acme.com"},
    "U03CAROL": {"name": "carol", "real_name": "Carol Williams", "email": "carol@acme.com"},
}


def _find_channel(channel: str) -> dict[str, object] | None:
    """Find channel by name or ID."""
    channel_clean = channel.lstrip("#")
    for ch in DEMO_CHANNELS:
        if ch["id"] == channel or ch["name"] == channel_clean:
            return ch
    return None


def _slack_api(method: str, **kwargs: object) -> dict:
    """Make Slack API call."""
    import httpx

    resp = httpx.post(
        f"https://slack.com/api/{method}",
        headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
        json=kwargs,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


@mcp.tool()
def send_message(
    channel: str,
    text: str,
    thread_ts: str = "",
    blocks: str = "",
) -> dict:
    """Send a message to a Slack channel.

    Args:
        channel: Channel name (e.g., #incidents) or ID
        text: Message text (supports Slack markdown)
        thread_ts: Optional thread timestamp to reply in thread
        blocks: Optional JSON string of Slack Block Kit blocks

    Returns:
        Message details including timestamp
    """
    if OFFLINE:
        ch = _find_channel(channel)
        if not ch:
            return {"error": f"Channel '{channel}' not found", "available": [c["name"] for c in DEMO_CHANNELS]}

        ts = f"{datetime.now().timestamp():.6f}"
        return {
            "ok": True,
            "channel": ch["id"],
            "ts": ts,
            "message": {
                "text": text,
                "ts": ts,
                "user": "UBOT",
                "thread_ts": thread_ts or None,
            },
            "mode": "offline_demo",
            "note": "Message simulated - no actual Slack message sent",
        }

    kwargs: dict[str, object] = {
        "channel": channel,
        "text": text,
    }
    if thread_ts:
        kwargs["thread_ts"] = thread_ts
    if blocks:
        try:
            kwargs["blocks"] = json.loads(blocks)
        except json.JSONDecodeError:
            pass

    return _slack_api("chat.postMessage", **kwargs)


@mcp.tool()
def post_incident_update(
    channel: str,
    incident_id: str,
    status: str,
    summary: str,
    severity: str = "high",
    service: str = "",
    next_steps: str = "",
) -> dict:
    """Post a formatted incident update to Slack.

    Args:
        channel: Channel to post to
        incident_id: Incident identifier (e.g., INC-123)
        status: Current status (investigating, identified, monitoring, resolved)
        summary: Brief summary of the incident
        severity: Severity level (critical, high, medium, low)
        service: Affected service name
        next_steps: Planned next actions

    Returns:
        Posted message details
    """
    status_emoji = {
        "investigating": "🔍",
        "identified": "🎯",
        "monitoring": "👀",
        "resolved": "✅",
    }.get(status.lower(), "📋")

    severity_emoji = {
        "critical": "🔴",
        "high": "🟠",
        "medium": "🟡",
        "low": "🟢",
    }.get(severity.lower(), "⚪")

    blocks_data = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"{status_emoji} Incident Update: {incident_id}",
            },
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Status:*\n{status.title()}"},
                {"type": "mrkdwn", "text": f"*Severity:*\n{severity_emoji} {severity.title()}"},
            ],
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Summary:*\n{summary}"},
        },
    ]

    if service:
        blocks_data.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Affected Service:*\n`{service}`"},
        })

    if next_steps:
        blocks_data.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Next Steps:*\n{next_steps}"},
        })

    blocks_data.append({
        "type": "context",
        "elements": [
            {"type": "mrkdwn", "text": f"Posted by DevOps Copilot • {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}"},
        ],
    })

    text = f"{status_emoji} {incident_id}: {status.title()} - {summary}"

    return send_message(
        channel=channel,
        text=text,
        blocks=json.dumps(blocks_data),
    )


@mcp.tool()
def create_channel(
    name: str,
    is_private: bool = False,
    description: str = "",
) -> dict:
    """Create a new Slack channel.

    Args:
        name: Channel name (will be lowercased, spaces replaced with hyphens)
        is_private: Whether the channel should be private
        description: Channel description/purpose

    Returns:
        Created channel details
    """
    name_clean = name.lower().replace(" ", "-").replace("_", "-")

    if OFFLINE:
        if _find_channel(name_clean):
            return {"error": f"Channel '{name_clean}' already exists"}

        new_channel = {
            "id": f"C{len(DEMO_CHANNELS) + 1:02d}NEW",
            "name": name_clean,
            "is_private": is_private,
            "topic": "",
            "purpose": description,
            "num_members": 1,
            "created": datetime.now().isoformat() + "Z",
        }

        return {
            "ok": True,
            "channel": new_channel,
            "mode": "offline_demo",
            "note": "Channel simulated - no actual Slack channel created",
        }

    result = _slack_api(
        "conversations.create",
        name=name_clean,
        is_private=is_private,
    )

    if result.get("ok") and description:
        channel_id = result.get("channel", {}).get("id")
        if channel_id:
            _slack_api("conversations.setPurpose", channel=channel_id, purpose=description)

    return result


@mcp.tool()
def list_channels(
    include_private: bool = False,
    limit: int = 50,
) -> dict:
    """List available Slack channels.

    Args:
        include_private: Include private channels (requires membership)
        limit: Maximum number of channels to return

    Returns:
        List of channels with details
    """
    if OFFLINE:
        channels = DEMO_CHANNELS
        if not include_private:
            channels = [c for c in channels if not c.get("is_private")]
        return {
            "ok": True,
            "channels": channels[:limit],
            "total": len(channels),
            "mode": "offline_demo",
        }

    types = "public_channel,private_channel" if include_private else "public_channel"
    return _slack_api("conversations.list", types=types, limit=limit)


@mcp.tool()
def get_channel_history(
    channel: str,
    limit: int = 20,
) -> dict:
    """Get recent messages from a Slack channel.

    Args:
        channel: Channel name or ID
        limit: Maximum number of messages to return

    Returns:
        Recent messages with user info
    """
    if OFFLINE:
        ch = _find_channel(channel)
        if not ch:
            return {"error": f"Channel '{channel}' not found", "available": [c["name"] for c in DEMO_CHANNELS]}

        messages = [m for m in DEMO_MESSAGES if m["channel"] == ch["id"]]
        messages = sorted(messages, key=lambda x: str(x["ts"]), reverse=True)[:limit]

        return {
            "ok": True,
            "channel": ch,
            "messages": messages,
            "has_more": False,
            "mode": "offline_demo",
        }

    ch = _find_channel(channel) if not channel.startswith("C") else {"id": channel}
    if not ch:
        return {"error": f"Channel '{channel}' not found"}

    return _slack_api("conversations.history", channel=ch["id"], limit=limit)


@mcp.tool()
def add_reaction(
    channel: str,
    timestamp: str,
    emoji: str,
) -> dict:
    """Add an emoji reaction to a message.

    Args:
        channel: Channel containing the message
        timestamp: Message timestamp (ts)
        emoji: Emoji name without colons (e.g., "thumbsup")

    Returns:
        Reaction result
    """
    emoji_clean = emoji.strip(":")

    if OFFLINE:
        ch = _find_channel(channel)
        if not ch:
            return {"error": f"Channel '{channel}' not found"}

        return {
            "ok": True,
            "channel": ch["id"],
            "timestamp": timestamp,
            "emoji": emoji_clean,
            "mode": "offline_demo",
            "note": "Reaction simulated - no actual reaction added",
        }

    ch = _find_channel(channel) if not channel.startswith("C") else {"id": channel}
    return _slack_api("reactions.add", channel=ch["id"] if ch else channel, timestamp=timestamp, name=emoji_clean)


@mcp.tool()
def invite_to_channel(
    channel: str,
    users: list[str],
) -> dict:
    """Invite users to a Slack channel.

    Args:
        channel: Channel name or ID
        users: List of user IDs to invite

    Returns:
        Invitation result
    """
    if OFFLINE:
        ch = _find_channel(channel)
        if not ch:
            return {"error": f"Channel '{channel}' not found"}

        return {
            "ok": True,
            "channel": ch["id"],
            "invited_users": users,
            "mode": "offline_demo",
            "note": "Invitation simulated - no actual invitations sent",
        }

    ch = _find_channel(channel) if not channel.startswith("C") else {"id": channel}
    return _slack_api("conversations.invite", channel=ch["id"] if ch else channel, users=",".join(users))


@mcp.tool()
def set_channel_topic(
    channel: str,
    topic: str,
) -> dict:
    """Set the topic for a Slack channel.

    Args:
        channel: Channel name or ID
        topic: New topic text

    Returns:
        Updated channel details
    """
    if OFFLINE:
        ch = _find_channel(channel)
        if not ch:
            return {"error": f"Channel '{channel}' not found"}

        return {
            "ok": True,
            "channel": ch["id"],
            "topic": topic,
            "mode": "offline_demo",
            "note": "Topic simulated - no actual topic change",
        }

    ch = _find_channel(channel) if not channel.startswith("C") else {"id": channel}
    return _slack_api("conversations.setTopic", channel=ch["id"] if ch else channel, topic=topic)


@mcp.tool()
def create_incident_channel(
    incident_id: str,
    title: str,
    severity: str = "high",
    invite_users: list[str] | None = None,
) -> dict:
    """Create a dedicated incident channel with standard setup.

    Creates a channel named inc-{incident_id}, sets topic, posts
    initial message, and invites specified users.

    Args:
        incident_id: Incident identifier (e.g., INC-123)
        title: Brief incident title
        severity: Severity level (critical, high, medium, low)
        invite_users: List of user IDs to invite

    Returns:
        Created channel details with initial setup status
    """
    channel_name = f"inc-{incident_id.lower().replace(' ', '-')}"

    create_result = create_channel(
        name=channel_name,
        is_private=False,
        description=f"Incident coordination for {incident_id}: {title}",
    )

    if not create_result.get("ok"):
        return create_result

    channel_id = create_result.get("channel", {}).get("id", channel_name)

    set_channel_topic(
        channel=channel_id,
        topic=f"🚨 {severity.upper()} | {title} | Status: Investigating",
    )

    post_incident_update(
        channel=channel_id,
        incident_id=incident_id,
        status="investigating",
        summary=title,
        severity=severity,
    )

    if invite_users:
        invite_to_channel(channel=channel_id, users=invite_users)

    return {
        "ok": True,
        "channel": create_result.get("channel"),
        "incident_id": incident_id,
        "setup_complete": True,
        "mode": "offline_demo" if OFFLINE else "live",
    }


if __name__ == "__main__":
    mcp.run(transport="stdio")
