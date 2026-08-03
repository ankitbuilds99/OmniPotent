"""A/B Testing Framework for Remediation Strategies.

Provides:
- Experiment creation and management
- Traffic allocation
- Statistical significance testing
- Automatic winner detection
- Gradual rollout
"""

from __future__ import annotations

import hashlib
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any
import uuid

log = logging.getLogger("omnipotent.learning.ab_testing")


class ExperimentStatus(str, Enum):
    """Status of an experiment."""
    DRAFT = "draft"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    ROLLED_BACK = "rolled_back"


class MetricType(str, Enum):
    """Types of metrics to track."""
    SUCCESS_RATE = "success_rate"
    MTTR = "mttr"  # Mean time to resolve
    ERROR_RATE = "error_rate"
    USER_SATISFACTION = "user_satisfaction"
    COST = "cost"
    LATENCY = "latency"
    CONFIDENCE = "confidence"


class SignificanceLevel(str, Enum):
    """Statistical significance levels."""
    VERY_HIGH = "very_high"  # p < 0.01
    HIGH = "high"  # p < 0.05
    MODERATE = "moderate"  # p < 0.10
    LOW = "low"  # p >= 0.10


@dataclass
class ExperimentVariant:
    """A variant in an A/B test."""
    id: str
    name: str
    description: str
    weight: float  # Traffic percentage (0-100)
    is_control: bool = False

    # Configuration for this variant
    config: dict[str, Any] = field(default_factory=dict)

    # Results
    sample_count: int = 0
    conversions: int = 0
    metric_values: dict[str, list[float]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "weight": self.weight,
            "is_control": self.is_control,
            "config": self.config,
            "sample_count": self.sample_count,
            "conversions": self.conversions,
        }

    @property
    def conversion_rate(self) -> float:
        if self.sample_count == 0:
            return 0.0
        return self.conversions / self.sample_count

    def metric_mean(self, metric: str) -> float | None:
        values = self.metric_values.get(metric, [])
        if not values:
            return None
        return sum(values) / len(values)

    def metric_std(self, metric: str) -> float | None:
        values = self.metric_values.get(metric, [])
        if len(values) < 2:
            return None
        mean = sum(values) / len(values)
        variance = sum((x - mean) ** 2 for x in values) / (len(values) - 1)
        return math.sqrt(variance)


@dataclass
class StatisticalSignificance:
    """Statistical analysis result."""
    is_significant: bool
    p_value: float
    confidence_level: float
    significance_level: SignificanceLevel
    effect_size: float
    relative_improvement: float
    sample_size_sufficient: bool
    recommended_action: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_significant": self.is_significant,
            "p_value": self.p_value,
            "confidence_level": self.confidence_level,
            "significance_level": self.significance_level.value,
            "effect_size": self.effect_size,
            "relative_improvement": self.relative_improvement,
            "sample_size_sufficient": self.sample_size_sufficient,
            "recommended_action": self.recommended_action,
        }


@dataclass
class ExperimentResult:
    """Complete result of an experiment."""
    experiment_id: str
    winner: ExperimentVariant | None
    loser: ExperimentVariant | None
    statistical_significance: StatisticalSignificance
    primary_metric_results: dict[str, Any]
    secondary_metric_results: dict[str, dict[str, Any]]
    duration: timedelta
    total_samples: int
    recommendation: str
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "winner": self.winner.to_dict() if self.winner else None,
            "loser": self.loser.to_dict() if self.loser else None,
            "statistical_significance": self.statistical_significance.to_dict(),
            "primary_metric_results": self.primary_metric_results,
            "secondary_metric_results": self.secondary_metric_results,
            "duration_hours": self.duration.total_seconds() / 3600,
            "total_samples": self.total_samples,
            "recommendation": self.recommendation,
            "confidence": self.confidence,
        }


