"""Tests for the multi-agent collaboration system.

Tests the protocol, agents, and orchestration without making real LLM calls.
"""

from __future__ import annotations

from unittest.mock import patch

from app.agents.protocol import (
    AgentHandoff,
    AgentMessage,
    AgentRole,
    MessageType,
    SharedContext,
    TaskStatus,
)
from app.agents.investigator import InvestigatorAgent
from app.agents.remediation import RemediationAgent
from app.agents.reviewer import ReviewerAgent
from app.agents.orchestrator import OrchestratorAgent, InvestigationPhase


class TestAgentMessage:
    """Tests for AgentMessage serialization."""

    def test_message_creation(self):
        msg = AgentMessage(
            id="test-123",
            from_agent=AgentRole.INVESTIGATOR,
            to_agent=AgentRole.ORCHESTRATOR,
            message_type=MessageType.RESULT,
            content={"root_cause": "null check missing"},
        )
        assert msg.from_agent == AgentRole.INVESTIGATOR
        assert msg.to_agent == AgentRole.ORCHESTRATOR
        assert msg.content["root_cause"] == "null check missing"

    def test_message_serialization(self):
        msg = AgentMessage(
            id="test-456",
            from_agent=AgentRole.REMEDIATION,
            to_agent=AgentRole.REVIEWER,
            message_type=MessageType.HANDOFF,
            content={"action": "create_pr"},
            priority=1,
        )
        data = msg.to_dict()
        assert data["from_agent"] == "remediation"
        assert data["to_agent"] == "reviewer"
        assert data["priority"] == 1

        restored = AgentMessage.from_dict(data)
        assert restored.from_agent == AgentRole.REMEDIATION
        assert restored.priority == 1


class TestAgentHandoff:
    """Tests for AgentHandoff task delegation."""

    def test_handoff_creation(self):
        handoff = AgentHandoff(
            id="handoff-1",
            from_agent=AgentRole.ORCHESTRATOR,
            to_agent=AgentRole.INVESTIGATOR,
            task="Investigate the checkout service 5xx spike",
        )
        assert handoff.status == TaskStatus.PENDING
        assert handoff.result is None

    def test_handoff_completion(self):
        handoff = AgentHandoff(
            id="handoff-2",
            from_agent=AgentRole.ORCHESTRATOR,
            to_agent=AgentRole.INVESTIGATOR,
            task="Find root cause",
        )
        handoff.complete({"root_cause": "database connection pool exhaustion"})
        assert handoff.status == TaskStatus.COMPLETED
        assert handoff.result["root_cause"] == "database connection pool exhaustion"

    def test_handoff_failure(self):
        handoff = AgentHandoff(
            id="handoff-3",
            from_agent=AgentRole.ORCHESTRATOR,
            to_agent=AgentRole.REMEDIATION,
            task="Propose fix",
        )
        handoff.fail("Insufficient evidence")
        assert handoff.status == TaskStatus.FAILED
        assert "error" in handoff.result


