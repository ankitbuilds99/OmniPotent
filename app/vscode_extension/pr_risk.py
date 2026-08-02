"""PR Risk Preview.

Analyzes PR risk before pushing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("omnipotent.vscode_extension.pr_risk")


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class RiskFactor:
    """A risk factor in the analysis."""

    factor_id: str
    category: str
    description: str
    severity: RiskLevel
    score: float
    recommendation: str = ""
    similar_incident: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.factor_id,
            "category": self.category,
            "description": self.description,
            "severity": self.severity.value,
            "score": round(self.score, 1),
            "recommendation": self.recommendation,
            "similar_incident": self.similar_incident,
        }


@dataclass
class PRRiskAnalysis:
    """Full risk analysis for a PR."""

    analysis_id: str
    total_score: float
    risk_level: RiskLevel
    factors: list[RiskFactor]
    files_analyzed: int
    lines_changed: int
    safe_to_push: bool
    analyzed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.analysis_id,
            "score": round(self.total_score, 1),
            "risk_level": self.risk_level.value,
            "factors": [f.to_dict() for f in self.factors],
            "files_analyzed": self.files_analyzed,
            "lines_changed": self.lines_changed,
            "safe_to_push": self.safe_to_push,
            "analyzed_at": self.analyzed_at.isoformat(),
        }


class PRRiskPreview:
    """Previews PR risk before pushing."""

    def __init__(self) -> None:
        self._incident_patterns: dict[str, dict[str, Any]] = {}
        self._init_patterns()

    def _init_patterns(self) -> None:
        """Initialize incident patterns for matching."""
        self._incident_patterns["null_check"] = {
            "patterns": ["is None", "is not None", ".get(", "or None"],
            "incident": "INC-4521",
            "description": "Removed null check",
            "severity": RiskLevel.HIGH,
        }

        self._incident_patterns["error_handling"] = {
            "patterns": ["except:", "except Exception:", "try:", "raise"],
            "incident": "INC-3892",
            "description": "Modified error handling",
            "severity": RiskLevel.MEDIUM,
        }

        self._incident_patterns["timeout"] = {
            "patterns": ["timeout", "deadline", "ttl", "expire"],
            "incident": "INC-4102",
            "description": "Changed timeout configuration",
            "severity": RiskLevel.MEDIUM,
        }

        self._incident_patterns["connection"] = {
            "patterns": ["connection", "pool", "close()", "disconnect"],
            "incident": "INC-5521",
            "description": "Modified connection handling",
            "severity": RiskLevel.HIGH,
        }

    def analyze_changes(
        self,
        files: list[dict[str, Any]],
    ) -> PRRiskAnalysis:
        """Analyze staged changes for risk."""
        factors: list[RiskFactor] = []
        total_lines = 0

        for file_info in files:
            file_path = file_info.get("path", "")
            additions = file_info.get("additions", [])
            deletions = file_info.get("deletions", [])

            total_lines += len(additions) + len(deletions)

            file_factors = self._analyze_file(
                file_path,
                additions,
                deletions,
            )
            factors.extend(file_factors)

        total_score = sum(f.score for f in factors)

        if total_score >= 8:
            risk_level = RiskLevel.CRITICAL
            safe = False
        elif total_score >= 5:
            risk_level = RiskLevel.HIGH
            safe = False
        elif total_score >= 2:
            risk_level = RiskLevel.MEDIUM
            safe = True
        else:
            risk_level = RiskLevel.LOW
            safe = True

        return PRRiskAnalysis(
            analysis_id=f"analysis_{datetime.now(timezone.utc).timestamp()}",
            total_score=total_score,
            risk_level=risk_level,
            factors=factors,
            files_analyzed=len(files),
            lines_changed=total_lines,
            safe_to_push=safe,
        )

    def _analyze_file(
        self,
        file_path: str,
        additions: list[str],
        deletions: list[str],
    ) -> list[RiskFactor]:
        """Analyze a single file for risk factors."""
        factors: list[RiskFactor] = []
        factor_count = 0

        for pattern_name, pattern_info in self._incident_patterns.items():
            patterns = pattern_info["patterns"]

            for deletion in deletions:
                for pattern in patterns:
                    if pattern.lower() in deletion.lower():
                        has_in_additions = any(
                            pattern.lower() in add.lower()
                            for add in additions
                        )

                        if not has_in_additions:
                            factor_count += 1
                            factors.append(RiskFactor(
                                factor_id=f"risk_{factor_count}",
                                category=pattern_name,
                                description=f"{pattern_info['description']} in {file_path}",
                                severity=pattern_info["severity"],
                                score=3.0 if pattern_info["severity"] == RiskLevel.HIGH else 1.5,
                                recommendation="Verify this change doesn't introduce regressions",
                                similar_incident=pattern_info["incident"],
                            ))
                            break

        if not self._has_test_file(file_path):
            has_testable_changes = any(
                "def " in add or "class " in add
                for add in additions
            )
            if has_testable_changes:
                factor_count += 1
                factors.append(RiskFactor(
                    factor_id=f"risk_{factor_count}",
                    category="test_coverage",
                    description="Missing test coverage for new code",
                    severity=RiskLevel.MEDIUM,
                    score=2.0,
                    recommendation="Add unit tests for the new functionality",
                ))

        if len(additions) + len(deletions) > 200:
            factor_count += 1
            factors.append(RiskFactor(
                factor_id=f"risk_{factor_count}",
                category="large_change",
                description=f"Large change ({len(additions) + len(deletions)} lines)",
                severity=RiskLevel.MEDIUM,
                score=1.5,
                recommendation="Consider breaking into smaller PRs",
            ))

        return factors

    def _has_test_file(self, file_path: str) -> bool:
        """Check if a test file exists for the given file."""
        return "test_" in file_path or "_test.py" in file_path

    def quick_check(
        self,
        staged_files: list[str],
    ) -> dict[str, Any]:
        """Quick risk check for status bar."""
        total_risk: float = 0
        high_risk_files = []

        for file_path in staged_files:
            risk = self._quick_file_risk(file_path)
            total_risk += risk
            if risk > 2:
                high_risk_files.append(file_path)

        if total_risk >= 8:
            status = "critical"
            message = "Critical risk detected"
        elif total_risk >= 5:
            status = "high"
            message = "High risk changes"
        elif total_risk >= 2:
            status = "medium"
            message = "Some risk factors"
        else:
            status = "low"
            message = "Changes look safe"

        return {
            "status": status,
            "score": round(total_risk, 1),
            "message": message,
            "high_risk_files": high_risk_files,
        }

    def _quick_file_risk(self, file_path: str) -> float:
        """Quick risk assessment for a file path."""
        risk = 0.0

        high_risk_paths = ["payment", "auth", "checkout", "billing"]
        for path in high_risk_paths:
            if path in file_path.lower():
                risk += 2.0
                break

        if "migration" in file_path.lower():
            risk += 1.5

        if "config" in file_path.lower():
            risk += 1.0

        return risk

    def get_similar_incidents(
        self,
        changes_summary: str,
    ) -> list[dict[str, Any]]:
        """Find similar past incidents."""
        results = []

        for pattern_name, pattern_info in self._incident_patterns.items():
            for pattern in pattern_info["patterns"]:
                if pattern.lower() in changes_summary.lower():
                    results.append({
                        "incident_id": pattern_info["incident"],
                        "pattern": pattern_name,
                        "description": pattern_info["description"],
                        "severity": pattern_info["severity"].value,
                    })

        return results[:5]


_preview: PRRiskPreview | None = None


def get_pr_risk_preview() -> PRRiskPreview:
    """Get the default PR risk preview."""
    global _preview
    if _preview is None:
        _preview = PRRiskPreview()
    return _preview
