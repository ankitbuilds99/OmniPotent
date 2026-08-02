"""Audit logging implementation.

Tracks all significant actions for:
- Security compliance (SOC2, HIPAA, etc.)
- Debugging and troubleshooting
- User activity monitoring
- Change tracking
"""

from __future__ import annotations

import json
import logging
import secrets
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("omnipotent.audit")


class AuditAction(Enum):
    """Types of auditable actions."""

    LOGIN = "login"
    LOGOUT = "logout"
    LOGIN_FAILED = "login_failed"
    PASSWORD_CHANGE = "password_change"
    MFA_ENABLED = "mfa_enabled"
    MFA_DISABLED = "mfa_disabled"

    USER_CREATE = "user_create"
    USER_UPDATE = "user_update"
    USER_DELETE = "user_delete"
    USER_INVITE = "user_invite"

    ROLE_ASSIGN = "role_assign"
    ROLE_REVOKE = "role_revoke"
    PERMISSION_CHANGE = "permission_change"

    TEAM_CREATE = "team_create"
    TEAM_UPDATE = "team_update"
    TEAM_DELETE = "team_delete"
    TEAM_MEMBER_ADD = "team_member_add"
    TEAM_MEMBER_REMOVE = "team_member_remove"

    INVESTIGATION_CREATE = "investigation_create"
    INVESTIGATION_UPDATE = "investigation_update"
    INVESTIGATION_DELETE = "investigation_delete"
    INVESTIGATION_COMPLETE = "investigation_complete"

    RUNBOOK_CREATE = "runbook_create"
    RUNBOOK_UPDATE = "runbook_update"
    RUNBOOK_DELETE = "runbook_delete"
    RUNBOOK_EXECUTE = "runbook_execute"
    RUNBOOK_APPROVE = "runbook_approve"

    ALERT_CREATE = "alert_create"
    ALERT_UPDATE = "alert_update"
    ALERT_ACKNOWLEDGE = "alert_acknowledge"
    ALERT_RESOLVE = "alert_resolve"
    ALERT_ESCALATE = "alert_escalate"

    INTEGRATION_CREATE = "integration_create"
    INTEGRATION_UPDATE = "integration_update"
    INTEGRATION_DELETE = "integration_delete"
    INTEGRATION_SYNC = "integration_sync"

    API_KEY_CREATE = "api_key_create"
    API_KEY_REVOKE = "api_key_revoke"

    WEBHOOK_CREATE = "webhook_create"
    WEBHOOK_UPDATE = "webhook_update"
    WEBHOOK_DELETE = "webhook_delete"

    CONFIG_UPDATE = "config_update"
    SETTINGS_CHANGE = "settings_change"

    DATA_EXPORT = "data_export"
    DATA_DELETE = "data_delete"
    BULK_ACTION = "bulk_action"

    SECURITY_VIOLATION = "security_violation"
    RATE_LIMIT_EXCEEDED = "rate_limit_exceeded"
    SUSPICIOUS_ACTIVITY = "suspicious_activity"


class AuditCategory(Enum):
    """Categories for grouping audit actions."""

    AUTHENTICATION = "authentication"
    AUTHORIZATION = "authorization"
    USER_MANAGEMENT = "user_management"
    TEAM_MANAGEMENT = "team_management"
    INVESTIGATION = "investigation"
    RUNBOOK = "runbook"
    ALERT = "alert"
    INTEGRATION = "integration"
    API = "api"
    CONFIG = "config"
    DATA = "data"
    SECURITY = "security"


