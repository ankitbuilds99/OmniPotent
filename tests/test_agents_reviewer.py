"""Comprehensive tests for app/agents/reviewer.py.

Tests ReviewerAgent initialization, review parsing, quick checks,
and the review process.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from langchain_core.messages import AIMessage, HumanMessage

from app.agents.reviewer import ReviewerAgent
from app.agents.protocol import AgentRole, MessageType, SharedContext


# -----------------------------------------------------------------------------
# Initialization Tests
# -----------------------------------------------------------------------------


class TestReviewerAgentInitialization:
    """Tests for ReviewerAgent initialization."""

    def test_init_with_no_tools(self):
        agent = ReviewerAgent()
        assert agent._tools == []
        assert agent._fast is True  # Default is fast mode

    def test_init_with_tools(self):
        mock_tool = MagicMock(name="test_tool")
        agent = ReviewerAgent(tools=[mock_tool])
        assert len(agent._tools) == 1

    def test_init_defaults_to_fast_mode(self):
        agent = ReviewerAgent()
        assert agent._fast is True

    def test_init_with_fast_false(self):
        agent = ReviewerAgent(fast=False)
        assert agent._fast is False


class TestReviewerAgentProperties:
    """Tests for ReviewerAgent property accessors."""

    def test_role_property(self):
        agent = ReviewerAgent()
        assert agent.role == AgentRole.REVIEWER

    def test_system_prompt_property(self):
        agent = ReviewerAgent()
        assert "ReviewerAgent" in agent.system_prompt
        assert "validating" in agent.system_prompt.lower()

    def test_name_property(self):
        agent = ReviewerAgent()
        assert agent.name == "ReviewerAgent"


# -----------------------------------------------------------------------------
# Format Review Prompt Tests
# -----------------------------------------------------------------------------


class TestFormatReviewPrompt:
    """Tests for _format_review_prompt method."""

    def test_format_prompt_basic(self):
        agent = ReviewerAgent()
        prompt = agent._format_review_prompt(
            action="Rollback deployment to revision 5",
            root_cause="Bad deploy",
            evidence="Error spike after deploy",
        )

        assert "Rollback deployment" in prompt
        assert "Bad deploy" in prompt
        assert "Error spike" in prompt

    def test_format_prompt_includes_all_sections(self):
        agent = ReviewerAgent()
        prompt = agent._format_review_prompt(
            action="Test action",
            root_cause="Test cause",
            evidence="Test evidence",
        )

        assert "Proposed action to review:" in prompt
        assert "Root cause" in prompt
        assert "Evidence" in prompt


# -----------------------------------------------------------------------------
# Parse Review Result Tests (JSON)
# -----------------------------------------------------------------------------


class TestParseReviewResultJson:
    """Tests for _parse_review_result with JSON responses."""

    def test_parse_json_approve(self):
        agent = ReviewerAgent()
        content = json.dumps({
            "verdict": "approve",
            "confidence": "high",
            "rationale": "Fix addresses root cause",
            "concerns": [],
            "conditions": ["Monitor error rate"],
        })

        result = agent._parse_review_result(content)

        assert result["verdict"] == "approve"
        assert result["confidence"] == "high"
        assert result["rationale"] == "Fix addresses root cause"

    def test_parse_json_reject(self):
        agent = ReviewerAgent()
        content = json.dumps({
            "verdict": "reject",
            "confidence": "medium",
            "rationale": "Fix does not address the root cause",
            "concerns": ["Blast radius too high"],
        })

        result = agent._parse_review_result(content)

        assert result["verdict"] == "reject"
        assert len(result["concerns"]) == 1

    def test_parse_json_needs_clarification(self):
        agent = ReviewerAgent()
        content = json.dumps({
            "verdict": "needs_clarification",
            "confidence": "low",
            "rationale": "Need more information about the deploy",
        })

        result = agent._parse_review_result(content)

        assert result["verdict"] == "needs_clarification"

    def test_parse_json_approved_variant(self):
        agent = ReviewerAgent()
        content = json.dumps({"verdict": "approved"})
        result = agent._parse_review_result(content)
        assert result["verdict"] == "approve"

    def test_parse_json_yes_variant(self):
        agent = ReviewerAgent()
        content = json.dumps({"verdict": "yes"})
        result = agent._parse_review_result(content)
        assert result["verdict"] == "approve"

    def test_parse_json_accept_variant(self):
        agent = ReviewerAgent()
        content = json.dumps({"verdict": "accept"})
        result = agent._parse_review_result(content)
        assert result["verdict"] == "approve"

    def test_parse_json_clarify_variant(self):
        agent = ReviewerAgent()
        content = json.dumps({"verdict": "clarify"})
        result = agent._parse_review_result(content)
        assert result["verdict"] == "needs_clarification"

    def test_parse_json_with_arrays(self):
        agent = ReviewerAgent()
        content = json.dumps({
            "verdict": "approve",
            "confidence": "high",
            "concerns": ["Risk 1", "Risk 2"],
            "alternatives": ["Alternative 1"],
            "conditions": ["Condition 1"],
            "monitoring": ["Watch error rate"],
        })

        result = agent._parse_review_result(content)

        assert len(result["concerns"]) == 2
        assert len(result["alternatives"]) == 1
        assert len(result["conditions"]) == 1
        assert len(result["monitoring"]) == 1

    def test_parse_json_truncates_long_items(self):
        agent = ReviewerAgent()
        long_concern = "X" * 500
        content = json.dumps({
            "verdict": "reject",
            "concerns": [long_concern],
        })

        result = agent._parse_review_result(content)

        # Should truncate to 200 chars
        assert len(result["concerns"][0]) == 200

    def test_parse_json_limits_array_size(self):
        agent = ReviewerAgent()
        many_concerns = [f"Concern {i}" for i in range(20)]
        content = json.dumps({
            "verdict": "reject",
            "concerns": many_concerns,
        })

        result = agent._parse_review_result(content)

        # Should limit to 10 items
        assert len(result["concerns"]) == 10

    def test_parse_json_truncates_rationale(self):
        agent = ReviewerAgent()
        long_rationale = "X" * 1000
        content = json.dumps({
            "verdict": "approve",
            "rationale": long_rationale,
        })

        result = agent._parse_review_result(content)

        assert len(result["rationale"]) == 500


# -----------------------------------------------------------------------------
# Parse Review Result Tests (Text Fallback)
# -----------------------------------------------------------------------------


class TestParseReviewResultText:
    """Tests for _parse_review_result with text (non-JSON) responses."""

    def test_parse_text_approve(self):
        agent = ReviewerAgent()
        content = "I approve this fix because it addresses the root cause."

        result = agent._parse_review_result(content)

        assert result["verdict"] == "approve"

    def test_parse_text_reject(self):
        agent = ReviewerAgent()
        content = "I reject this fix because it doesn't address the issue."

        result = agent._parse_review_result(content)

        assert result["verdict"] == "reject"

    def test_parse_text_needs_clarification(self):
        agent = ReviewerAgent()
        content = "I need more information before approving. Need more information about the deploy."

        result = agent._parse_review_result(content)

        assert result["verdict"] == "needs_clarification"

    def test_parse_text_needs_clarification_variant(self):
        agent = ReviewerAgent()
        content = "This needs clarification. Please provide more details."

        result = agent._parse_review_result(content)

        assert result["verdict"] == "needs_clarification"

    def test_parse_text_extracts_confidence(self):
        agent = ReviewerAgent()
        content = "With high confidence, I approve this fix."

        result = agent._parse_review_result(content)

        assert result["confidence"] == "high"

    def test_parse_text_extracts_medium_confidence(self):
        agent = ReviewerAgent()
        content = "I have medium confidence in this assessment."

        result = agent._parse_review_result(content)

        assert result["confidence"] == "medium"

    def test_parse_text_extracts_rationale(self):
        agent = ReviewerAgent()
        content = """
        I approve this fix.

        The fix correctly addresses the root cause by adding the missing null check.

        Some concerns remain.
        """

        result = agent._parse_review_result(content)

        assert result["rationale"] is not None
        assert len(result["rationale"]) > 0

    def test_parse_text_default_reject(self):
        agent = ReviewerAgent()
        # No clear approve/reject markers
        content = "The fix is questionable."

        result = agent._parse_review_result(content)

        # Should default to reject
        assert result["verdict"] == "reject"

    def test_parse_text_default_values(self):
        agent = ReviewerAgent()
        content = "Simple response."

        result = agent._parse_review_result(content)

        assert result["verdict"] == "reject"
        assert result["confidence"] == "low"
        assert result["concerns"] == []
        assert result["alternatives"] == []


# -----------------------------------------------------------------------------
# Quick Check Tests
# -----------------------------------------------------------------------------


class TestQuickCheck:
    """Tests for quick_check method (rule-based pre-check)."""

    def test_quick_check_no_root_cause(self):
        agent = ReviewerAgent()
        result = agent.quick_check(
            action={"type": "create_pull_request"},
            root_cause=None,
            evidence_count=5,
        )

        assert result["recommendation"] == "block"
        assert any("No root cause" in c for c in result["concerns"])
        assert result["has_root_cause"] is False

    def test_quick_check_empty_root_cause(self):
        agent = ReviewerAgent()
        result = agent.quick_check(
            action={"type": "rollback_deployment"},
            root_cause="",
            evidence_count=5,
        )

        assert result["recommendation"] == "block"

    def test_quick_check_insufficient_evidence(self):
        agent = ReviewerAgent()
        result = agent.quick_check(
            action={"type": "scale_deployment"},
            root_cause="Resource exhaustion",
            evidence_count=1,
        )

        assert result["recommendation"] == "block"
        assert any("Insufficient evidence" in c for c in result["concerns"])

    def test_quick_check_zero_evidence(self):
        agent = ReviewerAgent()
        result = agent.quick_check(
            action={"type": "restart_deployment"},
            root_cause="Memory leak",
            evidence_count=0,
        )

        assert result["recommendation"] == "block"

    def test_quick_check_production_scale(self):
        agent = ReviewerAgent()
        result = agent.quick_check(
            action={"type": "scale_deployment", "namespace": "production"},
            root_cause="High traffic",
            evidence_count=5,
        )

        assert any("production" in c.lower() for c in result["concerns"])

    def test_quick_check_production_restart(self):
        agent = ReviewerAgent()
        result = agent.quick_check(
            action={"type": "restart_deployment", "deployment": "prod-checkout"},
            root_cause="Memory leak",
            evidence_count=5,
        )

        assert any("production" in c.lower() for c in result["concerns"])

    def test_quick_check_rollback_without_revision(self):
        agent = ReviewerAgent()
        result = agent.quick_check(
            action={"type": "rollback_deployment"},  # No revision specified
            root_cause="Bad deploy",
            evidence_count=5,
        )

        assert any("Rollback without specific revision" in c for c in result["concerns"])

    def test_quick_check_rollback_with_revision(self):
        agent = ReviewerAgent()
        result = agent.quick_check(
            action={"type": "rollback_deployment", "revision": "5"},
            root_cause="Bad deploy",
            evidence_count=5,
        )

        # Should not have rollback concern
        assert not any("Rollback without" in c for c in result["concerns"])

    def test_quick_check_passes(self):
        agent = ReviewerAgent()
        result = agent.quick_check(
            action={"type": "create_pull_request"},
            root_cause="Null check missing in checkout.js:42",
            evidence_count=5,
        )

        assert result["recommendation"] == "proceed_to_review"
        assert result["has_root_cause"] is True
        assert result["evidence_count"] == 5

    def test_quick_check_multiple_concerns(self):
        agent = ReviewerAgent()
        result = agent.quick_check(
            action={"type": "scale_deployment", "namespace": "production"},
            root_cause=None,
            evidence_count=0,
        )

        # Should have multiple concerns
        assert len(result["concerns"]) >= 2
        assert result["recommendation"] == "block"


# -----------------------------------------------------------------------------
# Process Method Tests
# -----------------------------------------------------------------------------


class TestReviewerProcess:
    """Tests for the process method."""

    @patch.object(ReviewerAgent, "invoke")
    @patch.object(ReviewerAgent, "log_usage")
    @patch.object(ReviewerAgent, "send_message")
    def test_process_basic(self, mock_send, mock_log, mock_invoke):
        mock_response = AIMessage(content=json.dumps({
            "verdict": "approve",
            "confidence": "high",
            "rationale": "Looks good",
        }))
        mock_invoke.return_value = mock_response
        mock_log.return_value = 50

        agent = ReviewerAgent()
        context = SharedContext(session_id="test")
        context.root_cause = "Database timeout"
        messages = [HumanMessage(content="Review this fix")]

        result = agent.process("Review fix", messages, context)

        assert result["status"] == "success"
        assert result["result"]["verdict"] == "approve"

    @patch.object(ReviewerAgent, "invoke")
    @patch.object(ReviewerAgent, "log_usage")
    @patch.object(ReviewerAgent, "send_message")
    def test_process_adds_timeline_event(self, mock_send, mock_log, mock_invoke):
        mock_response = AIMessage(content=json.dumps({
            "verdict": "reject",
            "rationale": "Fix is incomplete",
        }))
        mock_invoke.return_value = mock_response
        mock_log.return_value = 50

        agent = ReviewerAgent()
        context = SharedContext(session_id="test")

        agent.process("Review", [], context)

        assert len(context.timeline) == 1
        assert "Review verdict" in context.timeline[0]["event"]
        assert "reject" in context.timeline[0]["event"].lower()

    @patch.object(ReviewerAgent, "invoke")
    @patch.object(ReviewerAgent, "log_usage")
    @patch.object(ReviewerAgent, "send_message")
    def test_process_sends_result_to_orchestrator(self, mock_send, mock_log, mock_invoke):
        mock_response = AIMessage(content=json.dumps({
            "verdict": "approve",
            "confidence": "high",
            "rationale": "Approved",
            "concerns": [],
            "conditions": ["Monitor metrics"],
            "monitoring": ["Error rate"],
        }))
        mock_invoke.return_value = mock_response
        mock_log.return_value = 50

        agent = ReviewerAgent()
        context = SharedContext(session_id="test")

        agent.process("Review", [], context)

        mock_send.assert_called_once()
        call_args = mock_send.call_args
        assert call_args[1]["to_agent"] == AgentRole.ORCHESTRATOR
        assert call_args[1]["message_type"] == MessageType.RESULT
        assert call_args[1]["content"]["phase"] == "review_complete"
        assert call_args[1]["content"]["verdict"] == "approve"

    @patch.object(ReviewerAgent, "invoke")
    @patch.object(ReviewerAgent, "log_usage")
    @patch.object(ReviewerAgent, "send_message")
    def test_process_uses_root_cause_from_context(self, mock_send, mock_log, mock_invoke):
        mock_response = AIMessage(content='{"verdict": "approve"}')
        mock_invoke.return_value = mock_response
        mock_log.return_value = 50

        agent = ReviewerAgent()
        context = SharedContext(session_id="test")
        context.root_cause = "Memory leak in worker process"

        agent.process("Review", [], context)

        call_args = mock_invoke.call_args
        volatile = call_args[1]["volatile_prompt"]
        assert "Memory leak" in volatile

    @patch.object(ReviewerAgent, "invoke")
    @patch.object(ReviewerAgent, "log_usage")
    @patch.object(ReviewerAgent, "send_message")
    def test_process_uses_evidence_from_context(self, mock_send, mock_log, mock_invoke):
        mock_response = AIMessage(content='{"verdict": "approve"}')
        mock_invoke.return_value = mock_response
        mock_log.return_value = 50

        agent = ReviewerAgent()
        context = SharedContext(session_id="test")
        context.add_evidence("logs", ["OOM killed process"])

        agent.process("Review", [], context)

        call_args = mock_invoke.call_args
        volatile = call_args[1]["volatile_prompt"]
        assert "OOM" in volatile

    @patch.object(ReviewerAgent, "invoke")
    @patch.object(ReviewerAgent, "log_usage")
    @patch.object(ReviewerAgent, "send_message")
    def test_process_returns_tokens_used(self, mock_send, mock_log, mock_invoke):
        mock_response = AIMessage(content='{"verdict": "approve"}')
        mock_invoke.return_value = mock_response
        mock_log.return_value = 75

        agent = ReviewerAgent()
        context = SharedContext(session_id="test")

        result = agent.process("Review", [], context)

        assert result["tokens_used"] == 75

    @patch.object(ReviewerAgent, "invoke")
    @patch.object(ReviewerAgent, "log_usage")
    @patch.object(ReviewerAgent, "send_message")
    def test_process_handles_missing_root_cause(self, mock_send, mock_log, mock_invoke):
        mock_response = AIMessage(content='{"verdict": "reject"}')
        mock_invoke.return_value = mock_response
        mock_log.return_value = 50

        agent = ReviewerAgent()
        context = SharedContext(session_id="test")
        # No root_cause set

        agent.process("Review", [], context)

        call_args = mock_invoke.call_args
        volatile = call_args[1]["volatile_prompt"]
        assert "Not identified" in volatile


# -----------------------------------------------------------------------------
# Edge Case Tests
# -----------------------------------------------------------------------------


class TestReviewerEdgeCases:
    """Tests for edge cases and error handling."""

    def test_parse_json_with_text_before(self):
        agent = ReviewerAgent()
        content = """Here is my review:

        {"verdict": "approve", "confidence": "high"}

        That's my assessment.
        """

        result = agent._parse_review_result(content)

        assert result["verdict"] == "approve"

    def test_parse_malformed_json(self):
        agent = ReviewerAgent()
        content = '{"verdict": "approve", "confidence": }'  # Invalid JSON

        result = agent._parse_review_result(content)

        # Should fall back to text parsing
        assert result["verdict"] == "approve"

    def test_parse_empty_content(self):
        agent = ReviewerAgent()
        result = agent._parse_review_result("")

        assert result["verdict"] == "reject"  # Default
        assert result["confidence"] == "low"

    def test_parse_json_not_dict(self):
        agent = ReviewerAgent()
        content = '["approve"]'  # Valid JSON but not a dict

        result = agent._parse_review_result(content)

        # Should fall back to text parsing
        assert result["verdict"] == "reject"

    def test_parse_handles_unicode(self):
        agent = ReviewerAgent()
        content = json.dumps({
            "verdict": "approve",
            "rationale": "Unicode test: fixed issue"
        })

        result = agent._parse_review_result(content)

        assert result["verdict"] == "approve"

    @patch.object(ReviewerAgent, "invoke")
    @patch.object(ReviewerAgent, "log_usage")
    @patch.object(ReviewerAgent, "send_message")
    def test_process_returns_messages(self, mock_send, mock_log, mock_invoke):
        mock_response = AIMessage(content='{"verdict": "approve"}')
        mock_invoke.return_value = mock_response
        mock_log.return_value = 50

        agent = ReviewerAgent()
        context = SharedContext(session_id="test")

        result = agent.process("Review", [], context)

        assert len(result["messages"]) == 1
        assert result["messages"][0] == mock_response

    def test_quick_check_returns_all_fields(self):
        agent = ReviewerAgent()
        result = agent.quick_check(
            action={"type": "test"},
            root_cause="Test cause",
            evidence_count=5,
        )

        assert "recommendation" in result
        assert "concerns" in result
        assert "evidence_count" in result
        assert "has_root_cause" in result

    def test_parse_text_skips_json_lines(self):
        agent = ReviewerAgent()
        content = """
        {invalid json that looks like json}

        I approve this fix because it's safe.
        """

        result = agent._parse_review_result(content)

        # Should extract rationale from the prose line
        assert result["verdict"] == "approve"

    def test_parse_text_skips_list_markers(self):
        agent = ReviewerAgent()
        content = """
        - Point 1
        * Point 2
        1. Point 3

        I approve this fix with high confidence.
        """

        result = agent._parse_review_result(content)

        assert result["verdict"] == "approve"
