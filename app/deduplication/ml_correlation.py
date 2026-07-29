"""Layer 5: ML-based correlation and pattern learning."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from app.deduplication.models import (
    RawAlert,
    CorrelationCluster,
)

log = logging.getLogger("devcopilot.deduplication.ml_correlation")


@dataclass
class MLConfig:
    """Configuration for ML-based correlation."""

    clustering_eps: float = 0.3
    clustering_min_samples: int = 2
    min_pattern_confidence: float = 0.75
    pattern_min_occurrences: int = 5
    feature_weights: dict[str, float] = field(
        default_factory=lambda: {
            "service": 0.25,
            "alert_type": 0.20,
            "message_embedding": 0.30,
            "time_proximity": 0.15,
            "severity": 0.10,
        }
    )
    retrain_interval_hours: int = 24
    max_clusters: int = 100


@dataclass
class LearnedPattern:
    """Pattern learned from historical incidents."""

    pattern_id: UUID = field(default_factory=uuid4)
    name: str = ""
    description: str = ""
    signal_sequence: list[dict[str, Any]] = field(default_factory=list)
    occurrence_count: int = 0
    confidence: float = 0.0
    suggested_root_cause: str = ""
    suggested_action: str = ""
    last_seen: datetime = field(default_factory=datetime.utcnow)
    created_at: datetime = field(default_factory=datetime.utcnow)
    training_incidents: list[UUID] = field(default_factory=list)

    def matches(self, alerts: list[RawAlert]) -> float:
        """Calculate match score against a sequence of alerts."""
        if not self.signal_sequence or not alerts:
            return 0.0

        matched_signals = 0
        for signal in self.signal_sequence:
            for alert in alerts:
                if self._signal_matches_alert(signal, alert):
                    matched_signals += 1
                    break

        return matched_signals / len(self.signal_sequence)

    def _signal_matches_alert(
        self,
        signal: dict[str, Any],
        alert: RawAlert,
    ) -> bool:
        """Check if a signal definition matches an alert."""
        if signal.get("service") and signal["service"] != alert.service:
            return False
        if signal.get("alert_type") and signal["alert_type"] != alert.alert_type:
            return False
        if signal.get("severity") and signal["severity"] != alert.severity.value:
            return False
        if signal.get("alert_name_contains"):
            if signal["alert_name_contains"].lower() not in alert.alert_name.lower():
                return False
        return True

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "pattern_id": str(self.pattern_id),
            "name": self.name,
            "description": self.description,
            "signal_sequence": self.signal_sequence,
            "occurrence_count": self.occurrence_count,
            "confidence": self.confidence,
            "suggested_root_cause": self.suggested_root_cause,
            "suggested_action": self.suggested_action,
            "last_seen": self.last_seen.isoformat(),
        }


@dataclass
class ClusterFeatures:
    """Features extracted for clustering."""

    alert_id: UUID
    service_hash: int = 0
    alert_type_hash: int = 0
    severity_numeric: float = 0.0
    time_bucket: int = 0
    message_embedding: list[float] = field(default_factory=list)

    def to_vector(self, embed_dim: int = 32) -> list[float]:
        """Convert features to numeric vector."""
        vector = [
            float(self.service_hash % 1000) / 1000,
            float(self.alert_type_hash % 1000) / 1000,
            self.severity_numeric,
            float(self.time_bucket % 100) / 100,
        ]

        if self.message_embedding:
            vector.extend(self.message_embedding[:embed_dim])
        else:
            vector.extend([0.0] * embed_dim)

        return vector


class PatternLearner:
    """Learn patterns from historical incidents."""

    def __init__(self, config: MLConfig | None = None):
        self.config = config or MLConfig()
        self._patterns: dict[UUID, LearnedPattern] = {}
        self._incident_history: list[tuple[UUID, list[RawAlert]]] = []
        self._load_builtin_patterns()

    def _load_builtin_patterns(self) -> None:
        """Load built-in patterns."""
        builtin = [
            LearnedPattern(
                name="Deploy → Latency → Errors",
                description="Deploy event followed by latency spike and error rate increase",
                signal_sequence=[
                    {"alert_type": "deploy", "alert_name_contains": "deploy"},
                    {"alert_name_contains": "latency"},
                    {"alert_name_contains": "error"},
                ],
                confidence=0.89,
                occurrence_count=47,
                suggested_root_cause="Recent deployment",
                suggested_action="Consider rollback if metrics don't stabilize",
            ),
            LearnedPattern(
                name="Memory Leak → OOM → Restart Loop",
                description="Gradual memory increase leading to OOM and pod restarts",
                signal_sequence=[
                    {"alert_name_contains": "memory", "severity": "medium"},
                    {"alert_name_contains": "oom"},
                    {"alert_name_contains": "restart"},
                ],
                confidence=0.94,
                occurrence_count=23,
                suggested_root_cause="Memory leak in application",
                suggested_action="Restart pod and investigate memory leak",
            ),
            LearnedPattern(
                name="Certificate Expiry Cascade",
                description="TLS certificate expiry causing connection failures",
                signal_sequence=[
                    {"alert_name_contains": "certificate"},
                    {"alert_name_contains": "tls"},
                    {"alert_name_contains": "connection"},
                ],
                confidence=0.92,
                occurrence_count=15,
                suggested_root_cause="Expired TLS certificate",
                suggested_action="Renew certificate immediately",
            ),
            LearnedPattern(
                name="Database Connection Pool Exhaustion",
                description="DB connection pool saturation causing timeouts",
                signal_sequence=[
                    {"alert_name_contains": "connection_pool"},
                    {"alert_name_contains": "timeout"},
                    {"alert_name_contains": "5xx"},
                ],
                confidence=0.87,
                occurrence_count=31,
                suggested_root_cause="Connection pool exhaustion",
                suggested_action="Scale DB connections or investigate connection leaks",
            ),
        ]

        for pattern in builtin:
            self._patterns[pattern.pattern_id] = pattern

    def record_incident(
        self,
        incident_id: UUID,
        alerts: list[RawAlert],
        root_cause: str | None = None,
        resolution: str | None = None,
    ) -> None:
        """Record an incident for pattern learning."""
        self._incident_history.append((incident_id, alerts))

        if len(self._incident_history) > 1000:
            self._incident_history = self._incident_history[-1000:]

    def find_matching_pattern(
        self,
        alerts: list[RawAlert],
    ) -> tuple[LearnedPattern | None, float]:
        """Find best matching pattern for a set of alerts."""
        best_pattern: LearnedPattern | None = None
        best_score = 0.0

        for pattern in self._patterns.values():
            if pattern.confidence < self.config.min_pattern_confidence:
                continue

            score = pattern.matches(alerts)
            if score > best_score and score >= 0.6:
                best_score = score
                best_pattern = pattern

        return best_pattern, best_score

    def learn_new_patterns(self) -> list[LearnedPattern]:
        """Analyze incident history to learn new patterns."""
        if len(self._incident_history) < self.config.pattern_min_occurrences:
            return []

        new_patterns = []
        alert_sequences = self._extract_alert_sequences()

        for sequence in self._find_frequent_sequences(alert_sequences):
            if self._is_duplicate_pattern(sequence):
                continue

            pattern = LearnedPattern(
                name=f"Learned: {sequence[0].get('summary', 'Unknown')}",
                description=f"Auto-learned pattern with {len(sequence)} signals",
                signal_sequence=sequence,
                occurrence_count=self._count_sequence_occurrences(
                    sequence, alert_sequences
                ),
                confidence=0.75,
            )

            if pattern.occurrence_count >= self.config.pattern_min_occurrences:
                self._patterns[pattern.pattern_id] = pattern
                new_patterns.append(pattern)

        return new_patterns

    def _extract_alert_sequences(self) -> list[list[dict[str, Any]]]:
        """Extract alert sequences from incident history."""
        sequences = []
        for _, alerts in self._incident_history:
            sorted_alerts = sorted(alerts, key=lambda a: a.timestamp)
            sequence = [
                {
                    "service": a.service,
                    "alert_type": a.alert_type,
                    "severity": a.severity.value,
                    "summary": a.alert_name,
                }
                for a in sorted_alerts
            ]
            sequences.append(sequence)
        return sequences

    def _find_frequent_sequences(
        self,
        sequences: list[list[dict[str, Any]]],
    ) -> list[list[dict[str, Any]]]:
        """Find frequently occurring subsequences."""
        subsequence_counts: dict[str, tuple[list[dict[str, Any]], int]] = {}

        for seq in sequences:
            for length in range(2, min(len(seq) + 1, 5)):
                for i in range(len(seq) - length + 1):
                    subseq = seq[i : i + length]
                    key = self._sequence_key(subseq)
                    if key in subsequence_counts:
                        _, count = subsequence_counts[key]
                        subsequence_counts[key] = (subseq, count + 1)
                    else:
                        subsequence_counts[key] = (subseq, 1)

        frequent = [
            subseq
            for subseq, count in subsequence_counts.values()
            if count >= self.config.pattern_min_occurrences
        ]

        return frequent

    def _sequence_key(self, sequence: list[dict[str, Any]]) -> str:
        """Generate unique key for a sequence."""
        parts = []
        for signal in sequence:
            parts.append(
                f"{signal.get('service', '')}:{signal.get('alert_type', '')}:"
                f"{signal.get('severity', '')}"
            )
        return "|".join(parts)

    def _is_duplicate_pattern(self, sequence: list[dict[str, Any]]) -> bool:
        """Check if sequence matches an existing pattern."""
        for pattern in self._patterns.values():
            if len(pattern.signal_sequence) == len(sequence):
                if self._sequence_key(pattern.signal_sequence) == self._sequence_key(
                    sequence
                ):
                    return True
        return False

    def _count_sequence_occurrences(
        self,
        sequence: list[dict[str, Any]],
        all_sequences: list[list[dict[str, Any]]],
    ) -> int:
        """Count how many times a sequence occurs."""
        key = self._sequence_key(sequence)
        count = 0
        for seq in all_sequences:
            for i in range(len(seq) - len(sequence) + 1):
                subseq = seq[i : i + len(sequence)]
                if self._sequence_key(subseq) == key:
                    count += 1
                    break
        return count

    def get_all_patterns(self) -> list[LearnedPattern]:
        """Get all learned patterns."""
        return list(self._patterns.values())

    def get_pattern(self, pattern_id: UUID) -> LearnedPattern | None:
        """Get a specific pattern."""
        return self._patterns.get(pattern_id)

    def disable_pattern(self, pattern_id: UUID) -> bool:
        """Disable a pattern."""
        if pattern_id in self._patterns:
            del self._patterns[pattern_id]
            return True
        return False


class AnomalyClusterer:
    """Cluster alerts using density-based clustering."""

    def __init__(self, config: MLConfig | None = None):
        self.config = config or MLConfig()
        self._clusters: dict[UUID, CorrelationCluster] = {}
        self._alert_features: dict[UUID, ClusterFeatures] = {}
        self._cluster_assignments: dict[UUID, UUID] = {}

    def extract_features(self, alert: RawAlert) -> ClusterFeatures:
        """Extract features from an alert for clustering."""
        features = ClusterFeatures(
            alert_id=alert.alert_id,
            service_hash=hash(alert.service) if alert.service else 0,
            alert_type_hash=hash(alert.alert_type) if alert.alert_type else 0,
            severity_numeric=self._severity_to_numeric(alert.severity.value),
            time_bucket=int(alert.timestamp.timestamp() / 300),
        )

        text = f"{alert.service} {alert.alert_name} {alert.message}"
        features.message_embedding = self._simple_embedding(text)

        self._alert_features[alert.alert_id] = features
        return features

    def _severity_to_numeric(self, severity: str) -> float:
        """Convert severity to numeric value."""
        mapping = {
            "critical": 1.0,
            "high": 0.8,
            "medium": 0.5,
            "low": 0.3,
            "info": 0.1,
        }
        return mapping.get(severity.lower(), 0.5)

    def _simple_embedding(self, text: str, dim: int = 32) -> list[float]:
        """Generate simple embedding using character hashing."""
        import hashlib

        embedding = [0.0] * dim
        words = text.lower().split()

        for i, word in enumerate(words):
            h = int(hashlib.md5(word.encode()).hexdigest()[:8], 16)
            for j in range(dim):
                if (h >> (j % 32)) & 1:
                    embedding[j] += 1.0 / (i + 1)

        norm = math.sqrt(sum(x * x for x in embedding))
        if norm > 0:
            embedding = [x / norm for x in embedding]

        return embedding

    def find_cluster(
        self,
        alert: RawAlert,
    ) -> CorrelationCluster | None:
        """Find existing cluster for an alert."""
        features = self.extract_features(alert)
        vector = features.to_vector()

        best_cluster: CorrelationCluster | None = None
        best_distance = float("inf")

        for cluster in self._clusters.values():
            if not cluster.centroid:
                continue

            distance = self._euclidean_distance(vector, cluster.centroid)
            if distance < self.config.clustering_eps and distance < best_distance:
                best_distance = distance
                best_cluster = cluster

        return best_cluster

    def add_to_cluster(
        self,
        alert: RawAlert,
        cluster: CorrelationCluster,
    ) -> None:
        """Add alert to existing cluster."""
        cluster.alerts.append(alert.alert_id)
        self._cluster_assignments[alert.alert_id] = cluster.cluster_id

        features = self._alert_features.get(alert.alert_id)
        if features:
            vector = features.to_vector()
            self._update_centroid(cluster, vector)

    def create_cluster(
        self,
        alert: RawAlert,
        label: str | None = None,
    ) -> CorrelationCluster:
        """Create new cluster with alert."""
        features = self._alert_features.get(alert.alert_id)
        if not features:
            features = self.extract_features(alert)

        cluster = CorrelationCluster(
            alerts=[alert.alert_id],
            cluster_label=label or f"Cluster: {alert.service} {alert.alert_name}",
            confidence=0.5,
            centroid=features.to_vector(),
        )

        self._clusters[cluster.cluster_id] = cluster
        self._cluster_assignments[alert.alert_id] = cluster.cluster_id

        return cluster

    def _update_centroid(
        self,
        cluster: CorrelationCluster,
        new_vector: list[float],
    ) -> None:
        """Update cluster centroid with new vector."""
        if not cluster.centroid:
            cluster.centroid = new_vector
            return

        n = len(cluster.alerts)
        cluster.centroid = [
            (c * (n - 1) + v) / n for c, v in zip(cluster.centroid, new_vector)
        ]

    def _euclidean_distance(self, v1: list[float], v2: list[float]) -> float:
        """Calculate Euclidean distance between vectors."""
        if len(v1) != len(v2):
            return float("inf")
        return math.sqrt(sum((a - b) ** 2 for a, b in zip(v1, v2)))

    def recluster_all(self) -> list[CorrelationCluster]:
        """Recluster all alerts using DBSCAN-like algorithm."""
        self._clusters.clear()
        self._cluster_assignments.clear()

        features_list = list(self._alert_features.values())
        if not features_list:
            return []

        vectors = [f.to_vector() for f in features_list]
        labels = self._dbscan(vectors)

        new_clusters = []
        cluster_map: dict[int, CorrelationCluster] = {}

        for features, label in zip(features_list, labels):
            if label == -1:
                continue

            if label not in cluster_map:
                cluster = CorrelationCluster(
                    cluster_label=f"Cluster {label}",
                    confidence=0.7,
                )
                cluster_map[label] = cluster
                self._clusters[cluster.cluster_id] = cluster
                new_clusters.append(cluster)

            cluster = cluster_map[label]
            cluster.alerts.append(features.alert_id)
            self._cluster_assignments[features.alert_id] = cluster.cluster_id

        for cluster in new_clusters:
            self._recompute_centroid(cluster)

        return new_clusters

    def _dbscan(self, vectors: list[list[float]]) -> list[int]:
        """Simple DBSCAN implementation."""
        n = len(vectors)
        labels = [-1] * n
        cluster_id = 0

        for i in range(n):
            if labels[i] != -1:
                continue

            neighbors = self._find_neighbors(vectors, i)
            if len(neighbors) < self.config.clustering_min_samples:
                continue

            labels[i] = cluster_id
            seed_set = list(neighbors)

            j = 0
            while j < len(seed_set):
                q = seed_set[j]
                if labels[q] == -1:
                    labels[q] = cluster_id
                    q_neighbors = self._find_neighbors(vectors, q)
                    if len(q_neighbors) >= self.config.clustering_min_samples:
                        seed_set.extend(
                            [x for x in q_neighbors if x not in seed_set]
                        )
                j += 1

            cluster_id += 1

        return labels

    def _find_neighbors(
        self,
        vectors: list[list[float]],
        index: int,
    ) -> list[int]:
        """Find neighbors within eps distance."""
        neighbors = []
        for i, v in enumerate(vectors):
            if self._euclidean_distance(vectors[index], v) <= self.config.clustering_eps:
                neighbors.append(i)
        return neighbors

    def _recompute_centroid(self, cluster: CorrelationCluster) -> None:
        """Recompute cluster centroid from all member alerts."""
        if not cluster.alerts:
            return

        vectors = []
        for alert_id in cluster.alerts:
            features = self._alert_features.get(alert_id)
            if features:
                vectors.append(features.to_vector())

        if not vectors:
            return

        dim = len(vectors[0])
        centroid = [0.0] * dim
        for v in vectors:
            for i, val in enumerate(v):
                centroid[i] += val
        centroid = [c / len(vectors) for c in centroid]
        cluster.centroid = centroid

    def get_cluster(self, cluster_id: UUID) -> CorrelationCluster | None:
        """Get cluster by ID."""
        return self._clusters.get(cluster_id)

    def get_alert_cluster(self, alert_id: UUID) -> CorrelationCluster | None:
        """Get cluster containing an alert."""
        cluster_id = self._cluster_assignments.get(alert_id)
        if cluster_id:
            return self._clusters.get(cluster_id)
        return None

    def get_stats(self) -> dict[str, Any]:
        """Get clustering statistics."""
        return {
            "total_clusters": len(self._clusters),
            "total_alerts": len(self._alert_features),
            "assigned_alerts": len(self._cluster_assignments),
            "cluster_sizes": {
                str(c.cluster_id): len(c.alerts) for c in self._clusters.values()
            },
        }


class ClusterLabeler:
    """Generate human-readable labels for clusters."""

    def __init__(self):
        self._label_templates = {
            "service_focused": "{service} cluster: {alert_types}",
            "issue_focused": "{issue_type} affecting {services}",
            "severity_focused": "{severity} alerts from {service}",
        }

    def generate_label(
        self,
        cluster: CorrelationCluster,
        alerts: list[RawAlert],
    ) -> str:
        """Generate descriptive label for cluster."""
        if not alerts:
            return cluster.cluster_label

        services = list(set(a.service for a in alerts if a.service))
        alert_types = list(set(a.alert_type for a in alerts if a.alert_type))
        severities = list(set(a.severity.value for a in alerts))

        if len(services) == 1:
            return self._label_templates["service_focused"].format(
                service=services[0],
                alert_types=", ".join(alert_types[:3]),
            )

        issue_type = self._infer_issue_type(alerts)
        if issue_type:
            return self._label_templates["issue_focused"].format(
                issue_type=issue_type,
                services=", ".join(services[:3]),
            )

        if len(severities) == 1 and severities[0] in ["critical", "high"]:
            return self._label_templates["severity_focused"].format(
                severity=severities[0].upper(),
                service=services[0] if services else "multiple services",
            )

        return f"Alert cluster: {len(alerts)} alerts from {len(services)} services"

    def _infer_issue_type(self, alerts: list[RawAlert]) -> str | None:
        """Infer the type of issue from alerts."""
        messages = " ".join(a.message.lower() for a in alerts if a.message)

        issue_patterns = [
            ("database", ["database", "db", "sql", "connection pool", "query"]),
            ("latency", ["latency", "slow", "timeout", "response time"]),
            ("memory", ["memory", "oom", "heap", "gc"]),
            ("cpu", ["cpu", "throttle", "processor"]),
            ("disk", ["disk", "storage", "filesystem"]),
            ("network", ["network", "connection", "dns", "socket"]),
            ("deployment", ["deploy", "rollout", "release"]),
        ]

        for issue_type, keywords in issue_patterns:
            if any(kw in messages for kw in keywords):
                return issue_type

        return None

    def generate_analysis(
        self,
        cluster: CorrelationCluster,
        alerts: list[RawAlert],
    ) -> str:
        """Generate AI-style analysis for cluster."""
        if not alerts:
            return "No alerts to analyze."

        services = list(set(a.service for a in alerts if a.service))
        issue_type = self._infer_issue_type(alerts)

        analysis_parts = []
        analysis_parts.append(
            f"Detected {len(alerts)} related alerts from {len(services)} service(s)."
        )

        if issue_type:
            analysis_parts.append(f"Primary issue appears to be: {issue_type}.")

        if len(services) > 1:
            analysis_parts.append(
                "Multiple services affected suggests upstream dependency issue."
            )

        timestamps = sorted(a.timestamp for a in alerts)
        if len(timestamps) >= 2:
            duration = (timestamps[-1] - timestamps[0]).total_seconds()
            analysis_parts.append(f"Event span: {duration:.0f} seconds.")

        return " ".join(analysis_parts)


class MLCorrelationEngine:
    """Main ML correlation engine combining all components."""

    def __init__(self, config: MLConfig | None = None):
        self.config = config or MLConfig()
        self._pattern_learner = PatternLearner(config)
        self._clusterer = AnomalyClusterer(config)
        self._labeler = ClusterLabeler()
        self._last_retrain: datetime | None = None

    def process_alert(
        self,
        alert: RawAlert,
        recent_alerts: list[RawAlert] | None = None,
    ) -> tuple[CorrelationCluster | None, LearnedPattern | None, float]:
        """Process alert through ML correlation."""
        pattern, pattern_score = None, 0.0
        if recent_alerts:
            pattern, pattern_score = self._pattern_learner.find_matching_pattern(
                recent_alerts + [alert]
            )

        cluster = self._clusterer.find_cluster(alert)
        if cluster:
            self._clusterer.add_to_cluster(alert, cluster)
            cluster.confidence = max(cluster.confidence, pattern_score)
        else:
            cluster = self._clusterer.create_cluster(alert)

        confidence = max(cluster.confidence, pattern_score) if cluster else pattern_score

        return cluster, pattern, confidence

    def record_incident_resolution(
        self,
        incident_id: UUID,
        alerts: list[RawAlert],
        root_cause: str | None = None,
        resolution: str | None = None,
    ) -> None:
        """Record resolved incident for learning."""
        self._pattern_learner.record_incident(
            incident_id, alerts, root_cause, resolution
        )

        if self._should_retrain():
            self._retrain()

    def _should_retrain(self) -> bool:
        """Check if retraining is needed."""
        if not self._last_retrain:
            return True

        hours_since = (
            datetime.utcnow() - self._last_retrain
        ).total_seconds() / 3600
        return hours_since >= self.config.retrain_interval_hours

    def _retrain(self) -> None:
        """Retrain ML models."""
        log.info("Retraining ML correlation models")

        new_patterns = self._pattern_learner.learn_new_patterns()
        if new_patterns:
            log.info("Learned %d new patterns", len(new_patterns))

        new_clusters = self._clusterer.recluster_all()
        if new_clusters:
            log.info("Created %d clusters", len(new_clusters))

        self._last_retrain = datetime.utcnow()

    def get_cluster_label(
        self,
        cluster: CorrelationCluster,
        alerts: list[RawAlert],
    ) -> str:
        """Get human-readable label for cluster."""
        return self._labeler.generate_label(cluster, alerts)

    def get_cluster_analysis(
        self,
        cluster: CorrelationCluster,
        alerts: list[RawAlert],
    ) -> str:
        """Get AI-style analysis for cluster."""
        return self._labeler.generate_analysis(cluster, alerts)

    def get_all_patterns(self) -> list[LearnedPattern]:
        """Get all learned patterns."""
        return self._pattern_learner.get_all_patterns()

    def get_stats(self) -> dict[str, Any]:
        """Get ML engine statistics."""
        return {
            "patterns": {
                "total": len(self._pattern_learner.get_all_patterns()),
            },
            "clustering": self._clusterer.get_stats(),
            "last_retrain": self._last_retrain.isoformat()
            if self._last_retrain
            else None,
        }
