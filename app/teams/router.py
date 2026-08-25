"""Team management API routes."""

from __future__ import annotations

import hmac
import logging

from fastapi import APIRouter, Depends, HTTPException, Header, Request
from typing import Any

from app.teams.models import (
    TeamRole,
    CreateTeamRequest,
    UpdateTeamRequest,
    InviteMemberRequest,
    UpdateMemberRoleRequest,
    TeamResponse,
    TeamMemberResponse,
    InvitationResponse,
)
from app import tenant_context
from app.auth.oauth import verify_session_token
from app.config import get_settings
from app.teams.store import SlugTaken, get_team_store
from app.tenancy import models as tenancy_models
from app.tenancy.supabase_auth import verify_jwt

log = logging.getLogger("omnipotent.teams.router")

# Identity of a caller holding the single-tenant shared credential. There is
# exactly one such principal per deployment (COPILOT_API_TOKEN is one token, not
# one per person), so it gets one stable id rather than a fabricated user.
_SHARED_PRINCIPAL = "shared-api-token"


def _principal_for_opaque_token(token: str) -> dict[str, Any] | None:
    """Identity for a caller whose credential is not a JWT, or None if there is none.

    `require_auth` (app/api/main.py) is an app-wide dependency, so anything that
    reaches a handler here already presented a credential this deployment accepts:
    a per-tenant `dcp_…` API key, the shared COPILOT_API_TOKEN, or nothing at all
    when auth is switched off for local dev. Demanding a Supabase JWT on top of
    that 401'd every real caller, because `verify_jwt` returns None unconditionally
    unless SUPABASE_JWKS_URL is configured — which no deployment of this app sets
    by default.

    The token is re-checked here rather than assumed: this must fail closed even
    if the router is ever mounted on an app without the global auth dependency.
    """
    s = get_settings()
    if s.copilot_multi_tenant:
        cfg = tenant_context.get_tenant()
        if cfg is None:
            return None
        # An API key is issued to an org, not to a person, and keys rotate — so
        # the org is the principal. `app.teams.store` already keeps one store per
        # org, so this identity never reaches another tenant's roster.
        return {"id": f"org:{cfg.org_id}", "email": ""}

    expected = s.copilot_api_token
    if not expected:
        return {"id": _SHARED_PRINCIPAL, "email": ""}  # auth disabled (local dev)
    if token and hmac.compare_digest(token, expected):
        return {"id": _SHARED_PRINCIPAL, "email": ""}
    return None


def _get_user_from_header(authorization: str | None) -> dict[str, Any]:
    """Resolve the caller's identity from the Authorization header.

    The signature is verified. A previous version base64-decoded the JWT payload
    without checking the signature, so anyone who knew a user's `sub` could forge
    `header.payload.ANYTHING` and act as that user — including deleting their teams.
    """
    token = ""
    if authorization:
        if not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Invalid authorization header")
        token = authorization[7:].strip()  # Remove "Bearer " prefix

    if token.count(".") == 2:
        # Two signed identities are JWT-shaped: a Supabase/SSO JWT, and the session
        # token our own OAuth callback mints (HS256 over COPILOT_SECRET_KEY). Only
        # the first was accepted, so a user who signed in with GitHub/Google got
        # "Invalid or expired token" on every team route. Both are signature-checked.
        claims = verify_jwt(token) or verify_session_token(token)
        if claims is None:
            raise HTTPException(
                status_code=401,
                detail="Invalid or expired token. Please authenticate.",
            )
        user_id = claims.get("sub", "")
        if not user_id:
            raise HTTPException(status_code=401, detail="Token is missing a subject claim")
        return {"id": user_id, "email": claims.get("email", "")}

    principal = _principal_for_opaque_token(token)
    if principal is None:
        raise HTTPException(
            status_code=401,
            detail="Authorization required" if not token else "Invalid or expired credential.",
        )
    return principal


