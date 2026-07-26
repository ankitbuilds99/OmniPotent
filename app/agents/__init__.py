"""Multi-agent collaboration module for DevOps Copilot.

This module provides a multi-agent system with specialized agents:
- InvestigatorAgent: Analyzes logs, metrics, traces to find root cause
- RemediationAgent: Proposes and executes fixes
- ReviewerAgent: Validates proposed actions before execution
- OrchestratorAgent: Routes tasks, manages handoffs, synthesizes results
"""

from app.agents.base import BaseAgent
from app.agents.investigator import InvestigatorAgent
from app.agents.remediation import RemediationAgent
from app.agents.reviewer import ReviewerAgent
from app.agents.orchestrator import OrchestratorAgent
from app.agents.protocol import AgentMessage, AgentHandoff, SharedContext

__all__ = [
    "BaseAgent",
    "InvestigatorAgent",
    "RemediationAgent",
    "ReviewerAgent",
    "OrchestratorAgent",
    "AgentMessage",
    "AgentHandoff",
    "SharedContext",
]
