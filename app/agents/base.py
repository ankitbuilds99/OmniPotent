"""Base agent class with common functionality.

All specialized agents inherit from BaseAgent, which provides:
- Tool binding and execution
- Message handling
- State management
- LLM invocation
"""

from __future__ import annotations

import logging
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.agents.protocol import (
    AgentHandoff,
    AgentMessage,
    AgentRole,
    MessageType,
    SharedContext,
    TaskStatus,
)
from app.llm import cached_system, get_llm

if TYPE_CHECKING:
    from langchain_core.language_models.chat_models import BaseChatModel

log = logging.getLogger("devcopilot.agents.base")


class BaseAgent(ABC):
    """Base class for all specialized agents in the multi-agent system.

    Provides common functionality for:
    - Tool management and execution
    - Inter-agent communication
    - State observation and mutation
    - LLM invocation with proper prompting

    Subclasses must implement:
    - role: The agent's role in the system
    - system_prompt: The system prompt for this agent
    - process: The main processing logic
    """

    def __init__(
        self,
        tools: list[Any] | None = None,
        fast: bool = False,
    ):
        """Initialize the agent.

        Args:
            tools: List of tools this agent can use (None = no tools)
            fast: Whether to use the fast/cheap model
        """
        self._tools = tools or []
        self._fast = fast
        self._llm: BaseChatModel | None = None
        self._llm_with_tools: BaseChatModel | None = None

    @property
    @abstractmethod
    def role(self) -> AgentRole:
        """The role this agent plays in the multi-agent system."""
        ...

    @property
    @abstractmethod
    def system_prompt(self) -> str:
        """The system prompt for this agent."""
        ...

    @property
    def name(self) -> str:
        """Human-readable name for this agent."""
        return self.role.value.capitalize() + "Agent"

    @property
    def tools(self) -> list[Any]:
        """Tools available to this agent."""
        return self._tools

    def get_llm(self) -> "BaseChatModel":
        """Get the LLM instance (lazy initialization)."""
        if self._llm is None:
            self._llm = get_llm(fast=self._fast)
        return self._llm

    def get_llm_with_tools(self) -> "BaseChatModel":
        """Get the LLM instance with tools bound (lazy initialization)."""
        if self._llm_with_tools is None:
            llm = self.get_llm()
            if self._tools:
                self._llm_with_tools = llm.bind_tools(self._tools)  # type: ignore[assignment]
            else:
                self._llm_with_tools = llm
        return self._llm_with_tools  # type: ignore[return-value]

    def invoke(
        self,
        messages: list[Any],
        context: SharedContext | None = None,
        volatile_prompt: str = "",
    ) -> AIMessage:
        """Invoke the LLM with the agent's system prompt and given messages.

        Args:
            messages: The conversation messages to send
            context: Shared context for cross-agent coordination
            volatile_prompt: Per-invocation additions to the system prompt

        Returns:
            The AIMessage response from the LLM
        """
        system = self._build_system_prompt(context, volatile_prompt)
        llm = self.get_llm_with_tools() if self._tools else self.get_llm()
        response = llm.invoke([system, *messages])
        return response  # type: ignore[return-value]

    def _build_system_prompt(
        self,
        context: SharedContext | None,
        volatile: str = "",
    ) -> SystemMessage:
        """Build the system prompt with optional context and volatile additions.

        Args:
            context: Shared context to include in the prompt
            volatile: Per-invocation prompt additions

        Returns:
            A SystemMessage with the complete prompt
        """
        stable = self.system_prompt
        if context:
            stable += self._context_block(context)
        return cached_system(stable, volatile)

    def _context_block(self, context: SharedContext) -> str:
        """Format the shared context for inclusion in the system prompt."""
        lines = ["\n\n--- Shared Investigation Context ---"]
        if context.incident_summary:
            lines.append(f"Incident: {context.incident_summary}")
        if context.severity:
            lines.append(f"Severity: {context.severity}")
        if context.confidence:
            lines.append(f"Confidence: {context.confidence}")
        if context.root_cause:
            lines.append(f"Current root cause hypothesis: {context.root_cause}")
        if context.affected_services:
            lines.append(f"Affected services: {', '.join(context.affected_services)}")
        if context.hypotheses:
            lines.append("Hypotheses:")
            for h in context.hypotheses[:5]:
                lines.append(f"  - {h.get('cause', '?')} ({h.get('verdict', 'inconclusive')})")
        if context.evidence:
            lines.append("Evidence gathered:")
            for source, items in list(context.evidence.items())[:5]:
                for item in items[:3]:
                    lines.append(f"  [{source}] {item[:200]}")
        return "\n".join(lines)

    @abstractmethod
    def process(
        self,
        task: str,
        messages: list[Any],
        context: SharedContext,
    ) -> dict[str, Any]:
        """Process a task and return results.

        Args:
            task: Description of what this agent should do
            messages: Conversation history relevant to the task
            context: Shared context for cross-agent coordination

        Returns:
            A dict with at least:
                - status: "success" | "failed" | "needs_handoff"
                - result: The agent's output
                - messages: New messages generated
                - evidence: Any evidence gathered
        """
        ...

    def send_message(
        self,
        to_agent: AgentRole,
        message_type: MessageType,
        content: dict[str, Any],
        context: SharedContext,
        requires_response: bool = False,
        priority: int = 2,
    ) -> AgentMessage:
        """Send a message to another agent.

        Args:
            to_agent: The receiving agent's role
            message_type: Type of message being sent
            content: The message payload
            context: Shared context to record the message
            requires_response: Whether this message expects a reply
            priority: 1=high, 2=normal, 3=low

        Returns:
            The created AgentMessage
        """
        msg = AgentMessage(
            id=str(uuid.uuid4()),
            from_agent=self.role,
            to_agent=to_agent,
            message_type=message_type,
            content=content,
            context_id=context.session_id,
            timestamp=datetime.now(timezone.utc),
            priority=priority,  # type: ignore[arg-type]
            requires_response=requires_response,
        )
        context.send_message(msg)
        log.debug("%s -> %s: %s", self.name, to_agent.value, message_type.value)
        return msg

    def create_handoff(
        self,
        to_agent: AgentRole,
        task: str,
        context: SharedContext,
        expected_output: str = "",
        constraints: dict[str, Any] | None = None,
    ) -> AgentHandoff:
        """Create a handoff to delegate work to another agent.

        Args:
            to_agent: The agent to delegate to
            task: Description of what they should do
            context: Shared context for coordination
            expected_output: What output is expected back
            constraints: Any constraints on how to complete the task

        Returns:
            The created AgentHandoff
        """
        handoff = AgentHandoff(
            id=str(uuid.uuid4()),
            from_agent=self.role,
            to_agent=to_agent,
            task=task,
            context={"session_id": context.session_id},
            expected_output=expected_output,
            status=TaskStatus.PENDING,
            constraints=constraints or {},
        )
        context.create_handoff(handoff)
        log.info(
            "Handoff created: %s -> %s: %s",
            self.name,
            to_agent.value,
            task[:100],
        )
        return handoff

    def receive_handoff(
        self,
        handoff: AgentHandoff,
        context: SharedContext,
    ) -> dict[str, Any]:
        """Receive and process a handoff from another agent.

        Args:
            handoff: The handoff to process
            context: Shared context for coordination

        Returns:
            The result of processing the handoff
        """
        handoff.status = TaskStatus.IN_PROGRESS
        log.info(
            "%s received handoff from %s: %s",
            self.name,
            handoff.from_agent.value,
            handoff.task[:100],
        )

        try:
            result = self.process(
                task=handoff.task,
                messages=[HumanMessage(content=handoff.task)],
                context=context,
            )
            handoff.complete(result)
            return result
        except Exception as e:
            log.exception("Handoff processing failed: %s", handoff.id)
            handoff.fail(str(e))
            return {"status": "failed", "error": str(e)}

    def log_usage(self, node: str, response: Any) -> int:
        """Log LLM token usage and return the total tokens used.

        Args:
            node: Name of the node/step for logging
            response: The LLM response object

        Returns:
            Total tokens used (0 if unavailable)
        """
        um = getattr(response, "usage_metadata", None)
        if not um:
            return 0
        total = um.get("total_tokens", 0)
        log.info(
            "llm_usage agent=%s node=%s input=%s output=%s total=%s",
            self.name,
            node,
            um.get("input_tokens"),
            um.get("output_tokens"),
            total,
        )
        try:
            return int(total)
        except (TypeError, ValueError):
            return 0
