"""Comprehensive tests for app/agents/parallel_coordinator.py.

Tests ParallelInvestigationCoordinator initialization, specialist execution,
finding synthesis, confidence calculation, and result aggregation.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.agents.parallel_coordinator import (
    ConfidenceFactor,
    Finding,
    ParallelInvestigationCoordinator,
    ParallelInvestigationResult,
    SpecialistResult,
    SpecialistType,
    run_parallel_investigation,
)
from app.agents.protocol import SharedContext


# -----------------------------------------------------------------------------
# Data Structure Tests
# -----------------------------------------------------------------------------


class TestFinding:
    """Tests for Finding dataclass."""

    def test_finding_creation(self):
        finding = Finding(
            finding_type="anomaly",
            title="Error spike",
            description="500 errors increased by 50%",
            confidence_score=0.85,
        )

        assert finding.finding_type == "anomaly"
        assert finding.title == "Error spike"
        assert finding.confidence_score == 0.85
        assert finding.evidence == []
        assert finding.affected_services == []

    def test_finding_with_evidence(self):
        finding = Finding(
            finding_type="correlation",
            title="Deploy correlation",
            description="Issue started after deploy",
            confidence_score=0.9,
            evidence=[{"type": "deployment", "sha": "abc123"}],
            affected_services=["checkout-svc"],
            severity="high",
        )

        assert len(finding.evidence) == 1
        assert finding.affected_services == ["checkout-svc"]
        assert finding.severity == "high"


class TestSpecialistResult:
    """Tests for SpecialistResult dataclass."""

    def test_specialist_result_creation(self):
        result = SpecialistResult(
            specialist_type=SpecialistType.TELEMETRY,
            status="completed",
        )

        assert result.specialist_type == SpecialistType.TELEMETRY
        assert result.status == "completed"
        assert result.findings == []
        assert result.confidence_score == 0.0
        assert result.error is None

    def test_specialist_result_with_findings(self):
        finding = Finding(
            finding_type="anomaly",
            title="Test",
            description="Test finding",
            confidence_score=0.8,
        )
        result = SpecialistResult(
            specialist_type=SpecialistType.CODE,
            status="completed",
            findings=[finding],
            confidence_score=0.8,
            reasoning="Strong evidence",
            duration_ms=1500,
            input_tokens=100,
            output_tokens=50,
        )

        assert len(result.findings) == 1
        assert result.duration_ms == 1500

    def test_specialist_result_failed(self):
        result = SpecialistResult(
            specialist_type=SpecialistType.DEPENDENCIES,
            status="failed",
            error="API timeout",
        )

        assert result.status == "failed"
        assert result.error == "API timeout"


class TestConfidenceFactor:
    """Tests for ConfidenceFactor dataclass."""

    def test_confidence_factor_creation(self):
        factor = ConfidenceFactor(
            name="Evidence Quality",
            score=0.85,
            weight=0.25,
            explanation="5 high-confidence findings",
        )

        assert factor.name == "Evidence Quality"
        assert factor.score == 0.85
        assert factor.weight == 0.25


class TestParallelInvestigationResult:
    """Tests for ParallelInvestigationResult dataclass."""

    def test_result_to_dict(self):
        finding = Finding(
            finding_type="anomaly",
            title="Test",
            description="Test",
            confidence_score=0.8,
        )
        factor = ConfidenceFactor(
            name="Test Factor",
            score=0.9,
            weight=0.5,
            explanation="Test",
        )
        specialist_result = SpecialistResult(
            specialist_type=SpecialistType.TELEMETRY,
            status="completed",
            findings=[finding],
            confidence_score=0.8,
        )

        result = ParallelInvestigationResult(
            session_id=uuid4(),
            status="completed",
            root_cause_hypothesis="Database timeout",
            confidence_score=0.85,
            confidence_factors=[factor],
            summary="Investigation complete",
            all_findings=[finding],
            suggested_actions=[{"action": "rollback"}],
            specialist_results={"telemetry": specialist_result},
            total_duration_ms=5000,
            total_cost_usd=Decimal("0.05"),
        )

        data = result.to_dict()

        assert isinstance(data["session_id"], str)
        assert data["status"] == "completed"
        assert data["confidence_score"] == 0.85
        assert len(data["confidence_factors"]) == 1
        assert len(data["all_findings"]) == 1
        assert data["total_cost_usd"] == 0.05


class TestSpecialistType:
    """Tests for SpecialistType enum."""

    def test_all_specialist_types(self):
        assert SpecialistType.TELEMETRY == "telemetry"
        assert SpecialistType.CODE == "code"
        assert SpecialistType.DEPENDENCIES == "dependencies"
        assert SpecialistType.HISTORICAL == "historical"
        assert SpecialistType.KNOWLEDGE == "knowledge"
        assert SpecialistType.COLLABORATION == "collaboration"

    def test_six_specialist_types(self):
        assert len(list(SpecialistType)) == 6


# -----------------------------------------------------------------------------
# Coordinator Initialization Tests
# -----------------------------------------------------------------------------


class TestCoordinatorInitialization:
    """Tests for ParallelInvestigationCoordinator initialization."""

    def test_init_default(self):
        coord = ParallelInvestigationCoordinator()
        assert coord._client is None
        assert coord._model == "claude-sonnet-4-20250514"

    def test_init_with_client(self):
        mock_client = MagicMock()
        coord = ParallelInvestigationCoordinator(client=mock_client)
        assert coord._client == mock_client

    def test_init_with_model(self):
        coord = ParallelInvestigationCoordinator(model="claude-3-haiku-20240307")
        assert coord._model == "claude-3-haiku-20240307"

    def test_client_lazy_creation(self):
        coord = ParallelInvestigationCoordinator()
        assert coord._client is None
        # Accessing client property would create it, but we'll mock that

    def test_specialist_prompts_defined(self):
        coord = ParallelInvestigationCoordinator()
        assert len(coord.SPECIALIST_PROMPTS) == 6
        for spec_type in SpecialistType:
            assert spec_type in coord.SPECIALIST_PROMPTS


# -----------------------------------------------------------------------------
# Model Selection Tests
# -----------------------------------------------------------------------------


class TestModelSelection:
    """Tests for _select_model method."""

    def test_select_model_critical_severity(self):
        coord = ParallelInvestigationCoordinator()
        assert coord._select_model("critical") == "claude-sonnet-4-20250514"
        assert coord._select_model("sev1") == "claude-sonnet-4-20250514"
        assert coord._select_model("SEV1") == "claude-sonnet-4-20250514"

    def test_select_model_high_severity(self):
        coord = ParallelInvestigationCoordinator()
        assert coord._select_model("high") == "claude-sonnet-4-20250514"
        assert coord._select_model("sev2") == "claude-sonnet-4-20250514"
        assert coord._select_model("SEV2") == "claude-sonnet-4-20250514"

    def test_select_model_other_severity(self):
        coord = ParallelInvestigationCoordinator()
        assert coord._select_model("medium") == "claude-3-haiku-20240307"
        assert coord._select_model("low") == "claude-3-haiku-20240307"
        assert coord._select_model("unknown") == "claude-3-haiku-20240307"


# -----------------------------------------------------------------------------
# Cost Calculation Tests
# -----------------------------------------------------------------------------


class TestCostCalculation:
    """Tests for _calculate_cost method."""

    def test_calculate_cost_sonnet(self):
        coord = ParallelInvestigationCoordinator()
        cost = coord._calculate_cost(
            "claude-sonnet-4-20250514",
            input_tokens=1000000,
            output_tokens=100000,
        )
        # 1M input * $3/M + 100K output * $15/M = $3 + $1.5 = $4.5
        assert cost == Decimal("4.5")

    def test_calculate_cost_haiku(self):
        coord = ParallelInvestigationCoordinator()
        cost = coord._calculate_cost(
            "claude-3-haiku-20240307",
            input_tokens=1000000,
            output_tokens=100000,
        )
        # 1M input * $0.25/M + 100K output * $1.25/M = $0.25 + $0.125 = $0.375
        assert cost == Decimal("0.375")

    def test_calculate_cost_unknown_model(self):
        coord = ParallelInvestigationCoordinator()
        # Should fall back to default pricing
        cost = coord._calculate_cost(
            "unknown-model",
            input_tokens=1000,
            output_tokens=100,
        )
        assert cost > 0

    def test_calculate_cost_zero_tokens(self):
        coord = ParallelInvestigationCoordinator()
        cost = coord._calculate_cost("claude-sonnet-4-20250514", 0, 0)
        assert cost == Decimal("0")


# -----------------------------------------------------------------------------
# JSON Parsing Tests
# -----------------------------------------------------------------------------


class TestJsonParsing:
    """Tests for _parse_json_response method."""

    def test_parse_valid_json(self):
        coord = ParallelInvestigationCoordinator()
        result = coord._parse_json_response('{"key": "value"}')
        assert result == {"key": "value"}

    def test_parse_json_in_code_block(self):
        coord = ParallelInvestigationCoordinator()
        content = """```json
{"key": "value"}
```"""
        result = coord._parse_json_response(content)
        assert result == {"key": "value"}

    def test_parse_json_in_generic_code_block(self):
        coord = ParallelInvestigationCoordinator()
        content = """```
{"key": "value"}
```"""
        result = coord._parse_json_response(content)
        assert result == {"key": "value"}

    def test_parse_invalid_json(self):
        coord = ParallelInvestigationCoordinator()
        result = coord._parse_json_response("not valid json")
        assert result == {"findings": [], "confidence_score": 0.0}

    def test_parse_json_with_text_around(self):
        coord = ParallelInvestigationCoordinator()
        content = """Here is the response:
