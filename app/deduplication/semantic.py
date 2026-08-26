"""Layer 2: Semantic similarity detection using embeddings."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from app.deduplication.models import (
    RawAlert,
    SemanticMatch,
)

log = logging.getLogger("omnipotent.deduplication.semantic")


@dataclass
class SemanticConfig:
    """Configuration for semantic similarity."""

    similarity_threshold: float = 0.85
    embedding_model: str = "all-MiniLM-L6-v2"
    cache_ttl_hours: int = 24
    max_candidates: int = 100
    use_entity_extraction: bool = True
    use_learned_equivalences: bool = True


@dataclass
class ExtractedEntities:
    """Entities extracted from alert message."""

    service: str | None = None
    metric_type: str | None = None
    metric_name: str | None = None
    threshold_value: float | None = None
    current_value: float | None = None
    direction: str | None = None
    resource: str | None = None
    error_type: str | None = None
    duration: str | None = None
    percentile: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            k: v
            for k, v in {
                "service": self.service,
                "metric_type": self.metric_type,
                "metric_name": self.metric_name,
                "threshold_value": self.threshold_value,
                "current_value": self.current_value,
                "direction": self.direction,
                "resource": self.resource,
                "error_type": self.error_type,
                "duration": self.duration,
                "percentile": self.percentile,
            }.items()
            if v is not None
        }


class EntityExtractor:
    """Extract structured entities from alert messages."""

    METRIC_PATTERNS = {
        "latency": [
            r"latency[:\s><=]+(\d+\.?\d*)\s*(ms|s|seconds?|milliseconds?)?",
            r"response[\s_]?time[:\s><=]+(\d+\.?\d*)\s*(ms|s)?",
            r"duration[:\s><=]+(\d+\.?\d*)\s*(ms|s)?",
            r"p(\d+)[:\s><=]+(\d+\.?\d*)\s*(ms|s)?",
        ],
        "error_rate": [
            r"error[\s_]?rate[:\s><=]+(\d+\.?\d*)\s*%?",
            r"(\d+\.?\d*)\s*%?\s*(5xx|errors?|failures?)",
            r"failure[\s_]?rate[:\s><=]+(\d+\.?\d*)\s*%?",
        ],
        "cpu": [
            r"cpu[:\s><=]+(\d+\.?\d*)\s*%?",
            r"cpu[\s_]?(usage|utilization)[:\s><=]+(\d+\.?\d*)\s*%?",
        ],
        "memory": [
            r"memory[:\s><=]+(\d+\.?\d*)\s*(%|GB|MB|GiB|MiB)?",
            r"(mem|ram)[:\s><=]+(\d+\.?\d*)\s*(%|GB|MB)?",
            r"oom|out[\s_]?of[\s_]?memory",
        ],
        "disk": [
            r"disk[:\s><=]+(\d+\.?\d*)\s*(%|GB|TB)?",
            r"storage[:\s><=]+(\d+\.?\d*)\s*(%|GB|TB)?",
        ],
        "connections": [
            r"connection[\s_]?(pool|count)?[:\s><=]+(\d+)",
            r"(\d+)\s*connections?",
        ],
        "requests": [
            r"(rps|qps|requests?[\s_]?per[\s_]?sec)[:\s><=]+(\d+\.?\d*)",
            r"throughput[:\s><=]+(\d+\.?\d*)",
        ],
    }

    DIRECTION_PATTERNS = [
        (r">\s*|above|exceeded|high|increased|spike", "high"),
        (r"<\s*|below|under|low|decreased|drop", "low"),
        (r"=\s*|equals?|at", "equal"),
    ]

    SERVICE_PATTERNS = [
        r"service[:\s]+([a-zA-Z0-9_-]+)",
        r"([a-zA-Z0-9_-]+)[\s_]?(service|api|server)",
        r"from[\s]+([a-zA-Z0-9_-]+)",
    ]

    def extract(self, alert: RawAlert) -> ExtractedEntities:
        """Extract entities from alert."""
        text = f"{alert.alert_name} {alert.message}".lower()
        entities = ExtractedEntities()

        entities.service = alert.service or self._extract_service(text)
        entities.metric_type = self._extract_metric_type(text)
        entities.direction = self._extract_direction(text)

        value_match = re.search(r"(\d+\.?\d*)\s*(ms|s|%|GB|MB)?", text)
        if value_match:
            try:
                entities.current_value = float(value_match.group(1))
            except ValueError:
                pass

        threshold_match = re.search(
            r"threshold[:\s><=]+(\d+\.?\d*)", text, re.IGNORECASE
        )
        if threshold_match:
            try:
                entities.threshold_value = float(threshold_match.group(1))
            except ValueError:
                pass

        percentile_match = re.search(r"p(\d+)", text)
        if percentile_match:
            entities.percentile = f"p{percentile_match.group(1)}"

        error_patterns = [
            "timeout",
            "connection refused",
            "5xx",
            "500",
            "503",
            "504",
            "oom",
            "crash",
            "panic",
            "exception",
        ]
        for pattern in error_patterns:
            if pattern in text:
                entities.error_type = pattern
                break

        return entities

    def _extract_service(self, text: str) -> str | None:
        """Extract service name from text."""
        for pattern in self.SERVICE_PATTERNS:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1)
        return None

    def _extract_metric_type(self, text: str) -> str | None:
        """Extract metric type from text."""
        for metric_type, patterns in self.METRIC_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, text, re.IGNORECASE):
                    return metric_type
        return None

    def _extract_direction(self, text: str) -> str | None:
        """Extract direction (high/low) from text."""
        for pattern, direction in self.DIRECTION_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                return direction
        return None


class MetricEquivalenceMapper:
    """Map equivalent metric names across different systems."""

    EQUIVALENCES = {
        "latency": {
            "latency",
            "response_time",
            "duration",
            "http_request_duration_seconds",
            "request_latency",
            "request_duration",
            "p50",
            "p95",
            "p99",
        },
        "error_rate": {
            "error_rate",
            "error_percentage",
            "5xx_rate",
            "failures",
            "failed_requests",
            "errors",
            "http_errors",
            "error_count",
        },
        "cpu": {
            "cpu",
            "cpu_usage",
            "cpu_utilization",
            "cpu_percent",
            "processor",
            "cpu_load",
        },
        "memory": {
            "memory",
            "mem",
            "ram",
            "memory_usage",
            "memory_utilization",
            "heap",
            "rss",
        },
        "disk": {
            "disk",
            "disk_usage",
            "storage",
            "disk_utilization",
            "filesystem",
            "volume",
        },
        "connections": {
            "connections",
            "connection_pool",
            "active_connections",
            "connection_count",
            "open_connections",
            "db_connections",
        },
        "throughput": {
            "rps",
            "qps",
            "requests_per_second",
            "throughput",
            "request_rate",
            "traffic",
        },
    }

    def __init__(self):
        self._reverse_map: dict[str, str] = {}
        self._custom_mappings: dict[str, set[str]] = {}

        for category, terms in self.EQUIVALENCES.items():
            for term in terms:
                self._reverse_map[term.lower()] = category

    def get_category(self, metric_name: str) -> str | None:
        """Get the canonical category for a metric name."""
        normalized = metric_name.lower().replace("-", "_").replace(" ", "_")
        return self._reverse_map.get(normalized)

    def are_equivalent(self, metric1: str, metric2: str) -> bool:
        """Check if two metrics are equivalent."""
        cat1 = self.get_category(metric1)
        cat2 = self.get_category(metric2)

        if cat1 and cat2 and cat1 == cat2:
            return True

        return False

    def add_equivalence(self, term: str, category: str) -> None:
        """Add custom equivalence mapping."""
        normalized = term.lower().replace("-", "_").replace(" ", "_")
        self._reverse_map[normalized] = category

        if category not in self._custom_mappings:
            self._custom_mappings[category] = set()
        self._custom_mappings[category].add(normalized)

    def get_all_equivalents(self, metric_name: str) -> set[str]:
        """Get all equivalent metric names."""
        category = self.get_category(metric_name)
        if not category:
            return {metric_name}

        equivalents = self.EQUIVALENCES.get(category, set()).copy()
        equivalents.update(self._custom_mappings.get(category, set()))
        return equivalents


@dataclass
class CachedEmbedding:
    """Cached embedding with metadata."""

    alert_id: UUID
    embedding: list[float]
    text: str
    created_at: datetime = field(default_factory=datetime.utcnow)
    incident_id: UUID | None = None


class EmbeddingCache:
    """Cache for alert embeddings."""

    def __init__(self, ttl_hours: int = 24):
        self.ttl = timedelta(hours=ttl_hours)
        self._cache: dict[UUID, CachedEmbedding] = {}
        self._text_cache: dict[str, list[float]] = {}

    def get(self, alert_id: UUID) -> list[float] | None:
        """Get cached embedding by alert ID."""
        cached = self._cache.get(alert_id)
        if cached and datetime.utcnow() - cached.created_at < self.ttl:
            return cached.embedding
        return None

    def get_by_text(self, text: str) -> list[float] | None:
        """Get cached embedding by text."""
        return self._text_cache.get(text)

    def put(
        self,
        alert_id: UUID,
        embedding: list[float],
        text: str,
        incident_id: UUID | None = None,
    ) -> None:
        """Cache an embedding."""
        self._cache[alert_id] = CachedEmbedding(
            alert_id=alert_id,
            embedding=embedding,
            text=text,
            incident_id=incident_id,
        )
        self._text_cache[text] = embedding

    def get_all_with_incident(self) -> list[CachedEmbedding]:
        """Get all cached embeddings with incident IDs."""
        cutoff = datetime.utcnow() - self.ttl
        return [
            cached
            for cached in self._cache.values()
            if cached.incident_id and cached.created_at > cutoff
        ]

    def cleanup(self) -> int:
        """Remove expired entries."""
        cutoff = datetime.utcnow() - self.ttl
        expired = [
            aid for aid, cached in self._cache.items() if cached.created_at < cutoff
        ]
        for aid in expired:
            cached = self._cache.pop(aid, None)
            if cached:
                self._text_cache.pop(cached.text, None)
        return len(expired)

    def stats(self) -> dict[str, Any]:
        """Get cache statistics."""
        return {
            "total_entries": len(self._cache),
            "text_cache_entries": len(self._text_cache),
            "with_incident": sum(
                1 for c in self._cache.values() if c.incident_id
            ),
        }


class SemanticSimilarityEngine:
    """Semantic similarity detection using multiple methods."""

    def __init__(self, config: SemanticConfig | None = None):
        self.config = config or SemanticConfig()
        self._entity_extractor = EntityExtractor()
        self._equivalence_mapper = MetricEquivalenceMapper()
        self._embedding_cache = EmbeddingCache(self.config.cache_ttl_hours)
        self._incident_alerts: dict[UUID, list[RawAlert]] = {}
        self._model: Any = None

    def _load_model(self) -> None:
        """Lazy load embedding model."""
        if self._model is not None:
            return

        try:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.config.embedding_model)
            log.info("Loaded embedding model: %s", self.config.embedding_model)
        except ImportError:
            log.warning(
                "sentence-transformers not installed, using fallback similarity"
            )
            self._model = None

    def register_alert(self, alert: RawAlert, incident_id: UUID) -> None:
        """Register an alert with its incident."""
        if incident_id not in self._incident_alerts:
            self._incident_alerts[incident_id] = []
        self._incident_alerts[incident_id].append(alert)

        text = self._get_alert_text(alert)
        embedding = self._get_embedding(text)
        if embedding:
            self._embedding_cache.put(alert.alert_id, embedding, text, incident_id)

    def find_similar(
        self,
        alert: RawAlert,
        threshold: float | None = None,
    ) -> SemanticMatch | None:
        """Find semantically similar alerts."""
        threshold = threshold or self.config.similarity_threshold

        if self.config.use_entity_extraction:
            entity_match = self._find_by_entity_matching(alert, threshold)
            if entity_match:
                return entity_match

        embedding_match = self._find_by_embedding(alert, threshold)
        if embedding_match:
            return embedding_match

        if self.config.use_learned_equivalences:
            equiv_match = self._find_by_equivalence(alert, threshold)
            if equiv_match:
                return equiv_match

        return None

    def _find_by_embedding(
        self,
        alert: RawAlert,
        threshold: float,
    ) -> SemanticMatch | None:
        """Find similar alerts using embedding similarity."""
        self._load_model()

        text = self._get_alert_text(alert)
        embedding = self._get_embedding(text)

        if not embedding:
            return None

        cached_embeddings = self._embedding_cache.get_all_with_incident()
        if not cached_embeddings:
            return None

        best_match: SemanticMatch | None = None
        best_score = threshold

        for cached in cached_embeddings:
            if cached.alert_id == alert.alert_id:
                continue

            score = self._cosine_similarity(embedding, cached.embedding)
            if score > best_score and cached.incident_id:
                best_score = score
                best_match = SemanticMatch(
                    alert_id=cached.alert_id,
                    incident_id=cached.incident_id,
                    similarity_score=score,
                    method="embedding",
                    embedding_distance=1 - score,
                )

        return best_match

    def _find_by_entity_matching(
        self,
        alert: RawAlert,
        threshold: float,
    ) -> SemanticMatch | None:
        """Find similar alerts using entity extraction."""
        entities = self._entity_extractor.extract(alert)

        if not entities.service and not entities.metric_type:
            return None

        for incident_id, alerts in self._incident_alerts.items():
            for existing_alert in alerts:
                if existing_alert.alert_id == alert.alert_id:
                    continue

                existing_entities = self._entity_extractor.extract(existing_alert)
                score = self._entity_similarity(entities, existing_entities)

                if score >= threshold:
                    return SemanticMatch(
                        alert_id=existing_alert.alert_id,
                        incident_id=incident_id,
                        similarity_score=score,
                        method="entity_extraction",
                        matched_entities={
                            "service_match": str(
                                entities.service == existing_entities.service
                            ),
                            "metric_match": str(
                                entities.metric_type == existing_entities.metric_type
                            ),
                        },
                    )

        return None

    def _find_by_equivalence(
        self,
        alert: RawAlert,
        threshold: float,
    ) -> SemanticMatch | None:
        """Find similar alerts using metric equivalence."""
        entities = self._entity_extractor.extract(alert)

        if not entities.metric_type:
            return None

        equivalents = self._equivalence_mapper.get_all_equivalents(entities.metric_type)

        for incident_id, alerts in self._incident_alerts.items():
            for existing_alert in alerts:
                if existing_alert.alert_id == alert.alert_id:
                    continue

                if (
                    entities.service
                    and existing_alert.service
                    and entities.service != existing_alert.service
                ):
                    continue

                existing_entities = self._entity_extractor.extract(existing_alert)
                if existing_entities.metric_type in equivalents:
                    return SemanticMatch(
                        alert_id=existing_alert.alert_id,
                        incident_id=incident_id,
                        similarity_score=0.90,
                        method="metric_equivalence",
                        matched_entities={
                            "metric1": entities.metric_type or "",
                            "metric2": existing_entities.metric_type or "",
                            "category": self._equivalence_mapper.get_category(
                                entities.metric_type
                            )
                            or "",
                        },
                    )

        return None

    def _entity_similarity(
        self,
        e1: ExtractedEntities,
        e2: ExtractedEntities,
    ) -> float:
        """Calculate similarity between two entity sets."""
        score = 0.0
        weights = {
            "service": 0.35,
            "metric_type": 0.30,
            "direction": 0.15,
            "error_type": 0.20,
        }

        if e1.service and e2.service and e1.service == e2.service:
            score += weights["service"]
        elif e1.service and e2.service:
            score += 0

        if e1.metric_type and e2.metric_type:
            if e1.metric_type == e2.metric_type:
                score += weights["metric_type"]
            elif self._equivalence_mapper.are_equivalent(
                e1.metric_type, e2.metric_type
            ):
                score += weights["metric_type"] * 0.9

        if e1.direction and e2.direction and e1.direction == e2.direction:
            score += weights["direction"]

        if e1.error_type and e2.error_type and e1.error_type == e2.error_type:
            score += weights["error_type"]

        return score

    def _get_alert_text(self, alert: RawAlert) -> str:
        """Get text representation of alert for embedding."""
        parts = [
            alert.service,
            alert.alert_name,
            alert.message,
        ]
        return " ".join(p for p in parts if p)

    def _get_embedding(self, text: str) -> list[float] | None:
        """Get embedding for text."""
        cached = self._embedding_cache.get_by_text(text)
        if cached:
            return cached

        if self._model is None:
            return self._fallback_embedding(text)

        try:
            embedding = self._model.encode(text).tolist()
            return embedding
        except Exception as e:
            log.warning("Failed to generate embedding: %s", e)
            return self._fallback_embedding(text)

    def _fallback_embedding(self, text: str) -> list[float]:
        """Simple fallback embedding using word hashing."""
        import hashlib

        words = text.lower().split()
        embedding = [0.0] * 128

        for i, word in enumerate(words):
            word_hash = int(hashlib.md5(word.encode(), usedforsecurity=False).hexdigest()[:8], 16)
            for j in range(128):
                if (word_hash >> (j % 32)) & 1:
                    embedding[j] += 1.0 / (i + 1)

        norm = sum(x * x for x in embedding) ** 0.5
        if norm > 0:
            embedding = [x / norm for x in embedding]

        return embedding

    def _cosine_similarity(self, v1: list[float], v2: list[float]) -> float:
        """Calculate cosine similarity between two vectors."""
        if len(v1) != len(v2):
            return 0.0

        dot_product = sum(a * b for a, b in zip(v1, v2))
        norm1 = sum(a * a for a in v1) ** 0.5
        norm2 = sum(b * b for b in v2) ** 0.5

        if norm1 == 0 or norm2 == 0:
            return 0.0

        return dot_product / (norm1 * norm2)

    def compare_alerts(
        self,
        alert1: RawAlert,
        alert2: RawAlert,
    ) -> dict[str, Any]:
        """Compare two alerts for similarity."""
        e1 = self._entity_extractor.extract(alert1)
        e2 = self._entity_extractor.extract(alert2)

        t1 = self._get_alert_text(alert1)
        t2 = self._get_alert_text(alert2)

        emb1 = self._get_embedding(t1)
        emb2 = self._get_embedding(t2)

        embedding_sim = 0.0
        if emb1 and emb2:
            embedding_sim = self._cosine_similarity(emb1, emb2)

        entity_sim = self._entity_similarity(e1, e2)

        return {
            "alert1_entities": e1.to_dict(),
            "alert2_entities": e2.to_dict(),
            "embedding_similarity": embedding_sim,
            "entity_similarity": entity_sim,
            "combined_score": (embedding_sim * 0.6) + (entity_sim * 0.4),
            "metric_equivalent": self._equivalence_mapper.are_equivalent(
                e1.metric_type or "", e2.metric_type or ""
            ),
            "recommendation": "MERGE"
            if (embedding_sim * 0.6 + entity_sim * 0.4) >= self.config.similarity_threshold
            else "SEPARATE",
        }

    def get_stats(self) -> dict[str, Any]:
        """Get semantic engine statistics."""
        return {
            "cache_stats": self._embedding_cache.stats(),
            "incidents_tracked": len(self._incident_alerts),
            "total_alerts": sum(
                len(alerts) for alerts in self._incident_alerts.values()
            ),
            "model_loaded": self._model is not None,
            "config": {
                "threshold": self.config.similarity_threshold,
                "model": self.config.embedding_model,
            },
        }
