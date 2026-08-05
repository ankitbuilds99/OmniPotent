"""Jira issue tracking MCP server.

Gives the agent Jira ticket management capabilities for incident tracking:
- Create/update issues
- Link incidents to tickets
- Search issues with JQL
- Get project info

When `JIRA_API_TOKEN` is set it calls the real Jira REST API; otherwise it
runs in OFFLINE DEMO mode with fixtures.

Live API (per https://developer.atlassian.com/cloud/jira/platform/rest/):
  - POST https://{domain}.atlassian.net/rest/api/3/issue
  - GET  https://{domain}.atlassian.net/rest/api/3/issue/{key}
  - POST https://{domain}.atlassian.net/rest/api/3/search
  - auth: Basic (email:api_token)

Run standalone for debugging:
    JIRA_API_TOKEN=... JIRA_BASE_URL=... python -m app.mcp.servers.jira.server
"""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

JIRA_BASE_URL = os.environ.get("JIRA_BASE_URL", "").strip()
JIRA_EMAIL = os.environ.get("JIRA_EMAIL", "").strip()
JIRA_API_TOKEN = os.environ.get("JIRA_API_TOKEN", "").strip()
JIRA_PROJECT_KEY = os.environ.get("JIRA_PROJECT_KEY", "").strip()
OFFLINE = not (JIRA_BASE_URL and JIRA_EMAIL and JIRA_API_TOKEN)

mcp = FastMCP("jira")


def _as_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _auth() -> tuple[str, str]:
    return (JIRA_EMAIL, JIRA_API_TOKEN)


def _headers() -> dict:
    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


# --- Offline fixtures ---
def _get_fixture_issues() -> list[dict]:
    return [
        {
            "key": "INC-101",
            "id": "10101",
            "summary": "[HIGH] checkout-svc 5xx spike",
            "description": "Investigating high error rate in checkout service",
            "status": "In Progress",
            "priority": "High",
            "assignee": "oncall-team",
            "labels": ["incident", "severity:high"],
            "url": "https://example.atlassian.net/browse/INC-101",
        },
        {
            "key": "INC-100",
            "id": "10100",
            "summary": "[MEDIUM] payment gateway latency",
            "description": "Payment service showing elevated p95 latency",
            "status": "Open",
            "priority": "Medium",
            "assignee": None,
            "labels": ["incident", "severity:medium"],
            "url": "https://example.atlassian.net/browse/INC-100",
        },
    ]


def _get_fixture_project() -> dict:
    return {
        "key": JIRA_PROJECT_KEY or "INC",
        "name": "Incidents",
        "description": "Incident tracking project",
        "issue_types": ["Bug", "Task", "Story", "Incident"],
    }


