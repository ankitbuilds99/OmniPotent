"""SSO Audit Logging.

Comprehensive audit logging for SSO events.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("omnipotent.sso_advanced.audit")


class AuditEventType(str, Enum):
    LOGIN_SUCCESS = "login_success"
    LOGIN_FAILURE = "login_failure"
    LOGOUT = "logout"
    SESSION_CREATED = "session_created"
    SESSION_EXPIRED = "session_expired"
    SESSION_REVOKED = "session_revoked"
    MFA_CHALLENGE = "mfa_challenge"
    MFA_SUCCESS = "mfa_success"
    MFA_FAILURE = "mfa_failure"
    PASSWORD_CHANGE = "password_change"
    PASSWORD_RESET = "password_reset"
    USER_CREATED = "user_created"
    USER_UPDATED = "user_updated"
    USER_DEACTIVATED = "user_deactivated"
    USER_DELETED = "user_deleted"
    ROLE_ASSIGNED = "role_assigned"
    ROLE_REVOKED = "role_revoked"
    GROUP_SYNC = "group_sync"
    EMERGENCY_ACCESS = "emergency_access"
    SUSPICIOUS_ACTIVITY = "suspicious_activity"
    CONFIG_CHANGE = "config_change"


class AuditSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class AuditEvent:
    """An audit event."""

    event_id: str
    org_id: str
    event_type: AuditEventType
    severity: AuditSeverity
    user_id: str | None
    user_email: str | None
    ip_address: str
    user_agent: str
    description: str
    details: dict[str, Any]
    success: bool
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.event_id,
            "org_id": self.org_id,
            "type": self.event_type.value,
            "severity": self.severity.value,
            "user_id": self.user_id,
            "user_email": self.user_email,
            "ip": self.ip_address,
            "user_agent": self.user_agent[:100] if self.user_agent else "",
            "description": self.description,
            "details": self.details,
            "success": self.success,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class AuditLog:
    """An audit log containing multiple events."""

    log_id: str
    org_id: str
    events: list[AuditEvent]
    start_time: datetime
    end_time: datetime
    total_events: int
    filters_applied: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "log_id": self.log_id,
            "org_id": self.org_id,
            "events": [e.to_dict() for e in self.events],
            "period": {
                "start": self.start_time.isoformat(),
                "end": self.end_time.isoformat(),
            },
            "total_events": self.total_events,
            "filters": self.filters_applied,
        }


class SSOAuditLogger:
    """Logs and queries SSO audit events."""

    def __init__(self) -> None:
        self._events: dict[str, list[AuditEvent]] = {}
        self._alerts: dict[str, list[dict[str, Any]]] = {}

    def log_event(
        self,
        org_id: str,
        event_type: AuditEventType,
        user_id: str | None,
        user_email: str | None,
        ip_address: str,
        user_agent: str,
        description: str,
        details: dict[str, Any] | None = None,
        success: bool = True,
    ) -> AuditEvent:
        """Log an audit event."""
        event_id = f"audit_{datetime.now(timezone.utc).timestamp()}"

        severity = self._determine_severity(event_type, success)

        event = AuditEvent(
            event_id=event_id,
            org_id=org_id,
            event_type=event_type,
            severity=severity,
            user_id=user_id,
            user_email=user_email,
            ip_address=ip_address,
            user_agent=user_agent,
            description=description,
            details=details or {},
            success=success,
        )

        if org_id not in self._events:
            self._events[org_id] = []
        self._events[org_id].append(event)

        if len(self._events[org_id]) > 10000:
            self._events[org_id] = self._events[org_id][-5000:]

        self._check_alerts(org_id, event)

        log.info(
            "Audit event: org=%s type=%s user=%s success=%s",
            org_id,
            event_type.value,
            user_email,
            success,
        )

        return event

    def _determine_severity(
        self,
        event_type: AuditEventType,
        success: bool,
    ) -> AuditSeverity:
        """Determine severity based on event type."""
        critical_types = {
            AuditEventType.EMERGENCY_ACCESS,
            AuditEventType.SUSPICIOUS_ACTIVITY,
            AuditEventType.USER_DELETED,
            AuditEventType.CONFIG_CHANGE,
        }

        warning_types = {
            AuditEventType.LOGIN_FAILURE,
            AuditEventType.MFA_FAILURE,
            AuditEventType.SESSION_REVOKED,
            AuditEventType.USER_DEACTIVATED,
        }

        if event_type in critical_types:
            return AuditSeverity.CRITICAL
        elif event_type in warning_types or not success:
            return AuditSeverity.WARNING
        else:
            return AuditSeverity.INFO

    def _check_alerts(
        self,
        org_id: str,
        event: AuditEvent,
    ) -> None:
        """Check if event should trigger alerts."""
        if event.event_type == AuditEventType.EMERGENCY_ACCESS:
            self._create_alert(
                org_id,
                "Emergency access used",
                event,
            )

        if event.event_type == AuditEventType.LOGIN_FAILURE:
            recent_failures = self._count_recent_failures(
                org_id,
                event.ip_address,
                minutes=15,
            )
            if recent_failures >= 5:
                self._create_alert(
                    org_id,
                    f"Multiple login failures from {event.ip_address}",
                    event,
                )

        if event.event_type == AuditEventType.SUSPICIOUS_ACTIVITY:
            self._create_alert(
                org_id,
                "Suspicious activity detected",
                event,
            )

    def _count_recent_failures(
        self,
        org_id: str,
        ip_address: str,
        minutes: int,
    ) -> int:
        """Count recent login failures from an IP."""
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
        events = self._events.get(org_id, [])

        return sum(
            1 for e in events
            if (
                e.event_type == AuditEventType.LOGIN_FAILURE and
                e.ip_address == ip_address and
                e.timestamp >= cutoff
            )
        )

    def _create_alert(
        self,
        org_id: str,
        message: str,
        event: AuditEvent,
    ) -> None:
        """Create an alert."""
        if org_id not in self._alerts:
            self._alerts[org_id] = []

        self._alerts[org_id].append({
            "message": message,
            "event_id": event.event_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        log.warning(
            "Audit alert: org=%s message=%s",
            org_id,
            message,
        )

    def log_login(
        self,
        org_id: str,
        user_id: str,
        user_email: str,
        ip_address: str,
        user_agent: str,
        success: bool,
        failure_reason: str = "",
        idp_provider: str = "",
    ) -> AuditEvent:
        """Log a login event."""
        event_type = (
            AuditEventType.LOGIN_SUCCESS if success
            else AuditEventType.LOGIN_FAILURE
        )

        return self.log_event(
            org_id=org_id,
            event_type=event_type,
            user_id=user_id,
            user_email=user_email,
            ip_address=ip_address,
            user_agent=user_agent,
            description=f"User login {'successful' if success else 'failed'}",
            details={
                "idp_provider": idp_provider,
                "failure_reason": failure_reason,
            },
            success=success,
        )

    def log_logout(
        self,
        org_id: str,
        user_id: str,
        user_email: str,
        ip_address: str,
        user_agent: str,
        session_duration_minutes: float,
    ) -> AuditEvent:
        """Log a logout event."""
        return self.log_event(
            org_id=org_id,
            event_type=AuditEventType.LOGOUT,
            user_id=user_id,
            user_email=user_email,
            ip_address=ip_address,
            user_agent=user_agent,
            description="User logged out",
            details={
                "session_duration_minutes": round(session_duration_minutes, 1),
            },
            success=True,
        )

    def log_mfa_attempt(
        self,
        org_id: str,
        user_id: str,
        user_email: str,
        ip_address: str,
        user_agent: str,
        mfa_method: str,
        success: bool,
    ) -> AuditEvent:
        """Log an MFA attempt."""
        event_type = (
            AuditEventType.MFA_SUCCESS if success
            else AuditEventType.MFA_FAILURE
        )

        return self.log_event(
            org_id=org_id,
            event_type=event_type,
            user_id=user_id,
            user_email=user_email,
            ip_address=ip_address,
            user_agent=user_agent,
            description=f"MFA {mfa_method} {'verified' if success else 'failed'}",
            details={"mfa_method": mfa_method},
            success=success,
        )

    def log_emergency_access(
        self,
        org_id: str,
        user_id: str,
        user_email: str,
        ip_address: str,
        user_agent: str,
        reason: str,
    ) -> AuditEvent:
        """Log emergency access usage."""
        return self.log_event(
            org_id=org_id,
            event_type=AuditEventType.EMERGENCY_ACCESS,
            user_id=user_id,
            user_email=user_email,
            ip_address=ip_address,
            user_agent=user_agent,
            description=f"Emergency access used: {reason}",
            details={"reason": reason},
            success=True,
        )

    def log_suspicious_activity(
        self,
        org_id: str,
        user_id: str | None,
        user_email: str | None,
        ip_address: str,
        user_agent: str,
        activity_type: str,
        details: dict[str, Any],
    ) -> AuditEvent:
        """Log suspicious activity."""
        return self.log_event(
            org_id=org_id,
            event_type=AuditEventType.SUSPICIOUS_ACTIVITY,
            user_id=user_id,
            user_email=user_email,
            ip_address=ip_address,
            user_agent=user_agent,
            description=f"Suspicious activity: {activity_type}",
            details=details,
            success=False,
        )

    def query_events(
        self,
        org_id: str,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        event_types: list[AuditEventType] | None = None,
        user_id: str | None = None,
        user_email: str | None = None,
        ip_address: str | None = None,
        success: bool | None = None,
        severity: AuditSeverity | None = None,
        limit: int = 100,
    ) -> AuditLog:
        """Query audit events."""
        events = self._events.get(org_id, [])

        if start_time is None:
            start_time = datetime.now(timezone.utc) - timedelta(days=7)
        if end_time is None:
            end_time = datetime.now(timezone.utc)

        filtered = []

        for event in events:
            if event.timestamp < start_time or event.timestamp > end_time:
                continue
            if event_types and event.event_type not in event_types:
                continue
            if user_id and event.user_id != user_id:
                continue
            if user_email and event.user_email != user_email:
                continue
            if ip_address and event.ip_address != ip_address:
                continue
            if success is not None and event.success != success:
                continue
            if severity and event.severity != severity:
                continue

            filtered.append(event)

        filtered.sort(key=lambda e: e.timestamp, reverse=True)
        total = len(filtered)
        filtered = filtered[:limit]

        filters_applied: dict[str, Any] = {
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat(),
        }
        if event_types:
            filters_applied["event_types"] = [e.value for e in event_types]
        if user_id:
            filters_applied["user_id"] = user_id
        if user_email:
            filters_applied["user_email"] = user_email
        if ip_address:
            filters_applied["ip_address"] = ip_address
        if success is not None:
            filters_applied["success"] = success
        if severity:
            filters_applied["severity"] = severity.value

        return AuditLog(
            log_id=f"log_{datetime.now(timezone.utc).timestamp()}",
            org_id=org_id,
            events=filtered,
            start_time=start_time,
            end_time=end_time,
            total_events=total,
            filters_applied=filters_applied,
        )

    def get_user_activity(
        self,
        org_id: str,
        user_id: str,
        days: int = 30,
    ) -> dict[str, Any]:
        """Get activity summary for a user."""
        start_time = datetime.now(timezone.utc) - timedelta(days=days)

        events = self._events.get(org_id, [])
        user_events = [
            e for e in events
            if e.user_id == user_id and e.timestamp >= start_time
        ]

        logins = sum(
            1 for e in user_events
            if e.event_type == AuditEventType.LOGIN_SUCCESS
        )
        login_failures = sum(
            1 for e in user_events
            if e.event_type == AuditEventType.LOGIN_FAILURE
        )

        ips = set(e.ip_address for e in user_events)

        last_login = None
        for e in reversed(user_events):
            if e.event_type == AuditEventType.LOGIN_SUCCESS:
                last_login = e.timestamp
                break

        return {
            "user_id": user_id,
            "period_days": days,
            "total_events": len(user_events),
            "logins": logins,
            "login_failures": login_failures,
            "unique_ips": len(ips),
            "last_login": last_login.isoformat() if last_login else None,
            "recent_events": [e.to_dict() for e in user_events[-10:]],
        }

    def get_security_summary(
        self,
        org_id: str,
        days: int = 7,
    ) -> dict[str, Any]:
        """Get security summary for an org."""
        start_time = datetime.now(timezone.utc) - timedelta(days=days)

        events = self._events.get(org_id, [])
        recent = [e for e in events if e.timestamp >= start_time]

        return {
            "period_days": days,
            "total_events": len(recent),
            "by_type": self._count_by_type(recent),
            "by_severity": self._count_by_severity(recent),
            "failed_logins": sum(
                1 for e in recent
                if e.event_type == AuditEventType.LOGIN_FAILURE
            ),
            "successful_logins": sum(
                1 for e in recent
                if e.event_type == AuditEventType.LOGIN_SUCCESS
            ),
            "emergency_accesses": sum(
                1 for e in recent
                if e.event_type == AuditEventType.EMERGENCY_ACCESS
            ),
            "suspicious_activities": sum(
                1 for e in recent
                if e.event_type == AuditEventType.SUSPICIOUS_ACTIVITY
            ),
            "alerts": self._alerts.get(org_id, [])[-10:],
        }

    def _count_by_type(
        self,
        events: list[AuditEvent],
    ) -> dict[str, int]:
        """Count events by type."""
        counts: dict[str, int] = {}
        for event in events:
            key = event.event_type.value
            counts[key] = counts.get(key, 0) + 1
        return dict(sorted(counts.items(), key=lambda x: x[1], reverse=True))

    def _count_by_severity(
        self,
        events: list[AuditEvent],
    ) -> dict[str, int]:
        """Count events by severity."""
        counts: dict[str, int] = {s.value: 0 for s in AuditSeverity}
        for event in events:
            counts[event.severity.value] += 1
        return counts

    def export_to_siem(
        self,
        org_id: str,
        start_time: datetime,
        end_time: datetime,
        format_type: str = "json",
    ) -> list[dict[str, Any]]:
        """Export events in SIEM-compatible format."""
        log_result = self.query_events(
            org_id=org_id,
            start_time=start_time,
            end_time=end_time,
            limit=10000,
        )

        return [e.to_dict() for e in log_result.events]


_logger: SSOAuditLogger | None = None


def get_sso_audit_logger() -> SSOAuditLogger:
    """Get the default SSO audit logger."""
    global _logger
    if _logger is None:
        _logger = SSOAuditLogger()
    return _logger