class AuditSeverity(Enum):
    """Severity levels for audit entries."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


ACTION_CATEGORIES: dict[AuditAction, AuditCategory] = {
    AuditAction.LOGIN: AuditCategory.AUTHENTICATION,
    AuditAction.LOGOUT: AuditCategory.AUTHENTICATION,
    AuditAction.LOGIN_FAILED: AuditCategory.AUTHENTICATION,
    AuditAction.PASSWORD_CHANGE: AuditCategory.AUTHENTICATION,
    AuditAction.MFA_ENABLED: AuditCategory.AUTHENTICATION,
    AuditAction.MFA_DISABLED: AuditCategory.AUTHENTICATION,
    AuditAction.USER_CREATE: AuditCategory.USER_MANAGEMENT,
    AuditAction.USER_UPDATE: AuditCategory.USER_MANAGEMENT,
    AuditAction.USER_DELETE: AuditCategory.USER_MANAGEMENT,
    AuditAction.USER_INVITE: AuditCategory.USER_MANAGEMENT,
    AuditAction.ROLE_ASSIGN: AuditCategory.AUTHORIZATION,
    AuditAction.ROLE_REVOKE: AuditCategory.AUTHORIZATION,
    AuditAction.PERMISSION_CHANGE: AuditCategory.AUTHORIZATION,
    AuditAction.TEAM_CREATE: AuditCategory.TEAM_MANAGEMENT,
    AuditAction.TEAM_UPDATE: AuditCategory.TEAM_MANAGEMENT,
    AuditAction.TEAM_DELETE: AuditCategory.TEAM_MANAGEMENT,
    AuditAction.TEAM_MEMBER_ADD: AuditCategory.TEAM_MANAGEMENT,
    AuditAction.TEAM_MEMBER_REMOVE: AuditCategory.TEAM_MANAGEMENT,
    AuditAction.INVESTIGATION_CREATE: AuditCategory.INVESTIGATION,
    AuditAction.INVESTIGATION_UPDATE: AuditCategory.INVESTIGATION,
    AuditAction.INVESTIGATION_DELETE: AuditCategory.INVESTIGATION,
    AuditAction.INVESTIGATION_COMPLETE: AuditCategory.INVESTIGATION,
    AuditAction.RUNBOOK_CREATE: AuditCategory.RUNBOOK,
    AuditAction.RUNBOOK_UPDATE: AuditCategory.RUNBOOK,
    AuditAction.RUNBOOK_DELETE: AuditCategory.RUNBOOK,
    AuditAction.RUNBOOK_EXECUTE: AuditCategory.RUNBOOK,
    AuditAction.RUNBOOK_APPROVE: AuditCategory.RUNBOOK,
    AuditAction.ALERT_CREATE: AuditCategory.ALERT,
    AuditAction.ALERT_UPDATE: AuditCategory.ALERT,
    AuditAction.ALERT_ACKNOWLEDGE: AuditCategory.ALERT,
    AuditAction.ALERT_RESOLVE: AuditCategory.ALERT,
    AuditAction.ALERT_ESCALATE: AuditCategory.ALERT,
    AuditAction.INTEGRATION_CREATE: AuditCategory.INTEGRATION,
    AuditAction.INTEGRATION_UPDATE: AuditCategory.INTEGRATION,
    AuditAction.INTEGRATION_DELETE: AuditCategory.INTEGRATION,
    AuditAction.INTEGRATION_SYNC: AuditCategory.INTEGRATION,
    AuditAction.API_KEY_CREATE: AuditCategory.API,
    AuditAction.API_KEY_REVOKE: AuditCategory.API,
    AuditAction.WEBHOOK_CREATE: AuditCategory.API,
    AuditAction.WEBHOOK_UPDATE: AuditCategory.API,
    AuditAction.WEBHOOK_DELETE: AuditCategory.API,
    AuditAction.CONFIG_UPDATE: AuditCategory.CONFIG,
    AuditAction.SETTINGS_CHANGE: AuditCategory.CONFIG,
    AuditAction.DATA_EXPORT: AuditCategory.DATA,
    AuditAction.DATA_DELETE: AuditCategory.DATA,
    AuditAction.BULK_ACTION: AuditCategory.DATA,
    AuditAction.SECURITY_VIOLATION: AuditCategory.SECURITY,
    AuditAction.RATE_LIMIT_EXCEEDED: AuditCategory.SECURITY,
    AuditAction.SUSPICIOUS_ACTIVITY: AuditCategory.SECURITY,
}


@dataclass
class AuditEntry:
    """A single audit log entry."""

    id: str
    timestamp: datetime
    action: AuditAction
    category: AuditCategory
    severity: AuditSeverity
    actor_id: str
    actor_type: str = "user"
    actor_ip: str = ""
    actor_user_agent: str = ""
    org_id: str = ""
    team_id: str = ""
    resource_type: str = ""
    resource_id: str = ""
    resource_name: str = ""
    description: str = ""
    old_value: dict[str, Any] | None = None
    new_value: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    request_id: str = ""
    session_id: str = ""
    success: bool = True
    error_message: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "timestamp": self.timestamp.isoformat(),
            "action": self.action.value,
            "category": self.category.value,
            "severity": self.severity.value,
            "actor_id": self.actor_id,
            "actor_type": self.actor_type,
            "actor_ip": self.actor_ip,
            "org_id": self.org_id,
            "team_id": self.team_id,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "resource_name": self.resource_name,
            "description": self.description,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "metadata": self.metadata,
            "request_id": self.request_id,
            "success": self.success,
            "error_message": self.error_message,
        }

    def to_json(self) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict(), default=str)


@dataclass
class AuditFilter:
    """Filter for searching audit logs."""

    start_time: datetime | None = None
    end_time: datetime | None = None
    actions: list[AuditAction] | None = None
    categories: list[AuditCategory] | None = None
    severities: list[AuditSeverity] | None = None
    actor_id: str | None = None
    org_id: str | None = None
    team_id: str | None = None
    resource_type: str | None = None
    resource_id: str | None = None
    success: bool | None = None
    search_query: str = ""
    limit: int = 1000
    offset: int = 0


class AuditLogger:
    """Audit log manager."""

    def __init__(self, buffer_size: int = 100000):
        self.buffer_size = buffer_size
        self._entries: list[AuditEntry] = []

    def log(
        self,
        action: AuditAction,
        actor_id: str,
        severity: AuditSeverity = AuditSeverity.INFO,
        org_id: str = "",
        resource_type: str = "",
        resource_id: str = "",
        resource_name: str = "",
        description: str = "",
        old_value: dict[str, Any] | None = None,
        new_value: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        actor_ip: str = "",
        actor_user_agent: str = "",
        request_id: str = "",
        session_id: str = "",
        success: bool = True,
        error_message: str = "",
        team_id: str = "",
    ) -> AuditEntry:
        """Create an audit log entry."""
        entry = AuditEntry(
            id=f"audit_{secrets.token_hex(12)}",
            timestamp=datetime.now(timezone.utc),
            action=action,
            category=ACTION_CATEGORIES.get(action, AuditCategory.DATA),
            severity=severity,
            actor_id=actor_id,
            actor_ip=actor_ip,
            actor_user_agent=actor_user_agent,
            org_id=org_id,
            team_id=team_id,
            resource_type=resource_type,
            resource_id=resource_id,
            resource_name=resource_name,
            description=description,
            old_value=old_value,
            new_value=new_value,
            metadata=metadata or {},
            request_id=request_id,
            session_id=session_id,
            success=success,
            error_message=error_message,
        )

        self._entries.append(entry)

        if len(self._entries) > self.buffer_size:
            self._entries = self._entries[-self.buffer_size:]

        log.info(
            "AUDIT: %s | actor=%s | resource=%s/%s | success=%s",
            action.value,
            actor_id,
            resource_type,
            resource_id,
            success,
        )

        return entry

    def search(self, filter: AuditFilter) -> list[AuditEntry]:
        """Search audit logs with filters."""
        results = self._entries

        if filter.start_time:
            results = [e for e in results if e.timestamp >= filter.start_time]

        if filter.end_time:
            results = [e for e in results if e.timestamp <= filter.end_time]

        if filter.actions:
            results = [e for e in results if e.action in filter.actions]

        if filter.categories:
            results = [e for e in results if e.category in filter.categories]

        if filter.severities:
            results = [e for e in results if e.severity in filter.severities]

        if filter.actor_id:
            results = [e for e in results if e.actor_id == filter.actor_id]

        if filter.org_id:
            results = [e for e in results if e.org_id == filter.org_id]

        if filter.team_id:
            results = [e for e in results if e.team_id == filter.team_id]

        if filter.resource_type:
            results = [e for e in results if e.resource_type == filter.resource_type]

        if filter.resource_id:
            results = [e for e in results if e.resource_id == filter.resource_id]

        if filter.success is not None:
            results = [e for e in results if e.success == filter.success]

        if filter.search_query:
            query = filter.search_query.lower()
            results = [
                e
                for e in results
                if query in e.description.lower()
                or query in e.resource_name.lower()
                or query in e.action.value.lower()
            ]

        results = sorted(results, key=lambda e: e.timestamp, reverse=True)

        return results[filter.offset : filter.offset + filter.limit]

    def get_summary(
        self,
        org_id: str,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> dict[str, Any]:
        """Get audit log summary statistics."""
        if end_time is None:
            end_time = datetime.now(timezone.utc)
        if start_time is None:
            start_time = end_time - timedelta(days=30)

        filter = AuditFilter(
            org_id=org_id,
            start_time=start_time,
            end_time=end_time,
            limit=100000,
        )
        entries = self.search(filter)

        by_action: dict[str, int] = defaultdict(int)
        by_category: dict[str, int] = defaultdict(int)
        by_severity: dict[str, int] = defaultdict(int)
        by_actor: dict[str, int] = defaultdict(int)
        by_resource: dict[str, int] = defaultdict(int)
        failures = 0

        for entry in entries:
            by_action[entry.action.value] += 1
            by_category[entry.category.value] += 1
            by_severity[entry.severity.value] += 1
            by_actor[entry.actor_id] += 1
            if entry.resource_type:
                by_resource[entry.resource_type] += 1
            if not entry.success:
                failures += 1

        return {
            "total_entries": len(entries),
            "period_start": start_time.isoformat(),
            "period_end": end_time.isoformat(),
            "by_action": dict(by_action),
            "by_category": dict(by_category),
            "by_severity": dict(by_severity),
            "top_actors": sorted(by_actor.items(), key=lambda x: x[1], reverse=True)[:10],
            "by_resource_type": dict(by_resource),
            "failure_count": failures,
            "failure_rate": failures / len(entries) if entries else 0,
        }

    def export(
        self,
        filter: AuditFilter,
        format: str = "json",
    ) -> bytes:
        """Export audit logs."""
        entries = self.search(filter)

        if format == "json":
            data = [e.to_dict() for e in entries]
            return json.dumps(data, indent=2, default=str).encode("utf-8")

        elif format == "csv":
            import csv
            import io

            output = io.StringIO()
            if entries:
                fieldnames = [
                    "id",
                    "timestamp",
                    "action",
                    "category",
                    "severity",
                    "actor_id",
                    "org_id",
                    "resource_type",
                    "resource_id",
                    "description",
                    "success",
                ]
                writer = csv.DictWriter(output, fieldnames=fieldnames)
                writer.writeheader()
                for entry in entries:
                    row = entry.to_dict()
                    writer.writerow({k: row.get(k, "") for k in fieldnames})
            return output.getvalue().encode("utf-8")

        elif format == "jsonl":
            lines = [e.to_json() for e in entries]
            return "\n".join(lines).encode("utf-8")

        else:
            raise ValueError(f"Unsupported format: {format}")


_logger: AuditLogger | None = None


def _get_logger() -> AuditLogger:
    """Get or create audit logger singleton."""
    global _logger
    if _logger is None:
        _logger = AuditLogger()
    return _logger


def log_action(
    action: AuditAction,
    actor_id: str,
    org_id: str = "",
    resource_type: str = "",
    resource_id: str = "",
    description: str = "",
    **kwargs: Any,
) -> AuditEntry:
    """Log an audit action."""
    logger = _get_logger()
    return logger.log(
        action=action,
        actor_id=actor_id,
        org_id=org_id,
        resource_type=resource_type,
        resource_id=resource_id,
        description=description,
        **kwargs,
    )


def log_auth_event(
    action: AuditAction,
    actor_id: str,
    success: bool = True,
    actor_ip: str = "",
    error_message: str = "",
    **kwargs: Any,
) -> AuditEntry:
    """Log an authentication event."""
    severity = AuditSeverity.INFO if success else AuditSeverity.WARNING
    if action == AuditAction.SECURITY_VIOLATION:
        severity = AuditSeverity.CRITICAL

    return log_action(
        action=action,
        actor_id=actor_id,
        severity=severity,
        success=success,
        actor_ip=actor_ip,
        error_message=error_message,
        **kwargs,
    )


def log_data_access(
    actor_id: str,
    resource_type: str,
    resource_id: str,
    action: AuditAction = AuditAction.DATA_EXPORT,
    description: str = "",
    **kwargs: Any,
) -> AuditEntry:
    """Log data access or export."""
    return log_action(
        action=action,
        actor_id=actor_id,
        resource_type=resource_type,
        resource_id=resource_id,
        description=description,
        **kwargs,
    )


def log_config_change(
    actor_id: str,
    setting_name: str,
    old_value: Any,
    new_value: Any,
    org_id: str = "",
    **kwargs: Any,
) -> AuditEntry:
    """Log a configuration change."""
    return log_action(
        action=AuditAction.CONFIG_UPDATE,
        actor_id=actor_id,
        org_id=org_id,
        resource_type="config",
        resource_id=setting_name,
        description=f"Changed {setting_name}",
        old_value={"value": old_value} if old_value is not None else None,
        new_value={"value": new_value} if new_value is not None else None,
        **kwargs,
    )


def log_security_event(
    action: AuditAction,
    actor_id: str,
    description: str,
    severity: AuditSeverity = AuditSeverity.WARNING,
    **kwargs: Any,
) -> AuditEntry:
    """Log a security-related event."""
    return log_action(
        action=action,
        actor_id=actor_id,
        severity=severity,
        description=description,
        **kwargs,
    )


def search_audit_logs(
    org_id: str,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    actions: list[AuditAction] | None = None,
    actor_id: str | None = None,
    limit: int = 100,
) -> list[AuditEntry]:
    """Search audit logs."""
    logger = _get_logger()
    filter = AuditFilter(
        org_id=org_id,
        start_time=start_time,
        end_time=end_time,
        actions=actions,
        actor_id=actor_id,
        limit=limit,
    )
    return logger.search(filter)


def export_audit_logs(
    org_id: str,
    format: str = "json",
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> bytes:
    """Export audit logs."""
    logger = _get_logger()
    filter = AuditFilter(
        org_id=org_id,
        start_time=start_time,
        end_time=end_time,
        limit=100000,
    )
    return logger.export(filter, format)


def get_audit_summary(
    org_id: str,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> dict[str, Any]:
    """Get audit log summary."""
    logger = _get_logger()
    return logger.get_summary(org_id, start_time, end_time)
