"""Jira integration for creating and updating issues."""

from __future__ import annotations

import base64
import logging
from typing import Any

import httpx

from app.config import get_settings

log = logging.getLogger("omnipotent.integrations.jira")


def is_jira_configured() -> bool:
    """Check if Jira integration is configured."""
    s = get_settings()
    return bool(
        getattr(s, "jira_base_url", None)
        and getattr(s, "jira_email", None)
        and getattr(s, "jira_api_token", None)
    )


def _get_auth_header() -> str:
    """Get Basic auth header for Jira API."""
    s = get_settings()
    email = getattr(s, "jira_email", "")
    token = getattr(s, "jira_api_token", "")
    credentials = base64.b64encode(f"{email}:{token}".encode()).decode()
    return f"Basic {credentials}"


async def create_issue(
    project_key: str,
    summary: str,
    description: str,
    issue_type: str = "Bug",
    priority: str | None = None,
    labels: list[str] | None = None,
    components: list[str] | None = None,
) -> dict[str, Any]:
    """Create a new Jira issue.

    Args:
        project_key: Jira project key (e.g., "OPS")
        summary: Issue summary/title
        description: Issue description (supports Jira markup)
        issue_type: Issue type (Bug, Task, Story, etc.)
        priority: Priority name (Highest, High, Medium, Low, Lowest)
        labels: List of labels to add
        components: List of component names

    Returns:
        Result dict with success status and issue data
    """
    s = get_settings()
    base_url = getattr(s, "jira_base_url", None)

    if not is_jira_configured():
        return {"success": False, "message": "Jira not configured"}

    try:
        fields: dict[str, Any] = {
            "project": {"key": project_key},
            "summary": summary,
            "description": {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [{"type": "text", "text": description}],
                    }
                ],
            },
            "issuetype": {"name": issue_type},
        }

        if priority:
            fields["priority"] = {"name": priority}

        if labels:
            fields["labels"] = labels

        if components:
            fields["components"] = [{"name": c} for c in components]

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{base_url}/rest/api/3/issue",
                headers={
                    "Authorization": _get_auth_header(),
                    "Content-Type": "application/json",
                },
                json={"fields": fields},
            )

            if resp.status_code == 201:
                data = resp.json()
                return {
                    "success": True,
                    "issue_key": data["key"],
                    "issue_id": data["id"],
                    "issue_url": f"{base_url}/browse/{data['key']}",
                }
            else:
                log.warning("Jira API error: %s %s", resp.status_code, resp.text)
                return {"success": False, "message": f"API error: {resp.status_code}"}

    except httpx.HTTPError as e:
        log.exception("Failed to create Jira issue")
        return {"success": False, "message": str(e)}


async def add_comment(
    issue_key: str,
    comment: str,
) -> dict[str, Any]:
    """Add a comment to a Jira issue.

    Args:
        issue_key: Jira issue key (e.g., "OPS-123")
        comment: Comment text (supports Jira markup)

    Returns:
        Result dict with success status
    """
    s = get_settings()
    base_url = getattr(s, "jira_base_url", None)

    if not is_jira_configured():
        return {"success": False, "message": "Jira not configured"}

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{base_url}/rest/api/3/issue/{issue_key}/comment",
                headers={
                    "Authorization": _get_auth_header(),
                    "Content-Type": "application/json",
                },
                json={
                    "body": {
                        "type": "doc",
                        "version": 1,
                        "content": [
                            {
                                "type": "paragraph",
                                "content": [{"type": "text", "text": comment}],
                            }
                        ],
                    }
                },
            )

            if resp.status_code == 201:
                return {"success": True, "message": "Comment added"}
            else:
                return {"success": False, "message": f"API error: {resp.status_code}"}

    except httpx.HTTPError as e:
        log.exception("Failed to add Jira comment")
        return {"success": False, "message": str(e)}


