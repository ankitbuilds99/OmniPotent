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
    # `sentence-transformers` lives in the optional `vectors-local` extra and is not
    # installed in the deployed image, so the fallback below is the live path. Its
    # scores are token-overlap cosines, a different scale from a transformer cosine,
    # and they get their own threshold rather than borrowing one calibrated for a
    # model that is not running. At 0.85 an overlap match means near-identical
    # wording, which is the only claim this measure can honestly make.
    lexical_similarity_threshold: float = 0.85
    embedding_model: str = "all-MiniLM-L6-v2"
    cache_ttl_hours: int = 24
    max_candidates: int = 100
    use_entity_extraction: bool = True
    use_learned_equivalences: bool = True
    # Two alerts from different services describing different failures are two
    # incidents, however alike their wording. `_find_by_equivalence` has always
    # enforced this; the text path did not, which is how an OOM on one service was
    # merged into another service's certificate incident. Despite the name — kept
    # because it is a stored configuration key — this switch governs the invariant
    # on every Layer 2 branch, entity matching included, so that turning it off is
    # one decision rather than three different ones per code path.
    require_same_service_for_text_match: bool = True


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
    """Cached embedding with metadata.

    `embedding` is empty when no embedding model is loaded: the text similarity
    path then works from `text` directly instead of from a vector that was never a
    real embedding. `service` is carried so a candidate can be rejected without a
    lookup back into the incident store.
    """

    alert_id: UUID
    embedding: list[float]
    text: str
    created_at: datetime = field(default_factory=datetime.utcnow)
    incident_id: UUID | None = None
    service: str = ""


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
        service: str = "",
    ) -> None:
        """Cache an embedding, or just the text when there is no embedding."""
        self._cache[alert_id] = CachedEmbedding(
            alert_id=alert_id,
            embedding=embedding,
            text=text,
            incident_id=incident_id,
            service=service,
        )
        if embedding:
            self._text_cache[text] = embedding

    def get_all_with_incident(self) -> list[CachedEmbedding]:
        """Get all cached embeddings with incident IDs."""
        cutoff = datetime.utcnow() - self.ttl
        return [
            cached
            for cached in self._cache.values()
            if cached.incident_id and cached.created_at > cutoff
        ]

    def forget_incident(self, incident_id: UUID) -> int:
        """Drop every cached alert belonging to `incident_id`."""
        doomed = [
            aid for aid, cached in self._cache.items() if cached.incident_id == incident_id
        ]
        for aid in doomed:
            cached = self._cache.pop(aid, None)
            if cached:
                self._text_cache.pop(cached.text, None)
        return len(doomed)

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

    _TOKEN_RE = re.compile(r"[a-z0-9]+")

    def __init__(self, config: SemanticConfig | None = None):
        self.config = config or SemanticConfig()
        self._entity_extractor = EntityExtractor()
        self._equivalence_mapper = MetricEquivalenceMapper()
        self._embedding_cache = EmbeddingCache(self.config.cache_ttl_hours)
        self._incident_alerts: dict[UUID, list[RawAlert]] = {}
        self._model: Any = None
        self._model_load_attempted = False
        self._model_unavailable_reason: str | None = None

    def _load_model(self) -> None:
        """Lazy load embedding model, remembering why it is unavailable.

        The previous version left `_model = None` after a failed import and had no
        "already tried" flag, so every alert re-ran the import and the only record
        of the failure was a repeated log line. The reason is now kept so
        `get_stats()` can name it instead of reporting a bare `model_loaded: false`.
        """
        if self._model_load_attempted:
            return
        self._model_load_attempted = True

        try:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.config.embedding_model)
            log.info("Loaded embedding model: %s", self.config.embedding_model)
        except Exception as e:
            self._model = None
            self._model_unavailable_reason = f"{type(e).__name__}: {e}"
            log.warning(
                "Embedding model %s unavailable (%s); Layer 2 text matching falls "
                "back to token-overlap similarity, which detects re-wordings of the "
                "same alert but not synonyms.",
                self.config.embedding_model,
                self._model_unavailable_reason,
            )

    @property
    def similarity_backend(self) -> str:
        """Which measure `find_similar` is actually using."""
        return "embedding" if self._model is not None else "lexical_overlap"

    def _active_threshold(self) -> float:
        """Threshold matching the measure in use."""
        self._load_model()
        if self._model is not None:
            return self.config.similarity_threshold
        return self.config.lexical_similarity_threshold

    def register_alert(self, alert: RawAlert, incident_id: UUID) -> None:
        """Register an alert with its incident."""
        if incident_id not in self._incident_alerts:
            self._incident_alerts[incident_id] = []
        self._incident_alerts[incident_id].append(alert)

        text = self._get_alert_text(alert)
        embedding = self._get_embedding(text)
        # The alert is cached even with no embedding: its text and service are what
        # the fallback comparison needs, and dropping it would leave the text path
        # with no candidates at all rather than with honest ones.
        self._embedding_cache.put(
            alert.alert_id,
            embedding or [],
            text,
            incident_id,
            service=alert.service,
        )

    def forget_incident(self, incident_id: UUID) -> int:
        """Stop offering this incident's alerts as match candidates.

        Layer 2 keeps every registered alert forever, so a resolved incident went
        on attracting new alerts to itself; `DeduplicationPipeline` had to refuse
        each stale match at merge time. Refused work is still work, and the index
        still grew without bound. Called from `resolve_incident`, this removes the
        candidates instead, so the refusal never has to happen.

        Returns the number of cached alerts dropped — the answer is a measurement,
        not a promise, so a caller that gets 0 knows nothing was there.
        """
        alerts = self._incident_alerts.pop(incident_id, [])
        self._embedding_cache.forget_incident(incident_id)
        return len(alerts)

    def find_similar(
        self,
        alert: RawAlert,
        threshold: float | None = None,
    ) -> SemanticMatch | None:
        """Find semantically similar alerts."""
        # Two measures with different scales can both run inside one call — a
        # candidate cached before the model loaded, or one the model failed to
        # encode, is compared lexically even when everything else is compared by
        # embedding. Each measure therefore carries its own threshold. An explicit
        # `threshold` argument is the caller overriding the decision outright, so it
        # applies to whichever measure ends up running.
        if threshold is not None:
            lexical_threshold = threshold
        else:
            threshold = self._active_threshold()
            lexical_threshold = self.config.lexical_similarity_threshold

        if self.config.use_entity_extraction:
            entity_match = self._find_by_entity_matching(alert, threshold)
            if entity_match:
                return entity_match

        text_match = self._find_by_text_similarity(alert, threshold, lexical_threshold)
        if text_match:
            return text_match

        if self.config.use_learned_equivalences:
            equiv_match = self._find_by_equivalence(alert, threshold)
            if equiv_match:
                return equiv_match

        return None

    def _find_by_text_similarity(
        self,
        alert: RawAlert,
        threshold: float,
        lexical_threshold: float,
    ) -> SemanticMatch | None:
        """Find similar alerts by comparing alert text.

        This used to compare hash-bucket vectors produced by `_fallback_embedding`.
        Those vectors were non-negative in every dimension, so the cosine between
        two entirely unrelated alerts sat around 0.75-0.8 and routinely crossed the
        0.85 threshold: a search-index OOM was merged into an auth-service
        certificate incident because their hashes happened to overlap. The measure
        is now either a real embedding cosine or an explicit token-overlap cosine,
        both of which are near zero for unrelated text, and a candidate from a
        different service is never a text match at all.

        Which of the two ran is decided per candidate, not once per call. A model
        can be loaded and still leave a particular candidate unembedded — it was
        cached before the model was, or `encode` raised on it — and the earlier
        version read the backend once up front, so those candidates were scored
        lexically while the match reported `method="embedding"` and was judged
        against the embedding threshold. The measure that produced the number now
        names itself and is held to its own threshold.

        "Best" is likewise decided within a measure and never across two. A single
        candidate set can hold both kinds, and the two numbers are not on the same
        scale: a transformer cosine of 0.86 between two re-wordings of one failure
        says far more than a token-overlap cosine of 0.90 between two alerts that
        happen to share boilerplate. A shared `best_score` let the lexical number
        win that comparison and hand back the wrong incident. The embedding is the
        calibrated measure, so when any candidate produced one it decides the
        match; the lexical best is the answer only when nothing was embedded.
        """
        self._load_model()

        text = self._get_alert_text(alert)
        embedding = self._get_embedding(text)

        cached_embeddings = self._embedding_cache.get_all_with_incident()
        if not cached_embeddings:
            return None

        best: dict[str, tuple[float, SemanticMatch]] = {}

        for cached in cached_embeddings:
            if cached.alert_id == alert.alert_id:
                continue
            if not cached.incident_id:
                continue
            if self._different_service(alert.service, cached.service):
                continue

            if embedding and cached.embedding:
                score = self._cosine_similarity(embedding, cached.embedding)
                method = "embedding"
                candidate_threshold = threshold
            else:
                score = self._lexical_similarity(text, cached.text)
                method = "lexical_overlap"
                candidate_threshold = lexical_threshold

            if score <= candidate_threshold:
                continue
            incumbent = best.get(method)
            if incumbent is not None and score <= incumbent[0]:
                continue

            best[method] = (
                score,
                SemanticMatch(
                    alert_id=cached.alert_id,
                    incident_id=cached.incident_id,
                    similarity_score=score,
                    method=method,
                    embedding_distance=1 - score,
                ),
            )

        for method in ("embedding", "lexical_overlap"):
            if method in best:
                return best[method][1]
        return None

    def _different_service(self, service: str, candidate_service: str) -> bool:
        """Whether the same-service guard should reject this candidate.

        An empty service is an unknown service, not a wildcard. `service` on both
        `RawAlert` and the API's `ProcessAlertRequest` defaults to the empty string
        with nothing requiring it to be filled in, so treating "" as "matches
        anything" handed back the whole invariant: one alert posted with
        `service: ""` could be text-matched onto any incident in the org, and every
        such alert could be matched onto each other. Two alerts are the same service
        only when both name it and both name the same one.
        """
        if not self.config.require_same_service_for_text_match:
            return False
        if not service or not candidate_service:
            return True
        return service != candidate_service

    def _find_by_entity_matching(
        self,
        alert: RawAlert,
        threshold: float,
    ) -> SemanticMatch | None:
        """Find similar alerts using entity extraction.

        This is the first branch `find_similar` tries, ahead of the text path, and
        it had no service guard of any kind. It was safe only by arithmetic: the
        service term carries 0.35 of `_entity_similarity`'s weight, so two alerts
        from different services top out at 0.65 and cannot reach the 0.85 default
        threshold. Nothing said so, and nothing held it — lowering the threshold,
        passing `threshold=0.6` from a caller, or re-weighting the entity scores
        would have quietly reopened cross-service merging on the branch that runs
        before the one where the invariant is written down. The same-service rule
        is now enforced here explicitly, on the alerts' own `service` fields rather
        than on `entities.service`, which falls back to a token scraped out of the
        message and is a guess rather than a fact.
        """
        entities = self._entity_extractor.extract(alert)

        if not entities.service and not entities.metric_type:
            return None

        for incident_id, alerts in self._incident_alerts.items():
            for existing_alert in alerts:
                if existing_alert.alert_id == alert.alert_id:
                    continue

                if self._different_service(alert.service, existing_alert.service):
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
        """Find similar alerts using metric equivalence.

        "Both alerts measure latency" is a weak enough signal on its own that the
        service has to carry the rest of the claim. The guard here used to compare
        `entities.service` — which falls back to a token scraped out of the message
        when the field is empty — against the *other* alert's raw field, and to skip
        itself entirely when either side came out empty. So an alert posted with
        `service: ""` and the word "latency" in it merged into whichever incident
        had last mentioned latency, in any service. Both alerts must now name the
        same service, on the field rather than on a guess about it.
        """
        entities = self._entity_extractor.extract(alert)

        if not entities.metric_type:
            return None

        equivalents = self._equivalence_mapper.get_all_equivalents(entities.metric_type)

        for incident_id, alerts in self._incident_alerts.items():
            for existing_alert in alerts:
                if existing_alert.alert_id == alert.alert_id:
                    continue

                if not alert.service or alert.service != existing_alert.service:
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
        """Get an embedding for text, or None when no model can produce one.

        Returning None is the point: there used to be a `_fallback_embedding` that
        manufactured a vector out of word hashes and handed it back as though a
        model had produced it, which made every caller believe an embedding
        similarity had been measured when none had. Callers now see the absence and
        use `_lexical_similarity`, which says what it is.
        """
        cached = self._embedding_cache.get_by_text(text)
        if cached:
            return cached

        self._load_model()
        if self._model is None:
            return None

        try:
            return self._model.encode(text).tolist()
        except Exception as e:
            log.warning(
                "Embedding model %s failed on an alert (%s); falling back to "
                "token-overlap similarity for it.",
                self.config.embedding_model,
                e,
            )
            return None

    def _tokenize(self, text: str) -> dict[str, float]:
        """Term frequencies for the words of an alert."""
        counts: dict[str, float] = {}
        for token in self._TOKEN_RE.findall(text.lower()):
            counts[token] = counts.get(token, 0.0) + 1.0
        return counts

    def _lexical_similarity(self, text1: str, text2: str) -> float:
        """Cosine similarity over the two texts' term frequencies.

        A plain bag-of-words cosine: 1.0 for the same words, 0.0 for texts that
        share none. Unlike the hash-bucket vectors this replaced, the number has no
        floor, so "unrelated" reads as near zero rather than as 0.8. It measures
        wording overlap and nothing more — two ways of saying the same thing in
        different words score low here, which is the honest answer from a measure
        that has no notion of meaning.
        """
        tf1 = self._tokenize(text1)
        tf2 = self._tokenize(text2)
        if not tf1 or not tf2:
            return 0.0

        shared = set(tf1) & set(tf2)
        if not shared:
            return 0.0

        dot = sum(tf1[t] * tf2[t] for t in shared)
        norm1 = sum(v * v for v in tf1.values()) ** 0.5
        norm2 = sum(v * v for v in tf2.values()) ** 0.5
        return dot / (norm1 * norm2)

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

        # `embedding_similarity` stays None when no model measured one. Reporting
        # 0.0 there would claim the two alerts are semantically unrelated, which is
        # a measurement nobody took.
        if emb1 and emb2:
            text_sim = self._cosine_similarity(emb1, emb2)
            text_method = "embedding"
            embedding_sim: float | None = text_sim
        else:
            text_sim = self._lexical_similarity(t1, t2)
            text_method = "lexical_overlap"
            embedding_sim = None

        entity_sim = self._entity_similarity(e1, e2)
        combined = (text_sim * 0.6) + (entity_sim * 0.4)
        threshold = self._active_threshold()
        same_service = not self._different_service(alert1.service, alert2.service)

        return {
            "alert1_entities": e1.to_dict(),
            "alert2_entities": e2.to_dict(),
            "text_similarity": text_sim,
            "text_similarity_method": text_method,
            "embedding_similarity": embedding_sim,
            "entity_similarity": entity_sim,
            "combined_score": combined,
            "metric_equivalent": self._equivalence_mapper.are_equivalent(
                e1.metric_type or "", e2.metric_type or ""
            ),
            "recommendation": "MERGE"
            if combined >= threshold and same_service
            else "SEPARATE",
        }

    def get_stats(self) -> dict[str, Any]:
        """Get semantic engine statistics."""
        self._load_model()
        return {
            "cache_stats": self._embedding_cache.stats(),
            "incidents_tracked": len(self._incident_alerts),
            "total_alerts": sum(
                len(alerts) for alerts in self._incident_alerts.values()
            ),
            "model_loaded": self._model is not None,
            # Which measure Layer 2 is really running, and why it is not the
            # embedding one when it is not.
            "similarity_backend": self.similarity_backend,
            "model_unavailable_reason": self._model_unavailable_reason,
            "config": {
                "threshold": self._active_threshold(),
                "embedding_threshold": self.config.similarity_threshold,
                "lexical_threshold": self.config.lexical_similarity_threshold,
                "model": self.config.embedding_model,
                "require_same_service_for_text_match": (
                    self.config.require_same_service_for_text_match
                ),
            },
        }
