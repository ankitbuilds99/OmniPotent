"""Comprehensive tests for app/agents/orchestrator.py.

Tests OrchestratorAgent initialization, phase management, routing,
agent team management, and status reporting.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


from app.agents.orchestrator import InvestigationPhase, OrchestratorAgent
from app.agents.protocol import (
    AgentHandoff,
    AgentMessage,
    AgentRole,
    MessageType,
    SharedContext,
    TaskStatus,
)


# -----------------------------------------------------------------------------
# Initialization Tests
# -----------------------------------------------------------------------------


class TestOrchestratorAgentInitialization:
    """Tests for OrchestratorAgent initialization."""

    def test_init_with_no_tools(self):
        orch = OrchestratorAgent()
        assert orch._tools == []
        assert orch._all_tools == []
        assert orch._fast is False

    def test_init_with_tools(self):
        mock_tool = MagicMock(name="test_tool")
        orch = OrchestratorAgent(tools=[mock_tool])

        # Orchestrator doesn't use tools directly
        assert orch._tools == []
        # But stores them for the team
        assert orch._all_tools == [mock_tool]

    def test_init_with_fast_mode(self):
        orch = OrchestratorAgent(fast=True)
        assert orch._fast is True

    def test_init_lazy_agent_creation(self):
        orch = OrchestratorAgent()
        assert orch._investigator is None
        assert orch._remediator is None
        assert orch._reviewer is None


class TestOrchestratorAgentProperties:
    """Tests for OrchestratorAgent property accessors."""

    def test_role_property(self):
        orch = OrchestratorAgent()
        assert orch.role == AgentRole.ORCHESTRATOR

    def test_system_prompt_property(self):
        orch = OrchestratorAgent()
        assert "OrchestratorAgent" in orch.system_prompt
        assert "coordinator" in orch.system_prompt.lower()

    def test_name_property(self):
        orch = OrchestratorAgent()
        assert orch.name == "OrchestratorAgent"


# -----------------------------------------------------------------------------
# Agent Team Management Tests
# -----------------------------------------------------------------------------


class TestOrchestratorAgentTeam:
    """Tests for lazy agent team creation."""

    def test_investigator_lazy_creation(self):
        orch = OrchestratorAgent()
        assert orch._investigator is None

        inv = orch.investigator
        assert inv is not None
        assert orch._investigator is not None
        assert inv.role == AgentRole.INVESTIGATOR

    def test_investigator_caching(self):
        orch = OrchestratorAgent()
        inv1 = orch.investigator
        inv2 = orch.investigator
        assert inv1 is inv2

    def test_remediator_lazy_creation(self):
        orch = OrchestratorAgent()
        assert orch._remediator is None

        rem = orch.remediator
        assert rem is not None
        assert orch._remediator is not None
        assert rem.role == AgentRole.REMEDIATION

    def test_remediator_caching(self):
        orch = OrchestratorAgent()
        rem1 = orch.remediator
        rem2 = orch.remediator
        assert rem1 is rem2

    def test_reviewer_lazy_creation(self):
        orch = OrchestratorAgent()
        assert orch._reviewer is None

        rev = orch.reviewer
        assert rev is not None
        assert orch._reviewer is not None
        assert rev.role == AgentRole.REVIEWER

    def test_reviewer_uses_fast_mode(self):
        orch = OrchestratorAgent()
        rev = orch.reviewer
        assert rev._fast is True

    def test_reviewer_caching(self):
        orch = OrchestratorAgent()
        rev1 = orch.reviewer
        rev2 = orch.reviewer
        assert rev1 is rev2

    def test_agents_inherit_tools(self):
        mock_tool = MagicMock()
        mock_tool.name = "search_logs"
        orch = OrchestratorAgent(tools=[mock_tool])

        inv = orch.investigator
        # Investigator should have access to tools
        assert len(inv._tools) > 0


# -----------------------------------------------------------------------------
# Investigation Phase Tests
# -----------------------------------------------------------------------------


class TestInvestigationPhase:
    """Tests for InvestigationPhase constants."""

    def test_all_phases_defined(self):
        assert InvestigationPhase.INITIAL == "initial"
        assert InvestigationPhase.INVESTIGATING == "investigating"
        assert InvestigationPhase.ROOT_CAUSE_FOUND == "root_cause_found"
        assert InvestigationPhase.REMEDIATING == "remediating"
        assert InvestigationPhase.REVIEWING == "reviewing"
        assert InvestigationPhase.AWAITING_APPROVAL == "awaiting_approval"
        assert InvestigationPhase.EXECUTING == "executing"
        assert InvestigationPhase.VERIFYING == "verifying"
        assert InvestigationPhase.COMPLETE == "complete"
        assert InvestigationPhase.FAILED == "failed"


# -----------------------------------------------------------------------------
# Process Method Tests
# -----------------------------------------------------------------------------


class TestOrchestratorProcess:
    """Tests for the process method and phase routing."""

    @patch.object(OrchestratorAgent, "_start_investigation")
    def test_process_routes_to_initial(self, mock_start):
        mock_start.return_value = {"status": "investigating"}

        orch = OrchestratorAgent()
        context = SharedContext(session_id="test")
        # No phase set = INITIAL

        orch.process("Investigate issue", [], context)
        mock_start.assert_called_once()

    @patch.object(OrchestratorAgent, "_continue_investigation")
    def test_process_routes_to_investigating(self, mock_continue):
        mock_continue.return_value = {"status": "investigating"}

        orch = OrchestratorAgent()
        context = SharedContext(session_id="test")
        context.metadata["phase"] = InvestigationPhase.INVESTIGATING

        orch.process("Continue", [], context)
        mock_continue.assert_called_once()

    @patch.object(OrchestratorAgent, "_start_remediation")
    def test_process_routes_to_root_cause_found(self, mock_start_rem):
        mock_start_rem.return_value = {"status": "remediating"}

        orch = OrchestratorAgent()
        context = SharedContext(session_id="test")
        context.metadata["phase"] = InvestigationPhase.ROOT_CAUSE_FOUND

        orch.process("Start fix", [], context)
        mock_start_rem.assert_called_once()

    @patch.object(OrchestratorAgent, "_continue_remediation")
    def test_process_routes_to_remediating(self, mock_continue_rem):
        mock_continue_rem.return_value = {"status": "remediating"}

        orch = OrchestratorAgent()
        context = SharedContext(session_id="test")
        context.metadata["phase"] = InvestigationPhase.REMEDIATING

        orch.process("Continue fix", [], context)
        mock_continue_rem.assert_called_once()

    @patch.object(OrchestratorAgent, "_continue_review")
    def test_process_routes_to_reviewing(self, mock_review):
        mock_review.return_value = {"status": "reviewed"}

        orch = OrchestratorAgent()
        context = SharedContext(session_id="test")
        context.metadata["phase"] = InvestigationPhase.REVIEWING

        orch.process("Review", [], context)
        mock_review.assert_called_once()

    @patch.object(OrchestratorAgent, "_handle_approval_response")
    def test_process_routes_to_awaiting_approval(self, mock_approval):
        mock_approval.return_value = {"status": "complete"}

        orch = OrchestratorAgent()
        context = SharedContext(session_id="test")
        context.metadata["phase"] = InvestigationPhase.AWAITING_APPROVAL

        orch.process("Approved", [], context)
        mock_approval.assert_called_once()

    @patch.object(OrchestratorAgent, "_synthesize_final_report")
    def test_process_routes_to_complete(self, mock_report):
        mock_report.return_value = {"status": "complete"}

        orch = OrchestratorAgent()
        context = SharedContext(session_id="test")
        context.metadata["phase"] = InvestigationPhase.COMPLETE

        orch.process("Done", [], context)
        mock_report.assert_called_once()

    @patch.object(OrchestratorAgent, "_start_investigation")
    def test_process_unknown_phase_falls_back_to_initial(self, mock_start):
        mock_start.return_value = {"status": "investigating"}

        orch = OrchestratorAgent()
        context = SharedContext(session_id="test")
        context.metadata["phase"] = "unknown_phase"

        orch.process("Unknown", [], context)
        mock_start.assert_called_once()


# -----------------------------------------------------------------------------
# Start Investigation Tests
# -----------------------------------------------------------------------------


class TestStartInvestigation:
    """Tests for _start_investigation method."""

    def test_start_investigation_sets_phase(self):
        orch = OrchestratorAgent()
        context = SharedContext(session_id="test")

        # Mock the investigator to avoid real processing
        mock_inv = MagicMock()
        mock_inv.receive_handoff.return_value = {
            "status": "success",
            "result": {},
            "messages": [],
            "tokens_used": 0,
        }
        orch._investigator = mock_inv

        orch._start_investigation("Test task", [], context)

        assert context.metadata["phase"] == InvestigationPhase.INVESTIGATING

    def test_start_investigation_adds_timeline_event(self):
        orch = OrchestratorAgent()
        context = SharedContext(session_id="test")

        mock_inv = MagicMock()
        mock_inv.receive_handoff.return_value = {
            "status": "success",
            "result": {},
            "messages": [],
            "tokens_used": 0,
        }
        orch._investigator = mock_inv

        orch._start_investigation("Test task", [], context)

        assert len(context.timeline) == 1
        assert "Investigation started" in context.timeline[0]["event"]

    def test_start_investigation_creates_handoff(self):
        orch = OrchestratorAgent()
        context = SharedContext(session_id="test")

        mock_inv = MagicMock()
        mock_inv.receive_handoff.return_value = {
            "status": "success",
            "result": {},
            "messages": [],
            "tokens_used": 0,
        }
        orch._investigator = mock_inv

        orch._start_investigation("Test task", [], context)

        assert len(context.handoffs) == 1
        assert context.handoffs[0].to_agent == AgentRole.INVESTIGATOR

    def test_start_investigation_handles_needs_tools(self):
        orch = OrchestratorAgent()
        context = SharedContext(session_id="test")

        mock_inv = MagicMock()
        mock_inv.receive_handoff.return_value = {
            "status": "needs_tools",
            "tool_calls": [{"name": "search_logs"}],
            "messages": [],
            "tokens_used": 50,
        }
        orch._investigator = mock_inv

        result = orch._start_investigation("Test", [], context)

        assert result["status"] == "needs_tools"
        assert result["agent"] == "investigator"
        assert len(result["tool_calls"]) == 1

    def test_start_investigation_advances_phase_on_root_cause(self):
        orch = OrchestratorAgent()
        context = SharedContext(session_id="test")

        mock_inv = MagicMock()
        mock_inv.receive_handoff.return_value = {
            "status": "success",
            "result": {"root_cause": "Database timeout"},
            "messages": [],
            "tokens_used": 100,
        }
        orch._investigator = mock_inv

        orch._start_investigation("Test", [], context)

        assert context.metadata["phase"] == InvestigationPhase.ROOT_CAUSE_FOUND


# -----------------------------------------------------------------------------
# Continue Investigation Tests
# -----------------------------------------------------------------------------


class TestContinueInvestigation:
    """Tests for _continue_investigation method."""

    def test_continue_investigation_calls_process(self):
        orch = OrchestratorAgent()
        context = SharedContext(session_id="test")

        mock_inv = MagicMock()
        mock_inv.process.return_value = {
            "status": "success",
            "result": {},
            "messages": [],
            "tokens_used": 0,
        }
        orch._investigator = mock_inv

        orch._continue_investigation("Continue", [], context)

        mock_inv.process.assert_called_once()

    def test_continue_investigation_handles_needs_tools(self):
        orch = OrchestratorAgent()
        context = SharedContext(session_id="test")

        mock_inv = MagicMock()
        mock_inv.process.return_value = {
            "status": "needs_tools",
            "tool_calls": [{"name": "get_metric"}],
            "messages": [],
            "tokens_used": 50,
        }
        orch._investigator = mock_inv

        result = orch._continue_investigation("Continue", [], context)

        assert result["status"] == "needs_tools"

    def test_continue_investigation_advances_on_root_cause(self):
        orch = OrchestratorAgent()
        context = SharedContext(session_id="test")

        mock_inv = MagicMock()
        mock_inv.process.return_value = {
            "status": "success",
            "result": {"root_cause": "Found it"},
            "messages": [],
            "tokens_used": 100,
        }
        orch._investigator = mock_inv

        orch._continue_investigation("Continue", [], context)

        assert context.metadata["phase"] == InvestigationPhase.ROOT_CAUSE_FOUND


# -----------------------------------------------------------------------------
# Start Remediation Tests
# -----------------------------------------------------------------------------


class TestStartRemediation:
    """Tests for _start_remediation method."""

    def test_start_remediation_sets_phase(self):
        orch = OrchestratorAgent()
        context = SharedContext(session_id="test")
        context.root_cause = "Database timeout"

        mock_rem = MagicMock()
        mock_rem.receive_handoff.return_value = {
            "status": "success",
            "result": {},
            "messages": [],
            "tokens_used": 0,
        }
        orch._remediator = mock_rem

        orch._start_remediation("Fix", [], context)

        assert context.metadata["phase"] == InvestigationPhase.REMEDIATING

    def test_start_remediation_adds_timeline_event(self):
        orch = OrchestratorAgent()
        context = SharedContext(session_id="test")
        context.root_cause = "Database timeout"

        mock_rem = MagicMock()
        mock_rem.receive_handoff.return_value = {
            "status": "success",
            "result": {},
            "messages": [],
            "tokens_used": 0,
        }
        orch._remediator = mock_rem

        orch._start_remediation("Fix", [], context)

        assert any("remediation" in e["event"].lower() for e in context.timeline)

    def test_start_remediation_creates_handoff(self):
        orch = OrchestratorAgent()
        context = SharedContext(session_id="test")
        context.root_cause = "Database timeout"

        mock_rem = MagicMock()
        mock_rem.receive_handoff.return_value = {
            "status": "success",
            "result": {},
            "messages": [],
            "tokens_used": 0,
        }
        orch._remediator = mock_rem

        orch._start_remediation("Fix", [], context)

        assert any(h.to_agent == AgentRole.REMEDIATION for h in context.handoffs)

    def test_start_remediation_advances_to_review_on_needs_review(self):
        orch = OrchestratorAgent()
        context = SharedContext(session_id="test")
        context.root_cause = "Database timeout"

        mock_rem = MagicMock()
        mock_rem.receive_handoff.return_value = {
            "status": "needs_review",
            "result": {"proposed_action": {"type": "rollback"}},
            "messages": [],
            "tokens_used": 100,
        }
        orch._remediator = mock_rem

        orch._start_remediation("Fix", [], context)

        assert context.metadata["phase"] == InvestigationPhase.REVIEWING


# -----------------------------------------------------------------------------
# Continue Remediation Tests
# -----------------------------------------------------------------------------


class TestContinueRemediation:
    """Tests for _continue_remediation method."""

    def test_continue_remediation_calls_process(self):
        orch = OrchestratorAgent()
        context = SharedContext(session_id="test")

        mock_rem = MagicMock()
        mock_rem.process.return_value = {
            "status": "success",
            "result": {},
            "messages": [],
            "tokens_used": 0,
        }
        orch._remediator = mock_rem

        orch._continue_remediation("Continue fix", [], context)

        mock_rem.process.assert_called_once()

    def test_continue_remediation_handles_needs_tools(self):
        orch = OrchestratorAgent()
        context = SharedContext(session_id="test")

        mock_rem = MagicMock()
        mock_rem.process.return_value = {
            "status": "needs_tools",
            "tool_calls": [{"name": "read_file"}],
            "messages": [],
            "tokens_used": 50,
        }
        orch._remediator = mock_rem

        result = orch._continue_remediation("Continue", [], context)

        assert result["status"] == "needs_tools"

    def test_continue_remediation_advances_to_review(self):
        orch = OrchestratorAgent()
        context = SharedContext(session_id="test")

        mock_rem = MagicMock()
        mock_rem.process.return_value = {
            "status": "needs_review",
            "result": {"proposed_action": {}},
            "messages": [],
            "tokens_used": 100,
        }
        orch._remediator = mock_rem

        orch._continue_remediation("Continue", [], context)

        assert context.metadata["phase"] == InvestigationPhase.REVIEWING


# -----------------------------------------------------------------------------
# Continue Review Tests
# -----------------------------------------------------------------------------


class TestContinueReview:
    """Tests for _continue_review method."""

    def test_continue_review_calls_reviewer(self):
        orch = OrchestratorAgent()
        context = SharedContext(session_id="test")
        context.metadata["pending_action"] = {"type": "rollback"}

        mock_rev = MagicMock()
        mock_rev.process.return_value = {
            "status": "success",
            "result": {"verdict": "approve"},
            "messages": [],
            "tokens_used": 50,
        }
        orch._reviewer = mock_rev

        orch._continue_review("Review", [], context)

        mock_rev.process.assert_called_once()

    def test_continue_review_approve_advances_to_awaiting_approval(self):
        orch = OrchestratorAgent()
        context = SharedContext(session_id="test")
        context.metadata["pending_action"] = {}

        mock_rev = MagicMock()
        mock_rev.process.return_value = {
            "status": "success",
            "result": {"verdict": "approve"},
            "messages": [],
            "tokens_used": 50,
        }
        orch._reviewer = mock_rev

        orch._continue_review("Review", [], context)

        assert context.metadata["phase"] == InvestigationPhase.AWAITING_APPROVAL

    def test_continue_review_reject_returns_to_remediating(self):
        orch = OrchestratorAgent()
        context = SharedContext(session_id="test")
        context.metadata["pending_action"] = {}

        mock_rev = MagicMock()
        mock_rev.process.return_value = {
            "status": "success",
            "result": {"verdict": "reject", "rationale": "Fix does not address root cause"},
            "messages": [],
            "tokens_used": 50,
        }
        orch._reviewer = mock_rev

        orch._continue_review("Review", [], context)

        assert context.metadata["phase"] == InvestigationPhase.REMEDIATING

    def test_continue_review_needs_clarification_returns_to_remediating(self):
        orch = OrchestratorAgent()
        context = SharedContext(session_id="test")
        context.metadata["pending_action"] = {}

        mock_rev = MagicMock()
        mock_rev.process.return_value = {
            "status": "success",
            "result": {"verdict": "needs_clarification", "rationale": "More info needed"},
            "messages": [],
            "tokens_used": 50,
        }
        orch._reviewer = mock_rev

        orch._continue_review("Review", [], context)

        assert context.metadata["phase"] == InvestigationPhase.REMEDIATING


# -----------------------------------------------------------------------------
# Handle Approval Response Tests
# -----------------------------------------------------------------------------


class TestHandleApprovalResponse:
    """Tests for _handle_approval_response method."""

    def test_handle_approval_marks_complete(self):
        orch = OrchestratorAgent()
        context = SharedContext(session_id="test")

        result = orch._handle_approval_response("Approved", [], context)

        assert context.metadata["phase"] == InvestigationPhase.COMPLETE
        assert result["status"] == "complete"


# -----------------------------------------------------------------------------
# Synthesize Final Report Tests
# -----------------------------------------------------------------------------


class TestSynthesizeFinalReport:
    """Tests for _synthesize_final_report method."""

    def test_synthesize_report_includes_context(self):
        orch = OrchestratorAgent()
        context = SharedContext(session_id="test-session")
        context.incident_summary = "Test incident"
        context.severity = "SEV2"
        context.confidence = "high"
        context.root_cause = "Database timeout"
        context.affected_services = ["checkout-svc"]
        context.add_hypothesis(
            cause="Database timeout",
            confidence="high",
            evidence=["Log evidence"],
            source_agent=AgentRole.INVESTIGATOR,
        )

        result = orch._synthesize_final_report(context)

        assert result["status"] == "complete"
        report = result["result"]
        assert report["session_id"] == "test-session"
        assert report["incident_summary"] == "Test incident"
        assert report["severity"] == "SEV2"
        assert report["root_cause"] == "Database timeout"

    def test_synthesize_report_adds_timeline_event(self):
        orch = OrchestratorAgent()
        context = SharedContext(session_id="test")

        orch._synthesize_final_report(context)

        assert any("complete" in e["event"].lower() for e in context.timeline)

    def test_synthesize_report_counts_messages_and_handoffs(self):
        orch = OrchestratorAgent()
        context = SharedContext(session_id="test")

        # Add some messages and handoffs
        context.send_message(AgentMessage(
            id="msg-1",
            from_agent=AgentRole.INVESTIGATOR,
            to_agent=AgentRole.ORCHESTRATOR,
            message_type=MessageType.RESULT,
            content={},
        ))
        context.create_handoff(AgentHandoff(
            id="handoff-1",
            from_agent=AgentRole.ORCHESTRATOR,
            to_agent=AgentRole.INVESTIGATOR,
            task="Test",
        ))

        result = orch._synthesize_final_report(context)

        assert result["result"]["agent_messages"] == 1
        assert result["result"]["handoffs"] == 1


# -----------------------------------------------------------------------------
# Route Message Tests
# -----------------------------------------------------------------------------


class TestRouteMessage:
    """Tests for route_message method."""

    def test_route_to_investigator(self):
        orch = OrchestratorAgent()
        context = SharedContext(session_id="test")

        mock_inv = MagicMock()
        mock_inv.process.return_value = {"status": "success"}
        orch._investigator = mock_inv

        message = AgentMessage(
            id="msg-1",
            from_agent=AgentRole.ORCHESTRATOR,
            to_agent=AgentRole.INVESTIGATOR,
            message_type=MessageType.TASK,
            content={"task": "Investigate"},
        )

        result = orch.route_message(message, context)

        mock_inv.process.assert_called_once()
        assert result["status"] == "success"

    def test_route_to_remediation(self):
        orch = OrchestratorAgent()
        context = SharedContext(session_id="test")

        mock_rem = MagicMock()
        mock_rem.process.return_value = {"status": "success"}
        orch._remediator = mock_rem

        message = AgentMessage(
            id="msg-1",
            from_agent=AgentRole.ORCHESTRATOR,
            to_agent=AgentRole.REMEDIATION,
            message_type=MessageType.TASK,
            content={"task": "Fix"},
        )

        orch.route_message(message, context)

        mock_rem.process.assert_called_once()

    def test_route_to_reviewer(self):
        orch = OrchestratorAgent()
        context = SharedContext(session_id="test")

        mock_rev = MagicMock()
        mock_rev.process.return_value = {"status": "success"}
        orch._reviewer = mock_rev

        message = AgentMessage(
            id="msg-1",
            from_agent=AgentRole.ORCHESTRATOR,
            to_agent=AgentRole.REVIEWER,
            message_type=MessageType.TASK,
            content={"task": "Review"},
        )

        orch.route_message(message, context)

        mock_rev.process.assert_called_once()

    def test_route_unknown_agent_fails(self):
        orch = OrchestratorAgent()
        context = SharedContext(session_id="test")

        message = AgentMessage(
            id="msg-1",
            from_agent=AgentRole.INVESTIGATOR,
            to_agent=AgentRole.ORCHESTRATOR,  # Routing to self
            message_type=MessageType.TASK,
            content={},
        )

        result = orch.route_message(message, context)

        assert result["status"] == "failed"


# -----------------------------------------------------------------------------
# Get Status Tests
# -----------------------------------------------------------------------------


class TestGetStatus:
    """Tests for get_status method."""

    def test_get_status_basic(self):
        orch = OrchestratorAgent()
        context = SharedContext(session_id="test-session")

        status = orch.get_status(context)

        assert status["session_id"] == "test-session"
        assert status["phase"] == InvestigationPhase.INITIAL
        assert status["root_cause_found"] is False

    def test_get_status_with_root_cause(self):
        orch = OrchestratorAgent()
        context = SharedContext(session_id="test")
        context.root_cause = "Database timeout"

        status = orch.get_status(context)

        assert status["root_cause_found"] is True

    def test_get_status_counts_evidence(self):
        orch = OrchestratorAgent()
        context = SharedContext(session_id="test")
        context.add_evidence("logs", ["log1", "log2"])
        context.add_evidence("metrics", ["metric1"])

        status = orch.get_status(context)

        assert status["evidence_count"] == 3

    def test_get_status_counts_hypotheses(self):
        orch = OrchestratorAgent()
        context = SharedContext(session_id="test")
        context.add_hypothesis(
            cause="Cause 1",
            confidence="high",
            evidence=[],
            source_agent=AgentRole.INVESTIGATOR,
        )
        context.add_hypothesis(
            cause="Cause 2",
            confidence="low",
            evidence=[],
            source_agent=AgentRole.INVESTIGATOR,
        )

        status = orch.get_status(context)

        assert status["hypotheses_count"] == 2

    def test_get_status_counts_pending_handoffs(self):
        orch = OrchestratorAgent()
        context = SharedContext(session_id="test")
        context.create_handoff(AgentHandoff(
            id="h1",
            from_agent=AgentRole.ORCHESTRATOR,
            to_agent=AgentRole.INVESTIGATOR,
            task="Test",
            status=TaskStatus.PENDING,
        ))
        context.create_handoff(AgentHandoff(
            id="h2",
            from_agent=AgentRole.ORCHESTRATOR,
            to_agent=AgentRole.REMEDIATION,
            task="Test",
            status=TaskStatus.COMPLETED,
        ))

        status = orch.get_status(context)

        assert status["pending_handoffs"] == 1
        assert status["completed_handoffs"] == 1

    def test_get_status_counts_messages(self):
        orch = OrchestratorAgent()
        context = SharedContext(session_id="test")
        context.send_message(AgentMessage(
            id="m1",
            from_agent=AgentRole.INVESTIGATOR,
            to_agent=AgentRole.ORCHESTRATOR,
            message_type=MessageType.RESULT,
            content={},
        ))

        status = orch.get_status(context)

        assert status["messages_exchanged"] == 1
