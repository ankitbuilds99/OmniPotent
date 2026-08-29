"""The multi-agent path from a proposed write to an executed one.

The single-agent graph runs ``agent -> approval -> tools``, so the message
carrying the write is still the last one when the human is asked. The
multi-agent graph runs ``remediation -> reviewer -> approval -> tools``, and the
reviewer's verdict lands in between. Everything downstream of the proposal used
to read that last message and find nothing: the operator was interrupted with an
empty action list, and an approved fix was routed to ``report`` and never run.

These tests drive the real graph, so they fail if any link in that chain — what
the remediation agent returns, what the remediation node stores, what the
approval node recovers, where the approval router sends it, or what the tool
node executes — stops carrying the proposal.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from app.agents.protocol import SharedContext
from app.agents.remediation import RemediationAgent, review_summary
from app.graph.nodes import approval_node
from app.graph.state import AgentState

_ROLLBACK = {
    "name": "rollback_deployment",
    "args": {"deployment": "checkout", "namespace": "production", "revision": "41"},
    "id": "call_write",
    "type": "tool_call",
}
_READ = {
    "name": "get_deployment_status",
    "args": {"deployment": "checkout", "namespace": "production"},
    "id": "call_read",
    "type": "tool_call",
}


def _proposed_action(tool_calls: list[dict]) -> dict:
    """The `proposed_action` the remediation agent really produces for these calls."""
    with patch.object(RemediationAgent, "invoke", return_value=AIMessage(content="", tool_calls=tool_calls)), \
         patch.object(RemediationAgent, "log_usage", return_value=0), \
         patch.object(RemediationAgent, "send_message"):
        agent = RemediationAgent()
        result = agent.process("fix it", [], SharedContext(session_id="t"))
    return result["result"]["proposed_action"]


# --------------------------------------------------------------------------
# The approval gate itself, on a real (one-node) graph so `interrupt()` runs.
# --------------------------------------------------------------------------


def _run_approval(values: dict, resume) -> tuple[dict | None, dict]:
    g = StateGraph(AgentState)
    g.add_node("approval", approval_node)
    g.add_edge(START, "approval")
    g.add_edge("approval", END)
    graph = g.compile(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": f"approval-{id(values)}"}}

    payload = None
    for event in graph.stream(values, config, stream_mode="updates"):
        if "__interrupt__" in event:
            payload = event["__interrupt__"][0].value
    for _ in graph.stream(Command(resume=resume), config, stream_mode="updates"):
        pass
    return payload, graph.get_state(config).values


def _reviewed_state(tool_calls: list[dict]) -> dict:
    """State as the approval node finds it in multi-agent mode: the proposal is
    two messages back, behind the reviewer's verdict."""
    return {
        "messages": [
            HumanMessage(content="checkout is 500ing"),
            AIMessage(content="", tool_calls=tool_calls),
            AIMessage(content='{"verdict": "approve"}'),
        ],
        "pending_action": _proposed_action(tool_calls),
    }


class TestTheOperatorSeesTheAction:
    def test_the_prompt_describes_the_write_behind_the_reviewer_verdict(self):
        payload, _ = _run_approval(_reviewed_state([_ROLLBACK]), {"approved": False})

        assert payload is not None, "the graph never paused for approval"
        assert [a["tool"] for a in payload["actions"]] == ["rollback_deployment"]
        assert payload["actions"][0]["args"]["namespace"] == "production"
        # An action list is the only thing the operator is shown, so it has to
        # carry the classification too, not just a name.
        assert payload["actions"][0]["write"] is True
        assert payload["actions"][0]["preview"]
        assert payload["risk"] == "high"

    def test_a_co_bundled_read_is_shown_as_well(self):
        """Approving runs the whole message, so nothing on it may be invisible."""
        payload, _ = _run_approval(_reviewed_state([_ROLLBACK, _READ]), {"approved": False})

        assert [a["tool"] for a in payload["actions"]] == [
            "rollback_deployment",
            "get_deployment_status",
        ]


class TestTheDecisionIsActedOn:
    def test_approving_queues_the_calls_for_the_tool_node(self):
        _, final = _run_approval(_reviewed_state([_ROLLBACK]), {"approved": True})

        queued = final["pending_tool_calls"]
        assert [c["id"] for c in queued] == ["call_write"]
        assert queued[0]["name"] == "rollback_deployment"
        assert queued[0]["args"]["revision"] == "41"
        # Must be a well-formed tool call: the tool node replays these verbatim.
        assert queued[0]["type"] == "tool_call"
        assert final["pending_action"] is None

    def test_rejecting_answers_the_recovered_ids_and_queues_nothing(self):
        _, final = _run_approval(
            _reviewed_state([_ROLLBACK]), {"approved": False, "reason": "peak traffic"}
        )

        rejections = [m for m in final["messages"] if isinstance(m, ToolMessage)]
        assert [m.tool_call_id for m in rejections] == ["call_write"]
        assert "REJECTED" in rejections[0].content
        assert "peak traffic" in rejections[0].content
        # A rejected write must not leave anything runnable behind.
        assert not final.get("pending_tool_calls")

    def test_the_single_agent_shape_is_untouched(self):
        """When the proposal *is* the last message the gate changes nothing: the
        message stands and `route_after_approval` executes it off the message."""
        values = {
            "messages": [
                HumanMessage(content="roll it back"),
                AIMessage(content="", tool_calls=[_ROLLBACK]),
            ],
            "pending_action": _proposed_action([_ROLLBACK]),
        }
        _, final = _run_approval(values, {"approved": True})

        assert not final.get("pending_tool_calls")
        assert final["messages"][-1].tool_calls[0]["id"] == "call_write"


