"""Regression tests for the remediation hand-off to the approval gate.

A write the RemediationAgent proposes is not executed by the agent — it is
handed to a reviewer, shown to a human, and only then run by the tool node. So
everything that surface needs (which tool, which arguments, which tool-call id
to answer) has to survive the hand-off. It used to not: the calls existed only
under ``result["tool_calls"]``, a key no caller reads, and the summary the graph
stored as ``pending_action`` named no tool and carried no id. These tests pin
the shape of the hand-off, including through the real multi-agent graph.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage

from app.agents.protocol import SharedContext
from app.agents.remediation import RemediationAgent


def _response(tool_calls: list[dict]) -> AIMessage:
    return AIMessage(content="", tool_calls=tool_calls)


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


def _process(tool_calls: list[dict]) -> dict:
    with patch.object(RemediationAgent, "invoke", return_value=_response(tool_calls)), \
         patch.object(RemediationAgent, "log_usage", return_value=17), \
         patch.object(RemediationAgent, "send_message"):
        agent = RemediationAgent()
        return agent.process("Fix the checkout outage", [], SharedContext(session_id="t"))


class TestWriteProposalHandOff:
    """What `process` returns when the model proposes a write."""

    def test_the_calls_travel_at_the_top_level_where_callers_look(self):
        result = _process([_ROLLBACK, _READ])

        assert result["status"] == "needs_review"
        # Every caller (the orchestrator's remediation hand-off, the graph's
        # remediation node) reads a top-level `tool_calls` key and nothing else.
        top = result["tool_calls"]
        assert [c["id"] for c in top] == ["call_write", "call_read"]
        assert top[0]["args"]["namespace"] == "production"

    def test_the_whole_message_travels_not_just_the_write(self):
        """The tool node runs every call on the message, so all of them are
        under review — a co-bundled read must not be invisible to the approver."""
        result = _process([_ROLLBACK, _READ])

        assert {c["name"] for c in result["tool_calls"]} == {
            "rollback_deployment",
            "get_deployment_status",
        }
        # `result["tool_calls"]` stays the write subset: that is what the
        # reviewer agent is asked about.
        assert [c["name"] for c in result["result"]["tool_calls"]] == ["rollback_deployment"]

    def test_the_proposed_action_is_executable_not_just_readable(self):
        """`proposed_action` is what the graph stores as `pending_action`, and
        the only description of the fix that survives into state."""
        proposed = _process([_ROLLBACK])["result"]["proposed_action"]

        assert proposed["tool_calls"] == [
            {
                "id": "call_write",
                "name": "rollback_deployment",
                "args": {
                    "deployment": "checkout",
                    "namespace": "production",
                    "revision": "41",
                },
            }
        ]

    def test_each_display_action_names_its_tool_and_its_call(self):
        """`type` is a label — create_pull_request shows as "pull_request" — so
        on its own it identifies neither the tool nor which call is answered."""
        proposed = _process([_ROLLBACK, _READ])["result"]["proposed_action"]
        actions = proposed["actions"]

        assert [a["tool"] for a in actions] == ["rollback_deployment", "get_deployment_status"]
        assert [a["id"] for a in actions] == ["call_write", "call_read"]

    def test_the_verbatim_args_are_not_truncated_like_the_summary(self):
        body = "x" * 900
        call = {
            "name": "create_pull_request",
            "args": {"title": "Fix checkout", "body": body, "patch": "diff"},
            "id": "call_pr",
            "type": "tool_call",
        }
        proposed = _process([call])["result"]["proposed_action"]

        assert len(proposed["actions"][0]["body"]) == 500  # display summary
        assert proposed["tool_calls"][0]["args"]["body"] == body  # what would run

    def test_read_only_proposals_still_bypass_review(self):
        result = _process([_READ])

        assert result["status"] == "needs_tools"
        assert [c["id"] for c in result["tool_calls"]] == ["call_read"]


class _ScriptedModel:
    """Drives the multi-agent graph to a write proposal.

    Root cause first (so the graph routes to remediation), then the rollback
    call, then an approving review verdict.
    """

    def __init__(self) -> None:
        self.calls = 0

    def bind_tools(self, tools, **kwargs):
        return self

    def with_structured_output(self, *a, **kw):
        raise NotImplementedError("stub model has no structured-output support")

    def invoke(self, messages, *args, **kwargs):
        self.calls += 1
        text = " ".join(str(getattr(m, "content", "")) for m in messages)
        if "rollback_deployment" in text and "Proposed action to review" in text:
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


def _rollback_tool():
    from langchain_core.tools import tool

    @tool
    def rollback_deployment(deployment: str, namespace: str, revision: str) -> str:
        """Roll a deployment back to a previous revision."""
        return f"rolled {deployment} back to {revision}"

    return rollback_deployment


@pytest.mark.asyncio
async def test_the_graph_carries_executable_calls_into_pending_action(monkeypatch):
    """End to end on the real multi-agent graph.

    The remediation node stores `result["result"]["proposed_action"]` as
    `pending_action`. If that dict is a prose summary with no tool, no
    arguments and no call id — as it was — then nothing downstream can render
    an approval prompt or execute what was approved, no matter how it routes.
    """
    from langchain_core.messages import HumanMessage
    from langgraph.checkpoint.memory import MemorySaver

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
    config = {"configurable": {"thread_id": "remediation-approval"}, "recursion_limit": 30}

    seen: list[str] = []
    async for event in graph.astream(
        {"messages": [HumanMessage(content="checkout is 500ing")]},
        config,
        stream_mode="updates",
    ):
        seen.extend(event)
    state = await graph.aget_state(config)
    cfg.get_settings.cache_clear()

    assert "remediation" in seen, f"never proposed a fix: {seen}"
    pending = state.values.get("pending_action")
    assert pending, f"no action pending after the write proposal: {seen}"

    calls = pending.get("tool_calls")
    assert calls, "the pending action carries nothing executable"
    assert calls[0]["name"] == "rollback_deployment"
    assert calls[0]["args"]["namespace"] == "production"
    assert calls[0]["id"], "no tool-call id, so the call can be neither run nor rejected"
