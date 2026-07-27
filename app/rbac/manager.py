"""RBAC manager for user and team role assignments.

Handles:
- User role assignments at org and team level
- Team creation and membership
- Role inheritance and resolution
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.rbac.permissions import (
    Role,
    RoleType,
    PermissionSet,
    ROLE_PERMISSIONS,
)

log = logging.getLogger("devcopilot.rbac.manager")


@dataclass
class UserRole:
    """A user's role assignment."""

    id: str
    user_id: str
    org_id: str
    role_type: RoleType
    team_id: str | None = None
    custom_permissions: PermissionSet | None = None
    granted_by: str = ""
    granted_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def is_expired(self) -> bool:
        """Check if role assignment is expired."""
        if self.expires_at is None:
            return False
        return datetime.now(timezone.utc) > self.expires_at


@dataclass
class Team:
    """A team within an organization."""

    id: str
    org_id: str
    name: str
    description: str = ""
    parent_team_id: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    created_by: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TeamMembership:
    """A user's membership in a team."""

    id: str
    user_id: str
    team_id: str
    role_type: RoleType = RoleType.VIEWER
    joined_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    added_by: str = ""


_USER_ROLES: dict[str, UserRole] = {}
_TEAMS: dict[str, Team] = {}
_TEAM_MEMBERSHIPS: dict[str, TeamMembership] = {}
_CUSTOM_ROLES: dict[str, Role] = {}


class RBACManager:
    """Manages role assignments and permissions."""

    def __init__(self, org_id: str):
        self.org_id = org_id

    def assign_role(
        self,
        user_id: str,
        role_type: RoleType,
        team_id: str | None = None,
        granted_by: str = "",
        expires_at: datetime | None = None,
        custom_permissions: PermissionSet | None = None,
    ) -> UserRole:
        """Assign a role to a user."""
        role_id = f"role_{secrets.token_hex(8)}"

        user_role = UserRole(
            id=role_id,
            user_id=user_id,
            org_id=self.org_id,
            role_type=role_type,
            team_id=team_id,
            custom_permissions=custom_permissions,
            granted_by=granted_by,
            expires_at=expires_at,
        )

        _USER_ROLES[role_id] = user_role
        log.info(
            "Assigned role %s to user %s in org %s",
            role_type.value,
            user_id,
            self.org_id,
        )

        return user_role

    def revoke_role(self, role_id: str) -> bool:
        """Revoke a role assignment."""
        if role_id not in _USER_ROLES:
            return False

        role = _USER_ROLES.pop(role_id)
        log.info(
            "Revoked role %s from user %s",
            role.role_type.value,
            role.user_id,
        )
        return True

    def get_user_roles(
        self,
        user_id: str,
        team_id: str | None = None,
        include_expired: bool = False,
    ) -> list[UserRole]:
        """Get all roles for a user."""
        roles = []
        for role in _USER_ROLES.values():
            if role.user_id != user_id:
                continue
            if role.org_id != self.org_id:
                continue
            if team_id and role.team_id != team_id:
                continue
            if not include_expired and role.is_expired():
                continue
            roles.append(role)
        return roles

    def get_user_permissions(
        self,
        user_id: str,
        team_id: str | None = None,
    ) -> PermissionSet:
        """Get effective permissions for a user."""
        roles = self.get_user_roles(user_id, team_id)

        combined = PermissionSet()

        for user_role in roles:
            role_def = ROLE_PERMISSIONS.get(user_role.role_type)
            if role_def:
                combined.permissions.update(role_def.permissions.permissions)

            if user_role.custom_permissions:
                combined.permissions.update(user_role.custom_permissions.permissions)

        return combined

    def get_highest_role(self, user_id: str) -> RoleType | None:
        """Get the highest role for a user."""
        roles = self.get_user_roles(user_id)
        if not roles:
            return None

        role_hierarchy = [
            RoleType.OWNER,
            RoleType.ADMIN,
            RoleType.ONCALL,
            RoleType.EDITOR,
            RoleType.VIEWER,
        ]

        for role_type in role_hierarchy:
            if any(r.role_type == role_type for r in roles):
                return role_type

        return roles[0].role_type

    def create_team(
        self,
        name: str,
        description: str = "",
        parent_team_id: str | None = None,
        created_by: str = "",
    ) -> Team:
        """Create a new team."""
        team_id = f"team_{secrets.token_hex(8)}"

        team = Team(
            id=team_id,
            org_id=self.org_id,
            name=name,
            description=description,
            parent_team_id=parent_team_id,
            created_by=created_by,
        )

        _TEAMS[team_id] = team
        log.info("Created team %s in org %s", name, self.org_id)

        return team

    def get_team(self, team_id: str) -> Team | None:
        """Get a team by ID."""
        team = _TEAMS.get(team_id)
        if team and team.org_id == self.org_id:
            return team
        return None

    def list_teams(self) -> list[Team]:
        """List all teams in the organization."""
        return [t for t in _TEAMS.values() if t.org_id == self.org_id]

    def delete_team(self, team_id: str) -> bool:
        """Delete a team."""
        team = _TEAMS.get(team_id)
        if not team or team.org_id != self.org_id:
            return False

        memberships_to_remove = [
            m.id for m in _TEAM_MEMBERSHIPS.values() if m.team_id == team_id
        ]
        for m_id in memberships_to_remove:
            del _TEAM_MEMBERSHIPS[m_id]

        del _TEAMS[team_id]
        log.info("Deleted team %s", team_id)
        return True

    def add_team_member(
        self,
        team_id: str,
        user_id: str,
        role_type: RoleType = RoleType.VIEWER,
        added_by: str = "",
    ) -> TeamMembership | None:
        """Add a user to a team."""
        team = self.get_team(team_id)
        if not team:
            return None

        for membership in _TEAM_MEMBERSHIPS.values():
            if membership.team_id == team_id and membership.user_id == user_id:
                membership.role_type = role_type
                return membership

        membership_id = f"membership_{secrets.token_hex(8)}"
        membership = TeamMembership(
            id=membership_id,
            user_id=user_id,
            team_id=team_id,
            role_type=role_type,
            added_by=added_by,
        )

        _TEAM_MEMBERSHIPS[membership_id] = membership
        log.info("Added user %s to team %s", user_id, team_id)

        return membership

    def remove_team_member(self, team_id: str, user_id: str) -> bool:
        """Remove a user from a team."""
        for m_id, membership in list(_TEAM_MEMBERSHIPS.items()):
            if membership.team_id == team_id and membership.user_id == user_id:
                del _TEAM_MEMBERSHIPS[m_id]
                log.info("Removed user %s from team %s", user_id, team_id)
                return True
        return False

    def get_team_members(self, team_id: str) -> list[TeamMembership]:
        """Get all members of a team."""
        return [m for m in _TEAM_MEMBERSHIPS.values() if m.team_id == team_id]

    def get_user_teams(self, user_id: str) -> list[TeamMembership]:
        """Get all teams a user belongs to."""
        return [
            m
            for m in _TEAM_MEMBERSHIPS.values()
            if m.user_id == user_id
            and _TEAMS.get(m.team_id, Team("", "", "")).org_id == self.org_id
        ]

    def create_custom_role(
        self,
        name: str,
        description: str,
        permissions: PermissionSet,
        inherits_from: list[str] | None = None,
    ) -> Role:
        """Create a custom role."""
        role_id = f"custom_role_{secrets.token_hex(8)}"

        role = Role(
            id=role_id,
            name=name,
            role_type=RoleType.CUSTOM,
            description=description,
            permissions=permissions,
            inherits_from=inherits_from or [],
            is_system=False,
            metadata={"org_id": self.org_id},
        )

        _CUSTOM_ROLES[role_id] = role
        log.info("Created custom role %s in org %s", name, self.org_id)

        return role

    def get_custom_roles(self) -> list[Role]:
        """Get all custom roles for the organization."""
        return [
            r
            for r in _CUSTOM_ROLES.values()
            if r.metadata.get("org_id") == self.org_id
        ]


