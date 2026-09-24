"""Evidence-gap-driven active diagnosis, separate from remediation execution.

The existing InvestigatorAgent/LangGraph remains responsible for proposing
hypotheses and presenting results. This orchestrator accepts that proposer and
trusted diagnostic tool adapters by interface, so test/replay and production
connectors use the same deterministic selection and evidence handling.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.contracts.common import DomainContract
from app.contracts.evidence import EvidenceItem
from app.contracts.hypothesis import Hypothesis, HypothesisStatus


class DiagnosisStatus(StrEnum):
    ROOT_CAUSE_IDENTIFIED = "root_cause_identified"
    AMBIGUOUS = "ambiguous"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    UNKNOWN_INCIDENT = "unknown_incident"


class EvidenceGap(DomainContract):
    hypothesis_id: UUID
    evidence_kind: str = Field(min_length=1, max_length=256)
    diagnostic_question: str = Field(min_length=1, max_length=2000)


class DiagnosticToolSpec(DomainContract):
    """Registered read-only capability and its current selection attributes."""

    tool_id: str = Field(min_length=1, max_length=512)
    evidence_kinds: tuple[str, ...] = Field(min_length=1)
    information_value: float = Field(ge=0, le=1)
    latency_seconds: float = Field(ge=0)
    cost: float = Field(ge=0)
    risk: float = Field(ge=0, le=1)
    availability: float = Field(ge=0, le=1)
    freshness_seconds: float = Field(ge=0)
    mutates: Literal[False] = False


class EvidenceGapPlannerConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    information_value_weight: float = Field(ge=0)
    latency_weight: float = Field(ge=0)
    cost_weight: float = Field(ge=0)
    risk_weight: float = Field(ge=0)
    availability_weight: float = Field(ge=0)
    freshness_weight: float = Field(ge=0)
    maximum_latency_seconds: float = Field(gt=0)
    maximum_cost: float = Field(gt=0)
    maximum_risk: float = Field(ge=0, le=1)
    maximum_evidence_age_seconds: float = Field(gt=0)
    minimum_supporting_evidence: int = Field(ge=1)
    minimum_confidence: float = Field(ge=0, le=1)
    minimum_lead_over_alternative: float = Field(ge=0, le=1)
    max_diagnostic_rounds: int = Field(ge=0)


class DiagnosisIncident(DomainContract):
    incident_id: str = Field(min_length=1, max_length=256)
    tenant_id: str = Field(min_length=1, max_length=256)
    description: str = Field(min_length=1, max_length=8000)
    affected_entities: tuple[str, ...] = ()


class DiagnosisResult(DomainContract):
    incident_id: str
    tenant_id: str
    status: DiagnosisStatus
    hypotheses: tuple[Hypothesis, ...] = ()
    evidence: tuple[EvidenceItem, ...] = ()
    selected_hypothesis_id: UUID | None = None
    unresolved_gaps: tuple[EvidenceGap, ...] = ()
    diagnostic_rounds: int = Field(ge=0)
    reason_codes: tuple[str, ...] = ()


class HypothesisProposer(Protocol):
    async def propose(
        self, incident: DiagnosisIncident, evidence: Sequence[EvidenceItem]
    ) -> Sequence[Hypothesis]: ...


class DiagnosticEvidenceTool(Protocol):
    @property
    def spec(self) -> DiagnosticToolSpec: ...

    async def gather(self, incident: DiagnosisIncident, gap: EvidenceGap) -> Sequence[EvidenceItem]: ...


@dataclass(frozen=True)
class EvidenceGapPlanner:
    """Select the best eligible tool for the next explicit evidence gap."""

    config: EvidenceGapPlannerConfig

    def plan(
        self,
        hypotheses: Sequence[Hypothesis],
        tools: Sequence[DiagnosticEvidenceTool],
        attempted: frozenset[tuple[UUID, str, str]],
    ) -> tuple[DiagnosticEvidenceTool, EvidenceGap] | None:
        choices: list[tuple[float, str, str, DiagnosticEvidenceTool, EvidenceGap]] = []
        for hypothesis in hypotheses:
            if hypothesis.status in {HypothesisStatus.REFUTED, HypothesisStatus.CONTRADICTED}:
                continue
            for kind in hypothesis.missing_evidence:
                gap = EvidenceGap(
                    hypothesis_id=hypothesis.hypothesis_id,
                    evidence_kind=kind,
                    diagnostic_question=f"Gather {kind} evidence relevant to: {hypothesis.claim}",
                )
                for tool in tools:
                    spec = tool.spec
                    if kind not in spec.evidence_kinds:
                        continue
                    attempt_key = (hypothesis.hypothesis_id, kind, spec.tool_id)
                    if attempt_key in attempted:
                        continue
                    if (
                        spec.availability <= 0
                        or spec.latency_seconds > self.config.maximum_latency_seconds
                        or spec.cost > self.config.maximum_cost
                        or spec.risk > self.config.maximum_risk
                    ):
                        continue
                    score = self._score(spec)
                    choices.append((score, spec.tool_id, str(hypothesis.hypothesis_id), tool, gap))
        if not choices:
            return None
        _, _, _, tool, gap = max(choices, key=lambda choice: (choice[0], choice[1], choice[2]))
        return tool, gap

    def _score(self, spec: DiagnosticToolSpec) -> float:
        c = self.config
        return (
            c.information_value_weight * spec.information_value
            - c.latency_weight * spec.latency_seconds / c.maximum_latency_seconds
            - c.cost_weight * spec.cost / c.maximum_cost
            - c.risk_weight * spec.risk
            + c.availability_weight * spec.availability
            + c.freshness_weight / (1 + spec.freshness_seconds)
        )


class ActiveDiagnosisEngine:
    """Iteratively gather only evidence needed to distinguish hypotheses."""

    def __init__(
        self,
        *,
        incident_resolver: Callable[[str, str], Awaitable[DiagnosisIncident | None]],
        proposer: HypothesisProposer,
        tools: Sequence[DiagnosticEvidenceTool],
        planner: EvidenceGapPlanner,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._incident_resolver = incident_resolver
        self._proposer = proposer
        self._tools = tuple(tools)
        self._planner = planner
        self._now = now

    async def diagnose(self, incident_id: str, tenant_id: str) -> DiagnosisResult:
        incident = await self._incident_resolver(incident_id, tenant_id)
        if incident is None:
            return DiagnosisResult(
                incident_id=incident_id,
                tenant_id=tenant_id,
                status=DiagnosisStatus.UNKNOWN_INCIDENT,
                diagnostic_rounds=0,
                reason_codes=("unknown_incident",),
            )
        if incident.incident_id != incident_id or incident.tenant_id != tenant_id:
            return DiagnosisResult(
                incident_id=incident_id,
                tenant_id=tenant_id,
                status=DiagnosisStatus.UNKNOWN_INCIDENT,
                diagnostic_rounds=0,
                reason_codes=("incident_scope_mismatch",),
            )

        evidence: list[EvidenceItem] = []
        hypotheses = list(await self._proposer.propose(incident, evidence))
        if any(h.tenant_id not in (None, tenant_id) or h.incident_id not in (None, incident_id) for h in hypotheses):
            raise ValueError("hypothesis scope does not match diagnosis incident")
        hypotheses = [
            h.model_copy(update={"tenant_id": tenant_id, "incident_id": incident_id})
            for h in hypotheses
        ]
        attempted: set[tuple[UUID, str, str]] = set()
        reasons: list[str] = []
        rounds = 0
        while rounds < self._planner.config.max_diagnostic_rounds:
            hypotheses = self._update_hypotheses(hypotheses, evidence)
            selected = self._select_winner(hypotheses)
            if selected is not None:
                return self._result(incident, DiagnosisStatus.ROOT_CAUSE_IDENTIFIED, hypotheses, evidence, selected, (), rounds, reasons)
            plan = self._planner.plan(hypotheses, self._tools, frozenset(attempted))
            if plan is None:
                break
            tool, gap = plan
            key = (gap.hypothesis_id, gap.evidence_kind, tool.spec.tool_id)
            attempted.add(key)
            rounds += 1
            try:
                gathered = await tool.gather(incident, gap)
            except Exception:
                reasons.append("diagnostic_tool_failed")
                self._mark_gap_attempted(hypotheses, gap)
                continue
            for item in gathered:
                if not isinstance(item, EvidenceItem):
                    reasons.append("diagnostic_evidence_invalid")
                    continue
                if item.tenant_id != tenant_id or item.incident_id != incident_id:
                    reasons.append("evidence_scope_mismatch")
                    continue
                evidence.append(item)
            # Revisit competing explanations against the newly arrived record.
            hypotheses = list(await self._proposer.propose(incident, evidence))
            if any(h.tenant_id not in (None, tenant_id) or h.incident_id not in (None, incident_id) for h in hypotheses):
                raise ValueError("hypothesis scope does not match diagnosis incident")
            hypotheses = [h.model_copy(update={"tenant_id": tenant_id, "incident_id": incident_id}) for h in hypotheses]

        hypotheses = self._update_hypotheses(hypotheses, evidence)
        winner = self._select_winner(hypotheses)
        if winner is not None:
            return self._result(incident, DiagnosisStatus.ROOT_CAUSE_IDENTIFIED, hypotheses, evidence, winner, (), rounds, reasons)
        gaps = tuple(
            EvidenceGap(
                hypothesis_id=h.hypothesis_id,
                evidence_kind=kind,
                diagnostic_question=f"Gather {kind} evidence relevant to: {h.claim}",
            )
            for h in hypotheses
            if h.status not in {HypothesisStatus.REFUTED, HypothesisStatus.CONTRADICTED}
            for kind in h.missing_evidence
        )
        status = DiagnosisStatus.AMBIGUOUS if len([h for h in hypotheses if h.status != HypothesisStatus.REFUTED]) > 1 and evidence else DiagnosisStatus.INSUFFICIENT_EVIDENCE
        if not evidence:
            reasons.append("no_usable_evidence")
        return self._result(incident, status, hypotheses, evidence, None, gaps, rounds, reasons)

    def _update_hypotheses(self, hypotheses: Sequence[Hypothesis], evidence: Sequence[EvidenceItem]) -> list[Hypothesis]:
        now = self._now()
        updated: list[Hypothesis] = []
        max_age = self._planner.config.maximum_evidence_age_seconds
        for hypothesis in hypotheses:
            support = set(hypothesis.supporting_evidence_ids)
            contradiction = set(hypothesis.contradicting_evidence_ids)
            missing = set(hypothesis.missing_evidence)
            usable_support: list[EvidenceItem] = []
            for item in evidence:
                age = max(0.0, (now - item.observed_at).total_seconds())
                if age > max_age:
                    if item.domain is not None:
                        missing.add(item.domain.value)
                    continue
                if hypothesis.hypothesis_id in item.supports_hypothesis_ids:
                    support.add(item.evidence_id)
                    usable_support.append(item)
                if hypothesis.hypothesis_id in item.contradicts_hypothesis_ids:
                    contradiction.add(item.evidence_id)
            confidence_values = [item.confidence for item in usable_support if item.confidence is not None]
            confidence = sum(confidence_values) / len(confidence_values) if confidence_values else hypothesis.confidence
            if contradiction:
                status = HypothesisStatus.CONTRADICTED
            elif support:
                status = HypothesisStatus.SUPPORTED
            else:
                status = HypothesisStatus.INSUFFICIENT_EVIDENCE
            updated.append(hypothesis.model_copy(update={
                "supporting_evidence_ids": tuple(sorted(support, key=str)),
                "contradicting_evidence_ids": tuple(sorted(contradiction, key=str)),
                "evidence_ids": tuple(sorted(support, key=str)),
                "confidence": confidence,
                "status": status,
                "missing_evidence": tuple(sorted(missing)),
                "updated_at": now,
            }))
        return updated

    def _select_winner(self, hypotheses: Sequence[Hypothesis]) -> Hypothesis | None:
        c = self._planner.config
        ranked = sorted(
            (h for h in hypotheses if h.status == HypothesisStatus.SUPPORTED and not h.contradicting_evidence_ids and len(h.supporting_evidence_ids) >= c.minimum_supporting_evidence),
            key=lambda h: (-h.confidence, str(h.hypothesis_id)),
        )
        if not ranked or ranked[0].confidence < c.minimum_confidence:
            return None
        if len(ranked) > 1 and ranked[0].confidence - ranked[1].confidence < c.minimum_lead_over_alternative:
            return None
        return ranked[0]

    @staticmethod
    def _mark_gap_attempted(hypotheses: list[Hypothesis], gap: EvidenceGap) -> None:
        for index, hypothesis in enumerate(hypotheses):
            if hypothesis.hypothesis_id == gap.hypothesis_id:
                hypotheses[index] = hypothesis.model_copy(update={
                    "status": HypothesisStatus.INSUFFICIENT_EVIDENCE,
                })

    @staticmethod
    def _result(incident, status, hypotheses, evidence, selected, gaps, rounds, reasons):
        return DiagnosisResult(
            incident_id=incident.incident_id,
            tenant_id=incident.tenant_id,
            status=status,
            hypotheses=tuple(hypotheses),
            evidence=tuple(evidence),
            selected_hypothesis_id=selected.hypothesis_id if selected else None,
            unresolved_gaps=tuple(gaps),
            diagnostic_rounds=rounds,
            reason_codes=tuple(dict.fromkeys(reasons)),
        )
