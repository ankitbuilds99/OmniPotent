"""Decision Engine Integration with DAG Coordinator.

Integrates the Decision Engine to:
- Evaluate remediation options before execution
- Predict success probability for each task
- Apply policy checks (OPA, change windows)
- Make go/no-go decisions based on risk
- Support automated and approval-required workflows
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.coordinator.dag_executor import (
    DAGExecutor,
    TaskDAG,
    TaskNode,
    TaskExecution,
    ExecutionState,
)
from app.decision_engine import (
    DecisionEngine,
    DecisionOption,
    PredictionFeatures,
    create_decision_engine,
)
from app.decision_engine.engine import (
    IncidentContext,
    DecisionResult,
    DecisionOutcome,
)
from app.decision_engine.success_predictor import ActionType
from app.decision_engine.option_ranker import RankingStrategy
from app.policy_engine import (
    PolicyEngine,
    PolicyDecision,
    create_policy_engine,
)
from app.digital_twin import (
    DigitalTwinSimulator,
    SimulationConfig,
    create_digital_twin,
)
from app.digital_twin.simulator import SimulationType
from app.digital_twin.chaos_engine import SafetyLevel

log = logging.getLogger("omnipotent.coordinator.decision_integration")


@dataclass
class DecisionGate:
    """A decision gate in the execution pipeline."""
    name: str
    decision_result: DecisionResult | None = None
    policy_result: Any | None = None
    simulation_result: Any | None = None
    approved: bool = False
    approved_by: str | None = None
    approved_at: datetime | None = None
    override_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "approved": self.approved,
            "approved_by": self.approved_by,
            "approved_at": self.approved_at.isoformat() if self.approved_at else None,
            "decision_outcome": self.decision_result.outcome.value if self.decision_result else None,
            "decision_confidence": self.decision_result.confidence.value if self.decision_result else None,
            "override_reason": self.override_reason,
        }


@dataclass
class EnhancedTaskExecution(TaskExecution):
    """Task execution with decision engine integration."""
    success_prediction: float | None = None
    risk_level: str | None = None
    decision_gate: DecisionGate | None = None
    policy_violations: list[dict[str, Any]] = field(default_factory=list)
    safeguards_applied: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        base = super().to_dict()
        base.update({
            "success_prediction": self.success_prediction,
            "risk_level": self.risk_level,
            "decision_gate": self.decision_gate.to_dict() if self.decision_gate else None,
            "policy_violations": self.policy_violations,
            "safeguards_applied": self.safeguards_applied,
        })
        return base


class DecisionAwareDAGExecutor(DAGExecutor):
    """DAG Executor with integrated decision engine.

    Adds:
    - Pre-execution decision evaluation
    - Success probability prediction
    - Policy enforcement
    - Automatic safeguard application
    - Approval workflow integration
    """

    def __init__(
        self,
        decision_engine: DecisionEngine | None = None,
        policy_engine: PolicyEngine | None = None,
        digital_twin: DigitalTwinSimulator | None = None,
        require_decision_approval: bool = True,
        auto_apply_safeguards: bool = True,
        min_success_probability: float = 0.7,
        max_risk_score: float = 0.5,
        simulate_before_execute: bool = False,
    ):
        super().__init__()

        self.decision = decision_engine or create_decision_engine()
        self.policy = policy_engine or create_policy_engine()
        self.twin = digital_twin

        self.require_approval = require_decision_approval
        self.auto_safeguards = auto_apply_safeguards
        self.min_success_prob = min_success_probability
        self.max_risk = max_risk_score
        self.simulate_first = simulate_before_execute

        self._decision_gates: dict[str, DecisionGate] = {}
        self._decision_cache: dict[str, DecisionResult] = {}

    async def execute_with_decision(
        self,
        dag: TaskDAG,
        incident_context: dict[str, Any] | None = None,
        ranking_strategy: RankingStrategy = RankingStrategy.BALANCED,
    ) -> dict[str, EnhancedTaskExecution]:
        """Execute a DAG with integrated decision making.

        Args:
            dag: The task DAG to execute
            incident_context: Context about the incident being addressed
            ranking_strategy: Strategy for ranking remediation options

        Returns:
            Dict mapping task IDs to their enhanced execution results
        """
        log.info("Starting decision-aware DAG execution: %s", dag.id)

        # Build incident context
        context = self._build_incident_context(dag, incident_context)

        # Convert tasks to decision options
        options = self._tasks_to_options(dag)

        # Get decision for the overall DAG
        dag_decision = await self.decision.decide(context, options, ranking_strategy)
        self._decision_cache[dag.id] = dag_decision

        log.info(
            "DAG decision: %s (confidence=%s, score=%.2f)",
            dag_decision.outcome.value,
            dag_decision.confidence.value,
            dag_decision.composite_score,
        )

        # Check if we can proceed
        if dag_decision.outcome == DecisionOutcome.ABORT:
            log.warning("Decision engine recommends ABORT for DAG %s", dag.id)
            return self._create_aborted_results(dag, dag_decision)

        if dag_decision.outcome == DecisionOutcome.REQUIRE_APPROVAL:
            gate = DecisionGate(name=f"dag_{dag.id}", decision_result=dag_decision)
            self._decision_gates[dag.id] = gate

            if self.require_approval:
                log.info("DAG %s requires approval before execution", dag.id)
                return self._create_pending_approval_results(dag, gate)

        # Check change windows
        window_check = self.policy.windows.check_change_allowed(
            action_type="dag_execution",
            risk_score=1 - dag_decision.composite_score,
            environment=incident_context.get("environment") if incident_context else None,
        ) if self.policy.windows else None

        if window_check and not window_check.allowed:
            log.warning("DAG blocked by change window: %s", window_check.reason)
            return self._create_blocked_results(dag, window_check.reason)

        # Optional: Run simulation first
        if self.simulate_first and self.twin:
            sim_result = await self._simulate_dag(dag, incident_context)
            if not sim_result.safe_to_proceed:
                log.warning("Simulation indicates unsafe to proceed")
                return self._create_simulation_blocked_results(dag, sim_result)

        # Execute with enhanced tracking
        results = await self._execute_dag_with_tracking(dag, dag_decision)

        return results

    def _build_incident_context(
        self,
        dag: TaskDAG,
        context: dict[str, Any] | None,
    ) -> IncidentContext:
        """Build an IncidentContext from DAG and provided context."""
        ctx = context or {}

        return IncidentContext(
            incident_id=ctx.get("incident_id", f"dag-{dag.id}"),
            title=ctx.get("title", dag.name),
            severity=ctx.get("severity", "P2"),
            status=ctx.get("status", "investigating"),
            started_at=datetime.fromisoformat(ctx["started_at"]) if "started_at" in ctx else datetime.now(timezone.utc),
            duration_minutes=ctx.get("duration_minutes", 30),
            affected_services=ctx.get("affected_services", []),
            affected_users_estimate=ctx.get("affected_users_estimate", 0),
            is_customer_facing=ctx.get("is_customer_facing", True),
            is_revenue_impacting=ctx.get("is_revenue_impacting", False),
            root_cause_hypothesis=ctx.get("root_cause_hypothesis", ""),
            root_cause_confidence=ctx.get("root_cause_confidence", 0.5),
            evidence_collected=ctx.get("evidence_collected", []),
            is_peak_traffic=ctx.get("is_peak_traffic", False),
            is_change_freeze=ctx.get("is_change_freeze", False),
            is_maintenance_window=ctx.get("is_maintenance_window", False),
            recent_deployments=ctx.get("recent_deployments", []),
            responders=ctx.get("responders", []),
            on_call_experience_level=ctx.get("on_call_experience_level", 0.7),
        )

    def _tasks_to_options(self, dag: TaskDAG) -> list[DecisionOption]:
        """Convert DAG tasks to decision options."""
        options = []

        for task_id, task in dag.tasks.items():
            # Map handler to action type
            action_type = self._handler_to_action_type(task.handler)

            options.append(DecisionOption(
                option_id=task_id,
                name=task.name,
                description=task.description or f"Execute {task.handler}",
                action_type=action_type,
                success_probability=0.8,  # Will be updated by predictor
                risk_score=0.3,  # Default, will be adjusted
                estimated_time_minutes=task.estimated_duration_seconds // 60,
                estimated_cost_usd=0,
                impact_reduction_percent=50,
                is_reversible=task.is_reversible,
                is_automated=True,
                requires_approval=task.requires_approval,
                requires_downtime=False,
                prerequisites=[],
                side_effects=[],
                evidence_supporting=[],
                evidence_against=[],
            ))

        return options

    def _handler_to_action_type(self, handler: str) -> str:
        """Map a handler name to an action type."""
        handler_lower = handler.lower()

        if "restart" in handler_lower:
            return "restart_service"
        elif "scale" in handler_lower:
            if "up" in handler_lower:
                return "scale_up"
            return "scale_down"
        elif "rollback" in handler_lower:
            return "rollback"
        elif "config" in handler_lower:
            return "config_change"
        elif "cache" in handler_lower:
            return "cache_clear"
        elif "flag" in handler_lower or "toggle" in handler_lower:
            return "feature_flag_toggle"
        elif "pod" in handler_lower:
            return "pod_restart"
        elif "database" in handler_lower or "migration" in handler_lower:
            return "database_migration"
        else:
            return "custom"

    async def _execute_dag_with_tracking(
        self,
        dag: TaskDAG,
        decision: DecisionResult,
    ) -> dict[str, EnhancedTaskExecution]:
        """Execute DAG with enhanced tracking."""
        results: dict[str, EnhancedTaskExecution] = {}

        # Apply safeguards if configured
        safeguards = decision.safeguards if self.auto_safeguards else []

        # Get execution order
        levels = dag.get_execution_levels()

        for level_tasks in levels:
            level_results = await asyncio.gather(*[
                self._execute_task_with_decision(dag.tasks[tid], safeguards)
                for tid in level_tasks
            ], return_exceptions=True)

            for tid, raw_result in zip(level_tasks, level_results):
                if isinstance(raw_result, Exception):
                    results[tid] = EnhancedTaskExecution(
                        task_id=tid,
                        state=ExecutionState.FAILED,
                        error=str(raw_result),
                    )
                elif isinstance(raw_result, EnhancedTaskExecution):
                    results[tid] = raw_result
                else:
                    results[tid] = EnhancedTaskExecution(
                        task_id=tid,
                        state=ExecutionState.COMPLETED,
                    )

        return results

    async def _execute_task_with_decision(
        self,
        task: TaskNode,
        safeguards: list[str],
    ) -> EnhancedTaskExecution:
        """Execute a single task with decision integration."""
        execution = EnhancedTaskExecution(
            task_id=task.id,
            state=ExecutionState.RUNNING,
            started_at=datetime.now(timezone.utc),
            safeguards_applied=safeguards,
        )

        try:
            # Check policy for this specific task
            if self.policy:
                policy_result = await self.policy.evaluate(
                    action=task.handler,
                    resource={"task_id": task.id, "name": task.name},
                    context=task.input_params,
                )

                if policy_result.decision == PolicyDecision.DENY:
                    execution.state = ExecutionState.BLOCKED
                    execution.policy_violations = [v.to_dict() for v in policy_result.violations]
                    execution.error = f"Blocked by policy: {policy_result.violations[0].message if policy_result.violations else 'Unknown'}"
                    return execution

            # Predict success for this task
            predictor = self.decision.predictor
            features = self._build_task_features(task)
            prediction = await predictor.predict(features)

            execution.success_prediction = prediction.probability
            execution.risk_level = prediction.risk_level.value

            # Check if prediction meets thresholds
            if prediction.probability < self.min_success_prob:
                log.warning(
                    "Task %s below success threshold: %.2f < %.2f",
                    task.id, prediction.probability, self.min_success_prob
                )

                if task.requires_approval:
                    execution.state = ExecutionState.WAITING_APPROVAL
                    execution.decision_gate = DecisionGate(name=f"task_{task.id}")
                    return execution

            # Execute the actual task
            handler = self.handlers.get(task.handler)
            if handler:
                result = await handler(task, task.input_params)
                execution.output = result
                execution.state = ExecutionState.COMPLETED
            else:
                execution.state = ExecutionState.FAILED
                execution.error = f"Handler not found: {task.handler}"

        except Exception as e:
            execution.state = ExecutionState.FAILED
            execution.error = str(e)
            log.error("Task %s failed: %s", task.id, e)

        finally:
            execution.completed_at = datetime.now(timezone.utc)
            if execution.started_at:
                execution.duration_ms = int(
                    (execution.completed_at - execution.started_at).total_seconds() * 1000
                )

        return execution

    def _build_task_features(self, task: TaskNode) -> PredictionFeatures:
        """Build prediction features for a task."""
        action_type_map = {
            "restart_service": ActionType.RESTART_SERVICE,
            "scale_up": ActionType.SCALE_UP,
            "scale_down": ActionType.SCALE_DOWN,
            "rollback": ActionType.ROLLBACK,
            "config_change": ActionType.CONFIG_CHANGE,
            "cache_clear": ActionType.CACHE_CLEAR,
            "feature_flag_toggle": ActionType.FEATURE_FLAG_TOGGLE,
            "pod_restart": ActionType.POD_RESTART,
        }

        action_type = action_type_map.get(
            self._handler_to_action_type(task.handler),
            ActionType.CUSTOM
        )

        return PredictionFeatures(
            action_type=action_type,
            action_complexity=0.5,
            is_reversible=task.is_reversible,
            requires_downtime=False,
            estimated_duration_seconds=task.estimated_duration_seconds,
            incident_severity="P2",
            incident_duration_minutes=30,
            affected_services_count=1,
            is_customer_facing=True,
            is_revenue_impacting=False,
            similar_incidents_count=10,
            similar_action_success_rate=0.85,
            service_change_frequency=2.0,
            time_since_last_deploy_hours=24.0,
            is_peak_traffic=False,
            is_change_freeze=False,
            is_maintenance_window=False,
            on_call_experience_level=0.7,
            root_cause_confidence=0.7,
            evidence_count=5,
            diagnostic_coverage=0.6,
        )

    async def _simulate_dag(
        self,
        dag: TaskDAG,
        context: dict[str, Any] | None,
    ) -> Any:
        """Simulate DAG execution before running."""
        if not self.twin:
            return None

        config = SimulationConfig(
            simulation_type=SimulationType.DEPLOYMENT,
            target_service=context.get("service", "unknown") if context else "unknown",
            environment=context.get("environment", "production") if context else "production",
            clone_environment=True,
            parameters={"dag_id": dag.id},
            safety_level=SafetyLevel.SAFE,
        )

        return await self.twin.simulate(config)

    def _create_aborted_results(
        self,
        dag: TaskDAG,
        decision: DecisionResult,
    ) -> dict[str, EnhancedTaskExecution]:
        """Create results for an aborted DAG."""
        results = {}
        for task_id in dag.tasks:
            results[task_id] = EnhancedTaskExecution(
                task_id=task_id,
                state=ExecutionState.CANCELLED,
                error=f"DAG aborted by decision engine: {decision.reasoning}",
                decision_gate=DecisionGate(name="abort", decision_result=decision),
            )
        return results

    def _create_pending_approval_results(
        self,
        dag: TaskDAG,
        gate: DecisionGate,
    ) -> dict[str, EnhancedTaskExecution]:
        """Create results for a DAG pending approval."""
        results = {}
        for task_id in dag.tasks:
            results[task_id] = EnhancedTaskExecution(
                task_id=task_id,
                state=ExecutionState.WAITING_APPROVAL,
                decision_gate=gate,
            )
        return results

    def _create_blocked_results(
        self,
        dag: TaskDAG,
        reason: str,
    ) -> dict[str, EnhancedTaskExecution]:
        """Create results for a blocked DAG."""
        results = {}
        for task_id in dag.tasks:
            results[task_id] = EnhancedTaskExecution(
                task_id=task_id,
                state=ExecutionState.BLOCKED,
                error=f"Blocked by change window: {reason}",
            )
        return results

    def _create_simulation_blocked_results(
        self,
        dag: TaskDAG,
        sim_result: Any,
    ) -> dict[str, EnhancedTaskExecution]:
        """Create results for a DAG blocked by simulation."""
        results = {}
        for task_id in dag.tasks:
            results[task_id] = EnhancedTaskExecution(
                task_id=task_id,
                state=ExecutionState.BLOCKED,
                error="Blocked by simulation: unsafe to proceed",
            )
        return results

    async def approve_dag(
        self,
        dag_id: str,
        approved_by: str,
        reason: str = "",
    ) -> bool:
        """Approve a pending DAG for execution."""
        gate = self._decision_gates.get(dag_id)
        if not gate:
            return False

        gate.approved = True
        gate.approved_by = approved_by
        gate.approved_at = datetime.now(timezone.utc)
        gate.override_reason = reason

        log.info("DAG %s approved by %s: %s", dag_id, approved_by, reason)
        return True

    def get_pending_approvals(self) -> list[dict[str, Any]]:
        """Get all pending approval requests."""
        return [
            {"dag_id": dag_id, "gate": gate.to_dict()}
            for dag_id, gate in self._decision_gates.items()
            if not gate.approved
        ]

    def get_decision_summary(self, dag_id: str) -> dict[str, Any] | None:
        """Get the decision summary for a DAG."""
        decision = self._decision_cache.get(dag_id)
        if not decision:
            return None

        return {
            "decision_id": decision.decision_id,
            "outcome": decision.outcome.value,
            "confidence": decision.confidence.value,
            "composite_score": decision.composite_score,
            "recommended_option": decision.recommended_option.option.name if decision.recommended_option else None,
            "safeguards": decision.safeguards,
            "warnings": decision.warnings,
            "reasoning": decision.reasoning,
        }


def create_decision_aware_executor(
    require_approval: bool = True,
    simulate_first: bool = False,
) -> DecisionAwareDAGExecutor:
    """Factory function to create a decision-aware DAG executor."""
    return DecisionAwareDAGExecutor(
        decision_engine=create_decision_engine(),
        policy_engine=create_policy_engine(),
        digital_twin=create_digital_twin() if simulate_first else None,
        require_decision_approval=require_approval,
        simulate_before_execute=simulate_first,
    )