```json
{"findings": [{"title": "test"}]}
```
That's all."""
        result = coord._parse_json_response(content)
        assert "findings" in result


# -----------------------------------------------------------------------------
# Specialist Context Building Tests
# -----------------------------------------------------------------------------


class TestBuildSpecialistContext:
    """Tests for _build_specialist_context method."""

    def test_build_telemetry_context(self):
        coord = ParallelInvestigationCoordinator()
        context = {
            "title": "API errors",
            "severity": "SEV2",
            "telemetry": {
                "metrics": {"error_rate": 0.5},
                "logs": "Error: Connection refused",
                "traces": [],
            },
        }

        result = coord._build_specialist_context(SpecialistType.TELEMETRY, context)

        assert "API errors" in result
        assert "SEV2" in result
        assert "error_rate" in result
        assert "Connection refused" in result

    def test_build_code_context(self):
        coord = ParallelInvestigationCoordinator()
        context = {
            "title": "Checkout failing",
            "code": {
                "deployments": [{"sha": "abc123"}],
                "commits": [{"message": "fix: update handler"}],
            },
        }

        result = coord._build_specialist_context(SpecialistType.CODE, context)

        assert "Checkout failing" in result
        assert "abc123" in result

    def test_build_dependencies_context(self):
        coord = ParallelInvestigationCoordinator()
        context = {
            "title": "Slow responses",
            "dependencies": {
                "services": {"db": "healthy"},
                "databases": {"postgres": "degraded"},
            },
        }

        result = coord._build_specialist_context(SpecialistType.DEPENDENCIES, context)

        assert "postgres" in result
        assert "degraded" in result

    def test_build_historical_context(self):
        coord = ParallelInvestigationCoordinator()
        context = {
            "title": "Memory leak",
            "historical": {
                "past_incidents": [{"id": "INC-123"}],
                "runbooks": [{"name": "memory-runbook"}],
            },
        }

        result = coord._build_specialist_context(SpecialistType.HISTORICAL, context)

        assert "INC-123" in result
        assert "memory-runbook" in result

    def test_build_knowledge_context(self):
        coord = ParallelInvestigationCoordinator()
        context = {
            "title": "Auth failure",
            "knowledge": {
                "architecture_docs": [{"title": "Auth Service Design"}],
                "service_docs": [],
                "wiki_pages": [],
            },
        }

        result = coord._build_specialist_context(SpecialistType.KNOWLEDGE, context)

        assert "Auth Service Design" in result

    def test_build_collaboration_context(self):
        coord = ParallelInvestigationCoordinator()
        context = {
            "title": "Payment errors",
            "collaboration": {
                "service_owners": [{"name": "John Doe"}],
                "oncall": {"primary": "Jane"},
                "recent_contributors": [],
            },
        }

        result = coord._build_specialist_context(SpecialistType.COLLABORATION, context)

        assert "John Doe" in result
        assert "Jane" in result


