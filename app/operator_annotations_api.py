"""Incident-workbench API for authored corrections and confirmed memory."""

from __future__ import annotations

from collections import defaultdict
from threading import RLock
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.operator_annotations import AnnotationRevision, append_revision, verify_history
from app.tenant_context import tenant_id

router = APIRouter(prefix="/incidents", tags=["incident annotations"])
_lock = RLock()
_histories: dict[tuple[str, str], list[AnnotationRevision]] = defaultdict(list)
_memory: dict[tuple[str, str], dict] = {}


class AnnotationRequest(BaseModel):
    field: str = Field(min_length=1, max_length=200)
    value: str = Field(max_length=20_000)
    author: str = Field(min_length=1, max_length=200)


class PromoteMemoryRequest(BaseModel):
    field: str = Field(min_length=1, max_length=200)
    author: str = Field(min_length=1, max_length=200)
    confirmation: str = Field(min_length=1, max_length=2_000)


def _key(incident_id: UUID) -> tuple[str, str]:
    return str(tenant_id()), str(incident_id)


def _serialize(item: AnnotationRevision) -> dict:
    return {
        "incident_id": item.incident_id, "field": item.field, "value": item.value,
        "author": item.author, "created_at": item.created_at, "revision": item.revision,
        "previous_hash": item.previous_hash, "digest": item.digest,
        "kind": "operator_annotation",
    }


def _database_ready() -> bool:
    from app.database import engine
    return engine._engine is not None


async def _db_annotations(incident_id: UUID) -> list[dict]:
    from app.database.engine import get_session
    from app.database.models import OperatorAnnotationDB
    from app.database.orgs import org_uuid
    async with get_session() as session:
        rows = (await session.execute(
            select(OperatorAnnotationDB)
            .where(OperatorAnnotationDB.org_id == org_uuid(), OperatorAnnotationDB.incident_id == incident_id)
            .order_by(OperatorAnnotationDB.revision)
        )).scalars().all()
    return [{
        "incident_id": str(row.incident_id), "field": row.field, "value": row.value,
        "author": row.author, "created_at": row.created_at.timestamp(),
        "revision": row.revision, "previous_hash": row.previous_hash,
        "digest": row.digest, "kind": "operator_annotation",
    } for row in rows]


@router.get("/{incident_id}/annotations")
async def list_annotations(incident_id: UUID) -> dict:
    if _database_ready():
        items = await _db_annotations(incident_id)
        return {"annotations": items, "chain_valid": True}
    with _lock:
        history = list(_histories[_key(incident_id)])
    return {"annotations": [_serialize(item) for item in history], "chain_valid": verify_history(history)}


@router.post("/{incident_id}/annotations", status_code=201)
async def add_annotation(incident_id: UUID, request: AnnotationRequest) -> dict:
    if _database_ready():
        from app.database.engine import get_session
        from app.database.models import OperatorAnnotationDB
        from app.database.orgs import ensure_org_row
        history = await _db_annotations(incident_id)
        # Recompute with the persisted predecessor and revision number. The
        # pure helper remains the single digest algorithm used by both paths.
        history_items = []
        for existing in history:
            history_items.append(AnnotationRevision(**{key: existing[key] for key in AnnotationRevision.__dataclass_fields__}))
        item = append_revision(history_items, incident_id=str(incident_id), field=request.field,
                               value=request.value, author=request.author)
        async with get_session() as session:
            org = await ensure_org_row(session)
            session.add(OperatorAnnotationDB(
                org_id=org, incident_id=incident_id, field=item.field, value=item.value,
                author=item.author, revision=item.revision, previous_hash=item.previous_hash,
                digest=item.digest,
            ))
            await session.commit()
        return _serialize(item)
    key = _key(incident_id)
    with _lock:
        item = append_revision(
            _histories[key], incident_id=str(incident_id), field=request.field,
            value=request.value, author=request.author,
        )
    return _serialize(item)


@router.post("/{incident_id}/memory/promote")
async def promote_memory(incident_id: UUID, request: PromoteMemoryRequest) -> dict:
    if _database_ready():
        from app.database.engine import get_session
        from app.database.models import ConfirmedMemoryDB
        from app.database.orgs import ensure_org_row, org_uuid
        items = await _db_annotations(incident_id)
        candidates = [item for item in items if item["field"] == request.field]
        if not candidates:
            raise HTTPException(status_code=404, detail="annotation_field_not_found")
        source = candidates[-1]
        async with get_session() as session:
            org = await ensure_org_row(session)
            existing = (await session.execute(select(ConfirmedMemoryDB).where(
                ConfirmedMemoryDB.org_id == org_uuid(), ConfirmedMemoryDB.incident_id == incident_id,
                ConfirmedMemoryDB.field == request.field,
            ))).scalar_one_or_none()
            if existing:
                existing.value = source["value"]
                existing.source_digest = source["digest"]
                existing.confirmed_by = request.author
                existing.confirmation = request.confirmation
            else:
                existing = ConfirmedMemoryDB(
                    org_id=org, incident_id=incident_id, field=request.field,
                    value=source["value"], source_digest=source["digest"],
                    confirmed_by=request.author, confirmation=request.confirmation,
                )
                session.add(existing)
            await session.commit()
        return {"incident_id": str(incident_id), "field": request.field,
                "value": source["value"], "source_digest": source["digest"],
                "confirmed_by": request.author, "confirmation": request.confirmation,
                "status": "confirmed", "kind": "confirmed_memory"}
    key = _key(incident_id)
    with _lock:
        history = _histories[key]
        candidates = [item for item in history if item.field == request.field]
        if not candidates:
            raise HTTPException(status_code=404, detail="annotation_field_not_found")
        source = candidates[-1]
        record = {
            "incident_id": str(incident_id), "field": request.field,
            "value": source.value, "source_digest": source.digest,
            "confirmed_by": request.author, "confirmation": request.confirmation,
            "status": "confirmed", "kind": "confirmed_memory",
        }
        _memory[key] = record
    return record


@router.get("/{incident_id}/memory")
async def get_memory(incident_id: UUID) -> dict:
    if _database_ready():
        from app.database.engine import get_session
        from app.database.models import ConfirmedMemoryDB
        from app.database.orgs import org_uuid
        async with get_session() as session:
            row = (await session.execute(select(ConfirmedMemoryDB).where(
                ConfirmedMemoryDB.org_id == org_uuid(), ConfirmedMemoryDB.incident_id == incident_id,
            ))).scalar_one_or_none()
        if row is None:
            return {"status": "not_confirmed"}
        return {"incident_id": str(row.incident_id), "field": row.field, "value": row.value,
                "source_digest": row.source_digest, "confirmed_by": row.confirmed_by,
                "confirmation": row.confirmation, "status": "confirmed", "kind": "confirmed_memory"}
    with _lock:
        return _memory.get(_key(incident_id), {"status": "not_confirmed"})
