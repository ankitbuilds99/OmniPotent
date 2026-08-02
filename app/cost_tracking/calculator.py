"""Incident Cost Calculator.

Calculates the full cost of incidents including engineer time, revenue impact,
and customer churn.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger("omnipotent.cost_tracking.calculator")


@dataclass
class EngineerTime:
    """Time spent by engineers on an incident."""

    engineer_id: str
    engineer_name: str
    role: str
    hours: float
    hourly_rate: float
    activity: str

    @property
    def cost(self) -> float:
        return self.hours * self.hourly_rate

    def to_dict(self) -> dict[str, Any]:
        return {
            "engineer": self.engineer_name,
            "role": self.role,
            "hours": round(self.hours, 2),
            "rate": self.hourly_rate,
            "activity": self.activity,
            "cost": round(self.cost, 2),
        }


@dataclass
class CostBreakdown:
    """Breakdown of incident costs."""

    engineer_time: float = 0
    lost_revenue: float = 0
    sla_penalties: float = 0
    customer_support: float = 0
    reputation_impact: float = 0
    infrastructure: float = 0
    opportunity_cost: float = 0

    @property
    def total(self) -> float:
        return (
            self.engineer_time
            + self.lost_revenue
            + self.sla_penalties
            + self.customer_support
            + self.reputation_impact
            + self.infrastructure
            + self.opportunity_cost
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "engineer_time": round(self.engineer_time, 2),
            "lost_revenue": round(self.lost_revenue, 2),
            "sla_penalties": round(self.sla_penalties, 2),
            "customer_support": round(self.customer_support, 2),
            "reputation_impact": round(self.reputation_impact, 2),
            "infrastructure": round(self.infrastructure, 2),
            "opportunity_cost": round(self.opportunity_cost, 2),
            "total": round(self.total, 2),
        }


@dataclass
class IncidentCost:
    """Full cost tracking for an incident."""

    incident_id: str
    title: str
    severity: str
    duration_minutes: int
    engineer_entries: list[EngineerTime]
    breakdown: CostBreakdown
    is_ongoing: bool = False
    calculated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def total_cost(self) -> float:
        return self.breakdown.total

    @property
    def cost_per_minute(self) -> float:
        if self.duration_minutes == 0:
            return 0
        return self.total_cost / self.duration_minutes

    def to_dict(self) -> dict[str, Any]:
        return {
            "incident_id": self.incident_id,
            "title": self.title,
            "severity": self.severity,
            "duration_minutes": self.duration_minutes,
            "engineer_time": [e.to_dict() for e in self.engineer_entries],
            "breakdown": self.breakdown.to_dict(),
            "total_cost": round(self.total_cost, 2),
            "cost_per_minute": round(self.cost_per_minute, 2),
            "is_ongoing": self.is_ongoing,
            "calculated_at": self.calculated_at.isoformat(),
        }


class CostCalculator:
    """Calculates incident costs in real-time."""

    def __init__(self) -> None:
        self._incidents: dict[str, IncidentCost] = {}
        self._salary_bands: dict[str, float] = {}
        self._revenue_rates: dict[str, float] = {}
        self._init_defaults()

    def _init_defaults(self) -> None:
        """Initialize default salary bands and rates."""
        self._salary_bands = {
            "sre": 150.0,
            "senior_engineer": 140.0,
            "engineer": 100.0,
            "manager": 180.0,
            "director": 250.0,
            "vp": 350.0,
            "support": 50.0,
        }

        self._revenue_rates = {
            "checkout": 500.0,
            "api": 100.0,
            "web": 50.0,
            "mobile": 75.0,
        }

    def set_salary_band(self, role: str, hourly_rate: float) -> None:
        """Set hourly rate for a role."""
        self._salary_bands[role] = hourly_rate

    def start_tracking(
        self,
        incident_id: str,
        title: str,
        severity: str,
    ) -> IncidentCost:
        """Start tracking costs for an incident."""
        incident = IncidentCost(
            incident_id=incident_id,
            title=title,
            severity=severity,
            duration_minutes=0,
            engineer_entries=[],
            breakdown=CostBreakdown(),
            is_ongoing=True,
        )

        self._incidents[incident_id] = incident

        log.info("Started cost tracking: incident=%s", incident_id)

        return incident

    def add_engineer_time(
        self,
        incident_id: str,
        engineer_id: str,
        engineer_name: str,
        role: str,
        hours: float,
        activity: str = "investigation",
    ) -> bool:
        """Add engineer time to an incident."""
        incident = self._incidents.get(incident_id)
        if not incident:
            return False

        hourly_rate = self._salary_bands.get(role, 100.0)

        entry = EngineerTime(
            engineer_id=engineer_id,
            engineer_name=engineer_name,
            role=role,
            hours=hours,
            hourly_rate=hourly_rate,
            activity=activity,
        )

        incident.engineer_entries.append(entry)
        incident.breakdown.engineer_time += entry.cost

        log.info(
            "Added engineer time: incident=%s engineer=%s hours=%.2f",
            incident_id,
            engineer_name,
            hours,
        )

        return True

    def update_revenue_loss(
        self,
        incident_id: str,
        failed_transactions: int,
        avg_transaction_value: float,
        recovery_rate: float = 0.2,
    ) -> float:
        """Update lost revenue for an incident."""
        incident = self._incidents.get(incident_id)
        if not incident:
            return 0

        gross_loss = failed_transactions * avg_transaction_value
        actual_loss = gross_loss * (1 - recovery_rate)

        incident.breakdown.lost_revenue = actual_loss

        return actual_loss

    def add_sla_penalty(
        self,
        incident_id: str,
        penalty_amount: float,
        customer: str = "",
    ) -> bool:
        """Add SLA penalty to an incident."""
        incident = self._incidents.get(incident_id)
        if not incident:
            return False

        incident.breakdown.sla_penalties += penalty_amount

        log.info(
            "Added SLA penalty: incident=%s amount=%.2f customer=%s",
            incident_id,
            penalty_amount,
            customer,
        )

        return True

    def add_support_cost(
        self,
        incident_id: str,
        tickets: int,
        cost_per_ticket: float = 50.0,
    ) -> float:
        """Add customer support costs."""
        incident = self._incidents.get(incident_id)
        if not incident:
            return 0

        cost = tickets * cost_per_ticket
        incident.breakdown.customer_support += cost

        return cost

    def add_reputation_cost(
        self,
        incident_id: str,
        negative_mentions: int,
        estimated_cost_per_mention: float = 200.0,
    ) -> float:
        """Add reputation/social media impact cost."""
        incident = self._incidents.get(incident_id)
        if not incident:
            return 0

        cost = negative_mentions * estimated_cost_per_mention
        incident.breakdown.reputation_impact += cost

        return cost

    def update_duration(
        self,
        incident_id: str,
        duration_minutes: int,
    ) -> bool:
        """Update incident duration."""
        incident = self._incidents.get(incident_id)
        if not incident:
            return False

        incident.duration_minutes = duration_minutes
        return True

    def finalize_incident(self, incident_id: str) -> IncidentCost | None:
        """Finalize and close incident cost tracking."""
        incident = self._incidents.get(incident_id)
        if not incident:
            return None

        incident.is_ongoing = False
        incident.calculated_at = datetime.now(timezone.utc)

        log.info(
            "Finalized incident cost: incident=%s total=%.2f",
            incident_id,
            incident.total_cost,
        )

        return incident

    def get_incident_cost(self, incident_id: str) -> IncidentCost | None:
        """Get current cost for an incident."""
        return self._incidents.get(incident_id)

    def get_live_cost(self, incident_id: str) -> dict[str, Any]:
        """Get live cost ticker for an incident."""
        incident = self._incidents.get(incident_id)
        if not incident:
            return {"error": "Incident not found"}

        return {
            "incident_id": incident_id,
            "is_ongoing": incident.is_ongoing,
            "total_cost": round(incident.total_cost, 2),
            "cost_per_minute": round(incident.cost_per_minute, 2),
            "duration_minutes": incident.duration_minutes,
            "breakdown": incident.breakdown.to_dict(),
        }

    def get_period_summary(
        self,
        incident_ids: list[str],
    ) -> dict[str, Any]:
        """Get cost summary for multiple incidents."""
        total = CostBreakdown()
        count = 0

        for iid in incident_ids:
            incident = self._incidents.get(iid)
            if incident:
                total.engineer_time += incident.breakdown.engineer_time
                total.lost_revenue += incident.breakdown.lost_revenue
                total.sla_penalties += incident.breakdown.sla_penalties
                total.customer_support += incident.breakdown.customer_support
                total.reputation_impact += incident.breakdown.reputation_impact
                count += 1

        return {
            "incident_count": count,
            "total_cost": round(total.total, 2),
            "breakdown": total.to_dict(),
            "avg_cost_per_incident": round(total.total / count, 2) if count > 0 else 0,
        }


_calculator: CostCalculator | None = None


def get_cost_calculator() -> CostCalculator:
    """Get the default cost calculator."""
    global _calculator
    if _calculator is None:
        _calculator = CostCalculator()
    return _calculator
