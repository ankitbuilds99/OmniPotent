"""ServiceNow Connector - ITSM and incident management."""

from __future__ import annotations

import base64
import logging
import time
from typing import Any

from app.connectors.base import Connector, ConnectorError, QueryResult

log = logging.getLogger("devcopilot.connectors.servicenow")


class ServiceNowConnector(Connector):
    """Connector for ServiceNow ITSM.

    Supports:
    - Incidents
    - Problems
    - Changes
    - CMDB (Configuration Items)
    - Knowledge articles
    """

    @property
    def connector_type(self) -> str:
        return "servicenow"

    def _default_headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

        if self.config.username and self.config.password:
            credentials = f"{self.config.username}:{self.config.password}"
            encoded = base64.b64encode(credentials.encode()).decode()
            headers["Authorization"] = f"Basic {encoded}"

        return headers

    async def health_check(self) -> bool:
        """Check ServiceNow is reachable."""
        try:
            response = await self._get("/api/now/table/sys_user?sysparm_limit=1")
            return response.status_code == 200
        except ConnectorError:
            return False

    async def get_incidents(
        self,
        state: str | None = None,
        priority: int | None = None,
        assignment_group: str | None = None,
        limit: int = 50,
    ) -> QueryResult:
        """Get incidents.

        Args:
            state: State filter (New, In Progress, Resolved, Closed)
            priority: Priority filter (1-5)
            assignment_group: Filter by assignment group
            limit: Max results
        """
        start = time.time()

        params: dict[str, Any] = {
            "sysparm_limit": limit,
            "sysparm_display_value": "true",
            "sysparm_order_by": "sys_created_on",
            "sysparm_order_direction": "desc",
        }

        # Build query
        queries = []
        if state:
            queries.append(f"state={state}")
        if priority:
            queries.append(f"priority={priority}")
        if assignment_group:
            queries.append(f"assignment_group.name={assignment_group}")

        if queries:
            params["sysparm_query"] = "^".join(queries)

        try:
            response = await self._get("/api/now/table/incident", params=params)
            data = response.json()

            incidents = data.get("result", [])

            return QueryResult(
                success=True,
                data=incidents,
                duration_ms=int((time.time() - start) * 1000),
                metadata={"count": len(incidents)},
            )

        except ConnectorError as e:
            return QueryResult(
                success=False,
                data=[],
                error=str(e),
                duration_ms=int((time.time() - start) * 1000),
            )

    async def get_incident(self, incident_number: str) -> QueryResult:
        """Get incident details.

        Args:
            incident_number: Incident number (e.g., INC0010001)
        """
        start = time.time()

        try:
            response = await self._get(
                "/api/now/table/incident",
                params={
                    "sysparm_query": f"number={incident_number}",
                    "sysparm_display_value": "true",
                    "sysparm_limit": 1,
                },
            )
            data = response.json()

            incidents = data.get("result", [])

            return QueryResult(
                success=len(incidents) > 0,
                data=incidents[0] if incidents else {},
                duration_ms=int((time.time() - start) * 1000),
            )

        except ConnectorError as e:
            return QueryResult(
                success=False,
                data={},
                error=str(e),
                duration_ms=int((time.time() - start) * 1000),
            )

    async def create_incident(
        self,
        short_description: str,
        description: str,
        priority: int = 3,
        category: str = "",
        assignment_group: str = "",
        caller_id: str = "",
    ) -> QueryResult:
        """Create a new incident.

        Args:
            short_description: Brief description
            description: Full description
            priority: Priority (1=Critical, 5=Planning)
            category: Incident category
            assignment_group: Assignment group name
            caller_id: Caller user sys_id
        """
        start = time.time()

        payload: dict[str, Any] = {
            "short_description": short_description,
            "description": description,
            "priority": priority,
            "impact": min(3, priority),
            "urgency": min(3, priority),
        }

        if category:
            payload["category"] = category
        if assignment_group:
            payload["assignment_group"] = assignment_group
        if caller_id:
            payload["caller_id"] = caller_id

        try:
            response = await self._post("/api/now/table/incident", json=payload)
            data = response.json()

            return QueryResult(
                success=True,
                data=data.get("result", {}),
                duration_ms=int((time.time() - start) * 1000),
            )

        except ConnectorError as e:
            return QueryResult(
                success=False,
                data={},
                error=str(e),
                duration_ms=int((time.time() - start) * 1000),
            )

    async def update_incident(
        self,
        sys_id: str,
        updates: dict[str, Any],
    ) -> QueryResult:
        """Update an incident.

        Args:
            sys_id: Incident sys_id
            updates: Fields to update
        """
        start = time.time()

        try:
            response = await self.client.patch(
                f"/api/now/table/incident/{sys_id}",
                json=updates,
            )
            data = response.json()

            return QueryResult(
                success=True,
                data=data.get("result", {}),
                duration_ms=int((time.time() - start) * 1000),
            )

        except Exception as e:
            return QueryResult(
                success=False,
                data={},
                error=str(e),
                duration_ms=int((time.time() - start) * 1000),
            )

    async def get_changes(
        self,
        state: str | None = None,
        type: str | None = None,
        limit: int = 50,
    ) -> QueryResult:
        """Get change requests.

        Args:
            state: State filter
            type: Change type (Normal, Standard, Emergency)
            limit: Max results
        """
        start = time.time()

        params: dict[str, Any] = {
            "sysparm_limit": limit,
            "sysparm_display_value": "true",
            "sysparm_order_by": "sys_created_on",
            "sysparm_order_direction": "desc",
        }

        queries = []
        if state:
            queries.append(f"state={state}")
        if type:
            queries.append(f"type={type}")

        if queries:
            params["sysparm_query"] = "^".join(queries)

        try:
            response = await self._get("/api/now/table/change_request", params=params)
            data = response.json()

            changes = data.get("result", [])

            return QueryResult(
                success=True,
                data=changes,
                duration_ms=int((time.time() - start) * 1000),
                metadata={"count": len(changes)},
            )

        except ConnectorError as e:
            return QueryResult(
                success=False,
                data=[],
                error=str(e),
                duration_ms=int((time.time() - start) * 1000),
            )

    async def get_problems(
        self,
        state: str | None = None,
        limit: int = 50,
    ) -> QueryResult:
        """Get problems.

        Args:
            state: State filter
            limit: Max results
        """
        start = time.time()

        params: dict[str, Any] = {
            "sysparm_limit": limit,
            "sysparm_display_value": "true",
        }

        if state:
            params["sysparm_query"] = f"state={state}"

        try:
            response = await self._get("/api/now/table/problem", params=params)
            data = response.json()

            problems = data.get("result", [])

            return QueryResult(
                success=True,
                data=problems,
                duration_ms=int((time.time() - start) * 1000),
                metadata={"count": len(problems)},
            )

        except ConnectorError as e:
            return QueryResult(
                success=False,
                data=[],
                error=str(e),
                duration_ms=int((time.time() - start) * 1000),
            )

    async def get_ci(self, ci_name: str) -> QueryResult:
        """Get Configuration Item from CMDB.

        Args:
            ci_name: CI name
        """
        start = time.time()

        try:
            response = await self._get(
                "/api/now/table/cmdb_ci",
                params={
                    "sysparm_query": f"name={ci_name}",
                    "sysparm_display_value": "true",
                    "sysparm_limit": 1,
                },
            )
            data = response.json()

            cis = data.get("result", [])

            return QueryResult(
                success=len(cis) > 0,
                data=cis[0] if cis else {},
                duration_ms=int((time.time() - start) * 1000),
            )

        except ConnectorError as e:
            return QueryResult(
                success=False,
                data={},
                error=str(e),
                duration_ms=int((time.time() - start) * 1000),
            )

    async def search_knowledge(
        self,
        query: str,
        limit: int = 10,
    ) -> QueryResult:
        """Search knowledge base.

        Args:
            query: Search query
            limit: Max results
        """
        start = time.time()

        try:
            response = await self._get(
                "/api/now/table/kb_knowledge",
                params={
                    "sysparm_query": f"short_descriptionLIKE{query}^ORtextLIKE{query}",
                    "sysparm_limit": limit,
                    "sysparm_display_value": "true",
                    "sysparm_fields": "number,short_description,text,sys_updated_on",
                },
            )
            data = response.json()

            articles = data.get("result", [])

            return QueryResult(
                success=True,
                data=articles,
                duration_ms=int((time.time() - start) * 1000),
                metadata={"count": len(articles)},
            )

        except ConnectorError as e:
            return QueryResult(
                success=False,
                data=[],
                error=str(e),
                duration_ms=int((time.time() - start) * 1000),
            )
