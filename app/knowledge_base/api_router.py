"""Knowledge Base API Router.

REST API for knowledge base CRUD operations and search.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.knowledge_base.rag import KnowledgeBase, Document, DocumentType
from app.tenancy.scoping import scoped

log = logging.getLogger("omnipotent.knowledge_base.api")

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


def _build_knowledge_base() -> KnowledgeBase:
    kb = KnowledgeBase()
    _seed_default_documents(kb)
    return kb


# One knowledge base per org. Runbook write-ups and postmortems are the most
# sensitive text a customer uploads here — architecture, credentials-adjacent
# procedures, outage detail — and a shared instance made every document
# searchable by every other tenant.
_bases = scoped(_build_knowledge_base)


def get_knowledge_base() -> KnowledgeBase:
    """The knowledge base for the org on the current request."""
    return _bases.get()


def _seed_default_documents(kb: KnowledgeBase) -> None:
    """Seed the knowledge base with default documents if empty."""
    default_docs = [
        Document(
            id="kb-001",
            title="Database Connection Pool Exhaustion",
            content="""## Symptoms
- High latency on database queries
- Errors: "Connection pool exhausted"
- Increasing queue depth in metrics

## Root Cause Analysis
Connection pool exhaustion typically occurs when:
1. Connection leaks (not returning connections)
2. Long-running queries holding connections
3. Pool size too small for load

## Resolution Steps
1. Check current pool usage: `SELECT count(*) FROM pg_stat_activity`
2. Identify long-running queries
3. If urgent: Restart the affected service
4. For permanent fix: Increase pool size or fix leaks

## Prevention
- Set connection timeouts
- Use connection pool monitoring
- Regular load testing""",
            doc_type=DocumentType.RUNBOOK,
            tags=["database", "postgres", "performance"],
            author="Platform Team",
            service="database",
        ),
        Document(
            id="kb-002",
            title="Kubernetes Pod Crash Loop",
            content="""## Definition
A CrashLoopBackOff occurs when a container repeatedly fails to start.

## Debugging Steps
1. Get pod status: `kubectl describe pod <name>`
2. Check logs: `kubectl logs <pod> --previous`
3. Check events: `kubectl get events --sort-by=.metadata.creationTimestamp`

## Common Causes
- Missing config/secrets
- Resource limits too low
- Image pull failures
- Application startup errors

## Quick Fixes
- Increase memory limits
- Fix readiness/liveness probes
- Check image tag exists""",
            doc_type=DocumentType.RUNBOOK,
            tags=["kubernetes", "k8s", "pods", "debugging"],
            author="SRE Team",
        ),
        Document(
            id="kb-003",
            title="MTTR - Mean Time To Recovery",
            content="""## Definition
Mean Time To Recovery (MTTR) measures the average time to recover from a failure.

## Formula
MTTR = Total Downtime / Number of Incidents

## Best Practices
- Target: < 30 minutes for critical services
- Track by service and team
- Include detection + diagnosis + recovery time

## Improvement Strategies
- Automated rollback procedures
- Pre-built runbooks
- AI-assisted root cause analysis""",
            doc_type=DocumentType.DOCUMENTATION,
            tags=["metrics", "sre", "reliability"],
            author="SRE Team",
        ),
        Document(
            id="kb-004",
            title="High CPU Usage Investigation",
            content="""## Symptoms
- CPU utilization > 80%
- Slow response times
- Service degradation

## Investigation Steps
1. Identify hot processes: `top` or `htop`
2. Check for runaway queries or jobs
3. Review recent deployments
4. Check for memory pressure causing swapping

## Resolution
- Scale horizontally (add pods)
- Optimize hot code paths
- Add rate limiting
- Review and cancel runaway jobs""",
            doc_type=DocumentType.RUNBOOK,
            tags=["cpu", "performance", "investigation"],
            author="Platform Team",
        ),
        Document(
            id="kb-005",
            title="Service Mesh Troubleshooting",
            content="""## Common Issues
- Sidecar injection not working
- mTLS certificate issues
- Service discovery failures

## Debugging Commands
```bash
# Check Istio proxy status
istioctl proxy-status

# Analyze configuration
istioctl analyze

# Check sidecar logs
kubectl logs <pod> -c istio-proxy
```

