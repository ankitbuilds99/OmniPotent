"""Comprehensive tests for app/agents/remediation.py.

Tests RemediationAgent initialization, tool filtering, fix proposal,
result parsing, and remediation process.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from langchain_core.messages import AIMessage, HumanMessage

from app.agents.remediation import RemediationAgent, _READ_TOOLS, _WRITE_TOOLS
from app.agents.protocol import AgentRole, MessageType, SharedContext


# -----------------------------------------------------------------------------
# Initialization Tests
# -----------------------------------------------------------------------------


class TestRemediationAgentInitialization:
    """Tests for RemediationAgent initialization."""

    def test_init_with_no_tools(self):
        agent = RemediationAgent()
        assert agent._tools == []
        assert agent._fast is False

    def test_init_filters_remediation_tools(self):
        # Create mock tools with names
        mock_read_file = MagicMock()
        mock_read_file.name = "read_file"

        mock_create_pr = MagicMock()
        mock_create_pr.name = "create_pull_request"

        mock_search_logs = MagicMock()
        mock_search_logs.name = "search_logs"

        mock_unknown = MagicMock()
        mock_unknown.name = "unknown_tool"

        tools = [mock_read_file, mock_create_pr, mock_search_logs, mock_unknown]
        agent = RemediationAgent(tools=tools)

        # Should include read_file and create_pull_request, exclude others
        tool_names = [t.name for t in agent._tools]
        assert "read_file" in tool_names
        assert "create_pull_request" in tool_names
        assert "search_logs" not in tool_names
        assert "unknown_tool" not in tool_names

    def test_init_falls_back_to_all_tools_if_filter_empty(self):
        mock_unknown = MagicMock()
        mock_unknown.name = "unknown_tool"

        tools = [mock_unknown]
        agent = RemediationAgent(tools=tools)

        # Should fall back to all tools
        assert len(agent._tools) == 1

    def test_init_with_fast_mode(self):
        agent = RemediationAgent(fast=True)
        assert agent._fast is True


class TestRemediationAgentProperties:
    """Tests for RemediationAgent property accessors."""

    def test_role_property(self):
        agent = RemediationAgent()
        assert agent.role == AgentRole.REMEDIATION

    def test_system_prompt_property(self):
        agent = RemediationAgent()
        assert "RemediationAgent" in agent.system_prompt
        assert "fix" in agent.system_prompt.lower()

    def test_name_property(self):
        agent = RemediationAgent()
        assert agent.name == "RemediationAgent"


class TestRemediationToolSets:
    """Tests for tool set configurations."""

    def test_write_tools_defined(self):
        assert "create_pull_request" in _WRITE_TOOLS
        assert "scale_deployment" in _WRITE_TOOLS
        assert "rollback_deployment" in _WRITE_TOOLS
        assert "restart_deployment" in _WRITE_TOOLS
        assert "acknowledge_incident" in _WRITE_TOOLS
        assert "resolve_incident" in _WRITE_TOOLS
        assert "add_incident_note" in _WRITE_TOOLS

    def test_read_tools_defined(self):
        assert "read_file" in _READ_TOOLS
        assert "list_dir" in _READ_TOOLS
        assert "grep" in _READ_TOOLS
        assert "git_log" in _READ_TOOLS
        assert "list_recent_commits" in _READ_TOOLS
        assert "get_commit_diff" in _READ_TOOLS
        assert "get_deployment_status" in _READ_TOOLS
        assert "rollout_history" in _READ_TOOLS
        assert "get_metric" in _READ_TOOLS
        assert "search_logs" in _READ_TOOLS

    def test_tool_sets_disjoint(self):
        # Write and read tools should not overlap
        assert _WRITE_TOOLS.isdisjoint(_READ_TOOLS)


# -----------------------------------------------------------------------------
# Format Remediation Prompt Tests
# -----------------------------------------------------------------------------


class TestFormatRemediationPrompt:
    """Tests for _format_remediation_prompt method."""

    def test_format_prompt_basic(self):
        agent = RemediationAgent()
        prompt = agent._format_remediation_prompt(
            task="Fix the bug",
            root_cause="Null pointer",
            evidence="Log shows NullPointerException",
        )

        assert "Fix the bug" in prompt
        assert "Null pointer" in prompt
        assert "NullPointerException" in prompt

    def test_format_prompt_includes_all_sections(self):
        agent = RemediationAgent()
        prompt = agent._format_remediation_prompt(
            task="Test task",
            root_cause="Test cause",
            evidence="Test evidence",
        )

        assert "Task:" in prompt
        assert "Root cause to address:" in prompt
        assert "Evidence from investigation:" in prompt


# -----------------------------------------------------------------------------
# Format Proposed Action Tests
# -----------------------------------------------------------------------------


class TestFormatProposedAction:
    """Tests for _format_proposed_action method."""

    def test_format_pull_request_action(self):
        agent = RemediationAgent()
        tool_calls = [{
            "name": "create_pull_request",
            "args": {
                "title": "Fix: Add null check",
                "body": "This PR fixes the null pointer issue",
                "patch": "diff --git a/...",
                "head": "fix/null-check",
            },
        }]

        result = agent._format_proposed_action(tool_calls)

        assert result["count"] == 1
        assert result["has_code_change"] is True
        assert result["has_infra_change"] is False
        assert result["actions"][0]["type"] == "pull_request"
        assert result["actions"][0]["title"] == "Fix: Add null check"
        assert result["actions"][0]["has_patch"] is True

    def test_format_scale_deployment_action(self):
        agent = RemediationAgent()
        tool_calls = [{
            "name": "scale_deployment",
            "args": {
                "deployment": "checkout-svc",
                "namespace": "production",
                "replicas": 5,
            },
        }]

        result = agent._format_proposed_action(tool_calls)

        assert result["has_infra_change"] is True
        assert result["actions"][0]["type"] == "scale_deployment"
        assert result["actions"][0]["replicas"] == 5

    def test_format_rollback_deployment_action(self):
        agent = RemediationAgent()
        tool_calls = [{
            "name": "rollback_deployment",
            "args": {
                "name": "checkout-svc",
                "namespace": "production",
                "revision": "5",
            },
        }]

        result = agent._format_proposed_action(tool_calls)

        assert result["actions"][0]["type"] == "rollback_deployment"
        assert result["actions"][0]["revision"] == "5"

    def test_format_restart_deployment_action(self):
        agent = RemediationAgent()
        tool_calls = [{
            "name": "restart_deployment",
            "args": {
                "deployment": "api-gateway",
                "namespace": "default",
            },
        }]

        result = agent._format_proposed_action(tool_calls)

        assert result["actions"][0]["type"] == "restart_deployment"

    def test_format_incident_actions(self):
        agent = RemediationAgent()
        tool_calls = [
            {
                "name": "acknowledge_incident",
                "args": {"incident_id": "INC-123"},
            },
            {
                "name": "add_incident_note",
                "args": {"id": "INC-123", "note": "Fix deployed"},
            },
            {
                "name": "resolve_incident",
                "args": {"incident_id": "INC-123", "message": "Issue resolved"},
            },
        ]

        result = agent._format_proposed_action(tool_calls)

        assert result["count"] == 3
        types = [a["type"] for a in result["actions"]]
        assert "acknowledge_incident" in types
        assert "add_incident_note" in types
        assert "resolve_incident" in types

    def test_format_unknown_action(self):
        agent = RemediationAgent()
        tool_calls = [{
            "name": "custom_action",
            "args": {"key": "value"},
        }]

        result = agent._format_proposed_action(tool_calls)

        assert result["actions"][0]["type"] == "custom_action"
        assert result["actions"][0]["args"] == {"key": "value"}

    def test_format_multiple_actions(self):
        agent = RemediationAgent()
        tool_calls = [
            {"name": "rollback_deployment", "args": {"name": "svc", "revision": "1"}},
            {"name": "add_incident_note", "args": {"id": "INC-1", "note": "Rollback initiated"}},
        ]

        result = agent._format_proposed_action(tool_calls)

        assert result["count"] == 2
        assert result["has_infra_change"] is True


# -----------------------------------------------------------------------------
# Parse Remediation Result Tests
# -----------------------------------------------------------------------------


class TestParseRemediationResult:
    """Tests for _parse_remediation_result method."""

    def test_parse_result_extracts_risk(self):
        agent = RemediationAgent()
        context = SharedContext(session_id="test")

        content = "This is a high risk fix that requires approval."
        result = agent._parse_remediation_result(content, context)

        assert result["risk"] == "high"

    def test_parse_result_extracts_medium_risk(self):
        agent = RemediationAgent()
        context = SharedContext(session_id="test")

        content = "This is a medium risk change."
        result = agent._parse_remediation_result(content, context)

        assert result["risk"] == "medium"

    def test_parse_result_extracts_low_risk(self):
        agent = RemediationAgent()
        context = SharedContext(session_id="test")

        content = "This is a low risk fix."
        result = agent._parse_remediation_result(content, context)

        assert result["risk"] == "low"

    def test_parse_result_extracts_rollback_plan(self):
        agent = RemediationAgent()
        context = SharedContext(session_id="test")

        content = "Fix the issue. Rollback: Run kubectl rollback to revision 5.\n\nMore details."
        result = agent._parse_remediation_result(content, context)

        assert "rollback" in result["rollback_plan"].lower()

    def test_parse_result_extracts_rollback_plan_alternative_markers(self):
        agent = RemediationAgent()
        context = SharedContext(session_id="test")

        content = "Fix applied. To rollback: revert the commit and redeploy."
        result = agent._parse_remediation_result(content, context)

        assert len(result["rollback_plan"]) > 0

    def test_parse_result_extracts_resolution_criteria(self):
        agent = RemediationAgent()
        context = SharedContext(session_id="test")

        content = """
