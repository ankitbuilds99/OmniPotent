"""Focused tests for durable coordinator cancellation."""

import asyncio

from app.coordinator.dag_executor import DAGExecution, DAGExecutor, ExecutionState


def test_worker_honors_durable_cancel_at_level_boundary(monkeypatch):
    async def requested(org_id, execution_id):
        assert (org_id, execution_id) == ("tenant-a", "exec-1")
        return True

    monkeypatch.setattr("app.coordinator.store.is_cancel_requested", requested)
    executor = DAGExecutor()
    execution = DAGExecution(
        id="exec-1",
        dag_id="dag-1",
        org_id="tenant-a",
        execution_levels=[["task-1"]],
    )

    async def run():
        await executor._execute_dag(type("DAG", (), {})(), execution)

    asyncio.run(run())
    assert execution.state is ExecutionState.CANCELLED
    assert execution.error == "Cancellation requested by operator"

