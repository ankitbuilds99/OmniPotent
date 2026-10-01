"""Tenant-scoped durable evidence storage."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.contracts.evidence import EvidenceItem
from app.evidence.models import EvidenceTrustLevel, artifact_hash
from app.evidence.normalizer import EvidenceRedactor
from app.evidence.provenance import EvidenceProvenance, provenance_matches


class EvidenceStoreError(RuntimeError):
    """Base error for evidence persistence failures."""


class EvidenceTenantError(EvidenceStoreError):
    """Evidence is not owned by the active tenant context."""


class DuplicateEvidenceError(EvidenceStoreError):
    """An evidence ID already exists for this tenant."""


class MissingProvenanceError(EvidenceStoreError):
    """Evidence cannot be persisted without valid provenance."""


class EvidenceIntegrityError(EvidenceStoreError):
    """The submitted artifact hash or unverified ingest state is inconsistent."""


class EvidenceStore(Protocol):
    async def put(self, item: EvidenceItem, provenance: EvidenceProvenance) -> EvidenceItem: ...

    async def get(self, evidence_id: UUID, *, incident_id: str) -> EvidenceItem | None: ...

    async def list_for_incident(self, incident_id: str) -> list[EvidenceItem]: ...


class SqlEvidenceStore:
    """SQLAlchemy store; every operation is scoped to a trusted tenant resolver.

    The resolver defaults to the app's captured storage tenant context. Worker
    tasks may inject a resolver that closes over the tenant captured at enqueue
    time; callers cannot pass an arbitrary tenant to individual reads/writes.
    """

    def __init__(
        self,
        tenant_resolver: Callable[[], str] | None = None,
        *,
        redactor: EvidenceRedactor | None = None,
    ) -> None:
        if tenant_resolver is None:
            from app.tenant_context import storage_org_id

            tenant_resolver = storage_org_id
        self._tenant_resolver = tenant_resolver
        self._redactor = redactor or EvidenceRedactor()

    def _tenant(self, requested: str | None = None) -> str:
        active = self._tenant_resolver()
        if not active or active in {"-", "unknown"}:
            raise EvidenceTenantError("active tenant context is required")
        if requested is not None and requested != active:
            raise EvidenceTenantError("cross-tenant evidence access denied")
        return active

    async def put(self, item: EvidenceItem, provenance: EvidenceProvenance) -> EvidenceItem:
        from app.database.engine import get_session
        from app.database.models import EvidenceObservation

        active_tenant = self._tenant(item.tenant_id)
        serialized_evidence = item.model_dump(mode="json")
        if self._redactor.redact(serialized_evidence) != serialized_evidence:
            raise EvidenceIntegrityError("evidence contains credential-like data")
        if item.artifact_hash != artifact_hash(item.payload) or item.trust_level != EvidenceTrustLevel.UNVERIFIED:
            raise EvidenceIntegrityError("evidence hash is invalid or trust was asserted at ingestion")
        if not item.incident_id or not item.artifact_hash or not provenance_matches(item, provenance):
            raise MissingProvenanceError("valid artifact hash and matching provenance are required")
        async with get_session() as session:
            existing = await session.execute(
                select(EvidenceObservation.id).where(
                    EvidenceObservation.org_id == active_tenant,
                    EvidenceObservation.evidence_id == str(item.evidence_id),
                )
            )
            if existing.scalar_one_or_none() is not None:
                raise DuplicateEvidenceError("evidence ID already exists for this tenant")
            session.add(
                EvidenceObservation(
                    org_id=active_tenant,
                    evidence_id=str(item.evidence_id),
                    incident_id=item.incident_id,
                    artifact_hash=item.artifact_hash,
                    evidence=serialized_evidence,
                    provenance=provenance.model_dump(mode="json"),
                )
            )
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise DuplicateEvidenceError("evidence ID already exists for this tenant") from exc
        return item

    async def get(self, evidence_id: UUID, *, incident_id: str) -> EvidenceItem | None:
        from app.database.engine import get_session
        from app.database.models import EvidenceObservation

        active_tenant = self._tenant()
        async with get_session() as session:
            row = (
                await session.execute(
                    select(EvidenceObservation).where(
                        EvidenceObservation.org_id == active_tenant,
                        EvidenceObservation.evidence_id == str(evidence_id),
                        EvidenceObservation.incident_id == incident_id,
                    )
                )
            ).scalar_one_or_none()
            return EvidenceItem.model_validate(row.evidence) if row else None

    async def list_for_incident(self, incident_id: str) -> list[EvidenceItem]:
        from app.database.engine import get_session
        from app.database.models import EvidenceObservation

        active_tenant = self._tenant()
        async with get_session() as session:
            rows = (
                await session.execute(
                    select(EvidenceObservation)
                    .where(
                        EvidenceObservation.org_id == active_tenant,
                        EvidenceObservation.incident_id == incident_id,
                    )
                    .order_by(EvidenceObservation.recorded_at, EvidenceObservation.id)
                )
            ).scalars().all()
            return [EvidenceItem.model_validate(row.evidence) for row in rows]
