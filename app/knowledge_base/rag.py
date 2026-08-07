"""Knowledge Base - RAG-based incident knowledge retrieval."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from enum import Enum
import hashlib

log = logging.getLogger("omnipotent.knowledge_base.rag")


class DocumentType(str, Enum):
    """Types of knowledge documents."""
    RUNBOOK = "runbook"
    POSTMORTEM = "postmortem"
    DOCUMENTATION = "documentation"
    FAQ = "faq"
    ALERT_PATTERN = "alert_pattern"
    INCIDENT_RESOLUTION = "incident_resolution"
    ARCHITECTURE = "architecture"


@dataclass
class Document:
    """A knowledge base document."""
    id: str
    title: str
    content: str
    doc_type: DocumentType
    tags: list[str] = field(default_factory=list)
    service: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    author: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "content": self.content[:500] + "..." if len(self.content) > 500 else self.content,
            "doc_type": self.doc_type.value,
            "tags": self.tags,
            "service": self.service,
            "created_at": self.created_at.isoformat(),
            "author": self.author,
        }


@dataclass
class SearchResult:
    """A search result from the knowledge base."""
    document: Document
    relevance_score: float
    snippet: str
    matched_terms: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "document": self.document.to_dict(),
            "relevance_score": self.relevance_score,
            "snippet": self.snippet,
            "matched_terms": self.matched_terms,
        }


@dataclass
class RAGResponse:
    """Response from RAG query."""
    query: str
    answer: str
    sources: list[SearchResult]
    confidence: float
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "answer": self.answer,
            "sources": [s.to_dict() for s in self.sources],
            "confidence": self.confidence,
            "generated_at": self.generated_at.isoformat(),
        }


class KnowledgeBase:
    """RAG-based knowledge retrieval system.

    Features:
    - Document ingestion and indexing
    - Semantic search
    - Context-aware retrieval
    - Answer generation with citations
    - Automatic learning from incidents
    """

    def __init__(self):
        """Initialize the knowledge base."""
        self._documents: dict[str, Document] = {}
        self._embeddings: dict[str, list[float]] = {}
        self._index_built = False
        log.info("KnowledgeBase initialized")

    def add_document(self, document: Document) -> str:
        """Add a document to the knowledge base.

        Args:
            document: Document to add

        Returns:
            Document ID
        """
        if not document.id:
            document.id = self._generate_id(document.title + document.content)

        self._documents[document.id] = document
        self._embeddings[document.id] = self._generate_embedding(document.content)

        log.info("Added document: %s (%s)", document.title, document.doc_type.value)
        return document.id

    def _generate_id(self, content: str) -> str:
        """Generate a document ID."""
        return hashlib.md5(content.encode(), usedforsecurity=False).hexdigest()[:12]

    def _generate_embedding(self, text: str) -> list[float]:
        """Generate text embedding.

        In production, use a proper embedding model (OpenAI, Cohere, etc.)
        """
        # Simplified: use word frequency as pseudo-embedding
        words = text.lower().split()
        word_freq: dict[str, int] = {}
        for word in words:
            word_freq[word] = word_freq.get(word, 0) + 1

        # Create fixed-size vector
        embedding = [0.0] * 128
        for i, (word, freq) in enumerate(sorted(word_freq.items())[:128]):
            embedding[i] = freq / len(words)

        return embedding

    def _cosine_similarity(self, a: list[float], b: list[float]) -> float:
        """Calculate cosine similarity between two vectors."""
        dot_product = sum(x * y for x, y in zip(a, b))
        norm_a = sum(x ** 2 for x in a) ** 0.5
        norm_b = sum(x ** 2 for x in b) ** 0.5

        if norm_a == 0 or norm_b == 0:
            return 0.0

        return dot_product / (norm_a * norm_b)

    async def search(
        self,
        query: str,
        doc_type: DocumentType | None = None,
        service: str | None = None,
        limit: int = 5,
    ) -> list[SearchResult]:
        """Search the knowledge base.

        Args:
            query: Search query
            doc_type: Filter by document type
            service: Filter by service
            limit: Maximum results

        Returns:
            List of search results
        """
        query_embedding = self._generate_embedding(query)
        query_terms = set(query.lower().split())

        results: list[SearchResult] = []

        for doc_id, doc in self._documents.items():
            # Apply filters
            if doc_type and doc.doc_type != doc_type:
                continue
            if service and doc.service != service:
                continue

            # Calculate relevance
            embedding = self._embeddings.get(doc_id, [])
            semantic_score = self._cosine_similarity(query_embedding, embedding)

            # Keyword matching
            doc_terms = set(doc.content.lower().split())
            matched_terms = query_terms & doc_terms
            keyword_score = len(matched_terms) / len(query_terms) if query_terms else 0

            # Combined score
            relevance = (semantic_score * 0.6) + (keyword_score * 0.4)

            if relevance > 0.1:  # Threshold
                snippet = self._extract_snippet(doc.content, query)
                results.append(SearchResult(
                    document=doc,
                    relevance_score=relevance,
                    snippet=snippet,
                    matched_terms=list(matched_terms),
                ))

        # Sort by relevance
        results.sort(key=lambda r: r.relevance_score, reverse=True)

        return results[:limit]

    def _extract_snippet(self, content: str, query: str, length: int = 200) -> str:
        """Extract relevant snippet from content."""
        query_words = query.lower().split()

        # Find position of first matching word
        content_lower = content.lower()
        best_pos = 0

        for word in query_words:
            pos = content_lower.find(word)
            if pos != -1:
                best_pos = pos
                break

        # Extract snippet around match
        start = max(0, best_pos - 50)
        end = min(len(content), best_pos + length)

        snippet = content[start:end]
        if start > 0:
            snippet = "..." + snippet
        if end < len(content):
            snippet = snippet + "..."

        return snippet

    async def query(
        self,
        question: str,
        service: str | None = None,
    ) -> RAGResponse:
        """Query the knowledge base with RAG.

        Args:
            question: Natural language question
            service: Filter to specific service

        Returns:
            RAGResponse with answer and sources
        """
        log.info("RAG query: %s", question)

        # Search for relevant documents
        results = await self.search(question, service=service, limit=3)

        if not results:
            return RAGResponse(
                query=question,
                answer="I couldn't find relevant information in the knowledge base.",
                sources=[],
                confidence=0.0,
            )

        # Generate answer from sources
        context = "\n\n".join([
            f"Source: {r.document.title}\n{r.snippet}"
            for r in results
        ])

        # In production: use LLM to generate answer
        answer = self._generate_answer(question, context, results)

        # Calculate confidence
        avg_relevance = sum(r.relevance_score for r in results) / len(results)
        confidence = min(1.0, avg_relevance * 1.5)

        return RAGResponse(
            query=question,
            answer=answer,
            sources=results,
            confidence=confidence,
        )

    def _generate_answer(
        self,
        question: str,
        context: str,
        results: list[SearchResult],
    ) -> str:
        """Generate answer from context.

        In production, use an LLM for this.
        """
        # Simple extractive answer
        if results:
            top_result = results[0]
            return (
                f"Based on the knowledge base, here's what I found:\n\n"
                f"From '{top_result.document.title}':\n"
                f"{top_result.snippet}\n\n"
                f"Relevance: {top_result.relevance_score:.0%}"
            )

        return "No relevant information found."

    async def learn_from_incident(
        self,
        incident_id: str,
        root_cause: str,
        resolution: str,
        service: str,
        symptoms: list[str],
    ) -> str:
        """Learn from a resolved incident.

        Args:
            incident_id: Incident identifier
            root_cause: Root cause description
            resolution: How it was resolved
            service: Affected service
            symptoms: Symptoms/alerts observed

        Returns:
            Document ID of created knowledge
        """
        content = f"""
