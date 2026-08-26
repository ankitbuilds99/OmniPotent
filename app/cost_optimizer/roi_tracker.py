"""ROI Tracker for Automation.

Tracks return on investment for AI-powered automation:
- Time savings from automated actions
- Cost savings from faster resolution
- Revenue protection from uptime
- Efficiency gains
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("omnipotent.cost.roi_tracker")


class SavingCategory(str, Enum):
    """Categories of cost savings."""
    TIME_SAVED = "time_saved"
    INCIDENT_PREVENTION = "incident_prevention"
    FASTER_MTTR = "faster_mttr"
    AUTOMATION = "automation"
    RESOURCE_OPTIMIZATION = "resource_optimization"
    REVENUE_PROTECTION = "revenue_protection"


class ActionType(str, Enum):
    """Types of automated actions."""
    AUTO_REMEDIATION = "auto_remediation"
    AUTO_SCALING = "auto_scaling"
    ALERT_CORRELATION = "alert_correlation"
    RCA_AUTOMATION = "rca_automation"
    RUNBOOK_EXECUTION = "runbook_execution"
    PREDICTIVE_ACTION = "predictive_action"


@dataclass
class CostSaving:
    """A recorded cost saving."""
    id: str
    timestamp: datetime
    category: SavingCategory
    action_type: ActionType
    description: str
    amount_usd: float

    # Context
    incident_id: str | None = None
    service: str | None = None
    team: str | None = None

    # Calculation details
    time_saved_minutes: float = 0
    manual_cost_estimate: float = 0
    ai_cost: float = 0
    net_saving: float = 0

    # Metadata
    confidence: float = 0.9
    calculation_method: str = "estimate"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "timestamp": self.timestamp.isoformat(),
            "category": self.category.value,
            "action_type": self.action_type.value,
            "description": self.description,
            "amount_usd": self.amount_usd,
            "incident_id": self.incident_id,
            "service": self.service,
            "time_saved_minutes": self.time_saved_minutes,
            "net_saving": self.net_saving,
            "confidence": self.confidence,
        }


@dataclass
class AutomationROI:
    """ROI calculation for an automation."""
    action_type: ActionType
    total_executions: int
    successful_executions: int
    total_time_saved_hours: float
    total_cost_saved_usd: float
    total_ai_cost_usd: float
    net_roi_usd: float
    roi_percentage: float
    avg_time_saved_per_execution: float
    success_rate: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_type": self.action_type.value,
            "total_executions": self.total_executions,
            "successful_executions": self.successful_executions,
            "total_time_saved_hours": self.total_time_saved_hours,
            "total_cost_saved_usd": self.total_cost_saved_usd,
            "total_ai_cost_usd": self.total_ai_cost_usd,
            "net_roi_usd": self.net_roi_usd,
            "roi_percentage": self.roi_percentage,
            "avg_time_saved_per_execution": self.avg_time_saved_per_execution,
            "success_rate": self.success_rate,
        }


@dataclass
class ROIReport:
    """Comprehensive ROI report."""
    period_start: datetime
    period_end: datetime
    generated_at: datetime

    # Summary metrics
    total_savings_usd: float
    total_ai_cost_usd: float
    net_roi_usd: float
    roi_percentage: float
    total_time_saved_hours: float

    # Breakdowns
    by_category: dict[str, float]
    by_action_type: dict[str, AutomationROI]
    by_service: dict[str, float]
    by_team: dict[str, float]

    # Highlights
    top_savings: list[CostSaving]
    incidents_prevented: int
    automations_executed: int

    # Projections
    monthly_projection: float
    yearly_projection: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "generated_at": self.generated_at.isoformat(),
            "total_savings_usd": self.total_savings_usd,
            "total_ai_cost_usd": self.total_ai_cost_usd,
            "net_roi_usd": self.net_roi_usd,
            "roi_percentage": self.roi_percentage,
            "total_time_saved_hours": self.total_time_saved_hours,
            "by_category": self.by_category,
            "by_action_type": {k: v.to_dict() for k, v in self.by_action_type.items()},
            "by_service": self.by_service,
            "by_team": self.by_team,
            "top_savings": [s.to_dict() for s in self.top_savings],
            "incidents_prevented": self.incidents_prevented,
            "automations_executed": self.automations_executed,
            "monthly_projection": self.monthly_projection,
            "yearly_projection": self.yearly_projection,
        }


class ROITracker:
    """Tracks ROI for AI-powered automation.

    Features:
    - Time and cost savings tracking
    - MTTR improvement measurement
    - Revenue protection calculation
    - ROI projections
    - Detailed reporting
    """

    def __init__(
        self,
        engineer_hourly_rate: float = 150.0,
        revenue_per_minute_down: float = 100.0,
        redis_client: Any = None,
    ):
        self.engineer_hourly_rate = engineer_hourly_rate
        self.revenue_per_minute_down = revenue_per_minute_down
        self.redis = redis_client

        self._savings: list[CostSaving] = []
        self._ai_costs: list[dict[str, Any]] = []
        self._saving_counter = 0

        # Benchmarks for manual actions (minutes)
        self._manual_time_benchmarks = {
            ActionType.AUTO_REMEDIATION: 30,
            ActionType.AUTO_SCALING: 15,
            ActionType.ALERT_CORRELATION: 20,
            ActionType.RCA_AUTOMATION: 60,
            ActionType.RUNBOOK_EXECUTION: 45,
            ActionType.PREDICTIVE_ACTION: 0,  # Wouldn't happen manually
        }

    def record_saving(
        self,
        category: SavingCategory,
        action_type: ActionType,
        description: str,
        incident_id: str | None = None,
        service: str | None = None,
        team: str | None = None,
        time_saved_minutes: float | None = None,
        amount_usd: float | None = None,
        ai_cost: float = 0,
        metadata: dict[str, Any] | None = None,
    ) -> CostSaving:
        """Record a cost saving.

        Args:
            category: Category of saving
            action_type: Type of automated action
            description: Description of the saving
            incident_id: Related incident ID
            service: Related service
            team: Related team
            time_saved_minutes: Time saved (or calculated from benchmark)
            amount_usd: Direct dollar saving (or calculated)
            ai_cost: Cost of AI resources used
            metadata: Additional metadata

        Returns:
            Recorded CostSaving
        """
        self._saving_counter += 1
        saving_id = f"sav_{self._saving_counter}"

        # Calculate time saved if not provided
        if time_saved_minutes is None:
            time_saved_minutes = self._manual_time_benchmarks.get(action_type, 30)

        # Calculate dollar amount if not provided
        if amount_usd is None:
            amount_usd = self._calculate_saving_amount(
                category, action_type, time_saved_minutes
            )

        # Calculate manual cost estimate
        manual_cost = (time_saved_minutes / 60) * self.engineer_hourly_rate

        # Net saving
        net_saving = amount_usd - ai_cost

        saving = CostSaving(
            id=saving_id,
            timestamp=datetime.now(timezone.utc),
            category=category,
            action_type=action_type,
            description=description,
            amount_usd=amount_usd,
            incident_id=incident_id,
            service=service,
            team=team,
            time_saved_minutes=time_saved_minutes,
            manual_cost_estimate=manual_cost,
            ai_cost=ai_cost,
            net_saving=net_saving,
            metadata=metadata or {},
        )

        self._savings.append(saving)

        log.info(
            "Recorded saving: %s (category=%s, amount=$%.2f, net=$%.2f)",
            description, category.value, amount_usd, net_saving
        )

        return saving

    def _calculate_saving_amount(
        self,
        category: SavingCategory,
        action_type: ActionType,
        time_saved_minutes: float,
    ) -> float:
        """Calculate dollar amount for a saving."""
        # Base time-based saving
        time_cost = (time_saved_minutes / 60) * self.engineer_hourly_rate

        # Category-specific multipliers
        if category == SavingCategory.INCIDENT_PREVENTION:
            # Prevented incidents save investigation + resolution + downtime
            return time_cost * 3 + (time_saved_minutes * self.revenue_per_minute_down * 0.5)
        elif category == SavingCategory.FASTER_MTTR:
            # Faster resolution saves revenue
            return time_cost + (time_saved_minutes * self.revenue_per_minute_down)
        elif category == SavingCategory.REVENUE_PROTECTION:
            return time_saved_minutes * self.revenue_per_minute_down
        elif category == SavingCategory.RESOURCE_OPTIMIZATION:
            # Direct cloud cost savings
            return time_cost * 0.5  # Conservative estimate
        else:
            return time_cost

    def record_ai_cost(
        self,
        amount_usd: float,
        action_type: ActionType,
        description: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Record AI/LLM cost.

        Args:
            amount_usd: Cost in USD
            action_type: Type of action
            description: Description
            metadata: Additional metadata
        """
        self._ai_costs.append({
            "timestamp": datetime.now(timezone.utc),
            "amount_usd": amount_usd,
            "action_type": action_type.value,
            "description": description,
            "metadata": metadata or {},
        })

    def record_auto_remediation(
        self,
        incident_id: str,
        service: str,
        resolution_time_minutes: float,
        success: bool,
        ai_cost: float = 0,
    ) -> CostSaving | None:
        """Record an automated remediation.

        Args:
            incident_id: Incident that was remediated
            service: Service affected
            resolution_time_minutes: Time to resolve
            success: Whether remediation succeeded
            ai_cost: AI cost for remediation

        Returns:
            CostSaving if successful
        """
        if not success:
            return None

        # Manual benchmark is much longer
        manual_time = self._manual_time_benchmarks[ActionType.AUTO_REMEDIATION]
        time_saved = max(0, manual_time - resolution_time_minutes)

        return self.record_saving(
            category=SavingCategory.FASTER_MTTR,
            action_type=ActionType.AUTO_REMEDIATION,
            description=f"Auto-remediated incident {incident_id}",
            incident_id=incident_id,
            service=service,
            time_saved_minutes=time_saved,
            ai_cost=ai_cost,
            metadata={
                "resolution_time_minutes": resolution_time_minutes,
                "manual_benchmark_minutes": manual_time,
            },
        )

    def record_incident_prevention(
        self,
        service: str,
        prediction_type: str,
        estimated_severity: str,
        ai_cost: float = 0,
    ) -> CostSaving:
        """Record a prevented incident.

        Args:
            service: Service that would have been affected
            prediction_type: Type of prediction that prevented it
            estimated_severity: Estimated severity of prevented incident
            ai_cost: AI cost for prediction

        Returns:
            CostSaving
        """
        # Estimate time saved based on severity
        severity_time_map = {
            "critical": 120,  # 2 hours
            "high": 60,
            "medium": 30,
            "low": 15,
        }
        time_saved = severity_time_map.get(estimated_severity.lower(), 30)

        return self.record_saving(
            category=SavingCategory.INCIDENT_PREVENTION,
            action_type=ActionType.PREDICTIVE_ACTION,
            description=f"Prevented {estimated_severity} incident via {prediction_type}",
            service=service,
            time_saved_minutes=time_saved,
            ai_cost=ai_cost,
            metadata={
                "prediction_type": prediction_type,
                "estimated_severity": estimated_severity,
            },
        )

    def calculate_roi_by_action(
        self,
        action_type: ActionType,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> AutomationROI:
        """Calculate ROI for a specific action type.

        Args:
            action_type: Action type to analyze
            start_time: Start of period
            end_time: End of period

        Returns:
            AutomationROI with metrics
        """
        savings = [s for s in self._savings if s.action_type == action_type]

        if start_time:
            savings = [s for s in savings if s.timestamp >= start_time]
        if end_time:
            savings = [s for s in savings if s.timestamp <= end_time]

        # Get AI costs for this action type
        ai_costs = [
            c for c in self._ai_costs
            if c["action_type"] == action_type.value
        ]
        if start_time:
            ai_costs = [c for c in ai_costs if c["timestamp"] >= start_time]
        if end_time:
            ai_costs = [c for c in ai_costs if c["timestamp"] <= end_time]

        total_cost_saved = sum(s.amount_usd for s in savings)
        total_ai_cost = sum(c["amount_usd"] for c in ai_costs)
        total_time_saved = sum(s.time_saved_minutes for s in savings)

        net_roi = total_cost_saved - total_ai_cost
        roi_percentage = (net_roi / total_ai_cost * 100) if total_ai_cost > 0 else 0

        return AutomationROI(
            action_type=action_type,
            total_executions=len(savings),
            successful_executions=len(savings),  # All recorded are successful
            total_time_saved_hours=total_time_saved / 60,
            total_cost_saved_usd=total_cost_saved,
            total_ai_cost_usd=total_ai_cost,
            net_roi_usd=net_roi,
            roi_percentage=roi_percentage,
            avg_time_saved_per_execution=total_time_saved / max(1, len(savings)),
            success_rate=1.0,
        )

    def generate_report(
        self,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> ROIReport:
        """Generate comprehensive ROI report.

        Args:
            start_time: Start of period (default: 30 days ago)
            end_time: End of period (default: now)

        Returns:
            ROIReport with all metrics
        """
        now = datetime.now(timezone.utc)

        if not start_time:
            start_time = now - timedelta(days=30)
        if not end_time:
            end_time = now

        # Filter data
        savings = [
            s for s in self._savings
            if start_time <= s.timestamp <= end_time
        ]
        ai_costs = [
            c for c in self._ai_costs
            if start_time <= c["timestamp"] <= end_time
        ]

        # Calculate totals
        total_savings = sum(s.amount_usd for s in savings)
        total_ai_cost = sum(c["amount_usd"] for c in ai_costs)
        total_time_saved = sum(s.time_saved_minutes for s in savings) / 60

        net_roi = total_savings - total_ai_cost
        roi_percentage = (net_roi / total_ai_cost * 100) if total_ai_cost > 0 else 0

        # By category
        by_category: dict[str, float] = {}
        for saving in savings:
            cat = saving.category.value
            by_category[cat] = by_category.get(cat, 0) + saving.amount_usd

        # By action type
        by_action_type: dict[str, AutomationROI] = {}
        for action in ActionType:
            roi = self.calculate_roi_by_action(action, start_time, end_time)
            if roi.total_executions > 0:
                by_action_type[action.value] = roi

        # By service
        by_service: dict[str, float] = {}
        for saving in savings:
            svc = saving.service or "unknown"
            by_service[svc] = by_service.get(svc, 0) + saving.amount_usd

        # By team
        by_team: dict[str, float] = {}
        for saving in savings:
            team = saving.team or "unknown"
            by_team[team] = by_team.get(team, 0) + saving.amount_usd

        # Top savings
        top_savings = sorted(savings, key=lambda s: s.amount_usd, reverse=True)[:10]

        # Counts
        incidents_prevented = len([
            s for s in savings
            if s.category == SavingCategory.INCIDENT_PREVENTION
        ])
        automations_executed = len(savings)

        # Projections
        period_days = (end_time - start_time).days or 1
        daily_avg = net_roi / period_days
        monthly_projection = daily_avg * 30
        yearly_projection = daily_avg * 365

        return ROIReport(
            period_start=start_time,
            period_end=end_time,
            generated_at=now,
            total_savings_usd=total_savings,
            total_ai_cost_usd=total_ai_cost,
            net_roi_usd=net_roi,
            roi_percentage=roi_percentage,
            total_time_saved_hours=total_time_saved,
            by_category=by_category,
            by_action_type=by_action_type,
            by_service=by_service,
            by_team=by_team,
            top_savings=top_savings,
            incidents_prevented=incidents_prevented,
            automations_executed=automations_executed,
            monthly_projection=monthly_projection,
            yearly_projection=yearly_projection,
        )

    def get_savings(
        self,
        category: SavingCategory | None = None,
        action_type: ActionType | None = None,
        service: str | None = None,
        limit: int = 100,
    ) -> list[CostSaving]:
        """Get recorded savings with filters.

        Args:
            category: Filter by category
            action_type: Filter by action type
            service: Filter by service
            limit: Maximum results

        Returns:
            List of CostSaving
        """
        savings = self._savings

        if category:
            savings = [s for s in savings if s.category == category]
        if action_type:
            savings = [s for s in savings if s.action_type == action_type]
        if service:
            savings = [s for s in savings if s.service == service]

        return savings[-limit:]

    def get_stats(self) -> dict[str, Any]:
        """Get tracker statistics."""
        total_savings = sum(s.amount_usd for s in self._savings)
        total_ai_cost = sum(c["amount_usd"] for c in self._ai_costs)
        total_time = sum(s.time_saved_minutes for s in self._savings)

        return {
            "total_savings_recorded": len(self._savings),
            "total_savings_usd": total_savings,
            "total_ai_cost_usd": total_ai_cost,
            "net_roi_usd": total_savings - total_ai_cost,
            "total_time_saved_hours": total_time / 60,
            "avg_saving_per_action": total_savings / max(1, len(self._savings)),
        }


def create_roi_tracker(
    engineer_hourly_rate: float = 150.0,
    revenue_per_minute_down: float = 100.0,
    redis_client: Any = None,
) -> ROITracker:
    """Factory function to create an ROI tracker."""
    return ROITracker(
        engineer_hourly_rate=engineer_hourly_rate,
        revenue_per_minute_down=revenue_per_minute_down,
        redis_client=redis_client,
    )
