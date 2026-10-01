"""Runbook API Router.

REST API for runbook management and execution.

Three things about this module changed together, because fixing any one of them
alone would have left the API still lying about something:

*Storage.* Definitions and executions lived in two module-level dicts, seeded at
import from DEFAULT_RUNBOOKS. A runbook an operator authored was visible only to
the replica that served the POST and gone at the next deploy. Both now live in
the database, scoped per tenant (see `app/runbooks/store.py`). The owning org is
resolved once per request with `storage_org_id()` and passed to the store
explicitly, rather than left to the store to read back off the contextvar: the
background task that runs the steps can outlive the middleware that set it.

*Execution.* `POST /{id}/execute` took a `BackgroundTasks` argument and never
scheduled anything on it. It wrote a record with status "running" that nothing
would ever advance, and `approve` flipped a status field to "running" with the
same effect — no step of any runbook was ever run through this API. Both now go
through the executor, which is fail-closed: a step whose backend is not
configured reports failure rather than claiming an action it did not perform.

*Routing.* `GET /executions` was declared after `GET /{runbook_id}`, so FastAPI
matched it as a runbook id and it answered 404 "Runbook not found" — the
execution listing had never been reachable. Literal paths now precede the
parameterised one.

*Authorisation.* Every route here was open to any authenticated caller. The
app-wide dependency is `Depends(require_auth)`, which only proves a credential
is valid; `require_perm` is the sole authorisation mechanism in this codebase and
it is a per-route dependency, so a router that declares none is a router with no
RBAC at all. A `viewer` API key — the least-privileged role, refused 403 when it
tries to approve a single agent write — could author a runbook containing a
`kubernetes` or `command` step, execute it, clear its own approval gate and then
delete both the runbook and the execution rows that recorded what it did. The
matrix is applied below, once, for the whole router.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from app import tenant_context
from app.runbooks import store
from app.runbooks.executor import run_execution
from app.runbooks.models import (
    ExecutionMode,
    Runbook,
    RunbookExecution,
    RunbookStep,
    StepStatus,
    StepType,
)
from app.tenant_context import storage_org_id

log = logging.getLogger("omnipotent.runbooks.api")


async def _enforce_runbook_rbac(request: Request) -> None:
    """Run the org RBAC matrix for whichever runbook route was matched.

    Keyed on the matched endpoint rather than on the HTTP method, because method
    is not enough here: `POST /{id}/execute` and `POST ""` are both writes but
    they are not the same privilege. The table lives at the foot of this module,
    next to the routes it classifies.

    Authoring is deliberately a rung above executing. A responder may run a
    runbook someone else wrote; if a responder could also *write* one, then
    "may execute a runbook" would silently mean "may run any command the
    executor's allowlist permits", and the distinction between the two roles
    would buy nothing. So definitions are `manage_integrations` (admin, the same
    rung as handing the deployment a Datadog or GitHub credential) and running,
    approving and rejecting are `approve_action` (responder, the rung the agent's
    own proposed writes already sit on).

    Unclassified routes are refused rather than served: a route added later
    without a table entry fails loudly here instead of quietly inheriting no
    authorisation, which is the exact way this router lost its RBAC in the first
    place. `require_perm` itself is a no-op in single-tenant deployments, which
    have no roles, so the offline demo is unaffected.
    """
    from app.api.main import require_perm  # here, not at module scope: main imports us

    endpoint = getattr(request.scope.get("route"), "endpoint", None)
    action = _PERMISSION_BY_ENDPOINT.get(endpoint)
    if action is None:
        name = getattr(endpoint, "__name__", None) or request.url.path
        log.error("Runbook route %s has no entry in _PERMISSION_BY_ENDPOINT", name)
        raise HTTPException(
            status_code=500,
            detail=(
                f"Runbook route '{name}' has no RBAC classification; refusing to "
                "serve it rather than run it unauthorised."
            ),
        )
    await require_perm(action)()


router = APIRouter(
    prefix="/runbooks",
    tags=["runbooks"],
    dependencies=[Depends(_enforce_runbook_rbac)],
)


class RunbookResponse(BaseModel):
    id: str
    name: str
    description: str
    service: str
    trigger: str
    execution_mode: str
    step_count: int
    labels: dict[str, str]
    enabled: bool


class RunbookDetailResponse(BaseModel):
    id: str
    name: str
    description: str
    service: str
    trigger: str
    execution_mode: str
    steps: list[dict[str, Any]]
    labels: dict[str, str]
    enabled: bool


class CreateRunbookRequest(BaseModel):
    name: str
    description: str
    service: str = "*"
    trigger: str = "manual"
    execution_mode: str = "requires_approval"
    steps: list[dict[str, Any]] = []
    labels: dict[str, str] = {}


class UpdateRunbookRequest(BaseModel):
    """Full replacement of a runbook's editable fields (PUT semantics)."""

    name: str
    description: str
    service: str = "*"
    trigger: str = "manual"
    execution_mode: str = "requires_approval"
    steps: list[dict[str, Any]] = []
    labels: dict[str, str] = {}
    enabled: bool | None = None


