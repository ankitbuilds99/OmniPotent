"""Evidence Collection for Verification.

Collects evidence from multiple sources:
- Prometheus/Metrics
- Logs (Loki/Elasticsearch)
- Kubernetes
- Git/GitHub
- Knowledge Graph
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

import httpx

log = logging.getLogger("devcopilot.verification.evidence_collector")


class EvidenceSource(str, Enum):
    METRICS = "metrics"
    LOGS = "logs"
    GIT = "git"
    KUBERNETES = "kubernetes"
    KNOWLEDGE_GRAPH = "knowledge_graph"
    TRACES = "traces"
    EXTERNAL = "external"


@dataclass
class Evidence:
    """Evidence supporting or refuting a claim."""
    source: EvidenceSource
    data: Any
    timestamp: datetime
    confidence: float

    metric_name: str | None = None
    metric_value: float | None = None

    log_lines: list[str] = field(default_factory=list)

    commit_sha: str | None = None
    author: str | None = None

    resource_type: str | None = None
    resource_name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source.value,
            "timestamp": self.timestamp.isoformat(),
            "confidence": self.confidence,
            "metric_name": self.metric_name,
            "metric_value": self.metric_value,
            "log_lines_count": len(self.log_lines),
            "commit_sha": self.commit_sha,
        }


class EvidenceCollector:
    """Collects evidence from various sources for verification."""

    def __init__(
        self,
        prometheus_url: str | None = None,
        loki_url: str | None = None,
        elasticsearch_url: str | None = None,
        k8s_client: Any = None,
        github_token: str | None = None,
        knowledge_graph: Any = None,
    ):
        self.prometheus_url = prometheus_url
        self.loki_url = loki_url
        self.elasticsearch_url = elasticsearch_url
        self.k8s = k8s_client
        self.github_token = github_token
        self.graph = knowledge_graph
        self._http_client: httpx.AsyncClient | None = None

    async def _get_http_client(self) -> httpx.AsyncClient:
        if self._http_client is None:
            headers = {}
            if self.github_token:
                headers["Authorization"] = f"token {self.github_token}"
            self._http_client = httpx.AsyncClient(timeout=30.0, headers=headers)
        return self._http_client

    async def close(self) -> None:
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None

    async def collect_evidence(
        self,
        claim_text: str,
        service_id: str | None = None,
        incident_id: str | None = None,
        time_window_minutes: int = 60,
        sources: list[EvidenceSource] | None = None,
    ) -> list[Evidence]:
        """Collect all relevant evidence for a claim."""
        if sources is None:
            sources = list(EvidenceSource)

        tasks = []

        if EvidenceSource.METRICS in sources:
            tasks.append(self._collect_metric_evidence(
                claim_text, service_id, time_window_minutes
            ))

        if EvidenceSource.LOGS in sources:
            tasks.append(self._collect_log_evidence(
                claim_text, service_id, time_window_minutes
            ))

        if EvidenceSource.KUBERNETES in sources and self.k8s:
            tasks.append(self._collect_k8s_evidence(
                claim_text, service_id, time_window_minutes
            ))

        if EvidenceSource.GIT in sources:
            tasks.append(self._collect_git_evidence(
                claim_text, service_id, time_window_minutes
            ))

        if EvidenceSource.KNOWLEDGE_GRAPH in sources and self.graph:
            tasks.append(self._collect_graph_evidence(
                claim_text, service_id, incident_id, time_window_minutes
            ))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        evidence: list[Evidence] = []
        for result in results:
            if isinstance(result, list):
                evidence.extend(result)
            elif isinstance(result, Exception):
                log.debug("Evidence collection failed: %s", result)

        return evidence

    async def _collect_metric_evidence(
        self,
        claim_text: str,
        service_id: str | None,
        time_window: int,
    ) -> list[Evidence]:
        """Collect metric evidence from Prometheus."""
        evidence: list[Evidence] = []

        if not self.prometheus_url or not service_id:
            return evidence

        queries = self._infer_relevant_metrics(claim_text, service_id)
        client = await self._get_http_client()

        for query_name, query in queries.items():
            try:
                response = await client.get(
                    f"{self.prometheus_url}/api/v1/query",
                    params={"query": query},
                )
                response.raise_for_status()

                data = response.json()
                result = data.get("data", {}).get("result", [])

                if result:
                    value = float(result[0]["value"][1])
                    evidence.append(Evidence(
                        source=EvidenceSource.METRICS,
                        data=result,
                        timestamp=datetime.now(timezone.utc),
                        confidence=0.9,
                        metric_name=query_name,
                        metric_value=value,
                    ))

            except Exception as e:
                log.debug("Failed to query metric %s: %s", query_name, e)

        return evidence

    async def _collect_log_evidence(
        self,
        claim_text: str,
        service_id: str | None,
        time_window: int,
    ) -> list[Evidence]:
        """Collect log evidence."""
        evidence: list[Evidence] = []

        if not service_id:
            return evidence

        keywords = self._extract_keywords(claim_text)
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(minutes=time_window)

        if self.loki_url:
            evidence.extend(await self._query_loki(
                service_id, keywords, start_time, end_time
            ))
        elif self.elasticsearch_url:
            evidence.extend(await self._query_elasticsearch(
                service_id, keywords, start_time, end_time
            ))

        return evidence

    async def _query_loki(
        self,
        service_id: str,
        keywords: list[str],
        start_time: datetime,
        end_time: datetime,
    ) -> list[Evidence]:
        """Query Loki for log evidence."""
        evidence: list[Evidence] = []
        client = await self._get_http_client()

        for keyword in keywords[:3]:
            try:
                query = f'{{app="{service_id}"}} |= "{keyword}"'
                params: dict[str, str | int] = {
                    "query": query,
                    "start": int(start_time.timestamp() * 1e9),
                    "end": int(end_time.timestamp() * 1e9),
                    "limit": 100,
                }

                response = await client.get(
                    f"{self.loki_url}/loki/api/v1/query_range",
                    params=params,
                )
                response.raise_for_status()

                data = response.json()
                log_lines: list[str] = []

                for stream in data.get("data", {}).get("result", []):
                    for value in stream.get("values", []):
                        log_lines.append(value[1])

                if log_lines:
                    evidence.append(Evidence(
                        source=EvidenceSource.LOGS,
                        data={"keyword": keyword, "count": len(log_lines)},
                        timestamp=datetime.now(timezone.utc),
                        confidence=0.8,
                        log_lines=log_lines[:10],
                    ))

            except Exception as e:
                log.debug("Loki query failed for %s: %s", keyword, e)

        return evidence

    async def _query_elasticsearch(
        self,
        service_id: str,
        keywords: list[str],
        start_time: datetime,
        end_time: datetime,
    ) -> list[Evidence]:
        """Query Elasticsearch for log evidence."""
        evidence: list[Evidence] = []
        client = await self._get_http_client()

        for keyword in keywords[:3]:
            try:
                query = {
                    "query": {
                        "bool": {
                            "must": [
                                {"term": {"kubernetes.labels.app": service_id}},
                                {"match": {"message": keyword}},
                                {
                                    "range": {
                                        "@timestamp": {
                                            "gte": start_time.isoformat(),
                                            "lte": end_time.isoformat(),
                                        }
                                    }
                                },
                            ]
                        }
                    },
                    "size": 100,
                }

                response = await client.post(
                    f"{self.elasticsearch_url}/_search",
                    json=query,
                )
                response.raise_for_status()

                data = response.json()
                hits = data.get("hits", {}).get("hits", [])

                if hits:
                    log_lines = [h["_source"].get("message", "") for h in hits]
                    evidence.append(Evidence(
                        source=EvidenceSource.LOGS,
                        data={"keyword": keyword, "count": len(hits)},
                        timestamp=datetime.now(timezone.utc),
                        confidence=0.8,
                        log_lines=log_lines[:10],
                    ))

            except Exception as e:
                log.debug("Elasticsearch query failed for %s: %s", keyword, e)

        return evidence

    async def _collect_k8s_evidence(
        self,
        claim_text: str,
        service_id: str | None,
        time_window: int,
    ) -> list[Evidence]:
        """Collect Kubernetes state evidence."""
        evidence: list[Evidence] = []

        if not self.k8s or not service_id:
            return evidence

        try:
            deployment = await self.k8s.read_namespaced_deployment(
                name=service_id,
                namespace="default",
            )

            evidence.append(Evidence(
                source=EvidenceSource.KUBERNETES,
                data={
                    "replicas": deployment.spec.replicas,
                    "ready_replicas": deployment.status.ready_replicas,
                    "available_replicas": deployment.status.available_replicas,
                    "image": deployment.spec.template.spec.containers[0].image,
                },
                timestamp=datetime.now(timezone.utc),
                confidence=0.95,
                resource_type="Deployment",
                resource_name=service_id,
            ))

        except Exception as e:
            log.debug("Failed to get deployment %s: %s", service_id, e)

        try:
            events = await self.k8s.list_namespaced_event(
                namespace="default",
                field_selector=f"involvedObject.name={service_id}",
            )

            warning_events = [
                e for e in events.items
                if e.type != "Normal"
            ]

            if warning_events:
                evidence.append(Evidence(
                    source=EvidenceSource.KUBERNETES,
                    data={
                        "event_count": len(warning_events),
                        "events": [
                            {
                                "reason": e.reason,
                                "message": e.message,
                                "count": e.count,
                            }
                            for e in warning_events[:5]
                        ],
                    },
                    timestamp=datetime.now(timezone.utc),
                    confidence=0.9,
                    resource_type="Event",
                ))

        except Exception as e:
            log.debug("Failed to get events for %s: %s", service_id, e)

        return evidence

    async def _collect_git_evidence(
        self,
        claim_text: str,
        service_id: str | None,
        time_window: int,
    ) -> list[Evidence]:
        """Collect Git/GitHub evidence."""
        evidence: list[Evidence] = []

        if not self.github_token or not service_id:
            return evidence

        return evidence

    async def _collect_graph_evidence(
        self,
        claim_text: str,
        service_id: str | None,
        incident_id: str | None,
        time_window: int,
    ) -> list[Evidence]:
        """Collect knowledge graph evidence."""
        evidence: list[Evidence] = []

        if not self.graph:
            return evidence

        try:
            if incident_id:
                changes = await self.graph.find_changes_before_incident(
                    incident_id,
                    window_hours=time_window // 60,
                )

                if changes:
                    evidence.append(Evidence(
                        source=EvidenceSource.KNOWLEDGE_GRAPH,
                        data={
                            "type": "correlated_changes",
                            "count": len(changes),
                            "changes": [c.to_cypher_properties() for c in changes[:5]],
                        },
                        timestamp=datetime.now(timezone.utc),
                        confidence=0.8,
                    ))

            if service_id:
                deployments = await self.graph.get_recent_deployments(
                    service_id=service_id,
                    hours=time_window // 60,
                    limit=5,
                )

                if deployments:
                    evidence.append(Evidence(
                        source=EvidenceSource.KNOWLEDGE_GRAPH,
                        data={
                            "type": "recent_deployments",
                            "count": len(deployments),
                            "deployments": [d.to_cypher_properties() for d in deployments],
                        },
                        timestamp=datetime.now(timezone.utc),
                        confidence=0.85,
                    ))

        except Exception as e:
            log.debug("Knowledge graph query failed: %s", e)

        return evidence

    def _infer_relevant_metrics(
        self,
        claim_text: str,
        service_id: str,
    ) -> dict[str, str]:
        """Infer which metrics are relevant to a claim."""
        metrics: dict[str, str] = {}
        text_lower = claim_text.lower()

        if "error" in text_lower or "fail" in text_lower:
            metrics["error_rate"] = f'sum(rate(http_requests_total{{service="{service_id}",status=~"5.."}}[5m])) / sum(rate(http_requests_total{{service="{service_id}"}}[5m]))'

        if "latency" in text_lower or "slow" in text_lower or "response" in text_lower:
            metrics["latency_p99"] = f'histogram_quantile(0.99, sum(rate(http_request_duration_seconds_bucket{{service="{service_id}"}}[5m])) by (le))'

        if "cpu" in text_lower:
            metrics["cpu_usage"] = f'sum(rate(container_cpu_usage_seconds_total{{pod=~"{service_id}.*"}}[5m]))'

        if "memory" in text_lower or "oom" in text_lower:
            metrics["memory_usage"] = f'sum(container_memory_usage_bytes{{pod=~"{service_id}.*"}})'

        if "traffic" in text_lower or "request" in text_lower:
            metrics["request_rate"] = f'sum(rate(http_requests_total{{service="{service_id}"}}[5m]))'

        if not metrics:
            metrics["error_rate"] = f'sum(rate(http_requests_total{{service="{service_id}",status=~"5.."}}[5m])) / sum(rate(http_requests_total{{service="{service_id}"}}[5m]))'
            metrics["latency_p99"] = f'histogram_quantile(0.99, sum(rate(http_request_duration_seconds_bucket{{service="{service_id}"}}[5m])) by (le))'

        return metrics

    def _extract_keywords(self, text: str) -> list[str]:
        """Extract search keywords from claim text."""
        stopwords = {
            "the", "a", "an", "is", "are", "was", "were", "been", "being",
            "have", "has", "had", "do", "does", "did", "will", "would",
            "could", "should", "may", "might", "must", "to", "from", "in",
            "on", "at", "by", "for", "with", "about", "after", "before",
            "this", "that", "these", "those", "it", "its", "and", "or",
        }

        words = re.findall(r"\b\w+\b", text.lower())
        keywords = [w for w in words if w not in stopwords and len(w) > 2]

        return sorted(set(keywords), key=len, reverse=True)[:5]
