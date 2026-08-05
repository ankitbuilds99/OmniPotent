"""Opsgenie alert management MCP server.

Gives the agent Opsgenie alerting and on-call capabilities:
- Alert creation/management
- Acknowledge/close/escalate alerts
- On-call schedule sync
- Team management

When `OPSGENIE_API_KEY` is set it calls the real Opsgenie API; otherwise it
runs in OFFLINE DEMO mode with fixtures.

Live API (per https://docs.opsgenie.com/docs/api-overview):
  - POST https://api.opsgenie.com/v2/alerts
  - GET  https://api.opsgenie.com/v2/alerts/{id}
  - auth: GenieKey {api_key}

Run standalone for debugging:
    OPSGENIE_API_KEY=... python -m app.mcp.servers.opsgenie.server
"""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

OPSGENIE_API_KEY = os.environ.get("OPSGENIE_API_KEY", "").strip()
OPSGENIE_BASE_URL = os.environ.get("OPSGENIE_BASE_URL", "https://api.opsgenie.com").strip()
OPSGENIE_TEAM_ID = os.environ.get("OPSGENIE_TEAM_ID", "").strip()
OFFLINE = not OPSGENIE_API_KEY

mcp = FastMCP("opsgenie")


def _as_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _headers() -> dict:
    return {
        "Authorization": f"GenieKey {OPSGENIE_API_KEY}",
        "Content-Type": "application/json",
    }


# --- Offline fixtures ---
def _get_fixture_alerts() -> list[dict]:
    return [
        {
            "id": "alert-uuid-1",
            "tiny_id": "101",
            "message": "[P1] checkout-svc 5xx spike",
            "description": "Error rate exceeded 5% threshold",
            "status": "open",
            "acknowledged": False,
            "priority": "P1",
            "source": "Datadog",
            "tags": ["incident", "checkout-svc", "production"],
            "created_at": "2024-01-15T10:30:00Z",
        },
        {
            "id": "alert-uuid-2",
            "tiny_id": "100",
            "message": "[P3] payment-svc latency warning",
            "description": "P95 latency exceeded 500ms",
            "status": "acknowledged",
            "acknowledged": True,
            "priority": "P3",
            "source": "Prometheus",
            "tags": ["warning", "payment-svc", "production"],
            "created_at": "2024-01-15T09:15:00Z",
        },
    ]


def _get_fixture_oncall() -> list[dict]:
    return [
        {
            "id": "user-1",
            "name": "Alice Engineer",
            "type": "user",
            "schedule": "Primary On-Call",
        },
        {
            "id": "user-2",
            "name": "Bob Developer",
            "type": "user",
            "schedule": "Secondary On-Call",
        },
    ]


def _get_fixture_schedules() -> list[dict]:
    return [
        {
            "id": "schedule-1",
            "name": "Primary On-Call",
            "timezone": "America/Los_Angeles",
            "enabled": True,
        },
        {
            "id": "schedule-2",
            "name": "Secondary On-Call",
            "timezone": "America/Los_Angeles",
            "enabled": True,
        },
    ]


