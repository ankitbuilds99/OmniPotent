"""Main deduplication pipeline combining all layers."""

from __future__ import annotations

import logging
import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from app.deduplication.models import (
    RawAlert,
    DeduplicationDecision,
    Decision,
    IncidentGroup,
    AlertSeverity,
)
from app.deduplication.fingerprint import (
    FingerprintCalculator,
    FingerprintMatcher,
    FingerprintConfig,
)
from app.deduplication.semantic import (
    SemanticSimilarityEngine,
    SemanticConfig,
)
from app.deduplication.temporal import (
    TemporalCorrelator,
    CascadeDetector,
    TemporalConfig,
)
from app.deduplication.topology import (
    ServiceDependencyGraph,
    TopologyCorrelator,
    TopologyConfig,
)
from app.deduplication.ml_correlation import (
    MLCorrelationEngine,
    MLConfig,
)
from app.deduplication.suppression import (
    SuppressionEngine,
    SuppressionConfig,
)
from app.deduplication.state_store import DedupStateStore, RedisDedupStateStore, local_lock

log = logging.getLogger("omnipotent.deduplication.pipeline")


@dataclass
class PipelineConfig:
    """Configuration for deduplication pipeline."""

    enable_layer1_fingerprint: bool = True
    enable_layer2_semantic: bool = True
    enable_layer3_temporal: bool = True
    enable_layer4_topology: bool = True
    enable_layer5_ml: bool = True
    enable_layer6_suppression: bool = True

    fingerprint_config: FingerprintConfig = field(default_factory=FingerprintConfig)
    semantic_config: SemanticConfig = field(default_factory=SemanticConfig)
    temporal_config: TemporalConfig = field(default_factory=TemporalConfig)
    topology_config: TopologyConfig = field(default_factory=TopologyConfig)
    ml_config: MLConfig = field(default_factory=MLConfig)
    suppression_config: SuppressionConfig = field(default_factory=SuppressionConfig)

    min_confidence_to_merge: float = 0.7
    max_alerts_per_incident: int = 100
    # Empty means process-local state, suitable only for development.  A
    # production deployment should set this through environment/configuration.
    shared_state_url: str = field(
        default_factory=lambda: os.getenv("COPILOT_DEDUP_STATE_URL", "")
    )
    shared_state_key: str = "omnipotent:dedup:v1"
    require_shared_state: bool = False


@dataclass
class LayerResult:
    """Result from a single deduplication layer."""

    layer_name: str
    matched: bool
    incident_id: UUID | None = None
    confidence: float = 0.0
    reason: str = ""
    processing_time_ms: float = 0.0
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class PipelineMetrics:
    """Metrics for the deduplication pipeline."""

    total_alerts_processed: int = 0
    alerts_deduplicated: int = 0
    alerts_suppressed: int = 0
    new_incidents_created: int = 0

    layer_matches: dict[str, int] = field(default_factory=lambda: {
        "fingerprint": 0,
        "semantic": 0,
        "temporal": 0,
        "topology": 0,
        "ml": 0,
        "suppression": 0,
    })

    avg_processing_time_ms: float = 0.0
    total_processing_time_ms: float = 0.0

    def record_processing(
        self,
        decision: DeduplicationDecision,
        processing_time_ms: float,
    ) -> None:
        """Record processing metrics."""
        self.total_alerts_processed += 1
        self.total_processing_time_ms += processing_time_ms
        self.avg_processing_time_ms = (
            self.total_processing_time_ms / self.total_alerts_processed
        )

        if decision.decision == Decision.ADD_TO_EXISTING:
            self.alerts_deduplicated += 1
        elif decision.decision == Decision.SUPPRESS:
            self.alerts_suppressed += 1
        elif decision.decision == Decision.NEW_INCIDENT:
            self.new_incidents_created += 1

        for layer in decision.layers_applied:
            if layer in self.layer_matches:
                self.layer_matches[layer] += 1

    def get_reduction_rate(self) -> float:
        """Calculate noise reduction rate."""
        if self.total_alerts_processed == 0:
            return 0.0
        reduced = self.alerts_deduplicated + self.alerts_suppressed
        return reduced / self.total_alerts_processed

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "total_alerts_processed": self.total_alerts_processed,
            "alerts_deduplicated": self.alerts_deduplicated,
            "alerts_suppressed": self.alerts_suppressed,
            "new_incidents_created": self.new_incidents_created,
            "reduction_rate": self.get_reduction_rate(),
            "layer_matches": self.layer_matches,
            "avg_processing_time_ms": self.avg_processing_time_ms,
        }


