"""Tests for the Digital Twin / Simulation module."""

import pytest
from datetime import datetime, timedelta, timezone

from app.digital_twin import (
    DigitalTwinSimulator,
    InfrastructureCloner,
    DowntimePredictor,
    RollbackSimulator,
    ChaosEngine,
    CloneConfig,
    CloneStrategy,
    DowntimeRisk,
    RollbackStrategy,
    FailureType,
    SimulationConfig,
    create_digital_twin,
)
from app.digital_twin.simulator import SimulationType
from app.digital_twin.chaos_engine import SafetyLevel, ExperimentStatus


class TestInfrastructureCloner:
    """Tests for Infrastructure Cloner."""

    @pytest.fixture
    def cloner(self):
        return InfrastructureCloner()

    @pytest.mark.asyncio
    async def test_clone_environment_basic(self, cloner):
        """Test basic environment cloning."""
        env = await cloner.clone_environment(
            source_namespace="production",
            name="test-clone",
        )

        assert env.id is not None
        assert env.name == "test-clone"
        assert env.source_namespace == "production"
        assert env.status in ["creating", "ready", "degraded"]

    @pytest.mark.asyncio
    async def test_clone_with_config(self, cloner):
        """Test cloning with custom configuration."""
        config = CloneConfig(
            strategy=CloneStrategy.MINIMAL,
            ttl_hours=2,
            scale_factor=0.1,
            isolate_network=True,
            max_resources=20,
        )

        env = await cloner.clone_environment(
            source_namespace="staging",
            config=config,
        )

        assert env.config.strategy == CloneStrategy.MINIMAL
        assert env.config.scale_factor == 0.1

    @pytest.mark.asyncio
    async def test_clone_environment_has_endpoints(self, cloner):
        """Test that cloned environment has endpoints."""
        env = await cloner.clone_environment(
            source_namespace="production",
        )

        assert isinstance(env.endpoints, dict)

    @pytest.mark.asyncio
    async def test_delete_environment(self, cloner):
        """Test deleting a cloned environment."""
        env = await cloner.clone_environment(
            source_namespace="test",
        )

        result = await cloner.delete_environment(env.id)

        assert result is True
        assert cloner.get_environment(env.id) is None

    def test_list_environments(self, cloner):
        """Test listing environments."""
        envs = cloner.list_environments()
        assert isinstance(envs, list)

    @pytest.mark.asyncio
    async def test_cleanup_expired(self, cloner):
        """Test cleanup of expired environments."""
        count = await cloner.cleanup_expired()
        assert isinstance(count, int)


class TestDowntimePredictor:
    """Tests for Downtime Predictor."""

    @pytest.fixture
    def predictor(self):
        return DowntimePredictor()

    @pytest.mark.asyncio
    async def test_predict_downtime_basic(self, predictor):
        """Test basic downtime prediction."""
        predictions = await predictor.predict_downtime(
            service="api-gateway",
            environment="production",
            horizon_hours=24,
        )

        assert isinstance(predictions, list)
        for pred in predictions:
            assert 0 <= pred.probability <= 1
            assert pred.risk_level in list(DowntimeRisk)

    @pytest.mark.asyncio
    async def test_predict_downtime_returns_factors(self, predictor):
        """Test that predictions include risk factors."""
        predictions = await predictor.predict_downtime(
            service="database",
            horizon_hours=48,
        )

        for pred in predictions:
            assert isinstance(pred.risk_factors, list)
            for factor in pred.risk_factors:
                assert hasattr(factor, 'name')
                assert hasattr(factor, 'contribution')

    @pytest.mark.asyncio
    async def test_suggest_maintenance_windows(self, predictor):
        """Test maintenance window suggestions."""
        windows = await predictor.suggest_maintenance_windows(
            service="api-service",
            duration_hours=2.0,
            horizon_days=7,
            count=3,
        )

        assert len(windows) <= 3
        for window in windows:
            assert 0 <= window.score <= 1
            assert window.start_time < window.end_time

    @pytest.mark.asyncio
    async def test_forecast_capacity(self, predictor):
        """Test capacity forecasting."""
        forecast = await predictor.forecast_capacity(
            service="api-service",
            resource_type="cpu",
            horizon_days=30,
        )

        assert forecast.resource_type == "cpu"
        assert 0 <= forecast.current_usage <= 1
        assert len(forecast.forecasted_usage) > 0


