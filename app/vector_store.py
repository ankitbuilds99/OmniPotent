"""Vector store for semantic incident search — adds embedding-based retrieval
on top of the existing BM25 lexical search.

Supports ChromaDB (default) or FAISS for local vector storage, and configurable
embedding backends (OpenAI text-embedding-3-small or local sentence-transformers).

Key design choices:
  - Backwards compatible: when COPILOT_VECTOR_STORE=none (the default), all vector
    operations are no-ops and the existing BM25-only path continues to work.
  - Hybrid search: combines BM25 lexical scores with vector similarity via RRF
    (Reciprocal Rank Fusion) for better recall without losing lexical grounding.
  - Lazy initialization: embedding models and vector stores are loaded only on
    first use, avoiding startup cost when vectors are disabled.
  - Pure functions where possible: embedding and search are side-effect-free;
    only add_document() mutates state.

The vector store lives alongside the JSON corpus; it's automatically populated
on first search if empty, and new incidents are added incrementally as they're
learned.
"""

from __future__ import annotations

import hashlib
import logging
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol, Sequence, TypedDict

if TYPE_CHECKING:
    from chromadb import ClientAPI as ChromaClient
    from chromadb.api.models.Collection import Collection as ChromaCollection

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

class VectorSearchResult(TypedDict):
    id: str
    score: float
    document: str
    metadata: dict[str, Any]


class HybridSearchResult(TypedDict):
    """A record annotated with hybrid search scores."""
    id: str
    bm25_rank: int | None
    vector_rank: int | None
    rrf_score: float
    record: dict[str, Any]


VectorStoreType = Literal["chromadb", "faiss", "none"]
EmbeddingModelType = Literal["text-embedding-3-small", "text-embedding-3-large", "all-MiniLM-L6-v2", "all-mpnet-base-v2"]


