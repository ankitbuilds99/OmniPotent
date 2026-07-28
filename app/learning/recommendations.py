"""Proactive recommendations based on incident learning.

Recommends actions before incidents happen.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, TypedDict

log = logging.getLogger("devcopilot.learning.recommendations")


class RecommendationPriority(Enum):
    """Priority level of a recommendation."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class RecommendationType(Enum):
    """Type of recommendation."""

    CODE_CHANGE = "code_change"
    MONITORING = "monitoring"
    PROCESS = "process"
    INFRASTRUCTURE = "infrastructure"
    TESTING = "testing"
    DOCUMENTATION = "documentation"


class _RecTemplate(TypedDict):
    type: RecommendationType
    title: str
    description: str
    steps: list[str]
    effort: float


class _RunbookTemplate(TypedDict):
    title: str
    symptoms: list[str]
    diagnostic: list[str]
    resolution: list[str]


@dataclass
class PreventionMetric:
    """Metric showing prevention potential."""

    incidents_preventable: int
    percentage_of_total: float
    estimated_downtime_saved_minutes: int
    estimated_revenue_saved: float


@dataclass
class ProactiveRecommendation:
    """A proactive recommendation to prevent incidents."""

    recommendation_id: str
    priority: RecommendationPriority
    recommendation_type: RecommendationType
    title: str
    description: str
    rationale: str
    affected_services: list[str]
    prevention_metrics: PreventionMetric
    implementation_effort_days: float
    implementation_steps: list[str]
    related_incidents: list[str]
    status: str = "open"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "recommendation_id": self.recommendation_id,
            "priority": self.priority.value,
            "recommendation_type": self.recommendation_type.value,
            "title": self.title,
            "description": self.description,
            "rationale": self.rationale,
            "affected_services": self.affected_services,
            "prevention_metrics": {
                "incidents_preventable": self.prevention_metrics.incidents_preventable,
                "percentage_of_total": round(
                    self.prevention_metrics.percentage_of_total, 1
                ),
                "estimated_downtime_saved_minutes": self.prevention_metrics.estimated_downtime_saved_minutes,
                "estimated_revenue_saved": self.prevention_metrics.estimated_revenue_saved,
            },
            "implementation_effort_days": self.implementation_effort_days,
            "implementation_steps": self.implementation_steps,
            "related_incidents": self.related_incidents,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class AutoRunbook:
    """Auto-generated runbook from incident resolutions."""

    runbook_id: str
    title: str
    description: str
    symptoms: list[str]
    diagnostic_steps: list[str]
    resolution_steps: list[str]
    source_incidents: list[str]
    success_rate: float
    avg_resolution_time_minutes: int
    created_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "runbook_id": self.runbook_id,
            "title": self.title,
            "description": self.description,
            "symptoms": self.symptoms,
            "diagnostic_steps": self.diagnostic_steps,
            "resolution_steps": self.resolution_steps,
            "source_incidents": self.source_incidents,
            "success_rate": round(self.success_rate, 2),
            "avg_resolution_time_minutes": self.avg_resolution_time_minutes,
            "created_at": self.created_at.isoformat(),
        }


