"""Semantic caching for LLM responses.

Caches LLM responses based on semantic similarity of queries,
not just exact matches. Uses embeddings to find similar past queries.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Coroutine

log = logging.getLogger("omnipotent.llm.semantic_cache")


@dataclass
class CacheEntry:
    """A cached response entry."""

    key: str
    query: str
    query_embedding: list[float] | None
    response: str
    model: str
    created_at: datetime
    expires_at: datetime
    hit_count: int = 0
    last_hit_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def is_expired(self) -> bool:
        """Check if entry has expired."""
        return datetime.now(timezone.utc) > self.expires_at

    def record_hit(self) -> None:
        """Record a cache hit."""
        self.hit_count += 1
        self.last_hit_at = datetime.now(timezone.utc)


@dataclass
class CacheStats:
    """Cache statistics."""

    total_queries: int = 0
    exact_hits: int = 0
    semantic_hits: int = 0
    misses: int = 0
    evictions: int = 0
    total_tokens_saved: int = 0
    total_cost_saved: float = 0.0

    @property
    def hit_rate(self) -> float:
        """Get overall hit rate."""
        if self.total_queries == 0:
            return 0.0
        return (self.exact_hits + self.semantic_hits) / self.total_queries

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "total_queries": self.total_queries,
            "exact_hits": self.exact_hits,
            "semantic_hits": self.semantic_hits,
            "misses": self.misses,
            "hit_rate": round(self.hit_rate, 3),
            "evictions": self.evictions,
            "total_tokens_saved": self.total_tokens_saved,
            "total_cost_saved": round(self.total_cost_saved, 4),
        }


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    if len(a) != len(b):
        return 0.0

    dot_product = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5

    if norm_a == 0 or norm_b == 0:
        return 0.0

    return dot_product / (norm_a * norm_b)


class SemanticCache:
    """Semantic cache for LLM responses."""

    def __init__(
        self,
        embedding_func: Callable[[str], Coroutine[Any, Any, list[float]]] | None = None,
        similarity_threshold: float = 0.92,
        default_ttl: timedelta = timedelta(hours=24),
        max_entries: int = 10000,
        cost_per_1k_tokens: float = 0.01,
    ):
        self._embedding_func = embedding_func
        self._similarity_threshold = similarity_threshold
        self._default_ttl = default_ttl
        self._max_entries = max_entries
        self._cost_per_1k = cost_per_1k_tokens
        self._entries: dict[str, CacheEntry] = {}
        self._embeddings_index: list[tuple[str, list[float]]] = []
        self._stats = CacheStats()

    def _compute_key(self, query: str, model: str, context: dict[str, Any] | None) -> str:
        """Compute cache key for exact matching."""
        key_data = json.dumps(
            {"query": query, "model": model, "context": context or {}},
            sort_keys=True,
        )
        return hashlib.sha256(key_data.encode()).hexdigest()

    async def get(
        self,
        query: str,
        model: str,
        context: dict[str, Any] | None = None,
    ) -> CacheEntry | None:
        """Get cached response."""
        self._stats.total_queries += 1

        key = self._compute_key(query, model, context)
        entry = self._entries.get(key)

        if entry and not entry.is_expired():
            entry.record_hit()
            self._stats.exact_hits += 1
            log.debug("exact cache hit", extra={"key": key[:16]})
            return entry

        if entry:
            del self._entries[key]

        if self._embedding_func:
            similar = await self._find_similar(query, model)
            if similar:
                similar.record_hit()
                self._stats.semantic_hits += 1
                log.debug(
                    "semantic cache hit",
                    extra={"original_key": similar.key[:16]},
                )
                return similar

        self._stats.misses += 1
        return None

    async def _find_similar(
        self,
        query: str,
        model: str,
    ) -> CacheEntry | None:
        """Find semantically similar cached entry."""
        if not self._embedding_func or not self._embeddings_index:
            return None

        try:
            query_embedding = await self._embedding_func(query)
        except Exception:
            log.warning("embedding generation failed", exc_info=True)
            return None

        best_match: tuple[str, float] | None = None

        for key, embedding in self._embeddings_index:
            entry = self._entries.get(key)
            if not entry or entry.is_expired() or entry.model != model:
                continue

            similarity = cosine_similarity(query_embedding, embedding)
            if similarity >= self._similarity_threshold:
                if best_match is None or similarity > best_match[1]:
                    best_match = (key, similarity)

        if best_match:
            return self._entries.get(best_match[0])

        return None

    async def set(
        self,
        query: str,
        response: str,
        model: str,
        context: dict[str, Any] | None = None,
        ttl: timedelta | None = None,
        tokens_used: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> CacheEntry:
        """Cache a response."""
        key = self._compute_key(query, model, context)
        now = datetime.now(timezone.utc)

        embedding = None
        if self._embedding_func:
            try:
                embedding = await self._embedding_func(query)
            except Exception:
                log.warning("embedding generation failed", exc_info=True)

        entry = CacheEntry(
            key=key,
            query=query,
            query_embedding=embedding,
            response=response,
            model=model,
            created_at=now,
            expires_at=now + (ttl or self._default_ttl),
            metadata=metadata or {},
        )

        if tokens_used > 0:
            entry.metadata["tokens"] = tokens_used

        self._entries[key] = entry

        if embedding:
            self._embeddings_index.append((key, embedding))

        self._evict_if_needed()

        log.debug(
            "cached response",
            extra={"key": key[:16], "model": model, "tokens": tokens_used},
        )

        return entry

    def _evict_if_needed(self) -> None:
        """Evict entries if cache is full."""
        if len(self._entries) <= self._max_entries:
            return

        expired = [k for k, e in self._entries.items() if e.is_expired()]
        for key in expired:
            del self._entries[key]
            self._stats.evictions += 1

        self._embeddings_index = [
            (k, e) for k, e in self._embeddings_index if k in self._entries
        ]

        if len(self._entries) <= self._max_entries:
            return

        by_last_hit = sorted(
            self._entries.items(),
            key=lambda x: x[1].last_hit_at or x[1].created_at,
        )

        to_remove = len(self._entries) - int(self._max_entries * 0.9)
        for key, _ in by_last_hit[:to_remove]:
            del self._entries[key]
            self._stats.evictions += 1

        self._embeddings_index = [
            (k, e) for k, e in self._embeddings_index if k in self._entries
        ]

    def invalidate(self, query: str, model: str, context: dict[str, Any] | None = None) -> bool:
        """Invalidate a cached entry."""
        key = self._compute_key(query, model, context)
        if key in self._entries:
            del self._entries[key]
            self._embeddings_index = [
                (k, e) for k, e in self._embeddings_index if k != key
            ]
            return True
        return False

    def invalidate_by_model(self, model: str) -> int:
        """Invalidate all entries for a model."""
        keys_to_remove = [
            k for k, e in self._entries.items() if e.model == model
        ]
        for key in keys_to_remove:
            del self._entries[key]

        self._embeddings_index = [
            (k, e) for k, e in self._embeddings_index if k in self._entries
        ]

        return len(keys_to_remove)

    def clear(self) -> None:
        """Clear all cached entries."""
        count = len(self._entries)
        self._entries.clear()
        self._embeddings_index.clear()
        self._stats.evictions += count

    def get_stats(self) -> CacheStats:
        """Get cache statistics."""
        return self._stats

    def get_entries_info(self, limit: int = 100) -> list[dict[str, Any]]:
        """Get information about cached entries."""
        entries = sorted(
            self._entries.values(),
            key=lambda e: e.hit_count,
            reverse=True,
        )[:limit]

        return [
            {
                "key": e.key[:16],
                "query_preview": e.query[:100],
                "model": e.model,
                "created_at": e.created_at.isoformat(),
                "expires_at": e.expires_at.isoformat(),
                "hit_count": e.hit_count,
                "is_expired": e.is_expired(),
            }
            for e in entries
        ]

    def update_similarity_threshold(self, threshold: float) -> None:
        """Update similarity threshold."""
        if 0.0 <= threshold <= 1.0:
            self._similarity_threshold = threshold

    def record_savings(self, tokens: int) -> None:
        """Record tokens saved by cache hit."""
        self._stats.total_tokens_saved += tokens
        self._stats.total_cost_saved += (tokens / 1000) * self._cost_per_1k


class TieredSemanticCache:
    """Multi-tier cache with different TTLs and costs."""

    def __init__(
        self,
        embedding_func: Callable[[str], Coroutine[Any, Any, list[float]]] | None = None,
    ):
        self._tiers: dict[str, SemanticCache] = {
            "hot": SemanticCache(
                embedding_func=embedding_func,
                similarity_threshold=0.95,
                default_ttl=timedelta(hours=1),
                max_entries=1000,
            ),
            "warm": SemanticCache(
                embedding_func=embedding_func,
                similarity_threshold=0.92,
                default_ttl=timedelta(hours=12),
                max_entries=5000,
            ),
            "cold": SemanticCache(
                embedding_func=embedding_func,
                similarity_threshold=0.90,
                default_ttl=timedelta(days=7),
                max_entries=20000,
            ),
        }

    async def get(
        self,
        query: str,
        model: str,
        context: dict[str, Any] | None = None,
    ) -> CacheEntry | None:
        """Get from tiered cache, checking hot -> warm -> cold."""
        for tier_name in ["hot", "warm", "cold"]:
            entry = await self._tiers[tier_name].get(query, model, context)
            if entry:
                if tier_name != "hot":
                    await self._tiers["hot"].set(
                        query,
                        entry.response,
                        model,
                        context,
                        tokens_used=entry.metadata.get("tokens", 0),
                    )
                return entry
        return None

    async def set(
        self,
        query: str,
        response: str,
        model: str,
        context: dict[str, Any] | None = None,
        tier: str = "warm",
        tokens_used: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> CacheEntry:
        """Set in specified tier."""
        cache = self._tiers.get(tier, self._tiers["warm"])
        return await cache.set(
            query, response, model, context,
            tokens_used=tokens_used, metadata=metadata,
        )

    def get_stats(self) -> dict[str, dict[str, Any]]:
        """Get stats for all tiers."""
        return {name: cache.get_stats().to_dict() for name, cache in self._tiers.items()}


_cache: SemanticCache | None = None


def get_semantic_cache(
    embedding_func: Callable[[str], Coroutine[Any, Any, list[float]]] | None = None,
) -> SemanticCache:
    """Get the default semantic cache."""
    global _cache
    if _cache is None:
        _cache = SemanticCache(embedding_func=embedding_func)
    return _cache
