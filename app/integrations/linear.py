"""Linear integration for issue tracking.

Provides:
- Issue creation/update from incidents
- Team and project management
- GraphQL API support
- Bidirectional sync with incidents
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import httpx

log = logging.getLogger("omnipotent.integrations.linear")


class LinearPriority(Enum):
    """Linear priority levels (0=No priority, 1=Urgent, 2=High, 3=Medium, 4=Low)."""

    NO_PRIORITY = 0
    URGENT = 1
    HIGH = 2
    MEDIUM = 3
    LOW = 4


class LinearStateType(Enum):
    """Linear workflow state types."""

    TRIAGE = "triage"
    BACKLOG = "backlog"
    UNSTARTED = "unstarted"
    STARTED = "started"
    COMPLETED = "completed"
    CANCELED = "canceled"


@dataclass
class LinearConfig:
    """Linear integration configuration."""

    api_key: str = ""
    team_id: str = ""
    default_state_id: str = ""
    custom_fields: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> "LinearConfig":
        """Load config from environment."""
        return cls(
            api_key=os.environ.get("LINEAR_API_KEY", ""),
            team_id=os.environ.get("LINEAR_TEAM_ID", ""),
            default_state_id=os.environ.get("LINEAR_DEFAULT_STATE_ID", ""),
        )

    def is_configured(self) -> bool:
        """Check if Linear is configured."""
        return bool(self.api_key)


@dataclass
class LinearIssue:
    """A Linear issue."""

    id: str
    identifier: str  # e.g., "ENG-123"
    title: str
    description: str = ""
    state: str = ""
    state_type: str = ""
    priority: int = 0
    priority_label: str = ""
    assignee_id: str | None = None
    assignee_name: str | None = None
    team_id: str = ""
    team_name: str = ""
    labels: list[str] = field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None
    completed_at: datetime | None = None
    url: str = ""
    custom_fields: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "identifier": self.identifier,
            "title": self.title,
            "description": self.description,
            "state": self.state,
            "state_type": self.state_type,
            "priority": self.priority,
            "priority_label": self.priority_label,
            "assignee_name": self.assignee_name,
            "team_name": self.team_name,
            "labels": self.labels,
            "url": self.url,
        }


@dataclass
class LinearComment:
    """A Linear comment."""

    id: str
    body: str
    user_id: str
    user_name: str
    created_at: datetime
    updated_at: datetime | None = None


@dataclass
class LinearTeam:
    """A Linear team."""

    id: str
    name: str
    key: str
    description: str = ""


class LinearClient:
    """Linear GraphQL API client."""

    BASE_URL = "https://api.linear.app/graphql"

    def __init__(self, config: LinearConfig | None = None):
        self.config = config or LinearConfig.from_env()

    def _get_headers(self) -> dict[str, str]:
        """Get request headers."""
        return {
            "Authorization": self.config.api_key,
            "Content-Type": "application/json",
        }

    async def _graphql(
        self,
        query: str,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Execute a GraphQL query."""
        if not self.config.is_configured():
            log.warning("Linear not configured")
            return None

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    self.BASE_URL,
                    headers=self._get_headers(),
                    json={"query": query, "variables": variables or {}},
                    timeout=30,
                )

                if resp.status_code >= 400:
                    log.error("Linear API error: %s %s", resp.status_code, resp.text)
                    return None

                result = resp.json()
                if "errors" in result:
                    log.error("Linear GraphQL errors: %s", result["errors"])
                    return None

                return result.get("data")
        except Exception as e:
            log.error("Linear request failed: %s", e)
            return None

    async def create_issue(
        self,
        title: str,
        description: str = "",
        team_id: str | None = None,
        priority: int = 0,
        state_id: str | None = None,
        assignee_id: str | None = None,
        label_ids: list[str] | None = None,
    ) -> LinearIssue | None:
        """Create a new Linear issue."""
        mutation = """
        mutation CreateIssue($input: IssueCreateInput!) {
            issueCreate(input: $input) {
                success
                issue {
                    id
                    identifier
                    title
                    description
                    url
                    priority
                    priorityLabel
                    state {
                        id
                        name
                        type
                    }
                    team {
                        id
                        name
                    }
                    assignee {
                        id
                        name
                    }
                    labels {
                        nodes {
                            name
                        }
                    }
                    createdAt
                }
            }
        }
        """

        input_data: dict[str, Any] = {
            "title": title,
            "teamId": team_id or self.config.team_id,
        }

        if description:
            input_data["description"] = description
        if priority:
            input_data["priority"] = priority
        if state_id:
            input_data["stateId"] = state_id
        elif self.config.default_state_id:
            input_data["stateId"] = self.config.default_state_id
        if assignee_id:
            input_data["assigneeId"] = assignee_id
        if label_ids:
            input_data["labelIds"] = label_ids

        result = await self._graphql(mutation, {"input": input_data})
        if not result:
            return None

        issue_data = result.get("issueCreate", {}).get("issue")
        if not issue_data:
            return None

        issue = self._parse_issue(issue_data)
        log.info("Created Linear issue %s", issue.identifier)
        return issue

    async def get_issue(self, issue_id: str) -> LinearIssue | None:
        """Get a Linear issue by ID or identifier."""
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
                state {
                    id
                    name
                    type
                }
                team {
                    id
                    name
                }
                assignee {
                    id
                    name
                }
                labels {
                    nodes {
                        name
                    }
                }
                createdAt
                updatedAt
                completedAt
            }
        }
        """

        result = await self._graphql(query, {"id": issue_id})
        if not result or not result.get("issue"):
            return None

        return self._parse_issue(result["issue"])

    def _parse_issue(self, data: dict[str, Any]) -> LinearIssue:
        """Parse issue data from GraphQL response."""
        state_data = data.get("state") or {}
        team_data = data.get("team") or {}
        assignee_data = data.get("assignee") or {}
        labels_data = data.get("labels", {}).get("nodes", [])

        return LinearIssue(
            id=data["id"],
            identifier=data.get("identifier", ""),
            title=data.get("title", ""),
            description=data.get("description", ""),
            state=state_data.get("name", ""),
            state_type=state_data.get("type", ""),
            priority=data.get("priority", 0),
            priority_label=data.get("priorityLabel", ""),
            assignee_id=assignee_data.get("id"),
            assignee_name=assignee_data.get("name"),
            team_id=team_data.get("id", ""),
            team_name=team_data.get("name", ""),
            labels=[label.get("name", "") for label in labels_data],
            created_at=self._parse_datetime(data.get("createdAt")),
            updated_at=self._parse_datetime(data.get("updatedAt")),
            completed_at=self._parse_datetime(data.get("completedAt")),
            url=data.get("url", ""),
        )

    def _parse_datetime(self, value: str | None) -> datetime | None:
        """Parse ISO datetime string."""
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return None

    async def update_issue(
        self,
        issue_id: str,
        title: str | None = None,
        description: str | None = None,
        priority: int | None = None,
        state_id: str | None = None,
        assignee_id: str | None = None,
        label_ids: list[str] | None = None,
    ) -> bool:
        """Update a Linear issue."""
        mutation = """
        mutation UpdateIssue($id: String!, $input: IssueUpdateInput!) {
            issueUpdate(id: $id, input: $input) {
                success
            }
        }
        """

        input_data: dict[str, Any] = {}
        if title is not None:
            input_data["title"] = title
        if description is not None:
            input_data["description"] = description
        if priority is not None:
            input_data["priority"] = priority
        if state_id is not None:
            input_data["stateId"] = state_id
        if assignee_id is not None:
            input_data["assigneeId"] = assignee_id
        if label_ids is not None:
            input_data["labelIds"] = label_ids

        if not input_data:
            return True

        result = await self._graphql(mutation, {"id": issue_id, "input": input_data})
        return bool(result and result.get("issueUpdate", {}).get("success"))

    async def add_comment(
        self,
        issue_id: str,
        body: str,
    ) -> LinearComment | None:
        """Add a comment to an issue."""
        mutation = """
        mutation AddComment($input: CommentCreateInput!) {
            commentCreate(input: $input) {
                success
                comment {
                    id
                    body
                    user {
                        id
                        name
                    }
                    createdAt
                }
            }
        }
        """

        result = await self._graphql(
            mutation,
            {"input": {"issueId": issue_id, "body": body}},
        )
        if not result:
            return None

        comment_data = result.get("commentCreate", {}).get("comment")
        if not comment_data:
            return None

        user_data = comment_data.get("user") or {}
        return LinearComment(
            id=comment_data["id"],
            body=body,
            user_id=user_data.get("id", ""),
            user_name=user_data.get("name", ""),
            created_at=self._parse_datetime(comment_data.get("createdAt")) or datetime.now(timezone.utc),
        )

    async def search_issues(
        self,
        query: str,
        team_id: str | None = None,
        limit: int = 50,
    ) -> list[LinearIssue]:
        """Search for issues."""
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
                    state {
                        id
                        name
                        type
                    }
                    team {
                        id
                        name
                    }
                    assignee {
                        id
                        name
                    }
                    labels {
                        nodes {
                            name
                        }
                    }
                    createdAt
                    updatedAt
                }
            }
        }
        """

        filter_data: dict[str, Any] = {}
        if query:
            filter_data["or"] = [
                {"title": {"containsIgnoreCase": query}},
                {"description": {"containsIgnoreCase": query}},
            ]
        if team_id:
            filter_data["team"] = {"id": {"eq": team_id}}

        result = await self._graphql(
            gql_query,
            {"filter": filter_data if filter_data else None, "first": limit},
        )
        if not result:
            return []

        issues = []
        for item in result.get("issues", {}).get("nodes", []):
            issues.append(self._parse_issue(item))

        return issues

    async def get_teams(self) -> list[LinearTeam]:
        """Get all teams."""
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

        result = await self._graphql(query)
        if not result:
            return []

        teams = []
        for item in result.get("teams", {}).get("nodes", []):
            teams.append(
                LinearTeam(
                    id=item["id"],
                    name=item.get("name", ""),
                    key=item.get("key", ""),
                    description=item.get("description", ""),
                )
            )

        return teams

    async def get_workflow_states(self, team_id: str | None = None) -> list[dict[str, Any]]:
        """Get workflow states for a team."""
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

        result = await self._graphql(
            query,
            {"teamId": team_id or self.config.team_id},
        )
        if not result:
            return []

        return result.get("workflowStates", {}).get("nodes", [])

    async def link_issue_to_url(
        self,
        issue_id: str,
        url: str,
        title: str = "",
    ) -> bool:
        """Link an issue to an external URL (e.g., incident page)."""
        mutation = """
        mutation CreateAttachment($input: AttachmentCreateInput!) {
            attachmentCreate(input: $input) {
                success
            }
        }
        """

        result = await self._graphql(
            mutation,
            {"input": {"issueId": issue_id, "url": url, "title": title or url}},
        )
        return bool(result and result.get("attachmentCreate", {}).get("success"))