async def _enforce_tenant_rbac(request: Request) -> None:
    """Apply the org-level RBAC matrix to a tenant-authenticated caller.

    Team roles govern who may do what *within* a team, but a caller who arrives on
    a tenant credential also carries an org role, and the org matrix says member
    management is admin-and-up. Without this a `viewer` API key could create a
    team, make itself the owner and invite people — a privilege it is denied
    everywhere else in the API.

    No-op when there is no tenant on the request (single-tenant deployments and
    the offline demo have no org roles).
    """
    cfg = tenant_context.get_tenant()
    if cfg is None:
        return
    action = "view" if request.method in ("GET", "HEAD", "OPTIONS") else "manage_members"
    if not tenancy_models.can(cfg.role, action):
        raise HTTPException(
            status_code=403,
            detail=f"Role '{cfg.role}' is not permitted to {action.replace('_', ' ')}.",
        )


router = APIRouter(
    prefix="/teams",
    tags=["teams"],
    dependencies=[Depends(_enforce_tenant_rbac)],
)


async def _check_team_permission(
    team_id: str, user_id: str, required_roles: list[TeamRole]
) -> None:
    """Check if user has required role in team."""
    store = get_team_store()
    member = await store.get_member(team_id, user_id)
    if not member:
        raise HTTPException(status_code=403, detail="Not a team member")
    if member.role not in required_roles:
        raise HTTPException(status_code=403, detail="Insufficient permissions")


# Team endpoints
@router.post("", response_model=TeamResponse)
async def create_team(
    request: CreateTeamRequest,
    authorization: str | None = Header(None),
) -> TeamResponse:
    """Create a new team."""
    user = _get_user_from_header(authorization)
    store = get_team_store()

    try:
        team = await store.create_team(
            name=request.name,
            owner_id=user["id"],
            owner_email=user["email"],
            slug=request.slug,
        )
    except SlugTaken as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return TeamResponse(
        id=team.id,
        name=team.name,
        slug=team.slug,
        owner_id=team.owner_id,
        created_at=team.created_at,
        member_count=1,
        role=TeamRole.OWNER,
    )


@router.get("", response_model=list[TeamResponse])
async def list_teams(
    authorization: str | None = Header(None),
) -> list[TeamResponse]:
    """List all teams the user belongs to."""
    user = _get_user_from_header(authorization)
    store = get_team_store()

    teams = await store.get_user_teams(user["id"])
    return [
        TeamResponse(
            id=team.id,
            name=team.name,
            slug=team.slug,
            owner_id=team.owner_id,
            created_at=team.created_at,
            member_count=member_count,
            role=role,
        )
        for team, role, member_count in teams
    ]


