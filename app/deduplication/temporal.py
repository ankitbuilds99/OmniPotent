"""Layer 3: Temporal correlation and cascade detection."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from app.deduplication.models import (
    RawAlert,
    TemporalWindow,
    WindowConfidence,
    CascadeChain,
)

log = logging.getLogger("devcopilot.deduplication.temporal")


@dataclass
class TemporalConfig:
    """Configuration for temporal correlation."""

    tight_window_seconds: int = 30
    medium_window_seconds: int = 300
    loose_window_seconds: int = 900
    cascade_max_delay_seconds: int = 300
    cascade_min_confidence: float = 0.7
    require_service_relation: bool = True
    auto_group_tight_window: bool = True


@dataclass
class TimeWindowMatch:
    """Result of time window matching."""

    window_type: str
    confidence: WindowConfidence
    matched_alerts: list[UUID]
    incident_id: UUID | None
    time_span_seconds: float
    first_alert_time: datetime
    last_alert_time: datetime


@dataclass
class AlertTimeEntry:
    """Alert entry in temporal index."""

    alert_id: UUID
    timestamp: datetime
    service: str
    incident_id: UUID | None = None
    fingerprint: str | None = None


class TimeWindowManager:
    """Manage time windows for correlation."""

    def __init__(self, config: TemporalConfig | None = None):
        self.config = config or TemporalConfig()

    def get_window_for_delay(self, delay_seconds: float) -> tuple[str, WindowConfidence]:
        """Determine window type and confidence for a delay."""
        if delay_seconds <= self.config.tight_window_seconds:
            return "tight", WindowConfidence.HIGH
        elif delay_seconds <= self.config.medium_window_seconds:
            return "medium", WindowConfidence.MEDIUM
        elif delay_seconds <= self.config.loose_window_seconds:
            return "loose", WindowConfidence.LOW
        else:
            return "none", WindowConfidence.LOW

    def create_window(
        self,
        window_type: str,
        reference_time: datetime,
    ) -> TemporalWindow:
        """Create a temporal window."""
        if window_type == "tight":
            delta = timedelta(seconds=self.config.tight_window_seconds)
            confidence = WindowConfidence.HIGH
        elif window_type == "medium":
            delta = timedelta(seconds=self.config.medium_window_seconds)
            confidence = WindowConfidence.MEDIUM
        else:
            delta = timedelta(seconds=self.config.loose_window_seconds)
            confidence = WindowConfidence.LOW

        return TemporalWindow(
            window_type=window_type,
            start_time=reference_time - delta,
            end_time=reference_time + delta,
            confidence=confidence,
            max_delay_seconds=int(delta.total_seconds()),
        )

    def is_in_window(
        self,
        timestamp: datetime,
        window: TemporalWindow,
    ) -> bool:
        """Check if timestamp falls within window."""
        return window.start_time <= timestamp <= window.end_time


class TemporalCorrelator:
    """Correlate alerts based on time proximity."""

    def __init__(self, config: TemporalConfig | None = None):
        self.config = config or TemporalConfig()
        self._window_manager = TimeWindowManager(config)
        self._time_index: list[AlertTimeEntry] = []
        self._service_index: dict[str, list[AlertTimeEntry]] = {}
        self._incident_first_alert: dict[UUID, datetime] = {}

    def register_alert(
        self,
        alert: RawAlert,
        incident_id: UUID,
        fingerprint: str | None = None,
    ) -> None:
        """Register an alert in the temporal index."""
        entry = AlertTimeEntry(
            alert_id=alert.alert_id,
            timestamp=alert.timestamp,
            service=alert.service,
            incident_id=incident_id,
            fingerprint=fingerprint,
        )

        self._time_index.append(entry)
        self._time_index.sort(key=lambda e: e.timestamp, reverse=True)

        if alert.service:
            if alert.service not in self._service_index:
                self._service_index[alert.service] = []
            self._service_index[alert.service].append(entry)

        if incident_id not in self._incident_first_alert:
            self._incident_first_alert[incident_id] = alert.timestamp
        else:
            if alert.timestamp < self._incident_first_alert[incident_id]:
                self._incident_first_alert[incident_id] = alert.timestamp

    def find_correlated(
        self,
        alert: RawAlert,
        require_same_service: bool | None = None,
    ) -> TimeWindowMatch | None:
        """Find temporally correlated alerts."""
        require_service = (
            require_same_service
            if require_same_service is not None
            else self.config.require_service_relation
        )

        tight_match = self._find_in_window(
            alert,
            "tight",
            require_service,
        )
        if tight_match and self.config.auto_group_tight_window:
            return tight_match

        medium_match = self._find_in_window(
            alert,
            "medium",
            require_service,
        )
        if medium_match:
            return medium_match

        if not require_service:
            loose_match = self._find_in_window(
                alert,
                "loose",
                require_service=True,
            )
            if loose_match:
                return loose_match

        return None

    def _find_in_window(
        self,
        alert: RawAlert,
        window_type: str,
        require_service: bool,
    ) -> TimeWindowMatch | None:
        """Find alerts in a specific time window."""
        window = self._window_manager.create_window(window_type, alert.timestamp)

        if require_service and alert.service:
            candidates = self._service_index.get(alert.service, [])
        else:
            candidates = self._time_index

        matched = []
        incident_counts: dict[UUID, int] = {}

        for entry in candidates:
            if entry.alert_id == alert.alert_id:
                continue

            if self._window_manager.is_in_window(entry.timestamp, window):
                matched.append(entry)
                if entry.incident_id:
                    incident_counts[entry.incident_id] = (
                        incident_counts.get(entry.incident_id, 0) + 1
                    )

        if not matched:
            return None

        best_incident: UUID | None = None
        if incident_counts:
            best_incident = max(incident_counts.keys(), key=lambda k: incident_counts[k])

        timestamps = [e.timestamp for e in matched]
        timestamps.append(alert.timestamp)
        first_time = min(timestamps)
        last_time = max(timestamps)

        return TimeWindowMatch(
            window_type=window_type,
            confidence=window.confidence,
            matched_alerts=[e.alert_id for e in matched],
            incident_id=best_incident,
            time_span_seconds=(last_time - first_time).total_seconds(),
            first_alert_time=first_time,
            last_alert_time=last_time,
        )

    def get_incident_timeline(
        self,
        incident_id: UUID,
    ) -> list[dict[str, Any]]:
        """Get timeline of alerts for an incident."""
        entries = [e for e in self._time_index if e.incident_id == incident_id]
        entries.sort(key=lambda e: e.timestamp)

        timeline = []
        for entry in entries:
            timeline.append({
                "alert_id": str(entry.alert_id),
                "timestamp": entry.timestamp.isoformat(),
                "service": entry.service,
                "fingerprint": entry.fingerprint,
            })

        return timeline

    def cleanup_old_entries(self, before: datetime) -> int:
        """Remove entries older than specified time."""
        original_count = len(self._time_index)
        self._time_index = [e for e in self._time_index if e.timestamp >= before]

        for service in list(self._service_index.keys()):
            self._service_index[service] = [
                e for e in self._service_index[service] if e.timestamp >= before
            ]
            if not self._service_index[service]:
                del self._service_index[service]

        return original_count - len(self._time_index)

    def get_stats(self) -> dict[str, Any]:
        """Get temporal correlator statistics."""
        return {
            "total_entries": len(self._time_index),
            "services_tracked": len(self._service_index),
            "incidents_tracked": len(self._incident_first_alert),
        }


@dataclass
class CascadeCandidate:
    """Candidate cascade relationship."""

    upstream_alert_id: UUID
    upstream_service: str
    upstream_timestamp: datetime
    downstream_alert_id: UUID
    downstream_service: str
    downstream_timestamp: datetime
    delay_seconds: float
    confidence: float


class CascadeDetector:
    """Detect cascade failures across services."""

    def __init__(
        self,
        config: TemporalConfig | None = None,
        dependency_graph: Any | None = None,
    ):
        self.config = config or TemporalConfig()
        self._dependency_graph = dependency_graph
        self._cascade_chains: dict[UUID, CascadeChain] = {}
        self._service_alerts: dict[str, list[AlertTimeEntry]] = {}

    def set_dependency_graph(self, graph: Any) -> None:
        """Set the service dependency graph."""
        self._dependency_graph = graph

    def register_alert(
        self,
        alert: RawAlert,
        incident_id: UUID | None = None,
    ) -> None:
        """Register an alert for cascade detection."""
        entry = AlertTimeEntry(
            alert_id=alert.alert_id,
            timestamp=alert.timestamp,
            service=alert.service,
            incident_id=incident_id,
        )

        if alert.service:
            if alert.service not in self._service_alerts:
                self._service_alerts[alert.service] = []
            self._service_alerts[alert.service].append(entry)
            self._service_alerts[alert.service].sort(
                key=lambda e: e.timestamp, reverse=True
            )

    def detect_cascade(
        self,
        alert: RawAlert,
    ) -> CascadeChain | None:
        """Detect if alert is part of a cascade."""
        if not alert.service:
            return None

        upstream_alerts = self._find_upstream_alerts(alert)

        if not upstream_alerts:
            downstream = self._find_downstream_alerts(alert)
            if downstream:
                chain = CascadeChain(
                    root_alert_id=alert.alert_id,
                    root_service=alert.service,
                    cascade_alerts=[d.downstream_alert_id for d in downstream],
                    cascade_services=list(set(d.downstream_service for d in downstream)),
                    detection_confidence=max(d.confidence for d in downstream),
                    detected_at=datetime.utcnow(),
                )
                self._cascade_chains[chain.chain_id] = chain
                return chain
            return None

        best_upstream = max(upstream_alerts, key=lambda c: c.confidence)

        existing_chain = self._find_chain_for_alert(best_upstream.upstream_alert_id)
        if existing_chain:
            existing_chain.cascade_alerts.append(alert.alert_id)
            if alert.service not in existing_chain.cascade_services:
                existing_chain.cascade_services.append(alert.service)
            existing_chain.cascade_delay_seconds = max(
                existing_chain.cascade_delay_seconds,
                best_upstream.delay_seconds,
            )
            return existing_chain

        chain = CascadeChain(
            root_alert_id=best_upstream.upstream_alert_id,
            root_service=best_upstream.upstream_service,
            cascade_alerts=[alert.alert_id],
            cascade_services=[alert.service],
            detection_confidence=best_upstream.confidence,
            cascade_delay_seconds=best_upstream.delay_seconds,
            detected_at=datetime.utcnow(),
        )
        self._cascade_chains[chain.chain_id] = chain
        return chain

    def _find_upstream_alerts(
        self,
        alert: RawAlert,
    ) -> list[CascadeCandidate]:
        """Find potential upstream alerts that could have caused this alert."""
        candidates: list[CascadeCandidate] = []

        if not alert.service:
            return candidates

        upstream_services = self._get_upstream_services(alert.service)

        for upstream_service in upstream_services:
            upstream_entries = self._service_alerts.get(upstream_service, [])

            for entry in upstream_entries:
                delay = (alert.timestamp - entry.timestamp).total_seconds()

                if 0 < delay <= self.config.cascade_max_delay_seconds:
                    confidence = self._calculate_cascade_confidence(
                        upstream_service,
                        alert.service,
                        delay,
                    )

                    if confidence >= self.config.cascade_min_confidence:
                        candidates.append(
                            CascadeCandidate(
                                upstream_alert_id=entry.alert_id,
                                upstream_service=upstream_service,
                                upstream_timestamp=entry.timestamp,
                                downstream_alert_id=alert.alert_id,
                                downstream_service=alert.service,
                                downstream_timestamp=alert.timestamp,
                                delay_seconds=delay,
                                confidence=confidence,
                            )
                        )

        return candidates

    def _find_downstream_alerts(
        self,
        alert: RawAlert,
    ) -> list[CascadeCandidate]:
        """Find potential downstream alerts caused by this alert."""
        candidates: list[CascadeCandidate] = []

        if not alert.service:
            return candidates

        downstream_services = self._get_downstream_services(alert.service)

        for downstream_service in downstream_services:
            downstream_entries = self._service_alerts.get(downstream_service, [])

            for entry in downstream_entries:
                delay = (entry.timestamp - alert.timestamp).total_seconds()

                if 0 < delay <= self.config.cascade_max_delay_seconds:
                    confidence = self._calculate_cascade_confidence(
                        alert.service,
                        downstream_service,
                        delay,
                    )

                    if confidence >= self.config.cascade_min_confidence:
                        candidates.append(
                            CascadeCandidate(
                                upstream_alert_id=alert.alert_id,
                                upstream_service=alert.service,
                                upstream_timestamp=alert.timestamp,
                                downstream_alert_id=entry.alert_id,
                                downstream_service=downstream_service,
                                downstream_timestamp=entry.timestamp,
                                delay_seconds=delay,
                                confidence=confidence,
                            )
                        )

        return candidates

    def _get_upstream_services(self, service: str) -> list[str]:
        """Get services that this service depends on."""
        if self._dependency_graph:
            try:
                return self._dependency_graph.get_upstream_services(service)
            except Exception:
                pass

        return self._infer_upstream_services(service)

    def _get_downstream_services(self, service: str) -> list[str]:
        """Get services that depend on this service."""
        if self._dependency_graph:
            try:
                return self._dependency_graph.get_downstream_services(service)
            except Exception:
                pass

        return self._infer_downstream_services(service)

    def _infer_upstream_services(self, service: str) -> list[str]:
        """Infer upstream services from naming patterns."""
        upstream = []

        if "api" in service.lower():
            for svc in self._service_alerts.keys():
                if "db" in svc.lower() or "database" in svc.lower():
                    upstream.append(svc)
                if "cache" in svc.lower() or "redis" in svc.lower():
                    upstream.append(svc)

        if "frontend" in service.lower() or "web" in service.lower():
            for svc in self._service_alerts.keys():
                if "api" in svc.lower() or "backend" in svc.lower():
                    upstream.append(svc)

        return upstream

    def _infer_downstream_services(self, service: str) -> list[str]:
        """Infer downstream services from naming patterns."""
        downstream = []

        if "db" in service.lower() or "database" in service.lower():
            for svc in self._service_alerts.keys():
                if svc != service:
                    downstream.append(svc)

        if "api" in service.lower() or "backend" in service.lower():
            for svc in self._service_alerts.keys():
                if "frontend" in svc.lower() or "web" in svc.lower():
                    downstream.append(svc)

        return downstream

    def _calculate_cascade_confidence(
        self,
        upstream: str,
        downstream: str,
        delay_seconds: float,
    ) -> float:
        """Calculate confidence that this is a real cascade."""
        confidence = 0.5

        if self._dependency_graph:
            try:
                if self._dependency_graph.has_dependency(downstream, upstream):
                    confidence += 0.3
            except Exception:
                pass

        if delay_seconds <= 10:
            confidence += 0.15
        elif delay_seconds <= 30:
            confidence += 0.10
        elif delay_seconds <= 60:
            confidence += 0.05

        return min(confidence, 1.0)

    def _find_chain_for_alert(self, alert_id: UUID) -> CascadeChain | None:
        """Find existing cascade chain containing an alert."""
        for chain in self._cascade_chains.values():
            if chain.root_alert_id == alert_id:
                return chain
            if alert_id in chain.cascade_alerts:
                return chain
        return None

    def get_cascade_chain(self, chain_id: UUID) -> CascadeChain | None:
        """Get a cascade chain by ID."""
        return self._cascade_chains.get(chain_id)

    def get_root_cause_for_alert(
        self,
        alert_id: UUID,
    ) -> tuple[UUID | None, str | None]:
        """Get root cause alert and service for a given alert."""
        for chain in self._cascade_chains.values():
            if alert_id in chain.cascade_alerts:
                return chain.root_alert_id, chain.root_service
        return None, None

    def visualize_cascade(self, chain_id: UUID) -> str:
        """Generate ASCII visualization of cascade."""
        chain = self._cascade_chains.get(chain_id)
        if not chain:
            return "Chain not found"

        lines = []
        lines.append(f"Cascade Chain {chain_id}")
        lines.append("=" * 50)
        lines.append(f"Root Cause: {chain.root_service}")
        lines.append(f"  Alert ID: {chain.root_alert_id}")
        lines.append("")
        lines.append("Cascade Effects:")

        for i, (alert_id, service) in enumerate(
            zip(chain.cascade_alerts, chain.cascade_services)
        ):
            prefix = "  " + "│   " * i + "├── "
            lines.append(f"{prefix}{service}")
            lines.append(f"{prefix}  Alert: {alert_id}")

        lines.append("")
        lines.append(f"Confidence: {chain.detection_confidence:.1%}")
        lines.append(f"Max Delay: {chain.cascade_delay_seconds:.1f}s")

        return "\n".join(lines)

    def get_stats(self) -> dict[str, Any]:
        """Get cascade detector statistics."""
        return {
            "active_chains": len(self._cascade_chains),
            "services_tracked": len(self._service_alerts),
            "total_alerts": sum(
                len(alerts) for alerts in self._service_alerts.values()
            ),
        }
