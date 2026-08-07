"""Comprehensive tests for app/agents/base.py.

Tests BaseAgent initialization, LLM invocation, message handling,
handoff management, and state management.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.agents.base import BaseAgent
from app.agents.protocol import (
    AgentHandoff,
    AgentRole,
    MessageType,
    SharedContext,
    TaskStatus,
)


# -----------------------------------------------------------------------------
# Concrete implementation for testing the abstract BaseAgent
# -----------------------------------------------------------------------------


class ConcreteTestAgent(BaseAgent):
    """Concrete agent implementation for testing."""

    @property
    def role(self) -> AgentRole:
        return AgentRole.INVESTIGATOR

    @property
    def system_prompt(self) -> str:
        return "You are a test agent for unit testing."

    def process(
        self,
        task: str,
        messages: list[Any],
        context: SharedContext,
    ) -> dict[str, Any]:
        return {
            "status": "success",
            "result": {"task": task},
            "messages": messages,
            "evidence": {},
        }


# -----------------------------------------------------------------------------
# Initialization Tests
# -----------------------------------------------------------------------------


class TestBaseAgentInitialization:
    """Tests for BaseAgent initialization."""

    def test_init_with_no_tools(self):
        agent = ConcreteTestAgent()
        assert agent._tools == []
        assert agent._fast is False
        assert agent._llm is None
        assert agent._llm_with_tools is None

    def test_init_with_tools(self):
        mock_tool1 = MagicMock(name="tool1")
        mock_tool2 = MagicMock(name="tool2")
        agent = ConcreteTestAgent(tools=[mock_tool1, mock_tool2])
        assert len(agent._tools) == 2
        assert agent.tools == [mock_tool1, mock_tool2]

    def test_init_with_fast_mode(self):
        agent = ConcreteTestAgent(fast=True)
        assert agent._fast is True

    def test_init_with_none_tools(self):
        agent = ConcreteTestAgent(tools=None)
        assert agent._tools == []


class TestBaseAgentProperties:
    """Tests for BaseAgent property accessors."""

    def test_role_property(self):
        agent = ConcreteTestAgent()
        assert agent.role == AgentRole.INVESTIGATOR

    def test_system_prompt_property(self):
        agent = ConcreteTestAgent()
        assert "test agent" in agent.system_prompt

    def test_name_property(self):
        agent = ConcreteTestAgent()
        assert agent.name == "InvestigatorAgent"

    def test_tools_property(self):
        mock_tool = MagicMock(name="test_tool")
        agent = ConcreteTestAgent(tools=[mock_tool])
        assert agent.tools == [mock_tool]


# -----------------------------------------------------------------------------
# LLM Access Tests
# -----------------------------------------------------------------------------


class TestBaseAgentLLMAccess:
    """Tests for LLM lazy initialization and access."""

    @patch("app.agents.base.get_llm")
    def test_get_llm_lazy_initialization(self, mock_get_llm):
        mock_llm = MagicMock()
        mock_get_llm.return_value = mock_llm

        agent = ConcreteTestAgent()
        assert agent._llm is None

        llm = agent.get_llm()
        assert llm == mock_llm
        assert agent._llm == mock_llm
        mock_get_llm.assert_called_once_with(fast=False)

    @patch("app.agents.base.get_llm")
    def test_get_llm_fast_mode(self, mock_get_llm):
        mock_llm = MagicMock()
        mock_get_llm.return_value = mock_llm

        agent = ConcreteTestAgent(fast=True)
        agent.get_llm()
        mock_get_llm.assert_called_once_with(fast=True)

    @patch("app.agents.base.get_llm")
    def test_get_llm_cached(self, mock_get_llm):
        mock_llm = MagicMock()
        mock_get_llm.return_value = mock_llm

        agent = ConcreteTestAgent()
        llm1 = agent.get_llm()
        llm2 = agent.get_llm()
        assert llm1 is llm2
        mock_get_llm.assert_called_once()

    @patch("app.agents.base.get_llm")
    def test_get_llm_with_tools_binds_tools(self, mock_get_llm):
        mock_llm = MagicMock()
        mock_bound_llm = MagicMock()
        mock_llm.bind_tools.return_value = mock_bound_llm
        mock_get_llm.return_value = mock_llm

        mock_tool = MagicMock(name="test_tool")
        agent = ConcreteTestAgent(tools=[mock_tool])

        llm_with_tools = agent.get_llm_with_tools()
        assert llm_with_tools == mock_bound_llm
        mock_llm.bind_tools.assert_called_once_with([mock_tool])

    @patch("app.agents.base.get_llm")
    def test_get_llm_with_tools_no_tools(self, mock_get_llm):
        mock_llm = MagicMock()
        mock_get_llm.return_value = mock_llm

        agent = ConcreteTestAgent()
        llm_with_tools = agent.get_llm_with_tools()
        assert llm_with_tools == mock_llm
        mock_llm.bind_tools.assert_not_called()

    @patch("app.agents.base.get_llm")
    def test_get_llm_with_tools_cached(self, mock_get_llm):
        mock_llm = MagicMock()
        mock_bound_llm = MagicMock()
        mock_llm.bind_tools.return_value = mock_bound_llm
        mock_get_llm.return_value = mock_llm

        mock_tool = MagicMock(name="test_tool")
        agent = ConcreteTestAgent(tools=[mock_tool])

        llm1 = agent.get_llm_with_tools()
        llm2 = agent.get_llm_with_tools()
        assert llm1 is llm2
        mock_llm.bind_tools.assert_called_once()


# -----------------------------------------------------------------------------
# Invoke Tests
# -----------------------------------------------------------------------------


class TestBaseAgentInvoke:
    """Tests for the invoke method."""

    @patch("app.agents.base.get_llm")
    @patch("app.agents.base.cached_system")
    def test_invoke_basic(self, mock_cached_system, mock_get_llm):
        mock_llm = MagicMock()
        mock_response = AIMessage(content="Test response")
        mock_llm.invoke.return_value = mock_response
        mock_get_llm.return_value = mock_llm

        mock_system_msg = SystemMessage(content="System prompt")
        mock_cached_system.return_value = mock_system_msg

        agent = ConcreteTestAgent()
        messages = [HumanMessage(content="Test input")]

        response = agent.invoke(messages)

        assert response == mock_response
        mock_llm.invoke.assert_called_once()
        call_args = mock_llm.invoke.call_args[0][0]
        assert call_args[0] == mock_system_msg
        assert call_args[1] == messages[0]

    @patch("app.agents.base.get_llm")
    @patch("app.agents.base.cached_system")
    def test_invoke_with_context(self, mock_cached_system, mock_get_llm):
        mock_llm = MagicMock()
        mock_response = AIMessage(content="Test response")
        mock_llm.invoke.return_value = mock_response
        mock_get_llm.return_value = mock_llm

        mock_system_msg = SystemMessage(content="System prompt with context")
        mock_cached_system.return_value = mock_system_msg

        agent = ConcreteTestAgent()
        context = SharedContext(session_id="test-session")
        context.incident_summary = "Test incident"
        context.severity = "SEV2"

        messages = [HumanMessage(content="Test input")]
        response = agent.invoke(messages, context=context)

        assert response == mock_response
        # Verify cached_system was called with context included
        mock_cached_system.assert_called_once()

    @patch("app.agents.base.get_llm")
    @patch("app.agents.base.cached_system")
    def test_invoke_with_volatile_prompt(self, mock_cached_system, mock_get_llm):
        mock_llm = MagicMock()
        mock_response = AIMessage(content="Test response")
        mock_llm.invoke.return_value = mock_response
        mock_get_llm.return_value = mock_llm

        mock_system_msg = SystemMessage(content="System prompt")
        mock_cached_system.return_value = mock_system_msg

        agent = ConcreteTestAgent()
        messages = [HumanMessage(content="Test input")]

        response = agent.invoke(messages, volatile_prompt="Extra context")

        assert response == mock_response
        # Verify volatile prompt was passed
        mock_cached_system.assert_called_once()
        call_args = mock_cached_system.call_args
        assert call_args[0][1] == "Extra context"

    @patch("app.agents.base.get_llm")
    @patch("app.agents.base.cached_system")
    def test_invoke_uses_tools_llm_when_tools_present(self, mock_cached_system, mock_get_llm):
        mock_llm = MagicMock()
        mock_bound_llm = MagicMock()
        mock_response = AIMessage(content="Test response")
        mock_bound_llm.invoke.return_value = mock_response
        mock_llm.bind_tools.return_value = mock_bound_llm
        mock_get_llm.return_value = mock_llm

        mock_system_msg = SystemMessage(content="System prompt")
        mock_cached_system.return_value = mock_system_msg

        mock_tool = MagicMock(name="test_tool")
        agent = ConcreteTestAgent(tools=[mock_tool])
        messages = [HumanMessage(content="Test input")]

        response = agent.invoke(messages)

        assert response == mock_response
        mock_bound_llm.invoke.assert_called_once()


# -----------------------------------------------------------------------------
# Context Block Tests
# -----------------------------------------------------------------------------


class TestBaseAgentContextBlock:
    """Tests for _context_block method."""

    def test_context_block_empty_context(self):
        agent = ConcreteTestAgent()
        context = SharedContext(session_id="test")
        block = agent._context_block(context)
        assert "Shared Investigation Context" in block

    def test_context_block_with_incident_summary(self):
        agent = ConcreteTestAgent()
        context = SharedContext(session_id="test")
        context.incident_summary = "Checkout service failing"
        block = agent._context_block(context)
        assert "Incident: Checkout service failing" in block

    def test_context_block_with_severity(self):
        agent = ConcreteTestAgent()
        context = SharedContext(session_id="test")
        context.severity = "SEV1"
        block = agent._context_block(context)
        assert "Severity: SEV1" in block

    def test_context_block_with_confidence(self):
        agent = ConcreteTestAgent()
        context = SharedContext(session_id="test")
        context.confidence = "high"
        block = agent._context_block(context)
        assert "Confidence: high" in block

    def test_context_block_with_root_cause(self):
        agent = ConcreteTestAgent()
        context = SharedContext(session_id="test")
        context.root_cause = "Database connection timeout"
        block = agent._context_block(context)
        assert "Current root cause hypothesis: Database connection timeout" in block

    def test_context_block_with_affected_services(self):
        agent = ConcreteTestAgent()
        context = SharedContext(session_id="test")
        context.affected_services = ["checkout-svc", "payment-svc"]
        block = agent._context_block(context)
        assert "Affected services: checkout-svc, payment-svc" in block

    def test_context_block_with_hypotheses(self):
        agent = ConcreteTestAgent()
        context = SharedContext(session_id="test")
        context.add_hypothesis(
            cause="Database timeout",
            confidence="high",
            evidence=["log evidence"],
            source_agent=AgentRole.INVESTIGATOR,
        )
        block = agent._context_block(context)
        assert "Hypotheses:" in block
        assert "Database timeout" in block

    def test_context_block_with_evidence(self):
        agent = ConcreteTestAgent()
        context = SharedContext(session_id="test")
        context.add_evidence("logs", ["Error: Connection refused"])
        block = agent._context_block(context)
        assert "Evidence gathered:" in block
        assert "[logs]" in block
        assert "Error: Connection refused" in block

    def test_context_block_truncates_long_evidence(self):
        agent = ConcreteTestAgent()
        context = SharedContext(session_id="test")
        long_evidence = "X" * 500  # Very long evidence
        context.add_evidence("logs", [long_evidence])
        block = agent._context_block(context)
        # Evidence should be truncated to 200 chars
        assert len(long_evidence) > 200
        # The block should contain the evidence but truncated
        assert "XXXX" in block


# -----------------------------------------------------------------------------
# Message Sending Tests
# -----------------------------------------------------------------------------


class TestBaseAgentSendMessage:
    """Tests for send_message method."""

    def test_send_message_basic(self):
        agent = ConcreteTestAgent()
        context = SharedContext(session_id="test-session")

        msg = agent.send_message(
            to_agent=AgentRole.ORCHESTRATOR,
            message_type=MessageType.RESULT,
            content={"root_cause": "test cause"},
            context=context,
        )

        assert msg.from_agent == AgentRole.INVESTIGATOR
        assert msg.to_agent == AgentRole.ORCHESTRATOR
        assert msg.message_type == MessageType.RESULT
        assert msg.content["root_cause"] == "test cause"
        assert len(context.messages) == 1
        assert context.messages[0] is msg

    def test_send_message_with_priority(self):
        agent = ConcreteTestAgent()
        context = SharedContext(session_id="test-session")

        msg = agent.send_message(
            to_agent=AgentRole.REVIEWER,
            message_type=MessageType.HANDOFF,
            content={"action": "review"},
            context=context,
            priority=1,
        )

        assert msg.priority == 1

    def test_send_message_requires_response(self):
        agent = ConcreteTestAgent()
        context = SharedContext(session_id="test-session")

        msg = agent.send_message(
            to_agent=AgentRole.REMEDIATION,
            message_type=MessageType.QUERY,
            content={"question": "What is the fix?"},
            context=context,
            requires_response=True,
        )

        assert msg.requires_response is True

    def test_send_message_records_context_id(self):
        agent = ConcreteTestAgent()
        context = SharedContext(session_id="session-123")

        msg = agent.send_message(
            to_agent=AgentRole.ORCHESTRATOR,
            message_type=MessageType.RESULT,
            content={},
            context=context,
        )

        assert msg.context_id == "session-123"


# -----------------------------------------------------------------------------
# Handoff Tests
# -----------------------------------------------------------------------------


class TestBaseAgentHandoff:
    """Tests for create_handoff and receive_handoff methods."""

    def test_create_handoff_basic(self):
        agent = ConcreteTestAgent()
        context = SharedContext(session_id="test-session")

        handoff = agent.create_handoff(
            to_agent=AgentRole.REMEDIATION,
            task="Fix the bug",
            context=context,
        )

        assert handoff.from_agent == AgentRole.INVESTIGATOR
        assert handoff.to_agent == AgentRole.REMEDIATION
        assert handoff.task == "Fix the bug"
        assert handoff.status == TaskStatus.PENDING
        assert len(context.handoffs) == 1
        assert context.handoffs[0] is handoff

    def test_create_handoff_with_expected_output(self):
        agent = ConcreteTestAgent()
        context = SharedContext(session_id="test-session")

        handoff = agent.create_handoff(
            to_agent=AgentRole.REVIEWER,
            task="Review the fix",
            context=context,
            expected_output="Approval verdict",
        )

        assert handoff.expected_output == "Approval verdict"

    def test_create_handoff_with_constraints(self):
        agent = ConcreteTestAgent()
        context = SharedContext(session_id="test-session")

        constraints = {"max_time": 60, "risk_level": "low"}
        handoff = agent.create_handoff(
            to_agent=AgentRole.REMEDIATION,
            task="Fix the bug",
            context=context,
            constraints=constraints,
        )

        assert handoff.constraints == constraints

    def test_receive_handoff_success(self):
        agent = ConcreteTestAgent()
        context = SharedContext(session_id="test-session")

        handoff = AgentHandoff(
            id="handoff-1",
            from_agent=AgentRole.ORCHESTRATOR,
            to_agent=AgentRole.INVESTIGATOR,
            task="Investigate the issue",
        )

        result = agent.receive_handoff(handoff, context)

        assert handoff.status == TaskStatus.COMPLETED
        assert result["status"] == "success"
        assert result["result"]["task"] == "Investigate the issue"

    def test_receive_handoff_failure(self):
        class FailingAgent(ConcreteTestAgent):
            def process(self, task, messages, context):
                raise ValueError("Process failed")

        agent = FailingAgent()
        context = SharedContext(session_id="test-session")

        handoff = AgentHandoff(
            id="handoff-1",
            from_agent=AgentRole.ORCHESTRATOR,
            to_agent=AgentRole.INVESTIGATOR,
            task="This will fail",
        )

        result = agent.receive_handoff(handoff, context)

        assert handoff.status == TaskStatus.FAILED
        assert result["status"] == "failed"
        assert "Process failed" in result["error"]


# -----------------------------------------------------------------------------
# Usage Logging Tests
# -----------------------------------------------------------------------------


class TestBaseAgentLogUsage:
    """Tests for log_usage method."""

    def test_log_usage_with_metadata(self):
        agent = ConcreteTestAgent()
        mock_response = MagicMock()
        mock_response.usage_metadata = {
            "input_tokens": 100,
            "output_tokens": 50,
            "total_tokens": 150,
        }

        total = agent.log_usage("test_node", mock_response)
        assert total == 150

    def test_log_usage_no_metadata(self):
        agent = ConcreteTestAgent()
        mock_response = MagicMock(spec=[])  # No usage_metadata

        total = agent.log_usage("test_node", mock_response)
        assert total == 0

    def test_log_usage_none_metadata(self):
        agent = ConcreteTestAgent()
        mock_response = MagicMock()
        mock_response.usage_metadata = None

        total = agent.log_usage("test_node", mock_response)
        assert total == 0

    def test_log_usage_invalid_total(self):
        agent = ConcreteTestAgent()
        mock_response = MagicMock()
        mock_response.usage_metadata = {"total_tokens": "invalid"}

        total = agent.log_usage("test_node", mock_response)
        assert total == 0

    def test_log_usage_missing_total(self):
        agent = ConcreteTestAgent()
        mock_response = MagicMock()
        mock_response.usage_metadata = {"input_tokens": 100}

        total = agent.log_usage("test_node", mock_response)
        assert total == 0


# -----------------------------------------------------------------------------
# Build System Prompt Tests
# -----------------------------------------------------------------------------


class TestBaseAgentBuildSystemPrompt:
    """Tests for _build_system_prompt method."""

    @patch("app.agents.base.cached_system")
    def test_build_system_prompt_no_context(self, mock_cached_system):
        mock_msg = SystemMessage(content="test")
        mock_cached_system.return_value = mock_msg

        agent = ConcreteTestAgent()
        result = agent._build_system_prompt(None, "")

        mock_cached_system.assert_called_once_with(agent.system_prompt, "")
        assert result == mock_msg

    @patch("app.agents.base.cached_system")
    def test_build_system_prompt_with_context(self, mock_cached_system):
        mock_msg = SystemMessage(content="test")
        mock_cached_system.return_value = mock_msg

        agent = ConcreteTestAgent()
        context = SharedContext(session_id="test")
        context.incident_summary = "Test incident"

        agent._build_system_prompt(context, "")

        # Verify that context was appended to the system prompt
        call_args = mock_cached_system.call_args[0]
        assert "Test incident" in call_args[0]

    @patch("app.agents.base.cached_system")
    def test_build_system_prompt_with_volatile(self, mock_cached_system):
        mock_msg = SystemMessage(content="test")
        mock_cached_system.return_value = mock_msg

        agent = ConcreteTestAgent()

        agent._build_system_prompt(None, "volatile content")

        mock_cached_system.assert_called_once()
        call_args = mock_cached_system.call_args[0]
        assert call_args[1] == "volatile content"
