"""Runbooks MCP server — automated remediation workflows.

Provides tools for managing and executing runbooks:
- List and view runbook definitions
- Execute runbooks with context
- Approve or reject pending executions
- View execution history

Tools:
  - list_runbooks:        List available runbooks
  - get_runbook:          Get runbook details
  - execute_runbook:      Start runbook execution
  - approve_execution:    Approve pending execution
  - reject_execution:     Reject pending execution
  - list_executions:      View execution history
  - get_execution:        Get execution details
  - list_pending:         List pending approvals

Every tool here reads and writes `app.runbooks.store`, which is the same
database the HTTP API uses. They used to call `app.runbooks.get_executor()` —
a per-process, in-memory object seeded with only the built-in runbooks — so the
agent could not see a single runbook the customer had written, and every
execution it started existed solely inside this subprocess: no audit row, no
history in the UI, and gone the moment the subprocess exited. Since the two
sides disagreed about what a runbook even was, an agent asked to "run the
rollback runbook" would report success for a run nobody could later find.

The subprocess is passed COPILOT_DATABASE_URL by the parent (see
`app/mcp/client.py`); without it this would open a *different*, empty database
and the symptom would look identical.

Scope: there is no request in a stdio subprocess, so `tenant_id()` resolves to
the single-tenant org. On a multi-tenant deployment this server therefore sees
the default org's runbooks only — correct for local and single-org installs,
and a deliberate deny rather than a leak anywhere else.

Run standalone:
    python -m app.mcp.servers.runbooks.server
"""

from __future__ import annotations

import asyncio
import json
import threading
from typing import Any, Coroutine, TypeVar

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("runbooks")

T = TypeVar("T")

_loop: asyncio.AbstractEventLoop | None = None
_loop_lock = threading.Lock()


def _shared_loop() -> asyncio.AbstractEventLoop:
    """One event loop for the life of the process, on its own thread.

    Not a fresh `asyncio.run` per call: the database engine is a module global
    that caches pooled connections bound to whichever loop opened them, so the
    second tool call would fail against Postgres with "attached to a different
    loop". `asyncio.get_event_loop().run_until_complete` — what these tools did
    originally — has that problem plus a DeprecationWarning on 3.12 and a
    RuntimeError once a loop has been created and closed.

    On its own thread rather than the caller's because FastMCP dispatches sync
    tools into a worker pool, so two tools can be in flight at once and
    `run_until_complete` on a shared loop would raise for the second.
    """
    global _loop
    with _loop_lock:
        if _loop is None or _loop.is_closed():
            loop = asyncio.new_event_loop()
            threading.Thread(
                target=loop.run_forever,
                name="runbooks-mcp-loop",
                daemon=True,
            ).start()
            _loop = loop
        return _loop


def _run(coro: Coroutine[Any, Any, T]) -> T:
    """Drive a coroutine to completion from a synchronous MCP tool."""
    return asyncio.run_coroutine_threadsafe(coro, _shared_loop()).result()


@mcp.tool()
def list_runbooks(
    service: str = "",
    trigger: str = "",
) -> dict:
    """List available runbooks.

    Args:
        service: Filter by service name
        trigger: Filter by trigger type

    Returns:
        List of runbooks with summary info
    """
    from app.runbooks import store

    runbooks = _run(store.list_runbooks(
        service=service or None,
        trigger=trigger or None,
    ))

    return {
        "runbooks": [
            {
                "id": r.id,
                "name": r.name,
                "description": r.description,
                "service": r.service,
                "trigger": r.trigger,
                "execution_mode": r.execution_mode.value,
                "step_count": len(r.steps),
                "labels": r.labels,
            }
            for r in runbooks
        ],
        "total": len(runbooks),
    }


@mcp.tool()
def get_runbook(runbook_id: str) -> dict:
    """Get detailed information about a runbook.

    Args:
        runbook_id: Runbook identifier

    Returns:
        Full runbook definition with steps
    """
    from app.runbooks import store

    runbook = _run(store.get_runbook(runbook_id))

    if not runbook:
        return {"error": f"Runbook '{runbook_id}' not found"}

    return {
        "runbook": runbook.to_dict(),
    }


@mcp.tool()
def execute_runbook(
    runbook_id: str,
    service: str = "",
    namespace: str = "default",
    node: str = "",
    extra_context: str = "",
    dry_run: bool = False,
) -> dict:
    """Execute a runbook.

    Args:
        runbook_id: Runbook to execute
        service: Target service name
        namespace: Kubernetes namespace
        node: Target node (for node operations)
        extra_context: Additional context as JSON string
        dry_run: If true, validate but don't execute

    Returns:
        Execution status and ID
    """
    from app.runbooks import store
    from app.runbooks.executor import run_execution
    from app.runbooks.models import StepStatus

    runbook = _run(store.get_runbook(runbook_id))
    if not runbook:
        return {"error": f"Runbook '{runbook_id}' not found"}

    if not runbook.enabled:
        return {"error": f"Runbook '{runbook_id}' is disabled"}

    context: dict[str, Any] = {
        "service": service or runbook.service,
        "namespace": namespace,
        "node": node,
    }

    if extra_context:
        try:
            extra = json.loads(extra_context)
            if isinstance(extra, dict):
                context.update(extra)
            else:
                return {"error": "extra_context must be a JSON object"}
        except json.JSONDecodeError as exc:
            # Silently ignoring this meant the agent's carefully-built context
            # was dropped and the runbook ran against defaults instead.
            return {"error": f"extra_context is not valid JSON: {exc}"}

    if dry_run:
        return {
            "dry_run": True,
            "runbook": runbook.to_dict(),
            "context": context,
            "would_execute": True,
        }

    async def _start() -> Any:
        execution = await store.record_execution(
            runbook,
            status=store.default_execution_status(runbook.execution_mode),
            context=context,
        )
        if execution.status == StepStatus.RUNNING:
            await run_execution(execution.id, approved_through=-1)
            refreshed = await store.get_execution(execution.id)
            return refreshed or execution
        return execution

    execution = _run(_start())

    return {
        "execution_id": execution.id,
        "runbook_name": execution.runbook_name,
        "status": execution.status.value,
        "steps_completed": len([s for s in execution.steps if s.status.value == "success"]),
        "steps_total": len(runbook.steps),
        "requires_approval": execution.status.value in ("pending", "waiting_approval"),
    }


