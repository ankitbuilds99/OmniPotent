"""Budget Manager.

Manages AI and cloud cost budgets:
- Multi-level budget hierarchies
- Real-time tracking
- Alerts and notifications
- Forecasting
- Cost allocation
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("omnipotent.cost.budget_manager")


class BudgetPeriod(str, Enum):
    """Budget time periods."""
    HOURLY = "hourly"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    YEARLY = "yearly"


class BudgetType(str, Enum):
    """Types of budgets."""
    AI_TOKENS = "ai_tokens"
    AI_COST = "ai_cost"
    CLOUD_COMPUTE = "cloud_compute"
    CLOUD_STORAGE = "cloud_storage"
    CLOUD_NETWORK = "cloud_network"
    TOTAL = "total"


class AlertSeverity(str, Enum):
    """Alert severity levels."""
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class BudgetThreshold:
    """A budget alert threshold."""
    percentage: float  # 0-100
    severity: AlertSeverity
    notify_channels: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "percentage": self.percentage,
            "severity": self.severity.value,
            "notify_channels": self.notify_channels,
        }


@dataclass
class Budget:
    """A budget definition."""
    id: str
    name: str
    budget_type: BudgetType
    period: BudgetPeriod
    limit: float  # USD or tokens
    thresholds: list[BudgetThreshold]

    # Scope
    tenant_id: str = "default"
    team: str | None = None
    service: str | None = None
    environment: str | None = None

    # Tracking
    current_usage: float = 0.0
    period_start: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_reset: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # Settings
    rollover: bool = False
    rollover_percentage: float = 0.0
    auto_scale: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "budget_type": self.budget_type.value,
            "period": self.period.value,
            "limit": self.limit,
            "thresholds": [t.to_dict() for t in self.thresholds],
            "tenant_id": self.tenant_id,
            "team": self.team,
            "service": self.service,
            "current_usage": self.current_usage,
            "usage_percent": self.usage_percent,
            "remaining": self.remaining,
        }

    @property
    def usage_percent(self) -> float:
        if self.limit == 0:
            return 0.0
        return (self.current_usage / self.limit) * 100

    @property
    def remaining(self) -> float:
        return max(0, self.limit - self.current_usage)

    @property
    def is_exceeded(self) -> bool:
        return self.current_usage >= self.limit


@dataclass
class BudgetAlert:
    """An alert triggered by budget threshold."""
    id: str
    budget_id: str
    threshold: BudgetThreshold
    triggered_at: datetime
    usage_at_trigger: float
    usage_percent: float
    message: str
    acknowledged: bool = False
    acknowledged_by: str | None = None
    acknowledged_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "budget_id": self.budget_id,
            "severity": self.threshold.severity.value,
            "triggered_at": self.triggered_at.isoformat(),
            "usage_at_trigger": self.usage_at_trigger,
            "usage_percent": self.usage_percent,
            "message": self.message,
            "acknowledged": self.acknowledged,
        }


@dataclass
class CostAllocation:
    """Cost allocation record."""
    timestamp: datetime
    amount: float
    budget_type: BudgetType
    tenant_id: str
    team: str | None
    service: str | None
    description: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "amount": self.amount,
            "budget_type": self.budget_type.value,
            "tenant_id": self.tenant_id,
            "team": self.team,
            "service": self.service,
            "description": self.description,
        }


@dataclass
class BudgetForecast:
    """Budget usage forecast."""
    budget_id: str
    forecasted_at: datetime
    forecast_period: BudgetPeriod
    predicted_usage: float
    predicted_exceed: bool
    exceed_date: datetime | None
    confidence: float
    trend: str  # "increasing", "stable", "decreasing"
    recommendations: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "budget_id": self.budget_id,
            "forecasted_at": self.forecasted_at.isoformat(),
            "forecast_period": self.forecast_period.value,
            "predicted_usage": self.predicted_usage,
            "predicted_exceed": self.predicted_exceed,
            "exceed_date": self.exceed_date.isoformat() if self.exceed_date else None,
            "confidence": self.confidence,
            "trend": self.trend,
            "recommendations": self.recommendations,
        }


class BudgetManager:
    """Manages budgets and cost allocation.

    Features:
    - Hierarchical budgets (org -> team -> service)
    - Real-time tracking
    - Threshold alerts
    - Usage forecasting
    - Cost allocation
    """

    def __init__(
        self,
        notification_service: Any = None,
        redis_client: Any = None,
    ):
        self.notifications = notification_service
        self.redis = redis_client

        self._budgets: dict[str, Budget] = {}
        self._alerts: list[BudgetAlert] = []
        self._allocations: list[CostAllocation] = []
        self._alert_counter = 0

        # Track which thresholds have been triggered
        self._triggered_thresholds: dict[str, set[float]] = {}

    def create_budget(
        self,
        name: str,
        budget_type: BudgetType,
        period: BudgetPeriod,
        limit: float,
        thresholds: list[dict[str, Any]] | None = None,
        tenant_id: str = "default",
        team: str | None = None,
        service: str | None = None,
    ) -> Budget:
        """Create a new budget.

        Args:
            name: Budget name
            budget_type: Type of budget
            period: Budget period
            limit: Budget limit (USD or tokens)
            thresholds: Alert thresholds
            tenant_id: Tenant identifier
            team: Team scope
            service: Service scope

        Returns:
            Created Budget
        """
        import uuid
        budget_id = str(uuid.uuid4())[:8]

        # Default thresholds
        default_thresholds = [
            BudgetThreshold(percentage=50, severity=AlertSeverity.INFO),
            BudgetThreshold(percentage=80, severity=AlertSeverity.WARNING),
            BudgetThreshold(percentage=100, severity=AlertSeverity.CRITICAL),
        ]

        if thresholds:
            parsed_thresholds = [
                BudgetThreshold(
                    percentage=t["percentage"],
                    severity=AlertSeverity(t.get("severity", "warning")),
                    notify_channels=t.get("notify_channels", []),
                )
                for t in thresholds
            ]
        else:
            parsed_thresholds = default_thresholds

        budget = Budget(
            id=budget_id,
            name=name,
            budget_type=budget_type,
            period=period,
            limit=limit,
            thresholds=parsed_thresholds,
            tenant_id=tenant_id,
            team=team,
            service=service,
        )

        self._budgets[budget_id] = budget
        self._triggered_thresholds[budget_id] = set()

        log.info(
            "Created budget: %s (type=%s, limit=%.2f, period=%s)",
            name, budget_type.value, limit, period.value
        )

        return budget

    def record_usage(
        self,
        amount: float,
        budget_type: BudgetType,
        tenant_id: str = "default",
        team: str | None = None,
        service: str | None = None,
        description: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> list[BudgetAlert]:
        """Record usage against budgets.

        Args:
            amount: Amount to record (USD or tokens)
            budget_type: Type of budget
            tenant_id: Tenant identifier
            team: Team
            service: Service
            description: Usage description
            metadata: Additional metadata

        Returns:
            List of triggered alerts
        """
        now = datetime.now(timezone.utc)

        # Record allocation
        allocation = CostAllocation(
            timestamp=now,
            amount=amount,
            budget_type=budget_type,
            tenant_id=tenant_id,
            team=team,
            service=service,
            description=description,
            metadata=metadata or {},
        )
        self._allocations.append(allocation)

        # Find matching budgets
        alerts = []
        for budget in self._budgets.values():
            if not self._budget_matches(budget, budget_type, tenant_id, team, service):
                continue

            # Check if period needs reset
            self._check_period_reset(budget, now)

            # Update usage
            budget.current_usage += amount

            # Check thresholds
            budget_alerts = self._check_thresholds(budget)
            alerts.extend(budget_alerts)

        return alerts

    def _budget_matches(
        self,
        budget: Budget,
        budget_type: BudgetType,
        tenant_id: str,
        team: str | None,
        service: str | None,
    ) -> bool:
        """Check if budget matches the usage context."""
        # Type must match or be TOTAL
        if budget.budget_type != budget_type and budget.budget_type != BudgetType.TOTAL:
            return False

        # Tenant must match
        if budget.tenant_id != tenant_id:
            return False

        # Team filter (if specified)
        if budget.team and budget.team != team:
            return False

        # Service filter (if specified)
        if budget.service and budget.service != service:
            return False

        return True

    def _check_period_reset(self, budget: Budget, now: datetime) -> None:
        """Check if budget period needs to be reset."""
        should_reset = False

        if budget.period == BudgetPeriod.HOURLY:
            if (now - budget.period_start).total_seconds() >= 3600:
                should_reset = True
        elif budget.period == BudgetPeriod.DAILY:
            if now.date() > budget.period_start.date():
                should_reset = True
        elif budget.period == BudgetPeriod.WEEKLY:
            if (now - budget.period_start).days >= 7:
                should_reset = True
        elif budget.period == BudgetPeriod.MONTHLY:
            if now.month != budget.period_start.month or now.year != budget.period_start.year:
                should_reset = True

        if should_reset:
            # Handle rollover
            rollover_amount = 0.0
            if budget.rollover and budget.remaining > 0:
                rollover_amount = budget.remaining * (budget.rollover_percentage / 100)

            budget.current_usage = 0.0
            budget.period_start = now
            budget.last_reset = now

            # Apply rollover to limit
            if rollover_amount > 0:
                budget.limit += rollover_amount

            # Clear triggered thresholds
            self._triggered_thresholds[budget.id] = set()

            log.info("Reset budget period: %s (rollover=%.2f)", budget.name, rollover_amount)

    def _check_thresholds(self, budget: Budget) -> list[BudgetAlert]:
        """Check if any thresholds are crossed."""
        alerts = []
        triggered = self._triggered_thresholds.get(budget.id, set())

        for threshold in budget.thresholds:
            if threshold.percentage in triggered:
                continue

            if budget.usage_percent >= threshold.percentage:
                # Threshold crossed
                self._alert_counter += 1
                alert = BudgetAlert(
                    id=f"alert_{self._alert_counter}",
                    budget_id=budget.id,
                    threshold=threshold,
                    triggered_at=datetime.now(timezone.utc),
                    usage_at_trigger=budget.current_usage,
                    usage_percent=budget.usage_percent,
                    message=self._generate_alert_message(budget, threshold),
                )

                self._alerts.append(alert)
                alerts.append(alert)
                triggered.add(threshold.percentage)

                # Send notification
                self._send_alert_notification(alert, budget)

                log.warning(
                    "Budget alert: %s at %.1f%% (threshold=%.0f%%, severity=%s)",
                    budget.name, budget.usage_percent,
                    threshold.percentage, threshold.severity.value
                )

        self._triggered_thresholds[budget.id] = triggered
        return alerts

    def _generate_alert_message(
        self,
        budget: Budget,
        threshold: BudgetThreshold,
    ) -> str:
        """Generate alert message."""
        return (
            f"Budget '{budget.name}' has reached {budget.usage_percent:.1f}% "
            f"(${budget.current_usage:.2f} of ${budget.limit:.2f}). "
            f"Remaining: ${budget.remaining:.2f}"
        )

    def _send_alert_notification(self, alert: BudgetAlert, budget: Budget) -> None:
        """Send alert notification."""
        if self.notifications and alert.threshold.notify_channels:
            # In production, send to notification channels
            for channel in alert.threshold.notify_channels:
                log.debug("Would notify channel %s: %s", channel, alert.message)

    def forecast_budget(self, budget_id: str) -> BudgetForecast:
        """Forecast budget usage.

        Args:
            budget_id: Budget to forecast

        Returns:
            BudgetForecast with predictions
        """
        budget = self._budgets.get(budget_id)
        if not budget:
            raise ValueError(f"Budget not found: {budget_id}")

        now = datetime.now(timezone.utc)

        # Calculate usage rate
        period_duration = self._get_period_duration(budget.period)
        elapsed = (now - budget.period_start).total_seconds()
        remaining_seconds = period_duration - elapsed

        if elapsed > 0:
            usage_rate = budget.current_usage / elapsed  # per second
        else:
            usage_rate = 0

        # Predict end-of-period usage
        predicted_usage = budget.current_usage + (usage_rate * remaining_seconds)

        # Determine trend
        if usage_rate > budget.limit / period_duration * 1.2:
            trend = "increasing"
        elif usage_rate < budget.limit / period_duration * 0.8:
            trend = "decreasing"
        else:
            trend = "stable"

        # Check if will exceed
        predicted_exceed = predicted_usage > budget.limit
        exceed_date = None

        if predicted_exceed and usage_rate > 0:
            seconds_to_exceed = (budget.limit - budget.current_usage) / usage_rate
            if seconds_to_exceed > 0:
                exceed_date = now + timedelta(seconds=seconds_to_exceed)

        # Generate recommendations
        recommendations = []
        if predicted_exceed:
            recommendations.append("Consider increasing budget limit")
            recommendations.append("Review high-cost operations")
            if trend == "increasing":
                recommendations.append("Usage accelerating - immediate action recommended")
        elif budget.usage_percent > 70:
            recommendations.append("Monitor usage closely")

        return BudgetForecast(
            budget_id=budget_id,
            forecasted_at=now,
            forecast_period=budget.period,
            predicted_usage=predicted_usage,
            predicted_exceed=predicted_exceed,
            exceed_date=exceed_date,
            confidence=0.8 if elapsed > period_duration * 0.25 else 0.5,
            trend=trend,
            recommendations=recommendations,
        )

    def _get_period_duration(self, period: BudgetPeriod) -> float:
        """Get period duration in seconds."""
        durations = {
            BudgetPeriod.HOURLY: 3600,
            BudgetPeriod.DAILY: 86400,
            BudgetPeriod.WEEKLY: 604800,
            BudgetPeriod.MONTHLY: 2592000,  # 30 days
            BudgetPeriod.QUARTERLY: 7776000,  # 90 days
            BudgetPeriod.YEARLY: 31536000,  # 365 days
        }
        return durations.get(period, 86400)

    def acknowledge_alert(
        self,
        alert_id: str,
        acknowledged_by: str,
    ) -> bool:
        """Acknowledge an alert."""
        alert = next((a for a in self._alerts if a.id == alert_id), None)
        if not alert:
            return False

        alert.acknowledged = True
        alert.acknowledged_by = acknowledged_by
        alert.acknowledged_at = datetime.now(timezone.utc)

        return True

    def get_budget(self, budget_id: str) -> Budget | None:
        """Get a budget by ID."""
        return self._budgets.get(budget_id)

    def list_budgets(
        self,
        tenant_id: str | None = None,
        team: str | None = None,
        budget_type: BudgetType | None = None,
    ) -> list[Budget]:
        """List budgets with optional filters."""
        budgets = list(self._budgets.values())

        if tenant_id:
            budgets = [b for b in budgets if b.tenant_id == tenant_id]
        if team:
            budgets = [b for b in budgets if b.team == team]
        if budget_type:
            budgets = [b for b in budgets if b.budget_type == budget_type]

        return budgets

    def get_alerts(
        self,
        budget_id: str | None = None,
        severity: AlertSeverity | None = None,
        unacknowledged_only: bool = False,
    ) -> list[BudgetAlert]:
        """Get budget alerts."""
        alerts = self._alerts

        if budget_id:
            alerts = [a for a in alerts if a.budget_id == budget_id]
        if severity:
            alerts = [a for a in alerts if a.threshold.severity == severity]
        if unacknowledged_only:
            alerts = [a for a in alerts if not a.acknowledged]

        return alerts

    def get_usage_by_team(
        self,
        tenant_id: str = "default",
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> dict[str, float]:
        """Get usage breakdown by team."""
        allocations = self._allocations

        if start_time:
            allocations = [a for a in allocations if a.timestamp >= start_time]
        if end_time:
            allocations = [a for a in allocations if a.timestamp <= end_time]

        allocations = [a for a in allocations if a.tenant_id == tenant_id]

        by_team: dict[str, float] = {}
        for alloc in allocations:
            team = alloc.team or "unassigned"
            by_team[team] = by_team.get(team, 0) + alloc.amount

        return by_team

    def get_usage_by_service(
        self,
        tenant_id: str = "default",
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> dict[str, float]:
        """Get usage breakdown by service."""
        allocations = self._allocations

        if start_time:
            allocations = [a for a in allocations if a.timestamp >= start_time]
        if end_time:
            allocations = [a for a in allocations if a.timestamp <= end_time]

        allocations = [a for a in allocations if a.tenant_id == tenant_id]

        by_service: dict[str, float] = {}
        for alloc in allocations:
            service = alloc.service or "unassigned"
            by_service[service] = by_service.get(service, 0) + alloc.amount

        return by_service

    def get_stats(self) -> dict[str, Any]:
        """Get budget manager statistics."""
        budgets = list(self._budgets.values())

        return {
            "total_budgets": len(budgets),
            "exceeded_budgets": len([b for b in budgets if b.is_exceeded]),
            "warning_budgets": len([b for b in budgets if 80 <= b.usage_percent < 100]),
            "total_alerts": len(self._alerts),
            "unacknowledged_alerts": len([a for a in self._alerts if not a.acknowledged]),
            "total_allocations": len(self._allocations),
        }


def create_budget_manager(
    notification_service: Any = None,
    redis_client: Any = None,
) -> BudgetManager:
    """Factory function to create a budget manager."""
    return BudgetManager(
        notification_service=notification_service,
        redis_client=redis_client,
    )
