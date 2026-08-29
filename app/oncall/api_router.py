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
from datetime import datetime, date, timedelta, timezone as dt_timezone
from typing import Any
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from app.database.engine import session_dependency as get_async_session
from app.database.models import Member
from app.database.orgs import ensure_org_row, org_uuid
from app.notifications.delivery import notify
from app.oncall.schedule_service import get_schedule_service
from app.oncall.escalation_service import get_escalation_service
from app.oncall.ai_oncall_service import get_ai_oncall_service
from app.oncall.models import (
    DEFAULT_ESCALATION_CHANNELS,
    OncallTeam,
    OncallTeamMember,
    OncallShift,
    OncallOverride,
    OncallSwapRequest,
    OncallAlert,
)

log = logging.getLogger("omnipotent.oncall.api")

router = APIRouter(prefix="/oncall", tags=["oncall"])

# Every on-call row used to be created with the nil UUID and no query filtered
# on it, so one tenant's teams, schedules, escalation policies and alerts were
# another tenant's too — including paging their responders. The org_id column
# existed the whole time; nothing set it. `_org_uuid()` is what sets it now.
_org_uuid = org_uuid
_ensure_org_row = ensure_org_row


def _as_utc(value: datetime) -> datetime:
    """A stored timestamp as tz-aware UTC.

    SQLite has no timezone type and returns these columns naive, so comparing
    one against `datetime.now(utc)` raises rather than answering.
    """
    return value if value.tzinfo else value.replace(tzinfo=dt_timezone.utc)


async def _owned_team(db: Any, team_id: UUID) -> OncallTeam:
    """Load a team, 404ing if it belongs to another org.

    404 rather than 403: whether a team id exists at all is itself the other
    tenant's information.
    """
    from sqlalchemy import select

    result = await db.execute(
        select(OncallTeam).where(
            OncallTeam.id == team_id, OncallTeam.org_id == _org_uuid()
        )
    )
    team = result.scalar_one_or_none()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    return team


async def _owned_schedule(db: Any, schedule_id: UUID):
    """Load a schedule via its team, 404ing if it belongs to another org.

    Schedules, shifts and overrides carry no org_id of their own — they hang off
    a team — so ownership is checked one join up.
    """
    from sqlalchemy import select

    from app.oncall.models import OncallSchedule

    result = await db.execute(
        select(OncallSchedule)
        .join(OncallTeam, OncallTeam.id == OncallSchedule.team_id)
        .where(OncallSchedule.id == schedule_id, OncallTeam.org_id == _org_uuid())
    )
    schedule = result.scalar_one_or_none()
    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return schedule


def _overlaps(start_date: date, end_date: date) -> tuple[Any, Any]:
    """Filter clauses for shifts that overlap [start_date, end_date] at all.

    Every shift query here used to ask for *containment* — `start_time >= from`
    AND `end_time <= to` — which silently drops the shift you are most likely to
    care about: the one that is in progress at the edge of the window. A weekly
    rotation running Mon→Mon can never be contained in a Sun–Sat week, so the
    calendar rendered an empty row for a week that was fully covered, the gap
    analyser reported a gap where somebody was on call, and the iCal feed
    exported nothing for the current shift. Callers papered over it by padding
    the window by a day, which works only for daily rotations.

    Overlap is what "shifts between these dates" means: the shift starts before
    the window ends and ends after the window begins.
    """
    return (
        OncallShift.start_time <= datetime.combine(end_date, datetime.max.time()),
        OncallShift.end_time >= datetime.combine(start_date, datetime.min.time()),
    )


async def _owned_shift(db: Any, shift_id: UUID) -> OncallShift:
    """Load a shift via schedule → team, 404ing if it belongs to another org."""
    from sqlalchemy import select

    from app.oncall.models import OncallSchedule

    result = await db.execute(
        select(OncallShift)
        .join(OncallSchedule, OncallSchedule.id == OncallShift.schedule_id)
        .join(OncallTeam, OncallTeam.id == OncallSchedule.team_id)
        .where(OncallShift.id == shift_id, OncallTeam.org_id == _org_uuid())
    )
    shift = result.scalar_one_or_none()
    if not shift:
        raise HTTPException(status_code=404, detail="Shift not found")
    return shift


async def _owned_override(db: Any, override_id: UUID) -> OncallOverride:
    """Load an override via schedule → team, 404ing if it belongs to another org."""
    from sqlalchemy import select

    from app.oncall.models import OncallSchedule

    result = await db.execute(
        select(OncallOverride)
        .join(OncallSchedule, OncallSchedule.id == OncallOverride.schedule_id)
        .join(OncallTeam, OncallTeam.id == OncallSchedule.team_id)
        .where(OncallOverride.id == override_id, OncallTeam.org_id == _org_uuid())
    )
    override = result.scalar_one_or_none()
    if not override:
        raise HTTPException(status_code=404, detail="Override not found")
    return override


async def _owned_swap(db: Any, swap_id: UUID) -> OncallSwapRequest:
    """Load a swap request via its shift, 404ing if it belongs to another org.

    Accepting or declining a swap changes who is paged for an outage, so this is
    one of the endpoints where cross-tenant write access matters most.
    """
    from sqlalchemy import select

    from app.oncall.models import OncallSchedule

    result = await db.execute(
        select(OncallSwapRequest)
        .join(OncallShift, OncallShift.id == OncallSwapRequest.from_shift_id)
        .join(OncallSchedule, OncallSchedule.id == OncallShift.schedule_id)
        .join(OncallTeam, OncallTeam.id == OncallSchedule.team_id)
        .where(OncallSwapRequest.id == swap_id, OncallTeam.org_id == _org_uuid())
    )
    swap = result.scalar_one_or_none()
    if not swap:
        raise HTTPException(status_code=404, detail="Swap request not found")
    return swap


async def _owned_policy(db: Any, policy_id: UUID):
    """Load an escalation policy via its team, 404ing if another org owns it."""
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from app.oncall.models import OncallEscalationPolicy

    result = await db.execute(
        select(OncallEscalationPolicy)
        .join(OncallTeam, OncallTeam.id == OncallEscalationPolicy.team_id)
        .where(
            OncallEscalationPolicy.id == policy_id,
            OncallTeam.org_id == _org_uuid(),
        )
        .options(selectinload(OncallEscalationPolicy.steps))
    )
    policy = result.scalar_one_or_none()
    if not policy:
        raise HTTPException(status_code=404, detail="Policy not found")
    return policy


async def _owned_alert(db: Any, alert_id: UUID) -> OncallAlert:
    """Load an alert, 404ing if it belongs to another org.

    Alerts carry their own org_id, so this is a direct filter — but nothing set
    that column before, so every alert in the table read as the nil org.
    """
    from sqlalchemy import select

    result = await db.execute(
        select(OncallAlert).where(
            OncallAlert.id == alert_id, OncallAlert.org_id == _org_uuid()
        )
    )
    alert = result.scalar_one_or_none()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    return alert


