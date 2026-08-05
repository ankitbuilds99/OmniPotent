"""Opsgenie integration for alert and on-call management.

Provides:
- Alert creation/management
- On-call schedule sync
- Acknowledge/close/escalate alerts
- Team and user management
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import httpx

log = logging.getLogger("omnipotent.integrations.opsgenie")


class OpsgenieAlertPriority(Enum):
    """Opsgenie alert priorities."""

    P1 = "P1"  # Critical
    P2 = "P2"  # High
    P3 = "P3"  # Moderate
    P4 = "P4"  # Low
    P5 = "P5"  # Informational


class OpsgenieAlertStatus(Enum):
    """Opsgenie alert statuses."""

    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    CLOSED = "closed"


@dataclass
class OpsgenieConfig:
    """Opsgenie integration configuration."""

    api_key: str = ""
    base_url: str = "https://api.opsgenie.com"
    team_id: str = ""
    integration_id: str = ""

    @classmethod
    def from_env(cls) -> "OpsgenieConfig":
        """Load config from environment."""
        return cls(
            api_key=os.environ.get("OPSGENIE_API_KEY", ""),
            base_url=os.environ.get("OPSGENIE_BASE_URL", "https://api.opsgenie.com"),
            team_id=os.environ.get("OPSGENIE_TEAM_ID", ""),
            integration_id=os.environ.get("OPSGENIE_INTEGRATION_ID", ""),
        )

    def is_configured(self) -> bool:
        """Check if Opsgenie is configured."""
        return bool(self.api_key)


@dataclass
class OpsgenieAlert:
    """An Opsgenie alert."""

    id: str
    tiny_id: str
    message: str
    alias: str = ""
    description: str = ""
    status: str = ""
    acknowledged: bool = False
    is_seen: bool = False
    priority: str = "P3"
    source: str = ""
    owner: str = ""
    owner_team_id: str = ""
    responders: list[dict[str, str]] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    entity: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None
    count: int = 1
    url: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "tiny_id": self.tiny_id,
            "message": self.message,
            "alias": self.alias,
            "description": self.description,
            "status": self.status,
            "acknowledged": self.acknowledged,
            "priority": self.priority,
            "source": self.source,
            "owner": self.owner,
            "tags": self.tags,
            "url": self.url,
        }


@dataclass
class OpsgenieOnCallParticipant:
    """An on-call participant."""

    id: str
    name: str
    type: str  # user, team, escalation
    on_call_start: datetime | None = None
    on_call_end: datetime | None = None


@dataclass
class OpsgenieSchedule:
    """An on-call schedule."""

    id: str
    name: str
    description: str = ""
    timezone: str = "UTC"
    enabled: bool = True
    owner_team_id: str = ""
    rotations: list[dict[str, Any]] = field(default_factory=list)


class OpsgenieClient:
    """Opsgenie API client."""

    def __init__(self, config: OpsgenieConfig | None = None):
        self.config = config or OpsgenieConfig.from_env()

    def _get_headers(self) -> dict[str, str]:
        """Get request headers."""
        return {
            "Authorization": f"GenieKey {self.config.api_key}",
            "Content-Type": "application/json",
        }

    async def _request(
        self,
        method: str,
        endpoint: str,
        data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Make an API request."""
        if not self.config.is_configured():
            log.warning("Opsgenie not configured")
            return None

        url = f"{self.config.base_url}{endpoint}"

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.request(
                    method,
                    url,
                    headers=self._get_headers(),
                    json=data,
                    params=params,
                    timeout=30,
                )

                if resp.status_code >= 400:
                    log.error("Opsgenie API error: %s %s", resp.status_code, resp.text)
                    return None

                if resp.status_code == 204:
                    return {}

                return resp.json()
        except Exception as e:
            log.error("Opsgenie request failed: %s", e)
            return None

    # Alert Management
    async def create_alert(
        self,
        message: str,
        description: str = "",
        priority: str = "P3",
        alias: str = "",
        source: str = "OmniPotent",
        tags: list[str] | None = None,
        entity: str = "",
        responders: list[dict[str, str]] | None = None,
        visible_to: list[dict[str, str]] | None = None,
        actions: list[str] | None = None,
        details: dict[str, str] | None = None,
        user: str = "",
        note: str = "",
    ) -> OpsgenieAlert | None:
        """Create a new alert."""
        payload: dict[str, Any] = {
            "message": message,
            "priority": priority,
            "source": source,
        }

        if description:
            payload["description"] = description
        if alias:
            payload["alias"] = alias
        if tags:
            payload["tags"] = tags
        if entity:
            payload["entity"] = entity
        if responders:
            payload["responders"] = responders
        if visible_to:
            payload["visibleTo"] = visible_to
        if actions:
            payload["actions"] = actions
        if details:
            payload["details"] = details
        if user:
            payload["user"] = user
        if note:
            payload["note"] = note

        result = await self._request("POST", "/v2/alerts", payload)
        if not result:
            return None

        request_id = result.get("requestId", "")
        log.info("Created Opsgenie alert request %s", request_id)

        # Get the alert details
        if "data" in result and "alertId" in result["data"]:
            return await self.get_alert(result["data"]["alertId"])

        return OpsgenieAlert(
            id=request_id,
            tiny_id="",
            message=message,
            alias=alias,
            description=description,
            priority=priority,
            source=source,
            tags=tags or [],
        )

    async def get_alert(self, alert_id: str) -> OpsgenieAlert | None:
        """Get an alert by ID or alias."""
        result = await self._request("GET", f"/v2/alerts/{alert_id}")
        if not result or "data" not in result:
            return None

        return self._parse_alert(result["data"])

    def _parse_alert(self, data: dict[str, Any]) -> OpsgenieAlert:
        """Parse alert data from API response."""
        return OpsgenieAlert(
            id=data.get("id", ""),
            tiny_id=data.get("tinyId", ""),
            message=data.get("message", ""),
            alias=data.get("alias", ""),
            description=data.get("description", ""),
            status=data.get("status", ""),
            acknowledged=data.get("acknowledged", False),
            is_seen=data.get("isSeen", False),
            priority=data.get("priority", "P3"),
            source=data.get("source", ""),
            owner=data.get("owner", ""),
            owner_team_id=data.get("ownerTeamId", ""),
            responders=data.get("responders", []),
            tags=data.get("tags", []),
            actions=data.get("actions", []),
            entity=data.get("entity", ""),
            created_at=self._parse_datetime(data.get("createdAt")),
            updated_at=self._parse_datetime(data.get("updatedAt")),
            count=data.get("count", 1),
        )

    def _parse_datetime(self, value: str | None) -> datetime | None:
        """Parse datetime from ISO string."""
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return None

    async def list_alerts(
        self,
        query: str = "",
        limit: int = 100,
        offset: int = 0,
        sort: str = "createdAt",
        order: str = "desc",
    ) -> list[OpsgenieAlert]:
        """List alerts with optional query filter."""
        params: dict[str, Any] = {
            "limit": min(limit, 100),
            "offset": offset,
            "sort": sort,
            "order": order,
        }

        if query:
            params["query"] = query

        result = await self._request("GET", "/v2/alerts", params=params)
        if not result or "data" not in result:
            return []

        return [self._parse_alert(alert) for alert in result["data"]]

    async def acknowledge_alert(
        self,
        alert_id: str,
        user: str = "OmniPotent",
        source: str = "OmniPotent",
        note: str = "",
    ) -> bool:
        """Acknowledge an alert."""
        payload: dict[str, Any] = {
            "user": user,
            "source": source,
        }
        if note:
            payload["note"] = note

        result = await self._request("POST", f"/v2/alerts/{alert_id}/acknowledge", payload)
        if result is not None:
            log.info("Acknowledged Opsgenie alert %s", alert_id)
            return True
        return False

    async def close_alert(
        self,
        alert_id: str,
        user: str = "OmniPotent",
        source: str = "OmniPotent",
        note: str = "",
    ) -> bool:
        """Close an alert."""
        payload: dict[str, Any] = {
            "user": user,
            "source": source,
        }
        if note:
            payload["note"] = note

        result = await self._request("POST", f"/v2/alerts/{alert_id}/close", payload)
        if result is not None:
            log.info("Closed Opsgenie alert %s", alert_id)
            return True
        return False

    async def add_note(
        self,
        alert_id: str,
        note: str,
        user: str = "OmniPotent",
        source: str = "OmniPotent",
    ) -> bool:
        """Add a note to an alert."""
        payload = {
            "note": note,
            "user": user,
            "source": source,
        }

        result = await self._request("POST", f"/v2/alerts/{alert_id}/notes", payload)
        if result is not None:
            log.info("Added note to Opsgenie alert %s", alert_id)
            return True
        return False

    async def escalate_alert(
        self,
        alert_id: str,
        escalation_id: str,
        user: str = "OmniPotent",
        source: str = "OmniPotent",
        note: str = "",
    ) -> bool:
        """Escalate an alert to an escalation policy."""
        payload: dict[str, Any] = {
            "escalation": {"id": escalation_id},
            "user": user,
            "source": source,
        }
        if note:
            payload["note"] = note

        result = await self._request("POST", f"/v2/alerts/{alert_id}/escalate", payload)
        if result is not None:
            log.info("Escalated Opsgenie alert %s", alert_id)
            return True
        return False

    async def assign_alert(
        self,
        alert_id: str,
        owner_id: str,
        owner_type: str = "user",  # user or team
        user: str = "OmniPotent",
        source: str = "OmniPotent",
        note: str = "",
    ) -> bool:
        """Assign an alert to a user or team."""
        payload: dict[str, Any] = {
            "owner": {"id": owner_id, "type": owner_type},
            "user": user,
            "source": source,
        }
        if note:
            payload["note"] = note

        result = await self._request("POST", f"/v2/alerts/{alert_id}/assign", payload)
        if result is not None:
            log.info("Assigned Opsgenie alert %s to %s", alert_id, owner_id)
            return True
        return False

    async def add_tags(
        self,
        alert_id: str,
        tags: list[str],
        user: str = "OmniPotent",
        source: str = "OmniPotent",
        note: str = "",
    ) -> bool:
        """Add tags to an alert."""
        payload: dict[str, Any] = {
            "tags": tags,
            "user": user,
            "source": source,
        }
        if note:
            payload["note"] = note

        result = await self._request("POST", f"/v2/alerts/{alert_id}/tags", payload)
        return result is not None

    # On-Call Schedule Management
    async def get_on_call(
        self,
        schedule_id: str | None = None,
        flat: bool = True,
    ) -> list[OpsgenieOnCallParticipant]:
        """Get current on-call participants."""
        if schedule_id:
            endpoint = f"/v2/schedules/{schedule_id}/on-calls"
        else:
            endpoint = "/v2/schedules/on-calls"

        params = {"flat": str(flat).lower()}
        result = await self._request("GET", endpoint, params=params)
        if not result or "data" not in result:
            return []

        participants = []
        for item in result["data"].get("onCallParticipants", []):
            participants.append(
                OpsgenieOnCallParticipant(
                    id=item.get("id", ""),
                    name=item.get("name", ""),
                    type=item.get("type", "user"),
                    on_call_start=self._parse_datetime(item.get("onCallStart")),
                    on_call_end=self._parse_datetime(item.get("onCallEnd")),
                )
            )

        return participants

    async def list_schedules(self) -> list[OpsgenieSchedule]:
        """List all schedules."""
        result = await self._request("GET", "/v2/schedules")
        if not result or "data" not in result:
            return []

        schedules = []
        for item in result["data"]:
            schedules.append(
                OpsgenieSchedule(
                    id=item.get("id", ""),
                    name=item.get("name", ""),
                    description=item.get("description", ""),
                    timezone=item.get("timezone", "UTC"),
                    enabled=item.get("enabled", True),
                    owner_team_id=item.get("ownerTeam", {}).get("id", ""),
                )
            )

        return schedules

    async def get_schedule(self, schedule_id: str) -> OpsgenieSchedule | None:
        """Get a schedule by ID."""
        result = await self._request("GET", f"/v2/schedules/{schedule_id}")
        if not result or "data" not in result:
            return None

        item = result["data"]
        return OpsgenieSchedule(
            id=item.get("id", ""),
            name=item.get("name", ""),
            description=item.get("description", ""),
            timezone=item.get("timezone", "UTC"),
            enabled=item.get("enabled", True),
            owner_team_id=item.get("ownerTeam", {}).get("id", ""),
            rotations=item.get("rotations", []),
        )

    # Team Management
    async def list_teams(self) -> list[dict[str, Any]]:
        """List all teams."""
        result = await self._request("GET", "/v2/teams")
        if not result or "data" not in result:
            return []

        return [
            {
                "id": team.get("id", ""),
                "name": team.get("name", ""),
                "description": team.get("description", ""),
            }
            for team in result["data"]
        ]

    async def get_team_on_call(self, team_id: str) -> list[OpsgenieOnCallParticipant]:
        """Get on-call participants for a specific team."""
        result = await self._request("GET", f"/v2/teams/{team_id}/on-calls")
        if not result or "data" not in result:
            return []

        participants = []
        for item in result["data"].get("onCallParticipants", []):
            participants.append(
                OpsgenieOnCallParticipant(
                    id=item.get("id", ""),
                    name=item.get("name", ""),
                    type=item.get("type", "user"),
                )
            )

        return participants


