"""Tests for the multi-agent collaboration system.

Tests the protocol, agents, and orchestration without making real LLM calls.
"""

from __future__ import annotations

from unittest.mock import patch

from app.agents.protocol import (
    AgentHandoff,
    AgentMessage,
    AgentRole,
    MessageType,
    SharedContext,
    TaskStatus,
)
from app.agents.investigator import InvestigatorAgent
from app.agents.remediation import RemediationAgent
from app.agents.reviewer import ReviewerAgent
from app.agents.orchestrator import OrchestratorAgent, InvestigationPhase


class TestAgentMessage:
    """Tests for AgentMessage serialization."""

    def test_message_creation(self):
        msg = AgentMessage(
            id="test-123",
            from_agent=AgentRole.INVESTIGATOR,
            to_agent=AgentRole.ORCHESTRATOR,
            message_type=MessageType.RESULT,
            content={"root_cause": "null check missing"},
        )
        assert msg.from_agent == AgentRole.INVESTIGATOR
        assert msg.to_agent == AgentRole.ORCHESTRATOR
        assert msg.content["root_cause"] == "null check missing"

    def test_message_serialization(self):
        msg = AgentMessage(
            id="test-456",
            from_agent=AgentRole.REMEDIATION,
            to_agent=AgentRole.REVIEWER,
            message_type=MessageType.HANDOFF,
            content={"action": "create_pr"},
            priority=1,
        )
        data = msg.to_dict()
        assert data["from_agent"] == "remediation"
        assert data["to_agent"] == "reviewer"
        assert data["priority"] == 1

        restored = AgentMessage.from_dict(data)
        assert restored.from_agent == AgentRole.REMEDIATION
        assert restored.priority == 1


class TestAgentHandoff:
    """Tests for AgentHandoff task delegation."""

    def test_handoff_creation(self):
        handoff = AgentHandoff(
            id="handoff-1",
            from_agent=AgentRole.ORCHESTRATOR,
            to_agent=AgentRole.INVESTIGATOR,
            task="Investigate the checkout service 5xx spike",
        )
        assert handoff.status == TaskStatus.PENDING
        assert handoff.result is None

    def test_handoff_completion(self):
        handoff = AgentHandoff(
            id="handoff-2",
            from_agent=AgentRole.ORCHESTRATOR,
            to_agent=AgentRole.INVESTIGATOR,
            task="Find root cause",
        )
        handoff.complete({"root_cause": "database connection pool exhaustion"})
        assert handoff.status == TaskStatus.COMPLETED
        assert handoff.result["root_cause"] == "database connection pool exhaustion"

    def test_handoff_failure(self):
        handoff = AgentHandoff(
            id="handoff-3",
            from_agent=AgentRole.ORCHESTRATOR,
            to_agent=AgentRole.REMEDIATION,
            task="Propose fix",
        )
        handoff.fail("Insufficient evidence")
        assert handoff.status == TaskStatus.FAILED
        assert "error" in handoff.result


class TestSharedContext:
    """Tests for SharedContext cross-agent state."""

    def test_context_creation(self):
        ctx = SharedContext(session_id="session-123")
        assert ctx.session_id == "session-123"
        assert ctx.severity == "SEV3"
        assert ctx.confidence == "low"

    def test_add_evidence(self):
        ctx = SharedContext(session_id="s1")
        ctx.add_evidence("datadog", ["error spike at 14:32", "500 errors in checkout"])
        ctx.add_evidence("sentry", ["NullPointerException at line 42"])

        assert len(ctx.evidence["datadog"]) == 2
        assert len(ctx.evidence["sentry"]) == 1

    def test_add_hypothesis(self):
        ctx = SharedContext(session_id="s2")
        ctx.add_hypothesis(
            cause="Database connection timeout",
            confidence="medium",
            evidence=["connection pool metrics elevated", "slow query logs"],
            source_agent=AgentRole.INVESTIGATOR,
        )

        assert len(ctx.hypotheses) == 1
        assert ctx.hypotheses[0]["cause"] == "Database connection timeout"
        assert ctx.hypotheses[0]["source_agent"] == "investigator"

    def test_all_evidence_list(self):
        ctx = SharedContext(session_id="s3")
        ctx.add_evidence("logs", ["log1", "log2"])
        ctx.add_evidence("metrics", ["metric1"])

        all_ev = ctx.all_evidence_list()
        assert len(all_ev) == 3
        assert any("[logs]" in e for e in all_ev)
        assert any("[metrics]" in e for e in all_ev)

    def test_serialization(self):
        ctx = SharedContext(session_id="s4")
        ctx.incident_summary = "Checkout failing"
        ctx.severity = "SEV2"
        ctx.root_cause = "Null check missing"
        ctx.add_evidence("sentry", ["NullPointerException"])

        data = ctx.to_dict()
        restored = SharedContext.from_dict(data)

        assert restored.session_id == "s4"
        assert restored.severity == "SEV2"
        assert restored.root_cause == "Null check missing"
        assert "sentry" in restored.evidence


