"""Digital Twin / Simulation API.

Provides endpoints for:
- Infrastructure cloning
- Chaos engineering experiments
- Downtime prediction
- Rollback simulation
- What-if analysis
"""

from __future__ import annotations

import logging
from typing import Any
from fastapi import APIRouter, HTTPException, Depends, Query, BackgroundTasks
from pydantic import BaseModel, Field

from app.digital_twin import (
    DigitalTwinSimulator,
    InfrastructureCloner,
    DowntimePredictor,
    RollbackSimulator,
    ChaosEngine,
    CloneConfig,
    CloneStrategy,
    SimulationConfig,
    create_digital_twin,
)
from app.digital_twin.simulator import SimulationType
from app.digital_twin.chaos_engine import SafetyLevel

log = logging.getLogger("devcopilot.api.digital_twin")

router = APIRouter(prefix="/digital-twin", tags=["digital-twin"])

# Singleton instances
_digital_twin: DigitalTwinSimulator | None = None
_cloner: InfrastructureCloner | None = None
_downtime_predictor: DowntimePredictor | None = None
_rollback_simulator: RollbackSimulator | None = None
_chaos_engine: ChaosEngine | None = None


def get_digital_twin() -> DigitalTwinSimulator:
    global _digital_twin
    if _digital_twin is None:
        _digital_twin = create_digital_twin()
    return _digital_twin


def get_cloner() -> InfrastructureCloner:
    global _cloner
    if _cloner is None:
        _cloner = InfrastructureCloner()
    return _cloner


def get_downtime_predictor() -> DowntimePredictor:
    global _downtime_predictor
    if _downtime_predictor is None:
        _downtime_predictor = DowntimePredictor()
    return _downtime_predictor


def get_rollback_simulator() -> RollbackSimulator:
    global _rollback_simulator
    if _rollback_simulator is None:
        _rollback_simulator = RollbackSimulator()
    return _rollback_simulator


def get_chaos_engine() -> ChaosEngine:
    global _chaos_engine
    if _chaos_engine is None:
        _chaos_engine = ChaosEngine()
    return _chaos_engine


# Request/Response Models
class CloneRequest(BaseModel):
    source_namespace: str = Field(..., description="Source namespace to clone")
    name: str | None = Field(None, description="Name for the cloned environment")
    strategy: str = Field("minimal", description="Clone strategy: full, minimal, schema_only, snapshot, synthetic")
    ttl_hours: int = Field(4, ge=1, le=24, description="Time-to-live in hours")
    scale_factor: float = Field(0.1, ge=0.01, le=1.0, description="Scale factor for replicas")
    include_data: bool = Field(False, description="Include data in clone")
    isolate_network: bool = Field(True, description="Isolate network")
    max_resources: int = Field(50, ge=1, le=200)
    max_cost_per_hour: float = Field(10.0, ge=0.1)


class CloneResponse(BaseModel):
    id: str
    name: str
    source_namespace: str
    clone_namespace: str
    status: str
    resources_count: int
    endpoints: dict[str, str]
    estimated_cost_per_hour: float | None
    expires_at: str | None
    created_at: str


class DowntimePredictionRequest(BaseModel):
    service: str = Field(..., description="Service name to analyze")
    environment: str = Field("production", description="Target environment")
    horizon_hours: int = Field(24, ge=1, le=168, description="Prediction horizon in hours")


class RiskFactorResponse(BaseModel):
    name: str
    cause: str
    contribution: float
    current_value: str | float
    threshold: str | float
    trend: str
    time_to_breach_hours: float | None
    remediation: str


class DowntimePredictionResponse(BaseModel):
    id: str
    service: str
    environment: str
    probability: float
    risk_level: str
    primary_cause: str
    risk_factors: list[RiskFactorResponse]
    estimated_impact_minutes: float
    estimated_affected_users: int
    preventable: bool
    prevention_actions: list[str]
    predicted_start: str
    predicted_at: str


class MaintenanceWindowRequest(BaseModel):
    service: str = Field(..., description="Service to maintain")
    duration_hours: float = Field(2.0, ge=0.5, le=8.0, description="Required duration")
    horizon_days: int = Field(7, ge=1, le=30, description="Days to look ahead")
    count: int = Field(3, ge=1, le=10, description="Number of suggestions")


