"""Ambient Monitor - Continuous proactive monitoring.

Watches telemetry streams and triggers investigations BEFORE
alerts fire, catching issues during the early warning window.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Awaitable
from uuid import uuid4

from app.config import get_settings
from app.monitoring.detectors import (
    AnomalyDetector,
    StatisticalDetector,
    ThresholdDetector,
    TrendDetector,
    PatternDetector,
    Anomaly,
)

log = logging.getLogger("devcopilot.monitoring.ambient")
_settings = get_settings()


@dataclass
class MonitorConfig:
    """Configuration for the ambient monitor."""
    # Polling
    poll_interval_seconds: float = 30.0
    lookback_minutes: int = 15

    # Services to monitor
    services: list[str] = field(default_factory=lambda: ["*"])  # ["*"] = all

    # Metrics to watch
    metrics: list[str] = field(default_factory=lambda: [
        "error_rate",
        "latency_p99",
        "latency_p95",
        "request_rate",
        "cpu_usage",
        "memory_usage",
    ])

    # Detection thresholds
    min_severity_to_alert: str = "medium"  # low, medium, high, critical
    min_confidence_to_alert: float = 0.7

    # Cooldown to prevent alert storms
    alert_cooldown_seconds: float = 300.0

    # Investigation trigger
    auto_investigate: bool = True
    investigation_cooldown_seconds: float = 600.0


@dataclass
class MonitorState:
    """Runtime state for the monitor."""
    running: bool = False
    last_poll: datetime | None = None
    anomalies_detected: int = 0
    investigations_triggered: int = 0
    last_alert_times: dict[str, datetime] = field(default_factory=dict)
    last_investigation_times: dict[str, datetime] = field(default_factory=dict)


@dataclass
class MetricData:
    """Time series data for a metric."""
    service: str
    metric_name: str
    values: list[float]
    timestamps: list[datetime]


class AmbientMonitor:
    """Continuous ambient monitoring for proactive issue detection.

    Watches metrics streams from all configured services and runs
    multiple detection algorithms to catch anomalies early.

    Features:
    - Statistical anomaly detection (z-score)
    - Threshold breach detection
    - Trend change detection
    - Pattern detection (error bursts, resource exhaustion)
    - Auto-triggers investigations before pages fire
    """

    SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}

    def __init__(
        self,
        config: MonitorConfig | None = None,
        metrics_fetcher: Callable[[str, str, int], Awaitable[MetricData]] | None = None,
        investigation_trigger: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ):
        """Initialize the ambient monitor.

        Args:
            config: Monitor configuration
            metrics_fetcher: Async function to fetch metrics (service, metric, minutes) -> MetricData
            investigation_trigger: Async function to trigger an investigation
        """
        self.config = config or MonitorConfig()
        self._metrics_fetcher = metrics_fetcher
        self._investigation_trigger = investigation_trigger

        # Initialize detectors with configurable thresholds
        self._detectors: list[AnomalyDetector] = [
            StatisticalDetector(z_threshold=_settings.anomaly_z_threshold),
            ThresholdDetector(),
            TrendDetector(),
            PatternDetector(),
        ]

        # State
        self._state = MonitorState()
        self._task: asyncio.Task | None = None

        log.info(
            "AmbientMonitor initialized: poll_interval=%.1fs, metrics=%d, services=%s",
            self.config.poll_interval_seconds,
            len(self.config.metrics),
            self.config.services,
        )

    @property
    def state(self) -> MonitorState:
        """Get current monitor state."""
        return self._state

    async def start(self) -> None:
        """Start the ambient monitor."""
        if self._state.running:
            log.warning("Monitor already running")
            return

        self._state.running = True
        self._task = asyncio.create_task(self._poll_loop())
        log.info("Ambient monitor started")

    async def stop(self) -> None:
        """Stop the ambient monitor."""
        self._state.running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        log.info("Ambient monitor stopped")

    async def _poll_loop(self) -> None:
        """Main polling loop."""
        while self._state.running:
            try:
                await self._poll_once()
                self._state.last_poll = datetime.now(timezone.utc)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.exception("Poll loop error: %s", e)

            await asyncio.sleep(self.config.poll_interval_seconds)

    async def _poll_once(self) -> None:
        """Execute one poll cycle."""
        if not self._metrics_fetcher:
            return

        services = await self._get_services()
        all_anomalies: list[Anomaly] = []

        for service in services:
            for metric in self.config.metrics:
                try:
                    data = await self._metrics_fetcher(
                        service, metric, self.config.lookback_minutes
                    )
                    anomalies = self._detect_anomalies(data)
                    all_anomalies.extend(anomalies)
                except Exception as e:
                    log.debug("Failed to fetch %s/%s: %s", service, metric, e)

        # Filter by severity and confidence
        significant = [
            a for a in all_anomalies
            if self._is_significant(a) and self._not_in_cooldown(a)
        ]

        if significant:
            self._state.anomalies_detected += len(significant)
            await self._handle_anomalies(significant)

    async def _get_services(self) -> list[str]:
        """Get list of services to monitor."""
        if "*" in self.config.services:
            # In production: fetch from service discovery
            return ["api-server", "auth-service", "payment-service", "user-service"]
        return self.config.services

    def _detect_anomalies(self, data: MetricData) -> list[Anomaly]:
        """Run all detectors on metric data."""
        anomalies = []

        for detector in self._detectors:
            try:
                detected = detector.detect(
                    metric_name=data.metric_name,
                    service=data.service,
                    values=data.values,
                    timestamps=data.timestamps,
                )
                anomalies.extend(detected)
            except Exception as e:
                log.debug("Detector %s failed: %s", detector.detector_type, e)

        return anomalies

    def _is_significant(self, anomaly: Anomaly) -> bool:
        """Check if anomaly meets severity/confidence thresholds."""
        min_severity = self.SEVERITY_ORDER.get(self.config.min_severity_to_alert, 1)
        anomaly_severity = self.SEVERITY_ORDER.get(anomaly.severity, 0)

        return (
            anomaly_severity >= min_severity and
            anomaly.confidence >= self.config.min_confidence_to_alert
        )

    def _not_in_cooldown(self, anomaly: Anomaly) -> bool:
        """Check if this anomaly type/service is not in cooldown."""
        key = f"{anomaly.service}:{anomaly.metric_name}:{anomaly.anomaly_type.value}"
        last_alert = self._state.last_alert_times.get(key)

        if last_alert is None:
            return True

        elapsed = (datetime.now(timezone.utc) - last_alert).total_seconds()
        return elapsed >= self.config.alert_cooldown_seconds

    async def _handle_anomalies(self, anomalies: list[Anomaly]) -> None:
        """Handle detected anomalies."""
        now = datetime.now(timezone.utc)

        # Group by service
        by_service: dict[str, list[Anomaly]] = {}
        for anomaly in anomalies:
            by_service.setdefault(anomaly.service, []).append(anomaly)

        for service, service_anomalies in by_service.items():
            # Update cooldown tracking
            for anomaly in service_anomalies:
                key = f"{anomaly.service}:{anomaly.metric_name}:{anomaly.anomaly_type.value}"
                self._state.last_alert_times[key] = now

            # Log anomalies
            log.warning(
                "Anomalies detected for %s: %s",
                service,
                [f"{a.anomaly_type.value}({a.metric_name})" for a in service_anomalies],
            )

            # Trigger investigation if enabled
            if self.config.auto_investigate:
                await self._maybe_trigger_investigation(service, service_anomalies)

    async def _maybe_trigger_investigation(
        self,
        service: str,
        anomalies: list[Anomaly],
    ) -> None:
        """Maybe trigger a proactive investigation."""
        if not self._investigation_trigger:
            return

        # Check investigation cooldown
        last = self._state.last_investigation_times.get(service)
        if last:
            elapsed = (datetime.now(timezone.utc) - last).total_seconds()
            if elapsed < self.config.investigation_cooldown_seconds:
                log.debug("Investigation for %s in cooldown", service)
                return

        # Only investigate high/critical anomalies
        high_severity = [
            a for a in anomalies
            if a.severity in ("high", "critical")
        ]

        if not high_severity:
            return

        # Build incident from anomalies
        incident = self._build_incident(service, high_severity)

        log.info(
            "Triggering proactive investigation for %s: %d anomalies",
            service, len(high_severity),
        )

        try:
            await self._investigation_trigger(incident)
            self._state.investigations_triggered += 1
            self._state.last_investigation_times[service] = datetime.now(timezone.utc)
        except Exception as e:
            log.exception("Failed to trigger investigation: %s", e)

    def _build_incident(
        self,
        service: str,
        anomalies: list[Anomaly],
    ) -> dict[str, Any]:
        """Build an incident payload from anomalies."""
        # Find highest severity
        max_severity = max(
            anomalies,
            key=lambda a: self.SEVERITY_ORDER.get(a.severity, 0),
        ).severity

        # Build summary
        summary_parts = [
            f"{a.anomaly_type.value}: {a.description}"
            for a in anomalies[:3]  # Top 3 anomalies
        ]
        summary = f"Proactive: {'; '.join(summary_parts)}"

        return {
            "id": f"ambient-{uuid4().hex[:8]}",
            "title": f"[Proactive] Anomalies detected in {service}",
            "summary": summary,
            "service": service,
            "severity": max_severity,
            "source": "ambient_monitor",
            "proactive": True,
            "anomalies": [a.to_dict() for a in anomalies],
            "detected_at": datetime.now(timezone.utc).isoformat(),
        }

    def get_stats(self) -> dict[str, Any]:
        """Get monitor statistics."""
        return {
            "running": self._state.running,
            "last_poll": self._state.last_poll.isoformat() if self._state.last_poll else None,
            "anomalies_detected": self._state.anomalies_detected,
            "investigations_triggered": self._state.investigations_triggered,
            "detectors": [d.detector_type for d in self._detectors],
            "services": self.config.services,
            "metrics": self.config.metrics,
        }

    async def check_now(self) -> list[Anomaly]:
        """Manually trigger a check (for testing/debugging)."""
        await self._poll_once()
        return []  # Anomalies are handled internally


async def create_ambient_monitor(
    investigation_trigger: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
) -> AmbientMonitor:
    """Factory function to create a configured ambient monitor.

    Connects to the appropriate metrics source based on environment.
    """
    async def default_metrics_fetcher(
        service: str,
        metric: str,
        minutes: int,
    ) -> MetricData:
        # In production: fetch from Prometheus/Datadog/etc
        # For now, return empty data
        return MetricData(
            service=service,
            metric_name=metric,
            values=[],
            timestamps=[],
        )

    return AmbientMonitor(
        metrics_fetcher=default_metrics_fetcher,
        investigation_trigger=investigation_trigger,
    )
