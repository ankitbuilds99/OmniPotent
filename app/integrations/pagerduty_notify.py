"""PagerDuty notification service for creating incidents and notes."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import get_settings

log = logging.getLogger("devcopilot.integrations.pagerduty_notify")

PAGERDUTY_API_BASE = "https://api.pagerduty.com"


def is_pagerduty_configured() -> bool:
    """Check if PagerDuty integration is configured."""
    s = get_settings()
    return bool(getattr(s, "pagerduty_api_key", None))


async def create_incident(
    title: str,
    service_id: str,
    urgency: str = "high",
    body: str | None = None,
    incident_key: str | None = None,
) -> dict[str, Any]:
    """Create a new PagerDuty incident.

    Args:
        title: Incident title
        service_id: PagerDuty service ID to create incident on
        urgency: "high" or "low"
        body: Optional incident body/details
        incident_key: Optional dedup key to prevent duplicate incidents

    Returns:
        Result dict with success status and incident data
    """
    s = get_settings()
    api_key = getattr(s, "pagerduty_api_key", None)

    if not api_key:
        return {"success": False, "message": "PagerDuty not configured"}

    try:
        payload: dict[str, Any] = {
            "incident": {
                "type": "incident",
                "title": title,
                "service": {
                    "id": service_id,
                    "type": "service_reference",
                },
                "urgency": urgency,
            }
        }

        if body:
            payload["incident"]["body"] = {
                "type": "incident_body",
                "details": body,
            }

        if incident_key:
            payload["incident"]["incident_key"] = incident_key

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{PAGERDUTY_API_BASE}/incidents",
                headers={
                    "Authorization": f"Token token={api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )

            if resp.status_code == 201:
                data = resp.json()
                return {
                    "success": True,
                    "incident_id": data["incident"]["id"],
                    "incident_url": data["incident"]["html_url"],
                }
            else:
                log.warning("PagerDuty API error: %s %s", resp.status_code, resp.text)
                return {"success": False, "message": f"API error: {resp.status_code}"}

    except httpx.HTTPError as e:
        log.exception("Failed to create PagerDuty incident")
        return {"success": False, "message": str(e)}


async def add_incident_note(
    incident_id: str,
    content: str,
    user_email: str,
) -> dict[str, Any]:
    """Add a note to an existing PagerDuty incident.

    Args:
        incident_id: PagerDuty incident ID
        content: Note content
        user_email: Email of user adding the note

    Returns:
        Result dict with success status
    """
    s = get_settings()
    api_key = getattr(s, "pagerduty_api_key", None)

    if not api_key:
        return {"success": False, "message": "PagerDuty not configured"}

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{PAGERDUTY_API_BASE}/incidents/{incident_id}/notes",
                headers={
                    "Authorization": f"Token token={api_key}",
                    "Content-Type": "application/json",
                    "From": user_email,
                },
                json={
                    "note": {
                        "content": content,
                    }
                },
            )

            if resp.status_code == 201:
                return {"success": True, "message": "Note added"}
            else:
                return {"success": False, "message": f"API error: {resp.status_code}"}

    except httpx.HTTPError as e:
        log.exception("Failed to add PagerDuty note")
        return {"success": False, "message": str(e)}


async def resolve_incident(
    incident_id: str,
    user_email: str,
    resolution: str | None = None,
) -> dict[str, Any]:
    """Resolve a PagerDuty incident.

    Args:
        incident_id: PagerDuty incident ID
        user_email: Email of user resolving
        resolution: Optional resolution note

    Returns:
        Result dict with success status
    """
    s = get_settings()
    api_key = getattr(s, "pagerduty_api_key", None)

    if not api_key:
        return {"success": False, "message": "PagerDuty not configured"}

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.put(
                f"{PAGERDUTY_API_BASE}/incidents/{incident_id}",
                headers={
                    "Authorization": f"Token token={api_key}",
                    "Content-Type": "application/json",
                    "From": user_email,
                },
                json={
                    "incident": {
                        "type": "incident",
                        "status": "resolved",
                    }
                },
            )

            if resp.status_code == 200:
                # Add resolution note if provided
                if resolution:
                    await add_incident_note(incident_id, f"Resolution: {resolution}", user_email)
                return {"success": True, "message": "Incident resolved"}
            else:
                return {"success": False, "message": f"API error: {resp.status_code}"}

    except httpx.HTTPError as e:
        log.exception("Failed to resolve PagerDuty incident")
        return {"success": False, "message": str(e)}


async def list_services() -> dict[str, Any]:
    """List available PagerDuty services."""
    s = get_settings()
    api_key = getattr(s, "pagerduty_api_key", None)

    if not api_key:
        return {"success": False, "message": "PagerDuty not configured", "services": []}

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{PAGERDUTY_API_BASE}/services",
                headers={
                    "Authorization": f"Token token={api_key}",
                    "Content-Type": "application/json",
                },
                params={"limit": 100},
            )

            if resp.status_code == 200:
                data = resp.json()
                services = [
                    {"id": s["id"], "name": s["name"], "status": s["status"]}
                    for s in data.get("services", [])
                ]
                return {"success": True, "services": services}
            else:
                return {"success": False, "message": f"API error: {resp.status_code}", "services": []}

    except httpx.HTTPError as e:
        log.exception("Failed to list PagerDuty services")
        return {"success": False, "message": str(e), "services": []}
