"""`tokens_used` must report this turn's spend, not the thread's lifetime total.

The reported failure: `tokens_used` was an additive channel (`Annotated[int,
_add_int]`) that no node ever reset, while the checkpointer keeps channel values
per `thread_id` across turns. So a conversation that spent 10k, 12k and 9k tokens
on three messages reported 10k, then 22k, then 31k — 63k metered and billed for
31k actually spent — and once an operator set COPILOT_MAX_TOKENS_PER_RUN the
thread's lifetime total would cross the per-investigation cap and pin every later
turn into "no tools, answer now" forever.

These tests drive the real `AgentState` schema through a real LangGraph run with a
real SQLite checkpointer (production's saver) and the real plan node, taking
several turns on one thread id the way the UI does.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from app.graph import nodes
from app.graph.nodes import _over_token_budget, make_plan_node, make_verify_node
from app.graph.prompts import VERIFY_SYSTEM
from app.graph.state import AgentState, RunScopedSum, RunStart

PLAN_TOKENS = 40
AGENT_TOKENS = 60
VERIFY_TOKENS = 7
TURN_TOKENS = PLAN_TOKENS + AGENT_TOKENS


class _FakeLLM:
    """Fake provider: the nodes still compute their own token delta from
    usage_metadata exactly as they do against Anthropic."""

    def __init__(self, content: str, tokens: int):
        self.content = content
        self.tokens = tokens

    def invoke(self, _msgs):
        return SimpleNamespace(
            content=self.content, usage_metadata={"total_tokens": self.tokens}
        )


class _Dispatcher:
    """One fake LLM handed to every node (get_llm is patched once), answering as
    whichever node called it — nodes are told apart by their system prompt."""

    def __init__(self, by_system: dict[str, _FakeLLM], default: _FakeLLM):
        self.by_system = by_system
        self.default = default

    def invoke(self, msgs):
        return self.by_system.get(str(msgs[0].content), self.default).invoke(msgs)


def _stub_planner_llm(monkeypatch, tokens: int = PLAN_TOKENS):
    llm = _FakeLLM("1. check logs\n2. check deploys", tokens)
    monkeypatch.setattr(nodes, "get_llm", lambda fast=False: llm)


def _seen_tokens_graph(checkpointer, seen: list[int]):
    """plan -> agent, where `agent` records the token total the graph handed it
    before adding its own delta (that is the number the cost kill-switch reads)."""

    def agent(state: AgentState) -> dict:
        seen.append(state.get("tokens_used", 0))
        return {"tokens_used": AGENT_TOKENS, "status": "done"}

    g = StateGraph(AgentState)
    g.add_node("plan", make_plan_node())
    g.add_node("agent", agent)
    g.add_edge(START, "plan")
    g.add_edge("plan", "agent")
    g.add_edge("agent", END)
    return g.compile(checkpointer=checkpointer)


async def test_tokens_used_reports_only_the_current_turns_spend(monkeypatch):
    _stub_planner_llm(monkeypatch)
    seen: list[int] = []
    async with AsyncSqliteSaver.from_conn_string(":memory:") as saver:
        graph = _seen_tokens_graph(saver, seen)
        cfg = {"configurable": {"thread_id": "web-multiturn"}}

        reported = []
        for question in ("why the 500s?", "and now the fix?", "did it work?"):
            out = await graph.ainvoke({"messages": [HumanMessage(content=question)]}, cfg)
            reported.append(out["tokens_used"])
            # session.py reads the number off the persisted checkpoint, not the
            # invoke() return value — both must say the same thing.
            snapshot = await graph.aget_state(cfg)
            assert snapshot.values["tokens_used"] == out["tokens_used"]

        # Every turn costs the same, so every turn must report the same. Before the
        # fix this was [100, 200, 300].
        assert reported == [TURN_TOKENS, TURN_TOKENS, TURN_TOKENS]
        # Each turn's own nodes still see the running sum within their run.
        assert seen == [PLAN_TOKENS, PLAN_TOKENS, PLAN_TOKENS]


async def test_cost_kill_switch_reads_this_turn_not_the_threads_history(monkeypatch):
    """With COPILOT_MAX_TOKENS_PER_RUN set, a thread whose *history* exceeds the cap
    must still be allowed to investigate: the cap is per investigation."""
    _stub_planner_llm(monkeypatch)
    settings = SimpleNamespace(copilot_max_tokens_per_run=TURN_TOKENS * 2)
    seen: list[int] = []
    async with AsyncSqliteSaver.from_conn_string(":memory:") as saver:
        graph = _seen_tokens_graph(saver, seen)
        cfg = {"configurable": {"thread_id": "web-capped"}}
        for _ in range(4):
            await graph.ainvoke({"messages": [HumanMessage(content="why?")]}, cfg)

    # Four turns spent 400 tokens in total, well past a 200-token cap, but no single
    # turn came anywhere near it — so the agent keeps its tools on every turn.
    assert not any(_over_token_budget(t, settings) for t in seen)


async def test_tokens_accumulate_across_a_human_in_the_loop_pause(monkeypatch):
    """An approval pause splits one turn across two `ainvoke` calls. Only the
    resumed run is metered (api/main.py meters `status == "completed"`), so the
    pre-approval spend has to survive the pause instead of being reset with it."""
    _stub_planner_llm(monkeypatch)

    def approval(state: AgentState) -> dict:
        decision = interrupt({"tool": "restart_deployment"})
        return {"tokens_used": AGENT_TOKENS, "status": "done" if decision else "failed"}

    g = StateGraph(AgentState)
    g.add_node("plan", make_plan_node())
    g.add_node("approval", approval)
    g.add_edge(START, "plan")
    g.add_edge("plan", "approval")
    g.add_edge("approval", END)

    async with AsyncSqliteSaver.from_conn_string(":memory:") as saver:
        graph = g.compile(checkpointer=saver)
        cfg = {"configurable": {"thread_id": "web-approval"}}

        paused = await graph.ainvoke({"messages": [HumanMessage(content="restart it")]}, cfg)
        assert paused["tokens_used"] == PLAN_TOKENS

        resumed = await graph.ainvoke(Command(resume=True), cfg)
        assert resumed["tokens_used"] == TURN_TOKENS  # the whole investigation, once

        # The next question is a new turn and starts from zero again.
        nxt = await graph.ainvoke({"messages": [HumanMessage(content="anything else?")]}, cfg)
        assert nxt["tokens_used"] == PLAN_TOKENS


async def test_verify_attempts_resets_between_turns(monkeypatch):
    """The verify->agent revision loop is bounded per run. Carried across turns, a
    thread that used its one revision would lose fix verification forever.

    The only thing that reads this counter is the verify node, and it reads it
    *before* it writes it (`attempts = state.get("verify_attempts", 0)`), so that is
    what this asserts. An earlier version of this test asserted the value the stub
    node had just written and so passed either way: 1 is what the node returns
    whether or not the counter was carried over from the previous turn.
    """
    _stub_planner_llm(monkeypatch)
    read_before_writing: list[int] = []

    def verify(state: AgentState) -> dict:
        read_before_writing.append(state.get("verify_attempts", 0))
        return {"verify_attempts": 1, "status": "done"}

    g = StateGraph(AgentState)
    g.add_node("plan", make_plan_node())
    g.add_node("verify", verify)
    g.add_edge(START, "plan")
    g.add_edge("plan", "verify")
    g.add_edge("verify", END)

    async with AsyncSqliteSaver.from_conn_string(":memory:") as saver:
        graph = g.compile(checkpointer=saver)
        cfg = {"configurable": {"thread_id": "web-verify"}}
        for _ in range(3):
            await graph.ainvoke({"messages": [HumanMessage(content="fix it")]}, cfg)

    # Every turn starts with its full revision budget unspent. Before the fix this
    # was [0, 1, 1] — from turn two on the node believed it had already revised.
    assert read_before_writing == [0, 0, 0]


async def test_the_real_verify_node_can_still_revise_on_a_later_turn(monkeypatch):
    """The same thing through the actual verify node: it must be willing to bounce an
    unverified fix back to the agent on turn three, not just turn one."""
    verify_llm = _FakeLLM(
        '{"verdict":"unverified","confidence":"low",'
        '"resolution_criteria":["5xx back to baseline"],'
        '"rationale":"the change touches billing, the cause is in checkout"}',
        tokens=VERIFY_TOKENS,
    )
    plan_llm = _FakeLLM("1. check logs", tokens=PLAN_TOKENS)
    monkeypatch.setattr(
        nodes,
        "get_llm",
        lambda fast=False: _Dispatcher({VERIFY_SYSTEM: verify_llm}, plan_llm),
    )
    report = {
        "root_cause": "checkout-api dereferences a null coupon on /checkout",
        "recommended_actions": ["Roll back the billing deploy"],
    }

    g = StateGraph(AgentState)
    g.add_node("plan", make_plan_node())
    g.add_node("verify", make_verify_node())
    g.add_edge(START, "plan")
    g.add_edge("plan", "verify")
    g.add_edge("verify", END)

    async with AsyncSqliteSaver.from_conn_string(":memory:") as saver:
        graph = g.compile(checkpointer=saver)
        cfg = {"configurable": {"thread_id": "web-verify-real"}}
        outcomes = []
        for _ in range(3):
            out = await graph.ainvoke(
                {"messages": [HumanMessage(content="fix the 500s")], "report": dict(report)},
                cfg,
            )
            outcomes.append((out["status"], out["verify_attempts"]))

    # "investigating" is the revision bounce. Before the fix turns two and three came
    # back ("done", 2) and ("done", 3): the budget was spent by the first turn.
    assert outcomes == [("investigating", 1)] * 3


async def test_a_turn_that_crashes_is_not_billed_to_the_next_turn(monkeypatch):
    """A run that raises never reaches finish(), so nothing seals its partial total.
    That unsealed remainder must not become the next turn's opening balance."""
    _stub_planner_llm(monkeypatch)
    turns = {"n": 0}
    seen: list[int] = []

    def agent(state: AgentState) -> dict:
        turns["n"] += 1
        seen.append(state.get("tokens_used", 0))
        if turns["n"] == 1:
            raise RuntimeError("provider returned 503")
        return {"tokens_used": AGENT_TOKENS, "status": "done"}

    g = StateGraph(AgentState)
    g.add_node("plan", make_plan_node())
    g.add_node("agent", agent)
    g.add_edge(START, "plan")
    g.add_edge("plan", "agent")
    g.add_edge("agent", END)

    async with AsyncSqliteSaver.from_conn_string(":memory:") as saver:
        graph = g.compile(checkpointer=saver)
        cfg = {"configurable": {"thread_id": "web-crash"}}
        with pytest.raises(RuntimeError):
            await graph.ainvoke({"messages": [HumanMessage(content="why the 500s?")]}, cfg)

        # The dead run's planning tokens really were spent, so they stay on the
        # record for the turn that spent them...
        snapshot = await graph.aget_state(cfg)
        assert snapshot.values["tokens_used"] == PLAN_TOKENS

        # ...and the retry is a new turn that pays for its own work only. Before the
        # fix it reported 140 for 100 tokens of work.
        out = await graph.ainvoke({"messages": [HumanMessage(content="try again?")]}, cfg)
        assert out["tokens_used"] == TURN_TOKENS

    assert seen == [PLAN_TOKENS, PLAN_TOKENS]


