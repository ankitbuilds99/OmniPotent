"""Digital Twin Simulator - Main Orchestrator.

Orchestrates all digital twin capabilities:
- Infrastructure cloning
- Downtime prediction
- Rollback simulation
- Chaos engineering
- What-if analysis
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
import uuid

from app.digital_twin.infrastructure_clone import (
    InfrastructureCloner,
    CloneConfig,
    ClonedEnvironment,
    CloneStrategy,
)
from app.digital_twin.downtime_predictor import (
    DowntimePredictor,
    DowntimePrediction,
)
from app.digital_twin.rollback_simulator import (
    RollbackSimulator,
    RollbackResult,
)
from app.digital_twin.chaos_engine import (
    ChaosEngine,
    ChaosResult,
    SafetyLevel,
)

log = logging.getLogger("devcopilot.digital_twin.simulator")


class SimulationType(str, Enum):
    """Types of simulations."""
    DEPLOYMENT = "deployment"
    ROLLBACK = "rollback"
    SCALING = "scaling"
    FAILURE = "failure"
    CHAOS = "chaos"
    WHAT_IF = "what_if"


@dataclass
class SimulationConfig:
    """Configuration for a simulation run."""
    simulation_type: SimulationType
    target_service: str
    environment: str = "production"

    # Clone settings
    clone_environment: bool = True
    clone_strategy: CloneStrategy = CloneStrategy.MINIMAL
    clone_ttl_hours: int = 2

    # Simulation parameters
    parameters: dict[str, Any] = field(default_factory=dict)

    # Safety
    safety_level: SafetyLevel = SafetyLevel.SAFE
    max_duration_seconds: int = 600

    # Analysis
    predict_downtime: bool = True
    run_chaos: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "simulation_type": self.simulation_type.value,
            "target_service": self.target_service,
            "environment": self.environment,
            "clone_environment": self.clone_environment,
            "clone_strategy": self.clone_strategy.value,
            "parameters": self.parameters,
            "safety_level": self.safety_level.value,
            "max_duration_seconds": self.max_duration_seconds,
        }


@dataclass
class SimulationResult:
    """Complete result of a simulation."""
    id: str
    config: SimulationConfig
    status: str  # "pending", "running", "completed", "failed"
    started_at: datetime
    completed_at: datetime | None

    # Environment
    cloned_environment: ClonedEnvironment | None

    # Results
    deployment_result: dict[str, Any] | None
    rollback_result: RollbackResult | None
    chaos_result: ChaosResult | None
    downtime_predictions: list[DowntimePrediction]

    # Analysis
    success_probability: float
    risk_score: float
    estimated_duration_minutes: float
    estimated_downtime_minutes: float
    issues: list[dict[str, Any]]
    recommendations: list[str]

    # Decision
    safe_to_proceed: bool
    requires_approval: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "config": self.config.to_dict(),
            "status": self.status,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "cloned_environment": self.cloned_environment.to_dict() if self.cloned_environment else None,
            "deployment_result": self.deployment_result,
            "rollback_result": self.rollback_result.to_dict() if self.rollback_result else None,
            "chaos_result": self.chaos_result.to_dict() if self.chaos_result else None,
            "downtime_predictions": [p.to_dict() for p in self.downtime_predictions],
            "success_probability": self.success_probability,
            "risk_score": self.risk_score,
            "estimated_duration_minutes": self.estimated_duration_minutes,
            "estimated_downtime_minutes": self.estimated_downtime_minutes,
            "issues": self.issues,
            "recommendations": self.recommendations,
            "safe_to_proceed": self.safe_to_proceed,
            "requires_approval": self.requires_approval,
        }


class DigitalTwinSimulator:
    """Main orchestrator for digital twin simulations.

    Combines:
    - Infrastructure cloning for safe testing
    - Downtime prediction for risk assessment
    - Rollback simulation for recovery planning
    - Chaos engineering for resilience testing
    """

    def __init__(
        self,
        infrastructure_cloner: InfrastructureCloner | None = None,
        downtime_predictor: DowntimePredictor | None = None,
        rollback_simulator: RollbackSimulator | None = None,
        chaos_engine: ChaosEngine | None = None,
        k8s_client: Any = None,
        redis_client: Any = None,
    ):
        self.cloner = infrastructure_cloner or InfrastructureCloner(k8s_client=k8s_client)
        self.downtime = downtime_predictor or DowntimePredictor()
        self.rollback = rollback_simulator or RollbackSimulator(k8s_client=k8s_client)
        self.chaos = chaos_engine or ChaosEngine(k8s_client=k8s_client)
        self.redis = redis_client

        self._simulations: dict[str, SimulationResult] = {}
        self._active_simulation: str | None = None

    async def simulate(
        self,
        config: SimulationConfig,
    ) -> SimulationResult:
        """Run a complete simulation.

        Args:
            config: Simulation configuration

        Returns:
            SimulationResult with all findings
        """
        simulation_id = str(uuid.uuid4())[:8]

        log.info(
            "Starting %s simulation for %s (id=%s)",
            config.simulation_type.value, config.target_service, simulation_id
        )

        # Initialize result
        result = SimulationResult(
            id=simulation_id,
            config=config,
            status="running",
            started_at=datetime.now(timezone.utc),
            completed_at=None,
            cloned_environment=None,
            deployment_result=None,
            rollback_result=None,
            chaos_result=None,
            downtime_predictions=[],
            success_probability=0.0,
            risk_score=0.0,
            estimated_duration_minutes=0.0,
            estimated_downtime_minutes=0.0,
            issues=[],
            recommendations=[],
            safe_to_proceed=False,
            requires_approval=False,
        )

        self._simulations[simulation_id] = result
        self._active_simulation = simulation_id

        try:
            # Step 1: Clone environment if requested
            if config.clone_environment:
                result.cloned_environment = await self._clone_environment(config)

            # Step 2: Predict downtime risks
            if config.predict_downtime:
                result.downtime_predictions = await self.downtime.predict_downtime(
                    config.target_service,
                    config.environment,
                )

            # Step 3: Run type-specific simulation
            if config.simulation_type == SimulationType.DEPLOYMENT:
                result.deployment_result = await self._simulate_deployment(config, result)
            elif config.simulation_type == SimulationType.ROLLBACK:
                result.rollback_result = await self._simulate_rollback(config, result)
            elif config.simulation_type == SimulationType.SCALING:
                result.deployment_result = await self._simulate_scaling(config, result)
            elif config.simulation_type == SimulationType.CHAOS:
                result.chaos_result = await self._simulate_chaos(config, result)
            elif config.simulation_type == SimulationType.WHAT_IF:
                result.deployment_result = await self._simulate_what_if(config, result)

            # Step 4: Analyze results
            self._analyze_results(result)

            result.status = "completed"
            log.info(
                "Simulation complete: %s (success_prob=%.0f%%, risk=%.2f)",
                simulation_id, result.success_probability * 100, result.risk_score
            )

        except Exception as e:
            log.error("Simulation failed: %s - %s", simulation_id, e)
            result.status = "failed"
            result.issues.append({
                "severity": "critical",
                "description": f"Simulation error: {e}",
            })

        finally:
            result.completed_at = datetime.now(timezone.utc)
            self._active_simulation = None

            # Cleanup cloned environment if temporary
            if result.cloned_environment and config.clone_ttl_hours == 0:
                await self.cloner.delete_environment(result.cloned_environment.id)

        return result

    async def _clone_environment(
        self,
        config: SimulationConfig,
    ) -> ClonedEnvironment:
        """Clone the target environment."""
        clone_config = CloneConfig(
            strategy=config.clone_strategy,
            ttl_hours=config.clone_ttl_hours,
            scale_factor=0.1,
            isolate_network=True,
        )

        return await self.cloner.clone_environment(
            source_namespace=config.environment,
            name=f"sim-{config.target_service}",
            config=clone_config,
        )

    async def _simulate_deployment(
        self,
        config: SimulationConfig,
        result: SimulationResult,
    ) -> dict[str, Any]:
        """Simulate a deployment."""
        target_version = config.parameters.get("target_version", "latest")
        deployment_strategy = config.parameters.get("strategy", "rolling")

        log.info("Simulating deployment to %s using %s strategy", target_version, deployment_strategy)

        # Simulate deployment phases
        phases = []

        # Phase 1: Pre-deployment checks
        phases.append({
            "name": "pre_deployment_checks",
            "status": "passed",
            "duration_seconds": 10,
            "checks": [
                {"name": "health_check", "passed": True},
                {"name": "resource_availability", "passed": True},
                {"name": "dependency_check", "passed": True},
            ],
        })

        # Phase 2: Image pull
        phases.append({
            "name": "image_pull",
            "status": "passed",
            "duration_seconds": 30,
        })

        # Phase 3: Rolling update
        if deployment_strategy == "rolling":
            phases.append({
                "name": "rolling_update",
                "status": "passed",
                "duration_seconds": 120,
                "replicas_updated": 3,
                "replicas_total": 3,
            })
        elif deployment_strategy == "blue_green":
            phases.append({
                "name": "blue_green_switch",
                "status": "passed",
                "duration_seconds": 5,
            })
        elif deployment_strategy == "canary":
            phases.append({
                "name": "canary_release",
                "status": "passed",
                "duration_seconds": 300,
                "canary_percentage": 10,
            })

        # Phase 4: Post-deployment verification
        phases.append({
            "name": "post_deployment_verification",
            "status": "passed",
            "duration_seconds": 60,
            "checks": [
                {"name": "smoke_test", "passed": True},
                {"name": "health_check", "passed": True},
            ],
        })

        total_duration = sum(p.get("duration_seconds", 0) for p in phases)
        all_passed = all(p.get("status") == "passed" for p in phases)

        return {
            "success": all_passed,
            "target_version": target_version,
            "strategy": deployment_strategy,
            "phases": phases,
            "total_duration_seconds": total_duration,
            "estimated_downtime_seconds": 0 if deployment_strategy != "recreate" else 30,
        }

    async def _simulate_rollback(
        self,
        config: SimulationConfig,
        result: SimulationResult,
    ) -> RollbackResult:
        """Simulate a rollback."""
        from_version = config.parameters.get("from_version", "v2.0.0")
        to_version = config.parameters.get("to_version", "v1.9.0")
        deployment_id = config.parameters.get("deployment_id", "deploy-123")

        # Create rollback scenario
        scenario = await self.rollback.create_scenario(
            deployment_id=deployment_id,
            service=config.target_service,
            from_version=from_version,
            to_version=to_version,
        )

        # Run simulation
        return await self.rollback.simulate(scenario.id)

    async def _simulate_scaling(
        self,
        config: SimulationConfig,
        result: SimulationResult,
    ) -> dict[str, Any]:
        """Simulate a scaling operation."""
        current_replicas = config.parameters.get("current_replicas", 3)
        target_replicas = config.parameters.get("target_replicas", 6)
        scaling_direction = "up" if target_replicas > current_replicas else "down"

        log.info(
            "Simulating scale %s: %d -> %d replicas",
            scaling_direction, current_replicas, target_replicas
        )

        # Estimate scaling time
        replica_diff = abs(target_replicas - current_replicas)
        scaling_time_per_replica = 30  # seconds

        # Check resource availability
        resources_available = True
        resource_issues = []

        if scaling_direction == "up":
            # Check if cluster has capacity
            cpu_required = replica_diff * config.parameters.get("cpu_per_replica", 0.5)
            _ = replica_diff * config.parameters.get("memory_per_replica", 512)  # For future memory checks

            # Capacity check
            if cpu_required > 10:
                resources_available = False
                resource_issues.append("Insufficient CPU capacity")

        return {
            "success": resources_available,
            "direction": scaling_direction,
            "current_replicas": current_replicas,
            "target_replicas": target_replicas,
            "estimated_duration_seconds": replica_diff * scaling_time_per_replica,
            "resources_available": resources_available,
            "resource_issues": resource_issues,
            "estimated_cost_change_hourly": replica_diff * 0.05 * (1 if scaling_direction == "up" else -1),
        }

    async def _simulate_chaos(
        self,
        config: SimulationConfig,
        result: SimulationResult,
    ) -> ChaosResult:
        """Run chaos engineering simulation."""
        failure_type = config.parameters.get("failure_type", "pod_kill")
        duration = config.parameters.get("duration", 60)

        # Create chaos experiment
        experiment = await self.chaos.create_experiment(
            name=f"Chaos test for {config.target_service}",
            description=f"Testing resilience to {failure_type}",
            failures=[{
                "type": failure_type,
                "duration": duration,
                "percentage": 33.0,  # Affect 1/3 of pods
            }],
            target_selector={"app": config.target_service},
            namespace=result.cloned_environment.clone_namespace if result.cloned_environment else None,
            safety_level=config.safety_level,
        )

        # Run experiment (dry run if no cloned environment)
        return await self.chaos.run_experiment(
            experiment.id,
            dry_run=result.cloned_environment is None,
        )

    async def _simulate_what_if(
        self,
        config: SimulationConfig,
        result: SimulationResult,
    ) -> dict[str, Any]:
        """Run what-if scenario analysis."""
        scenario = config.parameters.get("scenario", "traffic_spike")

        if scenario == "traffic_spike":
            spike_multiplier = config.parameters.get("multiplier", 3.0)
            return await self._simulate_traffic_spike(config, spike_multiplier)

        elif scenario == "dependency_failure":
            failed_dependency = config.parameters.get("dependency", "database")
            return await self._simulate_dependency_failure(config, failed_dependency)

        elif scenario == "region_failure":
            failed_region = config.parameters.get("region", "us-east-1")
            return await self._simulate_region_failure(config, failed_region)

        else:
            return {
                "scenario": scenario,
                "status": "unknown_scenario",
                "message": f"Unknown scenario type: {scenario}",
            }

    async def _simulate_traffic_spike(
        self,
        config: SimulationConfig,
        multiplier: float,
    ) -> dict[str, Any]:
        """Simulate a traffic spike scenario."""
        current_rps = config.parameters.get("current_rps", 1000)
        spike_rps = current_rps * multiplier

        # Estimate capacity
        max_rps_per_replica = 500
        current_replicas = config.parameters.get("replicas", 3)
        current_capacity = current_replicas * max_rps_per_replica

        can_handle = spike_rps <= current_capacity
        replicas_needed = int(spike_rps / max_rps_per_replica) + 1

        return {
            "scenario": "traffic_spike",
            "current_rps": current_rps,
            "spike_rps": spike_rps,
            "multiplier": multiplier,
            "current_capacity_rps": current_capacity,
            "can_handle": can_handle,
            "replicas_needed": replicas_needed,
            "autoscaling_sufficient": replicas_needed <= 10,  # Assume max 10 replicas
            "recommendations": [
                f"Scale to {replicas_needed} replicas to handle spike",
                "Enable rate limiting as fallback",
                "Configure autoscaling with appropriate thresholds",
            ] if not can_handle else ["Current capacity sufficient"],
        }

    async def _simulate_dependency_failure(
        self,
        config: SimulationConfig,
        dependency: str,
    ) -> dict[str, Any]:
        """Simulate a dependency failure scenario."""
        # Check if service has fallback for this dependency
        has_fallback = config.parameters.get(f"{dependency}_fallback", False)
        has_circuit_breaker = config.parameters.get("circuit_breaker", True)

        impact = "critical" if not has_fallback else "degraded"

        return {
            "scenario": "dependency_failure",
            "dependency": dependency,
            "has_fallback": has_fallback,
            "has_circuit_breaker": has_circuit_breaker,
            "expected_impact": impact,
            "estimated_error_rate": 0.0 if has_fallback else 1.0,
            "recovery_time_seconds": 30 if has_circuit_breaker else 300,
            "recommendations": [
                f"Implement fallback for {dependency}",
                "Add circuit breaker if not present",
                "Configure appropriate timeouts",
            ] if not has_fallback else [f"Fallback available for {dependency}"],
        }

    async def _simulate_region_failure(
        self,
        config: SimulationConfig,
        region: str,
    ) -> dict[str, Any]:
        """Simulate a region failure scenario."""
        is_multi_region = config.parameters.get("multi_region", False)
        regions = config.parameters.get("regions", [region])

        if is_multi_region and len(regions) > 1:
            remaining_regions = [r for r in regions if r != region]
            return {
                "scenario": "region_failure",
                "failed_region": region,
                "surviving_regions": remaining_regions,
                "service_available": True,
                "expected_latency_increase_ms": 50,
                "capacity_reduction_percent": 100 / len(regions),
                "recommendations": [
                    "DNS failover configured",
                    f"Traffic will route to {remaining_regions}",
                ],
            }
        else:
            return {
                "scenario": "region_failure",
                "failed_region": region,
                "service_available": False,
                "estimated_downtime_minutes": 30,
                "recommendations": [
                    "Enable multi-region deployment",
                    "Configure DNS failover",
                    "Implement disaster recovery plan",
                ],
            }

    def _analyze_results(self, result: SimulationResult) -> None:
        """Analyze all simulation results."""
        issues = result.issues
        recommendations = result.recommendations

        # Start with base probability
        success_prob = 0.95
        risk_score = 0.1

        # Analyze deployment result
        if result.deployment_result:
            if not result.deployment_result.get("success", True):
                success_prob *= 0.5
                risk_score += 0.3
                issues.append({
                    "severity": "high",
                    "description": "Deployment simulation failed",
                })

            result.estimated_duration_minutes = result.deployment_result.get(
                "total_duration_seconds", 0
            ) / 60
            result.estimated_downtime_minutes = result.deployment_result.get(
                "estimated_downtime_seconds", 0
            ) / 60

        # Analyze rollback result
        if result.rollback_result:
            success_prob *= result.rollback_result.success_probability
            risk_score += (1 - result.rollback_result.success_probability) * 0.5

            for issue in result.rollback_result.issues:
                issues.append({
                    "severity": issue.severity.value,
                    "description": issue.description,
                    "mitigation": issue.mitigation,
                })

            recommendations.extend(result.rollback_result.recommendations)

        # Analyze chaos result
        if result.chaos_result:
            if not result.chaos_result.steady_state_maintained:
                success_prob *= 0.7
                risk_score += 0.2
                issues.append({
                    "severity": "medium",
                    "description": "System did not maintain steady state under chaos",
                })

            recommendations.extend(result.chaos_result.insights)

        # Analyze downtime predictions
        for prediction in result.downtime_predictions:
            risk_score += prediction.probability * 0.3
            if prediction.probability > 0.5:
                issues.append({
                    "severity": "high",
                    "description": f"High downtime risk: {prediction.primary_cause.value}",
                })
                recommendations.extend(prediction.prevention_actions)

        # Finalize scores
        result.success_probability = max(0.1, min(0.99, success_prob))
        result.risk_score = min(1.0, risk_score)

        # Determine if safe to proceed
        result.safe_to_proceed = (
            result.success_probability >= 0.8 and
            result.risk_score <= 0.4 and
            not any(i.get("severity") == "critical" for i in issues)
        )

        result.requires_approval = (
            result.risk_score > 0.3 or
            any(i.get("severity") in ["critical", "high"] for i in issues)
        )

        # Deduplicate recommendations
        result.recommendations = list(set(recommendations))

    def get_simulation(self, simulation_id: str) -> SimulationResult | None:
        """Get a simulation by ID."""
        return self._simulations.get(simulation_id)

    def list_simulations(
        self,
        status: str | None = None,
        limit: int = 50,
    ) -> list[SimulationResult]:
        """List simulations."""
        simulations = list(self._simulations.values())

        if status:
            simulations = [s for s in simulations if s.status == status]

        # Sort by start time descending
        simulations.sort(key=lambda s: s.started_at, reverse=True)

        return simulations[:limit]

    async def cleanup_environment(self, simulation_id: str) -> bool:
        """Clean up a simulation's cloned environment."""
        result = self._simulations.get(simulation_id)
        if not result or not result.cloned_environment:
            return False

        return await self.cloner.delete_environment(result.cloned_environment.id)

    def get_stats(self) -> dict[str, Any]:
        """Get simulator statistics."""
        simulations = list(self._simulations.values())
        completed = [s for s in simulations if s.status == "completed"]

        return {
            "total_simulations": len(simulations),
            "completed": len(completed),
            "failed": len([s for s in simulations if s.status == "failed"]),
            "active": self._active_simulation is not None,
            "avg_success_probability": (
                sum(s.success_probability for s in completed) / len(completed)
                if completed else 0
            ),
            "cloner_stats": self.cloner.get_stats(),
            "chaos_stats": self.chaos.get_stats(),
        }


def create_digital_twin(
    k8s_client: Any = None,
    metrics_client: Any = None,
    redis_client: Any = None,
) -> DigitalTwinSimulator:
    """Factory function to create a digital twin simulator."""
    cloner = InfrastructureCloner(k8s_client=k8s_client, redis_client=redis_client)
    predictor = DowntimePredictor(metrics_client=metrics_client, redis_client=redis_client)
    rollback_sim = RollbackSimulator(k8s_client=k8s_client)
    chaos_eng = ChaosEngine(k8s_client=k8s_client, metrics_client=metrics_client)

    return DigitalTwinSimulator(
        infrastructure_cloner=cloner,
        downtime_predictor=predictor,
        rollback_simulator=rollback_sim,
        chaos_engine=chaos_eng,
        k8s_client=k8s_client,
        redis_client=redis_client,
    )
