"""Real-time compliance dashboard with alerts.

Provides:
- Live compliance scores
- Control status summaries
- Pre-emptive violation alerts
- Framework-specific views
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from app.compliance.controls import (
    ComplianceControl,
    Control,
    ControlCategory,
    ControlFramework,
    ControlStatus,
    get_control_registry,
)

log = logging.getLogger("omnipotent.compliance.dashboard")


class AlertSeverity(Enum):
    """Severity levels for compliance alerts."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"
    URGENT = "urgent"


class AlertStatus(Enum):
    """Status of a compliance alert."""

    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    FALSE_POSITIVE = "false_positive"


@dataclass
class ComplianceScore:
    """Overall compliance score for a framework."""

    framework: ControlFramework
    score: float
    total_controls: int
    passing_controls: int
    failing_controls: int
    warning_controls: int
    pending_controls: int
    trend: str
    last_updated: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "framework": self.framework.value,
            "score": round(self.score, 2),
            "total_controls": self.total_controls,
            "passing_controls": self.passing_controls,
            "failing_controls": self.failing_controls,
            "warning_controls": self.warning_controls,
            "pending_controls": self.pending_controls,
            "trend": self.trend,
            "last_updated": self.last_updated.isoformat(),
        }


@dataclass
class ControlStatusSummary:
    """Summary of a control's status."""

    control_id: str
    name: str
    category: ControlCategory
    status: ControlStatus
    message: str
    last_checked: datetime
    next_check: datetime | None
    critical: bool
    owner: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "control_id": self.control_id,
            "name": self.name,
            "category": self.category.value,
            "status": self.status.value,
            "message": self.message,
            "last_checked": self.last_checked.isoformat(),
            "next_check": self.next_check.isoformat() if self.next_check else None,
            "critical": self.critical,
            "owner": self.owner,
        }


@dataclass
class ViolationAlert:
    """A compliance violation alert."""

    alert_id: str
    control_id: str
    control_name: str
    severity: AlertSeverity
    status: AlertStatus
    title: str
    description: str
    detected_at: datetime
    details: dict[str, Any]
    recommended_actions: list[str]
    audit_impact: str
    acknowledged_by: str | None = None
    acknowledged_at: datetime | None = None
    resolved_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "alert_id": self.alert_id,
            "control_id": self.control_id,
            "control_name": self.control_name,
            "severity": self.severity.value,
            "status": self.status.value,
            "title": self.title,
            "description": self.description,
            "detected_at": self.detected_at.isoformat(),
            "details": self.details,
            "recommended_actions": self.recommended_actions,
            "audit_impact": self.audit_impact,
            "acknowledged_by": self.acknowledged_by,
            "acknowledged_at": self.acknowledged_at.isoformat() if self.acknowledged_at else None,
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
        }