async def test_a_reader_that_never_writes_still_sees_this_turns_spend():
    """Not every graph opens with a node that spends tokens — the multi-agent graph
    starts at an orchestrator that just routes. The counter must already read zero by
    the time the first node that *does* read it runs, not on the first write."""
    seen: list[int] = []

    def orchestrator(state: AgentState) -> dict:
        return {"status": "investigating"}

    def worker(state: AgentState) -> dict:
        seen.append(state.get("tokens_used", 0))
        return {"tokens_used": AGENT_TOKENS, "status": "done"}

    g = StateGraph(AgentState)
    g.add_node("orchestrator", orchestrator)
    g.add_node("worker", worker)
    g.add_edge(START, "orchestrator")
    g.add_edge("orchestrator", "worker")
    g.add_edge("worker", END)

    async with AsyncSqliteSaver.from_conn_string(":memory:") as saver:
        graph = g.compile(checkpointer=saver)
        cfg = {"configurable": {"thread_id": "web-orchestrated"}}
        for _ in range(3):
            await graph.ainvoke({"messages": [HumanMessage(content="why?")]}, cfg)

    # Before the fix this was [0, 60, 120]: the reset waited for a write, and the
    # kill-switch this feeds would have fired on the thread's history.
    assert seen == [0, 0, 0]


