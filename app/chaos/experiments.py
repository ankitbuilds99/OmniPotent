"""Chaos Engineering - Controlled failure injection experiments."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Awaitable
from enum import Enum
import random

log = logging.getLogger("devcopilot.chaos.experiments")


class ExperimentType(str, Enum):
    """Types of chaos experiments."""
    POD_KILL = "pod_kill"
    POD_FAILURE = "pod_failure"
    NETWORK_LATENCY = "network_latency"
    NETWORK_LOSS = "network_loss"
    NETWORK_PARTITION = "network_partition"
    CPU_STRESS = "cpu_stress"
    MEMORY_STRESS = "memory_stress"
    IO_STRESS = "io_stress"
    DNS_FAILURE = "dns_failure"
    TIME_SKEW = "time_skew"


class ExperimentState(str, Enum):
    """State of a chaos experiment."""
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"


@dataclass
class ExperimentTarget:
    """Target for chaos experiment."""
    namespace: str = "default"
    labels: dict[str, str] = field(default_factory=dict)
    pod_names: list[str] = field(default_factory=list)
    percentage: int = 100  # % of matching pods to target


@dataclass
class ExperimentConfig:
    """Configuration for a chaos experiment."""
    experiment_type: ExperimentType
    target: ExperimentTarget
    duration_seconds: int = 60
    parameters: dict[str, Any] = field(default_factory=dict)
    steady_state_check: Callable[[], Awaitable[bool]] | None = None
    abort_conditions: list[str] = field(default_factory=list)
    schedule: str | None = None  # Cron expression


@dataclass
class ExperimentResult:
    """Result of a chaos experiment."""
    experiment_id: str
    experiment_type: ExperimentType
    state: ExperimentState
    started_at: datetime
    ended_at: datetime | None
    duration_seconds: float
    steady_state_before: bool
    steady_state_after: bool
    hypothesis_validated: bool
    affected_pods: list[str]
    observations: list[str]
    metrics: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "experiment_type": self.experiment_type.value,
            "state": self.state.value,
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "duration_seconds": self.duration_seconds,
            "steady_state_before": self.steady_state_before,
            "steady_state_after": self.steady_state_after,
            "hypothesis_validated": self.hypothesis_validated,
            "affected_pods": self.affected_pods,
            "observations": self.observations,
            "metrics": self.metrics,
            "error": self.error,
        }


@dataclass
class Experiment:
    """A chaos experiment definition."""
    id: str
    name: str
    description: str
    config: ExperimentConfig
    hypothesis: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    created_by: str = ""
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "experiment_type": self.config.experiment_type.value,
            "hypothesis": self.hypothesis,
            "created_at": self.created_at.isoformat(),
            "created_by": self.created_by,
            "tags": self.tags,
        }


class ChaosEngine:
    """Manages and executes chaos experiments.

    Features:
    - Multiple experiment types
    - Steady state validation
    - Automatic rollback
    - Scheduled experiments
    - Safety controls
    """

    def __init__(self):
        """Initialize the chaos engine."""
        self._experiments: dict[str, Experiment] = {}
        self._running: dict[str, asyncio.Task] = {}
        self._results: list[ExperimentResult] = []
        self._safety_enabled = True
        log.info("ChaosEngine initialized")

    def register_experiment(self, experiment: Experiment) -> None:
        """Register an experiment.

        Args:
            experiment: Experiment to register
        """
        self._experiments[experiment.id] = experiment
        log.info("Registered experiment: %s", experiment.name)

    async def run_experiment(
        self,
        experiment_id: str,
        dry_run: bool = False,
    ) -> ExperimentResult:
        """Run a chaos experiment.

        Args:
            experiment_id: ID of experiment to run
            dry_run: If True, simulate without actual chaos

        Returns:
            ExperimentResult
        """
        experiment = self._experiments.get(experiment_id)
        if not experiment:
            raise ValueError(f"Experiment not found: {experiment_id}")

        log.info("Starting experiment: %s (dry_run=%s)", experiment.name, dry_run)

        started_at = datetime.now(timezone.utc)
        observations: list[str] = []
        affected_pods: list[str] = []
        error = None
        state = ExperimentState.RUNNING

        try:
            # Check steady state before
            steady_before = await self._check_steady_state(experiment.config)
            observations.append(f"Steady state before: {steady_before}")

            if not steady_before and self._safety_enabled:
                observations.append("SAFETY: Aborting - system not in steady state")
                state = ExperimentState.ABORTED
                return self._create_result(
                    experiment, started_at, state, steady_before, False,
                    affected_pods, observations, error
                )

            # Select targets
            pods = await self._select_targets(experiment.config.target)
            affected_pods = pods
            observations.append(f"Selected {len(pods)} target pods")

            if not dry_run:
                # Apply chaos
                await self._apply_chaos(experiment.config, pods, observations)

                # Wait for duration
                await asyncio.sleep(experiment.config.duration_seconds)

                # Cleanup
                await self._cleanup_chaos(experiment.config, pods, observations)

            # Check steady state after
            steady_after = await self._check_steady_state(experiment.config)
            observations.append(f"Steady state after: {steady_after}")

            state = ExperimentState.COMPLETED

        except asyncio.CancelledError:
            state = ExperimentState.ABORTED
            observations.append("Experiment aborted")
            await self._cleanup_chaos(experiment.config, affected_pods, observations)
            steady_after = False

        except Exception as e:
            state = ExperimentState.FAILED
            error = str(e)
            observations.append(f"Experiment failed: {e}")
            await self._cleanup_chaos(experiment.config, affected_pods, observations)
            steady_after = False
            log.exception("Experiment failed: %s", experiment_id)

        result = self._create_result(
            experiment, started_at, state,
            steady_before if 'steady_before' in dir() else False,
            steady_after if 'steady_after' in dir() else False,
            affected_pods, observations, error
        )

        self._results.append(result)
        return result

    def _create_result(
        self,
        experiment: Experiment,
        started_at: datetime,
        state: ExperimentState,
        steady_before: bool,
        steady_after: bool,
        affected_pods: list[str],
        observations: list[str],
        error: str | None,
    ) -> ExperimentResult:
        """Create an experiment result."""
        ended_at = datetime.now(timezone.utc)
        duration = (ended_at - started_at).total_seconds()

        return ExperimentResult(
            experiment_id=experiment.id,
            experiment_type=experiment.config.experiment_type,
            state=state,
            started_at=started_at,
            ended_at=ended_at,
            duration_seconds=duration,
            steady_state_before=steady_before,
            steady_state_after=steady_after,
            hypothesis_validated=steady_before and steady_after,
            affected_pods=affected_pods,
            observations=observations,
            error=error,
        )

    async def _check_steady_state(self, config: ExperimentConfig) -> bool:
        """Check if system is in steady state."""
        if config.steady_state_check:
            return await config.steady_state_check()

        # Default check: all pods healthy
        # In production: query Kubernetes/health endpoints
        return True

    async def _select_targets(self, target: ExperimentTarget) -> list[str]:
        """Select pods matching target criteria."""
        # In production: query Kubernetes API
        matching_pods = [
            f"pod-{target.namespace}-{i}"
            for i in range(5)
        ]

        # Apply percentage
        count = max(1, int(len(matching_pods) * target.percentage / 100))
        return random.sample(matching_pods, min(count, len(matching_pods)))

    async def _apply_chaos(
        self,
        config: ExperimentConfig,
        pods: list[str],
        observations: list[str],
    ) -> None:
        """Apply chaos to selected pods."""
        exp_type = config.experiment_type

        if exp_type == ExperimentType.POD_KILL:
            for pod in pods:
                # In production: kubectl delete pod
                observations.append(f"Killed pod: {pod}")

        elif exp_type == ExperimentType.NETWORK_LATENCY:
            latency_ms = config.parameters.get("latency_ms", 100)
            for pod in pods:
                # In production: tc qdisc add netem delay
                observations.append(f"Added {latency_ms}ms latency to: {pod}")

        elif exp_type == ExperimentType.NETWORK_LOSS:
            loss_percent = config.parameters.get("loss_percent", 10)
            for pod in pods:
                observations.append(f"Added {loss_percent}% packet loss to: {pod}")

        elif exp_type == ExperimentType.CPU_STRESS:
            workers = config.parameters.get("workers", 4)
            for pod in pods:
                observations.append(f"Applied CPU stress ({workers} workers) to: {pod}")

        elif exp_type == ExperimentType.MEMORY_STRESS:
            memory_mb = config.parameters.get("memory_mb", 256)
            for pod in pods:
                observations.append(f"Applied memory stress ({memory_mb}MB) to: {pod}")

        elif exp_type == ExperimentType.DNS_FAILURE:
            for pod in pods:
                observations.append(f"Induced DNS failure on: {pod}")

        log.info("Applied %s chaos to %d pods", exp_type.value, len(pods))

    async def _cleanup_chaos(
        self,
        config: ExperimentConfig,
        pods: list[str],
        observations: list[str],
    ) -> None:
        """Cleanup chaos effects."""
        exp_type = config.experiment_type

        if exp_type == ExperimentType.POD_KILL:
            observations.append("Pods will be recreated by controller")

        elif exp_type in (ExperimentType.NETWORK_LATENCY, ExperimentType.NETWORK_LOSS):
            for pod in pods:
                observations.append(f"Removed network chaos from: {pod}")

        elif exp_type in (ExperimentType.CPU_STRESS, ExperimentType.MEMORY_STRESS):
            for pod in pods:
                observations.append(f"Stopped stress on: {pod}")

        log.info("Cleanup complete for %s", exp_type.value)

    async def abort_experiment(self, experiment_id: str) -> bool:
        """Abort a running experiment.

        Args:
            experiment_id: Experiment to abort

        Returns:
            True if aborted
        """
        if experiment_id in self._running:
            self._running[experiment_id].cancel()
            del self._running[experiment_id]
            log.info("Aborted experiment: %s", experiment_id)
            return True
        return False

    def set_safety(self, enabled: bool) -> None:
        """Enable/disable safety controls.

        Args:
            enabled: Whether safety controls are enabled
        """
        self._safety_enabled = enabled
        log.info("Safety controls: %s", "enabled" if enabled else "DISABLED")

    def get_results(self, limit: int = 10) -> list[ExperimentResult]:
        """Get recent experiment results."""
        return self._results[-limit:]

    def create_pod_kill_experiment(
        self,
        name: str,
        namespace: str,
        labels: dict[str, str],
        percentage: int = 50,
    ) -> Experiment:
        """Create a pod kill experiment."""
        return Experiment(
            id=f"pod-kill-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
            name=name,
            description=f"Kill {percentage}% of pods matching labels in {namespace}",
            config=ExperimentConfig(
                experiment_type=ExperimentType.POD_KILL,
                target=ExperimentTarget(
                    namespace=namespace,
                    labels=labels,
                    percentage=percentage,
                ),
                duration_seconds=0,  # Instant
            ),
            hypothesis="System should recover automatically via replica controller",
        )

    def create_network_latency_experiment(
        self,
        name: str,
        namespace: str,
        labels: dict[str, str],
        latency_ms: int = 100,
        duration_seconds: int = 60,
    ) -> Experiment:
        """Create a network latency experiment."""
        return Experiment(
            id=f"network-latency-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
            name=name,
            description=f"Add {latency_ms}ms latency to pods for {duration_seconds}s",
            config=ExperimentConfig(
                experiment_type=ExperimentType.NETWORK_LATENCY,
                target=ExperimentTarget(
                    namespace=namespace,
                    labels=labels,
                ),
                duration_seconds=duration_seconds,
                parameters={"latency_ms": latency_ms},
            ),
            hypothesis="System should handle increased latency gracefully with circuit breakers",
        )


def create_chaos_engine() -> ChaosEngine:
    """Factory function to create a chaos engine."""
    return ChaosEngine()