@mcp.tool()
def create_issue(
    summary: str,
    description: str = "",
    issue_type: str = "Task",
    priority: str = "Medium",
    labels: str = "",
    project_key: str = "",
) -> dict:
    """Create a new Jira issue.

    Args:
        summary: Issue title/summary.
        description: Detailed description.
        issue_type: Type of issue (Bug, Task, Story, Incident).
        priority: Priority level (Highest, High, Medium, Low, Lowest).
        labels: Comma-separated list of labels.
        project_key: Project key (defaults to JIRA_PROJECT_KEY env var).
    """
    if OFFLINE:
        return {
            "status": "created (simulated - offline demo)",
            "key": "INC-999",
            "id": "10999",
            "summary": summary,
            "url": f"https://example.atlassian.net/browse/INC-999",
        }

    import httpx

    fields: dict = {
        "project": {"key": project_key or JIRA_PROJECT_KEY},
        "summary": summary,
        "issuetype": {"name": issue_type},
        "priority": {"name": priority},
    }

    if description:
        fields["description"] = {
            "type": "doc",
            "version": 1,
            "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": description}]}
            ],
        }

    if labels:
        fields["labels"] = [l.strip() for l in labels.split(",") if l.strip()]

    resp = httpx.post(
        f"{JIRA_BASE_URL}/rest/api/3/issue",
        auth=_auth(),
        headers=_headers(),
        json={"fields": fields},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    return {
        "status": "created",
        "key": data["key"],
        "id": data["id"],
        "summary": summary,
        "url": f"{JIRA_BASE_URL}/browse/{data['key']}",
    }


@mcp.tool()
def get_issue(issue_key: str) -> dict:
    """Get a Jira issue by key (e.g., INC-123)."""
    if OFFLINE:
        for issue in _get_fixture_issues():
            if issue["key"] == issue_key or issue_key == "":
                return issue
        return {"error": f"Issue {issue_key} not found (offline demo)"}

    import httpx

    resp = httpx.get(
        f"{JIRA_BASE_URL}/rest/api/3/issue/{issue_key}",
        auth=_auth(),
        headers=_headers(),
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    fields = data.get("fields", {})

    return {
        "key": data["key"],
        "id": data["id"],
        "summary": fields.get("summary", ""),
        "description": _extract_text(fields.get("description")),
        "status": (fields.get("status") or {}).get("name", ""),
        "priority": (fields.get("priority") or {}).get("name", ""),
        "assignee": (fields.get("assignee") or {}).get("displayName"),
        "labels": fields.get("labels", []),
        "url": f"{JIRA_BASE_URL}/browse/{data['key']}",
    }


def _extract_text(doc: dict | None) -> str:
    """Extract plain text from Atlassian Document Format."""
    if not doc:
        return ""
    if isinstance(doc, str):
        return doc

    parts: list[str] = []

    def extract(node: dict) -> None:
        if node.get("type") == "text":
            parts.append(node.get("text", ""))
        for child in node.get("content", []):
            extract(child)

    extract(doc)
    return "".join(parts)


@mcp.tool()
def update_issue(
    issue_key: str,
    summary: str = "",
    description: str = "",
    priority: str = "",
    labels: str = "",
    status: str = "",
) -> dict:
    """Update a Jira issue.

    Args:
        issue_key: Issue key to update (e.g., INC-123).
        summary: New summary (optional).
        description: New description (optional).
        priority: New priority (optional).
        labels: New labels, comma-separated (optional).
        status: Transition to this status (optional, must be valid transition).
    """
    if OFFLINE:
        return {
            "status": "updated (simulated - offline demo)",
            "key": issue_key,
        }

    import httpx

    fields: dict = {}
    if summary:
        fields["summary"] = summary
    if description:
        fields["description"] = {
            "type": "doc",
            "version": 1,
            "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": description}]}
            ],
        }
    if priority:
        fields["priority"] = {"name": priority}
    if labels:
        fields["labels"] = [l.strip() for l in labels.split(",") if l.strip()]

    if fields:
        resp = httpx.put(
            f"{JIRA_BASE_URL}/rest/api/3/issue/{issue_key}",
            auth=_auth(),
            headers=_headers(),
            json={"fields": fields},
            timeout=30,
        )
        resp.raise_for_status()

    if status:
        _transition_issue(issue_key, status)

    return {"status": "updated", "key": issue_key}


def _transition_issue(issue_key: str, status_name: str) -> bool:
    """Transition an issue to a new status."""
    import httpx

    # Get available transitions
    resp = httpx.get(
        f"{JIRA_BASE_URL}/rest/api/3/issue/{issue_key}/transitions",
        auth=_auth(),
        headers=_headers(),
        timeout=30,
    )
    resp.raise_for_status()

    transition_id = None
    for t in resp.json().get("transitions", []):
        if t.get("name", "").lower() == status_name.lower():
            transition_id = t["id"]
            break

    if not transition_id:
        return False

    resp = httpx.post(
        f"{JIRA_BASE_URL}/rest/api/3/issue/{issue_key}/transitions",
        auth=_auth(),
        headers=_headers(),
        json={"transition": {"id": transition_id}},
        timeout=30,
    )
    resp.raise_for_status()
    return True