# --- the channel itself ----------------------------------------------------- #


def test_run_scoped_sum_adds_every_delta_written_in_a_step():
    chan = RunScopedSum(int, "tokens_used")
    assert chan.get() == 0
    assert chan.update([7, 11]) is True
    assert chan.update([3]) is True
    assert chan.get() == 21
    assert chan.update([]) is False  # a step with no writer must not disturb the sum
    assert chan.get() == 21


def test_a_sealed_total_goes_before_the_next_run_can_read_it():
    """LangGraph pokes every unwritten channel once per superstep. That poke is the
    first thing the new run says to this channel, and it has to be enough — a node
    that reads the counter without writing it must not see the finished run's total."""
    chan = RunScopedSum(int, "tokens_used")
    chan.update([10_000])
    chan.finish()
    assert chan.get() == 10_000  # the finished turn's cost, for the caller to report

    assert chan.update([]) is False  # first superstep of the next run
    assert chan.get() == 0
    chan.update([300])
    assert chan.get() == 300


def test_the_poke_leaves_a_live_total_alone():
    """The same poke arrives on every superstep of a run in progress, where clearing
    the total would silently under-report the turn."""
    chan = RunScopedSum(int, "tokens_used")
    chan.update([10_000])
    assert chan.update([]) is False
    assert chan.get() == 10_000


