"""Structured evidence ingestion and tenant-scoped persistence."""

from app.contracts.evidence import EvidenceDomain, EvidenceItem, EvidenceReference
from app.evidence.diversity import (
    EvidenceDiversityAssessment,
    EvidenceDiversityPolicy,
    assess_evidence_diversity,
)
from app.evidence.normalizer import EvidenceContext, EvidenceNormalizer, EvidenceSource
from app.evidence.provenance import EvidenceProvenance, create_provenance
from app.evidence.store import EvidenceStore, SqlEvidenceStore

__all__ = [
    "EvidenceContext",
    "EvidenceDomain",
    "EvidenceDiversityAssessment",
    "EvidenceDiversityPolicy",
    "EvidenceItem",
    "EvidenceNormalizer",
    "EvidenceProvenance",
    "EvidenceReference",
    "EvidenceSource",
    "EvidenceStore",
    "SqlEvidenceStore",
    "create_provenance",
    "assess_evidence_diversity",
]
