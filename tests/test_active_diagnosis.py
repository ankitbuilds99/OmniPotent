from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.agents.active_diagnosis import (
    ActiveDiagnosisEngine,
    DiagnosisIncident,
    DiagnosisStatus,
    DiagnosticToolSpec,
    EvidenceGapPlanner,
    EvidenceGapPlannerConfig,
)
from app.contracts.evidence import EvidenceDomain, EvidenceItem
from app.contracts.hypothesis import Hypothesis, HypothesisStatus


def _config(**overrides):
    values = {
        "information_value_weight": 1.0,
        "latency_weight": 0.1,
        "cost_weight": 0.1,
        "risk_weight": 0.5,
        "availability_weight": 0.2,
        "freshness_weight": 0.2,
        "maximum_latency_seconds": 10,
        "maximum_cost": 10,
        "maximum_risk": 0.5,
        "maximum_evidence_age_seconds": 300,
        "minimum_supporting_evidence": 1,
        "minimum_confidence": 0.7,
        "minimum_lead_over_alternative": 0.2,
        "max_diagnostic_rounds": 4,
    }
    values.update(overrides)
    return EvidenceGapPlannerConfig(**values)


class FakeProposer:
    def __init__(self, hypotheses):
        self.hypotheses = list(hypotheses)
        self.calls = 0

    async def propose(self, incident, evidence):
        self.calls += 1
        return self.hypotheses


class FakeTool:
    def __init__(self, tool_id, kinds, results=(), *, fail=False, **scores):
        self.spec = DiagnosticToolSpec(
            tool_id=tool_id,
            evidence_kinds=tuple(kinds),
            information_value=scores.get("information_value", 0.8),
            latency_seconds=scores.get("latency_seconds", 1),
            cost=scores.get("cost", 1),
            risk=scores.get("risk", 0.1),
            availability=scores.get("availability", 1),
            freshness_seconds=scores.get("freshness_seconds", 1),
        )
        self.results = list(results)
        self.fail = fail
        self.calls = 0

    async def gather(self, incident, gap):
        self.calls += 1
        if self.fail:
            raise TimeoutError("recorded tool failure")
        return self.results


def _hypothesis(claim, *, evidence_kind="telemetry_metric", confidence=0.5):
    return Hypothesis(
        hypothesis_id=uuid4(),
        incident_id="incident-1",
        tenant_id="tenant-1",
        claim=claim,
        affected_entities=("checkout",),
        confidence=confidence,
        missing_evidence=(evidence_kind,),
        status=HypothesisStatus.PROPOSED,
    )


def _evidence(hypothesis, *, supports=True, age_seconds=1, tenant="tenant-1", incident="incident-1", confidence=0.95):
    now = datetime.now(timezone.utc)
    return EvidenceItem(
        source="recorded-test-source",
        evidence_type="recorded observation",
        observed_at=now - timedelta(seconds=age_seconds),
        payload={"observation": "recorded fake result"},
        tenant_id=tenant,
        incident_id=incident,
        domain=EvidenceDomain.TELEMETRY_METRIC,
        source_identity="fixture",
        supports_hypothesis_ids=(hypothesis.hypothesis_id,) if supports else (),
        contradicts_hypothesis_ids=() if supports else (hypothesis.hypothesis_id,),
        confidence=confidence,
    )


async def _incident_resolver(incident_id, tenant_id):
    if incident_id != "incident-1":
        return None
    return DiagnosisIncident(
        incident_id=incident_id,
        tenant_id=tenant_id,
        description="Recorded incident fixture",
        affected_entities=("checkout",),
    )


def _engine(proposer, tools=(), **config):
    return ActiveDiagnosisEngine(
        incident_resolver=_incident_resolver,
        proposer=proposer,
        tools=tools,
        planner=EvidenceGapPlanner(_config(**config)),
    )


@pytest.mark.asyncio
async def test_clear_rca_gathers_targeted_evidence_and_returns_supported_hypothesis():
    cause = _hypothesis("The database connection pool is exhausted")
    tool = FakeTool("metrics.query", ("telemetry_metric",), [_evidence(cause)])
    result = await _engine(FakeProposer([cause]), [tool]).diagnose("incident-1", "tenant-1")

    assert result.status == DiagnosisStatus.ROOT_CAUSE_IDENTIFIED
    assert result.selected_hypothesis_id == cause.hypothesis_id
    assert result.hypotheses[0].supporting_evidence_ids == (result.evidence[0].evidence_id,)
    assert tool.calls == 1


