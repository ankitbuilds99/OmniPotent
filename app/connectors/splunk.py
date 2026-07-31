"""Splunk Connector - Log analytics and search."""

from __future__ import annotations

import base64
import logging
import time

from app.connectors.base import Connector, ConnectorError, QueryResult

log = logging.getLogger("devcopilot.connectors.splunk")


class SplunkConnector(Connector):
    """Connector for Splunk Enterprise/Cloud.

    Supports:
    - Search queries (SPL)
    - Saved searches
    - Alerts
    - Index management
    - Data summary
    """

    @property
    def connector_type(self) -> str:
        return "splunk"

    def _default_headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}

        if self.config.api_key:
            # Splunk token auth
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        elif self.config.username and self.config.password:
            # Basic auth
            credentials = f"{self.config.username}:{self.config.password}"
            encoded = base64.b64encode(credentials.encode()).decode()
            headers["Authorization"] = f"Basic {encoded}"

        return headers

    async def health_check(self) -> bool:
        """Check Splunk is reachable."""
        try:
            response = await self._get("/services/server/info?output_mode=json")
            return response.status_code == 200
        except ConnectorError:
            return False

    async def search(
        self,
        query: str,
        earliest_time: str = "-1h",
        latest_time: str = "now",
        max_results: int = 1000,
    ) -> QueryResult:
        """Execute a search query.

        Args:
            query: SPL query (without leading "search")
            earliest_time: Start time (Splunk format: -1h, -24h, etc.)
            latest_time: End time
            max_results: Maximum results

        Returns:
            QueryResult with search results
        """
        start = time.time()

        # Ensure query starts with "search" if it doesn't
        if not query.strip().lower().startswith("search"):
            query = f"search {query}"

        payload = {
            "search": query,
            "earliest_time": earliest_time,
            "latest_time": latest_time,
            "max_count": max_results,
            "output_mode": "json",
        }

        try:
            # Create search job
            response = await self._post(
                "/services/search/jobs",
                data=payload,
            )
            job_data = response.json()
            job_id = job_data.get("sid")

            if not job_id:
                return QueryResult(
                    success=False,
                    data=[],
                    error="Failed to create search job",
                    duration_ms=int((time.time() - start) * 1000),
                )

            # Wait for job completion
            results = await self._wait_for_job(job_id)

            return QueryResult(
                success=True,
                data=results,
                duration_ms=int((time.time() - start) * 1000),
                metadata={"job_id": job_id, "count": len(results)},
            )

        except ConnectorError as e:
            return QueryResult(
                success=False,
                data=[],
                error=str(e),
                duration_ms=int((time.time() - start) * 1000),
            )

    async def _wait_for_job(self, job_id: str, timeout: int = 60) -> list[dict]:
        """Wait for a search job to complete and return results."""
        import asyncio

        deadline = time.time() + timeout

        while time.time() < deadline:
            # Check job status
            response = await self._get(f"/services/search/jobs/{job_id}?output_mode=json")
            job_info = response.json()

            entry = job_info.get("entry", [{}])[0]
            content = entry.get("content", {})

            if content.get("isDone"):
                # Get results
                results_response = await self._get(
                    f"/services/search/jobs/{job_id}/results?output_mode=json"
                )
                results_data = results_response.json()
                return results_data.get("results", [])

            await asyncio.sleep(1)

        return []

    async def search_oneshot(
        self,
        query: str,
        earliest_time: str = "-1h",
        latest_time: str = "now",
        max_results: int = 100,
    ) -> QueryResult:
        """Execute a synchronous one-shot search (faster for small queries).

        Args:
            query: SPL query
            earliest_time: Start time
            latest_time: End time
            max_results: Maximum results
        """
        start = time.time()

        if not query.strip().lower().startswith("search"):
            query = f"search {query}"

        params = {
            "search": query,
            "earliest_time": earliest_time,
            "latest_time": latest_time,
            "count": max_results,
            "output_mode": "json",
        }

        try:
            response = await self._get("/services/search/jobs/export", params=params)
            data = response.json()

            results = data.get("results", [])

            return QueryResult(
                success=True,
                data=results,
                duration_ms=int((time.time() - start) * 1000),
                metadata={"count": len(results)},
            )

        except ConnectorError as e:
            return QueryResult(
                success=False,
                data=[],
                error=str(e),
                duration_ms=int((time.time() - start) * 1000),
            )

    async def get_saved_searches(self) -> QueryResult:
        """Get saved searches."""
        start = time.time()

        try:
            response = await self._get("/services/saved/searches?output_mode=json")
            data = response.json()

            searches = [
                {
                    "name": entry.get("name"),
                    "search": entry.get("content", {}).get("search"),
                    "cron": entry.get("content", {}).get("cron_schedule"),
                    "enabled": entry.get("content", {}).get("is_scheduled"),
                }
                for entry in data.get("entry", [])
            ]

            return QueryResult(
                success=True,
                data=searches,
                duration_ms=int((time.time() - start) * 1000),
                metadata={"count": len(searches)},
            )

        except ConnectorError as e:
            return QueryResult(
                success=False,
                data=[],
                error=str(e),
                duration_ms=int((time.time() - start) * 1000),
            )

    async def get_alerts(self, severity: str | None = None) -> QueryResult:
        """Get triggered alerts.

        Args:
            severity: Filter by severity
        """
        start = time.time()

        try:
            response = await self._get("/services/alerts/fired_alerts?output_mode=json")
            data = response.json()

            alerts = [
                {
                    "name": entry.get("name"),
                    "trigger_time": entry.get("content", {}).get("trigger_time"),
                    "severity": entry.get("content", {}).get("severity"),
                    "triggered_alerts": entry.get("content", {}).get("triggered_alert_count"),
                }
                for entry in data.get("entry", [])
            ]

            if severity:
                alerts = [a for a in alerts if a.get("severity") == severity]

            return QueryResult(
                success=True,
                data=alerts,
                duration_ms=int((time.time() - start) * 1000),
                metadata={"count": len(alerts)},
            )

        except ConnectorError as e:
            return QueryResult(
                success=False,
                data=[],
                error=str(e),
                duration_ms=int((time.time() - start) * 1000),
            )

    async def get_indexes(self) -> QueryResult:
        """Get available indexes."""
        start = time.time()

        try:
            response = await self._get("/services/data/indexes?output_mode=json")
            data = response.json()

            indexes = [
                {
                    "name": entry.get("name"),
                    "total_event_count": entry.get("content", {}).get("totalEventCount"),
                    "current_db_size_mb": entry.get("content", {}).get("currentDBSizeMB"),
                    "max_size": entry.get("content", {}).get("maxTotalDataSizeMB"),
                }
                for entry in data.get("entry", [])
            ]

            return QueryResult(
                success=True,
                data=indexes,
                duration_ms=int((time.time() - start) * 1000),
                metadata={"count": len(indexes)},
            )

        except ConnectorError as e:
            return QueryResult(
                success=False,
                data=[],
                error=str(e),
                duration_ms=int((time.time() - start) * 1000),
            )

    async def get_data_summary(
        self,
        index: str = "*",
        earliest_time: str = "-24h",
    ) -> QueryResult:
        """Get data summary for an index.

        Args:
            index: Index name (* for all)
            earliest_time: Time range
        """
        query = f"| tstats count WHERE index={index} by sourcetype, source"
        return await self.search_oneshot(query, earliest_time=earliest_time)

    async def get_error_summary(
        self,
        index: str = "*",
        earliest_time: str = "-1h",
    ) -> QueryResult:
        """Get error summary.

        Args:
            index: Index to search
            earliest_time: Time range
        """
        query = f"""
        index={index} (error OR exception OR fail OR critical)
        | stats count by sourcetype, host
        | sort -count
        | head 20
        """
        return await self.search_oneshot(query, earliest_time=earliest_time)
