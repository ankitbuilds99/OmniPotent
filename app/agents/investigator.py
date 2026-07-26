"""InvestigatorAgent — specialized for root cause analysis.

Analyzes logs, metrics, traces, and deploys to identify the root cause
of production incidents. Does not propose fixes (that's RemediationAgent's job).
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage

from app.agents.base import BaseAgent
from app.agents.protocol import AgentRole, MessageType, SharedContext
from app.graph.prompts import INVESTIGATOR_AGENT_SYSTEM

log = logging.getLogger("devcopilot.agents.investigator")

# Tools the investigator prioritizes (read-only investigation tools)
_INVESTIGATION_TOOLS = {
    # Logs
    "search_logs", "get_error_summary", "cluster_logs",
    # Metrics
    "get_metric", "list_services", "detect_anomaly", "compute_burn_rate", "onset_timeline",
    # Alerts
    "list_incidents", "get_incident", "get_incident_alerts",
    # Traces
    "search_traces", "get_trace", "service_dependencies", "analyze_blast_radius",
    "analyze_critical_path", "get_exemplars", "correlate_incidents",
    # Deploys
    "list_deploys", "get_deploy", "deploys_in_window",
    # Memory
    "search_incidents", "get_incident_record",
    # Repo
    "list_dir", "read_file", "grep", "git_log",
    # GitHub
    "list_recent_commits", "get_commit_diff", "correlate_changes", "first_bad_deploy",
    "list_workflow_runs", "get_failed_job_logs",
    # Sentry
    "list_issues", "get_issue", "get_latest_event",
    # Kubernetes (read-only)
    "list_pods", "describe_pod", "get_events", "get_deployment_status", "rollout_history",
}


class InvestigatorAgent(BaseAgent):
    """Agent specialized in investigating production incidents.

    Focuses on:
    - Symptom gathering (logs, metrics, alerts)
    - Change correlation (deploys, commits)
    - Causal chain tracing (what changed -> what broke -> symptom)
    - Hypothesis formulation and validation
    """

    @property
    def role(self) -> AgentRole:
        return AgentRole.INVESTIGATOR

    @property
    def system_prompt(self) -> str:
        return INVESTIGATOR_AGENT_SYSTEM

    def __init__(self, tools: list[Any] | None = None, fast: bool = False):
        # Filter to only investigation tools if tools are provided
        if tools:
            filtered = [t for t in tools if getattr(t, "name", "") in _INVESTIGATION_TOOLS]
            # Fall back to all tools if filtering leaves nothing
            super().__init__(tools=filtered or tools, fast=fast)
        else:
            super().__init__(tools=tools, fast=fast)

    def process(
        self,
        task: str,
        messages: list[Any],
        context: SharedContext,
    ) -> dict[str, Any]:
        """Investigate an incident and identify the root cause.

        Args:
            task: Description of the incident to investigate
            messages: Conversation history
            context: Shared context for coordination

        Returns:
            Investigation results including root cause and evidence
        """
        # Update context with incident summary
        if not context.incident_summary:
            context.incident_summary = task[:500]

        # Build the investigation prompt
        plan = context.metadata.get("plan", ["Investigate the incident"])
        plan_steps = "\n".join(f"{i+1}. {step}" for i, step in enumerate(plan))
        volatile = f"Task: {task}\n\nInvestigation Plan:\n{plan_steps}"

        # Invoke the LLM
        response = self.invoke(
            messages=messages,
            context=context,
            volatile_prompt=volatile,
        )
        tokens = self.log_usage("investigate", response)

        # Extract evidence from tool calls/results in the response
        evidence = self._extract_evidence(messages, response)
        for source, items in evidence.items():
            context.add_evidence(source, items)

        # Check if we have tool calls to execute
        tool_calls = getattr(response, "tool_calls", [])
        if tool_calls:
            return {
                "status": "needs_tools",
                "result": None,
                "messages": [response],
                "evidence": evidence,
                "tokens_used": tokens,
                "tool_calls": tool_calls,
            }

        # No tool calls — this is a conclusion
        content = str(response.content)
        result = self._parse_investigation_result(content, context)

        # Update shared context with findings
        if result.get("root_cause"):
            context.root_cause = result["root_cause"]
            context.add_hypothesis(
                cause=result["root_cause"],
                confidence=result.get("confidence", "medium"),
                evidence=result.get("evidence_items", []),
                source_agent=self.role,
            )
        if result.get("affected_services"):
            context.affected_services = list(set(
                context.affected_services + result["affected_services"]
            ))
        if result.get("severity"):
            context.severity = result["severity"]

        # Send result to orchestrator
        self.send_message(
            to_agent=AgentRole.ORCHESTRATOR,
            message_type=MessageType.RESULT,
            content={
                "phase": "investigation",
                "root_cause": result.get("root_cause"),
                "confidence": result.get("confidence"),
                "evidence": result.get("evidence_items", []),
                "affected_services": result.get("affected_services", []),
                "severity": result.get("severity"),
                "summary": result.get("summary", ""),
            },
            context=context,
        )

        return {
            "status": "success",
            "result": result,
            "messages": [response],
            "evidence": evidence,
            "tokens_used": tokens,
        }

    def _extract_evidence(
        self,
        messages: list[Any],
        response: AIMessage,
    ) -> dict[str, list[str]]:
        """Extract evidence from tool results in the conversation."""
        evidence: dict[str, list[str]] = {}
        for msg in messages:
            if isinstance(msg, ToolMessage):
                name = getattr(msg, "name", "tool")
                content = msg.content if isinstance(msg.content, str) else str(msg.content)
                if name not in evidence:
                    evidence[name] = []
                # Truncate long tool outputs
                evidence[name].append(content[:500])
        return evidence

    def _parse_investigation_result(
        self,
        content: str,
        context: SharedContext,
    ) -> dict[str, Any]:
        """Parse the investigation conclusion from the LLM response."""
        result: dict[str, Any] = {
            "summary": content[:1000],
            "root_cause": None,
            "confidence": "medium",
            "severity": "SEV3",
            "affected_services": [],
            "evidence_items": [],
        }

        # Try to extract structured data from the response
        lower = content.lower()

        # Look for root cause markers
        for marker in ["root cause:", "root cause is", "root cause:", "caused by"]:
            if marker in lower:
                idx = lower.find(marker)
                # Extract the sentence after the marker
                after = content[idx + len(marker):].strip()
                # Take up to the next period or newline
                end = min(
                    after.find(".") if "." in after else len(after),
                    after.find("\n") if "\n" in after else len(after),
                )
                if end > 0:
                    result["root_cause"] = after[:end].strip()[:400]
                    break

        # Look for severity markers
        for sev in ["SEV1", "SEV2", "SEV3", "SEV4"]:
            if sev in content.upper():
                result["severity"] = sev
                break

        # Look for confidence markers
        for conf in ["high confidence", "medium confidence", "low confidence"]:
            if conf in lower:
                result["confidence"] = conf.split()[0]
                break

        # Extract service names (simple heuristic)
        for marker in ["affected service", "impacted service", "service:"]:
            if marker in lower:
                idx = lower.find(marker)
                after = content[idx:idx + 200]
                # Look for service-like names (lowercase with hyphens/underscores)
                import re
                services = re.findall(r"\b[a-z][a-z0-9_-]*(?:-svc|-service)?\b", after)
                result["affected_services"] = list(set(services))[:10]
                break

        # Gather evidence items from context
        result["evidence_items"] = context.all_evidence_list(max_items=10)

        return result
