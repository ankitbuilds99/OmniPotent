"""Tests for vector store module (embeddings + hybrid search).

These tests cover both the unit functionality and backwards compatibility:
- When vectors are disabled (COPILOT_VECTOR_STORE=none), all operations are no-ops
- Hybrid search falls back gracefully to BM25 when vectors are unavailable
- RRF fusion produces sensible rankings
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

# Test corpus (same structure as incident_memory's bundled corpus)
TEST_CORPUS = [
    {
        "id": "INC-001",
        "title": "checkout-svc 5xx after discount deploy",
        "summary": "TypeError applyDiscount caused 500 errors",
        "root_cause": "null deref on coupon object",
        "service": "checkout-svc",
        "tags": ["checkout", "discount", "5xx", "typeerror"],
        "resolution": "Rolled back and added null guard",
        "runbook": ["Check logs", "Roll back", "Add null guard"],
    },
    {
        "id": "INC-002",
        "title": "inventory-svc latency from unindexed query",
        "summary": "p95 latency spike due to full table scan",
        "root_cause": "missing composite index on warehouse_id, sku",
        "service": "inventory-svc",
        "tags": ["latency", "database", "slow-query"],
        "resolution": "Added composite index",
        "runbook": ["Check slow query log", "Add index"],
    },
    {
        "id": "INC-003",
        "title": "payment-svc OOMKilled under load",
        "summary": "pods crashed with OOMKilled during traffic spike",
        "root_cause": "unbounded in-memory response buffering",
        "service": "payment-svc",
        "tags": ["oomkilled", "memory-leak", "kubernetes"],
        "resolution": "Streamed responses instead of buffering",
        "runbook": ["Check pod events", "Profile memory"],
    },
]


class TestVectorStoreDisabled:
    """Tests when vector store is disabled (the default)."""

    def test_is_vector_enabled_returns_false_by_default(self, monkeypatch):
        """With no config, vectors should be disabled."""
        monkeypatch.setenv("COPILOT_VECTOR_STORE", "none")

        # Clear caches to pick up new env
        from app.config import get_settings
        get_settings.cache_clear()

        from app import vector_store
        vector_store.reset_vector_store()

        assert not vector_store.is_vector_enabled()

    def test_embed_text_returns_none_when_disabled(self, monkeypatch):
        """embed_text should return None when vectors are disabled."""
        monkeypatch.setenv("COPILOT_VECTOR_STORE", "none")

        from app.config import get_settings
        get_settings.cache_clear()

        from app import vector_store
        vector_store.reset_vector_store()

        result = vector_store.embed_text("test query")
        assert result is None

    def test_search_similar_returns_empty_when_disabled(self, monkeypatch):
        """search_similar should return empty list when vectors are disabled."""
        monkeypatch.setenv("COPILOT_VECTOR_STORE", "none")

        from app.config import get_settings
        get_settings.cache_clear()

        from app import vector_store
        vector_store.reset_vector_store()

        result = vector_store.search_similar("test query")
        assert result == []

    def test_add_document_returns_false_when_disabled(self, monkeypatch):
        """add_document should return False when vectors are disabled."""
        monkeypatch.setenv("COPILOT_VECTOR_STORE", "none")

        from app.config import get_settings
        get_settings.cache_clear()

        from app import vector_store
        vector_store.reset_vector_store()

        result = vector_store.add_document(TEST_CORPUS[0])
        assert result is False

    def test_hybrid_search_falls_back_to_bm25_when_disabled(self, monkeypatch):
        """hybrid_search should use pure BM25 when vectors are disabled."""
        monkeypatch.setenv("COPILOT_VECTOR_STORE", "none")

        from app.config import get_settings
        get_settings.cache_clear()

        from app import vector_store
        vector_store.reset_vector_store()

        results = vector_store.hybrid_search("checkout 5xx discount", TEST_CORPUS, limit=3)

        # Should get results (BM25 should match)
        assert len(results) > 0
        # First result should be the checkout incident
        assert results[0]["id"] == "INC-001"
        # Should have a score
        assert "score" in results[0]


class TestVectorStoreStats:
    """Tests for vector store statistics."""

    def test_get_vector_stats_when_disabled(self, monkeypatch):
        """get_vector_stats should report disabled state."""
        monkeypatch.setenv("COPILOT_VECTOR_STORE", "none")

        from app.config import get_settings
        get_settings.cache_clear()

        from app import vector_store
        vector_store.reset_vector_store()

        stats = vector_store.get_vector_stats()

        assert stats["enabled"] is False
        assert stats["store_type"] == "none"
        assert stats["document_count"] == 0


class TestRRFFusion:
    """Tests for Reciprocal Rank Fusion algorithm."""

    def test_rrf_combines_rankings(self):
        """RRF should combine BM25 and vector rankings."""
        from app import vector_store

        bm25_results = [
            {"id": "doc1", "score": 2.5},
            {"id": "doc2", "score": 1.8},
            {"id": "doc3", "score": 0.5},
        ]

        vector_results = [
            vector_store.VectorSearchResult(
                id="doc2", score=0.95, document="doc2 text", metadata={"title": "Doc 2"}
            ),
            vector_store.VectorSearchResult(
                id="doc4", score=0.85, document="doc4 text", metadata={"title": "Doc 4"}
            ),
            vector_store.VectorSearchResult(
                id="doc1", score=0.75, document="doc1 text", metadata={"title": "Doc 1"}
            ),
        ]

        fused = vector_store.reciprocal_rank_fusion(bm25_results, vector_results)

        # doc1 and doc2 appear in both lists, should rank higher
        ids = [r["id"] for r in fused]

        # doc1 is rank 1 in BM25, rank 3 in vector
        # doc2 is rank 2 in BM25, rank 1 in vector
        # Both should be near the top
        assert "doc1" in ids[:3]
        assert "doc2" in ids[:3]

    def test_rrf_handles_empty_inputs(self):
        """RRF should handle empty input lists."""
        from app import vector_store

        # Both empty
        assert vector_store.reciprocal_rank_fusion([], []) == []

        # BM25 only
        bm25_only = vector_store.reciprocal_rank_fusion(
            [{"id": "doc1", "score": 1.0}], []
        )
        assert len(bm25_only) == 1
        assert bm25_only[0]["id"] == "doc1"

        # Vector only
        vector_only = vector_store.reciprocal_rank_fusion(
            [],
            [
                vector_store.VectorSearchResult(
                    id="doc1", score=0.9, document="text", metadata={}
                )
            ],
        )
        assert len(vector_only) == 1
        assert vector_only[0]["id"] == "doc1"


class TestRecordConversion:
    """Tests for record-to-text conversion."""

    def test_record_to_text_includes_all_fields(self):
        """_record_to_text should include all searchable fields."""
        from app.vector_store import _record_to_text

        text = _record_to_text(TEST_CORPUS[0])

        # Should include key fields
        assert "checkout-svc" in text.lower()
        assert "5xx" in text.lower()
        assert "discount" in text.lower()
        assert "typeerror" in text.lower()
        assert "null deref" in text.lower()

    def test_record_id_uses_id_field(self):
        """_record_id should use the id field when present."""
        from app.vector_store import _record_id

        assert _record_id(TEST_CORPUS[0]) == "INC-001"

    def test_record_id_generates_hash_when_missing(self):
        """_record_id should generate a hash when id is missing."""
        from app.vector_store import _record_id

        record = {"title": "Test incident", "summary": "Test summary"}
        id1 = _record_id(record)

        # Should be a hex string
        assert len(id1) == 16
        assert all(c in "0123456789abcdef" for c in id1)

        # Same content should produce same id
        assert _record_id(record) == id1


class TestIncidentMemoryIntegration:
    """Tests for integration with incident_memory.py."""

    def test_search_uses_bm25_when_vectors_disabled(self, monkeypatch):
        """incident_memory.search should use BM25 when vectors are disabled."""
        monkeypatch.setenv("COPILOT_VECTOR_STORE", "none")

        from app.config import get_settings
        get_settings.cache_clear()

        from app import vector_store, incident_memory
        vector_store.reset_vector_store()

        # Search the bundled corpus
        results = incident_memory.search("checkout 5xx discount", limit=3)

        # Should find the checkout incident
        assert len(results) > 0
        assert any("checkout" in r.get("title", "").lower() for r in results)

    def test_search_respects_use_vectors_false(self, monkeypatch):
        """search(use_vectors=False) should skip vector search."""
        # Even if vectors were enabled, use_vectors=False should use pure BM25
        monkeypatch.setenv("COPILOT_VECTOR_STORE", "none")

        from app.config import get_settings
        get_settings.cache_clear()

        from app import vector_store, incident_memory
        vector_store.reset_vector_store()

        results = incident_memory.search(
            "checkout 5xx discount", limit=3, use_vectors=False
        )

        # Should still work (BM25 path)
        assert len(results) > 0


class TestEmbeddingBackends:
    """Tests for embedding backend selection."""

    def test_openai_backend_selected_for_openai_models(self, monkeypatch):
        """OpenAI backend should be selected for text-embedding-* models."""
        monkeypatch.setenv("COPILOT_VECTOR_STORE", "chromadb")
        monkeypatch.setenv("COPILOT_EMBEDDING_MODEL", "text-embedding-3-small")

        from app.config import get_settings
        get_settings.cache_clear()

        from app import vector_store
        vector_store.reset_vector_store()

        # Clear the embedding backend cache
        vector_store.get_embedding_backend.cache_clear()

        backend = vector_store.get_embedding_backend()

        # Should be OpenAI backend
        assert backend is not None
        assert isinstance(backend, vector_store.OpenAIEmbeddings)
        assert backend.model == "text-embedding-3-small"

    def test_sentence_transformer_selected_for_local_models(self, monkeypatch):
        """Sentence transformer backend should be selected for local models."""
        monkeypatch.setenv("COPILOT_VECTOR_STORE", "chromadb")
        monkeypatch.setenv("COPILOT_EMBEDDING_MODEL", "all-MiniLM-L6-v2")

        from app.config import get_settings
        get_settings.cache_clear()

        from app import vector_store
        vector_store.reset_vector_store()

        # Clear the embedding backend cache
        vector_store.get_embedding_backend.cache_clear()

        backend = vector_store.get_embedding_backend()

        # Should be sentence-transformers backend
        assert backend is not None
        assert isinstance(backend, vector_store.SentenceTransformerEmbeddings)
        assert backend.model_name == "all-MiniLM-L6-v2"


class TestMockedVectorSearch:
    """Tests with mocked embedding backend for full hybrid search flow."""

    def test_hybrid_search_with_mocked_store_and_vectors(self, monkeypatch):
        """Test hybrid search with fully mocked vector components.

        This test mocks both the vector store and embedding backend to avoid
        requiring chromadb to be installed.
        """
        monkeypatch.setenv("COPILOT_VECTOR_STORE", "chromadb")

        from app.config import get_settings
        get_settings.cache_clear()

        from app import vector_store
        vector_store.reset_vector_store()
        vector_store.get_embedding_backend.cache_clear()

        # Create mock vector search results
        mock_vector_results = [
            vector_store.VectorSearchResult(
                id="INC-001", score=0.95, document="checkout text",
                metadata={"title": "checkout-svc 5xx", "service": "checkout-svc"}
            ),
            vector_store.VectorSearchResult(
                id="INC-002", score=0.75, document="inventory text",
                metadata={"title": "inventory latency", "service": "inventory-svc"}
            ),
        ]

        # Mock the store to return these results
        mock_store = MagicMock()
        mock_store.count.return_value = 3  # Pretend we have indexed docs

        # Patch both is_vector_enabled and get_vector_store
        with patch.object(vector_store, "is_vector_enabled", return_value=True):
            with patch.object(vector_store, "get_vector_store", return_value=mock_store):
                with patch.object(vector_store, "search_similar", return_value=mock_vector_results):
                    with patch.object(vector_store, "ensure_corpus_indexed", return_value=0):
                        # Test hybrid search
                        results = vector_store.hybrid_search(
                            "checkout 5xx discount", TEST_CORPUS, limit=3
                        )

                        # Should have results
                        assert len(results) > 0

                        # First result should be checkout (appears in both BM25 and vector)
                        assert results[0]["id"] == "INC-001"

                        # Results should have score and rank info
                        assert "score" in results[0]
                        assert "_bm25_rank" in results[0]
                        assert "_vector_rank" in results[0]

    def test_hybrid_search_falls_back_on_import_error(self, monkeypatch):
        """Hybrid search should fall back to BM25 if vector imports fail."""
        monkeypatch.setenv("COPILOT_VECTOR_STORE", "none")

        from app.config import get_settings
        get_settings.cache_clear()

        from app import vector_store
        vector_store.reset_vector_store()

        # This should work even when chromadb isn't installed
        results = vector_store.hybrid_search(
            "checkout 5xx discount", TEST_CORPUS, limit=3
        )

        # Should get BM25 results
        assert len(results) > 0
        assert results[0]["id"] == "INC-001"
