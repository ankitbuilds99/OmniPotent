"""Data Collectors - Gather data from various sources inside customer VPC.

Collectors:
- LogCollector: Elasticsearch, Loki, CloudWatch, file-based
- MetricsCollector: Prometheus, Datadog, CloudWatch Metrics
- CodeCollector: Git, GitHub/GitLab, deployment systems
- KubernetesCollector: Pod status, events, deployments
"""

from __future__ import annotations

import json
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from app.vpc_connector.redaction import RedactionEngine

log = logging.getLogger("omnipotent.vpc_connector.collectors")


class DataSourceType(str, Enum):
    """Types of data sources."""
    LOGS = "logs"
    METRICS = "metrics"
    CODE = "code"
    KUBERNETES = "kubernetes"
    TRACES = "traces"


@dataclass
class QueryResult:
    """Result from a data source query."""
    source_type: DataSourceType
    data: dict[str, Any]
    redacted_fields: list[str] = field(default_factory=list)
    query_duration_ms: int = 0
    bytes_processed: int = 0
    truncated: bool = False
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_type": self.source_type.value,
            "data": self.data,
            "redacted_fields": self.redacted_fields,
            "query_duration_ms": self.query_duration_ms,
            "bytes_processed": self.bytes_processed,
            "truncated": self.truncated,
            "error": self.error,
        }


class DataCollector(ABC):
    """Base class for data source collectors."""

    def __init__(
        self,
        config: dict[str, Any],
        redaction: RedactionEngine,
    ):
        self.config = config
        self.redaction = redaction

    @abstractmethod
    async def collect(self, query: dict[str, Any]) -> QueryResult:
        """Collect data based on query parameters."""
        pass

    @abstractmethod
    async def health_check(self) -> bool:
        """Check if the data source is healthy."""
        pass

    @property
    @abstractmethod
    def source_type(self) -> DataSourceType:
        """Return the type of data source."""
        pass


class LogCollector(DataCollector):
    """Collects logs from various sources.

    Supported backends:
    - Elasticsearch
    - Grafana Loki
    - AWS CloudWatch Logs
    - File-based logs
    """

    @property
    def source_type(self) -> DataSourceType:
        return DataSourceType.LOGS

    async def collect(self, query: dict[str, Any]) -> QueryResult:
        """Collect logs matching the query.

        Query params:
        - service: Service name filter
        - level: Log level (error, warn, info, debug)
        - time_range: {"start": ISO8601, "end": ISO8601}
        - search: Text search query
        - limit: Max logs to return (default 100)
        """
        start_time = time.time()

        backend = self.config.get("type", "elasticsearch")

        try:
            if backend == "elasticsearch":
                logs = await self._collect_elasticsearch(query)
            elif backend == "loki":
                logs = await self._collect_loki(query)
            elif backend == "cloudwatch":
                logs = await self._collect_cloudwatch(query)
            else:
                logs = await self._collect_file(query)

            # Redact before returning
            redacted, redacted_fields = self.redaction.redact_dict({"logs": logs})

            duration_ms = int((time.time() - start_time) * 1000)

            return QueryResult(
                source_type=self.source_type,
                data=redacted,
                redacted_fields=redacted_fields,
                query_duration_ms=duration_ms,
                bytes_processed=len(json.dumps(logs)),
            )

        except Exception as e:
            log.exception("Log collection failed")
            return QueryResult(
                source_type=self.source_type,
                data={"logs": []},
                error=str(e),
                query_duration_ms=int((time.time() - start_time) * 1000),
            )

    async def _collect_elasticsearch(self, query: dict[str, Any]) -> list[dict]:
        """Collect from Elasticsearch."""
        # In production: use elasticsearch-py async client
        es_url = self.config.get("url", "http://localhost:9200")
        index = self.config.get("index", "logs-*")

        time_range = query.get("time_range", {})
        es_query = {
            "query": {
                "bool": {
                    "must": [],
                    "filter": [
                        {
                            "range": {
                                "@timestamp": {
                                    "gte": time_range.get("start", "now-1h"),
                                    "lte": time_range.get("end", "now"),
                                }
                            }
                        }
                    ]
                }
            },
            "size": query.get("limit", 100),
            "sort": [{"@timestamp": "desc"}],
        }

        if query.get("service"):
            es_query["query"]["bool"]["must"].append(
                {"match": {"service": query["service"]}}
            )

        if query.get("level"):
            es_query["query"]["bool"]["must"].append(
                {"match": {"level": query["level"]}}
            )

        if query.get("search"):
            es_query["query"]["bool"]["must"].append(
                {"match": {"message": query["search"]}}
            )

        # Simulate response for now
        log.debug("Would query ES at %s/%s with: %s", es_url, index, es_query)
        return [
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "service": query.get("service", "unknown"),
                "level": query.get("level", "ERROR"),
                "message": "Sample log entry",
            }
        ]

    async def _collect_loki(self, query: dict[str, Any]) -> list[dict]:
        """Collect from Grafana Loki."""
        # In production: use Loki's HTTP API
        return []

    async def _collect_cloudwatch(self, query: dict[str, Any]) -> list[dict]:
        """Collect from AWS CloudWatch Logs."""
        # In production: use boto3 logs client
        return []

    async def _collect_file(self, query: dict[str, Any]) -> list[dict]:
        """Collect from file-based logs."""
        # In production: tail/read log files
        return []

    async def health_check(self) -> bool:
        """Check if log source is reachable."""
        try:
            await self.collect({"limit": 1})
            return True
        except Exception:
            return False


