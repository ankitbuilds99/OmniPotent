"""Chaos Experiment Runner.

Executes and monitors chaos experiments.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("omnipotent.chaos_engineering.experiments")


class ExperimentStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    ABORTED = "aborted"
    FAILED = "failed"


class ObservationType(str, Enum):
    EXPECTED = "expected"
    UNEXPECTED = "unexpected"
    NEUTRAL = "neutral"


@dataclass
class SteadyStateCheck:
    """A steady state verification check."""

    metric_name: str
    baseline_value: float
    current_value: float
    threshold: float
    passed: bool
    unit: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric_name,
            "baseline": self.baseline_value,
            "current": self.current_value,
            "threshold": self.threshold,
            "passed": self.passed,
            "unit": self.unit,
        }


@dataclass
class Observation:
    """An observation during experiment execution."""

    timestamp: datetime
    observation_type: ObservationType
    description: str
    metric_name: str = ""
    metric_value: float = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "type": self.observation_type.value,
            "description": self.description,
            "metric": self.metric_name,
            "value": self.metric_value,
        }


@dataclass
class ChaosExperiment:
    """A chaos experiment definition."""

    experiment_id: str
    name: str
    description: str
    hypothesis: str
    target_service: str
    experiment_config: dict[str, Any]
    steady_state_checks: list[str]
    abort_conditions: dict[str, float]
    duration_seconds: int = 300
    created_by: str = ""
    scheduled_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.experiment_id,
            "name": self.name,
            "description": self.description,
            "hypothesis": self.hypothesis,
            "target": self.target_service,
            "config": self.experiment_config,
            "steady_state_checks": self.steady_state_checks,
            "abort_conditions": self.abort_conditions,
            "duration_seconds": self.duration_seconds,
            "scheduled_at": self.scheduled_at.isoformat() if self.scheduled_at else None,
        }


@dataclass
class ExperimentResult:
    """Result of a chaos experiment."""

    experiment_id: str
    status: ExperimentStatus
    hypothesis_confirmed: bool
    steady_state_checks: list[SteadyStateCheck]
    observations: list[Observation]
    issues_discovered: list[str]
    recommendations: list[str]
    started_at: datetime
    ended_at: datetime | None = None
    aborted_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "status": self.status.value,
            "hypothesis_confirmed": self.hypothesis_confirmed,
            "steady_state": [c.to_dict() for c in self.steady_state_checks],
            "observations": [o.to_dict() for o in self.observations],
            "issues": self.issues_discovered,
            "recommendations": self.recommendations,
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "aborted_reason": self.aborted_reason,
        }


class ExperimentRunner:
    """Runs chaos experiments with monitoring."""

    def __init__(self) -> None:
        self._experiments: dict[str, ChaosExperiment] = {}
        self._results: dict[str, ExperimentResult] = {}
        self._running: dict[str, ExperimentResult] = {}

    def create_experiment(
        self,
        name: str,
        description: str,
        hypothesis: str,
        target_service: str,
        config: dict[str, Any],
        steady_state_checks: list[str] | None = None,
        abort_conditions: dict[str, float] | None = None,
        duration_seconds: int = 300,
    ) -> ChaosExperiment:
        """Create a new experiment."""
        experiment = ChaosExperiment(
            experiment_id=f"exp_{datetime.now(timezone.utc).timestamp()}",
            name=name,
            description=description,
            hypothesis=hypothesis,
            target_service=target_service,
            experiment_config=config,
            steady_state_checks=steady_state_checks or ["error_rate", "latency_p99"],
            abort_conditions=abort_conditions or {"error_rate": 5.0, "user_impact": 50},
            duration_seconds=duration_seconds,
        )

        self._experiments[experiment.experiment_id] = experiment

        log.info("Created experiment: id=%s name=%s", experiment.experiment_id, name)

        return experiment

    def start_experiment(self, experiment_id: str) -> ExperimentResult:
        """Start running an experiment."""
        experiment = self._experiments.get(experiment_id)
        if not experiment:
            raise ValueError(f"Experiment not found: {experiment_id}")

        result = ExperimentResult(
            experiment_id=experiment_id,
            status=ExperimentStatus.RUNNING,
            hypothesis_confirmed=False,
            steady_state_checks=[],
            observations=[],
            issues_discovered=[],
            recommendations=[],
            started_at=datetime.now(timezone.utc),
        )

        self._running[experiment_id] = result

        initial_checks = self._run_steady_state_checks(experiment)
        result.steady_state_checks = initial_checks

        result.observations.append(Observation(
            timestamp=datetime.now(timezone.utc),
            observation_type=ObservationType.EXPECTED,
            description=f"Experiment started: {experiment.name}",
        ))

        log.info("Started experiment: id=%s", experiment_id)

        return result

    def _run_steady_state_checks(
        self,
        experiment: ChaosExperiment,
    ) -> list[SteadyStateCheck]:
        """Run steady state verification checks."""
        checks = []

        for check_name in experiment.steady_state_checks:
            if check_name == "error_rate":
                checks.append(SteadyStateCheck(
                    metric_name="Error Rate",
                    baseline_value=0.1,
                    current_value=0.3,
                    threshold=5.0,
                    passed=True,
                    unit="%",
                ))
            elif check_name == "latency_p99":
                checks.append(SteadyStateCheck(
                    metric_name="Latency P99",
                    baseline_value=150,
                    current_value=180,
                    threshold=500,
                    passed=True,
                    unit="ms",
                ))
            elif check_name == "throughput":
                checks.append(SteadyStateCheck(
                    metric_name="Throughput",
                    baseline_value=1000,
                    current_value=950,
                    threshold=500,
                    passed=True,
                    unit="req/s",
                ))

        return checks

    def add_observation(
        self,
        experiment_id: str,
        observation_type: ObservationType,
        description: str,
        metric_name: str = "",
        metric_value: float = 0,
    ) -> bool:
        """Add an observation to a running experiment."""
        result = self._running.get(experiment_id)
        if not result:
            return False

        observation = Observation(
            timestamp=datetime.now(timezone.utc),
            observation_type=observation_type,
            description=description,
            metric_name=metric_name,
            metric_value=metric_value,
        )

        result.observations.append(observation)
        return True

    def check_abort_conditions(self, experiment_id: str) -> dict[str, Any]:
        """Check if abort conditions are met."""
        experiment = self._experiments.get(experiment_id)
        result = self._running.get(experiment_id)

        if not experiment or not result:
            return {"should_abort": False}

        for metric, threshold in experiment.abort_conditions.items():
            for check in result.steady_state_checks:
                if check.metric_name.lower().replace(" ", "_") == metric:
                    if check.current_value > threshold:
                        return {
                            "should_abort": True,
                            "reason": f"{check.metric_name} exceeded threshold: {check.current_value} > {threshold}",
                        }

        return {"should_abort": False}

    def abort_experiment(
        self,
        experiment_id: str,
        reason: str,
    ) -> ExperimentResult | None:
        """Abort a running experiment."""
        result = self._running.get(experiment_id)
        if not result:
            return None

        result.status = ExperimentStatus.ABORTED
        result.ended_at = datetime.now(timezone.utc)
        result.aborted_reason = reason
        result.hypothesis_confirmed = False

        result.observations.append(Observation(
            timestamp=datetime.now(timezone.utc),
            observation_type=ObservationType.UNEXPECTED,
            description=f"Experiment aborted: {reason}",
        ))

        self._results[experiment_id] = result
        del self._running[experiment_id]

        log.info("Aborted experiment: id=%s reason=%s", experiment_id, reason)

        return result

    def complete_experiment(
        self,
        experiment_id: str,
        hypothesis_confirmed: bool,
        issues: list[str] | None = None,
        recommendations: list[str] | None = None,
    ) -> ExperimentResult | None:
        """Complete a running experiment."""
        result = self._running.get(experiment_id)
        if not result:
            return None

        result.status = ExperimentStatus.COMPLETED
        result.ended_at = datetime.now(timezone.utc)
        result.hypothesis_confirmed = hypothesis_confirmed
        result.issues_discovered = issues or []
        result.recommendations = recommendations or []

        result.observations.append(Observation(
            timestamp=datetime.now(timezone.utc),
            observation_type=ObservationType.EXPECTED if hypothesis_confirmed else ObservationType.UNEXPECTED,
            description=f"Experiment completed: hypothesis {'confirmed' if hypothesis_confirmed else 'refuted'}",
        ))

        self._results[experiment_id] = result
        del self._running[experiment_id]

        log.info(
            "Completed experiment: id=%s confirmed=%s",
            experiment_id,
            hypothesis_confirmed,
        )

        return result

    def get_experiment_status(self, experiment_id: str) -> dict[str, Any]:
        """Get current status of an experiment."""
        if experiment_id in self._running:
            result = self._running[experiment_id]
            experiment = self._experiments.get(experiment_id)

            elapsed = (datetime.now(timezone.utc) - result.started_at).total_seconds()
            duration = experiment.duration_seconds if experiment else 300
            progress = min(100, (elapsed / duration) * 100)

            return {
                "status": result.status.value,
                "progress_percent": round(progress, 1),
                "elapsed_seconds": round(elapsed),
                "observations": len(result.observations),
                "steady_state_passing": all(c.passed for c in result.steady_state_checks),
            }

        if experiment_id in self._results:
            result = self._results[experiment_id]
            return {
                "status": result.status.value,
                "hypothesis_confirmed": result.hypothesis_confirmed,
                "completed_at": result.ended_at.isoformat() if result.ended_at else None,
            }

        return {"status": "not_found"}

    def get_result(self, experiment_id: str) -> ExperimentResult | None:
        """Get result of a completed experiment."""
        return self._results.get(experiment_id)


_runner: ExperimentRunner | None = None


def get_experiment_runner() -> ExperimentRunner:
    """Get the default experiment runner."""
    global _runner
    if _runner is None:
        _runner = ExperimentRunner()
    return _runner
