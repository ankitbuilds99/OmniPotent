"""PagerDuty API Client.

Full-featured async client for PagerDuty REST API v2.
Supports incidents, services, schedules, users, and events.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import httpx

log = logging.getLogger("omnipotent.integrations.pagerduty")

PAGERDUTY_API_BASE = "https://api.pagerduty.com"
PAGERDUTY_EVENTS_BASE = "https://events.pagerduty.com/v2"


class IncidentStatus(str, Enum):
    TRIGGERED = "triggered"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


class IncidentUrgency(str, Enum):
    HIGH = "high"
    LOW = "low"


class PagerDutyError(Exception):
    """PagerDuty API error."""

    def __init__(self, message: str, code: int | None = None, errors: list[str] | None = None):
        self.message = message
        self.code = code
        self.errors = errors or []
        super().__init__(message)


@dataclass
class PagerDutyConfig:
    """Configuration for PagerDuty client."""

    api_key: str
    integration_key: str | None = None
    webhook_secret: str | None = None
    timeout: float = 30.0
    max_retries: int = 3


@dataclass
class Incident:
    """PagerDuty incident."""

    id: str
    incident_number: int
    title: str
    status: IncidentStatus
    urgency: IncidentUrgency
    service_id: str
    service_name: str
    created_at: datetime
    html_url: str
    description: str | None = None
    escalation_policy_id: str | None = None
    assigned_to: list[str] = field(default_factory=list)
    teams: list[str] = field(default_factory=list)
    priority: str | None = None
    resolved_at: datetime | None = None
    last_status_change_at: datetime | None = None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "Incident":
        """Create from API response."""
        service = data.get("service", {})
        assignments = data.get("assignments", [])

        return cls(
            id=data["id"],
            incident_number=data.get("incident_number", 0),
            title=data.get("title", ""),
            status=IncidentStatus(data.get("status", "triggered")),
            urgency=IncidentUrgency(data.get("urgency", "high")),
            service_id=service.get("id", ""),
            service_name=service.get("summary", ""),
            created_at=datetime.fromisoformat(data["created_at"].replace("Z", "+00:00")),
            html_url=data.get("html_url", ""),
            description=data.get("description"),
            escalation_policy_id=data.get("escalation_policy", {}).get("id"),
            assigned_to=[a.get("assignee", {}).get("summary", "") for a in assignments],
            teams=[t.get("summary", "") for t in data.get("teams", [])],
            priority=data.get("priority", {}).get("summary") if data.get("priority") else None,
            resolved_at=(
                datetime.fromisoformat(data["resolved_at"].replace("Z", "+00:00"))
                if data.get("resolved_at")
                else None
            ),
            last_status_change_at=(
                datetime.fromisoformat(data["last_status_change_at"].replace("Z", "+00:00"))
                if data.get("last_status_change_at")
                else None
            ),
        )


@dataclass
class Service:
    """PagerDuty service."""

    id: str
    name: str
    status: str
    escalation_policy_id: str
    description: str | None = None
    html_url: str | None = None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "Service":
        return cls(
            id=data["id"],
            name=data.get("name", ""),
            status=data.get("status", "active"),
            escalation_policy_id=data.get("escalation_policy", {}).get("id", ""),
            description=data.get("description"),
            html_url=data.get("html_url"),
        )


@dataclass
class User:
    """PagerDuty user."""

    id: str
    name: str
    email: str
    role: str
    html_url: str | None = None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "User":
        return cls(
            id=data["id"],
            name=data.get("name", ""),
            email=data.get("email", ""),
            role=data.get("role", ""),
            html_url=data.get("html_url"),
        )


@dataclass
class OnCall:
    """On-call entry."""

    user_id: str
    user_name: str
    user_email: str
    schedule_id: str
    schedule_name: str
    escalation_level: int
    start: datetime
    end: datetime

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "OnCall":
        user = data.get("user", {})
        schedule = data.get("schedule", {})

        return cls(
            user_id=user.get("id", ""),
            user_name=user.get("summary", ""),
            user_email=user.get("email", ""),
            schedule_id=schedule.get("id", ""),
            schedule_name=schedule.get("summary", ""),
            escalation_level=data.get("escalation_level", 1),
            start=datetime.fromisoformat(data["start"].replace("Z", "+00:00")),
            end=datetime.fromisoformat(data["end"].replace("Z", "+00:00")),
        )


class PagerDutyClient:
    """Async PagerDuty API client."""

    def __init__(self, config: PagerDutyConfig) -> None:
        self.config = config
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "PagerDutyClient":
        await self.connect()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def connect(self) -> None:
        """Initialize the HTTP client."""
        self._client = httpx.AsyncClient(
            base_url=PAGERDUTY_API_BASE,
            timeout=self.config.timeout,
            headers={
                "Authorization": f"Token token={self.config.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/vnd.pagerduty+json;version=2",
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
                elif method == "DELETE":
                    response = await self._client.delete(path)
                else:
                    raise ValueError(f"Unsupported method: {method}")

                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", 60))
                    log.warning("Rate limited, waiting %ds", retry_after)
                    import asyncio
                    await asyncio.sleep(retry_after)
                    continue

                result = response.json() if response.content else {}

                if response.status_code >= 400:
                    error = result.get("error", {})
                    raise PagerDutyError(
                        message=error.get("message", f"HTTP {response.status_code}"),
                        code=error.get("code"),
                        errors=error.get("errors", []),
                    )

                return result

            except httpx.HTTPError as e:
                if attempt == self.config.max_retries - 1:
                    raise PagerDutyError(f"HTTP error: {e}")
                import asyncio
                await asyncio.sleep(1)

        raise PagerDutyError("Max retries exceeded")

    def verify_webhook(self, raw_body: bytes, signature_header: str) -> bool:
        """Verify webhook signature."""
        if not self.config.webhook_secret:
            return False

        digest = hmac.new(
            self.config.webhook_secret.encode(),
            raw_body,
            hashlib.sha256,
        ).hexdigest()
        expected = f"v1={digest}"

        return any(
            hmac.compare_digest(expected, part.strip())
            for part in signature_header.split(",")
        )

    async def list_incidents(
        self,
        statuses: list[IncidentStatus] | None = None,
        service_ids: list[str] | None = None,
        team_ids: list[str] | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        urgencies: list[IncidentUrgency] | None = None,
        limit: int = 25,
        offset: int = 0,
    ) -> list[Incident]:
        """List incidents with filters."""
        params: dict[str, Any] = {
            "limit": limit,
            "offset": offset,
            "sort_by": "created_at:desc",
        }

        if statuses:
            params["statuses[]"] = [s.value for s in statuses]
        if service_ids:
            params["service_ids[]"] = service_ids
        if team_ids:
            params["team_ids[]"] = team_ids
        if since:
            params["since"] = since.isoformat()
        if until:
            params["until"] = until.isoformat()
        if urgencies:
            params["urgencies[]"] = [u.value for u in urgencies]

        result = await self._request("GET", "/incidents", params=params)
        return [Incident.from_api(i) for i in result.get("incidents", [])]

    async def get_incident(self, incident_id: str) -> Incident:
        """Get a specific incident."""
        result = await self._request("GET", f"/incidents/{incident_id}")
        return Incident.from_api(result["incident"])

    async def create_incident(
        self,
        title: str,
        service_id: str,
        urgency: IncidentUrgency = IncidentUrgency.HIGH,
        body: str | None = None,
        escalation_policy_id: str | None = None,
        priority_id: str | None = None,
        incident_key: str | None = None,
    ) -> Incident:
        """Create a new incident."""
        data: dict[str, Any] = {
            "incident": {
                "type": "incident",
                "title": title,
                "service": {"id": service_id, "type": "service_reference"},
                "urgency": urgency.value,
            }
        }

        if body:
            data["incident"]["body"] = {"type": "incident_body", "details": body}
        if escalation_policy_id:
            data["incident"]["escalation_policy"] = {
                "id": escalation_policy_id,
                "type": "escalation_policy_reference",
            }
        if priority_id:
            data["incident"]["priority"] = {"id": priority_id, "type": "priority_reference"}
        if incident_key:
            data["incident"]["incident_key"] = incident_key

        result = await self._request("POST", "/incidents", data=data)
        return Incident.from_api(result["incident"])

    async def acknowledge_incident(self, incident_id: str, user_email: str) -> Incident:
        """Acknowledge an incident."""
        data = {
            "incident": {
                "type": "incident_reference",
                "status": "acknowledged",
            }
        }

        headers = {"From": user_email}

        if self._client:
            self._client.headers["From"] = user_email

        try:
            result = await self._request("PUT", f"/incidents/{incident_id}", data=data)
            return Incident.from_api(result["incident"])
        finally:
            if self._client:
                self._client.headers.pop("From", None)

    async def resolve_incident(self, incident_id: str, user_email: str, resolution: str | None = None) -> Incident:
        """Resolve an incident."""
        data: dict[str, Any] = {
            "incident": {
                "type": "incident_reference",
                "status": "resolved",
            }
        }

        if resolution:
            data["incident"]["resolution"] = resolution

        if self._client:
            self._client.headers["From"] = user_email

        try:
            result = await self._request("PUT", f"/incidents/{incident_id}", data=data)
            return Incident.from_api(result["incident"])
        finally:
            if self._client:
                self._client.headers.pop("From", None)

    async def add_incident_note(
        self,
        incident_id: str,
        content: str,
        user_email: str,
    ) -> dict[str, Any]:
        """Add a note to an incident."""
        data = {
            "note": {
                "content": content,
            }
        }

        if self._client:
            self._client.headers["From"] = user_email

        try:
            result = await self._request("POST", f"/incidents/{incident_id}/notes", data=data)
            return result.get("note", {})
        finally:
            if self._client:
                self._client.headers.pop("From", None)

    async def reassign_incident(
        self,
        incident_id: str,
        user_ids: list[str],
        user_email: str,
    ) -> Incident:
        """Reassign an incident to different users."""
        data = {
            "incident": {
                "type": "incident_reference",
                "assignments": [
                    {"assignee": {"id": uid, "type": "user_reference"}}
                    for uid in user_ids
                ],
            }
        }

        if self._client:
            self._client.headers["From"] = user_email

        try:
            result = await self._request("PUT", f"/incidents/{incident_id}", data=data)
            return Incident.from_api(result["incident"])
        finally:
            if self._client:
                self._client.headers.pop("From", None)

    async def get_incident_alerts(self, incident_id: str) -> list[dict[str, Any]]:
        """Get alerts for an incident."""
        result = await self._request("GET", f"/incidents/{incident_id}/alerts")
        return result.get("alerts", [])

    async def get_incident_log_entries(
        self,
        incident_id: str,
        is_overview: bool = True,
    ) -> list[dict[str, Any]]:
        """Get log entries for an incident."""
        params = {"is_overview": str(is_overview).lower()}
        result = await self._request("GET", f"/incidents/{incident_id}/log_entries", params=params)
        return result.get("log_entries", [])

    async def list_services(
        self,
        team_ids: list[str] | None = None,
        query: str | None = None,
        limit: int = 25,
        offset: int = 0,
    ) -> list[Service]:
        """List services."""
        params: dict[str, Any] = {"limit": limit, "offset": offset}

        if team_ids:
            params["team_ids[]"] = team_ids
        if query:
            params["query"] = query

        result = await self._request("GET", "/services", params=params)
        return [Service.from_api(s) for s in result.get("services", [])]

    async def get_service(self, service_id: str) -> Service:
        """Get a specific service."""
        result = await self._request("GET", f"/services/{service_id}")
        return Service.from_api(result["service"])

    async def list_users(
        self,
        query: str | None = None,
        team_ids: list[str] | None = None,
        limit: int = 25,
        offset: int = 0,
    ) -> list[User]:
        """List users."""
        params: dict[str, Any] = {"limit": limit, "offset": offset}

        if query:
            params["query"] = query
        if team_ids:
            params["team_ids[]"] = team_ids

        result = await self._request("GET", "/users", params=params)
        return [User.from_api(u) for u in result.get("users", [])]

    async def get_user(self, user_id: str) -> User:
        """Get a specific user."""
        result = await self._request("GET", f"/users/{user_id}")
        return User.from_api(result["user"])

    async def get_current_oncalls(
        self,
        schedule_ids: list[str] | None = None,
        escalation_policy_ids: list[str] | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[OnCall]:
        """Get current on-call entries."""
        params: dict[str, Any] = {}

        if schedule_ids:
            params["schedule_ids[]"] = schedule_ids
        if escalation_policy_ids:
            params["escalation_policy_ids[]"] = escalation_policy_ids
        if since:
            params["since"] = since.isoformat()
        if until:
            params["until"] = until.isoformat()

        result = await self._request("GET", "/oncalls", params=params)
        return [OnCall.from_api(o) for o in result.get("oncalls", [])]

    async def get_oncall_for_service(self, service_id: str) -> list[OnCall]:
        """Get on-call for a specific service."""
        service = await self.get_service(service_id)
        if not service.escalation_policy_id:
            return []

        return await self.get_current_oncalls(
            escalation_policy_ids=[service.escalation_policy_id]
        )

    async def send_event(
        self,
        routing_key: str,
        event_action: str,
        dedup_key: str | None = None,
        summary: str | None = None,
        source: str | None = None,
        severity: str = "error",
        custom_details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send an event to PagerDuty Events API v2."""
        async with httpx.AsyncClient(timeout=self.config.timeout) as client:
            data: dict[str, Any] = {
                "routing_key": routing_key,
                "event_action": event_action,
            }

            if dedup_key:
                data["dedup_key"] = dedup_key

            if event_action == "trigger":
                data["payload"] = {
                    "summary": summary or "Event from OmniPotent",
                    "source": source or "omnipotent",
                    "severity": severity,
                }
                if custom_details:
                    data["payload"]["custom_details"] = custom_details

            response = await client.post(
                f"{PAGERDUTY_EVENTS_BASE}/enqueue",
                json=data,
            )

            return response.json()

    async def trigger_event(
        self,
        summary: str,
        source: str,
        severity: str = "error",
        dedup_key: str | None = None,
        custom_details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Trigger a new event/incident."""
        if not self.config.integration_key:
            raise PagerDutyError("Integration key not configured")

        return await self.send_event(
            routing_key=self.config.integration_key,
            event_action="trigger",
            dedup_key=dedup_key,
            summary=summary,
            source=source,
            severity=severity,
            custom_details=custom_details,
        )

    async def acknowledge_event(self, dedup_key: str) -> dict[str, Any]:
        """Acknowledge an event."""
        if not self.config.integration_key:
            raise PagerDutyError("Integration key not configured")

        return await self.send_event(
            routing_key=self.config.integration_key,
            event_action="acknowledge",
            dedup_key=dedup_key,
        )

    async def resolve_event(self, dedup_key: str) -> dict[str, Any]:
        """Resolve an event."""
        if not self.config.integration_key:
            raise PagerDutyError("Integration key not configured")

        return await self.send_event(
            routing_key=self.config.integration_key,
            event_action="resolve",
            dedup_key=dedup_key,
        )


_client: PagerDutyClient | None = None


async def get_pagerduty_client() -> PagerDutyClient:
    """Get the default PagerDuty client."""
    global _client

    if _client is None:
        import os

        config = PagerDutyConfig(
            api_key=os.getenv("PAGERDUTY_API_KEY", ""),
            integration_key=os.getenv("PAGERDUTY_INTEGRATION_KEY"),
            webhook_secret=os.getenv("PAGERDUTY_WEBHOOK_SECRET"),
        )

        _client = PagerDutyClient(config)
        await _client.connect()

    return _client


async def close_pagerduty_client() -> None:
    """Close the default PagerDuty client."""
    global _client

    if _client:
        await _client.close()
        _client = None
