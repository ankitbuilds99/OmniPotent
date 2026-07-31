"""Evaluation Pipeline - Main orchestrator for nightly evals."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Awaitable
from uuid import uuid4

from app.eval.scenarios import Scenario, ScenarioLoader, ExpectedOutcome
from app.eval.metrics import (
    MetricsCalculator,
    AccuracyMetrics,
    LatencyMetrics,
    CostMetrics,
    EvalResultData,
)

log = logging.getLogger("devcopilot.eval.pipeline")


@dataclass
class EvalConfig:
    """Configuration for the evaluation pipeline."""
    # Scenarios
    scenarios_dir: Path | None = None
    scenario_filter_tags: list[str] = field(default_factory=list)
    max_scenarios: int = 0  # 0 = unlimited

    # Execution
    parallel_workers: int = 4
    timeout_per_scenario_seconds: float = 300.0
    retry_count: int = 1

    # Model
    model: str = "claude-sonnet-4-20250514"
    fast_model: str = "claude-3-haiku-20240307"

    # Output
    output_dir: Path = field(default_factory=lambda: Path("eval_results"))
    save_detailed_results: bool = True

    # Thresholds for pass/fail
    min_accuracy: float = 0.7
    max_mean_latency_ms: float = 60000.0
    max_cost_per_investigation_usd: float = 0.50


@dataclass
class EvalScenario:
    """Result of evaluating a single scenario."""
    scenario_id: str
    scenario_name: str
    success: bool
    root_cause_predicted: str
    root_cause_expected: str
    root_cause_match: bool
    confidence: float
    severity_predicted: str
    severity_expected: str
    severity_match: bool
    services_predicted: list[str]
    services_expected: list[str]
    duration_ms: float
    tokens_used: int
    cost_usd: float
    error: str | None = None
    trace: list[str] = field(default_factory=list)


@dataclass
class EvalMetrics:
    """Combined metrics from evaluation."""
    accuracy: AccuracyMetrics
    latency: LatencyMetrics
    cost: CostMetrics

    def to_dict(self) -> dict[str, Any]:
        return {
            "accuracy": self.accuracy.to_dict(),
            "latency": self.latency.to_dict(),
            "cost": self.cost.to_dict(),
        }


@dataclass
class EvalResult:
    """Complete evaluation result."""
    eval_id: str
    started_at: datetime
    completed_at: datetime
    config: EvalConfig
    scenarios_total: int
    scenarios_passed: int
    scenarios_failed: int
    metrics: EvalMetrics
    scenario_results: list[EvalScenario]
    overall_passed: bool
    failure_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "eval_id": self.eval_id,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat(),
            "duration_seconds": (self.completed_at - self.started_at).total_seconds(),
            "scenarios_total": self.scenarios_total,
            "scenarios_passed": self.scenarios_passed,
            "scenarios_failed": self.scenarios_failed,
            "pass_rate": self.scenarios_passed / max(1, self.scenarios_total),
            "metrics": self.metrics.to_dict(),
            "overall_passed": self.overall_passed,
            "failure_reasons": self.failure_reasons,
        }


class EvalPipeline:
    """Main evaluation pipeline for nightly runs.

    Orchestrates:
    1. Loading evaluation scenarios
    2. Running agent against each scenario
    3. Collecting and calculating metrics
    4. Generating reports
    5. Alerting on regressions
    """

    def __init__(
        self,
        config: EvalConfig | None = None,
        investigator: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
    ):
        """Initialize the pipeline.

        Args:
            config: Evaluation configuration
            investigator: Function to run investigations (injected for testing)
        """
        self.config = config or EvalConfig()
        self._investigator = investigator

        self._loader = ScenarioLoader(self.config.scenarios_dir)
        self._metrics = MetricsCalculator(
            slo_threshold_ms=self.config.max_mean_latency_ms
        )

    async def run(self) -> EvalResult:
        """Run the full evaluation pipeline."""
        eval_id = f"eval-{uuid4().hex[:8]}"
        started_at = datetime.now(timezone.utc)

        log.info("Starting evaluation %s", eval_id)

        # Load scenarios
        scenarios = self._load_scenarios()
        log.info("Loaded %d scenarios", len(scenarios))

        # Run evaluations
        scenario_results = await self._run_scenarios(scenarios)

        # Calculate metrics
        metrics = self._calculate_metrics(scenario_results)

        # Determine pass/fail
        passed, failure_reasons = self._evaluate_thresholds(metrics, scenario_results)

        completed_at = datetime.now(timezone.utc)

        result = EvalResult(
            eval_id=eval_id,
            started_at=started_at,
            completed_at=completed_at,
            config=self.config,
            scenarios_total=len(scenarios),
            scenarios_passed=sum(1 for r in scenario_results if r.root_cause_match),
            scenarios_failed=sum(1 for r in scenario_results if not r.root_cause_match),
            metrics=metrics,
            scenario_results=scenario_results,
            overall_passed=passed,
            failure_reasons=failure_reasons,
        )

        # Save results
        if self.config.save_detailed_results:
            await self._save_results(result)

        log.info(
            "Evaluation %s completed: %d/%d passed (%.1f%%), overall: %s",
            eval_id,
            result.scenarios_passed,
            result.scenarios_total,
            result.scenarios_passed / max(1, result.scenarios_total) * 100,
            "PASSED" if passed else "FAILED",
        )

        return result

    def _load_scenarios(self) -> list[Scenario]:
        """Load and filter scenarios."""
        scenarios = self._loader.load_all()

        # Filter by tags
        if self.config.scenario_filter_tags:
            scenarios = [
                s for s in scenarios
                if any(t in s.tags for t in self.config.scenario_filter_tags)
            ]

        # Limit count
        if self.config.max_scenarios > 0:
            scenarios = scenarios[:self.config.max_scenarios]

        return scenarios

    async def _run_scenarios(self, scenarios: list[Scenario]) -> list[EvalScenario]:
        """Run all scenarios with parallel workers."""
        semaphore = asyncio.Semaphore(self.config.parallel_workers)

        async def run_one(scenario: Scenario) -> EvalScenario:
            async with semaphore:
                return await self._evaluate_scenario(scenario)

        tasks = [run_one(s) for s in scenarios]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Handle exceptions
        processed = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                processed.append(EvalScenario(
                    scenario_id=scenarios[i].id,
                    scenario_name=scenarios[i].name,
                    success=False,
                    root_cause_predicted="",
                    root_cause_expected=scenarios[i].expected.root_cause,
                    root_cause_match=False,
                    confidence=0.0,
                    severity_predicted="unknown",
                    severity_expected=scenarios[i].expected.severity,
                    severity_match=False,
                    services_predicted=[],
                    services_expected=scenarios[i].expected.affected_services,
                    duration_ms=0.0,
                    tokens_used=0,
                    cost_usd=0.0,
                    error=str(result),
                ))
            else:
                processed.append(result)

        return processed

    async def _evaluate_scenario(self, scenario: Scenario) -> EvalScenario:
        """Evaluate a single scenario."""
        start_time = time.time()

        try:
            # Run investigation
            if self._investigator:
                result = await asyncio.wait_for(
                    self._investigator(scenario.incident),
                    timeout=self.config.timeout_per_scenario_seconds,
                )
            else:
                # Mock result for testing
                result = await self._mock_investigation(scenario)

            duration_ms = (time.time() - start_time) * 1000

            # Extract results
            root_cause = result.get("root_cause", "")
            confidence = result.get("confidence", 0.0)
            severity = result.get("severity", "unknown")
            services = result.get("affected_services", [])
            tokens = result.get("tokens_used", 0)

            # Check correctness
            root_cause_match = scenario.expected.matches_root_cause(root_cause)
            severity_match = severity.lower() == scenario.expected.severity.lower()

            # Calculate cost
            cost = self._metrics.estimate_cost(tokens, self.config.model)

            return EvalScenario(
                scenario_id=scenario.id,
                scenario_name=scenario.name,
                success=True,
                root_cause_predicted=root_cause,
                root_cause_expected=scenario.expected.root_cause,
                root_cause_match=root_cause_match,
                confidence=confidence,
                severity_predicted=severity,
                severity_expected=scenario.expected.severity,
                severity_match=severity_match,
                services_predicted=services,
                services_expected=scenario.expected.affected_services,
                duration_ms=duration_ms,
                tokens_used=tokens,
                cost_usd=cost,
                trace=result.get("trace", []),
            )

        except asyncio.TimeoutError:
            return EvalScenario(
                scenario_id=scenario.id,
                scenario_name=scenario.name,
                success=False,
                root_cause_predicted="",
                root_cause_expected=scenario.expected.root_cause,
                root_cause_match=False,
                confidence=0.0,
                severity_predicted="unknown",
                severity_expected=scenario.expected.severity,
                severity_match=False,
                services_predicted=[],
                services_expected=scenario.expected.affected_services,
                duration_ms=self.config.timeout_per_scenario_seconds * 1000,
                tokens_used=0,
                cost_usd=0.0,
                error="Timeout",
            )

        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            return EvalScenario(
                scenario_id=scenario.id,
                scenario_name=scenario.name,
                success=False,
                root_cause_predicted="",
                root_cause_expected=scenario.expected.root_cause,
                root_cause_match=False,
                confidence=0.0,
                severity_predicted="unknown",
                severity_expected=scenario.expected.severity,
                severity_match=False,
                services_predicted=[],
                services_expected=scenario.expected.affected_services,
                duration_ms=duration_ms,
                tokens_used=0,
                cost_usd=0.0,
                error=str(e),
            )

    async def _mock_investigation(self, scenario: Scenario) -> dict[str, Any]:
        """Mock investigation for testing."""
        await asyncio.sleep(0.1)  # Simulate work
        return {
            "root_cause": scenario.expected.root_cause,
            "confidence": 0.85,
            "severity": scenario.expected.severity,
            "affected_services": scenario.expected.affected_services,
            "tokens_used": 5000,
            "trace": ["Analyzed logs", "Found root cause"],
        }

    def _calculate_metrics(self, results: list[EvalScenario]) -> EvalMetrics:
        """Calculate all metrics from results."""
        # Convert to EvalResultData
        data = [
            EvalResultData(
                scenario_id=r.scenario_id,
                root_cause_predicted=r.root_cause_predicted,
                root_cause_expected=r.root_cause_expected,
                is_correct=r.root_cause_match,
                confidence=r.confidence,
                severity_predicted=r.severity_predicted,
                severity_expected=r.severity_expected,
                services_predicted=r.services_predicted,
                services_expected=r.services_expected,
                duration_ms=r.duration_ms,
                tokens_used=r.tokens_used,
                cost_usd=r.cost_usd,
            )
            for r in results
        ]

        return EvalMetrics(
            accuracy=self._metrics.calculate_accuracy(data),
            latency=self._metrics.calculate_latency(data),
            cost=self._metrics.calculate_cost(data, self.config.model),
        )

    def _evaluate_thresholds(
        self,
        metrics: EvalMetrics,
        results: list[EvalScenario],
    ) -> tuple[bool, list[str]]:
        """Evaluate if metrics meet thresholds."""
        failures = []

        # Accuracy
        if metrics.accuracy.exact_match_rate < self.config.min_accuracy:
            failures.append(
                f"Accuracy {metrics.accuracy.exact_match_rate:.1%} < {self.config.min_accuracy:.1%}"
            )

        # Latency
        if metrics.latency.mean_ms > self.config.max_mean_latency_ms:
            failures.append(
                f"Mean latency {metrics.latency.mean_ms:.0f}ms > {self.config.max_mean_latency_ms:.0f}ms"
            )

        # Cost
        if metrics.cost.mean_cost_per_investigation_usd > self.config.max_cost_per_investigation_usd:
            failures.append(
                f"Mean cost ${metrics.cost.mean_cost_per_investigation_usd:.3f} > ${self.config.max_cost_per_investigation_usd:.3f}"
            )

        return len(failures) == 0, failures

    async def _save_results(self, result: EvalResult) -> None:
        """Save evaluation results to disk."""
        self.config.output_dir.mkdir(parents=True, exist_ok=True)

        # Save summary
        summary_path = self.config.output_dir / f"{result.eval_id}_summary.json"
        with open(summary_path, "w") as f:
            json.dump(result.to_dict(), f, indent=2, default=str)

        # Save detailed results
        if self.config.save_detailed_results:
            details_path = self.config.output_dir / f"{result.eval_id}_details.json"
            with open(details_path, "w") as f:
                json.dump(
                    [
                        {
                            "scenario_id": r.scenario_id,
                            "scenario_name": r.scenario_name,
                            "root_cause_match": r.root_cause_match,
                            "root_cause_predicted": r.root_cause_predicted,
                            "root_cause_expected": r.root_cause_expected,
                            "confidence": r.confidence,
                            "duration_ms": r.duration_ms,
                            "tokens_used": r.tokens_used,
                            "error": r.error,
                        }
                        for r in result.scenario_results
                    ],
                    f,
                    indent=2,
                )

        log.info("Results saved to %s", self.config.output_dir)
