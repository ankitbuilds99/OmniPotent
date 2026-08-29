"""The agent's shared state — the object that flows through every graph node."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated, Any, Literal, TypedDict

from langgraph.channels import BaseChannel
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


class RunStart(int):
    """A delta that also declares "a new run begins with this write".

    The run boundary is normally drawn by LangGraph itself: it calls `finish()` on
    every channel as a run winds down, and `RunScopedSum` seals the total there. A
    run that *dies* never gets that far — a node raises, or the worker is killed —
    and its partial total is checkpointed unsealed, so the next turn's deltas land
    on top of it. A turn that crashed just after planning left `tokens_used=40`
    behind and the next turn reported (and billed) 140 for 100 tokens of work.

    So the node that opens a turn says so itself instead of trusting the previous
    run to have ended tidily. It is an `int` subclass, so every other consumer of a
    node's update dict — the live trace, the multi-agent plumbing — still sees a
    plain token count; only the channel looks at the type. The one thing it does not
    survive is the checkpointer's serializer, which stores it as a bare int: a write
    replayed after a process restart falls back to the seal, which is the durable
    half of the boundary.
    """

    __slots__ = ()


class RunScopedSum(BaseChannel[int, int, tuple]):
    """An additive counter whose sum covers exactly one run, not the thread's lifetime.

    Every node that calls an LLM returns its own delta and lets the channel add them
    up. Plain `Annotated[int, _add_int]` did that correctly *within* a run, but the
    channel is checkpointed per thread and nothing ever wrote a reset, so the sum kept
    growing across turns: a follow-up question on the same `thread_id` reported (and
    metered, and charged against the token kill-switch) every token the whole
    conversation had ever spent, not the ones this turn spent. Three 10k-token turns
    billed 10k, then 20k, then 30k.

    This channel keeps the same "nodes emit deltas" contract and adds the missing
    boundary. LangGraph tells every channel when a run is winding down (`finish()`),
    so the total is *sealed* there rather than cleared — the run's own value stays in
    the final checkpoint, which is what `session.py` reads back and reports as the
    turn's cost. The next run drops that sealed total before anything reads it.

    Dropping it before the new run's first *read* rather than on its first *write* is
    the part that matters. Nodes read this counter before adding to it: the verify node
    checks how much of its revision budget the run has spent, the agent and reflect
    nodes check the token kill-switch. A reset that waited for a write handed those
    readers the previous turn's number — the verify budget stayed shared across the
    whole thread. LangGraph pokes every unwritten channel once per superstep, so the
    sealed total goes on the new run's first poke, at the end of its first superstep,
    before any node except the first one has run. The node that runs first must
    therefore write before it reads; `plan_node` is that node, reads neither counter,
    and tags its own write `RunStart` to cover the case where the previous run died
    without sealing.

    The guarantee is: every node except the one that opens the run reads the sum of
    the deltas written since this run began, however the previous run ended. A run
    that spans a human-in-the-loop interrupt keeps accumulating across the pause —
    that resume is the same turn, and it is the resumed run, not the paused one, that
    gets metered.
    """

    __slots__ = ("total", "sealed")

    def __init__(self, typ: Any = int, key: str = "") -> None:
        super().__init__(typ, key)
        self.total = 0
        self.sealed = False

    @property
    def ValueType(self) -> Any:
        return int

    @property
    def UpdateType(self) -> Any:
        return int

    def checkpoint(self) -> tuple[int, bool]:
        # The seal travels with the total, so a thread resumed from disk (or handed
        # to another process behind the Postgres saver) still knows the run it
        # belongs to is over and the next delta must start a new sum.
        return (self.total, self.sealed)

    def from_checkpoint(self, checkpoint: Any) -> RunScopedSum:
        restored = self.__class__(self.typ, self.key)
        # Anything that isn't the pair we wrote — LangGraph's "no value" sentinel, or
        # a checkpoint taken before this channel existed — starts a fresh run at zero
        # instead of inventing a total nothing measured.
        if isinstance(checkpoint, (tuple, list)) and len(checkpoint) == 2:
            total, sealed = checkpoint
            restored.total = int(total or 0)
            restored.sealed = bool(sealed)
        return restored

    def get(self) -> int:
        return self.total

    def is_available(self) -> bool:
        # Always readable, so `state["tokens_used"]` is a real 0 before the first LLM
        # call rather than a missing channel every reader would have to special-case.
        return True

    def update(self, values: Sequence[int]) -> bool:
        # Two ways a new run announces itself: the previous one sealed itself on the
        # way out, or a node declares the boundary (`RunStart`) because the previous
        # one died before it could. Either way the old total goes first, including on
        # the empty per-superstep poke below — that poke is the earliest moment in a
        # run at which this channel hears anything, so it is where a reader that
        # never writes gets its zero.
        if self.sealed or any(isinstance(v, RunStart) for v in values):
            self.total = 0
            self.sealed = False
        if not values:
            # Nothing was written this step. Clearing a sealed total changes nothing
            # the scheduler can act on (no node is triggered by this channel), so
            # report "not updated" and leave the channel version alone; the value
            # itself is still persisted with the step's checkpoint.
            return False
        for value in values:
            self.total = _add_int(self.total, value)
        return True

    def finish(self) -> bool:
        self.sealed = True
        # Sealing changes no readable value, so report "not updated": bumping the
        # channel version here would look like a write and could schedule another
        # superstep on a graph that has just decided it is done.
        return False


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
    # Drives the per-investigation cost kill-switch and is surfaced to the UI. Scoped
    # to one run (see RunScopedSum) — a follow-up on the same thread starts at zero
    # instead of re-reporting what earlier turns already spent and were billed for.
    tokens_used: Annotated[int, RunScopedSum(int)]

    # Fix-verification verdict produced by the verify node when the report proposes
    # a fix: does the proposed remediation address the root cause, what signal would
    # confirm resolution, residual risks. None when verification didn't run.
    verification: dict | None

    # How many times the verify node has bounced the run back to the agent to revise
    # a fix that missed the root cause. Bounds that loop (see copilot_verify_max_attempts).
    # Run-scoped like tokens_used, and for a sharper reason: the verify node reads this
    # before it writes it, so on a per-thread counter a thread that spent its one
    # revision on turn 1 would refuse to revise anything ever again.
    verify_attempts: Annotated[int, RunScopedSum(int)]

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
