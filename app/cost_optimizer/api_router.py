"""Cost Optimizer API Router.

`app/cost_optimizer/` held four distinct capabilities that nothing imported: LLM
token accounting and prompt compression, multi-dimensional budgets with alerts and
forecasting, ROI attribution for automated actions, and GPU fleet cost tracking
across AWS and GCP.

Scope note — three modules in this repo have "cost" in the name and only one of them
was reachable. `app/model_router/cost_optimizer.py` (wired) prices LLM calls and
enforces a per-customer monthly cap. `app/cost_optimization/analyzer.py` (unwired)
analyses cloud spend. This package is the third. Its `model_router.py` submodule was
a second implementation of the already-wired `AdaptiveModelRouter` and is not served
here; the other four are, because nothing else covers them.

No plan flag gates these routes. That is deliberate rather than an oversight: there
is no cost/ROI/GPU flag in `PlanFeatures`, and inventing one would be a pricing
decision. If cost reporting should be a paid tier, add the flag and gate here the way
`app/dependency_graph/api_router.py` gates on `service_dependency_map`.
"""

from __future__ import annotations

import logging
from datetime import datetime
from enum import Enum
from typing import Any, TypeVar

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.billing.runtime import current_org_id
from app.cost_optimizer.budget_manager import (
    AlertSeverity,
    BudgetManager,
    BudgetPeriod,
    BudgetType,
)
from app.cost_optimizer.gpu_tracker import CloudProvider, GPUTracker, GPUType
from app.cost_optimizer.roi_tracker import ActionType, ROITracker, SavingCategory
from app.cost_optimizer.token_optimizer import TokenOptimizer

log = logging.getLogger("omnipotent.cost_optimizer.api")

EnumT = TypeVar("EnumT", bound=Enum)

router = APIRouter(prefix="/cost", tags=["cost-optimizer"])

# Per-org engines: budgets, recorded savings and token usage are all tenant data,
# and a shared instance would report one customer's spend to another.
# Process-local — see tests/test_single_replica_until_state_externalised.py.
_budget_managers: dict[str, BudgetManager] = {}
_roi_trackers: dict[str, ROITracker] = {}
_token_optimizers: dict[str, TokenOptimizer] = {}

# The GPU tracker is keyed by org too, but holds cloud API clients rather than
# tenant records, so it is created lazily and closed on shutdown.
_gpu_trackers: dict[str, GPUTracker] = {}


def reset_engines() -> None:
    """Drop every per-org engine. Used by tests and process shutdown."""
    _budget_managers.clear()
    _roi_trackers.clear()
    _token_optimizers.clear()
    _gpu_trackers.clear()


async def close_engines() -> None:
    """Close cloud API clients held by the GPU trackers."""
    for tracker in _gpu_trackers.values():
        try:
            await tracker.close()
        except Exception as exc:  # pragma: no cover - shutdown best effort
            log.warning("Failed to close GPU tracker cleanly: %s", exc)
    _gpu_trackers.clear()


def _org_key() -> str:
    return str(current_org_id())


def _budgets() -> BudgetManager:
    return _budget_managers.setdefault(_org_key(), BudgetManager())


def _roi() -> ROITracker:
    return _roi_trackers.setdefault(_org_key(), ROITracker())


def _tokens() -> TokenOptimizer:
    return _token_optimizers.setdefault(_org_key(), TokenOptimizer())


def _gpus() -> GPUTracker:
    return _gpu_trackers.setdefault(_org_key(), GPUTracker())


def _enum_or_422(enum_cls: type[EnumT], value: str, field: str) -> EnumT:
    """Turn an unknown enum value into a 422 that names the valid options.

    Without this the constructor's ValueError surfaces as an opaque 500.
    """
    try:
        return enum_cls(value)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Unknown {field} '{value}'. Expected one of: "
                + ", ".join(str(e.value) for e in enum_cls)
            ),
        ) from None


# --------------------------------------------------------------------------
# Budgets
# --------------------------------------------------------------------------


class CreateBudgetRequest(BaseModel):
    name: str
    budget_type: str = "total"
    period: str = "monthly"
    limit: float = Field(gt=0)
    thresholds: list[dict[str, Any]] | None = None
    team: str | None = None
    service: str | None = None


class RecordSpendRequest(BaseModel):
    amount: float = Field(gt=0)
    budget_type: str = "total"
    team: str | None = None
    service: str | None = None
    description: str = ""
    metadata: dict[str, Any] | None = None


