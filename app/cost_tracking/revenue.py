"""Revenue Impact Calculator.

Calculates revenue impact from incidents including lost transactions,
customer churn, and long-term effects.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger("devcopilot.cost_tracking.revenue")


@dataclass
class RevenueImpact:
    """Revenue impact from an incident."""

    incident_id: str
    lost_transactions: int
    failed_transaction_value: float
    recovery_rate: float
    churn_risk_customers: int
    estimated_churn: int
    customer_ltv: float
    immediate_loss: float
    churn_loss: float
    brand_impact: float

    @property
    def total_impact(self) -> float:
        return self.immediate_loss + self.churn_loss + self.brand_impact

    def to_dict(self) -> dict[str, Any]:
        return {
            "incident_id": self.incident_id,
            "lost_transactions": self.lost_transactions,
            "transaction_value": round(self.failed_transaction_value, 2),
            "recovery_rate": round(self.recovery_rate, 2),
            "churn_risk_customers": self.churn_risk_customers,
            "estimated_churn": self.estimated_churn,
            "customer_ltv": round(self.customer_ltv, 2),
            "immediate_loss": round(self.immediate_loss, 2),
            "churn_loss": round(self.churn_loss, 2),
            "brand_impact": round(self.brand_impact, 2),
            "total_impact": round(self.total_impact, 2),
        }


@dataclass
class CustomerChurnEvent:
    """A customer churn event attributed to an incident."""

    customer_id: str
    incident_id: str
    ltv: float
    churn_date: datetime
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "customer_id": self.customer_id,
            "incident_id": self.incident_id,
            "ltv": round(self.ltv, 2),
            "churn_date": self.churn_date.isoformat(),
            "confidence": round(self.confidence, 2),
        }


class RevenueImpactCalculator:
    """Calculates revenue impact from incidents."""

    def __init__(self) -> None:
        self._impacts: dict[str, RevenueImpact] = {}
        self._churn_events: dict[str, list[CustomerChurnEvent]] = {}
        self._config = {
            "default_recovery_rate": 0.2,
            "churn_rate_per_incident": 0.01,
            "avg_customer_ltv": 500.0,
            "brand_impact_multiplier": 0.05,
        }

    def set_config(self, key: str, value: float) -> None:
        """Set configuration value."""
        self._config[key] = value

    def calculate_immediate_loss(
        self,
        incident_id: str,
        lost_transactions: int,
        avg_transaction_value: float,
        recovery_rate: float | None = None,
    ) -> float:
        """Calculate immediate revenue loss from failed transactions."""
        if recovery_rate is None:
            recovery_rate = self._config["default_recovery_rate"]

        gross_loss = lost_transactions * avg_transaction_value
        net_loss = gross_loss * (1 - recovery_rate)

        log.info(
            "Calculated immediate loss: incident=%s transactions=%d loss=%.2f",
            incident_id,
            lost_transactions,
            net_loss,
        )

        return net_loss

    def calculate_churn_impact(
        self,
        incident_id: str,
        affected_customers: int,
        incident_severity: str,
        customer_ltv: float | None = None,
    ) -> tuple[int, float]:
        """Calculate customer churn impact."""
        if customer_ltv is None:
            customer_ltv = self._config["avg_customer_ltv"]

        severity_multipliers = {
            "SEV1": 0.05,
            "SEV2": 0.02,
            "SEV3": 0.005,
            "SEV4": 0.001,
        }

        churn_rate = severity_multipliers.get(
            incident_severity,
            self._config["churn_rate_per_incident"],
        )

        estimated_churn = int(affected_customers * churn_rate)
        churn_cost = estimated_churn * customer_ltv

        log.info(
            "Calculated churn impact: incident=%s affected=%d churn=%d cost=%.2f",
            incident_id,
            affected_customers,
            estimated_churn,
            churn_cost,
        )

        return estimated_churn, churn_cost

    def calculate_brand_impact(
        self,
        incident_id: str,
        social_mentions: int,
        press_coverage: bool,
        duration_hours: float,
    ) -> float:
        """Calculate brand/reputation impact."""
        base_impact = social_mentions * 200

        if press_coverage:
            base_impact *= 5

        duration_factor = min(3.0, 1.0 + (duration_hours / 4))
        total_impact = base_impact * duration_factor

        log.info(
            "Calculated brand impact: incident=%s mentions=%d impact=%.2f",
            incident_id,
            social_mentions,
            total_impact,
        )

        return total_impact

    def calculate_full_impact(
        self,
        incident_id: str,
        lost_transactions: int,
        avg_transaction_value: float,
        affected_customers: int,
        incident_severity: str,
        social_mentions: int = 0,
        press_coverage: bool = False,
        duration_hours: float = 1.0,
        recovery_rate: float | None = None,
        customer_ltv: float | None = None,
    ) -> RevenueImpact:
        """Calculate full revenue impact."""
        if recovery_rate is None:
            recovery_rate = self._config["default_recovery_rate"]
        if customer_ltv is None:
            customer_ltv = self._config["avg_customer_ltv"]

        immediate_loss = self.calculate_immediate_loss(
            incident_id,
            lost_transactions,
            avg_transaction_value,
            recovery_rate,
        )

        estimated_churn, churn_loss = self.calculate_churn_impact(
            incident_id,
            affected_customers,
            incident_severity,
            customer_ltv,
        )

        brand_impact = self.calculate_brand_impact(
            incident_id,
            social_mentions,
            press_coverage,
            duration_hours,
        )

        impact = RevenueImpact(
            incident_id=incident_id,
            lost_transactions=lost_transactions,
            failed_transaction_value=lost_transactions * avg_transaction_value,
            recovery_rate=recovery_rate,
            churn_risk_customers=affected_customers,
            estimated_churn=estimated_churn,
            customer_ltv=customer_ltv,
            immediate_loss=immediate_loss,
            churn_loss=churn_loss,
            brand_impact=brand_impact,
        )

        self._impacts[incident_id] = impact

        return impact

    def attribute_churn(
        self,
        customer_id: str,
        incident_id: str,
        ltv: float,
        confidence: float = 0.8,
    ) -> CustomerChurnEvent:
        """Attribute a customer churn event to an incident."""
        event = CustomerChurnEvent(
            customer_id=customer_id,
            incident_id=incident_id,
            ltv=ltv,
            churn_date=datetime.now(timezone.utc),
            confidence=confidence,
        )

        if incident_id not in self._churn_events:
            self._churn_events[incident_id] = []
        self._churn_events[incident_id].append(event)

        impact = self._impacts.get(incident_id)
        if impact:
            impact.churn_loss += ltv * confidence

        log.info(
            "Attributed churn: customer=%s incident=%s ltv=%.2f",
            customer_id,
            incident_id,
            ltv,
        )

        return event

    def get_impact(self, incident_id: str) -> RevenueImpact | None:
        """Get revenue impact for an incident."""
        return self._impacts.get(incident_id)

    def get_churn_events(self, incident_id: str) -> list[CustomerChurnEvent]:
        """Get churn events for an incident."""
        return self._churn_events.get(incident_id, [])

    def get_period_summary(
        self,
        days: int = 30,
    ) -> dict[str, Any]:
        """Get revenue impact summary for a period."""
        total_immediate = 0.0
        total_churn = 0.0
        total_brand = 0.0
        incident_count = 0

        for impact in self._impacts.values():
            total_immediate += impact.immediate_loss
            total_churn += impact.churn_loss
            total_brand += impact.brand_impact
            incident_count += 1

        return {
            "period_days": days,
            "incident_count": incident_count,
            "total_immediate_loss": round(total_immediate, 2),
            "total_churn_loss": round(total_churn, 2),
            "total_brand_impact": round(total_brand, 2),
            "total_revenue_impact": round(total_immediate + total_churn + total_brand, 2),
        }


_calculator: RevenueImpactCalculator | None = None


def get_revenue_calculator() -> RevenueImpactCalculator:
    """Get the default revenue impact calculator."""
    global _calculator
    if _calculator is None:
        _calculator = RevenueImpactCalculator()
    return _calculator