class TestReviewerQuickCheck:
    """Tests for ReviewerAgent's quick validation checks."""

    def test_quick_check_no_root_cause(self):
        reviewer = ReviewerAgent(tools=None, fast=True)
        result = reviewer.quick_check(
            action={"type": "create_pull_request"},
            root_cause=None,
            evidence_count=5,
        )
        assert result["recommendation"] == "block"
        assert any("No root cause" in c for c in result["concerns"])

    def test_quick_check_insufficient_evidence(self):
        reviewer = ReviewerAgent(tools=None, fast=True)
        result = reviewer.quick_check(
            action={"type": "rollback_deployment"},
            root_cause="Bad deploy",
            evidence_count=1,
        )
        assert result["recommendation"] == "block"
        assert any("Insufficient evidence" in c for c in result["concerns"])

    def test_quick_check_high_risk_action(self):
        reviewer = ReviewerAgent(tools=None, fast=True)
        result = reviewer.quick_check(
            action={"type": "scale_deployment", "deployment": "prod-checkout"},
            root_cause="Resource exhaustion",
            evidence_count=5,
        )
        # Should flag high-risk production action
        assert any("production" in c.lower() for c in result["concerns"])

    def test_quick_check_passes(self):
        reviewer = ReviewerAgent(tools=None, fast=True)
        result = reviewer.quick_check(
            action={"type": "create_pull_request"},
            root_cause="Null check missing in checkout.js:42",
            evidence_count=5,
        )
        assert result["recommendation"] == "proceed_to_review"
        assert result["has_root_cause"] is True


class TestInvestigationPhases:
    """Tests for OrchestratorAgent phase management."""

    def test_initial_phase(self):
        ctx = SharedContext(session_id="test")
        assert ctx.metadata.get("phase") is None

    def test_phase_transitions(self):
        ctx = SharedContext(session_id="test")
        ctx.metadata["phase"] = InvestigationPhase.INITIAL
        assert ctx.metadata["phase"] == "initial"

        ctx.metadata["phase"] = InvestigationPhase.INVESTIGATING
        assert ctx.metadata["phase"] == "investigating"

        ctx.metadata["phase"] = InvestigationPhase.ROOT_CAUSE_FOUND
        assert ctx.metadata["phase"] == "root_cause_found"


class TestAgentRoles:
    """Tests for agent role properties."""

    def test_investigator_role(self):
        inv = InvestigatorAgent(tools=None)
        assert inv.role == AgentRole.INVESTIGATOR
        assert "Investigator" in inv.name

    def test_remediation_role(self):
        rem = RemediationAgent(tools=None)
        assert rem.role == AgentRole.REMEDIATION
        assert "Remediation" in rem.name

    def test_reviewer_role(self):
        rev = ReviewerAgent(tools=None)
        assert rev.role == AgentRole.REVIEWER
        assert "Reviewer" in rev.name

    def test_orchestrator_role(self):
        orch = OrchestratorAgent(tools=None)
        assert orch.role == AgentRole.ORCHESTRATOR
        assert "Orchestrator" in orch.name


class TestOrchestratorTeam:
    """Tests for OrchestratorAgent's agent team management."""

    def test_lazy_agent_creation(self):
        orch = OrchestratorAgent(tools=None)
        # Agents not created until accessed
        assert orch._investigator is None
        assert orch._remediator is None
        assert orch._reviewer is None

        # Access triggers creation
        _ = orch.investigator
        assert orch._investigator is not None

    def test_get_status(self):
        orch = OrchestratorAgent(tools=None)
        ctx = SharedContext(session_id="test-session")
        ctx.root_cause = "Test cause"
        ctx.severity = "SEV2"
        ctx.add_evidence("logs", ["log1", "log2", "log3"])
        ctx.add_hypothesis(
            cause="Test hypothesis",
            confidence="high",
            evidence=["evidence1"],
            source_agent=AgentRole.INVESTIGATOR,
        )

        status = orch.get_status(ctx)
        assert status["session_id"] == "test-session"
        assert status["root_cause_found"] is True
        assert status["severity"] == "SEV2"
        assert status["evidence_count"] == 3
        assert status["hypotheses_count"] == 1