class RecommendationEngine:
    """Generates proactive recommendations from incident data."""

    def __init__(self) -> None:
        self._recommendations: dict[str, ProactiveRecommendation] = {}
        self._runbooks: dict[str, AutoRunbook] = {}
        self._rec_counter = 0
        self._runbook_counter = 0

    def generate_recommendations(
        self,
        incident_clusters: list[dict[str, Any]],
        total_incidents: int,
    ) -> list[ProactiveRecommendation]:
        """Generate recommendations from incident clusters."""
        recommendations = []

        for cluster in incident_clusters:
            root_cause = cluster.get("root_cause_category", "unknown")
            incident_count = cluster.get("incident_count", 0)
            services = cluster.get("common_services", [])
            trend = cluster.get("trend", "stable")

            if incident_count >= 3:
                rec = self._create_recommendation_for_cluster(
                    root_cause,
                    incident_count,
                    services,
                    trend,
                    total_incidents,
                    cluster.get("incidents", []),
                )
                if rec:
                    recommendations.append(rec)
                    self._recommendations[rec.recommendation_id] = rec

        recommendations.sort(
            key=lambda r: (
                0 if r.priority == RecommendationPriority.CRITICAL else
                1 if r.priority == RecommendationPriority.HIGH else
                2 if r.priority == RecommendationPriority.MEDIUM else 3
            )
        )

        return recommendations

    def _create_recommendation_for_cluster(
        self,
        root_cause: str,
        incident_count: int,
        services: list[str],
        trend: str,
        total_incidents: int,
        incident_ids: list[str],
    ) -> ProactiveRecommendation | None:
        """Create a recommendation for an incident cluster."""
        self._rec_counter += 1
        rec_id = f"rec_{self._rec_counter}"

        rec_templates: dict[str, _RecTemplate] = {
            "null_undefined": {
                "type": RecommendationType.CODE_CHANGE,
                "title": "Add null safety linting to {services}",
                "description": "Implement static analysis rules to catch null/undefined issues at build time",
                "steps": [
                    "Add eslint-plugin-null-safety or mypy strict mode",
                    "Configure pre-commit hooks for null checks",
                    "Add null safety tests to CI pipeline",
                    "Review and fix existing null issues",
                ],
                "effort": 2.0,
            },
            "resource_exhaustion": {
                "type": RecommendationType.MONITORING,
                "title": "Implement connection pool monitoring for {services}",
                "description": "Add monitoring and alerting for connection pool exhaustion",
                "steps": [
                    "Add connection pool metrics to Prometheus",
                    "Create Grafana dashboard for pool stats",
                    "Set up alerts at 80% pool usage",
                    "Implement auto-scaling for pool size",
                ],
                "effort": 1.0,
            },
            "configuration": {
                "type": RecommendationType.PROCESS,
                "title": "Add config validation at startup",
                "description": "Validate all configuration values during service startup",
                "steps": [
                    "Define config schema with required fields",
                    "Add startup validation hook",
                    "Fail fast on invalid config",
                    "Add config drift detection in CI",
                ],
                "effort": 1.5,
            },
            "external_dependency": {
                "type": RecommendationType.INFRASTRUCTURE,
                "title": "Implement circuit breakers for external dependencies",
                "description": "Add circuit breakers to prevent cascade failures from external services",
                "steps": [
                    "Install circuit breaker library",
                    "Configure breakers for each external call",
                    "Add fallback handlers",
                    "Set up monitoring for breaker state",
                ],
                "effort": 3.0,
            },
            "deployment": {
                "type": RecommendationType.PROCESS,
                "title": "Enforce deployment best practices",
                "description": "Implement guardrails for safer deployments",
                "steps": [
                    "Block Friday deployments after 2pm",
                    "Require 2 approvers for production",
                    "Implement canary deployments",
                    "Add automatic rollback on error spike",
                ],
                "effort": 2.0,
            },
        }

        template = rec_templates.get(root_cause)
        if not template:
            return None

        service_str = ", ".join(services[:3]) if services else "affected services"

        priority = RecommendationPriority.CRITICAL if incident_count >= 8 else \
                   RecommendationPriority.HIGH if incident_count >= 5 or trend == "increasing" else \
                   RecommendationPriority.MEDIUM if incident_count >= 3 else \
                   RecommendationPriority.LOW

        percentage = (incident_count / total_incidents * 100) if total_incidents > 0 else 0
        downtime_saved = incident_count * 45
        revenue_saved = downtime_saved * 2000

        return ProactiveRecommendation(
            recommendation_id=rec_id,
            priority=priority,
            recommendation_type=template["type"],
            title=template["title"].format(services=service_str),
            description=template["description"],
            rationale=f"Would have prevented {incident_count} incidents ({percentage:.1f}% of total)",
            affected_services=services,
            prevention_metrics=PreventionMetric(
                incidents_preventable=incident_count,
                percentage_of_total=percentage,
                estimated_downtime_saved_minutes=downtime_saved,
                estimated_revenue_saved=revenue_saved,
            ),
            implementation_effort_days=template["effort"],
            implementation_steps=template["steps"],
            related_incidents=incident_ids[:5] if isinstance(incident_ids, list) else [],
        )

    def generate_runbook(
        self,
        root_cause: str,
        resolutions: list[dict[str, Any]],
    ) -> AutoRunbook:
        """Generate a runbook from past resolutions."""
        self._runbook_counter += 1
        runbook_id = f"runbook_{self._runbook_counter}"

        runbook_templates: dict[str, _RunbookTemplate] = {
            "resource_exhaustion": {
                "title": "Connection Pool Exhaustion",
                "symptoms": [
                    "Connection timeout errors in logs",
                    "Increased latency on database operations",
                    "Pods showing high connection count",
                ],
                "diagnostic": [
                    "Check current connection count: SELECT count(*) FROM pg_stat_activity;",
                    "Identify service holding connections: kubectl get pods | grep -v Running",
                    "Check pool configuration: kubectl describe configmap db-config",
                ],
                "resolution": [
                    "Restart affected service: kubectl rollout restart deploy/{service}",
                    "If persists, increase pool size in configmap",
                    "If still failing, check for connection leaks in recent deploys",
                ],
            },
            "null_undefined": {
                "title": "Null Pointer Exception",
                "symptoms": [
                    "AttributeError or NoneType errors in logs",
                    "Specific endpoint returning 500 errors",
                    "Error rate spike after recent deploy",
                ],
                "diagnostic": [
                    "Check recent deploys: gh pr list --state merged --limit 5",
                    "Review error logs: kubectl logs deploy/{service} | grep -i none",
                    "Identify affected code path from stack trace",
                ],
                "resolution": [
                    "If recent deploy: rollback with kubectl rollout undo deploy/{service}",
                    "Add null check to identified code path",
                    "Deploy fix with hotfix process",
                ],
            },
        }

        default_template: _RunbookTemplate = {
            "title": f"Generic {root_cause} Resolution",
            "symptoms": ["Error rate increase", "Service degradation"],
            "diagnostic": ["Check logs", "Review recent changes"],
            "resolution": ["Investigate and fix", "Consider rollback if recent deploy"],
        }
        template: _RunbookTemplate = runbook_templates.get(root_cause, default_template)

        incident_ids = [r.get("incident_id", "") for r in resolutions[:5]]
        avg_time = sum(r.get("resolution_time_minutes", 30) for r in resolutions) / len(resolutions) if resolutions else 30
        success_rate = sum(1 for r in resolutions if r.get("successful", True)) / len(resolutions) if resolutions else 0.8

        runbook = AutoRunbook(
            runbook_id=runbook_id,
            title=template["title"],
            description=f"Auto-generated from {len(resolutions)} similar incidents",
            symptoms=list(template["symptoms"]),
            diagnostic_steps=list(template["diagnostic"]),
            resolution_steps=list(template["resolution"]),
            source_incidents=incident_ids,
            success_rate=success_rate,
            avg_resolution_time_minutes=int(avg_time),
            created_at=datetime.now(timezone.utc),
        )

        self._runbooks[runbook_id] = runbook

        log.info(
            "runbook generated",
            extra={
                "runbook_id": runbook_id,
                "source_incidents": len(resolutions),
                "success_rate": success_rate,
            },
        )

        return runbook

    def get_recommendation(
        self,
        recommendation_id: str,
    ) -> ProactiveRecommendation | None:
        """Get a recommendation by ID."""
        return self._recommendations.get(recommendation_id)

    def get_runbook(self, runbook_id: str) -> AutoRunbook | None:
        """Get a runbook by ID."""
        return self._runbooks.get(runbook_id)

    def mark_recommendation_implemented(
        self,
        recommendation_id: str,
        implemented_by: str,
    ) -> bool:
        """Mark a recommendation as implemented."""
        rec = self._recommendations.get(recommendation_id)
        if rec:
            rec.status = "implemented"
            return True
        return False

    def get_high_priority_recommendations(
        self,
    ) -> list[ProactiveRecommendation]:
        """Get all high priority recommendations."""
        return [
            r for r in self._recommendations.values()
            if r.priority in (RecommendationPriority.CRITICAL, RecommendationPriority.HIGH)
            and r.status == "open"
        ]

    def get_all_runbooks(self) -> list[AutoRunbook]:
        """Get all runbooks."""
        return list(self._runbooks.values())

    def get_dashboard_data(self) -> dict[str, Any]:
        """Get data for the recommendations dashboard."""
        all_recs = list(self._recommendations.values())
        open_recs = [r for r in all_recs if r.status == "open"]

        by_priority: dict[str, int] = {}
        for rec in open_recs:
            p = rec.priority.value
            by_priority[p] = by_priority.get(p, 0) + 1

        total_preventable = sum(
            r.prevention_metrics.incidents_preventable for r in open_recs
        )
        total_savings = sum(
            r.prevention_metrics.estimated_revenue_saved for r in open_recs
        )

        return {
            "total_recommendations": len(all_recs),
            "open_recommendations": len(open_recs),
            "by_priority": by_priority,
            "total_preventable_incidents": total_preventable,
            "total_potential_savings": total_savings,
            "top_recommendations": [
                r.to_dict() for r in sorted(
                    open_recs,
                    key=lambda x: x.prevention_metrics.incidents_preventable,
                    reverse=True,
                )[:5]
            ],
            "runbooks": [r.to_dict() for r in list(self._runbooks.values())[:5]],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }


_engine: RecommendationEngine | None = None


def get_recommendation_engine() -> RecommendationEngine:
    """Get the default recommendation engine."""
    global _engine
    if _engine is None:
        _engine = RecommendationEngine()
    return _engine
