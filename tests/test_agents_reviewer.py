"""Comprehensive tests for app/agents/reviewer.py.

Tests ReviewerAgent initialization, review parsing, quick checks,
and the review process.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from langchain_core.messages import AIMessage

from app.agents.reviewer import ReviewerAgent
from app.agents.protocol import AgentRole, MessageType, SharedContext


class TestReviewerAgentInitialization:
    """Tests for ReviewerAgent initialization."""

    def test_init_with_no_tools(self):
        agent = ReviewerAgent()
        assert agent._tools == []
        assert agent._fast is True

    def test_init_with_tools(self):
        mock_tool = MagicMock(name="test_tool")
        agent = ReviewerAgent(tools=[mock_tool])
        assert len(agent._tools) == 1

    def test_init_defaults_to_fast_mode(self):
        agent = ReviewerAgent()
        assert agent._fast is True


class TestReviewerAgentProperties:
    """Tests for ReviewerAgent property accessors."""

    def test_role_property(self):
        agent = ReviewerAgent()
        assert agent.role == AgentRole.REVIEWER

    def test_system_prompt_property(self):
        agent = ReviewerAgent()
        assert "ReviewerAgent" in agent.system_prompt

    def test_name_property(self):
        agent = ReviewerAgent()
        assert agent.name == "ReviewerAgent"


class TestFormatReviewPrompt:
    """Tests for _format_review_prompt method."""

    def test_format_prompt_basic(self):
        agent = ReviewerAgent()
        prompt = agent._format_review_prompt(
            action="Rollback",
            root_cause="Bad deploy",
            evidence="Error spike",
        )
        assert "Rollback" in prompt
        assert "Bad deploy" in prompt


class TestParseReviewResultJson:
    """Tests for _parse_review_result with JSON responses."""

    def test_parse_json_approve(self):
        agent = ReviewerAgent()
        content = json.dumps({"verdict": "approve", "confidence": "high"})
        result = agent._parse_review_result(content)
        assert result["verdict"] == "approve"
        assert result["confidence"] == "high"

    def test_parse_json_reject(self):
        agent = ReviewerAgent()
        content = json.dumps({"verdict": "reject", "concerns": ["Risk"]})
        result = agent._parse_review_result(content)
        assert result["verdict"] == "reject"

    def test_parse_json_needs_clarification(self):
        agent = ReviewerAgent()
        content = json.dumps({"verdict": "needs_clarification"})
        result = agent._parse_review_result(content)
        assert result["verdict"] == "needs_clarification"


class TestParseReviewResultText:
    """Tests for _parse_review_result with text responses."""

    def test_parse_text_approve(self):
        agent = ReviewerAgent()
        content = "I approve this fix."
        result = agent._parse_review_result(content)
        assert result["verdict"] == "approve"

    def test_parse_text_reject(self):
        agent = ReviewerAgent()
        content = "I reject this fix."
        result = agent._parse_review_result(content)
        assert result["verdict"] == "reject"


class TestQuickCheck:
    """Tests for quick_check method."""

    def test_quick_check_no_root_cause(self):
        agent = ReviewerAgent()
        result = agent.quick_check(
            action={"type": "create_pull_request"},
            root_cause=None,
            evidence_count=5,
        )
        assert result["recommendation"] == "block"
        assert result["has_root_cause"] is False

    def test_quick_check_insufficient_evidence(self):
        agent = ReviewerAgent()
        result = agent.quick_check(
            action={"type": "scale_deployment"},
            root_cause="Issue",
            evidence_count=1,
        )
        assert result["recommendation"] == "block"

    def test_quick_check_passes(self):
        agent = ReviewerAgent()
        result = agent.quick_check(
            action={"type": "create_pull_request"},
            root_cause="Null check missing",
            evidence_count=5,
        )
        assert result["recommendation"] == "proceed_to_review"
        assert result["has_root_cause"] is True

    def test_quick_check_production_scale(self):
        agent = ReviewerAgent()
        result = agent.quick_check(
            action={"type": "scale_deployment", "namespace": "production"},
            root_cause="High traffic",
            evidence_count=5,
        )
        assert any("production" in c.lower() for c in result["concerns"])

    def test_quick_check_rollback_without_revision(self):
        agent = ReviewerAgent()
        result = agent.quick_check(
            action={"type": "rollback_deployment"},
            root_cause="Bad deploy",
            evidence_count=5,
        )
        assert any("Rollback without" in c for c in result["concerns"])


class TestReviewerProcess:
    """Tests for the process method."""

    @patch.object(ReviewerAgent, "invoke")
    @patch.object(ReviewerAgent, "log_usage")
    @patch.object(ReviewerAgent, "send_message")
    def test_process_basic(self, mock_send, mock_log, mock_invoke):
        mock_response = AIMessage(content=json.dumps({
            "verdict": "approve",
            "confidence": "high",
        }))
        mock_invoke.return_value = mock_response
        mock_log.return_value = 50

        agent = ReviewerAgent()
        context = SharedContext(session_id="test")
        result = agent.process("Review", [], context)

        assert result["status"] == "success"
        assert result["result"]["verdict"] == "approve"

    @patch.object(ReviewerAgent, "invoke")
    @patch.object(ReviewerAgent, "log_usage")
    @patch.object(ReviewerAgent, "send_message")
    def test_process_adds_timeline_event(self, mock_send, mock_log, mock_invoke):
        mock_response = AIMessage(content=json.dumps({"verdict": "reject"}))
        mock_invoke.return_value = mock_response
        mock_log.return_value = 50

        agent = ReviewerAgent()
        context = SharedContext(session_id="test")
        agent.process("Review", [], context)

        assert len(context.timeline) == 1
        assert "Review verdict" in context.timeline[0]["event"]

    @patch.object(ReviewerAgent, "invoke")
    @patch.object(ReviewerAgent, "log_usage")
    @patch.object(ReviewerAgent, "send_message")
    def test_process_sends_result(self, mock_send, mock_log, mock_invoke):
        mock_response = AIMessage(content=json.dumps({"verdict": "approve"}))
        mock_invoke.return_value = mock_response
        mock_log.return_value = 50

        agent = ReviewerAgent()
        context = SharedContext(session_id="test")
        agent.process("Review", [], context)

        call_args = mock_send.call_args
        assert call_args[1]["to_agent"] == AgentRole.ORCHESTRATOR
        assert call_args[1]["message_type"] == MessageType.RESULT