@mcp.tool()
def search_issues(
    jql: str = "",
    project: str = "",
    status: str = "",
    labels: str = "",
    limit: int | str = 50,
) -> list[dict]:
    """Search for Jira issues.

    Args:
        jql: JQL query string (if provided, other filters ignored).
        project: Filter by project key.
        status: Filter by status.
        labels: Filter by labels (comma-separated).
        limit: Max results to return.
    """
    limit = _as_int(limit, 50)

    if OFFLINE:
        results = _get_fixture_issues()
        if status:
            results = [i for i in results if i.get("status", "").lower() == status.lower()]
        return results[:limit]

    import httpx

    if not jql:
        conditions = []
        if project:
            conditions.append(f'project = "{project}"')
        if status:
            conditions.append(f'status = "{status}"')
        if labels:
            for label in labels.split(","):
                label = label.strip()
                if label:
                    conditions.append(f'labels = "{label}"')
        jql = " AND ".join(conditions) if conditions else "ORDER BY created DESC"

    resp = httpx.post(
        f"{JIRA_BASE_URL}/rest/api/3/search",
        auth=_auth(),
        headers=_headers(),
        json={
            "jql": jql,
            "maxResults": limit,
            "fields": ["summary", "status", "priority", "assignee", "labels"],
        },
        timeout=30,
    )
    resp.raise_for_status()

    issues = []
    for item in resp.json().get("issues", []):
        fields = item.get("fields", {})
        issues.append({
            "key": item["key"],
            "id": item["id"],
            "summary": fields.get("summary", ""),
            "status": (fields.get("status") or {}).get("name", ""),
            "priority": (fields.get("priority") or {}).get("name", ""),
            "assignee": (fields.get("assignee") or {}).get("displayName"),
            "labels": fields.get("labels", []),
            "url": f"{JIRA_BASE_URL}/browse/{item['key']}",
        })

    return issues


@mcp.tool()
def add_comment(issue_key: str, comment: str) -> dict:
    """Add a comment to a Jira issue.

    Args:
        issue_key: Issue key (e.g., INC-123).
        comment: Comment text to add.
    """
    if OFFLINE:
        return {
            "status": "comment added (simulated - offline demo)",
            "key": issue_key,
            "comment": comment[:100] + "..." if len(comment) > 100 else comment,
        }

    import httpx

    resp = httpx.post(
        f"{JIRA_BASE_URL}/rest/api/3/issue/{issue_key}/comment",
        auth=_auth(),
        headers=_headers(),
        json={
            "body": {
                "type": "doc",
                "version": 1,
                "content": [
                    {"type": "paragraph", "content": [{"type": "text", "text": comment}]}
                ],
            }
        },
        timeout=30,
    )
    resp.raise_for_status()

    return {"status": "comment added", "key": issue_key}


@mcp.tool()
def link_issues(
    source_key: str,
    target_key: str,
    link_type: str = "Relates",
) -> dict:
    """Link two Jira issues.

    Args:
        source_key: Source issue key.
        target_key: Target issue key.
        link_type: Type of link (Relates, Blocks, Duplicates, etc.).
    """
    if OFFLINE:
        return {
            "status": "linked (simulated - offline demo)",
            "source": source_key,
            "target": target_key,
            "link_type": link_type,
        }

    import httpx

    resp = httpx.post(
        f"{JIRA_BASE_URL}/rest/api/3/issueLink",
        auth=_auth(),
        headers=_headers(),
        json={
            "type": {"name": link_type},
            "inwardIssue": {"key": source_key},
            "outwardIssue": {"key": target_key},
        },
        timeout=30,
    )
    resp.raise_for_status()

    return {
        "status": "linked",
        "source": source_key,
        "target": target_key,
        "link_type": link_type,
    }


@mcp.tool()
def get_project_info(project_key: str = "") -> dict:
    """Get project information and available issue types.

    Args:
        project_key: Project key (defaults to JIRA_PROJECT_KEY env var).
    """
    if OFFLINE:
        return _get_fixture_project()

    import httpx

    key = project_key or JIRA_PROJECT_KEY
    resp = httpx.get(
        f"{JIRA_BASE_URL}/rest/api/3/project/{key}",
        auth=_auth(),
        headers=_headers(),
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    return {
        "key": data.get("key", ""),
        "name": data.get("name", ""),
        "description": data.get("description", ""),
        "issue_types": [it.get("name", "") for it in data.get("issueTypes", [])],
    }


if __name__ == "__main__":
    mcp.run(transport="stdio")
