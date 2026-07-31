"""Grafana Connector - Dashboard queries and annotations."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from app.connectors.base import Connector, ConnectorError, QueryResult

log = logging.getLogger("devcopilot.connectors.grafana")


class GrafanaConnector(Connector):
    """Connector for Grafana dashboards and annotations.

    Supports:
    - Dashboard search and retrieval
    - Annotation queries and creation
    - Datasource queries
    - Alert queries
    """

    @property
    def connector_type(self) -> str:
        return "grafana"

    def _default_headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        return headers

    async def health_check(self) -> bool:
        """Check Grafana is reachable."""
        try:
            response = await self._get("/api/health")
            return response.status_code == 200
        except ConnectorError:
            return False

    async def search_dashboards(self, query: str = "", tag: str = "") -> QueryResult:
        """Search for dashboards.

        Args:
            query: Search query
            tag: Filter by tag

        Returns:
            QueryResult with dashboard list
        """
        start = time.time()

        params = {"type": "dash-db"}
        if query:
            params["query"] = query
        if tag:
            params["tag"] = tag

        try:
            response = await self._get("/api/search", params=params)
            dashboards = response.json()

            return QueryResult(
                success=True,
                data=dashboards,
                duration_ms=int((time.time() - start) * 1000),
                metadata={"count": len(dashboards)},
            )

        except ConnectorError as e:
            return QueryResult(
                success=False,
                data=[],
                error=str(e),
                duration_ms=int((time.time() - start) * 1000),
            )

    async def get_dashboard(self, uid: str) -> QueryResult:
        """Get a dashboard by UID.

        Args:
            uid: Dashboard UID

        Returns:
            QueryResult with dashboard definition
        """
        start = time.time()

        try:
            response = await self._get(f"/api/dashboards/uid/{uid}")
            data = response.json()

            return QueryResult(
                success=True,
                data=data,
                duration_ms=int((time.time() - start) * 1000),
            )

        except ConnectorError as e:
            return QueryResult(
                success=False,
                data={},
                error=str(e),
                duration_ms=int((time.time() - start) * 1000),
            )

    async def get_annotations(
        self,
        dashboard_uid: str | None = None,
        from_time: datetime | None = None,
        to_time: datetime | None = None,
        tags: list[str] | None = None,
        limit: int = 100,
    ) -> QueryResult:
        """Get annotations.

        Args:
            dashboard_uid: Filter by dashboard
            from_time: Start time
            to_time: End time
            tags: Filter by tags
            limit: Max annotations to return

        Returns:
            QueryResult with annotations
        """
        start = time.time()

        params: dict[str, Any] = {"limit": limit}
        if dashboard_uid:
            params["dashboardUID"] = dashboard_uid
        if from_time:
            params["from"] = int(from_time.timestamp() * 1000)
        if to_time:
            params["to"] = int(to_time.timestamp() * 1000)
        if tags:
            params["tags"] = ",".join(tags)

        try:
            response = await self._get("/api/annotations", params=params)
            annotations = response.json()

            return QueryResult(
                success=True,
                data=annotations,
                duration_ms=int((time.time() - start) * 1000),
                metadata={"count": len(annotations)},
            )

        except ConnectorError as e:
            return QueryResult(
                success=False,
                data=[],
                error=str(e),
                duration_ms=int((time.time() - start) * 1000),
            )

    async def create_annotation(
        self,
        text: str,
        tags: list[str] | None = None,
        dashboard_uid: str | None = None,
        panel_id: int | None = None,
        time_start: datetime | None = None,
        time_end: datetime | None = None,
    ) -> QueryResult:
        """Create an annotation.

        Args:
            text: Annotation text
            tags: Tags for the annotation
            dashboard_uid: Optional dashboard to attach to
            panel_id: Optional panel within dashboard
            time_start: Start time (defaults to now)
            time_end: End time (for region annotations)

        Returns:
            QueryResult with created annotation
        """
        start = time.time()

        now = datetime.now(timezone.utc)
        payload: dict[str, Any] = {
            "text": text,
            "tags": tags or ["devops-copilot"],
            "time": int((time_start or now).timestamp() * 1000),
        }

        if dashboard_uid:
            payload["dashboardUID"] = dashboard_uid
        if panel_id:
            payload["panelId"] = panel_id
        if time_end:
            payload["timeEnd"] = int(time_end.timestamp() * 1000)

        try:
            response = await self._post("/api/annotations", json=payload)
            data = response.json()

            return QueryResult(
                success=True,
                data=data,
                duration_ms=int((time.time() - start) * 1000),
            )

        except ConnectorError as e:
            return QueryResult(
                success=False,
                data={},
                error=str(e),
                duration_ms=int((time.time() - start) * 1000),
            )

    async def get_alerts(self, state: str | None = None) -> QueryResult:
        """Get Grafana alerts.

        Args:
            state: Filter by state (alerting, pending, ok, paused)

        Returns:
            QueryResult with alerts
        """
        start = time.time()

        try:
            response = await self._get("/api/v1/provisioning/alert-rules")
            rules = response.json()

            return QueryResult(
                success=True,
                data=rules,
                duration_ms=int((time.time() - start) * 1000),
            )

        except ConnectorError as e:
            return QueryResult(
                success=False,
                data=[],
                error=str(e),
                duration_ms=int((time.time() - start) * 1000),
            )

    async def get_datasources(self) -> QueryResult:
        """Get configured datasources."""
        start = time.time()

        try:
            response = await self._get("/api/datasources")
            datasources = response.json()

            return QueryResult(
                success=True,
                data=datasources,
                duration_ms=int((time.time() - start) * 1000),
                metadata={"count": len(datasources)},
            )

        except ConnectorError as e:
            return QueryResult(
                success=False,
                data=[],
                error=str(e),
                duration_ms=int((time.time() - start) * 1000),
            )

    async def query_datasource(
        self,
        datasource_uid: str,
        query: dict[str, Any],
        from_time: datetime,
        to_time: datetime,
    ) -> QueryResult:
        """Query a datasource directly.

        Args:
            datasource_uid: Datasource UID
            query: Query payload (datasource-specific)
            from_time: Start time
            to_time: End time

        Returns:
            QueryResult with query results
        """
        start = time.time()

        payload = {
            "queries": [
                {
                    "datasourceId": datasource_uid,
                    **query,
                }
            ],
            "from": str(int(from_time.timestamp() * 1000)),
            "to": str(int(to_time.timestamp() * 1000)),
        }

        try:
            response = await self._post("/api/ds/query", json=payload)
            data = response.json()

            return QueryResult(
                success=True,
                data=data.get("results", {}),
                duration_ms=int((time.time() - start) * 1000),
            )

        except ConnectorError as e:
            return QueryResult(
                success=False,
                data={},
                error=str(e),
                duration_ms=int((time.time() - start) * 1000),
            )
