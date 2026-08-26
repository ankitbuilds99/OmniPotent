"""Team and workspace models for multi-tenant support."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

# Invitations are valid for a week from the moment they are created. Kept here
# rather than inlined in the default so the store can re-derive it when it
# writes a row, and so a test can name the same number the product does.
INVITATION_TTL = timedelta(days=7)


def _now() -> datetime:
    """UTC, timezone-aware.

    These were `datetime.utcnow()`, which returns a *naive* datetime. Once teams
    are stored in a `DateTime(timezone=True)` column the values read back are
    aware, and comparing an aware `expires_at` against a naive "now" raises
    TypeError rather than expiring the invitation. (`utcnow` is also deprecated
    from 3.12.)
    """
    return datetime.now(timezone.utc)


class TeamRole(str, Enum):
    """Role within a team."""
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
    VIEWER = "viewer"


class InvitationStatus(str, Enum):
    """Status of a team invitation."""
    PENDING = "pending"
    ACCEPTED = "accepted"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class Team(BaseModel):
    """A team/workspace that can have multiple members."""
    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    slug: str
    owner_id: str
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    settings: dict[str, Any] = Field(default_factory=dict)


class TeamMember(BaseModel):
    """A user's membership in a team."""
    id: str = Field(default_factory=lambda: str(uuid4()))
    team_id: str
    user_id: str
    email: str
    role: TeamRole = TeamRole.MEMBER
    joined_at: datetime = Field(default_factory=_now)
    last_active: datetime | None = None


class TeamInvitation(BaseModel):
    """An invitation to join a team."""
    id: str = Field(default_factory=lambda: str(uuid4()))
    team_id: str
    email: str
    role: TeamRole = TeamRole.MEMBER
    invited_by: str
    status: InvitationStatus = InvitationStatus.PENDING
    created_at: datetime = Field(default_factory=_now)
    expires_at: datetime = Field(default_factory=lambda: _now() + INVITATION_TTL)
    accepted_at: datetime | None = None


# Request/Response models for API
class CreateTeamRequest(BaseModel):
    """Request to create a new team."""
    name: str = Field(..., min_length=1, max_length=100)
    slug: str | None = None


class UpdateTeamRequest(BaseModel):
    """Request to update team settings."""
    name: str | None = None
    settings: dict[str, Any] | None = None


class InviteMemberRequest(BaseModel):
    """Request to invite a member to a team."""
    email: str = Field(..., min_length=5)
    role: TeamRole = TeamRole.MEMBER


class UpdateMemberRoleRequest(BaseModel):
    """Request to update a member's role."""
    role: TeamRole


class TeamResponse(BaseModel):
    """Team response with member count."""
    id: str
    name: str
    slug: str
    owner_id: str
    created_at: datetime
    member_count: int = 0
    role: TeamRole | None = None


class TeamMemberResponse(BaseModel):
    """Team member response."""
    id: str
    user_id: str
    email: str
    name: str | None = None
    avatar_url: str | None = None
    role: TeamRole
    joined_at: datetime
    last_active: datetime | None = None


class InvitationResponse(BaseModel):
    """Invitation response."""
    id: str
    email: str
    role: TeamRole
    status: InvitationStatus
    created_at: datetime
    expires_at: datetime
