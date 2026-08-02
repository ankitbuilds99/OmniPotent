"""Change Intelligence - Detect and correlate changes with incidents."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from enum import Enum

log = logging.getLogger("omnipotent.change_intelligence.detector")


class ChangeType(str, Enum):
    """Types of changes."""
    DEPLOYMENT = "deployment"
    CONFIG_CHANGE = "config_change"
    FEATURE_FLAG = "feature_flag"
    DATABASE_MIGRATION = "database_migration"
    INFRASTRUCTURE = "infrastructure"
    SCALING = "scaling"
    DEPENDENCY_UPDATE = "dependency_update"
    SECURITY_PATCH = "security_patch"


class ChangeRisk(str, Enum):
    """Risk levels for changes."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class Change:
    """A change event."""
    id: str
    change_type: ChangeType
    service: str
    description: str
    timestamp: datetime
    author: str = ""
    risk: ChangeRisk = ChangeRisk.LOW
    version: str = ""
    previous_version: str = ""
    commit_sha: str = ""
    pr_number: int | None = None
    rollback_available: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "change_type": self.change_type.value,
            "service": self.service,
            "description": self.description,
            "timestamp": self.timestamp.isoformat(),
            "author": self.author,
            "risk": self.risk.value,
            "version": self.version,
            "previous_version": self.previous_version,
            "commit_sha": self.commit_sha,
            "pr_number": self.pr_number,
            "rollback_available": self.rollback_available,
        }


@dataclass
class ChangeCorrelation:
    """Correlation between a change and an incident."""
    change: Change
    incident_id: str
    confidence: float  # 0-1
    time_delta_seconds: int
    correlation_reasons: list[str]
    is_likely_cause: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "change": self.change.to_dict(),
            "incident_id": self.incident_id,
            "confidence": self.confidence,
            "time_delta_seconds": self.time_delta_seconds,
            "correlation_reasons": self.correlation_reasons,
            "is_likely_cause": self.is_likely_cause,
        }


@dataclass
class ChangeAnalysis:
    """Analysis result for change intelligence."""
    incident_id: str
    incident_time: datetime
    changes_found: int
    correlated_changes: list[ChangeCorrelation]
    likely_cause: Change | None
    recommendation: str
    analysis_duration_ms: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "incident_id": self.incident_id,
            "incident_time": self.incident_time.isoformat(),
            "changes_found": self.changes_found,
            "correlated_changes": [c.to_dict() for c in self.correlated_changes],
            "likely_cause": self.likely_cause.to_dict() if self.likely_cause else None,
            "recommendation": self.recommendation,
            "analysis_duration_ms": self.analysis_duration_ms,
        }


