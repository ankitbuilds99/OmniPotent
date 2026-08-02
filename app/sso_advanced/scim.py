"""SCIM 2.0 User Provisioning.

Automatic user creation, updates, and deactivation via SCIM.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("omnipotent.sso_advanced.scim")


class SCIMOperation(str, Enum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    ACTIVATE = "activate"
    DEACTIVATE = "deactivate"


class ProvisioningStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class SCIMUser:
    """A user provisioned via SCIM."""

    scim_id: str
    external_id: str
    username: str
    email: str
    first_name: str
    last_name: str
    display_name: str
    active: bool
    groups: list[str] = field(default_factory=list)
    roles: list[str] = field(default_factory=list)
    attributes: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_login: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.scim_id,
            "externalId": self.external_id,
            "userName": self.username,
            "name": {
                "givenName": self.first_name,
                "familyName": self.last_name,
            },
            "displayName": self.display_name,
            "emails": [{"value": self.email, "primary": True}],
            "active": self.active,
            "groups": [{"value": g} for g in self.groups],
            "roles": self.roles,
            "meta": {
                "created": self.created_at.isoformat(),
                "lastModified": self.updated_at.isoformat(),
            },
        }

    def to_scim_response(self) -> dict[str, Any]:
        """Format as SCIM 2.0 response."""
        return {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
            "id": self.scim_id,
            "externalId": self.external_id,
            "userName": self.username,
            "name": {
                "givenName": self.first_name,
                "familyName": self.last_name,
                "formatted": self.display_name,
            },
            "displayName": self.display_name,
            "emails": [
                {"value": self.email, "primary": True, "type": "work"},
            ],
            "active": self.active,
            "groups": [
                {"value": g, "$ref": f"/scim/v2/Groups/{g}", "display": g}
                for g in self.groups
            ],
            "meta": {
                "resourceType": "User",
                "created": self.created_at.isoformat(),
                "lastModified": self.updated_at.isoformat(),
                "location": f"/scim/v2/Users/{self.scim_id}",
            },
        }


@dataclass
class SCIMGroup:
    """A group provisioned via SCIM."""

    scim_id: str
    external_id: str
    display_name: str
    members: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.scim_id,
            "externalId": self.external_id,
            "displayName": self.display_name,
            "members": self.members,
            "meta": {
                "created": self.created_at.isoformat(),
                "lastModified": self.updated_at.isoformat(),
            },
        }

    def to_scim_response(self) -> dict[str, Any]:
        """Format as SCIM 2.0 response."""
        return {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:Group"],
            "id": self.scim_id,
            "externalId": self.external_id,
            "displayName": self.display_name,
            "members": [
                {"value": m, "$ref": f"/scim/v2/Users/{m}"}
                for m in self.members
            ],
            "meta": {
                "resourceType": "Group",
                "created": self.created_at.isoformat(),
                "lastModified": self.updated_at.isoformat(),
                "location": f"/scim/v2/Groups/{self.scim_id}",
            },
        }


@dataclass
class SCIMProvisioningEvent:
    """A provisioning event."""

    event_id: str
    org_id: str
    operation: SCIMOperation
    resource_type: str
    resource_id: str
    status: ProvisioningStatus
    details: dict[str, Any]
    error: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "org_id": self.org_id,
            "operation": self.operation.value,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "status": self.status.value,
            "details": self.details,
            "error": self.error,
            "created_at": self.created_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }


class SCIMProvider:
    """SCIM 2.0 provisioning provider."""

    def __init__(self) -> None:
        self._users: dict[str, dict[str, SCIMUser]] = {}
        self._groups: dict[str, dict[str, SCIMGroup]] = {}
        self._events: dict[str, list[SCIMProvisioningEvent]] = {}
        self._bearer_tokens: dict[str, str] = {}

    def configure_tenant(
        self,
        org_id: str,
        bearer_token: str,
    ) -> dict[str, Any]:
        """Configure SCIM for a tenant."""
        self._bearer_tokens[org_id] = bearer_token

        if org_id not in self._users:
            self._users[org_id] = {}
        if org_id not in self._groups:
            self._groups[org_id] = {}
        if org_id not in self._events:
            self._events[org_id] = []

        log.info("Configured SCIM for org: org_id=%s", org_id)

        return {
            "scim_endpoint": f"https://api.devopscopilot.io/scim/v2/{org_id}",
            "bearer_token": f"{bearer_token[:8]}...",
            "status": "configured",
        }

    def validate_token(
        self,
        org_id: str,
        token: str,
    ) -> bool:
        """Validate a bearer token."""
        expected = self._bearer_tokens.get(org_id)
        return expected is not None and expected == token

    def create_user(
        self,
        org_id: str,
        external_id: str,
        username: str,
        email: str,
        first_name: str,
        last_name: str,
        groups: list[str] | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> SCIMUser:
        """Create a user via SCIM."""
        scim_id = f"user_{external_id}"

        user = SCIMUser(
            scim_id=scim_id,
            external_id=external_id,
            username=username,
            email=email,
            first_name=first_name,
            last_name=last_name,
            display_name=f"{first_name} {last_name}",
            active=True,
            groups=groups or [],
            attributes=attributes or {},
        )

        if org_id not in self._users:
            self._users[org_id] = {}
        self._users[org_id][scim_id] = user

        self._record_event(
            org_id,
            SCIMOperation.CREATE,
            "User",
            scim_id,
            {"email": email, "groups": groups},
        )

        log.info(
            "Created SCIM user: org=%s user=%s email=%s",
            org_id,
            scim_id,
            email,
        )

        return user

    def update_user(
        self,
        org_id: str,
        scim_id: str,
        updates: dict[str, Any],
    ) -> SCIMUser | None:
        """Update a user via SCIM."""
        users = self._users.get(org_id, {})
        user = users.get(scim_id)

        if not user:
            return None

        if "email" in updates:
            user.email = updates["email"]
        if "firstName" in updates:
            user.first_name = updates["firstName"]
        if "lastName" in updates:
            user.last_name = updates["lastName"]
        if "displayName" in updates:
            user.display_name = updates["displayName"]
        if "active" in updates:
            user.active = updates["active"]
        if "groups" in updates:
            user.groups = updates["groups"]

        user.updated_at = datetime.now(timezone.utc)

        self._record_event(
            org_id,
            SCIMOperation.UPDATE,
            "User",
            scim_id,
            updates,
        )

        log.info(
            "Updated SCIM user: org=%s user=%s",
            org_id,
            scim_id,
        )

        return user

    def deactivate_user(
        self,
        org_id: str,
        scim_id: str,
    ) -> SCIMUser | None:
        """Deactivate a user via SCIM."""
        users = self._users.get(org_id, {})
        user = users.get(scim_id)

        if not user:
            return None

        user.active = False
        user.updated_at = datetime.now(timezone.utc)

        self._record_event(
            org_id,
            SCIMOperation.DEACTIVATE,
            "User",
            scim_id,
            {"active": False},
        )

        log.info(
            "Deactivated SCIM user: org=%s user=%s",
            org_id,
            scim_id,
        )

        return user

    def delete_user(
        self,
        org_id: str,
        scim_id: str,
    ) -> bool:
        """Delete a user via SCIM."""
        users = self._users.get(org_id, {})

        if scim_id not in users:
            return False

        del users[scim_id]

        self._record_event(
            org_id,
            SCIMOperation.DELETE,
            "User",
            scim_id,
            {},
        )

        log.info(
            "Deleted SCIM user: org=%s user=%s",
            org_id,
            scim_id,
        )

        return True

    def get_user(
        self,
        org_id: str,
        scim_id: str,
    ) -> SCIMUser | None:
        """Get a user by SCIM ID."""
        users = self._users.get(org_id, {})
        return users.get(scim_id)

    def get_user_by_email(
        self,
        org_id: str,
        email: str,
    ) -> SCIMUser | None:
        """Get a user by email."""
        users = self._users.get(org_id, {})
        for user in users.values():
            if user.email.lower() == email.lower():
                return user
        return None

    def list_users(
        self,
        org_id: str,
        start_index: int = 1,
        count: int = 100,
        filter_str: str = "",
    ) -> dict[str, Any]:
        """List users with SCIM pagination."""
        users = list(self._users.get(org_id, {}).values())

        if filter_str:
            if "userName eq" in filter_str:
                value = filter_str.split('"')[1]
                users = [u for u in users if u.username == value]
            elif "email eq" in filter_str:
                value = filter_str.split('"')[1]
                users = [u for u in users if u.email.lower() == value.lower()]

        total = len(users)
        start = start_index - 1
        end = start + count
        page = users[start:end]

        return {
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
            "totalResults": total,
            "startIndex": start_index,
            "itemsPerPage": len(page),
            "Resources": [u.to_scim_response() for u in page],
        }

    def create_group(
        self,
        org_id: str,
        external_id: str,
        display_name: str,
        members: list[str] | None = None,
    ) -> SCIMGroup:
        """Create a group via SCIM."""
        scim_id = f"group_{external_id}"

        group = SCIMGroup(
            scim_id=scim_id,
            external_id=external_id,
            display_name=display_name,
            members=members or [],
        )

        if org_id not in self._groups:
            self._groups[org_id] = {}
        self._groups[org_id][scim_id] = group

        self._record_event(
            org_id,
            SCIMOperation.CREATE,
            "Group",
            scim_id,
            {"display_name": display_name, "members": members},
        )

        log.info(
            "Created SCIM group: org=%s group=%s name=%s",
            org_id,
            scim_id,
            display_name,
        )

        return group

    def update_group(
        self,
        org_id: str,
        scim_id: str,
        updates: dict[str, Any],
    ) -> SCIMGroup | None:
        """Update a group via SCIM."""
        groups = self._groups.get(org_id, {})
        group = groups.get(scim_id)

        if not group:
            return None

        if "displayName" in updates:
            group.display_name = updates["displayName"]
        if "members" in updates:
            group.members = updates["members"]

        group.updated_at = datetime.now(timezone.utc)

        self._record_event(
            org_id,
            SCIMOperation.UPDATE,
            "Group",
            scim_id,
            updates,
        )

        return group

    def add_member_to_group(
        self,
        org_id: str,
        group_id: str,
        user_id: str,
    ) -> bool:
        """Add a member to a group."""
        groups = self._groups.get(org_id, {})
        group = groups.get(group_id)

        if not group:
            return False

        if user_id not in group.members:
            group.members.append(user_id)
            group.updated_at = datetime.now(timezone.utc)

        users = self._users.get(org_id, {})
        user = users.get(user_id)
        if user and group_id not in user.groups:
            user.groups.append(group_id)

        return True

    def remove_member_from_group(
        self,
        org_id: str,
        group_id: str,
        user_id: str,
    ) -> bool:
        """Remove a member from a group."""
        groups = self._groups.get(org_id, {})
        group = groups.get(group_id)

        if not group:
            return False

        if user_id in group.members:
            group.members.remove(user_id)
            group.updated_at = datetime.now(timezone.utc)

        users = self._users.get(org_id, {})
        user = users.get(user_id)
        if user and group_id in user.groups:
            user.groups.remove(group_id)

        return True

    def get_group(
        self,
        org_id: str,
        scim_id: str,
    ) -> SCIMGroup | None:
        """Get a group by SCIM ID."""
        groups = self._groups.get(org_id, {})
        return groups.get(scim_id)

    def list_groups(
        self,
        org_id: str,
        start_index: int = 1,
        count: int = 100,
    ) -> dict[str, Any]:
        """List groups with SCIM pagination."""
        groups = list(self._groups.get(org_id, {}).values())

        total = len(groups)
        start = start_index - 1
        end = start + count
        page = groups[start:end]

        return {
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
            "totalResults": total,
            "startIndex": start_index,
            "itemsPerPage": len(page),
            "Resources": [g.to_scim_response() for g in page],
        }

    def _record_event(
        self,
        org_id: str,
        operation: SCIMOperation,
        resource_type: str,
        resource_id: str,
        details: dict[str, Any],
    ) -> SCIMProvisioningEvent:
        """Record a provisioning event."""
        event = SCIMProvisioningEvent(
            event_id=f"event_{datetime.now(timezone.utc).timestamp()}",
            org_id=org_id,
            operation=operation,
            resource_type=resource_type,
            resource_id=resource_id,
            status=ProvisioningStatus.COMPLETED,
            details=details,
            completed_at=datetime.now(timezone.utc),
        )

        if org_id not in self._events:
            self._events[org_id] = []
        self._events[org_id].append(event)

        if len(self._events[org_id]) > 1000:
            self._events[org_id] = self._events[org_id][-500:]

        return event

    def get_provisioning_stats(
        self,
        org_id: str,
        days: int = 30,
    ) -> dict[str, Any]:
        """Get provisioning statistics."""
        events = self._events.get(org_id, [])
        users = self._users.get(org_id, {})
        groups = self._groups.get(org_id, {})

        created = sum(1 for e in events if e.operation == SCIMOperation.CREATE)
        updated = sum(1 for e in events if e.operation == SCIMOperation.UPDATE)
        deactivated = sum(1 for e in events if e.operation == SCIMOperation.DEACTIVATE)
        errors = sum(1 for e in events if e.status == ProvisioningStatus.FAILED)

        return {
            "users": {
                "total": len(users),
                "active": sum(1 for u in users.values() if u.active),
                "inactive": sum(1 for u in users.values() if not u.active),
            },
            "groups": {
                "total": len(groups),
            },
            "events": {
                "created": created,
                "updated": updated,
                "deactivated": deactivated,
                "errors": errors,
            },
        }


_provider: SCIMProvider | None = None


def get_scim_provider() -> SCIMProvider:
    """Get the default SCIM provider."""
    global _provider
    if _provider is None:
        _provider = SCIMProvider()
    return _provider