@mcp.tool()
def create_alert(
    message: str,
    description: str = "",
    priority: str = "P3",
    alias: str = "",
    source: str = "DevOps Copilot",
    tags: str = "",
    entity: str = "",
    responders: str = "",
) -> dict:
    """Create a new Opsgenie alert.

    Args:
        message: Alert message/title.
        description: Detailed description.
        priority: Priority (P1=Critical, P2=High, P3=Moderate, P4=Low, P5=Info).
        alias: Unique alias for deduplication.
        source: Source of the alert.
        tags: Comma-separated tags.
        entity: Related entity (e.g., service name).
        responders: Comma-separated team IDs to notify.
    """
    if OFFLINE:
        return {
            "status": "created (simulated - offline demo)",
            "id": "alert-uuid-demo",
            "message": message,
            "priority": priority,
        }

    import httpx

    payload: dict = {
        "message": message,
        "priority": priority,
        "source": source,
    }

    if description:
        payload["description"] = description
    if alias:
        payload["alias"] = alias
    if tags:
        payload["tags"] = [t.strip() for t in tags.split(",") if t.strip()]
    if entity:
        payload["entity"] = entity
    if responders:
        payload["responders"] = [
            {"id": r.strip(), "type": "team"}
            for r in responders.split(",")
            if r.strip()
        ]

    resp = httpx.post(
        f"{OPSGENIE_BASE_URL}/v2/alerts",
        headers=_headers(),
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    return {
        "status": "created",
        "request_id": data.get("requestId", ""),
        "message": message,
        "priority": priority,
    }


@mcp.tool()
def get_alert(alert_id: str) -> dict:
    """Get an alert by ID or alias.

    Args:
        alert_id: Alert ID or alias.
    """
    if OFFLINE:
        for alert in _get_fixture_alerts():
            if alert["id"] == alert_id or alert["tiny_id"] == alert_id:
                return alert
        return {"error": f"Alert {alert_id} not found (offline demo)"}

    import httpx

    resp = httpx.get(
        f"{OPSGENIE_BASE_URL}/v2/alerts/{alert_id}",
        headers=_headers(),
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json().get("data", {})

    return {
        "id": data.get("id", ""),
        "tiny_id": data.get("tinyId", ""),
        "message": data.get("message", ""),
        "description": data.get("description", ""),
        "status": data.get("status", ""),
        "acknowledged": data.get("acknowledged", False),
        "priority": data.get("priority", ""),
        "source": data.get("source", ""),
        "tags": data.get("tags", []),
        "created_at": data.get("createdAt", ""),
    }


@mcp.tool()
def list_alerts(
    query: str = "",
    status: str = "",
    priority: str = "",
    limit: int | str = 50,
) -> list[dict]:
    """List Opsgenie alerts.

    Args:
        query: Search query string.
        status: Filter by status (open, acknowledged, closed).
        priority: Filter by priority (P1-P5).
        limit: Max results.
    """
    limit = _as_int(limit, 50)

    if OFFLINE:
        results = _get_fixture_alerts()
        if status:
            results = [a for a in results if a.get("status") == status]
        if priority:
            results = [a for a in results if a.get("priority") == priority]
        return results[:limit]

    import httpx

    params: dict = {"limit": min(limit, 100)}
    if query:
        params["query"] = query
    if status:
        params["query"] = params.get("query", "") + f" status:{status}"
    if priority:
        params["query"] = params.get("query", "") + f" priority:{priority}"

    resp = httpx.get(
        f"{OPSGENIE_BASE_URL}/v2/alerts",
        headers=_headers(),
        params=params,
        timeout=30,
    )
    resp.raise_for_status()

    return [
        {
            "id": alert.get("id", ""),
            "tiny_id": alert.get("tinyId", ""),
            "message": alert.get("message", ""),
            "status": alert.get("status", ""),
            "acknowledged": alert.get("acknowledged", False),
            "priority": alert.get("priority", ""),
            "source": alert.get("source", ""),
            "tags": alert.get("tags", []),
            "created_at": alert.get("createdAt", ""),
        }
        for alert in resp.json().get("data", [])
    ]


@mcp.tool()
def acknowledge_alert(
    alert_id: str,
    note: str = "",
    user: str = "DevOps Copilot",
) -> dict:
    """Acknowledge an alert.

    Args:
        alert_id: Alert ID or alias.
        note: Optional note to add.
        user: User performing the action.
    """
    if OFFLINE:
        return {
            "status": "acknowledged (simulated - offline demo)",
            "id": alert_id,
        }

    import httpx

    payload = {"user": user, "source": "DevOps Copilot"}
    if note:
        payload["note"] = note

    resp = httpx.post(
        f"{OPSGENIE_BASE_URL}/v2/alerts/{alert_id}/acknowledge",
        headers=_headers(),
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()

    return {"status": "acknowledged", "id": alert_id}


@mcp.tool()
def close_alert(
    alert_id: str,
    note: str = "",
    user: str = "DevOps Copilot",
) -> dict:
    """Close an alert.

    Args:
        alert_id: Alert ID or alias.
        note: Optional note to add.
        user: User performing the action.
    """
    if OFFLINE:
        return {
            "status": "closed (simulated - offline demo)",
            "id": alert_id,
        }

    import httpx

    payload = {"user": user, "source": "DevOps Copilot"}
    if note:
        payload["note"] = note

    resp = httpx.post(
        f"{OPSGENIE_BASE_URL}/v2/alerts/{alert_id}/close",
        headers=_headers(),
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()

    return {"status": "closed", "id": alert_id}


@mcp.tool()
def add_note(
    alert_id: str,
    note: str,
    user: str = "DevOps Copilot",
) -> dict:
    """Add a note to an alert.

    Args:
        alert_id: Alert ID or alias.
        note: Note text to add.
        user: User adding the note.
    """
    if OFFLINE:
        return {
            "status": "note added (simulated - offline demo)",
            "id": alert_id,
        }

    import httpx

    resp = httpx.post(
        f"{OPSGENIE_BASE_URL}/v2/alerts/{alert_id}/notes",
        headers=_headers(),
        json={"note": note, "user": user, "source": "DevOps Copilot"},
        timeout=30,
    )
    resp.raise_for_status()

    return {"status": "note added", "id": alert_id}


@mcp.tool()
def escalate_alert(
    alert_id: str,
    escalation_id: str,
    note: str = "",
    user: str = "DevOps Copilot",
) -> dict:
    """Escalate an alert to an escalation policy.

    Args:
        alert_id: Alert ID or alias.
        escalation_id: Escalation policy ID.
        note: Optional note.
        user: User performing the action.
    """
    if OFFLINE:
        return {
            "status": "escalated (simulated - offline demo)",
            "id": alert_id,
            "escalation_id": escalation_id,
        }

    import httpx

    payload = {
        "escalation": {"id": escalation_id},
        "user": user,
        "source": "DevOps Copilot",
    }
    if note:
        payload["note"] = note

    resp = httpx.post(
        f"{OPSGENIE_BASE_URL}/v2/alerts/{alert_id}/escalate",
        headers=_headers(),
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()

    return {"status": "escalated", "id": alert_id, "escalation_id": escalation_id}


@mcp.tool()
def assign_alert(
    alert_id: str,
    owner_id: str,
    owner_type: str = "user",
    note: str = "",
) -> dict:
    """Assign an alert to a user or team.

    Args:
        alert_id: Alert ID or alias.
        owner_id: User or team ID.
        owner_type: Type of owner (user or team).
        note: Optional note.
    """
    if OFFLINE:
        return {
            "status": "assigned (simulated - offline demo)",
            "id": alert_id,
            "owner_id": owner_id,
        }

    import httpx

    payload = {
        "owner": {"id": owner_id, "type": owner_type},
        "user": "DevOps Copilot",
        "source": "DevOps Copilot",
    }
    if note:
        payload["note"] = note

    resp = httpx.post(
        f"{OPSGENIE_BASE_URL}/v2/alerts/{alert_id}/assign",
        headers=_headers(),
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()

    return {"status": "assigned", "id": alert_id, "owner_id": owner_id}


@mcp.tool()
def get_on_call(schedule_id: str = "") -> list[dict]:
    """Get current on-call participants.

    Args:
        schedule_id: Optional schedule ID to filter by.
    """
    if OFFLINE:
        return _get_fixture_oncall()

    import httpx

    if schedule_id:
        endpoint = f"/v2/schedules/{schedule_id}/on-calls"
    else:
        endpoint = "/v2/schedules/on-calls"

    resp = httpx.get(
        f"{OPSGENIE_BASE_URL}{endpoint}",
        headers=_headers(),
        params={"flat": "true"},
        timeout=30,
    )
    resp.raise_for_status()

    return [
        {
            "id": p.get("id", ""),
            "name": p.get("name", ""),
            "type": p.get("type", "user"),
        }
        for p in resp.json().get("data", {}).get("onCallParticipants", [])
    ]


@mcp.tool()
def list_schedules() -> list[dict]:
    """List all on-call schedules."""
    if OFFLINE:
        return _get_fixture_schedules()

    import httpx

    resp = httpx.get(
        f"{OPSGENIE_BASE_URL}/v2/schedules",
        headers=_headers(),
        timeout=30,
    )
    resp.raise_for_status()

    return [
        {
            "id": s.get("id", ""),
            "name": s.get("name", ""),
            "timezone": s.get("timezone", ""),
            "enabled": s.get("enabled", True),
        }
        for s in resp.json().get("data", [])
    ]


@mcp.tool()
def list_teams() -> list[dict]:
    """List all Opsgenie teams."""
    if OFFLINE:
        return [
            {"id": "team-1", "name": "Engineering", "description": "Engineering team"},
            {"id": "team-2", "name": "Platform", "description": "Platform team"},
        ]

    import httpx

    resp = httpx.get(
        f"{OPSGENIE_BASE_URL}/v2/teams",
        headers=_headers(),
        timeout=30,
    )
    resp.raise_for_status()

    return [
        {
            "id": t.get("id", ""),
            "name": t.get("name", ""),
            "description": t.get("description", ""),
        }
        for t in resp.json().get("data", [])
    ]


@mcp.tool()
def get_team_on_call(team_id: str) -> list[dict]:
    """Get on-call participants for a specific team.

    Args:
        team_id: Team ID.
    """
    if OFFLINE:
        return _get_fixture_oncall()

    import httpx

    resp = httpx.get(
        f"{OPSGENIE_BASE_URL}/v2/teams/{team_id}/on-calls",
        headers=_headers(),
        timeout=30,
    )
    resp.raise_for_status()

    return [
        {
            "id": p.get("id", ""),
            "name": p.get("name", ""),
            "type": p.get("type", "user"),
        }
        for p in resp.json().get("data", {}).get("onCallParticipants", [])
    ]


if __name__ == "__main__":
    mcp.run(transport="stdio")
