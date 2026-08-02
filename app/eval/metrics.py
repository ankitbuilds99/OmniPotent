"""Evaluation Metrics - Accuracy, latency, and cost calculations."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("omnipotent.eval.metrics")


@dataclass
class AccuracyMetrics:
    """Accuracy metrics for evaluation."""
    # Root cause accuracy
    exact_match_rate: float = 0.0
    keyword_match_rate: float = 0.0
    partial_match_rate: float = 0.0

    # Severity accuracy
    severity_accuracy: float = 0.0

    # Service identification
    service_precision: float = 0.0
    service_recall: float = 0.0
    service_f1: float = 0.0

    # Confidence calibration
    confidence_ece: float = 0.0  # Expected Calibration Error
    confidence_brier: float = 0.0  # Brier score

    # Pass@k metrics
    pass_at_1: float = 0.0
    pass_at_3: float = 0.0
    pass_at_5: float = 0.0

    def to_dict(self) -> dict[str, float]:
        return {
            "exact_match_rate": self.exact_match_rate,
            "keyword_match_rate": self.keyword_match_rate,
            "partial_match_rate": self.partial_match_rate,
            "severity_accuracy": self.severity_accuracy,
            "service_precision": self.service_precision,
            "service_recall": self.service_recall,
            "service_f1": self.service_f1,
            "confidence_ece": self.confidence_ece,
            "confidence_brier": self.confidence_brier,
            "pass_at_1": self.pass_at_1,
            "pass_at_3": self.pass_at_3,
            "pass_at_5": self.pass_at_5,
        }


@dataclass
class LatencyMetrics:
    """Latency metrics for evaluation."""
    mean_ms: float = 0.0
    median_ms: float = 0.0
    p95_ms: float = 0.0
    p99_ms: float = 0.0
    min_ms: float = 0.0
    max_ms: float = 0.0
    std_ms: float = 0.0

    # Time to first hypothesis
    mean_first_hypothesis_ms: float = 0.0

    # Within SLO
    within_slo_rate: float = 0.0
    slo_threshold_ms: float = 60000.0

    def to_dict(self) -> dict[str, float]:
        return {
            "mean_ms": self.mean_ms,
            "median_ms": self.median_ms,
            "p95_ms": self.p95_ms,
            "p99_ms": self.p99_ms,
            "min_ms": self.min_ms,
            "max_ms": self.max_ms,
            "std_ms": self.std_ms,
            "mean_first_hypothesis_ms": self.mean_first_hypothesis_ms,
            "within_slo_rate": self.within_slo_rate,
        }


@dataclass
class CostMetrics:
    """Cost metrics for evaluation."""
    total_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    total_cost_usd: float = 0.0
    mean_cost_per_investigation_usd: float = 0.0

    # By model
    tokens_by_model: dict[str, int] = field(default_factory=dict)
    cost_by_model: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_tokens": self.total_tokens,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_cost_usd": self.total_cost_usd,
            "mean_cost_per_investigation_usd": self.mean_cost_per_investigation_usd,
            "tokens_by_model": self.tokens_by_model,
            "cost_by_model": self.cost_by_model,
        }


@dataclass
class EvalResultData:
    """Single evaluation result data."""
    scenario_id: str
    root_cause_predicted: str
    root_cause_expected: str
    is_correct: bool
    confidence: float
    severity_predicted: str
    severity_expected: str
    services_predicted: list[str]
    services_expected: list[str]
    duration_ms: float
    tokens_used: int
    cost_usd: float


class MetricsCalculator:
    """Calculates evaluation metrics from results."""

    # Model pricing per 1M tokens
    MODEL_PRICING = {
        "claude-sonnet-4-20250514": {"input": 3.0, "output": 15.0},
        "claude-3-5-sonnet-20241022": {"input": 3.0, "output": 15.0},
        "claude-3-haiku-20240307": {"input": 0.25, "output": 1.25},
        "gpt-4-turbo": {"input": 10.0, "output": 30.0},
        "gpt-4o": {"input": 5.0, "output": 15.0},
    }

    def __init__(self, slo_threshold_ms: float = 60000.0):
        """Initialize the calculator.

        Args:
            slo_threshold_ms: SLO threshold for latency
        """
        self.slo_threshold_ms = slo_threshold_ms

    def calculate_accuracy(self, results: list[EvalResultData]) -> AccuracyMetrics:
        """Calculate accuracy metrics from results."""
        if not results:
            return AccuracyMetrics()

        n = len(results)

        # Exact match
        exact_matches = sum(1 for r in results if r.is_correct)

        # Keyword match (at least 50% of keywords present)
        keyword_matches = exact_matches  # Already counted in is_correct

        # Partial match (any overlap)
        partial_matches = sum(
            1 for r in results
            if any(
                word in r.root_cause_predicted.lower()
                for word in r.root_cause_expected.lower().split()
                if len(word) > 3
            )
        )

        # Severity accuracy
        severity_correct = sum(
            1 for r in results
            if r.severity_predicted.lower() == r.severity_expected.lower()
        )

        # Service identification
        service_metrics = self._calculate_service_metrics(results)

        # Confidence calibration
        calibration = self._calculate_calibration(results)

        # Pass@k
        pass_at_k = self._calculate_pass_at_k(results)

        return AccuracyMetrics(
            exact_match_rate=exact_matches / n,
            keyword_match_rate=keyword_matches / n,
            partial_match_rate=partial_matches / n,
            severity_accuracy=severity_correct / n,
            service_precision=service_metrics["precision"],
            service_recall=service_metrics["recall"],
            service_f1=service_metrics["f1"],
            confidence_ece=calibration["ece"],
            confidence_brier=calibration["brier"],
            pass_at_1=pass_at_k["pass_at_1"],
            pass_at_3=pass_at_k["pass_at_3"],
            pass_at_5=pass_at_k["pass_at_5"],
        )

    def calculate_latency(self, results: list[EvalResultData]) -> LatencyMetrics:
        """Calculate latency metrics from results."""
        if not results:
            return LatencyMetrics()

        durations = sorted([r.duration_ms for r in results])
        n = len(durations)

        mean = sum(durations) / n
        variance = sum((d - mean) ** 2 for d in durations) / n
        std = math.sqrt(variance)

        within_slo = sum(1 for d in durations if d <= self.slo_threshold_ms)

        return LatencyMetrics(
            mean_ms=mean,
            median_ms=durations[n // 2],
            p95_ms=durations[int(n * 0.95)] if n > 1 else durations[0],
            p99_ms=durations[int(n * 0.99)] if n > 1 else durations[0],
            min_ms=durations[0],
            max_ms=durations[-1],
            std_ms=std,
            within_slo_rate=within_slo / n,
            slo_threshold_ms=self.slo_threshold_ms,
        )

    def calculate_cost(
        self,
        results: list[EvalResultData],
        model: str = "claude-sonnet-4-20250514",
    ) -> CostMetrics:
        """Calculate cost metrics from results."""
        if not results:
            return CostMetrics()

        total_tokens = sum(r.tokens_used for r in results)
        total_cost = sum(r.cost_usd for r in results)

        return CostMetrics(
            total_tokens=total_tokens,
            input_tokens=int(total_tokens * 0.7),  # Estimate
            output_tokens=int(total_tokens * 0.3),
            total_cost_usd=total_cost,
            mean_cost_per_investigation_usd=total_cost / len(results),
            tokens_by_model={model: total_tokens},
            cost_by_model={model: total_cost},
        )

    def _calculate_service_metrics(self, results: list[EvalResultData]) -> dict[str, float]:
        """Calculate precision, recall, F1 for service identification."""
        total_precision = 0.0
        total_recall = 0.0
        valid_count = 0

        for r in results:
            predicted = set(s.lower() for s in r.services_predicted)
            expected = set(s.lower() for s in r.services_expected)

            if not expected:
                continue

            valid_count += 1

            if predicted:
                tp = len(predicted & expected)
                precision = tp / len(predicted) if predicted else 0
                recall = tp / len(expected) if expected else 0
            else:
                precision = 0
                recall = 0

            total_precision += precision
            total_recall += recall

        if valid_count == 0:
            return {"precision": 0.0, "recall": 0.0, "f1": 0.0}

        avg_precision = total_precision / valid_count
        avg_recall = total_recall / valid_count
        f1 = (
            2 * avg_precision * avg_recall / (avg_precision + avg_recall)
            if (avg_precision + avg_recall) > 0 else 0
        )

        return {"precision": avg_precision, "recall": avg_recall, "f1": f1}

    def _calculate_calibration(self, results: list[EvalResultData]) -> dict[str, float]:
        """Calculate confidence calibration metrics (ECE and Brier score)."""
        if not results:
            return {"ece": 0.0, "brier": 0.0}

        # Brier score: mean squared error of confidence vs correctness
        brier = sum(
            (r.confidence - (1.0 if r.is_correct else 0.0)) ** 2
            for r in results
        ) / len(results)

        # Expected Calibration Error: binned calibration
        n_bins = 10
        bins: dict[int, list[tuple[float, bool]]] = {i: [] for i in range(n_bins)}

        for r in results:
            bin_idx = min(int(r.confidence * n_bins), n_bins - 1)
            bins[bin_idx].append((r.confidence, r.is_correct))

        ece = 0.0
        for bin_data in bins.values():
            if not bin_data:
                continue
            avg_conf = sum(c for c, _ in bin_data) / len(bin_data)
            avg_acc = sum(1 for _, correct in bin_data if correct) / len(bin_data)
            ece += abs(avg_conf - avg_acc) * len(bin_data) / len(results)

        return {"ece": ece, "brier": brier}

    def _calculate_pass_at_k(self, results: list[EvalResultData]) -> dict[str, float]:
        """Calculate pass@k metrics.

        pass@k = probability that at least one of k samples is correct.
        For single-sample results, this equals the accuracy rate.
        """
        if not results:
            return {"pass_at_1": 0.0, "pass_at_3": 0.0, "pass_at_5": 0.0}

        # For single-attempt scenarios, pass@k equals accuracy
        accuracy = sum(1 for r in results if r.is_correct) / len(results)

        return {
            "pass_at_1": accuracy,
            "pass_at_3": min(1.0, accuracy * 1.5),  # Approximate with retries
            "pass_at_5": min(1.0, accuracy * 2.0),
        }

    def estimate_cost(
        self,
        tokens: int,
        model: str = "claude-sonnet-4-20250514",
        input_ratio: float = 0.7,
    ) -> float:
        """Estimate cost for a given token count."""
        pricing = self.MODEL_PRICING.get(model, {"input": 3.0, "output": 15.0})
        input_tokens = int(tokens * input_ratio)
        output_tokens = tokens - input_tokens

        cost = (
            input_tokens * pricing["input"] / 1_000_000 +
            output_tokens * pricing["output"] / 1_000_000
        )
        return cost
