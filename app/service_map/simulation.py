"""Failure simulation for service map.

Simulates "what if this service dies" scenarios.
"""

from __future__ import annotations

import logging
import secrets
from typing import Any

from app.service_map.graph import ServiceGraph
from app.service_map.models import (
    FailureScenario,
    FailureSimulation,
    ImpactedService,
    ImpactLevel,
)

log = logging.getLogger("omnipotent.service_map.simulation")


class FailureSimulator:
    """Simulates service failures to assess resilience."""

    def __init__(self, graph: ServiceGraph) -> None:
        self._graph = graph
        self._failure_modes = [
            "complete_outage",
            "high_latency",
            "high_error_rate",
            "intermittent_failures",
        ]

    def simulate_failure(
        self,
        service_id: str,
        failure_mode: str = "complete_outage",
    ) -> FailureScenario:
        """Simulate a single service failure."""
        blast = self._graph.calculate_blast_radius(service_id)

        mode_impact_multiplier = {
            "complete_outage": 1.0,
            "high_latency": 0.6,
            "high_error_rate": 0.5,
            "intermittent_failures": 0.3,
        }

        multiplier = mode_impact_multiplier.get(failure_mode, 1.0)

        adjusted_impacts: list[ImpactedService] = []
        for imp in blast.impacted_services:
            adjusted_percent = imp.estimated_impact_percent * multiplier

            if adjusted_percent >= 80:
                level = ImpactLevel.CRITICAL
            elif adjusted_percent >= 50:
                level = ImpactLevel.HIGH
            elif adjusted_percent >= 20:
                level = ImpactLevel.MEDIUM
            elif adjusted_percent >= 5:
                level = ImpactLevel.LOW
            else:
                level = ImpactLevel.NONE

            adjusted_impacts.append(ImpactedService(
                service_id=imp.service_id,
                service_name=imp.service_name,
                impact_level=level,
                impact_reason=f"{failure_mode} in {service_id}",
                estimated_impact_percent=adjusted_percent,
                is_direct_dependency=imp.is_direct_dependency,
            ))

        total_impact = sum(
            imp.estimated_impact_percent for imp in adjusted_impacts
        ) / len(adjusted_impacts) if adjusted_impacts else 0

        revenue_per_service = 5000
        revenue_impact = len(adjusted_impacts) * revenue_per_service * multiplier

        recommendations = self._generate_recommendations(
            service_id,
            failure_mode,
            adjusted_impacts,
        )

        return FailureScenario(
            failed_service=service_id,
            failure_mode=failure_mode,
            impacted_services=adjusted_impacts,
            total_impact_percent=total_impact,
            revenue_impact_per_hour=revenue_impact,
            mitigation_recommendations=recommendations,
        )

    def _generate_recommendations(
        self,
        service_id: str,
        failure_mode: str,
        impacted: list[ImpactedService],
    ) -> list[str]:
        """Generate mitigation recommendations."""
        recommendations: list[str] = []

        critical_count = sum(
            1 for imp in impacted
            if imp.impact_level == ImpactLevel.CRITICAL
        )
        high_count = sum(
            1 for imp in impacted
            if imp.impact_level == ImpactLevel.HIGH
        )

        if critical_count > 0:
            recommendations.append(
                f"Add circuit breaker for {service_id} in {critical_count} critical dependents"
            )
            recommendations.append(
                f"Consider redundancy/multi-region deployment for {service_id}"
            )

        if high_count > 2:
            recommendations.append(
                "Implement graceful degradation in downstream services"
            )

        if failure_mode == "high_latency":
            recommendations.append(
                f"Add timeout and retry logic for calls to {service_id}"
            )
            recommendations.append(
                "Implement request hedging for latency-sensitive paths"
            )

        if failure_mode == "high_error_rate":
            recommendations.append(
                f"Add fallback responses when {service_id} returns errors"
            )
            recommendations.append(
                "Implement bulkhead pattern to isolate failures"
            )

        if failure_mode == "intermittent_failures":
            recommendations.append(
                f"Add retry logic with exponential backoff for {service_id}"
            )

        recommendations.append(
            f"Add health checks and automated recovery for {service_id}"
        )

        return recommendations[:5]

    def simulate_all_failures(
        self,
        failure_mode: str = "complete_outage",
    ) -> FailureSimulation:
        """Simulate failures for all services."""
        simulation_id = f"sim_{secrets.token_hex(8)}"

        scenarios: list[FailureScenario] = []
        for service in self._graph.get_all_services():
            scenario = self.simulate_failure(service.service_id, failure_mode)
            scenarios.append(scenario)

        scenarios.sort(
            key=lambda s: s.total_impact_percent,
            reverse=True,
        )

        most_critical = [s.failed_service for s in scenarios[:5]]

        avg_impact = sum(s.total_impact_percent for s in scenarios) / len(scenarios) if scenarios else 0
        resilience_score = 100 - avg_impact

        recommendations = self._aggregate_recommendations(scenarios)

        return FailureSimulation(
            simulation_id=simulation_id,
            scenarios=scenarios,
            most_critical_services=most_critical,
            resilience_score=resilience_score,
            recommendations=recommendations,
        )

    def _aggregate_recommendations(
        self,
        scenarios: list[FailureScenario],
    ) -> list[str]:
        """Aggregate recommendations from all scenarios."""
        recommendation_counts: dict[str, int] = {}

        for scenario in scenarios:
            for rec in scenario.mitigation_recommendations:
                rec_key = rec[:50]
                recommendation_counts[rec_key] = recommendation_counts.get(rec_key, 0) + 1

        sorted_recs = sorted(
            recommendation_counts.items(),
            key=lambda x: x[1],
            reverse=True,
        )

        top_recs: list[str] = []
        for rec, count in sorted_recs[:10]:
            for scenario in scenarios:
                for full_rec in scenario.mitigation_recommendations:
                    if full_rec.startswith(rec):
                        top_recs.append(full_rec)
                        break
                else:
                    continue
                break

        return top_recs[:5]

    def compare_scenarios(
        self,
        service_ids: list[str],
        failure_mode: str = "complete_outage",
    ) -> dict[str, Any]:
        """Compare failure scenarios for multiple services."""
        scenarios: list[dict[str, Any]] = []
        for sid in service_ids:
            scenario = self.simulate_failure(sid, failure_mode)
            scenarios.append({
                "service_id": sid,
                "scenario": scenario.to_dict(),
            })

        most_impactful_entry = max(
            scenarios,
            key=lambda s: s["scenario"]["total_impact_percent"],
        )
        most_impactful: str = most_impactful_entry["service_id"]

        least_impactful_entry = min(
            scenarios,
            key=lambda s: s["scenario"]["total_impact_percent"],
        )
        least_impactful: str = least_impactful_entry["service_id"]

        return {
            "scenarios": scenarios,
            "most_impactful_service": most_impactful,
            "least_impactful_service": least_impactful,
            "comparison_summary": f"Failing {most_impactful} would cause the most impact",
        }

    def chaos_test_plan(
        self,
        target_services: list[str] | None = None,
    ) -> dict[str, Any]:
        """Generate a chaos testing plan."""
        if target_services is None:
            simulation = self.simulate_all_failures()
            target_services = simulation.most_critical_services[:3]

        test_phases: list[dict[str, Any]] = []

        for i, sid in enumerate(target_services):
            for mode in ["high_latency", "high_error_rate", "complete_outage"]:
                scenario = self.simulate_failure(sid, mode)

                test_phases.append({
                    "phase": len(test_phases) + 1,
                    "service": sid,
                    "failure_mode": mode,
                    "expected_impact": scenario.total_impact_percent,
                    "expected_services_affected": len(scenario.impacted_services),
                    "success_criteria": [
                        "Affected services gracefully degrade",
                        "Error budgets not exhausted",
                        "Recovery within 5 minutes",
                    ],
                })

        return {
            "plan_id": f"chaos_{secrets.token_hex(4)}",
            "target_services": target_services,
            "total_phases": len(test_phases),
            "estimated_duration_hours": len(test_phases) * 0.5,
            "phases": test_phases,
            "prerequisites": [
                "Ensure monitoring is active",
                "Have rollback procedures ready",
                "Notify on-call teams",
            ],
        }


_simulator: FailureSimulator | None = None


def get_failure_simulator() -> FailureSimulator:
    """Get the default failure simulator."""
    global _simulator
    if _simulator is None:
        from app.service_map.graph import get_service_graph
        _simulator = FailureSimulator(get_service_graph())
    return _simulator
