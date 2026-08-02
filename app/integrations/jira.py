"""Jira integration for ticket management.

Provides:
- Issue creation from incidents/action items
- Bidirectional sync
- Custom field mapping
- Workflow automation
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import httpx

log = logging.getLogger("omnipotent.integrations.jira")


class JiraIssueType(Enum):
    """Jira issue types."""

    BUG = "Bug"
    TASK = "Task"
    STORY = "Story"
    EPIC = "Epic"
    INCIDENT = "Incident"
    PROBLEM = "Problem"
    CHANGE = "Change"


class JiraPriority(Enum):
    """Jira priority levels."""

    HIGHEST = "Highest"
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"
    LOWEST = "Lowest"


@dataclass
class JiraConfig:
    """Jira integration configuration."""

    base_url: str = ""
    email: str = ""
    api_token: str = ""
    project_key: str = ""
    default_issue_type: str = "Task"
    custom_fields: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> "JiraConfig":
        """Load config from environment."""
        return cls(
            base_url=os.environ.get("JIRA_BASE_URL", ""),
            email=os.environ.get("JIRA_EMAIL", ""),
            api_token=os.environ.get("JIRA_API_TOKEN", ""),
            project_key=os.environ.get("JIRA_PROJECT_KEY", ""),
        )

    def is_configured(self) -> bool:
        """Check if Jira is configured."""
        return bool(self.base_url and self.email and self.api_token and self.project_key)


@dataclass
class JiraIssue:
    """A Jira issue."""

    key: str
    id: str
    summary: str
    description: str = ""
    issue_type: str = "Task"
    status: str = ""
    priority: str = "Medium"
    assignee: str | None = None
    reporter: str | None = None
    labels: list[str] = field(default_factory=list)
    components: list[str] = field(default_factory=list)
    created: datetime | None = None
    updated: datetime | None = None
    resolved: datetime | None = None
    url: str = ""
    custom_fields: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "key": self.key,
            "id": self.id,
            "summary": self.summary,
            "description": self.description,
            "issue_type": self.issue_type,
            "status": self.status,
            "priority": self.priority,
            "assignee": self.assignee,
            "labels": self.labels,
            "url": self.url,
        }


@dataclass
class JiraComment:
    """A Jira comment."""

    id: str
    body: str
    author: str
    created: datetime
    updated: datetime | None = None


class JiraClient:
    """Jira API client."""

    def __init__(self, config: JiraConfig | None = None):
        self.config = config or JiraConfig.from_env()
        self._auth = (self.config.email, self.config.api_token)

    def _get_headers(self) -> dict[str, str]:
        """Get request headers."""
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    async def _request(
        self,
        method: str,
        endpoint: str,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Make an API request."""
        if not self.config.is_configured():
            log.warning("Jira not configured")
            return None

        url = f"{self.config.base_url}/rest/api/3/{endpoint}"

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.request(
                    method,
                    url,
                    auth=self._auth,
                    headers=self._get_headers(),
                    json=data,
                    timeout=30,
                )

                if resp.status_code >= 400:
                    log.error("Jira API error: %s %s", resp.status_code, resp.text)
                    return None

                if resp.status_code == 204:
                    return {}

                return resp.json()
        except Exception as e:
            log.error("Jira request failed: %s", e)
            return None

    async def create_issue(
        self,
        summary: str,
        description: str = "",
        issue_type: str | None = None,
        priority: str = "Medium",
        labels: list[str] | None = None,
        components: list[str] | None = None,
        assignee_account_id: str | None = None,
        custom_fields: dict[str, Any] | None = None,
    ) -> JiraIssue | None:
        """Create a new Jira issue."""
        fields: dict[str, Any] = {
            "project": {"key": self.config.project_key},
            "summary": summary,
            "issuetype": {"name": issue_type or self.config.default_issue_type},
            "priority": {"name": priority},
        }

        if description:
            fields["description"] = {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [{"type": "text", "text": description}],
                    }
                ],
            }

        if labels:
            fields["labels"] = labels

        if components:
            fields["components"] = [{"name": c} for c in components]

        if assignee_account_id:
            fields["assignee"] = {"accountId": assignee_account_id}

        if custom_fields:
            for field_id, value in custom_fields.items():
                fields[field_id] = value

        result = await self._request("POST", "issue", {"fields": fields})
        if not result:
            return None

        issue = JiraIssue(
            key=result["key"],
            id=result["id"],
            summary=summary,
            description=description,
            issue_type=issue_type or self.config.default_issue_type,
            priority=priority,
            labels=labels or [],
            url=f"{self.config.base_url}/browse/{result['key']}",
        )

        log.info("Created Jira issue %s", issue.key)
        return issue

    async def get_issue(self, issue_key: str) -> JiraIssue | None:
        """Get a Jira issue by key."""
        result = await self._request("GET", f"issue/{issue_key}")
        if not result:
            return None

        fields = result.get("fields", {})

        return JiraIssue(
            key=result["key"],
            id=result["id"],
            summary=fields.get("summary", ""),
            description=self._extract_description(fields.get("description")),
            issue_type=fields.get("issuetype", {}).get("name", ""),
            status=fields.get("status", {}).get("name", ""),
            priority=fields.get("priority", {}).get("name", ""),
            assignee=fields.get("assignee", {}).get("displayName") if fields.get("assignee") else None,
            reporter=fields.get("reporter", {}).get("displayName") if fields.get("reporter") else None,
            labels=fields.get("labels", []),
            url=f"{self.config.base_url}/browse/{result['key']}",
        )

    def _extract_description(self, desc: Any) -> str:
        """Extract plain text from Atlassian Document Format."""
        if not desc:
            return ""
        if isinstance(desc, str):
            return desc

        text_parts: list[str] = []

        def extract_text(node: dict[str, Any]) -> None:
            if node.get("type") == "text":
                text_parts.append(node.get("text", ""))
            for child in node.get("content", []):
                extract_text(child)

        extract_text(desc)
        return "".join(text_parts)

    async def update_issue(
        self,
        issue_key: str,
        summary: str | None = None,
        description: str | None = None,
        priority: str | None = None,
        labels: list[str] | None = None,
        custom_fields: dict[str, Any] | None = None,
    ) -> bool:
        """Update a Jira issue."""
        fields: dict[str, Any] = {}

        if summary:
            fields["summary"] = summary

        if description:
            fields["description"] = {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [{"type": "text", "text": description}],
                    }
                ],
            }

        if priority:
            fields["priority"] = {"name": priority}

        if labels is not None:
            fields["labels"] = labels

        if custom_fields:
            fields.update(custom_fields)

        if not fields:
            return True

        result = await self._request("PUT", f"issue/{issue_key}", {"fields": fields})
        return result is not None

    async def transition_issue(
        self,
        issue_key: str,
        transition_name: str,
    ) -> bool:
        """Transition an issue to a new status."""
        transitions = await self._request("GET", f"issue/{issue_key}/transitions")
        if not transitions:
            return False

        transition_id = None
        for t in transitions.get("transitions", []):
            if t.get("name", "").lower() == transition_name.lower():
                transition_id = t["id"]
                break

        if not transition_id:
            log.warning("Transition '%s' not found for issue %s", transition_name, issue_key)
            return False

        result = await self._request(
            "POST",
            f"issue/{issue_key}/transitions",
            {"transition": {"id": transition_id}},
        )
        return result is not None

    async def add_comment(
        self,
        issue_key: str,
        body: str,
    ) -> JiraComment | None:
        """Add a comment to an issue."""
        data = {
            "body": {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [{"type": "text", "text": body}],
                    }
                ],
            }
        }

        result = await self._request("POST", f"issue/{issue_key}/comment", data)
        if not result:
            return None

        return JiraComment(
            id=result["id"],
            body=body,
            author=result.get("author", {}).get("displayName", ""),
            created=datetime.now(timezone.utc),
        )

    async def search_issues(
        self,
        jql: str,
        max_results: int = 50,
        fields: list[str] | None = None,
    ) -> list[JiraIssue]:
        """Search for issues using JQL."""
        params = {
            "jql": jql,
            "maxResults": max_results,
            "fields": fields or ["summary", "status", "priority", "assignee", "labels"],
        }

        result = await self._request("POST", "search", params)
        if not result:
            return []

        issues: list[JiraIssue] = []
        for item in result.get("issues", []):
            fields_data = item.get("fields", {})
            issues.append(JiraIssue(
                key=item["key"],
                id=item["id"],
                summary=fields_data.get("summary", ""),
                status=fields_data.get("status", {}).get("name", ""),
                priority=fields_data.get("priority", {}).get("name", ""),
                assignee=fields_data.get("assignee", {}).get("displayName") if fields_data.get("assignee") else None,
                labels=fields_data.get("labels", []),
                url=f"{self.config.base_url}/browse/{item['key']}",
            ))

        return issues

    async def link_issues(
        self,
        inward_issue: str,
        outward_issue: str,
        link_type: str = "Relates",
    ) -> bool:
        """Link two issues."""
        data = {
            "type": {"name": link_type},
            "inwardIssue": {"key": inward_issue},
            "outwardIssue": {"key": outward_issue},
        }

        result = await self._request("POST", "issueLink", data)
        return result is not None


