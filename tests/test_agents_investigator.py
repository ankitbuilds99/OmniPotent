"""Comprehensive tests for app/agents/investigator.py.

Tests InvestigatorAgent initialization, tool filtering, evidence extraction,
result parsing, and the investigation process.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.agents.investigator import InvestigatorAgent, _INVESTIGATION_TOOLS
from app.agents.protocol import AgentRole, MessageType, SharedContext


class TestInvestigatorAgentInitialization:
    """Tests for InvestigatorAgent initialization."""

    def test_init_with_no_tools(self):
        agent = InvestigatorAgent()
        assert agent._tools == []
        assert agent._fast is False

    def test_init_filters_investigation_tools(self):
        mock_search_logs = MagicMock()
        mock_search_logs.name = "search_logs"
        mock_get_metric = MagicMock()
        mock_get_metric.name = "get_metric"
        mock_create_pr = MagicMock()
        mock_create_pr.name = "create_pull_request"

        tools = [mock_search_logs, mock_get_metric, mock_create_pr]
        agent = InvestigatorAgent(tools=tools)

        tool_names = [t.name for t in agent._tools]
        assert "search_logs" in tool_names
        assert "get_metric" in tool_names
        assert "create_pull_request" not in tool_names

    def test_init_falls_back_to_all_tools_if_filter_empty(self):
        mock_unknown = MagicMock()
        mock_unknown.name = "unknown_tool"
        agent = InvestigatorAgent(tools=[mock_unknown])
        assert len(agent._tools) == 1


class TestInvestigatorAgentProperties:
    """Tests for InvestigatorAgent property accessors."""

    def test_role_property(self):
        agent = InvestigatorAgent()
        assert agent.role == AgentRole.INVESTIGATOR

    def test_system_prompt_property(self):
        agent = InvestigatorAgent()
        assert "InvestigatorAgent" in agent.system_prompt

    def test_name_property(self):
        agent = InvestigatorAgent()
        assert agent.name == "InvestigatorAgent"


class TestInvestigationToolsSet:
    """Tests for the investigation tools configuration."""

    def test_investigation_tools_contains_log_tools(self):
        log_tools = {"search_logs", "get_error_summary", "cluster_logs"}
        assert log_tools.issubset(_INVESTIGATION_TOOLS)

    def test_investigation_tools_contains_metric_tools(self):
        metric_tools = {"get_metric", "list_services", "detect_anomaly"}
        assert metric_tools.issubset(_INVESTIGATION_TOOLS)


class TestInvestigatorEvidenceExtraction:
    """Tests for _extract_evidence method."""

    def test_extract_evidence_from_tool_messages(self):
        agent = InvestigatorAgent()
        messages = [
            HumanMessage(content="Investigate"),
            ToolMessage(content="Error: Connection refused", name="search_logs", tool_call_id="tc1"),
        ]
        response = AIMessage(content="Done")
        evidence = agent._extract_evidence(messages, response)
        assert "search_logs" in evidence
        assert "Connection refused" in evidence["search_logs"][0]

    def test_extract_evidence_truncates_long_content(self):
        agent = InvestigatorAgent()
        long_content = "X" * 1000
        messages = [ToolMessage(content=long_content, name="search_logs", tool_call_id="tc1")]
        evidence = agent._extract_evidence(messages, AIMessage(content="Done"))
        assert len(evidence["search_logs"][0]) == 500

    def test_extract_evidence_empty_messages(self):
        agent = InvestigatorAgent()
        evidence = agent._extract_evidence([], AIMessage(content="Done"))
        assert evidence == {}


class TestInvestigatorResultParsing:
    """Tests for _parse_investigation_result method."""

    def test_parse_result_extracts_root_cause(self):
        agent = InvestigatorAgent()
        context = SharedContext(session_id="test")
        content = "The root cause: null pointer in checkout.js."
        result = agent._parse_investigation_result(content, context)
        assert result["root_cause"] is not None

    def test_parse_result_extracts_severity(self):
        agent = InvestigatorAgent()
        context = SharedContext(session_id="test")
        content = "This is a SEV1 incident."
        result = agent._parse_investigation_result(content, context)
        assert result["severity"] == "SEV1"

    def test_parse_result_extracts_confidence(self):
        agent = InvestigatorAgent()
        context = SharedContext(session_id="test")
        content = "With high confidence, the root cause is X."
        result = agent._parse_investigation_result(content, context)
        assert result["confidence"] == "high"

    def test_parse_result_default_values(self):
        agent = InvestigatorAgent()
        context = SharedContext(session_id="test")
        result = agent._parse_investigation_result("Simple response", context)
        assert result["root_cause"] is None
        assert result["confidence"] == "medium"
        assert result["severity"] == "SEV3"


class TestInvestigatorProcess:
    """Tests for the process method."""

    @patch.object(InvestigatorAgent, "invoke")
    @patch.object(InvestigatorAgent, "log_usage")
    @patch.object(InvestigatorAgent, "send_message")
    def test_process_without_tool_calls(self, mock_send, mock_log, mock_invoke):
        mock_response = AIMessage(content="Root cause: database timeout")
        mock_response.tool_calls = []
        mock_invoke.return_value = mock_response
        mock_log.return_value = 100

        agent = InvestigatorAgent()
        context = SharedContext(session_id="test")
        result = agent.process("Investigate", [], context)

        assert result["status"] == "success"
        mock_send.assert_called_once()

    @patch.object(InvestigatorAgent, "invoke")
    @patch.object(InvestigatorAgent, "log_usage")
    def test_process_with_tool_calls(self, mock_log, mock_invoke):
        mock_response = AIMessage(content="")
        mock_response.tool_calls = [{"name": "search_logs", "args": {"query": "error"}}]
        mock_invoke.return_value = mock_response
        mock_log.return_value = 50

        agent = InvestigatorAgent()
        context = SharedContext(session_id="test")
        result = agent.process("Investigate", [], context)

        assert result["status"] == "needs_tools"
        assert len(result["tool_calls"]) == 1

    @patch.object(InvestigatorAgent, "invoke")
    @patch.object(InvestigatorAgent, "log_usage")
    @patch.object(InvestigatorAgent, "send_message")
    def test_process_updates_context_summary(self, mock_send, mock_log, mock_invoke):
        mock_response = AIMessage(content="Done")
        mock_response.tool_calls = []
        mock_invoke.return_value = mock_response
        mock_log.return_value = 100

        agent = InvestigatorAgent()
        context = SharedContext(session_id="test")
        agent.process("API 500 errors", [], context)

        assert context.incident_summary == "API 500 errors"

    @patch.object(InvestigatorAgent, "invoke")
    @patch.object(InvestigatorAgent, "log_usage")
    @patch.object(InvestigatorAgent, "send_message")
    def test_process_sends_result_message(self, mock_send, mock_log, mock_invoke):
        mock_response = AIMessage(content="Root cause: found")
        mock_response.tool_calls = []
        mock_invoke.return_value = mock_response
        mock_log.return_value = 100

        agent = InvestigatorAgent()
        context = SharedContext(session_id="test")
        agent.process("Investigate", [], context)

        call_args = mock_send.call_args
        assert call_args[1]["to_agent"] == AgentRole.ORCHESTRATOR
        assert call_args[1]["message_type"] == MessageType.RESULT
