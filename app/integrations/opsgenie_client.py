"""OpsGenie API Client.

Full-featured async client for Atlassian OpsGenie REST API.
Supports alerts, incidents, schedules, and on-call.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import httpx

log = logging.getLogger("omnipotent.integrations.opsgenie")

OPSGENIE_API_BASE = "https://api.opsgenie.com/v2"


class AlertStatus(str, Enum):
    OPEN = "open"
    CLOSED = "closed"


class AlertPriority(str, Enum):
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"
    P4 = "P4"
    P5 = "P5"


class OpsGenieError(Exception):
    """OpsGenie API error."""

    def __init__(self, message: str, code: str | None = None, request_id: str | None = None):
        self.message = message
        self.code = code
        self.request_id = request_id
        super().__init__(message)


@dataclass
class OpsGenieConfig:
    """Configuration for OpsGenie client."""

    api_key: str
    timeout: float = 30.0
    max_retries: int = 3


@dataclass
class Alert:
    """OpsGenie alert."""

    id: str
    tiny_id: str
    message: str
    status: AlertStatus
    priority: AlertPriority
    source: str
    created_at: datetime
    acknowledged: bool = False
    seen: bool = False
    is_seen: bool = False
    alias: str | None = None
    description: str | None = None
    teams: list[str] = field(default_factory=list)
    responders: list[dict[str, str]] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    updated_at: datetime | None = None
    count: int = 1

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "Alert":
        """Create from API response."""
        return cls(
            id=data["id"],
            tiny_id=data.get("tinyId", ""),
            message=data.get("message", ""),
            status=AlertStatus(data.get("status", "open")),
            priority=AlertPriority(data.get("priority", "P3")),
            source=data.get("source", ""),
            created_at=datetime.fromisoformat(data["createdAt"].replace("Z", "+00:00")),
            acknowledged=data.get("acknowledged", False),
            seen=data.get("seen", False),
            is_seen=data.get("isSeen", False),
            alias=data.get("alias"),
            description=data.get("description"),
            teams=[t.get("name", "") for t in data.get("teams", [])],
            responders=data.get("responders", []),
            tags=data.get("tags", []),
            updated_at=(
                datetime.fromisoformat(data["updatedAt"].replace("Z", "+00:00"))
                if data.get("updatedAt")
                else None
            ),
            count=data.get("count", 1),
        )


@dataclass
class Schedule:
    """OpsGenie schedule."""

    id: str
    name: str
    description: str | None = None
    timezone: str = "UTC"
    enabled: bool = True
    owner_team_id: str | None = None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "Schedule":
        return cls(
            id=data["id"],
            name=data.get("name", ""),
            description=data.get("description"),
            timezone=data.get("timezone", "UTC"),
            enabled=data.get("enabled", True),
            owner_team_id=data.get("ownerTeam", {}).get("id"),
        )


@dataclass
class OnCallParticipant:
    """On-call participant."""

    id: str
    name: str
    type: str
    on_call_start: datetime
    on_call_end: datetime

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "OnCallParticipant":
        return cls(
            id=data.get("id", ""),
            name=data.get("name", ""),
            type=data.get("type", "user"),
            on_call_start=datetime.fromisoformat(
                data.get("onCallParticipants", [{}])[0].get("onCallRecipientType", {}).get("onCallStart", "")
                .replace("Z", "+00:00")
            ) if data.get("onCallParticipants") else datetime.now(timezone.utc),
            on_call_end=datetime.fromisoformat(
                data.get("onCallParticipants", [{}])[0].get("onCallRecipientType", {}).get("onCallEnd", "")
                .replace("Z", "+00:00")
            ) if data.get("onCallParticipants") else datetime.now(timezone.utc),
        )


class OpsGenieClient:
    """Async OpsGenie API client."""

    def __init__(self, config: OpsGenieConfig) -> None:
        self.config = config
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "OpsGenieClient":
        await self.connect()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def connect(self) -> None:
        """Initialize the HTTP client."""
        self._client = httpx.AsyncClient(
            base_url=OPSGENIE_API_BASE,
            timeout=self.config.timeout,
            headers={
                "Authorization": f"GenieKey {self.config.api_key}",
                "Content-Type": "application/json",
            },
        )

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _request(
        self,
        method: str,
        path: str,
        data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make an API request."""
        if not self._client:
            raise RuntimeError("Client not connected")

        for attempt in range(self.config.max_retries):
            try:
                if method == "GET":
                    response = await self._client.get(path, params=params)
                elif method == "POST":
                    response = await self._client.post(path, json=data)
                elif method == "PUT":
                    response = await self._client.put(path, json=data)
                elif method == "PATCH":
                    response = await self._client.patch(path, json=data)
                elif method == "DELETE":
                    response = await self._client.delete(path)
                else:
                    raise ValueError(f"Unsupported method: {method}")

                if response.status_code == 429:
                    retry_after = int(response.headers.get("X-RateLimit-Reset-After", 60))
                    log.warning("Rate limited, waiting %ds", retry_after)
                    import asyncio
                    await asyncio.sleep(retry_after)
                    continue

                result = response.json() if response.content else {}

                if response.status_code >= 400:
                    raise OpsGenieError(
                        message=result.get("message", f"HTTP {response.status_code}"),
                        code=result.get("code"),
                        request_id=result.get("requestId"),
                    )

                return result

            except httpx.HTTPError as e:
                if attempt == self.config.max_retries - 1:
                    raise OpsGenieError(f"HTTP error: {e}")
                import asyncio
                await asyncio.sleep(1)

        raise OpsGenieError("Max retries exceeded")

    async def create_alert(
        self,
        message: str,
        priority: AlertPriority = AlertPriority.P3,
        alias: str | None = None,
        description: str | None = None,
        responders: list[dict[str, str]] | None = None,
        teams: list[str] | None = None,
        tags: list[str] | None = None,
        details: dict[str, str] | None = None,
        source: str = "omnipotent",
        note: str | None = None,
    ) -> dict[str, Any]:
        """Create a new alert."""
        data: dict[str, Any] = {
            "message": message,
            "priority": priority.value,
            "source": source,
        }

        if alias:
            data["alias"] = alias
        if description:
            data["description"] = description
        if responders:
            data["responders"] = responders
        if teams:
            data["teams"] = [{"name": t} for t in teams]
        if tags:
            data["tags"] = tags
        if details:
            data["details"] = details
        if note:
            data["note"] = note

        result = await self._request("POST", "/alerts", data=data)
        log.info("Alert created: request_id=%s", result.get("requestId"))
        return result

    async def get_alert(self, identifier: str, identifier_type: str = "id") -> Alert:
        """Get alert details."""
        params = {"identifierType": identifier_type}
        result = await self._request("GET", f"/alerts/{identifier}", params=params)
        return Alert.from_api(result.get("data", {}))

    async def list_alerts(
        self,
        query: str | None = None,
        status: AlertStatus | None = None,
        priority: AlertPriority | None = None,
        limit: int = 20,
        offset: int = 0,
        sort: str = "createdAt",
        order: str = "desc",
    ) -> list[Alert]:
        """List alerts with filters."""
        params: dict[str, Any] = {
            "limit": limit,
            "offset": offset,
            "sort": sort,
            "order": order,
        }

        query_parts = []
        if status:
            query_parts.append(f"status:{status.value}")
        if priority:
            query_parts.append(f"priority:{priority.value}")
        if query:
            query_parts.append(query)

        if query_parts:
            params["query"] = " AND ".join(query_parts)

        result = await self._request("GET", "/alerts", params=params)
        return [Alert.from_api(a) for a in result.get("data", [])]

    async def acknowledge_alert(
        self,
        identifier: str,
        identifier_type: str = "id",
        user: str | None = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        """Acknowledge an alert."""
        data: dict[str, Any] = {}

        if user:
            data["user"] = user
        if note:
            data["note"] = note

        params = {"identifierType": identifier_type}
        return await self._request(
            "POST",
            f"/alerts/{identifier}/acknowledge",
            data=data,
            params=params,
        )

    async def close_alert(
        self,
        identifier: str,
        identifier_type: str = "id",
        user: str | None = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        """Close an alert."""
        data: dict[str, Any] = {}

        if user:
            data["user"] = user
        if note:
            data["note"] = note

        params = {"identifierType": identifier_type}
        return await self._request(
            "POST",
            f"/alerts/{identifier}/close",
            data=data,
            params=params,
        )

    async def add_note_to_alert(
        self,
        identifier: str,
        note: str,
        identifier_type: str = "id",
        user: str | None = None,
    ) -> dict[str, Any]:
        """Add a note to an alert."""
        data: dict[str, Any] = {"note": note}

        if user:
            data["user"] = user

        params = {"identifierType": identifier_type}
        return await self._request(
            "POST",
            f"/alerts/{identifier}/notes",
            data=data,
            params=params,
        )

    async def escalate_alert(
        self,
        identifier: str,
        escalation_id: str,
        identifier_type: str = "id",
        user: str | None = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        """Escalate an alert."""
        data: dict[str, Any] = {
            "escalation": {"id": escalation_id},
        }

        if user:
            data["user"] = user
        if note:
            data["note"] = note

        params = {"identifierType": identifier_type}
        return await self._request(
            "POST",
            f"/alerts/{identifier}/escalate",
            data=data,
            params=params,
        )

    async def assign_alert(
        self,
        identifier: str,
        responder: dict[str, str],
        identifier_type: str = "id",
        user: str | None = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        """Assign an alert to a responder."""
        data: dict[str, Any] = {"owner": responder}

        if user:
            data["user"] = user
        if note:
            data["note"] = note

        params = {"identifierType": identifier_type}
        return await self._request(
            "POST",
            f"/alerts/{identifier}/assign",
            data=data,
            params=params,
        )

    async def get_alert_logs(
        self,
        identifier: str,
        identifier_type: str = "id",
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Get alert activity logs."""
        params = {
            "identifierType": identifier_type,
            "limit": limit,
            "offset": offset,
        }

        result = await self._request("GET", f"/alerts/{identifier}/logs", params=params)
        return result.get("data", [])

    async def list_schedules(self) -> list[Schedule]:
        """List all schedules."""
        result = await self._request("GET", "/schedules")
        return [Schedule.from_api(s) for s in result.get("data", [])]

    async def get_schedule(self, identifier: str, identifier_type: str = "id") -> Schedule:
        """Get schedule details."""
        params = {"identifierType": identifier_type}
        result = await self._request("GET", f"/schedules/{identifier}", params=params)
        return Schedule.from_api(result.get("data", {}))

    async def get_on_call(
        self,
        schedule_identifier: str,
        identifier_type: str = "id",
        flat: bool = True,
    ) -> list[dict[str, Any]]:
        """Get on-call participants for a schedule."""
        params = {
            "scheduleIdentifierType": identifier_type,
            "flat": str(flat).lower(),
        }

        result = await self._request(
            "GET",
            f"/schedules/{schedule_identifier}/on-calls",
            params=params,
        )

        return result.get("data", {}).get("onCallParticipants", [])

    async def get_next_on_call(
        self,
        schedule_identifier: str,
        identifier_type: str = "id",
    ) -> list[dict[str, Any]]:
        """Get next on-call participants."""
        params = {"scheduleIdentifierType": identifier_type}

        result = await self._request(
            "GET",
            f"/schedules/{schedule_identifier}/next-on-calls",
            params=params,
        )

        return result.get("data", {}).get("exactNextOnCallRecipients", [])

    async def create_override(
        self,
        schedule_identifier: str,
        user: dict[str, str],
        start_date: datetime,
        end_date: datetime,
        identifier_type: str = "id",
        rotations: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        """Create a schedule override."""
        data: dict[str, Any] = {
            "user": user,
            "startDate": start_date.isoformat(),
            "endDate": end_date.isoformat(),
        }

        if rotations:
            data["rotations"] = rotations

        params = {"scheduleIdentifierType": identifier_type}

        return await self._request(
            "POST",
            f"/schedules/{schedule_identifier}/overrides",
            data=data,
            params=params,
        )

    async def list_teams(self) -> list[dict[str, Any]]:
        """List all teams."""
        result = await self._request("GET", "/teams")
        return result.get("data", [])

    async def get_team(self, identifier: str, identifier_type: str = "id") -> dict[str, Any]:
        """Get team details."""
        params = {"identifierType": identifier_type}
        result = await self._request("GET", f"/teams/{identifier}", params=params)
        return result.get("data", {})

    async def list_users(self, query: str | None = None) -> list[dict[str, Any]]:
        """List users."""
        params: dict[str, Any] = {}
        if query:
            params["query"] = query

        result = await self._request("GET", "/users", params=params)
        return result.get("data", [])

    async def get_user(self, identifier: str, identifier_type: str = "id") -> dict[str, Any]:
        """Get user details."""
        params = {"identifierType": identifier_type}
        result = await self._request("GET", f"/users/{identifier}", params=params)
        return result.get("data", {})


_client: OpsGenieClient | None = None


async def get_opsgenie_client() -> OpsGenieClient:
    """Get the default OpsGenie client."""
    global _client

    if _client is None:
        import os

        config = OpsGenieConfig(
            api_key=os.getenv("OPSGENIE_API_KEY", ""),
        )

        _client = OpsGenieClient(config)
        await _client.connect()

    return _client


async def close_opsgenie_client() -> None:
    """Close the default OpsGenie client."""
    global _client

    if _client:
        await _client.close()
        _client = None