@router.post("/budgets")
async def create_budget(req: CreateBudgetRequest) -> dict[str, Any]:
    """Create a budget with alert thresholds."""
    budget = _budgets().create_budget(
        name=req.name,
        budget_type=_enum_or_422(BudgetType, req.budget_type, "budget_type"),
        period=_enum_or_422(BudgetPeriod, req.period, "period"),
        limit=req.limit,
        thresholds=req.thresholds,
        tenant_id=_org_key(),
        team=req.team,
        service=req.service,
    )
    return budget.to_dict()


@router.get("/budgets")
async def list_budgets(
    team: str | None = Query(None),
    budget_type: str | None = Query(None),
) -> dict[str, Any]:
    """Every budget for this organization."""
    parsed = (
        _enum_or_422(BudgetType, budget_type, "budget_type") if budget_type else None
    )
    budgets = _budgets().list_budgets(
        tenant_id=_org_key(), team=team, budget_type=parsed
    )
    return {"count": len(budgets), "budgets": [b.to_dict() for b in budgets]}


@router.get("/budgets/{budget_id}")
async def get_budget(budget_id: str) -> dict[str, Any]:
    budget = _budgets().get_budget(budget_id)
    if budget is None:
        raise HTTPException(status_code=404, detail=f"No budget '{budget_id}'")
    return budget.to_dict()


@router.post("/budgets/spend")
async def record_spend(req: RecordSpendRequest) -> dict[str, Any]:
    """Record spend against matching budgets, returning any alerts it triggered."""
    alerts = _budgets().record_usage(
        amount=req.amount,
        budget_type=_enum_or_422(BudgetType, req.budget_type, "budget_type"),
        tenant_id=_org_key(),
        team=req.team,
        service=req.service,
        description=req.description,
        metadata=req.metadata,
    )
    return {
        "alerts_triggered": len(alerts),
        "alerts": [a.to_dict() for a in alerts],
    }


@router.get("/budgets/{budget_id}/forecast")
async def forecast_budget(budget_id: str) -> dict[str, Any]:
    """Project this budget's end-of-period spend from what has been recorded."""
    if _budgets().get_budget(budget_id) is None:
        raise HTTPException(status_code=404, detail=f"No budget '{budget_id}'")
    return _budgets().forecast_budget(budget_id).to_dict()


@router.get("/alerts")
async def list_alerts(
    budget_id: str | None = Query(None),
    severity: str | None = Query(None),
    unacknowledged_only: bool = Query(False),
) -> dict[str, Any]:
    parsed = _enum_or_422(AlertSeverity, severity, "severity") if severity else None
    alerts = _budgets().get_alerts(
        budget_id=budget_id,
        severity=parsed,
        unacknowledged_only=unacknowledged_only,
    )
    return {"count": len(alerts), "alerts": [a.to_dict() for a in alerts]}


@router.post("/alerts/{alert_id}/acknowledge")
async def acknowledge_alert(alert_id: str, acknowledged_by: str) -> dict[str, Any]:
    if not _budgets().acknowledge_alert(alert_id, acknowledged_by):
        raise HTTPException(status_code=404, detail=f"No alert '{alert_id}'")
    return {"alert_id": alert_id, "acknowledged_by": acknowledged_by}


@router.get("/spend/by-team")
async def spend_by_team() -> dict[str, Any]:
    return _budgets().get_usage_by_team(tenant_id=_org_key())


@router.get("/spend/by-service")
async def spend_by_service() -> dict[str, Any]:
    return _budgets().get_usage_by_service(tenant_id=_org_key())


# --------------------------------------------------------------------------
# ROI
# --------------------------------------------------------------------------


class RecordSavingRequest(BaseModel):
    category: str
    action_type: str
    description: str
    incident_id: str | None = None
    service: str | None = None
    team: str | None = None
    time_saved_minutes: float | None = None
    amount_usd: float | None = None
    ai_cost: float = 0.0
    metadata: dict[str, Any] | None = None


@router.post("/roi/savings")
async def record_saving(req: RecordSavingRequest) -> dict[str, Any]:
    """Record a saving attributable to an automated action."""
    saving = _roi().record_saving(
        category=_enum_or_422(SavingCategory, req.category, "category"),
        action_type=_enum_or_422(ActionType, req.action_type, "action_type"),
        description=req.description,
        incident_id=req.incident_id,
        service=req.service,
        team=req.team,
        time_saved_minutes=req.time_saved_minutes,
        amount_usd=req.amount_usd,
        ai_cost=req.ai_cost,
        metadata=req.metadata,
    )
    return saving.to_dict()