@mcp.tool()
def approve_execution(execution_id: str) -> dict:
    """Approve a pending runbook execution and run it.

    Args:
        execution_id: Execution to approve

    Returns:
        Updated execution status
    """
    from app.runbooks import store
    from app.runbooks.executor import run_execution
    from app.runbooks.models import StepStatus

    async def _approve() -> Any:
        result = await store.set_execution_status(
            execution_id,
            StepStatus.RUNNING,
            expect={StepStatus.PENDING, StepStatus.WAITING_APPROVAL},
        )
        if result is None or isinstance(result, str):
            return result
        # The approval covers the step this run is parked on: the next one not
        # yet recorded. Anything after it that needs approval parks again.
        await run_execution(result.id, approved_through=len(result.steps))
        return await store.get_execution(execution_id) or result

    execution = _run(_approve())

    if execution is None:
        return {"error": f"Execution '{execution_id}' not found"}
    if isinstance(execution, str):  # the store's "conflict" sentinel
        return {"error": f"Execution '{execution_id}' is not pending approval"}

    return {
        "execution_id": execution.id,
        "status": execution.status.value,
        "approved": True,
        "steps_completed": len([s for s in execution.steps if s.status.value == "success"]),
    }


@mcp.tool()
def reject_execution(execution_id: str) -> dict:
    """Reject a pending runbook execution.

    Args:
        execution_id: Execution to reject

    Returns:
        Updated execution status
    """
    from app.runbooks import store
    from app.runbooks.models import StepStatus

    # SKIPPED, not FAILED: a human declining to run a remediation is not the
    # same event as one that ran and broke.
    execution = _run(store.set_execution_status(
        execution_id,
        StepStatus.SKIPPED,
        expect={StepStatus.PENDING, StepStatus.WAITING_APPROVAL},
    ))

    if execution is None:
        return {"error": f"Execution '{execution_id}' not found"}
    if isinstance(execution, str):
        return {"error": f"Execution '{execution_id}' is not pending approval"}

    return {
        "execution_id": execution.id,
        "status": execution.status.value,
        "rejected": True,
    }


@mcp.tool()
def list_executions(
    runbook_id: str = "",
    status: str = "",
    limit: int = 20,
) -> dict:
    """List runbook executions.

    Args:
        runbook_id: Filter by runbook
        status: Filter by status (pending, running, success, failed, skipped, waiting_approval)
        limit: Maximum results

    Returns:
        List of executions
    """
    from app.runbooks import store
    from app.runbooks.models import StepStatus

    if status:
        try:
            StepStatus(status)
        except ValueError:
            return {
                "error": f"Invalid status '{status}'",
                "valid_statuses": [s.value for s in StepStatus],
            }

    executions = _run(store.list_executions(
        runbook_id=runbook_id or None,
        status=status or None,
        limit=limit,
    ))

    return {
        "executions": [
            {
                "id": e.id,
                "runbook_id": e.runbook_id,
                "runbook_name": e.runbook_name,
                "status": e.status.value,
                "triggered_by": e.triggered_by,
                "started_at": e.started_at.isoformat(),
                "completed_at": e.completed_at.isoformat() if e.completed_at else None,
                "steps_completed": len([s for s in e.steps if s.status.value == "success"]),
            }
            for e in executions
        ],
        "total": len(executions),
    }


@mcp.tool()
def get_execution(execution_id: str) -> dict:
    """Get detailed execution information.

    Args:
        execution_id: Execution to retrieve

    Returns:
        Full execution details with step results
    """
    from app.runbooks import store

    execution = _run(store.get_execution(execution_id))

    if not execution:
        return {"error": f"Execution '{execution_id}' not found"}

    return {
        "execution": execution.to_dict(),
    }


@mcp.tool()
def list_pending_approvals() -> dict:
    """List all executions waiting for approval.

    Returns:
        List of pending executions
    """
    from app.runbooks import store
    from app.runbooks.models import StepStatus

    async def _pending() -> list[Any]:
        found = []
        for state in (StepStatus.PENDING, StepStatus.WAITING_APPROVAL):
            found.extend(await store.list_executions(status=state.value, limit=100))
        return found

    pending = _run(_pending())

    return {
        "pending": [
            {
                "execution_id": e.id,
                "runbook_id": e.runbook_id,
                "runbook_name": e.runbook_name,
                "status": e.status.value,
                "triggered_by": e.triggered_by,
                "started_at": e.started_at.isoformat(),
                "context": e.context,
            }
            for e in pending
        ],
        "total": len(pending),
    }


if __name__ == "__main__":
    mcp.run(transport="stdio")