class TestSharedContext:
    """Tests for SharedContext cross-agent state."""

    def test_context_creation(self):
        ctx = SharedContext(session_id="session-123")
        assert ctx.session_id == "session-123"
        assert ctx.severity == "SEV3"
        assert ctx.confidence == "low"

    def test_add_evidence(self):
        ctx = SharedContext(session_id="s1")
        ctx.add_evidence("datadog", ["error spike at 14:32", "500 errors in checkout"])
        ctx.add_evidence("sentry", ["NullPointerException at line 42"])

        assert len(ctx.evidence["datadog"]) == 2
        assert len(ctx.evidence["sentry"]) == 1

    def test_add_hypothesis(self):
        ctx = SharedContext(session_id="s2")
        ctx.add_hypothesis(
            cause="Database connection timeout",
            confidence="medium",
            evidence=["connection pool metrics elevated", "slow query logs"],
            source_agent=AgentRole.INVESTIGATOR,
        )

        assert len(ctx.hypotheses) == 1
        assert ctx.hypotheses[0]["cause"] == "Database connection timeout"
        assert ctx.hypotheses[0]["source_agent"] == "investigator"

    def test_all_evidence_list(self):
        ctx = SharedContext(session_id="s3")
        ctx.add_evidence("logs", ["log1", "log2"])
        ctx.add_evidence("metrics", ["metric1"])

        all_ev = ctx.all_evidence_list()
        assert len(all_ev) == 3
        assert any("[logs]" in e for e in all_ev)
        assert any("[metrics]" in e for e in all_ev)

    def test_serialization(self):
        ctx = SharedContext(session_id="s4")
        ctx.incident_summary = "Checkout failing"
        ctx.severity = "SEV2"
        ctx.root_cause = "Null check missing"
        ctx.add_evidence("sentry", ["NullPointerException"])

        data = ctx.to_dict()
        restored = SharedContext.from_dict(data)

        assert restored.session_id == "s4"
        assert restored.severity == "SEV2"
        assert restored.root_cause == "Null check missing"
        assert "sentry" in restored.evidence


class TestReviewerQuickCheck:
    """Tests for ReviewerAgent's quick validation checks."""

    def test_quick_check_no_root_cause(self):
        reviewer = ReviewerAgent(tools=None, fast=True)
        result = reviewer.quick_check(
            action={"type": "create_pull_request"},
            root_cause=None,
            evidence_count=5,
        )
        assert result["recommendation"] == "block"
        assert any("No root cause" in c for c in result["concerns"])

    def test_quick_check_insufficient_evidence(self):
        reviewer = ReviewerAgent(tools=None, fast=True)
        result = reviewer.quick_check(
            action={"type": "rollback_deployment"},
            root_cause="Bad deploy",
            evidence_count=1,
        )
        assert result["recommendation"] == "block"
        assert any("Insufficient evidence" in c for c in result["concerns"])

    def test_quick_check_high_risk_action(self):
        reviewer = ReviewerAgent(tools=None, fast=True)
        result = reviewer.quick_check(
            action={"type": "scale_deployment", "deployment": "prod-checkout"},
            root_cause="Resource exhaustion",
            evidence_count=5,
        )
        # Should flag high-risk production action
        assert any("production" in c.lower() for c in result["concerns"])

    def test_quick_check_passes(self):
        reviewer = ReviewerAgent(tools=None, fast=True)
        result = reviewer.quick_check(
            action={"type": "create_pull_request"},
            root_cause="Null check missing in checkout.js:42",
            evidence_count=5,
        )
        assert result["recommendation"] == "proceed_to_review"
        assert result["has_root_cause"] is True


class TestInvestigationPhases:
    """Tests for OrchestratorAgent phase management."""

    def test_initial_phase(self):
        ctx = SharedContext(session_id="test")
        assert ctx.metadata.get("phase") is None

    def test_phase_transitions(self):
        ctx = SharedContext(session_id="test")
        ctx.metadata["phase"] = InvestigationPhase.INITIAL
        assert ctx.metadata["phase"] == "initial"

        ctx.metadata["phase"] = InvestigationPhase.INVESTIGATING
        assert ctx.metadata["phase"] == "investigating"

        ctx.metadata["phase"] = InvestigationPhase.ROOT_CAUSE_FOUND
        assert ctx.metadata["phase"] == "root_cause_found"


class TestAgentRoles:
    """Tests for agent role properties."""

    def test_investigator_role(self):
        inv = InvestigatorAgent(tools=None)
        assert inv.role == AgentRole.INVESTIGATOR
        assert "Investigator" in inv.name

    def test_remediation_role(self):
        rem = RemediationAgent(tools=None)
        assert rem.role == AgentRole.REMEDIATION
        assert "Remediation" in rem.name

    def test_reviewer_role(self):
        rev = ReviewerAgent(tools=None)
        assert rev.role == AgentRole.REVIEWER
        assert "Reviewer" in rev.name

    def test_orchestrator_role(self):
        orch = OrchestratorAgent(tools=None)
        assert orch.role == AgentRole.ORCHESTRATOR
        assert "Orchestrator" in orch.name


