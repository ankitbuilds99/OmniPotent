"""PR Risk Analyzer.

Analyzes pull requests for risk factors and generates risk scores.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("omnipotent.github_app.risk_analyzer")


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class RiskFactor:
    """A factor contributing to PR risk."""

    factor_id: str
    name: str
    score: float
    max_score: float = 10.0
    description: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def level(self) -> RiskLevel:
        if self.score >= 8:
            return RiskLevel.CRITICAL
        elif self.score >= 6:
            return RiskLevel.HIGH
        elif self.score >= 4:
            return RiskLevel.MEDIUM
        return RiskLevel.LOW

    @property
    def icon(self) -> str:
        if self.score >= 8:
            return "red_circle"
        elif self.score >= 6:
            return "orange_circle"
        elif self.score >= 4:
            return "yellow_circle"
        return "green_circle"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.factor_id,
            "name": self.name,
            "score": round(self.score, 1),
            "level": self.level.value,
            "description": self.description,
            "details": self.details,
        }


@dataclass
class PRFile:
    """A file changed in a PR."""

    path: str
    additions: int = 0
    deletions: int = 0
    status: str = "modified"

    @property
    def changes(self) -> int:
        return self.additions + self.deletions


@dataclass
class PRRiskAnalysis:
    """Complete risk analysis for a PR."""

    pr_number: int
    repo: str
    title: str
    author: str
    overall_score: float
    level: RiskLevel
    factors: list[RiskFactor]
    recommendations: list[str]
    suggested_reviewers: list[str]
    deployment_strategy: str
    should_block: bool = False
    similar_incidents: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pr_number": self.pr_number,
            "repo": self.repo,
            "title": self.title,
            "author": self.author,
            "overall_score": round(self.overall_score, 1),
            "level": self.level.value,
            "factors": [f.to_dict() for f in self.factors],
            "recommendations": self.recommendations,
            "suggested_reviewers": self.suggested_reviewers,
            "deployment_strategy": self.deployment_strategy,
            "should_block": self.should_block,
            "similar_incidents": self.similar_incidents,
        }

    def to_github_comment(self) -> str:
        """Generate GitHub PR comment."""
        icon = {"critical": "rotating_light", "high": "warning", "medium": "large_orange_diamond", "low": "white_check_mark"}

        lines = [
            f"## :{icon.get(self.level.value, 'question')}: DevOps Copilot Risk Analysis",
            "",
            f"**Risk Score: {self.overall_score:.1f}/10 ({self.level.value.upper()})**",
            "",
            "### Risk Factors",
            "",
            "| Factor | Score | Details |",
            "|--------|-------|---------|",
        ]

        for factor in sorted(self.factors, key=lambda f: f.score, reverse=True):
            lines.append(f"| :{factor.icon}: {factor.name} | {factor.score:.1f} | {factor.description} |")

        if self.similar_incidents:
            lines.extend([
                "",
                "### Similar PRs That Caused Incidents",
                "",
            ])
            for inc in self.similar_incidents[:3]:
                lines.append(f"- #{inc.get('pr_number')} ({inc.get('similarity')}% similar) - {inc.get('incident_id')}")

        lines.extend([
            "",
            "### Recommendations",
            "",
        ])
        for rec in self.recommendations:
            lines.append(f"- {rec}")

        if self.suggested_reviewers:
            lines.extend([
                "",
                "### Suggested Reviewers",
                "",
                f"{', '.join(f'@{r}' for r in self.suggested_reviewers)}",
            ])

        lines.extend([
            "",
            f"**Deployment Strategy:** {self.deployment_strategy}",
            "",
            "---",
            "*Powered by [DevOps Copilot](https://devopscopilot.com)*",
        ])

        return "\n".join(lines)


class RiskAnalyzer:
    """Analyzes PR risk."""

    def __init__(self) -> None:
        self._file_incident_history: dict[str, list[dict[str, Any]]] = {}
        self._author_stats: dict[str, dict[str, Any]] = {}

        self._high_risk_patterns = [
            (r"payment|billing|checkout|transaction", "Payment processing code"),
            (r"auth|login|session|token|password|credential", "Authentication code"),
            (r"database|migration|schema", "Database changes"),
            (r"config|secret|key|env", "Configuration/secrets"),
            (r"production|prod|deploy", "Production-related"),
        ]

        self._dangerous_patterns = [
            (r"\.rm\s+-rf", "Dangerous rm command"),
            (r"DROP\s+TABLE", "SQL DROP TABLE"),
            (r"DELETE\s+FROM.*WHERE\s+1=1", "Mass delete"),
            (r"eval\(", "Eval usage"),
            (r"exec\(", "Exec usage"),
        ]

        self._null_safety_patterns = [
            (r"if\s+\w+\s+is\s+None:", "Null check removed"),
            (r"\.get\([^)]+,\s*None\)", "Safe get pattern"),
            (r"\w+\s+or\s+\[\]", "Default empty list"),
        ]

    def register_file_incident(
        self,
        file_path: str,
        incident_id: str,
        incident_date: datetime,
        severity: str,
    ) -> None:
        """Register that a file was involved in an incident."""
        if file_path not in self._file_incident_history:
            self._file_incident_history[file_path] = []

        self._file_incident_history[file_path].append({
            "incident_id": incident_id,
            "date": incident_date,
            "severity": severity,
        })

    def register_author_stats(
        self,
        author: str,
        total_prs: int,
        incident_prs: int,
    ) -> None:
        """Register author's historical stats."""
        self._author_stats[author] = {
            "total_prs": total_prs,
            "incident_prs": incident_prs,
            "incident_rate": incident_prs / total_prs if total_prs > 0 else 0,
        }

    def analyze_pr(
        self,
        pr_number: int,
        repo: str,
        title: str,
        author: str,
        files: list[PRFile],
        diff_content: str = "",
        base_branch: str = "main",
    ) -> PRRiskAnalysis:
        """Analyze a PR for risk."""
        factors: list[RiskFactor] = []

        file_risk = self._analyze_file_risk(files)
        factors.append(file_risk)

        history_risk = self._analyze_history_risk(files)
        factors.append(history_risk)

        pattern_risk = self._analyze_pattern_risk(diff_content, files)
        factors.append(pattern_risk)

        blast_risk = self._analyze_blast_radius(files)
        factors.append(blast_risk)

        author_risk = self._analyze_author_risk(author)
        factors.append(author_risk)

        overall_score = self._calculate_overall_score(factors)
        level = self._determine_level(overall_score)

        recommendations = self._generate_recommendations(level, factors, files)
        deployment_strategy = self._suggest_deployment_strategy(overall_score)

        reviewers = self._suggest_reviewers(files)

        should_block = overall_score >= 8.0

        return PRRiskAnalysis(
            pr_number=pr_number,
            repo=repo,
            title=title,
            author=author,
            overall_score=overall_score,
            level=level,
            factors=factors,
            recommendations=recommendations,
            suggested_reviewers=reviewers,
            deployment_strategy=deployment_strategy,
            should_block=should_block,
        )

    def _analyze_file_risk(self, files: list[PRFile]) -> RiskFactor:
        """Analyze risk based on files changed."""
        score = 0.0
        details: dict[str, Any] = {"high_risk_files": []}

        for f in files:
            for pattern, desc in self._high_risk_patterns:
                if re.search(pattern, f.path, re.IGNORECASE):
                    score += 2.0
                    details["high_risk_files"].append({
                        "path": f.path,
                        "reason": desc,
                    })
                    break

        if len(files) > 20:
            score += 2.0
            details["large_change"] = f"{len(files)} files modified"
        elif len(files) > 10:
            score += 1.0

        total_changes = sum(f.changes for f in files)
        if total_changes > 1000:
            score += 2.0
            details["lines_changed"] = total_changes
        elif total_changes > 500:
            score += 1.0

        return RiskFactor(
            factor_id="file_risk",
            name="File Risk",
            score=min(10.0, score),
            description=f"{len(files)} files, {total_changes} lines changed",
            details=details,
        )

    def _analyze_history_risk(self, files: list[PRFile]) -> RiskFactor:
        """Analyze risk based on file incident history."""
        score = 0.0
        details: dict[str, Any] = {"incident_files": []}

        ninety_days_ago = datetime.now(timezone.utc) - timedelta(days=90)

        for f in files:
            history = self._file_incident_history.get(f.path, [])
            recent = [h for h in history if h["date"] > ninety_days_ago]

            if recent:
                incident_count = len(recent)
                score += min(3.0, incident_count * 1.5)
                details["incident_files"].append({
                    "path": f.path,
                    "incidents_90d": incident_count,
                })

        description = ""
        if details["incident_files"]:
            count = len(details["incident_files"])
            description = f"{count} files with recent incidents"
        else:
            description = "No recent incident history"

        return RiskFactor(
            factor_id="history_risk",
            name="Incident History",
            score=min(10.0, score),
            description=description,
            details=details,
        )

    def _analyze_pattern_risk(
        self,
        diff_content: str,
        files: list[PRFile],
    ) -> RiskFactor:
        """Analyze risk based on code patterns."""
        score = 0.0
        details: dict[str, Any] = {"patterns_found": []}

        for pattern, desc in self._dangerous_patterns:
            if re.search(pattern, diff_content, re.IGNORECASE):
                score += 3.0
                details["patterns_found"].append({
                    "pattern": desc,
                    "severity": "critical",
                })

        null_checks_removed = 0
        for line in diff_content.split("\n"):
            if line.startswith("-") and "is None" in line:
                null_checks_removed += 1

        if null_checks_removed > 0:
            score += 2.0 * null_checks_removed
            details["null_checks_removed"] = null_checks_removed

        description = ""
        if details["patterns_found"]:
            description = f"Found {len(details['patterns_found'])} risky patterns"
        elif null_checks_removed:
            description = f"Removed {null_checks_removed} null safety checks"
        else:
            description = "No risky patterns detected"

        return RiskFactor(
            factor_id="pattern_risk",
            name="Pattern Match",
            score=min(10.0, score),
            description=description,
            details=details,
        )

    def _analyze_blast_radius(self, files: list[PRFile]) -> RiskFactor:
        """Analyze potential blast radius."""
        score = 0.0
        details: dict[str, Any] = {"services_affected": []}

        services = set()
        for f in files:
            parts = f.path.split("/")
            if len(parts) >= 2:
                if parts[0] in ["app", "src", "lib", "services"]:
                    services.add(parts[1])
                else:
                    services.add(parts[0])

        service_count = len(services)
        if service_count >= 5:
            score = 8.0
        elif service_count >= 3:
            score = 5.0
        elif service_count >= 2:
            score = 3.0
        else:
            score = 1.0

        details["services_affected"] = list(services)
        details["service_count"] = service_count

        return RiskFactor(
            factor_id="blast_radius",
            name="Blast Radius",
            score=score,
            description=f"Affects {service_count} service(s)",
            details=details,
        )

    def _analyze_author_risk(self, author: str) -> RiskFactor:
        """Analyze risk based on author history."""
        stats = self._author_stats.get(author, {
            "total_prs": 0,
            "incident_prs": 0,
            "incident_rate": 0,
        })

        incident_rate = stats.get("incident_rate", 0)

        if incident_rate > 0.1:
            score = 7.0
        elif incident_rate > 0.05:
            score = 5.0
        elif incident_rate > 0.02:
            score = 3.0
        else:
            score = 1.0

        if stats.get("total_prs", 0) < 10:
            score = max(score, 4.0)

        return RiskFactor(
            factor_id="author_risk",
            name="Author History",
            score=score,
            description=f"{incident_rate*100:.1f}% historical incident rate",
            details={"author": author, **stats},
        )

    def _calculate_overall_score(self, factors: list[RiskFactor]) -> float:
        """Calculate overall risk score."""
        if not factors:
            return 0.0

        weights = {
            "file_risk": 0.2,
            "history_risk": 0.25,
            "pattern_risk": 0.3,
            "blast_radius": 0.15,
            "author_risk": 0.1,
        }

        total_weight = 0.0
        weighted_sum = 0.0

        for factor in factors:
            weight = weights.get(factor.factor_id, 0.1)
            weighted_sum += factor.score * weight
            total_weight += weight

        return weighted_sum / total_weight if total_weight > 0 else 0.0

    def _determine_level(self, score: float) -> RiskLevel:
        """Determine risk level from score."""
        if score >= 8:
            return RiskLevel.CRITICAL
        elif score >= 6:
            return RiskLevel.HIGH
        elif score >= 4:
            return RiskLevel.MEDIUM
        return RiskLevel.LOW

    def _generate_recommendations(
        self,
        level: RiskLevel,
        factors: list[RiskFactor],
        files: list[PRFile],
    ) -> list[str]:
        """Generate recommendations based on risk analysis."""
        recs = []

        if level in [RiskLevel.CRITICAL, RiskLevel.HIGH]:
            recs.append("Do NOT deploy directly to 100%")
            recs.append("Use canary deployment at 5% traffic")
            recs.append("Deploy Monday-Wednesday (not Friday)")

        for factor in factors:
            if factor.factor_id == "pattern_risk" and factor.score >= 6:
                recs.append("Review null safety changes carefully")
            if factor.factor_id == "history_risk" and factor.score >= 5:
                recs.append("These files have caused recent incidents - extra review needed")
            if factor.factor_id == "blast_radius" and factor.score >= 6:
                recs.append("Large blast radius - consider splitting PR")

        return recs

    def _suggest_deployment_strategy(self, score: float) -> str:
        """Suggest deployment strategy based on risk."""
        if score >= 8:
            return "Canary with 1% traffic, extended monitoring, manual approval required"
        elif score >= 6:
            return "Canary with 5% traffic, 30-minute bake time"
        elif score >= 4:
            return "Canary with 10% traffic"
        return "Standard deployment"

    def _suggest_reviewers(self, files: list[PRFile]) -> list[str]:
        """Suggest reviewers based on files changed."""
        return []


_analyzer: RiskAnalyzer | None = None


def get_risk_analyzer() -> RiskAnalyzer:
    """Get the default risk analyzer."""
    global _analyzer
    if _analyzer is None:
        _analyzer = RiskAnalyzer()
    return _analyzer
