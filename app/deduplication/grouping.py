"""Incident grouping, merge/split, and feedback learning."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from app.deduplication.models import (
    RawAlert,
    IncidentGroup,
    AlertSeverity,
)

log = logging.getLogger("devcopilot.deduplication.grouping")


@dataclass
class GroupingConfig:
    """Configuration for incident grouping."""

    auto_group_threshold: float = 0.9
    suggest_merge_threshold: float = 0.7
    suggest_split_threshold: float = 0.3
    min_alerts_for_split: int = 3
    max_group_size: int = 100


@dataclass
class GroupingSuggestion:
    """Suggestion for grouping action."""

    action: str
    incident_ids: list[UUID]
    confidence: float
    reason: str
    ai_analysis: str = ""


@dataclass
class FeedbackEntry:
    """Record of user feedback on grouping."""

    feedback_id: UUID = field(default_factory=uuid4)
    action: str = ""
    incident_ids: list[UUID] = field(default_factory=list)
    user: str = ""
    timestamp: datetime = field(default_factory=datetime.utcnow)
    was_correct: bool = True
    notes: str = ""


class IncidentGrouper:
    """Manage incident grouping decisions."""

    def __init__(
        self,
        config: GroupingConfig | None = None,
    ):
        self.config = config or GroupingConfig()
        self._incidents: dict[UUID, IncidentGroup] = {}
        self._alert_index: dict[UUID, UUID] = {}

    def register_incident(self, incident: IncidentGroup) -> None:
        """Register an incident for grouping analysis."""
        self._incidents[incident.incident_id] = incident
        for alert_id in incident.alerts:
            self._alert_index[alert_id] = incident.incident_id

    def suggest_grouping(
        self,
        alert: RawAlert,
        active_incidents: list[IncidentGroup],
    ) -> GroupingSuggestion | None:
        """Suggest grouping for a new alert."""
        if not active_incidents:
            return None

        best_match: IncidentGroup | None = None
        best_score = 0.0
        best_reason = ""

        for incident in active_incidents:
            if len(incident.alerts) >= self.config.max_group_size:
                continue

            score, reason = self._calculate_grouping_score(alert, incident)
            if score > best_score:
                best_score = score
                best_match = incident
                best_reason = reason

        if not best_match:
            return None

        if best_score >= self.config.auto_group_threshold:
            return GroupingSuggestion(
                action="add_to_incident",
                incident_ids=[best_match.incident_id],
                confidence=best_score,
                reason=best_reason,
                ai_analysis=self._generate_analysis(alert, best_match, best_score),
            )
        elif best_score >= self.config.suggest_merge_threshold:
            return GroupingSuggestion(
                action="suggest_add",
                incident_ids=[best_match.incident_id],
                confidence=best_score,
                reason=best_reason,
                ai_analysis=self._generate_analysis(alert, best_match, best_score),
            )

        return None

    def _calculate_grouping_score(
        self,
        alert: RawAlert,
        incident: IncidentGroup,
    ) -> tuple[float, str]:
        """Calculate grouping score between alert and incident."""
        score = 0.0
        reasons = []

        if alert.service and incident.root_cause_service:
            if alert.service == incident.root_cause_service:
                score += 0.4
                reasons.append("same root cause service")

        time_since_start = (
            alert.timestamp - incident.created_at
        ).total_seconds()
        if time_since_start < 300:
            score += 0.3
            reasons.append("within 5 minutes")
        elif time_since_start < 900:
            score += 0.2
            reasons.append("within 15 minutes")
        elif time_since_start < 1800:
            score += 0.1

        severity_match = {
            (AlertSeverity.CRITICAL, AlertSeverity.CRITICAL): 0.15,
            (AlertSeverity.CRITICAL, AlertSeverity.HIGH): 0.10,
            (AlertSeverity.HIGH, AlertSeverity.CRITICAL): 0.10,
            (AlertSeverity.HIGH, AlertSeverity.HIGH): 0.15,
        }
        sev_score = severity_match.get((alert.severity, incident.severity), 0.05)
        score += sev_score

        if incident.ai_analysis and alert.service:
            if alert.service.lower() in incident.ai_analysis.lower():
                score += 0.15
                reasons.append("mentioned in incident analysis")

        return min(score, 1.0), ", ".join(reasons)

    def _generate_analysis(
        self,
        alert: RawAlert,
        incident: IncidentGroup,
        score: float,
    ) -> str:
        """Generate AI-style analysis for grouping decision."""
        parts = []
        parts.append(
            f"Recommendation: Add to {incident.incident_id} "
            f"(confidence: {score:.0%})"
        )
        parts.append("")
        parts.append("Reasons:")

        if alert.service == incident.root_cause_service:
            parts.append(
                f"- {incident.incident_id} is about {incident.root_cause_service} issues (match)"
            )

        time_diff = (alert.timestamp - incident.created_at).total_seconds()
        parts.append(f"- Timing: {time_diff/60:.1f} min after incident started")

        if incident.grouping_reason:
            parts.append("- Similar past incidents grouped this way")

        return "\n".join(parts)


class MergeSplitManager:
    """Manage incident merge and split operations."""

    def __init__(self):
        self._merge_history: list[dict[str, Any]] = []
        self._split_history: list[dict[str, Any]] = []

    def analyze_merge(
        self,
        incidents: list[IncidentGroup],
    ) -> dict[str, Any]:
        """Analyze whether incidents should be merged."""
        if len(incidents) < 2:
            return {"should_merge": False, "reason": "Need at least 2 incidents"}

        services = set()
        for inc in incidents:
            if inc.root_cause_service:
                services.add(inc.root_cause_service)

        all_alerts = []
        for inc in incidents:
            all_alerts.extend(inc.alerts)

        timestamps = []
        for inc in incidents:
            timestamps.append(inc.created_at)

        time_span = (max(timestamps) - min(timestamps)).total_seconds()

        confidence = 0.0
        reasons = []

        if len(services) == 1:
            confidence += 0.4
            reasons.append(f"Same root cause service: {list(services)[0]}")

        if time_span < 300:
            confidence += 0.3
            reasons.append(f"Close in time: {time_span/60:.1f} minutes apart")
        elif time_span < 900:
            confidence += 0.2

        if any("dependency" in (inc.grouping_reason or "").lower() for inc in incidents):
            confidence += 0.2
            reasons.append("Dependency relationship detected")

        return {
            "should_merge": confidence >= 0.7,
            "confidence": confidence,
            "reasons": reasons,
            "services_involved": list(services),
            "total_alerts": len(all_alerts),
            "time_span_minutes": time_span / 60,
        }

    def merge_incidents(
        self,
        incidents: list[IncidentGroup],
        merged_by: str = "",
    ) -> IncidentGroup:
        """Merge multiple incidents into one."""
        if len(incidents) < 2:
            raise ValueError("Need at least 2 incidents to merge")

        incidents.sort(key=lambda i: i.created_at)
        primary = incidents[0]

        severity_order = {
            AlertSeverity.CRITICAL: 4,
            AlertSeverity.HIGH: 3,
            AlertSeverity.MEDIUM: 2,
            AlertSeverity.LOW: 1,
            AlertSeverity.INFO: 0,
        }
        max_severity = max(
            incidents, key=lambda i: severity_order.get(i.severity, 0)
        ).severity

        merged = IncidentGroup(
            title=primary.title,
            alerts=list(primary.alerts),
            root_cause_alert_id=primary.root_cause_alert_id,
            root_cause_service=primary.root_cause_service,
            severity=max_severity,
            created_at=primary.created_at,
            grouping_reason=f"Merged from {len(incidents)} incidents",
            merged_from=[i.incident_id for i in incidents],
        )

        for inc in incidents[1:]:
            for alert_id in inc.alerts:
                if alert_id not in merged.alerts:
                    merged.alerts.append(alert_id)

        self._merge_history.append({
            "timestamp": datetime.utcnow().isoformat(),
            "merged_from": [str(i.incident_id) for i in incidents],
            "merged_into": str(merged.incident_id),
            "merged_by": merged_by,
            "total_alerts": len(merged.alerts),
        })

        log.info(
            "Merged %d incidents into %s",
            len(incidents),
            merged.incident_id,
        )

        return merged

    def analyze_split(
        self,
        incident: IncidentGroup,
        alerts: list[RawAlert],
    ) -> dict[str, Any]:
        """Analyze whether incident should be split."""
        if len(alerts) < 3:
            return {"should_split": False, "reason": "Too few alerts to split"}

        service_groups: dict[str, list[UUID]] = {}
        for alert in alerts:
            if alert.service not in service_groups:
                service_groups[alert.service] = []
            service_groups[alert.service].append(alert.alert_id)

        if len(service_groups) < 2:
            return {"should_split": False, "reason": "All alerts from same service"}

        min_group_size = min(len(g) for g in service_groups.values())
        if min_group_size < 2:
            return {
                "should_split": False,
                "reason": "One group too small",
            }

        return {
            "should_split": True,
            "confidence": 0.7,
            "suggested_groups": [
                {"service": svc, "alert_count": len(aids)}
                for svc, aids in service_groups.items()
            ],
            "reason": f"Found {len(service_groups)} distinct service groups",
        }

    def split_incident(
        self,
        incident: IncidentGroup,
        alerts: list[RawAlert],
        group_by: str = "service",
        split_by: str = "",
    ) -> list[IncidentGroup]:
        """Split incident into multiple incidents."""
        groups: dict[str, list[RawAlert]] = {}

        for alert in alerts:
            if group_by == "service":
                key = alert.service
            elif group_by == "severity":
                key = alert.severity.value
            else:
                key = "default"

            if key not in groups:
                groups[key] = []
            groups[key].append(alert)

        severity_order = {
            AlertSeverity.CRITICAL: 4,
            AlertSeverity.HIGH: 3,
            AlertSeverity.MEDIUM: 2,
            AlertSeverity.LOW: 1,
            AlertSeverity.INFO: 0,
        }

        new_incidents = []
        for key, group_alerts in groups.items():
            max_sev = max(
                group_alerts, key=lambda a: severity_order.get(a.severity, 0)
            ).severity
            new_incident = IncidentGroup(
                title=f"{key}: {incident.title}",
                alerts=[a.alert_id for a in group_alerts],
                root_cause_alert_id=group_alerts[0].alert_id,
                root_cause_service=group_alerts[0].service,
                severity=max_sev,
                split_from=incident.incident_id,
                grouping_reason=f"Split by {group_by}: {key}",
            )
            new_incidents.append(new_incident)

        self._split_history.append({
            "timestamp": datetime.utcnow().isoformat(),
            "split_from": str(incident.incident_id),
            "split_into": [str(i.incident_id) for i in new_incidents],
            "split_by": split_by,
            "group_by": group_by,
        })

        log.info(
            "Split incident %s into %d incidents",
            incident.incident_id,
            len(new_incidents),
        )

        return new_incidents

    def get_merge_history(self) -> list[dict[str, Any]]:
        """Get merge history."""
        return self._merge_history.copy()

    def get_split_history(self) -> list[dict[str, Any]]:
        """Get split history."""
        return self._split_history.copy()


class GroupingFeedback:
    """Track and learn from user feedback on grouping decisions."""

    def __init__(self):
        self._feedback: list[FeedbackEntry] = []
        self._learned_patterns: dict[str, float] = {}

    def record_feedback(
        self,
        action: str,
        incident_ids: list[UUID],
        user: str,
        was_correct: bool,
        notes: str = "",
    ) -> FeedbackEntry:
        """Record user feedback on a grouping decision."""
        entry = FeedbackEntry(
            action=action,
            incident_ids=incident_ids,
            user=user,
            was_correct=was_correct,
            notes=notes,
        )
        self._feedback.append(entry)

        self._update_learned_patterns(entry)

        return entry

    def _update_learned_patterns(self, entry: FeedbackEntry) -> None:
        """Update learned patterns from feedback."""
        pattern_key = f"{entry.action}:{len(entry.incident_ids)}"

        if pattern_key not in self._learned_patterns:
            self._learned_patterns[pattern_key] = 0.5

        current = self._learned_patterns[pattern_key]
        if entry.was_correct:
            self._learned_patterns[pattern_key] = current * 0.9 + 0.1
        else:
            self._learned_patterns[pattern_key] = current * 0.9

    def get_accuracy_stats(self) -> dict[str, Any]:
        """Get accuracy statistics from feedback."""
        if not self._feedback:
            return {"total_feedback": 0, "accuracy": 0.0}

        correct = sum(1 for f in self._feedback if f.was_correct)
        total = len(self._feedback)

        by_action: dict[str, dict[str, int]] = {}
        for f in self._feedback:
            if f.action not in by_action:
                by_action[f.action] = {"correct": 0, "total": 0}
            by_action[f.action]["total"] += 1
            if f.was_correct:
                by_action[f.action]["correct"] += 1

        return {
            "total_feedback": total,
            "accuracy": correct / total if total > 0 else 0.0,
            "by_action": {
                action: stats["correct"] / stats["total"] if stats["total"] > 0 else 0.0
                for action, stats in by_action.items()
            },
            "learned_patterns": self._learned_patterns,
        }

    def should_auto_group(self, context: dict[str, Any]) -> float:
        """Get learned confidence for auto-grouping."""
        base_confidence = context.get("base_confidence", 0.5)

        action = context.get("action", "add_to_incident")
        pattern_key = f"{action}:{context.get('incident_count', 1)}"

        if pattern_key in self._learned_patterns:
            learned = self._learned_patterns[pattern_key]
            return (base_confidence + learned) / 2

        return base_confidence

    def get_recent_feedback(
        self,
        limit: int = 10,
    ) -> list[FeedbackEntry]:
        """Get recent feedback entries."""
        return sorted(
            self._feedback,
            key=lambda f: f.timestamp,
            reverse=True,
        )[:limit]