@dataclass
class Experiment:
    """An A/B test experiment."""
    id: str
    name: str
    description: str
    hypothesis: str
    variants: list[ExperimentVariant]
    primary_metric: MetricType
    secondary_metrics: list[MetricType]
    status: ExperimentStatus = ExperimentStatus.DRAFT

    # Configuration
    min_sample_size: int = 100
    max_duration_hours: int = 168  # 1 week
    min_confidence: float = 0.95
    early_stopping: bool = True
    early_stopping_threshold: float = 0.99

    # Targeting
    target_services: list[str] = field(default_factory=list)
    target_severity: list[str] = field(default_factory=list)
    target_percentage: float = 100.0

    # Timestamps
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: datetime | None = None
    ended_at: datetime | None = None

    # Results
    result: ExperimentResult | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "hypothesis": self.hypothesis,
            "variants": [v.to_dict() for v in self.variants],
            "primary_metric": self.primary_metric.value,
            "secondary_metrics": [m.value for m in self.secondary_metrics],
            "status": self.status.value,
            "min_sample_size": self.min_sample_size,
            "max_duration_hours": self.max_duration_hours,
            "min_confidence": self.min_confidence,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "result": self.result.to_dict() if self.result else None,
        }

    @property
    def control(self) -> ExperimentVariant | None:
        return next((v for v in self.variants if v.is_control), None)

    @property
    def treatment(self) -> ExperimentVariant | None:
        return next((v for v in self.variants if not v.is_control), None)

    @property
    def total_samples(self) -> int:
        return sum(v.sample_count for v in self.variants)


