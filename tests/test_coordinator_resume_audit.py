"""Resuming a DAG from a checkpoint has to actually run the rest of the DAG.

Checkpoints did not carry the level plan, so `_load_checkpoint` rebuilt an
execution whose `execution_levels` was `[]` and `_execute_dag` iterated
`range(current_level, 0)` — an empty range. Nothing ran, every task kept the
state it had when the run was interrupted, and the execution was written down as
COMPLETED. An operator resuming an interrupted remediation was told it had
finished when not one handler had been called.

These tests drive the real `DAGExecutor` against an in-memory stand-in for the
Redis checkpoint store: the resume of a run interrupted mid-DAG, the resume of a
checkpoint written before the plan was persisted, and a checkpoint that no
longer describes the DAG it is resumed against.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from app.coordinator.dag_executor import (
    DAGExecutor,
    ExecutionState,
    TaskDAG,
    TaskNode,
)


class FakeRedis:
    """The five operations `_save_checkpoint`/`_load_checkpoint` actually use."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.lists: dict[str, list[str]] = {}

    async def set(self, key: str, value: str) -> None:
        self.values[key] = value

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def expire(self, key: str, ttl: int) -> None:
        return None

    async def lpush(self, key: str, value: str) -> None:
        self.lists.setdefault(key, []).insert(0, value)

    async def ltrim(self, key: str, start: int, end: int) -> None:
        self.lists[key] = self.lists.get(key, [])[start : end + 1]


def _dag() -> TaskDAG:
    dag = TaskDAG("dag_resume", "Resume me")
    dag.add_task(TaskNode(id="a", name="first", handler="record"))
    dag.add_task(TaskNode(id="b", name="second", handler="record", depends_on=["a"]))
    return dag


def _executor(redis: FakeRedis, ran: list[str], *, fail: str | None = None) -> DAGExecutor:
    async def record(task: TaskNode, payload: dict) -> dict:
        ran.append(task.id)
        if task.id == fail:
            raise RuntimeError("connection reset")
        return {"task": task.id}

    return DAGExecutor(handlers={"record": record}, redis_client=redis)


def _interrupted_run(redis: FakeRedis) -> str:
    """Run the DAG until `b` dies, and return the checkpoint id that leaves."""
    ran: list[str] = []
    executor = _executor(redis, ran, fail="b")
    execution = asyncio.run(executor.execute(_dag(), context={"incident": "inc-1"}))

    assert ran == ["a", "b"], "the first pass should have tried both tasks"
    assert execution.state == ExecutionState.FAILED
    return redis.lists[f"dag_checkpoints:{execution.id}:history"][0]


def test_resuming_an_interrupted_run_executes_the_remaining_tasks():
    redis = FakeRedis()
    checkpoint_id = _interrupted_run(redis)

    ran: list[str] = []
    executor = _executor(redis, ran)
    resumed = asyncio.run(executor.execute(_dag(), resume_from=checkpoint_id))

    # Without the level plan this list was empty and the run still came back
    # COMPLETED — the fabricated success this test exists to catch.
    assert ran == ["b"]
    assert resumed.state == ExecutionState.COMPLETED
    assert resumed.task_executions["b"].state == ExecutionState.COMPLETED
    assert resumed.task_executions["b"].error is None
    # The completed upstream is not re-run, and its recorded output survives.
    assert resumed.task_executions["a"].state == ExecutionState.COMPLETED
    assert resumed.accumulated_context["task_a"] == {"task": "a"}
    # What the run was started with, so the stored record is not overwritten
    # with an empty context by the resume.
    assert resumed.initial_context == {"incident": "inc-1"}


def test_the_resumed_run_is_filed_under_the_id_its_caller_was_given():
    redis = FakeRedis()
    checkpoint_id = _interrupted_run(redis)

    ran: list[str] = []
    executor = _executor(redis, ran)
    resumed = asyncio.run(
        executor.execute(_dag(), execution_id="exec_polled", resume_from=checkpoint_id)
    )

    assert resumed.id == "exec_polled"
    assert resumed.resumed_from == checkpoint_id
    assert ran == ["b"]


def test_a_checkpoint_written_without_a_plan_is_replanned_from_the_dag():
    """Checkpoints already in Redis predate the plan being persisted."""
    redis = FakeRedis()
    checkpoint_id = _interrupted_run(redis)

    key = next(k for k in redis.values if k.startswith("dag_checkpoint:"))
    old_shape = json.loads(redis.values[key])
    old_shape.pop("execution_levels")
    redis.values[key] = json.dumps(old_shape)

    ran: list[str] = []
    executor = _executor(redis, ran)
    resumed = asyncio.run(executor.execute(_dag(), resume_from=checkpoint_id))

    assert ran == ["b"]
    assert resumed.state == ExecutionState.COMPLETED
    assert resumed.execution_levels == [["a"], ["b"]]


def test_a_checkpoint_that_does_not_match_the_dag_is_refused_not_half_run():
    redis = FakeRedis()
    checkpoint_id = _interrupted_run(redis)

    grown = _dag()
    grown.add_task(TaskNode(id="c", name="third", handler="record", depends_on=["b"]))

    ran: list[str] = []
    executor = _executor(redis, ran)

    with pytest.raises(ValueError) as excinfo:
        asyncio.run(executor.execute(grown, resume_from=checkpoint_id))

    assert "does not match DAG" in str(excinfo.value)
    assert "'c'" in str(excinfo.value)
    assert ran == [], "nothing should run against a plan that omits a task"


def test_the_saved_checkpoint_carries_the_level_plan():
    redis = FakeRedis()
    _interrupted_run(redis)

    key = next(k for k in redis.values if k.startswith("dag_checkpoint:"))
    assert json.loads(redis.values[key])["execution_levels"] == [["a"], ["b"]]
