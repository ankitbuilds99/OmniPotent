"""RemediationAgent — specialized for proposing and preparing fixes.

Takes a root cause analysis from InvestigatorAgent and proposes concrete
fixes with risk assessment, rollback plans, and resolution criteria.
"""

from __future__ import annotations

import logging
from typing import Any


from app.agents.base import BaseAgent
from app.agents.protocol import AgentRole, MessageType, SharedContext
from app.graph.prompts import REMEDIATION_AGENT_SYSTEM

log = logging.getLogger("devcopilot.agents.remediation")

# Write tools that the remediation agent can propose (all require approval)
_WRITE_TOOLS = {
    "create_pull_request",
    "scale_deployment",
    "rollback_deployment",
    "restart_deployment",
    "acknowledge_incident",
    "resolve_incident",
    "add_incident_note",
}

# Read tools the remediator can use to understand what to fix
_READ_TOOLS = {
    "read_file", "list_dir", "grep", "git_log",
    "list_recent_commits", "get_commit_diff",
    "get_deployment_status", "rollout_history",
    "get_metric", "search_logs",
}


class RemediationAgent(BaseAgent):
    """Agent specialized in proposing and preparing fixes.

    Focuses on:
    - Understanding the root cause context
    - Identifying what needs to change
    - Preparing concrete, executable fixes
    - Assessing risk and preparing rollback plans
    """

    @property
    def role(self) -> AgentRole:
        return AgentRole.REMEDIATION

    @property
    def system_prompt(self) -> str:
        return REMEDIATION_AGENT_SYSTEM

    def __init__(self, tools: list[Any] | None = None, fast: bool = False):
        # Filter to remediation-relevant tools
        if tools:
            allowed = _WRITE_TOOLS | _READ_TOOLS
            filtered = [t for t in tools if getattr(t, "name", "") in allowed]
            super().__init__(tools=filtered or tools, fast=fast)
        else:
            super().__init__(tools=tools, fast=fast)

    def process(
        self,
        task: str,
        messages: list[Any],
        context: SharedContext,
    ) -> dict[str, Any]:
        """Propose a fix for an identified root cause.

        Args:
            task: Description of what to fix (usually from orchestrator)
            messages: Conversation history
            context: Shared context with root cause and evidence

        Returns:
            Proposed fix with risk assessment and rollback plan
        """
        # Build remediation prompt with root cause and evidence
        root_cause = context.root_cause or "Not yet identified"
        evidence = "\n".join(context.all_evidence_list(max_items=20))

        volatile = self._format_remediation_prompt(task, root_cause, evidence)

        # Invoke the LLM
        response = self.invoke(
            messages=messages,
            context=context,
            volatile_prompt=volatile,
        )
        tokens = self.log_usage("remediate", response)

        # Check if we have tool calls (likely a write action)
        tool_calls = getattr(response, "tool_calls", [])
        if tool_calls:
            # Identify write actions that need review
            write_calls = [c for c in tool_calls if c.get("name") in _WRITE_TOOLS]
            if write_calls:
                # Send to reviewer before executing
                proposed_action = self._format_proposed_action(write_calls)
                self.send_message(
                    to_agent=AgentRole.REVIEWER,
                    message_type=MessageType.HANDOFF,
                    content={
                        "phase": "review_needed",
                        "proposed_action": proposed_action,
                        "tool_calls": [{"name": c["name"], "args": c.get("args", {})} for c in write_calls],
                        "root_cause": root_cause,
                    },
                    context=context,
                    requires_response=True,
                )
                return {
                    "status": "needs_review",
                    "result": {
                        "proposed_action": proposed_action,
                        "tool_calls": write_calls,
                    },
                    "messages": [response],
                    "tokens_used": tokens,
                }
            else:
                # Read-only tools, can execute
                return {
                    "status": "needs_tools",
                    "result": None,
                    "messages": [response],
                    "tokens_used": tokens,
                    "tool_calls": tool_calls,
                }

        # No tool calls — parse the remediation proposal
        content = str(response.content)
        result = self._parse_remediation_result(content, context)

        # If we have a proposed action, route to reviewer
        if result.get("proposed_action"):
            self.send_message(
                to_agent=AgentRole.ORCHESTRATOR,
                message_type=MessageType.RESULT,
                content={
                    "phase": "remediation_proposed",
                    "proposed_action": result["proposed_action"],
                    "risk": result.get("risk", "medium"),
                    "rollback_plan": result.get("rollback_plan", ""),
                    "resolution_criteria": result.get("resolution_criteria", []),
                },
                context=context,
            )

        return {
            "status": "success",
            "result": result,
            "messages": [response],
            "tokens_used": tokens,
        }

    def _format_remediation_prompt(
        self,
        task: str,
        root_cause: str,
        evidence: str,
    ) -> str:
        """Format the prompt for the remediation task."""
        return f"""
Task: {task}

Root cause to address:
{root_cause}

Evidence from investigation:
{evidence}

Propose a concrete fix that addresses this root cause.
"""

    def _format_proposed_action(self, tool_calls: list[dict]) -> dict[str, Any]:
        """Format tool calls into a proposed action description."""
        actions = []
        for call in tool_calls:
            name = call.get("name", "unknown")
            args = call.get("args", {})
            if name == "create_pull_request":
                actions.append({
                    "type": "pull_request",
                    "title": args.get("title", "Fix"),
                    "body": args.get("body", "")[:500],
                    "has_patch": bool(args.get("patch")),
                    "branch": args.get("branch", args.get("head", "")),
                })
            elif name in ("scale_deployment", "rollback_deployment", "restart_deployment"):
                actions.append({
                    "type": name,
                    "deployment": args.get("deployment", args.get("name", "")),
                    "namespace": args.get("namespace", "default"),
                    "replicas": args.get("replicas"),
                    "revision": args.get("revision"),
                })
            elif name in ("acknowledge_incident", "resolve_incident", "add_incident_note"):
                actions.append({
                    "type": name,
                    "incident_id": args.get("incident_id", args.get("id", "")),
                    "note": args.get("note", args.get("message", ""))[:200],
                })
            else:
                actions.append({
                    "type": name,
                    "args": args,
                })

        return {
            "actions": actions,
            "count": len(actions),
            "has_code_change": any(a["type"] == "pull_request" for a in actions),
            "has_infra_change": any(a["type"] in ("scale_deployment", "rollback_deployment", "restart_deployment") for a in actions),
        }

    def _parse_remediation_result(
        self,
        content: str,
        context: SharedContext,
    ) -> dict[str, Any]:
        """Parse the remediation proposal from the LLM response."""
        result: dict[str, Any] = {
            "summary": content[:1000],
            "proposed_action": None,
            "risk": "medium",
            "rollback_plan": "",
            "resolution_criteria": [],
        }

        lower = content.lower()

        # Look for risk assessment
        for risk in ["high risk", "medium risk", "low risk"]:
            if risk in lower:
                result["risk"] = risk.split()[0]
                break

        # Look for rollback plan
        for marker in ["rollback:", "rollback plan:", "to rollback:", "revert:"]:
            if marker in lower:
                idx = lower.find(marker)
                after = content[idx + len(marker):].strip()
                end = after.find("\n\n") if "\n\n" in after else min(len(after), 300)
                result["rollback_plan"] = after[:end].strip()
                break

        # Look for resolution criteria
        for marker in ["resolution criteria:", "to verify:", "confirm by:"]:
            if marker in lower:
                idx = lower.find(marker)
                after = content[idx + len(marker):].strip()
                # Extract bullet points or numbered items
                lines = after.split("\n")
                for line in lines[:5]:
                    line = line.strip().lstrip("-*123456789.)")
                    if line and len(line) > 10:
                        result["resolution_criteria"].append(line[:200])
                break

        # Look for proposed action summary
        for marker in ["proposed fix:", "recommendation:", "fix:", "action:"]:
            if marker in lower:
                idx = lower.find(marker)
                after = content[idx + len(marker):].strip()
                end = after.find("\n\n") if "\n\n" in after else min(len(after), 500)
                result["proposed_action"] = after[:end].strip()
                break

        return result
