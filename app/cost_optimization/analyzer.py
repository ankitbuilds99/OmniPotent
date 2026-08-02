"""Cost Optimization - Analyze and optimize cloud costs."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from enum import Enum

from app.config import get_settings

log = logging.getLogger("omnipotent.cost_optimization.analyzer")


class ResourceCategory(str, Enum):
    """Resource categories for cost tracking."""
    COMPUTE = "compute"
    STORAGE = "storage"
    DATABASE = "database"
    NETWORK = "network"
    SERVERLESS = "serverless"
    CONTAINERS = "containers"
    OTHER = "other"


class OptimizationType(str, Enum):
    """Types of cost optimizations."""
    RIGHTSIZING = "rightsizing"
    RESERVED_INSTANCES = "reserved_instances"
    SPOT_INSTANCES = "spot_instances"
    IDLE_RESOURCES = "idle_resources"
    STORAGE_TIER = "storage_tier"
    AUTO_SCALING = "auto_scaling"
    UNUSED_LICENSES = "unused_licenses"


@dataclass
class CostMetric:
    """Cost metric for a resource or service."""
    resource_id: str
    resource_name: str
    category: ResourceCategory
    provider: str
    region: str
    current_cost_daily: float
    current_cost_monthly: float
    trend_percent: float  # +/- percent change
    utilization_percent: float
    tags: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "resource_id": self.resource_id,
            "resource_name": self.resource_name,
            "category": self.category.value,
            "provider": self.provider,
            "region": self.region,
            "current_cost_daily": self.current_cost_daily,
            "current_cost_monthly": self.current_cost_monthly,
            "trend_percent": self.trend_percent,
            "utilization_percent": self.utilization_percent,
            "tags": self.tags,
        }


@dataclass
class Recommendation:
    """Cost optimization recommendation."""
    id: str
    optimization_type: OptimizationType
    resource_id: str
    resource_name: str
    provider: str
    description: str
    current_cost_monthly: float
    estimated_savings_monthly: float
    savings_percent: float
    effort: str  # low, medium, high
    risk: str  # low, medium, high
    implementation_steps: list[str] = field(default_factory=list)
    auto_applicable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "optimization_type": self.optimization_type.value,
            "resource_id": self.resource_id,
            "resource_name": self.resource_name,
            "provider": self.provider,
            "description": self.description,
            "current_cost_monthly": self.current_cost_monthly,
            "estimated_savings_monthly": self.estimated_savings_monthly,
            "savings_percent": self.savings_percent,
            "effort": self.effort,
            "risk": self.risk,
            "implementation_steps": self.implementation_steps,
            "auto_applicable": self.auto_applicable,
        }


@dataclass
class CostReport:
    """Cost analysis report."""
    report_id: str
    period_start: datetime
    period_end: datetime
    total_cost: float
    cost_by_category: dict[str, float]
    cost_by_provider: dict[str, float]
    cost_by_team: dict[str, float]
    top_resources: list[CostMetric]
    recommendations: list[Recommendation]
    potential_savings: float
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "total_cost": self.total_cost,
            "cost_by_category": self.cost_by_category,
            "cost_by_provider": self.cost_by_provider,
            "cost_by_team": self.cost_by_team,
            "top_resources": [r.to_dict() for r in self.top_resources],
            "recommendations": [r.to_dict() for r in self.recommendations],
            "potential_savings": self.potential_savings,
            "generated_at": self.generated_at.isoformat(),
        }


class CostOptimizer:
    """Analyzes cloud costs and generates optimization recommendations.

    Features:
    - Multi-cloud cost tracking
    - Rightsizing recommendations
    - Reserved instance analysis
    - Idle resource detection
    - Cost anomaly detection
    - Budget alerts
    """

    def __init__(self):
        """Initialize the cost optimizer."""
        settings = get_settings()
        self._metrics: list[CostMetric] = []
        self._budgets: dict[str, float] = {}
        # Load thresholds from config
        self.underutilized_threshold = settings.cost_underutilized_threshold
        self.idle_threshold = settings.cost_idle_threshold
        self.anomaly_threshold = settings.cost_anomaly_threshold
        self.budget_alert_threshold = settings.cost_budget_alert_threshold
        self.default_region = settings.aws_region
        log.info("CostOptimizer initialized")

    async def analyze_costs(
        self,
        start_date: datetime,
        end_date: datetime,
    ) -> CostReport:
        """Analyze costs for the given period.

        Args:
            start_date: Start of analysis period
            end_date: End of analysis period

        Returns:
            CostReport with analysis
        """
        log.info("Analyzing costs from %s to %s", start_date, end_date)

        # Collect cost data
        metrics = await self._collect_cost_data(start_date, end_date)

        # Aggregate by category
        cost_by_category: dict[str, float] = {}
        for m in metrics:
            cat = m.category.value
            cost_by_category[cat] = cost_by_category.get(cat, 0) + m.current_cost_monthly

        # Aggregate by provider
        cost_by_provider: dict[str, float] = {}
        for m in metrics:
            cost_by_provider[m.provider] = cost_by_provider.get(m.provider, 0) + m.current_cost_monthly

        # Aggregate by team (from tags)
        cost_by_team: dict[str, float] = {}
        for m in metrics:
            team = m.tags.get("team", "untagged")
            cost_by_team[team] = cost_by_team.get(team, 0) + m.current_cost_monthly

        # Generate recommendations
        recommendations = await self._generate_recommendations(metrics)

        # Calculate total potential savings
        potential_savings = sum(r.estimated_savings_monthly for r in recommendations)

        # Top 10 resources by cost
        top_resources = sorted(metrics, key=lambda m: m.current_cost_monthly, reverse=True)[:10]

        return CostReport(
            report_id=f"cost-report-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
            period_start=start_date,
            period_end=end_date,
            total_cost=sum(m.current_cost_monthly for m in metrics),
            cost_by_category=cost_by_category,
            cost_by_provider=cost_by_provider,
            cost_by_team=cost_by_team,
            top_resources=top_resources,
            recommendations=recommendations,
            potential_savings=potential_savings,
        )

    async def _collect_cost_data(
        self,
        start_date: datetime,
        end_date: datetime,
    ) -> list[CostMetric]:
        """Collect cost data from cloud providers."""
        # In production: query AWS Cost Explorer, GCP Billing, Azure Cost Management
        metrics = [
            CostMetric(
                resource_id="i-1234567890",
                resource_name="web-server-prod-1",
                category=ResourceCategory.COMPUTE,
                provider="aws",
                region=self.default_region,
                current_cost_daily=2.50,
                current_cost_monthly=75.0,
                trend_percent=5.2,
                utilization_percent=15.0,  # Underutilized
                tags={"team": "platform", "env": "production"},
            ),
            CostMetric(
                resource_id="i-0987654321",
                resource_name="web-server-prod-2",
                category=ResourceCategory.COMPUTE,
                provider="aws",
                region=self.default_region,
                current_cost_daily=2.50,
                current_cost_monthly=75.0,
                trend_percent=3.1,
                utilization_percent=65.0,
                tags={"team": "platform", "env": "production"},
            ),
            CostMetric(
                resource_id="rds-postgres-1",
                resource_name="postgres-primary",
                category=ResourceCategory.DATABASE,
                provider="aws",
                region=self.default_region,
                current_cost_daily=15.0,
                current_cost_monthly=450.0,
                trend_percent=0.0,
                utilization_percent=45.0,
                tags={"team": "data", "env": "production"},
            ),
            CostMetric(
                resource_id="s3-logs-bucket",
                resource_name="logs-archive",
                category=ResourceCategory.STORAGE,
                provider="aws",
                region=self.default_region,
                current_cost_daily=5.0,
                current_cost_monthly=150.0,
                trend_percent=15.0,
                utilization_percent=100.0,
                tags={"team": "ops", "env": "production"},
            ),
            CostMetric(
                resource_id="gce-instance-1",
                resource_name="api-server-gcp",
                category=ResourceCategory.COMPUTE,
                provider="gcp",
                region="us-central1",
                current_cost_daily=3.0,
                current_cost_monthly=90.0,
                trend_percent=-2.0,
                utilization_percent=3.0,  # Idle!
                tags={"team": "api", "env": "staging"},
            ),
        ]

        self._metrics = metrics
        return metrics

    async def _generate_recommendations(
        self,
        metrics: list[CostMetric],
    ) -> list[Recommendation]:
        """Generate cost optimization recommendations."""
        recommendations: list[Recommendation] = []

        for metric in metrics:
            # Check for underutilized compute
            if metric.category == ResourceCategory.COMPUTE:
                if metric.utilization_percent <= self.idle_threshold:
                    recommendations.append(Recommendation(
                        id=f"rec-idle-{metric.resource_id}",
                        optimization_type=OptimizationType.IDLE_RESOURCES,
                        resource_id=metric.resource_id,
                        resource_name=metric.resource_name,
                        provider=metric.provider,
                        description=f"Resource {metric.resource_name} is idle ({metric.utilization_percent}% utilization)",
                        current_cost_monthly=metric.current_cost_monthly,
                        estimated_savings_monthly=metric.current_cost_monthly,
                        savings_percent=100.0,
                        effort="low",
                        risk="medium",
                        implementation_steps=[
                            f"Verify {metric.resource_name} is not needed",
                            "Backup any important data",
                            "Terminate the instance",
                        ],
                        auto_applicable=False,
                    ))

                elif metric.utilization_percent <= self.underutilized_threshold:
                    savings = metric.current_cost_monthly * 0.5
                    recommendations.append(Recommendation(
                        id=f"rec-rightsize-{metric.resource_id}",
                        optimization_type=OptimizationType.RIGHTSIZING,
                        resource_id=metric.resource_id,
                        resource_name=metric.resource_name,
                        provider=metric.provider,
                        description=f"Downsize {metric.resource_name} - only {metric.utilization_percent}% utilized",
                        current_cost_monthly=metric.current_cost_monthly,
                        estimated_savings_monthly=savings,
                        savings_percent=50.0,
                        effort="medium",
                        risk="low",
                        implementation_steps=[
                            "Review current resource specifications",
                            "Choose smaller instance type",
                            "Schedule maintenance window",
                            "Resize instance",
                            "Monitor performance",
                        ],
                        auto_applicable=True,
                    ))

            # Check for storage optimization
            if metric.category == ResourceCategory.STORAGE:
                if metric.trend_percent > 10:
                    savings = metric.current_cost_monthly * 0.3
                    recommendations.append(Recommendation(
                        id=f"rec-storage-{metric.resource_id}",
                        optimization_type=OptimizationType.STORAGE_TIER,
                        resource_id=metric.resource_id,
                        resource_name=metric.resource_name,
                        provider=metric.provider,
                        description=f"Move {metric.resource_name} to cheaper storage tier",
                        current_cost_monthly=metric.current_cost_monthly,
                        estimated_savings_monthly=savings,
                        savings_percent=30.0,
                        effort="low",
                        risk="low",
                        implementation_steps=[
                            "Analyze access patterns",
                            "Set up lifecycle policies",
                            "Move infrequently accessed data to cold storage",
                        ],
                        auto_applicable=True,
                    ))

        # Check for reserved instance opportunities
        compute_costs = [m for m in metrics if m.category == ResourceCategory.COMPUTE and m.utilization_percent > 50]
        if compute_costs:
            total_compute = sum(m.current_cost_monthly for m in compute_costs)
            savings = total_compute * 0.4  # ~40% savings with reserved
            recommendations.append(Recommendation(
                id="rec-reserved-instances",
                optimization_type=OptimizationType.RESERVED_INSTANCES,
                resource_id="multiple",
                resource_name="High-utilization compute instances",
                provider="aws",
                description=f"Purchase reserved instances for {len(compute_costs)} consistently used servers",
                current_cost_monthly=total_compute,
                estimated_savings_monthly=savings,
                savings_percent=40.0,
                effort="high",
                risk="medium",
                implementation_steps=[
                    "Analyze 12-month usage patterns",
                    "Calculate break-even point",
                    "Choose commitment term (1 or 3 years)",
                    "Purchase reserved capacity",
                ],
                auto_applicable=False,
            ))

        # Sort by savings potential
        recommendations.sort(key=lambda r: r.estimated_savings_monthly, reverse=True)

        return recommendations

    def set_budget(self, team: str, monthly_budget: float) -> None:
        """Set a monthly budget for a team.

        Args:
            team: Team name
            monthly_budget: Budget in dollars
        """
        self._budgets[team] = monthly_budget
        log.info("Budget set for %s: $%.2f/month", team, monthly_budget)

    async def check_budget_alerts(self) -> list[dict[str, Any]]:
        """Check for budget threshold violations.

        Returns:
            List of budget alerts
        """
        alerts = []

        # Get current spend by team
        cost_by_team: dict[str, float] = {}
        for m in self._metrics:
            team = m.tags.get("team", "untagged")
            cost_by_team[team] = cost_by_team.get(team, 0) + m.current_cost_monthly

        for team, budget in self._budgets.items():
            current = cost_by_team.get(team, 0)
            percent = (current / budget * 100) if budget > 0 else 0

            if percent >= 100:
                alerts.append({
                    "team": team,
                    "severity": "critical",
                    "message": f"Budget exceeded: ${current:.2f} / ${budget:.2f} ({percent:.0f}%)",
                    "current_spend": current,
                    "budget": budget,
                    "percent": percent,
                })
            elif percent >= 80:
                alerts.append({
                    "team": team,
                    "severity": "warning",
                    "message": f"Budget threshold reached: ${current:.2f} / ${budget:.2f} ({percent:.0f}%)",
                    "current_spend": current,
                    "budget": budget,
                    "percent": percent,
                })

        return alerts

    async def detect_anomalies(self) -> list[dict[str, Any]]:
        """Detect cost anomalies.

        Returns:
            List of anomalies
        """
        anomalies = []

        for metric in self._metrics:
            # Check for sudden cost increases
            if metric.trend_percent >= (self.anomaly_threshold - 1) * 100:
                anomalies.append({
                    "resource_id": metric.resource_id,
                    "resource_name": metric.resource_name,
                    "provider": metric.provider,
                    "anomaly_type": "cost_spike",
                    "current_cost": metric.current_cost_monthly,
                    "trend_percent": metric.trend_percent,
                    "message": f"Cost increased by {metric.trend_percent:.0f}%",
                })

        return anomalies


def create_cost_optimizer() -> CostOptimizer:
    """Factory function to create a cost optimizer."""
    return CostOptimizer()
