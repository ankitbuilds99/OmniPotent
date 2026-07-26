"""OrchestratorAgent — coordinates the multi-agent system.

Routes tasks between specialized agents, manages handoffs, and
synthesizes results into a coherent response.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import HumanMessage

from app.agents.base import BaseAgent
from app.agents.investigator import InvestigatorAgent
from app.agents.protocol import (
    AgentMessage,
    AgentRole,
    SharedContext,
    TaskStatus,
)
from app.agents.remediation import RemediationAgent
from app.agents.reviewer import ReviewerAgent
from app.graph.prompts import ORCHESTRATOR_AGENT_SYSTEM

log = logging.getLogger("devcopilot.agents.orchestrator")


class InvestigationPhase:
    """Phases of a multi-agent investigation."""
    INITIAL = "initial"
    INVESTIGATING = "investigating"
    ROOT_CAUSE_FOUND = "root_cause_found"
    REMEDIATING = "remediating"
    REVIEWING = "reviewing"
    AWAITING_APPROVAL = "awaiting_approval"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    COMPLETE = "complete"
    FAILED = "failed"


class OrchestratorAgent(BaseAgent):
    """Agent that coordinates the multi-agent investigation workflow.

    Responsibilities:
    - Route incoming tasks to appropriate agents
    - Manage handoffs between agents
    - Track investigation phase and progress
    - Synthesize results from multiple agents
    - Escalate to humans when needed
    """

    def __init__(self, tools: list[Any] | None = None, fast: bool = False):
        """Initialize the orchestrator with its team of agents.

        Args:
            tools: Tools available to the agent team
            fast: Whether to use fast model for orchestrator decisions
        """
        super().__init__(tools=None, fast=fast)  # Orchestrator doesn't use tools directly
        self._all_tools = tools or []

        # Initialize the agent team (lazy — created on first use)
        self._investigator: InvestigatorAgent | None = None
        self._remediator: RemediationAgent | None = None
        self._reviewer: ReviewerAgent | None = None

    @property
    def role(self) -> AgentRole:
        return AgentRole.ORCHESTRATOR

    @property
    def system_prompt(self) -> str:
        return ORCHESTRATOR_AGENT_SYSTEM

    @property
    def investigator(self) -> InvestigatorAgent:
        """Get or create the investigator agent."""
        if self._investigator is None:
            self._investigator = InvestigatorAgent(tools=self._all_tools)
        return self._investigator

    @property
    def remediator(self) -> RemediationAgent:
        """Get or create the remediation agent."""
        if self._remediator is None:
            self._remediator = RemediationAgent(tools=self._all_tools)
        return self._remediator

    @property
    def reviewer(self) -> ReviewerAgent:
        """Get or create the reviewer agent."""
        if self._reviewer is None:
            self._reviewer = ReviewerAgent(tools=self._all_tools, fast=True)
        return self._reviewer

    def process(
        self,
        task: str,
        messages: list[Any],
        context: SharedContext,
    ) -> dict[str, Any]:
        """Process a task by routing to appropriate agents.

        Args:
            task: The task to process
            messages: Conversation history
            context: Shared context for coordination

        Returns:
            Aggregated results from all involved agents
        """
        phase = context.metadata.get("phase", InvestigationPhase.INITIAL)
        log.info("Orchestrator processing task in phase: %s", phase)

        # Route based on current phase
        if phase == InvestigationPhase.INITIAL:
            return self._start_investigation(task, messages, context)
        elif phase == InvestigationPhase.INVESTIGATING:
            return self._continue_investigation(task, messages, context)
        elif phase == InvestigationPhase.ROOT_CAUSE_FOUND:
            return self._start_remediation(task, messages, context)
        elif phase == InvestigationPhase.REMEDIATING:
            return self._continue_remediation(task, messages, context)
        elif phase == InvestigationPhase.REVIEWING:
            return self._continue_review(task, messages, context)
        elif phase == InvestigationPhase.AWAITING_APPROVAL:
            return self._handle_approval_response(task, messages, context)
        elif phase == InvestigationPhase.COMPLETE:
            return self._synthesize_final_report(context)
        else:
            log.warning("Unknown phase: %s, starting fresh investigation", phase)
            return self._start_investigation(task, messages, context)

    def _start_investigation(
        self,
        task: str,
        messages: list[Any],
        context: SharedContext,
    ) -> dict[str, Any]:
        """Start a new investigation by routing to the investigator."""
        context.metadata["phase"] = InvestigationPhase.INVESTIGATING
        context.add_timeline_event(
            event=f"Investigation started: {task[:100]}",
            source=self.name,
        )

        # Create handoff to investigator
        handoff = self.create_handoff(
            to_agent=AgentRole.INVESTIGATOR,
            task=task,
            context=context,
            expected_output="Root cause with supporting evidence",
        )

        # Process with investigator
        result = self.investigator.receive_handoff(handoff, context)

        # Check if investigator needs to run tools
        if result.get("status") == "needs_tools":
            return {
                "status": "needs_tools",
                "agent": "investigator",
                "tool_calls": result.get("tool_calls", []),
                "messages": result.get("messages", []),
                "tokens_used": result.get("tokens_used", 0),
                "context": context.to_dict(),
            }

        # Check if root cause was found
        if result.get("result", {}).get("root_cause"):
            context.metadata["phase"] = InvestigationPhase.ROOT_CAUSE_FOUND
            log.info("Root cause identified: %s", context.root_cause)

        return {
            "status": result.get("status", "investigating"),
            "agent": "investigator",
            "result": result.get("result"),
            "messages": result.get("messages", []),
            "tokens_used": result.get("tokens_used", 0),
            "context": context.to_dict(),
        }

    def _continue_investigation(
        self,
        task: str,
        messages: list[Any],
        context: SharedContext,
    ) -> dict[str, Any]:
        """Continue an ongoing investigation with new tool results."""
        result = self.investigator.process(task, messages, context)

        if result.get("status") == "needs_tools":
            return {
                "status": "needs_tools",
                "agent": "investigator",
                "tool_calls": result.get("tool_calls", []),
                "messages": result.get("messages", []),
                "tokens_used": result.get("tokens_used", 0),
                "context": context.to_dict(),
            }

        if result.get("result", {}).get("root_cause"):
            context.metadata["phase"] = InvestigationPhase.ROOT_CAUSE_FOUND

        return {
            "status": result.get("status", "investigating"),
            "agent": "investigator",
            "result": result.get("result"),
            "messages": result.get("messages", []),
            "tokens_used": result.get("tokens_used", 0),
            "context": context.to_dict(),
        }

    def _start_remediation(
        self,
        task: str,
        messages: list[Any],
        context: SharedContext,
    ) -> dict[str, Any]:
        """Start remediation phase after root cause is found."""
        context.metadata["phase"] = InvestigationPhase.REMEDIATING
        context.add_timeline_event(
            event=f"Starting remediation for: {context.root_cause[:100] if context.root_cause else 'unknown'}",
            source=self.name,
        )

        # Create handoff to remediator
        remediation_task = f"Propose a fix for the root cause: {context.root_cause}"
        handoff = self.create_handoff(
            to_agent=AgentRole.REMEDIATION,
            task=remediation_task,
            context=context,
            expected_output="Concrete fix with risk assessment and rollback plan",
        )

        result = self.remediator.receive_handoff(handoff, context)

        if result.get("status") == "needs_tools":
            return {
                "status": "needs_tools",
                "agent": "remediation",
                "tool_calls": result.get("tool_calls", []),
                "messages": result.get("messages", []),
                "tokens_used": result.get("tokens_used", 0),
                "context": context.to_dict(),
            }

        if result.get("status") == "needs_review":
            context.metadata["phase"] = InvestigationPhase.REVIEWING
            context.metadata["pending_action"] = result.get("result", {}).get("proposed_action")

        return {
            "status": result.get("status", "remediating"),
            "agent": "remediation",
            "result": result.get("result"),
            "messages": result.get("messages", []),
            "tokens_used": result.get("tokens_used", 0),
            "context": context.to_dict(),
        }

    def _continue_remediation(
        self,
        task: str,
        messages: list[Any],
        context: SharedContext,
    ) -> dict[str, Any]:
        """Continue remediation with new tool results."""
        result = self.remediator.process(task, messages, context)

        if result.get("status") == "needs_tools":
            return {
                "status": "needs_tools",
                "agent": "remediation",
                "tool_calls": result.get("tool_calls", []),
                "messages": result.get("messages", []),
                "tokens_used": result.get("tokens_used", 0),
                "context": context.to_dict(),
            }

        if result.get("status") == "needs_review":
            context.metadata["phase"] = InvestigationPhase.REVIEWING
            context.metadata["pending_action"] = result.get("result", {}).get("proposed_action")

        return {
            "status": result.get("status", "remediating"),
            "agent": "remediation",
            "result": result.get("result"),
            "messages": result.get("messages", []),
            "tokens_used": result.get("tokens_used", 0),
            "context": context.to_dict(),
        }

    def _continue_review(
        self,
        task: str,
        messages: list[Any],
        context: SharedContext,
    ) -> dict[str, Any]:
        """Process review of a proposed action."""
        pending_action = context.metadata.get("pending_action", {})
        review_task = f"Review this proposed action: {pending_action}"

        result = self.reviewer.process(review_task, messages, context)

        verdict = result.get("result", {}).get("verdict", "reject")

        if verdict == "approve":
            context.metadata["phase"] = InvestigationPhase.AWAITING_APPROVAL
            context.add_timeline_event(
                event="Fix approved by ReviewerAgent, awaiting human approval",
                source=self.name,
            )
        elif verdict == "needs_clarification":
            # Send back to remediator for clarification
            context.metadata["phase"] = InvestigationPhase.REMEDIATING
            context.add_timeline_event(
                event=f"Review requested clarification: {result.get('result', {}).get('rationale', '')[:100]}",
                source=self.name,
            )
        else:
            # Rejected - may need to revise the fix
            context.metadata["phase"] = InvestigationPhase.REMEDIATING
            context.add_timeline_event(
                event=f"Fix rejected by ReviewerAgent: {result.get('result', {}).get('rationale', '')[:100]}",
                source=self.name,
            )

        return {
            "status": "reviewed",
            "agent": "reviewer",
            "verdict": verdict,
            "result": result.get("result"),
            "messages": result.get("messages", []),
            "tokens_used": result.get("tokens_used", 0),
            "context": context.to_dict(),
        }

    def _handle_approval_response(
        self,
        task: str,
        messages: list[Any],
        context: SharedContext,
    ) -> dict[str, Any]:
        """Handle response after human approval decision."""
        # This would be called after the human approves/rejects
        # For now, just mark as complete and synthesize report
        context.metadata["phase"] = InvestigationPhase.COMPLETE
        return self._synthesize_final_report(context)

    def _synthesize_final_report(self, context: SharedContext) -> dict[str, Any]:
        """Synthesize all agent findings into a final report."""
        report = {
            "session_id": context.session_id,
            "incident_summary": context.incident_summary,
            "severity": context.severity,
            "confidence": context.confidence,
            "root_cause": context.root_cause,
            "affected_services": context.affected_services,
            "hypotheses": context.hypotheses,
            "evidence": context.evidence,
            "timeline": context.timeline,
            "agent_messages": len(context.messages),
            "handoffs": len(context.handoffs),
        }

        context.add_timeline_event(
            event="Investigation complete - final report synthesized",
            source=self.name,
        )

        return {
            "status": "complete",
            "agent": "orchestrator",
            "result": report,
            "context": context.to_dict(),
        }

    def route_message(
        self,
        message: AgentMessage,
        context: SharedContext,
    ) -> dict[str, Any]:
        """Route an incoming message to the appropriate agent.

        Args:
            message: The message to route
            context: Shared context

        Returns:
            Result of processing the message
        """
        target = message.to_agent

        if target == AgentRole.INVESTIGATOR:
            return self.investigator.process(
                task=str(message.content.get("task", "")),
                messages=[HumanMessage(content=str(message.content))],
                context=context,
            )
        elif target == AgentRole.REMEDIATION:
            return self.remediator.process(
                task=str(message.content.get("task", "")),
                messages=[HumanMessage(content=str(message.content))],
                context=context,
            )
        elif target == AgentRole.REVIEWER:
            return self.reviewer.process(
                task=str(message.content.get("task", "")),
                messages=[HumanMessage(content=str(message.content))],
                context=context,
            )
        else:
            log.warning("Unknown target agent: %s", target)
            return {"status": "failed", "error": f"Unknown agent: {target}"}

    def get_status(self, context: SharedContext) -> dict[str, Any]:
        """Get current status of the multi-agent investigation.

        Args:
            context: Shared context

        Returns:
            Current investigation status
        """
        return {
            "session_id": context.session_id,
            "phase": context.metadata.get("phase", InvestigationPhase.INITIAL),
            "root_cause_found": context.root_cause is not None,
            "confidence": context.confidence,
            "severity": context.severity,
            "evidence_count": sum(len(v) for v in context.evidence.values()),
            "hypotheses_count": len(context.hypotheses),
            "pending_handoffs": len([h for h in context.handoffs if h.status == TaskStatus.PENDING]),
            "completed_handoffs": len([h for h in context.handoffs if h.status == TaskStatus.COMPLETED]),
            "messages_exchanged": len(context.messages),
        }
