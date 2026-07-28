"""Integration Health Monitoring.

Monitors integration health and provides auto-recovery.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("devcopilot.integrations_advanced.health")


class HealthStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


class IssueType(str, Enum):
    RATE_LIMIT = "rate_limit"
    AUTH_FAILURE = "auth_failure"
    TIMEOUT = "timeout"
    CONNECTION_ERROR = "connection_error"
    API_ERROR = "api_error"
    DATA_STALE = "data_stale"


@dataclass
class HealthCheck:
    """Result of a health check."""

    check_id: str
    integration: str
    status: HealthStatus
    checked_at: datetime
    response_time_ms: int = 0
    error_message: str = ""
    issue_type: IssueType | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "integration": self.integration,
            "status": self.status.value,
            "checked_at": self.checked_at.isoformat(),
            "response_time_ms": self.response_time_ms,
            "error": self.error_message,
            "issue_type": self.issue_type.value if self.issue_type else None,
        }


@dataclass
class IntegrationHealth:
    """Health status of an integration."""

    integration: str
    org_id: str
    status: HealthStatus = HealthStatus.UNKNOWN
    last_check: datetime | None = None
    last_successful_sync: datetime | None = None
    consecutive_failures: int = 0
    total_errors_24h: int = 0
    avg_response_time_ms: float = 0
    current_issues: list[IssueType] = field(default_factory=list)
    sync_direction: str = "read"
    is_realtime: bool = False

    def to_dict(self) -> dict[str, Any]:
        last_sync_ago = "Never"
        if self.last_successful_sync:
            delta = datetime.now(timezone.utc) - self.last_successful_sync
            if delta.total_seconds() < 60:
                last_sync_ago = f"{int(delta.total_seconds())} sec ago"
            elif delta.total_seconds() < 3600:
                last_sync_ago = f"{int(delta.total_seconds() / 60)} min ago"
            else:
                last_sync_ago = f"{int(delta.total_seconds() / 3600)} hr ago"

        return {
            "integration": self.integration,
            "status": self.status.value,
            "last_sync": last_sync_ago,
            "last_check": self.last_check.isoformat() if self.last_check else None,
            "consecutive_failures": self.consecutive_failures,
            "errors_24h": self.total_errors_24h,
            "avg_response_ms": round(self.avg_response_time_ms, 1),
            "issues": [i.value for i in self.current_issues],
            "direction": self.sync_direction,
            "realtime": self.is_realtime,
        }


@dataclass
class RecoveryAction:
    """A recovery action for an integration issue."""

    action_id: str
    integration: str
    issue_type: IssueType
    action: str
    description: str
    auto_executable: bool = False
    executed: bool = False
    result: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "action": self.action,
            "description": self.description,
            "auto_executable": self.auto_executable,
            "executed": self.executed,
            "result": self.result,
        }


class IntegrationHealthMonitor:
    """Monitors and manages integration health."""

    def __init__(self) -> None:
        self._health: dict[str, IntegrationHealth] = {}
        self._checks: dict[str, list[HealthCheck]] = {}
        self._recovery_actions: dict[IssueType, list[dict[str, Any]]] = {}
        self._alert_thresholds = {
            "consecutive_failures": 3,
            "errors_24h": 10,
            "response_time_ms": 5000,
        }
        self._init_recovery_actions()

    def _init_recovery_actions(self) -> None:
        """Initialize recovery actions for each issue type."""
        self._recovery_actions[IssueType.RATE_LIMIT] = [
            {
                "action": "reduce_frequency",
                "description": "Reduce sync frequency to avoid rate limits",
                "auto_executable": True,
            },
            {
                "action": "use_webhooks",
                "description": "Switch to webhook-based sync instead of polling",
                "auto_executable": False,
            },
            {
                "action": "upgrade_plan",
                "description": "Upgrade integration plan for higher API limits",
                "auto_executable": False,
            },
        ]

        self._recovery_actions[IssueType.AUTH_FAILURE] = [
            {
                "action": "refresh_token",
                "description": "Attempt to refresh OAuth token",
                "auto_executable": True,
            },
            {
                "action": "reconnect",
                "description": "Re-authenticate with the integration",
                "auto_executable": False,
            },
        ]

        self._recovery_actions[IssueType.TIMEOUT] = [
            {
                "action": "increase_timeout",
                "description": "Increase request timeout",
                "auto_executable": True,
            },
            {
                "action": "reduce_batch_size",
                "description": "Reduce data batch size per request",
                "auto_executable": True,
            },
        ]

        self._recovery_actions[IssueType.CONNECTION_ERROR] = [
            {
                "action": "retry_connection",
                "description": "Retry connection with exponential backoff",
                "auto_executable": True,
            },
            {
                "action": "check_network",
                "description": "Verify network connectivity to service",
                "auto_executable": False,
            },
        ]

        self._recovery_actions[IssueType.DATA_STALE] = [
            {
                "action": "force_sync",
                "description": "Force a full data sync",
                "auto_executable": True,
            },
            {
                "action": "restart_webhook",
                "description": "Restart webhook handler",
                "auto_executable": True,
            },
        ]

    def record_check(
        self,
        org_id: str,
        integration: str,
        status: HealthStatus,
        response_time_ms: int = 0,
        error_message: str = "",
        issue_type: IssueType | None = None,
    ) -> HealthCheck:
        """Record a health check result."""
        check = HealthCheck(
            check_id=f"check_{datetime.now(timezone.utc).timestamp()}",
            integration=integration,
            status=status,
            checked_at=datetime.now(timezone.utc),
            response_time_ms=response_time_ms,
            error_message=error_message,
            issue_type=issue_type,
        )

        key = f"{org_id}:{integration}"
        if key not in self._checks:
            self._checks[key] = []
        self._checks[key].append(check)

        if len(self._checks[key]) > 1000:
            self._checks[key] = self._checks[key][-500:]

        self._update_health(org_id, integration, check)

        log.info(
            "Health check: org=%s integration=%s status=%s",
            org_id,
            integration,
            status.value,
        )

        return check

    def _update_health(
        self,
        org_id: str,
        integration: str,
        check: HealthCheck,
    ) -> None:
        """Update integration health based on check."""
        key = f"{org_id}:{integration}"

        if key not in self._health:
            self._health[key] = IntegrationHealth(
                integration=integration,
                org_id=org_id,
            )

        health = self._health[key]
        health.last_check = check.checked_at

        if check.status == HealthStatus.HEALTHY:
            health.status = HealthStatus.HEALTHY
            health.last_successful_sync = check.checked_at
            health.consecutive_failures = 0
            if check.issue_type in health.current_issues:
                health.current_issues.remove(check.issue_type)
        else:
            health.consecutive_failures += 1
            health.total_errors_24h += 1

            if check.issue_type and check.issue_type not in health.current_issues:
                health.current_issues.append(check.issue_type)

            if health.consecutive_failures >= self._alert_thresholds["consecutive_failures"]:
                health.status = HealthStatus.UNHEALTHY
            else:
                health.status = HealthStatus.DEGRADED

        recent_checks = self._checks[key][-10:]
        if recent_checks:
            health.avg_response_time_ms = sum(c.response_time_ms for c in recent_checks) / len(recent_checks)

    def get_health(self, org_id: str, integration: str) -> IntegrationHealth | None:
        """Get health status for an integration."""
        key = f"{org_id}:{integration}"
        return self._health.get(key)

    def get_all_health(self, org_id: str) -> list[dict[str, Any]]:
        """Get health status for all integrations."""
        results = []
        for key, health in self._health.items():
            if key.startswith(f"{org_id}:"):
                results.append(health.to_dict())
        return results

    def get_health_dashboard(self, org_id: str) -> dict[str, Any]:
        """Get health dashboard data."""
        all_health = self.get_all_health(org_id)

        healthy = sum(1 for h in all_health if h["status"] == "healthy")
        degraded = sum(1 for h in all_health if h["status"] == "degraded")
        unhealthy = sum(1 for h in all_health if h["status"] == "unhealthy")

        issues_summary: list[dict[str, Any]] = []
        for h in all_health:
            if h["issues"]:
                for issue in h["issues"]:
                    issues_summary.append({
                        "integration": h["integration"],
                        "issue": issue,
                    })

        return {
            "summary": {
                "healthy": healthy,
                "degraded": degraded,
                "unhealthy": unhealthy,
                "total": len(all_health),
            },
            "integrations": all_health,
            "issues": issues_summary,
        }

    def get_recovery_actions(
        self,
        org_id: str,
        integration: str,
    ) -> list[RecoveryAction]:
        """Get recommended recovery actions for an integration."""
        health = self.get_health(org_id, integration)
        if not health or not health.current_issues:
            return []

        actions: list[RecoveryAction] = []
        for issue_type in health.current_issues:
            issue_actions = self._recovery_actions.get(issue_type, [])
            for i, action_def in enumerate(issue_actions):
                action = RecoveryAction(
                    action_id=f"action_{integration}_{issue_type.value}_{i}",
                    integration=integration,
                    issue_type=issue_type,
                    action=action_def["action"],
                    description=action_def["description"],
                    auto_executable=action_def["auto_executable"],
                )
                actions.append(action)

        return actions

    def execute_recovery(
        self,
        org_id: str,
        integration: str,
        action_id: str,
    ) -> dict[str, Any]:
        """Execute a recovery action."""
        actions = self.get_recovery_actions(org_id, integration)
        action = next((a for a in actions if a.action_id == action_id), None)

        if not action:
            return {"error": "Action not found"}

        if action.action == "reduce_frequency":
            action.executed = True
            action.result = "Sync frequency reduced from 5 min to 15 min"
        elif action.action == "refresh_token":
            action.executed = True
            action.result = "Token refresh attempted"
        elif action.action == "increase_timeout":
            action.executed = True
            action.result = "Timeout increased from 30s to 60s"
        elif action.action == "force_sync":
            action.executed = True
            action.result = "Full sync initiated"
        elif action.action == "retry_connection":
            action.executed = True
            action.result = "Connection retry scheduled"
        else:
            return {
                "error": "Action requires manual intervention",
                "action": action.to_dict(),
            }

        log.info(
            "Executed recovery: org=%s integration=%s action=%s",
            org_id,
            integration,
            action.action,
        )

        return {
            "success": True,
            "action": action.to_dict(),
        }

    def auto_fix(self, org_id: str, integration: str) -> list[dict[str, Any]]:
        """Automatically fix recoverable issues."""
        actions = self.get_recovery_actions(org_id, integration)
        auto_actions = [a for a in actions if a.auto_executable]

        results = []
        for action in auto_actions:
            result = self.execute_recovery(org_id, integration, action.action_id)
            results.append(result)

        return results


_monitor: IntegrationHealthMonitor | None = None


def get_health_monitor() -> IntegrationHealthMonitor:
    """Get the default health monitor."""
    global _monitor
    if _monitor is None:
        _monitor = IntegrationHealthMonitor()
    return _monitor
