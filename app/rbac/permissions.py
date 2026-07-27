"""Permission definitions and role hierarchies.

Defines:
- Granular permissions for all resources
- Role definitions with permission sets
- Permission inheritance hierarchy
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Action(Enum):
    """Actions that can be performed on resources."""

    CREATE = "create"
    READ = "read"
    UPDATE = "update"
    DELETE = "delete"
    EXECUTE = "execute"
    APPROVE = "approve"
    ADMIN = "admin"


class ResourceType(Enum):
    """Types of resources that can be protected."""

    ORGANIZATION = "organization"
    TEAM = "team"
    USER = "user"
    INVESTIGATION = "investigation"
    RUNBOOK = "runbook"
    ALERT = "alert"
    INTEGRATION = "integration"
    API_KEY = "api_key"
    WEBHOOK = "webhook"
    DASHBOARD = "dashboard"
    REPORT = "report"
    POSTMORTEM = "postmortem"
    SLO = "slo"
    ONCALL = "oncall"
    SETTINGS = "settings"
    AUDIT_LOG = "audit_log"
    BILLING = "billing"


class RoleType(Enum):
    """Built-in role types."""

    OWNER = "owner"
    ADMIN = "admin"
    EDITOR = "editor"
    VIEWER = "viewer"
    ONCALL = "oncall"
    CUSTOM = "custom"


@dataclass(frozen=True)
class Permission:
    """A single permission."""

    resource: ResourceType
    action: Action
    scope: str = "*"

    def __str__(self) -> str:
        return f"{self.resource.value}:{self.action.value}:{self.scope}"

    @classmethod
    def from_string(cls, s: str) -> "Permission":
        """Parse permission from string format."""
        parts = s.split(":")
        if len(parts) < 2:
            raise ValueError(f"Invalid permission format: {s}")

        resource = ResourceType(parts[0])
        action = Action(parts[1])
        scope = parts[2] if len(parts) > 2 else "*"

        return cls(resource=resource, action=action, scope=scope)


@dataclass
class PermissionSet:
    """A set of permissions."""

    permissions: set[Permission] = field(default_factory=set)

    def add(self, permission: Permission) -> None:
        """Add a permission."""
        self.permissions.add(permission)

    def remove(self, permission: Permission) -> None:
        """Remove a permission."""
        self.permissions.discard(permission)

    def has(self, permission: Permission) -> bool:
        """Check if permission exists (including wildcards)."""
        if permission in self.permissions:
            return True

        for p in self.permissions:
            if p.resource == permission.resource:
                if p.action == Action.ADMIN:
                    return True
                if p.action == permission.action and p.scope == "*":
                    return True
                if p.scope == "*" and p.action == permission.action:
                    return True

        return False

    def has_any(self, *permissions: Permission) -> bool:
        """Check if any of the permissions exist."""
        return any(self.has(p) for p in permissions)

    def has_all(self, *permissions: Permission) -> bool:
        """Check if all permissions exist."""
        return all(self.has(p) for p in permissions)

    def to_list(self) -> list[str]:
        """Convert to list of permission strings."""
        return [str(p) for p in self.permissions]

    @classmethod
    def from_list(cls, permissions: list[str]) -> "PermissionSet":
        """Create from list of permission strings."""
        ps = cls()
        for p in permissions:
            ps.add(Permission.from_string(p))
        return ps


@dataclass
class Role:
    """A role definition."""

    id: str
    name: str
    role_type: RoleType
    description: str = ""
    permissions: PermissionSet = field(default_factory=PermissionSet)
    inherits_from: list[str] = field(default_factory=list)
    is_system: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def get_all_permissions(self, all_roles: dict[str, "Role"]) -> PermissionSet:
        """Get all permissions including inherited."""
        result = PermissionSet()
        result.permissions = self.permissions.permissions.copy()

        for parent_id in self.inherits_from:
            if parent_id in all_roles:
                parent_perms = all_roles[parent_id].get_all_permissions(all_roles)
                result.permissions.update(parent_perms.permissions)

        return result


def _create_permission(resource: ResourceType, action: Action) -> Permission:
    return Permission(resource=resource, action=action)


VIEWER_PERMISSIONS = PermissionSet()
for resource in [
    ResourceType.INVESTIGATION,
    ResourceType.RUNBOOK,
    ResourceType.ALERT,
    ResourceType.DASHBOARD,
    ResourceType.REPORT,
    ResourceType.POSTMORTEM,
    ResourceType.SLO,
    ResourceType.ONCALL,
]:
    VIEWER_PERMISSIONS.add(_create_permission(resource, Action.READ))


EDITOR_PERMISSIONS = PermissionSet()
EDITOR_PERMISSIONS.permissions = VIEWER_PERMISSIONS.permissions.copy()
for resource in [
    ResourceType.INVESTIGATION,
    ResourceType.RUNBOOK,
    ResourceType.DASHBOARD,
    ResourceType.REPORT,
    ResourceType.POSTMORTEM,
]:
    EDITOR_PERMISSIONS.add(_create_permission(resource, Action.CREATE))
    EDITOR_PERMISSIONS.add(_create_permission(resource, Action.UPDATE))
EDITOR_PERMISSIONS.add(_create_permission(ResourceType.RUNBOOK, Action.EXECUTE))


ONCALL_PERMISSIONS = PermissionSet()
ONCALL_PERMISSIONS.permissions = EDITOR_PERMISSIONS.permissions.copy()
ONCALL_PERMISSIONS.add(_create_permission(ResourceType.ALERT, Action.UPDATE))
ONCALL_PERMISSIONS.add(_create_permission(ResourceType.RUNBOOK, Action.EXECUTE))
ONCALL_PERMISSIONS.add(_create_permission(ResourceType.RUNBOOK, Action.APPROVE))
ONCALL_PERMISSIONS.add(_create_permission(ResourceType.INVESTIGATION, Action.APPROVE))


ADMIN_PERMISSIONS = PermissionSet()
for resource in ResourceType:
    if resource not in [ResourceType.BILLING, ResourceType.ORGANIZATION]:
        ADMIN_PERMISSIONS.add(_create_permission(resource, Action.ADMIN))
ADMIN_PERMISSIONS.add(_create_permission(ResourceType.USER, Action.ADMIN))
ADMIN_PERMISSIONS.add(_create_permission(ResourceType.TEAM, Action.ADMIN))
ADMIN_PERMISSIONS.add(_create_permission(ResourceType.INTEGRATION, Action.ADMIN))
ADMIN_PERMISSIONS.add(_create_permission(ResourceType.API_KEY, Action.ADMIN))
ADMIN_PERMISSIONS.add(_create_permission(ResourceType.WEBHOOK, Action.ADMIN))
ADMIN_PERMISSIONS.add(_create_permission(ResourceType.SETTINGS, Action.ADMIN))
ADMIN_PERMISSIONS.add(_create_permission(ResourceType.AUDIT_LOG, Action.READ))


OWNER_PERMISSIONS = PermissionSet()
for resource in ResourceType:
    OWNER_PERMISSIONS.add(_create_permission(resource, Action.ADMIN))


ROLE_PERMISSIONS: dict[RoleType, Role] = {
    RoleType.VIEWER: Role(
        id="viewer",
        name="Viewer",
        role_type=RoleType.VIEWER,
        description="Read-only access to most resources",
        permissions=VIEWER_PERMISSIONS,
        is_system=True,
    ),
    RoleType.EDITOR: Role(
        id="editor",
        name="Editor",
        role_type=RoleType.EDITOR,
        description="Can create and edit investigations, runbooks, and reports",
        permissions=EDITOR_PERMISSIONS,
        inherits_from=["viewer"],
        is_system=True,
    ),
    RoleType.ONCALL: Role(
        id="oncall",
        name="On-Call Engineer",
        role_type=RoleType.ONCALL,
        description="Editor permissions plus alert management and approvals",
        permissions=ONCALL_PERMISSIONS,
        inherits_from=["editor"],
        is_system=True,
    ),
    RoleType.ADMIN: Role(
        id="admin",
        name="Admin",
        role_type=RoleType.ADMIN,
        description="Full access except billing and org settings",
        permissions=ADMIN_PERMISSIONS,
        inherits_from=["oncall"],
        is_system=True,
    ),
    RoleType.OWNER: Role(
        id="owner",
        name="Owner",
        role_type=RoleType.OWNER,
        description="Full access including billing and org settings",
        permissions=OWNER_PERMISSIONS,
        inherits_from=["admin"],
        is_system=True,
    ),
}


def get_role_permissions(role_type: RoleType) -> PermissionSet:
    """Get permissions for a role type."""
    role = ROLE_PERMISSIONS.get(role_type)
    if not role:
        return PermissionSet()
    return role.permissions


def has_permission(
    user_permissions: PermissionSet,
    resource: ResourceType,
    action: Action,
    scope: str = "*",
) -> bool:
    """Check if user has a specific permission."""
    required = Permission(resource=resource, action=action, scope=scope)
    return user_permissions.has(required)


def check_permission(
    user_permissions: PermissionSet,
    resource: ResourceType,
    action: Action,
    scope: str = "*",
) -> None:
    """Check permission and raise if denied."""
    if not has_permission(user_permissions, resource, action, scope):
        raise PermissionError(
            f"Permission denied: {resource.value}:{action.value}:{scope}"
        )