class EmbeddingBackend(Protocol):
    """Protocol for embedding backends."""
    def embed(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...
    @property
    def dimension(self) -> int: ...


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def _get_vector_config() -> tuple[VectorStoreType, EmbeddingModelType, Path]:
    """Read vector config from environment/settings.

    Returns (store_type, embedding_model, persist_directory).
    """
    from app.config import get_settings

    settings = get_settings()

    store_type = getattr(settings, "copilot_vector_store", "none").strip().lower()
    if store_type not in ("chromadb", "faiss", "none"):
        store_type = "none"

    embedding_model = getattr(settings, "copilot_embedding_model", "text-embedding-3-small").strip()
    if not embedding_model:
        embedding_model = "text-embedding-3-small"

    # Everything this process persists locally lives under one directory
    # (COPILOT_STATE_DIR), so the vectors sit next to the checkpoint/tenant DBs
    # and a single mounted volume keeps them all. A bare persist-dir name lands
    # there; an explicit path ("./vectors", "/mnt/vectors") is honoured as
    # written. Resolution only — the backends mkdir it when they open it.
    persist_dir = getattr(settings, "copilot_vector_persist_dir", "").strip()
    persist_path = (
        settings.state_path(persist_dir) if persist_dir else settings.state_dir / "vector_store"
    )

    return store_type, embedding_model, persist_path  # type: ignore


# ---------------------------------------------------------------------------
# Embedding Backends
# ---------------------------------------------------------------------------

class OpenAIEmbeddings:
    """OpenAI embedding backend using text-embedding-3-* models."""

    def __init__(self, model: str = "text-embedding-3-small"):
        self.model = model
        self._client: Any = None
        self._dimension = 1536 if "small" in model else 3072

    @property
    def client(self) -> Any:
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError as e:
                raise ImportError(
                    "openai package required for OpenAI embeddings. "
                    "Install with: pip install openai"
                ) from e

            from app.config import get_settings
            api_key = get_settings().openai_api_key
            if not api_key:
                raise ValueError(
                    "OPENAI_API_KEY required for OpenAI embeddings. "
                    "Set it in .env or use a local embedding model."
                )
            self._client = OpenAI(api_key=api_key)
        return self._client

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        # OpenAI API accepts up to 2048 texts per batch
        batch_size = 2048
        all_embeddings: list[list[float]] = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            response = self.client.embeddings.create(input=batch, model=self.model)
            all_embeddings.extend([d.embedding for d in response.data])
        return all_embeddings

    def embed_query(self, text: str) -> list[float]:
        return self.embed([text])[0]

    @property
    def dimension(self) -> int:
        return self._dimension


class SentenceTransformerEmbeddings:
    """Local embedding backend using sentence-transformers."""

    def __init__(self, model: str = "all-MiniLM-L6-v2"):
        self.model_name = model
        self._model: Any = None
        self._dimension: int | None = None

    @property
    def model(self) -> Any:
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as e:
                raise ImportError(
                    "sentence-transformers package required for local embeddings. "
                    "Install with: pip install sentence-transformers"
                ) from e
            self._model = SentenceTransformer(self.model_name)
            self._dimension = self._model.get_sentence_embedding_dimension()
        return self._model

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        embeddings = self.model.encode(texts, convert_to_numpy=True)
        return [emb.tolist() for emb in embeddings]

    def embed_query(self, text: str) -> list[float]:
        return self.embed([text])[0]

    @property
    def dimension(self) -> int:
        if self._dimension is None:
            # Force model load to get dimension
            _ = self.model
        return self._dimension or 384  # MiniLM default


@lru_cache(maxsize=1)
def get_embedding_backend() -> EmbeddingBackend | None:
    """Get the configured embedding backend (cached singleton)."""
    store_type, embedding_model, _ = _get_vector_config()

    if store_type == "none":
        return None

    if embedding_model.startswith("text-embedding-"):
        return OpenAIEmbeddings(model=embedding_model)
    else:
        return SentenceTransformerEmbeddings(model=embedding_model)


# ---------------------------------------------------------------------------
# Vector Store Backends
# ---------------------------------------------------------------------------

class VectorStore(Protocol):
    """Protocol for vector store backends."""
    def add(self, ids: list[str], embeddings: list[list[float]], documents: list[str], metadatas: list[dict]) -> None: ...
    def search(self, query_embedding: list[float], k: int) -> list[VectorSearchResult]: ...
    def count(self) -> int: ...
    def delete(self, ids: list[str]) -> None: ...
    def has_id(self, doc_id: str) -> bool: ...


class ChromaDBStore:
    """ChromaDB vector store backend."""

    def __init__(self, persist_directory: Path, collection_name: str = "incidents"):
        self.persist_directory = persist_directory
        self.collection_name = collection_name
        self._client: ChromaClient | None = None
        self._collection: ChromaCollection | None = None

    @property
    def client(self) -> "ChromaClient":
        if self._client is None:
            try:
                import chromadb
                from chromadb.config import Settings as ChromaSettings
            except ImportError as e:
                raise ImportError(
                    "chromadb package required for ChromaDB vector store. "
                    "Install with: pip install chromadb"
                ) from e

            self.persist_directory.mkdir(parents=True, exist_ok=True)
            self._client = chromadb.Client(ChromaSettings(
                persist_directory=str(self.persist_directory),
                is_persistent=True,
                anonymized_telemetry=False,
            ))
        return self._client

    @property
    def collection(self) -> "ChromaCollection":
        if self._collection is None:
            self._collection = self.client.get_or_create_collection(
                name=self.collection_name,
                metadata={"hnsw:space": "cosine"}
            )
        return self._collection

    def add(
        self,
        ids: list[str],
        embeddings: list[list[float]],
        documents: list[str],
        metadatas: list[dict]
    ) -> None:
        if not ids:
            return
        # ChromaDB handles upsert automatically
        self.collection.upsert(
            ids=ids,
            embeddings=embeddings,
            documents=documents,
            metadatas=metadatas
        )

    def search(self, query_embedding: list[float], k: int) -> list[VectorSearchResult]:
        results = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=k,
            include=["documents", "metadatas", "distances"]
        )

        output: list[VectorSearchResult] = []
        if results["ids"] and results["ids"][0]:
            for i, doc_id in enumerate(results["ids"][0]):
                # ChromaDB returns distances, convert to similarity score
                # For cosine distance: similarity = 1 - distance
                distance = results["distances"][0][i] if results["distances"] else 0
                score = 1.0 - distance
                output.append(VectorSearchResult(
                    id=doc_id,
                    score=score,
                    document=results["documents"][0][i] if results["documents"] else "",
                    metadata=results["metadatas"][0][i] if results["metadatas"] else {}
                ))
        return output

    def count(self) -> int:
        return self.collection.count()

    def delete(self, ids: list[str]) -> None:
        if ids:
            self.collection.delete(ids=ids)

    def has_id(self, doc_id: str) -> bool:
        results = self.collection.get(ids=[doc_id], include=[])
        return bool(results["ids"])