class MaintenanceWindowResponse(BaseModel):
    start_time: str
    end_time: str
    duration_hours: float
    score: float
    impact_score: float
    factors: dict[str, Any]


class RollbackScenarioRequest(BaseModel):
    deployment_id: str = Field(..., description="Deployment to rollback")
    service: str = Field(..., description="Service name")
    from_version: str = Field(..., description="Current version")
    to_version: str = Field(..., description="Target version")
    state_changes: list[dict[str, Any]] | None = Field(None, description="State changes to rollback")


class RollbackScenarioResponse(BaseModel):
    id: str
    name: str
    deployment_id: str
    service: str
    from_version: str
    to_version: str
    strategy: str
    steps_count: int
    created_at: str


class RollbackSimulationResponse(BaseModel):
    scenario_id: str
    success_probability: float
    risk_level: str
    estimated_duration_minutes: float
    estimated_downtime_minutes: float
    can_proceed: bool
    requires_approval: bool
    issues: list[dict[str, Any]]
    recommendations: list[str]
    simulated_at: str


class ChaosExperimentRequest(BaseModel):
    name: str = Field(..., description="Experiment name")
    description: str = Field("", description="Experiment description")
    failures: list[dict[str, Any]] = Field(..., description="List of failures to inject")
    target_selector: dict[str, str] = Field(..., description="Labels to select targets")
    namespace: str | None = Field(None, description="Target namespace (None = digital twin only)")
    safety_level: str = Field("safe", description="Safety level: safe, cautious, moderate, aggressive")
    max_duration_seconds: int = Field(300, ge=60, le=3600)
    steady_state: dict[str, Any] | None = Field(None, description="Steady state hypothesis")


class ChaosExperimentResponse(BaseModel):
    id: str
    name: str
    description: str
    safety_level: str
    status: str
    targets_count: int
    failures_count: int
    created_at: str


class ChaosResultResponse(BaseModel):
    experiment_id: str
    status: str
    duration_seconds: float
    steady_state_maintained: bool
    affected_targets_count: int
    events: list[dict[str, Any]]
    errors: list[str]
    insights: list[str]


class SimulationRequest(BaseModel):
    simulation_type: str = Field(..., description="Type: deployment, rollback, scaling, chaos, what_if")
    target_service: str = Field(..., description="Service to simulate")
    environment: str = Field("production", description="Target environment")
    clone_environment: bool = Field(True, description="Clone environment for safe testing")
    clone_strategy: str = Field("minimal")
    clone_ttl_hours: int = Field(2, ge=1, le=8)
    parameters: dict[str, Any] = Field(default_factory=dict, description="Simulation parameters")
    safety_level: str = Field("safe")
    max_duration_seconds: int = Field(600, ge=60, le=3600)
    predict_downtime: bool = Field(True)
    run_chaos: bool = Field(False)


class SimulationResultResponse(BaseModel):
    id: str
    simulation_type: str
    target_service: str
    status: str
    success_probability: float
    risk_score: float
    estimated_duration_minutes: float
    estimated_downtime_minutes: float
    safe_to_proceed: bool
    requires_approval: bool
    issues: list[dict[str, Any]]
    recommendations: list[str]
    started_at: str
    completed_at: str | None


class CapacityForecastRequest(BaseModel):
    service: str = Field(..., description="Service to analyze")
    resource_type: str = Field(..., description="Resource type: cpu, memory, disk, connections")
    horizon_days: int = Field(30, ge=7, le=90)


class CapacityForecastResponse(BaseModel):
    resource_type: str
    current_usage: float
    breach_time: str | None
    recommendations: list[str]
    forecasted_usage: list[dict[str, Any]]


