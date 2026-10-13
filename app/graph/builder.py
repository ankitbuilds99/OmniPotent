r"""Assemble the LangGraph state machine.

Supports two modes:
1. Single-agent mode (legacy): A single agent handles the full investigation
2. Multi-agent mode: Specialized agents collaborate (Investigator, Remediation, Reviewer)

Single-agent wiring:

    START -> plan -> agent --(route_after_agent)--> approval / tools / reflect
                       ^                                  |        |       |
                       |        approved -> tools <-------+        |       |
                       |        rejected -> agent                  |       |
                       +------------------ tools --(back to)-- agent       |
                       +-------------- reflect --(continue)-- agent        |
                       |                       \--(done)--> report --> verify
                       +--(verify: unverified fix, revise once)--/       |
                                              (verified / done) --> END <-+

Multi-agent wiring:

    START -> orchestrator -> investigator --(investigate)--> tools
                ^                  |                           |
                |                  v                           |
                |            root_cause_found -> remediation <-+
                |                                    |
                |                                    v
                |                              reviewer --(approve)--> approval
                |                                    |                   |
                +------------------------------------+                   v
                |                                               tools -> report -> END
                +---(reject/clarify)--- remediation <-----------+

The checkpointer persists state per `thread_id`, which is what makes the
human-in-the-loop interrupt resumable across separate API calls.
"""

from __future__ import annotations

import logging
import json
import re
import uuid
from contextlib import asynccontextmanager
from typing import Literal

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from app import audit, guardrails, redaction
from app.config import get_settings
from app.graph.edges import (
    route_after_agent,
    route_after_approval,
    route_after_reflect,
    route_after_verify,
)
from app.graph.nodes import (
    approval_node,
    make_agent_node,
    make_plan_node,
    make_reflect_node,
    make_report_node,
    make_verify_node,
)
from app.graph.state import AgentState

log = logging.getLogger("omnipotent.guardrails")


def _tool_failure_message(exc: Exception) -> str:
    """Convert one failed evidence call into a model-readable result.

    Read-only connector failures are evidence gaps, not graph failures. Keep
    the payload structured and sanitized so the agent can continue with the
    successful sources. Approval/policy failures retain their normal error
    semantics because they are not optional evidence calls.
    """
    from app.tool_trust.gateway import ToolGatewayError

    if isinstance(exc, ToolGatewayError):
        provider = exc.provider or "unknown"
        tool_name = exc.tool_name or "unknown"
        reason = exc.code.value
    else:
        provider = "unknown"
        tool_name = "unknown"
        reason = "tool_failure"
    return json.dumps({
        "status": "unavailable",
        "provider": provider,
        "tool": tool_name,
        "reason": reason,
    }, separators=(",", ":"))


def _apply_requested_kubernetes_namespace(state: AgentState, calls: list[dict]) -> list[dict]:
    """Fill omitted Kubernetes namespaces from the user's explicit request.

    Models sometimes infer the pod correctly but omit the namespace on a later
    call. That silently sends the request to the deployment default namespace.
    Only an explicit ``namespace X`` in the current user turn is trusted; no
    namespace is invented.
    """
    text = ""
    for message in reversed(state.get("messages") or []):
        if isinstance(message, HumanMessage):
            text = str(message.content)
            break
    match = re.search(r"\bnamespace\s+([a-z0-9][a-z0-9.-]*)", text, re.IGNORECASE)
    if not match:
        return calls
    namespace = match.group(1)
    k8s_tools = {"list_pods", "describe_pod", "get_events", "get_deployment_status", "rollout_history"}
    return [
        {**call, "args": {**(call.get("args") or {}), "namespace": namespace}}
        if call.get("name") in k8s_tools and not (call.get("args") or {}).get("namespace")
        else call
        for call in calls
    ]


