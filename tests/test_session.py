"""CopilotSession pure helpers + the recursion_limit derivation."""

import asyncio

from langchain_core.messages import AIMessage, HumanMessage

from app.config import get_settings
from app.session import CopilotSession, _describe, _last_ai_text


def test_config_derives_recursion_limit_above_default():
    s = CopilotSession(thread_id="t1")
    cfg = s._config
    assert cfg["configurable"]["thread_id"] == "t1"
    cap = get_settings().copilot_max_iterations
    assert cfg["recursion_limit"] == cap * 4 + 10
    # Must exceed LangGraph's default of 25 so approval/reflect round-trips
    # don't crash the graph before the agent's own iteration cap stops it.
    assert cfg["recursion_limit"] > 25


def test_last_ai_text_prefers_toolfree_final_answer():
    msgs = [
        HumanMessage(content="why 500s?"),
        AIMessage(content="", tool_calls=[{"name": "search_logs", "args": {}, "id": "1"}]),
        AIMessage(content="ROOT CAUSE: null deref in applyDiscount"),
    ]
    assert _last_ai_text(msgs) == "ROOT CAUSE: null deref in applyDiscount"


def test_last_ai_text_empty_history():
    assert _last_ai_text([]) == "(no final answer produced)"


def test_describe_renders_each_node():
    assert _describe("plan", {"plan": ["a", "b"]})
    assert "search_logs" in _describe(
        "agent",
        {"messages": [AIMessage(content="", tool_calls=[{"name": "search_logs", "args": {}, "id": "1"}])]},
    )
    assert _describe("reflect", {"status": "done"})
    assert _describe("__interrupt__", None)


def test_confidence_gate_blocks_programmatic_auto_approval(monkeypatch):
    """auto=True approval of a gated (thin-evidence, high-risk) write is refused."""
    s = CopilotSession(thread_id="gate")

    async def _blocked():
        return {"auto_approve_blocked": True, "reason": "high-risk on low confidence"}

    monkeypatch.setattr(s, "_pending_gate", _blocked)
    approved, reason = asyncio.run(s._apply_confidence_gate(True, "", auto=True))
    assert approved is False
    assert "confidence gate" in reason.lower()


def test_confidence_gate_lets_human_approve(monkeypatch):
    """A human (auto=False) is never blocked — they see the warning and decide."""
    s = CopilotSession(thread_id="gate")

    async def _blocked():  # would block, but human path must not consult it
        raise AssertionError("human approval must not run the gate")

    monkeypatch.setattr(s, "_pending_gate", _blocked)
    approved, _ = asyncio.run(s._apply_confidence_gate(True, "", auto=False))
    assert approved is True


def test_confidence_gate_allows_well_evidenced_auto_approval(monkeypatch):
    s = CopilotSession(thread_id="gate")

    async def _ok():
        return {"auto_approve_blocked": False, "reason": ""}

    monkeypatch.setattr(s, "_pending_gate", _ok)
    approved, _ = asyncio.run(s._apply_confidence_gate(True, "", auto=True))
    assert approved is True


# --------------------------------------------------------------------------- #
# pending_interrupt must mean "a human owes us a decision", not "the graph has
# a next node". A crashed turn also leaves `next` set, and conflating the two
# bricked the thread: /chat refused every later message as "awaiting approval"
# while /approve had nothing to resume.
# --------------------------------------------------------------------------- #
class _Snapshot:
    def __init__(self, next_nodes=(), interrupts=()):
        self.next = next_nodes
        self.interrupts = interrupts
        self.tasks = ()


class _FakeGraph:
    def __init__(self, snapshot):
        self._snapshot = snapshot

    async def aget_state(self, _config):
        return self._snapshot


def _pending(snapshot) -> bool:
    s = CopilotSession(thread_id="t")
    s._graph = _FakeGraph(snapshot)
    return asyncio.run(s.pending_interrupt())


def test_pending_interrupt_true_for_real_approval_pause():
    assert _pending(_Snapshot(next_nodes=("approval",), interrupts=({"action": "deploy"},))) is True


def test_pending_interrupt_false_after_crashed_turn():
    """The graph died at `plan` with no interrupt attached — nothing to approve."""
    assert _pending(_Snapshot(next_nodes=("plan",), interrupts=())) is False


def test_pending_interrupt_false_after_normal_completion():
    assert _pending(_Snapshot(next_nodes=(), interrupts=())) is False


def test_pending_interrupt_reads_task_interrupts_when_snapshot_lacks_them():
    """Older LangGraph hangs interrupts off the pending tasks instead."""
    class _Task:
        name = "approval"
        interrupts = ({"action": "deploy"},)

    snap = _Snapshot(next_nodes=("approval",))
    del snap.interrupts
    snap.tasks = (_Task(),)
    assert _pending(snap) is True
