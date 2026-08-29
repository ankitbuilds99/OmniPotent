"""Regression tests for the planning execution engine's concurrency limit.

`max_parallel` arrives straight from the request body of
POST /api/planning/graphs/{id}/execute, and a value below one used to be fatal
to the whole worker: the batch slice came out empty, the gather over it finished
without ever suspending, no task changed status, and the `while True:` loop
respun forever without giving the event loop a turn. These tests pin the two
observable halves of that fix — the limit is normalised to at least one, and an
execution asked to run "zero at a time" actually finishes.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.planning.execution_engine import ExecutionContext, ExecutionEngine
from app.planning.task_graph import TaskGraph

REPO_ROOT = Path(__file__).resolve().parents[1]


async def _ok_handler(inputs: dict) -> dict:
    return {"ok": True}


def _graph(task_count: int = 2) -> TaskGraph:
    graph = TaskGraph("audit-max-parallel")
    for i in range(task_count):
        graph.add_task(name=f"t{i}", task_id=f"t{i}", handler=_ok_handler)
    return graph


@pytest.mark.parametrize("requested", [0, -1, -100])
def test_context_normalises_unusable_concurrency_limits(requested: int) -> None:
    ctx = ExecutionContext(
        execution_id="ctx-probe",
        graph=_graph(1),
        started_at=datetime.now(timezone.utc),
        tools={},
        max_parallel=requested,
    )
    assert ctx.max_parallel == 1


def test_context_leaves_usable_limits_alone() -> None:
    ctx = ExecutionContext(
        execution_id="ctx-probe",
        graph=_graph(1),
        started_at=datetime.now(timezone.utc),
        tools={},
        max_parallel=7,
    )
    assert ctx.max_parallel == 7


@pytest.mark.asyncio
async def test_negative_max_parallel_executes_instead_of_raising() -> None:
    # asyncio.Semaphore(-1) raised ValueError straight out of the execute call,
    # so this request used to be a 500 rather than an execution.
    engine = ExecutionEngine()
    result = await engine.execute(_graph(2), max_parallel=-1)

    assert result.status == "completed"
    assert result.completed_tasks == 2


@pytest.mark.asyncio
async def test_normal_concurrency_still_runs_every_task() -> None:
    engine = ExecutionEngine()
    result = await engine.execute(_graph(4), max_parallel=3)

    assert result.status == "completed"
    assert result.completed_tasks == 4


@pytest.mark.asyncio
async def test_execution_loop_yields_to_other_coroutines() -> None:
    # The wedge was not "one slow request": the loop never suspended, so nothing
    # else on the event loop could run. A co-running ticker proves the loop is
    # shared again.
    ticks = 0

    async def ticker() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0)
            ticks += 1

    spinner = asyncio.create_task(ticker())
    try:
        engine = ExecutionEngine()
        result = await engine.execute(_graph(2), max_parallel=1)
    finally:
        spinner.cancel()

    assert result.status == "completed"
    assert ticks > 0


def test_zero_max_parallel_does_not_wedge_the_event_loop() -> None:
    """The original defect, exercised in a child process.

    In-process this cannot be asserted safely: before the fix the loop never
    yields, so `asyncio.wait_for` can never fire and the test run itself would
    hang. A child process with a hard wall-clock timeout turns the wedge into an
    ordinary test failure.
    """
    script = """
import asyncio, json
from app.planning.execution_engine import ExecutionEngine
from app.planning.task_graph import TaskGraph

async def handler(inputs):
    return {"ok": True}

async def main():
    graph = TaskGraph("wedge-probe")
    graph.add_task(name="t0", task_id="t0", handler=handler)
    graph.add_task(name="t1", task_id="t1", handler=handler)
    result = await ExecutionEngine().execute(graph, max_parallel=0)
    print(json.dumps({"status": result.status, "completed": result.completed_tasks}))

asyncio.run(main())
"""
    try:
        proc = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=90,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(
            "execute(max_parallel=0) never returned - the execution loop is "
            "spinning without yielding to the event loop"
        )

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload == {"status": "completed", "completed": 2}
