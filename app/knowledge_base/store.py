"""Durable knowledge base storage.

Every document lived in `KnowledgeBase._documents`, a dict on a per-org object
held in process memory. Runbook write-ups, postmortems and architecture notes
are the text customers put the most work into, and a restart deleted all of
them; with more than one worker, whether a document existed depended on which
process answered the request. The API router made it worse by reaching into
`_documents` directly from six of its eight handlers.

Documents live in `knowledge_documents` now, filtered by `org_uuid()`. The
built-ins are seeded per org on first read and then owned by that org, so
deleting one makes it stay deleted.

`KnowledgeBase` is still the search engine: `search()` loads this org's rows and
ranks them with BM25, which needs corpus-wide term statistics rather than a
per-row vector, so there is nothing worth persisting — see the note on
`KnowledgeDocument`.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select

from app.database.orgs import ensure_org_row, org_uuid
from app.knowledge_base.rag import Document, DocumentType, KnowledgeBase, SearchResult
from app.knowledge_base.seeds import DEFAULT_DOCUMENTS

# Marker on the org's settings JSON. Seeding on "this org has no documents yet"
# would resurrect the built-ins every time an org deleted the last one.
_SEEDED_KEY = "knowledge_seeded"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(value: datetime) -> datetime:
    """Read a timestamp back as UTC-aware.

    SQLite has no timezone type, so a `DateTime(timezone=True)` column round
    trips to a naive datetime and `Document.to_dict()` would emit an ISO string
    with no offset for the frontend to guess at.
    """
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def new_document_id(title: str, content: str) -> str:
    """The id an unnamed document gets — same rule as `KnowledgeBase._generate_id`."""
    return hashlib.md5((title + content).encode(), usedforsecurity=False).hexdigest()[:12]


def _to_document(row: Any) -> Document:
    return Document(
        id=row.doc_id,
        title=row.title,
        content=row.content,
        doc_type=DocumentType(row.doc_type),
        tags=list(row.tags or []),
        service=row.service,
        created_at=_utc(row.created_at),
        updated_at=_utc(row.updated_at),
        author=row.author,
        metadata=dict(row.meta or {}),
    )


def _new_row(org: uuid.UUID, doc: Document) -> Any:
    from app.database.models import KnowledgeDocument

    now = _now()
    return KnowledgeDocument(
        id=uuid.uuid4(),
        org_id=org,
        doc_id=doc.id,
        title=doc.title,
        content=doc.content,
        doc_type=doc.doc_type.value,
        tags=list(doc.tags),
        service=doc.service,
        author=doc.author,
        meta=dict(doc.metadata),
        created_at=now,
        updated_at=now,
    )


async def _ensure_seeded(session: Any) -> uuid.UUID:
    """Give this org its own copy of the built-in documents, once."""
    from app.database.models import Organization

    org = await ensure_org_row(session)
    row = (
        await session.execute(select(Organization).where(Organization.id == org))
    ).scalar_one_or_none()
    if row is None or (row.settings or {}).get(_SEEDED_KEY):
        return org

    for doc in DEFAULT_DOCUMENTS:
        session.add(_new_row(org, doc))
    # Rebound, not mutated in place: SQLAlchemy does not track edits inside a
    # plain JSON value, so `row.settings[key] = ...` would never be flushed.
    row.settings = {**(row.settings or {}), _SEEDED_KEY: True}
    await session.commit()
    return org


def _select_for_org(doc_type: str | None = None, service: str | None = None):
    from app.database.models import KnowledgeDocument

    stmt = select(KnowledgeDocument).where(KnowledgeDocument.org_id == org_uuid())
    if doc_type:
        stmt = stmt.where(KnowledgeDocument.doc_type == doc_type)
    if service:
        stmt = stmt.where(KnowledgeDocument.service == service)
    # `doc_id` breaks ties: several documents are created in the same second and
    # the caller pages through this list, so the order has to be total.
    return stmt.order_by(KnowledgeDocument.updated_at.desc(), KnowledgeDocument.doc_id)


async def list_documents(
    doc_type: str | None = None,
    tag: str | None = None,
    service: str | None = None,
    search_text: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Document], int]:
    """One page of this org's documents, plus the total the filters matched."""
    from app.database.engine import get_session
    from app.database.models import KnowledgeDocument

    async with get_session() as session:
        await _ensure_seeded(session)
        stmt = _select_for_org(doc_type, service)
        if search_text:
            needle = search_text.lower()
            stmt = stmt.where(
                func.lower(KnowledgeDocument.title).contains(needle, autoescape=True)
                | func.lower(KnowledgeDocument.content).contains(needle, autoescape=True)
            )
        rows = (await session.execute(stmt)).scalars().all()

    docs = [_to_document(row) for row in rows]
    # `tags` is a JSON array; containment is not portable across SQLite and
    # Postgres, so this one filter runs here. A single org's knowledge base is
    # small enough that the extra rows are cheap.
    if tag:
        docs = [d for d in docs if tag in d.tags]

    total = len(docs)
    return docs[offset : offset + limit], total