class FAISSStore:
    """FAISS vector store backend with metadata stored in a sidecar JSON file."""

    def __init__(self, persist_directory: Path, dimension: int):
        self.persist_directory = persist_directory
        self.dimension = dimension
        self.index_path = persist_directory / "faiss.index"
        self.meta_path = persist_directory / "faiss_meta.json"
        self._index: Any = None
        self._id_to_idx: dict[str, int] = {}
        self._idx_to_id: dict[int, str] = {}
        self._documents: dict[str, str] = {}
        self._metadatas: dict[str, dict] = {}
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return

        try:
            import faiss
        except ImportError as e:
            raise ImportError(
                "faiss-cpu package required for FAISS vector store. "
                "Install with: pip install faiss-cpu"
            ) from e

        self.persist_directory.mkdir(parents=True, exist_ok=True)

        if self.index_path.exists() and self.meta_path.exists():
            import json
            self._index = faiss.read_index(str(self.index_path))
            meta = json.loads(self.meta_path.read_text())
            self._id_to_idx = meta.get("id_to_idx", {})
            self._idx_to_id = {int(v): k for k, v in self._id_to_idx.items()}
            self._documents = meta.get("documents", {})
            self._metadatas = meta.get("metadatas", {})
        else:
            self._index = faiss.IndexFlatIP(self.dimension)  # Inner product for cosine
            self._id_to_idx = {}
            self._idx_to_id = {}
            self._documents = {}
            self._metadatas = {}

        self._loaded = True

    def _save(self) -> None:
        import json
        try:
            import faiss
        except ImportError:
            return

        faiss.write_index(self._index, str(self.index_path))
        meta = {
            "id_to_idx": self._id_to_idx,
            "documents": self._documents,
            "metadatas": self._metadatas,
        }
        self.meta_path.write_text(json.dumps(meta))

    def add(
        self,
        ids: list[str],
        embeddings: list[list[float]],
        documents: list[str],
        metadatas: list[dict]
    ) -> None:
        if not ids:
            return

        self._ensure_loaded()

        import numpy as np

        # Normalize embeddings for cosine similarity
        emb_array = np.array(embeddings, dtype=np.float32)
        norms = np.linalg.norm(emb_array, axis=1, keepdims=True)
        norms[norms == 0] = 1
        emb_array = emb_array / norms

        for i, doc_id in enumerate(ids):
            if doc_id in self._id_to_idx:
                # Update existing: FAISS doesn't support in-place update, so we just
                # update metadata. For full update, would need to rebuild the index.
                self._documents[doc_id] = documents[i]
                self._metadatas[doc_id] = metadatas[i]
            else:
                idx = self._index.ntotal
                self._index.add(emb_array[i:i+1])
                self._id_to_idx[doc_id] = idx
                self._idx_to_id[idx] = doc_id
                self._documents[doc_id] = documents[i]
                self._metadatas[doc_id] = metadatas[i]

        self._save()

    def search(self, query_embedding: list[float], k: int) -> list[VectorSearchResult]:
        self._ensure_loaded()

        if self._index.ntotal == 0:
            return []

        import numpy as np

        # Normalize query
        query = np.array([query_embedding], dtype=np.float32)
        norm = np.linalg.norm(query)
        if norm > 0:
            query = query / norm

        k = min(k, self._index.ntotal)
        distances, indices = self._index.search(query, k)

        output: list[VectorSearchResult] = []
        for i, idx in enumerate(indices[0]):
            if idx < 0:  # FAISS returns -1 for missing
                continue
            doc_id = self._idx_to_id.get(int(idx))
            if doc_id:
                output.append(VectorSearchResult(
                    id=doc_id,
                    score=float(distances[0][i]),  # Already cosine similarity due to IP
                    document=self._documents.get(doc_id, ""),
                    metadata=self._metadatas.get(doc_id, {})
                ))
        return output

    def count(self) -> int:
        self._ensure_loaded()
        return self._index.ntotal

    def delete(self, ids: list[str]) -> None:
        # FAISS doesn't support efficient deletion; mark as deleted in metadata
        self._ensure_loaded()
        for doc_id in ids:
            if doc_id in self._id_to_idx:
                self._documents.pop(doc_id, None)
                self._metadatas.pop(doc_id, None)
                # Note: vector remains in index but won't be returned due to missing metadata
        self._save()

    def has_id(self, doc_id: str) -> bool:
        self._ensure_loaded()
        return doc_id in self._id_to_idx and doc_id in self._documents


