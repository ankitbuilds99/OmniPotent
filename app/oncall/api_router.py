"""On-Call Management API Router.

Complete REST API for enterprise on-call management:
- Teams and members
- Schedules and rotations
- Shifts and overrides
- Swap requests
- Escalation policies
- Alerts
- AI features
"""

from __future__ import annotations

import logging
from datetime import datetime, date, timedelta
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from pydantic import BaseModel, Field

from app.database.engine import get_session as get_async_session
from app.oncall.schedule_service import ScheduleService, get_schedule_service
from app.oncall.escalation_service import EscalationService, get_escalation_service
from app.oncall.ai_oncall_service import AIOncallService, get_ai_oncall_service
from app.oncall.models import (
    OncallTeam,
    OncallTeamMember,
    OncallSchedule,
    OncallShift,
    OncallOverride,
    OncallSwapRequest,
    OncallAlert,
    OncallEscalationPolicy,
)

log = logging.getLogger("devcopilot.oncall.api")

router = APIRouter(prefix="/oncall", tags=["oncall"])


# ============================================================================
# REQUEST/RESPONSE MODELS
# ============================================================================

class CreateTeamRequest(BaseModel):
    name: str
    slug: str
    description: str | None = None
    timezone: str = "UTC"


class AddTeamMemberRequest(BaseModel):
    user_id: UUID
    role: str = "member"
    skills: list[str] = []
    timezone: str | None = None


class CreateScheduleRequest(BaseModel):
    team_id: UUID
    name: str
    timezone: str = "UTC"
    rotation_type: str = "weekly"
    handoff_time: str = "09:00"
    handoff_day: str = "monday"
    length_days: int = 7
    coverage_type: str = "24x7"
    coverage_hours: dict[str, Any] | None = None
    layer: int = 1
    participants: list[UUID] | None = None


class UpdateRotationOrderRequest(BaseModel):
    participant_order: list[UUID]


class GenerateShiftsRequest(BaseModel):
    start_date: date
    end_date: date
    regenerate: bool = False


class CreateOverrideRequest(BaseModel):
    schedule_id: UUID
    override_user_id: UUID
    start_time: datetime
    end_time: datetime
    reason: str | None = None
    notes: str | None = None
    original_user_id: UUID | None = None


class CreateSwapRequest(BaseModel):
    from_shift_id: UUID
    to_user_id: UUID | None = None
    to_shift_id: UUID | None = None
    reason: str | None = None
    expires_in_hours: int = 48


class CreateEscalationPolicyRequest(BaseModel):
    team_id: UUID
    name: str
    description: str | None = None
    steps: list[dict[str, Any]]
    conditions: dict[str, Any] | None = None
    is_default: bool = False
    repeat_enabled: bool = True
    repeat_limit: int = 3


class CreateAlertRequest(BaseModel):
    team_id: UUID
    title: str
    description: str | None = None
    severity: str = "warning"
    source: str | None = None
    source_id: str | None = None
    source_url: str | None = None
    dedup_key: str | None = None
    tags: list[str] = []
    custom_fields: dict[str, Any] = {}


class TeamResponse(BaseModel):
    id: UUID
    name: str
    slug: str
    description: str | None
    timezone: str
    is_active: bool

    class Config:
        from_attributes = True


class ScheduleResponse(BaseModel):
    id: UUID
    team_id: UUID
    name: str
    timezone: str
    schedule_type: str
    rotation_config: dict[str, Any]
    coverage_type: str
    layer: int
    is_active: bool

    class Config:
        from_attributes = True


class ShiftResponse(BaseModel):
    id: UUID
    schedule_id: UUID
    user_id: UUID
    start_time: datetime
    end_time: datetime
    shift_type: str
    status: str

    class Config:
        from_attributes = True


class AlertResponse(BaseModel):
    id: UUID
    title: str
    description: str | None
    severity: str
    status: str
    triggered_at: datetime
    acknowledged_at: datetime | None
    resolved_at: datetime | None

    class Config:
        from_attributes = True


# ============================================================================
# TEAM ENDPOINTS
# ============================================================================