async def get_document(doc_id: str) -> Document | None:
    from app.database.engine import get_session
    from app.database.models import KnowledgeDocument

    async with get_session() as session:
        await _ensure_seeded(session)
        row = (
            await session.execute(
                select(KnowledgeDocument).where(
                    KnowledgeDocument.org_id == org_uuid(),
                    KnowledgeDocument.doc_id == doc_id,
                )
            )
        ).scalar_one_or_none()
        return _to_document(row) if row else None


async def create_document(doc: Document) -> str:
    """Store `doc`, giving it an id if it has none. Returns the id."""
    from app.database.engine import get_session
    from app.database.models import KnowledgeDocument

    if not doc.id:
        doc.id = new_document_id(doc.title, doc.content)

    async with get_session() as session:
        org = await _ensure_seeded(session)
        existing = (
            await session.execute(
                select(KnowledgeDocument).where(
                    KnowledgeDocument.org_id == org,
                    KnowledgeDocument.doc_id == doc.id,
                )
            )
        ).scalar_one_or_none()

        if existing is None:
            session.add(_new_row(org, doc))
        else:
            # Ids are derived from title+content, so re-posting the same
            # document collides with itself. The dict this replaced overwrote
            # silently; a unique-constraint violation here would be a 500.
            existing.title = doc.title
            existing.content = doc.content
            existing.doc_type = doc.doc_type.value
            existing.tags = list(doc.tags)
            existing.service = doc.service
            existing.author = doc.author
            existing.meta = dict(doc.metadata)
            existing.updated_at = _now()

        await session.commit()
    return doc.id


async def update_document(
    doc_id: str,
    *,
    title: str | None = None,
    content: str | None = None,
    tags: list[str] | None = None,
    service: str | None = None,
) -> bool:
    """Apply the fields that were supplied. False if this org has no such document."""
    from app.database.engine import get_session
    from app.database.models import KnowledgeDocument

    async with get_session() as session:
        org = await _ensure_seeded(session)
        row = (
            await session.execute(
                select(KnowledgeDocument).where(
                    KnowledgeDocument.org_id == org,
                    KnowledgeDocument.doc_id == doc_id,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return False

        if title is not None:
            row.title = title
        if content is not None:
            row.content = content
        if tags is not None:
            row.tags = list(tags)
        if service is not None:
            row.service = service
        row.updated_at = _now()

        await session.commit()
        return True


async def delete_document(doc_id: str) -> bool:
    from app.database.engine import get_session
    from app.database.models import KnowledgeDocument

    async with get_session() as session:
        org = await _ensure_seeded(session)
        row = (
            await session.execute(
                select(KnowledgeDocument).where(
                    KnowledgeDocument.org_id == org,
                    KnowledgeDocument.doc_id == doc_id,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return False
        await session.delete(row)
        await session.commit()
        return True


async def search(query: str, limit: int = 10) -> list[SearchResult]:
    """Rank this org's documents against `query`."""
    from app.database.engine import get_session

    async with get_session() as session:
        await _ensure_seeded(session)
        rows = (await session.execute(_select_for_org())).scalars().all()

    index = KnowledgeBase()
    for row in rows:
        index.add_document(_to_document(row))
    return await index.search(query, limit=limit)


async def list_tags() -> list[str]:
    from app.database.engine import get_session

    async with get_session() as session:
        await _ensure_seeded(session)
        rows = (await session.execute(_select_for_org())).scalars().all()

    tags: set[str] = set()
    for row in rows:
        tags.update(row.tags or [])
    return sorted(tags)


async def stats() -> dict[str, Any]:
    from app.database.engine import get_session

    async with get_session() as session:
        await _ensure_seeded(session)
        rows = (await session.execute(_select_for_org())).scalars().all()

    by_type: dict[str, int] = {}
    by_service: dict[str, int] = {}
    for row in rows:
        by_type[row.doc_type] = by_type.get(row.doc_type, 0) + 1
        if row.service:
            by_service[row.service] = by_service.get(row.service, 0) + 1

    return {
        "total_documents": len(rows),
        "by_type": by_type,
        "by_service": by_service,
    }
