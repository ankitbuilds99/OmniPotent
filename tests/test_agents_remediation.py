"""Comprehensive tests for app/agents/remediation.py.

Tests RemediationAgent initialization, tool filtering, fix proposal,
result parsing, and remediation process.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from langchain_core.messages import AIMessage

from app.agents.remediation import RemediationAgent, _READ_TOOLS, _WRITE_TOOLS
from app.agents.protocol import AgentRole, SharedContext


class TestRemediationAgentInitialization:
    """Tests for RemediationAgent initialization."""

    def test_init_with_no_tools(self):
        agent = RemediationAgent()
        assert agent._tools == []
        assert agent._fast is False

    def test_init_filters_remediation_tools(self):
        mock_read_file = MagicMock()
        mock_read_file.name = "read_file"
        mock_create_pr = MagicMock()
        mock_create_pr.name = "create_pull_request"
        mock_search_traces = MagicMock()
        mock_search_traces.name = "search_traces"  # Not a remediation tool

        tools = [mock_read_file, mock_create_pr, mock_search_traces]
        agent = RemediationAgent(tools=tools)

        tool_names = [t.name for t in agent._tools]
        assert "read_file" in tool_names
        assert "create_pull_request" in tool_names
        assert "search_traces" not in tool_names


class TestRemediationAgentProperties:
    """Tests for RemediationAgent property accessors."""

    def test_role_property(self):
        agent = RemediationAgent()
        assert agent.role == AgentRole.REMEDIATION

    def test_system_prompt_property(self):
        agent = RemediationAgent()
        assert "RemediationAgent" in agent.system_prompt

    def test_name_property(self):
        agent = RemediationAgent()
        assert agent.name == "RemediationAgent"


class TestRemediationToolSets:
    """Tests for tool set configurations."""

    def test_write_tools_defined(self):
        assert "create_pull_request" in _WRITE_TOOLS
        assert "scale_deployment" in _WRITE_TOOLS
        assert "rollback_deployment" in _WRITE_TOOLS

    def test_read_tools_defined(self):
        assert "read_file" in _READ_TOOLS
        assert "grep" in _READ_TOOLS
        assert "git_log" in _READ_TOOLS

    def test_tool_sets_disjoint(self):
        assert _WRITE_TOOLS.isdisjoint(_READ_TOOLS)


class TestFormatRemediationPrompt:
    """Tests for _format_remediation_prompt method."""

    def test_format_prompt_basic(self):
        agent = RemediationAgent()
        prompt = agent._format_remediation_prompt(
            task="Fix the bug",
            root_cause="Null pointer",
            evidence="Log shows error",
        )
        assert "Fix the bug" in prompt
        assert "Null pointer" in prompt


class TestFormatProposedAction:
    """Tests for _format_proposed_action method."""

    def test_format_pull_request_action(self):
        agent = RemediationAgent()
        tool_calls = [{
            "name": "create_pull_request",
            "args": {"title": "Fix", "patch": "diff"},
        }]
        result = agent._format_proposed_action(tool_calls)
        assert result["has_code_change"] is True
        assert result["actions"][0]["type"] == "pull_request"

    def test_format_scale_deployment_action(self):
        agent = RemediationAgent()
        tool_calls = [{"name": "scale_deployment", "args": {"replicas": 5}}]
        result = agent._format_proposed_action(tool_calls)
        assert result["has_infra_change"] is True


class TestParseRemediationResult:
    """Tests for _parse_remediation_result method."""

    def test_parse_result_extracts_risk(self):
        agent = RemediationAgent()
        context = SharedContext(session_id="test")
        content = "This is a high risk fix."
        result = agent._parse_remediation_result(content, context)
        assert result["risk"] == "high"

    def test_parse_result_extracts_rollback_plan(self):
        agent = RemediationAgent()
        context = SharedContext(session_id="test")
        content = "Fix. Rollback: Run kubectl rollback."
        result = agent._parse_remediation_result(content, context)
        assert len(result["rollback_plan"]) > 0

    def test_parse_result_default_values(self):
        agent = RemediationAgent()
        context = SharedContext(session_id="test")
        result = agent._parse_remediation_result("Simple", context)
        assert result["risk"] == "medium"


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
        result = agent.process("Fix", [], context)

        assert result["status"] == "success"

    @patch.object(RemediationAgent, "invoke")
    @patch.object(RemediationAgent, "log_usage")
    @patch.object(RemediationAgent, "send_message")
    def test_process_with_write_tool_calls(self, mock_send, mock_log, mock_invoke):
        mock_response = AIMessage(content="")
        mock_response.tool_calls = [{"name": "create_pull_request", "args": {"title": "Fix"}}]
        mock_invoke.return_value = mock_response
        mock_log.return_value = 50

        agent = RemediationAgent()
        context = SharedContext(session_id="test")
        result = agent.process("Fix", [], context)

        assert result["status"] == "needs_review"
        mock_send.assert_called_once()

    @patch.object(RemediationAgent, "invoke")
    @patch.object(RemediationAgent, "log_usage")
    def test_process_with_read_tool_calls(self, mock_log, mock_invoke):
        mock_response = AIMessage(content="")
        mock_response.tool_calls = [{"name": "read_file", "args": {"path": "/app/file.js"}}]
        mock_invoke.return_value = mock_response
        mock_log.return_value = 50

        agent = RemediationAgent()
        context = SharedContext(session_id="test")
        result = agent.process("Fix", [], context)

        assert result["status"] == "needs_tools"