def make_guarded_tool_node(tools):
    """ToolNode wrapped with prompt-injection defenses: every tool result is
    provenance-boxed and scanned for injection patterns before it re-enters the
    model's context, and any hit is audited. The agent therefore never sees raw
    untrusted telemetry — only labeled, defanged data."""
    # LangGraph's default handler re-raises ToolException. That made one
    # missing pod/metric terminate an otherwise useful investigation. Return a
    # structured unavailable evidence item instead, preserving the tool-call
    # pair so the model can continue and report partial evidence.
    inner = ToolNode(tools, handle_tool_errors=_tool_failure_message)

    async def guarded(state: AgentState) -> dict:
        from app import otel

        # ToolNode runs the tool calls on the *last* message. In multi-agent mode
        # the message carrying an approved write is not last — the reviewer's
        # verdict and the approval gate come after it — so the queue the approval
        # gate filled is the only place those calls still exist. Replay them onto
        # a scratch message the node can read; it is not returned, so it never
        # enters the transcript, and the ToolMessages it produces carry the
        # original tool-call ids and so answer the original proposal.
        payload = state
        queued = state.get("pending_tool_calls") or []
        messages = list(state.get("messages") or [])
        if messages and getattr(messages[-1], "tool_calls", None):
            rewritten = _apply_requested_kubernetes_namespace(state, list(messages[-1].tool_calls))
            if rewritten != list(messages[-1].tool_calls):
                messages[-1] = AIMessage(content=messages[-1].content, tool_calls=rewritten)
                payload = {**state, "messages": messages}
        if queued and not getattr(messages[-1] if messages else None, "tool_calls", None):
            queued = _apply_requested_kubernetes_namespace(state, list(queued))
            payload = {**state, "messages": [*messages, AIMessage(content="", tool_calls=queued)]}

        from app.tool_trust.gateway import approved_tool_execution

        with approved_tool_execution(bool(state.get("tool_execution_approved"))), otel.span("mcp.tools"):
            result = await inner.ainvoke(payload)
        out: list = []
        for m in result.get("messages", []):
            if isinstance(m, ToolMessage):
                raw = m.content if isinstance(m.content, str) else str(m.content)
                # 1) Redact PII/secrets BEFORE the model sees it or state persists it.
                redacted, entities = redaction.scrub(raw)
                if entities:
                    audit.record("telemetry.redacted", tool=m.name, count=len(entities),
                                 types=sorted({e["type"] for e in entities}))
                # 2) Provenance-box + injection-scan the (already redacted) output.
                clean, flags = guardrails.sanitize_tool_output(m.name or "tool", redacted)
                if flags:
                    log.warning("prompt-injection patterns in %s output: %s", m.name, flags)
                    audit.record("security.prompt_injection_detected", tool=m.name, patterns=flags)
                out.append(
                    ToolMessage(content=clean, tool_call_id=m.tool_call_id, name=m.name)
                )
            else:
                out.append(m)
        # Clear the multi-agent tool queue *here*, in the node. The routing
        # function that used to do it (`state.pop("pending_tool_calls", None)`)
        # cannot write graph state — LangGraph routers only choose an edge — so
        # the queue stayed set forever and every agent node bounced straight
        # back to `tools`, burning the recursion limit without ever reaching
        # `report`. Nodes write state; routers read it.
        return {
            "messages": out,
            "pending_tool_calls": None,
            "tool_execution_approved": False,
        }

    return guarded


def build_graph(tools, checkpointer, multi_agent: bool | None = None):
    """Compile the graph given the loaded MCP tools and a checkpointer.

    Args:
        tools: The MCP tools available to the agent(s)
        checkpointer: State persistence backend
        multi_agent: Override for multi-agent mode (None = use config)

    Returns:
        Compiled LangGraph state machine
    """
    settings = get_settings()

    # Determine agent mode
    if multi_agent is None:
        multi_agent = settings.copilot_multi_agent_enabled or settings.copilot_agent_mode == "multi"

    if multi_agent:
        return _build_multi_agent_graph(tools, checkpointer)
    else:
        return _build_single_agent_graph(tools, checkpointer)


