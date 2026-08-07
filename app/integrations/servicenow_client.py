"""ServiceNow API Client.

Full-featured async client for ServiceNow REST API.
Supports incidents, changes, problems, and CMDB.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import httpx

log = logging.getLogger("omnipotent.integrations.servicenow")


class IncidentState(int, Enum):
    NEW = 1
    IN_PROGRESS = 2
    ON_HOLD = 3
    RESOLVED = 6
    CLOSED = 7
    CANCELLED = 8


class IncidentPriority(int, Enum):
    CRITICAL = 1
    HIGH = 2
    MODERATE = 3
    LOW = 4
    PLANNING = 5


class IncidentImpact(int, Enum):
    HIGH = 1
    MEDIUM = 2
    LOW = 3


class IncidentUrgency(int, Enum):
    HIGH = 1
    MEDIUM = 2
    LOW = 3


class ChangeState(int, Enum):
    NEW = -5
    ASSESS = -4
    AUTHORIZE = -3
    SCHEDULED = -2
    IMPLEMENT = -1
    REVIEW = 0
    CLOSED = 3
    CANCELLED = 4


class ServiceNowError(Exception):
    """ServiceNow API error."""

    def __init__(self, message: str, status_code: int | None = None, detail: str | None = None):
        self.message = message
        self.status_code = status_code
        self.detail = detail
        super().__init__(message)


@dataclass
class ServiceNowConfig:
    """Configuration for ServiceNow client."""

    instance: str
    username: str
    password: str
    client_id: str | None = None
    client_secret: str | None = None
    timeout: float = 30.0
    max_retries: int = 3


@dataclass
class Incident:
    """ServiceNow incident."""

    sys_id: str
    number: str
    short_description: str
    state: IncidentState
    priority: IncidentPriority
    impact: IncidentImpact
    urgency: IncidentUrgency
    description: str | None = None
    caller_id: str | None = None
    assignment_group: str | None = None
    assigned_to: str | None = None
    category: str | None = None
    subcategory: str | None = None
    cmdb_ci: str | None = None
    opened_at: datetime | None = None
    resolved_at: datetime | None = None
    closed_at: datetime | None = None
    close_code: str | None = None
    close_notes: str | None = None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "Incident":
        """Create from API response."""
        def parse_datetime(value: str | None) -> datetime | None:
            if not value:
                return None
            try:
                return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            except ValueError:
                return None

        return cls(
            sys_id=data.get("sys_id", ""),
            number=data.get("number", ""),
            short_description=data.get("short_description", ""),
            state=IncidentState(int(data.get("state", 1))),
            priority=IncidentPriority(int(data.get("priority", 4))),
            impact=IncidentImpact(int(data.get("impact", 3))),
            urgency=IncidentUrgency(int(data.get("urgency", 3))),
            description=data.get("description"),
            caller_id=data.get("caller_id", {}).get("value") if isinstance(data.get("caller_id"), dict) else data.get("caller_id"),
            assignment_group=data.get("assignment_group", {}).get("value") if isinstance(data.get("assignment_group"), dict) else data.get("assignment_group"),
            assigned_to=data.get("assigned_to", {}).get("value") if isinstance(data.get("assigned_to"), dict) else data.get("assigned_to"),
            category=data.get("category"),
            subcategory=data.get("subcategory"),
            cmdb_ci=data.get("cmdb_ci", {}).get("value") if isinstance(data.get("cmdb_ci"), dict) else data.get("cmdb_ci"),
            opened_at=parse_datetime(data.get("opened_at")),
            resolved_at=parse_datetime(data.get("resolved_at")),
            closed_at=parse_datetime(data.get("closed_at")),
            close_code=data.get("close_code"),
            close_notes=data.get("close_notes"),
        )


@dataclass
class Change:
    """ServiceNow change request."""

    sys_id: str
    number: str
    short_description: str
    state: ChangeState
    type: str
    risk: str
    impact: str
    description: str | None = None
    assignment_group: str | None = None
    assigned_to: str | None = None
    cmdb_ci: str | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None
    requested_by: str | None = None
    work_notes: str | None = None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "Change":
        """Create from API response."""
        def parse_datetime(value: str | None) -> datetime | None:
            if not value:
                return None
            try:
                return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            except ValueError:
                return None

        return cls(
            sys_id=data.get("sys_id", ""),
            number=data.get("number", ""),
            short_description=data.get("short_description", ""),
            state=ChangeState(int(data.get("state", -5))),
            type=data.get("type", ""),
            risk=data.get("risk", ""),
            impact=data.get("impact", ""),
            description=data.get("description"),
            assignment_group=data.get("assignment_group", {}).get("value") if isinstance(data.get("assignment_group"), dict) else data.get("assignment_group"),
            assigned_to=data.get("assigned_to", {}).get("value") if isinstance(data.get("assigned_to"), dict) else data.get("assigned_to"),
            cmdb_ci=data.get("cmdb_ci", {}).get("value") if isinstance(data.get("cmdb_ci"), dict) else data.get("cmdb_ci"),
            start_date=parse_datetime(data.get("start_date")),
            end_date=parse_datetime(data.get("end_date")),
            requested_by=data.get("requested_by", {}).get("value") if isinstance(data.get("requested_by"), dict) else data.get("requested_by"),
            work_notes=data.get("work_notes"),
        )


@dataclass
class CMDBItem:
    """ServiceNow CMDB configuration item."""

    sys_id: str
    name: str
    sys_class_name: str
    operational_status: str
    category: str | None = None
    subcategory: str | None = None
    company: str | None = None
    department: str | None = None
    location: str | None = None
    assigned_to: str | None = None
    support_group: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "CMDBItem":
        return cls(
            sys_id=data.get("sys_id", ""),
            name=data.get("name", ""),
            sys_class_name=data.get("sys_class_name", ""),
            operational_status=data.get("operational_status", ""),
            category=data.get("category"),
            subcategory=data.get("subcategory"),
            company=data.get("company", {}).get("value") if isinstance(data.get("company"), dict) else data.get("company"),
            department=data.get("department", {}).get("value") if isinstance(data.get("department"), dict) else data.get("department"),
            location=data.get("location", {}).get("value") if isinstance(data.get("location"), dict) else data.get("location"),
            assigned_to=data.get("assigned_to", {}).get("value") if isinstance(data.get("assigned_to"), dict) else data.get("assigned_to"),
            support_group=data.get("support_group", {}).get("value") if isinstance(data.get("support_group"), dict) else data.get("support_group"),
            attributes={k: v for k, v in data.items() if k not in ["sys_id", "name", "sys_class_name", "operational_status", "category", "subcategory", "company", "department", "location", "assigned_to", "support_group"]},
        )


class ServiceNowClient:
    """Async ServiceNow API client."""

    def __init__(self, config: ServiceNowConfig) -> None:
        self.config = config
        self._client: httpx.AsyncClient | None = None
        self._base_url = f"https://{config.instance}.service-now.com/api"

    async def __aenter__(self) -> "ServiceNowClient":
        await self.connect()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def connect(self) -> None:
        """Initialize the HTTP client."""
        auth_string = base64.b64encode(
            f"{self.config.username}:{self.config.password}".encode()
        ).decode()

        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=self.config.timeout,
            headers={
                "Authorization": f"Basic {auth_string}",
                "Content-Type": "application/json",
                "Accept": "application/json",
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
                    log.warning("Rate limited, waiting...")
                    import asyncio
                    await asyncio.sleep(60)
                    continue

                result = response.json() if response.content else {}

                if response.status_code >= 400:
                    error = result.get("error", {})
                    raise ServiceNowError(
                        message=error.get("message", f"HTTP {response.status_code}"),
                        status_code=response.status_code,
                        detail=error.get("detail"),
                    )

                return result

            except httpx.HTTPError as e:
                if attempt == self.config.max_retries - 1:
                    raise ServiceNowError(f"HTTP error: {e}")
                import asyncio
                await asyncio.sleep(1)

        raise ServiceNowError("Max retries exceeded")

    async def create_incident(
        self,
        short_description: str,
        description: str | None = None,
        caller_id: str | None = None,
        assignment_group: str | None = None,
        assigned_to: str | None = None,
        category: str | None = None,
        subcategory: str | None = None,
        impact: IncidentImpact = IncidentImpact.MEDIUM,
        urgency: IncidentUrgency = IncidentUrgency.MEDIUM,
        cmdb_ci: str | None = None,
        additional_fields: dict[str, Any] | None = None,
    ) -> Incident:
        """Create a new incident."""
        data: dict[str, Any] = {
            "short_description": short_description,
            "impact": impact.value,
            "urgency": urgency.value,
        }

        if description:
            data["description"] = description
        if caller_id:
            data["caller_id"] = caller_id
        if assignment_group:
            data["assignment_group"] = assignment_group
        if assigned_to:
            data["assigned_to"] = assigned_to
        if category:
            data["category"] = category
        if subcategory:
            data["subcategory"] = subcategory
        if cmdb_ci:
            data["cmdb_ci"] = cmdb_ci
        if additional_fields:
            data.update(additional_fields)

        result = await self._request("POST", "/now/table/incident", data=data)
        return Incident.from_api(result.get("result", {}))

    async def get_incident(self, sys_id: str) -> Incident:
        """Get an incident by sys_id."""
        result = await self._request("GET", f"/now/table/incident/{sys_id}")
        return Incident.from_api(result.get("result", {}))

    async def get_incident_by_number(self, number: str) -> Incident | None:
        """Get an incident by number."""
        incidents = await self.list_incidents(query=f"number={number}", limit=1)
        return incidents[0] if incidents else None

    async def list_incidents(
        self,
        query: str | None = None,
        state: IncidentState | None = None,
        priority: IncidentPriority | None = None,
        assignment_group: str | None = None,
        limit: int = 20,
        offset: int = 0,
        order_by: str = "sys_created_on",
        order_desc: bool = True,
    ) -> list[Incident]:
        """List incidents with filters."""
        params: dict[str, Any] = {
            "sysparm_limit": limit,
            "sysparm_offset": offset,
            "sysparm_order_by": f"{order_by}{'%5Edesc' if order_desc else ''}",
        }

        query_parts = []
        if state:
            query_parts.append(f"state={state.value}")
        if priority:
            query_parts.append(f"priority={priority.value}")
        if assignment_group:
            query_parts.append(f"assignment_group={assignment_group}")
        if query:
            query_parts.append(query)

        if query_parts:
            params["sysparm_query"] = "^".join(query_parts)

        result = await self._request("GET", "/now/table/incident", params=params)
        return [Incident.from_api(i) for i in result.get("result", [])]

    async def update_incident(
        self,
        sys_id: str,
        updates: dict[str, Any],
    ) -> Incident:
        """Update an incident."""
        result = await self._request("PATCH", f"/now/table/incident/{sys_id}", data=updates)
        return Incident.from_api(result.get("result", {}))

    async def add_work_note(
        self,
        sys_id: str,
        work_notes: str,
    ) -> Incident:
        """Add a work note to an incident."""
        return await self.update_incident(sys_id, {"work_notes": work_notes})

    async def add_comment(
        self,
        sys_id: str,
        comments: str,
    ) -> Incident:
        """Add a customer-visible comment to an incident."""
        return await self.update_incident(sys_id, {"comments": comments})

    async def resolve_incident(
        self,
        sys_id: str,
        close_code: str,
        close_notes: str,
    ) -> Incident:
        """Resolve an incident."""
        return await self.update_incident(
            sys_id,
            {
                "state": IncidentState.RESOLVED.value,
                "close_code": close_code,
                "close_notes": close_notes,
            },
        )

    async def close_incident(
        self,
        sys_id: str,
    ) -> Incident:
        """Close an incident."""
        return await self.update_incident(
            sys_id,
            {"state": IncidentState.CLOSED.value},
        )

    async def create_change(
        self,
        short_description: str,
        description: str | None = None,
        type: str = "normal",
        risk: str = "moderate",
        impact: str = "medium",
        assignment_group: str | None = None,
        assigned_to: str | None = None,
        cmdb_ci: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        additional_fields: dict[str, Any] | None = None,
    ) -> Change:
        """Create a change request."""
        data: dict[str, Any] = {
            "short_description": short_description,
            "type": type,
            "risk": risk,
            "impact": impact,
        }

        if description:
            data["description"] = description
        if assignment_group:
            data["assignment_group"] = assignment_group
        if assigned_to:
            data["assigned_to"] = assigned_to
        if cmdb_ci:
            data["cmdb_ci"] = cmdb_ci
        if start_date:
            data["start_date"] = start_date.strftime("%Y-%m-%d %H:%M:%S")
        if end_date:
            data["end_date"] = end_date.strftime("%Y-%m-%d %H:%M:%S")
        if additional_fields:
            data.update(additional_fields)

        result = await self._request("POST", "/now/table/change_request", data=data)
        return Change.from_api(result.get("result", {}))

    async def get_change(self, sys_id: str) -> Change:
        """Get a change request."""
        result = await self._request("GET", f"/now/table/change_request/{sys_id}")
        return Change.from_api(result.get("result", {}))

    async def list_changes(
        self,
        query: str | None = None,
        state: ChangeState | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[Change]:
        """List change requests."""
        params: dict[str, Any] = {
            "sysparm_limit": limit,
            "sysparm_offset": offset,
        }

        query_parts = []
        if state:
            query_parts.append(f"state={state.value}")
        if query:
            query_parts.append(query)

        if query_parts:
            params["sysparm_query"] = "^".join(query_parts)

        result = await self._request("GET", "/now/table/change_request", params=params)
        return [Change.from_api(c) for c in result.get("result", [])]

    async def update_change(
        self,
        sys_id: str,
        updates: dict[str, Any],
    ) -> Change:
        """Update a change request."""
        result = await self._request("PATCH", f"/now/table/change_request/{sys_id}", data=updates)
        return Change.from_api(result.get("result", {}))

    async def search_cmdb(
        self,
        sys_class_name: str = "cmdb_ci",
        query: str | None = None,
        name_contains: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[CMDBItem]:
        """Search CMDB configuration items."""
        params: dict[str, Any] = {
            "sysparm_limit": limit,
            "sysparm_offset": offset,
        }

        query_parts = []
        if name_contains:
            query_parts.append(f"nameLIKE{name_contains}")
        if query:
            query_parts.append(query)

        if query_parts:
            params["sysparm_query"] = "^".join(query_parts)

        result = await self._request("GET", f"/now/table/{sys_class_name}", params=params)
        return [CMDBItem.from_api(item) for item in result.get("result", [])]

    async def get_cmdb_item(self, sys_id: str, sys_class_name: str = "cmdb_ci") -> CMDBItem:
        """Get a CMDB configuration item."""
        result = await self._request("GET", f"/now/table/{sys_class_name}/{sys_id}")
        return CMDBItem.from_api(result.get("result", {}))

    async def get_user(self, sys_id: str) -> dict[str, Any]:
        """Get a user."""
        result = await self._request("GET", f"/now/table/sys_user/{sys_id}")
        return result.get("result", {})

    async def get_user_by_email(self, email: str) -> dict[str, Any] | None:
        """Get a user by email."""
        params = {"sysparm_query": f"email={email}", "sysparm_limit": 1}
        result = await self._request("GET", "/now/table/sys_user", params=params)
        users = result.get("result", [])
        return users[0] if users else None

    async def get_group(self, sys_id: str) -> dict[str, Any]:
        """Get a group."""
        result = await self._request("GET", f"/now/table/sys_user_group/{sys_id}")
        return result.get("result", {})

    async def get_group_by_name(self, name: str) -> dict[str, Any] | None:
        """Get a group by name."""
        params = {"sysparm_query": f"name={name}", "sysparm_limit": 1}
        result = await self._request("GET", "/now/table/sys_user_group", params=params)
        groups = result.get("result", [])
        return groups[0] if groups else None

    async def link_incident_to_change(
        self,
        incident_sys_id: str,
        change_sys_id: str,
    ) -> dict[str, Any]:
        """Link an incident to a change request."""
        data = {
            "parent": change_sys_id,
            "child": incident_sys_id,
            "type": "caused by::caused",
        }

        result = await self._request("POST", "/now/table/task_rel_task", data=data)
        return result.get("result", {})


_client: ServiceNowClient | None = None


async def get_servicenow_client() -> ServiceNowClient:
    """Get the default ServiceNow client."""
    global _client

    if _client is None:
        import os

        config = ServiceNowConfig(
            instance=os.getenv("SERVICENOW_INSTANCE", ""),
            username=os.getenv("SERVICENOW_USERNAME", ""),
            password=os.getenv("SERVICENOW_PASSWORD", ""),
            client_id=os.getenv("SERVICENOW_CLIENT_ID"),
            client_secret=os.getenv("SERVICENOW_CLIENT_SECRET"),
        )

        _client = ServiceNowClient(config)
        await _client.connect()

    return _client


async def close_servicenow_client() -> None:
    """Close the default ServiceNow client."""
    global _client

    if _client:
        await _client.close()
        _client = None
