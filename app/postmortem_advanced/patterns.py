"""Pattern detection and similar incident analysis."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from app.postmortem_advanced.models import (
    SimilarIncident,
    ActionItem,
    ActionItemStatus,
    LessonScope,
)

log = logging.getLogger("devcopilot.postmortem_advanced.patterns")


class PatternType(str, Enum):
    RESOURCE_LEAK = "resource_leak"
    CONNECTION_EXHAUSTION = "connection_exhaustion"
    MEMORY_LEAK = "memory_leak"
    DEPLOY_RELATED = "deploy_related"
    CONFIGURATION_ERROR = "configuration_error"
    DEPENDENCY_FAILURE = "dependency_failure"
    CAPACITY_EXCEEDED = "capacity_exceeded"
    TIMEOUT_CASCADE = "timeout_cascade"
    DATA_CORRUPTION = "data_corruption"
    AUTHENTICATION_FAILURE = "authentication_failure"


@dataclass
class RecurringPattern:
    """A recurring incident pattern."""

    pattern_type: PatternType
    description: str
    incident_ids: list[str]
    occurrence_count: int
    first_seen: datetime
    last_seen: datetime
    common_factors: list[str]
    unfulfilled_actions: list[str]
    recommended_systemic_fix: str
    severity_trend: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.pattern_type.value,
            "description": self.description,
            "incident_ids": self.incident_ids,
            "occurrences": self.occurrence_count,
            "timeline": {
                "first_seen": self.first_seen.isoformat(),
                "last_seen": self.last_seen.isoformat(),
            },
            "common_factors": self.common_factors,
            "unfulfilled_actions": self.unfulfilled_actions,
            "recommended_fix": self.recommended_systemic_fix,
            "severity_trend": self.severity_trend,
        }


@dataclass
class SimilarIncidentResult:
    """Result of similar incident search."""

    incidents: list[SimilarIncident]
    patterns_detected: list[RecurringPattern]
    total_searched: int
    has_unfulfilled_actions: bool
    recommendations: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "similar_incidents": [i.to_dict() for i in self.incidents],
            "patterns": [p.to_dict() for p in self.patterns_detected],
            "total_searched": self.total_searched,
            "has_unfulfilled_actions": self.has_unfulfilled_actions,
            "recommendations": self.recommendations,
        }


@dataclass
class OrganizationalInsight:
    """An insight for organizational learning."""

    insight_id: str
    title: str
    description: str
    scope: LessonScope
    evidence: list[str]
    suggested_actions: list[str]
    affected_teams: list[str]
    priority: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.insight_id,
            "title": self.title,
            "description": self.description,
            "scope": self.scope.value,
            "evidence": self.evidence,
            "suggested_actions": self.suggested_actions,
            "affected_teams": self.affected_teams,
            "priority": self.priority,
        }


@dataclass
class IncidentRecord:
    """Internal record of an incident for pattern matching."""

    incident_id: str
    title: str
    severity: str
    service: str
    error_message: str
    root_cause: str
    created_at: datetime
    resolved_at: datetime | None
    mttr_minutes: float
    action_items: list[ActionItem] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)


class PatternDetector:
    """Detects patterns and finds similar incidents."""

    def __init__(self) -> None:
        self._incidents: list[IncidentRecord] = []
        self._patterns: list[RecurringPattern] = []
        self._insight_counter = 0

        self._pattern_signatures: dict[PatternType, list[str]] = {
            PatternType.RESOURCE_LEAK: [
                r"connection.*exhaust",
                r"pool.*full",
                r"leak",
                r"not.*release",
            ],
            PatternType.CONNECTION_EXHAUSTION: [
                r"connection.*pool",
                r"max.*connection",
                r"no.*available.*connection",
            ],
            PatternType.MEMORY_LEAK: [
                r"memory",
                r"oom",
                r"out.of.memory",
                r"heap",
            ],
            PatternType.DEPLOY_RELATED: [
                r"deploy",
                r"release",
                r"rollout",
                r"upgrade",
            ],
            PatternType.CONFIGURATION_ERROR: [
                r"config",
                r"setting",
                r"env",
                r"flag",
            ],
            PatternType.DEPENDENCY_FAILURE: [
                r"downstream",
                r"upstream",
                r"dependency",
                r"external.*service",
            ],
            PatternType.CAPACITY_EXCEEDED: [
                r"capacity",
                r"limit",
                r"throttl",
                r"rate.*limit",
            ],
            PatternType.TIMEOUT_CASCADE: [
                r"timeout",
                r"deadline",
                r"cascade",
            ],
        }

    def record_incident(self, incident: IncidentRecord) -> None:
        """Record an incident for pattern analysis."""
        self._incidents.append(incident)

        if len(self._incidents) > 10000:
            cutoff = datetime.now(timezone.utc) - timedelta(days=730)
            self._incidents = [
                i for i in self._incidents if i.created_at >= cutoff
            ]

    def find_similar_incidents(
        self,
        current_incident: dict[str, Any],
        max_results: int = 5,
        min_similarity: float = 0.5,
    ) -> SimilarIncidentResult:
        """Find incidents similar to the current one."""
        error_message = current_incident.get("error_message", "")
        root_cause = current_incident.get("root_cause", "")
        service = current_incident.get("service", "")
        tags = current_incident.get("tags", [])

        scored_incidents = []

        for record in self._incidents:
            if record.incident_id == current_incident.get("incident_id"):
                continue

            score = self._calculate_similarity(
                error_message=error_message,
                root_cause=root_cause,
                service=service,
                tags=tags,
                record=record,
            )

            if score >= min_similarity:
                scored_incidents.append((score, record))

        scored_incidents.sort(key=lambda x: x[0], reverse=True)
        top_incidents = scored_incidents[:max_results]

        similar = []
        for score, record in top_incidents:
            matching = self._get_matching_factors(
                error_message, root_cause, service, record
            )
            differing = self._get_differing_factors(
                error_message, root_cause, service, record
            )

            unfulfilled = [
                a for a in record.action_items
                if a.status == ActionItemStatus.OPEN
            ]

            similar.append(SimilarIncident(
                incident_id=record.incident_id,
                title=record.title,
                date=record.created_at,
                similarity_score=score,
                matching_factors=matching,
                differing_factors=differing,
                root_cause=record.root_cause,
                resolution="Hotfix deployed",
                unfulfilled_action_items=unfulfilled,
                lessons_applicable=self._get_applicable_lessons(record),
                mttr_minutes=record.mttr_minutes,
            ))

        patterns = self._detect_patterns_from_similar(similar)

        has_unfulfilled = any(
            len(i.unfulfilled_action_items) > 0 for i in similar
        )

        recommendations = self._generate_recommendations(similar, patterns)

        return SimilarIncidentResult(
            incidents=similar,
            patterns_detected=patterns,
            total_searched=len(self._incidents),
            has_unfulfilled_actions=has_unfulfilled,
            recommendations=recommendations,
        )

    def _calculate_similarity(
        self,
        error_message: str,
        root_cause: str,
        service: str,
        tags: list[str],
        record: IncidentRecord,
    ) -> float:
        """Calculate similarity score between current and historical incident."""
        score = 0.0

        if service == record.service:
            score += 0.3

        error_words = set(error_message.lower().split())
        record_error_words = set(record.error_message.lower().split())
        if error_words and record_error_words:
            overlap = len(error_words & record_error_words)
            union = len(error_words | record_error_words)
            if union > 0:
                score += 0.3 * (overlap / union)

        cause_words = set(root_cause.lower().split())
        record_cause_words = set(record.root_cause.lower().split())
        if cause_words and record_cause_words:
            overlap = len(cause_words & record_cause_words)
            union = len(cause_words | record_cause_words)
            if union > 0:
                score += 0.25 * (overlap / union)

        if tags and record.tags:
            tag_overlap = len(set(tags) & set(record.tags))
            tag_union = len(set(tags) | set(record.tags))
            if tag_union > 0:
                score += 0.15 * (tag_overlap / tag_union)

        return min(1.0, score)

    def _get_matching_factors(
        self,
        error_message: str,
        root_cause: str,
        service: str,
        record: IncidentRecord,
    ) -> list[str]:
        """Get factors that match between incidents."""
        matching = []

        if service == record.service:
            matching.append(f"Same service ({service})")

        current_pattern = self._identify_pattern_type(error_message + " " + root_cause)
        record_pattern = self._identify_pattern_type(
            record.error_message + " " + record.root_cause
        )

        if current_pattern == record_pattern and current_pattern:
            matching.append(f"Same pattern ({current_pattern.value})")

        error_words = set(error_message.lower().split())
        record_words = set(record.error_message.lower().split())
        common_words = error_words & record_words
        significant = [w for w in common_words if len(w) > 4]
        if significant:
            matching.append(f"Similar error: {', '.join(significant[:3])}")

        return matching

    def _get_differing_factors(
        self,
        error_message: str,
        root_cause: str,
        service: str,
        record: IncidentRecord,
    ) -> list[str]:
        """Get factors that differ between incidents."""
        differing = []

        if service != record.service:
            differing.append(f"Different service ({record.service})")

        current_pattern = self._identify_pattern_type(error_message + " " + root_cause)
        record_pattern = self._identify_pattern_type(
            record.error_message + " " + record.root_cause
        )

        if current_pattern != record_pattern:
            if record_pattern:
                differing.append(f"Different root cause pattern ({record_pattern.value})")

        return differing

    def _identify_pattern_type(self, text: str) -> PatternType | None:
        """Identify pattern type from text."""
        text_lower = text.lower()

        for pattern_type, signatures in self._pattern_signatures.items():
            for sig in signatures:
                if re.search(sig, text_lower):
                    return pattern_type

        return None

    def _get_applicable_lessons(
        self,
        record: IncidentRecord,
    ) -> list[str]:
        """Get lessons from past incident that apply."""
        lessons = []

        pattern = self._identify_pattern_type(
            record.error_message + " " + record.root_cause
        )

        if pattern == PatternType.RESOURCE_LEAK:
            lessons.append("Use context managers for resource management")
            lessons.append("Add resource lifecycle tests")

        if pattern == PatternType.DEPLOY_RELATED:
            lessons.append("Implement canary deployments")
            lessons.append("Add pre-deploy checklist")

        if pattern == PatternType.CONNECTION_EXHAUSTION:
            lessons.append("Add connection pool monitoring")
            lessons.append("Configure connection timeouts")

        return lessons

    def _detect_patterns_from_similar(
        self,
        similar: list[SimilarIncident],
    ) -> list[RecurringPattern]:
        """Detect recurring patterns from similar incidents."""
        patterns: list[RecurringPattern] = []

        if len(similar) < 2:
            return patterns

        all_factors: list[str] = []
        for incident in similar:
            all_factors.extend(incident.matching_factors)

        factor_counts: dict[str, int] = {}
        for factor in all_factors:
            factor_counts[factor] = factor_counts.get(factor, 0) + 1

        common_factors = [
            f for f, count in factor_counts.items()
            if count >= 2
        ]

        if common_factors:
            all_unfulfilled: list[str] = []
            for incident in similar:
                for action in incident.unfulfilled_action_items:
                    all_unfulfilled.append(action.title)

            patterns.append(RecurringPattern(
                pattern_type=PatternType.RESOURCE_LEAK,
                description=f"Recurring pattern across {len(similar)} incidents",
                incident_ids=[i.incident_id for i in similar],
                occurrence_count=len(similar),
                first_seen=min(i.date for i in similar),
                last_seen=max(i.date for i in similar),
                common_factors=common_factors,
                unfulfilled_actions=list(set(all_unfulfilled)),
                recommended_systemic_fix="Implement org-wide standards for common factors",
                severity_trend="stable",
            ))

        return patterns

    def _generate_recommendations(
        self,
        similar: list[SimilarIncident],
        patterns: list[RecurringPattern],
    ) -> list[str]:
        """Generate recommendations based on similar incidents."""
        recommendations = []

        if any(len(i.unfulfilled_action_items) > 0 for i in similar):
            recommendations.append(
                "Complete unfulfilled action items from similar incidents - "
                "this incident may have been prevented"
            )

        if patterns:
            for pattern in patterns:
                if pattern.occurrence_count >= 3:
                    recommendations.append(
                        f"Recurring pattern detected ({pattern.occurrence_count} times). "
                        f"Consider: {pattern.recommended_systemic_fix}"
                    )

        if len(similar) >= 2:
            same_service = [
                i for i in similar
                if "Same service" in str(i.matching_factors)
            ]
            if len(same_service) >= 2:
                recommendations.append(
                    "Multiple similar incidents in this service - "
                    "consider architecture review"
                )

        return recommendations

    def detect_recurring_patterns(
        self,
        service: str | None = None,
        period_days: int = 90,
    ) -> list[RecurringPattern]:
        """Detect recurring patterns in incidents."""
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=period_days)

        incidents = [
            i for i in self._incidents
            if i.created_at >= cutoff
            and (service is None or i.service == service)
        ]

        by_pattern: dict[PatternType, list[IncidentRecord]] = {}

        for incident in incidents:
            pattern = self._identify_pattern_type(
                incident.error_message + " " + incident.root_cause
            )
            if pattern:
                if pattern not in by_pattern:
                    by_pattern[pattern] = []
                by_pattern[pattern].append(incident)

        patterns = []

        for pattern_type, pattern_incidents in by_pattern.items():
            if len(pattern_incidents) >= 2:
                unfulfilled: list[str] = []
                for incident in pattern_incidents:
                    for action in incident.action_items:
                        if action.status == ActionItemStatus.OPEN:
                            unfulfilled.append(action.title)

                patterns.append(RecurringPattern(
                    pattern_type=pattern_type,
                    description=f"{pattern_type.value} pattern",
                    incident_ids=[i.incident_id for i in pattern_incidents],
                    occurrence_count=len(pattern_incidents),
                    first_seen=min(i.created_at for i in pattern_incidents),
                    last_seen=max(i.created_at for i in pattern_incidents),
                    common_factors=self._extract_common_factors(pattern_incidents),
                    unfulfilled_actions=list(set(unfulfilled)),
                    recommended_systemic_fix=self._get_systemic_fix(pattern_type),
                    severity_trend=self._calculate_severity_trend(pattern_incidents),
                ))

        return sorted(patterns, key=lambda p: p.occurrence_count, reverse=True)

    def _extract_common_factors(
        self,
        incidents: list[IncidentRecord],
    ) -> list[str]:
        """Extract common factors from incidents."""
        factors = []

        services = set(i.service for i in incidents)
        if len(services) == 1:
            factors.append(f"Same service: {next(iter(services))}")

        all_tags: list[str] = []
        for incident in incidents:
            all_tags.extend(incident.tags)

        tag_counts: dict[str, int] = {}
        for tag in all_tags:
            tag_counts[tag] = tag_counts.get(tag, 0) + 1

        common_tags = [t for t, c in tag_counts.items() if c >= len(incidents) * 0.5]
        if common_tags:
            factors.append(f"Common tags: {', '.join(common_tags)}")

        return factors

    def _get_systemic_fix(self, pattern_type: PatternType) -> str:
        """Get recommended systemic fix for a pattern type."""
        fixes = {
            PatternType.RESOURCE_LEAK: (
                "Implement org-wide resource management standards "
                "with mandatory context managers and pool monitoring"
            ),
            PatternType.CONNECTION_EXHAUSTION: (
                "Standardize connection pool configuration "
                "and monitoring across all services"
            ),
            PatternType.DEPLOY_RELATED: (
                "Mandate progressive delivery (canary) "
                "for all production deployments"
            ),
            PatternType.CONFIGURATION_ERROR: (
                "Implement configuration validation in CI/CD "
                "and config change review process"
            ),
            PatternType.TIMEOUT_CASCADE: (
                "Implement circuit breakers and "
                "standardize timeout configuration"
            ),
        }
        return fixes.get(
            pattern_type,
            "Review recurring incidents and create specific prevention measures"
        )

    def _calculate_severity_trend(
        self,
        incidents: list[IncidentRecord],
    ) -> str:
        """Calculate severity trend over time."""
        if len(incidents) < 2:
            return "insufficient_data"

        sorted_incidents = sorted(incidents, key=lambda i: i.created_at)

        severity_map = {"P1": 4, "P2": 3, "P3": 2, "P4": 1}

        first_half = sorted_incidents[:len(sorted_incidents) // 2]
        second_half = sorted_incidents[len(sorted_incidents) // 2:]

        avg_first = sum(severity_map.get(i.severity, 2) for i in first_half) / len(first_half)
        avg_second = sum(severity_map.get(i.severity, 2) for i in second_half) / len(second_half)

        if avg_second > avg_first + 0.5:
            return "worsening"
        elif avg_second < avg_first - 0.5:
            return "improving"
        else:
            return "stable"

    def generate_organizational_insights(
        self,
        period_days: int = 90,
    ) -> list[OrganizationalInsight]:
        """Generate organizational insights from incident patterns."""
        insights = []
        patterns = self.detect_recurring_patterns(period_days=period_days)

        for pattern in patterns:
            if pattern.occurrence_count >= 3:
                self._insight_counter += 1

                insights.append(OrganizationalInsight(
                    insight_id=f"ORG-{self._insight_counter}",
                    title=f"Recurring {pattern.pattern_type.value} pattern detected",
                    description=(
                        f"This pattern has occurred {pattern.occurrence_count} times. "
                        f"Previous tactical fixes have not prevented recurrence."
                    ),
                    scope=LessonScope.ORG_WIDE,
                    evidence=pattern.common_factors,
                    suggested_actions=[
                        pattern.recommended_systemic_fix,
                        "Schedule architecture review",
                        "Allocate dedicated sprint for reliability",
                    ],
                    affected_teams=list(set(
                        i.split("-")[0] for i in pattern.incident_ids
                    )),
                    priority="high" if pattern.severity_trend == "worsening" else "medium",
                ))

        return insights


_detector: PatternDetector | None = None


def get_pattern_detector() -> PatternDetector:
    """Get the default pattern detector."""
    global _detector
    if _detector is None:
        _detector = PatternDetector()
    return _detector
