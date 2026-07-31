"""Router Metrics - Performance tracking for model routing."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any
from collections import defaultdict

log = logging.getLogger("devcopilot.router.metrics")


@dataclass
class ModelPerformance:
    """Performance metrics for a model."""
    model: str

    # Request counts
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0

    # Latency (milliseconds)
    total_latency_ms: float = 0.0
    min_latency_ms: float = float("inf")
    max_latency_ms: float = 0.0

    # Tokens
    total_input_tokens: int = 0
    total_output_tokens: int = 0

    # Cost
    total_cost_usd: float = 0.0

    # Quality
    total_confidence: float = 0.0
    correct_predictions: int = 0

    # By complexity
    requests_by_complexity: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    success_by_complexity: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    @property
    def success_rate(self) -> float:
        """Calculate success rate."""
        if self.total_requests == 0:
            return 0.0
        return self.successful_requests / self.total_requests

    @property
    def avg_latency_ms(self) -> float:
        """Calculate average latency."""
        if self.successful_requests == 0:
            return 0.0
        return self.total_latency_ms / self.successful_requests

    @property
    def avg_confidence(self) -> float:
        """Calculate average confidence."""
        if self.successful_requests == 0:
            return 0.0
        return self.total_confidence / self.successful_requests

    @property
    def accuracy(self) -> float:
        """Calculate accuracy (for tasks with ground truth)."""
        if self.successful_requests == 0:
            return 0.0
        return self.correct_predictions / self.successful_requests

    @property
    def avg_cost_per_request(self) -> float:
        """Calculate average cost per request."""
        if self.total_requests == 0:
            return 0.0
        return self.total_cost_usd / self.total_requests

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "total_requests": self.total_requests,
            "successful_requests": self.successful_requests,
            "failed_requests": self.failed_requests,
            "success_rate": self.success_rate,
            "avg_latency_ms": self.avg_latency_ms,
            "min_latency_ms": self.min_latency_ms if self.min_latency_ms != float("inf") else 0,
            "max_latency_ms": self.max_latency_ms,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_cost_usd": self.total_cost_usd,
            "avg_cost_per_request": self.avg_cost_per_request,
            "avg_confidence": self.avg_confidence,
            "accuracy": self.accuracy,
            "requests_by_complexity": dict(self.requests_by_complexity),
        }


@dataclass
class RequestRecord:
    """Record of a single request."""
    model: str
    complexity: str
    task_type: str
    latency_ms: float
    input_tokens: int
    output_tokens: int
    cost_usd: float
    success: bool
    confidence: float
    correct: bool | None  # None if no ground truth
    timestamp: datetime


class RouterMetrics:
    """Collects and analyzes routing metrics.

    Tracks:
    - Per-model performance
    - Per-complexity success rates
    - Latency distributions
    - Cost analysis
    - Routing decision quality
    """

    def __init__(self, window_hours: int = 24):
        """Initialize the metrics collector.

        Args:
            window_hours: Time window for metrics (default 24h)
        """
        self.window_hours = window_hours
        self._records: list[RequestRecord] = []
        self._model_metrics: dict[str, ModelPerformance] = {}

    def record_request(
        self,
        model: str,
        complexity: str,
        task_type: str,
        latency_ms: float,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
        success: bool,
        confidence: float = 0.0,
        correct: bool | None = None,
    ) -> None:
        """Record a completed request.

        Args:
            model: Model used
            complexity: Task complexity level
            task_type: Type of task
            latency_ms: Request latency
            input_tokens: Input token count
            output_tokens: Output token count
            cost_usd: Cost in USD
            success: Whether request succeeded
            confidence: Model's confidence
            correct: Whether prediction was correct (if known)
        """
        record = RequestRecord(
            model=model,
            complexity=complexity,
            task_type=task_type,
            latency_ms=latency_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
            success=success,
            confidence=confidence,
            correct=correct,
            timestamp=datetime.now(timezone.utc),
        )

        self._records.append(record)
        self._update_model_metrics(record)

        # Prune old records
        self._prune_old_records()

    def _update_model_metrics(self, record: RequestRecord) -> None:
        """Update model metrics with a new record."""
        if record.model not in self._model_metrics:
            self._model_metrics[record.model] = ModelPerformance(model=record.model)

        metrics = self._model_metrics[record.model]
        metrics.total_requests += 1
        metrics.requests_by_complexity[record.complexity] += 1

        if record.success:
            metrics.successful_requests += 1
            metrics.success_by_complexity[record.complexity] += 1
            metrics.total_latency_ms += record.latency_ms
            metrics.min_latency_ms = min(metrics.min_latency_ms, record.latency_ms)
            metrics.max_latency_ms = max(metrics.max_latency_ms, record.latency_ms)
            metrics.total_confidence += record.confidence

            if record.correct:
                metrics.correct_predictions += 1
        else:
            metrics.failed_requests += 1

        metrics.total_input_tokens += record.input_tokens
        metrics.total_output_tokens += record.output_tokens
        metrics.total_cost_usd += record.cost_usd

    def _prune_old_records(self) -> None:
        """Remove records outside the time window."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=self.window_hours)
        self._records = [r for r in self._records if r.timestamp > cutoff]

    def get_model_performance(self, model: str) -> ModelPerformance | None:
        """Get performance metrics for a specific model."""
        return self._model_metrics.get(model)

    def get_all_performance(self) -> dict[str, ModelPerformance]:
        """Get performance metrics for all models."""
        return self._model_metrics.copy()

    def get_best_model_for_complexity(self, complexity: str) -> str | None:
        """Get the best performing model for a complexity level.

        Uses a score combining success rate, latency, and cost.
        """
        scores: dict[str, float] = {}

        for model, metrics in self._model_metrics.items():
            requests = metrics.requests_by_complexity.get(complexity, 0)
            if requests < 5:  # Need minimum samples
                continue

            successes = metrics.success_by_complexity.get(complexity, 0)
            success_rate = successes / requests

            # Score: success_rate * (1 / normalized_latency) * (1 / normalized_cost)
            avg_latency = metrics.avg_latency_ms or 1000
            avg_cost = metrics.avg_cost_per_request or 0.01

            # Normalize (lower is better for latency and cost)
            latency_score = 1000 / max(avg_latency, 100)  # Cap at 100ms
            cost_score = 0.10 / max(avg_cost, 0.001)  # Cap at $0.001

            scores[model] = success_rate * latency_score * cost_score

        if not scores:
            return None

        return max(scores, key=lambda m: scores[m])

    def get_summary(self) -> dict[str, Any]:
        """Get a summary of routing metrics."""
        total_requests = sum(m.total_requests for m in self._model_metrics.values())
        total_cost = sum(m.total_cost_usd for m in self._model_metrics.values())

        model_distribution = {
            model: metrics.total_requests / total_requests if total_requests > 0 else 0
            for model, metrics in self._model_metrics.items()
        }

        return {
            "window_hours": self.window_hours,
            "total_requests": total_requests,
            "total_cost_usd": total_cost,
            "models": {
                model: metrics.to_dict()
                for model, metrics in self._model_metrics.items()
            },
            "model_distribution": model_distribution,
            "records_count": len(self._records),
        }

    def get_recommendations(self) -> list[dict[str, Any]]:
        """Get routing recommendations based on metrics."""
        recommendations = []

        for model, metrics in self._model_metrics.items():
            # High failure rate
            if metrics.success_rate < 0.9 and metrics.total_requests >= 10:
                recommendations.append({
                    "type": "warning",
                    "model": model,
                    "message": f"Low success rate ({metrics.success_rate:.0%})",
                    "action": "Consider reducing routing weight",
                })

            # High latency
            if metrics.avg_latency_ms > 30000 and metrics.successful_requests >= 5:
                recommendations.append({
                    "type": "info",
                    "model": model,
                    "message": f"High average latency ({metrics.avg_latency_ms:.0f}ms)",
                    "action": "Use for complex tasks only",
                })

            # High cost
            if metrics.avg_cost_per_request > 0.50 and metrics.total_requests >= 10:
                recommendations.append({
                    "type": "info",
                    "model": model,
                    "message": f"High cost per request (${metrics.avg_cost_per_request:.3f})",
                    "action": "Reserve for high-value tasks",
                })

        return recommendations

    def reset(self) -> None:
        """Reset all metrics."""
        self._records.clear()
        self._model_metrics.clear()
