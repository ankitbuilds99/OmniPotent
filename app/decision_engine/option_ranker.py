"""Option Ranker - Multi-criteria decision analysis.

Ranks remediation options using weighted scoring across:
- Success probability
- Risk score
- Cost estimation
- Time to resolution
- Impact analysis
- Reversibility
- Human approval requirements

Uses MCDM (Multi-Criteria Decision Making) with configurable weights.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
import hashlib

log = logging.getLogger("devcopilot.decision_engine.option_ranker")


class RankingStrategy(str, Enum):
    """Ranking strategy to use."""
    BALANCED = "balanced"  # Equal weight to all factors
    SPEED_FOCUSED = "speed_focused"  # Prioritize fast resolution
    SAFETY_FOCUSED = "safety_focused"  # Prioritize low risk
    COST_FOCUSED = "cost_focused"  # Prioritize low cost
    CUSTOM = "custom"  # Use provided weights


@dataclass
class RankingCriteria:
    """Configuration for ranking criteria weights."""
    success_probability: float = 0.25
    risk_score: float = 0.20  # Inverted - lower risk = higher score
    time_to_resolution: float = 0.15  # Inverted - faster = higher score
    cost: float = 0.10  # Inverted - lower cost = higher score
    impact_reduction: float = 0.15
    reversibility: float = 0.10
    automation_level: float = 0.05

    @classmethod
    def from_strategy(cls, strategy: RankingStrategy) -> RankingCriteria:
        """Create criteria from a predefined strategy."""
        if strategy == RankingStrategy.BALANCED:
            return cls()

        if strategy == RankingStrategy.SPEED_FOCUSED:
            return cls(
                success_probability=0.20,
                risk_score=0.10,
                time_to_resolution=0.35,
                cost=0.05,
                impact_reduction=0.15,
                reversibility=0.05,
                automation_level=0.10,
            )

        if strategy == RankingStrategy.SAFETY_FOCUSED:
            return cls(
                success_probability=0.30,
                risk_score=0.35,
                time_to_resolution=0.05,
                cost=0.05,
                impact_reduction=0.10,
                reversibility=0.15,
                automation_level=0.00,
            )

        if strategy == RankingStrategy.COST_FOCUSED:
            return cls(
                success_probability=0.20,
                risk_score=0.15,
                time_to_resolution=0.10,
                cost=0.35,
                impact_reduction=0.10,
                reversibility=0.05,
                automation_level=0.05,
            )

        return cls()

    def validate(self) -> bool:
        """Validate that weights sum to 1.0."""
        total = (
            self.success_probability +
            self.risk_score +
            self.time_to_resolution +
            self.cost +
            self.impact_reduction +
            self.reversibility +
            self.automation_level
        )
        return abs(total - 1.0) < 0.001

    def to_dict(self) -> dict[str, float]:
        return {
            "success_probability": self.success_probability,
            "risk_score": self.risk_score,
            "time_to_resolution": self.time_to_resolution,
            "cost": self.cost,
            "impact_reduction": self.impact_reduction,
            "reversibility": self.reversibility,
            "automation_level": self.automation_level,
        }


@dataclass
class DecisionOption:
    """A remediation option to be ranked."""
    option_id: str
    name: str
    description: str
    action_type: str

    # Core metrics (0-1 scale, higher is better except where noted)
    success_probability: float
    risk_score: float  # 0-1, lower is better (will be inverted)
    estimated_time_minutes: int  # Will be normalized
    estimated_cost_usd: float  # Will be normalized
    impact_reduction_percent: float  # 0-100, how much incident impact reduced

    # Boolean factors
    is_reversible: bool
    is_automated: bool
    requires_approval: bool
    requires_downtime: bool

    # Additional context
    prerequisites: list[str] = field(default_factory=list)
    side_effects: list[str] = field(default_factory=list)
    evidence_supporting: list[str] = field(default_factory=list)
    evidence_against: list[str] = field(default_factory=list)

    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "option_id": self.option_id,
            "name": self.name,
            "description": self.description,
            "action_type": self.action_type,
            "success_probability": self.success_probability,
            "risk_score": self.risk_score,
            "estimated_time_minutes": self.estimated_time_minutes,
            "estimated_cost_usd": self.estimated_cost_usd,
            "impact_reduction_percent": self.impact_reduction_percent,
            "is_reversible": self.is_reversible,
            "is_automated": self.is_automated,
            "requires_approval": self.requires_approval,
            "requires_downtime": self.requires_downtime,
            "prerequisites": self.prerequisites,
            "side_effects": self.side_effects,
            "evidence_supporting": self.evidence_supporting,
            "evidence_against": self.evidence_against,
        }


@dataclass
class RankedOption:
    """A ranked remediation option with scores."""
    option: DecisionOption
    rank: int
    total_score: float  # 0-1, weighted composite score

    # Individual normalized scores (0-1)
    scores: dict[str, float]

    # Comparison to other options
    score_delta_from_top: float
    percentage_of_top: float

    # Reasoning
    strengths: list[str]
    weaknesses: list[str]
    recommendation: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "option": self.option.to_dict(),
            "rank": self.rank,
            "total_score": round(self.total_score, 3),
            "scores": {k: round(v, 3) for k, v in self.scores.items()},
            "score_delta_from_top": round(self.score_delta_from_top, 3),
            "percentage_of_top": round(self.percentage_of_top, 1),
            "strengths": self.strengths,
            "weaknesses": self.weaknesses,
            "recommendation": self.recommendation,
        }


@dataclass
class RankingResult:
    """Result of ranking multiple options."""
    ranked_options: list[RankedOption]
    criteria_used: RankingCriteria
    strategy: RankingStrategy

    top_recommendation: RankedOption | None
    close_alternatives: list[RankedOption]  # Within 10% of top

    analysis_summary: str
    ranking_id: str
    ranked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "ranked_options": [ro.to_dict() for ro in self.ranked_options],
            "criteria_used": self.criteria_used.to_dict(),
            "strategy": self.strategy.value,
            "top_recommendation": self.top_recommendation.to_dict() if self.top_recommendation else None,
            "close_alternatives": [ca.to_dict() for ca in self.close_alternatives],
            "analysis_summary": self.analysis_summary,
            "ranking_id": self.ranking_id,
            "ranked_at": self.ranked_at.isoformat(),
        }


class OptionRanker:
    """Multi-criteria option ranker.

    Uses weighted scoring to rank remediation options across multiple
    dimensions. Supports different strategies for different contexts
    (speed vs safety vs cost).
    """

    def __init__(
        self,
        default_strategy: RankingStrategy = RankingStrategy.BALANCED,
        max_time_minutes: int = 120,  # For normalization
        max_cost_usd: float = 10000,  # For normalization
    ):
        self.default_strategy = default_strategy
        self.max_time = max_time_minutes
        self.max_cost = max_cost_usd
        self._ranking_count = 0

    def rank(
        self,
        options: list[DecisionOption],
        strategy: RankingStrategy | None = None,
        custom_criteria: RankingCriteria | None = None,
        context: dict[str, Any] | None = None,
    ) -> RankingResult:
        """Rank a list of remediation options.

        Args:
            options: List of options to rank
            strategy: Ranking strategy to use
            custom_criteria: Custom weights (if strategy is CUSTOM)
            context: Additional context for ranking (incident severity, etc.)

        Returns:
            RankingResult with ranked options and analysis
        """
        if not options:
            return RankingResult(
                ranked_options=[],
                criteria_used=RankingCriteria(),
                strategy=RankingStrategy.BALANCED,
                top_recommendation=None,
                close_alternatives=[],
                analysis_summary="No options provided to rank.",
                ranking_id=self._generate_ranking_id([]),
            )

        self._ranking_count += 1
        strategy = strategy or self.default_strategy

        # Get criteria
        if strategy == RankingStrategy.CUSTOM and custom_criteria:
            criteria = custom_criteria
        else:
            criteria = RankingCriteria.from_strategy(strategy)

        # Adjust criteria based on context
        if context:
            criteria = self._adjust_criteria_for_context(criteria, context)

        # Calculate normalized scores for each option
        scored_options = []
        for option in options:
            scores = self._calculate_scores(option, criteria)
            total_score = self._calculate_total_score(scores, criteria)
            scored_options.append((option, scores, total_score))

        # Sort by total score (descending)
        scored_options.sort(key=lambda x: x[2], reverse=True)

        # Build ranked options
        top_score = scored_options[0][2] if scored_options else 0
        ranked_options = []

        for rank, (option, scores, total_score) in enumerate(scored_options, 1):
            strengths, weaknesses = self._analyze_option(option, scores, criteria)
            recommendation = self._generate_recommendation(rank, option, total_score, top_score)

            ranked_option = RankedOption(
                option=option,
                rank=rank,
                total_score=total_score,
                scores=scores,
                score_delta_from_top=top_score - total_score,
                percentage_of_top=(total_score / top_score * 100) if top_score > 0 else 0,
                strengths=strengths,
                weaknesses=weaknesses,
                recommendation=recommendation,
            )
            ranked_options.append(ranked_option)

        # Identify close alternatives (within 10% of top)
        close_alternatives = [
            ro for ro in ranked_options[1:]
            if ro.percentage_of_top >= 90
        ]

        # Generate summary
        analysis_summary = self._generate_analysis_summary(ranked_options, criteria, context)

        return RankingResult(
            ranked_options=ranked_options,
            criteria_used=criteria,
            strategy=strategy,
            top_recommendation=ranked_options[0] if ranked_options else None,
            close_alternatives=close_alternatives,
            analysis_summary=analysis_summary,
            ranking_id=self._generate_ranking_id(options),
        )

    def _calculate_scores(
        self,
        option: DecisionOption,
        criteria: RankingCriteria,
    ) -> dict[str, float]:
        """Calculate normalized scores for each criterion."""
        scores = {}

        # Success probability (already 0-1)
        scores["success_probability"] = option.success_probability

        # Risk score (invert - lower risk = higher score)
        scores["risk_score"] = 1.0 - option.risk_score

        # Time to resolution (invert and normalize)
        normalized_time = min(option.estimated_time_minutes / self.max_time, 1.0)
        scores["time_to_resolution"] = 1.0 - normalized_time

        # Cost (invert and normalize)
        normalized_cost = min(option.estimated_cost_usd / self.max_cost, 1.0)
        scores["cost"] = 1.0 - normalized_cost

        # Impact reduction (normalize from 0-100 to 0-1)
        scores["impact_reduction"] = option.impact_reduction_percent / 100.0

        # Reversibility (boolean to score)
        scores["reversibility"] = 1.0 if option.is_reversible else 0.0

        # Automation level
        base_automation = 0.5
        if option.is_automated:
            base_automation += 0.3
        if not option.requires_approval:
            base_automation += 0.2
        scores["automation_level"] = min(base_automation, 1.0)

        return scores

    def _calculate_total_score(
        self,
        scores: dict[str, float],
        criteria: RankingCriteria,
    ) -> float:
        """Calculate weighted total score."""
        return (
            scores["success_probability"] * criteria.success_probability +
            scores["risk_score"] * criteria.risk_score +
            scores["time_to_resolution"] * criteria.time_to_resolution +
            scores["cost"] * criteria.cost +
            scores["impact_reduction"] * criteria.impact_reduction +
            scores["reversibility"] * criteria.reversibility +
            scores["automation_level"] * criteria.automation_level
        )

    def _adjust_criteria_for_context(
        self,
        criteria: RankingCriteria,
        context: dict[str, Any],
    ) -> RankingCriteria:
        """Adjust criteria weights based on incident context."""
        # Make a copy
        adjusted = RankingCriteria(
            success_probability=criteria.success_probability,
            risk_score=criteria.risk_score,
            time_to_resolution=criteria.time_to_resolution,
            cost=criteria.cost,
            impact_reduction=criteria.impact_reduction,
            reversibility=criteria.reversibility,
            automation_level=criteria.automation_level,
        )

        severity = context.get("incident_severity", "P3")

        # High severity = prioritize speed and impact
        if severity in ("P0", "P1"):
            adjusted.time_to_resolution += 0.10
            adjusted.impact_reduction += 0.05
            adjusted.cost -= 0.10  # Less important when system is down
            adjusted.automation_level -= 0.05

        # Revenue impacting = increase safety
        if context.get("is_revenue_impacting"):
            adjusted.risk_score += 0.05
            adjusted.reversibility += 0.05
            adjusted.automation_level -= 0.05
            adjusted.cost -= 0.05

        # Normalize to sum to 1.0
        total = (
            adjusted.success_probability +
            adjusted.risk_score +
            adjusted.time_to_resolution +
            adjusted.cost +
            adjusted.impact_reduction +
            adjusted.reversibility +
            adjusted.automation_level
        )

        if total > 0:
            adjusted.success_probability /= total
            adjusted.risk_score /= total
            adjusted.time_to_resolution /= total
            adjusted.cost /= total
            adjusted.impact_reduction /= total
            adjusted.reversibility /= total
            adjusted.automation_level /= total

        return adjusted

    def _analyze_option(
        self,
        option: DecisionOption,
        scores: dict[str, float],
        criteria: RankingCriteria,
    ) -> tuple[list[str], list[str]]:
        """Analyze option strengths and weaknesses."""
        strengths = []
        weaknesses = []

        # Success probability
        if scores["success_probability"] >= 0.85:
            strengths.append(f"High success probability ({option.success_probability:.0%})")
        elif scores["success_probability"] < 0.6:
            weaknesses.append(f"Lower success probability ({option.success_probability:.0%})")

        # Risk
        if scores["risk_score"] >= 0.8:  # Low risk
            strengths.append("Low risk approach")
        elif scores["risk_score"] < 0.5:  # High risk
            weaknesses.append(f"Higher risk (score: {option.risk_score:.2f})")

        # Time
        if option.estimated_time_minutes <= 15:
            strengths.append(f"Quick resolution (~{option.estimated_time_minutes} min)")
        elif option.estimated_time_minutes > 60:
            weaknesses.append(f"Long execution time (~{option.estimated_time_minutes} min)")

        # Cost
        if option.estimated_cost_usd < 100:
            strengths.append("Low cost")
        elif option.estimated_cost_usd > 1000:
            weaknesses.append(f"Higher cost (${option.estimated_cost_usd:.0f})")

        # Impact reduction
        if option.impact_reduction_percent >= 90:
            strengths.append(f"High impact reduction ({option.impact_reduction_percent:.0f}%)")
        elif option.impact_reduction_percent < 50:
            weaknesses.append(f"Partial impact reduction ({option.impact_reduction_percent:.0f}%)")

        # Reversibility
        if option.is_reversible:
            strengths.append("Easily reversible")
        else:
            weaknesses.append("Not easily reversible")

        # Automation
        if option.is_automated and not option.requires_approval:
            strengths.append("Fully automated, no approval needed")
        elif option.requires_approval:
            weaknesses.append("Requires human approval")

        # Downtime
        if option.requires_downtime:
            weaknesses.append("Requires service downtime")

        # Evidence
        if len(option.evidence_supporting) >= 3:
            strengths.append(f"Well-supported by evidence ({len(option.evidence_supporting)} signals)")
        if len(option.evidence_against) > 0:
            weaknesses.append(f"Some contradicting evidence ({len(option.evidence_against)} signals)")

        return strengths[:4], weaknesses[:4]

    def _generate_recommendation(
        self,
        rank: int,
        option: DecisionOption,
        total_score: float,
        top_score: float,
    ) -> str:
        """Generate recommendation text for an option."""
        percentage = (total_score / top_score * 100) if top_score > 0 else 0

        if rank == 1:
            if total_score >= 0.8:
                return "STRONGLY RECOMMENDED - High confidence, proceed with standard safety checks"
            elif total_score >= 0.6:
                return "RECOMMENDED - Good option, consider the identified weaknesses"
            else:
                return "BEST AVAILABLE - No ideal options, proceed with extra caution"

        elif rank == 2 and percentage >= 95:
            return "CLOSE SECOND - Nearly equivalent to top option, valid alternative"

        elif rank <= 3 and percentage >= 85:
            return "VIABLE ALTERNATIVE - Consider if top option has blockers"

        else:
            return "BACKUP OPTION - Use only if higher-ranked options are not feasible"

    def _generate_analysis_summary(
        self,
        ranked_options: list[RankedOption],
        criteria: RankingCriteria,
        context: dict[str, Any] | None,
    ) -> str:
        """Generate analysis summary text."""
        if not ranked_options:
            return "No options to analyze."

        top = ranked_options[0]
        parts = []

        # Top recommendation
        parts.append(
            f"**Top Recommendation:** {top.option.name} (score: {top.total_score:.2f})"
        )

        # Key factors
        top_criteria = max(
            [
                ("success_probability", criteria.success_probability),
                ("risk_score", criteria.risk_score),
                ("time_to_resolution", criteria.time_to_resolution),
            ],
            key=lambda x: x[1],
        )
        parts.append(f"**Primary Factor:** {top_criteria[0].replace('_', ' ').title()}")

        # Close alternatives
        close = [ro for ro in ranked_options[1:] if ro.percentage_of_top >= 90]
        if close:
            parts.append(f"**Close Alternatives:** {len(close)} options within 10% of top score")

        # Risk note
        if top.scores["risk_score"] < 0.5:
            parts.append("**Note:** Top option has elevated risk - consider additional safeguards")

        # Context-specific notes
        if context:
            if context.get("incident_severity") in ("P0", "P1"):
                parts.append("**Context:** High-severity incident - speed prioritized")
            if context.get("is_change_freeze"):
                parts.append("**Warning:** Change freeze active - approval required")

        return " | ".join(parts)

    def _generate_ranking_id(self, options: list[DecisionOption]) -> str:
        """Generate unique ranking ID."""
        content = ":".join([o.option_id for o in options])
        timestamp = datetime.now(timezone.utc).isoformat()
        return f"rank_{hashlib.sha256(f'{content}:{timestamp}'.encode()).hexdigest()[:12]}"

    def compare_options(
        self,
        option_a: DecisionOption,
        option_b: DecisionOption,
        criteria: RankingCriteria | None = None,
    ) -> dict[str, Any]:
        """Compare two options head-to-head."""
        criteria = criteria or RankingCriteria()

        scores_a = self._calculate_scores(option_a, criteria)
        scores_b = self._calculate_scores(option_b, criteria)

        total_a = self._calculate_total_score(scores_a, criteria)
        total_b = self._calculate_total_score(scores_b, criteria)

        comparison = {
            "option_a": {
                "name": option_a.name,
                "total_score": round(total_a, 3),
                "scores": {k: round(v, 3) for k, v in scores_a.items()},
            },
            "option_b": {
                "name": option_b.name,
                "total_score": round(total_b, 3),
                "scores": {k: round(v, 3) for k, v in scores_b.items()},
            },
            "winner": option_a.name if total_a > total_b else option_b.name,
            "margin": abs(total_a - total_b),
            "factors_a_wins": [],
            "factors_b_wins": [],
        }

        for factor in scores_a.keys():
            if scores_a[factor] > scores_b[factor]:
                comparison["factors_a_wins"].append(factor)
            elif scores_b[factor] > scores_a[factor]:
                comparison["factors_b_wins"].append(factor)

        return comparison


def create_option_ranker(
    strategy: RankingStrategy = RankingStrategy.BALANCED,
) -> OptionRanker:
    """Factory function to create an option ranker."""
    return OptionRanker(default_strategy=strategy)
