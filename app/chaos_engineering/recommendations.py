"""AI Chaos Recommendations.

Recommends chaos experiments based on incident history and system analysis.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("devcopilot.chaos_engineering.recommendations")


class ExperimentRisk(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ExperimentCategory(str, Enum):
    LATENCY = "latency"
    FAILURE = "failure"
    RESOURCE = "resource"
    NETWORK = "network"
    DATA = "data"
    DEPENDENCY = "dependency"


@dataclass
class ChaosRecommendation:
    """A recommended chaos experiment."""

    recommendation_id: str
    title: str
    description: str
    category: ExperimentCategory
    risk: ExperimentRisk
    hypothesis: str
    expected_behavior: str
    related_incidents: list[str]
    target_service: str
    experiment_config: dict[str, Any]
    estimated_duration_minutes: int = 10
    requires_maintenance_window: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.recommendation_id,
            "title": self.title,
            "description": self.description,
            "category": self.category.value,
            "risk": self.risk.value,
            "hypothesis": self.hypothesis,
            "expected_behavior": self.expected_behavior,
            "related_incidents": self.related_incidents,
            "target_service": self.target_service,
            "config": self.experiment_config,
            "duration_minutes": self.estimated_duration_minutes,
            "maintenance_window": self.requires_maintenance_window,
        }


class ChaosRecommendationEngine:
    """Generates AI-powered chaos recommendations."""

    def __init__(self) -> None:
        self._incidents: dict[str, list[dict[str, Any]]] = {}
        self._service_dependencies: dict[str, list[str]] = {}
        self._tested_scenarios: dict[str, set[str]] = {}

    def record_incident(
        self,
        org_id: str,
        incident_id: str,
        title: str,
        root_cause: str,
        affected_services: list[str],
    ) -> None:
        """Record an incident for analysis."""
        if org_id not in self._incidents:
            self._incidents[org_id] = []

        self._incidents[org_id].append({
            "incident_id": incident_id,
            "title": title,
            "root_cause": root_cause,
            "services": affected_services,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        })

    def set_dependencies(
        self,
        org_id: str,
        service: str,
        dependencies: list[str],
    ) -> None:
        """Set service dependencies."""
        key = f"{org_id}:{service}"
        self._service_dependencies[key] = dependencies

    def mark_tested(self, org_id: str, scenario: str) -> None:
        """Mark a scenario as tested."""
        if org_id not in self._tested_scenarios:
            self._tested_scenarios[org_id] = set()
        self._tested_scenarios[org_id].add(scenario)

    def get_recommendations(
        self,
        org_id: str,
        limit: int = 5,
    ) -> list[ChaosRecommendation]:
        """Get recommended chaos experiments."""
        recommendations: list[ChaosRecommendation] = []

        incident_based = self._analyze_incidents(org_id)
        recommendations.extend(incident_based)

        dependency_based = self._analyze_dependencies(org_id)
        recommendations.extend(dependency_based)

        untested = self._suggest_untested_scenarios(org_id)
        recommendations.extend(untested)

        recommendations.sort(key=lambda r: (
            {"high": 0, "medium": 1, "low": 2}[r.risk.value],
            len(r.related_incidents),
        ))

        return recommendations[:limit]

    def _analyze_incidents(self, org_id: str) -> list[ChaosRecommendation]:
        """Generate recommendations based on incident history."""
        recommendations = []
        incidents = self._incidents.get(org_id, [])

        patterns: dict[str, list[str]] = {}
        for incident in incidents:
            root_cause = incident["root_cause"].lower()

            if "timeout" in root_cause or "latency" in root_cause:
                key = "latency"
            elif "connection" in root_cause or "pool" in root_cause:
                key = "connection"
            elif "memory" in root_cause or "oom" in root_cause:
                key = "resource"
            elif "network" in root_cause:
                key = "network"
            else:
                continue

            if key not in patterns:
                patterns[key] = []
            patterns[key].append(incident["incident_id"])

        if "latency" in patterns:
            recommendations.append(ChaosRecommendation(
                recommendation_id="rec_latency_1",
                title="Service Timeout Injection",
                description="Inject latency to test circuit breaker behavior",
                category=ExperimentCategory.LATENCY,
                risk=ExperimentRisk.LOW,
                hypothesis="Circuit breaker activates, service degrades gracefully",
                expected_behavior="Checkout shows 'retry later' message",
                related_incidents=patterns["latency"][:3],
                target_service="payment-service",
                experiment_config={
                    "type": "latency_injection",
                    "latency_ms": 5000,
                    "duration_seconds": 120,
                },
                estimated_duration_minutes=5,
            ))

        if "connection" in patterns:
            recommendations.append(ChaosRecommendation(
                recommendation_id="rec_connection_1",
                title="Connection Pool Exhaustion",
                description="Simulate connection pool exhaustion",
                category=ExperimentCategory.RESOURCE,
                risk=ExperimentRisk.MEDIUM,
                hypothesis="Application handles connection limits gracefully",
                expected_behavior="Queue requests or return graceful error",
                related_incidents=patterns["connection"][:3],
                target_service="database",
                experiment_config={
                    "type": "connection_limit",
                    "max_connections": 5,
                    "duration_seconds": 180,
                },
                estimated_duration_minutes=10,
            ))

        if "resource" in patterns:
            recommendations.append(ChaosRecommendation(
                recommendation_id="rec_resource_1",
                title="Memory Pressure Test",
                description="Apply memory pressure to test OOM handling",
                category=ExperimentCategory.RESOURCE,
                risk=ExperimentRisk.MEDIUM,
                hypothesis="Pods restart cleanly without data loss",
                expected_behavior="Kubernetes reschedules pods, no cascade failure",
                related_incidents=patterns["resource"][:3],
                target_service="api-server",
                experiment_config={
                    "type": "memory_stress",
                    "memory_mb": 512,
                    "duration_seconds": 300,
                },
                estimated_duration_minutes=10,
            ))

        return recommendations

    def _analyze_dependencies(self, org_id: str) -> list[ChaosRecommendation]:
        """Generate recommendations based on dependencies."""
        recommendations = []

        for key, deps in self._service_dependencies.items():
            if not key.startswith(f"{org_id}:"):
                continue

            service = key.split(":")[1]

            for dep in deps:
                scenario = f"{service}_depends_{dep}"
                tested = self._tested_scenarios.get(org_id, set())

                if scenario not in tested:
                    recommendations.append(ChaosRecommendation(
                        recommendation_id=f"rec_dep_{scenario}",
                        title=f"{dep} Dependency Failure",
                        description=f"Test {service} behavior when {dep} is unavailable",
                        category=ExperimentCategory.DEPENDENCY,
                        risk=ExperimentRisk.MEDIUM,
                        hypothesis=f"{service} handles {dep} failure gracefully",
                        expected_behavior="Fallback behavior or graceful degradation",
                        related_incidents=[],
                        target_service=dep,
                        experiment_config={
                            "type": "service_unavailable",
                            "target": dep,
                            "duration_seconds": 120,
                        },
                        estimated_duration_minutes=5,
                    ))

        return recommendations[:2]

    def _suggest_untested_scenarios(self, org_id: str) -> list[ChaosRecommendation]:
        """Suggest scenarios that haven't been tested."""
        recommendations = []
        tested = self._tested_scenarios.get(org_id, set())

        standard_scenarios = [
            {
                "scenario": "database_failover",
                "title": "Database Primary Failure",
                "description": "Simulate primary database failure to test failover",
                "category": ExperimentCategory.FAILURE,
                "risk": ExperimentRisk.HIGH,
                "hypothesis": "Replica promotes within 30 seconds",
                "config": {"type": "kill_primary", "duration_seconds": 60},
                "maintenance": True,
            },
            {
                "scenario": "network_partition",
                "title": "Network Partition",
                "description": "Simulate network partition between services",
                "category": ExperimentCategory.NETWORK,
                "risk": ExperimentRisk.MEDIUM,
                "hypothesis": "Services detect partition and recover",
                "config": {"type": "network_partition", "duration_seconds": 120},
                "maintenance": False,
            },
            {
                "scenario": "dns_failure",
                "title": "DNS Resolution Failure",
                "description": "Simulate DNS lookup failures",
                "category": ExperimentCategory.NETWORK,
                "risk": ExperimentRisk.LOW,
                "hypothesis": "Services use cached DNS and handle failures",
                "config": {"type": "dns_failure", "duration_seconds": 60},
                "maintenance": False,
            },
        ]

        for scenario in standard_scenarios:
            scenario_name = str(scenario["scenario"])
            if scenario_name not in tested:
                config = scenario["config"]
                recommendations.append(ChaosRecommendation(
                    recommendation_id=f"rec_standard_{scenario_name}",
                    title=str(scenario["title"]),
                    description=str(scenario["description"]),
                    category=scenario["category"],  # type: ignore[arg-type]
                    risk=scenario["risk"],  # type: ignore[arg-type]
                    hypothesis=str(scenario["hypothesis"]),
                    expected_behavior="System handles failure gracefully",
                    related_incidents=[],
                    target_service="system",
                    experiment_config=config if isinstance(config, dict) else {},
                    requires_maintenance_window=bool(scenario["maintenance"]),
                ))

        return recommendations[:2]


_engine: ChaosRecommendationEngine | None = None


def get_recommendation_engine() -> ChaosRecommendationEngine:
    """Get the default recommendation engine."""
    global _engine
    if _engine is None:
        _engine = ChaosRecommendationEngine()
    return _engine
