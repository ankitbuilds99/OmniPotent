"""The agent's shared state — the object that flows through every graph node."""

from __future__ import annotations

from typing import Annotated, Any, Literal, TypedDict

from langgraph.graph.message import add_messages

Status = Literal["investigating", "awaiting_approval", "done", "failed"]

# Multi-agent phases for tracking investigation progress
MultiAgentPhase = Literal[
    "initial",
    "investigating",
    "root_cause_found",
    "remediating",
    "reviewing",
    "awaiting_approval",
    "executing",
    "verifying",
    "complete",
    "failed",
]


def _add_int(existing: int | None, update: int | None) -> int:
    """Additive reducer that treats a missing channel value as 0, so LLM nodes can
    each return their own token delta and have them accumulate across the run."""
    return (existing or 0) + (update or 0)


class AgentState(TypedDict, total=False):
    # Full conversation incl. tool calls/results. `add_messages` appends and
    # de-duplicates by id, so each node just returns the new messages.
    messages: Annotated[list, add_messages]

    # The investigation plan produced by the planner.
    plan: list[str]

    # A write tool call waiting for human approval (None when nothing pending).
    pending_action: dict | None

    # Loop guard so the agent can't spin forever.
    iteration: int

    # Reviewer note from the reflect node when it says CONTINUE — names the gap
    # the next agent pass should close, so the loop makes progress instead of
    # re-emitting a near-identical answer.
    feedback: str

    # Structured RCA deliverable produced by the report node when the
    # investigation finishes: ranked hypotheses + verdicts, cited evidence,
    # severity, confidence, recommended actions, and a rendered postmortem.
    report: dict | None

    # Running sum of LLM tokens spent this turn (every node adds its call's total).
    # Drives the per-investigation cost kill-switch and is surfaced to the UI.
    tokens_used: Annotated[int, _add_int]

    # Fix-verification verdict produced by the verify node when the report proposes
    # a fix: does the proposed remediation address the root cause, what signal would
    # confirm resolution, residual risks. None when verification didn't run.
    verification: dict | None

    # How many times the verify node has bounced the run back to the agent to revise
    # a fix that missed the root cause. Bounds that loop (see copilot_verify_max_attempts).
    verify_attempts: Annotated[int, _add_int]

    # Lifecycle marker, used by the API/CLI to know when to stop.
    status: Status

    # --- Multi-agent mode fields ---
    # These fields are only used when COPILOT_MULTI_AGENT_ENABLED is true.

    # Serialized SharedContext for cross-agent coordination. Contains evidence,
    # hypotheses, timeline, and inter-agent messages. Persisted so the multi-agent
    # investigation can be resumed across API calls.
    multi_agent_context: dict[str, Any] | None

    # Current phase in the multi-agent workflow (see MultiAgentPhase type).
    multi_agent_phase: MultiAgentPhase

    # Which agent is currently active (orchestrator, investigator, remediation, reviewer).
    multi_agent_agent: str

    # Pending tool calls that need to be executed. Set by agent nodes when they
    # request tool execution; cleared by the tools node after execution.
    pending_tool_calls: list[dict[str, Any]] | None