class ExecuteRunbookRequest(BaseModel):
    service: str | None = None
    namespace: str | None = None
    params: dict[str, Any] = {}


class ExecutionResponse(BaseModel):
    id: str
    runbook_id: str
    runbook_name: str
    status: str
    triggered_by: str
    started_at: str
    completed_at: str | None
    steps: list[dict[str, Any]]
    # Neither of these is a step result, which is why they are separate fields:
    # `waiting_on` is the step at an approval gate, which has not run, and
    # `error` is why a run died when no step was there to attribute it to (the
    # executor raising, or a resume the runbook no longer supports). Reported by
    # the single-execution read; the listing would need one extra read per row.
    waiting_on: str | None = None
    error: str | None = None


def _summary(rb: Runbook) -> RunbookResponse:
    return RunbookResponse(
        id=rb.id,
        name=rb.name,
        description=rb.description,
        service=rb.service,
        trigger=rb.trigger,
        execution_mode=rb.execution_mode.value,
        step_count=len(rb.steps),
        labels=rb.labels,
        enabled=rb.enabled,
    )


def _detail(rb: Runbook) -> RunbookDetailResponse:
    return RunbookDetailResponse(
        id=rb.id,
        name=rb.name,
        description=rb.description,
        service=rb.service,
        trigger=rb.trigger,
        execution_mode=rb.execution_mode.value,
        steps=[s.to_dict() for s in rb.steps],
        labels=rb.labels,
        enabled=rb.enabled,
    )


def _execution(
    exec_: RunbookExecution, notes: dict[str, str] | None = None
) -> ExecutionResponse:
    notes = notes or {}
    return ExecutionResponse(
        id=exec_.id,
        runbook_id=exec_.runbook_id,
        runbook_name=exec_.runbook_name,
        status=exec_.status.value,
        triggered_by=exec_.triggered_by,
        started_at=exec_.started_at.isoformat(),
        completed_at=exec_.completed_at.isoformat() if exec_.completed_at else None,
        steps=[s.to_dict() for s in exec_.steps],
        waiting_on=notes.get("waiting_on"),
        error=notes.get("error"),
    )


@router.get("")
async def list_runbooks(
    service: str | None = None,
    trigger: str | None = None,
    enabled: bool | None = None,
) -> list[RunbookResponse]:
    """List all runbooks with optional filtering."""
    runbooks = await store.list_runbooks(
        storage_org_id(), service=service, trigger=trigger, enabled=enabled
    )
    return [_summary(rb) for rb in runbooks]


# Declared before /{runbook_id}: FastAPI matches in registration order, so with
# the execution routes last every one of them was swallowed by the runbook-id
# route — `GET /runbooks/executions` answered 404 "Runbook not found".
@router.get("/executions")
async def list_executions(
    runbook_id: str | None = None,
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
) -> list[ExecutionResponse]:
    """List runbook executions, newest first."""
    executions = await store.list_executions(
        storage_org_id(), runbook_id=runbook_id, status=status, limit=limit
    )
    return [_execution(e) for e in executions]


async def _require_execution(execution_id: str, org_id: str) -> RunbookExecution:
    """Load an execution or 404. Scoped: another tenant's id is simply not found."""
    execution = await store.get_execution(execution_id, org_id=org_id)
    if execution is None:
        raise HTTPException(status_code=404, detail="Execution not found")
    return execution


@router.get("/executions/{execution_id}")
async def get_execution(execution_id: str) -> ExecutionResponse:
    """Get one execution, including the result of every step that has run.

    The id `POST /{id}/execute` hands back resolves here immediately, so a
    caller can poll a run that is still going rather than waiting for a terminal
    status to make the record appear.
    """
    org_id = storage_org_id()
    execution = await _require_execution(execution_id, org_id)
    return _execution(
        execution, await store.execution_notes(execution_id, org_id=org_id)
    )


@router.delete("/executions/{execution_id}")
async def delete_execution(execution_id: str) -> dict[str, Any]:
    """Delete an execution record."""
    if not await store.delete_execution(execution_id, org_id=storage_org_id()):
        raise HTTPException(status_code=404, detail="Execution not found")
    return {"status": "deleted", "deleted": True}