@router.get("/roi/savings")
async def list_savings(
    category: str | None = Query(None),
    action_type: str | None = Query(None),
    service: str | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
) -> dict[str, Any]:
    savings = _roi().get_savings(
        category=(
            _enum_or_422(SavingCategory, category, "category") if category else None
        ),
        action_type=(
            _enum_or_422(ActionType, action_type, "action_type")
            if action_type
            else None
        ),
        service=service,
        limit=limit,
    )
    return {"count": len(savings), "savings": [s.to_dict() for s in savings]}


@router.get("/roi/report")
async def roi_report(
    start: datetime | None = Query(None),
    end: datetime | None = Query(None),
) -> dict[str, Any]:
    """Savings, AI spend and net ROI over a window.

    Time-based savings are priced at the tracker's engineer hourly rate, which is an
    assumption rather than a measurement. It is reported alongside the figures so a
    reader can judge them.
    """
    tracker = _roi()
    report = tracker.generate_report(start_time=start, end_time=end).to_dict()
    report["assumptions"] = {
        "engineer_hourly_rate_usd": tracker.engineer_hourly_rate,
        "note": (
            "Savings recorded as time are converted to dollars at this rate. "
            "Incident-prevention savings additionally assume a fixed handling time "
            "per severity."
        ),
    }
    return report


@router.get("/roi/by-action/{action_type}")
async def roi_by_action(action_type: str) -> dict[str, Any]:
    parsed = _enum_or_422(ActionType, action_type, "action_type")
    return _roi().calculate_roi_by_action(parsed).to_dict()


# --------------------------------------------------------------------------
# Tokens
# --------------------------------------------------------------------------


class OptimizePromptRequest(BaseModel):
    prompt: str
    task_type: str = "general"
    preserve_examples: bool = True
    model: str | None = None


@router.post("/tokens/optimize")
async def optimize_prompt(req: OptimizePromptRequest) -> dict[str, Any]:
    """Compress a prompt, reporting what was removed and what it saves."""
    result = _tokens().optimize_prompt(
        prompt=req.prompt,
        task_type=req.task_type,
        preserve_examples=req.preserve_examples,
        model=req.model,
    )
    return {"optimized_prompt": result.optimized_text, **result.to_dict()}


@router.get("/tokens/usage")
async def token_usage() -> dict[str, Any]:
    """LLM token usage and spend for this organization."""
    return await _tokens().get_usage_stats(tenant_id=_org_key())


@router.get("/tokens/budget")
async def token_budget() -> dict[str, Any]:
    """Whether this organization is within its token budget."""
    return await _tokens().check_budget(tenant_id=_org_key())


# --------------------------------------------------------------------------
# GPU fleet
# --------------------------------------------------------------------------


@router.post("/gpu/scan")
async def scan_gpus(
    provider: str = Query("aws", pattern="^(aws|gcp)$"),
) -> dict[str, Any]:
    """Scan a cloud account for running GPU instances.

    Returns an empty inventory when the cloud credentials are absent rather than
    reporting a fleet that was never observed.
    """
    tracker = _gpus()
    if provider == "aws":
        instances = await tracker.scan_aws_instances()
    else:
        instances = await tracker.scan_gcp_instances()
    return {
        "provider": provider,
        "instances_found": len(instances),
        "instances": [i.to_dict() for i in instances],
    }


@router.get("/gpu/inventory")
async def gpu_inventory() -> dict[str, Any]:
    return _gpus().get_inventory_summary()


@router.get("/gpu/recommendations")
async def gpu_recommendations() -> dict[str, Any]:
    recs = await _gpus().get_recommendations()
    return {"count": len(recs), "recommendations": [r.to_dict() for r in recs]}


@router.get("/gpu/spot-prices")
async def gpu_spot_prices() -> dict[str, Any]:
    availability = await _gpus().get_aws_spot_prices()
    return {"count": len(availability), "prices": [a.to_dict() for a in availability]}


@router.get("/gpu/forecast/{gpu_type}")
async def gpu_forecast(
    gpu_type: str,
    horizon_days: int = Query(30, ge=1, le=365),
) -> dict[str, Any]:
    """Project demand for a GPU type from observed usage.

    Answers `status: "insufficient_data"` until enough scans have been recorded.
    """
    parsed = _enum_or_422(GPUType, gpu_type, "gpu_type")
    return await _gpus().forecast_demand(parsed, horizon_days=horizon_days)


@router.get("/gpu/instances")
async def gpu_instances(
    provider: str | None = Query(None),
    available_only: bool = Query(False),
) -> dict[str, Any]:
    parsed = (
        _enum_or_422(CloudProvider, provider, "provider") if provider else None
    )
    instances = _gpus().list_instances(
        provider=parsed, available_only=available_only
    )
    return {"count": len(instances), "instances": [i.to_dict() for i in instances]}