async def transition_issue(
    issue_key: str,
    transition_name: str,
    resolution: str | None = None,
) -> dict[str, Any]:
    """Transition a Jira issue to a new status.

    Args:
        issue_key: Jira issue key (e.g., "OPS-123")
        transition_name: Name of transition (e.g., "Done", "In Progress")
        resolution: Resolution name if transitioning to resolved state

    Returns:
        Result dict with success status
    """
    s = get_settings()
    base_url = getattr(s, "jira_base_url", None)

    if not is_jira_configured():
        return {"success": False, "message": "Jira not configured"}

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            # First, get available transitions
            trans_resp = await client.get(
                f"{base_url}/rest/api/3/issue/{issue_key}/transitions",
                headers={
                    "Authorization": _get_auth_header(),
                    "Content-Type": "application/json",
                },
            )

            if trans_resp.status_code != 200:
                return {"success": False, "message": "Failed to get transitions"}

            transitions = trans_resp.json().get("transitions", [])
            target_transition = None
            for t in transitions:
                if t["name"].lower() == transition_name.lower():
                    target_transition = t
                    break

            if not target_transition:
                available = [t["name"] for t in transitions]
                return {
                    "success": False,
                    "message": f"Transition '{transition_name}' not found. Available: {available}",
                }

            # Perform transition
            payload: dict[str, Any] = {"transition": {"id": target_transition["id"]}}
            if resolution:
                payload["fields"] = {"resolution": {"name": resolution}}

            resp = await client.post(
                f"{base_url}/rest/api/3/issue/{issue_key}/transitions",
                headers={
                    "Authorization": _get_auth_header(),
                    "Content-Type": "application/json",
                },
                json=payload,
            )

            if resp.status_code == 204:
                return {"success": True, "message": f"Issue transitioned to {transition_name}"}
            else:
                return {"success": False, "message": f"API error: {resp.status_code}"}

    except httpx.HTTPError as e:
        log.exception("Failed to transition Jira issue")
        return {"success": False, "message": str(e)}


async def search_issues(
    jql: str,
    max_results: int = 50,
) -> dict[str, Any]:
    """Search for Jira issues using JQL.

    Args:
        jql: JQL query string
        max_results: Maximum number of results

    Returns:
        Result dict with issues list
    """
    s = get_settings()
    base_url = getattr(s, "jira_base_url", None)

    if not is_jira_configured():
        return {"success": False, "message": "Jira not configured", "issues": []}

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{base_url}/rest/api/3/search",
                headers={
                    "Authorization": _get_auth_header(),
                    "Content-Type": "application/json",
                },
                json={
                    "jql": jql,
                    "maxResults": max_results,
                    "fields": ["summary", "status", "priority", "assignee", "created", "updated"],
                },
            )

            if resp.status_code == 200:
                data = resp.json()
                issues = [
                    {
                        "key": issue["key"],
                        "summary": issue["fields"]["summary"],
                        "status": issue["fields"]["status"]["name"],
                        "priority": issue["fields"].get("priority", {}).get("name"),
                        "assignee": issue["fields"].get("assignee", {}).get("displayName") if issue["fields"].get("assignee") else None,
                        "url": f"{base_url}/browse/{issue['key']}",
                    }
                    for issue in data.get("issues", [])
                ]
                return {"success": True, "issues": issues, "total": data.get("total", 0)}
            else:
                return {"success": False, "message": f"API error: {resp.status_code}", "issues": []}

    except httpx.HTTPError as e:
        log.exception("Failed to search Jira issues")
        return {"success": False, "message": str(e), "issues": []}


async def link_to_investigation(
    issue_key: str,
    investigation_id: str,
    investigation_url: str,
) -> dict[str, Any]:
    """Add a remote link to an investigation from a Jira issue.

    Args:
        issue_key: Jira issue key
        investigation_id: OmniPotent investigation ID
        investigation_url: URL to the investigation

    Returns:
        Result dict with success status
    """
    s = get_settings()
    base_url = getattr(s, "jira_base_url", None)

    if not is_jira_configured():
        return {"success": False, "message": "Jira not configured"}

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{base_url}/rest/api/3/issue/{issue_key}/remotelink",
                headers={
                    "Authorization": _get_auth_header(),
                    "Content-Type": "application/json",
                },
                json={
                    "globalId": f"omnipotent:{investigation_id}",
                    "application": {
                        "type": "com.omnipotent",
                        "name": "OmniPotent",
                    },
                    "object": {
                        "url": investigation_url,
                        "title": f"Investigation {investigation_id}",
                        "icon": {
                            "title": "OmniPotent Investigation",
                        },
                    },
                },
            )

            if resp.status_code in (200, 201):
                return {"success": True, "message": "Link added"}
            else:
                return {"success": False, "message": f"API error: {resp.status_code}"}

    except httpx.HTTPError as e:
        log.exception("Failed to add Jira remote link")
        return {"success": False, "message": str(e)}
