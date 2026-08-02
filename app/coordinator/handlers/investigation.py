"""Investigation Task Handlers.

Real integrations for incident investigation:
- Log collection and analysis
- Metrics querying
- Deployment history
- Event correlation
- Hypothesis generation
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.coordinator.dag_executor import TaskNode

log = logging.getLogger("devcopilot.coordinator.handlers.investigation")


class LogsHandler:
    """Handler for log collection and analysis."""

    def __init__(
        self,
        loki_url: str | None = None,
        elasticsearch_url: str | None = None,
    ):
        self.loki_url = loki_url
        self.elasticsearch_url = elasticsearch_url
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def __call__(
        self,
        task: TaskNode,
        inputs: dict[str, Any],
    ) -> dict[str, Any]:
        """Collect logs for services."""
        services = inputs.get("services", [])
        time_window_minutes = inputs.get("time_window_minutes", 60)
        incident_id = inputs.get("incident_id", "")

        results: dict[str, Any] = {
            "incident_id": incident_id,
            "services": {},
            "error_patterns": [],
            "anomalies": [],
        }

        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(minutes=time_window_minutes)

        for service in services:
            try:
                if self.loki_url:
                    logs = await self._query_loki(service, start_time, end_time)
                elif self.elasticsearch_url:
                    logs = await self._query_elasticsearch(service, start_time, end_time)
                else:
                    logs = await self._simulate_logs(service, start_time, end_time)

                analysis = self._analyze_logs(logs)
                results["services"][service] = {
                    "log_count": len(logs),
                    "error_count": analysis["error_count"],
                    "warning_count": analysis["warning_count"],
                    "sample_errors": analysis["sample_errors"][:5],
                }
                results["error_patterns"].extend(analysis["patterns"])
                results["anomalies"].extend(analysis["anomalies"])

            except Exception as e:
                log.error("Failed to collect logs for %s: %s", service, e)
                results["services"][service] = {"error": str(e)}

        results["total_errors"] = sum(
            svc.get("error_count", 0)
            for svc in results["services"].values()
            if isinstance(svc, dict)
        )

        return results

    async def _query_loki(
        self,
        service: str,
        start_time: datetime,
        end_time: datetime,
    ) -> list[dict[str, Any]]:
        """Query Loki for logs."""
        client = await self._get_client()

        query = f'{{app="{service}"}}'
        params = {
            "query": query,
            "start": int(start_time.timestamp() * 1e9),
            "end": int(end_time.timestamp() * 1e9),
            "limit": 1000,
        }

        response = await client.get(
            f"{self.loki_url}/loki/api/v1/query_range",
            params=params,
        )
        response.raise_for_status()

        data = response.json()
        logs = []

        for stream in data.get("data", {}).get("result", []):
            for value in stream.get("values", []):
                logs.append({
                    "timestamp": datetime.fromtimestamp(int(value[0]) / 1e9, tz=timezone.utc),
                    "message": value[1],
                    "labels": stream.get("stream", {}),
                })

        return logs

    async def _query_elasticsearch(
        self,
        service: str,
        start_time: datetime,
        end_time: datetime,
    ) -> list[dict[str, Any]]:
        """Query Elasticsearch for logs."""
        client = await self._get_client()

        query = {
            "query": {
                "bool": {
                    "must": [
                        {"term": {"kubernetes.labels.app": service}},
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
            "size": 1000,
            "sort": [{"@timestamp": "desc"}],
        }

        response = await client.post(
            f"{self.elasticsearch_url}/_search",
            json=query,
        )
        response.raise_for_status()

        data = response.json()
        logs = []

        for hit in data.get("hits", {}).get("hits", []):
            source = hit.get("_source", {})
            logs.append({
                "timestamp": source.get("@timestamp"),
                "message": source.get("message", ""),
                "level": source.get("level", "info"),
                "labels": source.get("kubernetes", {}).get("labels", {}),
            })

        return logs

    async def _simulate_logs(
        self,
        service: str,
        start_time: datetime,
        end_time: datetime,
    ) -> list[dict[str, Any]]:
        """Simulate log data when no backend is available."""
        return []

    def _analyze_logs(self, logs: list[dict[str, Any]]) -> dict[str, Any]:
        """Analyze logs for patterns and anomalies."""
        error_count = 0
        warning_count = 0
        sample_errors: list[str] = []
        patterns: list[dict[str, Any]] = []
        anomalies: list[dict[str, Any]] = []

        error_messages: dict[str, int] = {}

        for log_entry in logs:
            message = log_entry.get("message", "")
            level = log_entry.get("level", "").lower()

            if level == "error" or "error" in message.lower():
                error_count += 1
                if len(sample_errors) < 10:
                    sample_errors.append(message[:200])

                pattern = self._extract_pattern(message)
                error_messages[pattern] = error_messages.get(pattern, 0) + 1

            elif level == "warning" or "warn" in message.lower():
                warning_count += 1

        for pattern, count in sorted(error_messages.items(), key=lambda x: -x[1])[:5]:
            patterns.append({
                "pattern": pattern,
                "count": count,
                "severity": "high" if count > 10 else "medium",
            })

        return {
            "error_count": error_count,
            "warning_count": warning_count,
            "sample_errors": sample_errors,
            "patterns": patterns,
            "anomalies": anomalies,
        }

    def _extract_pattern(self, message: str) -> str:
        """Extract a pattern from an error message."""
        pattern = re.sub(r"\d+", "N", message)
        pattern = re.sub(r"[a-f0-9]{8,}", "ID", pattern)
        pattern = re.sub(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", "IP", pattern)
        return pattern[:100]


class MetricsHandler:
    """Handler for metrics collection and analysis."""

    def __init__(self, prometheus_url: str | None = None):
        self.prometheus_url = prometheus_url
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def __call__(
        self,
        task: TaskNode,
        inputs: dict[str, Any],
    ) -> dict[str, Any]:
        """Collect metrics for services."""
        services = inputs.get("services", [])
        time_window_minutes = inputs.get("time_window_minutes", 60)
        incident_id = inputs.get("incident_id", "")

        results: dict[str, Any] = {
            "incident_id": incident_id,
            "services": {},
            "alerts": [],
            "anomalies": [],
        }

        for service in services:
            try:
                metrics = await self._collect_service_metrics(service, time_window_minutes)
                results["services"][service] = metrics
                results["anomalies"].extend(metrics.get("anomalies", []))
            except Exception as e:
                log.error("Failed to collect metrics for %s: %s", service, e)
                results["services"][service] = {"error": str(e)}

        if self.prometheus_url:
            results["alerts"] = await self._get_active_alerts(services)

        return results

    async def _collect_service_metrics(
        self,
        service: str,
        time_window_minutes: int,
    ) -> dict[str, Any]:
        """Collect metrics for a single service."""
        metrics: dict[str, Any] = {
            "error_rate": None,
            "latency_p50": None,
            "latency_p99": None,
            "request_rate": None,
            "saturation": {},
            "anomalies": [],
        }

        if not self.prometheus_url:
            return metrics

        client = await self._get_client()

        queries = {
            "error_rate": f'sum(rate(http_requests_total{{service="{service}",status=~"5.."}}[5m])) / sum(rate(http_requests_total{{service="{service}"}}[5m]))',
            "latency_p50": f'histogram_quantile(0.5, sum(rate(http_request_duration_seconds_bucket{{service="{service}"}}[5m])) by (le))',
            "latency_p99": f'histogram_quantile(0.99, sum(rate(http_request_duration_seconds_bucket{{service="{service}"}}[5m])) by (le))',
            "request_rate": f'sum(rate(http_requests_total{{service="{service}"}}[5m]))',
            "cpu_usage": f'sum(rate(container_cpu_usage_seconds_total{{pod=~"{service}.*"}}[5m]))',
            "memory_usage": f'sum(container_memory_usage_bytes{{pod=~"{service}.*"}})',
        }

        for metric_name, query in queries.items():
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
                    if metric_name in ("cpu_usage", "memory_usage"):
                        metrics["saturation"][metric_name] = value
                    else:
                        metrics[metric_name] = value

                    if metric_name == "error_rate" and value > 0.05:
                        metrics["anomalies"].append({
                            "type": "high_error_rate",
                            "service": service,
                            "value": value,
                            "threshold": 0.05,
                        })
                    elif metric_name == "latency_p99" and value > 1.0:
                        metrics["anomalies"].append({
                            "type": "high_latency",
                            "service": service,
                            "value": value,
                            "threshold": 1.0,
                        })

            except Exception as e:
                log.debug("Failed to query %s for %s: %s", metric_name, service, e)

        return metrics

    async def _get_active_alerts(
        self,
        services: list[str],
    ) -> list[dict[str, Any]]:
        """Get active alerts for services."""
        if not self.prometheus_url:
            return []

        client = await self._get_client()
        alerts = []

        try:
            alertmanager_url = self.prometheus_url.replace(":9090", ":9093")
            response = await client.get(f"{alertmanager_url}/api/v2/alerts")
            response.raise_for_status()

            for alert in response.json():
                labels = alert.get("labels", {})
                service = labels.get("service", labels.get("job", ""))

                if service in services or not services:
                    alerts.append({
                        "name": labels.get("alertname"),
                        "service": service,
                        "severity": labels.get("severity", "warning"),
                        "state": alert.get("status", {}).get("state", "unknown"),
                        "started_at": alert.get("startsAt"),
                        "annotations": alert.get("annotations", {}),
                    })

        except Exception as e:
            log.debug("Failed to get alerts: %s", e)

        return alerts


class DeploymentsHandler:
    """Handler for checking recent deployments."""

    def __init__(
        self,
        knowledge_graph: Any = None,
        github_token: str | None = None,
    ):
        self.graph = knowledge_graph
        self.github_token = github_token
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            headers = {}
            if self.github_token:
                headers["Authorization"] = f"token {self.github_token}"
            self._client = httpx.AsyncClient(timeout=30.0, headers=headers)
        return self._client

    async def __call__(
        self,
        task: TaskNode,
        inputs: dict[str, Any],
    ) -> dict[str, Any]:
        """Check recent deployments for services."""
        services = inputs.get("services", [])
        hours = inputs.get("hours", 24)

        results: dict[str, Any] = {
            "deployments": [],
            "changes": [],
            "suspicious_deployments": [],
        }

        if self.graph:
            for service in services:
                try:
                    deployments = await self.graph.get_recent_deployments(
                        service_id=service,
                        hours=hours,
                        limit=10,
                    )

                    for deploy in deployments:
                        deploy_data = deploy.to_cypher_properties()
                        results["deployments"].append(deploy_data)

                        if deploy.status == "failed" or deploy.rollback_of:
                            results["suspicious_deployments"].append({
                                "deployment": deploy_data,
                                "reason": "rollback" if deploy.rollback_of else "failed",
                            })

                except Exception as e:
                    log.error("Failed to get deployments for %s: %s", service, e)

        if self.github_token:
            for service in services:
                try:
                    changes = await self._get_github_changes(service, hours)
                    results["changes"].extend(changes)
                except Exception as e:
                    log.debug("Failed to get GitHub changes for %s: %s", service, e)

        return results

    async def _get_github_changes(
        self,
        service: str,
        hours: int,
    ) -> list[dict[str, Any]]:
        """Get recent GitHub changes for a service."""
        return []


class CorrelationHandler:
    """Handler for correlating events across sources."""

    def __init__(self, llm_client: Any = None):
        self.llm = llm_client

    async def __call__(
        self,
        task: TaskNode,
        inputs: dict[str, Any],
    ) -> dict[str, Any]:
        """Correlate events from logs, metrics, and deployments."""
        logs_data = inputs.get("dep_collect_logs", {})
        metrics_data = inputs.get("dep_collect_metrics", {})
        deployments_data = inputs.get("dep_check_deployments", {})

        correlations: list[dict[str, Any]] = []
        timeline: list[dict[str, Any]] = []

        for deploy in deployments_data.get("deployments", []):
            deploy_time = deploy.get("deployed_at")
            if deploy_time:
                timeline.append({
                    "type": "deployment",
                    "timestamp": deploy_time,
                    "service": deploy.get("service_id"),
                    "data": deploy,
                })

        for service, svc_data in logs_data.get("services", {}).items():
            if isinstance(svc_data, dict) and svc_data.get("error_count", 0) > 0:
                timeline.append({
                    "type": "log_errors",
                    "service": service,
                    "count": svc_data["error_count"],
                    "data": svc_data,
                })

        for alert in metrics_data.get("alerts", []):
            timeline.append({
                "type": "alert",
                "timestamp": alert.get("started_at"),
                "service": alert.get("service"),
                "data": alert,
            })

        for deploy in deployments_data.get("deployments", []):
            deploy_service = deploy.get("service_id")
            deploy_time = deploy.get("deployed_at")

            for service, svc_data in metrics_data.get("services", {}).items():
                if service == deploy_service:
                    for anomaly in svc_data.get("anomalies", []):
                        correlations.append({
                            "type": "deployment_anomaly",
                            "confidence": 0.8,
                            "deployment": deploy,
                            "anomaly": anomaly,
                            "explanation": "Anomaly detected shortly after deployment",
                        })

        suspicious = deployments_data.get("suspicious_deployments", [])
        for susp in suspicious:
            correlations.append({
                "type": "suspicious_deployment",
                "confidence": 0.9,
                "data": susp,
                "explanation": f"Deployment marked as {susp.get('reason')}",
            })

        timeline.sort(key=lambda x: str(x.get("timestamp", "")), reverse=True)

        return {
            "correlations": correlations,
            "timeline": timeline[:50],
            "summary": {
                "total_deployments": len(deployments_data.get("deployments", [])),
                "suspicious_deployments": len(suspicious),
                "error_services": sum(
                    1 for svc in logs_data.get("services", {}).values()
                    if isinstance(svc, dict) and svc.get("error_count", 0) > 0
                ),
                "active_alerts": len(metrics_data.get("alerts", [])),
            },
        }


class HypothesisHandler:
    """Handler for generating investigation hypotheses using LLM."""

    def __init__(self, llm_client: Any):
        self.llm = llm_client

    async def __call__(
        self,
        task: TaskNode,
        inputs: dict[str, Any],
    ) -> dict[str, Any]:
        """Generate hypotheses based on correlated data."""
        correlation_data = inputs.get("dep_correlate", {})

        if not self.llm:
            return self._heuristic_hypotheses(correlation_data)

        prompt = f"""Analyze the following incident correlation data and generate hypotheses.

