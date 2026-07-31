"""Team management API routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Header
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
from app.teams.store import get_team_store

router = APIRouter(prefix="/teams", tags=["teams"])


def _get_user_from_header(authorization: str | None) -> dict[str, Any]:
    """Extract user info from auth header. In production, verify JWT."""
    # Simplified: in production, decode and verify Supabase JWT
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization required")

    # For demo, use a placeholder user
    # In production: decode JWT, extract user_id, email from Supabase token
    return {
        "id": "demo-user-id",
        "email": "user@example.com",
    }


def _check_team_permission(
    team_id: str, user_id: str, required_roles: list[TeamRole]
) -> None:
    """Check if user has required role in team."""
    store = get_team_store()
    member = store.get_member(team_id, user_id)
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

    team = store.create_team(
        name=request.name,
        owner_id=user["id"],
        owner_email=user["email"],
        slug=request.slug,
    )

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

    teams = store.get_user_teams(user["id"])
    return [
        TeamResponse(
            id=team.id,
            name=team.name,
            slug=team.slug,
            owner_id=team.owner_id,
            created_at=team.created_at,
            member_count=len(store.get_members(team.id)),
            role=role,
        )
        for team, role in teams
    ]


@router.get("/{team_id}", response_model=TeamResponse)
async def get_team(
    team_id: str,
    authorization: str | None = Header(None),
) -> TeamResponse:
    """Get team details."""
    user = _get_user_from_header(authorization)
    store = get_team_store()

    team = store.get_team(team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    member = store.get_member(team_id, user["id"])
    if not member:
        raise HTTPException(status_code=403, detail="Not a team member")

    return TeamResponse(
        id=team.id,
        name=team.name,
        slug=team.slug,
        owner_id=team.owner_id,
        created_at=team.created_at,
        member_count=len(store.get_members(team_id)),
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

    _check_team_permission(team_id, user["id"], [TeamRole.OWNER, TeamRole.ADMIN])

    updates = {}
    if request.name:
        updates["name"] = request.name
    if request.settings:
        updates["settings"] = request.settings

    team = store.update_team(team_id, updates)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    member = store.get_member(team_id, user["id"])
    return TeamResponse(
        id=team.id,
        name=team.name,
        slug=team.slug,
        owner_id=team.owner_id,
        created_at=team.created_at,
        member_count=len(store.get_members(team_id)),
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

    _check_team_permission(team_id, user["id"], [TeamRole.OWNER])

    if not store.delete_team(team_id):
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
    member = store.get_member(team_id, user["id"])
    if not member:
        raise HTTPException(status_code=403, detail="Not a team member")

    members = store.get_members(team_id)
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

    _check_team_permission(team_id, user["id"], [TeamRole.OWNER, TeamRole.ADMIN])

    # Admins can't promote to admin or owner
    current_user_member = store.get_member(team_id, user["id"])
    if current_user_member and current_user_member.role == TeamRole.ADMIN:
        if request.role in [TeamRole.ADMIN, TeamRole.OWNER]:
            raise HTTPException(
                status_code=403, detail="Admins cannot promote to admin or owner"
            )

    member = store.update_member_role(team_id, user_id, request.role)
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
        _check_team_permission(team_id, user["id"], [TeamRole.OWNER, TeamRole.ADMIN])

    if not store.remove_member(team_id, user_id):
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

    _check_team_permission(team_id, user["id"], [TeamRole.OWNER, TeamRole.ADMIN])

    invitations = store.get_invitations(team_id)
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

    _check_team_permission(team_id, user["id"], [TeamRole.OWNER, TeamRole.ADMIN])

    # Admins can't invite as admin or owner
    current_member = store.get_member(team_id, user["id"])
    if current_member and current_member.role == TeamRole.ADMIN:
        if request.role in [TeamRole.ADMIN, TeamRole.OWNER]:
            raise HTTPException(
                status_code=403, detail="Admins cannot invite as admin or owner"
            )

    invitation = store.create_invitation(
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

    _check_team_permission(team_id, user["id"], [TeamRole.OWNER, TeamRole.ADMIN])

    if not store.cancel_invitation(invitation_id):
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

    invitation, member = store.accept_invitation(invitation_id, user["id"])

    if not invitation:
        raise HTTPException(status_code=404, detail="Invitation not found")

    if not member:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot accept invitation (status: {invitation.status.value})",
        )

    team = store.get_team(invitation.team_id)
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

    invitations = store.get_user_pending_invitations(user["email"])
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
