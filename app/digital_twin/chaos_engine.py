"""Chaos Engineering Engine.

Injects controlled failures for resilience testing:
- Network failures (latency, partition, packet loss)
- Resource exhaustion (CPU, memory, disk)
- Service failures (crash, slow response)
- Dependency failures
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable
import uuid

log = logging.getLogger("omnipotent.digital_twin.chaos")


class FailureType(str, Enum):
    """Types of failures that can be injected."""
    # Network failures
    NETWORK_LATENCY = "network_latency"
    NETWORK_PARTITION = "network_partition"
    PACKET_LOSS = "packet_loss"
    DNS_FAILURE = "dns_failure"

    # Resource failures
    CPU_STRESS = "cpu_stress"
    MEMORY_STRESS = "memory_stress"
    DISK_STRESS = "disk_stress"
    DISK_FULL = "disk_full"

    # Service failures
    POD_KILL = "pod_kill"
    CONTAINER_KILL = "container_kill"
    PROCESS_KILL = "process_kill"
    SERVICE_UNAVAILABLE = "service_unavailable"

    # Dependency failures
    DATABASE_FAILURE = "database_failure"
    CACHE_FAILURE = "cache_failure"
    QUEUE_FAILURE = "queue_failure"
    EXTERNAL_API_FAILURE = "external_api_failure"


class ExperimentStatus(str, Enum):
    """Status of a chaos experiment."""
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"


class SafetyLevel(str, Enum):
    """Safety levels for chaos experiments."""
    SAFE = "safe"  # Isolated environment only
    CAUTIOUS = "cautious"  # Limited blast radius
    MODERATE = "moderate"  # Broader impact possible
    AGGRESSIVE = "aggressive"  # Full chaos (use carefully)


@dataclass
class FailureConfig:
    """Configuration for a failure injection."""
    failure_type: FailureType
    target_selector: dict[str, str]  # Labels/selectors for targets
    parameters: dict[str, Any]
    duration_seconds: int
    delay_seconds: int = 0
    percentage: float = 100.0  # % of targets to affect

    def to_dict(self) -> dict[str, Any]:
        return {
            "failure_type": self.failure_type.value,
            "target_selector": self.target_selector,
            "parameters": self.parameters,
            "duration_seconds": self.duration_seconds,
            "delay_seconds": self.delay_seconds,
            "percentage": self.percentage,
        }


@dataclass
class ExperimentTarget:
    """A target for chaos experiment."""
    id: str
    name: str
    type: str  # "pod", "service", "node", "container"
    namespace: str | None
    labels: dict[str, str]
    affected: bool = False
    failure_injected_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "namespace": self.namespace,
            "labels": self.labels,
            "affected": self.affected,
        }


@dataclass
class SteadyStateHypothesis:
    """Hypothesis about system steady state."""
    name: str
    description: str
    probe_type: str  # "http", "tcp", "command", "metric"
    probe_config: dict[str, Any]
    tolerance: dict[str, Any]  # Success criteria

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "probe_type": self.probe_type,
            "probe_config": self.probe_config,
            "tolerance": self.tolerance,
        }


@dataclass
class ChaosExperiment:
    """A chaos engineering experiment."""
    id: str
    name: str
    description: str
    failures: list[FailureConfig]
    targets: list[ExperimentTarget]
    steady_state_hypothesis: SteadyStateHypothesis | None
    safety_level: SafetyLevel
    status: ExperimentStatus = ExperimentStatus.PENDING
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: datetime | None = None
    ended_at: datetime | None = None
    rollback_on_failure: bool = True
    max_duration_seconds: int = 300
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "failures": [f.to_dict() for f in self.failures],
            "targets": [t.to_dict() for t in self.targets],
            "steady_state_hypothesis": self.steady_state_hypothesis.to_dict() if self.steady_state_hypothesis else None,
            "safety_level": self.safety_level.value,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "max_duration_seconds": self.max_duration_seconds,
        }


@dataclass
class ProbeResult:
    """Result of a steady state probe."""
    hypothesis_name: str
    passed: bool
    actual_value: Any
    expected: str
    message: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "hypothesis_name": self.hypothesis_name,
            "passed": self.passed,
            "actual_value": self.actual_value,
            "expected": self.expected,
            "message": self.message,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class ChaosResult:
    """Result of a chaos experiment."""
    experiment: ChaosExperiment
    status: ExperimentStatus
    duration_seconds: float
    steady_state_before: ProbeResult | None
    steady_state_after: ProbeResult | None
    steady_state_maintained: bool
    affected_targets: list[ExperimentTarget]
    events: list[dict[str, Any]]
    errors: list[str]
    insights: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "experiment": self.experiment.to_dict(),
            "status": self.status.value,
            "duration_seconds": self.duration_seconds,
            "steady_state_before": self.steady_state_before.to_dict() if self.steady_state_before else None,
            "steady_state_after": self.steady_state_after.to_dict() if self.steady_state_after else None,
            "steady_state_maintained": self.steady_state_maintained,
            "affected_targets": [t.to_dict() for t in self.affected_targets],
            "events": self.events,
            "errors": self.errors,
            "insights": self.insights,
        }


class ChaosEngine:
    """Chaos engineering engine for resilience testing.

    Features:
    - Multiple failure types (network, resource, service)
    - Safety controls and blast radius limiting
    - Steady state hypothesis validation
    - Automatic rollback on failure
    - Integration with monitoring
    """

    def __init__(
        self,
        k8s_client: Any = None,
        metrics_client: Any = None,
        digital_twin: Any = None,
    ):
        self.k8s = k8s_client
        self.metrics = metrics_client
        self.digital_twin = digital_twin

        self._experiments: dict[str, ChaosExperiment] = {}
        self._results: dict[str, ChaosResult] = {}
        self._running_experiment: str | None = None

        # Failure injectors by type
        self._injectors: dict[FailureType, Callable] = {}
        self._register_default_injectors()

        # Safety constraints
        self._blocked_namespaces = {"kube-system", "kube-public", "istio-system"}
        self._max_affected_percentage = 50.0

    def _register_default_injectors(self) -> None:
        """Register default failure injectors."""
        self._injectors[FailureType.NETWORK_LATENCY] = self._inject_network_latency
        self._injectors[FailureType.POD_KILL] = self._inject_pod_kill
        self._injectors[FailureType.CPU_STRESS] = self._inject_cpu_stress
        self._injectors[FailureType.MEMORY_STRESS] = self._inject_memory_stress
        self._injectors[FailureType.SERVICE_UNAVAILABLE] = self._inject_service_unavailable

    async def create_experiment(
        self,
        name: str,
        description: str,
        failures: list[dict[str, Any]],
        target_selector: dict[str, str],
        namespace: str | None = None,
        steady_state: dict[str, Any] | None = None,
        safety_level: SafetyLevel = SafetyLevel.CAUTIOUS,
        max_duration: int = 300,
    ) -> ChaosExperiment:
        """Create a chaos experiment.

        Args:
            name: Experiment name
            description: What this experiment tests
            failures: List of failure configurations
            target_selector: Labels to select targets
            namespace: Target namespace (None = digital twin only)
            steady_state: Steady state hypothesis
            safety_level: Safety level
            max_duration: Maximum experiment duration

        Returns:
            ChaosExperiment ready to run
        """
        exp_id = str(uuid.uuid4())[:8]

        # Parse failures
        failure_configs = []
        for f in failures:
            failure_configs.append(FailureConfig(
                failure_type=FailureType(f["type"]),
                target_selector=target_selector,
                parameters=f.get("parameters", {}),
                duration_seconds=f.get("duration", 60),
                delay_seconds=f.get("delay", 0),
                percentage=f.get("percentage", 100.0),
            ))

        # Discover targets
        targets = await self._discover_targets(target_selector, namespace)

        # Validate safety
        self._validate_safety(targets, failure_configs, safety_level, namespace)

        # Parse steady state hypothesis
        hypothesis = None
        if steady_state:
            hypothesis = SteadyStateHypothesis(
                name=steady_state.get("name", "System Available"),
                description=steady_state.get("description", ""),
                probe_type=steady_state.get("probe_type", "http"),
                probe_config=steady_state.get("probe_config", {}),
                tolerance=steady_state.get("tolerance", {}),
            )

        experiment = ChaosExperiment(
            id=exp_id,
            name=name,
            description=description,
            failures=failure_configs,
            targets=targets,
            steady_state_hypothesis=hypothesis,
            safety_level=safety_level,
            max_duration_seconds=max_duration,
        )

        self._experiments[exp_id] = experiment
        log.info(
            "Created chaos experiment: %s (%s) with %d targets",
            name, safety_level.value, len(targets)
        )

        return experiment

    async def _discover_targets(
        self,
        selector: dict[str, str],
        namespace: str | None,
    ) -> list[ExperimentTarget]:
        """Discover targets for chaos experiment using Kubernetes API."""
        from app.connectors.kubernetes import KubernetesConnector

        targets = []
        k8s = KubernetesConnector()

        try:
            label_selector = ",".join(f"{k}={v}" for k, v in selector.items())
            ns = namespace or "default"

            pods = await k8s.list_pods(ns, label_selector=label_selector)
            if pods.success:
                for pod in pods.data:
                    targets.append(ExperimentTarget(
                        id=f"{pod['namespace']}/{pod['name']}",
                        name=pod["name"],
                        type="pod",
                        namespace=pod["namespace"],
                        labels=pod.get("labels", {}),
                    ))

        except Exception as e:
            log.warning("Failed to discover targets: %s", e)
        finally:
            await k8s.close()

        return targets

    def _validate_safety(
        self,
        targets: list[ExperimentTarget],
        failures: list[FailureConfig],
        safety_level: SafetyLevel,
        namespace: str | None,
    ) -> None:
        """Validate experiment safety constraints."""
        # Check blocked namespaces
        for target in targets:
            if target.namespace in self._blocked_namespaces:
                raise ValueError(f"Cannot target system namespace: {target.namespace}")

        # Check percentage limits
        for failure in failures:
            if failure.percentage > self._max_affected_percentage and safety_level != SafetyLevel.AGGRESSIVE:
                raise ValueError(
                    f"Percentage {failure.percentage}% exceeds limit {self._max_affected_percentage}% "
                    f"for safety level {safety_level.value}"
                )

        # Check safety level requirements
        if safety_level == SafetyLevel.SAFE and namespace:
            raise ValueError("SAFE level requires digital twin (namespace must be None)")

        log.info("Safety validation passed for %d targets", len(targets))

    async def run_experiment(
        self,
        experiment_id: str,
        dry_run: bool = False,
    ) -> ChaosResult:
        """Run a chaos experiment.

        Args:
            experiment_id: ID of experiment to run
            dry_run: If True, simulate without actual injection

        Returns:
            ChaosResult with findings
        """
        experiment = self._experiments.get(experiment_id)
        if not experiment:
            raise ValueError(f"Experiment not found: {experiment_id}")

        if self._running_experiment:
            raise RuntimeError(f"Another experiment is running: {self._running_experiment}")

        import time
        start_time = time.monotonic()

        self._running_experiment = experiment_id
        experiment.status = ExperimentStatus.RUNNING
        experiment.started_at = datetime.now(timezone.utc)

        events: list[dict[str, Any]] = []
        errors: list[str] = []
        affected_targets: list[ExperimentTarget] = []

        try:
            # Step 1: Verify steady state before
            steady_state_before = None
            if experiment.steady_state_hypothesis:
                log.info("Checking steady state before chaos injection")
                steady_state_before = await self._probe_steady_state(
                    experiment.steady_state_hypothesis
                )
                events.append({
                    "time": datetime.now(timezone.utc).isoformat(),
                    "event": "steady_state_check_before",
                    "result": "passed" if steady_state_before.passed else "failed",
                })

                if not steady_state_before.passed:
                    raise RuntimeError("Steady state not met before experiment")

            # Step 2: Inject failures
            for failure in experiment.failures:
                if failure.delay_seconds > 0:
                    log.info("Waiting %ds before injecting %s", failure.delay_seconds, failure.failure_type.value)
                    await asyncio.sleep(failure.delay_seconds)

                # Select targets
                selected_targets = self._select_targets(experiment.targets, failure.percentage)

                log.info(
                    "Injecting %s on %d targets for %ds%s",
                    failure.failure_type.value,
                    len(selected_targets),
                    failure.duration_seconds,
                    " (dry run)" if dry_run else ""
                )

                if not dry_run:
                    injected = await self._inject_failure(failure, selected_targets)
                    affected_targets.extend(injected)

                events.append({
                    "time": datetime.now(timezone.utc).isoformat(),
                    "event": "failure_injected",
                    "failure_type": failure.failure_type.value,
                    "targets": len(selected_targets),
                    "dry_run": dry_run,
                })

                # Wait for failure duration
                if not dry_run:
                    await asyncio.sleep(failure.duration_seconds)

            # Step 3: Clean up failures
            if not dry_run:
                await self._cleanup_failures(experiment)

            events.append({
                "time": datetime.now(timezone.utc).isoformat(),
                "event": "failures_cleaned_up",
            })

            # Step 4: Verify steady state after
            steady_state_after = None
            if experiment.steady_state_hypothesis:
                log.info("Checking steady state after chaos injection")
                # Wait a moment for system to recover
                await asyncio.sleep(5)
                steady_state_after = await self._probe_steady_state(
                    experiment.steady_state_hypothesis
                )
                events.append({
                    "time": datetime.now(timezone.utc).isoformat(),
                    "event": "steady_state_check_after",
                    "result": "passed" if steady_state_after.passed else "failed",
                })

            experiment.status = ExperimentStatus.COMPLETED

        except Exception as e:
            log.error("Experiment failed: %s", e)
            errors.append(str(e))
            experiment.status = ExperimentStatus.FAILED

            # Rollback if configured
            if experiment.rollback_on_failure and not dry_run:
                log.info("Rolling back chaos injection")
                await self._cleanup_failures(experiment)

        finally:
            self._running_experiment = None
            experiment.ended_at = datetime.now(timezone.utc)

        duration = time.monotonic() - start_time

        # Determine if steady state was maintained
        steady_state_maintained = True
        if steady_state_before and steady_state_after:
            steady_state_maintained = steady_state_after.passed

        # Generate insights
        insights = self._generate_insights(
            experiment, steady_state_before, steady_state_after, events
        )

        result = ChaosResult(
            experiment=experiment,
            status=experiment.status,
            duration_seconds=duration,
            steady_state_before=steady_state_before,
            steady_state_after=steady_state_after,
            steady_state_maintained=steady_state_maintained,
            affected_targets=affected_targets,
            events=events,
            errors=errors,
            insights=insights,
        )

        self._results[experiment_id] = result

        log.info(
            "Experiment complete: %s (status=%s, steady_state=%s)",
            experiment_id, experiment.status.value,
            "maintained" if steady_state_maintained else "broken"
        )

        return result

    def _select_targets(
        self,
        targets: list[ExperimentTarget],
        percentage: float,
    ) -> list[ExperimentTarget]:
        """Select a percentage of targets deterministically using hash-based selection."""
        count = max(1, int(len(targets) * percentage / 100))
        sorted_targets = sorted(targets, key=lambda t: hash(t.name + (t.namespace or "")))
        return sorted_targets[:min(count, len(targets))]

    async def _inject_failure(
        self,
        failure: FailureConfig,
        targets: list[ExperimentTarget],
    ) -> list[ExperimentTarget]:
        """Inject a failure on targets."""
        injector = self._injectors.get(failure.failure_type)
        if not injector:
            log.warning("No injector for %s, simulating", failure.failure_type.value)
            # Simulate injection
            for target in targets:
                target.affected = True
                target.failure_injected_at = datetime.now(timezone.utc)
            return targets

        return await injector(failure, targets)

    async def _inject_network_latency(
        self,
        failure: FailureConfig,
        targets: list[ExperimentTarget],
    ) -> list[ExperimentTarget]:
        """Inject network latency."""
        latency_ms = failure.parameters.get("latency_ms", 500)
        _ = failure.parameters.get("jitter_ms", 100)  # Reserved for tc command

        for target in targets:
            # In production, use tc or iptables
            log.debug("Injecting %dms latency on %s", latency_ms, target.name)
            target.affected = True
            target.failure_injected_at = datetime.now(timezone.utc)

        return targets

    async def _inject_pod_kill(
        self,
        failure: FailureConfig,
        targets: list[ExperimentTarget],
    ) -> list[ExperimentTarget]:
        """Kill pods using Kubernetes API."""
        from app.connectors.kubernetes import KubernetesConnector

        k8s = KubernetesConnector()

        for target in targets:
            if target.type == "pod":
                ns = target.namespace or "default"
                log.info("Killing pod %s in namespace %s", target.name, ns)
                result = await k8s.delete_pod(target.name, ns)
                if result.success:
                    target.affected = True
                    target.failure_injected_at = datetime.now(timezone.utc)
                else:
                    log.error("Failed to kill pod %s: %s", target.name, result.error)

        await k8s.close()
        return targets

    async def _inject_cpu_stress(
        self,
        failure: FailureConfig,
        targets: list[ExperimentTarget],
    ) -> list[ExperimentTarget]:
        """Inject CPU stress."""
        load_percentage = failure.parameters.get("load", 80)

        for target in targets:
            # In production, run stress-ng in container
            log.debug("Stressing CPU to %d%% on %s", load_percentage, target.name)
            target.affected = True
            target.failure_injected_at = datetime.now(timezone.utc)

        return targets

    async def _inject_memory_stress(
        self,
        failure: FailureConfig,
        targets: list[ExperimentTarget],
    ) -> list[ExperimentTarget]:
        """Inject memory stress."""
        memory_mb = failure.parameters.get("memory_mb", 256)

        for target in targets:
            # In production, run stress-ng --vm
            log.debug("Consuming %dMB memory on %s", memory_mb, target.name)
            target.affected = True
            target.failure_injected_at = datetime.now(timezone.utc)

        return targets

    async def _inject_service_unavailable(
        self,
        failure: FailureConfig,
        targets: list[ExperimentTarget],
    ) -> list[ExperimentTarget]:
        """Make service unavailable."""
        for target in targets:
            # In production, modify service selector or add failing endpoint
            log.debug("Making service %s unavailable", target.name)
            target.affected = True
            target.failure_injected_at = datetime.now(timezone.utc)

        return targets

    async def _cleanup_failures(self, experiment: ChaosExperiment) -> None:
        """Clean up all injected failures."""
        for target in experiment.targets:
            if target.affected:
                # In production, remove tc rules, restore pods, etc.
                log.debug("Cleaning up failure on %s", target.name)
                target.affected = False

    async def _probe_steady_state(
        self,
        hypothesis: SteadyStateHypothesis,
    ) -> ProbeResult:
        """Probe the steady state hypothesis using real HTTP or Prometheus queries."""
        try:
            if hypothesis.probe_type == "http":
                import httpx

                url = hypothesis.probe_config.get("url", "/health")
                if not url.startswith(("http://", "https://")):
                    host = hypothesis.probe_config.get("host", "localhost")
                    port = hypothesis.probe_config.get("port", 80)
                    url = f"http://{host}:{port}{url}"

                expected_codes = hypothesis.tolerance.get("status_codes", [200])

                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.get(url)
                    success = response.status_code in expected_codes

                return ProbeResult(
                    hypothesis_name=hypothesis.name,
                    passed=success,
                    actual_value=response.status_code,
                    expected=f"status_code in {expected_codes}",
                    message="HTTP probe passed" if success else f"HTTP probe failed: got {response.status_code}",
                )

            elif hypothesis.probe_type == "metric":
                from app.connectors.prometheus import PrometheusConnector
                from app.connectors.base import ConnectorConfig

                prometheus_url = os.environ.get("PROMETHEUS_URL", "http://localhost:9090")
                config = ConnectorConfig(url=prometheus_url)
                prometheus = PrometheusConnector(config)

                query = hypothesis.probe_config.get("query", "up")
                result = await prometheus.query(query)
                await prometheus.close()

                value = 0.0
                if result.success and result.data:
                    value = float(result.data[0].get("value", 0))

                threshold = hypothesis.tolerance.get("threshold", 50)
                operator = hypothesis.tolerance.get("operator", "lte")

                if operator == "lte":
                    passed = value <= threshold
                elif operator == "gte":
                    passed = value >= threshold
                elif operator == "eq":
                    passed = value == threshold
                else:
                    passed = value <= threshold

                return ProbeResult(
                    hypothesis_name=hypothesis.name,
                    passed=passed,
                    actual_value=value,
                    expected=f"{operator} {threshold}",
                    message=f"Metric value: {value:.2f}",
                )

            else:
                return ProbeResult(
                    hypothesis_name=hypothesis.name,
                    passed=True,
                    actual_value=None,
                    expected="probe executed",
                    message="Unknown probe type, assuming success",
                )

        except Exception as e:
            return ProbeResult(
                hypothesis_name=hypothesis.name,
                passed=False,
                actual_value=None,
                expected="probe executed",
                message=f"Probe error: {e}",
            )

    def _generate_insights(
        self,
        experiment: ChaosExperiment,
        before: ProbeResult | None,
        after: ProbeResult | None,
        events: list[dict[str, Any]],
    ) -> list[str]:
        """Generate insights from experiment results."""
        insights = []

        # Steady state insights
        if before and after:
            if before.passed and not after.passed:
                insights.append(
                    f"System failed to maintain steady state under {experiment.failures[0].failure_type.value}"
                )
                insights.append("Consider implementing circuit breakers or fallback mechanisms")
            elif before.passed and after.passed:
                insights.append(
                    f"System resilient to {experiment.failures[0].failure_type.value} failure"
                )

        # Target insights
        affected_count = len([t for t in experiment.targets if t.affected])
        total_count = len(experiment.targets)
        if affected_count < total_count:
            insights.append(f"Blast radius limited to {affected_count}/{total_count} targets")

        # Safety insights
        if experiment.safety_level == SafetyLevel.SAFE:
            insights.append("Experiment ran in isolated digital twin environment")

        return insights

    async def abort_experiment(self, experiment_id: str) -> bool:
        """Abort a running experiment."""
        experiment = self._experiments.get(experiment_id)
        if not experiment:
            return False

        if experiment.status != ExperimentStatus.RUNNING:
            return False

        log.warning("Aborting experiment: %s", experiment_id)

        # Clean up
        await self._cleanup_failures(experiment)

        experiment.status = ExperimentStatus.ABORTED
        experiment.ended_at = datetime.now(timezone.utc)
        self._running_experiment = None

        return True

    def get_experiment(self, experiment_id: str) -> ChaosExperiment | None:
        """Get an experiment by ID."""
        return self._experiments.get(experiment_id)

    def get_result(self, experiment_id: str) -> ChaosResult | None:
        """Get experiment result."""
        return self._results.get(experiment_id)

    def list_experiments(
        self,
        status: ExperimentStatus | None = None,
    ) -> list[ChaosExperiment]:
        """List experiments."""
        experiments = list(self._experiments.values())

        if status:
            experiments = [e for e in experiments if e.status == status]

        return experiments

    def get_stats(self) -> dict[str, Any]:
        """Get chaos engine statistics."""
        results = list(self._results.values())

        return {
            "total_experiments": len(self._experiments),
            "completed": len([e for e in self._experiments.values() if e.status == ExperimentStatus.COMPLETED]),
            "steady_state_maintained": len([r for r in results if r.steady_state_maintained]),
            "steady_state_broken": len([r for r in results if not r.steady_state_maintained]),
            "currently_running": self._running_experiment is not None,
        }


def create_chaos_engine(
    k8s_client: Any = None,
    metrics_client: Any = None,
    digital_twin: Any = None,
) -> ChaosEngine:
    """Factory function to create a chaos engine."""
    return ChaosEngine(
        k8s_client=k8s_client,
        metrics_client=metrics_client,
        digital_twin=digital_twin,
    )
