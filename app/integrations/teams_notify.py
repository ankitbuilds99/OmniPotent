"""Microsoft Teams notification service via webhooks."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import get_settings

log = logging.getLogger("omnipotent.integrations.teams")


def is_teams_configured() -> bool:
    """Check if Microsoft Teams integration is configured."""
    s = get_settings()
    return bool(getattr(s, "teams_webhook_url", None))


def _format_adaptive_card(report: dict[str, Any], query: str) -> dict[str, Any]:
    """Format an investigation report as a Teams Adaptive Card."""
    confidence = report.get("confidence", "unknown")
    confidence_color = {
        "high": "good",
        "medium": "warning",
        "low": "attention",
    }.get(confidence, "default")

    facts = []
    if report.get("root_cause"):
        facts.append({"title": "Root Cause", "value": report["root_cause"]})

    if report.get("confidence"):
        facts.append({"title": "Confidence", "value": confidence.title()})

    if report.get("affected_services"):
        services = report["affected_services"]
        if isinstance(services, list):
            facts.append({"title": "Affected Services", "value": ", ".join(services)})

    body: list[dict[str, Any]] = [
        {
            "type": "TextBlock",
            "size": "Large",
            "weight": "Bolder",
            "text": "Investigation Report",
            "color": confidence_color,
        },
        {
            "type": "TextBlock",
            "text": f"**Query:** {query}",
            "wrap": True,
        },
    ]

    if facts:
        body.append({
            "type": "FactSet",
            "facts": facts,
        })

    # Evidence section
    if report.get("evidence") and isinstance(report["evidence"], list):
        body.append({
            "type": "TextBlock",
            "text": "**Evidence:**",
            "weight": "Bolder",
        })
        for evidence in report["evidence"][:5]:
            body.append({
                "type": "TextBlock",
                "text": f"• {evidence}",
                "wrap": True,
                "spacing": "Small",
            })

    # Resolution/Recommendation
    resolution = report.get("resolution") or report.get("recommendation")
    if resolution:
        body.append({
            "type": "TextBlock",
            "text": "**Resolution:**",
            "weight": "Bolder",
            "spacing": "Medium",
        })
        body.append({
            "type": "TextBlock",
            "text": resolution,
            "wrap": True,
        })

    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "contentUrl": None,
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": body,
                    "msteams": {
                        "width": "Full",
                    },
                },
            }
        ],
    }


async def post_to_teams(
    report: dict[str, Any],
    query: str,
    webhook_url: str | None = None,
) -> dict[str, Any]:
    """Post an investigation report to Microsoft Teams.

    Args:
        report: The investigation report dict
        query: The original investigation query
        webhook_url: Teams webhook URL (optional, uses config if not provided)

    Returns:
        Result dict with success status and message
    """
    s = get_settings()
    url = webhook_url or getattr(s, "teams_webhook_url", None)

    if not url:
        return {"success": False, "message": "Teams webhook not configured"}

    try:
        payload = _format_adaptive_card(report, query)

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, json=payload)

            # Teams webhook returns 200 on success with "1" in body
            if resp.status_code == 200:
                return {"success": True, "message": "Report posted to Teams"}
            else:
                log.warning("Teams webhook failed: %s %s", resp.status_code, resp.text)
                return {"success": False, "message": f"Teams error: {resp.status_code}"}

    except httpx.HTTPError as e:
        log.exception("Failed to post to Teams")
        return {"success": False, "message": f"HTTP error: {str(e)}"}
    except Exception as e:
        log.exception("Unexpected error posting to Teams")
        return {"success": False, "message": str(e)}


async def send_simple_message(
    text: str,
    webhook_url: str | None = None,
    title: str | None = None,
) -> dict[str, Any]:
    """Send a simple text message to Teams.

    Args:
        text: Message text
        webhook_url: Teams webhook URL
        title: Optional title for the message

    Returns:
        Result dict with success status
    """
    s = get_settings()
    url = webhook_url or getattr(s, "teams_webhook_url", None)

    if not url:
        return {"success": False, "message": "Teams webhook not configured"}

    try:
        body: list[dict[str, Any]] = []

        if title:
            body.append({
                "type": "TextBlock",
                "size": "Large",
                "weight": "Bolder",
                "text": title,
            })

        body.append({
            "type": "TextBlock",
            "text": text,
            "wrap": True,
        })

        payload = {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "contentUrl": None,
                    "content": {
                        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                        "type": "AdaptiveCard",
                        "version": "1.4",
                        "body": body,
                    },
                }
            ],
        }

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, json=payload)

            if resp.status_code == 200:
                return {"success": True, "message": "Message sent to Teams"}
            else:
                return {"success": False, "message": f"Teams error: {resp.status_code}"}

    except httpx.HTTPError as e:
        log.exception("Failed to send Teams message")
        return {"success": False, "message": str(e)}


async def send_alert(
    title: str,
    severity: str,
    description: str,
    source: str | None = None,
    webhook_url: str | None = None,
) -> dict[str, Any]:
    """Send an alert notification to Teams.

    Args:
        title: Alert title
        severity: Alert severity (critical, high, medium, low)
        description: Alert description
        source: Source of the alert
        webhook_url: Teams webhook URL

    Returns:
        Result dict with success status
    """
    s = get_settings()
    url = webhook_url or getattr(s, "teams_webhook_url", None)

    if not url:
        return {"success": False, "message": "Teams webhook not configured"}

    severity_colors = {
        "critical": "attention",
        "high": "warning",
        "medium": "accent",
        "low": "good",
    }

    try:
        body: list[dict[str, Any]] = [
            {
                "type": "TextBlock",
                "size": "Large",
                "weight": "Bolder",
                "text": f"🚨 {title}",
                "color": severity_colors.get(severity.lower(), "default"),
            },
            {
                "type": "FactSet",
                "facts": [
                    {"title": "Severity", "value": severity.upper()},
                ],
            },
            {
                "type": "TextBlock",
                "text": description,
                "wrap": True,
            },
        ]

        if source:
            body[1]["facts"].append({"title": "Source", "value": source})

        payload = {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "contentUrl": None,
                    "content": {
                        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                        "type": "AdaptiveCard",
                        "version": "1.4",
                        "body": body,
                    },
                }
            ],
        }

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, json=payload)

            if resp.status_code == 200:
                return {"success": True, "message": "Alert sent to Teams"}
            else:
                return {"success": False, "message": f"Teams error: {resp.status_code}"}

    except httpx.HTTPError as e:
        log.exception("Failed to send Teams alert")
        return {"success": False, "message": str(e)}