class DeduplicationPipeline:
    """Main deduplication pipeline combining all 6 layers."""

    def __init__(
        self,
        config: PipelineConfig | None = None,
        *,
        state_store: DedupStateStore | None = None,
    ):
        self.config = config or PipelineConfig()
        self._state_store = state_store
        if self._state_store is None and self.config.shared_state_url.strip():
            self._state_store = RedisDedupStateStore(
                self.config.shared_state_url, self.config.shared_state_key
            )
        if self.config.require_shared_state and self._state_store is None:
            raise RuntimeError(
                "shared deduplication state is required but no state store is configured"
            )
        self._metrics = PipelineMetrics()

        self._fingerprint_calculator = FingerprintCalculator(
            self.config.fingerprint_config
        )
        self._fingerprint_matcher = FingerprintMatcher(
            timedelta(minutes=self.config.fingerprint_config.dedup_window_minutes)
        )

        self._semantic_engine = SemanticSimilarityEngine(self.config.semantic_config)

        self._temporal_correlator = TemporalCorrelator(self.config.temporal_config)
        self._cascade_detector = CascadeDetector(self.config.temporal_config)

        self._dependency_graph = ServiceDependencyGraph(self.config.topology_config)
        self._topology_correlator = TopologyCorrelator(
            self._dependency_graph, self.config.topology_config
        )
        self._cascade_detector.set_dependency_graph(self._dependency_graph)

        self._ml_engine = MLCorrelationEngine(self.config.ml_config)

        self._suppression_engine = SuppressionEngine(self.config.suppression_config)

        self._incidents: dict[UUID, IncidentGroup] = {}
        self._alert_to_incident: dict[UUID, UUID] = {}
        self._recent_alerts: list[RawAlert] = []
        self._state_digest: str | None = None
        self._restore_state()

    def process_alert(self, alert: RawAlert) -> DeduplicationDecision:
        """Process one alert atomically when shared state is configured."""
        lock_factory = getattr(self._state_store, "lock", None)
        lock = lock_factory() if lock_factory is not None else local_lock()
        with lock:
            # Another worker may have committed a newer snapshot while this
            # process was idle. Rehydrate before deciding, while preserving the
            # current object's configuration and notification sink.
            if self._state_store is not None:
                self._restore_state()
            return self._process_alert_unlocked(alert)

    def _process_alert_unlocked(self, alert: RawAlert) -> DeduplicationDecision:
        """Process an alert through the deduplication pipeline."""
        start_time = time.time()
        layers_applied: list[str] = []

        fingerprint = self._fingerprint_calculator.calculate(alert)

        if self.config.enable_layer6_suppression:
            suppression_result = self._suppression_engine.check_suppression(
                alert, fingerprint.fingerprint
            )
            if suppression_result.should_suppress:
                layers_applied.append("suppression")
                decision = DeduplicationDecision(
                    raw_alert_id=alert.alert_id,
                    decision=Decision.SUPPRESS,
                    reason=suppression_result.reason,
                    confidence=1.0,
                    layers_applied=layers_applied,
                    suppression_rule=suppression_result.rule_name,
                    fingerprint=fingerprint.fingerprint,
                )
                self._record_decision(alert, decision, start_time)
                self._persist_state()
                return decision

        if self.config.enable_layer1_fingerprint:
            fp_match = self._fingerprint_matcher.find_match(
                fingerprint, alert.timestamp
            )
            if fp_match:
                layers_applied.append("fingerprint")
                incident = self._mergeable_incident(fp_match.incident_id)
                if incident:
                    decision = DeduplicationDecision(
                        raw_alert_id=alert.alert_id,
                        decision=Decision.ADD_TO_EXISTING,
                        incident_id=fp_match.incident_id,
                        reason="Exact fingerprint match",
                        confidence=1.0,
                        layers_applied=layers_applied,
                        fingerprint=fingerprint.fingerprint,
                    )
                    self._add_to_incident(alert, fp_match.incident_id, fingerprint)
                    self._record_decision(alert, decision, start_time)
                    self._persist_state()
                    return decision

        if self.config.enable_layer2_semantic:
            semantic_match = self._semantic_engine.find_similar(alert)
            if semantic_match and semantic_match.similarity_score >= self.config.min_confidence_to_merge:
                layers_applied.append("semantic")
                incident = self._mergeable_incident(semantic_match.incident_id)
                if incident:
                    decision = DeduplicationDecision(
                        raw_alert_id=alert.alert_id,
                        decision=Decision.ADD_TO_EXISTING,
                        incident_id=semantic_match.incident_id,
                        reason=f"Semantic similarity: {semantic_match.similarity_score:.2f}",
                        confidence=semantic_match.similarity_score,
                        layers_applied=layers_applied,
                        fingerprint=fingerprint.fingerprint,
                        semantic_score=semantic_match.similarity_score,
                    )
                    self._add_to_incident(alert, semantic_match.incident_id, fingerprint)
                    self._record_decision(alert, decision, start_time)
                    self._persist_state()
                    return decision

        if self.config.enable_layer3_temporal:
            temporal_match = self._temporal_correlator.find_correlated(alert)
            if temporal_match and temporal_match.incident_id:
                layers_applied.append("temporal")
                incident = self._mergeable_incident(temporal_match.incident_id)
                confidence = 0.9 if temporal_match.confidence == "high" else 0.75
                if incident and confidence >= self.config.min_confidence_to_merge:
                    decision = DeduplicationDecision(
                        raw_alert_id=alert.alert_id,
                        decision=Decision.ADD_TO_EXISTING,
                        incident_id=temporal_match.incident_id,
                        reason=f"Temporal correlation ({temporal_match.window_type} window)",
                        confidence=confidence,
                        layers_applied=layers_applied,
                        fingerprint=fingerprint.fingerprint,
                        temporal_window=temporal_match.window_type,
                    )
                    self._add_to_incident(alert, temporal_match.incident_id, fingerprint)
                    self._record_decision(alert, decision, start_time)
                    self._persist_state()
                    return decision

            cascade = self._cascade_detector.detect_cascade(alert)
            if cascade and cascade.root_alert_id:
                root_incident_id = self._alert_to_incident.get(cascade.root_alert_id)
                if root_incident_id and self._mergeable_incident(root_incident_id):
                    layers_applied.append("cascade")
                    decision = DeduplicationDecision(
                        raw_alert_id=alert.alert_id,
                        decision=Decision.ADD_TO_EXISTING,
                        incident_id=root_incident_id,
                        reason=f"Cascade from {cascade.root_service}",
                        confidence=cascade.detection_confidence,
                        layers_applied=layers_applied,
                        fingerprint=fingerprint.fingerprint,
                        cascade_root=cascade.root_service,
                    )
                    self._add_to_incident(alert, root_incident_id, fingerprint)
                    self._record_decision(alert, decision, start_time)
                    self._persist_state()
                    return decision

        if self.config.enable_layer4_topology:
            topo_match = self._topology_correlator.find_upstream_incident(alert.service)
            if topo_match:
                layers_applied.append("topology")
                incident = self._mergeable_incident(topo_match.upstream_incident_id)
                if incident and topo_match.confidence >= self.config.min_confidence_to_merge:
                    decision = DeduplicationDecision(
                        raw_alert_id=alert.alert_id,
                        decision=Decision.ADD_TO_EXISTING,
                        incident_id=topo_match.upstream_incident_id,
                        reason=f"Downstream of {topo_match.upstream_service}",
                        confidence=topo_match.confidence,
                        layers_applied=layers_applied,
                        fingerprint=fingerprint.fingerprint,
                        cascade_root=topo_match.upstream_service,
                    )
                    self._add_to_incident(alert, topo_match.upstream_incident_id, fingerprint)
                    self._record_decision(alert, decision, start_time)
                    self._persist_state()
                    return decision

        if self.config.enable_layer5_ml:
            cluster, pattern, confidence = self._ml_engine.process_alert(
                alert, self._recent_alerts[-10:]
            )
            if cluster and cluster.incident_id:
                layers_applied.append("ml")
                incident = self._mergeable_incident(cluster.incident_id)
                if incident and confidence >= self.config.min_confidence_to_merge:
                    decision = DeduplicationDecision(
                        raw_alert_id=alert.alert_id,
                        decision=Decision.ADD_TO_EXISTING,
                        incident_id=cluster.incident_id,
                        reason=f"ML cluster: {cluster.cluster_label}",
                        confidence=confidence,
                        layers_applied=layers_applied,
                        fingerprint=fingerprint.fingerprint,
                        cluster_label=cluster.cluster_label,
                    )
                    self._add_to_incident(alert, cluster.incident_id, fingerprint)
                    self._record_decision(alert, decision, start_time)
                    return decision

        incident = self._create_incident(alert, fingerprint)
        decision = DeduplicationDecision(
            raw_alert_id=alert.alert_id,
            decision=Decision.NEW_INCIDENT,
            incident_id=incident.incident_id,
            reason="No correlation found",
            confidence=1.0,
            layers_applied=layers_applied,
            fingerprint=fingerprint.fingerprint,
        )
        self._record_decision(alert, decision, start_time)
        self._persist_state()
        return decision

    def _snapshot(self) -> dict[str, Any]:
        alerts = {str(a.alert_id): a.to_dict() for a in self._recent_alerts}
        return {
            "schema": "omnipotent.dedup-state.v1",
            "incidents": [i.to_dict() for i in self._incidents.values()],
            "alerts": list(alerts.values()),
        }

    def _persist_state(self) -> None:
        if self._state_store is not None:
            state = self._snapshot()
            self._state_store.save(state)
            self._state_digest = self._digest_state(state)

    @staticmethod
    def _digest_state(state: dict[str, Any]) -> str:
        return hashlib.sha256(
            json.dumps(state, separators=(",", ":"), sort_keys=True).encode()
        ).hexdigest()

    def _restore_state(self) -> None:
        if self._state_store is None:
            return
        state = self._state_store.load()
        if not state:
            return
        digest = self._digest_state(state)
        if digest == self._state_digest:
            return
        if state.get("schema") != "omnipotent.dedup-state.v1":
            raise RuntimeError("unsupported deduplication state schema")
        incidents = {
            UUID(item["incident_id"]): self._incident_from_dict(item)
            for item in state.get("incidents", [])
        }
        alerts = [RawAlert.from_dict(item) for item in state.get("alerts", [])]
        by_id = {a.alert_id: a for a in alerts}
        self._incidents = incidents
        self._recent_alerts = alerts[-100:]
        for incident in incidents.values():
            for alert_id in incident.alerts:
                alert = by_id.get(alert_id)
                if alert is None or incident.status != "active":
                    continue
                fingerprint = self._fingerprint_calculator.calculate(alert)
                self._alert_to_incident[alert.alert_id] = incident.incident_id
                self._fingerprint_matcher.register_alert(fingerprint, alert, incident.incident_id)
                self._semantic_engine.register_alert(alert, incident.incident_id)
                self._temporal_correlator.register_alert(alert, incident.incident_id, fingerprint.fingerprint)
                self._cascade_detector.register_alert(alert, incident.incident_id)
                self._topology_correlator.register_incident(alert.service, incident.incident_id)
        self._state_digest = digest

    @staticmethod
    def _incident_from_dict(item: dict[str, Any]) -> IncidentGroup:
        def dt(value: str | None) -> datetime | None:
            return datetime.fromisoformat(value) if value else None

        return IncidentGroup(
            incident_id=UUID(item["incident_id"]),
            title=item.get("title", ""),
            alerts=[UUID(value) for value in item.get("alerts", [])],
            root_cause_alert_id=UUID(item["root_cause_alert_id"]) if item.get("root_cause_alert_id") else None,
            root_cause_service=item.get("root_cause_service"),
            severity=AlertSeverity(item.get("severity", "medium")),
            status=item.get("status", "active"),
            created_at=dt(item.get("created_at")) or datetime.utcnow(),
            updated_at=dt(item.get("updated_at")) or datetime.utcnow(),
            resolved_at=dt(item.get("resolved_at")),
            grouping_reason=item.get("grouping_reason", ""),
            grouping_confidence=float(item.get("grouping_confidence", 0.0)),
            ai_analysis=item.get("ai_analysis", ""),
        )

    def _mergeable_incident(self, incident_id: UUID | None) -> IncidentGroup | None:
        """The incident a layer's match may join, or None if it may not.

        Every layer keeps its own index of past alerts and hands back the incident
        the matching alert belonged to; none of those indexes are told when an
        incident is resolved. The layers used to check only that the incident still
        existed and had room, so an alert that fired again five minutes after its
        incident was closed was added to the *resolved* incident: the operator saw
        `add_to_existing` with an incident id, `GET /deduplication/incidents`
        returned nothing, and a live outage had no open incident anywhere.

        A resolved incident is closed. Refusing the merge here rather than at each
        call site means a stale match falls through to the next layer and, if
        nothing live claims the alert, opens a new incident — which is what a fresh
        occurrence of a closed problem is. The capacity limit lives here for the
        same reason: it is the same question, "can this incident take the alert".
        """
        if incident_id is None:
            return None

        incident = self._incidents.get(incident_id)
        if incident is None:
            return None
        if incident.status != "active":
            return None
        if len(incident.alerts) >= self.config.max_alerts_per_incident:
            return None
        return incident

    def _create_incident(
        self,
        alert: RawAlert,
        fingerprint: Any,
    ) -> IncidentGroup:
        """Create a new incident from an alert."""
        incident = IncidentGroup(
            title=f"{alert.service}: {alert.alert_name}",
            alerts=[alert.alert_id],
            root_cause_alert_id=alert.alert_id,
            root_cause_service=alert.service,
            severity=alert.severity,
            grouping_reason="Initial alert",
            grouping_confidence=1.0,
        )

        self._incidents[incident.incident_id] = incident
        self._alert_to_incident[alert.alert_id] = incident.incident_id

        self._fingerprint_matcher.register_alert(
            fingerprint, alert, incident.incident_id
        )
        self._semantic_engine.register_alert(alert, incident.incident_id)
        self._temporal_correlator.register_alert(
            alert, incident.incident_id, fingerprint.fingerprint
        )
        self._cascade_detector.register_alert(alert, incident.incident_id)
        self._topology_correlator.register_incident(alert.service, incident.incident_id)

        log.info(
            "Created incident %s for alert %s",
            incident.incident_id,
            alert.alert_id,
        )

        return incident

    def _add_to_incident(
        self,
        alert: RawAlert,
        incident_id: UUID,
        fingerprint: Any,
    ) -> None:
        """Add alert to existing incident."""
        incident = self._incidents.get(incident_id)
        if not incident:
            return

        incident.add_alert(alert.alert_id)
        self._alert_to_incident[alert.alert_id] = incident_id

        if alert.severity.value in ["critical", "high"]:
            if incident.severity.value not in ["critical"]:
                incident.severity = alert.severity

        self._fingerprint_matcher.register_alert(fingerprint, alert, incident_id)
        self._semantic_engine.register_alert(alert, incident_id)
        self._temporal_correlator.register_alert(
            alert, incident_id, fingerprint.fingerprint
        )
        self._cascade_detector.register_alert(alert, incident_id)

        log.debug(
            "Added alert %s to incident %s (total: %d)",
            alert.alert_id,
            incident_id,
            len(incident.alerts),
        )

    def _record_decision(
        self,
        alert: RawAlert,
        decision: DeduplicationDecision,
        start_time: float,
    ) -> None:
        """Record decision and update metrics."""
        processing_time = (time.time() - start_time) * 1000
        decision.processing_time_ms = processing_time
        self._metrics.record_processing(decision, processing_time)

        self._suppression_engine.record_alert(decision.fingerprint or "")

        self._recent_alerts.append(alert)
        if len(self._recent_alerts) > 100:
            self._recent_alerts = self._recent_alerts[-100:]

    def get_incident(self, incident_id: UUID) -> IncidentGroup | None:
        """Get incident by ID."""
        return self._incidents.get(incident_id)

    def get_incident_for_alert(self, alert_id: UUID) -> IncidentGroup | None:
        """Get incident containing an alert."""
        incident_id = self._alert_to_incident.get(alert_id)
        if incident_id:
            return self._incidents.get(incident_id)
        return None

    def resolve_incident(self, incident_id: UUID) -> bool:
        """Mark incident as resolved and take its alerts out of every index.

        Resolving used to tell only the topology correlator, because it was the
        only layer with an API for forgetting an incident. The fingerprint,
        semantic, temporal, cascade and ML indexes went on holding the incident's
        alerts, so a closed incident stayed a match candidate forever:
        `_mergeable_incident` refused each stale match at merge time, which kept
        the answer correct but did the same work again on every alert and let the
        indexes grow for the life of the process. Worse, Layer 3 picks the incident
        with the most matching alerts in the window, so a long-lived resolved
        incident could out-vote a live one and cost a correlation that was right.

        Layers 1 to 4 now forget the incident outright, which is why a critical
        that re-fires afterwards opens a new incident rather than landing on the
        closed one. `_mergeable_incident` stays as the backstop: `MLCorrelationEngine`
        has no forget API — its clusters are learned state, not an index of alerts —
        so its stale matches still have to be refused at merge time.
        """
        incident = self._incidents.get(incident_id)
        if not incident:
            return False

        incident.status = "resolved"
        incident.resolved_at = datetime.utcnow()

        evicted = {
            "fingerprint": self._fingerprint_matcher.forget_incident(incident_id),
            "semantic": self._semantic_engine.forget_incident(incident_id),
            "temporal": self._temporal_correlator.forget_incident(incident_id),
            "cascade": self._cascade_detector.forget_incident(incident_id),
        }

        if incident.root_cause_service:
            self._topology_correlator.clear_incident(incident.root_cause_service)

        alerts = [a for a in self._recent_alerts if a.alert_id in incident.alerts]
        self._ml_engine.record_incident_resolution(
            incident_id,
            alerts,
            incident.root_cause_service,
        )

        log.info("Resolved incident %s; evicted from indexes: %s", incident_id, evicted)
        return True

    def get_active_incidents(self) -> list[IncidentGroup]:
        """Get all active incidents."""
        return [i for i in self._incidents.values() if i.status == "active"]

    @property
    def metrics(self) -> PipelineMetrics:
        """Get pipeline metrics."""
        return self._metrics

    @property
    def dependency_graph(self) -> ServiceDependencyGraph:
        """Get service dependency graph."""
        return self._dependency_graph

    @property
    def suppression(self) -> SuppressionEngine:
        """Get suppression engine."""
        return self._suppression_engine

    def get_stats(self) -> dict[str, Any]:
        """Get comprehensive pipeline statistics."""
        return {
            "metrics": self._metrics.to_dict(),
            "active_incidents": len(self.get_active_incidents()),
            "total_incidents": len(self._incidents),
            "fingerprint": self._fingerprint_matcher.get_stats(),
            "semantic": self._semantic_engine.get_stats(),
            "temporal": self._temporal_correlator.get_stats(),
            "cascade": self._cascade_detector.get_stats(),
            "ml": self._ml_engine.get_stats(),
            "suppression": self._suppression_engine.get_suppression_stats(),
        }

    def cleanup(self, before: datetime | None = None) -> dict[str, int]:
        """Cleanup old data from all components."""
        before = before or (datetime.utcnow() - timedelta(hours=24))

        cleaned = {
            "fingerprints": self._fingerprint_matcher.cleanup_old_entries(before),
            "temporal": self._temporal_correlator.cleanup_old_entries(before),
            "flapping": self._suppression_engine.flapping.cleanup_old_entries(before),
        }

        log.info("Cleanup complete: %s", cleaned)
        return cleaned
