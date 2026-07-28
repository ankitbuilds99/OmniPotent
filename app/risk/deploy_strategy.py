"""Deploy strategy recommendations.

Recommends deployment strategies based on change risk.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from app.risk.scorer import RiskLevel, RiskScore

log = logging.getLogger("devcopilot.risk.deploy_strategy")


class StrategyType(Enum):
    """Type of deployment strategy."""

    DIRECT = "direct"
    CANARY = "canary"
    BLUE_GREEN = "blue_green"
    ROLLING = "rolling"
    FEATURE_FLAG = "feature_flag"
    SHADOW = "shadow"
    DO_NOT_DEPLOY = "do_not_deploy"


@dataclass
class DeployStage:
    """A stage in a deployment strategy."""

    stage_number: int
    name: str
    traffic_percentage: float
    duration_minutes: int
    success_criteria: list[str]
    rollback_triggers: list[str]
    monitoring_focus: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage_number": self.stage_number,
            "name": self.name,
            "traffic_percentage": self.traffic_percentage,
            "duration_minutes": self.duration_minutes,
            "success_criteria": self.success_criteria,
            "rollback_triggers": self.rollback_triggers,
            "monitoring_focus": self.monitoring_focus,
        }


@dataclass
class TimingRecommendation:
    """Timing recommendation for deployment."""

    recommended_time: datetime
    avoid_times: list[tuple[datetime, datetime]]
    reason: str
    alternative_times: list[datetime]

    def to_dict(self) -> dict[str, Any]:
        return {
            "recommended_time": self.recommended_time.isoformat(),
            "avoid_times": [
                (start.isoformat(), end.isoformat())
                for start, end in self.avoid_times
            ],
            "reason": self.reason,
            "alternative_times": [t.isoformat() for t in self.alternative_times],
        }


@dataclass
class DeployStrategy:
    """Complete deployment strategy recommendation."""

    strategy_id: str
    risk_score_id: str
    strategy_type: StrategyType
    stages: list[DeployStage]
    total_duration_minutes: int
    timing: TimingRecommendation
    pre_deploy_checks: list[str]
    post_deploy_checks: list[str]
    recommended_monitors: list[str]
    estimated_risk_reduction: float
    confidence: float
    generated_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "risk_score_id": self.risk_score_id,
            "strategy_type": self.strategy_type.value,
            "stages": [s.to_dict() for s in self.stages],
            "total_duration_minutes": self.total_duration_minutes,
            "timing": self.timing.to_dict(),
            "pre_deploy_checks": self.pre_deploy_checks,
            "post_deploy_checks": self.post_deploy_checks,
            "recommended_monitors": self.recommended_monitors,
            "estimated_risk_reduction": round(self.estimated_risk_reduction, 2),
            "confidence": round(self.confidence, 2),
            "generated_at": self.generated_at.isoformat(),
        }


class StrategyRecommender:
    """Recommends deployment strategies based on risk."""

    def __init__(self) -> None:
        self._strategies: dict[str, DeployStrategy] = {}
        self._strategy_counter = 0

    def recommend(
        self,
        risk_score: RiskScore,
        target_service: str | None = None,
    ) -> DeployStrategy:
        """Recommend a deployment strategy based on risk score."""
        self._strategy_counter += 1
        strategy_id = f"strategy_{self._strategy_counter}"

        strategy_type = self._select_strategy_type(risk_score.risk_level)
        stages = self._build_stages(strategy_type, risk_score)
        timing = self._recommend_timing(risk_score)
        pre_checks = self._get_pre_deploy_checks(risk_score)
        post_checks = self._get_post_deploy_checks(risk_score)
        monitors = self._get_recommended_monitors(risk_score, target_service)

        strategy = DeployStrategy(
            strategy_id=strategy_id,
            risk_score_id=risk_score.score_id,
            strategy_type=strategy_type,
            stages=stages,
            total_duration_minutes=sum(s.duration_minutes for s in stages),
            timing=timing,
            pre_deploy_checks=pre_checks,
            post_deploy_checks=post_checks,
            recommended_monitors=monitors,
            estimated_risk_reduction=self._estimate_risk_reduction(strategy_type),
            confidence=0.85,
            generated_at=datetime.now(timezone.utc),
        )

        self._strategies[strategy_id] = strategy

        log.info(
            "deploy strategy recommended",
            extra={
                "strategy_id": strategy_id,
                "strategy_type": strategy_type.value,
                "risk_level": risk_score.risk_level.value,
            },
        )

        return strategy

    def _select_strategy_type(self, risk_level: RiskLevel) -> StrategyType:
        """Select strategy type based on risk level."""
        strategy_map = {
            RiskLevel.MINIMAL: StrategyType.DIRECT,
            RiskLevel.LOW: StrategyType.ROLLING,
            RiskLevel.MEDIUM: StrategyType.CANARY,
            RiskLevel.HIGH: StrategyType.CANARY,
            RiskLevel.CRITICAL: StrategyType.DO_NOT_DEPLOY,
        }
        return strategy_map.get(risk_level, StrategyType.CANARY)

    def _build_stages(
        self,
        strategy_type: StrategyType,
        risk_score: RiskScore,
    ) -> list[DeployStage]:
        """Build deployment stages based on strategy type."""
        if strategy_type == StrategyType.DIRECT:
            return [
                DeployStage(
                    stage_number=1,
                    name="Deploy 100%",
                    traffic_percentage=100,
                    duration_minutes=5,
                    success_criteria=["No critical errors"],
                    rollback_triggers=["Error rate > 1%"],
                    monitoring_focus=["error_rate", "latency_p99"],
                )
            ]

        elif strategy_type == StrategyType.ROLLING:
            return [
                DeployStage(
                    stage_number=1,
                    name="Rolling deploy",
                    traffic_percentage=100,
                    duration_minutes=15,
                    success_criteria=["Each pod healthy before next"],
                    rollback_triggers=["Any pod fails health check"],
                    monitoring_focus=["pod_health", "error_rate"],
                )
            ]

        elif strategy_type == StrategyType.CANARY:
            stages = []
            percentages = [1, 5, 25, 100]
            durations = [30, 60, 120, 0]

            if risk_score.risk_level == RiskLevel.HIGH:
                percentages = [1, 2, 5, 10, 25, 50, 100]
                durations = [30, 30, 60, 60, 120, 120, 0]

            for i, (pct, dur) in enumerate(zip(percentages, durations)):
                stages.append(
                    DeployStage(
                        stage_number=i + 1,
                        name=f"Canary {pct}%",
                        traffic_percentage=pct,
                        duration_minutes=dur,
                        success_criteria=[
                            f"Error rate < 0.1%",
                            f"p99 latency < {500 if pct < 25 else 1000}ms",
                        ],
                        rollback_triggers=[
                            "Error rate > 0.5%",
                            "p99 latency > 2x baseline",
                            "Any SEV1/SEV2 alert",
                        ],
                        monitoring_focus=[
                            "checkout_error_rate",
                            "payment_latency_p99",
                            "conversion_rate",
                        ],
                    )
                )

            return stages

        elif strategy_type == StrategyType.DO_NOT_DEPLOY:
            return []

        return []

    def _recommend_timing(
        self,
        risk_score: RiskScore,
    ) -> TimingRecommendation:
        """Recommend deployment timing."""
        now = datetime.now(timezone.utc)

        avoid_times = []

        friday_start = now.replace(hour=14, minute=0)
        while friday_start.weekday() != 4:
            friday_start += timedelta(days=1)
        monday_start = friday_start + timedelta(days=3, hours=-6)
        avoid_times.append((friday_start, monday_start))

        today_end = now.replace(hour=16, minute=0)
        tomorrow_start = (now + timedelta(days=1)).replace(hour=8, minute=0)
        avoid_times.append((today_end, tomorrow_start))

        recommended = now.replace(hour=10, minute=0, second=0, microsecond=0)
        while recommended.weekday() in (4, 5, 6) or recommended <= now:
            recommended += timedelta(days=1)

        alternatives = []
        for days_ahead in [0, 1, 2]:
            alt = (now + timedelta(days=days_ahead)).replace(hour=10, minute=0)
            if alt > now and alt.weekday() not in (4, 5, 6):
                alternatives.append(alt)

        reason = "Weekday morning with full team availability"
        if risk_score.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL):
            reason = "Weekday morning required for high-risk changes"

        return TimingRecommendation(
            recommended_time=recommended,
            avoid_times=avoid_times,
            reason=reason,
            alternative_times=alternatives[:3],
        )

    def _get_pre_deploy_checks(self, risk_score: RiskScore) -> list[str]:
        """Get pre-deployment checks."""
        checks = [
            "All tests passing",
            "Code review approved",
            "No blocking alerts on target environment",
        ]

        if risk_score.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL):
            checks.extend([
                "Load test results reviewed",
                "Rollback plan documented",
                "On-call engineer notified",
                "Feature flag ready for kill switch",
            ])

        return checks

    def _get_post_deploy_checks(self, risk_score: RiskScore) -> list[str]:
        """Get post-deployment checks."""
        checks = [
            "Health checks passing",
            "Error rate within baseline",
            "Key business metrics stable",
        ]

        if risk_score.risk_level == RiskLevel.HIGH:
            checks.extend([
                "Manual smoke test completed",
                "Customer support team notified",
                "30-minute bake time completed",
            ])

        return checks

    def _get_recommended_monitors(
        self,
        risk_score: RiskScore,
        target_service: str | None,
    ) -> list[str]:
        """Get recommended monitors for the deployment."""
        monitors = [
            "error_rate",
            "latency_p99",
            "request_rate",
        ]

        for change in risk_score.semantic_changes:
            if "payment" in change.file_path.lower():
                monitors.extend([
                    "payment_success_rate",
                    "payment_latency",
                ])
            if "auth" in change.file_path.lower():
                monitors.extend([
                    "auth_failure_rate",
                    "login_success_rate",
                ])

        return list(set(monitors))

    def _estimate_risk_reduction(self, strategy_type: StrategyType) -> float:
        """Estimate risk reduction from the strategy."""
        reductions = {
            StrategyType.DIRECT: 0.0,
            StrategyType.ROLLING: 0.2,
            StrategyType.CANARY: 0.6,
            StrategyType.BLUE_GREEN: 0.7,
            StrategyType.FEATURE_FLAG: 0.8,
            StrategyType.SHADOW: 0.9,
            StrategyType.DO_NOT_DEPLOY: 1.0,
        }
        return reductions.get(strategy_type, 0.5)

    def get_strategy(self, strategy_id: str) -> DeployStrategy | None:
        """Get a strategy by ID."""
        return self._strategies.get(strategy_id)

    def compare_strategies(
        self,
        risk_score: RiskScore,
    ) -> list[dict[str, Any]]:
        """Compare different strategies for a risk score."""
        comparisons = []

        for strategy_type in StrategyType:
            if strategy_type == StrategyType.DO_NOT_DEPLOY:
                continue

            reduction = self._estimate_risk_reduction(strategy_type)
            residual_risk = risk_score.overall_score * (1 - reduction)

            stages = self._build_stages(strategy_type, risk_score)
            total_time = sum(s.duration_minutes for s in stages)

            comparisons.append({
                "strategy_type": strategy_type.value,
                "risk_reduction": reduction,
                "residual_risk": residual_risk,
                "deployment_time_minutes": total_time,
                "stages_count": len(stages),
                "recommended": strategy_type == self._select_strategy_type(
                    risk_score.risk_level
                ),
            })

        return sorted(comparisons, key=lambda x: x["residual_risk"])


_recommender: StrategyRecommender | None = None


def get_strategy_recommender() -> StrategyRecommender:
    """Get the default strategy recommender."""
    global _recommender
    if _recommender is None:
        _recommender = StrategyRecommender()
    return _recommender
