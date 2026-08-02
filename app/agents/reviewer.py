"""ReviewerAgent — specialized for validating proposed actions.

Reviews fixes proposed by RemediationAgent before they're executed,
checking that they address the root cause and assessing risk.
"""

from __future__ import annotations

import json
import logging
from typing import Any


from app.agents.base import BaseAgent
from app.agents.protocol import AgentRole, MessageType, SharedContext
from app.graph.prompts import REVIEWER_AGENT_SYSTEM

log = logging.getLogger("omnipotent.agents.reviewer")


class ReviewerAgent(BaseAgent):
    """Agent specialized in reviewing and validating proposed actions.

    Focuses on:
    - Verifying fixes address the actual root cause
    - Assessing evidence sufficiency
    - Evaluating blast radius and risk
    - Identifying safer alternatives
    - Ensuring rollback plans are adequate
    """

    @property
    def role(self) -> AgentRole:
        return AgentRole.REVIEWER

    @property
    def system_prompt(self) -> str:
        return REVIEWER_AGENT_SYSTEM

    def __init__(self, tools: list[Any] | None = None, fast: bool = True):
        # Reviewer uses fast model by default (it's a judgment task, not investigation)
        # and doesn't need many tools (mostly reads to verify)
        super().__init__(tools=tools, fast=fast)

    def process(
        self,
        task: str,
        messages: list[Any],
        context: SharedContext,
    ) -> dict[str, Any]:
        """Review a proposed action and determine if it should proceed.

        Args:
            task: Description of the action to review
            messages: Conversation history
            context: Shared context with root cause and evidence

        Returns:
            Review verdict with rationale and conditions
        """
        # Build review prompt with action details, root cause, and evidence
        root_cause = context.root_cause or "Not identified"
        evidence = "\n".join(context.all_evidence_list(max_items=15))

        volatile = self._format_review_prompt(task, root_cause, evidence)

        # Invoke the LLM
        response = self.invoke(
            messages=messages,
            context=context,
            volatile_prompt=volatile,
        )
        tokens = self.log_usage("review", response)

        # Parse the review verdict
        content = str(response.content)
        result = self._parse_review_result(content)

        # Record the review in context
        context.add_timeline_event(
            event=f"Review verdict: {result.get('verdict', 'unknown')} - {result.get('rationale', '')[:100]}",
            source=self.name,
        )

        # Send result to orchestrator
        self.send_message(
            to_agent=AgentRole.ORCHESTRATOR,
            message_type=MessageType.RESULT,
            content={
                "phase": "review_complete",
                "verdict": result.get("verdict", "reject"),
                "confidence": result.get("confidence", "low"),
                "rationale": result.get("rationale", ""),
                "concerns": result.get("concerns", []),
                "conditions": result.get("conditions", []),
                "monitoring": result.get("monitoring", []),
            },
            context=context,
        )

        return {
            "status": "success",
            "result": result,
            "messages": [response],
            "tokens_used": tokens,
        }

    def _format_review_prompt(
        self,
        action: str,
        root_cause: str,
        evidence: str,
    ) -> str:
        """Format the prompt for the review task."""
        return f"""
Proposed action to review:
{action}

Root cause (from investigation):
{root_cause}

Evidence (from investigation):
{evidence}

Assess whether this action should be approved for execution.
Output your verdict as a JSON object.
"""

    def _parse_review_result(self, content: str) -> dict[str, Any]:
        """Parse the review verdict from the LLM response."""
        result: dict[str, Any] = {
            "verdict": "reject",  # Default to reject if unclear
            "confidence": "low",
            "rationale": "",
            "concerns": [],
            "alternatives": [],
            "conditions": [],
            "monitoring": [],
        }

        # Try to parse as JSON first
        try:
            start = content.find("{")
            end = content.rfind("}") + 1
            if start != -1 and end > start:
                data = json.loads(content[start:end])
                if isinstance(data, dict):
                    verdict = str(data.get("verdict", "")).lower().strip()
                    if verdict in ("approve", "approved", "yes", "accept"):
                        result["verdict"] = "approve"
                    elif verdict in ("needs_clarification", "clarify", "unclear"):
                        result["verdict"] = "needs_clarification"
                    else:
                        result["verdict"] = "reject"

                    conf = str(data.get("confidence", "")).lower().strip()
                    if conf in ("high", "medium", "low"):
                        result["confidence"] = conf

                    if data.get("rationale"):
                        result["rationale"] = str(data["rationale"])[:500]

                    for key in ("concerns", "alternatives", "conditions", "monitoring"):
                        if isinstance(data.get(key), list):
                            result[key] = [str(x)[:200] for x in data[key][:10]]

                    return result
        except (json.JSONDecodeError, ValueError):
            pass

        # Fall back to text parsing
        lower = content.lower()

        # Look for verdict
        if "approve" in lower and "reject" not in lower:
            result["verdict"] = "approve"
        elif "needs clarification" in lower or "need more information" in lower:
            result["verdict"] = "needs_clarification"
        else:
            result["verdict"] = "reject"

        # Look for confidence
        for conf in ["high confidence", "medium confidence", "low confidence"]:
            if conf in lower:
                result["confidence"] = conf.split()[0]
                break

        # Extract rationale (first substantial paragraph)
        lines = content.strip().split("\n")
        for line in lines:
            line = line.strip()
            if len(line) > 50 and not line.startswith(("{", "[", "-", "*", "1", "2", "3")):
                result["rationale"] = line[:500]
                break

        return result

    def quick_check(
        self,
        action: dict[str, Any],
        root_cause: str | None,
        evidence_count: int,
    ) -> dict[str, Any]:
        """Perform a quick, rule-based review check (no LLM call).

        This is used for fast pre-checks before invoking the full review.

        Args:
            action: The proposed action details
            root_cause: The identified root cause (if any)
            evidence_count: Number of evidence items gathered

        Returns:
            Quick check result with recommendation
        """
        concerns: list[str] = []
        recommendation = "proceed_to_review"

        # Check if root cause is established
        if not root_cause:
            concerns.append("No root cause identified - fix may not address the actual problem")
            recommendation = "block"

        # Check evidence sufficiency
        if evidence_count < 2:
            concerns.append("Insufficient evidence gathered before proposing fix")
            recommendation = "block"

        # Check for high-risk actions
        action_type = action.get("type", "")
        if action_type in ("scale_deployment", "restart_deployment"):
            if action.get("namespace") == "production" or "prod" in str(action.get("deployment", "")).lower():
                concerns.append("High-risk production deployment action")

        # Check for destructive actions
        if action_type == "rollback_deployment" and not action.get("revision"):
            concerns.append("Rollback without specific revision target")

        return {
            "recommendation": recommendation,
            "concerns": concerns,
            "evidence_count": evidence_count,
            "has_root_cause": bool(root_cause),
        }