def _build_single_agent_graph(tools, checkpointer):
    """Build the original single-agent graph (legacy mode)."""
    g = StateGraph(AgentState)

    # Chat-only mode: simplified graph with no tools, no reflect loop
    # This is much faster and uses fewer LLM calls (important for rate-limited providers)
    chat_only = not tools

    if chat_only:
        # Minimal graph: plan -> agent -> report -> END
        g.add_node("plan", make_plan_node())
        g.add_node("agent", make_agent_node(tools))
        g.add_node("report", make_report_node())

        g.add_edge(START, "plan")
        g.add_edge("plan", "agent")
        g.add_edge("agent", "report")
        g.add_edge("report", END)

        return g.compile(checkpointer=checkpointer)

    # Full graph with tools
    g.add_node("plan", make_plan_node())
    g.add_node("agent", make_agent_node(tools))
    # Executes read tools + approved writes, then provenance-boxes & injection-scans
    # every result before it re-enters the agent's context.
    g.add_node("tools", make_guarded_tool_node(tools))
    g.add_node("approval", approval_node)
    g.add_node("reflect", make_reflect_node())
    g.add_node("report", make_report_node())  # compile the structured RCA deliverable
    g.add_node("verify", make_verify_node())  # assess the proposed fix; may revise once

    g.add_edge(START, "plan")
    g.add_edge("plan", "agent")

    g.add_conditional_edges(
        "agent",
        route_after_agent,
        {"approval": "approval", "tools": "tools", "reflect": "reflect"},
    )
    g.add_conditional_edges(
        "approval",
        route_after_approval,
        {"tools": "tools", "agent": "agent"},
    )
    g.add_edge("tools", "agent")
    g.add_conditional_edges(
        "reflect",
        route_after_reflect,
        {"agent": "agent", "report": "report"},
    )
    # After the RCA is compiled, verify the proposed fix. verify either ends the run
    # or bounces an unverified fix back to the agent (bounded) to revise it.
    g.add_edge("report", "verify")
    g.add_conditional_edges(
        "verify",
        route_after_verify,
        {"agent": "agent", END: END},
    )

    return g.compile(checkpointer=checkpointer)


