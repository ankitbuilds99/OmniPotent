"""Prometheus Connector - Metrics and alerting queries."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.connectors.base import Connector, ConnectorError, QueryResult

log = logging.getLogger("devcopilot.connectors.prometheus")


@dataclass
class PrometheusMetric:
    """A Prometheus metric result."""
    metric_name: str
    labels: dict[str, str]
    value: float
    timestamp: datetime


@dataclass
class PrometheusAlert:
    """A Prometheus alert."""
    alertname: str
    state: str  # firing, pending, inactive
    severity: str
    labels: dict[str, str]
    annotations: dict[str, str]
    starts_at: datetime | None = None
    ends_at: datetime | None = None


class PrometheusConnector(Connector):
    """Connector for Prometheus metrics and alerting.

    Supports:
    - Instant queries (current value)
    - Range queries (time series)
    - Alert queries
    - Metadata queries (labels, series)
    """

    @property
    def connector_type(self) -> str:
        return "prometheus"

    def _default_headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        return headers

    async def health_check(self) -> bool:
        """Check Prometheus is reachable."""
        try:
            response = await self._get("/api/v1/status/buildinfo")
            return response.status_code == 200
        except ConnectorError:
            return False

    async def query(self, promql: str, query_time: datetime | None = None) -> QueryResult:
        """Execute an instant query.

        Args:
            promql: PromQL query string
            query_time: Optional timestamp (defaults to now)

        Returns:
            QueryResult with metric values
        """
        import time as time_module
        start = time_module.time()

        params: dict[str, Any] = {"query": promql}
        if query_time:
            params["time"] = str(query_time.timestamp())

        try:
            response = await self._get("/api/v1/query", params=params)
            data = response.json()

            if data.get("status") != "success":
                return QueryResult(
                    success=False,
                    data=[],
                    error=data.get("error", "Unknown error"),
                    duration_ms=int((__import__('time').time() - start) * 1000),
                )

            results = self._parse_instant_results(data.get("data", {}))

            return QueryResult(
                success=True,
                data=results,
                duration_ms=int((__import__('time').time() - start) * 1000),
                metadata={"result_type": data.get("data", {}).get("resultType")},
            )

        except ConnectorError as e:
            return QueryResult(
                success=False,
                data=[],
                error=str(e),
                duration_ms=int((__import__('time').time() - start) * 1000),
            )

    async def query_range(
        self,
        promql: str,
        start: datetime,
        end: datetime,
        step: str = "1m",
    ) -> QueryResult:
        """Execute a range query.

        Args:
            promql: PromQL query string
            start: Start time
            end: End time
            step: Query resolution (e.g., "1m", "5m", "1h")

        Returns:
            QueryResult with time series data
        """
        query_start = __import__('time').time()

        params = {
            "query": promql,
            "start": start.timestamp(),
            "end": end.timestamp(),
            "step": step,
        }

        try:
            response = await self._get("/api/v1/query_range", params=params)
            data = response.json()

            if data.get("status") != "success":
                return QueryResult(
                    success=False,
                    data=[],
                    error=data.get("error", "Unknown error"),
                    duration_ms=int((__import__('time').time() - query_start) * 1000),
                )

            results = self._parse_range_results(data.get("data", {}))

            return QueryResult(
                success=True,
                data=results,
                duration_ms=int((__import__('time').time() - query_start) * 1000),
                metadata={
                    "result_type": "matrix",
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "step": step,
                },
            )

        except ConnectorError as e:
            return QueryResult(
                success=False,
                data=[],
                error=str(e),
                duration_ms=int((__import__('time').time() - query_start) * 1000),
            )

    async def get_alerts(self, state: str | None = None) -> QueryResult:
        """Get current alerts.

        Args:
            state: Filter by state (firing, pending, inactive)

        Returns:
            QueryResult with alert list
        """
        query_start = __import__('time').time()

        try:
            response = await self._get("/api/v1/alerts")
            data = response.json()

            if data.get("status") != "success":
                return QueryResult(
                    success=False,
                    data=[],
                    error=data.get("error"),
                    duration_ms=int((__import__('time').time() - query_start) * 1000),
                )

            alerts = self._parse_alerts(data.get("data", {}).get("alerts", []))

            if state:
                alerts = [a for a in alerts if a["state"] == state]

            return QueryResult(
                success=True,
                data=alerts,
                duration_ms=int((__import__('time').time() - query_start) * 1000),
                metadata={"total_alerts": len(alerts)},
            )

        except ConnectorError as e:
            return QueryResult(
                success=False,
                data=[],
                error=str(e),
                duration_ms=int((__import__('time').time() - query_start) * 1000),
            )

    async def get_rules(self) -> QueryResult:
        """Get alerting and recording rules."""
        query_start = __import__('time').time()

        try:
            response = await self._get("/api/v1/rules")
            data = response.json()

            return QueryResult(
                success=data.get("status") == "success",
                data=data.get("data", {}).get("groups", []),
                error=data.get("error"),
                duration_ms=int((__import__('time').time() - query_start) * 1000),
            )

        except ConnectorError as e:
            return QueryResult(
                success=False,
                data=[],
                error=str(e),
                duration_ms=int((__import__('time').time() - query_start) * 1000),
            )

    async def get_targets(self) -> QueryResult:
        """Get scrape targets and their health."""
        query_start = __import__('time').time()

        try:
            response = await self._get("/api/v1/targets")
            data = response.json()

            return QueryResult(
                success=data.get("status") == "success",
                data=data.get("data", {}),
                duration_ms=int((__import__('time').time() - query_start) * 1000),
            )

        except ConnectorError as e:
            return QueryResult(
                success=False,
                data={},
                error=str(e),
                duration_ms=int((__import__('time').time() - query_start) * 1000),
            )

    async def get_label_values(self, label: str) -> QueryResult:
        """Get all values for a label.

        Args:
            label: Label name (e.g., "job", "instance")
        """
        query_start = __import__('time').time()

        try:
            response = await self._get(f"/api/v1/label/{label}/values")
            data = response.json()

            return QueryResult(
                success=data.get("status") == "success",
                data=data.get("data", []),
                duration_ms=int((__import__('time').time() - query_start) * 1000),
            )

        except ConnectorError as e:
            return QueryResult(
                success=False,
                data=[],
                error=str(e),
                duration_ms=int((__import__('time').time() - query_start) * 1000),
            )

    def _parse_instant_results(self, data: dict) -> list[dict]:
        """Parse instant query results."""
        results = []
        result_type = data.get("resultType", "")

        if result_type == "vector":
            for item in data.get("result", []):
                metric = item.get("metric", {})
                value = item.get("value", [])
                results.append({
                    "metric": metric.get("__name__", ""),
                    "labels": {k: v for k, v in metric.items() if k != "__name__"},
                    "value": float(value[1]) if len(value) > 1 else None,
                    "timestamp": datetime.fromtimestamp(value[0], tz=timezone.utc).isoformat() if value else None,
                })
        elif result_type == "scalar":
            value = data.get("result", [])
            results.append({
                "metric": "scalar",
                "labels": {},
                "value": float(value[1]) if len(value) > 1 else None,
                "timestamp": datetime.fromtimestamp(value[0], tz=timezone.utc).isoformat() if value else None,
            })

        return results

    def _parse_range_results(self, data: dict) -> list[dict]:
        """Parse range query results."""
        results = []

        for item in data.get("result", []):
            metric = item.get("metric", {})
            values = item.get("values", [])

            series = {
                "metric": metric.get("__name__", ""),
                "labels": {k: v for k, v in metric.items() if k != "__name__"},
                "values": [
                    {
                        "timestamp": datetime.fromtimestamp(v[0], tz=timezone.utc).isoformat(),
                        "value": float(v[1]),
                    }
                    for v in values
                ],
            }
            results.append(series)

        return results

    def _parse_alerts(self, alerts: list) -> list[dict]:
        """Parse alert results."""
        results = []

        for alert in alerts:
            results.append({
                "alertname": alert.get("labels", {}).get("alertname", ""),
                "state": alert.get("state", ""),
                "severity": alert.get("labels", {}).get("severity", "warning"),
                "labels": alert.get("labels", {}),
                "annotations": alert.get("annotations", {}),
                "starts_at": alert.get("activeAt"),
                "value": alert.get("value"),
            })

        return results