class TestRollbackSimulator:
    """Tests for Rollback Simulator."""

    @pytest.fixture
    def simulator(self):
        return RollbackSimulator()

    @pytest.mark.asyncio
    async def test_create_scenario(self, simulator):
        """Test creating a rollback scenario."""
        scenario = await simulator.create_scenario(
            deployment_id="deploy-123",
            service="api-service",
            from_version="v2.0.0",
            to_version="v1.9.0",
        )

        assert scenario.id is not None
        assert scenario.service == "api-service"
        assert scenario.from_version == "v2.0.0"
        assert scenario.to_version == "v1.9.0"
        assert len(scenario.steps) > 0

    @pytest.mark.asyncio
    async def test_simulate_rollback(self, simulator):
        """Test simulating a rollback."""
        scenario = await simulator.create_scenario(
            deployment_id="deploy-456",
            service="backend",
            from_version="v3.0.0",
            to_version="v2.9.0",
        )

        result = await simulator.simulate(scenario.id)

        assert 0 <= result.success_probability <= 1
        assert result.estimated_duration.total_seconds() > 0
        assert isinstance(result.issues, list)
        assert isinstance(result.recommendations, list)

    @pytest.mark.asyncio
    async def test_simulate_with_state_changes(self, simulator):
        """Test simulation with explicit state changes."""
        scenario = await simulator.create_scenario(
            deployment_id="deploy-789",
            service="data-service",
            from_version="v1.1.0",
            to_version="v1.0.0",
            state_changes=[
                {
                    "resource_type": "database_schema",
                    "resource_id": "migration_001",
                    "change_type": "update",
                    "reversible": True,
                    "complexity": 5,
                },
            ],
        )

        result = await simulator.simulate(scenario.id)

        assert scenario.strategy in list(RollbackStrategy)

    @pytest.mark.asyncio
    async def test_simulate_irreversible_changes(self, simulator):
        """Test simulation with irreversible changes."""
        scenario = await simulator.create_scenario(
            deployment_id="deploy-danger",
            service="critical-service",
            from_version="v2.0.0",
            to_version="v1.0.0",
            state_changes=[
                {
                    "resource_type": "database_data",
                    "resource_id": "data_migration",
                    "change_type": "delete",
                    "reversible": False,
                    "complexity": 9,
                },
            ],
        )

        result = await simulator.simulate(scenario.id)

        assert result.success_probability < 1.0
        assert any(i.blocking for i in result.issues) or result.requires_approval


class TestChaosEngine:
    """Tests for Chaos Engine."""

    @pytest.fixture
    def chaos(self):
        return ChaosEngine()

    @pytest.mark.asyncio
    async def test_create_experiment(self, chaos):
        """Test creating a chaos experiment."""
        experiment = await chaos.create_experiment(
            name="Pod Kill Test",
            description="Test resilience to pod failures",
            failures=[
                {
                    "type": "pod_kill",
                    "duration": 30,
                    "percentage": 33.0,
                }
            ],
            target_selector={"app": "api-service"},
            safety_level=SafetyLevel.SAFE,
        )

        assert experiment.id is not None
        assert experiment.name == "Pod Kill Test"
        assert experiment.safety_level == SafetyLevel.SAFE
        assert len(experiment.failures) == 1

    @pytest.mark.asyncio
    async def test_run_experiment_dry_run(self, chaos):
        """Test running experiment in dry-run mode."""
        experiment = await chaos.create_experiment(
            name="Network Latency Test",
            description="Test latency handling",
            failures=[
                {
                    "type": "network_latency",
                    "duration": 60,
                    "percentage": 30.0,
                    "parameters": {"latency_ms": 500},
                }
            ],
            target_selector={"app": "backend"},
            safety_level=SafetyLevel.SAFE,
        )

        result = await chaos.run_experiment(experiment.id, dry_run=True)

        assert result.status in list(ExperimentStatus)
        assert result.duration_seconds >= 0

    @pytest.mark.asyncio
    async def test_create_experiment_with_steady_state(self, chaos):
        """Test experiment with steady state hypothesis."""
        experiment = await chaos.create_experiment(
            name="CPU Stress Test",
            description="Test under CPU pressure",
            failures=[
                {
                    "type": "cpu_stress",
                    "duration": 30,
                    "percentage": 25.0,
                    "parameters": {"load": 80},
                }
            ],
            target_selector={"app": "compute-service"},
            safety_level=SafetyLevel.SAFE,
            steady_state={
                "name": "API Available",
                "probe_type": "http",
                "probe_config": {"url": "/health"},
                "tolerance": {"status_codes": [200]},
            },
        )

        assert experiment.steady_state_hypothesis is not None

    @pytest.mark.asyncio
    async def test_abort_experiment(self, chaos):
        """Test aborting an experiment."""
        experiment = await chaos.create_experiment(
            name="Long Experiment",
            description="Test abort",
            failures=[
                {
                    "type": "memory_stress",
                    "duration": 300,
                    "percentage": 20.0,
                }
            ],
            target_selector={"app": "test"},
            safety_level=SafetyLevel.SAFE,
        )

        result = await chaos.abort_experiment(experiment.id)

        assert result is False

    @pytest.mark.asyncio
    async def test_safety_validation(self, chaos):
        """Test that safety validations work."""
        with pytest.raises(ValueError):
            await chaos.create_experiment(
                name="Unsafe Test",
                description="Should fail validation",
                failures=[
                    {
                        "type": "pod_kill",
                        "duration": 60,
                        "percentage": 100.0,
                    }
                ],
                target_selector={"app": "critical"},
                safety_level=SafetyLevel.CAUTIOUS,
            )