@router.get("/{team_id}", response_model=TeamResponse)
async def get_team(
    team_id: str,
    authorization: str | None = Header(None),
) -> TeamResponse:
    """Get team details."""
    user = _get_user_from_header(authorization)
    store = get_team_store()

    team = await store.get_team(team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    member = await store.get_member(team_id, user["id"])
    if not member:
        raise HTTPException(status_code=403, detail="Not a team member")

    return TeamResponse(
        id=team.id,
        name=team.name,
        slug=team.slug,
        owner_id=team.owner_id,
        created_at=team.created_at,
        member_count=await store.count_members(team_id),
        role=member.role,
    )


@router.patch("/{team_id}", response_model=TeamResponse)
async def update_team(
    team_id: str,
    request: UpdateTeamRequest,
    authorization: str | None = Header(None),
) -> TeamResponse:
    """Update team settings. Requires admin or owner role."""
    user = _get_user_from_header(authorization)
    store = get_team_store()

    await _check_team_permission(team_id, user["id"], [TeamRole.OWNER, TeamRole.ADMIN])

    updates: dict[str, Any] = {}
    if request.name:
        updates["name"] = request.name
    if request.settings:
        updates["settings"] = request.settings

    team = await store.update_team(team_id, updates)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    member = await store.get_member(team_id, user["id"])
    return TeamResponse(
        id=team.id,
        name=team.name,
        slug=team.slug,
        owner_id=team.owner_id,
        created_at=team.created_at,
        member_count=await store.count_members(team_id),
        role=member.role if member else None,
    )


@router.delete("/{team_id}")
async def delete_team(
    team_id: str,
    authorization: str | None = Header(None),
) -> dict[str, str]:
    """Delete a team. Only owner can delete."""
    user = _get_user_from_header(authorization)
    store = get_team_store()

    await _check_team_permission(team_id, user["id"], [TeamRole.OWNER])

    if not await store.delete_team(team_id):
        raise HTTPException(status_code=404, detail="Team not found")

    return {"message": "Team deleted"}


# Member endpoints
@router.get("/{team_id}/members", response_model=list[TeamMemberResponse])
async def list_members(
    team_id: str,
    authorization: str | None = Header(None),
) -> list[TeamMemberResponse]:
    """List all team members."""
    user = _get_user_from_header(authorization)
    store = get_team_store()

    # Any member can view the member list
    member = await store.get_member(team_id, user["id"])
    if not member:
        raise HTTPException(status_code=403, detail="Not a team member")

    members = await store.get_members(team_id)
    return [
        TeamMemberResponse(
            id=m.id,
            user_id=m.user_id,
            email=m.email,
            role=m.role,
            joined_at=m.joined_at,
            last_active=m.last_active,
        )
        for m in members
    ]


@router.patch("/{team_id}/members/{user_id}", response_model=TeamMemberResponse)
async def update_member_role(
    team_id: str,
    user_id: str,
    request: UpdateMemberRoleRequest,
    authorization: str | None = Header(None),
) -> TeamMemberResponse:
    """Update a member's role. Requires admin or owner."""
    user = _get_user_from_header(authorization)
    store = get_team_store()

    await _check_team_permission(team_id, user["id"], [TeamRole.OWNER, TeamRole.ADMIN])

    # Admins can't promote to admin or owner
    current_user_member = await store.get_member(team_id, user["id"])
    if current_user_member and current_user_member.role == TeamRole.ADMIN:
        if request.role in [TeamRole.ADMIN, TeamRole.OWNER]:
            raise HTTPException(
                status_code=403, detail="Admins cannot promote to admin or owner"
            )

    member = await store.update_member_role(team_id, user_id, request.role)
    if not member:
        raise HTTPException(status_code=404, detail="Member not found or cannot change role")

    return TeamMemberResponse(
        id=member.id,
        user_id=member.user_id,
        email=member.email,
        role=member.role,
        joined_at=member.joined_at,
        last_active=member.last_active,
    )


@router.delete("/{team_id}/members/{user_id}")
async def remove_member(
    team_id: str,
    user_id: str,
    authorization: str | None = Header(None),
) -> dict[str, str]:
    """Remove a member from team. Admins+ can remove, users can leave."""
    user = _get_user_from_header(authorization)
    store = get_team_store()

    # Users can remove themselves (leave)
    if user_id != user["id"]:
        await _check_team_permission(team_id, user["id"], [TeamRole.OWNER, TeamRole.ADMIN])

    if not await store.remove_member(team_id, user_id):
        raise HTTPException(
            status_code=400, detail="Cannot remove member (owner cannot be removed)"
        )

    return {"message": "Member removed"}


# Invitation endpoints
@router.get("/{team_id}/invitations", response_model=list[InvitationResponse])
async def list_invitations(
    team_id: str,
    authorization: str | None = Header(None),
) -> list[InvitationResponse]:
    """List pending invitations. Requires admin or owner."""
    user = _get_user_from_header(authorization)
    store = get_team_store()

    await _check_team_permission(team_id, user["id"], [TeamRole.OWNER, TeamRole.ADMIN])

    invitations = await store.get_invitations(team_id)
    return [
        InvitationResponse(
            id=inv.id,
            email=inv.email,
            role=inv.role,
            status=inv.status,
            created_at=inv.created_at,
            expires_at=inv.expires_at,
        )
        for inv in invitations
    ]


@router.post("/{team_id}/invitations", response_model=InvitationResponse)
async def invite_member(
    team_id: str,
    request: InviteMemberRequest,
    authorization: str | None = Header(None),
) -> InvitationResponse:
    """Invite a new member. Requires admin or owner."""
    user = _get_user_from_header(authorization)
    store = get_team_store()

    await _check_team_permission(team_id, user["id"], [TeamRole.OWNER, TeamRole.ADMIN])

    # Admins can't invite as admin or owner
    current_member = await store.get_member(team_id, user["id"])
    if current_member and current_member.role == TeamRole.ADMIN:
        if request.role in [TeamRole.ADMIN, TeamRole.OWNER]:
            raise HTTPException(
                status_code=403, detail="Admins cannot invite as admin or owner"
            )

    invitation = await store.create_invitation(
        team_id=team_id,
        email=request.email,
        role=request.role,
        invited_by=user["id"],
    )

    if not invitation:
        raise HTTPException(
            status_code=400,
            detail="Cannot create invitation (user may already be a member or invited)",
        )

    # In production: send email notification here

    return InvitationResponse(
        id=invitation.id,
        email=invitation.email,
        role=invitation.role,
        status=invitation.status,
        created_at=invitation.created_at,
        expires_at=invitation.expires_at,
    )


@router.delete("/{team_id}/invitations/{invitation_id}")
async def cancel_invitation(
    team_id: str,
    invitation_id: str,
    authorization: str | None = Header(None),
) -> dict[str, str]:
    """Cancel a pending invitation. Requires admin or owner."""
    user = _get_user_from_header(authorization)
    store = get_team_store()

    await _check_team_permission(team_id, user["id"], [TeamRole.OWNER, TeamRole.ADMIN])

    # The invitation id is a path parameter, so without this an admin of team A
    # could cancel an invitation belonging to team B just by naming its id: the
    # permission check above is against `team_id`, but the cancel was not.
    pending = await store.get_invitation(invitation_id)
    if pending is None or pending.team_id != team_id:
        raise HTTPException(status_code=404, detail="Invitation not found or already processed")

    if not await store.cancel_invitation(invitation_id):
        raise HTTPException(status_code=404, detail="Invitation not found or already processed")

    return {"message": "Invitation cancelled"}


@router.post("/invitations/{invitation_id}/accept")
async def accept_invitation(
    invitation_id: str,
    authorization: str | None = Header(None),
) -> dict[str, Any]:
    """Accept an invitation to join a team."""
    user = _get_user_from_header(authorization)
    store = get_team_store()

    # The invitation id is not a bearer credential: it is returned to the inviter
    # and listed by the API. Without this check any authenticated user who learns
    # an invitation id can redeem an invite addressed to somebody else.
    pending = await store.get_invitation(invitation_id)
    if not pending:
        raise HTTPException(status_code=404, detail="Invitation not found")

    invited_email = (pending.email or "").strip().lower()
    caller_email = (user.get("email") or "").strip().lower()
    if not caller_email or invited_email != caller_email:
        log.warning(
            "Rejected invitation %s: addressed to %r, redeemed by %r",
            invitation_id,
            pending.email,
            user.get("email"),
        )
        raise HTTPException(
            status_code=403,
            detail="This invitation was issued to a different email address.",
        )

    invitation, member = await store.accept_invitation(invitation_id, user["id"])

    if not invitation:
        raise HTTPException(status_code=404, detail="Invitation not found")

    if not member:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot accept invitation (status: {invitation.status.value})",
        )

    team = await store.get_team(invitation.team_id)
    return {
        "message": "Invitation accepted",
        "team": {
            "id": team.id if team else None,
            "name": team.name if team else None,
        },
    }


@router.get("/invitations/pending")
async def get_my_invitations(
    authorization: str | None = Header(None),
) -> list[dict[str, Any]]:
    """Get all pending invitations for the current user."""
    user = _get_user_from_header(authorization)
    store = get_team_store()

    invitations = await store.get_user_pending_invitations(user["email"])
    return [
        {
            "invitation": InvitationResponse(
                id=inv.id,
                email=inv.email,
                role=inv.role,
                status=inv.status,
                created_at=inv.created_at,
                expires_at=inv.expires_at,
            ),
            "team": {
                "id": team.id,
                "name": team.name,
            },
        }
        for inv, team in invitations
    ]
