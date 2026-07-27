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

Run standalone:
    python -m app.mcp.servers.runbooks.server
"""

from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("runbooks")


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
    from app.runbooks import get_executor

    executor = get_executor()
    runbooks = executor.list_runbooks(
        service=service or None,
        trigger=trigger or None,
    )

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
    from app.runbooks import get_executor

    executor = get_executor()
    runbook = executor.get_runbook(runbook_id)

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
    import asyncio
    from app.runbooks import get_executor

    executor = get_executor()
    runbook = executor.get_runbook(runbook_id)

    if not runbook:
        return {"error": f"Runbook '{runbook_id}' not found"}

    context = {
        "service": service or runbook.service,
        "namespace": namespace,
        "node": node,
    }

    if extra_context:
        try:
            extra = json.loads(extra_context)
            context.update(extra)
        except json.JSONDecodeError:
            pass

    if dry_run:
        return {
            "dry_run": True,
            "runbook": runbook.to_dict(),
            "context": context,
            "would_execute": True,
        }

    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    execution = loop.run_until_complete(
        executor.execute(
            runbook_id=runbook_id,
            context=context,
            triggered_by="mcp_tool",
        )
    )

    return {
        "execution_id": execution.id,
        "runbook_name": execution.runbook_name,
        "status": execution.status.value,
        "steps_completed": len([s for s in execution.steps if s.status.value == "success"]),
        "steps_total": len(runbook.steps),
        "requires_approval": execution.status.value == "waiting_approval",
    }


@mcp.tool()
def approve_execution(execution_id: str) -> dict:
    """Approve a pending runbook execution.

    Args:
        execution_id: Execution to approve

    Returns:
        Updated execution status
    """
    import asyncio
    from app.runbooks import get_executor

    executor = get_executor()

    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    execution = loop.run_until_complete(executor.approve(execution_id))

    if not execution:
        return {"error": f"Execution '{execution_id}' not found or not pending approval"}

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
    import asyncio
    from app.runbooks import get_executor

    executor = get_executor()

    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    execution = loop.run_until_complete(executor.reject(execution_id))

    if not execution:
        return {"error": f"Execution '{execution_id}' not found or not pending approval"}

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
    from app.runbooks import get_executor
    from app.runbooks.models import StepStatus

    executor = get_executor()

    status_filter = None
    if status:
        try:
            status_filter = StepStatus(status)
        except ValueError:
            return {
                "error": f"Invalid status '{status}'",
                "valid_statuses": [s.value for s in StepStatus],
            }

    executions = executor.list_executions(
        runbook_id=runbook_id or None,
        status=status_filter,
        limit=limit,
    )

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
    from app.runbooks import get_executor

    executor = get_executor()
    execution = executor.get_execution(execution_id)

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
    from app.runbooks import get_executor

    executor = get_executor()
    pending = executor.get_pending_approvals()

    return {
        "pending": [
            {
                "execution_id": e.id,
                "runbook_id": e.runbook_id,
                "runbook_name": e.runbook_name,
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
