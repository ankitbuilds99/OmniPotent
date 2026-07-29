"""Embedding generation and vector search for incidents."""

from __future__ import annotations

import hashlib
import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from app.similar_search.models import (
    IncidentRecord,
    IncidentEmbedding,
    SearchFilters,
)

log = logging.getLogger("devcopilot.similar_search.embeddings")


@dataclass
class EmbeddingConfig:
    """Configuration for embedding generation."""

    model_name: str = "text-embedding-3-small"
    embedding_dimension: int = 1536
    use_openai: bool = True
    fallback_dimension: int = 128
    cache_ttl_hours: int = 168
    batch_size: int = 100


class IncidentEmbedder:
    """Generate embeddings for incidents."""

    def __init__(self, config: EmbeddingConfig | None = None):
        self.config = config or EmbeddingConfig()
        self._model: Any = None
        self._openai_client: Any = None

    def _init_openai(self) -> bool:
        """Initialize OpenAI client."""
        if self._openai_client is not None:
            return True

        try:
            import os

            from openai import OpenAI

            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                return False

            self._openai_client = OpenAI(api_key=api_key)
            return True
        except ImportError:
            log.warning("OpenAI not installed, using fallback embeddings")
            return False

    def generate_embedding(
        self,
        incident: IncidentRecord,
    ) -> IncidentEmbedding:
        """Generate embeddings for an incident."""
        text = incident.get_searchable_text()
        symptom_text = " ".join(incident.alerts)
        service_text = " ".join(incident.services)
        error_text = " ".join(incident.error_messages)

        if self.config.use_openai and self._init_openai():
            text_emb = self._openai_embed(text)
            symptom_emb = self._openai_embed(symptom_text) if symptom_text else []
            service_emb = self._openai_embed(service_text) if service_text else []
            error_emb = self._openai_embed(error_text) if error_text else []
        else:
            text_emb = self._fallback_embed(text)
            symptom_emb = self._fallback_embed(symptom_text) if symptom_text else []
            service_emb = self._fallback_embed(service_text) if service_text else []
            error_emb = self._fallback_embed(error_text) if error_text else []

        return IncidentEmbedding(
            incident_id=incident.incident_id,
            text_embedding=text_emb,
            symptom_embedding=symptom_emb,
            service_embedding=service_emb,
            error_embedding=error_emb,
            model_version=self.config.model_name,
        )

    def generate_query_embedding(self, query: str) -> list[float]:
        """Generate embedding for search query."""
        if self.config.use_openai and self._init_openai():
            return self._openai_embed(query)
        return self._fallback_embed(query)

    def _openai_embed(self, text: str) -> list[float]:
        """Generate embedding using OpenAI."""
        if not text.strip():
            return [0.0] * self.config.embedding_dimension

        try:
            response = self._openai_client.embeddings.create(
                model=self.config.model_name,
                input=text[:8000],
            )
            return response.data[0].embedding
        except Exception as e:
            log.warning("OpenAI embedding failed: %s", e)
            return self._fallback_embed(text)

    def _fallback_embed(self, text: str) -> list[float]:
        """Generate fallback embedding using word hashing."""
        dim = self.config.fallback_dimension
        embedding = [0.0] * dim

        if not text.strip():
            return embedding

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

    def batch_generate(
        self,
        incidents: list[IncidentRecord],
    ) -> list[IncidentEmbedding]:
        """Generate embeddings for multiple incidents."""
        results = []
        for incident in incidents:
            emb = self.generate_embedding(incident)
            results.append(emb)
        return results


@dataclass
class StoredEmbedding:
    """Stored embedding with metadata."""

    incident_id: UUID
    embedding: list[float]
    metadata: dict[str, Any]
    created_at: datetime