@router.post("/teams", response_model=TeamResponse)
async def create_team(
    request: CreateTeamRequest,
    db=Depends(get_async_session),
):
    """Create a new on-call team."""
    from sqlalchemy import select

    result = await db.execute(
        select(OncallTeam).where(OncallTeam.slug == request.slug)
    )
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Team slug already exists")

    team = OncallTeam(
        org_id=UUID("00000000-0000-0000-0000-000000000000"),
        name=request.name,
        slug=request.slug,
        description=request.description,
        timezone=request.timezone,
    )
    db.add(team)
    await db.commit()
    await db.refresh(team)

    log.info("Created team %s", team.id)
    return team


@router.get("/teams")
async def list_teams(
    db=Depends(get_async_session),
) -> list[TeamResponse]:
    """List all on-call teams."""
    from sqlalchemy import select

    result = await db.execute(
        select(OncallTeam).where(OncallTeam.is_active == True)
    )
    teams = result.scalars().all()
    return [TeamResponse.model_validate(t) for t in teams]


@router.get("/teams/{team_id}", response_model=TeamResponse)
async def get_team(
    team_id: UUID,
    db=Depends(get_async_session),
):
    """Get team details."""
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    result = await db.execute(
        select(OncallTeam)
        .options(
            selectinload(OncallTeam.members),
            selectinload(OncallTeam.schedules),
            selectinload(OncallTeam.escalation_policies),
        )
        .where(OncallTeam.id == team_id)
    )
    team = result.scalar_one_or_none()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    return team


@router.post("/teams/{team_id}/members")
async def add_team_member(
    team_id: UUID,
    request: AddTeamMemberRequest,
    db=Depends(get_async_session),
):
    """Add a member to the team."""
    member = OncallTeamMember(
        team_id=team_id,
        user_id=request.user_id,
        role=request.role,
        skills=request.skills,
        timezone=request.timezone,
    )
    db.add(member)
    await db.commit()
    await db.refresh(member)

    log.info("Added member %s to team %s", request.user_id, team_id)
    return {"id": member.id, "user_id": member.user_id, "role": member.role}


@router.get("/teams/{team_id}/current-oncall")
async def get_current_oncall(
    team_id: UUID,
    at_time: datetime | None = None,
    db=Depends(get_async_session),
):
    """Get current on-call for a team."""
    service = await get_schedule_service(db)
    return await service.get_current_oncall(team_id, at_time)


# ============================================================================
# SCHEDULE ENDPOINTS
# ============================================================================

@router.post("/schedules", response_model=ScheduleResponse)
async def create_schedule(
    request: CreateScheduleRequest,
    db=Depends(get_async_session),
):
    """Create a new on-call schedule."""
    service = await get_schedule_service(db)
    schedule = await service.create_schedule(
        team_id=request.team_id,
        name=request.name,
        timezone_str=request.timezone,
        rotation_type=request.rotation_type,
        handoff_time=request.handoff_time,
        handoff_day=request.handoff_day,
        length_days=request.length_days,
        coverage_type=request.coverage_type,
        coverage_hours=request.coverage_hours,
        layer=request.layer,
        participants=request.participants,
    )
    return schedule


@router.get("/schedules/{schedule_id}", response_model=ScheduleResponse)
async def get_schedule(
    schedule_id: UUID,
    db=Depends(get_async_session),
):
    """Get schedule details."""
    service = await get_schedule_service(db)
    schedule = await service.get_schedule(schedule_id)
    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return schedule


@router.post("/schedules/{schedule_id}/reorder")
async def reorder_rotation(
    schedule_id: UUID,
    request: UpdateRotationOrderRequest,
    db=Depends(get_async_session),
):
    """Reorder rotation participants (drag-and-drop)."""
    service = await get_schedule_service(db)
    await service.update_rotation_order(schedule_id, request.participant_order)
    return {"success": True}


@router.post("/schedules/{schedule_id}/generate-shifts")
async def generate_shifts(
    schedule_id: UUID,
    request: GenerateShiftsRequest,
    db=Depends(get_async_session),
):
    """Generate shifts for a schedule."""
    service = await get_schedule_service(db)
    shifts = await service.generate_shifts(
        schedule_id=schedule_id,
        start_date=request.start_date,
        end_date=request.end_date,
        regenerate=request.regenerate,
    )
    return {"shifts_created": len(shifts)}