_client: LinearClient | None = None


def get_linear_client() -> LinearClient:
    """Get the Linear client singleton."""
    global _client
    if _client is None:
        _client = LinearClient()
    return _client


async def create_incident_issue(
    incident_id: str,
    title: str,
    description: str,
    severity: str,
    service: str = "",
    team_id: str | None = None,
) -> LinearIssue | None:
    """Create a Linear issue for an incident."""
    client = get_linear_client()

    priority_map = {
        "critical": LinearPriority.URGENT.value,
        "high": LinearPriority.HIGH.value,
        "medium": LinearPriority.MEDIUM.value,
        "low": LinearPriority.LOW.value,
    }

    full_description = f"""
## Incident Details

**Incident ID:** {incident_id}
**Severity:** {severity.upper()}
**Service:** {service or 'N/A'}

---

{description}

---
_Created by DevOps Copilot_
"""

    issue = await client.create_issue(
        title=f"[{severity.upper()}] {title}",
        description=full_description,
        team_id=team_id,
        priority=priority_map.get(severity.lower(), LinearPriority.MEDIUM.value),
    )

    if issue:
        # Link to incident page if we have one
        await client.link_issue_to_url(
            issue.id,
            f"https://app.example.com/incidents/{incident_id}",
            f"Incident {incident_id}",
        )

    return issue


async def sync_incident_status(
    issue_id: str,
    incident_status: str,
    team_id: str | None = None,
) -> bool:
    """Sync incident status to Linear issue state."""
    client = get_linear_client()

    # Get workflow states to find the matching one
    states = await client.get_workflow_states(team_id)

    status_map = {
        "investigating": "started",
        "identified": "started",
        "monitoring": "started",
        "resolved": "completed",
        "closed": "completed",
    }

    target_state_type = status_map.get(incident_status.lower())
    if not target_state_type:
        return False

    # Find a state with the matching type
    target_state_id = None
    for state in states:
        if state.get("type") == target_state_type:
            target_state_id = state.get("id")
            break

    if target_state_id:
        return await client.update_issue(issue_id, state_id=target_state_id)
    return False


async def add_investigation_update(
    issue_id: str,
    update_text: str,
    author: str = "DevOps Copilot",
) -> LinearComment | None:
    """Add an investigation update as a Linear comment."""
    client = get_linear_client()

    comment_body = f"""
## Investigation Update

{update_text}

---
_Posted by {author}_
"""

    return await client.add_comment(issue_id, comment_body)