def _schedule(
    background_tasks: BackgroundTasks, execution_id: str, approved_through: int
) -> None:
    """Queue the run, carrying this request's tenant into the background task.

    Both halves of the context are captured here rather than re-read inside the
    task: the org, because the task can run after the middleware has reset the
    contextvar and the run's rows would then land in the single-tenant bucket;
    and the tenant config, because the step handlers resolve per-tenant
    integration credentials from it.

    The run is wrapped so that a crash reaches a terminal state carrying the
    reason. Without this an executor that raised left the record in `running`
    for good, and `running` is the one status an operator reads as "it is still
    working on my incident".
    """
    cfg = tenant_context.get_tenant()
    org_id = storage_org_id()

    async def _run() -> None:
        token = tenant_context.set_tenant(cfg)
        try:
            await run_execution(
                execution_id, approved_through=approved_through, org_id=org_id
            )
        except Exception as exc:
            log.exception("Runbook execution %s failed", execution_id)
            await store.mark_failed(org_id, execution_id, str(exc))
        finally:
            tenant_context.reset_tenant(token)

    background_tasks.add_task(_run)


@router.post("/executions/{execution_id}/approve")
async def approve_execution(
    execution_id: str, background_tasks: BackgroundTasks
) -> ExecutionResponse:
    """Approve a pending execution and run it.

    Approving used to only flip a status column: the execution sat in `running`
    with zero steps forever, because nothing ever drove it and only a terminal
    status stamps `completed_at`.
    """
    result = await store.set_execution_status(
        execution_id,
        StepStatus.RUNNING,
        expect={StepStatus.PENDING, StepStatus.WAITING_APPROVAL},
        org_id=storage_org_id(),
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Execution not found")
    if isinstance(result, str):  # the "conflict" sentinel; isinstance narrows, == does not
        raise HTTPException(status_code=400, detail="Execution is not pending approval")

    # The approval covers the step this run is parked on — the next one that has
    # not been recorded yet. Anything after it that needs approval parks again.
    _schedule(background_tasks, result.id, approved_through=len(result.steps))
    return _execution(result)


@router.post("/executions/{execution_id}/reject")
async def reject_execution(execution_id: str) -> ExecutionResponse:
    """Reject a pending execution.

    Recorded as SKIPPED, not FAILED: a human declining to run a remediation is
    not the same event as a remediation that ran and broke, and the execution
    list is the audit trail both are read from.
    """
    result = await store.set_execution_status(
        execution_id,
        StepStatus.SKIPPED,
        expect={StepStatus.PENDING, StepStatus.WAITING_APPROVAL},
        org_id=storage_org_id(),
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Execution not found")
    if isinstance(result, str):
        raise HTTPException(status_code=400, detail="Execution is not pending approval")
    return _execution(result)


@router.get("/{runbook_id}")
async def get_runbook(runbook_id: str) -> RunbookDetailResponse:
    """Get a specific runbook by ID."""
    rb = await store.get_runbook(runbook_id, org_id=storage_org_id())
    if not rb:
        raise HTTPException(status_code=404, detail="Runbook not found")
    return _detail(rb)


def _parse_mode(raw: str) -> ExecutionMode:
    """Read an execution mode, naming the value and the alternatives when it is not one.

    `ExecutionMode("automatic")` used to raise ValueError straight out of the
    route, which FastAPI turned into a 500 that told the caller nothing.
    """
    try:
        return ExecutionMode(raw)
    except ValueError as e:
        raise ValueError(
            f"Unknown execution_mode {raw!r}; expected one of "
            f"{sorted(m.value for m in ExecutionMode)}"
        ) from e


def _build_steps(raw: list[dict[str, Any]]) -> list[RunbookStep]:
    """Turn the JSON step payload into RunbookStep records.

    Raises ValueError on an unknown step_type, naming the offending step and the
    valid values, which the routes translate into a 4xx instead of letting it
    surface as a 500.
    """
    steps: list[RunbookStep] = []
    for i, step_data in enumerate(raw):
        raw_type = step_data.get("step_type", "command")
        try:
            step_type = StepType(raw_type)
        except ValueError as e:
            raise ValueError(
                f"Step {i}: unknown step_type {raw_type!r}; expected one of "
                f"{sorted(t.value for t in StepType)}"
            ) from e
        steps.append(
            RunbookStep(
                id=step_data.get("id", f"step-{i}"),
                name=step_data.get("name", f"Step {i + 1}"),
                description=step_data.get("description", ""),
                step_type=step_type,
                action=step_data.get("action", ""),
                params=step_data.get("params", {}),
                timeout_seconds=step_data.get("timeout_seconds", 300),
                continue_on_failure=step_data.get("continue_on_failure", False),
                requires_approval=step_data.get("requires_approval", False),
                rollback_step=step_data.get("rollback_step"),
            )
        )
    return steps


@router.post("")
async def create_runbook(req: CreateRunbookRequest) -> RunbookDetailResponse:
    """Create a new runbook.

    An unusable enum value is a 422 here — the same code FastAPI's own body
    validation uses, since that is what this is — carrying a detail that names
    the value that was rejected. `PUT` below answers 400 for the same mistake;
    the two codes are pinned by tests written either side of this merge and were
    left as they were found rather than silently changing one API's contract.
    """
    try:
        steps = _build_steps(req.steps)
        mode = _parse_mode(req.execution_mode)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    rb = Runbook(
        id=store.new_runbook_id(),
        name=req.name,
        description=req.description,
        service=req.service,
        trigger=req.trigger,
        execution_mode=mode,
        steps=steps,
        labels=req.labels,
        enabled=True,
    )
    await store.create_runbook(rb, org_id=storage_org_id())
    return _detail(rb)


@router.put("/{runbook_id}")
async def update_runbook(
    runbook_id: str, req: UpdateRunbookRequest
) -> RunbookDetailResponse:
    """Replace an existing runbook. This is what the Runbook Builder's Save does.

    Without it the page issued a PUT that came back 405 and the edit was lost;
    the page reported nothing, so a save silently did nothing.
    """
    org_id = storage_org_id()
    existing = await store.get_runbook(runbook_id, org_id=org_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Runbook not found")

    try:
        steps = _build_steps(req.steps)
        mode = _parse_mode(req.execution_mode)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    rb = Runbook(
        id=runbook_id,
        name=req.name,
        description=req.description,
        service=req.service,
        trigger=req.trigger,
        execution_mode=mode,
        steps=steps,
        labels=req.labels,
        enabled=req.enabled if req.enabled is not None else existing.enabled,
    )
    if await store.update_runbook(rb, org_id=org_id) is None:
        raise HTTPException(status_code=404, detail="Runbook not found")
    return _detail(rb)


@router.delete("/{runbook_id}")
async def delete_runbook(runbook_id: str) -> dict[str, Any]:
    """Delete a runbook.

    `deleted` reports what actually happened rather than only that the request
    was accepted; a caller that reads it cannot mistake a no-op for a removal.
    """
    if not await store.delete_runbook(runbook_id, org_id=storage_org_id()):
        raise HTTPException(status_code=404, detail="Runbook not found")
    return {"status": "deleted", "deleted": True}


@router.post("/{runbook_id}/execute")
async def execute_runbook(
    runbook_id: str,
    req: ExecuteRunbookRequest,
    background_tasks: BackgroundTasks,
) -> ExecutionResponse:
    """Execute a runbook.

    Returns as soon as the run is recorded and scheduled; poll
    `GET /runbooks/executions/{id}` for step results. A runbook in
    REQUIRES_APPROVAL mode is recorded WAITING_APPROVAL, naming its first step,
    and nothing runs until a human approves it.
    """
    org_id = storage_org_id()
    rb = await store.get_runbook(runbook_id, org_id=org_id)
    if not rb:
        raise HTTPException(status_code=404, detail="Runbook not found")

    if not rb.enabled:
        raise HTTPException(status_code=400, detail="Runbook is disabled")

    execution = await store.record_execution(
        rb,
        status=store.default_execution_status(rb.execution_mode),
        context={
            "service": req.service or "default",
            "namespace": req.namespace or "default",
            **req.params,
        },
        org_id=org_id,
    )

    # A runbook that requires approval waits for a human before its *first*
    # step, not merely before the steps that repeat the flag; anything else
    # starts now. Recording the row and returning — which is all this route used
    # to do — left four declared steps and zero executed ones.
    if execution.status == StepStatus.RUNNING:
        _schedule(background_tasks, execution.id, approved_through=-1)
        return _execution(execution)
    return _execution(execution, {"waiting_on": rb.steps[0].id} if rb.steps else None)


# The permission every route above is served under, read by `_enforce_runbook_rbac`.
# Declared here so it can name the endpoint functions themselves: a rename or a
# deletion is then an ImportError-grade mistake rather than a path string that
# quietly stops matching and leaves a route ungoverned.
#
# Deleting an execution is an admin act, not a responder one: those rows are the
# record of what ran against production and who cleared it, and the trail they
# belong to is already `view_audit` (admin) to so much as read.
_PERMISSION_BY_ENDPOINT: dict[Any, str] = {
    list_runbooks: "view",
    get_runbook: "view",
    list_executions: "view",
    get_execution: "view",
    create_runbook: "manage_integrations",
    update_runbook: "manage_integrations",
    delete_runbook: "manage_integrations",
    delete_execution: "manage_integrations",
    execute_runbook: "approve_action",
    approve_execution: "approve_action",
    reject_execution: "approve_action",
}
