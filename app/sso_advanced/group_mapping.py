"""Group and Role Mapping.

Maps IdP groups to application roles.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("devcopilot.sso_advanced.group_mapping")


class AppRole(str, Enum):
    ADMIN = "admin"
    RESPONDER = "responder"
    VIEWER = "viewer"
    OWNER = "owner"
    BILLING = "billing"


@dataclass
class GroupMapping:
    """A mapping from IdP group to app role."""

    mapping_id: str
    org_id: str
    idp_group_name: str
    idp_group_id: str
    app_role: AppRole
    auto_sync: bool = True
    priority: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.mapping_id,
            "org_id": self.org_id,
            "idp_group": {
                "name": self.idp_group_name,
                "id": self.idp_group_id,
            },
            "app_role": self.app_role.value,
            "auto_sync": self.auto_sync,
            "priority": self.priority,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


@dataclass
class RoleMapping:
    """A user's role derived from group membership."""

    user_id: str
    email: str
    roles: list[AppRole]
    source_groups: list[str]
    last_synced: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "email": self.email,
            "roles": [r.value for r in self.roles],
            "source_groups": self.source_groups,
            "last_synced": self.last_synced.isoformat(),
        }


@dataclass
class GroupSyncResult:
    """Result of a group sync operation."""

    sync_id: str
    org_id: str
    users_updated: int
    roles_added: int
    roles_removed: int
    errors: list[str]
    started_at: datetime
    completed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def success(self) -> bool:
        return len(self.errors) == 0

    @property
    def duration_seconds(self) -> float:
        return (self.completed_at - self.started_at).total_seconds()

    def to_dict(self) -> dict[str, Any]:
        return {
            "sync_id": self.sync_id,
            "org_id": self.org_id,
            "users_updated": self.users_updated,
            "roles_added": self.roles_added,
            "roles_removed": self.roles_removed,
            "errors": self.errors,
            "success": self.success,
            "duration_seconds": round(self.duration_seconds, 2),
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat(),
        }