@router.get("/schedules/{schedule_id}/gaps")
async def detect_schedule_gaps(
    schedule_id: UUID,
    start_date: date = Query(...),
    end_date: date = Query(...),
    db=Depends(get_async_session),
):
    """Detect coverage gaps in schedule."""
    service = await get_schedule_service(db)
    return await service.detect_gaps(schedule_id, start_date, end_date)


@router.get("/schedules/{schedule_id}/shifts")
async def list_schedule_shifts(
    schedule_id: UUID,
    start_date: date = Query(...),
    end_date: date = Query(...),
    db=Depends(get_async_session),
) -> list[ShiftResponse]:
    """List shifts for a schedule."""
    from sqlalchemy import select

    result = await db.execute(
        select(OncallShift)
        .where(
            OncallShift.schedule_id == schedule_id,
            OncallShift.start_time >= datetime.combine(start_date, datetime.min.time()),
            OncallShift.end_time <= datetime.combine(end_date, datetime.max.time()),
        )
        .order_by(OncallShift.start_time)
    )
    shifts = result.scalars().all()
    return [ShiftResponse.model_validate(s) for s in shifts]


# ============================================================================
# OVERRIDE ENDPOINTS
# ============================================================================

@router.post("/overrides")
async def create_override(
    request: CreateOverrideRequest,
    db=Depends(get_async_session),
):
    """Create a schedule override."""
    override = OncallOverride(
        schedule_id=request.schedule_id,
        original_user_id=request.original_user_id,
        override_user_id=request.override_user_id,
        start_time=request.start_time,
        end_time=request.end_time,
        reason=request.reason,
        notes=request.notes,
        status="active",
        created_by=request.override_user_id,
    )
    db.add(override)
    await db.commit()
    await db.refresh(override)

    log.info("Created override %s", override.id)
    return {"id": override.id, "status": override.status}


@router.delete("/overrides/{override_id}")
async def cancel_override(
    override_id: UUID,
    db=Depends(get_async_session),
):
    """Cancel an override."""
    from sqlalchemy import select

    result = await db.execute(
        select(OncallOverride).where(OncallOverride.id == override_id)
    )
    override = result.scalar_one_or_none()

    if not override:
        raise HTTPException(status_code=404, detail="Override not found")

    override.status = "cancelled"
    await db.commit()

    log.info("Cancelled override %s", override_id)
    return {"success": True}


# ============================================================================
# SWAP ENDPOINTS
# ============================================================================

@router.post("/swaps")
async def create_swap_request(
    request: CreateSwapRequest,
    db=Depends(get_async_session),
):
    """Create a shift swap request."""
    from sqlalchemy import select

    result = await db.execute(
        select(OncallShift).where(OncallShift.id == request.from_shift_id)
    )
    shift = result.scalar_one_or_none()

    if not shift:
        raise HTTPException(status_code=404, detail="Shift not found")

    swap = OncallSwapRequest(
        from_shift_id=request.from_shift_id,
        from_user_id=shift.user_id,
        to_user_id=request.to_user_id,
        to_shift_id=request.to_shift_id,
        reason=request.reason,
        expires_at=datetime.utcnow() + timedelta(hours=request.expires_in_hours),
        status="pending",
    )
    db.add(swap)
    await db.commit()
    await db.refresh(swap)

    log.info("Created swap request %s", swap.id)
    return {"id": swap.id, "status": swap.status}


@router.post("/swaps/{swap_id}/accept")
async def accept_swap(
    swap_id: UUID,
    accepting_user_id: UUID,
    db=Depends(get_async_session),
):
    """Accept a swap request."""
    from sqlalchemy import select

    result = await db.execute(
        select(OncallSwapRequest).where(OncallSwapRequest.id == swap_id)
    )
    swap = result.scalar_one_or_none()

    if not swap:
        raise HTTPException(status_code=404, detail="Swap request not found")

    if swap.status != "pending":
        raise HTTPException(status_code=400, detail=f"Swap request is already {swap.status}")

    swap.status = "accepted"
    swap.responded_by = accepting_user_id
    swap.responded_at = datetime.utcnow()

    await db.commit()

    log.info("Swap request %s accepted by %s", swap_id, accepting_user_id)
    return {"success": True, "status": swap.status}