# --------------------------------------------------------------------------
# End to end on the real multi-agent graph.
# --------------------------------------------------------------------------

_EXECUTED: list[tuple] = []


@pytest.fixture
def _settings_cache_isolated():
    """These tests set COPILOT_MAX_ITERATIONS, which is read through a cached
    Settings object shared by the whole process."""
    import app.config as cfg

    cfg.get_settings.cache_clear()
    yield
    cfg.get_settings.cache_clear()


def _rollback_tool():
    @tool
    def rollback_deployment(deployment: str, namespace: str, revision: str) -> str:
        """Roll a deployment back to a previous revision."""
        _EXECUTED.append((deployment, namespace, revision))
        return f"rolled {deployment} back to {revision}"

    return rollback_deployment


class _ScriptedModel:
    """Root cause, then the rollback proposal, then an approving review verdict."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def bind_tools(self, tools, **kwargs):
        return self

    def with_structured_output(self, *a, **kw):
        raise NotImplementedError("stub model has no structured-output support")

    def invoke(self, messages, *args, **kwargs):
        text = " ".join(str(getattr(m, "content", "")) for m in messages)
        self.prompts.append(text)
        if "Proposed action to review" in text:
            return AIMessage(
                content='{"verdict": "approve", "confidence": "high", '
                        '"rationale": "revision 41 is the last known-good deploy"}'
            )
        if "Propose a fix" in text:
            return AIMessage(content="", tool_calls=[dict(_ROLLBACK)])
        return AIMessage(
            content="Root cause: the checkout deploy at 12:00 shipped a null "
                    "pointer.\nhigh confidence, SEV2."
        )


def _multi_agent_graph(monkeypatch, thread: str):
    import app.agents.base as agents_base
    import app.config as cfg
    import app.graph.nodes as nodes
    from app.graph.builder import build_graph

    monkeypatch.setenv("COPILOT_MAX_ITERATIONS", "6")
    cfg.get_settings.cache_clear()
    model = _ScriptedModel()
    monkeypatch.setattr(agents_base, "get_llm", lambda *a, **kw: model)
    monkeypatch.setattr(nodes, "get_llm", lambda *a, **kw: model)

    graph = build_graph([_rollback_tool()], MemorySaver(), multi_agent=True)
    config = {"configurable": {"thread_id": thread}, "recursion_limit": 30}
    return graph, config, model


async def _drive(graph, config, payload) -> tuple[list[str], list[dict]]:
    seen: list[str] = []
    interrupts: list[dict] = []
    async for event in graph.astream(payload, config, stream_mode="updates"):
        seen.extend(event)
        for item in event.get("__interrupt__", ()):
            interrupts.append(item.value)
    return seen, interrupts


@pytest.mark.asyncio
async def test_an_approved_fix_reaches_the_tool_node_and_runs(monkeypatch, _settings_cache_isolated):
    """The reported defect, end to end.

    Before: the interrupt payload was `actions: []` and resuming with
    approved=True went approval -> report, so the rollback never ran and the run
    still finished 'done'.
    """
    _EXECUTED.clear()
    graph, config, _ = _multi_agent_graph(monkeypatch, "wiring-approve")

    seen, interrupts = await _drive(
        graph, config, {"messages": [HumanMessage(content="checkout is 500ing")]}
    )
    assert "__interrupt__" in seen, f"never asked for approval: {seen}"

    payload = interrupts[-1]
    assert payload["type"] == "approval_request"
    assert [a["tool"] for a in payload["actions"]] == ["rollback_deployment"]
    assert payload["actions"][0]["args"] == {
        "deployment": "checkout",
        "namespace": "production",
        "revision": "41",
    }

    # Nothing may run before the human answers, and the review gate must not have
    # been jumped: no tool node ran between the proposal and the pause.
    assert _EXECUTED == []
    assert "tools" not in seen
    assert not (await graph.aget_state(config)).values.get("pending_tool_calls")

    resumed, _ = await _drive(graph, config, Command(resume={"approved": True}))
    assert "tools" in resumed, f"approved write never reached the tool node: {resumed}"
    assert _EXECUTED == [("checkout", "production", "41")]

    final = (await graph.aget_state(config)).values
    # The tool result answers the proposal's own tool_use block, so the
    # transcript the next provider call sends is not left with a dangling one.
    answered = [m.tool_call_id for m in final["messages"] if isinstance(m, ToolMessage)]
    assert "call_write" in answered
    assert not final.get("pending_tool_calls"), "the queue must not survive execution"


@pytest.mark.asyncio
async def test_a_rejected_fix_is_not_run_and_goes_back_for_another_proposal(
    monkeypatch, _settings_cache_isolated
):
    _EXECUTED.clear()
    graph, config, _ = _multi_agent_graph(monkeypatch, "wiring-reject")

    await _drive(graph, config, {"messages": [HumanMessage(content="checkout is 500ing")]})
    resumed, _ = await _drive(
        graph, config, Command(resume={"approved": False, "reason": "peak traffic"})
    )

    assert _EXECUTED == [], "a rejected write executed"
    assert "remediation" in resumed, f"rejection did not go back to remediation: {resumed}"

    final = (await graph.aget_state(config)).values
    rejected = [
        m for m in final["messages"]
        if isinstance(m, ToolMessage) and "REJECTED" in str(m.content)
    ]
    assert [m.tool_call_id for m in rejected] == ["call_write"]


@pytest.mark.asyncio
async def test_the_reviewer_prompt_gets_the_summary_not_the_executable_payload(
    monkeypatch, _settings_cache_isolated
):
    """`pending_action` holds both halves; only the truncated one is promptable."""
    graph, config, model = _multi_agent_graph(monkeypatch, "wiring-prompt")
    await _drive(graph, config, {"messages": [HumanMessage(content="checkout is 500ing")]})

    review_prompts = [p for p in model.prompts if "Proposed action to review" in p]
    assert review_prompts, "the reviewer was never asked"
    prompt = review_prompts[0]
    # The reviewer still needs the facts it judges...
    assert "rollback_deployment" in prompt and "production" in prompt
    # ...but not a second, untruncated copy of the executable calls.
    assert "'tool_calls'" not in prompt


# --------------------------------------------------------------------------
# The two halves of a proposed action, and what each is for.
# --------------------------------------------------------------------------


class TestProposedActionHalves:
    def test_count_is_the_number_of_writes_under_review(self):
        """Every call is described, because approving runs every call — but a
        co-bundled read is not a second write, and `count` counts writes."""
        proposed = _proposed_action([_ROLLBACK, _READ])

        assert len(proposed["actions"]) == 2
        assert [a["write"] for a in proposed["actions"]] == [True, False]
        assert proposed["count"] == 1

    def test_review_summary_drops_the_executable_half(self):
        body = "y" * 900
        proposed = _proposed_action([
            {
                "name": "create_pull_request",
                "args": {"title": "Fix checkout", "body": body, "patch": "diff --git a b"},
                "id": "call_pr",
                "type": "tool_call",
            }
        ])
        summary = review_summary(proposed)

        assert "tool_calls" not in summary
        assert summary["actions"][0]["has_patch"] is True
        assert len(summary["actions"][0]["body"]) == 500
        assert body not in str(summary)
        assert "diff --git a b" not in str(summary)
        # The original is untouched: the gate still has something to execute.
        assert proposed["tool_calls"][0]["args"]["body"] == body

    def test_review_summary_passes_through_anything_else(self):
        """The single-agent and parsed-prose paths store a plain string."""
        assert review_summary("scale checkout to 6") == "scale checkout to 6"
        assert review_summary(None) is None


class TestOrchestratorPropagation:
    """The graph is not the only caller: `OrchestratorAgent` drives the same
    agents for non-graph callers and must not drop the calls either."""

    def _orchestrator(self, response: AIMessage):
        from app.agents.orchestrator import InvestigationPhase, OrchestratorAgent

        orch = OrchestratorAgent(tools=[])
        with patch.object(RemediationAgent, "invoke", return_value=response), \
             patch.object(RemediationAgent, "log_usage", return_value=0), \
             patch.object(RemediationAgent, "send_message"), \
             patch.object(OrchestratorAgent, "create_handoff", return_value=None), \
             patch.object(RemediationAgent, "receive_handoff",
                          side_effect=lambda handoff, ctx: orch.remediator.process("t", [], ctx)):
            context = SharedContext(session_id="t")
            context.root_cause = "bad deploy"
            started = orch._start_remediation("fix it", [], context)
            context.metadata["phase"] = InvestigationPhase.REMEDIATING
            continued = orch._continue_remediation("fix it", [], context)
        return started, continued

    def test_a_needs_review_result_still_carries_the_calls(self):
        started, continued = self._orchestrator(
            AIMessage(content="", tool_calls=[dict(_ROLLBACK)])
        )

        for out in (started, continued):
            assert out["status"] == "needs_review"
            assert [c["id"] for c in out["tool_calls"]] == ["call_write"]
            assert out["result"]["proposed_action"]["tool_calls"][0]["args"]["revision"] == "41"

    def test_a_plain_proposal_carries_no_calls(self):
        """No tool calls proposed means no `tool_calls` key to mistake for work."""
        started, _ = self._orchestrator(
            AIMessage(content="Proposed fix: bump the pod memory limit. low risk.")
        )

        assert started["status"] == "success"
        assert "tool_calls" not in started