## Best Practices
- Use strict mTLS mode
- Configure proper timeouts
- Set up circuit breakers""",
            doc_type=DocumentType.RUNBOOK,
            tags=["istio", "service-mesh", "networking"],
            author="Platform Team",
        ),
    ]

    for doc in default_docs:
        kb.add_document(doc)

    log.info("Seeded %d default documents", len(default_docs))


# Request/Response Models

class CreateDocumentRequest(BaseModel):
    title: str
    content: str
    doc_type: str = "runbook"
    tags: list[str] = []
    service: str | None = None
    author: str = ""


class UpdateDocumentRequest(BaseModel):
    title: str | None = None
    content: str | None = None
    tags: list[str] | None = None
    service: str | None = None


class SearchRequest(BaseModel):
    query: str
    doc_types: list[str] | None = None
    tags: list[str] | None = None
    limit: int = 10


# API Endpoints

@router.get("/documents")
async def list_documents(
    doc_type: str | None = Query(None, description="Filter by document type"),
    tag: str | None = Query(None, description="Filter by tag"),
    service: str | None = Query(None, description="Filter by service"),
    search: str | None = Query(None, description="Search in title/content"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """List all documents with optional filters."""
    kb = get_knowledge_base()

    docs = list(kb._documents.values())

    # Apply filters
    if doc_type:
        docs = [d for d in docs if d.doc_type.value == doc_type]
    if tag:
        docs = [d for d in docs if tag in d.tags]
    if service:
        docs = [d for d in docs if d.service == service]
    if search:
        search_lower = search.lower()
        docs = [d for d in docs if search_lower in d.title.lower() or search_lower in d.content.lower()]

    # Sort by updated_at
    docs.sort(key=lambda d: d.updated_at, reverse=True)

    # Paginate
    total = len(docs)
    docs = docs[offset:offset + limit]

    return {
        "documents": [d.to_dict() for d in docs],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/documents/{doc_id}")
async def get_document(doc_id: str) -> dict[str, Any]:
    """Get a specific document by ID."""
    kb = get_knowledge_base()

    doc = kb._documents.get(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    return {
        "id": doc.id,
        "title": doc.title,
        "content": doc.content,
        "doc_type": doc.doc_type.value,
        "tags": doc.tags,
        "service": doc.service,
        "author": doc.author,
        "created_at": doc.created_at.isoformat(),
        "updated_at": doc.updated_at.isoformat(),
    }


@router.post("/documents")
async def create_document(req: CreateDocumentRequest) -> dict[str, Any]:
    """Create a new document."""
    kb = get_knowledge_base()

    try:
        doc_type = DocumentType(req.doc_type)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid doc_type: {req.doc_type}")

    doc = Document(
        id="",
        title=req.title,
        content=req.content,
        doc_type=doc_type,
        tags=req.tags,
        service=req.service,
        author=req.author,
    )

    doc_id = kb.add_document(doc)

    return {"id": doc_id, "message": "Document created"}


@router.put("/documents/{doc_id}")
async def update_document(doc_id: str, req: UpdateDocumentRequest) -> dict[str, Any]:
    """Update an existing document."""
    kb = get_knowledge_base()

    doc = kb._documents.get(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    if req.title is not None:
        doc.title = req.title
    if req.content is not None:
        doc.content = req.content
        kb._embeddings[doc_id] = kb._generate_embedding(doc.content)
    if req.tags is not None:
        doc.tags = req.tags
    if req.service is not None:
        doc.service = req.service

    doc.updated_at = datetime.now(timezone.utc)

    return {"message": "Document updated"}


@router.delete("/documents/{doc_id}")
async def delete_document(doc_id: str) -> dict[str, Any]:
    """Delete a document."""
    kb = get_knowledge_base()

    if doc_id not in kb._documents:
        raise HTTPException(status_code=404, detail="Document not found")

    del kb._documents[doc_id]
    if doc_id in kb._embeddings:
        del kb._embeddings[doc_id]

    return {"message": "Document deleted"}


@router.post("/search")
async def search_documents(req: SearchRequest) -> dict[str, Any]:
    """Search documents using semantic search."""
    kb = get_knowledge_base()

    results = await kb.search(req.query, limit=req.limit)

    # Filter by doc_types if specified
    if req.doc_types:
        results = [r for r in results if r.document.doc_type.value in req.doc_types]

    # Filter by tags if specified
    if req.tags:
        results = [r for r in results if any(t in r.document.tags for t in req.tags)]

    return {
        "query": req.query,
        "results": [r.to_dict() for r in results],
        "total": len(results),
    }


@router.get("/tags")
async def list_tags() -> dict[str, Any]:
    """Get all unique tags from documents."""
    kb = get_knowledge_base()

    tags: set[str] = set()
    for doc in kb._documents.values():
        tags.update(doc.tags)

    return {"tags": sorted(tags)}


@router.get("/stats")
async def get_stats() -> dict[str, Any]:
    """Get knowledge base statistics."""
    kb = get_knowledge_base()

    docs = list(kb._documents.values())

    by_type: dict[str, int] = {}
    for doc in docs:
        by_type[doc.doc_type.value] = by_type.get(doc.doc_type.value, 0) + 1

    by_service: dict[str, int] = {}
    for doc in docs:
        if doc.service:
            by_service[doc.service] = by_service.get(doc.service, 0) + 1

    return {
        "total_documents": len(docs),
        "by_type": by_type,
        "by_service": by_service,
    }
