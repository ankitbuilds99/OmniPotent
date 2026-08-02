"""Entry/Exit Criteria Evaluator.

Manages pre-conditions (entry criteria) and post-conditions (exit criteria)
for change operations:
- Service health checks
- Dependency validation
- Canary/rollout success criteria
- Rollback triggers
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Awaitable
import re

log = logging.getLogger("omnipotent.policy_engine.criteria")


class CriteriaType(str, Enum):
    """Types of criteria."""
    HEALTH_CHECK = "health_check"
    METRIC_THRESHOLD = "metric_threshold"
    ERROR_RATE = "error_rate"
    LATENCY = "latency"
    DEPENDENCY_READY = "dependency_ready"
    CUSTOM = "custom"


class CriteriaOperator(str, Enum):
    """Comparison operators for criteria."""
    LESS_THAN = "lt"
    LESS_THAN_OR_EQUAL = "lte"
    GREATER_THAN = "gt"
    GREATER_THAN_OR_EQUAL = "gte"
    EQUAL = "eq"
    NOT_EQUAL = "ne"
    BETWEEN = "between"
    REGEX_MATCH = "regex"


@dataclass
class Criterion:
    """A single criterion to evaluate."""
    name: str
    criteria_type: CriteriaType
    operator: CriteriaOperator
    threshold: float | str | tuple[float, float]

    # Source configuration
    metric_name: str | None = None
    query: str | None = None  # Prometheus/InfluxDB query
    endpoint: str | None = None  # Health check endpoint

    # Evaluation settings
    evaluation_window: int = 60  # seconds
    consecutive_failures: int = 1  # failures before criterion fails
    weight: float = 1.0  # importance weight

    # Metadata
    description: str = ""
    remediation_hint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "criteria_type": self.criteria_type.value,
            "operator": self.operator.value,
            "threshold": self.threshold,
            "metric_name": self.metric_name,
            "query": self.query,
            "endpoint": self.endpoint,
            "evaluation_window": self.evaluation_window,
            "weight": self.weight,
            "description": self.description,
        }


@dataclass
class EntryCriteria:
    """Entry criteria that must be met before a change."""
    name: str
    criteria: list[Criterion]
    require_all: bool = True  # AND vs OR
    timeout_seconds: int = 300
    skip_on_emergency: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "criteria": [c.to_dict() for c in self.criteria],
            "require_all": self.require_all,
            "timeout_seconds": self.timeout_seconds,
            "skip_on_emergency": self.skip_on_emergency,
        }


@dataclass
class ExitCriteria:
    """Exit criteria that must be met after a change."""
    name: str
    criteria: list[Criterion]
    require_all: bool = True
    stabilization_period: int = 60  # seconds to maintain criteria
    timeout_seconds: int = 600
    rollback_on_failure: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "criteria": [c.to_dict() for c in self.criteria],
            "require_all": self.require_all,
            "stabilization_period": self.stabilization_period,
            "timeout_seconds": self.timeout_seconds,
            "rollback_on_failure": self.rollback_on_failure,
        }


@dataclass
class CriterionEvaluation:
    """Result of evaluating a single criterion."""
    criterion: Criterion
    passed: bool
    actual_value: float | str | None
    expected: str  # Human-readable expectation
    message: str
    evaluated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "criterion_name": self.criterion.name,
            "passed": self.passed,
            "actual_value": self.actual_value,
            "expected": self.expected,
            "message": self.message,
            "evaluated_at": self.evaluated_at.isoformat(),
        }


@dataclass
class CriteriaResult:
    """Result of evaluating entry or exit criteria."""
    passed: bool
    criteria_name: str
    evaluations: list[CriterionEvaluation]
    passed_count: int
    failed_count: int
    skipped_count: int
    duration_ms: float
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "criteria_name": self.criteria_name,
            "evaluations": [e.to_dict() for e in self.evaluations],
            "passed_count": self.passed_count,
            "failed_count": self.failed_count,
            "skipped_count": self.skipped_count,
            "duration_ms": self.duration_ms,
            "message": self.message,
        }


# Type alias for metric fetchers
MetricFetcher = Callable[[str, int], Awaitable[float | None]]
HealthChecker = Callable[[str], Awaitable[bool]]


class CriteriaEvaluator:
    """Evaluates entry and exit criteria for changes.

    Features:
    - Multiple criterion types (health, metrics, latency, errors)
    - Configurable operators and thresholds
    - Stabilization period support
    - Weighted evaluation
    - Async evaluation with timeout
    """

    def __init__(
        self,
        metric_fetcher: MetricFetcher | None = None,
        health_checker: HealthChecker | None = None,
    ):
        self._metric_fetcher = metric_fetcher or self._default_metric_fetcher
        self._health_checker = health_checker or self._default_health_checker
        self._custom_evaluators: dict[str, Callable] = {}

        # Default criteria templates
        self._entry_templates: dict[str, EntryCriteria] = {}
        self._exit_templates: dict[str, ExitCriteria] = {}
        self._create_default_templates()

    def _create_default_templates(self) -> None:
        """Create default criteria templates."""
        # Standard entry criteria for deployments
        self._entry_templates["deployment"] = EntryCriteria(
            name="Standard Deployment Entry",
            criteria=[
                Criterion(
                    name="service_healthy",
                    criteria_type=CriteriaType.HEALTH_CHECK,
                    operator=CriteriaOperator.EQUAL,
                    threshold="healthy",
                    description="Service must be healthy before deployment",
                ),
                Criterion(
                    name="error_rate_baseline",
                    criteria_type=CriteriaType.ERROR_RATE,
                    operator=CriteriaOperator.LESS_THAN,
                    threshold=0.01,  # 1%
                    metric_name="error_rate_5m",
                    description="Error rate must be below 1%",
                ),
                Criterion(
                    name="no_active_incidents",
                    criteria_type=CriteriaType.CUSTOM,
                    operator=CriteriaOperator.EQUAL,
                    threshold=0,
                    metric_name="active_incidents_count",
                    description="No active incidents for this service",
                ),
            ],
            require_all=True,
            timeout_seconds=300,
        )

        # Standard exit criteria for deployments
        self._exit_templates["deployment"] = ExitCriteria(
            name="Standard Deployment Exit",
            criteria=[
                Criterion(
                    name="service_healthy",
                    criteria_type=CriteriaType.HEALTH_CHECK,
                    operator=CriteriaOperator.EQUAL,
                    threshold="healthy",
                    description="Service must be healthy after deployment",
                    consecutive_failures=3,
                ),
                Criterion(
                    name="error_rate_stable",
                    criteria_type=CriteriaType.ERROR_RATE,
                    operator=CriteriaOperator.LESS_THAN,
                    threshold=0.02,  # 2% (slightly higher post-deploy)
                    metric_name="error_rate_5m",
                    description="Error rate must remain below 2%",
                ),
                Criterion(
                    name="latency_p99",
                    criteria_type=CriteriaType.LATENCY,
                    operator=CriteriaOperator.LESS_THAN,
                    threshold=500.0,  # 500ms
                    metric_name="latency_p99_ms",
                    description="P99 latency must be under 500ms",
                ),
            ],
            require_all=True,
            stabilization_period=120,  # 2 minutes stable
            timeout_seconds=600,
            rollback_on_failure=True,
        )

        # Canary criteria
        self._exit_templates["canary"] = ExitCriteria(
            name="Canary Success Criteria",
            criteria=[
                Criterion(
                    name="canary_error_rate",
                    criteria_type=CriteriaType.ERROR_RATE,
                    operator=CriteriaOperator.LESS_THAN_OR_EQUAL,
                    threshold=0.01,
                    metric_name="canary_error_rate",
                    description="Canary error rate <= baseline",
                    weight=2.0,
                ),
                Criterion(
                    name="canary_latency_comparable",
                    criteria_type=CriteriaType.LATENCY,
                    operator=CriteriaOperator.LESS_THAN_OR_EQUAL,
                    threshold=1.1,  # 10% slower max
                    metric_name="canary_latency_ratio",
                    description="Canary latency within 10% of baseline",
                ),
                Criterion(
                    name="canary_success_rate",
                    criteria_type=CriteriaType.METRIC_THRESHOLD,
                    operator=CriteriaOperator.GREATER_THAN_OR_EQUAL,
                    threshold=0.99,
                    metric_name="canary_success_rate",
                    description="Canary success rate >= 99%",
                ),
            ],
            require_all=True,
            stabilization_period=300,  # 5 minutes stable
            timeout_seconds=1800,  # 30 minutes total
            rollback_on_failure=True,
        )

    async def evaluate_entry_criteria(
        self,
        criteria: EntryCriteria,
        context: dict[str, Any] | None = None,
    ) -> CriteriaResult:
        """Evaluate entry criteria before a change.

        Args:
            criteria: Entry criteria to evaluate
            context: Additional context for evaluation

        Returns:
            CriteriaResult with evaluation details
        """
        import time
        start_time = time.monotonic()

        evaluations: list[CriterionEvaluation] = []
        passed_count = 0
        failed_count = 0
        skipped_count = 0

        try:
            async with asyncio.timeout(criteria.timeout_seconds):
                for criterion in criteria.criteria:
                    try:
                        evaluation = await self._evaluate_criterion(criterion, context)
                        evaluations.append(evaluation)

                        if evaluation.passed:
                            passed_count += 1
                        else:
                            failed_count += 1

                            # If require_all and one fails, short-circuit
                            if criteria.require_all:
                                break

                    except Exception as e:
                        log.error("Error evaluating criterion %s: %s", criterion.name, e)
                        evaluations.append(CriterionEvaluation(
                            criterion=criterion,
                            passed=False,
                            actual_value=None,
                            expected=str(criterion.threshold),
                            message=f"Evaluation error: {e}",
                        ))
                        failed_count += 1

                        if criteria.require_all:
                            break

        except asyncio.TimeoutError:
            log.warning("Entry criteria evaluation timed out after %ds", criteria.timeout_seconds)
            return CriteriaResult(
                passed=False,
                criteria_name=criteria.name,
                evaluations=evaluations,
                passed_count=passed_count,
                failed_count=failed_count,
                skipped_count=len(criteria.criteria) - len(evaluations),
                duration_ms=(time.monotonic() - start_time) * 1000,
                message=f"Evaluation timed out after {criteria.timeout_seconds}s",
            )

        duration_ms = (time.monotonic() - start_time) * 1000

        # Determine overall result
        if criteria.require_all:
            passed = failed_count == 0
        else:
            passed = passed_count > 0

        if passed:
            message = f"All {passed_count} entry criteria passed"
        else:
            failed_names = [e.criterion.name for e in evaluations if not e.passed]
            message = f"Entry criteria failed: {', '.join(failed_names)}"

        return CriteriaResult(
            passed=passed,
            criteria_name=criteria.name,
            evaluations=evaluations,
            passed_count=passed_count,
            failed_count=failed_count,
            skipped_count=skipped_count,
            duration_ms=duration_ms,
            message=message,
        )

    async def evaluate_exit_criteria(
        self,
        criteria: ExitCriteria,
        context: dict[str, Any] | None = None,
    ) -> CriteriaResult:
        """Evaluate exit criteria after a change with stabilization period.

        Args:
            criteria: Exit criteria to evaluate
            context: Additional context for evaluation

        Returns:
            CriteriaResult with evaluation details
        """
        import time
        start_time = time.monotonic()

        evaluations: list[CriterionEvaluation] = []
        stable_since: datetime | None = None
        check_interval = min(10, criteria.stabilization_period // 6)  # Check every 10s or less

        try:
            async with asyncio.timeout(criteria.timeout_seconds):
                while True:
                    # Evaluate all criteria
                    current_evaluations = []
                    all_passed = True

                    for criterion in criteria.criteria:
                        try:
                            evaluation = await self._evaluate_criterion(criterion, context)
                            current_evaluations.append(evaluation)

                            if not evaluation.passed:
                                all_passed = False

                        except Exception as e:
                            log.error("Error evaluating criterion %s: %s", criterion.name, e)
                            current_evaluations.append(CriterionEvaluation(
                                criterion=criterion,
                                passed=False,
                                actual_value=None,
                                expected=str(criterion.threshold),
                                message=f"Evaluation error: {e}",
                            ))
                            all_passed = False

                    evaluations = current_evaluations

                    if all_passed:
                        if stable_since is None:
                            stable_since = datetime.now(timezone.utc)
                            log.info("Exit criteria passing, starting stabilization period")

                        # Check if we've been stable long enough
                        stable_duration = (datetime.now(timezone.utc) - stable_since).total_seconds()
                        if stable_duration >= criteria.stabilization_period:
                            break
                    else:
                        # Reset stabilization if criteria fail
                        if stable_since is not None:
                            log.warning("Criteria failed during stabilization, resetting")
                        stable_since = None

                    await asyncio.sleep(check_interval)

        except asyncio.TimeoutError:
            log.warning("Exit criteria evaluation timed out after %ds", criteria.timeout_seconds)
            passed_count = sum(1 for e in evaluations if e.passed)
            failed_count = len(evaluations) - passed_count

            return CriteriaResult(
                passed=False,
                criteria_name=criteria.name,
                evaluations=evaluations,
                passed_count=passed_count,
                failed_count=failed_count,
                skipped_count=0,
                duration_ms=(time.monotonic() - start_time) * 1000,
                message=f"Exit criteria not stable within {criteria.timeout_seconds}s timeout",
            )

        duration_ms = (time.monotonic() - start_time) * 1000
        passed_count = sum(1 for e in evaluations if e.passed)
        failed_count = len(evaluations) - passed_count

        return CriteriaResult(
            passed=True,
            criteria_name=criteria.name,
            evaluations=evaluations,
            passed_count=passed_count,
            failed_count=failed_count,
            skipped_count=0,
            duration_ms=duration_ms,
            message=f"Exit criteria stable for {criteria.stabilization_period}s",
        )

    async def _evaluate_criterion(
        self,
        criterion: Criterion,
        context: dict[str, Any] | None = None,
    ) -> CriterionEvaluation:
        """Evaluate a single criterion."""
        context = context or {}

        if criterion.criteria_type == CriteriaType.HEALTH_CHECK:
            return await self._evaluate_health_check(criterion, context)
        elif criterion.criteria_type == CriteriaType.METRIC_THRESHOLD:
            return await self._evaluate_metric(criterion, context)
        elif criterion.criteria_type == CriteriaType.ERROR_RATE:
            return await self._evaluate_metric(criterion, context)
        elif criterion.criteria_type == CriteriaType.LATENCY:
            return await self._evaluate_metric(criterion, context)
        elif criterion.criteria_type == CriteriaType.DEPENDENCY_READY:
            return await self._evaluate_dependency(criterion, context)
        elif criterion.criteria_type == CriteriaType.CUSTOM:
            return await self._evaluate_custom(criterion, context)
        else:
            return CriterionEvaluation(
                criterion=criterion,
                passed=False,
                actual_value=None,
                expected=str(criterion.threshold),
                message=f"Unknown criterion type: {criterion.criteria_type}",
            )

    async def _evaluate_health_check(
        self,
        criterion: Criterion,
        context: dict[str, Any],
    ) -> CriterionEvaluation:
        """Evaluate a health check criterion."""
        endpoint = criterion.endpoint or context.get("health_endpoint", "/health")

        try:
            is_healthy = await self._health_checker(endpoint)
            actual = "healthy" if is_healthy else "unhealthy"
            passed = self._compare(actual, criterion.operator, criterion.threshold)

            return CriterionEvaluation(
                criterion=criterion,
                passed=passed,
                actual_value=actual,
                expected=f"{criterion.operator.value} {criterion.threshold}",
                message="Health check passed" if passed else "Health check failed",
            )
        except Exception as e:
            return CriterionEvaluation(
                criterion=criterion,
                passed=False,
                actual_value=None,
                expected=str(criterion.threshold),
                message=f"Health check error: {e}",
            )

    async def _evaluate_metric(
        self,
        criterion: Criterion,
        context: dict[str, Any],
    ) -> CriterionEvaluation:
        """Evaluate a metric-based criterion."""
        metric_name = criterion.metric_name or criterion.name

        try:
            value = await self._metric_fetcher(metric_name, criterion.evaluation_window)

            if value is None:
                return CriterionEvaluation(
                    criterion=criterion,
                    passed=False,
                    actual_value=None,
                    expected=f"{criterion.operator.value} {criterion.threshold}",
                    message=f"Metric '{metric_name}' not available",
                )

            passed = self._compare(value, criterion.operator, criterion.threshold)

            return CriterionEvaluation(
                criterion=criterion,
                passed=passed,
                actual_value=value,
                expected=f"{criterion.operator.value} {criterion.threshold}",
                message=f"Metric {metric_name}={value:.4f}" if passed else f"Metric {metric_name}={value:.4f} failed threshold",
            )
        except Exception as e:
            return CriterionEvaluation(
                criterion=criterion,
                passed=False,
                actual_value=None,
                expected=str(criterion.threshold),
                message=f"Metric fetch error: {e}",
            )

    async def _evaluate_dependency(
        self,
        criterion: Criterion,
        context: dict[str, Any],
    ) -> CriterionEvaluation:
        """Evaluate a dependency readiness criterion."""
        dependencies = context.get("dependencies", [])
        target = criterion.metric_name or criterion.name

        # Check if dependency is in the ready list
        is_ready = target in dependencies or any(
            d.get("name") == target and d.get("status") == "ready"
            for d in dependencies if isinstance(d, dict)
        )

        return CriterionEvaluation(
            criterion=criterion,
            passed=is_ready,
            actual_value="ready" if is_ready else "not_ready",
            expected="ready",
            message=f"Dependency '{target}' is ready" if is_ready else f"Dependency '{target}' not ready",
        )

    async def _evaluate_custom(
        self,
        criterion: Criterion,
        context: dict[str, Any],
    ) -> CriterionEvaluation:
        """Evaluate a custom criterion."""
        # Check if custom evaluator is registered
        if criterion.name in self._custom_evaluators:
            try:
                result = await self._custom_evaluators[criterion.name](criterion, context)
                return result
            except Exception as e:
                return CriterionEvaluation(
                    criterion=criterion,
                    passed=False,
                    actual_value=None,
                    expected=str(criterion.threshold),
                    message=f"Custom evaluator error: {e}",
                )

        # Try to get value from context
        value = context.get(criterion.metric_name or criterion.name)
        if value is not None:
            passed = self._compare(value, criterion.operator, criterion.threshold)
            return CriterionEvaluation(
                criterion=criterion,
                passed=passed,
                actual_value=value,
                expected=f"{criterion.operator.value} {criterion.threshold}",
                message="Custom criterion passed" if passed else "Custom criterion failed",
            )

        # Fall back to metric fetcher
        return await self._evaluate_metric(criterion, context)

    def _compare(
        self,
        actual: float | str,
        operator: CriteriaOperator,
        threshold: float | str | tuple[float, float],
    ) -> bool:
        """Compare actual value against threshold using operator."""
        if operator == CriteriaOperator.LESS_THAN:
            thresh = float(threshold) if not isinstance(threshold, tuple) else threshold[0]
            return float(actual) < thresh
        elif operator == CriteriaOperator.LESS_THAN_OR_EQUAL:
            thresh = float(threshold) if not isinstance(threshold, tuple) else threshold[0]
            return float(actual) <= thresh
        elif operator == CriteriaOperator.GREATER_THAN:
            thresh = float(threshold) if not isinstance(threshold, tuple) else threshold[0]
            return float(actual) > thresh
        elif operator == CriteriaOperator.GREATER_THAN_OR_EQUAL:
            thresh = float(threshold) if not isinstance(threshold, tuple) else threshold[0]
            return float(actual) >= thresh
        elif operator == CriteriaOperator.EQUAL:
            return str(actual) == str(threshold)
        elif operator == CriteriaOperator.NOT_EQUAL:
            return str(actual) != str(threshold)
        elif operator == CriteriaOperator.BETWEEN:
            if isinstance(threshold, tuple):
                return threshold[0] <= float(actual) <= threshold[1]
            return False
        elif operator == CriteriaOperator.REGEX_MATCH:
            return bool(re.match(str(threshold), str(actual)))
        else:
            return False

    def register_custom_evaluator(
        self,
        name: str,
        evaluator: Callable[[Criterion, dict[str, Any]], Awaitable[CriterionEvaluation]],
    ) -> None:
        """Register a custom criterion evaluator."""
        self._custom_evaluators[name] = evaluator
        log.info("Registered custom criterion evaluator: %s", name)

    def get_entry_template(self, name: str) -> EntryCriteria | None:
        """Get an entry criteria template by name."""
        return self._entry_templates.get(name)

    def get_exit_template(self, name: str) -> ExitCriteria | None:
        """Get an exit criteria template by name."""
        return self._exit_templates.get(name)

    def add_entry_template(self, name: str, criteria: EntryCriteria) -> None:
        """Add an entry criteria template."""
        self._entry_templates[name] = criteria

    def add_exit_template(self, name: str, criteria: ExitCriteria) -> None:
        """Add an exit criteria template."""
        self._exit_templates[name] = criteria

    async def _default_metric_fetcher(self, metric_name: str, window: int) -> float | None:
        """Default metric fetcher - queries Prometheus for real metrics."""
        try:
            from app.connectors.prometheus import PrometheusConnector
            from app.connectors.base import ConnectorConfig
            import os

            prometheus_url = os.environ.get("PROMETHEUS_URL", "http://localhost:9090")
            config = ConnectorConfig(url=prometheus_url)
            prometheus = PrometheusConnector(config)

            metric_queries = {
                "error_rate_5m": f'sum(rate(http_requests_total{{status=~"5.."}}[{window}s])) / sum(rate(http_requests_total[{window}s]))',
                "latency_p99_ms": f'histogram_quantile(0.99, sum(rate(http_request_duration_seconds_bucket[{window}s])) by (le)) * 1000',
                "active_incidents_count": 'count(ALERTS{alertstate="firing"})',
                "canary_error_rate": f'sum(rate(http_requests_total{{status=~"5..", deployment="canary"}}[{window}s])) / sum(rate(http_requests_total{{deployment="canary"}}[{window}s]))',
                "canary_latency_ratio": f'histogram_quantile(0.99, sum(rate(http_request_duration_seconds_bucket{{deployment="canary"}}[{window}s])) by (le)) / histogram_quantile(0.99, sum(rate(http_request_duration_seconds_bucket{{deployment="stable"}}[{window}s])) by (le))',
                "canary_success_rate": f'sum(rate(http_requests_total{{status=~"2..", deployment="canary"}}[{window}s])) / sum(rate(http_requests_total{{deployment="canary"}}[{window}s]))',
                "cpu_usage": 'avg(rate(container_cpu_usage_seconds_total[5m]))',
                "memory_usage": 'avg(container_memory_usage_bytes) / avg(container_spec_memory_limit_bytes)',
            }

            query = metric_queries.get(metric_name, metric_name)
            result = await prometheus.query(query)
            await prometheus.close()

            if result.success and result.data:
                value = result.data[0].get("value") if result.data else None
                return float(value) if value is not None else None

            return None

        except Exception as e:
            log.warning("Failed to fetch metric %s from Prometheus: %s", metric_name, e)
            return None

    async def _default_health_checker(self, endpoint: str) -> bool:
        """Default health checker - makes real HTTP health check requests."""
        try:
            import httpx

            if not endpoint.startswith(("http://", "https://")):
                endpoint = f"http://{endpoint}"

            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(endpoint)
                return response.status_code in (200, 204)

        except Exception as e:
            log.warning("Health check failed for %s: %s", endpoint, e)
            return False


def create_criteria_evaluator(
    metric_fetcher: MetricFetcher | None = None,
    health_checker: HealthChecker | None = None,
) -> CriteriaEvaluator:
    """Factory function to create a criteria evaluator."""
    return CriteriaEvaluator(
        metric_fetcher=metric_fetcher,
        health_checker=health_checker,
    )