@router.post("/swaps/{swap_id}/decline")
async def decline_swap(
    swap_id: UUID,
    declining_user_id: UUID,
    reason: str | None = None,
    db=Depends(get_async_session),
):
    """Decline a swap request."""
    from sqlalchemy import select

    result = await db.execute(
        select(OncallSwapRequest).where(OncallSwapRequest.id == swap_id)
    )
    swap = result.scalar_one_or_none()

    if not swap:
        raise HTTPException(status_code=404, detail="Swap request not found")

    if swap.status != "pending":
        raise HTTPException(status_code=400, detail=f"Swap request is already {swap.status}")

    swap.status = "declined"
    swap.responded_by = declining_user_id
    swap.responded_at = datetime.utcnow()
    swap.notes = reason

    await db.commit()

    log.info("Swap request %s declined by %s", swap_id, declining_user_id)
    return {"success": True, "status": swap.status}


@router.get("/schedules/{schedule_id}/open-swaps")
async def get_open_swaps(
    schedule_id: UUID,
    db=Depends(get_async_session),
):
    """Get open swap requests for a schedule."""
    from sqlalchemy import select

    result = await db.execute(
        select(OncallSwapRequest, OncallShift)
        .join(OncallShift, OncallSwapRequest.from_shift_id == OncallShift.id)
        .where(
            OncallShift.schedule_id == schedule_id,
            OncallSwapRequest.status == "pending",
            OncallSwapRequest.to_user_id.is_(None),
            OncallSwapRequest.expires_at > datetime.utcnow(),
        )
        .order_by(OncallShift.start_time)
    )

    swaps = []
    for swap, shift in result.all():
        swaps.append({
            "swap_request_id": swap.id,
            "shift": {
                "id": shift.id,
                "start_time": shift.start_time,
                "end_time": shift.end_time,
            },
            "from_user_id": swap.from_user_id,
            "reason": swap.reason,
            "expires_at": swap.expires_at,
        })

    return swaps


# ============================================================================
# ESCALATION POLICY ENDPOINTS
# ============================================================================

@router.post("/escalation-policies")
async def create_escalation_policy(
    request: CreateEscalationPolicyRequest,
    db=Depends(get_async_session),
):
    """Create an escalation policy."""
    service = await get_escalation_service(db)
    policy = await service.create_escalation_policy(
        team_id=request.team_id,
        name=request.name,
        description=request.description,
        steps=request.steps,
        conditions=request.conditions,
        is_default=request.is_default,
        repeat_enabled=request.repeat_enabled,
        repeat_limit=request.repeat_limit,
    )
    return {"id": policy.id, "name": policy.name}


@router.get("/teams/{team_id}/escalation-policies")
async def get_team_policies(
    team_id: UUID,
    db=Depends(get_async_session),
):
    """Get escalation policies for a team."""
    service = await get_escalation_service(db)
    policies = await service.get_team_policies(team_id)
    return [
        {
            "id": p.id,
            "name": p.name,
            "is_default": p.is_default,
            "steps_count": len(p.steps),
        }
        for p in policies
    ]


@router.get("/escalation-policies/{policy_id}")
async def get_policy(
    policy_id: UUID,
    db=Depends(get_async_session),
):
    """Get escalation policy details."""
    service = await get_escalation_service(db)
    policy = await service.get_policy(policy_id)
    if not policy:
        raise HTTPException(status_code=404, detail="Policy not found")
    return {
        "id": policy.id,
        "name": policy.name,
        "description": policy.description,
        "is_default": policy.is_default,
        "repeat_enabled": policy.repeat_enabled,
        "repeat_limit": policy.repeat_limit,
        "steps": [
            {
                "step_number": s.step_number,
                "delay_minutes": s.delay_minutes,
                "targets": s.targets,
                "notification_channels": s.notification_channels,
            }
            for s in policy.steps
        ],
    }


# ============================================================================
# ALERT ENDPOINTS
# ============================================================================