class ChangeDetector:
    """Detects and tracks changes across the system.

    Features:
    - Multi-source change detection (Git, K8s, feature flags, DB)
    - Change correlation with incidents
    - Risk assessment
    - Rollback recommendations
    """

    # Time windows for correlation
    CORRELATION_WINDOWS = {
        ChangeType.DEPLOYMENT: timedelta(hours=2),
        ChangeType.CONFIG_CHANGE: timedelta(hours=1),
        ChangeType.FEATURE_FLAG: timedelta(hours=4),
        ChangeType.DATABASE_MIGRATION: timedelta(hours=6),
        ChangeType.INFRASTRUCTURE: timedelta(hours=3),
        ChangeType.SCALING: timedelta(minutes=30),
        ChangeType.DEPENDENCY_UPDATE: timedelta(hours=12),
        ChangeType.SECURITY_PATCH: timedelta(hours=2),
    }

    # Risk weights by change type
    RISK_WEIGHTS = {
        ChangeType.DEPLOYMENT: 0.8,
        ChangeType.CONFIG_CHANGE: 0.7,
        ChangeType.FEATURE_FLAG: 0.6,
        ChangeType.DATABASE_MIGRATION: 0.9,
        ChangeType.INFRASTRUCTURE: 0.85,
        ChangeType.SCALING: 0.3,
        ChangeType.DEPENDENCY_UPDATE: 0.75,
        ChangeType.SECURITY_PATCH: 0.5,
    }

    def __init__(self):
        """Initialize the change detector."""
        self._changes: list[Change] = []
        self._sources: list[str] = []
        log.info("ChangeDetector initialized")

    async def collect_changes(
        self,
        services: list[str],
        start_time: datetime,
        end_time: datetime,
    ) -> list[Change]:
        """Collect changes from all sources.

        Args:
            services: Services to check
            start_time: Start of time window
            end_time: End of time window

        Returns:
            List of changes
        """
        all_changes: list[Change] = []

        # Collect from various sources
        tasks = [
            self._collect_deployments(services, start_time, end_time),
            self._collect_config_changes(services, start_time, end_time),
            self._collect_feature_flags(services, start_time, end_time),
            self._collect_infrastructure_changes(services, start_time, end_time),
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, list):
                all_changes.extend(result)

        # Sort by timestamp
        all_changes.sort(key=lambda c: c.timestamp, reverse=True)

        self._changes = all_changes
        log.info("Collected %d changes", len(all_changes))

        return all_changes

    async def _collect_deployments(
        self,
        services: list[str],
        start_time: datetime,
        end_time: datetime,
    ) -> list[Change]:
        """Collect deployment changes from Kubernetes/CD system."""
        changes = []

        # In production: query K8s API, ArgoCD, etc.
        # Simulated changes
        for service in services[:2]:
            changes.append(Change(
                id=f"deploy-{service}-1",
                change_type=ChangeType.DEPLOYMENT,
                service=service,
                description=f"Deployed {service} v2.1.0",
                timestamp=start_time + timedelta(hours=1),
                author="ci-bot",
                version="2.1.0",
                previous_version="2.0.5",
                commit_sha="abc123",
                pr_number=456,
                risk=ChangeRisk.MEDIUM,
            ))

        return changes

    async def _collect_config_changes(
        self,
        services: list[str],
        start_time: datetime,
        end_time: datetime,
    ) -> list[Change]:
        """Collect config changes from ConfigMaps/Vault/etc."""
        return [
            Change(
                id="config-db-1",
                change_type=ChangeType.CONFIG_CHANGE,
                service="database",
                description="Updated connection pool size from 10 to 50",
                timestamp=start_time + timedelta(minutes=30),
                author="ops-team",
                risk=ChangeRisk.MEDIUM,
            ),
        ]

    async def _collect_feature_flags(
        self,
        services: list[str],
        start_time: datetime,
        end_time: datetime,
    ) -> list[Change]:
        """Collect feature flag changes from LaunchDarkly/etc."""
        return [
            Change(
                id="flag-new-checkout-1",
                change_type=ChangeType.FEATURE_FLAG,
                service="checkout",
                description="Enabled new-checkout-flow for 50% of users",
                timestamp=start_time + timedelta(hours=2),
                author="product-team",
                risk=ChangeRisk.LOW,
            ),
        ]

    async def _collect_infrastructure_changes(
        self,
        services: list[str],
        start_time: datetime,
        end_time: datetime,
    ) -> list[Change]:
        """Collect infrastructure changes from Terraform/CloudFormation."""
        return [
            Change(
                id="infra-scaling-1",
                change_type=ChangeType.SCALING,
                service="api-gateway",
                description="Auto-scaled from 3 to 5 replicas",
                timestamp=start_time + timedelta(minutes=45),
                author="auto-scaler",
                risk=ChangeRisk.LOW,
            ),
        ]

    async def correlate_with_incident(
        self,
        incident_id: str,
        incident_time: datetime,
        affected_services: list[str],
    ) -> ChangeAnalysis:
        """Correlate changes with an incident.

        Args:
            incident_id: Incident identifier
            incident_time: When the incident occurred
            affected_services: Services affected by incident

        Returns:
            ChangeAnalysis with correlations
        """
        import time
        start = time.time()

        # Collect changes in relevant window
        window_start = incident_time - timedelta(hours=24)
        changes = await self.collect_changes(
            affected_services, window_start, incident_time
        )

        correlations: list[ChangeCorrelation] = []

        for change in changes:
            # Check if change is within correlation window
            window = self.CORRELATION_WINDOWS.get(
                change.change_type, timedelta(hours=2)
            )

            if change.timestamp > incident_time - window and change.timestamp <= incident_time:
                correlation = self._calculate_correlation(
                    change, incident_id, incident_time, affected_services
                )
                if correlation.confidence > 0.3:
                    correlations.append(correlation)

        # Sort by confidence
        correlations.sort(key=lambda c: c.confidence, reverse=True)

        # Identify likely cause
        likely_cause = None
        recommendation = "No changes found that correlate with this incident."

        if correlations:
            top = correlations[0]
            if top.confidence >= 0.7:
                likely_cause = top.change
                recommendation = self._generate_recommendation(top)
            elif top.confidence >= 0.5:
                recommendation = (
                    f"Possible cause: {top.change.description}. "
                    f"Confidence: {top.confidence:.0%}. Consider investigating."
                )

        duration_ms = int((time.time() - start) * 1000)

        return ChangeAnalysis(
            incident_id=incident_id,
            incident_time=incident_time,
            changes_found=len(changes),
            correlated_changes=correlations,
            likely_cause=likely_cause,
            recommendation=recommendation,
            analysis_duration_ms=duration_ms,
        )

    def _calculate_correlation(
        self,
        change: Change,
        incident_id: str,
        incident_time: datetime,
        affected_services: list[str],
    ) -> ChangeCorrelation:
        """Calculate correlation between change and incident."""
        reasons: list[str] = []
        confidence = 0.0

        # Time proximity (closer = higher correlation)
        time_delta = (incident_time - change.timestamp).total_seconds()
        time_score = max(0, 1 - (time_delta / 3600))  # 1 hour decay
        if time_score > 0.5:
            reasons.append(f"Change occurred {int(time_delta/60)} minutes before incident")
        confidence += time_score * 0.3

        # Service match
        if change.service in affected_services:
            confidence += 0.3
            reasons.append(f"Change affects impacted service: {change.service}")

        # Change type risk
        risk_weight = self.RISK_WEIGHTS.get(change.change_type, 0.5)
        confidence += risk_weight * 0.2
        if risk_weight >= 0.7:
            reasons.append(f"High-risk change type: {change.change_type.value}")

        # Explicit risk level
        risk_scores = {
            ChangeRisk.CRITICAL: 0.2,
            ChangeRisk.HIGH: 0.15,
            ChangeRisk.MEDIUM: 0.1,
            ChangeRisk.LOW: 0.05,
        }
        confidence += risk_scores.get(change.risk, 0.05)

        is_likely = confidence >= 0.7

        return ChangeCorrelation(
            change=change,
            incident_id=incident_id,
            confidence=min(1.0, confidence),
            time_delta_seconds=int(time_delta),
            correlation_reasons=reasons,
            is_likely_cause=is_likely,
        )

    def _generate_recommendation(self, correlation: ChangeCorrelation) -> str:
        """Generate recommendation based on correlation."""
        change = correlation.change
        recs = []

        if change.rollback_available:
            recs.append(f"Consider rolling back {change.service} to {change.previous_version}")

        if change.change_type == ChangeType.FEATURE_FLAG:
            recs.append("Consider disabling the feature flag")

        if change.change_type == ChangeType.CONFIG_CHANGE:
            recs.append("Review and revert the configuration change")

        if change.pr_number:
            recs.append(f"Review PR #{change.pr_number} for issues")

        if not recs:
            recs.append(f"Investigate recent change: {change.description}")

        return " ".join(recs)

    def assess_change_risk(self, change: Change) -> dict[str, Any]:
        """Assess risk of a proposed change.

        Args:
            change: Change to assess

        Returns:
            Risk assessment
        """
        factors: list[str] = []
        risk_score = 0.0

        # Base risk from change type
        base_risk = self.RISK_WEIGHTS.get(change.change_type, 0.5)
        risk_score += base_risk * 0.4
        factors.append(f"Change type risk: {base_risk:.0%}")

        # Time-based risk (deployments during peak hours)
        hour = change.timestamp.hour
        if 9 <= hour <= 17:  # Business hours
            risk_score += 0.1
            factors.append("Deploying during business hours")

        # Check for recent incidents on service
        # In production: query incident history
        recent_incidents = 0
        if recent_incidents > 0:
            risk_score += 0.2
            factors.append(f"Service had {recent_incidents} recent incidents")

        # Determine risk level
        if risk_score >= 0.8:
            level = ChangeRisk.CRITICAL
        elif risk_score >= 0.6:
            level = ChangeRisk.HIGH
        elif risk_score >= 0.4:
            level = ChangeRisk.MEDIUM
        else:
            level = ChangeRisk.LOW

        return {
            "risk_score": risk_score,
            "risk_level": level.value,
            "factors": factors,
            "recommendations": self._get_risk_recommendations(level),
        }

    def _get_risk_recommendations(self, risk: ChangeRisk) -> list[str]:
        """Get recommendations based on risk level."""
        if risk == ChangeRisk.CRITICAL:
            return [
                "Require additional approval",
                "Enable enhanced monitoring",
                "Prepare rollback plan",
                "Consider deploying to staging first",
            ]
        elif risk == ChangeRisk.HIGH:
            return [
                "Enable enhanced monitoring",
                "Prepare rollback plan",
                "Notify on-call team",
            ]
        elif risk == ChangeRisk.MEDIUM:
            return [
                "Monitor closely for 30 minutes",
                "Have rollback ready",
            ]
        else:
            return ["Standard monitoring"]


def create_change_detector() -> ChangeDetector:
    """Factory function to create a change detector."""
    return ChangeDetector()
