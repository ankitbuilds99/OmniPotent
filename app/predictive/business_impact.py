"""Business impact forecasting for predicted incidents.

Calculates revenue impact, affected customers, and SLA consequences.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any

from app.predictive.predictions import PredictedIncident, IncidentType

log = logging.getLogger("omnipotent.predictive.business_impact")


class ImpactCategory(Enum):
    """Categories of business impact."""

    REVENUE = "revenue"
    CUSTOMERS = "customers"
    SLA = "sla"
    REPUTATION = "reputation"
    COMPLIANCE = "compliance"
    OPERATIONAL = "operational"


class ImpactSeverity(Enum):
    """Severity of business impact."""

    MINIMAL = "minimal"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class RevenueImpact:
    """Estimated revenue impact."""

    estimated_revenue_at_risk: Decimal
    revenue_per_minute: Decimal
    affected_transaction_types: list[str]
    estimated_failed_transactions: int
    currency: str = "USD"

    def to_dict(self) -> dict[str, Any]:
        return {
            "estimated_revenue_at_risk": str(self.estimated_revenue_at_risk),
            "revenue_per_minute": str(self.revenue_per_minute),
            "affected_transaction_types": self.affected_transaction_types,
            "estimated_failed_transactions": self.estimated_failed_transactions,
            "currency": self.currency,
        }


@dataclass
class CustomerImpact:
    """Estimated customer impact."""

    estimated_affected_customers: int
    customer_segments: list[str]
    geographic_regions: list[str]
    estimated_support_tickets: int
    churn_risk_score: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "estimated_affected_customers": self.estimated_affected_customers,
            "customer_segments": self.customer_segments,
            "geographic_regions": self.geographic_regions,
            "estimated_support_tickets": self.estimated_support_tickets,
            "churn_risk_score": self.churn_risk_score,
        }


@dataclass
class SLAImpact:
    """Estimated SLA impact."""

    sla_targets_at_risk: list[str]
    current_budget_remaining_minutes: Decimal
    budget_usage_if_incident: Decimal
    would_breach: bool
    credit_exposure: Decimal

    def to_dict(self) -> dict[str, Any]:
        return {
            "sla_targets_at_risk": self.sla_targets_at_risk,
            "current_budget_remaining_minutes": str(
                self.current_budget_remaining_minutes
            ),
            "budget_usage_if_incident": str(self.budget_usage_if_incident),
            "would_breach": self.would_breach,
            "credit_exposure": str(self.credit_exposure),
        }


@dataclass
class CascadeImpact:
    """Impact on downstream services."""

    downstream_services: list[str]
    cascade_lag_seconds: dict[str, int]
    total_blast_radius_services: int
    affected_api_endpoints: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "downstream_services": self.downstream_services,
            "cascade_lag_seconds": self.cascade_lag_seconds,
            "total_blast_radius_services": self.total_blast_radius_services,
            "affected_api_endpoints": self.affected_api_endpoints,
        }


@dataclass
class BusinessImpact:
    """Full business impact assessment for a predicted incident."""

    impact_id: str
    prediction_id: str
    overall_severity: ImpactSeverity
    impact_score: float
    estimated_downtime_minutes: int
    revenue_impact: RevenueImpact
    customer_impact: CustomerImpact
    sla_impact: SLAImpact
    cascade_impact: CascadeImpact
    prevention_value: Decimal
    assessed_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "impact_id": self.impact_id,
            "prediction_id": self.prediction_id,
            "overall_severity": self.overall_severity.value,
            "impact_score": round(self.impact_score, 2),
            "estimated_downtime_minutes": self.estimated_downtime_minutes,
            "revenue_impact": self.revenue_impact.to_dict(),
            "customer_impact": self.customer_impact.to_dict(),
            "sla_impact": self.sla_impact.to_dict(),
            "cascade_impact": self.cascade_impact.to_dict(),
            "prevention_value": str(self.prevention_value),
            "assessed_at": self.assessed_at.isoformat(),
        }


class ImpactForecaster:
    """Forecasts business impact of predicted incidents."""

    def __init__(self) -> None:
        self._impacts: dict[str, BusinessImpact] = {}
        self._impact_counter = 0
        self._service_metrics = self._setup_service_metrics()
        self._service_dependencies = self._setup_dependencies()

    def _setup_service_metrics(self) -> dict[str, dict[str, Any]]:
        """Set up baseline metrics for services."""
        return {
            "payment-service": {
                "requests_per_minute": 1200,
                "revenue_per_request": Decimal("45.00"),
                "customers_per_minute": 800,
                "sla_targets": ["api_monthly", "payments_monthly"],
            },
            "checkout-api": {
                "requests_per_minute": 2400,
                "revenue_per_request": Decimal("35.00"),
                "customers_per_minute": 1500,
                "sla_targets": ["api_monthly"],
            },
            "user-service": {
                "requests_per_minute": 5000,
                "revenue_per_request": Decimal("0.00"),
                "customers_per_minute": 3000,
                "sla_targets": ["api_monthly"],
            },
            "billing-db": {
                "requests_per_minute": 800,
                "revenue_per_request": Decimal("0.00"),
                "customers_per_minute": 0,
                "sla_targets": [],
            },
            "notification-service": {
                "requests_per_minute": 1000,
                "revenue_per_request": Decimal("0.00"),
                "customers_per_minute": 500,
                "sla_targets": [],
            },
        }

    def _setup_dependencies(self) -> dict[str, list[tuple[str, int]]]:
        """Set up service dependencies with cascade lag in seconds."""
        return {
            "payment-service": [
                ("checkout-api", 30),
                ("billing-db", 15),
                ("notification-service", 120),
            ],
            "checkout-api": [
                ("user-service", 45),
                ("notification-service", 90),
            ],
            "user-service": [
                ("notification-service", 60),
            ],
            "billing-db": [
                ("payment-service", 10),
                ("checkout-api", 20),
            ],
        }

    def forecast_impact(
        self,
        prediction: PredictedIncident,
        historical_mttr_minutes: int = 45,
    ) -> BusinessImpact:
        """Forecast the business impact of a predicted incident."""
        self._impact_counter += 1
        impact_id = f"impact_{self._impact_counter}"

        estimated_downtime = self._estimate_downtime(
            prediction,
            historical_mttr_minutes,
        )

        revenue = self._calculate_revenue_impact(prediction, estimated_downtime)
        customers = self._calculate_customer_impact(prediction, estimated_downtime)
        sla = self._calculate_sla_impact(prediction, estimated_downtime)
        cascade = self._calculate_cascade_impact(prediction)
        severity = self._calculate_overall_severity(revenue, customers, sla)
        impact_score = self._calculate_impact_score(
            revenue, customers, sla, severity
        )

        prevention_value = revenue.estimated_revenue_at_risk + sla.credit_exposure

        impact = BusinessImpact(
            impact_id=impact_id,
            prediction_id=prediction.prediction_id,
            overall_severity=severity,
            impact_score=impact_score,
            estimated_downtime_minutes=estimated_downtime,
            revenue_impact=revenue,
            customer_impact=customers,
            sla_impact=sla,
            cascade_impact=cascade,
            prevention_value=prevention_value,
            assessed_at=datetime.now(timezone.utc),
        )

        self._impacts[impact_id] = impact

        log.info(
            "business impact forecast",
            extra={
                "impact_id": impact_id,
                "prediction_id": prediction.prediction_id,
                "severity": severity.value,
                "prevention_value": str(prevention_value),
            },
        )

        return impact

    def _estimate_downtime(
        self,
        prediction: PredictedIncident,
        historical_mttr: int,
    ) -> int:
        """Estimate downtime in minutes based on incident type."""
        type_multipliers = {
            IncidentType.SERVICE_OUTAGE: 1.2,
            IncidentType.CASCADE_FAILURE: 1.5,
            IncidentType.RESOURCE_EXHAUSTION: 0.8,
            IncidentType.PERFORMANCE_DEGRADATION: 0.5,
            IncidentType.DATA_ISSUE: 2.0,
            IncidentType.NETWORK_ISSUE: 0.7,
        }

        multiplier = type_multipliers.get(prediction.incident_type, 1.0)
        service_factor = 1.0 + (len(prediction.affected_services) * 0.1)

        return int(historical_mttr * multiplier * service_factor)

    def _calculate_revenue_impact(
        self,
        prediction: PredictedIncident,
        downtime_minutes: int,
    ) -> RevenueImpact:
        """Calculate revenue impact."""
        total_revenue_per_minute = Decimal("0")
        total_requests_per_minute = 0
        transaction_types = []

        for service in prediction.affected_services:
            metrics = self._service_metrics.get(service, {})
            rpm = metrics.get("revenue_per_request", Decimal("0"))
            req_pm = metrics.get("requests_per_minute", 0)

            service_revenue = rpm * Decimal(str(req_pm))
            total_revenue_per_minute += service_revenue
            total_requests_per_minute += req_pm

            if rpm > 0:
                transaction_types.append(service.replace("-", " ").title())

        estimated_revenue_at_risk = total_revenue_per_minute * Decimal(
            str(downtime_minutes)
        )

        return RevenueImpact(
            estimated_revenue_at_risk=estimated_revenue_at_risk,
            revenue_per_minute=total_revenue_per_minute,
            affected_transaction_types=transaction_types,
            estimated_failed_transactions=total_requests_per_minute * downtime_minutes,
        )

    def _calculate_customer_impact(
        self,
        prediction: PredictedIncident,
        downtime_minutes: int,
    ) -> CustomerImpact:
        """Calculate customer impact."""
        total_customers_per_minute = 0

        for service in prediction.affected_services:
            metrics = self._service_metrics.get(service, {})
            total_customers_per_minute += metrics.get("customers_per_minute", 0)

        affected_customers = total_customers_per_minute * downtime_minutes
        affected_customers = min(affected_customers, 50000)

        support_ticket_rate = 0.02
        estimated_tickets = int(affected_customers * support_ticket_rate)

        churn_risk = min(1.0, downtime_minutes / 120)

        return CustomerImpact(
            estimated_affected_customers=affected_customers,
            customer_segments=["enterprise", "pro", "free"],
            geographic_regions=["us-east", "eu-west", "ap-south"],
            estimated_support_tickets=estimated_tickets,
            churn_risk_score=round(churn_risk, 2),
        )

    def _calculate_sla_impact(
        self,
        prediction: PredictedIncident,
        downtime_minutes: int,
    ) -> SLAImpact:
        """Calculate SLA impact."""
        sla_targets = set()
        for service in prediction.affected_services:
            metrics = self._service_metrics.get(service, {})
            sla_targets.update(metrics.get("sla_targets", []))

        monthly_budget = Decimal("43.2")
        current_usage = Decimal("28.5")
        remaining = monthly_budget - current_usage

        usage_if_incident = current_usage + Decimal(str(downtime_minutes))
        would_breach = usage_if_incident > monthly_budget

        credit_tiers = [
            (Decimal("99.9"), Decimal("0")),
            (Decimal("99.0"), Decimal("10")),
            (Decimal("95.0"), Decimal("25")),
            (Decimal("0"), Decimal("50")),
        ]

        monthly_minutes = Decimal("43200")
        uptime_after = (
            (monthly_minutes - usage_if_incident) / monthly_minutes * Decimal("100")
        )

        credit_exposure = Decimal("0")
        for threshold, credit_pct in credit_tiers:
            if uptime_after < threshold:
                credit_exposure = credit_pct
                break

        return SLAImpact(
            sla_targets_at_risk=list(sla_targets),
            current_budget_remaining_minutes=remaining,
            budget_usage_if_incident=Decimal(str(downtime_minutes)),
            would_breach=would_breach,
            credit_exposure=credit_exposure,
        )

    def _calculate_cascade_impact(
        self,
        prediction: PredictedIncident,
    ) -> CascadeImpact:
        """Calculate cascade/blast radius impact."""
        downstream = set()
        lag_seconds: dict[str, int] = {}

        for service in prediction.affected_services:
            deps = self._service_dependencies.get(service, [])
            for dep_service, lag in deps:
                downstream.add(dep_service)
                existing_lag = lag_seconds.get(dep_service, 999999)
                lag_seconds[dep_service] = min(existing_lag, lag)

        all_affected = set(prediction.affected_services) | downstream
        endpoints_per_service = 5

        return CascadeImpact(
            downstream_services=sorted(downstream),
            cascade_lag_seconds=lag_seconds,
            total_blast_radius_services=len(all_affected),
            affected_api_endpoints=len(all_affected) * endpoints_per_service,
        )

    def _calculate_overall_severity(
        self,
        revenue: RevenueImpact,
        customers: CustomerImpact,
        sla: SLAImpact,
    ) -> ImpactSeverity:
        """Calculate overall impact severity."""
        revenue_threshold = Decimal("100000")
        customer_threshold = 10000

        if (
            revenue.estimated_revenue_at_risk > revenue_threshold
            or sla.would_breach
            or customers.estimated_affected_customers > customer_threshold * 2
        ):
            return ImpactSeverity.CRITICAL

        if (
            revenue.estimated_revenue_at_risk > revenue_threshold / 2
            or customers.estimated_affected_customers > customer_threshold
        ):
            return ImpactSeverity.HIGH

        if (
            revenue.estimated_revenue_at_risk > revenue_threshold / 10
            or customers.estimated_affected_customers > customer_threshold / 2
        ):
            return ImpactSeverity.MEDIUM

        if revenue.estimated_revenue_at_risk > 0:
            return ImpactSeverity.LOW

        return ImpactSeverity.MINIMAL

    def _calculate_impact_score(
        self,
        revenue: RevenueImpact,
        customers: CustomerImpact,
        sla: SLAImpact,
        severity: ImpactSeverity,
    ) -> float:
        """Calculate normalized impact score 0-100."""
        severity_base = {
            ImpactSeverity.MINIMAL: 10,
            ImpactSeverity.LOW: 30,
            ImpactSeverity.MEDIUM: 50,
            ImpactSeverity.HIGH: 75,
            ImpactSeverity.CRITICAL: 95,
        }.get(severity, 50)

        revenue_factor = min(1.0, float(revenue.estimated_revenue_at_risk) / 100000)
        customer_factor = min(1.0, customers.estimated_affected_customers / 10000)
        sla_factor = 1.0 if sla.would_breach else 0.5

        score = severity_base + (
            revenue_factor * 5 + customer_factor * 5 + sla_factor * 5
        )

        return min(100.0, score)

    def get_impact(self, impact_id: str) -> BusinessImpact | None:
        """Get an impact assessment by ID."""
        return self._impacts.get(impact_id)

    def get_impact_for_prediction(
        self,
        prediction_id: str,
    ) -> BusinessImpact | None:
        """Get impact assessment for a prediction."""
        for impact in self._impacts.values():
            if impact.prediction_id == prediction_id:
                return impact
        return None

    def get_high_impact_predictions(
        self,
        min_severity: ImpactSeverity = ImpactSeverity.HIGH,
    ) -> list[BusinessImpact]:
        """Get predictions with high business impact."""
        severity_order = [
            ImpactSeverity.MINIMAL,
            ImpactSeverity.LOW,
            ImpactSeverity.MEDIUM,
            ImpactSeverity.HIGH,
            ImpactSeverity.CRITICAL,
        ]
        min_index = severity_order.index(min_severity)

        return [
            i
            for i in self._impacts.values()
            if severity_order.index(i.overall_severity) >= min_index
        ]


_forecaster: ImpactForecaster | None = None


def get_impact_forecaster() -> ImpactForecaster:
    """Get the default impact forecaster."""
    global _forecaster
    if _forecaster is None:
        _forecaster = ImpactForecaster()
    return _forecaster
