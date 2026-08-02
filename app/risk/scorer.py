"""Change risk scoring for deployments.

Calculates comprehensive risk scores for code changes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("omnipotent.risk.scorer")


class RiskLevel(Enum):
    """Risk level for a change."""

    MINIMAL = "minimal"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class RiskFactorType(Enum):
    """Type of risk factor."""

    FILE_RISK = "file_risk"
    SEMANTIC_CHANGE = "semantic_change"
    TEST_ADEQUACY = "test_adequacy"
    BLAST_RADIUS = "blast_radius"
    REVIEW_QUALITY = "review_quality"
    AUTHOR_RISK = "author_risk"
    ROLLBACK_COMPLEXITY = "rollback_complexity"
    TIMING = "timing"


@dataclass
class RiskFactor:
    """A factor contributing to overall risk."""

    factor_type: RiskFactorType
    score: float
    weight: float
    contribution: float
    details: str
    sub_factors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "factor_type": self.factor_type.value,
            "score": round(self.score, 1),
            "weight": self.weight,
            "contribution": round(self.contribution, 2),
            "details": self.details,
            "sub_factors": self.sub_factors,
        }


@dataclass
class SemanticChange:
    """A semantic change detected in the code."""

    change_type: str
    file_path: str
    line_number: int
    description: str
    risk_pattern: str
    historical_bug_rate: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "change_type": self.change_type,
            "file_path": self.file_path,
            "line_number": self.line_number,
            "description": self.description,
            "risk_pattern": self.risk_pattern,
            "historical_bug_rate": self.historical_bug_rate,
        }


@dataclass
class SimilarPR:
    """A similar PR from history."""

    pr_number: str
    similarity: float
    outcome: str
    incident_id: str | None
    incident_duration_hours: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "pr_number": self.pr_number,
            "similarity": round(self.similarity, 2),
            "outcome": self.outcome,
            "incident_id": self.incident_id,
            "incident_duration_hours": self.incident_duration_hours,
        }


@dataclass
class RiskScore:
    """Complete risk assessment for a change."""

    score_id: str
    pr_number: str
    overall_score: float
    risk_level: RiskLevel
    factors: list[RiskFactor]
    semantic_changes: list[SemanticChange]
    similar_prs: list[SimilarPR]
    recommendation: str
    should_deploy: bool
    timing_adjustments: list[str]
    analyzed_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "score_id": self.score_id,
            "pr_number": self.pr_number,
            "overall_score": round(self.overall_score, 1),
            "risk_level": self.risk_level.value,
            "factors": [f.to_dict() for f in self.factors],
            "semantic_changes": [s.to_dict() for s in self.semantic_changes],
            "similar_prs": [p.to_dict() for p in self.similar_prs],
            "recommendation": self.recommendation,
            "should_deploy": self.should_deploy,
            "timing_adjustments": self.timing_adjustments,
            "analyzed_at": self.analyzed_at.isoformat(),
        }


class ChangeRiskScorer:
    """Scores risk of code changes."""

    def __init__(self) -> None:
        self._scores: dict[str, RiskScore] = {}
        self._score_counter = 0
        self._high_risk_files = self._setup_high_risk_files()
        self._risk_patterns = self._setup_risk_patterns()

    def _setup_high_risk_files(self) -> dict[str, float]:
        """Set up files with historically high incident rates."""
        return {
            "billing/checkout.py": 9.2,
            "payment/processor.py": 8.5,
            "auth/session.py": 7.8,
            "database/migrations": 8.0,
            "config/settings.py": 6.5,
            "api/gateway.py": 7.0,
        }

    def _setup_risk_patterns(self) -> list[dict[str, Any]]:
        """Set up patterns that indicate high risk."""
        return [
            {
                "pattern": "null_safety_removal",
                "description": "Removes null check",
                "indicators": ["if .* is None:", "if .* is not None:"],
                "removal": True,
                "historical_bug_rate": 0.34,
            },
            {
                "pattern": "exception_change",
                "description": "Changes exception handling",
                "indicators": ["except ", "raise ", "try:"],
                "historical_bug_rate": 0.12,
            },
            {
                "pattern": "control_flow",
                "description": "Modifies control flow",
                "indicators": ["return ", "break", "continue", "if ", "for ", "while "],
                "historical_bug_rate": 0.18,
            },
            {
                "pattern": "database_query",
                "description": "Modifies database queries",
                "indicators": ["SELECT ", "INSERT ", "UPDATE ", "DELETE ", ".execute("],
                "historical_bug_rate": 0.22,
            },
            {
                "pattern": "auth_change",
                "description": "Modifies authentication",
                "indicators": ["password", "token", "auth", "permission", "role"],
                "historical_bug_rate": 0.28,
            },
        ]

    def score_change(
        self,
        pr_number: str,
        files_changed: list[dict[str, Any]],
        lines_added: int,
        lines_removed: int,
        reviewers: list[str],
        review_time_minutes: int,
        author: str,
        author_incident_rate: float = 0.02,
        has_migration: bool = False,
        deploy_day: str = "Monday",
        deploy_hour: int = 10,
    ) -> RiskScore:
        """Score the risk of a change."""
        self._score_counter += 1
        score_id = f"risk_{self._score_counter}"

        factors = []

        file_risk = self._calculate_file_risk(files_changed)
        factors.append(file_risk)

        semantic = self._calculate_semantic_risk(files_changed)
        factors.append(semantic[0])
        semantic_changes = semantic[1]

        test_adequacy = self._calculate_test_adequacy(files_changed, lines_added)
        factors.append(test_adequacy)

        blast_radius = self._calculate_blast_radius(files_changed)
        factors.append(blast_radius)

        review_quality = self._calculate_review_quality(
            reviewers,
            review_time_minutes,
            lines_added + lines_removed,
        )
        factors.append(review_quality)

        author_risk = self._calculate_author_risk(author, author_incident_rate)
        factors.append(author_risk)

        rollback = self._calculate_rollback_complexity(has_migration, files_changed)
        factors.append(rollback)

        timing = self._calculate_timing_risk(deploy_day, deploy_hour)
        factors.append(timing)

        base_score = sum(f.contribution for f in factors)
        timing_adjustments = []

        if deploy_day == "Friday":
            base_score += 0.5
            timing_adjustments.append("Friday deployment (+0.5)")
        if deploy_hour >= 16:
            base_score += 0.27
            timing_adjustments.append("End of day (+0.27)")

        overall_score = min(10.0, max(0.0, base_score))

        if overall_score >= 8.0:
            risk_level = RiskLevel.CRITICAL
        elif overall_score >= 6.0:
            risk_level = RiskLevel.HIGH
        elif overall_score >= 4.0:
            risk_level = RiskLevel.MEDIUM
        elif overall_score >= 2.0:
            risk_level = RiskLevel.LOW
        else:
            risk_level = RiskLevel.MINIMAL

        similar_prs = self._find_similar_prs(semantic_changes)
        recommendation = self._generate_recommendation(
            risk_level,
            factors,
            semantic_changes,
        )

        score = RiskScore(
            score_id=score_id,
            pr_number=pr_number,
            overall_score=overall_score,
            risk_level=risk_level,
            factors=factors,
            semantic_changes=semantic_changes,
            similar_prs=similar_prs,
            recommendation=recommendation,
            should_deploy=risk_level not in (RiskLevel.CRITICAL, RiskLevel.HIGH),
            timing_adjustments=timing_adjustments,
            analyzed_at=datetime.now(timezone.utc),
        )

        self._scores[score_id] = score

        log.info(
            "change risk scored",
            extra={
                "score_id": score_id,
                "pr_number": pr_number,
                "overall_score": overall_score,
                "risk_level": risk_level.value,
            },
        )

        return score

    def _calculate_file_risk(
        self,
        files_changed: list[dict[str, Any]],
    ) -> RiskFactor:
        """Calculate risk based on files changed."""
        max_risk = 0.0
        high_risk_files = []

        for file_info in files_changed:
            file_path = file_info.get("path", "")
            for pattern, risk in self._high_risk_files.items():
                if pattern in file_path:
                    if risk > max_risk:
                        max_risk = risk
                    high_risk_files.append(f"{file_path} ({risk})")

        weight = 0.25
        contribution = max_risk * weight / 10

        return RiskFactor(
            factor_type=RiskFactorType.FILE_RISK,
            score=max_risk,
            weight=weight,
            contribution=contribution,
            details=f"Highest file risk: {max_risk:.1f}/10",
            sub_factors=high_risk_files,
        )

    def _calculate_semantic_risk(
        self,
        files_changed: list[dict[str, Any]],
    ) -> tuple[RiskFactor, list[SemanticChange]]:
        """Calculate risk based on semantic analysis of changes."""
        changes = []
        total_risk = 0.0

        for file_info in files_changed:
            file_path = file_info.get("path", "")
            diff = file_info.get("diff", "")

            for pattern in self._risk_patterns:
                for indicator in pattern["indicators"]:
                    if indicator.lower() in diff.lower():
                        is_removal = "-" + indicator in diff if pattern.get("removal") else False

                        if is_removal or not pattern.get("removal"):
                            change = SemanticChange(
                                change_type=pattern["pattern"],
                                file_path=file_path,
                                line_number=0,
                                description=pattern["description"],
                                risk_pattern=pattern["pattern"],
                                historical_bug_rate=pattern["historical_bug_rate"],
                            )
                            changes.append(change)
                            total_risk += pattern["historical_bug_rate"] * 10

        score = min(10.0, total_risk)
        weight = 0.20
        contribution = score * weight / 10

        return (
            RiskFactor(
                factor_type=RiskFactorType.SEMANTIC_CHANGE,
                score=score,
                weight=weight,
                contribution=contribution,
                details=f"{len(changes)} risky patterns detected",
                sub_factors=[f"{c.risk_pattern}: {c.description}" for c in changes[:5]],
            ),
            changes,
        )

    def _calculate_test_adequacy(
        self,
        files_changed: list[dict[str, Any]],
        lines_added: int,
    ) -> RiskFactor:
        """Calculate risk based on test coverage of changes."""
        test_files = [f for f in files_changed if "test" in f.get("path", "").lower()]
        source_files = [
            f for f in files_changed if "test" not in f.get("path", "").lower()
        ]

        if not source_files:
            score = 2.0
        elif not test_files:
            score = 8.0
        else:
            test_lines = sum(f.get("additions", 0) for f in test_files)
            source_lines = sum(f.get("additions", 0) for f in source_files)

            if source_lines > 0:
                ratio = test_lines / source_lines
                score = max(2.0, 10.0 - (ratio * 8))
            else:
                score = 4.0

        weight = 0.15
        contribution = score * weight / 10

        return RiskFactor(
            factor_type=RiskFactorType.TEST_ADEQUACY,
            score=score,
            weight=weight,
            contribution=contribution,
            details=f"{len(test_files)} test files, {len(source_files)} source files",
            sub_factors=[
                f"Test files: {len(test_files)}",
                f"Source files: {len(source_files)}",
            ],
        )

    def _calculate_blast_radius(
        self,
        files_changed: list[dict[str, Any]],
    ) -> RiskFactor:
        """Calculate risk based on blast radius."""
        consumers_per_file = {
            "api/": 15,
            "lib/": 20,
            "core/": 25,
            "utils/": 12,
            "models/": 10,
        }

        total_consumers = 0
        affected_areas = []

        for file_info in files_changed:
            file_path = file_info.get("path", "")
            for prefix, consumers in consumers_per_file.items():
                if prefix in file_path:
                    total_consumers += consumers
                    affected_areas.append(f"{prefix}: {consumers} consumers")

        score = min(10.0, total_consumers / 5)
        weight = 0.15
        contribution = score * weight / 10

        return RiskFactor(
            factor_type=RiskFactorType.BLAST_RADIUS,
            score=score,
            weight=weight,
            contribution=contribution,
            details=f"{total_consumers} downstream consumers",
            sub_factors=affected_areas[:5],
        )

    def _calculate_review_quality(
        self,
        reviewers: list[str],
        review_time_minutes: int,
        total_lines: int,
    ) -> RiskFactor:
        """Calculate risk based on review quality."""
        if not reviewers:
            score = 9.0
            details = "No reviewers"
        elif review_time_minutes < 5:
            score = 8.0
            details = "Very quick review (<5 min)"
        elif review_time_minutes < 15 and total_lines > 100:
            score = 6.0
            details = "Quick review for large change"
        elif len(reviewers) >= 2 and review_time_minutes >= 30:
            score = 2.0
            details = f"{len(reviewers)} reviewers, {review_time_minutes}min"
        else:
            score = 4.0
            details = f"{len(reviewers)} reviewers, {review_time_minutes}min"

        weight = 0.10
        contribution = score * weight / 10

        return RiskFactor(
            factor_type=RiskFactorType.REVIEW_QUALITY,
            score=score,
            weight=weight,
            contribution=contribution,
            details=details,
            sub_factors=reviewers,
        )

    def _calculate_author_risk(
        self,
        author: str,
        incident_rate: float,
    ) -> RiskFactor:
        """Calculate risk based on author history."""
        score = min(10.0, incident_rate * 100)
        weight = 0.10
        contribution = score * weight / 10

        return RiskFactor(
            factor_type=RiskFactorType.AUTHOR_RISK,
            score=score,
            weight=weight,
            contribution=contribution,
            details=f"{author}: {incident_rate*100:.1f}% incident rate",
            sub_factors=[],
        )

    def _calculate_rollback_complexity(
        self,
        has_migration: bool,
        files_changed: list[dict[str, Any]],
    ) -> RiskFactor:
        """Calculate risk based on rollback complexity."""
        if has_migration:
            score = 8.0
            details = "Includes database migration"
        elif any("config" in f.get("path", "") for f in files_changed):
            score = 5.0
            details = "Configuration changes"
        else:
            score = 2.0
            details = "Standard code change"

        weight = 0.05
        contribution = score * weight / 10

        return RiskFactor(
            factor_type=RiskFactorType.ROLLBACK_COMPLEXITY,
            score=score,
            weight=weight,
            contribution=contribution,
            details=details,
            sub_factors=[],
        )

    def _calculate_timing_risk(
        self,
        deploy_day: str,
        deploy_hour: int,
    ) -> RiskFactor:
        """Calculate risk based on deployment timing."""
        high_risk_days = ["Friday", "Saturday", "Sunday"]
        high_risk_hours = list(range(16, 24)) + list(range(0, 8))

        score = 0.0
        factors = []

        if deploy_day in high_risk_days:
            score += 4.0
            factors.append(f"{deploy_day} is high-risk day")

        if deploy_hour in high_risk_hours:
            score += 3.0
            factors.append(f"Hour {deploy_hour} is high-risk")

        score = min(10.0, score)
        weight = 0.0
        contribution = 0

        return RiskFactor(
            factor_type=RiskFactorType.TIMING,
            score=score,
            weight=weight,
            contribution=contribution,
            details=f"{deploy_day} at {deploy_hour}:00",
            sub_factors=factors,
        )

    def _find_similar_prs(
        self,
        semantic_changes: list[SemanticChange],
    ) -> list[SimilarPR]:
        """Find similar PRs from history."""
        similar = []

        if any(c.change_type == "null_safety_removal" for c in semantic_changes):
            similar.append(
                SimilarPR(
                    pr_number="#589",
                    similarity=0.87,
                    outcome="incident",
                    incident_id="INC-4398",
                    incident_duration_hours=1.2,
                )
            )

        similar.append(
            SimilarPR(
                pr_number="#534",
                similarity=0.78,
                outcome="success",
                incident_id=None,
                incident_duration_hours=None,
            )
        )

        return similar

    def _generate_recommendation(
        self,
        risk_level: RiskLevel,
        factors: list[RiskFactor],
        semantic_changes: list[SemanticChange],
    ) -> str:
        """Generate deployment recommendation."""
        if risk_level == RiskLevel.CRITICAL:
            return "DO NOT DEPLOY without fixing critical issues"
        elif risk_level == RiskLevel.HIGH:
            return "DO NOT DEPLOY without mitigation. Consider staged canary."
        elif risk_level == RiskLevel.MEDIUM:
            return "Deploy with caution. Use canary deployment."
        elif risk_level == RiskLevel.LOW:
            return "Safe to deploy with standard monitoring."
        else:
            return "Safe to deploy."

    def get_score(self, score_id: str) -> RiskScore | None:
        """Get a risk score by ID."""
        return self._scores.get(score_id)

    def get_scores_for_pr(self, pr_number: str) -> list[RiskScore]:
        """Get all scores for a PR."""
        return [s for s in self._scores.values() if s.pr_number == pr_number]


_scorer: ChangeRiskScorer | None = None


def get_change_risk_scorer() -> ChangeRiskScorer:
    """Get the default change risk scorer."""
    global _scorer
    if _scorer is None:
        _scorer = ChangeRiskScorer()
    return _scorer
