"""SLO API Router.

Exposes SLO management endpoints for creating, updating, and querying SLOs.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.slo.tracker import (
    SLIType,
    SLOStatus,
    SLOWindow,
    SLOTracker,
)

router = APIRouter(prefix="/api/slos", tags=["SLOs"])


class SLOCreateRequest(BaseModel):
    """Request to create a new SLO."""

    name: str = Field(..., description="Human-readable name for the SLO")
    description: str = Field("", description="Description of what this SLO measures")
    service: str = Field(..., description="Service this SLO applies to")
    sli_type: str = Field("availability", description="Type of SLI (availability, latency, error_rate, etc.)")
    target: float = Field(..., ge=0, le=1, description="Target value (0-1, e.g., 0.999 for 99.9%)")
    window: str = Field("rolling_30d", description="Measurement window (rolling_1h, rolling_7d, rolling_30d, etc.)")
    team_id: str = Field("", description="Team responsible for this SLO")
    owner: str = Field("", description="Owner of this SLO")
    labels: dict[str, str] = Field(default_factory=dict, description="Labels for filtering")
    alert_burn_rate_threshold: float = Field(1.0, description="Burn rate threshold for alerts")


class SLOUpdateRequest(BaseModel):
    """Request to update an existing SLO."""

    name: str | None = None
    description: str | None = None
    target: float | None = Field(None, ge=0, le=1)
    owner: str | None = None
    labels: dict[str, str] | None = None
    alert_burn_rate_threshold: float | None = None


class SLIRecordRequest(BaseModel):
    """Request to record an SLI measurement."""

    good_count: int = Field(..., ge=0, description="Number of good events")
    total_count: int = Field(..., ge=1, description="Total number of events")
    timestamp: datetime | None = Field(None, description="Timestamp of measurement (defaults to now)")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Additional metadata")


class SLOResponse(BaseModel):
    """SLO response model."""

    id: str
    name: str
    description: str
    service: str
    sli_type: str
    target: float
    window: str
    team_id: str
    owner: str
    created_at: datetime
    updated_at: datetime
    labels: dict[str, str]
    alert_burn_rate_threshold: float


class ErrorBudgetResponse(BaseModel):
    """Error budget response model."""

    slo_id: str
    total_budget: float
    consumed: float
    remaining: float
    remaining_percent: float
    burn_rate: float
    projected_exhaustion: datetime | None
    status: str
    window_start: datetime
    window_end: datetime


class SLIResponse(BaseModel):
    """SLI measurement response."""

    id: str
    slo_id: str
    timestamp: datetime
    value: float
    good_count: int
    total_count: int


class BurnRateResponse(BaseModel):
    """Multi-window burn rate response."""

    slo_id: str
    burn_rate_1h: float
    burn_rate_6h: float
    burn_rate_24h: float
    burn_rate_7d: float
    is_alerting: bool
    alert_severity: str | None


def _get_tracker(org_id: str = "default") -> SLOTracker:
    """Get SLO tracker for organization."""
    return SLOTracker(org_id=org_id)


def _slo_to_response(slo: Any) -> SLOResponse:
    """Convert SLO dataclass to response model."""
    return SLOResponse(
        id=slo.id,
        name=slo.name,
        description=slo.description,
        service=slo.service,
        sli_type=slo.sli_type.value,
        target=slo.target,
        window=slo.window.value,
        team_id=slo.team_id,
        owner=slo.owner,
        created_at=slo.created_at,
        updated_at=slo.updated_at,
        labels=slo.labels,
        alert_burn_rate_threshold=slo.alert_burn_rate_threshold,
    )


@router.get("", response_model=list[SLOResponse])
async def list_slos(
    service: str | None = Query(None, description="Filter by service"),
    team_id: str | None = Query(None, description="Filter by team"),
    org_id: str = Query("default", description="Organization ID"),
) -> list[SLOResponse]:
    """List all SLOs."""
    tracker = _get_tracker(org_id)
    slos = tracker.list_slos(service=service, team_id=team_id)
    return [_slo_to_response(s) for s in slos]


@router.post("", response_model=SLOResponse, status_code=201)
async def create_slo(
    request: SLOCreateRequest,
    org_id: str = Query("default", description="Organization ID"),
) -> SLOResponse:
    """Create a new SLO."""
    tracker = _get_tracker(org_id)

    try:
        sli_type = SLIType(request.sli_type)
    except ValueError:
        raise HTTPException(400, f"Invalid SLI type: {request.sli_type}")

    try:
        window = SLOWindow(request.window)
    except ValueError:
        raise HTTPException(400, f"Invalid window: {request.window}")

    slo = tracker.create_slo(
        name=request.name,
        description=request.description,
        service=request.service,
        sli_type=sli_type,
        target=request.target,
        window=window,
        team_id=request.team_id,
        owner=request.owner,
        labels=request.labels,
        alert_burn_rate_threshold=request.alert_burn_rate_threshold,
    )

    return _slo_to_response(slo)


@router.get("/{slo_id}", response_model=SLOResponse)
async def get_slo(
    slo_id: str,
    org_id: str = Query("default", description="Organization ID"),
) -> SLOResponse:
    """Get an SLO by ID."""
    tracker = _get_tracker(org_id)
    slo = tracker.get_slo(slo_id)

    if not slo:
        raise HTTPException(404, f"SLO not found: {slo_id}")

    return _slo_to_response(slo)


@router.patch("/{slo_id}", response_model=SLOResponse)
async def update_slo(
    slo_id: str,
    request: SLOUpdateRequest,
    org_id: str = Query("default", description="Organization ID"),
) -> SLOResponse:
    """Update an existing SLO."""
    tracker = _get_tracker(org_id)

    updates = request.model_dump(exclude_none=True)
    slo = tracker.update_slo(slo_id, **updates)

    if not slo:
        raise HTTPException(404, f"SLO not found: {slo_id}")

    return _slo_to_response(slo)


@router.delete("/{slo_id}", status_code=204)
async def delete_slo(
    slo_id: str,
    org_id: str = Query("default", description="Organization ID"),
) -> None:
    """Delete an SLO."""
    tracker = _get_tracker(org_id)

    if not tracker.delete_slo(slo_id):
        raise HTTPException(404, f"SLO not found: {slo_id}")


@router.post("/{slo_id}/sli", response_model=SLIResponse, status_code=201)
async def record_sli(
    slo_id: str,
    request: SLIRecordRequest,
    org_id: str = Query("default", description="Organization ID"),
) -> SLIResponse:
    """Record an SLI measurement."""
    tracker = _get_tracker(org_id)

    sli = tracker.record_sli(
        slo_id=slo_id,
        good_count=request.good_count,
        total_count=request.total_count,
        timestamp=request.timestamp,
        metadata=request.metadata,
    )

    if not sli:
        raise HTTPException(404, f"SLO not found: {slo_id}")

    return SLIResponse(
        id=sli.id,
        slo_id=sli.slo_id,
        timestamp=sli.timestamp,
        value=sli.value,
        good_count=sli.good_count,
        total_count=sli.total_count,
    )


@router.get("/{slo_id}/sli", response_model=list[SLIResponse])
async def get_sli_data(
    slo_id: str,
    start_time: datetime | None = Query(None, description="Start of time range"),
    end_time: datetime | None = Query(None, description="End of time range"),
    org_id: str = Query("default", description="Organization ID"),
) -> list[SLIResponse]:
    """Get SLI measurements for an SLO."""
    tracker = _get_tracker(org_id)

    data = tracker.get_sli_data(slo_id, start_time=start_time, end_time=end_time)

    return [
        SLIResponse(
            id=sli.id,
            slo_id=sli.slo_id,
            timestamp=sli.timestamp,
            value=sli.value,
            good_count=sli.good_count,
            total_count=sli.total_count,
        )
        for sli in data
    ]


@router.get("/{slo_id}/budget", response_model=ErrorBudgetResponse)
async def get_error_budget(
    slo_id: str,
    org_id: str = Query("default", description="Organization ID"),
) -> ErrorBudgetResponse:
    """Get error budget status for an SLO."""
    tracker = _get_tracker(org_id)

    budget = tracker.calculate_error_budget(slo_id)

    if not budget:
        raise HTTPException(404, f"SLO not found: {slo_id}")

    return ErrorBudgetResponse(
        slo_id=budget.slo_id,
        total_budget=budget.total_budget,
        consumed=budget.consumed,
        remaining=budget.remaining,
        remaining_percent=budget.remaining_percent,
        burn_rate=budget.burn_rate,
        projected_exhaustion=budget.projected_exhaustion,
        status=budget.status.value,
        window_start=budget.window_start,
        window_end=budget.window_end,
    )


@router.get("/{slo_id}/burn-rate", response_model=BurnRateResponse)
async def get_burn_rate(
    slo_id: str,
    org_id: str = Query("default", description="Organization ID"),
) -> BurnRateResponse:
    """Get multi-window burn rate for an SLO."""
    tracker = _get_tracker(org_id)

    burn_rates = tracker.calculate_burn_rate(slo_id)

    if not burn_rates:
        raise HTTPException(404, f"SLO not found: {slo_id}")

    return BurnRateResponse(
        slo_id=burn_rates.slo_id,
        burn_rate_1h=burn_rates.burn_rate_1h,
        burn_rate_6h=burn_rates.burn_rate_6h,
        burn_rate_24h=burn_rates.burn_rate_24h,
        burn_rate_7d=burn_rates.burn_rate_7d,
        is_alerting=burn_rates.is_alerting,
        alert_severity=burn_rates.alert_severity,
    )
