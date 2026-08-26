"""Knowledge Base - RAG-based incident knowledge retrieval."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from enum import Enum
import hashlib

log = logging.getLogger("omnipotent.knowledge_base.rag")

# How much of a document body goes into a list response as a preview.
EXCERPT_CHARS = 500


def _terms(text: str) -> list[str]:
    """Split text the way the ranker does, so `matched_terms` cannot disagree
    with `relevance_score`."""
    from app.incident_memory import tokenize

    return tokenize(text)


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
        """The document as the API returns it.

        `content` is the whole body. It used to be cut to 500 characters with a
        literal "..." appended, and the Knowledge Base page renders list items
        directly without ever calling the detail endpoint — so every runbook
        longer than 500 characters, including the shipped kb-001, was displayed
        to the reader with its body truncated mid-sentence and no way to see the
        rest. The preview that truncation was for is `excerpt`.
        """
        return {
            "id": self.id,
            "title": self.title,
            "content": self.content,
            "excerpt": (
                self.content[:EXCERPT_CHARS] + "..."
                if len(self.content) > EXCERPT_CHARS
                else self.content
            ),
            "doc_type": self.doc_type.value,
            "tags": self.tags,
            "service": self.service,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
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
    """Knowledge retrieval over a set of documents.

    Features:
    - Document ingestion and indexing
    - BM25 keyword search over title, body, tags and service
    - Context-aware retrieval
    - Answer generation with citations
    - Automatic learning from incidents
    """

    def __init__(self):
        """Initialize the knowledge base."""
        self._documents: dict[str, Document] = {}
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

        log.info("Added document: %s (%s)", document.title, document.doc_type.value)
        return document.id

    def _generate_id(self, content: str) -> str:
        """Generate a document ID."""
        return hashlib.md5(content.encode(), usedforsecurity=False).hexdigest()[:12]

    def _indexed_text(self, doc: Document) -> str:
        """The text a document is matched on: its title and tags count, not just the body."""
        return "\n".join([doc.title, doc.content, " ".join(doc.tags), doc.service or ""])

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
            List of search results, best first. A query that shares no term with
            any document returns nothing.

        Ranking is BM25 over the whole corpus (`app.incident_memory`, the same
        implementation the memory MCP server and the planner use). It used to be
        0.6 * cosine-similarity-of-a-pseudo-embedding + 0.4 * keyword overlap,
        where the "embedding" put a word's frequency at the index of its
        alphabetical rank -- so dimension 7 meant nothing, two documents were
        similar when their word-frequency *profiles* lined up, and the score
        carried no information about the query at all. Two different nonsense
        queries returned every document in the org, identically ranked, with
        scores up to 0.39 and `matched_terms: []`.
        """
        from app.incident_memory import bm25_relevance

        candidates = [
            doc
            for doc in self._documents.values()
            if (not doc_type or doc.doc_type == doc_type)
            and (not service or doc.service == service)
        ]
        if not candidates:
            return []

        query_terms = set(_terms(query))
        scores = bm25_relevance(query, [self._indexed_text(doc) for doc in candidates])

        results: list[SearchResult] = []
        for doc, relevance in zip(candidates, scores):
            if relevance <= 0:
                continue
            matched = sorted(query_terms & set(_terms(self._indexed_text(doc))))
            results.append(SearchResult(
                document=doc,
                relevance_score=round(relevance, 4),
                snippet=self._extract_snippet(doc.content, query),
                matched_terms=matched,
            ))

        results.sort(key=lambda r: (-r.relevance_score, r.document.id))
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