class MetricsCollector(DataCollector):
    """Collects metrics from monitoring systems.

    Supported backends:
    - Prometheus
    - Datadog
    - AWS CloudWatch Metrics
    - InfluxDB
    """

    @property
    def source_type(self) -> DataSourceType:
        return DataSourceType.METRICS

    async def collect(self, query: dict[str, Any]) -> QueryResult:
        """Collect metrics matching the query.

        Query params:
        - metric_names: List of metric names
        - service: Service label filter
        - time_range: {"start": ISO8601, "end": ISO8601}
        - aggregation: sum, avg, max, min, p95, p99
        """
        start_time = time.time()

        backend = self.config.get("type", "prometheus")

        try:
            if backend == "prometheus":
                metrics = await self._collect_prometheus(query)
            elif backend == "datadog":
                metrics = await self._collect_datadog(query)
            elif backend == "cloudwatch":
                metrics = await self._collect_cloudwatch(query)
            else:
                metrics = {}

            duration_ms = int((time.time() - start_time) * 1000)

            return QueryResult(
                source_type=self.source_type,
                data={"metrics": metrics},
                redacted_fields=[],  # Metrics rarely need redaction
                query_duration_ms=duration_ms,
                bytes_processed=len(json.dumps(metrics)),
            )

        except Exception as e:
            log.exception("Metrics collection failed")
            return QueryResult(
                source_type=self.source_type,
                data={"metrics": {}},
                error=str(e),
                query_duration_ms=int((time.time() - start_time) * 1000),
            )

    async def _collect_prometheus(self, query: dict[str, Any]) -> dict[str, Any]:
        """Collect from Prometheus."""
        prom_url = self.config.get("url", "http://localhost:9090")

        metrics_result = {}
        metric_names = query.get("metric_names", [])

        for metric_name in metric_names:
            promql = metric_name
            if query.get("service"):
                promql = f'{metric_name}{{service="{query["service"]}"}}'

            # Simulate Prometheus response
            log.debug("Would query Prometheus at %s: %s", prom_url, promql)
            metrics_result[metric_name] = {
                "current": 0.0,
                "avg_1h": 0.0,
                "max_1h": 0.0,
                "min_1h": 0.0,
                "trend": "stable",
            }

        return metrics_result

    async def _collect_datadog(self, query: dict[str, Any]) -> dict[str, Any]:
        """Collect from Datadog."""
        # In production: use datadog API
        return {}

    async def _collect_cloudwatch(self, query: dict[str, Any]) -> dict[str, Any]:
        """Collect from AWS CloudWatch Metrics."""
        # In production: use boto3 cloudwatch client
        return {}

    async def health_check(self) -> bool:
        try:
            await self.collect({"metric_names": ["up"]})
            return True
        except Exception:
            return False


class CodeCollector(DataCollector):
    """Collects code-related information.

    Sources:
    - Git repositories
    - GitHub/GitLab APIs
    - Deployment systems (ArgoCD, Spinnaker)
    """

    @property
    def source_type(self) -> DataSourceType:
        return DataSourceType.CODE

    async def collect(self, query: dict[str, Any]) -> QueryResult:
        """Collect code information.

        Query params:
        - repo: Repository name
        - since: Time to look back (ISO8601)
        - types: ["deployments", "commits", "prs"]
        """
        start_time = time.time()

        try:
            results: dict[str, Any] = {}

            query_types = query.get("types", ["deployments", "commits"])

            if "deployments" in query_types:
                results["deployments"] = await self._get_deployments(query)

            if "commits" in query_types:
                results["commits"] = await self._get_commits(query)

            if "prs" in query_types:
                results["pull_requests"] = await self._get_prs(query)

            # Redact author emails, etc.
            redacted, redacted_fields = self.redaction.redact_dict(results)

            duration_ms = int((time.time() - start_time) * 1000)

            return QueryResult(
                source_type=self.source_type,
                data=redacted,
                redacted_fields=redacted_fields,
                query_duration_ms=duration_ms,
                bytes_processed=len(json.dumps(results)),
            )

        except Exception as e:
            log.exception("Code collection failed")
            return QueryResult(
                source_type=self.source_type,
                data={},
                error=str(e),
                query_duration_ms=int((time.time() - start_time) * 1000),
            )

    async def _get_deployments(self, query: dict[str, Any]) -> list[dict]:
        """Get recent deployments."""
        # In production: query ArgoCD, Spinnaker, or deployment API
        return [
            {
                "id": "deploy-sample",
                "service": query.get("repo", "unknown"),
                "sha": "abc123",
                "deployed_at": datetime.now(timezone.utc).isoformat(),
                "deployed_by": "user@example.com",
                "status": "success",
            }
        ]

    async def _get_commits(self, query: dict[str, Any]) -> list[dict]:
        """Get recent commits."""
        # In production: use git log or GitHub API
        return [
            {
                "sha": "abc123",
                "message": "Sample commit",
                "author": "user@example.com",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "files_changed": ["src/main.py"],
            }
        ]

    async def _get_prs(self, query: dict[str, Any]) -> list[dict]:
        """Get recent pull requests."""
        # In production: use GitHub/GitLab API
        return []

    async def health_check(self) -> bool:
        return True