# Endpoints - Infrastructure Cloning
@router.post("/clone", response_model=CloneResponse)
async def clone_environment(
    request: CloneRequest,
    background_tasks: BackgroundTasks,
    cloner: InfrastructureCloner = Depends(get_cloner),
) -> CloneResponse:
    """Clone an infrastructure environment for safe testing."""
    try:
        strategy_map = {
            "full": CloneStrategy.FULL,
            "minimal": CloneStrategy.MINIMAL,
            "schema_only": CloneStrategy.SCHEMA_ONLY,
            "snapshot": CloneStrategy.SNAPSHOT,
            "synthetic": CloneStrategy.SYNTHETIC,
        }

        config = CloneConfig(
            strategy=strategy_map.get(request.strategy, CloneStrategy.MINIMAL),
            ttl_hours=request.ttl_hours,
            scale_factor=request.scale_factor,
            include_data=request.include_data,
            isolate_network=request.isolate_network,
            max_resources=request.max_resources,
            max_cost_per_hour=request.max_cost_per_hour,
        )

        environment = await cloner.clone_environment(
            source_namespace=request.source_namespace,
            name=request.name,
            config=config,
        )

        return CloneResponse(
            id=environment.id,
            name=environment.name,
            source_namespace=environment.source_namespace,
            clone_namespace=environment.clone_namespace,
            status=environment.status,
            resources_count=len(environment.resources),
            endpoints=environment.endpoints,
            estimated_cost_per_hour=environment.metadata.get("estimated_cost_per_hour"),
            expires_at=environment.expires_at.isoformat() if environment.expires_at else None,
            created_at=environment.created_at.isoformat(),
        )

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        log.error("Clone failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Clone failed: {str(e)}")


@router.get("/clone")
async def list_cloned_environments(
    status: str | None = Query(None),
    include_expired: bool = Query(False),
    cloner: InfrastructureCloner = Depends(get_cloner),
) -> dict[str, Any]:
    """List cloned environments."""
    environments = cloner.list_environments(status=status, include_expired=include_expired)
    return {
        "environments": [e.to_dict() for e in environments],
        "count": len(environments),
    }


@router.get("/clone/{env_id}")
async def get_cloned_environment(
    env_id: str,
    cloner: InfrastructureCloner = Depends(get_cloner),
) -> dict[str, Any]:
    """Get a specific cloned environment."""
    env = cloner.get_environment(env_id)
    if not env:
        raise HTTPException(status_code=404, detail="Environment not found")
    return env.to_dict()


@router.delete("/clone/{env_id}")
async def delete_cloned_environment(
    env_id: str,
    force: bool = Query(False),
    cloner: InfrastructureCloner = Depends(get_cloner),
) -> dict[str, str]:
    """Delete a cloned environment."""
    if await cloner.delete_environment(env_id, force=force):
        return {"status": "deleted", "environment_id": env_id}
    raise HTTPException(status_code=404, detail="Environment not found")


@router.post("/clone/cleanup")
async def cleanup_expired_environments(
    cloner: InfrastructureCloner = Depends(get_cloner),
) -> dict[str, int]:
    """Cleanup all expired cloned environments."""
    count = await cloner.cleanup_expired()
    return {"cleaned_up": count}


# Endpoints - Downtime Prediction
@router.post("/predict-downtime", response_model=list[DowntimePredictionResponse])
async def predict_downtime(
    request: DowntimePredictionRequest,
    predictor: DowntimePredictor = Depends(get_downtime_predictor),
) -> list[DowntimePredictionResponse]:
    """Predict potential downtime for a service."""
    try:
        predictions = await predictor.predict_downtime(
            service=request.service,
            environment=request.environment,
            horizon_hours=request.horizon_hours,
        )

        return [
            DowntimePredictionResponse(
                id=p.id,
                service=p.service,
                environment=p.environment,
                probability=p.probability,
                risk_level=p.risk_level.value,
                primary_cause=p.primary_cause.value,
                risk_factors=[
                    RiskFactorResponse(
                        name=f.name,
                        cause=f.cause.value,
                        contribution=f.contribution,
                        current_value=f.current_value,
                        threshold=f.threshold,
                        trend=f.trend,
                        time_to_breach_hours=f.time_to_breach.total_seconds() / 3600 if f.time_to_breach else None,
                        remediation=f.remediation,
                    )
                    for f in p.risk_factors
                ],
                estimated_impact_minutes=p.estimated_impact_minutes,
                estimated_affected_users=p.estimated_affected_users,
                preventable=p.preventable,
                prevention_actions=p.prevention_actions,
                predicted_start=p.predicted_start.isoformat(),
                predicted_at=p.predicted_at.isoformat(),
            )
            for p in predictions
        ]

    except Exception as e:
        log.error("Prediction failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Prediction failed: {str(e)}")


