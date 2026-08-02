"""Rollback Simulation Engine.

Simulates rollback scenarios before executing:
- State change analysis
- Data migration rollback
- Service dependency ordering
- Risk assessment
- Time estimation
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any
import uuid

log = logging.getLogger("devcopilot.digital_twin.rollback")


class RollbackStrategy(str, Enum):
    """Strategies for rollback execution."""
    INSTANT = "instant"  # Immediate switch (blue-green)
    GRADUAL = "gradual"  # Progressive rollback (canary)
    STAGED = "staged"  # Multi-phase rollback
    MANUAL = "manual"  # Require manual steps


class RollbackRisk(str, Enum):
    """Risk levels for rollback operations."""
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class RollbackPhase(str, Enum):
    """Phases of a rollback operation."""
    PREPARATION = "preparation"
    PRE_CHECKS = "pre_checks"
    DATA_ROLLBACK = "data_rollback"
    SERVICE_ROLLBACK = "service_rollback"
    VERIFICATION = "verification"
    CLEANUP = "cleanup"
    COMPLETE = "complete"
    FAILED = "failed"


@dataclass
class StateChange:
    """A state change that needs to be rolled back."""
    resource_type: str
    resource_id: str
    change_type: str  # "create", "update", "delete"
    before_state: dict[str, Any] | None
    after_state: dict[str, Any] | None
    reversible: bool
    reversal_complexity: int  # 1-10
    dependencies: list[str] = field(default_factory=list)
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "change_type": self.change_type,
            "reversible": self.reversible,
            "reversal_complexity": self.reversal_complexity,
            "dependencies": self.dependencies,
            "notes": self.notes,
        }


@dataclass
class RollbackStep:
    """A single step in the rollback plan."""
    order: int
    phase: RollbackPhase
    action: str
    resource: str
    estimated_duration_seconds: int
    can_fail_safe: bool
    rollback_of_rollback: str | None = None  # Action if this step fails
    status: str = "pending"  # "pending", "running", "completed", "failed", "skipped"
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "order": self.order,
            "phase": self.phase.value,
            "action": self.action,
            "resource": self.resource,
            "estimated_duration_seconds": self.estimated_duration_seconds,
            "can_fail_safe": self.can_fail_safe,
            "status": self.status,
            "error": self.error,
        }


@dataclass
class RollbackScenario:
    """A complete rollback scenario."""
    id: str
    name: str
    deployment_id: str
    service: str
    from_version: str
    to_version: str
    strategy: RollbackStrategy
    state_changes: list[StateChange]
    steps: list[RollbackStep]
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "deployment_id": self.deployment_id,
            "service": self.service,
            "from_version": self.from_version,
            "to_version": self.to_version,
            "strategy": self.strategy.value,
            "state_changes": [c.to_dict() for c in self.state_changes],
            "steps": [s.to_dict() for s in self.steps],
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class SimulationIssue:
    """An issue discovered during simulation."""
    severity: RollbackRisk
    category: str  # "data", "dependency", "timing", "resource"
    description: str
    affected_step: int | None
    mitigation: str
    blocking: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity.value,
            "category": self.category,
            "description": self.description,
            "affected_step": self.affected_step,
            "mitigation": self.mitigation,
            "blocking": self.blocking,
        }


@dataclass
class RollbackResult:
    """Result of rollback simulation."""
    scenario: RollbackScenario
    simulated_at: datetime
    success_probability: float  # 0-1
    risk_level: RollbackRisk
    estimated_duration: timedelta
    estimated_downtime: timedelta
    issues: list[SimulationIssue]
    recommendations: list[str]
    can_proceed: bool
    requires_approval: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario.to_dict(),
            "simulated_at": self.simulated_at.isoformat(),
            "success_probability": self.success_probability,
            "risk_level": self.risk_level.value,
            "estimated_duration_minutes": self.estimated_duration.total_seconds() / 60,
            "estimated_downtime_minutes": self.estimated_downtime.total_seconds() / 60,
            "issues": [i.to_dict() for i in self.issues],
            "recommendations": self.recommendations,
            "can_proceed": self.can_proceed,
            "requires_approval": self.requires_approval,
        }


class RollbackSimulator:
    """Simulates rollback operations before execution.

    Features:
    - State change analysis
    - Dependency ordering
    - Risk assessment
    - Time estimation
    - Issue detection
    """

    def __init__(
        self,
        k8s_client: Any = None,
        db_client: Any = None,
        knowledge_graph: Any = None,
    ):
        self.k8s = k8s_client
        self.db = db_client
        self.knowledge_graph = knowledge_graph

        self._scenarios: dict[str, RollbackScenario] = {}
        self._results: dict[str, RollbackResult] = {}

        # Complexity weights by resource type
        self._complexity_weights = {
            "deployment": 2,
            "service": 1,
            "configmap": 1,
            "secret": 2,
            "database_schema": 8,
            "database_data": 9,
            "cache": 3,
            "queue": 4,
            "storage": 5,
        }

    async def create_scenario(
        self,
        deployment_id: str,
        service: str,
        from_version: str,
        to_version: str,
        state_changes: list[dict[str, Any]] | None = None,
    ) -> RollbackScenario:
        """Create a rollback scenario.

        Args:
            deployment_id: ID of deployment to rollback
            service: Service name
            from_version: Current version
            to_version: Target version (rollback to)
            state_changes: Optional list of state changes

        Returns:
            RollbackScenario with planned steps
        """
        scenario_id = str(uuid.uuid4())[:8]

        # Parse state changes
        changes = []
        if state_changes:
            for change in state_changes:
                changes.append(StateChange(
                    resource_type=change.get("resource_type", "unknown"),
                    resource_id=change.get("resource_id", ""),
                    change_type=change.get("change_type", "update"),
                    before_state=change.get("before_state"),
                    after_state=change.get("after_state"),
                    reversible=change.get("reversible", True),
                    reversal_complexity=change.get("complexity", 3),
                    dependencies=change.get("dependencies", []),
                    notes=change.get("notes", ""),
                ))
        else:
            # Discover changes from deployment history
            changes = await self._discover_state_changes(deployment_id, from_version, to_version)

        # Determine strategy
        strategy = self._determine_strategy(changes)

        # Generate rollback steps
        steps = self._generate_rollback_steps(changes, strategy)

        scenario = RollbackScenario(
            id=scenario_id,
            name=f"Rollback {service} {from_version} -> {to_version}",
            deployment_id=deployment_id,
            service=service,
            from_version=from_version,
            to_version=to_version,
            strategy=strategy,
            state_changes=changes,
            steps=steps,
        )

        self._scenarios[scenario_id] = scenario
        log.info("Created rollback scenario: %s (%s)", scenario_id, strategy.value)

        return scenario

    async def _discover_state_changes(
        self,
        deployment_id: str,
        from_version: str,
        to_version: str,
    ) -> list[StateChange]:
        """Discover state changes from deployment history using Kubernetes API."""
        import os
        from app.connectors.kubernetes import KubernetesConnector

        changes = []
        k8s = KubernetesConnector()

        try:
            namespace = os.environ.get("NAMESPACE", "default")
            dep_result = await k8s.get_deployment(deployment_id, namespace)

            if dep_result.success and dep_result.data:
                changes.append(StateChange(
                    resource_type="deployment",
                    resource_id=f"deployment/{deployment_id}",
                    change_type="update",
                    before_state={"image": dep_result.data.get("image", f"app:{to_version}")},
                    after_state={"image": f"app:{from_version}"},
                    reversible=True,
                    reversal_complexity=2,
                ))
            else:
                changes.append(StateChange(
                    resource_type="deployment",
                    resource_id=f"deployment/{deployment_id}",
                    change_type="update",
                    before_state={"image": f"app:{to_version}"},
                    after_state={"image": f"app:{from_version}"},
                    reversible=True,
                    reversal_complexity=2,
                ))

        except Exception as e:
            log.warning("Failed to discover state changes: %s", e)
            changes.append(StateChange(
                resource_type="deployment",
                resource_id=f"deployment/{deployment_id}",
                change_type="update",
                before_state={"image": f"app:{to_version}"},
                after_state={"image": f"app:{from_version}"},
                reversible=True,
                reversal_complexity=2,
            ))
        finally:
            await k8s.close()

        changes.append(StateChange(
            resource_type="configmap",
            resource_id=f"configmap/{deployment_id}-config",
            change_type="update",
            before_state={"version": to_version},
            after_state={"version": from_version},
            reversible=True,
            reversal_complexity=1,
        ))

        return changes

    def _determine_strategy(self, changes: list[StateChange]) -> RollbackStrategy:
        """Determine optimal rollback strategy based on changes."""
        has_data_changes = any(
            c.resource_type in ["database_schema", "database_data"]
            for c in changes
        )
        has_irreversible = any(not c.reversible for c in changes)
        total_complexity = sum(c.reversal_complexity for c in changes)

        if has_irreversible:
            return RollbackStrategy.MANUAL
        elif has_data_changes:
            return RollbackStrategy.STAGED
        elif total_complexity > 20:
            return RollbackStrategy.GRADUAL
        else:
            return RollbackStrategy.INSTANT

    def _generate_rollback_steps(
        self,
        changes: list[StateChange],
        strategy: RollbackStrategy,
    ) -> list[RollbackStep]:
        """Generate ordered rollback steps."""
        steps = []
        order = 0

        # Phase 1: Preparation
        order += 1
        steps.append(RollbackStep(
            order=order,
            phase=RollbackPhase.PREPARATION,
            action="Create rollback checkpoint",
            resource="system",
            estimated_duration_seconds=10,
            can_fail_safe=True,
        ))

        # Phase 2: Pre-checks
        order += 1
        steps.append(RollbackStep(
            order=order,
            phase=RollbackPhase.PRE_CHECKS,
            action="Verify target version available",
            resource="deployment",
            estimated_duration_seconds=5,
            can_fail_safe=True,
        ))

        order += 1
        steps.append(RollbackStep(
            order=order,
            phase=RollbackPhase.PRE_CHECKS,
            action="Check service health",
            resource="service",
            estimated_duration_seconds=10,
            can_fail_safe=True,
        ))

        # Phase 3: Data rollback (if needed)
        data_changes = [c for c in changes if c.resource_type.startswith("database")]
        for change in data_changes:
            order += 1
            steps.append(RollbackStep(
                order=order,
                phase=RollbackPhase.DATA_ROLLBACK,
                action=f"Rollback {change.change_type} on {change.resource_type}",
                resource=change.resource_id,
                estimated_duration_seconds=change.reversal_complexity * 30,
                can_fail_safe=False,
                rollback_of_rollback="Restore from backup",
            ))

        # Phase 4: Service rollback
        service_changes = [c for c in changes if not c.resource_type.startswith("database")]

        # Order by dependencies (reverse topological sort)
        ordered_changes = self._order_by_dependencies(service_changes)

        for change in ordered_changes:
            order += 1

            if strategy == RollbackStrategy.GRADUAL:
                # Add canary step before full rollback
                order += 1
                steps.append(RollbackStep(
                    order=order,
                    phase=RollbackPhase.SERVICE_ROLLBACK,
                    action=f"Canary rollback {change.resource_type}",
                    resource=change.resource_id,
                    estimated_duration_seconds=30,
                    can_fail_safe=True,
                ))

            steps.append(RollbackStep(
                order=order,
                phase=RollbackPhase.SERVICE_ROLLBACK,
                action=f"Rollback {change.resource_type}",
                resource=change.resource_id,
                estimated_duration_seconds=change.reversal_complexity * 10,
                can_fail_safe=True,
                rollback_of_rollback="Re-apply current version",
            ))

        # Phase 5: Verification
        order += 1
        steps.append(RollbackStep(
            order=order,
            phase=RollbackPhase.VERIFICATION,
            action="Verify service health",
            resource="service",
            estimated_duration_seconds=30,
            can_fail_safe=True,
        ))

        order += 1
        steps.append(RollbackStep(
            order=order,
            phase=RollbackPhase.VERIFICATION,
            action="Run smoke tests",
            resource="tests",
            estimated_duration_seconds=60,
            can_fail_safe=True,
        ))

        # Phase 6: Cleanup
        order += 1
        steps.append(RollbackStep(
            order=order,
            phase=RollbackPhase.CLEANUP,
            action="Clean up temporary resources",
            resource="system",
            estimated_duration_seconds=10,
            can_fail_safe=True,
        ))

        return steps

    def _order_by_dependencies(
        self,
        changes: list[StateChange],
    ) -> list[StateChange]:
        """Order changes by dependencies (reverse for rollback)."""
        # Simple ordering - in production use proper topological sort
        ordered = []

        # ConfigMaps first (might be needed by deployments)
        ordered.extend([c for c in changes if c.resource_type == "configmap"])

        # Then secrets
        ordered.extend([c for c in changes if c.resource_type == "secret"])

        # Then services
        ordered.extend([c for c in changes if c.resource_type == "service"])

        # Finally deployments
        ordered.extend([c for c in changes if c.resource_type == "deployment"])

        # Add any remaining
        for c in changes:
            if c not in ordered:
                ordered.append(c)

        return ordered

    async def simulate(
        self,
        scenario_id: str,
    ) -> RollbackResult:
        """Simulate a rollback scenario.

        Args:
            scenario_id: ID of scenario to simulate

        Returns:
            RollbackResult with simulation findings
        """
        scenario = self._scenarios.get(scenario_id)
        if not scenario:
            raise ValueError(f"Scenario not found: {scenario_id}")

        log.info("Simulating rollback scenario: %s", scenario_id)

        issues: list[SimulationIssue] = []
        recommendations: list[str] = []

        # Analyze state changes
        for change in scenario.state_changes:
            change_issues = await self._analyze_state_change(change)
            issues.extend(change_issues)

        # Analyze step ordering
        ordering_issues = self._analyze_step_ordering(scenario.steps)
        issues.extend(ordering_issues)

        # Analyze dependencies
        dep_issues = await self._analyze_dependencies(scenario)
        issues.extend(dep_issues)

        # Calculate estimates
        total_duration = timedelta(seconds=sum(s.estimated_duration_seconds for s in scenario.steps))
        downtime_steps = [s for s in scenario.steps if not s.can_fail_safe]
        downtime = timedelta(seconds=sum(s.estimated_duration_seconds for s in downtime_steps))

        # Calculate success probability
        success_prob = self._calculate_success_probability(scenario, issues)

        # Determine risk level
        risk_level = self._determine_risk_level(issues, scenario)

        # Generate recommendations
        recommendations = self._generate_recommendations(scenario, issues)

        # Determine if can proceed
        blocking_issues = [i for i in issues if i.blocking]
        can_proceed = len(blocking_issues) == 0
        requires_approval = risk_level in [RollbackRisk.HIGH, RollbackRisk.CRITICAL]

        result = RollbackResult(
            scenario=scenario,
            simulated_at=datetime.now(timezone.utc),
            success_probability=success_prob,
            risk_level=risk_level,
            estimated_duration=total_duration,
            estimated_downtime=downtime,
            issues=issues,
            recommendations=recommendations,
            can_proceed=can_proceed,
            requires_approval=requires_approval,
        )

        self._results[scenario_id] = result

        log.info(
            "Simulation complete: %s (success=%.0f%%, risk=%s, duration=%s)",
            scenario_id, success_prob * 100, risk_level.value, total_duration
        )

        return result

    async def _analyze_state_change(
        self,
        change: StateChange,
    ) -> list[SimulationIssue]:
        """Analyze a state change for issues."""
        issues = []

        if not change.reversible:
            issues.append(SimulationIssue(
                severity=RollbackRisk.CRITICAL,
                category="data",
                description=f"Irreversible change: {change.resource_type} {change.resource_id}",
                affected_step=None,
                mitigation="Manual intervention required",
                blocking=True,
            ))

        if change.reversal_complexity >= 8:
            issues.append(SimulationIssue(
                severity=RollbackRisk.HIGH,
                category="data",
                description=f"High complexity rollback: {change.resource_type}",
                affected_step=None,
                mitigation="Consider staged rollback approach",
                blocking=False,
            ))

        if change.resource_type == "database_schema":
            issues.append(SimulationIssue(
                severity=RollbackRisk.HIGH,
                category="data",
                description="Database schema change requires careful rollback",
                affected_step=None,
                mitigation="Ensure backward-compatible migration exists",
                blocking=False,
            ))

        return issues

    def _analyze_step_ordering(
        self,
        steps: list[RollbackStep],
    ) -> list[SimulationIssue]:
        """Analyze step ordering for issues."""
        issues = []

        # Check for unsafe operations early in sequence
        for i, step in enumerate(steps):
            if not step.can_fail_safe and i < 3:
                issues.append(SimulationIssue(
                    severity=RollbackRisk.MEDIUM,
                    category="timing",
                    description=f"Critical step early in sequence: {step.action}",
                    affected_step=step.order,
                    mitigation="Consider adding more pre-checks",
                    blocking=False,
                ))

        return issues

    async def _analyze_dependencies(
        self,
        scenario: RollbackScenario,
    ) -> list[SimulationIssue]:
        """Analyze service dependencies for issues."""
        issues = []

        # In production, query knowledge graph for dependencies
        # Check for circular dependencies, missing dependencies, etc.

        # Simulated check
        if len(scenario.state_changes) > 5:
            issues.append(SimulationIssue(
                severity=RollbackRisk.MEDIUM,
                category="dependency",
                description="Multiple resources may have interdependencies",
                affected_step=None,
                mitigation="Review dependency graph before proceeding",
                blocking=False,
            ))

        return issues

    def _calculate_success_probability(
        self,
        scenario: RollbackScenario,
        issues: list[SimulationIssue],
    ) -> float:
        """Calculate rollback success probability."""
        base_prob = 0.95

        # Reduce based on issues
        for issue in issues:
            if issue.severity == RollbackRisk.CRITICAL:
                base_prob *= 0.5
            elif issue.severity == RollbackRisk.HIGH:
                base_prob *= 0.8
            elif issue.severity == RollbackRisk.MEDIUM:
                base_prob *= 0.95

        # Reduce based on complexity
        total_complexity = sum(c.reversal_complexity for c in scenario.state_changes)
        complexity_factor = max(0.5, 1 - (total_complexity / 100))
        base_prob *= complexity_factor

        return max(0.1, min(0.99, base_prob))

    def _determine_risk_level(
        self,
        issues: list[SimulationIssue],
        scenario: RollbackScenario,
    ) -> RollbackRisk:
        """Determine overall risk level."""
        if any(i.severity == RollbackRisk.CRITICAL for i in issues):
            return RollbackRisk.CRITICAL

        high_count = len([i for i in issues if i.severity == RollbackRisk.HIGH])
        if high_count >= 2 or scenario.strategy == RollbackStrategy.MANUAL:
            return RollbackRisk.HIGH

        medium_count = len([i for i in issues if i.severity == RollbackRisk.MEDIUM])
        if medium_count >= 3 or high_count >= 1:
            return RollbackRisk.MEDIUM

        return RollbackRisk.LOW

    def _generate_recommendations(
        self,
        scenario: RollbackScenario,
        issues: list[SimulationIssue],
    ) -> list[str]:
        """Generate recommendations based on simulation."""
        recommendations = []

        # Strategy recommendations
        if scenario.strategy == RollbackStrategy.INSTANT:
            recommendations.append("Consider enabling gradual rollback for better observability")

        if scenario.strategy == RollbackStrategy.MANUAL:
            recommendations.append("Document manual steps clearly before proceeding")
            recommendations.append("Have support team on standby")

        # Issue-based recommendations
        for issue in issues:
            if issue.mitigation and issue.mitigation not in recommendations:
                recommendations.append(issue.mitigation)

        # General recommendations
        if len(scenario.state_changes) > 3:
            recommendations.append("Consider breaking into smaller rollback phases")

        recommendations.append("Notify stakeholders before starting rollback")
        recommendations.append("Have database backup ready for restore if needed")

        return list(set(recommendations))

    def get_scenario(self, scenario_id: str) -> RollbackScenario | None:
        """Get a scenario by ID."""
        return self._scenarios.get(scenario_id)

    def get_result(self, scenario_id: str) -> RollbackResult | None:
        """Get simulation result by scenario ID."""
        return self._results.get(scenario_id)

    def list_scenarios(self, service: str | None = None) -> list[RollbackScenario]:
        """List rollback scenarios."""
        scenarios = list(self._scenarios.values())

        if service:
            scenarios = [s for s in scenarios if s.service == service]

        return scenarios

    def get_stats(self) -> dict[str, Any]:
        """Get simulator statistics."""
        results = list(self._results.values())

        return {
            "total_scenarios": len(self._scenarios),
            "simulations_run": len(results),
            "avg_success_probability": sum(r.success_probability for r in results) / max(1, len(results)),
            "high_risk_count": len([r for r in results if r.risk_level == RollbackRisk.HIGH]),
        }


def create_rollback_simulator(
    k8s_client: Any = None,
    db_client: Any = None,
    knowledge_graph: Any = None,
) -> RollbackSimulator:
    """Factory function to create a rollback simulator."""
    return RollbackSimulator(
        k8s_client=k8s_client,
        db_client=db_client,
        knowledge_graph=knowledge_graph,
    )