class TestDigitalTwinSimulator:
    """Tests for the main Digital Twin Simulator."""

    @pytest.fixture
    def twin(self):
        return create_digital_twin()

    @pytest.mark.asyncio
    async def test_simulate_deployment(self, twin):
        """Test deployment simulation."""
        config = SimulationConfig(
            simulation_type=SimulationType.DEPLOYMENT,
            target_service="api-service",
            environment="production",
            clone_environment=True,
            parameters={
                "target_version": "v2.0.0",
                "strategy": "rolling",
            },
        )

        result = await twin.simulate(config)

        assert result.id is not None
        assert result.status in ["running", "completed", "failed"]
        assert 0 <= result.success_probability <= 1
        assert 0 <= result.risk_score <= 1

    @pytest.mark.asyncio
    async def test_simulate_rollback(self, twin):
        """Test rollback simulation."""
        config = SimulationConfig(
            simulation_type=SimulationType.ROLLBACK,
            target_service="backend",
            parameters={
                "from_version": "v2.0.0",
                "to_version": "v1.9.0",
                "deployment_id": "deploy-123",
            },
        )

        result = await twin.simulate(config)

        assert result.id is not None
        assert isinstance(result.recommendations, list)

    @pytest.mark.asyncio
    async def test_simulate_scaling(self, twin):
        """Test scaling simulation."""
        config = SimulationConfig(
            simulation_type=SimulationType.SCALING,
            target_service="compute-service",
            parameters={
                "current_replicas": 3,
                "target_replicas": 6,
            },
        )

        result = await twin.simulate(config)

        assert result.id is not None

    @pytest.mark.asyncio
    async def test_simulate_what_if_traffic_spike(self, twin):
        """Test what-if traffic spike scenario."""
        config = SimulationConfig(
            simulation_type=SimulationType.WHAT_IF,
            target_service="api-gateway",
            parameters={
                "scenario": "traffic_spike",
                "multiplier": 3.0,
                "current_rps": 1000,
                "replicas": 3,
            },
        )

        result = await twin.simulate(config)

        assert result.deployment_result is not None

    @pytest.mark.asyncio
    async def test_simulate_what_if_dependency_failure(self, twin):
        """Test what-if dependency failure scenario."""
        config = SimulationConfig(
            simulation_type=SimulationType.WHAT_IF,
            target_service="api-service",
            parameters={
                "scenario": "dependency_failure",
                "dependency": "database",
                "circuit_breaker": True,
            },
        )

        result = await twin.simulate(config)

        assert "dependency_failure" in str(result.deployment_result)

    @pytest.mark.asyncio
    async def test_simulate_chaos(self, twin):
        """Test chaos simulation."""
        config = SimulationConfig(
            simulation_type=SimulationType.CHAOS,
            target_service="backend",
            clone_environment=True,
            parameters={
                "failure_type": "pod_kill",
                "duration": 30,
            },
            safety_level=SafetyLevel.SAFE,
            run_chaos=True,
        )

        result = await twin.simulate(config)

        assert result.id is not None

    def test_list_simulations(self, twin):
        """Test listing simulations."""
        simulations = twin.list_simulations()
        assert isinstance(simulations, list)

    @pytest.mark.asyncio
    async def test_simulation_safe_to_proceed(self, twin):
        """Test safe_to_proceed determination."""
        config = SimulationConfig(
            simulation_type=SimulationType.DEPLOYMENT,
            target_service="low-risk-service",
            parameters={
                "strategy": "canary",
            },
        )

        result = await twin.simulate(config)

        assert isinstance(result.safe_to_proceed, bool)
        assert isinstance(result.requires_approval, bool)

    @pytest.mark.asyncio
    async def test_simulation_with_downtime_prediction(self, twin):
        """Test simulation includes downtime predictions."""
        config = SimulationConfig(
            simulation_type=SimulationType.DEPLOYMENT,
            target_service="api-service",
            predict_downtime=True,
        )

        result = await twin.simulate(config)

        assert isinstance(result.downtime_predictions, list)


class TestSimulationConfig:
    """Tests for SimulationConfig."""

    def test_config_defaults(self):
        """Test default configuration values."""
        config = SimulationConfig(
            simulation_type=SimulationType.DEPLOYMENT,
            target_service="test-service",
        )

        assert config.environment == "production"
        assert config.clone_environment is True
        assert config.predict_downtime is True
        assert config.run_chaos is False

    def test_config_to_dict(self):
        """Test config serialization."""
        config = SimulationConfig(
            simulation_type=SimulationType.ROLLBACK,
            target_service="backend",
            environment="staging",
        )

        data = config.to_dict()

        assert data["simulation_type"] == "rollback"
        assert data["target_service"] == "backend"
        assert data["environment"] == "staging"


class TestCloneConfig:
    """Tests for CloneConfig."""

    def test_config_defaults(self):
        """Test default clone configuration."""
        config = CloneConfig()

        assert config.strategy == CloneStrategy.MINIMAL
        assert config.ttl_hours == 4
        assert config.scale_factor == 0.1
        assert config.isolate_network is True

    def test_config_to_dict(self):
        """Test config serialization."""
        config = CloneConfig(
            strategy=CloneStrategy.FULL,
            ttl_hours=8,
        )

        data = config.to_dict()

        assert data["strategy"] == "full"
        assert data["ttl_hours"] == 8
