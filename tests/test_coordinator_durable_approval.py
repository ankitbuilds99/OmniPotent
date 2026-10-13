"""Focused tests for approval delivery across coordinator replicas."""

import asyncio

from app.coordinator.dag_executor import DAGExecution, DAGExecutor, TaskNode


def test_waiting_worker_observes_durable_approval(monkeypatch):
    """A worker with no local Future still resumes from the shared decision."""
    saved = []
    reads = 0

    async def save(org_id, execution):
        saved.append((org_id, execution.id))

    async def read(org_id, execution_id, task_id):
        nonlocal reads
        reads += 1
        return True if reads >= 1 else None

    monkeypatch.setattr("app.coordinator.store.save_execution", save)
    monkeypatch.setattr("app.coordinator.store.get_approval_decision", read)

    executor = DAGExecutor()
    execution = DAGExecution(
        id="exec-cross-replica",
        dag_id="dag-1",
        org_id="tenant-a",
        execution_levels=[["restart"]],
    )
    task = TaskNode(
        id="restart",
        name="Restart service",
        handler="restart_service",
        requires_approval=True,
        approval_timeout_minutes=1,
    )

    approved = asyncio.run(executor._request_approval(task, execution))

    assert approved is True
    assert reads == 1
    assert saved == [("tenant-a", "exec-cross-replica")]

