"""VPC Connector - Main connector deployed inside customer VPC.

This is the core component that:
1. Collects data from local sources (logs, metrics, code, k8s)
2. Redacts sensitive information
3. Sends only redacted evidence to cloud control plane
4. Maintains secure connection to control plane
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from app.vpc_connector.redaction import RedactionEngine, create_redaction_engine
from app.vpc_connector.collectors import (
    DataCollector,
    DataSourceType,
    LogCollector,
    MetricsCollector,
    CodeCollector,
    KubernetesCollector,
    TracesCollector,
    QueryResult,
)

log = logging.getLogger("devcopilot.vpc_connector.connector")


@dataclass
class ConnectorConfig:
    """Configuration for the VPC connector."""
    connector_id: str
    organization_id: str
    control_plane_url: str
    connector_token: str

    # Data source configs
    logs_config: dict[str, Any] = field(default_factory=dict)
    metrics_config: dict[str, Any] = field(default_factory=dict)
    code_config: dict[str, Any] = field(default_factory=dict)
    kubernetes_config: dict[str, Any] = field(default_factory=dict)
    traces_config: dict[str, Any] = field(default_factory=dict)

    # Features
    logs_enabled: bool = True
    metrics_enabled: bool = True
    code_enabled: bool = True
    kubernetes_enabled: bool = False
    traces_enabled: bool = False

    # Redaction
    custom_redaction_patterns: list[dict[str, Any]] = field(default_factory=list)

    # Connection settings
    heartbeat_interval_seconds: int = 30
    reconnect_delay_seconds: int = 5
    max_reconnect_attempts: int = 10


@dataclass
class EvidencePackage:
    """Package of evidence to send to cloud (after redaction)."""
    incident_id: str
    organization_id: str
    timestamp: str
    evidence: dict[str, Any]
    evidence_hash: str
    redacted_fields: list[str]
    collection_duration_ms: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "incident_id": self.incident_id,
            "organization_id": self.organization_id,
            "timestamp": self.timestamp,
            "evidence": self.evidence,
            "evidence_hash": self.evidence_hash,
            "redacted_fields": self.redacted_fields,
            "collection_duration_ms": self.collection_duration_ms,
        }


@dataclass
class ConnectorStatus:
    """Current status of the connector."""
    connector_id: str
    status: str  # connecting, connected, disconnected, error
    version: str
    uptime_seconds: int
    last_heartbeat: str | None
    collectors: dict[str, bool]
    stats: dict[str, Any]


class VPCConnector:
    """Main VPC Connector deployed inside customer environment.

    Responsibilities:
    1. Collect data from local sources (logs, metrics, code, k8s)
    2. Redact sensitive information before transmission
    3. Send only redacted evidence to cloud control plane
    4. Maintain secure websocket connection to control plane
    5. Handle investigation requests from control plane
    """

    VERSION = "1.0.0"

    def __init__(self, config: ConnectorConfig):
        """Initialize the VPC connector.

        Args:
            config: Connector configuration
        """
        self.config = config
        self.connector_id = config.connector_id
        self.organization_id = config.organization_id

        # Initialize redaction engine
        self.redaction = create_redaction_engine({
            "custom_patterns": config.custom_redaction_patterns,
        })

        # Initialize collectors
        self.collectors: dict[DataSourceType, DataCollector] = {}
        self._init_collectors()

        # State
        self.running = False
        self.connected = False
        self.start_time: float | None = None
        self.last_heartbeat: datetime | None = None

        # Stats
        self.total_queries = 0
        self.total_bytes_processed = 0
        self.total_evidence_packages = 0

        log.info(
            "VPCConnector initialized: connector_id=%s org=%s",
            self.connector_id, self.organization_id,
        )

    def _init_collectors(self) -> None:
        """Initialize data source collectors based on config."""
        if self.config.logs_enabled:
            self.collectors[DataSourceType.LOGS] = LogCollector(
                config=self.config.logs_config,
                redaction=self.redaction,
            )

        if self.config.metrics_enabled:
            self.collectors[DataSourceType.METRICS] = MetricsCollector(
                config=self.config.metrics_config,
                redaction=self.redaction,
            )

        if self.config.code_enabled:
            self.collectors[DataSourceType.CODE] = CodeCollector(
                config=self.config.code_config,
                redaction=self.redaction,
            )

        if self.config.kubernetes_enabled:
            self.collectors[DataSourceType.KUBERNETES] = KubernetesCollector(
                config=self.config.kubernetes_config,
                redaction=self.redaction,
            )

        if self.config.traces_enabled:
            self.collectors[DataSourceType.TRACES] = TracesCollector(
                config=self.config.traces_config,
                redaction=self.redaction,
            )

        log.info(
            "Initialized %d collectors: %s",
            len(self.collectors),
            list(self.collectors.keys()),
        )

    async def query(
        self,
        source_type: DataSourceType,
        query: dict[str, Any],
    ) -> QueryResult:
        """Execute a query against a data source.

        All results are automatically redacted.

        Args:
            source_type: Type of data source to query
            query: Query parameters

        Returns:
            QueryResult with redacted data
        """
        collector = self.collectors.get(source_type)
        if not collector:
            return QueryResult(
                source_type=source_type,
                data={},
                error=f"Data source {source_type.value} not configured",
            )

        result = await collector.collect(query)

        self.total_queries += 1
        self.total_bytes_processed += result.bytes_processed

        return result

    async def collect_incident_evidence(
        self,
        incident_id: str,
        service: str,
        time_range: dict[str, str],
        severity: str = "medium",
    ) -> EvidencePackage:
        """Collect all evidence for an incident.

        This is called when an incident is triggered to gather
        context for the investigation agents.

        Args:
            incident_id: ID of the incident
            service: Primary service to investigate
            time_range: {"start": ISO8601, "end": ISO8601}
            severity: Incident severity for prioritization

        Returns:
            EvidencePackage ready to send to control plane
        """
        start_time = time.time()
        evidence: dict[str, Any] = {}
        all_redacted_fields: list[str] = []

        # Collect from all enabled sources in parallel
        tasks = []

        if DataSourceType.LOGS in self.collectors:
            tasks.append(self._collect_logs(service, time_range))

        if DataSourceType.METRICS in self.collectors:
            tasks.append(self._collect_metrics(service, time_range))

        if DataSourceType.CODE in self.collectors:
            tasks.append(self._collect_code(service, time_range))

        if DataSourceType.KUBERNETES in self.collectors:
            tasks.append(self._collect_kubernetes(service))

        if DataSourceType.TRACES in self.collectors:
            tasks.append(self._collect_traces(service, time_range))

        # Execute all collections in parallel
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Process results
        source_types = list(self.collectors.keys())
        for i, result in enumerate(results):
            source = source_types[i] if i < len(source_types) else DataSourceType.LOGS

            if isinstance(result, Exception):
                log.error("Collection failed for %s: %s", source.value, result)
                evidence[source.value] = {"error": str(result)}
            elif isinstance(result, QueryResult):
                evidence[source.value] = result.data
                all_redacted_fields.extend(result.redacted_fields)

        # Calculate hash for integrity verification
        evidence_json = json.dumps(evidence, sort_keys=True, default=str)
        evidence_hash = hmac.new(
            self.config.connector_token.encode(),
            evidence_json.encode(),
            hashlib.sha256,
        ).hexdigest()

        collection_duration_ms = int((time.time() - start_time) * 1000)

        self.total_evidence_packages += 1

        log.info(
            "Collected evidence for incident %s: %d sources, %d redacted fields, %dms",
            incident_id, len(evidence), len(all_redacted_fields), collection_duration_ms,
        )

        return EvidencePackage(
            incident_id=incident_id,
            organization_id=self.organization_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            evidence=evidence,
            evidence_hash=evidence_hash,
            redacted_fields=list(set(all_redacted_fields)),
            collection_duration_ms=collection_duration_ms,
        )

    async def _collect_logs(
        self,
        service: str,
        time_range: dict[str, str],
    ) -> QueryResult:
        """Collect logs for a service."""
        return await self.query(DataSourceType.LOGS, {
            "service": service,
            "level": "error",
            "time_range": time_range,
            "limit": 100,
        })

    async def _collect_metrics(
        self,
        service: str,
        time_range: dict[str, str],
    ) -> QueryResult:
        """Collect metrics for a service."""
        return await self.query(DataSourceType.METRICS, {
            "metric_names": [
                f"{service}_latency_p95",
                f"{service}_latency_p99",
                f"{service}_error_rate",
                f"{service}_request_rate",
                "cpu_usage",
                "memory_usage",
            ],
            "service": service,
            "time_range": time_range,
        })

    async def _collect_code(
        self,
        service: str,
        time_range: dict[str, str],
    ) -> QueryResult:
        """Collect code changes for a service."""
        return await self.query(DataSourceType.CODE, {
            "repo": service,
            "since": time_range.get("start"),
            "types": ["deployments", "commits"],
        })

    async def _collect_kubernetes(self, service: str) -> QueryResult:
        """Collect Kubernetes state for a service."""
        return await self.query(DataSourceType.KUBERNETES, {
            "namespace": service,
            "types": ["pods", "events", "deployments"],
            "service": service,
        })

    async def _collect_traces(
        self,
        service: str,
        time_range: dict[str, str],
    ) -> QueryResult:
        """Collect traces for a service."""
        return await self.query(DataSourceType.TRACES, {
            "service": service,
            "time_range": time_range,
            "min_duration_ms": 1000,  # Slow traces
            "limit": 20,
        })

    async def start(self) -> None:
        """Start the connector and connect to control plane."""
        log.info("Starting VPC Connector %s", self.connector_id)
        self.running = True
        self.start_time = time.time()

        # Health check all collectors
        for source_type, collector in self.collectors.items():
            healthy = await collector.health_check()
            status = "OK" if healthy else "FAILED"
            log.info("  %s: %s", source_type.value, status)

        # Start background tasks
        asyncio.create_task(self._heartbeat_loop())
        asyncio.create_task(self._connect_control_plane())

    async def stop(self) -> None:
        """Stop the connector."""
        log.info("Stopping VPC Connector %s", self.connector_id)
        self.running = False
        self.connected = False

    async def _heartbeat_loop(self) -> None:
        """Send periodic heartbeats to control plane."""
        while self.running:
            try:
                await self._send_heartbeat()
                self.last_heartbeat = datetime.now(timezone.utc)
            except Exception as e:
                log.warning("Heartbeat failed: %s", e)

            await asyncio.sleep(self.config.heartbeat_interval_seconds)

    async def _send_heartbeat(self) -> None:
        """Send heartbeat to control plane."""
        heartbeat = {
            "connector_id": self.connector_id,
            "organization_id": self.organization_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "version": self.VERSION,
            "stats": {
                "total_queries": self.total_queries,
                "total_bytes_processed": self.total_bytes_processed,
                "total_evidence_packages": self.total_evidence_packages,
                "uptime_seconds": int(time.time() - (self.start_time or 0)),
            },
            "collectors": {
                source_type.value: await collector.health_check()
                for source_type, collector in self.collectors.items()
            },
        }

        # In production: send via websocket to control plane
        log.debug("Heartbeat: %s", heartbeat)

    async def _connect_control_plane(self) -> None:
        """Connect to control plane websocket."""
        attempts = 0

        while self.running and attempts < self.config.max_reconnect_attempts:
            try:
                # In production: establish websocket connection
                # ws = await websockets.connect(self.config.control_plane_url)
                # await self._handle_connection(ws)

                self.connected = True
                log.info("Connected to control plane")

                # Keep connection alive (simulated)
                while self.running:
                    await asyncio.sleep(1)

            except Exception as e:
                attempts += 1
                log.warning(
                    "Control plane connection failed (attempt %d/%d): %s",
                    attempts, self.config.max_reconnect_attempts, e,
                )
                await asyncio.sleep(self.config.reconnect_delay_seconds)

        if not self.running:
            return

        log.error("Max reconnection attempts reached, connector stopping")
        self.running = False

    def get_status(self) -> ConnectorStatus:
        """Get current connector status."""
        return ConnectorStatus(
            connector_id=self.connector_id,
            status="connected" if self.connected else "disconnected",
            version=self.VERSION,
            uptime_seconds=int(time.time() - (self.start_time or 0)),
            last_heartbeat=self.last_heartbeat.isoformat() if self.last_heartbeat else None,
            collectors={
                source_type.value: True
                for source_type in self.collectors.keys()
            },
            stats={
                "total_queries": self.total_queries,
                "total_bytes_processed": self.total_bytes_processed,
                "total_evidence_packages": self.total_evidence_packages,
            },
        )


def create_connector_from_env() -> VPCConnector:
    """Create a connector from environment variables.

    Expected env vars:
    - DEVCOPILOT_CONNECTOR_ID
    - DEVCOPILOT_ORGANIZATION_ID
    - DEVCOPILOT_CONNECTOR_TOKEN
    - DEVCOPILOT_CONTROL_PLANE_URL
    - DEVCOPILOT_LOG_SOURCE_TYPE (elasticsearch, loki, cloudwatch)
    - DEVCOPILOT_LOG_SOURCE_URL
    - DEVCOPILOT_METRICS_SOURCE_TYPE (prometheus, datadog)
    - DEVCOPILOT_METRICS_SOURCE_URL
    """
    import os

    config = ConnectorConfig(
        connector_id=os.environ["DEVCOPILOT_CONNECTOR_ID"],
        organization_id=os.environ["DEVCOPILOT_ORGANIZATION_ID"],
        connector_token=os.environ["DEVCOPILOT_CONNECTOR_TOKEN"],
        control_plane_url=os.environ.get(
            "DEVCOPILOT_CONTROL_PLANE_URL",
            "wss://api.devops-copilot.com/connector",
        ),
        logs_config={
            "type": os.environ.get("DEVCOPILOT_LOG_SOURCE_TYPE", "elasticsearch"),
            "url": os.environ.get("DEVCOPILOT_LOG_SOURCE_URL", "http://localhost:9200"),
        },
        metrics_config={
            "type": os.environ.get("DEVCOPILOT_METRICS_SOURCE_TYPE", "prometheus"),
            "url": os.environ.get("DEVCOPILOT_METRICS_SOURCE_URL", "http://localhost:9090"),
        },
        kubernetes_enabled=os.environ.get("DEVCOPILOT_KUBERNETES_ENABLED", "false").lower() == "true",
    )

    return VPCConnector(config)