# -----------------------------------------------------------------------------
# Format Specialist Result Tests
# -----------------------------------------------------------------------------


class TestFormatSpecialistResult:
    """Tests for _format_specialist_result method."""

    def test_format_none_result(self):
        coord = ParallelInvestigationCoordinator()
        result = coord._format_specialist_result(None)
        assert "did not run" in result

    def test_format_failed_result(self):
        coord = ParallelInvestigationCoordinator()
        specialist = SpecialistResult(
            specialist_type=SpecialistType.TELEMETRY,
            status="failed",
            error="API error",
        )
        result = coord._format_specialist_result(specialist)
        assert "failed" in result.lower()
        assert "API error" in result

    def test_format_timeout_result(self):
        coord = ParallelInvestigationCoordinator()
        specialist = SpecialistResult(
            specialist_type=SpecialistType.CODE,
            status="timeout",
        )
        result = coord._format_specialist_result(specialist)
        assert "timed out" in result.lower()

    def test_format_no_findings(self):
        coord = ParallelInvestigationCoordinator()
        specialist = SpecialistResult(
            specialist_type=SpecialistType.DEPENDENCIES,
            status="completed",
            findings=[],
        )
        result = coord._format_specialist_result(specialist)
        assert "No findings" in result

    def test_format_with_findings(self):
        coord = ParallelInvestigationCoordinator()
        finding = Finding(
            finding_type="anomaly",
            title="Error spike",
            description="500 errors increased",
            confidence_score=0.85,
        )
        specialist = SpecialistResult(
            specialist_type=SpecialistType.TELEMETRY,
            status="completed",
            findings=[finding],
            confidence_score=0.85,
            reasoning="Strong evidence",
        )
        result = coord._format_specialist_result(specialist)
        assert "Error spike" in result
        assert "85%" in result
        assert "Strong evidence" in result


