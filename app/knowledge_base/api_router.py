"""Knowledge Base API Router.

REST API for knowledge base CRUD operations and search. Every handler goes
through `app.knowledge_base.store`, which keeps documents in the
`knowledge_documents` table — these used to read and write a per-process dict
directly, so a restart deleted the customer's knowledge base.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.knowledge_base import store
from app.knowledge_base.rag import Document, DocumentType

log = logging.getLogger("omnipotent.knowledge_base.api")

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


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
    docs, total = await store.list_documents(
        doc_type=doc_type,
        tag=tag,
        service=service,
        search_text=search,
        limit=limit,
        offset=offset,
    )

    return {
        "documents": [d.to_dict() for d in docs],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/documents/{doc_id}")
async def get_document(doc_id: str) -> dict[str, Any]:
    """Get a specific document by ID."""
    doc = await store.get_document(doc_id)
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
    try:
        doc_type = DocumentType(req.doc_type)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid doc_type: {req.doc_type}")

    doc_id = await store.create_document(
        Document(
            id="",
            title=req.title,
            content=req.content,
            doc_type=doc_type,
            tags=req.tags,
            service=req.service,
            author=req.author,
        )
    )

    return {"id": doc_id, "message": "Document created"}


@router.put("/documents/{doc_id}")
async def update_document(doc_id: str, req: UpdateDocumentRequest) -> dict[str, Any]:
    """Update an existing document."""
    updated = await store.update_document(
        doc_id,
        title=req.title,
        content=req.content,
        tags=req.tags,
        service=req.service,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Document not found")

    return {"message": "Document updated"}


@router.delete("/documents/{doc_id}")
async def delete_document(doc_id: str) -> dict[str, Any]:
    """Delete a document."""
    if not await store.delete_document(doc_id):
        raise HTTPException(status_code=404, detail="Document not found")

    return {"message": "Document deleted"}


@router.post("/search")
async def search_documents(req: SearchRequest) -> dict[str, Any]:
    """Search documents using semantic search."""
    results = await store.search(req.query, limit=req.limit)

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
    return {"tags": await store.list_tags()}


@router.get("/stats")
async def get_stats() -> dict[str, Any]:
    """Get knowledge base statistics."""
    return await store.stats()
