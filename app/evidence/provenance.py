"""Deterministic provenance records binding an evidence item to its source."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field, field_validator

from app.contracts.common import DomainContract, require_aware_datetime
from app.contracts.evidence import EvidenceItem


class EvidenceProvenance(DomainContract):
    evidence_id: UUID
    tenant_id: str = Field(min_length=1, max_length=256)
    incident_id: str = Field(min_length=1, max_length=256)
    source: str = Field(min_length=1, max_length=128)
    source_type: str = Field(min_length=1, max_length=128)
    source_identity: str = Field(min_length=1, max_length=512)
    tool_name: str | None = None
    tool_version: str | None = None
    connector_version: str | None = None
    query_or_operation: str = Field(min_length=1, max_length=2048)
    artifact_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    observed_at: datetime
    recorded_at: datetime
    normalizer_version: str = Field(default="2.0.0", min_length=5, max_length=32)
    provenance_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @field_validator("observed_at", "recorded_at")
    @classmethod
    def timestamps_are_aware(cls, value: datetime) -> datetime:
        return require_aware_datetime(value)


def create_provenance(
    item: EvidenceItem,
    *,
    recorded_at: datetime,
    normalizer_version: str = "2.0.0",
) -> EvidenceProvenance:
    """Create a verifiable provenance envelope; missing V2 context fails closed."""
    required = {
        "tenant_id": item.tenant_id,
        "incident_id": item.incident_id,
        "source_type": item.source_type,
        "source_identity": item.source_identity,
        "query_or_operation": item.query_or_operation,
        "artifact_hash": item.artifact_hash,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise ValueError(f"cannot create evidence provenance without: {', '.join(missing)}")
    base = EvidenceProvenance(
        evidence_id=item.evidence_id,
        tenant_id=item.tenant_id or "",
        incident_id=item.incident_id or "",
        source=item.source,
        source_type=item.source_type or "",
        source_identity=item.source_identity or "",
        tool_name=item.tool_name,
        tool_version=item.tool_version,
        connector_version=item.connector_version,
        query_or_operation=item.query_or_operation or "",
        artifact_hash=item.artifact_hash or "",
        evidence_digest=item.canonical_digest,
        observed_at=item.observed_at,
        recorded_at=recorded_at,
        normalizer_version=normalizer_version,
    )
    return base.model_copy(update={"provenance_digest": base.canonical_digest})


def provenance_matches(item: EvidenceItem, provenance: EvidenceProvenance) -> bool:
    """Verify identity binding, source fields, and canonical provenance digest."""
    if provenance.provenance_digest is None:
        return False
    unsigned = provenance.model_copy(update={"provenance_digest": None})
    return (
        provenance.provenance_digest == unsigned.canonical_digest
        and provenance.evidence_id == item.evidence_id
        and provenance.tenant_id == item.tenant_id
        and provenance.incident_id == item.incident_id
        and provenance.artifact_hash == item.artifact_hash
        and provenance.evidence_digest == item.canonical_digest
        and provenance.source_identity == item.source_identity
        and provenance.source_type == item.source_type
    )
