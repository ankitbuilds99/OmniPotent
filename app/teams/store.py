"""In-memory store for teams (swap for database in production)."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from app.teams.models import (
    Team,
    TeamMember,
    TeamInvitation,
    TeamRole,
    InvitationStatus,
)
from app.tenancy.scoping import scoped


class TeamStore:
    """In-memory team storage. Replace with database in production."""

    def __init__(self) -> None:
        self._teams: dict[str, Team] = {}
        self._members: dict[str, list[TeamMember]] = {}  # team_id -> members
        self._invitations: dict[str, list[TeamInvitation]] = {}  # team_id -> invitations
        self._user_teams: dict[str, list[str]] = {}  # user_id -> team_ids

    def _generate_slug(self, name: str) -> str:
        """Generate URL-safe slug from team name."""
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        base_slug = slug
        counter = 1
        while any(t.slug == slug for t in self._teams.values()):
            slug = f"{base_slug}-{counter}"
            counter += 1
        return slug

    # Team CRUD
    def create_team(self, name: str, owner_id: str, owner_email: str, slug: str | None = None) -> Team:
        """Create a new team with the owner as first member."""
        team = Team(
            name=name,
            slug=slug or self._generate_slug(name),
            owner_id=owner_id,
        )
        self._teams[team.id] = team
        self._members[team.id] = []
        self._invitations[team.id] = []

        # Add owner as first member
        owner_member = TeamMember(
            team_id=team.id,
            user_id=owner_id,
            email=owner_email,
            role=TeamRole.OWNER,
        )
        self._members[team.id].append(owner_member)

        # Track user's teams
        if owner_id not in self._user_teams:
            self._user_teams[owner_id] = []
        self._user_teams[owner_id].append(team.id)

        return team

    def get_team(self, team_id: str) -> Team | None:
        """Get team by ID."""
        return self._teams.get(team_id)

    def get_team_by_slug(self, slug: str) -> Team | None:
        """Get team by slug."""
        for team in self._teams.values():
            if team.slug == slug:
                return team
        return None

    def update_team(self, team_id: str, updates: dict[str, Any]) -> Team | None:
        """Update team fields."""
        team = self._teams.get(team_id)
        if not team:
            return None

        for key, value in updates.items():
            if hasattr(team, key) and value is not None:
                setattr(team, key, value)
        team.updated_at = datetime.utcnow()
        return team

    def delete_team(self, team_id: str) -> bool:
        """Delete a team and all its members/invitations."""
        if team_id not in self._teams:
            return False

        # Remove from user_teams
        for members in self._members.get(team_id, []):
            if members.user_id in self._user_teams:
                self._user_teams[members.user_id] = [
                    tid for tid in self._user_teams[members.user_id] if tid != team_id
                ]

        del self._teams[team_id]
        self._members.pop(team_id, None)
        self._invitations.pop(team_id, None)
        return True

    def get_user_teams(self, user_id: str) -> list[tuple[Team, TeamRole]]:
        """Get all teams a user belongs to with their role."""
        team_ids = self._user_teams.get(user_id, [])
        result = []
        for team_id in team_ids:
            team = self._teams.get(team_id)
            if team:
                member = self.get_member(team_id, user_id)
                if member:
                    result.append((team, member.role))
        return result

    # Member management
    def get_members(self, team_id: str) -> list[TeamMember]:
        """Get all members of a team."""
        return self._members.get(team_id, [])

    def get_member(self, team_id: str, user_id: str) -> TeamMember | None:
        """Get a specific member."""
        for member in self._members.get(team_id, []):
            if member.user_id == user_id:
                return member
        return None

    def get_member_by_email(self, team_id: str, email: str) -> TeamMember | None:
        """Get member by email."""
        for member in self._members.get(team_id, []):
            if member.email.lower() == email.lower():
                return member
        return None

    def add_member(
        self, team_id: str, user_id: str, email: str, role: TeamRole = TeamRole.MEMBER
    ) -> TeamMember | None:
        """Add a member to a team."""
        if team_id not in self._teams:
            return None

        # Check if already a member
        if self.get_member(team_id, user_id):
            return None

        member = TeamMember(
            team_id=team_id,
            user_id=user_id,
            email=email,
            role=role,
        )
        self._members[team_id].append(member)

        # Track user's teams
        if user_id not in self._user_teams:
            self._user_teams[user_id] = []
        self._user_teams[user_id].append(team_id)

        return member

    def update_member_role(self, team_id: str, user_id: str, role: TeamRole) -> TeamMember | None:
        """Update a member's role."""
        member = self.get_member(team_id, user_id)
        if not member:
            return None

        # Can't change owner's role
        if member.role == TeamRole.OWNER:
            return None

        member.role = role
        return member

    def remove_member(self, team_id: str, user_id: str) -> bool:
        """Remove a member from a team."""
        members = self._members.get(team_id, [])
        for i, member in enumerate(members):
            if member.user_id == user_id:
                # Can't remove owner
                if member.role == TeamRole.OWNER:
                    return False
                members.pop(i)

                # Remove from user_teams
                if user_id in self._user_teams:
                    self._user_teams[user_id] = [
                        tid for tid in self._user_teams[user_id] if tid != team_id
                    ]
                return True
        return False

    def update_member_activity(self, team_id: str, user_id: str) -> None:
        """Update member's last active timestamp."""
        member = self.get_member(team_id, user_id)
        if member:
            member.last_active = datetime.utcnow()

    # Invitation management
    def get_invitations(self, team_id: str) -> list[TeamInvitation]:
        """Get all pending invitations for a team."""
        now = datetime.utcnow()
        invitations = []
        for inv in self._invitations.get(team_id, []):
            # Auto-expire old invitations
            if inv.status == InvitationStatus.PENDING and inv.expires_at < now:
                inv.status = InvitationStatus.EXPIRED
            if inv.status == InvitationStatus.PENDING:
                invitations.append(inv)
        return invitations

    def get_invitation(self, invitation_id: str) -> TeamInvitation | None:
        """Get invitation by ID."""
        for invitations in self._invitations.values():
            for inv in invitations:
                if inv.id == invitation_id:
                    return inv
        return None

    def get_invitation_by_email(self, team_id: str, email: str) -> TeamInvitation | None:
        """Get pending invitation by email."""
        for inv in self._invitations.get(team_id, []):
            if inv.email.lower() == email.lower() and inv.status == InvitationStatus.PENDING:
                return inv
        return None

    def create_invitation(
        self, team_id: str, email: str, role: TeamRole, invited_by: str
    ) -> TeamInvitation | None:
        """Create a new invitation."""
        if team_id not in self._teams:
            return None

        # Check if already a member
        if self.get_member_by_email(team_id, email):
            return None

        # Check for existing pending invitation
        existing = self.get_invitation_by_email(team_id, email)
        if existing:
            return None

        invitation = TeamInvitation(
            team_id=team_id,
            email=email,
            role=role,
            invited_by=invited_by,
        )
        self._invitations[team_id].append(invitation)
        return invitation

    def accept_invitation(
        self, invitation_id: str, user_id: str
    ) -> tuple[TeamInvitation | None, TeamMember | None]:
        """Accept an invitation and add user to team."""
        invitation = self.get_invitation(invitation_id)
        if not invitation:
            return None, None

        if invitation.status != InvitationStatus.PENDING:
            return invitation, None

        if invitation.expires_at < datetime.utcnow():
            invitation.status = InvitationStatus.EXPIRED
            return invitation, None

        # Add as member
        member = self.add_member(
            invitation.team_id,
            user_id,
            invitation.email,
            invitation.role,
        )

        if member:
            invitation.status = InvitationStatus.ACCEPTED
            invitation.accepted_at = datetime.utcnow()

        return invitation, member

    def cancel_invitation(self, invitation_id: str) -> bool:
        """Cancel a pending invitation."""
        invitation = self.get_invitation(invitation_id)
        if not invitation or invitation.status != InvitationStatus.PENDING:
            return False

        invitation.status = InvitationStatus.CANCELLED
        return True

    def get_user_pending_invitations(self, email: str) -> list[tuple[TeamInvitation, Team]]:
        """Get all pending invitations for a user by email."""
        result = []
        now = datetime.utcnow()
        for team_id, invitations in self._invitations.items():
            team = self._teams.get(team_id)
            if not team:
                continue
            for inv in invitations:
                if inv.email.lower() == email.lower():
                    if inv.status == InvitationStatus.PENDING:
                        if inv.expires_at < now:
                            inv.status = InvitationStatus.EXPIRED
                        else:
                            result.append((inv, team))
        return result


# One store per org. Team names, memberships and pending invitations are tenant
# data; a single process-wide instance put every customer's roster in one bag.
_stores = scoped(TeamStore)


def get_team_store() -> TeamStore:
    """The team store for the org on the current request."""
    return _stores.get()
