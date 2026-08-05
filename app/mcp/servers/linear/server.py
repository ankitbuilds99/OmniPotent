"""Linear issue tracking MCP server.

Gives the agent Linear issue management capabilities:
- Create/update issues
- Link incidents to issues
- GraphQL API support
- Team and project management

When `LINEAR_API_KEY` is set it calls the real Linear GraphQL API; otherwise it
runs in OFFLINE DEMO mode with fixtures.

Live API (per https://developers.linear.app/docs/graphql/):
  - POST https://api.linear.app/graphql
  - auth: Authorization header with API key

Run standalone for debugging:
    LINEAR_API_KEY=... python -m app.mcp.servers.linear.server
"""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

LINEAR_API_KEY = os.environ.get("LINEAR_API_KEY", "").strip()
LINEAR_TEAM_ID = os.environ.get("LINEAR_TEAM_ID", "").strip()
OFFLINE = not LINEAR_API_KEY

API_URL = "https://api.linear.app/graphql"

mcp = FastMCP("linear")


def _as_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _headers() -> dict:
    return {
        "Authorization": LINEAR_API_KEY,
        "Content-Type": "application/json",
    }


# --- Offline fixtures ---
def _get_fixture_issues() -> list[dict]:
    return [
        {
            "id": "issue-uuid-1",
            "identifier": "ENG-101",
            "title": "[HIGH] checkout-svc 5xx spike",
            "description": "Investigating high error rate in checkout service",
            "state": "In Progress",
            "state_type": "started",
            "priority": 2,
            "priority_label": "High",
            "team_name": "Engineering",
            "labels": ["incident", "severity:high"],
            "url": "https://linear.app/example/issue/ENG-101",
        },
        {
            "id": "issue-uuid-2",
            "identifier": "ENG-100",
            "title": "[MEDIUM] payment gateway latency",
            "description": "Payment service showing elevated p95 latency",
            "state": "Backlog",
            "state_type": "backlog",
            "priority": 3,
            "priority_label": "Medium",
            "team_name": "Engineering",
            "labels": ["incident", "severity:medium"],
            "url": "https://linear.app/example/issue/ENG-100",
        },
    ]


def _get_fixture_teams() -> list[dict]:
    return [
        {"id": "team-uuid-1", "name": "Engineering", "key": "ENG"},
        {"id": "team-uuid-2", "name": "Platform", "key": "PLT"},
    ]


async def _graphql(query: str, variables: dict | None = None) -> dict | None:
    """Execute a GraphQL query against Linear API."""
    import httpx

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            API_URL,
            headers=_headers(),
            json={"query": query, "variables": variables or {}},
            timeout=30,
        )

        if resp.status_code >= 400:
            return None

        result = resp.json()
        if "errors" in result:
            return None

        return result.get("data")