class TestMessageRouting:
    """Tests for inter-agent message passing."""

    def test_send_message(self):
        ctx = SharedContext(session_id="test")
        inv = InvestigatorAgent(tools=None)

        inv.send_message(
            to_agent=AgentRole.ORCHESTRATOR,
            message_type=MessageType.RESULT,
            content={"root_cause": "found it"},
            context=ctx,
        )

        assert len(ctx.messages) == 1
        assert ctx.messages[0].from_agent == AgentRole.INVESTIGATOR
        assert ctx.messages[0].to_agent == AgentRole.ORCHESTRATOR

    def test_create_handoff(self):
        ctx = SharedContext(session_id="test")
        orch = OrchestratorAgent(tools=None)

        orch.create_handoff(
            to_agent=AgentRole.INVESTIGATOR,
            task="Find root cause",
            context=ctx,
            expected_output="Root cause with evidence",
        )

        assert len(ctx.handoffs) == 1
        assert ctx.handoffs[0].to_agent == AgentRole.INVESTIGATOR
        assert ctx.handoffs[0].status == TaskStatus.PENDING


class TestConfigIntegration:
    """Tests that multi-agent config settings work correctly."""

    def test_multi_agent_disabled_by_default(self):
        from app.config import get_settings
        settings = get_settings()
        assert settings.copilot_multi_agent_enabled is False
        assert settings.copilot_agent_mode == "single"

    @patch.dict("os.environ", {"COPILOT_MULTI_AGENT_ENABLED": "true"})
    def test_multi_agent_enabled_via_env(self):
        # Need to clear the lru_cache to test with new env
        from app.config import get_settings
        get_settings.cache_clear()
        settings = get_settings()
        assert settings.copilot_multi_agent_enabled is True
        get_settings.cache_clear()  # Clean up


class TestPrompts:
    """Tests that agent prompts are properly defined."""

    def test_investigator_prompt_exists(self):
        from app.graph.prompts import INVESTIGATOR_AGENT_SYSTEM
        assert "InvestigatorAgent" in INVESTIGATOR_AGENT_SYSTEM
        assert "root cause" in INVESTIGATOR_AGENT_SYSTEM.lower()

    def test_remediation_prompt_exists(self):
        from app.graph.prompts import REMEDIATION_AGENT_SYSTEM
        assert "RemediationAgent" in REMEDIATION_AGENT_SYSTEM
        assert "fix" in REMEDIATION_AGENT_SYSTEM.lower()

    def test_reviewer_prompt_exists(self):
        from app.graph.prompts import REVIEWER_AGENT_SYSTEM
        assert "ReviewerAgent" in REVIEWER_AGENT_SYSTEM
        assert "validating" in REVIEWER_AGENT_SYSTEM.lower()

    def test_orchestrator_prompt_exists(self):
        from app.graph.prompts import ORCHESTRATOR_AGENT_SYSTEM
        assert "OrchestratorAgent" in ORCHESTRATOR_AGENT_SYSTEM
        assert "coordinator" in ORCHESTRATOR_AGENT_SYSTEM.lower()


class TestBackwardsCompatibility:
    """Tests that single-agent mode still works with multi-agent code present."""

    def test_single_agent_graph_builds(self):
        from app.graph.builder import build_graph, _build_single_agent_graph

        # Just verify the function is callable and doesn't error
        # (actual graph building needs tools and checkpointer)
        assert callable(build_graph)
        assert callable(_build_single_agent_graph)

    def test_multi_agent_graph_builds(self):
        from app.graph.builder import _build_multi_agent_graph
        assert callable(_build_multi_agent_graph)

    def test_state_has_multi_agent_fields(self):
        from app.graph.state import AgentState
        # Verify new fields exist in the TypedDict
        annotations = AgentState.__annotations__
        assert "multi_agent_context" in annotations
        assert "multi_agent_phase" in annotations
        assert "multi_agent_agent" in annotations
        assert "pending_tool_calls" in annotations

    def test_state_backwards_compatible(self):
        from app.graph.state import AgentState
        # Old fields still exist
        annotations = AgentState.__annotations__
        assert "messages" in annotations
        assert "plan" in annotations
        assert "pending_action" in annotations
        assert "status" in annotations
        assert "report" in annotations