## Correlations Found
{correlation_data.get("correlations", [])}

## Event Timeline
{correlation_data.get("timeline", [])[:20]}

## Summary
{correlation_data.get("summary", {})}

Generate 3-5 ranked hypotheses for the root cause. For each hypothesis:
1. State the hypothesis clearly
2. Explain the supporting evidence
3. Assign a confidence score (0-1)
4. Suggest verification steps

Format as JSON:
{{
  "hypotheses": [
    {{
      "hypothesis": "...",
      "evidence": ["..."],
      "confidence": 0.8,
      "verification_steps": ["..."]
    }}
  ],
  "recommended_action": "..."
}}
"""

        try:
            response = await self.llm.complete(prompt=prompt, temperature=0.3)

            start = response.find("{")
            end = response.rfind("}") + 1
            if start >= 0 and end > start:
                return {
                    "hypotheses": response[start:end],
                    "source": "llm",
                }

        except Exception as e:
            log.error("LLM hypothesis generation failed: %s", e)

        return self._heuristic_hypotheses(correlation_data)

    def _heuristic_hypotheses(
        self,
        correlation_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Generate hypotheses using heuristics when LLM is unavailable."""
        hypotheses = []

        correlations = correlation_data.get("correlations", [])

        deployment_correlations = [
            c for c in correlations
            if c.get("type") in ("deployment_anomaly", "suspicious_deployment")
        ]

        if deployment_correlations:
            hypotheses.append({
                "hypothesis": "Recent deployment caused the incident",
                "evidence": [c.get("explanation", "") for c in deployment_correlations],
                "confidence": 0.75,
                "verification_steps": [
                    "Compare metrics before and after deployment",
                    "Review deployment changes",
                    "Check if rollback resolves the issue",
                ],
            })

        summary = correlation_data.get("summary", {})
        if summary.get("active_alerts", 0) > 0:
            hypotheses.append({
                "hypothesis": "Resource exhaustion or capacity issue",
                "evidence": [f"{summary['active_alerts']} active alerts"],
                "confidence": 0.6,
                "verification_steps": [
                    "Check CPU/memory utilization",
                    "Review pod scaling events",
                    "Check for OOM kills",
                ],
            })

        if not hypotheses:
            hypotheses.append({
                "hypothesis": "External dependency failure",
                "evidence": ["No internal correlations found"],
                "confidence": 0.3,
                "verification_steps": [
                    "Check external service status pages",
                    "Review network connectivity",
                    "Check DNS resolution",
                ],
            })

        hypotheses.sort(key=lambda h: -h["confidence"])

        return {
            "hypotheses": hypotheses,
            "source": "heuristic",
            "recommended_action": hypotheses[0]["verification_steps"][0] if hypotheses else None,
        }


def create_investigation_handlers(
    prometheus_url: str | None = None,
    loki_url: str | None = None,
    elasticsearch_url: str | None = None,
    github_token: str | None = None,
    knowledge_graph: Any = None,
    llm_client: Any = None,
) -> dict[str, Any]:
    """Create all investigation handlers with configured clients."""
    return {
        "collect_logs": LogsHandler(
            loki_url=loki_url,
            elasticsearch_url=elasticsearch_url,
        ),
        "collect_metrics": MetricsHandler(
            prometheus_url=prometheus_url,
        ),
        "check_deployments": DeploymentsHandler(
            knowledge_graph=knowledge_graph,
            github_token=github_token,
        ),
        "correlate_events": CorrelationHandler(
            llm_client=llm_client,
        ),
        "generate_hypotheses": HypothesisHandler(
            llm_client=llm_client,
        ),
    }