_client: OpsgenieClient | None = None


def get_opsgenie_client() -> OpsgenieClient:
    """Get the Opsgenie client singleton."""
    global _client
    if _client is None:
        _client = OpsgenieClient()
    return _client


async def create_incident_alert(
    incident_id: str,
    title: str,
    description: str,
    severity: str,
    service: str = "",
    tags: list[str] | None = None,
    responders: list[dict[str, str]] | None = None,
) -> OpsgenieAlert | None:
    """Create an Opsgenie alert for an incident."""
    client = get_opsgenie_client()

    priority_map = {
        "critical": OpsgenieAlertPriority.P1.value,
        "high": OpsgenieAlertPriority.P2.value,
        "medium": OpsgenieAlertPriority.P3.value,
        "low": OpsgenieAlertPriority.P4.value,
    }

    all_tags = ["incident", f"severity:{severity}"]
    if service:
        all_tags.append(f"service:{service}")
    if tags:
        all_tags.extend(tags)

    full_description = f"""
Incident ID: {incident_id}
Severity: {severity.upper()}
Service: {service or 'N/A'}

---

{description}

---
Created by OmniPotent
"""

    return await client.create_alert(
        message=f"[{severity.upper()}] {title}",
        description=full_description,
        priority=priority_map.get(severity.lower(), OpsgenieAlertPriority.P3.value),
        alias=f"incident-{incident_id}",
        tags=all_tags,
        entity=service or "",
        responders=responders,
        details={"incident_id": incident_id, "severity": severity, "service": service},
    )


async def sync_incident_status(
    alert_id: str,
    incident_status: str,
    note: str = "",
) -> bool:
    """Sync incident status to Opsgenie alert."""
    client = get_opsgenie_client()

    if incident_status.lower() in ("resolved", "closed"):
        return await client.close_alert(
            alert_id,
            note=note or f"Incident {incident_status}",
        )
    elif incident_status.lower() in ("investigating", "identified", "monitoring"):
        return await client.acknowledge_alert(
            alert_id,
            note=note or f"Incident status: {incident_status}",
        )
    return False


async def add_investigation_update(
    alert_id: str,
    update_text: str,
    author: str = "OmniPotent",
) -> bool:
    """Add an investigation update as an Opsgenie alert note."""
    client = get_opsgenie_client()

    note = f"""
Investigation Update

{update_text}

---
Posted by {author}
"""

    return await client.add_note(alert_id, note)


async def get_current_on_call(
    team_id: str | None = None,
    schedule_id: str | None = None,
) -> list[OpsgenieOnCallParticipant]:
    """Get current on-call participants."""
    client = get_opsgenie_client()

    if team_id:
        return await client.get_team_on_call(team_id)
    return await client.get_on_call(schedule_id)