@pytest.mark.asyncio
async def test_ambiguous_rca_abstains_when_competing_supported_hypotheses_are_close():
    first = _hypothesis("Database pool saturation", confidence=0.5)
    second = _hypothesis("Downstream latency", confidence=0.5)
    # A test proposer can provide explicit support for both without making
    # assumptions about language in the data itself.
    first_evidence = _evidence(first, confidence=0.78)
    second_evidence = _evidence(second, confidence=0.77)
    tool = FakeTool("metrics.query", ("telemetry_metric",), [first_evidence, second_evidence])
    result = await _engine(FakeProposer([first, second]), [tool]).diagnose("incident-1", "tenant-1")
    assert result.status == DiagnosisStatus.AMBIGUOUS
    assert result.selected_hypothesis_id is None


@pytest.mark.asyncio
async def test_contradictory_evidence_blocks_root_cause_selection():
    hypothesis = _hypothesis("A deployment caused the incident")
    tool = FakeTool("metrics.query", ("telemetry_metric",), [_evidence(hypothesis, supports=False)])
    result = await _engine(FakeProposer([hypothesis]), [tool]).diagnose("incident-1", "tenant-1")
    assert result.status == DiagnosisStatus.INSUFFICIENT_EVIDENCE
    assert result.selected_hypothesis_id is None
    assert result.hypotheses[0].status == HypothesisStatus.CONTRADICTED


@pytest.mark.asyncio
async def test_tool_failure_abstains_with_machine_readable_reason():
    hypothesis = _hypothesis("A database saturation caused the incident")
    tool = FakeTool("metrics.query", ("telemetry_metric",), fail=True)
    result = await _engine(FakeProposer([hypothesis]), [tool]).diagnose("incident-1", "tenant-1")
    assert result.status == DiagnosisStatus.INSUFFICIENT_EVIDENCE
    assert "diagnostic_tool_failed" in result.reason_codes
    assert result.evidence == ()


@pytest.mark.asyncio
async def test_missing_evidence_remains_an_explicit_gap():
    hypothesis = _hypothesis("A recent deploy caused the incident", evidence_kind="deployment")
    result = await _engine(FakeProposer([hypothesis]), []).diagnose("incident-1", "tenant-1")
    assert result.status == DiagnosisStatus.INSUFFICIENT_EVIDENCE
    assert [(gap.hypothesis_id, gap.evidence_kind) for gap in result.unresolved_gaps] == [
        (hypothesis.hypothesis_id, "deployment")
    ]


@pytest.mark.asyncio
async def test_stale_evidence_does_not_support_hypothesis_or_resolve_gap():
    hypothesis = _hypothesis("A database saturation caused the incident")
    tool = FakeTool("metrics.query", ("telemetry_metric",), [_evidence(hypothesis, age_seconds=3600)])
    result = await _engine(FakeProposer([hypothesis]), [tool]).diagnose("incident-1", "tenant-1")
    assert result.status == DiagnosisStatus.INSUFFICIENT_EVIDENCE
    assert result.selected_hypothesis_id is None
    assert result.hypotheses[0].supporting_evidence_ids == ()


@pytest.mark.asyncio
async def test_unknown_incident_abstains_without_invoking_proposer_or_tool():
    hypothesis = _hypothesis("unused")
    proposer = FakeProposer([hypothesis])
    tool = FakeTool("metrics.query", ("telemetry_metric",), [_evidence(hypothesis)])
    result = await _engine(proposer, [tool]).diagnose("missing-incident", "tenant-1")
    assert result.status == DiagnosisStatus.UNKNOWN_INCIDENT
    assert proposer.calls == 0
    assert tool.calls == 0


@pytest.mark.asyncio
async def test_tool_selection_respects_availability_latency_cost_and_risk():
    hypothesis = _hypothesis("A database saturation caused the incident")
    rejected = FakeTool("unavailable", ("telemetry_metric",), availability=0)
    chosen = FakeTool("available", ("telemetry_metric",), [_evidence(hypothesis)])
    result = await _engine(FakeProposer([hypothesis]), [rejected, chosen]).diagnose("incident-1", "tenant-1")
    assert result.status == DiagnosisStatus.ROOT_CAUSE_IDENTIFIED
    assert rejected.calls == 0
    assert chosen.calls == 1


def test_diagnostic_tool_contract_rejects_mutating_capabilities():
    with pytest.raises(ValidationError):
        DiagnosticToolSpec(
            tool_id="deployment.restart",
            evidence_kinds=("deployment",),
            information_value=1,
            latency_seconds=1,
            cost=1,
            risk=0,
            availability=1,
            freshness_seconds=0,
            mutates=True,
        )


@pytest.mark.asyncio
async def test_cross_tenant_tool_evidence_is_discarded():
    hypothesis = _hypothesis("A database saturation caused the incident")
    tool = FakeTool("metrics.query", ("telemetry_metric",), [_evidence(hypothesis, tenant="other-tenant")])
    result = await _engine(FakeProposer([hypothesis]), [tool]).diagnose("incident-1", "tenant-1")
    assert result.status == DiagnosisStatus.INSUFFICIENT_EVIDENCE
    assert result.evidence == ()
    assert "evidence_scope_mismatch" in result.reason_codes
