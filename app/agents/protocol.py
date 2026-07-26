"""Multi-agent protocol definitions.

Defines the data structures for inter-agent communication:
- AgentMessage: messages passed between agents
- AgentHandoff: task delegation from one agent to another
- SharedContext: cross-agent state for coordination
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal


def _utcnow() -> datetime:
    """Return timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


class AgentRole(str, Enum):
    """The specialized role of an agent in the multi-agent system."""
    ORCHESTRATOR = "orchestrator"
    INVESTIGATOR = "investigator"
    REMEDIATION = "remediation"
    REVIEWER = "reviewer"


class TaskStatus(str, Enum):
    """Status of a task delegated between agents."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"  # Waiting for human approval or another agent


class MessageType(str, Enum):
    """Types of messages exchanged between agents."""
    TASK = "task"           # New task assignment
    RESULT = "result"       # Task completion result
    HANDOFF = "handoff"     # Delegation to another agent
    QUERY = "query"         # Request for information
    RESPONSE = "response"   # Response to a query
    FEEDBACK = "feedback"   # Feedback on work done
    ESCALATION = "escalation"  # Escalating to human or higher authority


@dataclass
class AgentMessage:
    """A message passed between agents in the multi-agent system.

    Attributes:
        id: Unique message identifier
        from_agent: The sending agent's role
        to_agent: The receiving agent's role
        message_type: Type of this message
        content: The message payload (varies by type)
        context_id: ID linking related messages in a conversation
        timestamp: When the message was created
        priority: Message priority (1=highest, 3=lowest)
        requires_response: Whether the sender expects a reply
        metadata: Additional data (tool calls, evidence refs, etc.)
    """
    id: str
    from_agent: AgentRole
    to_agent: AgentRole
    message_type: MessageType
    content: dict[str, Any]
    context_id: str = ""
    timestamp: datetime = field(default_factory=_utcnow)
    priority: Literal[1, 2, 3] = 2
    requires_response: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the message to a dictionary."""
        return {
            "id": self.id,
            "from_agent": self.from_agent.value,
            "to_agent": self.to_agent.value,
            "message_type": self.message_type.value,
            "content": self.content,
            "context_id": self.context_id,
            "timestamp": self.timestamp.isoformat(),
            "priority": self.priority,
            "requires_response": self.requires_response,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentMessage":
        """Deserialize a message from a dictionary."""
        return cls(
            id=data["id"],
            from_agent=AgentRole(data["from_agent"]),
            to_agent=AgentRole(data["to_agent"]),
            message_type=MessageType(data["message_type"]),
            content=data["content"],
            context_id=data.get("context_id", ""),
            timestamp=datetime.fromisoformat(data["timestamp"]) if isinstance(data.get("timestamp"), str) else _utcnow(),
            priority=data.get("priority", 2),
            requires_response=data.get("requires_response", False),
            metadata=data.get("metadata", {}),
        )


@dataclass
class AgentHandoff:
    """A task handoff from one agent to another.

    Used when an agent delegates part of its work to a specialized agent.
    The orchestrator uses this to route tasks, and agents use it to
    request help from peers.

    Attributes:
        id: Unique handoff identifier
        from_agent: The delegating agent
        to_agent: The agent receiving the task
        task: Description of what needs to be done
        context: Relevant context for the task
        expected_output: What the delegating agent expects back
        deadline: Optional deadline for completion
        status: Current status of this handoff
        result: The result when completed
        evidence: Evidence gathered or required
        tools_used: Tools the agent should use
        constraints: Any constraints on how to complete the task
    """
    id: str
    from_agent: AgentRole
    to_agent: AgentRole
    task: str
    context: dict[str, Any] = field(default_factory=dict)
    expected_output: str = ""
    deadline: datetime | None = None
    status: TaskStatus = TaskStatus.PENDING
    result: dict[str, Any] | None = None
    evidence: list[str] = field(default_factory=list)
    tools_used: list[str] = field(default_factory=list)
    constraints: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the handoff to a dictionary."""
        return {
            "id": self.id,
            "from_agent": self.from_agent.value,
            "to_agent": self.to_agent.value,
            "task": self.task,
            "context": self.context,
            "expected_output": self.expected_output,
            "deadline": self.deadline.isoformat() if self.deadline else None,
            "status": self.status.value,
            "result": self.result,
            "evidence": self.evidence,
            "tools_used": self.tools_used,
            "constraints": self.constraints,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentHandoff":
        """Deserialize a handoff from a dictionary."""
        return cls(
            id=data["id"],
            from_agent=AgentRole(data["from_agent"]),
            to_agent=AgentRole(data["to_agent"]),
            task=data["task"],
            context=data.get("context", {}),
            expected_output=data.get("expected_output", ""),
            deadline=datetime.fromisoformat(data["deadline"]) if data.get("deadline") else None,
            status=TaskStatus(data.get("status", "pending")),
            result=data.get("result"),
            evidence=data.get("evidence", []),
            tools_used=data.get("tools_used", []),
            constraints=data.get("constraints", {}),
        )

    def complete(self, result: dict[str, Any]) -> None:
        """Mark the handoff as completed with the given result."""
        self.status = TaskStatus.COMPLETED
        self.result = result

    def fail(self, reason: str) -> None:
        """Mark the handoff as failed with the given reason."""
        self.status = TaskStatus.FAILED
        self.result = {"error": reason}


@dataclass
class SharedContext:
    """Cross-agent state for coordination during an investigation.

    This is the shared memory that all agents can read from and write to.
    It maintains the investigation state, evidence gathered, hypotheses,
    and coordination metadata.

    Attributes:
        session_id: Unique identifier for this investigation session
        incident_summary: High-level description of the incident
        severity: Current assessed severity
        confidence: Current confidence in the root cause
        root_cause: The identified root cause (if any)
        hypotheses: List of candidate root causes being considered
        evidence: Evidence gathered from tools (keyed by source)
        affected_services: Services impacted by the incident
        timeline: Chronological events during investigation
        messages: Inter-agent messages for this session
        handoffs: Active handoffs between agents
        pending_approvals: Actions waiting for human approval
        metadata: Additional session metadata
    """
    session_id: str
    incident_summary: str = ""
    severity: Literal["SEV1", "SEV2", "SEV3", "SEV4", "info"] = "SEV3"
    confidence: Literal["high", "medium", "low"] = "low"
    root_cause: str | None = None
    hypotheses: list[dict[str, Any]] = field(default_factory=list)
    evidence: dict[str, list[str]] = field(default_factory=dict)
    affected_services: list[str] = field(default_factory=list)
    timeline: list[dict[str, Any]] = field(default_factory=list)
    messages: list[AgentMessage] = field(default_factory=list)
    handoffs: list[AgentHandoff] = field(default_factory=list)
    pending_approvals: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def add_evidence(self, source: str, items: list[str]) -> None:
        """Add evidence items from a given source."""
        if source not in self.evidence:
            self.evidence[source] = []
        self.evidence[source].extend(items)

    def add_hypothesis(
        self,
        cause: str,
        confidence: Literal["high", "medium", "low"],
        evidence: list[str],
        source_agent: AgentRole,
    ) -> None:
        """Add a hypothesis about the root cause."""
        self.hypotheses.append({
            "cause": cause,
            "confidence": confidence,
            "evidence": evidence,
            "source_agent": source_agent.value,
            "verdict": "inconclusive",
            "timestamp": _utcnow().isoformat(),
        })

    def add_timeline_event(self, event: str, source: str, timestamp: datetime | None = None) -> None:
        """Add an event to the investigation timeline."""
        self.timeline.append({
            "event": event,
            "source": source,
            "timestamp": (timestamp or _utcnow()).isoformat(),
        })

    def send_message(self, message: AgentMessage) -> None:
        """Record a message sent between agents."""
        self.messages.append(message)

    def create_handoff(self, handoff: AgentHandoff) -> None:
        """Create a new handoff between agents."""
        self.handoffs.append(handoff)

    def get_pending_handoffs(self, agent: AgentRole) -> list[AgentHandoff]:
        """Get all pending handoffs assigned to an agent."""
        return [h for h in self.handoffs if h.to_agent == agent and h.status == TaskStatus.PENDING]

    def get_active_handoffs(self, agent: AgentRole) -> list[AgentHandoff]:
        """Get all in-progress handoffs for an agent."""
        return [h for h in self.handoffs if h.to_agent == agent and h.status == TaskStatus.IN_PROGRESS]

    def all_evidence_list(self, max_items: int = 50) -> list[str]:
        """Flatten all evidence into a single list for prompts."""
        all_ev: list[str] = []
        for source, items in self.evidence.items():
            for item in items:
                all_ev.append(f"[{source}] {item}")
                if len(all_ev) >= max_items:
                    return all_ev
        return all_ev

    def to_dict(self) -> dict[str, Any]:
        """Serialize the context to a dictionary."""
        return {
            "session_id": self.session_id,
            "incident_summary": self.incident_summary,
            "severity": self.severity,
            "confidence": self.confidence,
            "root_cause": self.root_cause,
            "hypotheses": self.hypotheses,
            "evidence": self.evidence,
            "affected_services": self.affected_services,
            "timeline": self.timeline,
            "messages": [m.to_dict() for m in self.messages],
            "handoffs": [h.to_dict() for h in self.handoffs],
            "pending_approvals": self.pending_approvals,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SharedContext":
        """Deserialize a context from a dictionary."""
        ctx = cls(
            session_id=data["session_id"],
            incident_summary=data.get("incident_summary", ""),
            severity=data.get("severity", "SEV3"),
            confidence=data.get("confidence", "low"),
            root_cause=data.get("root_cause"),
            hypotheses=data.get("hypotheses", []),
            evidence=data.get("evidence", {}),
            affected_services=data.get("affected_services", []),
            timeline=data.get("timeline", []),
            pending_approvals=data.get("pending_approvals", []),
            metadata=data.get("metadata", {}),
        )
        ctx.messages = [AgentMessage.from_dict(m) for m in data.get("messages", [])]
        ctx.handoffs = [AgentHandoff.from_dict(h) for h in data.get("handoffs", [])]
        return ctx
