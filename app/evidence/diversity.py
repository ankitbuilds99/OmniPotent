"""Evidence-quality and source-diversity assessment.

This module describes evidence quality only. It has no execution interface and
must not be used as an authorization decision.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from enum import StrEnum
from typing import Iterable
from uuid import UUID

from pydantic import Field, field_validator

from app.contracts.common import DomainContract, RiskLevel, require_aware_datetime
from app.contracts.evidence import EvidenceDomain, EvidenceItem


class EvidenceDiversityReasonCode(StrEnum):
    NO_EVIDENCE = "no_evidence"
    CORRELATED_EVIDENCE = "correlated_evidence"
    INSUFFICIENT_DOMAINS = "insufficient_independent_domains"
    UNCLASSIFIED_DOMAIN = "unclassified_evidence_domain"
    FRESHNESS_REDUCED = "freshness_reduced"
    STALE_EVIDENCE = "stale_evidence"
    FUTURE_OBSERVATION = "future_observation"
    CONTRADICTION_PRESENT = "contradictory_evidence"
    DOMAIN_REQUIREMENT_MET = "domain_requirement_met"


def _default_minimum_domains() -> dict[RiskLevel, int]:
    """Default thresholds from the product policy; callers may override them."""
    return {
        RiskLevel.INFORMATIONAL: 1,
        RiskLevel.LOW: 1,
        RiskLevel.MEDIUM: 2,
        RiskLevel.HIGH: 3,
        RiskLevel.CRITICAL: 3,
    }


class EvidenceDiversityPolicy(DomainContract):
    """Configurable evidence-quality parameters; not an execution policy."""

    minimum_domains_by_risk: dict[RiskLevel, int] = Field(default_factory=_default_minimum_domains)
    correlation_window_seconds: float = Field(gt=0)
    freshness_horizon_seconds: float = Field(gt=0)

    @field_validator("minimum_domains_by_risk")
    @classmethod
    def domain_requirements_are_positive(cls, value: dict[RiskLevel, int]) -> dict[RiskLevel, int]:
        if not value or any(count < 1 for count in value.values()):
            raise ValueError("each configured risk threshold must require at least one domain")
        return value

    def minimum_domains(self, risk: RiskLevel) -> int:
        try:
            return self.minimum_domains_by_risk[risk]
        except KeyError as exc:
            raise ValueError(f"no evidence diversity threshold configured for risk={risk.value}") from exc


class EvidenceContradiction(DomainContract):
    hypothesis_id: UUID
    supporting_evidence_ids: tuple[UUID, ...]
    contradicting_evidence_ids: tuple[UUID, ...]


class EvidenceDiversityAssessment(DomainContract):
    unique_sources: tuple[str, ...]
    unique_domains: tuple[EvidenceDomain, ...]
    independent_domain_count: int = Field(ge=0)
    duplicate_ratio: float = Field(ge=0, le=1)
    freshness_score: float = Field(ge=0, le=1)
    coverage_score: float = Field(ge=0, le=1)
    contradictions: tuple[EvidenceContradiction, ...]
    quality_score: float = Field(ge=0, le=1)
    reason_codes: tuple[EvidenceDiversityReasonCode, ...]
    minimum_required_domains: int = Field(ge=1)
    assessed_at: datetime

    @field_validator("assessed_at")
    @classmethod
    def assessed_time_is_aware(cls, value: datetime) -> datetime:
        return require_aware_datetime(value)


class EvidenceDiversity(DomainContract):
    """Legacy summary preserved for callers of ``measure_diversity``."""

    item_count: int = Field(ge=0)
    unique_artifact_count: int = Field(ge=0)
    independent_source_count: int = Field(ge=0)
    source_types: tuple[str, ...]
    source_identities: tuple[str, ...]


def measure_diversity(items: tuple[EvidenceItem, ...] | list[EvidenceItem]) -> EvidenceDiversity:
    """Backward-compatible summary that counts sources and unique artifacts."""
    unique_artifacts = {item.artifact_hash or item.digest or str(item.evidence_id) for item in items}
    sources = {item.source_identity or item.source for item in items}
    source_types = {item.source_type for item in items if item.source_type}
    source_identities = {item.source_identity for item in items if item.source_identity}
    return EvidenceDiversity(
        item_count=len(items),
        unique_artifact_count=len(unique_artifacts),
        independent_source_count=len(sources),
        source_types=tuple(sorted(source_types)),
        source_identities=tuple(sorted(source_identities)),
    )


def _time_bounds(item: EvidenceItem) -> tuple[datetime, datetime]:
    start = item.observation_window_start or item.observed_at
    end = item.observation_window_end or item.observed_at
    return (start, end) if start <= end else (end, start)


def _time_distance_seconds(left: EvidenceItem, right: EvidenceItem) -> float:
    left_start, left_end = _time_bounds(left)
    right_start, right_end = _time_bounds(right)
    if left_start <= right_end and right_start <= left_end:
        return 0.0
    if left_end < right_start:
        return (right_start - left_end).total_seconds()
    return (left_start - right_end).total_seconds()


def _entity_key(item: EvidenceItem) -> tuple[str, str] | None:
    if item.entity_id:
        return (item.entity_type or "", item.entity_id)
    if item.service:
        return ("service", item.service)
    return None


def _hypothesis_ids(item: EvidenceItem) -> frozenset[UUID]:
    return frozenset((*item.supports_hypothesis_ids, *item.contradicts_hypothesis_ids))


def _hypotheses_compatible(left: EvidenceItem, right: EvidenceItem) -> bool:
    left_ids = _hypothesis_ids(left)
    right_ids = _hypothesis_ids(right)
    return not left_ids or not right_ids or bool(left_ids.intersection(right_ids))


def _correlated(left: EvidenceItem, right: EvidenceItem, window_seconds: float) -> bool:
    left_hash = left.artifact_hash or left.digest
    right_hash = right.artifact_hash or right.digest
    if left_hash and left_hash == right_hash:
        return True

    left_source = left.source_identity or left.source
    right_source = right.source_identity or right.source
    left_entity = _entity_key(left)
    right_entity = _entity_key(right)
    return (
        left.domain is not None
        and left.domain == right.domain
        and left_source == right_source
        and left_entity is not None
        and left_entity == right_entity
        and _time_distance_seconds(left, right) <= window_seconds
        and _hypotheses_compatible(left, right)
    )


def _correlation_groups(items: list[EvidenceItem], window_seconds: float) -> list[list[EvidenceItem]]:
    """Connected components over duplicate/correlation links between observations."""
    parents = list(range(len(items)))

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left: int, right: int) -> None:
        root_left, root_right = find(left), find(right)
        if root_left != root_right:
            parents[root_right] = root_left

    artifact_owners: dict[str, int] = {}
    for index, item in enumerate(items):
        digest = item.artifact_hash or item.digest
        if digest:
            owner = artifact_owners.setdefault(digest, index)
            union(owner, index)

    # Correlation only needs pairwise checks within the same source/domain/entity
    # bucket. The configured window and hypothesis overlap then determine the link.
    buckets: dict[tuple[str, EvidenceDomain, tuple[str, str]], list[int]] = defaultdict(list)
    for index, item in enumerate(items):
        entity = _entity_key(item)
        if entity is not None and item.domain is not None:
            buckets[(item.source_identity or item.source, item.domain, entity)].append(index)
    for indexes in buckets.values():
        indexes.sort(key=lambda index: _time_bounds(items[index])[0])
        for position, current_index in enumerate(indexes):
            for prior_index in indexes[:position]:
                if _time_distance_seconds(items[prior_index], items[current_index]) > window_seconds:
                    continue
                if _hypotheses_compatible(items[prior_index], items[current_index]):
                    union(prior_index, current_index)

    grouped: dict[int, list[EvidenceItem]] = defaultdict(list)
    for index, item in enumerate(items):
        grouped[find(index)].append(item)
    return list(grouped.values())


def _contradictions(items: list[EvidenceItem]) -> tuple[EvidenceContradiction, ...]:
    supports: dict[UUID, set[UUID]] = defaultdict(set)
    contradicts: dict[UUID, set[UUID]] = defaultdict(set)
    for item in items:
        for hypothesis_id in item.supports_hypothesis_ids:
            supports[hypothesis_id].add(item.evidence_id)
        for hypothesis_id in item.contradicts_hypothesis_ids:
            contradicts[hypothesis_id].add(item.evidence_id)
    return tuple(
        EvidenceContradiction(
            hypothesis_id=hypothesis_id,
            supporting_evidence_ids=tuple(sorted(supports[hypothesis_id], key=str)),
            contradicting_evidence_ids=tuple(sorted(contradicts[hypothesis_id], key=str)),
        )
        for hypothesis_id in sorted(supports.keys() & contradicts.keys(), key=str)
    )


def assess_evidence_diversity(
    items: Iterable[EvidenceItem],
    *,
    risk: RiskLevel,
    assessed_at: datetime,
    policy: EvidenceDiversityPolicy,
) -> EvidenceDiversityAssessment:
    """Assess correlated evidence, domain breadth, freshness, and contradictions.

    This function reports evidence quality only. A sufficient domain count is
    not a trust decision and must not authorize execution.
    """
    if assessed_at.tzinfo is None or assessed_at.utcoffset() is None:
        raise ValueError("assessed_at must be timezone-aware")
    evidence = list(items)
    groups = _correlation_groups(evidence, policy.correlation_window_seconds)
    unique_sources = tuple(sorted({item.source_identity or item.source for item in evidence}))
    unique_domains = tuple(sorted({item.domain for item in evidence if item.domain is not None}, key=lambda domain: domain.value))
    # A correlation component can carry many labels but contributes one
    # independent observation; repeated items from one domain still contribute
    # just one domain regardless of how many messages appeared.
    independent_domain_count = min(len(unique_domains), len(groups))
    duplicate_ratio = (len(evidence) - len(groups)) / len(evidence) if evidence else 0.0
    minimum_domains = policy.minimum_domains(risk)
    coverage_score = min(independent_domain_count / minimum_domains, 1.0)

    freshness_by_group: list[float] = []
    future_observation = False
    stale_observation = False
    for group in groups:
        item_scores: list[float] = []
        for item in group:
            age_seconds = (assessed_at - item.observed_at).total_seconds()
            future_observation = future_observation or age_seconds < 0
            stale_observation = stale_observation or age_seconds >= policy.freshness_horizon_seconds
            item_scores.append(
                max(0.0, min(1.0, 1.0 - age_seconds / policy.freshness_horizon_seconds))
            )
        # Repeated correlated samples contribute one freshness observation. Use
        # the group's newest sample rather than letting duplicate count weight it.
        freshness_by_group.append(max(item_scores))
    freshness_score = sum(freshness_by_group) / len(freshness_by_group) if freshness_by_group else 0.0

    contradictions = _contradictions(evidence)
    quality_score = (
        freshness_score * coverage_score * (1.0 - duplicate_ratio) / (1.0 + len(contradictions))
        if evidence
        else 0.0
    )
    reasons: list[EvidenceDiversityReasonCode] = []
    if not evidence:
        reasons.append(EvidenceDiversityReasonCode.NO_EVIDENCE)
    if duplicate_ratio > 0:
        reasons.append(EvidenceDiversityReasonCode.CORRELATED_EVIDENCE)
    if independent_domain_count < minimum_domains:
        reasons.append(EvidenceDiversityReasonCode.INSUFFICIENT_DOMAINS)
    if any(item.domain is None for item in evidence):
        reasons.append(EvidenceDiversityReasonCode.UNCLASSIFIED_DOMAIN)
    if freshness_score < 1.0:
        reasons.append(EvidenceDiversityReasonCode.FRESHNESS_REDUCED)
    if stale_observation:
        reasons.append(EvidenceDiversityReasonCode.STALE_EVIDENCE)
    if future_observation:
        reasons.append(EvidenceDiversityReasonCode.FUTURE_OBSERVATION)
    if contradictions:
        reasons.append(EvidenceDiversityReasonCode.CONTRADICTION_PRESENT)
    if evidence and independent_domain_count >= minimum_domains:
        reasons.append(EvidenceDiversityReasonCode.DOMAIN_REQUIREMENT_MET)

    return EvidenceDiversityAssessment(
        unique_sources=unique_sources,
        unique_domains=unique_domains,
        independent_domain_count=independent_domain_count,
        duplicate_ratio=duplicate_ratio,
        freshness_score=freshness_score,
        coverage_score=coverage_score,
        contradictions=contradictions,
        quality_score=max(0.0, min(1.0, quality_score)),
        reason_codes=tuple(reasons),
        minimum_required_domains=minimum_domains,
        assessed_at=assessed_at,
    )