def test_run_start_clears_a_total_the_previous_run_never_sealed():
    """A run that raised (or whose worker was killed) never reaches finish(), so its
    partial total is left unsealed. The next turn's opening write says so itself."""
    chan = RunScopedSum(int, "tokens_used")
    chan.update([40])  # ...and then the run died here
    assert chan.update([]) is False
    assert chan.get() == 40  # nothing may drop it: the paused-for-approval case

    chan.update([RunStart(40)])
    assert chan.get() == 40  # the new turn's own planning tokens, not 80
    chan.update([60])
    assert chan.get() == 100


def test_sealed_total_survives_the_checkpoint_round_trip():
    """The seal has to be persisted, not just held in memory: the next turn on a
    thread routinely runs in another process (Postgres saver) or after a restart."""
    chan = RunScopedSum(int, "tokens_used")
    chan.update([10_000])
    chan.finish()  # run over

    # What the checkpointer stores and hands back, including the list form a JSON
    # round-trip through the saver's serializer can produce.
    for stored in (chan.checkpoint(), list(chan.checkpoint())):
        resumed = chan.from_checkpoint(stored)
        assert resumed.get() == 10_000  # the finished turn's cost is still readable
        resumed.update([12_000])
        assert resumed.get() == 12_000  # ...but the next turn starts its own sum


def test_unsealed_total_survives_the_checkpoint_round_trip():
    """Mid-run checkpoints (every superstep writes one) must keep accumulating."""
    chan = RunScopedSum(int, "tokens_used")
    chan.update([10_000])
    resumed = chan.from_checkpoint(chan.checkpoint())
    resumed.update([12_000])
    assert resumed.get() == 22_000


@pytest.mark.parametrize("stored", [None, 5, "5", (), (1, 2, 3)])
def test_unrecognised_checkpoint_starts_a_fresh_run(stored):
    """A checkpoint written before this channel existed carries no total we can
    trust, so the run starts at a measured zero rather than an invented number."""
    resumed = RunScopedSum(int, "tokens_used").from_checkpoint(stored)
    assert resumed.get() == 0
    resumed.update([9])
    assert resumed.get() == 9