_managers: dict[str, RBACManager] = {}


def _get_manager(org_id: str) -> RBACManager:
    """Get or create RBAC manager for org."""
    if org_id not in _managers:
        _managers[org_id] = RBACManager(org_id)
    return _managers[org_id]


def assign_role(
    org_id: str,
    user_id: str,
    role_type: RoleType,
    team_id: str | None = None,
    granted_by: str = "",
) -> UserRole:
    """Assign a role to a user."""
    manager = _get_manager(org_id)
    return manager.assign_role(user_id, role_type, team_id, granted_by)


def revoke_role(org_id: str, role_id: str) -> bool:
    """Revoke a role assignment."""
    manager = _get_manager(org_id)
    return manager.revoke_role(role_id)


def get_user_roles(org_id: str, user_id: str) -> list[UserRole]:
    """Get all roles for a user."""
    manager = _get_manager(org_id)
    return manager.get_user_roles(user_id)


def get_team_members(org_id: str, team_id: str) -> list[TeamMembership]:
    """Get all members of a team."""
    manager = _get_manager(org_id)
    return manager.get_team_members(team_id)


def create_team(
    org_id: str,
    name: str,
    description: str = "",
    created_by: str = "",
) -> Team:
    """Create a new team."""
    manager = _get_manager(org_id)
    return manager.create_team(name, description, created_by=created_by)


def add_team_member(
    org_id: str,
    team_id: str,
    user_id: str,
    role_type: RoleType = RoleType.VIEWER,
) -> TeamMembership | None:
    """Add a user to a team."""
    manager = _get_manager(org_id)
    return manager.add_team_member(team_id, user_id, role_type)


def remove_team_member(org_id: str, team_id: str, user_id: str) -> bool:
    """Remove a user from a team."""
    manager = _get_manager(org_id)
    return manager.remove_team_member(team_id, user_id)
