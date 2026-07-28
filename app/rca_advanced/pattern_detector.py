"""Historical Bug Pattern Detection.

Detects recurring bug patterns across the codebase over time.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("devcopilot.rca_advanced.pattern_detector")


class PatternCategory(str, Enum):
    NULL_CHECK_REMOVAL = "null_check_removal"
    ERROR_HANDLING_GAP = "error_handling_gap"
    RESOURCE_LEAK = "resource_leak"
    TIMEOUT_ISSUE = "timeout_issue"
    RACE_CONDITION = "race_condition"
    VALIDATION_GAP = "validation_gap"
    TYPE_MISMATCH = "type_mismatch"
    CONFIG_ERROR = "config_error"
    DEPENDENCY_ISSUE = "dependency_issue"
    DEPLOY_REGRESSION = "deploy_regression"


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class BugPattern:
    """A recurring bug pattern."""

    pattern_id: str
    category: PatternCategory
    name: str
    description: str
    detection_regex: str
    file_patterns: list[str]
    teams_affected: list[str]
    total_occurrences: int
    total_cost: float
    avg_resolution_minutes: float
    prevention_suggestions: list[str]
    first_seen: datetime
    last_seen: datetime

    @property
    def days_since_last(self) -> int:
        return (datetime.now(timezone.utc) - self.last_seen).days

    @property
    def frequency_per_month(self) -> float:
        days = (self.last_seen - self.first_seen).days
        if days == 0:
            return self.total_occurrences
        return self.total_occurrences / (days / 30)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pattern_id": self.pattern_id,
            "category": self.category.value,
            "name": self.name,
            "description": self.description,
            "file_patterns": self.file_patterns,
            "teams": self.teams_affected,
            "stats": {
                "occurrences": self.total_occurrences,
                "cost": round(self.total_cost, 2),
                "avg_resolution_minutes": round(self.avg_resolution_minutes, 1),
                "frequency_per_month": round(self.frequency_per_month, 2),
            },
            "prevention": self.prevention_suggestions,
            "first_seen": self.first_seen.isoformat(),
            "last_seen": self.last_seen.isoformat(),
            "days_since_last": self.days_since_last,
        }


@dataclass
class PatternMatch:
    """A match of a bug pattern in current code."""

    match_id: str
    pattern: BugPattern
    file_path: str
    line_number: int
    code_snippet: str
    confidence: float
    severity: Severity
    similar_incidents: list[str]
    recommended_action: str
    estimated_cost_if_unaddressed: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "match_id": self.match_id,
            "pattern": self.pattern.to_dict(),
            "location": {
                "file": self.file_path,
                "line": self.line_number,
            },
            "code": self.code_snippet[:200],
            "confidence": round(self.confidence, 2),
            "severity": self.severity.value,
            "similar_incidents": self.similar_incidents,
            "action": self.recommended_action,
            "estimated_cost": round(self.estimated_cost_if_unaddressed, 2),
        }


@dataclass
class IncidentRecord:
    """A recorded incident for pattern analysis."""

    incident_id: str
    org_id: str
    title: str
    description: str
    root_cause: str
    file_path: str
    line_number: int
    author: str
    team: str
    occurred_at: datetime
    resolved_at: datetime | None
    cost: float
    category: PatternCategory | None = None

    @property
    def duration_minutes(self) -> float:
        if not self.resolved_at:
            return 0
        return (self.resolved_at - self.occurred_at).total_seconds() / 60


class HistoricalPatternDetector:
    """Detects recurring bug patterns from historical incidents."""

    def __init__(self) -> None:
        self._incidents: dict[str, list[IncidentRecord]] = {}
        self._patterns: dict[str, BugPattern] = {}
        self._init_base_patterns()

    def _init_base_patterns(self) -> None:
        """Initialize base pattern definitions."""
        now = datetime.now(timezone.utc)

        self._patterns["null_check"] = BugPattern(
            pattern_id="null_check",
            category=PatternCategory.NULL_CHECK_REMOVAL,
            name="Null Check Removal",
            description="Safety checks for null/None values removed during refactoring",
            detection_regex=r"removed.*null.*check|is\s+None.*deleted",
            file_patterns=["*.py", "*.js", "*.ts"],
            teams_affected=[],
            total_occurrences=0,
            total_cost=0,
            avg_resolution_minutes=0,
            prevention_suggestions=[
                "Enable strict null checking (Mypy/TypeScript strict)",
                "Add pre-commit hook to detect null check removals",
                "Require tests for None input cases",
            ],
            first_seen=now,
            last_seen=now,
        )

        self._patterns["resource_leak"] = BugPattern(
            pattern_id="resource_leak",
            category=PatternCategory.RESOURCE_LEAK,
            name="Resource Leak",
            description="Connections, files, or other resources not properly closed",
            detection_regex=r"connection.*leak|pool.*exhaust|file.*not.*closed",
            file_patterns=["*client.py", "*pool.py", "*connection.py"],
            teams_affected=[],
            total_occurrences=0,
            total_cost=0,
            avg_resolution_minutes=0,
            prevention_suggestions=[
                "Use context managers for all resources",
                "Add connection pool monitoring",
                "Set connection timeouts and limits",
            ],
            first_seen=now,
            last_seen=now,
        )

        self._patterns["timeout_cascade"] = BugPattern(
            pattern_id="timeout_cascade",
            category=PatternCategory.TIMEOUT_ISSUE,
            name="Timeout Cascade",
            description="Missing or misconfigured timeouts causing cascading failures",
            detection_regex=r"timeout.*cascade|deadline.*exceeded|slow.*response",
            file_patterns=["*api.py", "*client.py", "*service.py"],
            teams_affected=[],
            total_occurrences=0,
            total_cost=0,
            avg_resolution_minutes=0,
            prevention_suggestions=[
                "Set explicit timeouts on all external calls",
                "Implement circuit breakers",
                "Add timeout budget tracking",
            ],
            first_seen=now,
            last_seen=now,
        )

        self._patterns["validation_gap"] = BugPattern(
            pattern_id="validation_gap",
            category=PatternCategory.VALIDATION_GAP,
            name="Input Validation Gap",
            description="Missing input validation allowing invalid data through",
            detection_regex=r"invalid.*input|validation.*missing|unexpected.*value",
            file_patterns=["*api.py", "*handler.py", "*controller.py"],
            teams_affected=[],
            total_occurrences=0,
            total_cost=0,
            avg_resolution_minutes=0,
            prevention_suggestions=[
                "Use Pydantic/dataclasses for input validation",
                "Add API schema validation",
                "Test boundary cases explicitly",
            ],
            first_seen=now,
            last_seen=now,
        )

        self._patterns["config_error"] = BugPattern(
            pattern_id="config_error",
            category=PatternCategory.CONFIG_ERROR,
            name="Configuration Error",
            description="Misconfiguration in environment or service settings",
            detection_regex=r"config.*error|env.*missing|setting.*wrong",
            file_patterns=["*config.py", "*.env", "*.yaml", "*.yml"],
            teams_affected=[],
            total_occurrences=0,
            total_cost=0,
            avg_resolution_minutes=0,
            prevention_suggestions=[
                "Validate config at startup",
                "Use typed configuration",
                "Add config diff review to deploy process",
            ],
            first_seen=now,
            last_seen=now,
        )

    def record_incident(
        self,
        org_id: str,
        incident_id: str,
        title: str,
        description: str,
        root_cause: str,
        file_path: str,
        line_number: int,
        author: str,
        team: str,
        cost: float,
        occurred_at: datetime | None = None,
        resolved_at: datetime | None = None,
    ) -> IncidentRecord:
        """Record an incident for pattern analysis."""
        if occurred_at is None:
            occurred_at = datetime.now(timezone.utc)
        if resolved_at is None:
            resolved_at = datetime.now(timezone.utc)

        category = self._categorize_incident(title, description, root_cause)

        record = IncidentRecord(
            incident_id=incident_id,
            org_id=org_id,
            title=title,
            description=description,
            root_cause=root_cause,
            file_path=file_path,
            line_number=line_number,
            author=author,
            team=team,
            occurred_at=occurred_at,
            resolved_at=resolved_at,
            cost=cost,
            category=category,
        )

        if org_id not in self._incidents:
            self._incidents[org_id] = []

        self._incidents[org_id].append(record)

        if category:
            self._update_pattern(org_id, category, record)

        log.info(
            "Recorded incident: incident=%s category=%s",
            incident_id,
            category.value if category else "unknown",
        )

        return record

    def _categorize_incident(
        self,
        title: str,
        description: str,
        root_cause: str,
    ) -> PatternCategory | None:
        """Categorize an incident into a pattern category."""
        combined = f"{title} {description} {root_cause}".lower()

        if any(kw in combined for kw in ["null", "none", "undefined", "nonetype"]):
            return PatternCategory.NULL_CHECK_REMOVAL

        if any(kw in combined for kw in ["connection", "pool", "leak", "exhaust"]):
            return PatternCategory.RESOURCE_LEAK

        if any(kw in combined for kw in ["timeout", "deadline", "slow", "cascade"]):
            return PatternCategory.TIMEOUT_ISSUE

        if any(kw in combined for kw in ["validation", "invalid", "input"]):
            return PatternCategory.VALIDATION_GAP

        if any(kw in combined for kw in ["config", "environment", "setting"]):
            return PatternCategory.CONFIG_ERROR

        if any(kw in combined for kw in ["race", "concurrent", "thread"]):
            return PatternCategory.RACE_CONDITION

        if any(kw in combined for kw in ["deploy", "rollback", "regression"]):
            return PatternCategory.DEPLOY_REGRESSION

        return None

    def _update_pattern(
        self,
        org_id: str,
        category: PatternCategory,
        record: IncidentRecord,
    ) -> None:
        """Update pattern statistics with new incident."""
        for pid, pattern in self._patterns.items():
            if pattern.category == category:
                pattern.total_occurrences += 1
                pattern.total_cost += record.cost

                if record.team and record.team not in pattern.teams_affected:
                    pattern.teams_affected.append(record.team)

                if record.resolved_at:
                    new_resolution = record.duration_minutes
                    if pattern.avg_resolution_minutes > 0:
                        pattern.avg_resolution_minutes = (
                            pattern.avg_resolution_minutes + new_resolution
                        ) / 2
                    else:
                        pattern.avg_resolution_minutes = new_resolution

                pattern.last_seen = record.occurred_at
                break

    def detect_patterns(
        self,
        org_id: str,
        file_path: str,
        code_content: str,
    ) -> list[PatternMatch]:
        """Detect patterns in code that match historical incidents."""
        matches: list[PatternMatch] = []
        incidents = self._incidents.get(org_id, [])

        for pattern in self._patterns.values():
            if pattern.total_occurrences == 0:
                continue

            if not self._file_matches_pattern(file_path, pattern.file_patterns):
                continue

            code_lower = code_content.lower()

            risk_indicators = self._get_risk_indicators(pattern.category)
            matches_found = sum(1 for ind in risk_indicators if ind in code_lower)

            if matches_found >= 2:
                line_number = self._find_first_match_line(code_content, risk_indicators)

                similar = [
                    i.incident_id for i in incidents
                    if i.category == pattern.category
                ][:3]

                confidence = min(0.95, 0.6 + (matches_found * 0.1))

                severity = Severity.MEDIUM
                if pattern.total_cost > 50000:
                    severity = Severity.HIGH
                elif pattern.total_cost > 100000:
                    severity = Severity.CRITICAL

                matches.append(PatternMatch(
                    match_id=f"match_{pattern.pattern_id}_{line_number}",
                    pattern=pattern,
                    file_path=file_path,
                    line_number=line_number,
                    code_snippet=self._extract_snippet(code_content, line_number),
                    confidence=confidence,
                    severity=severity,
                    similar_incidents=similar,
                    recommended_action=pattern.prevention_suggestions[0] if pattern.prevention_suggestions else "Review code",
                    estimated_cost_if_unaddressed=pattern.total_cost / max(pattern.total_occurrences, 1),
                ))

        return matches

    def _file_matches_pattern(
        self,
        file_path: str,
        patterns: list[str],
    ) -> bool:
        """Check if file path matches any of the patterns."""
        import fnmatch

        for pattern in patterns:
            if fnmatch.fnmatch(file_path, pattern):
                return True
            if fnmatch.fnmatch(file_path.split("/")[-1], pattern):
                return True

        return True

    def _get_risk_indicators(
        self,
        category: PatternCategory,
    ) -> list[str]:
        """Get risk indicators for a pattern category."""
        indicators = {
            PatternCategory.NULL_CHECK_REMOVAL: [
                "is none", "is not none", ".get(", "if not",
                "or none", "optional[",
            ],
            PatternCategory.RESOURCE_LEAK: [
                "connection", "pool", "close()", "release(",
                "with open", "__exit__",
            ],
            PatternCategory.TIMEOUT_ISSUE: [
                "timeout", "deadline", "requests.get",
                "aiohttp", "httpx", "grpc",
            ],
            PatternCategory.VALIDATION_GAP: [
                "assert", "validate", "if not", "raise valueerror",
                "check", "verify",
            ],
            PatternCategory.CONFIG_ERROR: [
                "os.environ", "getenv", "config[", "settings.",
                ".yaml", ".json",
            ],
            PatternCategory.RACE_CONDITION: [
                "lock", "mutex", "async", "await", "thread",
                "concurrent", "parallel",
            ],
        }

        return indicators.get(category, [])

    def _find_first_match_line(
        self,
        content: str,
        indicators: list[str],
    ) -> int:
        """Find the first line containing any indicator."""
        for i, line in enumerate(content.split("\n"), 1):
            line_lower = line.lower()
            for indicator in indicators:
                if indicator in line_lower:
                    return i
        return 1

    def _extract_snippet(
        self,
        content: str,
        line_number: int,
        context: int = 3,
    ) -> str:
        """Extract code snippet around a line."""
        lines = content.split("\n")
        start = max(0, line_number - context - 1)
        end = min(len(lines), line_number + context)

        return "\n".join(lines[start:end])

    def get_pattern_summary(
        self,
        org_id: str,
        days: int = 90,
    ) -> dict[str, Any]:
        """Get summary of patterns for an org."""
        incidents = self._incidents.get(org_id, [])
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        recent = [i for i in incidents if i.occurred_at >= cutoff]

        by_category: dict[str, list[IncidentRecord]] = {}
        for incident in recent:
            if incident.category:
                key = incident.category.value
                if key not in by_category:
                    by_category[key] = []
                by_category[key].append(incident)

        top_patterns = []
        for category, category_incidents in sorted(
            by_category.items(),
            key=lambda x: len(x[1]),
            reverse=True,
        )[:5]:
            total_cost = sum(i.cost for i in category_incidents)
            top_patterns.append({
                "category": category,
                "count": len(category_incidents),
                "cost": round(total_cost, 2),
                "teams": list(set(i.team for i in category_incidents if i.team)),
            })

        return {
            "period_days": days,
            "total_incidents": len(recent),
            "categorized": len([i for i in recent if i.category]),
            "top_patterns": top_patterns,
            "total_cost": round(sum(i.cost for i in recent), 2),
            "recommendations": self._generate_recommendations(by_category),
        }

    def _generate_recommendations(
        self,
        by_category: dict[str, list[IncidentRecord]],
    ) -> list[str]:
        """Generate recommendations based on patterns."""
        recommendations = []

        for category, incidents in by_category.items():
            if len(incidents) >= 3:
                pattern = next(
                    (p for p in self._patterns.values() if p.category.value == category),
                    None,
                )
                if pattern and pattern.prevention_suggestions:
                    recommendations.append(
                        f"{pattern.name}: {pattern.prevention_suggestions[0]}"
                    )

        return recommendations[:5]

    def get_pattern(self, pattern_id: str) -> BugPattern | None:
        """Get a pattern by ID."""
        return self._patterns.get(pattern_id)


_detector: HistoricalPatternDetector | None = None


def get_pattern_detector() -> HistoricalPatternDetector:
    """Get the default pattern detector."""
    global _detector
    if _detector is None:
        _detector = HistoricalPatternDetector()
    return _detector