class TestOrchestratorTeam:
    """Tests for OrchestratorAgent's agent team management."""

    def test_lazy_agent_creation(self):
        orch = OrchestratorAgent(tools=None)
        # Agents not created until accessed
        assert orch._investigator is None
        assert orch._remediator is None
        assert orch._reviewer is None

        # Access triggers creation
        _ = orch.investigator
        assert orch._investigator is not None

    def test_get_status(self):
        orch = OrchestratorAgent(tools=None)
        ctx = SharedContext(session_id="test-session")
        ctx.root_cause = "Test cause"
        ctx.severity = "SEV2"
        ctx.add_evidence("logs", ["log1", "log2", "log3"])
        ctx.add_hypothesis(
            cause="Test hypothesis",
            confidence="high",
            evidence=["evidence1"],
            source_agent=AgentRole.INVESTIGATOR,
        )

        status = orch.get_status(ctx)
        assert status["session_id"] == "test-session"
        assert status["root_cause_found"] is True
        assert status["severity"] == "SEV2"
        assert status["evidence_count"] == 3
        assert status["hypotheses_count"] == 1


class TestMessageRouting:
    """Tests for inter-agent message passing."""

    def test_send_message(self):
        ctx = SharedContext(session_id="test")
        inv = InvestigatorAgent(tools=None)

        inv.send_message(
            to_agent=AgentRole.ORCHESTRATOR,
            message_type=MessageType.RESULT,
            content={"root_cause": "found it"},
            context=ctx,
        )

        assert len(ctx.messages) == 1
        assert ctx.messages[0].from_agent == AgentRole.INVESTIGATOR
        assert ctx.messages[0].to_agent == AgentRole.ORCHESTRATOR

    def test_create_handoff(self):
        ctx = SharedContext(session_id="test")
        orch = OrchestratorAgent(tools=None)

        orch.create_handoff(
            to_agent=AgentRole.INVESTIGATOR,
            task="Find root cause",
            context=ctx,
            expected_output="Root cause with evidence",
        )

        assert len(ctx.handoffs) == 1
        assert ctx.handoffs[0].to_agent == AgentRole.INVESTIGATOR
        assert ctx.handoffs[0].status == TaskStatus.PENDING


class TestConfigIntegration:
    """Tests that multi-agent config settings work correctly."""

    def test_multi_agent_disabled_by_default(self):
        from app.config import get_settings
        settings = get_settings()
        assert settings.copilot_multi_agent_enabled is False
        assert settings.copilot_agent_mode == "single"

    @patch.dict("os.environ", {"COPILOT_MULTI_AGENT_ENABLED": "true"})
    def test_multi_agent_enabled_via_env(self):
        # Need to clear the lru_cache to test with new env
        from app.config import get_settings
        get_settings.cache_clear()
        settings = get_settings()
        assert settings.copilot_multi_agent_enabled is True
        get_settings.cache_clear()  # Clean up


class TestPrompts:
    """Tests that agent prompts are properly defined."""

    def test_investigator_prompt_exists(self):
        from app.graph.prompts import INVESTIGATOR_AGENT_SYSTEM
        assert "InvestigatorAgent" in INVESTIGATOR_AGENT_SYSTEM
        assert "root cause" in INVESTIGATOR_AGENT_SYSTEM.lower()

    def test_remediation_prompt_exists(self):
        from app.graph.prompts import REMEDIATION_AGENT_SYSTEM
        assert "RemediationAgent" in REMEDIATION_AGENT_SYSTEM
        assert "fix" in REMEDIATION_AGENT_SYSTEM.lower()

    def test_reviewer_prompt_exists(self):
        from app.graph.prompts import REVIEWER_AGENT_SYSTEM
        assert "ReviewerAgent" in REVIEWER_AGENT_SYSTEM
        assert "validating" in REVIEWER_AGENT_SYSTEM.lower()

    def test_orchestrator_prompt_exists(self):
        from app.graph.prompts import ORCHESTRATOR_AGENT_SYSTEM
        assert "OrchestratorAgent" in ORCHESTRATOR_AGENT_SYSTEM
        assert "coordinator" in ORCHESTRATOR_AGENT_SYSTEM.lower()


