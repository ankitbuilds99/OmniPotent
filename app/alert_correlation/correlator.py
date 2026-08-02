"""Alert Correlation - Intelligent alert grouping and deduplication."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from enum import Enum

log = logging.getLogger("omnipotent.alert_correlation.correlator")


class AlertSeverity(str, Enum):
    """Alert severity levels."""
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class CorrelationType(str, Enum):
    """Types of alert correlations."""
    TEMPORAL = "temporal"
    CAUSAL = "causal"
    TOPOLOGICAL = "topological"
    SYMPTOM_ROOT = "symptom_root"


@dataclass
class Alert:
    """An alert event."""
    id: str
    name: str
    severity: AlertSeverity
    service: str
    description: str
    timestamp: datetime
    labels: dict[str, str] = field(default_factory=dict)
    annotations: dict[str, str] = field(default_factory=dict)
    fingerprint: str = ""
    status: str = "firing"  # firing, resolved

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "severity": self.severity.value,
            "service": self.service,
            "description": self.description,
            "timestamp": self.timestamp.isoformat(),
            "labels": self.labels,
            "status": self.status,
        }


@dataclass
class AlertGroup:
    """A group of correlated alerts."""
    id: str
    name: str
    alerts: list[Alert]
    root_cause_alert: Alert | None
    correlation_type: CorrelationType
    correlation_confidence: float
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    services_affected: list[str] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "alert_count": len(self.alerts),
            "alerts": [a.to_dict() for a in self.alerts],
            "root_cause_alert": self.root_cause_alert.to_dict() if self.root_cause_alert else None,
            "correlation_type": self.correlation_type.value,
            "correlation_confidence": self.correlation_confidence,
            "services_affected": self.services_affected,
            "summary": self.summary,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class CorrelationRule:
    """A rule for correlating alerts."""
    name: str
    description: str
    match_labels: dict[str, str]  # Labels that must match
    time_window_seconds: int = 300
    min_alerts: int = 2
    priority: int = 0


class AlertCorrelator:
    """Correlates and groups related alerts.

    Features:
    - Temporal correlation (alerts close in time)
    - Causal correlation (dependency-based)
    - Deduplication
    - Root cause identification
    - Alert suppression
    """

    # Default correlation windows
    TEMPORAL_WINDOW = timedelta(minutes=5)
    CAUSAL_WINDOW = timedelta(minutes=15)

    def __init__(self):
        """Initialize the alert correlator."""
        self._pending_alerts: list[Alert] = []
        self._groups: dict[str, AlertGroup] = {}
        self._rules: list[CorrelationRule] = []
        self._dependency_map: dict[str, list[str]] = {}
        self._suppression_rules: list[dict[str, Any]] = []

        # Add default rules
        self._add_default_rules()

        log.info("AlertCorrelator initialized")

    def _add_default_rules(self) -> None:
        """Add default correlation rules."""
        self._rules.extend([
            CorrelationRule(
                name="same_service",
                description="Group alerts from the same service",
                match_labels={"service": "*"},
                time_window_seconds=300,
            ),
            CorrelationRule(
                name="database_cascade",
                description="Database issues cause downstream failures",
                match_labels={"type": "database"},
                time_window_seconds=600,
                priority=10,
            ),
        ])

    def add_dependency(self, service: str, depends_on: list[str]) -> None:
        """Add service dependency information.

        Args:
            service: Service name
            depends_on: Services it depends on
        """
        self._dependency_map[service] = depends_on

    async def process_alert(self, alert: Alert) -> AlertGroup | None:
        """Process an incoming alert.

        Args:
            alert: Alert to process

        Returns:
            AlertGroup if alert was correlated, None otherwise
        """
        log.debug("Processing alert: %s from %s", alert.name, alert.service)

        # Check if alert is suppressed
        if self._is_suppressed(alert):
            log.info("Alert suppressed: %s", alert.id)
            return None

        # Try to find existing group
        existing_group = self._find_matching_group(alert)

        if existing_group:
            # Add to existing group
            existing_group.alerts.append(alert)
            existing_group.services_affected = list(set(
                existing_group.services_affected + [alert.service]
            ))
            self._update_root_cause(existing_group)
            log.info("Added alert to group: %s", existing_group.id)
            return existing_group

        # Check correlation with pending alerts
        correlated = self._find_correlated_alerts(alert)

        if correlated:
            # Create new group
            alerts = correlated + [alert]
            group = self._create_group(alerts)
            log.info("Created new alert group: %s with %d alerts", group.id, len(alerts))
            return group

        # Add to pending
        self._pending_alerts.append(alert)
        self._cleanup_pending()

        return None

    def _is_suppressed(self, alert: Alert) -> bool:
        """Check if alert should be suppressed."""
        for rule in self._suppression_rules:
            # Check if any suppression rule matches
            if self._matches_labels(alert.labels, rule.get("match_labels", {})):
                return True
        return False

    def _matches_labels(self, labels: dict[str, str], match: dict[str, str]) -> bool:
        """Check if labels match a pattern."""
        for key, pattern in match.items():
            if key not in labels:
                return False
            if pattern != "*" and labels[key] != pattern:
                return False
        return True

    def _find_matching_group(self, alert: Alert) -> AlertGroup | None:
        """Find an existing group that matches this alert."""
        for group in self._groups.values():
            # Check time window
            if group.alerts:
                latest = max(a.timestamp for a in group.alerts)
                if alert.timestamp - latest > self.TEMPORAL_WINDOW:
                    continue

            # Check service correlation
            if alert.service in group.services_affected:
                return group

            # Check dependency correlation
            for affected in group.services_affected:
                deps = self._dependency_map.get(affected, [])
                if alert.service in deps:
                    return group

        return None

    def _find_correlated_alerts(self, alert: Alert) -> list[Alert]:
        """Find pending alerts that correlate with this one."""
        correlated = []

        for pending in self._pending_alerts:
            # Temporal correlation
            time_diff = abs((alert.timestamp - pending.timestamp).total_seconds())
            if time_diff <= self.TEMPORAL_WINDOW.total_seconds():
                # Same service
                if alert.service == pending.service:
                    correlated.append(pending)
                    continue

                # Dependency relationship
                deps = self._dependency_map.get(alert.service, [])
                if pending.service in deps:
                    correlated.append(pending)
                    continue

                reverse_deps = self._dependency_map.get(pending.service, [])
                if alert.service in reverse_deps:
                    correlated.append(pending)
                    continue

        # Remove correlated from pending
        for c in correlated:
            if c in self._pending_alerts:
                self._pending_alerts.remove(c)

        return correlated

    def _create_group(self, alerts: list[Alert]) -> AlertGroup:
        """Create a new alert group."""
        # Determine correlation type
        services = set(a.service for a in alerts)
        if len(services) == 1:
            correlation_type = CorrelationType.TEMPORAL
        else:
            correlation_type = CorrelationType.CAUSAL

        # Calculate confidence
        confidence = self._calculate_correlation_confidence(alerts)

        # Identify root cause
        root_cause = self._identify_root_cause(alerts)

        # Generate summary
        summary = self._generate_summary(alerts, root_cause)

        group = AlertGroup(
            id=f"grp-{alerts[0].id}",
            name=f"Alert Group: {alerts[0].name}",
            alerts=alerts,
            root_cause_alert=root_cause,
            correlation_type=correlation_type,
            correlation_confidence=confidence,
            services_affected=list(services),
            summary=summary,
        )

        self._groups[group.id] = group
        return group

    def _calculate_correlation_confidence(self, alerts: list[Alert]) -> float:
        """Calculate correlation confidence."""
        if len(alerts) < 2:
            return 0.5

        # Factors:
        # - Time proximity
        # - Service relationships
        # - Alert similarity

        confidence = 0.5

        # Time proximity
        timestamps = [a.timestamp for a in alerts]
        time_range = (max(timestamps) - min(timestamps)).total_seconds()
        if time_range < 60:
            confidence += 0.3
        elif time_range < 300:
            confidence += 0.2

        # Service relationships
        services = [a.service for a in alerts]
        if len(set(services)) == 1:
            confidence += 0.2  # Same service
        else:
            # Check dependency relationships
            has_dependency = False
            for s1 in services:
                deps = self._dependency_map.get(s1, [])
                for s2 in services:
                    if s2 in deps:
                        has_dependency = True
                        break
            if has_dependency:
                confidence += 0.15

        return min(1.0, confidence)

    def _identify_root_cause(self, alerts: list[Alert]) -> Alert | None:
        """Identify the likely root cause alert."""
        if not alerts:
            return None

        # Sort by timestamp (earliest first)
        sorted_alerts = sorted(alerts, key=lambda a: a.timestamp)

        # Check for infrastructure/database alerts (likely root cause)
        for alert in sorted_alerts:
            service_lower = alert.service.lower()
            if any(infra in service_lower for infra in ["database", "postgres", "redis", "kafka"]):
                return alert

        # Check for dependency hierarchy
        for alert in sorted_alerts:
            dependents = []
            for a in alerts:
                if a == alert:
                    continue
                deps = self._dependency_map.get(a.service, [])
                if alert.service in deps:
                    dependents.append(a)

            if len(dependents) >= len(alerts) // 2:
                return alert

        # Default to earliest alert
        return sorted_alerts[0]

    def _update_root_cause(self, group: AlertGroup) -> None:
        """Update root cause for a group."""
        group.root_cause_alert = self._identify_root_cause(group.alerts)
        group.summary = self._generate_summary(group.alerts, group.root_cause_alert)

    def _generate_summary(self, alerts: list[Alert], root_cause: Alert | None) -> str:
        """Generate summary for an alert group."""
        services = list(set(a.service for a in alerts))
        severities = [a.severity for a in alerts]
        max_severity = max(severities, key=lambda s: ["info", "low", "medium", "high", "critical"].index(s.value))

        summary = f"{len(alerts)} related alerts across {len(services)} services. "
        summary += f"Max severity: {max_severity.value}. "

        if root_cause:
            summary += f"Likely root cause: {root_cause.name} ({root_cause.service})"

        return summary

    def _cleanup_pending(self) -> None:
        """Remove old pending alerts."""
        cutoff = datetime.now(timezone.utc) - self.TEMPORAL_WINDOW * 2
        self._pending_alerts = [
            a for a in self._pending_alerts
            if a.timestamp > cutoff
        ]

    def add_suppression_rule(
        self,
        name: str,
        match_labels: dict[str, str],
        duration_minutes: int,
    ) -> None:
        """Add an alert suppression rule.

        Args:
            name: Rule name
            match_labels: Labels to match for suppression
            duration_minutes: How long to suppress
        """
        self._suppression_rules.append({
            "name": name,
            "match_labels": match_labels,
            "expires_at": datetime.now(timezone.utc) + timedelta(minutes=duration_minutes),
        })
        log.info("Added suppression rule: %s for %d minutes", name, duration_minutes)

    def get_groups(self, status: str | None = None) -> list[AlertGroup]:
        """Get all alert groups."""
        groups = list(self._groups.values())

        if status:
            groups = [
                g for g in groups
                if any(a.status == status for a in g.alerts)
            ]

        return groups

    def get_stats(self) -> dict[str, Any]:
        """Get correlator statistics."""
        groups = list(self._groups.values())

        return {
            "total_groups": len(groups),
            "total_alerts": sum(len(g.alerts) for g in groups),
            "pending_alerts": len(self._pending_alerts),
            "suppression_rules": len(self._suppression_rules),
            "correlation_types": {
                ct.value: len([g for g in groups if g.correlation_type == ct])
                for ct in CorrelationType
            },
        }


def create_alert_correlator() -> AlertCorrelator:
    """Factory function to create an alert correlator."""
    return AlertCorrelator()