Fix the issue. Resolution criteria:
- Error rate drops below 0.1%
- No more 500 errors in logs
- Latency returns to normal
"""
        result = agent._parse_remediation_result(content, context)

        assert len(result["resolution_criteria"]) > 0

    def test_parse_result_extracts_proposed_action(self):
        agent = RemediationAgent()
        context = SharedContext(session_id="test")

        content = "Proposed fix: Add null check in checkout.js line 42."
        result = agent._parse_remediation_result(content, context)

        assert result["proposed_action"] is not None
        assert "null check" in result["proposed_action"]

    def test_parse_result_default_values(self):
        agent = RemediationAgent()
        context = SharedContext(session_id="test")

        content = "Simple response without markers."
        result = agent._parse_remediation_result(content, context)

        assert result["risk"] == "medium"
        assert result["rollback_plan"] == ""
        assert result["resolution_criteria"] == []
        assert result["proposed_action"] is None

    def test_parse_result_truncates_summary(self):
        agent = RemediationAgent()
        context = SharedContext(session_id="test")

        long_content = "X" * 2000
        result = agent._parse_remediation_result(long_content, context)

        assert len(result["summary"]) == 1000


# -----------------------------------------------------------------------------
# Process Method Tests
# -----------------------------------------------------------------------------


class TestRemediationProcess:
    """Tests for the process method."""

    @patch.object(RemediationAgent, "invoke")
    @patch.object(RemediationAgent, "log_usage")
    @patch.object(RemediationAgent, "send_message")
    def test_process_without_tool_calls(self, mock_send, mock_log, mock_invoke):
        mock_response = AIMessage(content="Proposed fix: Add null check")
        mock_response.tool_calls = []
        mock_invoke.return_value = mock_response
        mock_log.return_value = 100

        agent = RemediationAgent()
        context = SharedContext(session_id="test")
        context.root_cause = "Null pointer exception"
        messages = [HumanMessage(content="Fix the issue")]

        result = agent.process("Fix the issue", messages, context)

        assert result["status"] == "success"
        assert result["result"]["proposed_action"] is not None

    @patch.object(RemediationAgent, "invoke")
    @patch.object(RemediationAgent, "log_usage")
    @patch.object(RemediationAgent, "send_message")
    def test_process_with_write_tool_calls_needs_review(self, mock_send, mock_log, mock_invoke):
        mock_response = AIMessage(content="")
        mock_response.tool_calls = [
            {"name": "create_pull_request", "args": {"title": "Fix"}}
        ]
        mock_invoke.return_value = mock_response
        mock_log.return_value = 50

        agent = RemediationAgent()
        context = SharedContext(session_id="test")
        context.root_cause = "Bug"

        result = agent.process("Fix", [], context)

        assert result["status"] == "needs_review"
        assert "proposed_action" in result["result"]
        mock_send.assert_called_once()
        call_args = mock_send.call_args
        assert call_args[1]["to_agent"] == AgentRole.REVIEWER
        assert call_args[1]["message_type"] == MessageType.HANDOFF

    @patch.object(RemediationAgent, "invoke")
    @patch.object(RemediationAgent, "log_usage")
    def test_process_with_read_tool_calls_needs_tools(self, mock_log, mock_invoke):
        mock_response = AIMessage(content="")
        mock_response.tool_calls = [
            {"name": "read_file", "args": {"path": "/app/checkout.js"}}
        ]
        mock_invoke.return_value = mock_response
        mock_log.return_value = 50

        agent = RemediationAgent()
        context = SharedContext(session_id="test")

        result = agent.process("Investigate fix location", [], context)

        assert result["status"] == "needs_tools"
        assert len(result["tool_calls"]) == 1

    @patch.object(RemediationAgent, "invoke")
    @patch.object(RemediationAgent, "log_usage")
    @patch.object(RemediationAgent, "send_message")
    def test_process_uses_root_cause_from_context(self, mock_send, mock_log, mock_invoke):
        mock_response = AIMessage(content="Done")
        mock_response.tool_calls = []
        mock_invoke.return_value = mock_response
        mock_log.return_value = 100

        agent = RemediationAgent()
        context = SharedContext(session_id="test")
        context.root_cause = "Database connection timeout"

        agent.process("Fix", [], context)

        # Verify invoke was called with volatile prompt containing root cause
        call_args = mock_invoke.call_args
        volatile = call_args[1]["volatile_prompt"]
        assert "Database connection timeout" in volatile

    @patch.object(RemediationAgent, "invoke")
    @patch.object(RemediationAgent, "log_usage")
    @patch.object(RemediationAgent, "send_message")
    def test_process_uses_evidence_from_context(self, mock_send, mock_log, mock_invoke):
        mock_response = AIMessage(content="Done")
        mock_response.tool_calls = []
        mock_invoke.return_value = mock_response
        mock_log.return_value = 100

        agent = RemediationAgent()
        context = SharedContext(session_id="test")
        context.add_evidence("logs", ["Error: Connection refused"])

        agent.process("Fix", [], context)

        call_args = mock_invoke.call_args
        volatile = call_args[1]["volatile_prompt"]
        assert "Connection refused" in volatile

    @patch.object(RemediationAgent, "invoke")
    @patch.object(RemediationAgent, "log_usage")
    @patch.object(RemediationAgent, "send_message")
    def test_process_sends_result_to_orchestrator(self, mock_send, mock_log, mock_invoke):
        mock_response = AIMessage(content="Fix: Update the config")
        mock_response.tool_calls = []
        mock_invoke.return_value = mock_response
        mock_log.return_value = 100

        agent = RemediationAgent()
        context = SharedContext(session_id="test")

        agent.process("Fix", [], context)

        # Should send to orchestrator, not reviewer (no write tool calls)
        mock_send.assert_called_once()
        call_args = mock_send.call_args
        assert call_args[1]["to_agent"] == AgentRole.ORCHESTRATOR
        assert call_args[1]["message_type"] == MessageType.RESULT

    @patch.object(RemediationAgent, "invoke")
    @patch.object(RemediationAgent, "log_usage")
    @patch.object(RemediationAgent, "send_message")
    def test_process_returns_tokens_used(self, mock_send, mock_log, mock_invoke):
        mock_response = AIMessage(content="Done")
        mock_response.tool_calls = []
        mock_invoke.return_value = mock_response
        mock_log.return_value = 150

        agent = RemediationAgent()
        context = SharedContext(session_id="test")

        result = agent.process("Fix", [], context)

        assert result["tokens_used"] == 150

    @patch.object(RemediationAgent, "invoke")
    @patch.object(RemediationAgent, "log_usage")
    @patch.object(RemediationAgent, "send_message")
    def test_process_handles_missing_root_cause(self, mock_send, mock_log, mock_invoke):
        mock_response = AIMessage(content="Done")
        mock_response.tool_calls = []
        mock_invoke.return_value = mock_response
        mock_log.return_value = 100

        agent = RemediationAgent()
        context = SharedContext(session_id="test")
        # No root_cause set

        agent.process("Fix", [], context)

        call_args = mock_invoke.call_args
        volatile = call_args[1]["volatile_prompt"]
        assert "Not yet identified" in volatile


# -----------------------------------------------------------------------------
# Edge Case Tests
# -----------------------------------------------------------------------------


class TestRemediationEdgeCases:
    """Tests for edge cases and error handling."""

    @patch.object(RemediationAgent, "invoke")
    @patch.object(RemediationAgent, "log_usage")
    @patch.object(RemediationAgent, "send_message")
    def test_process_handles_no_tool_calls_attribute(self, mock_send, mock_log, mock_invoke):
        mock_response = AIMessage(content="Done")
        # Simulate response without tool_calls attribute
        del mock_response.tool_calls
        mock_invoke.return_value = mock_response
        mock_log.return_value = 100

        agent = RemediationAgent()
        context = SharedContext(session_id="test")

        # Should not raise
        result = agent.process("Fix", [], context)
        assert result["status"] == "success"

    @patch.object(RemediationAgent, "invoke")
    @patch.object(RemediationAgent, "log_usage")
    @patch.object(RemediationAgent, "send_message")
    def test_process_with_mixed_tool_calls(self, mock_send, mock_log, mock_invoke):
        mock_response = AIMessage(content="")
        # Mix of read and write tools
        mock_response.tool_calls = [
            {"name": "read_file", "args": {"path": "/app/file.js"}},
            {"name": "create_pull_request", "args": {"title": "Fix"}},
        ]
        mock_invoke.return_value = mock_response
        mock_log.return_value = 50

        agent = RemediationAgent()
        context = SharedContext(session_id="test")

        result = agent.process("Fix", [], context)

        # Should detect write tool and go to review
        assert result["status"] == "needs_review"

    def test_parse_result_handles_unicode(self):
        agent = RemediationAgent()
        context = SharedContext(session_id="test")

        content = "Proposed fix: Handle unicode properly."
        result = agent._parse_remediation_result(content, context)

        assert result["summary"] is not None

    def test_parse_result_handles_empty_content(self):
        agent = RemediationAgent()
        context = SharedContext(session_id="test")

        result = agent._parse_remediation_result("", context)

        assert result["summary"] == ""
        assert result["proposed_action"] is None

    def test_format_action_truncates_long_body(self):
        agent = RemediationAgent()
        long_body = "X" * 1000
        tool_calls = [{
            "name": "create_pull_request",
            "args": {
                "title": "Fix",
                "body": long_body,
            },
        }]

        result = agent._format_proposed_action(tool_calls)

        # Body should be truncated to 500 chars
        assert len(result["actions"][0]["body"]) == 500

    def test_format_action_truncates_long_note(self):
        agent = RemediationAgent()
        long_note = "X" * 500
        tool_calls = [{
            "name": "add_incident_note",
            "args": {
                "id": "INC-1",
                "note": long_note,
            },
        }]

        result = agent._format_proposed_action(tool_calls)

        # Note should be truncated to 200 chars
        assert len(result["actions"][0]["note"]) == 200