# Incident Resolution: {incident_id}

## Symptoms
{chr(10).join('- ' + s for s in symptoms)}

## Root Cause
{root_cause}

## Resolution
{resolution}

## Service
{service}
        """.strip()

        doc = Document(
            id="",
            title=f"Incident Resolution: {incident_id}",
            content=content,
            doc_type=DocumentType.INCIDENT_RESOLUTION,
            tags=symptoms + [service],
            service=service,
            metadata={"incident_id": incident_id},
        )

        return self.add_document(doc)

    def get_document(self, doc_id: str) -> Document | None:
        """Get a document by ID."""
        return self._documents.get(doc_id)

    def list_documents(
        self,
        doc_type: DocumentType | None = None,
        service: str | None = None,
    ) -> list[Document]:
        """List all documents."""
        docs = list(self._documents.values())

        if doc_type:
            docs = [d for d in docs if d.doc_type == doc_type]
        if service:
            docs = [d for d in docs if d.service == service]

        return docs

    def get_stats(self) -> dict[str, Any]:
        """Get knowledge base statistics."""
        docs = list(self._documents.values())

        by_type: dict[str, int] = {}
        for doc in docs:
            t = doc.doc_type.value
            by_type[t] = by_type.get(t, 0) + 1

        by_service: dict[str, int] = {}
        for doc in docs:
            s = doc.service or "unknown"
            by_service[s] = by_service.get(s, 0) + 1

        return {
            "total_documents": len(docs),
            "by_type": by_type,
            "by_service": by_service,
        }


def create_knowledge_base() -> KnowledgeBase:
    """Factory function to create a knowledge base."""
    kb = KnowledgeBase()

    # Seed with some example documents
    kb.add_document(Document(
        id="runbook-db-connection",
        title="Database Connection Issues Runbook",
        content="""
        # Database Connection Issues

        ## Symptoms
        - Connection timeout errors
        - "too many connections" errors
        - Slow query responses

        ## Diagnosis
        1. Check connection pool status
        2. Verify database health
        3. Check network connectivity

        ## Resolution
        1. Scale up connection pool if needed
        2. Restart affected services
        3. Contact DBA for database-level issues
        """,
        doc_type=DocumentType.RUNBOOK,
        tags=["database", "connection", "timeout"],
        service="database",
    ))

    kb.add_document(Document(
        id="postmortem-api-outage",
        title="API Gateway Outage - 2024-01-15",
        content="""
        # Postmortem: API Gateway Outage

        ## Impact
        - 45 minutes of degraded service
        - 15% of requests failed

        ## Root Cause
        Memory leak in connection pooling after config change.

        ## Resolution
        Rolled back config change and restarted pods.

        ## Prevention
        - Add memory monitoring alerts
        - Canary deployments for config changes
        """,
        doc_type=DocumentType.POSTMORTEM,
        tags=["api-gateway", "outage", "memory"],
        service="api-gateway",
    ))

    return kb
