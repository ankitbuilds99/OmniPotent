"""RBAC middleware for FastAPI.

Provides:
- Permission checking decorators
- Request-level permission context
- Role requirement enforcement
"""

from __future__ import annotations

import functools
import logging
from contextvars import ContextVar
from typing import Any, Callable, TypeVar

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware

from app.rbac.permissions import (
    PermissionSet,
    ResourceType,
    Action,
    RoleType,
    has_permission,
)
from app.rbac.manager import RBACManager

log = logging.getLogger("omnipotent.rbac.middleware")

_current_permissions: ContextVar[PermissionSet] = ContextVar(
    "current_permissions", default=PermissionSet()
)
_current_user_id: ContextVar[str] = ContextVar("current_user_id", default="")
_current_org_id: ContextVar[str] = ContextVar("current_org_id", default="")
_current_role: ContextVar[RoleType | None] = ContextVar("current_role", default=None)


def get_current_permissions() -> PermissionSet:
    """Get permissions for current request context."""
    return _current_permissions.get()


def get_current_user_id() -> str:
    """Get current user ID."""
    return _current_user_id.get()


def get_current_org_id() -> str:
    """Get current org ID."""
    return _current_org_id.get()


def get_current_role() -> RoleType | None:
    """Get current user's highest role."""
    return _current_role.get()


def set_permission_context(
    user_id: str,
    org_id: str,
    permissions: PermissionSet,
    role: RoleType | None = None,
) -> None:
    """Set permission context for current request."""
    _current_user_id.set(user_id)
    _current_org_id.set(org_id)
    _current_permissions.set(permissions)
    _current_role.set(role)


def clear_permission_context() -> None:
    """Clear permission context."""
    _current_user_id.set("")
    _current_org_id.set("")
    _current_permissions.set(PermissionSet())
    _current_role.set(None)


class RBACMiddleware(BaseHTTPMiddleware):
    """Middleware to load user permissions into request context."""

    async def dispatch(self, request: Request, call_next: Any) -> Any:
        user_id = getattr(request.state, "user_id", None)
        org_id = getattr(request.state, "org_id", None)

        if user_id and org_id:
            manager = RBACManager(org_id)
            permissions = manager.get_user_permissions(user_id)
            role = manager.get_highest_role(user_id)
            set_permission_context(user_id, org_id, permissions, role)
        else:
            clear_permission_context()

        try:
            response = await call_next(request)
            return response
        finally:
            clear_permission_context()


F = TypeVar("F", bound=Callable[..., Any])


def require_permission(
    resource: ResourceType,
    action: Action,
    scope: str = "*",
) -> Callable[[F], F]:
    """Decorator to require a specific permission."""

    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            permissions = get_current_permissions()

            if not has_permission(permissions, resource, action, scope):
                log.warning(
                    "Permission denied: user=%s resource=%s action=%s",
                    get_current_user_id(),
                    resource.value,
                    action.value,
                )
                raise HTTPException(
                    status_code=403,
                    detail=f"Permission denied: {resource.value}:{action.value}",
                )

            return await func(*args, **kwargs)

        return wrapper  # type: ignore

    return decorator


def require_role(*roles: RoleType) -> Callable[[F], F]:
    """Decorator to require one of the specified roles."""

    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            current_role = get_current_role()

            if current_role is None:
                raise HTTPException(status_code=401, detail="Not authenticated")

            role_hierarchy = {
                RoleType.OWNER: 5,
                RoleType.ADMIN: 4,
                RoleType.ONCALL: 3,
                RoleType.EDITOR: 2,
                RoleType.VIEWER: 1,
                RoleType.CUSTOM: 0,
            }

            current_level = role_hierarchy.get(current_role, 0)
            required_level = max(role_hierarchy.get(r, 0) for r in roles)

            if current_level < required_level:
                log.warning(
                    "Role denied: user=%s has=%s required=%s",
                    get_current_user_id(),
                    current_role.value,
                    [r.value for r in roles],
                )
                raise HTTPException(
                    status_code=403,
                    detail=f"Required role: {', '.join(r.value for r in roles)}",
                )

            return await func(*args, **kwargs)

        return wrapper  # type: ignore

    return decorator


def require_any_permission(*permissions: tuple[ResourceType, Action]) -> Callable[[F], F]:
    """Decorator to require any of the specified permissions."""

    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            user_permissions = get_current_permissions()

            for resource, action in permissions:
                if has_permission(user_permissions, resource, action):
                    return await func(*args, **kwargs)

            raise HTTPException(
                status_code=403,
                detail="Permission denied",
            )

        return wrapper  # type: ignore

    return decorator


def require_owner_or_admin(func: F) -> F:
    """Decorator requiring owner or admin role."""
    return require_role(RoleType.OWNER, RoleType.ADMIN)(func)


def require_editor_or_above(func: F) -> F:
    """Decorator requiring at least editor role."""
    return require_role(RoleType.OWNER, RoleType.ADMIN, RoleType.ONCALL, RoleType.EDITOR)(func)


class PermissionChecker:
    """Helper class for checking permissions in code."""

    def __init__(self, permissions: PermissionSet | None = None):
        self.permissions = permissions or get_current_permissions()

    def can(self, resource: ResourceType, action: Action, scope: str = "*") -> bool:
        """Check if action is allowed on resource."""
        return has_permission(self.permissions, resource, action, scope)

    def can_read(self, resource: ResourceType) -> bool:
        """Check read permission."""
        return self.can(resource, Action.READ)

    def can_create(self, resource: ResourceType) -> bool:
        """Check create permission."""
        return self.can(resource, Action.CREATE)

    def can_update(self, resource: ResourceType) -> bool:
        """Check update permission."""
        return self.can(resource, Action.UPDATE)

    def can_delete(self, resource: ResourceType) -> bool:
        """Check delete permission."""
        return self.can(resource, Action.DELETE)

    def can_execute(self, resource: ResourceType) -> bool:
        """Check execute permission."""
        return self.can(resource, Action.EXECUTE)

    def can_admin(self, resource: ResourceType) -> bool:
        """Check admin permission."""
        return self.can(resource, Action.ADMIN)

    def require(self, resource: ResourceType, action: Action, scope: str = "*") -> None:
        """Require permission or raise HTTPException."""
        if not self.can(resource, action, scope):
            raise HTTPException(
                status_code=403,
                detail=f"Permission denied: {resource.value}:{action.value}",
            )
