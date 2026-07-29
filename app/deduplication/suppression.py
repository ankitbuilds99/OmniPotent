"""Layer 6: Policy-based suppression engine."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from app.deduplication.models import (
    RawAlert,
    SuppressionRule,
    SuppressionType,
    MaintenanceWindow,
    KnownIssue,
    FlappingAlert,
)

log = logging.getLogger("devcopilot.deduplication.suppression")


@dataclass
class SuppressionConfig:
    """Configuration for suppression engine."""

    flapping_window_minutes: int = 30
    flapping_min_state_changes: int = 3
    flapping_hysteresis_minutes: int = 5
    max_suppress_duration_hours: int = 24
    auto_suppress_after_n_alerts: int = 3
    digest_interval_minutes: int = 60


@dataclass
class SuppressionResult:
    """Result of suppression check."""

    should_suppress: bool
    reason: str
    rule_id: UUID | None = None
    rule_name: str | None = None
    suppression_type: SuppressionType | None = None
    expires_at: datetime | None = None


class FlappingDetector:
    """Detect and handle flapping alerts."""

    def __init__(self, config: SuppressionConfig | None = None):
        self.config = config or SuppressionConfig()
        self._alert_states: dict[str, FlappingAlert] = {}

    def record_state_change(
        self,
        fingerprint: str,
        timestamp: datetime,
        state: str,
        service: str = "",
        alert_name: str = "",
    ) -> FlappingAlert:
        """Record a state change for an alert."""
        if fingerprint not in self._alert_states:
            self._alert_states[fingerprint] = FlappingAlert(
                alert_fingerprint=fingerprint,
                service=service,
                alert_name=alert_name,
                first_seen=timestamp,
            )

        flapping = self._alert_states[fingerprint]
        flapping.add_state_change(timestamp, state)

        if not flapping.service and service:
            flapping.service = service
        if not flapping.alert_name and alert_name:
            flapping.alert_name = alert_name

        return flapping

    def is_flapping(self, fingerprint: str) -> bool:
        """Check if alert is currently flapping."""
        flapping = self._alert_states.get(fingerprint)
        return flapping.is_flapping if flapping else False

    def get_flapping_alert(self, fingerprint: str) -> FlappingAlert | None:
        """Get flapping alert state."""
        return self._alert_states.get(fingerprint)

    def suppress_flapping(
        self,
        fingerprint: str,
        duration_minutes: int = 60,
    ) -> bool:
        """Suppress a flapping alert."""
        flapping = self._alert_states.get(fingerprint)
        if not flapping:
            return False

        flapping.suppressed_until = datetime.utcnow() + timedelta(
            minutes=duration_minutes
        )
        return True

    def is_suppressed(self, fingerprint: str) -> bool:
        """Check if flapping alert is suppressed."""
        flapping = self._alert_states.get(fingerprint)
        if not flapping or not flapping.suppressed_until:
            return False
        return datetime.utcnow() < flapping.suppressed_until

    def get_all_flapping(self) -> list[FlappingAlert]:
        """Get all currently flapping alerts."""
        return [f for f in self._alert_states.values() if f.is_flapping]

    def suggest_threshold_adjustment(
        self,
        fingerprint: str,
    ) -> dict[str, Any] | None:
        """Suggest threshold adjustment for flapping alert."""
        flapping = self._alert_states.get(fingerprint)
        if not flapping or not flapping.is_flapping:
            return None

        return {
            "alert_fingerprint": fingerprint,
            "current_pattern": flapping.pattern_description,
            "suggestion": "Consider increasing threshold or adding hysteresis",
            "flapping_score": flapping.flapping_score,
        }

    def cleanup_old_entries(self, before: datetime) -> int:
        """Remove old entries."""
        to_remove = [
            fp
            for fp, f in self._alert_states.items()
            if f.last_seen < before and not f.is_flapping
        ]
        for fp in to_remove:
            del self._alert_states[fp]
        return len(to_remove)


class MaintenanceWindowManager:
    """Manage maintenance windows."""

    def __init__(self):
        self._windows: dict[UUID, MaintenanceWindow] = {}
        self._recurring_windows: list[MaintenanceWindow] = []

    def create_window(
        self,
        name: str,
        services: list[str],
        starts_at: datetime,
        ends_at: datetime,
        description: str = "",
        regions: list[str] | None = None,
        created_by: str | None = None,
    ) -> MaintenanceWindow:
        """Create a maintenance window."""
        window = MaintenanceWindow(
            name=name,
            description=description,
            services=services,
            regions=regions or [],
            starts_at=starts_at,
            ends_at=ends_at,
            created_by=created_by,
        )
        self._windows[window.window_id] = window
        log.info("Created maintenance window: %s", name)
        return window

    def create_recurring_window(
        self,
        name: str,
        services: list[str],
        recurrence_pattern: str,
        duration_minutes: int,
        description: str = "",
        created_by: str | None = None,
    ) -> MaintenanceWindow:
        """Create a recurring maintenance window."""
        now = datetime.utcnow()
        window = MaintenanceWindow(
            name=name,
            description=description,
            services=services,
            starts_at=now,
            ends_at=now + timedelta(minutes=duration_minutes),
            created_by=created_by,
            is_recurring=True,
            recurrence_pattern=recurrence_pattern,
        )
        self._recurring_windows.append(window)
        log.info("Created recurring maintenance window: %s", name)
        return window

    def get_active_windows(
        self,
        at_time: datetime | None = None,
    ) -> list[MaintenanceWindow]:
        """Get all active maintenance windows."""
        now = at_time or datetime.utcnow()
        active = [w for w in self._windows.values() if w.is_active(now)]

        for recurring in self._recurring_windows:
            if self._is_recurring_active(recurring, now):
                active.append(recurring)

        return active

    def _is_recurring_active(
        self,
        window: MaintenanceWindow,
        at_time: datetime,
    ) -> bool:
        """Check if recurring window is active at given time."""
        pattern = window.recurrence_pattern
        if not pattern:
            return False

        if pattern == "daily":
            start_time = window.starts_at.time()
            end_time = window.ends_at.time()
            current_time = at_time.time()
            return start_time <= current_time <= end_time

        if pattern == "weekly":
            if at_time.weekday() == window.starts_at.weekday():
                start_time = window.starts_at.time()
                end_time = window.ends_at.time()
                current_time = at_time.time()
                return start_time <= current_time <= end_time

        return False

    def is_service_in_maintenance(
        self,
        service: str,
        at_time: datetime | None = None,
    ) -> tuple[bool, MaintenanceWindow | None]:
        """Check if a service is in maintenance."""
        active_windows = self.get_active_windows(at_time)

        for window in active_windows:
            if window.matches_service(service):
                return True, window

        return False, None

    def end_window_early(self, window_id: UUID) -> bool:
        """End a maintenance window early."""
        window = self._windows.get(window_id)
        if not window:
            return False

        window.ended_early = True
        log.info("Ended maintenance window early: %s", window.name)
        return True

    def extend_window(
        self,
        window_id: UUID,
        additional_minutes: int,
    ) -> bool:
        """Extend a maintenance window."""
        window = self._windows.get(window_id)
        if not window:
            return False

        window.extended_until = window.ends_at + timedelta(minutes=additional_minutes)
        log.info(
            "Extended maintenance window %s by %d minutes",
            window.name,
            additional_minutes,
        )
        return True

    def get_window(self, window_id: UUID) -> MaintenanceWindow | None:
        """Get maintenance window by ID."""
        return self._windows.get(window_id)

    def get_scheduled_windows(self) -> list[MaintenanceWindow]:
        """Get all scheduled (future) windows."""
        now = datetime.utcnow()
        return [w for w in self._windows.values() if w.starts_at > now]

    def delete_window(self, window_id: UUID) -> bool:
        """Delete a maintenance window."""
        if window_id in self._windows:
            del self._windows[window_id]
            return True
        return False


class KnownIssueTracker:
    """Track known issues for alert suppression."""

    def __init__(self):
        self._issues: dict[UUID, KnownIssue] = {}

    def create_issue(
        self,
        title: str,
        alert_pattern: dict[str, Any],
        description: str = "",
        jira_ticket: str | None = None,
        vendor_status_url: str | None = None,
        expires_at: datetime | None = None,
        created_by: str | None = None,
    ) -> KnownIssue:
        """Create a known issue."""
        issue = KnownIssue(
            title=title,
            description=description,
            alert_pattern=alert_pattern,
            jira_ticket=jira_ticket,
            vendor_status_url=vendor_status_url,
            expires_at=expires_at,
            created_by=created_by,
        )
        self._issues[issue.issue_id] = issue
        log.info("Created known issue: %s", title)
        return issue

    def find_matching_issue(
        self,
        alert: RawAlert,
    ) -> KnownIssue | None:
        """Find known issue matching an alert."""
        for issue in self._issues.values():
            if not issue.is_active():
                continue

            if self._matches_pattern(alert, issue.alert_pattern):
                return issue

        return None

    def _matches_pattern(
        self,
        alert: RawAlert,
        pattern: dict[str, Any],
    ) -> bool:
        """Check if alert matches pattern."""
        for key, value in pattern.items():
            alert_value = getattr(alert, key, None)
            if alert_value is None:
                alert_value = alert.labels.get(key)

            if alert_value is None:
                return False

            if hasattr(alert_value, "value"):
                alert_value = alert_value.value

            if isinstance(value, str):
                if value.startswith("contains:"):
                    if value[9:] not in str(alert_value):
                        return False
                elif value.startswith("regex:"):
                    if not re.match(value[6:], str(alert_value)):
                        return False
                elif str(alert_value) != value:
                    return False
            elif isinstance(value, list):
                if str(alert_value) not in value:
                    return False
            elif alert_value != value:
                return False

        return True

    def acknowledge_issue(
        self,
        issue_id: UUID,
        user: str,
    ) -> bool:
        """Acknowledge a known issue."""
        issue = self._issues.get(issue_id)
        if not issue:
            return False

        if user not in issue.acknowledged_by:
            issue.acknowledged_by.append(user)
        return True

    def extend_issue(
        self,
        issue_id: UUID,
        additional_days: int,
    ) -> bool:
        """Extend known issue expiration."""
        issue = self._issues.get(issue_id)
        if not issue:
            return False

        current_expiry = issue.expires_at or datetime.utcnow()
        issue.expires_at = current_expiry + timedelta(days=additional_days)
        return True

    def close_issue(self, issue_id: UUID) -> bool:
        """Close a known issue."""
        if issue_id in self._issues:
            del self._issues[issue_id]
            return True
        return False

    def get_issue(self, issue_id: UUID) -> KnownIssue | None:
        """Get known issue by ID."""
        return self._issues.get(issue_id)

    def get_active_issues(self) -> list[KnownIssue]:
        """Get all active known issues."""
        return [i for i in self._issues.values() if i.is_active()]

    def get_expiring_soon(self, days: int = 3) -> list[KnownIssue]:
        """Get issues expiring within specified days."""
        cutoff = datetime.utcnow() + timedelta(days=days)
        return [
            i
            for i in self._issues.values()
            if i.expires_at and i.expires_at < cutoff and i.is_active()
        ]


@dataclass
class BusinessRule:
    """Business rule for alert suppression."""

    rule_id: UUID = field(default_factory=uuid4)
    name: str = ""
    description: str = ""
    condition: str = ""
    action: str = "suppress"
    parameters: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    created_at: datetime = field(default_factory=datetime.utcnow)


class BusinessRuleEngine:
    """Engine for business-rule based suppression."""

    def __init__(self):
        self._rules: list[BusinessRule] = []
        self._load_default_rules()

    def _load_default_rules(self) -> None:
        """Load default business rules."""
        self._rules.extend([
            BusinessRule(
                name="Suppress duplicate alerts",
                description="Suppress after 3 identical alerts in 1 hour",
                condition="alert_count > 3 AND window = 1h",
                action="suppress",
                parameters={"digest_instead": True},
            ),
            BusinessRule(
                name="Low severity night suppression",
                description="Suppress low severity alerts during night hours",
                condition="severity = low AND hour BETWEEN 23 AND 7",
                action="suppress",
                parameters={"notify_channel": "low-priority"},
            ),
            BusinessRule(
                name="Weekend non-critical suppression",
                description="Reduce noise for non-critical alerts on weekends",
                condition="severity NOT IN (critical, high) AND day IN (sat, sun)",
                action="digest",
                parameters={"digest_interval_hours": 4},
            ),
        ])

    def add_rule(self, rule: BusinessRule) -> None:
        """Add a business rule."""
        self._rules.append(rule)

    def remove_rule(self, rule_id: UUID) -> bool:
        """Remove a business rule."""
        for i, rule in enumerate(self._rules):
            if rule.rule_id == rule_id:
                del self._rules[i]
                return True
        return False

    def evaluate(
        self,
        alert: RawAlert,
        context: dict[str, Any],
    ) -> tuple[BusinessRule | None, str]:
        """Evaluate rules against an alert."""
        for rule in self._rules:
            if not rule.enabled:
                continue

            if self._evaluate_condition(rule.condition, alert, context):
                return rule, rule.action

        return None, ""

    def _evaluate_condition(
        self,
        condition: str,
        alert: RawAlert,
        context: dict[str, Any],
    ) -> bool:
        """Evaluate a rule condition."""
        now = datetime.utcnow()
        variables = {
            "severity": alert.severity.value,
            "service": alert.service,
            "hour": now.hour,
            "day": now.strftime("%a").lower(),
            "alert_count": context.get("alert_count", 1),
            "window": context.get("window", "1h"),
        }

        condition = condition.lower()

        if "severity = low" in condition and variables["severity"] != "low":
            return False

        if "hour between" in condition:
            match = re.search(r"hour between (\d+) and (\d+)", condition)
            if match:
                start, end = int(match.group(1)), int(match.group(2))
                hour = variables["hour"]
                if start > end:
                    if not (hour >= start or hour <= end):
                        return False
                else:
                    if not (start <= hour <= end):
                        return False

        if "day in" in condition:
            match = re.search(r"day in \(([^)]+)\)", condition)
            if match:
                days = [d.strip() for d in match.group(1).split(",")]
                if variables["day"] not in days:
                    return False

        if "alert_count >" in condition:
            match = re.search(r"alert_count > (\d+)", condition)
            if match:
                threshold = int(match.group(1))
                if variables["alert_count"] <= threshold:
                    return False

        return True

    def get_all_rules(self) -> list[BusinessRule]:
        """Get all business rules."""
        return self._rules.copy()


class SuppressionEngine:
    """Main suppression engine combining all suppression sources."""

    def __init__(self, config: SuppressionConfig | None = None):
        self.config = config or SuppressionConfig()
        self._custom_rules: dict[UUID, SuppressionRule] = {}
        self._flapping_detector = FlappingDetector(config)
        self._maintenance_manager = MaintenanceWindowManager()
        self._known_issues = KnownIssueTracker()
        self._business_rules = BusinessRuleEngine()
        self._alert_counts: dict[str, list[datetime]] = {}

    def check_suppression(
        self,
        alert: RawAlert,
        fingerprint: str,
        context: dict[str, Any] | None = None,
    ) -> SuppressionResult:
        """Check if alert should be suppressed."""
        context = context or {}

        in_maintenance, window = self._maintenance_manager.is_service_in_maintenance(
            alert.service
        )
        if in_maintenance and window:
            window.alerts_suppressed += 1
            return SuppressionResult(
                should_suppress=True,
                reason=f"Service in maintenance: {window.name}",
                rule_id=window.window_id,
                rule_name=window.name,
                suppression_type=SuppressionType.MAINTENANCE,
                expires_at=window.extended_until or window.ends_at,
            )

        known_issue = self._known_issues.find_matching_issue(alert)
        if known_issue:
            known_issue.alerts_suppressed += 1
            return SuppressionResult(
                should_suppress=True,
                reason=f"Known issue: {known_issue.title}",
                rule_id=known_issue.issue_id,
                rule_name=known_issue.title,
                suppression_type=SuppressionType.KNOWN_ISSUE,
                expires_at=known_issue.expires_at,
            )

        if self._flapping_detector.is_suppressed(fingerprint):
            return SuppressionResult(
                should_suppress=True,
                reason="Flapping alert suppressed",
                suppression_type=SuppressionType.FLAPPING,
            )

        for rule in self._custom_rules.values():
            if rule.is_active() and rule.matches(alert):
                rule.alerts_suppressed += 1
                return SuppressionResult(
                    should_suppress=True,
                    reason=f"Custom rule: {rule.name}",
                    rule_id=rule.rule_id,
                    rule_name=rule.name,
                    suppression_type=rule.suppression_type,
                    expires_at=rule.expires_at,
                )

        alert_count = self._get_alert_count(fingerprint)
        context["alert_count"] = alert_count

        business_rule, action = self._business_rules.evaluate(alert, context)
        if business_rule and action == "suppress":
            return SuppressionResult(
                should_suppress=True,
                reason=f"Business rule: {business_rule.name}",
                rule_id=business_rule.rule_id,
                rule_name=business_rule.name,
                suppression_type=SuppressionType.BUSINESS_RULE,
            )

        return SuppressionResult(
            should_suppress=False,
            reason="No suppression rules matched",
        )

    def _get_alert_count(self, fingerprint: str) -> int:
        """Get recent alert count for fingerprint."""
        if fingerprint not in self._alert_counts:
            self._alert_counts[fingerprint] = []

        now = datetime.utcnow()
        cutoff = now - timedelta(hours=1)
        self._alert_counts[fingerprint] = [
            t for t in self._alert_counts[fingerprint] if t > cutoff
        ]

        return len(self._alert_counts[fingerprint])

    def record_alert(self, fingerprint: str) -> None:
        """Record alert occurrence."""
        if fingerprint not in self._alert_counts:
            self._alert_counts[fingerprint] = []
        self._alert_counts[fingerprint].append(datetime.utcnow())

    def record_state_change(
        self,
        fingerprint: str,
        state: str,
        alert: RawAlert,
    ) -> FlappingAlert:
        """Record alert state change for flapping detection."""
        return self._flapping_detector.record_state_change(
            fingerprint=fingerprint,
            timestamp=alert.timestamp,
            state=state,
            service=alert.service,
            alert_name=alert.alert_name,
        )

    def add_custom_rule(self, rule: SuppressionRule) -> None:
        """Add custom suppression rule."""
        self._custom_rules[rule.rule_id] = rule

    def remove_custom_rule(self, rule_id: UUID) -> bool:
        """Remove custom suppression rule."""
        if rule_id in self._custom_rules:
            del self._custom_rules[rule_id]
            return True
        return False

    @property
    def maintenance_windows(self) -> MaintenanceWindowManager:
        """Get maintenance window manager."""
        return self._maintenance_manager

    @property
    def known_issues(self) -> KnownIssueTracker:
        """Get known issue tracker."""
        return self._known_issues

    @property
    def flapping(self) -> FlappingDetector:
        """Get flapping detector."""
        return self._flapping_detector

    @property
    def business_rules(self) -> BusinessRuleEngine:
        """Get business rule engine."""
        return self._business_rules

    def get_suppression_stats(self) -> dict[str, Any]:
        """Get suppression statistics."""
        active_maintenance = self._maintenance_manager.get_active_windows()
        active_issues = self._known_issues.get_active_issues()
        flapping_alerts = self._flapping_detector.get_all_flapping()
        active_rules = [r for r in self._custom_rules.values() if r.is_active()]

        total_suppressed = (
            sum(w.alerts_suppressed for w in active_maintenance)
            + sum(i.alerts_suppressed for i in active_issues)
            + sum(r.alerts_suppressed for r in active_rules)
        )

        return {
            "active_maintenance_windows": len(active_maintenance),
            "active_known_issues": len(active_issues),
            "flapping_alerts": len(flapping_alerts),
            "active_custom_rules": len(active_rules),
            "total_alerts_suppressed": total_suppressed,
        }