class ABTestFramework:
    """A/B testing framework for remediation strategies.

    Features:
    - Multi-variant testing
    - Statistical significance analysis
    - Automatic early stopping
    - Gradual rollout support
    - Targeting rules
    """

    def __init__(
        self,
        redis_client: Any = None,
        metrics_client: Any = None,
    ):
        self.redis = redis_client
        self.metrics = metrics_client

        self._experiments: dict[str, Experiment] = {}
        self._active_experiments: dict[str, Experiment] = {}
        self._assignment_cache: dict[str, str] = {}  # entity_id -> variant_id

    def create_experiment(
        self,
        name: str,
        description: str,
        hypothesis: str,
        control_config: dict[str, Any],
        treatment_config: dict[str, Any],
        primary_metric: MetricType = MetricType.SUCCESS_RATE,
        secondary_metrics: list[MetricType] | None = None,
        control_weight: float = 50.0,
        min_sample_size: int = 100,
        max_duration_hours: int = 168,
        target_services: list[str] | None = None,
    ) -> Experiment:
        """Create a new A/B test experiment.

        Args:
            name: Experiment name
            description: What we're testing
            hypothesis: Expected outcome
            control_config: Configuration for control variant
            treatment_config: Configuration for treatment variant
            primary_metric: Primary metric to optimize
            secondary_metrics: Additional metrics to track
            control_weight: Percentage of traffic to control (0-100)
            min_sample_size: Minimum samples per variant
            max_duration_hours: Maximum experiment duration
            target_services: Services to include

        Returns:
            Created Experiment
        """
        exp_id = str(uuid.uuid4())[:8]

        control = ExperimentVariant(
            id=f"{exp_id}_control",
            name="Control",
            description="Current strategy",
            weight=control_weight,
            is_control=True,
            config=control_config,
        )

        treatment = ExperimentVariant(
            id=f"{exp_id}_treatment",
            name="Treatment",
            description="New strategy",
            weight=100.0 - control_weight,
            is_control=False,
            config=treatment_config,
        )

        experiment = Experiment(
            id=exp_id,
            name=name,
            description=description,
            hypothesis=hypothesis,
            variants=[control, treatment],
            primary_metric=primary_metric,
            secondary_metrics=secondary_metrics or [],
            min_sample_size=min_sample_size,
            max_duration_hours=max_duration_hours,
            target_services=target_services or [],
        )

        self._experiments[exp_id] = experiment
        log.info("Created experiment: %s - %s", exp_id, name)

        return experiment

    def start_experiment(self, experiment_id: str) -> Experiment:
        """Start an experiment."""
        experiment = self._experiments.get(experiment_id)
        if not experiment:
            raise ValueError(f"Experiment not found: {experiment_id}")

        if experiment.status != ExperimentStatus.DRAFT:
            raise ValueError(f"Experiment already started: {experiment.status}")

        experiment.status = ExperimentStatus.RUNNING
        experiment.started_at = datetime.now(timezone.utc)
        self._active_experiments[experiment_id] = experiment

        log.info("Started experiment: %s", experiment_id)
        return experiment

    def assign_variant(
        self,
        experiment_id: str,
        entity_id: str,  # e.g., incident_id, user_id
        context: dict[str, Any] | None = None,
    ) -> ExperimentVariant | None:
        """Assign an entity to a variant.

        Uses deterministic hashing for consistent assignment.

        Args:
            experiment_id: Experiment to assign to
            entity_id: Entity identifier
            context: Optional context for targeting

        Returns:
            Assigned variant or None if not in experiment
        """
        experiment = self._active_experiments.get(experiment_id)
        if not experiment or experiment.status != ExperimentStatus.RUNNING:
            return None

        # Check targeting rules
        context = context or {}
        if experiment.target_services:
            service = context.get("service")
            if service and service not in experiment.target_services:
                return None

        # Check if entity should be in experiment
        if experiment.target_percentage < 100:
            hash_val = int(hashlib.md5(
                f"{experiment_id}:{entity_id}:bucket".encode()
            ).hexdigest(), 16)
            if (hash_val % 100) >= experiment.target_percentage:
                return None

        # Check cache
        cache_key = f"{experiment_id}:{entity_id}"
        if cache_key in self._assignment_cache:
            variant_id = self._assignment_cache[cache_key]
            return next((v for v in experiment.variants if v.id == variant_id), None)

        # Deterministic assignment
        hash_val = int(hashlib.md5(
            f"{experiment_id}:{entity_id}".encode()
        ).hexdigest(), 16)
        bucket = hash_val % 100

        cumulative = 0.0
        for variant in experiment.variants:
            cumulative += variant.weight
            if bucket < cumulative:
                self._assignment_cache[cache_key] = variant.id
                variant.sample_count += 1
                return variant

        # Fallback to control
        control = experiment.control
        if control:
            self._assignment_cache[cache_key] = control.id
            control.sample_count += 1
        return control

    def record_conversion(
        self,
        experiment_id: str,
        entity_id: str,
        converted: bool = True,
    ) -> bool:
        """Record a conversion for an entity.

        Args:
            experiment_id: Experiment ID
            entity_id: Entity that converted
            converted: Whether conversion was successful

        Returns:
            True if recorded
        """
        experiment = self._experiments.get(experiment_id)
        if not experiment:
            return False

        cache_key = f"{experiment_id}:{entity_id}"
        variant_id = self._assignment_cache.get(cache_key)
        if not variant_id:
            return False

        variant = next((v for v in experiment.variants if v.id == variant_id), None)
        if variant and converted:
            variant.conversions += 1
            return True

        return False

    def record_metric(
        self,
        experiment_id: str,
        entity_id: str,
        metric: MetricType,
        value: float,
    ) -> bool:
        """Record a metric value for an entity.

        Args:
            experiment_id: Experiment ID
            entity_id: Entity ID
            metric: Metric type
            value: Metric value

        Returns:
            True if recorded
        """
        experiment = self._experiments.get(experiment_id)
        if not experiment:
            return False

        cache_key = f"{experiment_id}:{entity_id}"
        variant_id = self._assignment_cache.get(cache_key)
        if not variant_id:
            return False

        variant = next((v for v in experiment.variants if v.id == variant_id), None)
        if variant:
            if metric.value not in variant.metric_values:
                variant.metric_values[metric.value] = []
            variant.metric_values[metric.value].append(value)
            return True

        return False

    def analyze_experiment(
        self,
        experiment_id: str,
    ) -> ExperimentResult:
        """Analyze experiment results.

        Args:
            experiment_id: Experiment to analyze

        Returns:
            ExperimentResult with statistical analysis
        """
        experiment = self._experiments.get(experiment_id)
        if not experiment:
            raise ValueError(f"Experiment not found: {experiment_id}")

        control = experiment.control
        treatment = experiment.treatment

        if not control or not treatment:
            raise ValueError("Experiment must have control and treatment variants")

        # Calculate primary metric significance
        stat_sig = self._calculate_significance(
            control, treatment, experiment.primary_metric
        )

        # Calculate secondary metrics
        secondary_results = {}
        for metric in experiment.secondary_metrics:
            secondary_results[metric.value] = {
                "control_mean": control.metric_mean(metric.value),
                "treatment_mean": treatment.metric_mean(metric.value),
                "control_std": control.metric_std(metric.value),
                "treatment_std": treatment.metric_std(metric.value),
            }

        # Determine winner
        winner = None
        loser = None
        if stat_sig.is_significant:
            if stat_sig.effect_size > 0:
                winner = treatment
                loser = control
            else:
                winner = control
                loser = treatment

        # Generate recommendation
        recommendation = self._generate_recommendation(experiment, stat_sig, winner)

        duration = datetime.now(timezone.utc) - (experiment.started_at or experiment.created_at)

        result = ExperimentResult(
            experiment_id=experiment_id,
            winner=winner,
            loser=loser,
            statistical_significance=stat_sig,
            primary_metric_results={
                "control_rate": control.conversion_rate,
                "treatment_rate": treatment.conversion_rate,
                "control_samples": control.sample_count,
                "treatment_samples": treatment.sample_count,
            },
            secondary_metric_results=secondary_results,
            duration=duration,
            total_samples=experiment.total_samples,
            recommendation=recommendation,
            confidence=stat_sig.confidence_level,
        )

        experiment.result = result
        return result

    def _calculate_significance(
        self,
        control: ExperimentVariant,
        treatment: ExperimentVariant,
        metric: MetricType,
    ) -> StatisticalSignificance:
        """Calculate statistical significance between variants."""
        # Use conversion rates for success rate metric
        if metric == MetricType.SUCCESS_RATE:
            p1 = control.conversion_rate
            p2 = treatment.conversion_rate
            n1 = control.sample_count
            n2 = treatment.sample_count
        else:
            # Use metric means
            p1 = control.metric_mean(metric.value) or 0
            p2 = treatment.metric_mean(metric.value) or 0
            n1 = len(control.metric_values.get(metric.value, []))
            n2 = len(treatment.metric_values.get(metric.value, []))

        # Check sample size
        sample_size_sufficient = n1 >= 30 and n2 >= 30

        if n1 == 0 or n2 == 0:
            return StatisticalSignificance(
                is_significant=False,
                p_value=1.0,
                confidence_level=0.0,
                significance_level=SignificanceLevel.LOW,
                effect_size=0.0,
                relative_improvement=0.0,
                sample_size_sufficient=False,
                recommended_action="collect_more_data",
            )

        # Calculate pooled standard error (for proportions)
        pooled_p = (control.conversions + treatment.conversions) / (n1 + n2)
        se = math.sqrt(pooled_p * (1 - pooled_p) * (1/n1 + 1/n2)) if pooled_p > 0 and pooled_p < 1 else 0.01

        # Calculate z-score
        z_score = (p2 - p1) / se if se > 0 else 0

        # Calculate p-value (two-tailed)
        p_value = 2 * (1 - self._normal_cdf(abs(z_score)))

        # Determine significance level
        if p_value < 0.01:
            sig_level = SignificanceLevel.VERY_HIGH
        elif p_value < 0.05:
            sig_level = SignificanceLevel.HIGH
        elif p_value < 0.10:
            sig_level = SignificanceLevel.MODERATE
        else:
            sig_level = SignificanceLevel.LOW

        # Calculate effect size (Cohen's h for proportions)
        effect_size = 2 * (math.asin(math.sqrt(p2)) - math.asin(math.sqrt(p1))) if p1 >= 0 and p2 >= 0 else 0

        # Relative improvement
        relative_improvement = (p2 - p1) / p1 if p1 > 0 else 0

        # Determine recommended action
        if p_value < 0.05 and sample_size_sufficient:
            if effect_size > 0:
                recommended_action = "deploy_treatment"
            else:
                recommended_action = "keep_control"
        elif not sample_size_sufficient:
            recommended_action = "collect_more_data"
        else:
            recommended_action = "continue_experiment"

        return StatisticalSignificance(
            is_significant=p_value < 0.05,
            p_value=p_value,
            confidence_level=1 - p_value,
            significance_level=sig_level,
            effect_size=effect_size,
            relative_improvement=relative_improvement,
            sample_size_sufficient=sample_size_sufficient,
            recommended_action=recommended_action,
        )

    def _normal_cdf(self, x: float) -> float:
        """Approximate normal CDF."""
        # Approximation using error function
        return (1 + math.erf(x / math.sqrt(2))) / 2

    def _generate_recommendation(
        self,
        experiment: Experiment,
        stat_sig: StatisticalSignificance,
        winner: ExperimentVariant | None,
    ) -> str:
        """Generate actionable recommendation."""
        if not stat_sig.sample_size_sufficient:
            remaining = experiment.min_sample_size - min(
                v.sample_count for v in experiment.variants
            )
            return f"Continue experiment. Need approximately {remaining} more samples per variant."

        if not stat_sig.is_significant:
            return "No significant difference detected. Consider extending the experiment or accepting the null hypothesis."

        if winner:
            improvement = abs(stat_sig.relative_improvement) * 100
            return f"Deploy '{winner.name}'. Shows {improvement:.1f}% improvement with {stat_sig.confidence_level*100:.1f}% confidence."

        return "Results inconclusive. Review secondary metrics and business context."

    def stop_experiment(
        self,
        experiment_id: str,
        reason: str = "manual",
    ) -> Experiment:
        """Stop an experiment."""
        experiment = self._experiments.get(experiment_id)
        if not experiment:
            raise ValueError(f"Experiment not found: {experiment_id}")

        experiment.status = ExperimentStatus.COMPLETED
        experiment.ended_at = datetime.now(timezone.utc)

        if experiment_id in self._active_experiments:
            del self._active_experiments[experiment_id]

        # Final analysis
        self.analyze_experiment(experiment_id)

        log.info("Stopped experiment: %s (reason=%s)", experiment_id, reason)
        return experiment

    def check_early_stopping(self, experiment_id: str) -> bool:
        """Check if experiment should stop early.

        Returns True if we have high confidence result.
        """
        experiment = self._experiments.get(experiment_id)
        if not experiment or not experiment.early_stopping:
            return False

        if experiment.total_samples < experiment.min_sample_size * 2:
            return False

        result = self.analyze_experiment(experiment_id)

        if result.statistical_significance.confidence_level >= experiment.early_stopping_threshold:
            log.info(
                "Early stopping triggered for %s (confidence=%.2f%%)",
                experiment_id, result.statistical_significance.confidence_level * 100
            )
            return True

        return False

    def get_experiment(self, experiment_id: str) -> Experiment | None:
        """Get an experiment by ID."""
        return self._experiments.get(experiment_id)

    def list_experiments(
        self,
        status: ExperimentStatus | None = None,
    ) -> list[Experiment]:
        """List experiments."""
        experiments = list(self._experiments.values())

        if status:
            experiments = [e for e in experiments if e.status == status]

        return experiments

    def get_active_experiments(self) -> list[Experiment]:
        """Get all running experiments."""
        return list(self._active_experiments.values())

    def get_stats(self) -> dict[str, Any]:
        """Get framework statistics."""
        experiments = list(self._experiments.values())

        return {
            "total_experiments": len(experiments),
            "active_experiments": len(self._active_experiments),
            "completed_experiments": len([e for e in experiments if e.status == ExperimentStatus.COMPLETED]),
            "total_samples": sum(e.total_samples for e in experiments),
            "significant_results": len([
                e for e in experiments
                if e.result and e.result.statistical_significance.is_significant
            ]),
        }


def create_ab_test_framework(
    redis_client: Any = None,
    metrics_client: Any = None,
) -> ABTestFramework:
    """Factory function to create an A/B test framework."""
    return ABTestFramework(
        redis_client=redis_client,
        metrics_client=metrics_client,
    )
