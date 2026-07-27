"""Role-Based Access Control (RBAC) system.

Provides:
- Role definitions with hierarchical permissions
- Resource-level access control
- Permission checking middleware
- Team and organization scoping
"""

from app.rbac.permissions import (
    Permission,
    Role,
    RoleType,
    ResourceType,
    Action,
    PermissionSet,
    ROLE_PERMISSIONS,
    has_permission,
    check_permission,
    get_role_permissions,
)
from app.rbac.manager import (
    RBACManager,
    UserRole,
    TeamMembership,
    assign_role,
    revoke_role,
    get_user_roles,
    get_team_members,
    create_team,
    add_team_member,
    remove_team_member,
)
from app.rbac.middleware import (
    require_permission,
    require_role,
    RBACMiddleware,
    get_current_permissions,
)

__all__ = [
    "Permission",
    "Role",
    "RoleType",
    "ResourceType",
    "Action",
    "PermissionSet",
    "ROLE_PERMISSIONS",
    "has_permission",
    "check_permission",
    "get_role_permissions",
    "RBACManager",
    "UserRole",
    "TeamMembership",
    "assign_role",
    "revoke_role",
    "get_user_roles",
    "get_team_members",
    "create_team",
    "add_team_member",
    "remove_team_member",
    "require_permission",
    "require_role",
    "RBACMiddleware",
    "get_current_permissions",
]