class KubernetesCollector(DataCollector):
    """Collects Kubernetes state and events.

    Data collected:
    - Pod status and restarts
    - Deployment status
    - Events (warnings, errors)
    - Resource usage
    """

    @property
    def source_type(self) -> DataSourceType:
        return DataSourceType.KUBERNETES

    async def collect(self, query: dict[str, Any]) -> QueryResult:
        """Collect Kubernetes information.

        Query params:
        - namespace: K8s namespace
        - types: ["pods", "deployments", "events", "services"]
        - service: Filter by service label
        """
        start_time = time.time()

        try:
            results: dict[str, Any] = {}

            query_types = query.get("types", ["pods", "events"])

            if "pods" in query_types:
                results["pods"] = await self._get_pods(query)

            if "deployments" in query_types:
                results["deployments"] = await self._get_deployments(query)

            if "events" in query_types:
                results["events"] = await self._get_events(query)

            if "services" in query_types:
                results["services"] = await self._get_services(query)

            # Redact secrets, configmap values, etc.
            redacted, redacted_fields = self.redaction.redact_dict(results)

            duration_ms = int((time.time() - start_time) * 1000)

            return QueryResult(
                source_type=self.source_type,
                data=redacted,
                redacted_fields=redacted_fields,
                query_duration_ms=duration_ms,
                bytes_processed=len(json.dumps(results)),
            )

        except Exception as e:
            log.exception("Kubernetes collection failed")
            return QueryResult(
                source_type=self.source_type,
                data={},
                error=str(e),
                query_duration_ms=int((time.time() - start_time) * 1000),
            )

    async def _get_pods(self, query: dict[str, Any]) -> list[dict]:
        """Get pod status."""
        # In production: use kubernetes client
        namespace = query.get("namespace", "default")
        return [
            {
                "name": f"{query.get('service', 'app')}-pod-abc123",
                "namespace": namespace,
                "status": "Running",
                "restarts": 0,
                "ready": "1/1",
                "age": "2h",
            }
        ]

    async def _get_deployments(self, query: dict[str, Any]) -> list[dict]:
        """Get deployment status."""
        return []

    async def _get_events(self, query: dict[str, Any]) -> list[dict]:
        """Get recent events."""
        return [
            {
                "type": "Normal",
                "reason": "Scheduled",
                "message": "Successfully assigned pod",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        ]

    async def _get_services(self, query: dict[str, Any]) -> list[dict]:
        """Get service info."""
        return []

    async def health_check(self) -> bool:
        # In production: try to list namespaces
        return True


class TracesCollector(DataCollector):
    """Collects distributed traces.

    Supported backends:
    - Jaeger
    - Zipkin
    - Tempo
    """

    @property
    def source_type(self) -> DataSourceType:
        return DataSourceType.TRACES

    async def collect(self, query: dict[str, Any]) -> QueryResult:
        """Collect traces matching the query.

        Query params:
        - service: Service name
        - operation: Operation/endpoint name
        - time_range: {"start": ISO8601, "end": ISO8601}
        - min_duration_ms: Minimum trace duration
        - limit: Max traces to return
        """
        start_time = time.time()

        try:
            traces = await self._collect_traces(query)

            # Redact any sensitive data in spans
            redacted, redacted_fields = self.redaction.redact_dict({"traces": traces})

            duration_ms = int((time.time() - start_time) * 1000)

            return QueryResult(
                source_type=self.source_type,
                data=redacted,
                redacted_fields=redacted_fields,
                query_duration_ms=duration_ms,
                bytes_processed=len(json.dumps(traces)),
            )

        except Exception as e:
            log.exception("Traces collection failed")
            return QueryResult(
                source_type=self.source_type,
                data={"traces": []},
                error=str(e),
                query_duration_ms=int((time.time() - start_time) * 1000),
            )

    async def _collect_traces(self, query: dict[str, Any]) -> list[dict]:
        """Collect traces from backend."""
        # In production: use Jaeger/Zipkin/Tempo API
        return []

    async def health_check(self) -> bool:
        return True
