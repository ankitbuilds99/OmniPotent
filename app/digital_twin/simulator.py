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
log = logging.getLogger("omnipotent.digital_twin.simulator")


def build_k8s_client() -> Any | None:
    """The shared kubeconfig-or-None client builder, imported at call time.

    `app.coordinator` imports this package from its `__init__`, so importing
    the coordinator's builder at module scope is a cycle. Wrapping it keeps one
    definition of "how this deployment finds its cluster" rather than a second
    copy that could drift from the remediation handlers' one.
    """
    from app.coordinator.handlers.remediation import (  # local: breaks import cycle
        build_k8s_client as _build_k8s_client,
    )

    return _build_k8s_client()

#: Phase statuses that count towards a deployment's verdict. Anything else --
#: "projected" for a phase we can only forecast, "unverified" for one we could
#: not check -- is reported but never contributes a pass.
_CHECKED_STATUSES = frozenset({"passed", "failed"})


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

    #: True when no part of this run reached a real cluster, so the numbers
    #: below are a forecast rather than a measurement. `safe_to_proceed` is
    #: forced False in that case -- see `_analyze_results`.
    simulated: bool = False

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
            "simulated": self.simulated,
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
        self.k8s = k8s_client
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

    async def _read_deployment(self, config: SimulationConfig) -> tuple[Any | None, str | None]:
        """The live Deployment for this run's target, or why there isn't one.

        Returns `(deployment, None)` on a successful read and `(None, reason)`
        otherwise. A missing client and an API error are deliberately the same
        shape: both mean "this run verified nothing", and the caller must treat
        them identically rather than letting one of them pass.
        """
        if self.k8s is None:
            return None, "no Kubernetes client is configured (KUBE_CONFIG_PATH unset)"

        try:
            deployment = await self.k8s.read_namespaced_deployment(
                name=config.target_service,
                namespace=config.environment,
            )
        except Exception as e:
            log.warning(
                "Could not read deployment %s/%s: %s",
                config.environment, config.target_service, e,
            )
            return None, f"could not read deployment: {e}"

        return deployment, None

    @staticmethod
    def _replica_state(deployment: Any) -> dict[str, int]:
        """Spec and status replica counts, defaulting absent fields to 0.

        `ready_replicas` and friends are None on a Deployment that has never
        rolled out, and `None >= n` is a TypeError rather than a verdict.
        """
        spec = getattr(deployment, "spec", None)
        status = getattr(deployment, "status", None)
        return {
            "desired": int(getattr(spec, "replicas", 0) or 0),
            "ready": int(getattr(status, "ready_replicas", 0) or 0),
            "available": int(getattr(status, "available_replicas", 0) or 0),
            "unavailable": int(getattr(status, "unavailable_replicas", 0) or 0),
        }

    async def _simulate_deployment(
        self,
        config: SimulationConfig,
        result: SimulationResult,
    ) -> dict[str, Any]:
        """Check a deployment against the live cluster and project the rollout.

        This used to be a list of dict literals whose every `status` was the
        string "passed", so `all_passed` was True before the function ran and
        the `safe_to_proceed` gate in `DecisionIntegration.execute_dag` could
        not refuse anything -- for a degraded service, for a service that was
        not in the cluster, or on a host with no cluster at all.

        What is genuinely checkable before a rollout is the *current* state:
        that the Deployment exists and that its replicas are healthy enough to
        survive losing some to a rolling update. Those are the phases marked
        "passed"/"failed", and they decide `success`. The rollout itself has not
        happened yet, so its phase is marked "projected" and excluded from the
        verdict -- a forecast reported as a forecast. When nothing could be read
        every phase is "unverified" and `verified` is False, which
        `_analyze_results` turns into a closed gate.
        """
        target_version = config.parameters.get("target_version", "latest")
        deployment_strategy = config.parameters.get("strategy", "rolling")

        deployment, reason = await self._read_deployment(config)

        if deployment is None:
            log.warning(
                "Deployment simulation for %s is unverified: %s",
                config.target_service, reason,
            )
            return {
                "success": False,
                "verified": False,
                "reason": reason,
                "target_version": target_version,
                "strategy": deployment_strategy,
                "phases": [
                    {"name": "pre_deployment_checks", "status": "unverified", "reason": reason},
                    {"name": "rollout", "status": "unverified", "reason": reason},
                ],
                "total_duration_seconds": None,
                "estimated_downtime_seconds": None,
            }

        replicas = self._replica_state(deployment)
        log.info(
            "Simulating deployment of %s to %s using %s strategy (%d/%d replicas ready)",
            config.target_service, target_version, deployment_strategy,
            replicas["ready"], replicas["desired"],
        )

        # Real pre-flight: a rollout into an already-degraded Deployment is the
        # case this gate exists to catch, so it is a check, not a projection.
        fully_ready = replicas["desired"] > 0 and replicas["ready"] >= replicas["desired"]
        checks = [
            {
                "name": "deployment_exists",
                "passed": True,
                "detail": f"{config.environment}/{config.target_service}",
            },
            {
                "name": "replicas_ready",
                "passed": fully_ready,
                "detail": f"{replicas['ready']}/{replicas['desired']} ready",
            },
            {
                "name": "no_unavailable_replicas",
                "passed": replicas["unavailable"] == 0,
                "detail": f"{replicas['unavailable']} unavailable",
            },
        ]

        phases: list[dict[str, Any]] = [{
            "name": "pre_deployment_checks",
            "status": "passed" if all(c["passed"] for c in checks) else "failed",
            "checks": checks,
        }]

        # The rollout is a forecast. Its replica counts come from the live spec
        # rather than the literal 3 that used to be reported for every service,
        # and its time budget is the Deployment's own progressDeadlineSeconds --
        # a configured bound, not an invented one.
        deadline = getattr(getattr(deployment, "spec", None), "progress_deadline_seconds", None)
        rollout_phase = "rolling_update" if deployment_strategy == "rolling" else {
            "blue_green": "blue_green_switch",
            "canary": "canary_release",
            "recreate": "recreate",
        }.get(deployment_strategy, "rollout")

        projection: dict[str, Any] = {
            "name": rollout_phase,
            "status": "projected",
            "replicas_total": replicas["desired"],
            "replicas_updated": 0,
            "max_duration_seconds": int(deadline) if deadline else None,
        }
        if deployment_strategy == "canary":
            projection["canary_percentage"] = config.parameters.get("canary_percentage")
        phases.append(projection)

        checked = [p for p in phases if p.get("status") in _CHECKED_STATUSES]
        success = bool(checked) and all(p["status"] == "passed" for p in checked)

        return {
            "success": success,
            "verified": True,
            "target_version": target_version,
            "strategy": deployment_strategy,
            "current_replicas": replicas["desired"],
            "ready_replicas": replicas["ready"],
            "phases": phases,
            # The rollout has not run, so the only honest duration is the bound
            # the Deployment itself declares. None when it declares none.
            "total_duration_seconds": int(deadline) if deadline else None,
            # `recreate` tears down every pod before starting the new ones, so
            # its downtime is the rollout window; the others are designed not to
            # drop traffic, which is a property of the strategy, not a guess.
            "estimated_downtime_seconds": (
                (int(deadline) if deadline else None)
                if deployment_strategy == "recreate" else 0
            ),
        }

    async def _simulate_rollback(
        self,
        config: SimulationConfig,
        result: SimulationResult,
    ) -> RollbackResult:
        """Simulate a rollback.

        The versions and deployment id are required: they used to default to
        "v2.0.0"/"v1.9.0"/"deploy-123", so a caller who omitted them got a
        confident simulation of a rollback that does not exist. Raising here
        lands in `simulate()`'s except block, which marks the run failed with a
        critical issue — a closed gate, not an invented one.
        """
        missing = [
            key for key in ("deployment_id", "from_version", "to_version")
            if not config.parameters.get(key)
        ]
        if missing:
            raise ValueError(
                "rollback simulation requires parameters "
                f"{', '.join(missing)}; refusing to simulate a placeholder rollback"
            )
        from_version = config.parameters["from_version"]
        to_version = config.parameters["to_version"]
        deployment_id = config.parameters["deployment_id"]

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
        """Check a scaling operation against the cluster's real replicas and capacity.

        `current_replicas` used to default to 3 whatever the service actually
        ran, so the replica delta -- and therefore every number derived from it
        -- described a service that did not exist. The capacity check compared
        an invented CPU requirement against a literal 10, and the hourly cost
        was `replica_diff * 0.05`: a dollar figure with no pricing source behind
        it, which is why it is now reported as unavailable rather than made up.
        """
        deployment, reason = await self._read_deployment(config)

        current_replicas: int
        if deployment is not None:
            current_replicas = self._replica_state(deployment)["desired"]
        else:
            # Falling back to the caller's number keeps what-if scaling usable
            # without a cluster, but the result is marked unverified so it
            # cannot clear the gate on its own.
            requested = config.parameters.get("current_replicas")
            if requested is None:
                return {
                    "success": False,
                    "verified": False,
                    "reason": reason,
                    "resource_issues": [f"Current replica count is unknown: {reason}"],
                    "estimated_cost_change_hourly": None,
                }
            current_replicas = int(requested)

        target_replicas = config.parameters.get("target_replicas")
        if target_replicas is None:
            return {
                "success": False,
                "verified": deployment is not None,
                "reason": "no target_replicas given",
                "current_replicas": current_replicas,
                "resource_issues": ["No target replica count was requested"],
                "estimated_cost_change_hourly": None,
            }

        scaling_direction = "up" if target_replicas > current_replicas else "down"
        replica_diff = abs(target_replicas - current_replicas)

        log.info(
            "Simulating scale %s for %s: %d -> %d replicas",
            scaling_direction, config.target_service, current_replicas, target_replicas,
        )

        resource_issues: list[str] = []
        capacity = await self._cluster_cpu_headroom()

        if scaling_direction == "up" and capacity is not None:
            cpu_per_replica = self._cpu_request_per_replica(deployment)
            if cpu_per_replica is not None:
                cpu_required = replica_diff * cpu_per_replica
                if cpu_required > capacity:
                    resource_issues.append(
                        f"Insufficient CPU: {replica_diff} more replicas need "
                        f"{cpu_required:.2f} cores, cluster has {capacity:.2f} allocatable"
                    )

        return {
            "success": not resource_issues,
            "verified": deployment is not None,
            "reason": reason,
            "direction": scaling_direction,
            "current_replicas": current_replicas,
            "target_replicas": target_replicas,
            "resources_available": not resource_issues,
            "resource_issues": resource_issues,
            # No pricing source knows what a replica of this service costs, and
            # a fabricated rate in a cost field is worse than an absent one.
            "estimated_cost_change_hourly": None,
            "cost_unavailable_reason": "no infrastructure pricing source is configured",
        }

    async def _cluster_cpu_headroom(self) -> float | None:
        """Total allocatable CPU cores across nodes, or None if unreadable."""
        if self.k8s is None:
            return None
        try:
            nodes = await self.k8s.list_node()
        except Exception as e:
            log.warning("Could not list nodes for capacity check: %s", e)
            return None

        total = 0.0
        for node in getattr(nodes, "items", []) or []:
            allocatable = getattr(getattr(node, "status", None), "allocatable", None) or {}
            cores = self._parse_cpu(allocatable.get("cpu"))
            if cores is not None:
                total += cores
        return total or None

    @staticmethod
    def _parse_cpu(value: Any) -> float | None:
        """Kubernetes CPU quantity ("8", "500m", 1.5) as cores."""
        if value is None:
            return None
        text = str(value).strip()
        try:
            if text.endswith("m"):
                return float(text[:-1]) / 1000.0
            return float(text)
        except ValueError:
            return None

    @classmethod
    def _cpu_request_per_replica(cls, deployment: Any) -> float | None:
        """Summed CPU requests of one pod of this Deployment, if it declares any.

        A Deployment whose containers set no CPU request cannot be capacity
        checked, and assuming a request on its behalf is what the old
        `cpu_per_replica=0.5` default did.
        """
        spec = getattr(deployment, "spec", None)
        containers = getattr(
            getattr(getattr(spec, "template", None), "spec", None), "containers", None
        ) or []

        total = 0.0
        found = False
        for container in containers:
            requests = getattr(getattr(container, "resources", None), "requests", None) or {}
            cores = cls._parse_cpu(requests.get("cpu"))
            if cores is not None:
                total += cores
                found = True
        return total if found else None

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
            # Absent means unverified. The what-if scenarios below project from
            # request parameters and never read a cluster, so defaulting this to
            # True would let a pure forecast clear the gate the same way the
            # literal "passed" phases used to.
            unverified = not result.deployment_result.get("verified", False)
            result.simulated = unverified

            if unverified:
                # Not a failure -- an absence of evidence. It is reported as
                # critical because this result feeds an execution gate, and a
                # run that checked nothing must not read as a clean bill.
                issues.append({
                    "severity": "critical",
                    "description": (
                        "Deployment was not verified against a cluster: "
                        f"{result.deployment_result.get('reason') or 'unknown reason'}"
                    ),
                })
            elif not result.deployment_result.get("success", True):
                success_prob *= 0.5
                risk_score += 0.3
                issues.append({
                    "severity": "high",
                    "description": "Deployment pre-flight checks failed",
                })

            # None means "no bound was declared", which is not zero minutes.
            duration = result.deployment_result.get("total_duration_seconds")
            downtime = result.deployment_result.get("estimated_downtime_seconds")
            result.estimated_duration_minutes = duration / 60 if duration else 0.0
            result.estimated_downtime_minutes = downtime / 60 if downtime else 0.0

        # Analyze rollback result
        if result.rollback_result:
            # Rollback runs have no deployment_result, so without this they
            # skipped the branch above and came out `simulated=False,
            # safe_to_proceed=True` even when every state change was invented.
            # An unverified rollback feeds the same gate as an unverified
            # deployment; the critical "state is unverified" issue itself is
            # already in rollback_result.issues and is copied below.
            if not result.rollback_result.verified:
                result.simulated = True

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

        # Determine if safe to proceed. `simulated` is checked first and on its
        # own: a run that never reached a cluster has no evidence to clear a
        # gate with, whatever its scores happen to say.
        result.safe_to_proceed = (
            not result.simulated and
            result.success_probability >= 0.8 and
            result.risk_score <= 0.4 and
            not any(i.get("severity") == "critical" for i in issues)
        )

        result.requires_approval = (
            result.simulated or
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
    """Factory function to create a digital twin simulator.

    `k8s_client=None` means "work it out from settings", not "simulate" -- the
    same contract as `create_remediation_handlers`. It was previously taken
    literally, and since `DecisionIntegration` calls this with no arguments,
    there was no setting or request field that could give the twin a cluster:
    every simulation behind the remediation gate ran blind.
    """
    if k8s_client is None:
        k8s_client = build_k8s_client()

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