_vector_store_instance: VectorStore | None = None
_vector_store_initialized = False


def get_vector_store() -> VectorStore | None:
    """Get the configured vector store (cached singleton)."""
    global _vector_store_instance, _vector_store_initialized

    if _vector_store_initialized:
        return _vector_store_instance

    store_type, embedding_model, persist_dir = _get_vector_config()

    if store_type == "none":
        _vector_store_initialized = True
        _vector_store_instance = None
        return None

    embedding_backend = get_embedding_backend()
    if embedding_backend is None:
        _vector_store_initialized = True
        _vector_store_instance = None
        return None

    if store_type == "chromadb":
        _vector_store_instance = ChromaDBStore(persist_dir)
    elif store_type == "faiss":
        _vector_store_instance = FAISSStore(persist_dir, dimension=embedding_backend.dimension)
    else:
        _vector_store_instance = None

    _vector_store_initialized = True
    return _vector_store_instance


def reset_vector_store() -> None:
    """Reset the vector store singleton (for testing)."""
    global _vector_store_instance, _vector_store_initialized
    _vector_store_instance = None
    _vector_store_initialized = False
    get_embedding_backend.cache_clear()


# ---------------------------------------------------------------------------
# Core Functions
# ---------------------------------------------------------------------------

def embed_text(text: str) -> list[float] | None:
    """Embed a single text string. Returns None if vectors are disabled."""
    backend = get_embedding_backend()
    if backend is None:
        return None
    try:
        return backend.embed_query(text)
    except Exception as e:
        logger.warning(f"Embedding failed: {e}")
        return None


def embed_texts(texts: list[str]) -> list[list[float]] | None:
    """Embed multiple texts. Returns None if vectors are disabled."""
    backend = get_embedding_backend()
    if backend is None:
        return None
    try:
        return backend.embed(texts)
    except Exception as e:
        logger.warning(f"Batch embedding failed: {e}")
        return None


def _record_to_text(record: dict) -> str:
    """Convert an incident record to text for embedding."""
    parts = [
        record.get("title", ""),
        record.get("summary", ""),
        record.get("root_cause", ""),
        record.get("service", ""),
        " ".join(record.get("tags", [])),
        record.get("resolution", ""),
    ]
    runbook = record.get("runbook", [])
    if runbook:
        parts.append(" ".join(str(step) for step in runbook))
    return " ".join(p for p in parts if p)