def _build_multi_agent_graph(tools, checkpointer):
    """Build the multi-agent collaborative graph.

    This graph uses specialized agents:
    - Orchestrator: Routes tasks and manages handoffs
    - Investigator: Finds root causes
    - Remediation: Proposes fixes
    - Reviewer: Validates actions before execution
    """
    from app.agents import OrchestratorAgent
    from app.agents.remediation import review_summary

    g = StateGraph(AgentState)

    # Create the orchestrator (it manages the agent team internally)
    orchestrator = OrchestratorAgent(tools=tools)

    # --- Node definitions ---

    def orchestrator_node(state: AgentState) -> dict:
        """Entry point: route the task through the orchestrator."""
        from app import otel

        with otel.span("agent.orchestrator"):
            # Get or create shared context
            context = _get_or_create_context(state)

            # Get the latest user message as the task
            task = ""
            for msg in reversed(state.get("messages", [])):
                if isinstance(msg, HumanMessage):
                    task = msg.content if isinstance(msg.content, str) else str(msg.content)
                    break

            result = orchestrator.process(
                task=task,
                messages=state.get("messages", []),
                context=context,
            )

            # Update state with results
            updates: dict = {
                "iteration": state.get("iteration", 0) + 1,
                "status": "investigating",
            }

            if result.get("messages"):
                updates["messages"] = result["messages"]
            if result.get("tokens_used"):
                updates["tokens_used"] = result["tokens_used"]

            # Store context in state metadata for persistence
            updates["multi_agent_context"] = result.get("context", context.to_dict())
            updates["multi_agent_phase"] = result.get("context", {}).get("metadata", {}).get("phase", "investigating")
            updates["multi_agent_agent"] = result.get("agent", "orchestrator")

            # Check if we need tools
            if result.get("status") == "needs_tools":
                updates["pending_tool_calls"] = result.get("tool_calls", [])

            # Check if complete
            if result.get("status") == "complete":
                updates["status"] = "done"
                updates["report"] = result.get("result", {})

            return updates

    def investigator_node(state: AgentState) -> dict:
        """Investigator agent processes investigation tasks."""
        from app import otel

        with otel.span("agent.investigator"):
            context = _get_or_create_context(state)
            task = _get_latest_task(state)

            result = orchestrator.investigator.process(
                task=task,
                messages=state.get("messages", []),
                context=context,
            )

            updates: dict = {
                "iteration": state.get("iteration", 0) + 1,
            }

            if result.get("messages"):
                updates["messages"] = result["messages"]
            if result.get("tokens_used"):
                updates["tokens_used"] = result["tokens_used"]

            updates["multi_agent_context"] = context.to_dict()
            updates["multi_agent_agent"] = "investigator"

            if result.get("status") == "needs_tools":
                updates["pending_tool_calls"] = result.get("tool_calls", [])

            return updates

    def remediation_node(state: AgentState) -> dict:
        """Remediation agent proposes fixes."""
        from app import otel

        with otel.span("agent.remediation"):
            context = _get_or_create_context(state)
            task = f"Propose a fix for: {context.root_cause or 'the identified issue'}"

            result = orchestrator.remediator.process(
                task=task,
                messages=state.get("messages", []),
                context=context,
            )

            updates: dict = {
                "iteration": state.get("iteration", 0) + 1,
            }

            if result.get("messages"):
                updates["messages"] = result["messages"]
            if result.get("tokens_used"):
                updates["tokens_used"] = result["tokens_used"]

            updates["multi_agent_context"] = context.to_dict()
            updates["multi_agent_agent"] = "remediation"

            if result.get("status") == "needs_tools":
                updates["pending_tool_calls"] = result.get("tool_calls", [])
            elif result.get("status") == "needs_review":
                # Deliberately not `pending_tool_calls`, even though the agent
                # now returns the calls at the top level here too: that channel
                # means "run these", and `route_after_remediation` checks it
                # first, so filling it here would jump the write straight to the
                # tool node past both the reviewer and the human gate. The calls
                # travel inside `pending_action` instead, and only the approval
                # node — after a human says yes — promotes them to the queue.
                updates["multi_agent_phase"] = "reviewing"
                updates["pending_action"] = result.get("result", {}).get("proposed_action")

            return updates

    def reviewer_node(state: AgentState) -> dict:
        """Reviewer agent validates proposed actions."""
        from app import otel

        with otel.span("agent.reviewer"):
            context = _get_or_create_context(state)
            pending_action = state.get("pending_action") or context.metadata.get("pending_action", {})
            # The display half only: `pending_action` also carries the verbatim
            # calls for the approval gate to execute, and stringifying those into
            # the prompt would ship a pull request's whole body and patch to the
            # model that the summary truncates precisely to avoid.
            task = f"Review this proposed action: {review_summary(pending_action)}"

            result = orchestrator.reviewer.process(
                task=task,
                messages=state.get("messages", []),
                context=context,
            )

            updates: dict = {
                "iteration": state.get("iteration", 0) + 1,
            }

            if result.get("messages"):
                updates["messages"] = result["messages"]
            if result.get("tokens_used"):
                updates["tokens_used"] = result["tokens_used"]

            updates["multi_agent_context"] = context.to_dict()
            updates["multi_agent_agent"] = "reviewer"

            verdict = result.get("result", {}).get("verdict", "reject")
            if verdict == "approve":
                updates["multi_agent_phase"] = "awaiting_approval"
            else:
                updates["multi_agent_phase"] = "remediating"

            return updates

    def multi_agent_reflect_node(state: AgentState) -> dict:
        """Reflection node for multi-agent: synthesize and decide next steps."""
        context = _get_or_create_context(state)
        phase = state.get("multi_agent_phase", "investigating")

        # Check iteration cap
        settings = get_settings()
        if state.get("iteration", 0) >= settings.copilot_max_iterations:
            return {"status": "done", "multi_agent_phase": "complete"}

        # Determine if investigation is complete
        if context.root_cause and phase == "investigating":
            return {"multi_agent_phase": "root_cause_found"}

        if phase == "complete":
            return {"status": "done"}

        return {}

    # --- Add nodes ---
    g.add_node("orchestrator", orchestrator_node)
    g.add_node("investigator", investigator_node)
    g.add_node("remediation", remediation_node)
    g.add_node("reviewer", reviewer_node)
    g.add_node("tools", make_guarded_tool_node(tools))
    g.add_node("approval", approval_node)
    g.add_node("reflect", multi_agent_reflect_node)
    g.add_node("report", make_report_node())
    g.add_node("verify", make_verify_node())

    # --- Routing functions ---

    def route_after_orchestrator(state: AgentState) -> Literal["investigator", "remediation", "reviewer", "tools", "report", "reflect"]:
        """Route based on orchestrator's decision and phase."""
        phase = state.get("multi_agent_phase", "investigating")
        _agent = state.get("multi_agent_agent", "orchestrator")  # noqa: F841

        # If tools are pending, execute them
        if state.get("pending_tool_calls"):
            return "tools"

        if phase == "investigating":
            return "investigator"
        elif phase == "root_cause_found":
            return "remediation"
        elif phase == "remediating":
            return "remediation"
        elif phase == "reviewing":
            return "reviewer"
        elif phase == "complete":
            return "report"
        else:
            return "reflect"

    def route_after_investigator(state: AgentState) -> Literal["tools", "reflect", "remediation"]:
        """Route after investigator processes."""
        if state.get("pending_tool_calls"):
            return "tools"

        context = _get_or_create_context(state)
        if context.root_cause:
            return "remediation"

        return "reflect"

    def route_after_remediation(state: AgentState) -> Literal["tools", "reviewer", "reflect"]:
        """Route after remediation proposes a fix."""
        if state.get("pending_tool_calls"):
            return "tools"

        phase = state.get("multi_agent_phase", "")
        if phase == "reviewing" or state.get("pending_action"):
            return "reviewer"

        return "reflect"

    def route_after_reviewer(state: AgentState) -> Literal["approval", "remediation", "report"]:
        """Route after reviewer validates."""
        phase = state.get("multi_agent_phase", "")

        if phase == "awaiting_approval":
            return "approval"
        elif phase == "remediating":
            return "remediation"
        else:
            return "report"

    def route_after_tools_multi(state: AgentState) -> Literal["investigator", "remediation", "orchestrator", "report"]:
        """Route back to the appropriate agent after tools execute.

        The queue is cleared by the tools node itself; a router cannot mutate
        state, so doing it here was a no-op that looped the graph forever.
        """
        agent = state.get("multi_agent_agent", "orchestrator")

        if agent == "investigator":
            return "investigator"
        elif agent == "remediation":
            return "remediation"
        elif agent == "approval":
            # The human-approved fix just ran, which is the end of the work:
            # write it up. Returning to an agent instead would re-enter the
            # phase machine still holding a root cause and propose the very
            # fix that was just executed, asking for approval a second time.
            return "report"
        else:
            return "orchestrator"

    def route_after_reflect_multi(state: AgentState) -> Literal["investigator", "remediation", "report"]:
        """Route after reflection in multi-agent mode."""
        phase = state.get("multi_agent_phase", "investigating")
        status = state.get("status", "investigating")

        if status == "done" or phase == "complete":
            return "report"
        elif phase == "root_cause_found":
            return "remediation"
        else:
            return "investigator"

    def route_after_approval_multi(state: AgentState) -> Literal["tools", "remediation", "report"]:
        """Route after human approval decision in multi-agent mode."""
        # An approval reached through `pending_action` leaves its calls in the
        # queue rather than on the last message, which here is still the
        # reviewer's verdict. Re-reading that message found no tool calls and
        # sent the approved write to `report`, so the run announced a fix that
        # had never run. Only the approval node writes this queue, so reaching
        # `tools` through it still requires having passed the human gate.
        if state.get("pending_tool_calls"):
            return "tools"

        last = state.get("messages", [])[-1] if state.get("messages") else None
        if last and getattr(last, "tool_calls", None):
            return "tools"

        # Check if approved or rejected
        if isinstance(last, ToolMessage) and "REJECTED" in str(last.content).upper():
            return "remediation"

        return "report"

    # --- Add edges ---
    g.add_edge(START, "orchestrator")

    g.add_conditional_edges(
        "orchestrator",
        route_after_orchestrator,
        {
            "investigator": "investigator",
            "remediation": "remediation",
            "reviewer": "reviewer",
            "tools": "tools",
            "report": "report",
            "reflect": "reflect",
        },
    )

    g.add_conditional_edges(
        "investigator",
        route_after_investigator,
        {"tools": "tools", "reflect": "reflect", "remediation": "remediation"},
    )

    g.add_conditional_edges(
        "remediation",
        route_after_remediation,
        {"tools": "tools", "reviewer": "reviewer", "reflect": "reflect"},
    )

    g.add_conditional_edges(
        "reviewer",
        route_after_reviewer,
        {"approval": "approval", "remediation": "remediation", "report": "report"},
    )

    g.add_conditional_edges(
        "tools",
        route_after_tools_multi,
        {
            "investigator": "investigator",
            "remediation": "remediation",
            "orchestrator": "orchestrator",
            "report": "report",
        },
    )

    g.add_conditional_edges(
        "approval",
        route_after_approval_multi,
        {"tools": "tools", "remediation": "remediation", "report": "report"},
    )

    g.add_conditional_edges(
        "reflect",
        route_after_reflect_multi,
        {"investigator": "investigator", "remediation": "remediation", "report": "report"},
    )

    g.add_edge("report", "verify")
    g.add_conditional_edges(
        "verify",
        route_after_verify,
        {"agent": "investigator", END: END},  # In multi-agent, route back to investigator
    )

    return g.compile(checkpointer=checkpointer)


