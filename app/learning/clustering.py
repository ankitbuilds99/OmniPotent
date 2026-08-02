"""Semantic incident clustering.

Groups incidents by meaning, not just keywords.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("omnipotent.learning.clustering")


class RootCauseCategory(Enum):
    """Root cause category taxonomy."""

    NULL_UNDEFINED = "null_undefined"
    RESOURCE_EXHAUSTION = "resource_exhaustion"
    CONFIGURATION = "configuration"
    EXTERNAL_DEPENDENCY = "external_dependency"
    DEPLOYMENT = "deployment"
    DATABASE = "database"
    NETWORK = "network"
    LOGIC_ERROR = "logic_error"
    CONCURRENCY = "concurrency"
    SECURITY = "security"
    UNKNOWN = "unknown"


@dataclass
class IncidentFeatures:
    """Features extracted from an incident for clustering."""

    incident_id: str
    error_type: str
    error_message: str
    affected_services: list[str]
    root_cause_category: RootCauseCategory
    duration_minutes: int
    severity: str
    occurred_at: datetime
    fix_type: str | None = None
    fix_effective: bool | None = None
    feature_vector: list[float] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "incident_id": self.incident_id,
            "error_type": self.error_type,
            "affected_services": self.affected_services,
            "root_cause_category": self.root_cause_category.value,
            "duration_minutes": self.duration_minutes,
            "severity": self.severity,
            "occurred_at": self.occurred_at.isoformat(),
            "fix_effective": self.fix_effective,
        }


@dataclass
class IncidentCluster:
    """A cluster of similar incidents."""

    cluster_id: str
    name: str
    description: str
    root_cause_category: RootCauseCategory
    incidents: list[IncidentFeatures]
    centroid: list[float]
    common_services: list[str]
    common_error_patterns: list[str]
    avg_duration_minutes: float
    recurrence_count: int
    fix_effectiveness_rate: float
    trend: str
    recommendations: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "cluster_id": self.cluster_id,
            "name": self.name,
            "description": self.description,
            "root_cause_category": self.root_cause_category.value,
            "incident_count": len(self.incidents),
            "common_services": self.common_services,
            "common_error_patterns": self.common_error_patterns,
            "avg_duration_minutes": round(self.avg_duration_minutes, 1),
            "recurrence_count": self.recurrence_count,
            "fix_effectiveness_rate": round(self.fix_effectiveness_rate, 2),
            "trend": self.trend,
            "recommendations": self.recommendations,
        }


@dataclass
class ClusterAnalysis:
    """Analysis of incident clusters."""

    analysis_id: str
    clusters: list[IncidentCluster]
    total_incidents: int
    by_category: dict[str, int]
    trending_up: list[str]
    trending_down: list[str]
    ineffective_fixes: list[str]
    analyzed_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "analysis_id": self.analysis_id,
            "clusters": [c.to_dict() for c in self.clusters],
            "total_incidents": self.total_incidents,
            "by_category": self.by_category,
            "trending_up": self.trending_up,
            "trending_down": self.trending_down,
            "ineffective_fixes": self.ineffective_fixes,
            "analyzed_at": self.analyzed_at.isoformat(),
        }


class IncidentClusterer:
    """Clusters incidents semantically."""

    def __init__(self) -> None:
        self._incidents: list[IncidentFeatures] = []
        self._clusters: dict[str, IncidentCluster] = {}
        self._analysis_counter = 0
        self._cluster_counter = 0

    def add_incident(
        self,
        incident_id: str,
        error_type: str,
        error_message: str,
        affected_services: list[str],
        duration_minutes: int,
        severity: str,
        occurred_at: datetime | None = None,
        fix_type: str | None = None,
        fix_effective: bool | None = None,
    ) -> IncidentFeatures:
        """Add an incident for clustering."""
        category = self._categorize_root_cause(error_type, error_message)

        features = IncidentFeatures(
            incident_id=incident_id,
            error_type=error_type,
            error_message=error_message,
            affected_services=affected_services,
            root_cause_category=category,
            duration_minutes=duration_minutes,
            severity=severity,
            occurred_at=occurred_at or datetime.now(timezone.utc),
            fix_type=fix_type,
            fix_effective=fix_effective,
            feature_vector=self._extract_features(
                error_type,
                error_message,
                affected_services,
                category,
            ),
        )

        self._incidents.append(features)
        return features

    def _categorize_root_cause(
        self,
        error_type: str,
        error_message: str,
    ) -> RootCauseCategory:
        """Categorize the root cause of an incident."""
        combined = f"{error_type} {error_message}".lower()

        if "none" in combined or "null" in combined or "undefined" in combined:
            return RootCauseCategory.NULL_UNDEFINED
        elif "memory" in combined or "pool" in combined or "exhausted" in combined:
            return RootCauseCategory.RESOURCE_EXHAUSTION
        elif "config" in combined or "env" in combined or "setting" in combined:
            return RootCauseCategory.CONFIGURATION
        elif "api" in combined or "external" in combined or "third" in combined:
            return RootCauseCategory.EXTERNAL_DEPENDENCY
        elif "deploy" in combined or "release" in combined or "rollout" in combined:
            return RootCauseCategory.DEPLOYMENT
        elif "database" in combined or "query" in combined or "sql" in combined:
            return RootCauseCategory.DATABASE
        elif "network" in combined or "dns" in combined or "timeout" in combined:
            return RootCauseCategory.NETWORK
        elif "race" in combined or "deadlock" in combined or "concurrent" in combined:
            return RootCauseCategory.CONCURRENCY
        elif "auth" in combined or "permission" in combined or "security" in combined:
            return RootCauseCategory.SECURITY

        return RootCauseCategory.UNKNOWN

    def _extract_features(
        self,
        error_type: str,
        error_message: str,
        services: list[str],
        category: RootCauseCategory,
    ) -> list[float]:
        """Extract numerical features for clustering."""
        category_encoding = {cat: i for i, cat in enumerate(RootCauseCategory)}

        features = [
            float(category_encoding.get(category, 0)),
            len(services),
            len(error_message),
            1.0 if "critical" in error_type.lower() else 0.0,
            1.0 if any(s in ["payment", "billing", "checkout"] for s in services) else 0.0,
        ]

        return features

    def cluster_incidents(
        self,
        min_cluster_size: int = 2,
    ) -> ClusterAnalysis:
        """Cluster all incidents."""
        self._analysis_counter += 1
        analysis_id = f"analysis_{self._analysis_counter}"

        by_category: dict[RootCauseCategory, list[IncidentFeatures]] = {}
        for incident in self._incidents:
            cat = incident.root_cause_category
            if cat not in by_category:
                by_category[cat] = []
            by_category[cat].append(incident)

        clusters = []
        for category, incidents in by_category.items():
            if len(incidents) >= min_cluster_size:
                self._cluster_counter += 1
                cluster = self._create_cluster(
                    f"cluster_{self._cluster_counter}",
                    category,
                    incidents,
                )
                clusters.append(cluster)
                self._clusters[cluster.cluster_id] = cluster

        category_counts = {
            cat.value: len(incs) for cat, incs in by_category.items()
        }

        trending_up = self._find_trending(clusters, direction="up")
        trending_down = self._find_trending(clusters, direction="down")
        ineffective = self._find_ineffective_fixes(clusters)

        analysis = ClusterAnalysis(
            analysis_id=analysis_id,
            clusters=clusters,
            total_incidents=len(self._incidents),
            by_category=category_counts,
            trending_up=trending_up,
            trending_down=trending_down,
            ineffective_fixes=ineffective,
            analyzed_at=datetime.now(timezone.utc),
        )

        log.info(
            "incident clustering completed",
            extra={
                "analysis_id": analysis_id,
                "total_incidents": len(self._incidents),
                "cluster_count": len(clusters),
            },
        )

        return analysis

    def _create_cluster(
        self,
        cluster_id: str,
        category: RootCauseCategory,
        incidents: list[IncidentFeatures],
    ) -> IncidentCluster:
        """Create a cluster from incidents."""
        service_counts: dict[str, int] = {}
        error_patterns: dict[str, int] = {}
        total_duration = 0
        fix_effective_count = 0
        fix_total = 0

        for incident in incidents:
            for service in incident.affected_services:
                service_counts[service] = service_counts.get(service, 0) + 1

            error_key = incident.error_type[:50]
            error_patterns[error_key] = error_patterns.get(error_key, 0) + 1

            total_duration += incident.duration_minutes

            if incident.fix_effective is not None:
                fix_total += 1
                if incident.fix_effective:
                    fix_effective_count += 1

        common_services = sorted(
            service_counts.keys(),
            key=lambda s: service_counts[s],
            reverse=True,
        )[:3]

        common_errors = sorted(
            error_patterns.keys(),
            key=lambda e: error_patterns[e],
            reverse=True,
        )[:3]

        avg_duration = total_duration / len(incidents) if incidents else 0
        fix_rate = fix_effective_count / fix_total if fix_total > 0 else 0

        centroid = self._compute_centroid(incidents)

        now = datetime.now(timezone.utc)
        recent = sum(
            1 for i in incidents
            if (now - i.occurred_at).days < 30
        )
        older = len(incidents) - recent

        if recent > older * 1.5:
            trend = "increasing"
        elif older > recent * 1.5:
            trend = "decreasing"
        else:
            trend = "stable"

        recommendations = self._generate_cluster_recommendations(
            category,
            common_services,
            fix_rate,
            len(incidents),
        )

        return IncidentCluster(
            cluster_id=cluster_id,
            name=f"{category.value.replace('_', ' ').title()} Incidents",
            description=f"Cluster of {len(incidents)} incidents related to {category.value}",
            root_cause_category=category,
            incidents=incidents,
            centroid=centroid,
            common_services=common_services,
            common_error_patterns=common_errors,
            avg_duration_minutes=avg_duration,
            recurrence_count=len(incidents),
            fix_effectiveness_rate=fix_rate,
            trend=trend,
            recommendations=recommendations,
        )

    def _compute_centroid(
        self,
        incidents: list[IncidentFeatures],
    ) -> list[float]:
        """Compute centroid of feature vectors."""
        if not incidents:
            return []

        vectors = [i.feature_vector for i in incidents if i.feature_vector]
        if not vectors:
            return []

        dim = len(vectors[0])
        centroid = [0.0] * dim

        for vec in vectors:
            for i, val in enumerate(vec):
                centroid[i] += val

        return [c / len(vectors) for c in centroid]

    def _find_trending(
        self,
        clusters: list[IncidentCluster],
        direction: str,
    ) -> list[str]:
        """Find trending clusters."""
        if direction == "up":
            return [c.cluster_id for c in clusters if c.trend == "increasing"]
        else:
            return [c.cluster_id for c in clusters if c.trend == "decreasing"]

    def _find_ineffective_fixes(
        self,
        clusters: list[IncidentCluster],
    ) -> list[str]:
        """Find clusters with ineffective fixes."""
        return [
            c.cluster_id for c in clusters
            if c.fix_effectiveness_rate < 0.5 and c.recurrence_count > 2
        ]

    def _generate_cluster_recommendations(
        self,
        category: RootCauseCategory,
        services: list[str],
        fix_rate: float,
        incident_count: int,
    ) -> list[str]:
        """Generate recommendations for a cluster."""
        recommendations = []

        category_recs = {
            RootCauseCategory.NULL_UNDEFINED: [
                "Add null safety linting rules",
                "Implement TypeScript strict mode or Python type hints",
                "Add defensive null checks at service boundaries",
            ],
            RootCauseCategory.RESOURCE_EXHAUSTION: [
                "Implement connection pool monitoring",
                "Add memory leak detection to CI",
                "Set up auto-scaling based on resource metrics",
            ],
            RootCauseCategory.CONFIGURATION: [
                "Implement config validation at startup",
                "Use feature flags for gradual rollouts",
                "Add config drift detection",
            ],
            RootCauseCategory.EXTERNAL_DEPENDENCY: [
                "Implement circuit breakers",
                "Add fallback strategies",
                "Set up dependency health monitoring",
            ],
        }

        recommendations.extend(category_recs.get(category, []))

        if fix_rate < 0.5:
            recommendations.append(
                f"Review fix strategy - only {fix_rate*100:.0f}% effectiveness"
            )

        if incident_count > 5:
            recommendations.append(
                f"High recurrence ({incident_count}x) - consider systemic fix"
            )

        return recommendations[:5]

    def find_similar_incidents(
        self,
        incident_id: str,
        max_results: int = 5,
    ) -> list[IncidentFeatures]:
        """Find incidents similar to a given incident."""
        target = None
        for inc in self._incidents:
            if inc.incident_id == incident_id:
                target = inc
                break

        if not target:
            return []

        similar = []
        for inc in self._incidents:
            if inc.incident_id == incident_id:
                continue

            if inc.root_cause_category == target.root_cause_category:
                similarity = self._compute_similarity(target, inc)
                similar.append((inc, similarity))

        similar.sort(key=lambda x: x[1], reverse=True)
        return [inc for inc, _ in similar[:max_results]]

    def _compute_similarity(
        self,
        a: IncidentFeatures,
        b: IncidentFeatures,
    ) -> float:
        """Compute similarity between two incidents."""
        score = 0.0

        if a.root_cause_category == b.root_cause_category:
            score += 0.3

        common_services = set(a.affected_services) & set(b.affected_services)
        if common_services:
            score += 0.2 * len(common_services) / max(
                len(a.affected_services),
                len(b.affected_services),
            )

        if a.error_type == b.error_type:
            score += 0.3

        error_words_a = set(a.error_message.lower().split())
        error_words_b = set(b.error_message.lower().split())
        common_words = error_words_a & error_words_b
        if common_words:
            score += 0.2 * len(common_words) / max(
                len(error_words_a),
                len(error_words_b),
            )

        return min(1.0, score)

    def get_cluster(self, cluster_id: str) -> IncidentCluster | None:
        """Get a cluster by ID."""
        return self._clusters.get(cluster_id)

    def get_dashboard_data(self) -> dict[str, Any]:
        """Get data for the learning dashboard."""
        analysis = self.cluster_incidents()

        return {
            "total_incidents": analysis.total_incidents,
            "cluster_count": len(analysis.clusters),
            "by_category": analysis.by_category,
            "trending_up": analysis.trending_up,
            "trending_down": analysis.trending_down,
            "clusters": [c.to_dict() for c in analysis.clusters[:5]],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }


_clusterer: IncidentClusterer | None = None


def get_incident_clusterer() -> IncidentClusterer:
    """Get the default incident clusterer."""
    global _clusterer
    if _clusterer is None:
        _clusterer = IncidentClusterer()
    return _clusterer
