"""Similar Past Incident Matching.

Finds similar historical incidents to help predict outcomes
and inform resolution strategies.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("devcopilot.predictive_advanced.similar_incidents")


class PatternCategory(str, Enum):
    MEMORY_LEAK = "memory_leak"
    CONNECTION_LEAK = "connection_leak"
    CPU_SPIKE = "cpu_spike"
    DISK_EXHAUSTION = "disk_exhaustion"
    NETWORK_PARTITION = "network_partition"
    TIMEOUT_CASCADE = "timeout_cascade"
    OOM_KILLED = "oom_killed"
    DEADLOCK = "deadlock"
    RESOURCE_STARVATION = "resource_starvation"
    CONFIG_CHANGE = "config_change"
    DEPLOY_FAILURE = "deploy_failure"
    DEPENDENCY_FAILURE = "dependency_failure"
    NULL_POINTER = "null_pointer"
    AUTHENTICATION_FAILURE = "authentication_failure"
    RATE_LIMITING = "rate_limiting"


@dataclass
class SimilarityScore:
    """Breakdown of similarity between incidents."""

    total_score: float
    service_match: float
    pattern_match: float
    symptom_match: float
    time_pattern_match: float
    metric_correlation: float
    root_cause_similarity: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": round(self.total_score, 2),
            "breakdown": {
                "service": round(self.service_match, 2),
                "pattern": round(self.pattern_match, 2),
                "symptoms": round(self.symptom_match, 2),
                "time_pattern": round(self.time_pattern_match, 2),
                "metrics": round(self.metric_correlation, 2),
                "root_cause": round(self.root_cause_similarity, 2),
            },
        }


@dataclass
class IncidentPattern:
    """A pattern extracted from an incident."""

    pattern_id: str
    category: PatternCategory
    keywords: list[str]
    services_affected: list[str]
    metrics_involved: list[str]
    typical_duration_minutes: float
    typical_resolution: str
    occurrence_count: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "pattern_id": self.pattern_id,
            "category": self.category.value,
            "keywords": self.keywords,
            "services": self.services_affected,
            "metrics": self.metrics_involved,
            "typical_duration_minutes": round(self.typical_duration_minutes, 1),
            "typical_resolution": self.typical_resolution,
            "occurrences": self.occurrence_count,
        }


@dataclass
class SimilarIncident:
    """A similar past incident with resolution details."""

    incident_id: str
    title: str
    occurred_at: datetime
    duration_minutes: float
    services_affected: list[str]
    root_cause: str
    resolution: str
    cost_estimate: float
    similarity: SimilarityScore
    patterns: list[IncidentPattern] = field(default_factory=list)
    learnings: list[str] = field(default_factory=list)
    what_worked: str = ""
    what_didnt_work: str = ""

    @property
    def days_ago(self) -> int:
        return (datetime.now(timezone.utc) - self.occurred_at).days

    def to_dict(self) -> dict[str, Any]:
        return {
            "incident_id": self.incident_id,
            "title": self.title,
            "occurred_at": self.occurred_at.isoformat(),
            "days_ago": self.days_ago,
            "duration_minutes": round(self.duration_minutes, 1),
            "services": self.services_affected,
            "root_cause": self.root_cause,
            "resolution": self.resolution,
            "cost_estimate": round(self.cost_estimate, 2),
            "similarity": self.similarity.to_dict(),
            "patterns": [p.to_dict() for p in self.patterns],
            "learnings": self.learnings,
            "what_worked": self.what_worked,
            "what_didnt_work": self.what_didnt_work,
        }


@dataclass
class HistoricalIncident:
    """An incident stored for pattern matching."""

    incident_id: str
    org_id: str
    title: str
    description: str
    occurred_at: datetime
    resolved_at: datetime | None
    services: list[str]
    root_cause: str
    resolution: str
    cost: float
    patterns: list[PatternCategory]
    metrics: dict[str, float]
    symptoms: list[str]
    learnings: list[str] = field(default_factory=list)

    @property
    def duration_minutes(self) -> float:
        if not self.resolved_at:
            return 0
        return (self.resolved_at - self.occurred_at).total_seconds() / 60

    @property
    def days_ago(self) -> int:
        delta = datetime.now(timezone.utc) - self.occurred_at
        return delta.days


class SimilarIncidentMatcher:
    """Matches current signals against historical incidents."""

    def __init__(self) -> None:
        self._incidents: dict[str, list[HistoricalIncident]] = {}
        self._patterns: dict[str, list[IncidentPattern]] = {}
        self._pattern_keywords: dict[PatternCategory, list[str]] = {
            PatternCategory.MEMORY_LEAK: [
                "memory", "leak", "heap", "oom", "gc", "garbage",
                "allocation", "buffer", "cache growth",
            ],
            PatternCategory.CONNECTION_LEAK: [
                "connection", "pool", "leak", "socket", "tcp",
                "exhaustion", "max connections", "close",
            ],
            PatternCategory.CPU_SPIKE: [
                "cpu", "spike", "utilization", "load", "busy",
                "throttle", "compute", "processing",
            ],
            PatternCategory.DISK_EXHAUSTION: [
                "disk", "storage", "space", "full", "capacity",
                "inode", "write", "filesystem",
            ],
            PatternCategory.NETWORK_PARTITION: [
                "network", "partition", "split", "brain", "unreachable",
                "timeout", "connectivity", "dns",
            ],
            PatternCategory.TIMEOUT_CASCADE: [
                "timeout", "cascade", "chain", "propagate", "slow",
                "deadline", "latency", "downstream",
            ],
            PatternCategory.OOM_KILLED: [
                "oomkilled", "oom", "killed", "memory limit",
                "kubernetes", "container", "restart",
            ],
            PatternCategory.NULL_POINTER: [
                "null", "none", "undefined", "nullpointer", "attribute",
                "nonetype", "missing", "not found",
            ],
            PatternCategory.DEPLOY_FAILURE: [
                "deploy", "release", "rollout", "canary", "blue-green",
                "upgrade", "version", "config change",
            ],
            PatternCategory.DEPENDENCY_FAILURE: [
                "dependency", "downstream", "upstream", "external",
                "third-party", "api", "service unavailable",
            ],
        }

    def record_incident(
        self,
        org_id: str,
        incident_id: str,
        title: str,
        description: str,
        services: list[str],
        root_cause: str,
        resolution: str,
        cost: float,
        metrics: dict[str, float] | None = None,
        symptoms: list[str] | None = None,
        learnings: list[str] | None = None,
        occurred_at: datetime | None = None,
        resolved_at: datetime | None = None,
    ) -> HistoricalIncident:
        """Record an incident for future matching."""
        if occurred_at is None:
            occurred_at = datetime.now(timezone.utc)
        if resolved_at is None:
            resolved_at = datetime.now(timezone.utc)

        patterns = self._detect_patterns(title, description, root_cause)

        incident = HistoricalIncident(
            incident_id=incident_id,
            org_id=org_id,
            title=title,
            description=description,
            occurred_at=occurred_at,
            resolved_at=resolved_at,
            services=services,
            root_cause=root_cause,
            resolution=resolution,
            cost=cost,
            patterns=patterns,
            metrics=metrics or {},
            symptoms=symptoms or [],
            learnings=learnings or [],
        )

        if org_id not in self._incidents:
            self._incidents[org_id] = []

        self._incidents[org_id].append(incident)

        for pattern in patterns:
            self._update_pattern_stats(org_id, pattern, incident)

        log.info(
            "Recorded incident: org=%s incident=%s patterns=%s",
            org_id,
            incident_id,
            [p.value for p in patterns],
        )

        return incident

    def _detect_patterns(
        self,
        title: str,
        description: str,
        root_cause: str,
    ) -> list[PatternCategory]:
        """Detect patterns from incident text."""
        patterns = []
        combined_text = f"{title} {description} {root_cause}".lower()

        for category, keywords in self._pattern_keywords.items():
            matches = sum(1 for kw in keywords if kw in combined_text)
            if matches >= 2:
                patterns.append(category)

        return patterns

    def _update_pattern_stats(
        self,
        org_id: str,
        category: PatternCategory,
        incident: HistoricalIncident,
    ) -> None:
        """Update pattern statistics."""
        if org_id not in self._patterns:
            self._patterns[org_id] = []

        existing = None
        for p in self._patterns[org_id]:
            if p.category == category:
                existing = p
                break

        if existing:
            existing.occurrence_count += 1
            existing.typical_duration_minutes = (
                (existing.typical_duration_minutes + incident.duration_minutes) / 2
            )
            for svc in incident.services:
                if svc not in existing.services_affected:
                    existing.services_affected.append(svc)
        else:
            self._patterns[org_id].append(IncidentPattern(
                pattern_id=f"pattern_{category.value}",
                category=category,
                keywords=self._pattern_keywords.get(category, []),
                services_affected=incident.services.copy(),
                metrics_involved=list(incident.metrics.keys()),
                typical_duration_minutes=incident.duration_minutes,
                typical_resolution=incident.resolution,
                occurrence_count=1,
            ))

    def find_similar(
        self,
        org_id: str,
        current_service: str,
        current_symptoms: list[str],
        current_metrics: dict[str, float],
        limit: int = 5,
    ) -> list[SimilarIncident]:
        """Find similar past incidents."""
        incidents = self._incidents.get(org_id, [])

        if not incidents:
            return []

        scored: list[tuple[HistoricalIncident, SimilarityScore]] = []

        for incident in incidents:
            score = self._calculate_similarity(
                incident,
                current_service,
                current_symptoms,
                current_metrics,
            )

            if score.total_score >= 0.5:
                scored.append((incident, score))

        scored.sort(key=lambda x: x[1].total_score, reverse=True)

        results = []
        for incident, score in scored[:limit]:
            patterns = [
                p for p in self._patterns.get(org_id, [])
                if p.category in incident.patterns
            ]

            results.append(SimilarIncident(
                incident_id=incident.incident_id,
                title=incident.title,
                occurred_at=incident.occurred_at,
                duration_minutes=incident.duration_minutes,
                services_affected=incident.services,
                root_cause=incident.root_cause,
                resolution=incident.resolution,
                cost_estimate=incident.cost,
                similarity=score,
                patterns=patterns,
                learnings=incident.learnings,
                what_worked=self._extract_what_worked(incident),
                what_didnt_work=self._extract_what_didnt_work(incident),
            ))

        return results

    def _calculate_similarity(
        self,
        incident: HistoricalIncident,
        current_service: str,
        current_symptoms: list[str],
        current_metrics: dict[str, float],
    ) -> SimilarityScore:
        """Calculate similarity score between current state and past incident."""
        service_match = 1.0 if current_service in incident.services else 0.3

        pattern_keywords = []
        for pattern in incident.patterns:
            pattern_keywords.extend(
                self._pattern_keywords.get(pattern, [])
            )

        symptom_match = 0.0
        if current_symptoms and incident.symptoms:
            current_set = set(s.lower() for s in current_symptoms)
            incident_set = set(s.lower() for s in incident.symptoms)
            if current_set or incident_set:
                symptom_match = len(current_set & incident_set) / max(
                    len(current_set | incident_set), 1
                )

        pattern_match = 0.0
        if current_symptoms:
            symptom_text = " ".join(current_symptoms).lower()
            matches = sum(1 for kw in pattern_keywords if kw in symptom_text)
            pattern_match = min(1.0, matches / max(len(pattern_keywords), 1) * 2)

        days_ago = incident.days_ago
        if days_ago < 30:
            time_pattern_match = 1.0
        elif days_ago < 90:
            time_pattern_match = 0.8
        elif days_ago < 180:
            time_pattern_match = 0.5
        else:
            time_pattern_match = 0.3

        metric_correlation = 0.0
        if current_metrics and incident.metrics:
            common_metrics = set(current_metrics.keys()) & set(incident.metrics.keys())
            if common_metrics:
                metric_correlation = len(common_metrics) / max(
                    len(set(current_metrics.keys()) | set(incident.metrics.keys())), 1
                )

        root_cause_similarity = 0.0
        if current_symptoms:
            symptom_text = " ".join(current_symptoms).lower()
            root_cause_lower = incident.root_cause.lower()
            common_words = set(symptom_text.split()) & set(root_cause_lower.split())
            root_cause_similarity = min(1.0, len(common_words) / 5)

        total = (
            service_match * 0.25 +
            pattern_match * 0.25 +
            symptom_match * 0.15 +
            time_pattern_match * 0.1 +
            metric_correlation * 0.15 +
            root_cause_similarity * 0.1
        )

        return SimilarityScore(
            total_score=total,
            service_match=service_match,
            pattern_match=pattern_match,
            symptom_match=symptom_match,
            time_pattern_match=time_pattern_match,
            metric_correlation=metric_correlation,
            root_cause_similarity=root_cause_similarity,
        )

    def _extract_what_worked(self, incident: HistoricalIncident) -> str:
        """Extract what worked from the resolution."""
        if "rollback" in incident.resolution.lower():
            return "Rollback was effective"
        if "scale" in incident.resolution.lower():
            return "Scaling provided temporary relief"
        if "restart" in incident.resolution.lower():
            return "Service restart cleared the issue"
        if "fix" in incident.resolution.lower():
            return "Code fix resolved the root cause"
        return "Resolution strategy was successful"

    def _extract_what_didnt_work(self, incident: HistoricalIncident) -> str:
        """Extract what didn't work (if learnings mention it)."""
        for learning in incident.learnings:
            if "didn't" in learning.lower() or "failed" in learning.lower():
                return learning
        return ""

    def get_pattern_summary(
        self,
        org_id: str,
        days: int = 90,
    ) -> dict[str, Any]:
        """Get summary of incident patterns over time."""
        incidents = self._incidents.get(org_id, [])
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        recent = [i for i in incidents if i.occurred_at >= cutoff]

        pattern_counts: dict[str, int] = {}
        pattern_costs: dict[str, float] = {}
        service_patterns: dict[str, list[str]] = {}

        for incident in recent:
            for pattern in incident.patterns:
                key = pattern.value
                pattern_counts[key] = pattern_counts.get(key, 0) + 1
                pattern_costs[key] = pattern_costs.get(key, 0) + incident.cost

                for service in incident.services:
                    if service not in service_patterns:
                        service_patterns[service] = []
                    if key not in service_patterns[service]:
                        service_patterns[service].append(key)

        top_patterns = sorted(
            pattern_counts.items(),
            key=lambda x: x[1],
            reverse=True,
        )[:5]

        return {
            "period_days": days,
            "total_incidents": len(recent),
            "top_patterns": [
                {
                    "pattern": p,
                    "count": c,
                    "cost": round(pattern_costs.get(p, 0), 2),
                }
                for p, c in top_patterns
            ],
            "services_by_pattern_count": dict(
                sorted(
                    [(s, len(p)) for s, p in service_patterns.items()],
                    key=lambda x: x[1],
                    reverse=True,
                )[:10]
            ),
            "recommendations": self._generate_recommendations(pattern_counts),
        }

    def _generate_recommendations(
        self,
        pattern_counts: dict[str, int],
    ) -> list[str]:
        """Generate recommendations based on pattern frequency."""
        recommendations = []

        if pattern_counts.get(PatternCategory.MEMORY_LEAK.value, 0) >= 2:
            recommendations.append(
                "Memory leaks are recurring. Consider adding memory profiling to CI."
            )

        if pattern_counts.get(PatternCategory.NULL_POINTER.value, 0) >= 2:
            recommendations.append(
                "Null pointer issues are frequent. Enable strict null checking."
            )

        if pattern_counts.get(PatternCategory.CONNECTION_LEAK.value, 0) >= 2:
            recommendations.append(
                "Connection leaks detected. Add connection pool monitoring."
            )

        if pattern_counts.get(PatternCategory.DEPLOY_FAILURE.value, 0) >= 2:
            recommendations.append(
                "Deploy-related incidents are common. Implement canary deployments."
            )

        return recommendations


_matcher: SimilarIncidentMatcher | None = None


def get_similar_incident_matcher() -> SimilarIncidentMatcher:
    """Get the default similar incident matcher."""
    global _matcher
    if _matcher is None:
        _matcher = SimilarIncidentMatcher()
    return _matcher
