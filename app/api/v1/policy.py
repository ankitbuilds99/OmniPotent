"""Policy Engine API.

Provides endpoints for:
- Policy evaluation (OPA/Kyverno)
- Change window management
- Entry/exit criteria evaluation
- Policy versioning and audit
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field

from app.policy_engine import (
    PolicyEngine,
    OPAClient,
    OPAInput,
    KyvernoClient,
    ChangeWindowManager,
    WindowType,
    CriteriaEvaluator,
    EntryCriteria,
    Criterion,
    CriteriaType,
    CriteriaOperator,
    create_policy_engine,
    create_change_window_manager,
    create_criteria_evaluator,
)
from app.policy_engine.change_windows import RecurrencePattern
from app.api.main import require_perm
from app import mutation_freeze
from app.tenant_context import storage_org_id

log = logging.getLogger("omnipotent.api.policy")

# Router-wide `view`, plus a stronger action on every route that persists or
# executes something. Nothing in this file was gated, which cost nothing while
# the router was unreachable; including it on the app is what turned the gap into
# a live escalation for any multi-tenant `viewer` seat.
#
# `GET /policy/audit` is the sharpest edge and the reason this is not merely a
# missing gate: the root `GET /audit` is admin-only behind
# require_perm("view_audit"), and an ungated mirror of the same trail under /v1
# is a working way *round* a gate the deployment already enforces. It reads the
# policy engine's own audit log — evaluation ids, the actor named on each request
# and every approval decision — so it takes the same `view_audit`.
#
# Change windows are the deployment's production change-control record, not a
# view of one: a freeze blocks every deploy in its scope, an emergency override
# lifts every freeze at once, and deleting a window removes the block outright.
# Rolling a policy back to an earlier version is the same kind of act. That is
# configuration of how the fleet may be changed, so those take
# `manage_integrations` (admin) — the action the root config routes already use
# for the settings that decide what the fleet can reach. The approval routes take
# `approve_action` (responder), which is the vocabulary item in
# app/tenancy/models.py that names this exact workflow; there is no
# change-window-specific action to name instead.
#
# Evaluations stay at the router's `view`: /evaluate, /opa/evaluate,
# /kyverno/evaluate, /windows/check and /criteria/entry/evaluate all answer
# "would this change be allowed", which is what a read-only seat exists for, and
# none of them creates a window, an approval or a policy version. `require_perm`
# is a no-op in single-tenant mode, so the offline demo is unaffected.
_MANAGE = [Depends(require_perm("manage_integrations"))]
_APPROVE = [Depends(require_perm("approve_action"))]
_AUDIT = [Depends(require_perm("view_audit"))]


class MutationFreezeRequest(BaseModel):
    """Operator-controlled mutation freeze scope."""

    tenant_id: str | None = Field(default=None, min_length=1, max_length=256)
    global_scope: bool = False

router: APIRouter = APIRouter(
    prefix="/policy",
    tags=["policy-engine"],
    dependencies=[Depends(require_perm("view"))],
)

# Singleton instances
_policy_engine: PolicyEngine | None = None
_opa_client: OPAClient | None = None
_kyverno_client: KyvernoClient | None = None
_window_manager: ChangeWindowManager | None = None
_criteria_evaluator: CriteriaEvaluator | None = None


def get_policy_engine() -> PolicyEngine:
    global _policy_engine
    if _policy_engine is None:
        _policy_engine = create_policy_engine()
    return _policy_engine


def get_opa_client() -> OPAClient:
    global _opa_client
    if _opa_client is None:
        _opa_client = OPAClient()
    return _opa_client


def get_kyverno_client() -> KyvernoClient:
    global _kyverno_client
    if _kyverno_client is None:
        _kyverno_client = KyvernoClient()
    return _kyverno_client


def get_window_manager() -> ChangeWindowManager:
    global _window_manager
    if _window_manager is None:
        _window_manager = create_change_window_manager()
    return _window_manager


def get_criteria_evaluator() -> CriteriaEvaluator:
    global _criteria_evaluator
    if _criteria_evaluator is None:
        _criteria_evaluator = create_criteria_evaluator()
    return _criteria_evaluator


# Request/Response Models
class PolicyEvaluationRequest(BaseModel):
    action: str = Field(..., description="Action being performed (deploy, scale, restart, etc.)")
    resource: dict[str, Any] = Field(..., description="Resource being acted upon")
    context: dict[str, Any] = Field(default_factory=dict, description="Additional context")
    requested_by: str = Field("system", description="Who is requesting the action")


class PolicyViolationResponse(BaseModel):
    policy_name: str
    message: str
    severity: str
    source: str
    resource: str | None
    remediation: str | None


class PolicyEvaluationResponse(BaseModel):
    decision: str
    violations: list[PolicyViolationResponse]
    warnings: list[PolicyViolationResponse]
    opa_allowed: bool | None
    kyverno_allowed: bool | None
    window_allowed: bool | None
    criteria_passed: bool | None
    evaluation_id: str
    duration_ms: float
    requires_approval: bool
    approval_reasons: list[str]
    evaluated_at: str


class OPAEvaluationRequest(BaseModel):
    policy_id: str = Field(..., description="ID of the OPA policy to evaluate")
    action: str = Field(..., description="Action being performed")
    resource: dict[str, Any] = Field(..., description="Resource data")
    context: dict[str, Any] = Field(default_factory=dict, description="Context data")
    user: dict[str, Any] | None = Field(None, description="User data")
    environment: dict[str, Any] | None = Field(None, description="Environment data")


class OPAResultResponse(BaseModel):
    allowed: bool
    policy_id: str
    decision_id: str
    violations: list[dict[str, Any]]
    warnings: list[str]
    evaluation_time_ms: int
    evaluated_at: str


class KyvernoEvaluationRequest(BaseModel):
    resource: dict[str, Any] = Field(..., description="Kubernetes resource manifest")
    namespace: str = Field("default", description="Namespace context")


class KyvernoResultResponse(BaseModel):
    policy_name: str
    allowed: bool
    violations: list[dict[str, Any]]
    mutations: list[dict[str, Any]]
    warnings: list[str]
    evaluated_at: str
    engine: str
    authoritative: bool


class ChangeWindowRequest(BaseModel):
    name: str = Field(..., description="Window name")
    window_type: str = Field(..., description="Type: maintenance, freeze, restricted")
    start_time: str = Field(..., description="ISO timestamp for window start")
    end_time: str = Field(..., description="ISO timestamp for window end")
    recurrence: str = Field("none", description="Recurrence: none, daily, weekly, monthly")
    recurrence_day: int | None = Field(None, description="Day for recurrence (0=Mon for weekly, 1-31 for monthly)")
    environments: list[str] = Field(default_factory=lambda: ["*"])
    services: list[str] = Field(default_factory=lambda: ["*"])
    teams: list[str] = Field(default_factory=lambda: ["*"])
    allowed_action_types: list[str] = Field(default_factory=list)
    blocked_action_types: list[str] = Field(default_factory=list)
    max_risk_score: float = Field(1.0, ge=0, le=1)
    requires_approval: bool = Field(False)
    reason: str = Field("")
    created_by: str = Field("api")


class ChangeWindowResponse(BaseModel):
    id: str
    name: str
    window_type: str
    start_time: str
    end_time: str
    recurrence: str
    environments: list[str]
    services: list[str]
    max_risk_score: float
    requires_approval: bool
    reason: str
    is_active: bool


class WindowCheckRequest(BaseModel):
    action_type: str = Field(..., description="Type of action")
    risk_score: float = Field(0.5, ge=0, le=1, description="Risk score of the action")
    environment: str | None = Field(None, description="Target environment")
    service: str | None = Field(None, description="Target service")
    team: str | None = Field(None, description="Team performing action")


class WindowCheckResponse(BaseModel):
    allowed: bool
    reason: str
    requires_approval: bool
    override_available: bool
    active_windows: list[dict[str, Any]]
    blocking_window: dict[str, Any] | None


class EmergencyOverrideRequest(BaseModel):
    duration_minutes: int = Field(60, ge=5, le=480, description="Override duration (5-480 minutes)")
    environment: str | None = Field(None, description="Scope to environment")
    service: str | None = Field(None, description="Scope to service")
    reason: str = Field(..., description="Reason for override")
    activated_by: str = Field(..., description="Who activated the override")


class CriterionRequest(BaseModel):
    name: str
    criteria_type: str = Field(..., description="Type: health_check, metric_threshold, error_rate, latency, etc.")
    operator: str = Field(..., description="Comparison: lt, lte, gt, gte, eq, ne, between")
    threshold: float | str
    metric_name: str | None = None
    evaluation_window: int = Field(60)
    consecutive_failures: int = Field(1)
    weight: float = Field(1.0)
    description: str = Field("")


class EntryCriteriaRequest(BaseModel):
    name: str
    criteria: list[CriterionRequest]
    require_all: bool = Field(True)
    timeout_seconds: int = Field(300)
    context: dict[str, Any] = Field(default_factory=dict)


class CriteriaResultResponse(BaseModel):
    passed: bool
    criteria_name: str
    passed_count: int
    failed_count: int
    skipped_count: int
    duration_ms: float
    message: str
    evaluations: list[dict[str, Any]]


class ApprovalRequest(BaseModel):
    evaluation_id: str
    reason: str
    requested_by: str
    approvers: list[str] | None = None


class ApprovalActionRequest(BaseModel):
    approved_by: str
    notes: str = ""


# Endpoints
@router.post("/evaluate", response_model=PolicyEvaluationResponse)
async def evaluate_policy(
    request: PolicyEvaluationRequest,
    engine: PolicyEngine = Depends(get_policy_engine),
) -> PolicyEvaluationResponse:
    """Evaluate all policies for an action.

    Combines OPA, Kyverno, change windows, and entry criteria
    to produce a comprehensive policy decision.
    """
    try:
        result = await engine.evaluate(
            action=request.action,
            resource=request.resource,
            context=request.context,
            requested_by=request.requested_by,
        )

        violations = [
            PolicyViolationResponse(
                policy_name=v.policy_name,
                message=v.message,
                severity=v.severity.value,
                source=v.source,
                resource=v.resource,
                remediation=v.remediation,
            )
            for v in result.violations
        ]

        warnings = [
            PolicyViolationResponse(
                policy_name=w.policy_name,
                message=w.message,
                severity=w.severity.value,
                source=w.source,
                resource=w.resource,
                remediation=w.remediation,
            )
            for w in result.warnings
        ]

        return PolicyEvaluationResponse(
            decision=result.decision.value,
            violations=violations,
            warnings=warnings,
            opa_allowed=result.opa_allowed,
            kyverno_allowed=result.kyverno_allowed,
            window_allowed=result.window_allowed,
            criteria_passed=result.criteria_passed,
            evaluation_id=result.evaluation_id,
            duration_ms=result.duration_ms,
            requires_approval=result.requires_approval,
            approval_reasons=result.approval_reasons,
            evaluated_at=result.evaluated_at.isoformat(),
        )

    except Exception as e:
        log.error("Policy evaluation failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Policy evaluation failed: {str(e)}")


@router.post("/opa/evaluate", response_model=OPAResultResponse)
async def evaluate_opa_policy(
    request: OPAEvaluationRequest,
    opa: OPAClient = Depends(get_opa_client),
) -> OPAResultResponse:
    """Evaluate a specific OPA policy."""
    try:
        input_data = OPAInput(
            action=request.action,
            resource=request.resource,
            context=request.context,
            user=request.user,
            environment=request.environment,
        )

        result = await opa.evaluate(request.policy_id, input_data)

        return OPAResultResponse(
            allowed=result.allowed,
            policy_id=result.policy_id,
            decision_id=result.decision_id,
            violations=result.violations,
            warnings=result.warnings,
            evaluation_time_ms=result.evaluation_time_ms,
            evaluated_at=result.evaluated_at.isoformat(),
        )

    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        log.error("OPA evaluation failed: %s", e)
        raise HTTPException(status_code=500, detail=f"OPA evaluation failed: {str(e)}")


@router.get("/opa/policies")
async def list_opa_policies(
    opa: OPAClient = Depends(get_opa_client),
) -> dict[str, Any]:
    """List all registered OPA policies."""
    policies = opa.list_policies()
    return {
        "policies": [p.to_dict() for p in policies],
        "count": len(policies),
    }


@router.post("/kyverno/evaluate")
async def evaluate_kyverno_policies(
    request: KyvernoEvaluationRequest,
    kyverno: KyvernoClient = Depends(get_kyverno_client),
) -> dict[str, Any]:
    """Evaluate Kyverno policies against a Kubernetes resource."""
    try:
        results = await kyverno.evaluate(request.resource, request.namespace)

        return {
            "results": [
                KyvernoResultResponse(
                    policy_name=r.policy_name,
                    allowed=r.allowed,
                    violations=[v.to_dict() for v in r.violations],
                    mutations=r.mutations,
                    warnings=r.warnings,
                    evaluated_at=r.evaluated_at.isoformat(),
                    engine=r.engine,
                    authoritative=r.authoritative,
                ).model_dump()
                for r in results
            ],
            # An embedded evaluator can explain a local approximation, but it
            # cannot authorize a cluster mutation. Keep the aggregate fail
            # closed until every result came from the authoritative engine.
            "all_allowed": all(r.allowed and r.authoritative for r in results),
            "all_authoritative": all(r.authoritative for r in results),
            "count": len(results),
        }

    except Exception as e:
        log.error("Kyverno evaluation failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Kyverno evaluation failed: {str(e)}")


@router.get("/kyverno/policies")
async def list_kyverno_policies(
    kyverno: KyvernoClient = Depends(get_kyverno_client),
) -> dict[str, Any]:
    """List all registered Kyverno policies."""
    policies = kyverno.list_policies()
    return {
        "policies": [p.to_k8s_manifest() for p in policies],
        "count": len(policies),
    }


# Change Windows
@router.post("/windows", response_model=ChangeWindowResponse, dependencies=_MANAGE)
async def create_change_window(
    request: ChangeWindowRequest,
    manager: ChangeWindowManager = Depends(get_window_manager),
) -> ChangeWindowResponse:
    """Create a new change window."""
    try:
        window_type_map = {
            "maintenance": WindowType.MAINTENANCE,
            "freeze": WindowType.FREEZE,
            "restricted": WindowType.RESTRICTED,
            "emergency": WindowType.EMERGENCY,
        }
        recurrence_map = {
            "none": RecurrencePattern.NONE,
            "daily": RecurrencePattern.DAILY,
            "weekly": RecurrencePattern.WEEKLY,
            "biweekly": RecurrencePattern.BIWEEKLY,
            "monthly": RecurrencePattern.MONTHLY,
        }

        window = manager.create_window(
            name=request.name,
            window_type=window_type_map.get(request.window_type, WindowType.MAINTENANCE),
            start_time=datetime.fromisoformat(request.start_time.replace('Z', '+00:00')),
            end_time=datetime.fromisoformat(request.end_time.replace('Z', '+00:00')),
            recurrence=recurrence_map.get(request.recurrence, RecurrencePattern.NONE),
            recurrence_day=request.recurrence_day,
            environments=request.environments,
            services=request.services,
            teams=request.teams,
            allowed_action_types=request.allowed_action_types,
            blocked_action_types=request.blocked_action_types,
            max_risk_score=request.max_risk_score,
            requires_approval=request.requires_approval,
            reason=request.reason,
            created_by=request.created_by,
        )

        return ChangeWindowResponse(
            id=window.id,
            name=window.name,
            window_type=window.window_type.value,
            start_time=window.start_time.isoformat(),
            end_time=window.end_time.isoformat(),
            recurrence=window.recurrence.value,
            environments=window.environments,
            services=window.services,
            max_risk_score=window.max_risk_score,
            requires_approval=window.requires_approval,
            reason=window.reason,
            is_active=window.is_active(),
        )

    except Exception as e:
        log.error("Failed to create window: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/windows")
async def list_change_windows(
    active_only: bool = Query(False),
    window_type: str | None = Query(None),
    manager: ChangeWindowManager = Depends(get_window_manager),
) -> dict[str, Any]:
    """List change windows."""
    type_map = {
        "maintenance": WindowType.MAINTENANCE,
        "freeze": WindowType.FREEZE,
        "restricted": WindowType.RESTRICTED,
    }
    wt = type_map.get(window_type) if window_type else None

    windows = manager.list_windows(active_only=active_only, window_type=wt)

    return {
        "windows": [w.to_dict() for w in windows],
        "count": len(windows),
    }


@router.get("/windows/{window_id}")
async def get_change_window(
    window_id: str,
    manager: ChangeWindowManager = Depends(get_window_manager),
) -> dict[str, Any]:
    """Get a specific change window."""
    window = manager.get_window(window_id)
    if not window:
        raise HTTPException(status_code=404, detail="Window not found")
    return window.to_dict()


@router.post("/windows/check", response_model=WindowCheckResponse)
async def check_change_allowed(
    request: WindowCheckRequest,
    manager: ChangeWindowManager = Depends(get_window_manager),
) -> WindowCheckResponse:
    """Check if a change is allowed given current windows."""
    result = manager.check_change_allowed(
        action_type=request.action_type,
        risk_score=request.risk_score,
        environment=request.environment,
        service=request.service,
        team=request.team,
    )

    return WindowCheckResponse(
        allowed=result.allowed,
        reason=result.reason,
        requires_approval=result.requires_approval,
        override_available=result.override_available,
        active_windows=[w.to_dict() for w in result.active_windows],
        blocking_window=result.blocking_window.to_dict() if result.blocking_window else None,
    )


@router.post("/windows/freeze", dependencies=_MANAGE)
async def create_change_freeze(
    name: str,
    start_time: str,
    end_time: str,
    reason: str,
    environments: list[str] | None = None,
    services: list[str] | None = None,
    created_by: str = "api",
    approved_by: str = "",
    manager: ChangeWindowManager = Depends(get_window_manager),
) -> ChangeWindowResponse:
    """Create a change freeze window."""
    window = manager.create_freeze(
        name=name,
        start_time=datetime.fromisoformat(start_time.replace('Z', '+00:00')),
        end_time=datetime.fromisoformat(end_time.replace('Z', '+00:00')),
        reason=reason,
        environments=environments,
        services=services,
        created_by=created_by,
        approved_by=approved_by,
    )

    return ChangeWindowResponse(
        id=window.id,
        name=window.name,
        window_type=window.window_type.value,
        start_time=window.start_time.isoformat(),
        end_time=window.end_time.isoformat(),
        recurrence=window.recurrence.value,
        environments=window.environments,
        services=window.services,
        max_risk_score=window.max_risk_score,
        requires_approval=window.requires_approval,
        reason=window.reason,
        is_active=window.is_active(),
    )


@router.post("/windows/emergency-override", dependencies=_MANAGE)
async def activate_emergency_override(
    request: EmergencyOverrideRequest,
    manager: ChangeWindowManager = Depends(get_window_manager),
) -> dict[str, Any]:
    """Activate an emergency override to bypass change windows."""
    override_id = manager.activate_emergency_override(
        duration_minutes=request.duration_minutes,
        environment=request.environment,
        service=request.service,
        reason=request.reason,
        activated_by=request.activated_by,
    )

    return {
        "override_id": override_id,
        "status": "active",
        "expires_in_minutes": request.duration_minutes,
        "scope": {
            "environment": request.environment or "*",
            "service": request.service or "*",
        },
    }


@router.delete("/windows/emergency-override", dependencies=_MANAGE)
async def deactivate_emergency_override(
    environment: str | None = None,
    service: str | None = None,
    manager: ChangeWindowManager = Depends(get_window_manager),
) -> dict[str, str]:
    """Deactivate an emergency override."""
    if manager.deactivate_emergency_override(environment, service):
        return {"status": "deactivated"}
    raise HTTPException(status_code=404, detail="No active override found")


# Declared after `DELETE /windows/emergency-override`, and it has to stay there.
# Starlette matches routes in declaration order, so with `/windows/{window_id}`
# first the literal path never wins: `DELETE /windows/emergency-override` bound
# window_id="emergency-override" and answered 404 "Window not found" from the
# wrong handler, which is what a caller trying to lift an override actually got.
@router.delete("/windows/{window_id}", dependencies=_MANAGE)
async def delete_change_window(
    window_id: str,
    manager: ChangeWindowManager = Depends(get_window_manager),
) -> dict[str, str]:
    """Delete a change window."""
    if manager.delete_window(window_id):
        return {"status": "deleted", "window_id": window_id}
    raise HTTPException(status_code=404, detail="Window not found")


# Entry/Exit Criteria
@router.post("/criteria/entry/evaluate", response_model=CriteriaResultResponse)
async def evaluate_entry_criteria(
    request: EntryCriteriaRequest,
    evaluator: CriteriaEvaluator = Depends(get_criteria_evaluator),
) -> CriteriaResultResponse:
    """Evaluate entry criteria before a change."""
    try:
        type_map = {
            "health_check": CriteriaType.HEALTH_CHECK,
            "metric_threshold": CriteriaType.METRIC_THRESHOLD,
            "error_rate": CriteriaType.ERROR_RATE,
            "latency": CriteriaType.LATENCY,
            "dependency_ready": CriteriaType.DEPENDENCY_READY,
            "custom": CriteriaType.CUSTOM,
        }
        op_map = {
            "lt": CriteriaOperator.LESS_THAN,
            "lte": CriteriaOperator.LESS_THAN_OR_EQUAL,
            "gt": CriteriaOperator.GREATER_THAN,
            "gte": CriteriaOperator.GREATER_THAN_OR_EQUAL,
            "eq": CriteriaOperator.EQUAL,
            "ne": CriteriaOperator.NOT_EQUAL,
            "between": CriteriaOperator.BETWEEN,
            "regex": CriteriaOperator.REGEX_MATCH,
        }

        criteria = [
            Criterion(
                name=c.name,
                criteria_type=type_map.get(c.criteria_type, CriteriaType.CUSTOM),
                operator=op_map.get(c.operator, CriteriaOperator.EQUAL),
                threshold=c.threshold,
                metric_name=c.metric_name,
                evaluation_window=c.evaluation_window,
                consecutive_failures=c.consecutive_failures,
                weight=c.weight,
                description=c.description,
            )
            for c in request.criteria
        ]

        entry = EntryCriteria(
            name=request.name,
            criteria=criteria,
            require_all=request.require_all,
            timeout_seconds=request.timeout_seconds,
        )

        result = await evaluator.evaluate_entry_criteria(entry, request.context)

        return CriteriaResultResponse(
            passed=result.passed,
            criteria_name=result.criteria_name,
            passed_count=result.passed_count,
            failed_count=result.failed_count,
            skipped_count=result.skipped_count,
            duration_ms=result.duration_ms,
            message=result.message,
            evaluations=[e.to_dict() for e in result.evaluations],
        )

    except Exception as e:
        log.error("Entry criteria evaluation failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/criteria/templates")
async def list_criteria_templates(
    evaluator: CriteriaEvaluator = Depends(get_criteria_evaluator),
) -> dict[str, Any]:
    """List available criteria templates."""
    return {
        "entry_templates": list(evaluator._entry_templates.keys()),
        "exit_templates": list(evaluator._exit_templates.keys()),
    }


# Approvals
@router.post("/approvals/request", dependencies=_APPROVE)
async def request_approval(
    request: ApprovalRequest,
    engine: PolicyEngine = Depends(get_policy_engine),
) -> dict[str, Any]:
    """Request approval for a policy decision."""
    approval_id = await engine.request_approval(
        evaluation_id=request.evaluation_id,
        reason=request.reason,
        requested_by=request.requested_by,
        approvers=request.approvers,
    )

    return {
        "approval_id": approval_id,
        "status": "pending",
        "evaluation_id": request.evaluation_id,
    }


@router.post("/approvals/{approval_id}/approve", dependencies=_APPROVE)
async def approve_request(
    approval_id: str,
    request: ApprovalActionRequest,
    engine: PolicyEngine = Depends(get_policy_engine),
) -> dict[str, str]:
    """Approve a pending request."""
    if await engine.approve(approval_id, request.approved_by, request.notes):
        return {"status": "approved", "approval_id": approval_id}
    raise HTTPException(status_code=404, detail="Approval request not found")


@router.post("/approvals/{approval_id}/reject", dependencies=_APPROVE)
async def reject_request(
    approval_id: str,
    rejected_by: str,
    reason: str = "",
    engine: PolicyEngine = Depends(get_policy_engine),
) -> dict[str, str]:
    """Reject a pending request."""
    if await engine.reject(approval_id, rejected_by, reason):
        return {"status": "rejected", "approval_id": approval_id}
    raise HTTPException(status_code=404, detail="Approval request not found")


@router.get("/approvals/{approval_id}")
async def get_approval_status(
    approval_id: str,
    engine: PolicyEngine = Depends(get_policy_engine),
) -> dict[str, Any]:
    """Get status of an approval request."""
    status = engine.get_approval_status(approval_id)
    if not status:
        raise HTTPException(status_code=404, detail="Approval request not found")
    return status


# Audit
@router.get("/audit", dependencies=_AUDIT)
async def get_audit_log(
    limit: int = Query(100, ge=1, le=1000),
    action: str | None = Query(None),
    engine: PolicyEngine = Depends(get_policy_engine),
) -> dict[str, Any]:
    """Get policy evaluation audit log."""
    records = engine.get_audit_log(limit=limit, action=action)
    return {
        "records": [r.to_dict() for r in records],
        "count": len(records),
    }


# Policy Versioning
@router.get("/versions/{policy_name}")
async def list_policy_versions(
    policy_name: str,
    engine: PolicyEngine = Depends(get_policy_engine),
) -> dict[str, Any]:
    """List all versions of a policy."""
    versions = engine.list_policy_versions(policy_name)
    return {
        "policy_name": policy_name,
        "versions": [v.to_dict() for v in versions],
        "count": len(versions),
    }


@router.post("/versions/{policy_name}/rollback/{version}", dependencies=_MANAGE)
async def rollback_policy_version(
    policy_name: str,
    version: str,
    engine: PolicyEngine = Depends(get_policy_engine),
) -> dict[str, Any]:
    """Rollback a policy to a previous version."""
    result = engine.rollback_policy(policy_name, version)
    if not result:
        raise HTTPException(status_code=404, detail="Policy or version not found")
    return {
        "status": "rolled_back",
        "policy_name": policy_name,
        "version": version,
    }


@router.post("/mutation-freeze", dependencies=_MANAGE)
async def freeze_mutations(request: MutationFreezeRequest) -> dict[str, Any]:
    """Disable mutations independently of agent/LLM approval state."""
    if request.global_scope:
        mutation_freeze.freeze()
        scope = "global"
    else:
        tenant = request.tenant_id or storage_org_id()
        mutation_freeze.freeze(tenant_id=tenant)
        scope = tenant
    return {"status": "frozen", "scope": scope}


@router.delete("/mutation-freeze", dependencies=_MANAGE)
async def unfreeze_mutations(request: MutationFreezeRequest) -> dict[str, Any]:
    """Re-enable mutations for a tenant or globally."""
    if request.global_scope:
        mutation_freeze.unfreeze()
        scope = "global"
    else:
        tenant = request.tenant_id or storage_org_id()
        mutation_freeze.unfreeze(tenant_id=tenant)
        scope = tenant
    return {"status": "unfrozen", "scope": scope}


@router.get("/mutation-freeze")
async def mutation_freeze_status() -> dict[str, Any]:
    """Return whether the current tenant is mutation-frozen."""
    tenant = storage_org_id()
    return {"tenant_id": tenant, "frozen": mutation_freeze.is_frozen(tenant)}