_client: JiraClient | None = None


def get_jira_client() -> JiraClient:
    """Get the Jira client singleton."""
    global _client
    if _client is None:
        _client = JiraClient()
    return _client


async def create_incident_ticket(
    incident_id: str,
    title: str,
    description: str,
    severity: str,
    service: str = "",
    labels: list[str] | None = None,
) -> JiraIssue | None:
    """Create a Jira ticket for an incident."""
    client = get_jira_client()

    priority_map = {
        "critical": "Highest",
        "high": "High",
        "medium": "Medium",
        "low": "Low",
    }

    all_labels = ["incident", f"severity:{severity}"]
    if service:
        all_labels.append(f"service:{service}")
    if labels:
        all_labels.extend(labels)

    full_description = f"""
*Incident ID:* {incident_id}
*Severity:* {severity.upper()}
*Service:* {service or 'N/A'}

----

{description}

----
_Created by DevOps Copilot_
"""

    return await client.create_issue(
        summary=f"[{severity.upper()}] {title}",
        description=full_description,
        issue_type="Incident",
        priority=priority_map.get(severity.lower(), "Medium"),
        labels=all_labels,
    )


async def create_action_item_ticket(
    action_item_id: str,
    title: str,
    description: str,
    priority: str,
    owner: str = "",
    incident_id: str = "",
    labels: list[str] | None = None,
) -> JiraIssue | None:
    """Create a Jira ticket for a postmortem action item."""
    client = get_jira_client()

    priority_map = {
        "p0": "Highest",
        "p1": "High",
        "p2": "Medium",
        "p3": "Low",
    }

    all_labels = ["action-item", f"priority:{priority}"]
    if incident_id:
        all_labels.append(f"incident:{incident_id}")
    if labels:
        all_labels.extend(labels)

    full_description = f"""
*Action Item ID:* {action_item_id}
*Priority:* {priority.upper()}
*Owner:* {owner or 'TBD'}
*Related Incident:* {incident_id or 'N/A'}

----

{description}

----
_Created by DevOps Copilot from postmortem_
"""

    return await client.create_issue(
        summary=f"[{priority.upper()}] {title}",
        description=full_description,
        issue_type="Task",
        priority=priority_map.get(priority.lower(), "Medium"),
        labels=all_labels,
    )


async def sync_incident_status(
    jira_key: str,
    incident_status: str,
) -> bool:
    """Sync incident status to Jira."""
    client = get_jira_client()

    status_map = {
        "investigating": "In Progress",
        "identified": "In Progress",
        "monitoring": "In Progress",
        "resolved": "Done",
        "closed": "Done",
    }

    jira_status = status_map.get(incident_status.lower())
    if jira_status:
        return await client.transition_issue(jira_key, jira_status)
    return False


async def add_investigation_update(
    jira_key: str,
    update_text: str,
    author: str = "DevOps Copilot",
) -> JiraComment | None:
    """Add an investigation update as a Jira comment."""
    client = get_jira_client()

    comment_body = f"""
*Investigation Update*

{update_text}

----
_Posted by {author}_
"""

    return await client.add_comment(jira_key, comment_body)