class EmbeddingStore:
    """Store and retrieve embeddings."""

    def __init__(self):
        self._embeddings: dict[UUID, StoredEmbedding] = {}
        self._incident_data: dict[UUID, IncidentRecord] = {}

    def store(
        self,
        incident: IncidentRecord,
        embedding: IncidentEmbedding,
    ) -> None:
        """Store incident embedding."""
        metadata = {
            "title": incident.title,
            "severity": incident.severity.value,
            "services": incident.services,
            "root_cause_category": incident.root_cause_category.value,
            "created_at": incident.created_at.isoformat(),
            "resolved_at": incident.resolved_at.isoformat()
            if incident.resolved_at
            else None,
            "duration_minutes": incident.duration_minutes,
            "tags": incident.tags,
        }

        self._embeddings[incident.incident_id] = StoredEmbedding(
            incident_id=incident.incident_id,
            embedding=embedding.text_embedding,
            metadata=metadata,
            created_at=datetime.utcnow(),
        )
        self._incident_data[incident.incident_id] = incident

    def get(self, incident_id: UUID) -> StoredEmbedding | None:
        """Get stored embedding."""
        return self._embeddings.get(incident_id)

    def get_incident(self, incident_id: UUID) -> IncidentRecord | None:
        """Get incident record."""
        return self._incident_data.get(incident_id)

    def get_all_embeddings(self) -> list[StoredEmbedding]:
        """Get all stored embeddings."""
        return list(self._embeddings.values())

    def search_by_metadata(
        self,
        filters: SearchFilters,
    ) -> list[StoredEmbedding]:
        """Filter embeddings by metadata."""
        results = []
        cutoff = datetime.utcnow() - timedelta(days=filters.time_range_days)

        for stored in self._embeddings.values():
            created_str = stored.metadata.get("created_at", "")
            if created_str:
                try:
                    created = datetime.fromisoformat(created_str)
                    if created < cutoff:
                        continue
                except ValueError:
                    pass

            if filters.severities:
                sev = stored.metadata.get("severity")
                if sev not in [s.value for s in filters.severities]:
                    continue

            if filters.services:
                svc_meta = stored.metadata.get("services", [])
                if not any(s in svc_meta for s in filters.services):
                    continue

            if filters.root_cause_categories:
                rc = stored.metadata.get("root_cause_category")
                if rc not in [c.value for c in filters.root_cause_categories]:
                    continue

            if stored.incident_id in filters.exclude_incident_ids:
                continue

            results.append(stored)

        return results

    def delete(self, incident_id: UUID) -> bool:
        """Delete embedding."""
        if incident_id in self._embeddings:
            del self._embeddings[incident_id]
            self._incident_data.pop(incident_id, None)
            return True
        return False

    def stats(self) -> dict[str, Any]:
        """Get store statistics."""
        return {
            "total_embeddings": len(self._embeddings),
            "total_incidents": len(self._incident_data),
        }


class HybridSearchEngine:
    """Hybrid search combining vector similarity and keyword matching."""

    def __init__(
        self,
        embedder: IncidentEmbedder,
        store: EmbeddingStore,
    ):
        self._embedder = embedder
        self._store = store

    def search(
        self,
        query: str | None = None,
        current_incident: IncidentRecord | None = None,
        filters: SearchFilters | None = None,
        limit: int = 10,
    ) -> list[tuple[IncidentRecord, float]]:
        """Perform hybrid search."""
        filters = filters or SearchFilters()

        if current_incident:
            query_text = current_incident.get_searchable_text()
        elif query:
            query_text = query
        else:
            return []

        query_embedding = self._embedder.generate_query_embedding(query_text)

        candidates = self._store.search_by_metadata(filters)

        if current_incident:
            candidates = [
                c for c in candidates if c.incident_id != current_incident.incident_id
            ]

        scored: list[tuple[IncidentRecord, float]] = []
        for candidate in candidates:
            vector_score = self._cosine_similarity(
                query_embedding, candidate.embedding
            )

            incident = self._store.get_incident(candidate.incident_id)
            if not incident:
                continue

            keyword_score = 0.0
            if query:
                keyword_score = self._keyword_score(query, incident)

            combined_score = (vector_score * 0.7) + (keyword_score * 0.3)

            if combined_score >= filters.min_similarity:
                scored.append((incident, combined_score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:limit]

    def _cosine_similarity(self, v1: list[float], v2: list[float]) -> float:
        """Calculate cosine similarity."""
        if len(v1) != len(v2):
            min_len = min(len(v1), len(v2))
            v1 = v1[:min_len]
            v2 = v2[:min_len]

        if not v1:
            return 0.0

        dot = sum(a * b for a, b in zip(v1, v2))
        norm1 = math.sqrt(sum(a * a for a in v1))
        norm2 = math.sqrt(sum(b * b for b in v2))

        if norm1 == 0 or norm2 == 0:
            return 0.0

        return dot / (norm1 * norm2)

    def _keyword_score(
        self,
        query: str,
        incident: IncidentRecord,
    ) -> float:
        """Calculate keyword matching score."""
        query_terms = set(query.lower().split())
        incident_text = incident.get_searchable_text().lower()
        incident_terms = set(incident_text.split())

        if not query_terms:
            return 0.0

        matches = query_terms & incident_terms
        return len(matches) / len(query_terms)

    def index_incident(self, incident: IncidentRecord) -> None:
        """Index an incident for search."""
        embedding = self._embedder.generate_embedding(incident)
        self._store.store(incident, embedding)

    def reindex_all(self, incidents: list[IncidentRecord]) -> int:
        """Reindex all incidents."""
        count = 0
        for incident in incidents:
            self.index_incident(incident)
            count += 1
        return count

    def get_stats(self) -> dict[str, Any]:
        """Get search engine statistics."""
        return {
            "store": self._store.stats(),
            "embedder_config": {
                "model": self._embedder.config.model_name,
                "dimension": self._embedder.config.embedding_dimension,
            },
        }