@router.post("/alerts", response_model=AlertResponse)
async def create_alert(
    request: CreateAlertRequest,
    background_tasks: BackgroundTasks,
    db=Depends(get_async_session),
):
    """Create and trigger an alert."""
    alert = OncallAlert(
        org_id=UUID("00000000-0000-0000-0000-000000000000"),
        team_id=request.team_id,
        title=request.title,
        description=request.description,
        severity=request.severity,
        source=request.source,
        source_id=request.source_id,
        source_url=request.source_url,
        dedup_key=request.dedup_key,
        tags=request.tags,
        custom_fields=request.custom_fields,
        status="triggered",
    )
    db.add(alert)
    await db.commit()
    await db.refresh(alert)

    async def start_escalation():
        service = await get_escalation_service(db)
        await service.start_escalation(alert)

    background_tasks.add_task(start_escalation)

    log.info("Created alert %s", alert.id)
    return alert


@router.post("/alerts/{alert_id}/acknowledge")
async def acknowledge_alert(
    alert_id: UUID,
    user_id: UUID,
    db=Depends(get_async_session),
):
    """Acknowledge an alert."""
    service = await get_escalation_service(db)
    alert = await service.acknowledge_alert(alert_id, user_id)
    return {"id": alert.id, "status": alert.status}


@router.post("/alerts/{alert_id}/resolve")
async def resolve_alert(
    alert_id: UUID,
    user_id: UUID,
    notes: str | None = None,
    db=Depends(get_async_session),
):
    """Resolve an alert."""
    service = await get_escalation_service(db)
    alert = await service.resolve_alert(alert_id, user_id, notes)
    return {"id": alert.id, "status": alert.status}


@router.get("/alerts")
async def list_alerts(
    team_id: UUID | None = None,
    status: str | None = None,
    severity: str | None = None,
    since: datetime | None = None,
    limit: int = 50,
    db=Depends(get_async_session),
) -> list[AlertResponse]:
    """List alerts."""
    from sqlalchemy import select

    query = select(OncallAlert)

    if team_id:
        query = query.where(OncallAlert.team_id == team_id)
    if status:
        query = query.where(OncallAlert.status == status)
    if severity:
        query = query.where(OncallAlert.severity == severity)
    if since:
        query = query.where(OncallAlert.triggered_at >= since)

    query = query.order_by(OncallAlert.triggered_at.desc()).limit(limit)

    result = await db.execute(query)
    alerts = result.scalars().all()
    return [AlertResponse.model_validate(a) for a in alerts]


# ============================================================================
# AI ENDPOINTS
# ============================================================================

@router.post("/ai/route-alert")
async def ai_route_alert(
    alert_id: UUID,
    db=Depends(get_async_session),
):
    """Get AI recommendation for alert routing."""
    from sqlalchemy import select

    result = await db.execute(
        select(OncallAlert).where(OncallAlert.id == alert_id)
    )
    alert = result.scalar_one_or_none()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")

    if not alert.team_id:
        raise HTTPException(status_code=400, detail="Alert has no team")

    service = await get_ai_oncall_service(db)
    return await service.route_alert(alert, alert.team_id)


@router.get("/ai/find-coverage")
async def ai_find_coverage(
    schedule_id: UUID,
    start_time: datetime = Query(...),
    end_time: datetime = Query(...),
    exclude_user_id: UUID | None = None,
    reason: str | None = None,
    db=Depends(get_async_session),
):
    """AI-powered coverage finder."""
    service = await get_ai_oncall_service(db)
    return await service.find_optimal_coverage(
        schedule_id=schedule_id,
        start_time=start_time,
        end_time=end_time,
        exclude_user_id=exclude_user_id,
        reason=reason,
    )


@router.get("/ai/teams/{team_id}/alert-fatigue")
async def detect_alert_fatigue(
    team_id: UUID,
    window_hours: int = 24,
    db=Depends(get_async_session),
):
    """Detect alert fatigue patterns."""
    service = await get_ai_oncall_service(db)
    return await service.detect_alert_fatigue(team_id, window_hours)


@router.get("/ai/teams/{team_id}/workload")
async def analyze_workload(
    team_id: UUID,
    period_days: int = 30,
    db=Depends(get_async_session),
):
    """Analyze workload balance."""
    service = await get_ai_oncall_service(db)
    return await service.analyze_workload_balance(team_id, period_days)