class ComplianceDashboard:
    """Real-time compliance dashboard."""

    def __init__(self, control_registry: ComplianceControl | None = None) -> None:
        self._registry = control_registry or get_control_registry()
        self._alerts: dict[str, ViolationAlert] = {}
        self._alert_counter = 0
        self._scores_cache: dict[str, ComplianceScore] = {}
        self._notification_handlers: list[Any] = []

    def get_score(self, framework: ControlFramework) -> ComplianceScore:
        """Calculate compliance score for a framework."""
        controls = self._registry.list_controls(framework=framework)

        total = len(controls)
        passing = 0
        failing = 0
        warning = 0
        pending = 0

        for control in controls:
            result = self._registry.get_last_result(control.control_id)
            if result:
                if result.status == ControlStatus.PASS:
                    passing += 1
                elif result.status == ControlStatus.FAIL:
                    failing += 1
                elif result.status == ControlStatus.WARNING:
                    warning += 1
                else:
                    pending += 1
            else:
                pending += 1

        score = (passing / total * 100) if total > 0 else 0

        cached = self._scores_cache.get(framework.value)
        if cached:
            if score > cached.score:
                trend = "improving"
            elif score < cached.score:
                trend = "declining"
            else:
                trend = "stable"
        else:
            trend = "new"

        compliance_score = ComplianceScore(
            framework=framework,
            score=score,
            total_controls=total,
            passing_controls=passing,
            failing_controls=failing,
            warning_controls=warning,
            pending_controls=pending,
            trend=trend,
            last_updated=datetime.now(timezone.utc),
        )

        self._scores_cache[framework.value] = compliance_score
        return compliance_score

    def get_all_scores(self) -> list[ComplianceScore]:
        """Get compliance scores for all frameworks."""
        frameworks = [
            ControlFramework.SOC2,
            ControlFramework.ISO27001,
            ControlFramework.HIPAA,
        ]
        return [self.get_score(f) for f in frameworks]

    def get_control_status(
        self,
        framework: ControlFramework | None = None,
        category: ControlCategory | None = None,
    ) -> list[ControlStatusSummary]:
        """Get status summary for controls."""
        controls = self._registry.list_controls(framework=framework, category=category)
        summaries = []

        for control in controls:
            result = self._registry.get_last_result(control.control_id)

            if result:
                status = result.status
                message = result.message
                last_checked = result.checked_at
            else:
                status = ControlStatus.PENDING
                message = "Not yet checked"
                last_checked = datetime.now(timezone.utc)

            next_check = last_checked + timedelta(hours=control.frequency_hours)

            summaries.append(ControlStatusSummary(
                control_id=control.control_id,
                name=control.name,
                category=control.category,
                status=status,
                message=message,
                last_checked=last_checked,
                next_check=next_check,
                critical=control.critical,
                owner=control.owner,
            ))

        return summaries

    def get_category_breakdown(
        self,
        framework: ControlFramework = ControlFramework.SOC2,
    ) -> dict[str, dict[str, int]]:
        """Get control status breakdown by category."""
        controls = self._registry.list_controls(framework=framework)
        breakdown: dict[str, dict[str, int]] = {}

        for control in controls:
            cat = control.category.value
            if cat not in breakdown:
                breakdown[cat] = {"pass": 0, "fail": 0, "warning": 0, "pending": 0}

            result = self._registry.get_last_result(control.control_id)
            if result:
                if result.status == ControlStatus.PASS:
                    breakdown[cat]["pass"] += 1
                elif result.status == ControlStatus.FAIL:
                    breakdown[cat]["fail"] += 1
                elif result.status == ControlStatus.WARNING:
                    breakdown[cat]["warning"] += 1
                else:
                    breakdown[cat]["pending"] += 1
            else:
                breakdown[cat]["pending"] += 1

        return breakdown

    def check_for_violations(self) -> list[ViolationAlert]:
        """Check for new compliance violations and create alerts."""
        new_alerts = []

        for control in self._registry.list_controls():
            result = self._registry.get_last_result(control.control_id)
            if not result:
                continue

            if result.status in (ControlStatus.FAIL, ControlStatus.WARNING):
                existing = self._find_existing_alert(control.control_id)
                if not existing:
                    alert = self._create_alert(control, result.status, result.message, result.details)
                    new_alerts.append(alert)

        return new_alerts

    def _find_existing_alert(self, control_id: str) -> ViolationAlert | None:
        """Find an existing open alert for a control."""
        for alert in self._alerts.values():
            if alert.control_id == control_id and alert.status == AlertStatus.OPEN:
                return alert
        return None

    def _create_alert(
        self,
        control: Control,
        status: ControlStatus,
        message: str,
        details: dict[str, Any],
    ) -> ViolationAlert:
        """Create a new violation alert."""
        self._alert_counter += 1
        alert_id = f"alert_{self._alert_counter}"

        severity = AlertSeverity.CRITICAL if control.critical else AlertSeverity.WARNING
        if status == ControlStatus.WARNING and not control.critical:
            severity = AlertSeverity.INFO

        audit_impact = "Would fail SOC2 control" if control.critical else "May require explanation in audit"

        alert = ViolationAlert(
            alert_id=alert_id,
            control_id=control.control_id,
            control_name=control.name,
            severity=severity,
            status=AlertStatus.OPEN,
            title=f"Compliance Risk: {control.name}",
            description=message,
            detected_at=datetime.now(timezone.utc),
            details=details,
            recommended_actions=self._get_recommended_actions(control, details),
            audit_impact=audit_impact,
        )

        self._alerts[alert_id] = alert
        self._notify_alert(alert)

        log.warning(
            "compliance violation detected",
            extra={
                "alert_id": alert_id,
                "control_id": control.control_id,
                "severity": severity.value,
            },
        )

        return alert

    def _get_recommended_actions(
        self,
        control: Control,
        details: dict[str, Any],
    ) -> list[str]:
        """Get recommended actions for a violation."""
        actions = []

        if control.remediation:
            actions.append(control.remediation)

        users_without_mfa = details.get("users_without_mfa", [])
        if users_without_mfa:
            actions.append(f"Enable MFA for: {', '.join(users_without_mfa[:3])}")

        if control.control_id.startswith("CC6"):
            actions.append("Review access control settings")
            actions.append("Verify encryption configuration")

        if not actions:
            actions.append("Review control configuration")
            actions.append("Contact security team if needed")

        return actions

    def _notify_alert(self, alert: ViolationAlert) -> None:
        """Send notifications for new alerts."""
        for handler in self._notification_handlers:
            try:
                handler(alert)
            except Exception as e:
                log.error("notification failed", extra={"error": str(e)})

    def acknowledge_alert(
        self,
        alert_id: str,
        user: str,
    ) -> ViolationAlert | None:
        """Acknowledge an alert."""
        alert = self._alerts.get(alert_id)
        if alert:
            alert.status = AlertStatus.ACKNOWLEDGED
            alert.acknowledged_by = user
            alert.acknowledged_at = datetime.now(timezone.utc)
            return alert
        return None

    def resolve_alert(
        self,
        alert_id: str,
        resolution: str | None = None,
    ) -> ViolationAlert | None:
        """Resolve an alert."""
        alert = self._alerts.get(alert_id)
        if alert:
            alert.status = AlertStatus.RESOLVED
            alert.resolved_at = datetime.now(timezone.utc)
            if resolution:
                alert.details["resolution"] = resolution
            return alert
        return None

    def get_alerts(
        self,
        status: AlertStatus | None = None,
        severity: AlertSeverity | None = None,
    ) -> list[ViolationAlert]:
        """Get alerts with optional filtering."""
        alerts = list(self._alerts.values())

        if status:
            alerts = [a for a in alerts if a.status == status]

        if severity:
            alerts = [a for a in alerts if a.severity == severity]

        return sorted(alerts, key=lambda a: a.detected_at, reverse=True)

    def get_dashboard_data(self) -> dict[str, Any]:
        """Get complete dashboard data."""
        scores = self.get_all_scores()
        main_score = next((s for s in scores if s.framework == ControlFramework.SOC2), scores[0] if scores else None)

        open_alerts = self.get_alerts(status=AlertStatus.OPEN)
        critical_alerts = [a for a in open_alerts if a.severity == AlertSeverity.CRITICAL]

        return {
            "overall_score": main_score.score if main_score else 0,
            "scores": [s.to_dict() for s in scores],
            "category_breakdown": self.get_category_breakdown(),
            "open_alerts": len(open_alerts),
            "critical_alerts": len(critical_alerts),
            "alerts": [a.to_dict() for a in open_alerts[:10]],
            "controls": [s.to_dict() for s in self.get_control_status()],
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }

    def add_notification_handler(self, handler: Any) -> None:
        """Add a notification handler for alerts."""
        self._notification_handlers.append(handler)


_dashboard: ComplianceDashboard | None = None


def get_compliance_dashboard() -> ComplianceDashboard:
    """Get the default compliance dashboard."""
    global _dashboard
    if _dashboard is None:
        _dashboard = ComplianceDashboard()
    return _dashboard