@mcp.tool()
def create_issue(
    title: str,
    description: str = "",
    team_id: str = "",
    priority: int | str = 0,
    labels: str = "",
) -> dict:
    """Create a new Linear issue.

    Args:
        title: Issue title.
        description: Issue description (markdown supported).
        team_id: Team ID (defaults to LINEAR_TEAM_ID env var).
        priority: Priority (0=None, 1=Urgent, 2=High, 3=Medium, 4=Low).
        labels: Comma-separated label IDs (optional).
    """
    if OFFLINE:
        return {
            "status": "created (simulated - offline demo)",
            "id": "issue-uuid-demo",
            "identifier": "ENG-999",
            "title": title,
            "url": "https://linear.app/example/issue/ENG-999",
        }

    import httpx

    mutation = """
    mutation CreateIssue($input: IssueCreateInput!) {
        issueCreate(input: $input) {
            success
            issue {
                id
                identifier
                title
                url
                priority
                priorityLabel
                state { name type }
                team { name }
            }
        }
    }
    """

    input_data: dict = {
        "title": title,
        "teamId": team_id or LINEAR_TEAM_ID,
    }
    if description:
        input_data["description"] = description
    if priority:
        input_data["priority"] = _as_int(priority, 0)
    if labels:
        input_data["labelIds"] = [lbl.strip() for lbl in labels.split(",") if lbl.strip()]

    resp = httpx.post(
        API_URL,
        headers=_headers(),
        json={"query": mutation, "variables": {"input": input_data}},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    if "errors" in data:
        return {"error": str(data["errors"])}

    issue = data.get("data", {}).get("issueCreate", {}).get("issue", {})
    return {
        "status": "created",
        "id": issue.get("id", ""),
        "identifier": issue.get("identifier", ""),
        "title": issue.get("title", ""),
        "url": issue.get("url", ""),
    }


@mcp.tool()
def get_issue(issue_id: str) -> dict:
    """Get a Linear issue by ID or identifier.

    Args:
        issue_id: Issue ID or identifier (e.g., ENG-123).
    """
    if OFFLINE:
        for issue in _get_fixture_issues():
            if issue["id"] == issue_id or issue["identifier"] == issue_id:
                return issue
        return {"error": f"Issue {issue_id} not found (offline demo)"}

    import httpx

    query = """
    query GetIssue($id: String!) {
        issue(id: $id) {
            id
            identifier
            title
            description
            url
            priority
            priorityLabel
            state { name type }
            team { name }
            assignee { name }
            labels { nodes { name } }
            createdAt
            updatedAt
        }
    }
    """

    resp = httpx.post(
        API_URL,
        headers=_headers(),
        json={"query": query, "variables": {"id": issue_id}},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    if "errors" in data or not data.get("data", {}).get("issue"):
        return {"error": f"Issue {issue_id} not found"}

    issue = data["data"]["issue"]
    state = issue.get("state") or {}
    team = issue.get("team") or {}
    labels = issue.get("labels", {}).get("nodes", [])

    return {
        "id": issue.get("id", ""),
        "identifier": issue.get("identifier", ""),
        "title": issue.get("title", ""),
        "description": issue.get("description", ""),
        "state": state.get("name", ""),
        "state_type": state.get("type", ""),
        "priority": issue.get("priority", 0),
        "priority_label": issue.get("priorityLabel", ""),
        "team_name": team.get("name", ""),
        "labels": [lbl.get("name", "") for lbl in labels],
        "url": issue.get("url", ""),
    }


@mcp.tool()
def update_issue(
    issue_id: str,
    title: str = "",
    description: str = "",
    priority: int | str = "",
    state_id: str = "",
    assignee_id: str = "",
) -> dict:
    """Update a Linear issue.

    Args:
        issue_id: Issue ID or identifier.
        title: New title (optional).
        description: New description (optional).
        priority: New priority 0-4 (optional).
        state_id: New workflow state ID (optional).
        assignee_id: New assignee user ID (optional).
    """
    if OFFLINE:
        return {
            "status": "updated (simulated - offline demo)",
            "id": issue_id,
        }

    import httpx

    mutation = """
    mutation UpdateIssue($id: String!, $input: IssueUpdateInput!) {
        issueUpdate(id: $id, input: $input) {
            success
        }
    }
    """

    input_data: dict = {}
    if title:
        input_data["title"] = title
    if description:
        input_data["description"] = description
    if priority != "":
        input_data["priority"] = _as_int(priority, 0)
    if state_id:
        input_data["stateId"] = state_id
    if assignee_id:
        input_data["assigneeId"] = assignee_id

    if not input_data:
        return {"status": "no changes", "id": issue_id}

    resp = httpx.post(
        API_URL,
        headers=_headers(),
        json={"query": mutation, "variables": {"id": issue_id, "input": input_data}},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    if "errors" in data:
        return {"error": str(data["errors"])}

    success = data.get("data", {}).get("issueUpdate", {}).get("success", False)
    return {"status": "updated" if success else "failed", "id": issue_id}


@mcp.tool()
def search_issues(
    query: str = "",
    team_id: str = "",
    state: str = "",
    priority: int | str = "",
    limit: int | str = 50,
) -> list[dict]:
    """Search for Linear issues.

    Args:
        query: Text search query.
        team_id: Filter by team ID.
        state: Filter by state type (triage, backlog, unstarted, started, completed, canceled).
        priority: Filter by priority (0-4).
        limit: Max results.
    """
    limit = _as_int(limit, 50)

    if OFFLINE:
        results = _get_fixture_issues()
        if state:
            results = [i for i in results if i.get("state_type", "").lower() == state.lower()]
        return results[:limit]

    import httpx

    gql_query = """
    query SearchIssues($filter: IssueFilter, $first: Int) {
        issues(filter: $filter, first: $first) {
            nodes {
                id
                identifier
                title
                description
                url
                priority
                priorityLabel
                state { name type }
                team { name }
                labels { nodes { name } }
            }
        }
    }
    """

    filter_data: dict = {}
    if query:
        filter_data["or"] = [
            {"title": {"containsIgnoreCase": query}},
            {"description": {"containsIgnoreCase": query}},
        ]
    if team_id:
        filter_data["team"] = {"id": {"eq": team_id}}
    if state:
        filter_data["state"] = {"type": {"eq": state}}
    if priority != "":
        filter_data["priority"] = {"eq": _as_int(priority, 0)}

    resp = httpx.post(
        API_URL,
        headers=_headers(),
        json={
            "query": gql_query,
            "variables": {"filter": filter_data if filter_data else None, "first": limit},
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    if "errors" in data:
        return []

    issues = []
    for item in data.get("data", {}).get("issues", {}).get("nodes", []):
        state_data = item.get("state") or {}
        team_data = item.get("team") or {}
        labels_data = item.get("labels", {}).get("nodes", [])

        issues.append({
            "id": item.get("id", ""),
            "identifier": item.get("identifier", ""),
            "title": item.get("title", ""),
            "state": state_data.get("name", ""),
            "state_type": state_data.get("type", ""),
            "priority": item.get("priority", 0),
            "priority_label": item.get("priorityLabel", ""),
            "team_name": team_data.get("name", ""),
            "labels": [lbl.get("name", "") for lbl in labels_data],
            "url": item.get("url", ""),
        })

    return issues


@mcp.tool()
def add_comment(issue_id: str, comment: str) -> dict:
    """Add a comment to a Linear issue.

    Args:
        issue_id: Issue ID or identifier.
        comment: Comment text (markdown supported).
    """
    if OFFLINE:
        return {
            "status": "comment added (simulated - offline demo)",
            "id": issue_id,
        }

    import httpx

    mutation = """
    mutation AddComment($input: CommentCreateInput!) {
        commentCreate(input: $input) {
            success
            comment { id }
        }
    }
    """

    resp = httpx.post(
        API_URL,
        headers=_headers(),
        json={
            "query": mutation,
            "variables": {"input": {"issueId": issue_id, "body": comment}},
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    if "errors" in data:
        return {"error": str(data["errors"])}

    success = data.get("data", {}).get("commentCreate", {}).get("success", False)
    return {"status": "comment added" if success else "failed", "id": issue_id}


@mcp.tool()
def get_teams() -> list[dict]:
    """Get all Linear teams."""
    if OFFLINE:
        return _get_fixture_teams()

    import httpx

    query = """
    query GetTeams {
        teams {
            nodes {
                id
                name
                key
                description
            }
        }
    }
    """

    resp = httpx.post(
        API_URL,
        headers=_headers(),
        json={"query": query},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    if "errors" in data:
        return []

    return [
        {
            "id": team.get("id", ""),
            "name": team.get("name", ""),
            "key": team.get("key", ""),
            "description": team.get("description", ""),
        }
        for team in data.get("data", {}).get("teams", {}).get("nodes", [])
    ]


@mcp.tool()
def get_workflow_states(team_id: str = "") -> list[dict]:
    """Get workflow states for a team.

    Args:
        team_id: Team ID (defaults to LINEAR_TEAM_ID env var).
    """
    if OFFLINE:
        return [
            {"id": "state-1", "name": "Backlog", "type": "backlog", "position": 0},
            {"id": "state-2", "name": "Todo", "type": "unstarted", "position": 1},
            {"id": "state-3", "name": "In Progress", "type": "started", "position": 2},
            {"id": "state-4", "name": "Done", "type": "completed", "position": 3},
            {"id": "state-5", "name": "Canceled", "type": "canceled", "position": 4},
        ]

    import httpx

    query = """
    query GetStates($teamId: String) {
        workflowStates(filter: { team: { id: { eq: $teamId } } }) {
            nodes {
                id
                name
                type
                position
            }
        }
    }
    """

    resp = httpx.post(
        API_URL,
        headers=_headers(),
        json={"query": query, "variables": {"teamId": team_id or LINEAR_TEAM_ID}},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    if "errors" in data:
        return []

    return [
        {
            "id": state.get("id", ""),
            "name": state.get("name", ""),
            "type": state.get("type", ""),
            "position": state.get("position", 0),
        }
        for state in data.get("data", {}).get("workflowStates", {}).get("nodes", [])
    ]


@mcp.tool()
def link_incident(
    issue_id: str,
    incident_url: str,
    title: str = "Related Incident",
) -> dict:
    """Link an incident URL to a Linear issue.

    Args:
        issue_id: Issue ID.
        incident_url: URL of the incident.
        title: Title for the link.
    """
    if OFFLINE:
        return {
            "status": "linked (simulated - offline demo)",
            "issue_id": issue_id,
            "url": incident_url,
        }

    import httpx

    mutation = """
    mutation CreateAttachment($input: AttachmentCreateInput!) {
        attachmentCreate(input: $input) {
            success
        }
    }
    """

    resp = httpx.post(
        API_URL,
        headers=_headers(),
        json={
            "query": mutation,
            "variables": {"input": {"issueId": issue_id, "url": incident_url, "title": title}},
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    if "errors" in data:
        return {"error": str(data["errors"])}

    success = data.get("data", {}).get("attachmentCreate", {}).get("success", False)
    return {
        "status": "linked" if success else "failed",
        "issue_id": issue_id,
        "url": incident_url,
    }


if __name__ == "__main__":
    mcp.run(transport="stdio")
