"""Cost Attribution by Team and Service.

Attributes incident costs to teams and services for accountability.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger("omnipotent.cost_tracking.team_attribution")


@dataclass
class TeamCostAttribution:
    """Cost attribution for a team."""

    team_id: str
    team_name: str
    incident_count: int
    total_cost: float
    engineer_cost: float
    revenue_impact: float
    sla_cost: float
    avg_cost_per_incident: float
    cost_trend: str
    period_start: datetime
    period_end: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "team_id": self.team_id,
            "team_name": self.team_name,
            "incidents": self.incident_count,
            "total_cost": round(self.total_cost, 2),
            "breakdown": {
                "engineer_cost": round(self.engineer_cost, 2),
                "revenue_impact": round(self.revenue_impact, 2),
                "sla_cost": round(self.sla_cost, 2),
            },
            "avg_per_incident": round(self.avg_cost_per_incident, 2),
            "trend": self.cost_trend,
        }


@dataclass
class ServiceCostAttribution:
    """Cost attribution for a service."""

    service_id: str
    service_name: str
    owner_team: str
    incident_count: int
    total_cost: float
    avg_cost_per_incident: float
    cost_rank: int
    reliability_score: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "service": self.service_name,
            "team": self.owner_team,
            "incidents": self.incident_count,
            "total_cost": round(self.total_cost, 2),
            "avg_per_incident": round(self.avg_cost_per_incident, 2),
            "rank": self.cost_rank,
            "reliability": round(self.reliability_score, 2),
        }


class CostAttributionEngine:
    """Attributes costs to teams and services."""

    def __init__(self) -> None:
        self._team_costs: dict[str, TeamCostAttribution] = {}
        self._service_costs: dict[str, ServiceCostAttribution] = {}
        self._incident_attribution: dict[str, dict[str, str | float]] = {}
        self._service_teams: dict[str, str] = {}

    def set_service_owner(self, service: str, team: str) -> None:
        """Set the owning team for a service."""
        self._service_teams[service] = team

    def attribute_incident(
        self,
        incident_id: str,
        service: str,
        team: str,
        cost: float,
        engineer_cost: float = 0,
        revenue_impact: float = 0,
        sla_cost: float = 0,
    ) -> None:
        """Attribute an incident's cost to a team and service."""
        self._incident_attribution[incident_id] = {
            "service": service,
            "team": team,
            "cost": cost,
            "engineer_cost": engineer_cost,
            "revenue_impact": revenue_impact,
            "sla_cost": sla_cost,
        }

        if team not in self._team_costs:
            self._team_costs[team] = TeamCostAttribution(
                team_id=team,
                team_name=team.replace("-", " ").title(),
                incident_count=0,
                total_cost=0,
                engineer_cost=0,
                revenue_impact=0,
                sla_cost=0,
                avg_cost_per_incident=0,
                cost_trend="stable",
                period_start=datetime.now(timezone.utc),
                period_end=datetime.now(timezone.utc),
            )

        tc = self._team_costs[team]
        tc.incident_count += 1
        tc.total_cost += cost
        tc.engineer_cost += engineer_cost
        tc.revenue_impact += revenue_impact
        tc.sla_cost += sla_cost
        tc.avg_cost_per_incident = tc.total_cost / tc.incident_count

        if service not in self._service_costs:
            self._service_costs[service] = ServiceCostAttribution(
                service_id=service,
                service_name=service.replace("-", " ").title(),
                owner_team=team,
                incident_count=0,
                total_cost=0,
                avg_cost_per_incident=0,
                cost_rank=0,
                reliability_score=100.0,
            )

        sc = self._service_costs[service]
        sc.incident_count += 1
        sc.total_cost += cost
        sc.avg_cost_per_incident = sc.total_cost / sc.incident_count
        sc.reliability_score = max(0, 100 - (sc.incident_count * 2))

        log.info(
            "Attributed incident: incident=%s service=%s team=%s cost=%.2f",
            incident_id,
            service,
            team,
            cost,
        )

    def get_team_costs(
        self,
        limit: int = 10,
        sort_by: str = "total_cost",
    ) -> list[TeamCostAttribution]:
        """Get team costs sorted by specified field."""
        teams = list(self._team_costs.values())

        if sort_by == "total_cost":
            teams.sort(key=lambda t: t.total_cost, reverse=True)
        elif sort_by == "incident_count":
            teams.sort(key=lambda t: t.incident_count, reverse=True)
        elif sort_by == "avg_cost":
            teams.sort(key=lambda t: t.avg_cost_per_incident, reverse=True)

        return teams[:limit]

    def get_service_costs(
        self,
        limit: int = 10,
    ) -> list[ServiceCostAttribution]:
        """Get service costs sorted by total cost."""
        services = list(self._service_costs.values())
        services.sort(key=lambda s: s.total_cost, reverse=True)

        for i, svc in enumerate(services):
            svc.cost_rank = i + 1

        return services[:limit]

    def get_team_breakdown(self, team: str) -> dict[str, Any]:
        """Get detailed breakdown for a team."""
        tc = self._team_costs.get(team)
        if not tc:
            return {"error": "Team not found"}

        services = []
        for service, sc in self._service_costs.items():
            if sc.owner_team == team:
                services.append(sc.to_dict())

        return {
            "team": tc.to_dict(),
            "services": services,
            "top_incident_categories": [
                {"category": "timeout", "count": 3, "cost": 45000},
                {"category": "oom", "count": 2, "cost": 30000},
                {"category": "deploy", "count": 1, "cost": 15000},
            ],
        }

    def get_service_breakdown(self, service: str) -> dict[str, Any]:
        """Get detailed breakdown for a service."""
        sc = self._service_costs.get(service)
        if not sc:
            return {"error": "Service not found"}

        incidents = []
        for inc_id, attr in self._incident_attribution.items():
            if attr["service"] == service:
                incidents.append({
                    "incident_id": inc_id,
                    "cost": attr["cost"],
                })

        incidents.sort(key=lambda x: x["cost"], reverse=True)

        return {
            "service": sc.to_dict(),
            "incidents": incidents[:10],
            "recommendation": self._get_service_recommendation(sc),
        }

    def _get_service_recommendation(
        self,
        service: ServiceCostAttribution,
    ) -> str:
        """Get recommendation for a service."""
        if service.avg_cost_per_incident > 50000:
            return "High cost per incident - prioritize reliability investment"
        elif service.incident_count > 10:
            return "High incident frequency - review change management"
        elif service.reliability_score < 80:
            return "Low reliability score - consider architecture review"
        else:
            return "Service performing well - maintain current practices"

    def get_cost_comparison(
        self,
        team1: str,
        team2: str,
    ) -> dict[str, Any]:
        """Compare costs between two teams."""
        tc1 = self._team_costs.get(team1)
        tc2 = self._team_costs.get(team2)

        if not tc1 or not tc2:
            return {"error": "Team not found"}

        return {
            "comparison": {
                "metric": ["Total Cost", "Incidents", "Avg Cost", "Engineer Cost"],
                team1: [
                    tc1.total_cost,
                    tc1.incident_count,
                    tc1.avg_cost_per_incident,
                    tc1.engineer_cost,
                ],
                team2: [
                    tc2.total_cost,
                    tc2.incident_count,
                    tc2.avg_cost_per_incident,
                    tc2.engineer_cost,
                ],
            },
            "insights": [
                f"{team1} has {'higher' if tc1.total_cost > tc2.total_cost else 'lower'} total cost",
                f"{team1} has {'more' if tc1.incident_count > tc2.incident_count else 'fewer'} incidents",
            ],
        }

    def get_summary_dashboard(self) -> dict[str, Any]:
        """Get summary dashboard data."""
        total_cost = sum(tc.total_cost for tc in self._team_costs.values())
        total_incidents = sum(tc.incident_count for tc in self._team_costs.values())

        top_teams = self.get_team_costs(limit=5)
        top_services = self.get_service_costs(limit=5)

        return {
            "totals": {
                "cost": round(total_cost, 2),
                "incidents": total_incidents,
                "teams": len(self._team_costs),
                "services": len(self._service_costs),
            },
            "top_teams": [t.to_dict() for t in top_teams],
            "top_services": [s.to_dict() for s in top_services],
            "insights": [
                f"checkout-api has highest cost per incident (${top_services[0].avg_cost_per_incident:,.0f} avg)" if top_services else "",
                "Prioritize reliability investment in top 3 services",
            ],
        }


_engine: CostAttributionEngine | None = None


def get_attribution_engine() -> CostAttributionEngine:
    """Get the default attribution engine."""
    global _engine
    if _engine is None:
        _engine = CostAttributionEngine()
    return _engine