def _record_id(record: dict) -> str:
    """Get a stable ID for a record."""
    if "id" in record:
        return str(record["id"])
    # Fallback: hash the content
    text = _record_to_text(record)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def add_document(record: dict) -> bool:
    """Add an incident record to the vector store.

    Returns True if successfully added, False if vectors are disabled or on error.
    """
    store = get_vector_store()
    if store is None:
        return False

    backend = get_embedding_backend()
    if backend is None:
        return False

    doc_id = _record_id(record)
    text = _record_to_text(record)

    try:
        embedding = backend.embed_query(text)
        metadata = {
            "title": record.get("title", "")[:500],
            "service": record.get("service", ""),
            "date": record.get("date", ""),
        }
        store.add([doc_id], [embedding], [text], [metadata])
        return True
    except Exception as e:
        logger.warning(f"Failed to add document to vector store: {e}")
        return False


def add_documents(records: Sequence[dict]) -> int:
    """Add multiple incident records to the vector store.

    Returns the number of successfully added documents.
    """
    store = get_vector_store()
    if store is None:
        return 0

    backend = get_embedding_backend()
    if backend is None:
        return 0

    if not records:
        return 0

    # Filter out records already in the store
    new_records = []
    for record in records:
        doc_id = _record_id(record)
        if not store.has_id(doc_id):
            new_records.append(record)

    if not new_records:
        return 0

    try:
        texts = [_record_to_text(r) for r in new_records]
        embeddings = backend.embed(texts)
        if embeddings is None:
            return 0

        ids = [_record_id(r) for r in new_records]
        metadatas = [
            {
                "title": r.get("title", "")[:500],
                "service": r.get("service", ""),
                "date": r.get("date", ""),
            }
            for r in new_records
        ]

        store.add(ids, embeddings, texts, metadatas)
        return len(new_records)
    except Exception as e:
        logger.warning(f"Failed to add documents to vector store: {e}")
        return 0


def search_similar(query: str, k: int = 10) -> list[VectorSearchResult]:
    """Search for similar incidents using vector similarity.

    Returns empty list if vectors are disabled or on error.
    """
    store = get_vector_store()
    if store is None:
        return []

    backend = get_embedding_backend()
    if backend is None:
        return []

    try:
        query_embedding = backend.embed_query(query)
        return store.search(query_embedding, k)
    except Exception as e:
        logger.warning(f"Vector search failed: {e}")
        return []


def ensure_corpus_indexed(records: Sequence[dict]) -> int:
    """Ensure the corpus is indexed in the vector store.

    Idempotent: only adds records not already present.
    Returns the number of newly indexed records.
    """
    store = get_vector_store()
    if store is None:
        return 0

    return add_documents(records)


# ---------------------------------------------------------------------------
# Hybrid Search (BM25 + Vector with RRF)
# ---------------------------------------------------------------------------

def reciprocal_rank_fusion(
    bm25_results: list[dict],
    vector_results: list[VectorSearchResult],
    k: int = 60,  # RRF constant, typically 60
) -> list[HybridSearchResult]:
    """Combine BM25 and vector search results using Reciprocal Rank Fusion.

    RRF score = sum(1 / (k + rank)) across all result lists.
    Higher k gives more weight to items appearing in multiple lists vs. rank.

    This is a well-established fusion method that doesn't require score normalization
    and handles the different score scales of BM25 and vector similarity gracefully.
    """
    scores: dict[str, float] = {}
    bm25_ranks: dict[str, int] = {}
    vector_ranks: dict[str, int] = {}
    records: dict[str, dict] = {}

    # Process BM25 results
    for rank, rec in enumerate(bm25_results, start=1):
        doc_id = _record_id(rec)
        scores[doc_id] = scores.get(doc_id, 0) + 1.0 / (k + rank)
        bm25_ranks[doc_id] = rank
        records[doc_id] = rec

    # Process vector results
    for rank, vr in enumerate(vector_results, start=1):
        doc_id = vr["id"]
        scores[doc_id] = scores.get(doc_id, 0) + 1.0 / (k + rank)
        vector_ranks[doc_id] = rank
        # If we don't have the full record from BM25, we only have metadata
        if doc_id not in records:
            # Reconstruct minimal record from metadata
            records[doc_id] = {
                "id": doc_id,
                "title": vr["metadata"].get("title", ""),
                "service": vr["metadata"].get("service", ""),
                "date": vr["metadata"].get("date", ""),
                "_vector_only": True,  # Flag that this needs full record lookup
            }

    # Sort by RRF score
    sorted_ids = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)

    return [
        HybridSearchResult(
            id=doc_id,
            bm25_rank=bm25_ranks.get(doc_id),
            vector_rank=vector_ranks.get(doc_id),
            rrf_score=round(scores[doc_id], 6),
            record=records[doc_id],
        )
        for doc_id in sorted_ids
    ]