class TestBackwardsCompatibility:
    """Tests that single-agent mode still works with multi-agent code present."""

    def test_single_agent_graph_builds(self):
        from app.graph.builder import build_graph, _build_single_agent_graph

        # Just verify the function is callable and doesn't error
        # (actual graph building needs tools and checkpointer)
        assert callable(build_graph)
        assert callable(_build_single_agent_graph)

    def test_multi_agent_graph_builds(self):
        from app.graph.builder import _build_multi_agent_graph
        assert callable(_build_multi_agent_graph)

    def test_state_has_multi_agent_fields(self):
        from app.graph.state import AgentState
        # Verify new fields exist in the TypedDict
        annotations = AgentState.__annotations__
        assert "multi_agent_context" in annotations
        assert "multi_agent_phase" in annotations
        assert "multi_agent_agent" in annotations
        assert "pending_tool_calls" in annotations

    def test_state_backwards_compatible(self):
        from app.graph.state import AgentState
        # Old fields still exist
        annotations = AgentState.__annotations__
        assert "messages" in annotations
        assert "plan" in annotations
        assert "pending_action" in annotations
        assert "status" in annotations
        assert "report" in annotations


# --------------------------------------------------------------------------- #
# The graph, actually executed.
#
# Everything above this line builds objects and asserts on their shape; nothing
# ran the multi-agent graph. It did not work: the tools node returned only
# {"messages": ...} and never cleared `pending_tool_calls`, while the clearing
# lived in `route_after_tools_multi` — a routing function, which LangGraph calls
# for its return value and whose mutations of the state dict are discarded. So
# the queue stayed set, every agent node routed straight back to `tools`, and
# the run died with GraphRecursionError having never reached `report`:
#
#   ['orchestrator', 'tools', 'investigator', 'tools', 'investigator', ... x15]
# --------------------------------------------------------------------------- #
class _ScriptedModel:
    """Chat model stub: asks for one tool call, then states a root cause."""

    def __init__(self) -> None:
        self.calls = 0

    def bind_tools(self, tools, **kwargs):
        return self

    def with_structured_output(self, *a, **kw):
        raise NotImplementedError("stub model has no structured-output support")

    def invoke(self, messages, *args, **kwargs):
        from langchain_core.messages import AIMessage

        self.calls += 1
        if self.calls == 1:
            return AIMessage(
                content="",
                tool_calls=[
                    {"name": "search_logs", "args": {"q": "err"}, "id": "call_1",
                     "type": "tool_call"}
                ],
            )
        return AIMessage(
            content="Root cause: the checkout deploy at 12:00 introduced a null "
                    "pointer.\nhigh confidence, SEV2."
        )


def _fake_tool():
    from langchain_core.tools import tool

    @tool
    def search_logs(q: str) -> str:
        """Search logs for a query."""
        return "ERROR NullPointerException at checkout.applyDiscount"

    return search_logs