class GroupMapper:
    """Maps IdP groups to application roles."""

    def __init__(self) -> None:
        self._mappings: dict[str, list[GroupMapping]] = {}
        self._user_roles: dict[str, dict[str, RoleMapping]] = {}
        self._sync_history: dict[str, list[GroupSyncResult]] = {}

    def create_mapping(
        self,
        org_id: str,
        idp_group_name: str,
        idp_group_id: str,
        app_role: AppRole,
        auto_sync: bool = True,
        priority: int = 0,
    ) -> GroupMapping:
        """Create a new group-to-role mapping."""
        mapping_id = f"map_{org_id}_{idp_group_id}"

        mapping = GroupMapping(
            mapping_id=mapping_id,
            org_id=org_id,
            idp_group_name=idp_group_name,
            idp_group_id=idp_group_id,
            app_role=app_role,
            auto_sync=auto_sync,
            priority=priority,
        )

        if org_id not in self._mappings:
            self._mappings[org_id] = []

        existing = next(
            (m for m in self._mappings[org_id] if m.idp_group_id == idp_group_id),
            None,
        )
        if existing:
            self._mappings[org_id].remove(existing)

        self._mappings[org_id].append(mapping)

        self._mappings[org_id].sort(key=lambda m: m.priority, reverse=True)

        log.info(
            "Created group mapping: org=%s group=%s role=%s",
            org_id,
            idp_group_name,
            app_role.value,
        )

        return mapping

    def update_mapping(
        self,
        org_id: str,
        mapping_id: str,
        app_role: AppRole | None = None,
        auto_sync: bool | None = None,
        priority: int | None = None,
    ) -> GroupMapping | None:
        """Update an existing mapping."""
        mappings = self._mappings.get(org_id, [])
        mapping = next((m for m in mappings if m.mapping_id == mapping_id), None)

        if not mapping:
            return None

        if app_role is not None:
            mapping.app_role = app_role
        if auto_sync is not None:
            mapping.auto_sync = auto_sync
        if priority is not None:
            mapping.priority = priority

        mapping.updated_at = datetime.now(timezone.utc)

        mappings.sort(key=lambda m: m.priority, reverse=True)

        return mapping

    def delete_mapping(
        self,
        org_id: str,
        mapping_id: str,
    ) -> bool:
        """Delete a mapping."""
        mappings = self._mappings.get(org_id, [])
        mapping = next((m for m in mappings if m.mapping_id == mapping_id), None)

        if not mapping:
            return False

        mappings.remove(mapping)
        return True

    def get_mappings(self, org_id: str) -> list[GroupMapping]:
        """Get all mappings for an org."""
        return self._mappings.get(org_id, [])

    def resolve_roles(
        self,
        org_id: str,
        user_id: str,
        email: str,
        idp_groups: list[str],
    ) -> RoleMapping:
        """Resolve roles for a user based on their IdP groups."""
        mappings = self._mappings.get(org_id, [])

        roles: list[AppRole] = []
        source_groups: list[str] = []

        for group_id in idp_groups:
            for mapping in mappings:
                if mapping.idp_group_id == group_id:
                    if mapping.app_role not in roles:
                        roles.append(mapping.app_role)
                        source_groups.append(mapping.idp_group_name)

        if not roles:
            roles.append(AppRole.VIEWER)

        role_mapping = RoleMapping(
            user_id=user_id,
            email=email,
            roles=roles,
            source_groups=source_groups,
        )

        if org_id not in self._user_roles:
            self._user_roles[org_id] = {}
        self._user_roles[org_id][user_id] = role_mapping

        log.info(
            "Resolved roles: org=%s user=%s roles=%s",
            org_id,
            user_id,
            [r.value for r in roles],
        )

        return role_mapping

    def sync_all_users(
        self,
        org_id: str,
        user_groups: dict[str, list[str]],
        user_emails: dict[str, str],
    ) -> GroupSyncResult:
        """Sync roles for all users based on current group membership."""
        sync_id = f"sync_{org_id}_{datetime.now(timezone.utc).timestamp()}"
        started_at = datetime.now(timezone.utc)

        users_updated = 0
        roles_added = 0
        roles_removed = 0
        errors: list[str] = []

        existing_roles = self._user_roles.get(org_id, {})

        for user_id, groups in user_groups.items():
            try:
                email = user_emails.get(user_id, "")
                old_mapping = existing_roles.get(user_id)

                new_mapping = self.resolve_roles(org_id, user_id, email, groups)

                users_updated += 1

                if old_mapping:
                    old_roles = set(old_mapping.roles)
                    new_roles = set(new_mapping.roles)

                    roles_added += len(new_roles - old_roles)
                    roles_removed += len(old_roles - new_roles)
                else:
                    roles_added += len(new_mapping.roles)

            except Exception as e:
                errors.append(f"Failed to sync user {user_id}: {e}")

        result = GroupSyncResult(
            sync_id=sync_id,
            org_id=org_id,
            users_updated=users_updated,
            roles_added=roles_added,
            roles_removed=roles_removed,
            errors=errors,
            started_at=started_at,
        )

        if org_id not in self._sync_history:
            self._sync_history[org_id] = []
        self._sync_history[org_id].append(result)

        if len(self._sync_history[org_id]) > 100:
            self._sync_history[org_id] = self._sync_history[org_id][-50:]

        log.info(
            "Completed group sync: org=%s users=%d added=%d removed=%d errors=%d",
            org_id,
            users_updated,
            roles_added,
            roles_removed,
            len(errors),
        )

        return result

    def get_user_roles(
        self,
        org_id: str,
        user_id: str,
    ) -> RoleMapping | None:
        """Get current role mapping for a user."""
        user_roles = self._user_roles.get(org_id, {})
        return user_roles.get(user_id)

    def get_users_with_role(
        self,
        org_id: str,
        role: AppRole,
    ) -> list[RoleMapping]:
        """Get all users with a specific role."""
        user_roles = self._user_roles.get(org_id, {})
        return [
            mapping for mapping in user_roles.values()
            if role in mapping.roles
        ]

    def get_sync_history(
        self,
        org_id: str,
        limit: int = 10,
    ) -> list[GroupSyncResult]:
        """Get recent sync history."""
        history = self._sync_history.get(org_id, [])
        return history[-limit:]

    def get_role_summary(
        self,
        org_id: str,
    ) -> dict[str, Any]:
        """Get summary of role assignments."""
        user_roles = self._user_roles.get(org_id, {})

        by_role: dict[str, int] = {role.value: 0 for role in AppRole}
        for mapping in user_roles.values():
            for role in mapping.roles:
                by_role[role.value] += 1

        return {
            "total_users": len(user_roles),
            "by_role": by_role,
            "mappings": len(self._mappings.get(org_id, [])),
        }


_mapper: GroupMapper | None = None


def get_group_mapper() -> GroupMapper:
    """Get the default group mapper."""
    global _mapper
    if _mapper is None:
        _mapper = GroupMapper()
    return _mapper
