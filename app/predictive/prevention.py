"""Automated prevention actions for predicted incidents.

Suggests and executes prevention actions before incidents occur.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Callable

from app.predictive.predictions import PredictedIncident, IncidentType

log = logging.getLogger("devcopilot.predictive.prevention")


class ActionType(Enum):
    """Type of prevention action."""

    SCALE_UP = "scale_up"
    SCALE_DOWN = "scale_down"
    ROLLBACK = "rollback"
    RESTART_SERVICE = "restart_service"
    CLEAR_CACHE = "clear_cache"
    INCREASE_POOL = "increase_pool"
    ENABLE_CIRCUIT_BREAKER = "enable_circuit_breaker"
    SHED_LOAD = "shed_load"
    FAILOVER = "failover"
    HOTFIX = "hotfix"
    ALERT_ONLY = "alert_only"
    CUSTOM = "custom"


class ActionRisk(Enum):
    """Risk level of a prevention action."""

    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    @property
    def requires_approval(self) -> bool:
        return self in (ActionRisk.MEDIUM, ActionRisk.HIGH)


class ActionStatus(Enum):
    """Status of a prevention action."""

    SUGGESTED = "suggested"
    APPROVED = "approved"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class ActionCost:
    """Cost of executing a prevention action."""

    hourly_cost: Decimal
    one_time_cost: Decimal
    estimated_duration_hours: float
    total_estimated_cost: Decimal

    def to_dict(self) -> dict[str, Any]:
        return {
            "hourly_cost": str(self.hourly_cost),
            "one_time_cost": str(self.one_time_cost),
            "estimated_duration_hours": self.estimated_duration_hours,
            "total_estimated_cost": str(self.total_estimated_cost),
        }


@dataclass
class ActionEffect:
    """Expected effect of a prevention action."""

    buys_time_hours: float
    fixes_root_cause: bool
    reduces_blast_radius: bool
    reversible: bool
    side_effects: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "buys_time_hours": self.buys_time_hours,
            "fixes_root_cause": self.fixes_root_cause,
            "reduces_blast_radius": self.reduces_blast_radius,
            "reversible": self.reversible,
            "side_effects": self.side_effects,
        }


@dataclass
class PreventionAction:
    """A prevention action to avoid a predicted incident."""

    action_id: str
    prediction_id: str
    action_type: ActionType
    title: str
    description: str
    risk: ActionRisk
    status: ActionStatus
    cost: ActionCost
    effect: ActionEffect
    target_service: str
    parameters: dict[str, Any]
    created_at: datetime
    executed_at: datetime | None = None
    executed_by: str | None = None
    result: str | None = None

    @property
    def is_auto_executable(self) -> bool:
        return self.risk == ActionRisk.NONE or self.risk == ActionRisk.LOW

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "prediction_id": self.prediction_id,
            "action_type": self.action_type.value,
            "title": self.title,
            "description": self.description,
            "risk": self.risk.value,
            "status": self.status.value,
            "cost": self.cost.to_dict(),
            "effect": self.effect.to_dict(),
            "target_service": self.target_service,
            "parameters": self.parameters,
            "created_at": self.created_at.isoformat(),
            "executed_at": self.executed_at.isoformat() if self.executed_at else None,
            "executed_by": self.executed_by,
            "result": self.result,
            "is_auto_executable": self.is_auto_executable,
        }


class PreventionEngine:
    """Engine for suggesting and executing prevention actions."""

    def __init__(self) -> None:
        self._actions: dict[str, PreventionAction] = {}
        self._action_counter = 0
        self._action_handlers: dict[ActionType, Callable[..., bool]] = {}
        self._action_templates = self._setup_templates()

    def _setup_templates(self) -> dict[str, dict[str, Any]]:
        """Set up action templates for different incident types."""
        return {
            "scale_up_immediate": {
                "type": ActionType.SCALE_UP,
                "risk": ActionRisk.LOW,
                "buys_time_hours": 4,
                "fixes_root_cause": False,
                "reversible": True,
            },
            "rollback_deploy": {
                "type": ActionType.ROLLBACK,
                "risk": ActionRisk.MEDIUM,
                "buys_time_hours": 0,
                "fixes_root_cause": True,
                "reversible": True,
            },
            "hotfix_deploy": {
                "type": ActionType.HOTFIX,
                "risk": ActionRisk.HIGH,
                "buys_time_hours": 0,
                "fixes_root_cause": True,
                "reversible": True,
            },
            "increase_pool": {
                "type": ActionType.INCREASE_POOL,
                "risk": ActionRisk.LOW,
                "buys_time_hours": 2,
                "fixes_root_cause": False,
                "reversible": True,
            },
            "clear_cache": {
                "type": ActionType.CLEAR_CACHE,
                "risk": ActionRisk.LOW,
                "buys_time_hours": 1,
                "fixes_root_cause": False,
                "reversible": False,
            },
            "circuit_breaker": {
                "type": ActionType.ENABLE_CIRCUIT_BREAKER,
                "risk": ActionRisk.MEDIUM,
                "buys_time_hours": 0,
                "fixes_root_cause": False,
                "reversible": True,
            },
            "failover": {
                "type": ActionType.FAILOVER,
                "risk": ActionRisk.HIGH,
                "buys_time_hours": 0,
                "fixes_root_cause": True,
                "reversible": True,
            },
        }

    def suggest_actions(
        self,
        prediction: PredictedIncident,
    ) -> list[PreventionAction]:
        """Suggest prevention actions for a predicted incident."""
        actions = []

        incident_actions = {
            IncidentType.RESOURCE_EXHAUSTION: [
                self._create_scale_action(prediction),
                self._create_pool_action(prediction),
            ],
            IncidentType.SERVICE_OUTAGE: [
                self._create_scale_action(prediction),
                self._create_rollback_action(prediction),
                self._create_hotfix_action(prediction),
            ],
            IncidentType.CASCADE_FAILURE: [
                self._create_circuit_breaker_action(prediction),
                self._create_scale_action(prediction),
                self._create_shed_load_action(prediction),
            ],
            IncidentType.PERFORMANCE_DEGRADATION: [
                self._create_scale_action(prediction),
                self._create_cache_action(prediction),
            ],
        }

        suggested = incident_actions.get(prediction.incident_type, [])
        actions = [a for a in suggested if a is not None]

        actions.sort(key=lambda a: (a.risk.value, -a.effect.buys_time_hours))

        return actions

    def _create_scale_action(
        self,
        prediction: PredictedIncident,
    ) -> PreventionAction:
        """Create a scale-up action."""
        self._action_counter += 1
        action_id = f"action_{self._action_counter}"

        service = prediction.affected_services[0] if prediction.affected_services else "unknown"
        current_pods = 3
        target_pods = 6

        return PreventionAction(
            action_id=action_id,
            prediction_id=prediction.prediction_id,
            action_type=ActionType.SCALE_UP,
            title="Scale Service (Immediate Mitigation)",
            description=f"Scale {service} from {current_pods} to {target_pods} pods",
            risk=ActionRisk.LOW,
            status=ActionStatus.SUGGESTED,
            cost=ActionCost(
                hourly_cost=Decimal("2.40"),
                one_time_cost=Decimal("0"),
                estimated_duration_hours=4.0,
                total_estimated_cost=Decimal("9.60"),
            ),
            effect=ActionEffect(
                buys_time_hours=4.0,
                fixes_root_cause=False,
                reduces_blast_radius=True,
                reversible=True,
                side_effects=["Increased infrastructure cost"],
            ),
            target_service=service,
            parameters={
                "current_replicas": current_pods,
                "target_replicas": target_pods,
                "resource_request_cpu": "500m",
                "resource_request_memory": "512Mi",
            },
            created_at=datetime.now(timezone.utc),
        )

    def _create_rollback_action(
        self,
        prediction: PredictedIncident,
    ) -> PreventionAction:
        """Create a rollback action."""
        self._action_counter += 1
        action_id = f"action_{self._action_counter}"

        service = prediction.affected_services[0] if prediction.affected_services else "unknown"

        return PreventionAction(
            action_id=action_id,
            prediction_id=prediction.prediction_id,
            action_type=ActionType.ROLLBACK,
            title="Rollback Recent Deploy",
            description=f"Rollback {service} to previous stable version",
            risk=ActionRisk.MEDIUM,
            status=ActionStatus.SUGGESTED,
            cost=ActionCost(
                hourly_cost=Decimal("0"),
                one_time_cost=Decimal("0"),
                estimated_duration_hours=0.25,
                total_estimated_cost=Decimal("0"),
            ),
            effect=ActionEffect(
                buys_time_hours=0,
                fixes_root_cause=True,
                reduces_blast_radius=True,
                reversible=True,
                side_effects=[
                    "Reverts other changes in the deploy",
                    "May require re-deploy of fixes later",
                ],
            ),
            target_service=service,
            parameters={
                "current_version": "v2.3.1",
                "rollback_to": "v2.3.0",
                "deployment_id": "deploy-52",
            },
            created_at=datetime.now(timezone.utc),
        )

    def _create_hotfix_action(
        self,
        prediction: PredictedIncident,
    ) -> PreventionAction:
        """Create a hotfix action."""
        self._action_counter += 1
        action_id = f"action_{self._action_counter}"

        service = prediction.affected_services[0] if prediction.affected_services else "unknown"

        return PreventionAction(
            action_id=action_id,
            prediction_id=prediction.prediction_id,
            action_type=ActionType.HOTFIX,
            title="Deploy Hotfix",
            description=f"Deploy auto-generated hotfix for {service}",
            risk=ActionRisk.HIGH,
            status=ActionStatus.SUGGESTED,
            cost=ActionCost(
                hourly_cost=Decimal("0"),
                one_time_cost=Decimal("0"),
                estimated_duration_hours=0.5,
                total_estimated_cost=Decimal("0"),
            ),
            effect=ActionEffect(
                buys_time_hours=0,
                fixes_root_cause=True,
                reduces_blast_radius=True,
                reversible=True,
                side_effects=[
                    "Requires code review",
                    "May introduce new issues",
                ],
            ),
            target_service=service,
            parameters={
                "pr_number": "#893",
                "fix_type": "null_check",
                "file": "checkout.py",
                "line": 142,
            },
            created_at=datetime.now(timezone.utc),
        )

    def _create_pool_action(
        self,
        prediction: PredictedIncident,
    ) -> PreventionAction:
        """Create a connection pool increase action."""
        self._action_counter += 1
        action_id = f"action_{self._action_counter}"

        service = prediction.affected_services[0] if prediction.affected_services else "unknown"

        return PreventionAction(
            action_id=action_id,
            prediction_id=prediction.prediction_id,
            action_type=ActionType.INCREASE_POOL,
            title="Increase Connection Pool",
            description=f"Increase connection pool size for {service}",
            risk=ActionRisk.LOW,
            status=ActionStatus.SUGGESTED,
            cost=ActionCost(
                hourly_cost=Decimal("0.50"),
                one_time_cost=Decimal("0"),
                estimated_duration_hours=2.0,
                total_estimated_cost=Decimal("1.00"),
            ),
            effect=ActionEffect(
                buys_time_hours=2.0,
                fixes_root_cause=False,
                reduces_blast_radius=False,
                reversible=True,
                side_effects=["May increase database load"],
            ),
            target_service=service,
            parameters={
                "current_pool_size": 20,
                "target_pool_size": 50,
                "config_key": "DB_POOL_SIZE",
            },
            created_at=datetime.now(timezone.utc),
        )

    def _create_cache_action(
        self,
        prediction: PredictedIncident,
    ) -> PreventionAction:
        """Create a cache clear action."""
        self._action_counter += 1
        action_id = f"action_{self._action_counter}"

        service = prediction.affected_services[0] if prediction.affected_services else "unknown"

        return PreventionAction(
            action_id=action_id,
            prediction_id=prediction.prediction_id,
            action_type=ActionType.CLEAR_CACHE,
            title="Clear Cache",
            description=f"Clear application cache for {service}",
            risk=ActionRisk.LOW,
            status=ActionStatus.SUGGESTED,
            cost=ActionCost(
                hourly_cost=Decimal("0"),
                one_time_cost=Decimal("0"),
                estimated_duration_hours=0.1,
                total_estimated_cost=Decimal("0"),
            ),
            effect=ActionEffect(
                buys_time_hours=1.0,
                fixes_root_cause=False,
                reduces_blast_radius=False,
                reversible=False,
                side_effects=[
                    "Temporary increase in database load",
                    "Slower responses during cache rebuild",
                ],
            ),
            target_service=service,
            parameters={
                "cache_type": "redis",
                "keys_pattern": f"{service}:*",
            },
            created_at=datetime.now(timezone.utc),
        )

    def _create_circuit_breaker_action(
        self,
        prediction: PredictedIncident,
    ) -> PreventionAction:
        """Create a circuit breaker action."""
        self._action_counter += 1
        action_id = f"action_{self._action_counter}"

        service = prediction.affected_services[0] if prediction.affected_services else "unknown"

        return PreventionAction(
            action_id=action_id,
            prediction_id=prediction.prediction_id,
            action_type=ActionType.ENABLE_CIRCUIT_BREAKER,
            title="Enable Circuit Breaker",
            description=f"Enable circuit breaker for {service} dependencies",
            risk=ActionRisk.MEDIUM,
            status=ActionStatus.SUGGESTED,
            cost=ActionCost(
                hourly_cost=Decimal("0"),
                one_time_cost=Decimal("0"),
                estimated_duration_hours=0.05,
                total_estimated_cost=Decimal("0"),
            ),
            effect=ActionEffect(
                buys_time_hours=0,
                fixes_root_cause=False,
                reduces_blast_radius=True,
                reversible=True,
                side_effects=[
                    "Some requests will fail fast",
                    "Reduced functionality",
                ],
            ),
            target_service=service,
            parameters={
                "threshold_failures": 5,
                "timeout_seconds": 30,
                "half_open_after_seconds": 60,
            },
            created_at=datetime.now(timezone.utc),
        )

    def _create_shed_load_action(
        self,
        prediction: PredictedIncident,
    ) -> PreventionAction:
        """Create a load shedding action."""
        self._action_counter += 1
        action_id = f"action_{self._action_counter}"

        service = prediction.affected_services[0] if prediction.affected_services else "unknown"

        return PreventionAction(
            action_id=action_id,
            prediction_id=prediction.prediction_id,
            action_type=ActionType.SHED_LOAD,
            title="Enable Load Shedding",
            description=f"Shed 20% of traffic to {service}",
            risk=ActionRisk.MEDIUM,
            status=ActionStatus.SUGGESTED,
            cost=ActionCost(
                hourly_cost=Decimal("0"),
                one_time_cost=Decimal("0"),
                estimated_duration_hours=1.0,
                total_estimated_cost=Decimal("0"),
            ),
            effect=ActionEffect(
                buys_time_hours=2.0,
                fixes_root_cause=False,
                reduces_blast_radius=True,
                reversible=True,
                side_effects=[
                    "20% of requests will receive 503",
                    "Customer impact expected",
                ],
            ),
            target_service=service,
            parameters={
                "shed_percentage": 20,
                "priority_traffic_exempt": True,
            },
            created_at=datetime.now(timezone.utc),
        )

    def register_handler(
        self,
        action_type: ActionType,
        handler: Callable[..., bool],
    ) -> None:
        """Register a handler for executing an action type."""
        self._action_handlers[action_type] = handler

    def execute_action(
        self,
        action_id: str,
        executed_by: str,
        force: bool = False,
    ) -> bool:
        """Execute a prevention action."""
        action = self._actions.get(action_id)
        if not action:
            return False

        if action.risk.requires_approval and not force:
            log.warning(
                "action requires approval",
                extra={"action_id": action_id, "risk": action.risk.value},
            )
            return False

        action.status = ActionStatus.EXECUTING
        action.executed_by = executed_by
        action.executed_at = datetime.now(timezone.utc)

        handler = self._action_handlers.get(action.action_type)
        if handler:
            try:
                success = handler(action)
                action.status = ActionStatus.COMPLETED if success else ActionStatus.FAILED
                action.result = "Success" if success else "Handler returned failure"
            except Exception as e:
                action.status = ActionStatus.FAILED
                action.result = str(e)
                log.exception("action execution failed", extra={"action_id": action_id})
                return False
        else:
            action.status = ActionStatus.COMPLETED
            action.result = "No handler registered - simulated execution"

        log.info(
            "action executed",
            extra={
                "action_id": action_id,
                "action_type": action.action_type.value,
                "status": action.status.value,
                "executed_by": executed_by,
            },
        )

        return action.status == ActionStatus.COMPLETED

    def approve_action(self, action_id: str, approved_by: str) -> bool:
        """Approve an action for execution."""
        action = self._actions.get(action_id)
        if action and action.status == ActionStatus.SUGGESTED:
            action.status = ActionStatus.APPROVED
            log.info(
                "action approved",
                extra={"action_id": action_id, "approved_by": approved_by},
            )
            return True
        return False

    def skip_action(self, action_id: str, reason: str) -> bool:
        """Skip an action."""
        action = self._actions.get(action_id)
        if action:
            action.status = ActionStatus.SKIPPED
            action.result = f"Skipped: {reason}"
            return True
        return False

    def get_action(self, action_id: str) -> PreventionAction | None:
        """Get an action by ID."""
        return self._actions.get(action_id)

    def get_actions_for_prediction(
        self,
        prediction_id: str,
    ) -> list[PreventionAction]:
        """Get all actions for a prediction."""
        return [
            a for a in self._actions.values() if a.prediction_id == prediction_id
        ]

    def get_pending_actions(self) -> list[PreventionAction]:
        """Get all pending (suggested or approved) actions."""
        return [
            a
            for a in self._actions.values()
            if a.status in (ActionStatus.SUGGESTED, ActionStatus.APPROVED)
        ]

    def store_action(self, action: PreventionAction) -> None:
        """Store an action."""
        self._actions[action.action_id] = action


_engine: PreventionEngine | None = None


def get_prevention_engine() -> PreventionEngine:
    """Get the default prevention engine."""
    global _engine
    if _engine is None:
        _engine = PreventionEngine()
    return _engine