# -----------------------------------------------------------------------------
# Confidence Calculation Tests
# -----------------------------------------------------------------------------


class TestConfidenceCalculation:
    """Tests for _calculate_confidence method."""

    def test_calculate_confidence_all_successful(self):
        coord = ParallelInvestigationCoordinator()

        specialist_results = {
            spec.value: SpecialistResult(
                specialist_type=spec,
                status="completed",
                confidence_score=0.8,
                findings=[Finding(
                    finding_type="evidence",
                    title="Test",
                    description="Test",
                    confidence_score=0.8,
                )],
            )
            for spec in SpecialistType
        }

        synthesis = {"confidence_score": 0.9}

        score, factors = coord._calculate_confidence(specialist_results, synthesis)

        assert 0.0 <= score <= 1.0
        assert len(factors) == 4
        # All specialists succeeded with high confidence
        assert score > 0.5

    def test_calculate_confidence_some_failed(self):
        coord = ParallelInvestigationCoordinator()

        specialist_results = {
            SpecialistType.TELEMETRY.value: SpecialistResult(
                specialist_type=SpecialistType.TELEMETRY,
                status="completed",
                confidence_score=0.8,
            ),
            SpecialistType.CODE.value: SpecialistResult(
                specialist_type=SpecialistType.CODE,
                status="failed",
                error="Error",
            ),
            SpecialistType.DEPENDENCIES.value: SpecialistResult(
                specialist_type=SpecialistType.DEPENDENCIES,
                status="timeout",
            ),
            SpecialistType.HISTORICAL.value: SpecialistResult(
                specialist_type=SpecialistType.HISTORICAL,
                status="completed",
                confidence_score=0.3,
            ),
            SpecialistType.KNOWLEDGE.value: SpecialistResult(
                specialist_type=SpecialistType.KNOWLEDGE,
                status="completed",
                confidence_score=0.7,
            ),
            SpecialistType.COLLABORATION.value: SpecialistResult(
                specialist_type=SpecialistType.COLLABORATION,
                status="completed",
                confidence_score=0.6,
            ),
        }

        synthesis = {"confidence_score": 0.5}

        score, factors = coord._calculate_confidence(specialist_results, synthesis)

        # Lower score due to failures
        assert score < 0.8

    def test_confidence_factors_have_correct_weights(self):
        coord = ParallelInvestigationCoordinator()

        specialist_results = {}
        synthesis = {"confidence_score": 0.5}

        score, factors = coord._calculate_confidence(specialist_results, synthesis)

        total_weight = sum(f.weight for f in factors)
        assert abs(total_weight - 1.0) < 0.01  # Weights sum to 1


