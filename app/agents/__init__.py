"""Multi-agent collaboration module for OmniPotent.

This module provides a multi-agent system with specialized agents:
- InvestigatorAgent: Analyzes logs, metrics, traces to find root cause
- RemediationAgent: Proposes and executes fixes
- ReviewerAgent: Validates proposed actions before execution
- OrchestratorAgent: Routes tasks, manages handoffs, synthesizes results

And 6 parallel specialist agents for Omnipotent Architecture:
- Telemetry: Metrics, logs, traces analysis
- Code: Deployments, commits, PRs
- Dependencies: Services, databases, APIs
- Historical: Past incidents, runbooks
- Knowledge: Docs, architecture, wikis
- Collaboration: People, SMEs, communication
"""

from app.agents.base import BaseAgent
from app.agents.investigator import InvestigatorAgent
from app.agents.remediation import RemediationAgent
from app.agents.reviewer import ReviewerAgent
from app.agents.orchestrator import OrchestratorAgent
from app.agents.protocol import AgentMessage, AgentHandoff, SharedContext
from app.agents.parallel_coordinator import (
    ParallelInvestigationCoordinator,
    ParallelInvestigationResult,
    SpecialistType,
    SpecialistResult,
    Finding,
    ConfidenceFactor,
    run_parallel_investigation,
)

__all__ = [
    # Core agents
    "BaseAgent",
    "InvestigatorAgent",
    "RemediationAgent",
    "ReviewerAgent",
    "OrchestratorAgent",
    # Protocol
    "AgentMessage",
    "AgentHandoff",
    "SharedContext",
    # Parallel investigation (6 specialists)
    "ParallelInvestigationCoordinator",
    "ParallelInvestigationResult",
    "SpecialistType",
    "SpecialistResult",
    "Finding",
    "ConfidenceFactor",
    "run_parallel_investigation",
]