def hybrid_search(
    query: str,
    records: list[dict],
    limit: int = 5,
    bm25_weight: float = 0.5,
    vector_weight: float = 0.5,
) -> list[dict]:
    """Hybrid search combining BM25 and vector similarity.

    When vectors are disabled, falls back to pure BM25.
    When both are available, uses RRF to fuse rankings.

    Args:
        query: Search query
        records: Corpus of incident records
        limit: Max results to return
        bm25_weight: Weight for BM25 (for future weighted fusion, currently using RRF)
        vector_weight: Weight for vectors (for future weighted fusion)

    Returns:
        List of records annotated with a `score` (best first), matching the
        existing BM25 search API for backwards compatibility.
    """
    from app.incident_memory import bm25_rank

    # Always run BM25 (the lexical baseline)
    bm25_results = bm25_rank(query, records)

    # Try vector search
    store = get_vector_store()
    if store is None or store.count() == 0:
        # No vectors available, return pure BM25
        return [r for r in bm25_results if r.get("score", 0) > 0][:limit]

    # Ensure corpus is indexed (idempotent)
    ensure_corpus_indexed(records)

    # Run vector search
    vector_results = search_similar(query, k=limit * 2)

    if not vector_results:
        # Vector search failed or returned nothing, fall back to BM25
        return [r for r in bm25_results if r.get("score", 0) > 0][:limit]

    # Fuse results with RRF
    fused = reciprocal_rank_fusion(bm25_results, vector_results)

    # Map back to full records and annotate with hybrid score
    id_to_record = {_record_id(r): r for r in records}
    output = []

    for hr in fused[:limit]:
        doc_id = hr["id"]
        if doc_id in id_to_record:
            rec = id_to_record[doc_id].copy()
            rec["score"] = hr["rrf_score"]
            rec["_bm25_rank"] = hr["bm25_rank"]
            rec["_vector_rank"] = hr["vector_rank"]
            output.append(rec)
        elif not hr["record"].get("_vector_only"):
            # Record came from BM25 results
            rec = hr["record"].copy()
            rec["score"] = hr["rrf_score"]
            rec["_bm25_rank"] = hr["bm25_rank"]
            rec["_vector_rank"] = hr["vector_rank"]
            output.append(rec)
        # Skip vector-only results without full records

    # Filter zero-score results (items that appear in neither BM25 nor vector top-k
    # shouldn't be returned, but RRF shouldn't produce those anyway)
    return [r for r in output if r.get("score", 0) > 0]


def is_vector_enabled() -> bool:
    """Check if vector search is enabled and properly configured."""
    store_type, _, _ = _get_vector_config()
    return store_type != "none"


def get_vector_stats() -> dict[str, Any]:
    """Get statistics about the vector store."""
    store_type, embedding_model, persist_dir = _get_vector_config()

    if store_type == "none":
        return {
            "enabled": False,
            "store_type": "none",
            "embedding_model": None,
            "document_count": 0,
        }

    store = get_vector_store()
    count = store.count() if store else 0

    return {
        "enabled": True,
        "store_type": store_type,
        "embedding_model": embedding_model,
        "persist_directory": str(persist_dir),
        "document_count": count,
    }