# -----------------------------------------------------------------------------
# Async Tests
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
class TestAsyncInvestigation:
    """Tests for async investigation methods."""

    async def test_run_specialist_success(self):
        coord = ParallelInvestigationCoordinator()

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text='{"findings": [], "confidence_score": 0.8, "reasoning": "test"}')]
        mock_response.usage.input_tokens = 100
        mock_response.usage.output_tokens = 50

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        coord._client = mock_client

        context = {"title": "Test incident", "severity": "SEV2"}

        result = await coord._run_specialist(SpecialistType.TELEMETRY, context, "claude-sonnet-4-20250514")

        assert result.status == "completed"
        assert result.specialist_type == SpecialistType.TELEMETRY

    async def test_run_specialist_failure(self):
        coord = ParallelInvestigationCoordinator()

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(side_effect=Exception("API Error"))
        coord._client = mock_client

        context = {"title": "Test", "severity": "SEV3"}

        result = await coord._run_specialist(SpecialistType.CODE, context, "claude-sonnet-4-20250514")

        assert result.status == "failed"
        assert "API Error" in result.error

    async def test_synthesize_findings_success(self):
        coord = ParallelInvestigationCoordinator()

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text='{"root_cause_hypothesis": "Test", "confidence_score": 0.8, "summary": "Done", "suggested_actions": []}')]

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        coord._client = mock_client

        specialist_results = {
            SpecialistType.TELEMETRY.value: SpecialistResult(
                specialist_type=SpecialistType.TELEMETRY,
                status="completed",
            ),
        }

        result = await coord._synthesize_findings(specialist_results, "claude-sonnet-4-20250514")

        assert "root_cause_hypothesis" in result

    async def test_synthesize_findings_failure(self):
        coord = ParallelInvestigationCoordinator()

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(side_effect=Exception("Synthesis failed"))
        coord._client = mock_client

        specialist_results = {}

        result = await coord._synthesize_findings(specialist_results, "claude-sonnet-4-20250514")

        assert "Synthesis failed" in result["root_cause_hypothesis"]
        assert result["confidence_score"] == 0.0

    async def test_investigate_timeout(self):
        coord = ParallelInvestigationCoordinator()

        async def slow_specialist(*args, **kwargs):
            await asyncio.sleep(10)
            return SpecialistResult(
                specialist_type=SpecialistType.TELEMETRY,
                status="completed",
            )

        with patch.object(coord, "_run_specialist", side_effect=slow_specialist):
            with patch.object(coord, "_synthesize_findings", return_value={}):
                result = await coord.investigate(
                    incident_id=uuid4(),
                    organization_id=uuid4(),
                    context={"title": "Test", "severity": "SEV3"},
                    timeout_seconds=0,  # Immediate timeout
                )

        assert result.status == "completed"
        # All specialists should show timeout
        for spec_result in result.specialist_results.values():
            assert spec_result.status == "timeout"

    async def test_investigate_full_success(self):
        coord = ParallelInvestigationCoordinator()

        # Mock successful specialist run
        async def mock_run_specialist(spec_type, context, model):
            return SpecialistResult(
                specialist_type=spec_type,
                status="completed",
                confidence_score=0.8,
                findings=[Finding(
                    finding_type="evidence",
                    title="Test finding",
                    description="Test",
                    confidence_score=0.8,
                )],
                input_tokens=100,
                output_tokens=50,
            )

        # Mock synthesis
        async def mock_synthesize(results, model):
            return {
                "root_cause_hypothesis": "Database timeout",
                "confidence_score": 0.85,
                "summary": "Investigation complete",
                "suggested_actions": [{"action": "rollback"}],
                "alternative_hypotheses": [],
            }

        with patch.object(coord, "_run_specialist", side_effect=mock_run_specialist):
            with patch.object(coord, "_synthesize_findings", side_effect=mock_synthesize):
                result = await coord.investigate(
                    incident_id=uuid4(),
                    organization_id=uuid4(),
                    context={"title": "Test", "severity": "SEV2"},
                    timeout_seconds=60,
                )

        assert result.status == "completed"
        assert result.root_cause_hypothesis == "Database timeout"
        assert len(result.specialist_results) == 6
        assert len(result.all_findings) == 6  # One per specialist