class TestGuardedToolNodeState:
    async def test_one_tool_failure_becomes_partial_unavailable_evidence(self):
        from langchain_core.messages import AIMessage, ToolMessage
        from langchain_core.tools import tool
        from langgraph.graph import END, START, StateGraph

        from app.graph.builder import make_guarded_tool_node
        from app.graph.state import AgentState

        @tool
        async def failing_evidence(query: str) -> str:
            """Evidence source that is unavailable for this test."""
            raise RuntimeError("provider credential must never reach the model")

        g = StateGraph(AgentState)
        g.add_node("tools", make_guarded_tool_node([failing_evidence]))
        g.add_edge(START, "tools")
        g.add_edge("tools", END)
        call = {"name": "failing_evidence", "args": {"query": "x"}, "id": "fail-1",
                "type": "tool_call"}
        out = await g.compile().ainvoke({"messages": [AIMessage(content="", tool_calls=[call])]})
        result = next(m for m in out["messages"] if isinstance(m, ToolMessage))
        assert '"status":"unavailable"' in result.content
        assert "provider" in result.content
        assert "credential" not in result.content

    async def test_explicit_namespace_is_added_to_omitted_kubernetes_calls(self):
        from app.graph.builder import _apply_requested_kubernetes_namespace
        from langchain_core.messages import HumanMessage

        state = {"messages": [HumanMessage(content=(
            "Investigate crashloop-demo in namespace omnipotent-demo"
        ))]}
        calls = [{"name": "describe_pod", "args": {"name": "crashloop-demo"}, "id": "p-1"}]
        rewritten = _apply_requested_kubernetes_namespace(state, calls)
        assert rewritten[0]["args"]["namespace"] == "omnipotent-demo"

    async def test_the_tools_node_clears_the_pending_tool_queue(self):
        """A node writes state; the router that used to `pop` it could not.

        Runs the node inside a one-node graph because ToolNode needs the
        LangGraph runtime — which is also the point: it only ever runs there.
        """
        from langchain_core.messages import AIMessage
        from langgraph.graph import END, START, StateGraph

        from app.graph.builder import make_guarded_tool_node
        from app.graph.state import AgentState

        g = StateGraph(AgentState)
        g.add_node("tools", make_guarded_tool_node([_fake_tool()]))
        g.add_edge(START, "tools")
        g.add_edge("tools", END)

        call = {"name": "search_logs", "args": {"q": "err"}, "id": "call_1",
                "type": "tool_call"}
        out = await g.compile().ainvoke({
            "messages": [AIMessage(content="", tool_calls=[call])],
            "pending_tool_calls": [call],
        })

        assert any(getattr(m, "name", None) == "search_logs" for m in out["messages"])
        assert out.get("pending_tool_calls") is None, "the queue is never cleared"


class TestMultiAgentGraphRuns:
    """End-to-end execution of the multi-agent graph with a stubbed model."""

    async def _run(self, monkeypatch):
        from langchain_core.messages import HumanMessage
        from langgraph.checkpoint.memory import MemorySaver

        import app.agents.base as agents_base
        import app.config as cfg
        import app.graph.nodes as nodes
        from app.graph.builder import build_graph

        monkeypatch.setenv("COPILOT_MAX_ITERATIONS", "5")
        cfg.get_settings.cache_clear()
        model = _ScriptedModel()
        monkeypatch.setattr(agents_base, "get_llm", lambda *a, **kw: model)
        monkeypatch.setattr(nodes, "get_llm", lambda *a, **kw: model)

        graph = build_graph([_fake_tool()], MemorySaver(), multi_agent=True)
        config = {"configurable": {"thread_id": "multi-e2e"}, "recursion_limit": 30}

        seen: list[str] = []
        async for event in graph.astream(
            {"messages": [HumanMessage(content="checkout is 500ing")]},
            config,
            stream_mode="updates",
        ):
            seen.extend(event)
        state = await graph.aget_state(config)
        cfg.get_settings.cache_clear()
        return seen, state

    async def test_the_graph_reaches_report_instead_of_looping_on_tools(self, monkeypatch):
        seen, state = await self._run(monkeypatch)

        assert "report" in seen, f"never reached the report node: {seen}"
        assert state.values.get("report"), "no RCA deliverable produced"

    async def test_tools_hand_back_to_the_agent_that_asked_for_them(self, monkeypatch):
        seen, state = await self._run(monkeypatch)

        # tools ran once and control returned to the investigator, rather than
        # bouncing investigator -> tools -> investigator forever.
        assert seen.count("tools") == 1, seen
        assert seen[seen.index("tools") + 1] == "investigator", seen
        assert state.values.get("pending_tool_calls") is None