# ============================================================================
# REQUEST/RESPONSE MODELS
# ============================================================================

class CreateTeamRequest(BaseModel):
    name: str
    slug: str
    description: str | None = None
    timezone: str = "UTC"


class AddTeamMemberRequest(BaseModel):
    """Who to put on this team, and where a page to them should go.

    `user_id` alone used to be the only way to say who, and nothing in this
    product — API or frontend — ever produced one: `members` is created by
    migration and was written by no application code, while `/admin/members` and
    SCIM populate the separate tenancy store whose ids are strings, not UUIDs. So
    the only caller who could use this endpoint was one holding an id it had
    inserted by hand, and every page aimed at a responder resolved to no address.

    `email` is the way in now: it identifies the person the way the rest of the
    product does, and adding them creates the `members` row a page is addressed
    from. `phone` and `slack_user_id` are the addresses no store in this build
    previously had room for; they go into `contact_methods`, which is what
    `EscalationService._page_routing` reads when a policy pages on sms, voice,
    whatsapp or Slack.

    `user_id` still works — an existing responder is referred to by the id this
    endpoint handed back — but one of the two has to be given, because a
    membership with neither names nobody this deployment can page.

    Everything except who is optional and every omitted field is left as it was
    on an existing membership, rather than reset to its default. Posting the same
    person again is how a role is corrected, and a request that only says
    "Ada is now the lead" must not take away the phone number her sms pages are
    addressed to — a silently emptied address is exactly the failure this
    endpoint was added to end. `role` and `skills` therefore default to None
    ("unchanged") rather than to "member" and []; a new membership with neither
    given still gets those.
    """

    user_id: UUID | None = None
    email: str | None = None
    phone: str | None = None
    slack_user_id: str | None = None
    #: Full control over the order channels are tried in and what each one is
    #: addressed to, for callers that want more than the three fields above.
    #: Entries are {"type": ..., "delay_minutes": ..., "value": ...}; when given,
    #: this replaces what `phone`/`slack_user_id` would have built.
    contact_methods: list[dict[str, Any]] | None = None
    role: str | None = None
    skills: list[str] | None = None
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
    # Why this shift is assigned to whoever holds it: "rotation" as generated,
    # or "swap"/"override" where a human moved it. Without this a swapped shift
    # reads as a rotation-generation bug — the assignee is not the person the
    # rotation order says should be on.
    source: str = "rotation"

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

    org = await _ensure_org_row(db)
    # Slugs are unique per org (uq_oncall_team_slug), so the collision check has
    # to be too — otherwise one tenant taking "platform" blocks it for everyone.
    result = await db.execute(
        select(OncallTeam).where(
            OncallTeam.slug == request.slug, OncallTeam.org_id == org
        )
    )
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Team slug already exists")

    team = OncallTeam(
        org_id=org,
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
    """List this org's on-call teams."""
    from sqlalchemy import select

    result = await db.execute(
        select(OncallTeam).where(
            OncallTeam.is_active.is_(True), OncallTeam.org_id == _org_uuid()
        )
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
        .where(OncallTeam.id == team_id, OncallTeam.org_id == _org_uuid())
    )
    team = result.scalar_one_or_none()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    return team


async def _responder(db: Any, request: AddTeamMemberRequest) -> Member:
    """The `members` row this membership is for, creating it from an email.

    This is the writer `members` never had. The table is where every on-call
    `user_id` points and where `EscalationService._page_routing` reads the
    address a page is sent to, but no application code inserted into it, so a
    deployment provisioned through this API had a row for nobody and every page
    resolved to no address. Adding a responder to an on-call team is the moment
    the product learns a person exists, so it is where the row is written.

    An existing responder is matched case-insensitively but stored as typed:
    `ix_members_org_email` is unique on the exact string, so matching exactly
    would make Ada@corp and ada@corp two responders with two ids, and paging
    whichever one the caller last quoted. Storing as typed keeps the address
    `_acting_member` compares an actor against intact.
    """
    from sqlalchemy import func, select

    org = await _ensure_org_row(db)
    email = (request.email or "").strip()

    if request.user_id is not None:
        member = (
            await db.execute(
                select(Member).where(
                    Member.id == request.user_id, Member.org_id == org
                )
            )
        ).scalar_one_or_none()
        if member is None:
            # 404 on the id rather than silently creating a membership for it:
            # a member row that does not exist is a responder no page can be
            # addressed to, and the escalation would only discover that at 3am.
            raise HTTPException(
                status_code=404,
                detail=(
                    f"No member {request.user_id} in this organization. Add the "
                    f"responder by email to create one."
                ),
            )
        if email and member.email.lower() != email.lower():
            # A corrected address is the whole point of being able to re-post a
            # responder; refusing it would leave the page going to the old one.
            member.email = email
        return member

    if not email or "@" not in email:
        raise HTTPException(
            status_code=400,
            detail=(
                "Provide either an existing `user_id` or an `email` to page this "
                "responder at; a membership with neither can never be paged."
            ),
        )

    member = (
        await db.execute(
            select(Member).where(
                Member.org_id == org, func.lower(Member.email) == email.lower()
            )
        )
    ).scalar_one_or_none()
    if member is None:
        member = Member(org_id=org, email=email, role="responder")
        db.add(member)
        await db.flush()
        log.info("Created member %s for responder %s", member.id, email)
    return member


def _contact_methods(request: AddTeamMemberRequest) -> list[dict[str, Any]] | None:
    """The contact methods to store, in the order a page should try them.

    Only methods with something behind them are listed. The column used to
    default to push, then sms, then phone, then email with no address against
    any of them, which described a responder nobody could reach on the first
    three: `_page_routing` addresses a page from the values here, so an entry
    with no value is an ordering statement and nothing more.

    None when the request carries no address at all, which means "leave what is
    stored alone". Rebuilding the list from an empty request would erase the
    number an sms page is addressed to every time somebody re-posted a responder
    to change their role, and the erasure would only be discovered by the page
    that failed.
    """
    if request.contact_methods is not None:
        return request.contact_methods
    if not request.phone and not request.slack_user_id:
        return None

    methods: list[dict[str, Any]] = []
    if request.phone:
        methods.append({"type": "sms", "delay_minutes": 0, "value": request.phone.strip()})
        methods.append({"type": "phone", "delay_minutes": 2, "value": request.phone.strip()})
    if request.slack_user_id:
        methods.append(
            {"type": "slack", "delay_minutes": 0, "value": request.slack_user_id.strip()}
        )
    # `members.email` carries the address, so this entry is the ordering only.
    methods.append({"type": "email", "delay_minutes": 0 if not methods else 5})
    return methods


@router.post("/teams/{team_id}/members")
async def add_team_member(
    team_id: UUID,
    request: AddTeamMemberRequest,
    db=Depends(get_async_session),
):
    """Add a responder to the team, and record where a page reaches them.

    Idempotent on (team, responder): posting the same person again updates what
    the request names rather than violating `uq_oncall_member` and answering a
    500. Updating how somebody is paged is the same request as adding them, and
    the alternative was a caller who could never correct a wrong number. Fields
    the request omits keep the value they had, so correcting one of them cannot
    quietly empty the address the next page is aimed at.
    """
    from sqlalchemy import select

    await _owned_team(db, team_id)

    responder = await _responder(db, request)

    member = (
        await db.execute(
            select(OncallTeamMember).where(
                OncallTeamMember.team_id == team_id,
                OncallTeamMember.user_id == responder.id,
            )
        )
    ).scalar_one_or_none()

    contact_methods = _contact_methods(request)
    if member is None:
        member = OncallTeamMember(
            team_id=team_id,
            user_id=responder.id,
            role=request.role or "member",
            skills=request.skills or [],
            timezone=request.timezone,
        )
        # Left unset when the request named no address, so the column's own
        # default applies rather than a second copy of it written out here.
        if contact_methods is not None:
            member.contact_methods = contact_methods
        db.add(member)
    else:
        if request.role is not None:
            member.role = request.role
        if request.skills is not None:
            member.skills = request.skills
        if request.timezone is not None:
            member.timezone = request.timezone
        if contact_methods is not None:
            member.contact_methods = contact_methods
        member.is_active = True

    await db.commit()
    await db.refresh(member)

    log.info("Added member %s (%s) to team %s", responder.id, responder.email, team_id)
    return {
        "id": member.id,
        "user_id": member.user_id,
        "email": responder.email,
        "role": member.role,
        "contact_methods": member.contact_methods,
    }


@router.get("/teams/{team_id}/members")
async def list_team_members(
    team_id: UUID,
    db=Depends(get_async_session),
):
    """The team's responders, with the ids and addresses pages are aimed at.

    Without this there was no request that produced a `user_id`, so nothing could
    name an escalation target: `POST /escalation-policies` takes user ids and the
    only place they existed was inside rows nobody could read back.
    """
    from sqlalchemy import select

    await _owned_team(db, team_id)

    rows = (
        await db.execute(
            select(OncallTeamMember, Member.email)
            .outerjoin(Member, Member.id == OncallTeamMember.user_id)
            .where(OncallTeamMember.team_id == team_id)
            .order_by(OncallTeamMember.joined_at)
        )
    ).all()

    return [
        {
            "id": member.id,
            "user_id": member.user_id,
            # None, not "": no `members` row for this id is a different thing
            # from a member row with an empty address, and only the first means
            # the membership was written before this endpoint could create one.
            "email": email,
            "role": member.role,
            "skills": member.skills,
            "timezone": member.timezone,
            "contact_methods": member.contact_methods,
            "is_active": member.is_active,
        }
        for member, email in rows
    ]


@router.get("/teams/{team_id}/current-oncall")
async def get_current_oncall(
    team_id: UUID,
    at_time: datetime | None = None,
    db=Depends(get_async_session),
):
    """Get current on-call for a team."""
    await _owned_team(db, team_id)

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
    await _owned_team(db, request.team_id)

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
    await _owned_schedule(db, schedule_id)

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
    await _owned_schedule(db, schedule_id)

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
    await _owned_schedule(db, schedule_id)

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
    await _owned_schedule(db, schedule_id)

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

    await _owned_schedule(db, schedule_id)

    result = await db.execute(
        select(OncallShift)
        .where(
            OncallShift.schedule_id == schedule_id,
            *_overlaps(start_date, end_date),
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
    background_tasks: BackgroundTasks,
    db=Depends(get_async_session),
):
    """Create a schedule override."""
    await _owned_schedule(db, request.schedule_id)

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
    # An override is the shortest path there is to "somebody else is on call
    # now", which is the one thing the rest of the team has to hear about
    # without being told in person.
    background_tasks.add_task(
        notify,
        "oncall_changed",
        "On-call override created",
        f"{request.override_user_id} is covering for {request.original_user_id} "
        f"from {request.start_time} to {request.end_time}."
        + (f" Reason: {request.reason}" if request.reason else ""),
        data={
            "override_id": str(override.id),
            "schedule_id": str(request.schedule_id),
            "user_id": str(request.override_user_id),
        },
        dedupe_key=f"oncall-override:{override.id}",
    )
    return {"id": override.id, "status": override.status}


@router.delete("/overrides/{override_id}")
async def cancel_override(
    override_id: UUID,
    background_tasks: BackgroundTasks,
    db=Depends(get_async_session),
):
    """Cancel an override."""
    override = await _owned_override(db, override_id)

    override.status = "cancelled"
    await db.commit()

    log.info("Cancelled override %s", override_id)
    # Announced for the same reason the creation is: whoever rearranged their
    # evening around the override needs to know it is off again.
    background_tasks.add_task(
        notify,
        "oncall_changed",
        "On-call override cancelled",
        f"The override covering {override.original_user_id} has been cancelled; "
        f"the rotation's original assignment applies again.",
        data={"override_id": str(override_id), "schedule_id": str(override.schedule_id)},
        dedupe_key=f"oncall-override-cancelled:{override_id}",
    )
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
    shift = await _owned_shift(db, request.from_shift_id)

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
    background_tasks: BackgroundTasks,
    db=Depends(get_async_session),
):
    """Accept a swap request, and carry out the swap.

    Accepting used to mutate only the swap row — no write to `OncallShift.user_id`
    and no override created — so both engineers saw "accepted" while the
    rotation, the current-on-call lookup and every page still went to the
    original assignee. The swap is the point of the request; recording consent
    to it and not performing it is the worst of the two outcomes, because both
    parties now believe cover is arranged.
    """
    swap = await _owned_swap(db, swap_id)

    if swap.status != "pending":
        raise HTTPException(status_code=400, detail=f"Swap request is already {swap.status}")

    if swap.expires_at and _as_utc(swap.expires_at) < datetime.now(dt_timezone.utc):
        swap.status = "expired"
        await db.commit()
        raise HTTPException(status_code=400, detail="Swap request has expired")

    from_shift = await _owned_shift(db, swap.from_shift_id)

    # Whoever is taking the shift: the named target if the request had one,
    # otherwise whoever accepted an open request.
    taker = swap.to_user_id or accepting_user_id

    if swap.to_shift_id:
        # A two-way trade: each engineer takes the other's shift.
        to_shift = await _owned_shift(db, swap.to_shift_id)
        from_shift.user_id, to_shift.user_id = to_shift.user_id, from_shift.user_id
        to_shift.source = "swap"
    else:
        # A one-way handover of a single shift.
        from_shift.user_id = taker
    from_shift.source = "swap"

    swap.status = "accepted"
    swap.responded_by = accepting_user_id
    swap.responded_at = datetime.now(dt_timezone.utc)

    await db.commit()

    log.info(
        "Swap request %s accepted by %s; shift %s reassigned to %s",
        swap_id, accepting_user_id, from_shift.id, from_shift.user_id,
    )
    # An accepted swap reassigns a shift, so it changes who is on call just as
    # much as an override does.
    background_tasks.add_task(
        notify,
        "oncall_changed",
        "On-call shift swapped",
        f"Shift {from_shift.id} is now {from_shift.user_id} after "
        f"{accepting_user_id} accepted the swap request."
        + (" The two shifts were traded." if swap.to_shift_id else ""),
        data={
            "swap_id": str(swap_id),
            "shift_id": str(from_shift.id),
            "user_id": str(from_shift.user_id),
        },
        dedupe_key=f"oncall-swap:{swap_id}",
    )
    return {
        "success": True,
        "status": swap.status,
        "from_shift_id": str(from_shift.id),
        "from_shift_user_id": str(from_shift.user_id),
        "to_shift_id": str(swap.to_shift_id) if swap.to_shift_id else None,
    }


@router.post("/swaps/{swap_id}/decline")
async def decline_swap(
    swap_id: UUID,
    declining_user_id: UUID,
    reason: str | None = None,
    db=Depends(get_async_session),
):
    """Decline a swap request."""
    swap = await _owned_swap(db, swap_id)

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

    schedule = await _owned_schedule(db, schedule_id)

    result = await db.execute(
        select(OncallSwapRequest, OncallShift)
        .join(OncallShift, OncallSwapRequest.from_shift_id == OncallShift.id)
        .where(
            OncallShift.schedule_id == schedule.id,
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
    team = await _owned_team(db, request.team_id)
    service = await get_escalation_service(db)
    policy = await service.create_escalation_policy(
        team_id=team.id,
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
    team = await _owned_team(db, team_id)
    service = await get_escalation_service(db)
    policies = await service.get_team_policies(team.id)
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
    policy = await _owned_policy(db, policy_id)
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
    team = await _owned_team(db, request.team_id)
    alert = OncallAlert(
        org_id=await _ensure_org_row(db),
        team_id=team.id,
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

    alert_id = alert.id

    async def start_escalation() -> None:
        # A fresh session, not `db`: since FastAPI 0.106 a yield-dependency's
        # cleanup runs before background tasks, so `db` is already closed here
        # and `alert` is detached. start_escalation() commits, so it needs a
        # live session and an attached row.
        from app.database.engine import get_session

        async with get_session() as session:
            escalating = await session.get(OncallAlert, alert_id)
            if escalating is None:
                return
            service = await get_escalation_service(session)
            await service.start_escalation(escalating)

    background_tasks.add_task(start_escalation)

    # Separate from the escalation policy above, and deliberately so: the policy
    # decides who gets *paged*, an individual chosen by rotation; the org's
    # notification preferences decide where the team as a whole hears about it.
    # An org that wants only the page can switch every channel off for this row.
    background_tasks.add_task(
        notify,
        "alert_triggered",
        f"[{alert.severity.upper()}] {alert.title}",
        (alert.description or "").strip()
        or f"A {alert.severity} alert was raised from {alert.source or 'an unnamed source'}.",
        critical=str(alert.severity).lower() in ("critical", "sev1", "p1"),
        data={
            "alert_id": str(alert_id),
            "team_id": str(team.id),
            "severity": alert.severity,
            "source": alert.source,
            "url": alert.source_url,
        },
        dedupe_key=f"oncall-alert:{alert_id}",
    )

    log.info("Created alert %s", alert.id)
    return alert


async def _acting_member(db: Any, user_id: UUID | None) -> UUID | None:
    """Which `members` row to attribute an acknowledge/resolve to.

    `user_id` was a required query parameter and nothing in the UI sent one, so
    every Acknowledge and Resolve click on the dashboard was a 422 the page
    swallowed — `if (res.ok)` with no else branch. The sibling incident routes
    were fixed this way already (`_acting_user` in app/incidents/router.py);
    these two were missed.

    Falls back to the principal the request authenticated as, looked up by email
    in this org. `acknowledged_by`/`resolved_by` are foreign keys into `members`,
    so a synthetic id would be a constraint violation on Postgres — where no row
    matches, the columns stay NULL and the acknowledgement still happens. Losing
    the attribution is much better than refusing the click: the point of
    acknowledging is to stop the escalation paging people.
    """
    if user_id is not None:
        return user_id

    from sqlalchemy import select

    from app.database.models import Member
    from app.tenant_context import get_actor

    actor = get_actor()
    if not actor or actor == "-":
        return None
    row = await db.execute(
        select(Member.id).where(Member.org_id == _org_uuid(), Member.email == actor)
    )
    return row.scalar_one_or_none()


@router.post("/alerts/{alert_id}/acknowledge")
async def acknowledge_alert(
    alert_id: UUID,
    user_id: UUID | None = None,
    db=Depends(get_async_session),
):
    """Acknowledge an alert."""
    await _owned_alert(db, alert_id)
    service = await get_escalation_service(db)
    try:
        alert = await service.acknowledge_alert(alert_id, await _acting_member(db, user_id))
    except ValueError as exc:
        # The service raises for "already acknowledged" and "already resolved".
        # Unhandled, that was a 500 on a button whose worst case is that
        # somebody else got there first.
        raise HTTPException(409, str(exc)) from None
    return {"id": alert.id, "status": alert.status}


@router.post("/alerts/{alert_id}/resolve")
async def resolve_alert(
    alert_id: UUID,
    user_id: UUID | None = None,
    notes: str | None = None,
    db=Depends(get_async_session),
):
    """Resolve an alert."""
    await _owned_alert(db, alert_id)
    service = await get_escalation_service(db)
    try:
        alert = await service.resolve_alert(alert_id, await _acting_member(db, user_id), notes)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
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

    query = select(OncallAlert).where(OncallAlert.org_id == _org_uuid())

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
    alert = await _owned_alert(db, alert_id)

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
    schedule = await _owned_schedule(db, schedule_id)
    service = await get_ai_oncall_service(db)
    return await service.find_optimal_coverage(
        schedule_id=schedule.id,
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
    team = await _owned_team(db, team_id)
    service = await get_ai_oncall_service(db)
    return await service.detect_alert_fatigue(team.id, window_hours)


@router.get("/ai/teams/{team_id}/workload")
async def analyze_workload(
    team_id: UUID,
    period_days: int = 30,
    db=Depends(get_async_session),
):
    """Analyze workload balance."""
    team = await _owned_team(db, team_id)
    service = await get_ai_oncall_service(db)
    return await service.analyze_workload_balance(team.id, period_days)


# ============================================================================
# ROTATION TEMPLATES ENDPOINTS
# ============================================================================

class CreateRotationTemplateRequest(BaseModel):
    """Request to create a rotation template."""
    name: str
    pattern_type: str  # weekly, biweekly, follow_the_sun, custom, business_hours, weekend
    timezone: str = "UTC"
    participants: list[UUID] = []
    handoff_time: str = "09:00"
    handoff_day: str = "monday"
    rotation_length_days: int = 7
    # For follow-the-sun
    regions: list[dict[str, Any]] | None = None
    # For layered rotations
    layers: list[dict[str, Any]] | None = None
    # For business hours / custom
    restrictions: list[dict[str, Any]] | None = None


class RotationTemplateResponse(BaseModel):
    """Response model for rotation templates."""
    template_id: str
    name: str
    description: str
    pattern_type: str
    timezone: str


@router.post("/rotation-templates", response_model=RotationTemplateResponse)
async def create_rotation_template(
    request: CreateRotationTemplateRequest,
):
    """
    Create a new rotation template.

    Supported pattern types:
    - weekly: Standard weekly rotation
    - biweekly: Two-week rotation cycles
    - follow_the_sun: Coverage follows working hours across timezones
    - custom: Custom rotation length with optional restrictions
    - business_hours: Only active during business hours
    - weekend: Only active on weekends
    - layered: Multiple overlapping rotation layers
    """
    from app.oncall.rotations import (
        create_weekly_rotation,
        create_biweekly_rotation,
        create_follow_the_sun_rotation,
        create_custom_rotation,
        create_business_hours_rotation,
        create_weekend_rotation,
        create_layered_rotation,
    )

    pattern_type = request.pattern_type.lower()

    if pattern_type == "weekly":
        template = create_weekly_rotation(
            name=request.name,
            participants=request.participants,
            timezone=request.timezone,
            handoff_day=request.handoff_day,
            handoff_time_str=request.handoff_time,
        )
    elif pattern_type == "biweekly":
        template = create_biweekly_rotation(
            name=request.name,
            participants=request.participants,
            timezone=request.timezone,
            handoff_day=request.handoff_day,
            handoff_time_str=request.handoff_time,
        )
    elif pattern_type == "follow_the_sun":
        if not request.regions:
            raise HTTPException(status_code=400, detail="Regions required for follow-the-sun rotation")
        template = create_follow_the_sun_rotation(
            name=request.name,
            regions=request.regions,
        )
    elif pattern_type == "business_hours":
        template = create_business_hours_rotation(
            name=request.name,
            participants=request.participants,
            timezone=request.timezone,
        )
    elif pattern_type == "weekend":
        template = create_weekend_rotation(
            name=request.name,
            participants=request.participants,
            timezone=request.timezone,
        )
    elif pattern_type == "layered":
        if not request.layers:
            raise HTTPException(status_code=400, detail="Layers required for layered rotation")
        template = create_layered_rotation(
            name=request.name,
            layers=request.layers,
            timezone=request.timezone,
        )
    elif pattern_type == "custom":
        template = create_custom_rotation(
            name=request.name,
            participants=request.participants,
            rotation_length_days=request.rotation_length_days,
            timezone=request.timezone,
            handoff_day=request.handoff_day,
            handoff_time_str=request.handoff_time,
            restrictions=request.restrictions,
        )
    else:
        raise HTTPException(status_code=400, detail=f"Unknown pattern type: {pattern_type}")

    log.info("Created rotation template: %s", template.template_id)

    return RotationTemplateResponse(
        template_id=template.template_id,
        name=template.name,
        description=template.description,
        pattern_type=template.pattern_type.value,
        timezone=template.timezone,
    )


@router.post("/rotation-templates/{template_id}/preview")
async def preview_rotation_shifts(
    template_id: str,
    start_date: date = Query(...),
    end_date: date = Query(...),
    participants: list[UUID] = Query(default=[]),
):
    """
    Preview shifts that would be generated from a rotation template.

    This is useful for testing rotation configurations before committing them.
    """
    from app.oncall.rotations import create_weekly_rotation

    # For preview, create a temporary template with the given participants
    # In production, this would look up the template by ID
    template = create_weekly_rotation(
        name="Preview",
        participants=participants,
    )

    shifts = list(template.generate_shifts(start_date, end_date))

    return {
        "template_id": template_id,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "shifts_count": len(shifts),
        "shifts": [
            {
                "user_id": str(s["user_id"]),
                "start_time": s["start_time"].isoformat(),
                "end_time": s["end_time"].isoformat(),
                "shift_type": s["shift_type"],
                "layer": s.get("layer"),
                "region": s.get("region"),
            }
            for s in shifts
        ],
    }


# ============================================================================
# OVERRIDE MANAGEMENT ENDPOINTS
# ============================================================================

class CreateOverrideWithReasonRequest(BaseModel):
    """Request to create an override with reason tracking."""
    schedule_id: UUID
    override_user_id: UUID
    start_time: datetime
    end_time: datetime
    reason: str  # pto, sick, personal, swap, training, coverage, emergency, other
    reason_details: str | None = None
    original_user_id: UUID | None = None
    requires_approval: bool = False


class OverrideWithReasonResponse(BaseModel):
    """Response model for override with reason."""
    id: UUID
    schedule_id: UUID
    override_user_id: UUID
    original_user_id: UUID | None
    start_time: datetime
    end_time: datetime
    reason: str
    reason_details: str | None
    status: str
    requires_approval: bool
    approved_by: UUID | None
    approved_at: datetime | None

    class Config:
        from_attributes = True


@router.post("/overrides/with-reason", response_model=OverrideWithReasonResponse)
async def create_override_with_reason(
    request: CreateOverrideWithReasonRequest,
    db=Depends(get_async_session),
):
    """
    Create a schedule override with reason tracking.

    Supported reasons:
    - pto: Paid time off
    - sick: Sick leave
    - personal: Personal reasons
    - swap: Shift swap
    - training: Training/shadowing
    - coverage: Covering for someone
    - emergency: Emergency situation
    - other: Other reasons (provide details)
    """
    from app.oncall.schedules import OverrideReason

    try:
        reason_enum = OverrideReason(request.reason.lower())
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid reason. Valid options: {[r.value for r in OverrideReason]}"
        )

    schedule = await _owned_schedule(db, request.schedule_id)

    override = OncallOverride(
        schedule_id=schedule.id,
        original_user_id=request.original_user_id,
        override_user_id=request.override_user_id,
        start_time=request.start_time,
        end_time=request.end_time,
        reason=reason_enum.value,
        notes=request.reason_details,
        requires_approval=request.requires_approval,
        status="pending" if request.requires_approval else "active",
        created_by=request.override_user_id,
    )
    db.add(override)
    await db.commit()
    await db.refresh(override)

    log.info("Created override %s with reason: %s", override.id, reason_enum.value)

    return OverrideWithReasonResponse(
        id=override.id,
        schedule_id=override.schedule_id,
        override_user_id=override.override_user_id,
        original_user_id=override.original_user_id,
        start_time=override.start_time,
        end_time=override.end_time,
        reason=override.reason or "",
        reason_details=override.notes,
        status=override.status,
        requires_approval=override.requires_approval,
        approved_by=override.approved_by,
        approved_at=override.approved_at,
    )


@router.post("/overrides/{override_id}/approve")
async def approve_override(
    override_id: UUID,
    approver_id: UUID,
    db=Depends(get_async_session),
):
    """Approve a pending override."""
    override = await _owned_override(db, override_id)

    if override.status != "pending":
        raise HTTPException(status_code=400, detail=f"Override is already {override.status}")

    override.status = "active"
    override.approved_by = approver_id
    override.approved_at = datetime.utcnow()

    await db.commit()
    log.info("Override %s approved by %s", override_id, approver_id)

    return {"id": override.id, "status": override.status}


@router.get("/schedules/{schedule_id}/overrides")
async def list_schedule_overrides(
    schedule_id: UUID,
    status: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    db=Depends(get_async_session),
):
    """List overrides for a schedule with optional filtering."""
    from sqlalchemy import select

    schedule = await _owned_schedule(db, schedule_id)

    query = select(OncallOverride).where(OncallOverride.schedule_id == schedule.id)

    if status:
        query = query.where(OncallOverride.status == status)
    if start_date:
        query = query.where(OncallOverride.end_time >= datetime.combine(start_date, datetime.min.time()))
    if end_date:
        query = query.where(OncallOverride.start_time <= datetime.combine(end_date, datetime.max.time()))

    query = query.order_by(OncallOverride.start_time)
    result = await db.execute(query)
    overrides = result.scalars().all()

    return [
        {
            "id": o.id,
            "original_user_id": o.original_user_id,
            "override_user_id": o.override_user_id,
            "start_time": o.start_time.isoformat(),
            "end_time": o.end_time.isoformat(),
            "reason": o.reason,
            "notes": o.notes,
            "status": o.status,
            "requires_approval": o.requires_approval,
            "approved_by": o.approved_by,
        }
        for o in overrides
    ]


# ============================================================================
# SCHEDULE GAPS DETECTION ENDPOINTS
# ============================================================================

@router.get("/schedules/{schedule_id}/gaps/analyze")
async def analyze_schedule_gaps(
    schedule_id: UUID,
    start_date: date = Query(...),
    end_date: date = Query(...),
    coverage_type: str = "24x7",
    db=Depends(get_async_session),
):
    """
    Analyze schedule for coverage gaps.

    Returns detailed gap information including:
    - Gap start/end times
    - Gap duration
    - Severity (critical, high, medium, low)
    - Summary statistics
    """
    from sqlalchemy import select
    from app.oncall.schedules import GapDetector, ScheduleShift

    schedule = await _owned_schedule(db, schedule_id)

    # Fetch shifts for the period
    result = await db.execute(
        select(OncallShift)
        .where(
            OncallShift.schedule_id == schedule.id,
            OncallShift.status.in_(["scheduled", "active"]),
            *_overlaps(start_date, end_date),
        )
        .order_by(OncallShift.start_time)
    )
    shifts_db = result.scalars().all()

    # Convert to ScheduleShift objects
    shifts = [
        ScheduleShift(
            shift_id=s.id,
            schedule_id=s.schedule_id,
            user_id=s.user_id,
            start_time=s.start_time,
            end_time=s.end_time,
            shift_type=s.shift_type,
            layer=1,
            source=s.source,
        )
        for s in shifts_db
    ]

    # Detect gaps
    start_time = datetime.combine(start_date, datetime.min.time()).replace(tzinfo=None)
    end_time = datetime.combine(end_date + timedelta(days=1), datetime.min.time()).replace(tzinfo=None)

    gaps = GapDetector.detect_gaps(
        shifts=shifts,
        start_time=start_time.replace(tzinfo=None),
        end_time=end_time.replace(tzinfo=None),
        schedule_id=schedule_id,
        coverage_type=coverage_type,
    )

    summary = GapDetector.get_gap_summary(gaps)

    return {
        "schedule_id": str(schedule_id),
        "analysis_period": {
            "start": start_date.isoformat(),
            "end": end_date.isoformat(),
        },
        "coverage_type": coverage_type,
        "summary": summary,
        "gaps": [g.to_dict() for g in gaps],
    }


@router.get("/schedules/{schedule_id}/gaps/alert")
async def check_upcoming_gaps(
    schedule_id: UUID,
    lookahead_days: int = 7,
    db=Depends(get_async_session),
):
    """
    Check for coverage gaps in the upcoming period.

    Used for proactive gap alerting.
    """
    start_date = date.today()
    end_date = start_date + timedelta(days=lookahead_days)

    # Reuse the analysis endpoint logic
    return await analyze_schedule_gaps(
        schedule_id=schedule_id,
        start_date=start_date,
        end_date=end_date,
        coverage_type="24x7",
        db=db,
    )


# ============================================================================
# TIMEZONE SUPPORT ENDPOINTS
# ============================================================================

@router.get("/timezones")
async def list_supported_timezones():
    """
    List all supported timezones with common aliases.

    Useful for UI timezone selectors.
    """
    from app.oncall.schedules import TimezoneManager
    from zoneinfo import available_timezones

    common_timezones = [
        "UTC",
        "America/New_York",
        "America/Chicago",
        "America/Denver",
        "America/Los_Angeles",
        "Europe/London",
        "Europe/Paris",
        "Europe/Berlin",
        "Asia/Tokyo",
        "Asia/Singapore",
        "Asia/Kolkata",
        "Australia/Sydney",
        "Pacific/Auckland",
    ]

    return {
        "common": common_timezones,
        "aliases": TimezoneManager.TIMEZONE_ALIASES,
        "all": sorted(list(available_timezones())),
    }


@router.post("/timezones/convert")
async def convert_time_between_timezones(
    time_str: str = Query(..., description="ISO format datetime"),
    from_tz: str = Query(...),
    to_tz: str = Query(...),
):
    """Convert a time between timezones."""
    from app.oncall.schedules import TimezoneManager

    try:
        dt = datetime.fromisoformat(time_str)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid datetime format. Use ISO format.")

    try:
        converted = TimezoneManager.convert_time(dt, from_tz, to_tz)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Timezone conversion error: {e}")

    return {
        "original": {
            "datetime": time_str,
            "timezone": from_tz,
        },
        "converted": {
            "datetime": converted.isoformat(),
            "timezone": to_tz,
        },
    }


@router.get("/schedules/{schedule_id}/view-in-timezone")
async def view_schedule_in_timezone(
    schedule_id: UUID,
    timezone: str,
    start_date: date = Query(...),
    end_date: date = Query(...),
    db=Depends(get_async_session),
):
    """
    View schedule shifts converted to a specific timezone.

    Useful for team members viewing the schedule in their local time.
    """
    from sqlalchemy import select
    from app.oncall.schedules import TimezoneManager

    schedule = await _owned_schedule(db, schedule_id)

    result = await db.execute(
        select(OncallShift)
        .where(
            OncallShift.schedule_id == schedule.id,
            OncallShift.status.in_(["scheduled", "active"]),
            *_overlaps(start_date, end_date),
        )
        .order_by(OncallShift.start_time)
    )
    shifts = result.scalars().all()

    return {
        "schedule_id": str(schedule_id),
        "timezone": timezone,
        "shifts": [
            {
                "id": s.id,
                "user_id": s.user_id,
                "start_time_utc": s.start_time.isoformat(),
                "end_time_utc": s.end_time.isoformat(),
                "start_time_local": TimezoneManager.format_time_for_user(s.start_time, timezone),
                "end_time_local": TimezoneManager.format_time_for_user(s.end_time, timezone),
                "shift_type": s.shift_type,
            }
            for s in shifts
        ],
    }


@router.get("/schedules/{schedule_id}/dst-transitions")
async def get_dst_transitions(
    schedule_id: UUID,
    start_date: date = Query(...),
    end_date: date = Query(...),
    db=Depends(get_async_session),
):
    """
    Get DST transitions that may affect the schedule.

    Alerts about potential issues during daylight saving time changes.
    """
    from app.oncall.schedules import TimezoneManager

    schedule = await _owned_schedule(db, schedule_id)

    transitions = TimezoneManager.get_dst_transitions(
        schedule.timezone,
        start_date,
        end_date,
    )

    return {
        "schedule_id": str(schedule_id),
        "timezone": schedule.timezone,
        "transitions": transitions,
        "warning": "Schedule handoffs near DST transitions may shift by 1 hour" if transitions else None,
    }


# ============================================================================
# CALENDAR EXPORT ENDPOINTS
# ============================================================================

@router.get("/schedules/{schedule_id}/ical")
async def get_schedule_ical_feed(
    schedule_id: UUID,
    user_id: UUID | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    db=Depends(get_async_session),
):
    """
    Generate iCal feed for a schedule.

    Can be used to subscribe to on-call schedules in calendar apps.
    Optionally filter by user to get personal schedule only.
    """
    from sqlalchemy import select
    from fastapi.responses import Response
    from app.oncall.schedules import CalendarExporter, ScheduleShift

    schedule = await _owned_schedule(db, schedule_id)

    # Default to next 30 days
    if not start_date:
        start_date = date.today()
    if not end_date:
        end_date = start_date + timedelta(days=30)

    # Get shifts
    query = select(OncallShift).where(
        OncallShift.schedule_id == schedule.id,
        OncallShift.status.in_(["scheduled", "active"]),
        *_overlaps(start_date, end_date),
    )

    if user_id:
        query = query.where(OncallShift.user_id == user_id)

    query = query.order_by(OncallShift.start_time)
    result = await db.execute(query)
    shifts_db = result.scalars().all()

    # Convert to ScheduleShift objects
    shifts = [
        ScheduleShift(
            shift_id=s.id,
            schedule_id=s.schedule_id,
            user_id=s.user_id,
            start_time=s.start_time,
            end_time=s.end_time,
            shift_type=s.shift_type,
            layer=1,
            source=s.source,
        )
        for s in shifts_db
    ]

    # Generate iCal
    ical_content = CalendarExporter.generate_ical(
        shifts=shifts,
        schedule_name=schedule.name,
        include_reminders=True,
    )

    return Response(
        content=ical_content,
        media_type="text/calendar",
        headers={
            "Content-Disposition": f"attachment; filename=oncall-{schedule_id}.ics"
        },
    )


@router.get("/schedules/{schedule_id}/ical-url")
async def get_ical_subscription_url(
    schedule_id: UUID,
    request: Request,
    user_id: UUID | None = None,
    db=Depends(get_async_session),
):
    """
    Get the subscription URL for the iCal feed.

    Returns a URL that can be added to calendar applications
    for automatic syncing.

    The prefix used to be the hardcoded `/api/v1/oncall`, which no route has
    ever matched — this router is mounted at `/oncall` — so the URL handed out
    here 404'd while the real feed sat one path away. It is now reversed from
    the feed route itself, which cannot drift, and made absolute against the
    request: a calendar app given a relative path has nothing to subscribe to.
    """
    schedule = await _owned_schedule(db, schedule_id)

    url = str(request.url_for("get_schedule_ical_feed", schedule_id=schedule_id))
    if user_id:
        url += f"?user_id={user_id}"

    return {
        "schedule_id": str(schedule_id),
        "schedule_name": schedule.name,
        "subscription_url": url,
        "instructions": {
            "google_calendar": "Go to Settings > Add calendar > From URL, then paste the subscription URL",
            "apple_calendar": "File > New Calendar Subscription, then paste the subscription URL",
            "outlook": "Add calendar > Subscribe from web, then paste the subscription URL",
        },
    }


# ============================================================================
# WORKLOAD ANALYSIS ENDPOINTS
# ============================================================================

@router.get("/schedules/{schedule_id}/workload-analysis")
async def analyze_schedule_workload(
    schedule_id: UUID,
    start_date: date = Query(...),
    end_date: date = Query(...),
    db=Depends(get_async_session),
):
    """
    Analyze workload distribution for a schedule.

    Returns metrics on fairness of on-call distribution including:
    - Total hours per user
    - Weekend hours
    - Night hours
    - Fairness score
    """
    from sqlalchemy import select
    from app.oncall.schedules import ScheduleAnalyzer, ScheduleShift

    schedule = await _owned_schedule(db, schedule_id)

    result = await db.execute(
        select(OncallShift)
        .where(
            OncallShift.schedule_id == schedule.id,
            OncallShift.status.in_(["scheduled", "active", "completed"]),
            *_overlaps(start_date, end_date),
        )
    )
    shifts_db = result.scalars().all()

    shifts = [
        ScheduleShift(
            shift_id=s.id,
            schedule_id=s.schedule_id,
            user_id=s.user_id,
            start_time=s.start_time,
            end_time=s.end_time,
            shift_type=s.shift_type,
            layer=1,
            source=s.source,
        )
        for s in shifts_db
    ]

    analysis = ScheduleAnalyzer.analyze_workload(shifts, start_date, end_date)

    return {
        "schedule_id": str(schedule_id),
        **analysis,
    }


# ============================================================================
# ENHANCED ESCALATION POLICY ENDPOINTS
# ============================================================================

class CreateEnhancedEscalationPolicyRequest(BaseModel):
    """Request to create an enhanced escalation policy."""
    team_id: UUID
    name: str
    description: str = ""
    steps: list[dict[str, Any]]
    high_urgency_steps: list[dict[str, Any]] | None = None
    low_urgency_steps: list[dict[str, Any]] | None = None
    time_rules: list[dict[str, Any]] | None = None
    match_conditions: list[dict[str, Any]] | None = None
    repeat_enabled: bool = True
    repeat_limit: int = 3
    is_default: bool = False


def _requested_channels(channel_enum: Any, requested: Any) -> list[Any]:
    """The channels a step asked for, or the default that can deliver.

    An unrecognised name is a 400 naming the ones that exist: it used to be an
    unhandled ValueError out of the enum, so a typo in one channel of one step
    answered 500 with no indication of which word was wrong.
    """
    names = list(requested) if requested else list(DEFAULT_ESCALATION_CHANNELS)
    channels = []
    for name in names:
        try:
            channels.append(channel_enum(name))
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Unknown notification channel {name!r}. Known channels: "
                    + ", ".join(sorted(c.value for c in channel_enum))
                ),
            ) from None
    return channels


@router.post("/escalation-policies/enhanced")
async def create_enhanced_escalation_policy(
    request: CreateEnhancedEscalationPolicyRequest,
    db=Depends(get_async_session),
):
    """
    Create an escalation policy from a step list, with delays and repeat.

    The urgency-specific paths, time-based routing and match conditions this
    route advertises are refused rather than accepted, because only
    `steps` survives being saved: `oncall_escalation_policies` has columns for a
    single ordered step list, and this handler persists `policy.steps` alone —
    `low_urgency_steps`, `time_rules` and `match_conditions` were never even read
    off the request, and `high_urgency_steps` was built into an object that was
    then dropped on the floor. The response said otherwise, reporting
    `has_high_urgency_path: true` for a path that did not exist and
    `time_rules_count: 0` for rules the caller had just sent. An after-hours
    escalation path that silently never fires is discovered at 3am; a 400 is
    discovered when the policy is written.
    """
    from app.oncall.escalations import (
        EscalationPolicyBuilder,
        EscalationTarget,
        TargetType,
        NotificationChannel,
    )
    from uuid import uuid4

    team = await _owned_team(db, request.team_id)

    unstorable = [
        name
        for name, value in (
            ("high_urgency_steps", request.high_urgency_steps),
            ("low_urgency_steps", request.low_urgency_steps),
            ("time_rules", request.time_rules),
            ("match_conditions", request.match_conditions),
        )
        if value
    ]
    if unstorable:
        raise HTTPException(
            status_code=400,
            detail=(
                "This build stores a single escalation step list per policy, so "
                + ", ".join(unstorable)
                + " would be accepted and then never applied to an alert. Put the "
                "steps that must run in `steps`, or create one policy per case and "
                "match it with `conditions` on POST /oncall/escalation-policies."
            ),
        )

    policy_id = uuid4()
    builder = EscalationPolicyBuilder(request.name, policy_id)
    builder.for_team(team.id)
    builder.with_description(request.description)

    # Add default steps
    for step_config in request.steps:
        targets = []
        for t in step_config.get("targets", []):
            targets.append(EscalationTarget(
                target_type=TargetType(t["type"]),
                target_id=t["id"],
                target_name=t.get("name"),
                schedule_layer=t.get("layer"),
            ))

        # A step that names no channels gets the deliverable default rather than
        # a literal repeated here. This one said ["push", "sms"]: push cannot
        # deliver in this build at all and sms needs a number, so a policy posted
        # without channels was a policy that paged nobody. Because
        # `EscalationStep.to_dict()` writes `notification_channels` explicitly,
        # nothing downstream ever got the chance to substitute a working default.
        channels = _requested_channels(
            NotificationChannel, step_config.get("channels")
        )

        builder.add_step(
            delay_minutes=step_config.get("delay_minutes", 0),
            targets=targets,
            channels=channels,
            repeat_count=step_config.get("repeat_count", 1),
            description=step_config.get("description"),
        )

    builder.with_repeat(enabled=request.repeat_enabled, limit=request.repeat_limit)

    if request.is_default:
        builder.as_default()

    policy = builder.build()

    # Store in database (using existing escalation service)
    service = await get_escalation_service(db)
    db_policy = await service.create_escalation_policy(
        team_id=team.id,
        name=request.name,
        description=request.description,
        steps=[s.to_dict() for s in policy.steps],
        is_default=request.is_default,
        repeat_enabled=request.repeat_enabled,
        repeat_limit=request.repeat_limit,
    )

    # Counted off the rows that were written, not off the in-memory builder: the
    # two were not the same thing, and it was the builder that got reported.
    await db.refresh(db_policy, ["steps"])
    return {
        "id": db_policy.id,
        "name": db_policy.name,
        "steps_count": len(db_policy.steps),
    }


@router.post("/escalation-policies/from-template")
async def create_policy_from_template(
    template_type: str = Query(..., description="simple, critical, business_hours"),
    team_id: UUID = Query(...),
    schedule_id: str = Query(...),
    slack_channel_id: str | None = None,
    timezone: str = "UTC",
    db=Depends(get_async_session),
):
    """
    Create an escalation policy from a predefined template.

    Templates:
    - simple: Basic 3-level escalation (primary -> secondary -> team)
    - critical: Aggressive escalation for critical alerts with phone calls
    - business_hours: Different behavior during business hours vs after hours
    """
    from app.oncall.escalations import (
        create_simple_policy,
        create_critical_alerts_policy,
        create_business_hours_policy,
    )
    from uuid import uuid4

    team = await _owned_team(db, team_id)

    policy_id = uuid4()

    if template_type == "simple":
        policy = create_simple_policy(
            name=f"Simple Policy - {team_id}",
            schedule_id=schedule_id,
            team_id=team_id,
            policy_id=policy_id,
        )
    elif template_type == "critical":
        if not slack_channel_id:
            raise HTTPException(status_code=400, detail="slack_channel_id required for critical template")
        policy = create_critical_alerts_policy(
            name=f"Critical Alerts Policy - {team_id}",
            schedule_id=schedule_id,
            slack_channel_id=slack_channel_id,
            team_id=team_id,
            policy_id=policy_id,
        )
    elif template_type == "business_hours":
        policy = create_business_hours_policy(
            name=f"Business Hours Policy - {team_id}",
            schedule_id=schedule_id,
            timezone=timezone,
            team_id=team_id,
            policy_id=policy_id,
        )
    else:
        raise HTTPException(status_code=400, detail=f"Unknown template type: {template_type}")

    # Store in database
    service = await get_escalation_service(db)
    db_policy = await service.create_escalation_policy(
        team_id=team.id,
        name=policy.name,
        description=policy.description,
        steps=[s.to_dict() for s in policy.steps],
        is_default=policy.is_default,
        repeat_enabled=policy.repeat_enabled,
        repeat_limit=policy.repeat_limit,
    )

    return {
        "id": db_policy.id,
        "name": db_policy.name,
        "template": template_type,
        "steps_count": len(policy.steps),
    }