def _get_or_create_context(state: AgentState):
    """Get or create a SharedContext from state."""
    from app.agents.protocol import SharedContext

    ctx_data = state.get("multi_agent_context")
    if ctx_data and isinstance(ctx_data, dict):
        return SharedContext.from_dict(ctx_data)

    # Create new context
    session_id = str(uuid.uuid4())
    return SharedContext(session_id=session_id)


def _get_latest_task(state: AgentState) -> str:
    """Get the latest task/user message from state."""
    for msg in reversed(state.get("messages", [])):
        if isinstance(msg, HumanMessage):
            return msg.content if isinstance(msg.content, str) else str(msg.content)
    return ""


def make_checkpointer():
    """Return an async checkpointer context manager.

    Defaults to SQLite (single-instance). If COPILOT_CHECKPOINT_DB is a Postgres
    URL, uses the Postgres saver instead — the swap that enables multi-instance
    deployments, since graph state is already keyed by thread_id. Postgres needs
    the optional `langgraph-checkpoint-postgres` package.
    """
    settings = get_settings()
    # An empty override (a chart that renders COPILOT_CHECKPOINT_DB: "") is not
    # "unset" — it would resolve to the state directory itself, which SQLite
    # cannot open. Treat it as the default filename.
    db = settings.copilot_checkpoint_db.strip() or "copilot_checkpoints.sqlite"
    if db.startswith(("postgres://", "postgresql://")):
        return _postgres_checkpointer(db)
    if db == ":memory:":  # SQLite's in-memory DB — not a filesystem path
        return AsyncSqliteSaver.from_conn_string(db)
    # Bare names land in the shared state directory (COPILOT_STATE_DIR) so the
    # checkpoint DB lives with the rest of this process's state instead of
    # wherever the server happened to be started from; an explicitly written
    # path ("./copilot_checkpoints.sqlite", "/var/lib/...") is honoured as-is.
    # state_path() raises when the directory can't be created rather than
    # quietly relocating resumable graph state to /tmp, where the next restart
    # would lose every in-flight human-in-the-loop investigation.
    return AsyncSqliteSaver.from_conn_string(str(settings.state_path(db)))


@asynccontextmanager
async def _postgres_checkpointer(conn_string: str):
    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    except ImportError as exc:  # noqa: TRY003
        raise RuntimeError(
            "Postgres checkpointer requires 'langgraph-checkpoint-postgres' "
            "(pip install langgraph-checkpoint-postgres)."
        ) from exc
    async with AsyncPostgresSaver.from_conn_string(conn_string) as saver:
        await saver.setup()  # idempotent: ensures the checkpoint tables exist
        yield saver