# -----------------------------------------------------------------------------
# Integration Function Tests
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
class TestRunParallelInvestigation:
    """Tests for run_parallel_investigation entry point."""

    async def test_run_with_dict_context(self):
        mock_coord = MagicMock()
        mock_result = ParallelInvestigationResult(
            session_id=uuid4(),
            status="completed",
            root_cause_hypothesis="Test",
            confidence_score=0.8,
            confidence_factors=[],
            summary="Done",
            all_findings=[],
            suggested_actions=[],
            specialist_results={},
            total_duration_ms=1000,
            total_cost_usd=Decimal("0.01"),
        )
        mock_coord.investigate = AsyncMock(return_value=mock_result)

        with patch("app.agents.parallel_coordinator.ParallelInvestigationCoordinator", return_value=mock_coord):
            result = await run_parallel_investigation(
                incident_id=uuid4(),
                organization_id=uuid4(),
                context={"title": "Test"},
                timeout_seconds=30,
            )

        assert result.status == "completed"

    async def test_run_with_shared_context(self):
        mock_coord = MagicMock()
        mock_result = ParallelInvestigationResult(
            session_id=uuid4(),
            status="completed",
            root_cause_hypothesis="Test",
            confidence_score=0.8,
            confidence_factors=[],
            summary="Done",
            all_findings=[],
            suggested_actions=[],
            specialist_results={},
            total_duration_ms=1000,
            total_cost_usd=Decimal("0.01"),
        )
        mock_coord.investigate = AsyncMock(return_value=mock_result)

        shared_context = SharedContext(session_id="test")
        shared_context.incident_summary = "Test incident"
        shared_context.severity = "SEV2"
        shared_context.affected_services = ["checkout-svc"]

        with patch("app.agents.parallel_coordinator.ParallelInvestigationCoordinator", return_value=mock_coord):
            result = await run_parallel_investigation(
                incident_id=uuid4(),
                organization_id=uuid4(),
                context=shared_context,
                timeout_seconds=30,
            )

        assert result.status == "completed"
        # Verify the context was converted
        mock_coord.investigate.assert_called_once()
        call_args = mock_coord.investigate.call_args
        assert call_args[1]["context"]["title"] == "Test incident"


# -----------------------------------------------------------------------------
# Model Pricing Tests
# -----------------------------------------------------------------------------


class TestModelPricing:
    """Tests for model pricing configuration."""

    def test_pricing_defined_for_models(self):
        coord = ParallelInvestigationCoordinator()

        assert "claude-sonnet-4-20250514" in coord.MODEL_PRICING
        assert "claude-3-5-sonnet-20241022" in coord.MODEL_PRICING
        assert "claude-3-haiku-20240307" in coord.MODEL_PRICING
        assert "claude-3-opus-20240229" in coord.MODEL_PRICING

    def test_pricing_has_input_and_output(self):
        coord = ParallelInvestigationCoordinator()

        for model, pricing in coord.MODEL_PRICING.items():
            assert "input" in pricing
            assert "output" in pricing
            assert pricing["input"] > 0
            assert pricing["output"] > 0