@router.post("/maintenance-windows", response_model=list[MaintenanceWindowResponse])
async def suggest_maintenance_windows(
    request: MaintenanceWindowRequest,
    predictor: DowntimePredictor = Depends(get_downtime_predictor),
) -> list[MaintenanceWindowResponse]:
    """Suggest optimal maintenance windows."""
    try:
        windows = await predictor.suggest_maintenance_windows(
            service=request.service,
            duration_hours=request.duration_hours,
            horizon_days=request.horizon_days,
            count=request.count,
        )

        return [
            MaintenanceWindowResponse(
                start_time=w.start_time.isoformat(),
                end_time=w.end_time.isoformat(),
                duration_hours=(w.end_time - w.start_time).total_seconds() / 3600,
                score=w.score,
                impact_score=w.impact_score,
                factors=w.factors,
            )
            for w in windows
        ]

    except Exception as e:
        log.error("Maintenance window suggestion failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/capacity-forecast", response_model=CapacityForecastResponse)
async def forecast_capacity(
    request: CapacityForecastRequest,
    predictor: DowntimePredictor = Depends(get_downtime_predictor),
) -> CapacityForecastResponse:
    """Forecast capacity usage for a service."""
    try:
        forecast = await predictor.forecast_capacity(
            service=request.service,
            resource_type=request.resource_type,
            horizon_days=request.horizon_days,
        )

        return CapacityForecastResponse(
            resource_type=forecast.resource_type,
            current_usage=forecast.current_usage,
            breach_time=forecast.breach_time.isoformat() if forecast.breach_time else None,
            recommendations=forecast.recommendations,
            forecasted_usage=[
                {"time": t.isoformat(), "usage": v}
                for t, v in forecast.forecasted_usage
            ],
        )

    except Exception as e:
        log.error("Capacity forecast failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# Endpoints - Rollback Simulation
@router.post("/rollback/scenario", response_model=RollbackScenarioResponse)
async def create_rollback_scenario(
    request: RollbackScenarioRequest,
    simulator: RollbackSimulator = Depends(get_rollback_simulator),
) -> RollbackScenarioResponse:
    """Create a rollback scenario for simulation."""
    try:
        scenario = await simulator.create_scenario(
            deployment_id=request.deployment_id,
            service=request.service,
            from_version=request.from_version,
            to_version=request.to_version,
            state_changes=request.state_changes,
        )

        return RollbackScenarioResponse(
            id=scenario.id,
            name=scenario.name,
            deployment_id=scenario.deployment_id,
            service=scenario.service,
            from_version=scenario.from_version,
            to_version=scenario.to_version,
            strategy=scenario.strategy.value,
            steps_count=len(scenario.steps),
            created_at=scenario.created_at.isoformat(),
        )

    except Exception as e:
        log.error("Scenario creation failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/rollback/simulate/{scenario_id}", response_model=RollbackSimulationResponse)
async def simulate_rollback(
    scenario_id: str,
    simulator: RollbackSimulator = Depends(get_rollback_simulator),
) -> RollbackSimulationResponse:
    """Simulate a rollback scenario."""
    try:
        result = await simulator.simulate(scenario_id)

        return RollbackSimulationResponse(
            scenario_id=scenario_id,
            success_probability=result.success_probability,
            risk_level=result.risk_level.value,
            estimated_duration_minutes=result.estimated_duration.total_seconds() / 60,
            estimated_downtime_minutes=result.estimated_downtime.total_seconds() / 60,
            can_proceed=result.can_proceed,
            requires_approval=result.requires_approval,
            issues=[i.to_dict() for i in result.issues],
            recommendations=result.recommendations,
            simulated_at=result.simulated_at.isoformat(),
        )

    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        log.error("Simulation failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/rollback/scenarios")
async def list_rollback_scenarios(
    simulator: RollbackSimulator = Depends(get_rollback_simulator),
) -> dict[str, Any]:
    """List all rollback scenarios."""
    scenarios = list(simulator._scenarios.values())
    return {
        "scenarios": [s.to_dict() for s in scenarios],
        "count": len(scenarios),
    }


# Endpoints - Chaos Engineering
@router.post("/chaos/experiment", response_model=ChaosExperimentResponse)
async def create_chaos_experiment(
    request: ChaosExperimentRequest,
    chaos: ChaosEngine = Depends(get_chaos_engine),
) -> ChaosExperimentResponse:
    """Create a chaos engineering experiment."""
    try:
        safety_map = {
            "safe": SafetyLevel.SAFE,
            "cautious": SafetyLevel.CAUTIOUS,
            "moderate": SafetyLevel.MODERATE,
            "aggressive": SafetyLevel.AGGRESSIVE,
        }

        experiment = await chaos.create_experiment(
            name=request.name,
            description=request.description,
            failures=request.failures,
            target_selector=request.target_selector,
            namespace=request.namespace,
            safety_level=safety_map.get(request.safety_level, SafetyLevel.SAFE),
            max_duration=request.max_duration_seconds,
            steady_state=request.steady_state,
        )

        return ChaosExperimentResponse(
            id=experiment.id,
            name=experiment.name,
            description=experiment.description,
            safety_level=experiment.safety_level.value,
            status=experiment.status.value,
            targets_count=len(experiment.targets),
            failures_count=len(experiment.failures),
            created_at=experiment.created_at.isoformat(),
        )

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        log.error("Experiment creation failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/chaos/run/{experiment_id}", response_model=ChaosResultResponse)
async def run_chaos_experiment(
    experiment_id: str,
    dry_run: bool = Query(False),
    chaos: ChaosEngine = Depends(get_chaos_engine),
) -> ChaosResultResponse:
    """Run a chaos experiment."""
    try:
        result = await chaos.run_experiment(experiment_id, dry_run=dry_run)

        return ChaosResultResponse(
            experiment_id=experiment_id,
            status=result.status.value,
            duration_seconds=result.duration_seconds,
            steady_state_maintained=result.steady_state_maintained,
            affected_targets_count=len(result.affected_targets),
            events=result.events,
            errors=result.errors,
            insights=result.insights,
        )

    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        log.error("Experiment execution failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/chaos/abort/{experiment_id}")
async def abort_chaos_experiment(
    experiment_id: str,
    chaos: ChaosEngine = Depends(get_chaos_engine),
) -> dict[str, str]:
    """Abort a running chaos experiment."""
    if await chaos.abort_experiment(experiment_id):
        return {"status": "aborted", "experiment_id": experiment_id}
    raise HTTPException(status_code=404, detail="Experiment not found or not running")


@router.get("/chaos/experiments")
async def list_chaos_experiments(
    chaos: ChaosEngine = Depends(get_chaos_engine),
) -> dict[str, Any]:
    """List all chaos experiments."""
    experiments = list(chaos._experiments.values())
    return {
        "experiments": [e.to_dict() for e in experiments],
        "count": len(experiments),
    }


@router.get("/chaos/failure-types")
async def list_failure_types() -> dict[str, Any]:
    """List available failure types for chaos experiments."""
    return {
        "failure_types": [
            {"id": "network_latency", "description": "Inject network latency"},
            {"id": "network_partition", "description": "Create network partition"},
            {"id": "packet_loss", "description": "Cause packet loss"},
            {"id": "dns_failure", "description": "DNS resolution failure"},
            {"id": "cpu_stress", "description": "CPU stress test"},
            {"id": "memory_stress", "description": "Memory stress test"},
            {"id": "disk_stress", "description": "Disk I/O stress"},
            {"id": "disk_full", "description": "Simulate full disk"},
            {"id": "pod_kill", "description": "Kill pods"},
            {"id": "container_kill", "description": "Kill containers"},
            {"id": "service_unavailable", "description": "Make service unavailable"},
            {"id": "database_failure", "description": "Database connection failure"},
            {"id": "cache_failure", "description": "Cache service failure"},
            {"id": "queue_failure", "description": "Message queue failure"},
        ]
    }


# Endpoints - Full Simulation
@router.post("/simulate", response_model=SimulationResultResponse)
async def run_simulation(
    request: SimulationRequest,
    twin: DigitalTwinSimulator = Depends(get_digital_twin),
) -> SimulationResultResponse:
    """Run a complete simulation combining all digital twin capabilities."""
    try:
        type_map = {
            "deployment": SimulationType.DEPLOYMENT,
            "rollback": SimulationType.ROLLBACK,
            "scaling": SimulationType.SCALING,
            "chaos": SimulationType.CHAOS,
            "what_if": SimulationType.WHAT_IF,
        }
        strategy_map = {
            "full": CloneStrategy.FULL,
            "minimal": CloneStrategy.MINIMAL,
            "schema_only": CloneStrategy.SCHEMA_ONLY,
        }
        safety_map = {
            "safe": SafetyLevel.SAFE,
            "cautious": SafetyLevel.CAUTIOUS,
            "moderate": SafetyLevel.MODERATE,
        }

        config = SimulationConfig(
            simulation_type=type_map.get(request.simulation_type, SimulationType.DEPLOYMENT),
            target_service=request.target_service,
            environment=request.environment,
            clone_environment=request.clone_environment,
            clone_strategy=strategy_map.get(request.clone_strategy, CloneStrategy.MINIMAL),
            clone_ttl_hours=request.clone_ttl_hours,
            parameters=request.parameters,
            safety_level=safety_map.get(request.safety_level, SafetyLevel.SAFE),
            max_duration_seconds=request.max_duration_seconds,
            predict_downtime=request.predict_downtime,
            run_chaos=request.run_chaos,
        )

        result = await twin.simulate(config)

        return SimulationResultResponse(
            id=result.id,
            simulation_type=result.config.simulation_type.value,
            target_service=result.config.target_service,
            status=result.status,
            success_probability=result.success_probability,
            risk_score=result.risk_score,
            estimated_duration_minutes=result.estimated_duration_minutes,
            estimated_downtime_minutes=result.estimated_downtime_minutes,
            safe_to_proceed=result.safe_to_proceed,
            requires_approval=result.requires_approval,
            issues=result.issues,
            recommendations=result.recommendations,
            started_at=result.started_at.isoformat(),
            completed_at=result.completed_at.isoformat() if result.completed_at else None,
        )

    except Exception as e:
        log.error("Simulation failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Simulation failed: {str(e)}")


@router.get("/simulate/{simulation_id}")
async def get_simulation(
    simulation_id: str,
    twin: DigitalTwinSimulator = Depends(get_digital_twin),
) -> dict[str, Any]:
    """Get a specific simulation result."""
    result = twin.get_simulation(simulation_id)
    if not result:
        raise HTTPException(status_code=404, detail="Simulation not found")
    return result.to_dict()


@router.get("/simulations")
async def list_simulations(
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    twin: DigitalTwinSimulator = Depends(get_digital_twin),
) -> dict[str, Any]:
    """List all simulations."""
    simulations = twin.list_simulations(status=status, limit=limit)
    return {
        "simulations": [s.to_dict() for s in simulations],
        "count": len(simulations),
    }


@router.get("/simulation-types")
async def list_simulation_types() -> dict[str, Any]:
    """List available simulation types."""
    return {
        "types": [
            {
                "id": "deployment",
                "description": "Simulate a deployment including pre-checks, rolling update, and verification",
            },
            {
                "id": "rollback",
                "description": "Simulate a rollback with state change analysis and risk assessment",
            },
            {
                "id": "scaling",
                "description": "Simulate horizontal scaling with resource availability check",
            },
            {
                "id": "chaos",
                "description": "Run chaos engineering experiments in isolated environment",
            },
            {
                "id": "what_if",
                "description": "What-if analysis for traffic spikes, dependency failures, region failures",
            },
        ]
    }
