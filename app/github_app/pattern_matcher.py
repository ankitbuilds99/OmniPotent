"""Incident Pattern Matcher.

Matches PR changes against historical incident patterns.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger("devcopilot.github_app.pattern_matcher")


@dataclass
class IncidentPattern:
    """A pattern learned from past incidents."""

    pattern_id: str
    name: str
    description: str
    file_patterns: list[str]
    code_patterns: list[str]
    incident_ids: list[str]
    severity_distribution: dict[str, int]
    total_incidents: int
    avg_resolution_minutes: float
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.pattern_id,
            "name": self.name,
            "description": self.description,
            "file_patterns": self.file_patterns,
            "code_patterns": self.code_patterns,
            "total_incidents": self.total_incidents,
            "severity_distribution": self.severity_distribution,
            "avg_resolution_minutes": self.avg_resolution_minutes,
        }


@dataclass
class PatternMatch:
    """A match between a PR and an incident pattern."""

    pattern: IncidentPattern
    similarity_percent: float
    matched_files: list[str]
    matched_code: list[str]
    related_incidents: list[dict[str, Any]]
    risk_message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "pattern": self.pattern.to_dict(),
            "similarity": round(self.similarity_percent, 1),
            "matched_files": self.matched_files,
            "matched_code": self.matched_code,
            "related_incidents": self.related_incidents,
            "risk_message": self.risk_message,
        }


class PatternMatcher:
    """Matches PR changes against incident patterns."""

    def __init__(self) -> None:
        self._patterns: dict[str, IncidentPattern] = {}
        self._init_default_patterns()

    def _init_default_patterns(self) -> None:
        """Initialize default patterns from common incident types."""
        self._patterns["null_check_removal"] = IncidentPattern(
            pattern_id="null_check_removal",
            name="Null Check Removal",
            description="Removing null/None checks leads to AttributeError",
            file_patterns=[r".*\.py$", r".*\.js$", r".*\.ts$"],
            code_patterns=[
                r"-.*if\s+\w+\s+is\s+None",
                r"-.*if\s+\w+\s*==\s*None",
                r"-.*\?\.",
                r"-.*\?\?",
            ],
            incident_ids=["INC-4521", "INC-4398", "INC-4102"],
            severity_distribution={"SEV1": 1, "SEV2": 2, "SEV3": 0},
            total_incidents=3,
            avg_resolution_minutes=45,
        )

        self._patterns["error_handling_removal"] = IncidentPattern(
            pattern_id="error_handling_removal",
            name="Error Handling Removal",
            description="Removing try/catch leads to unhandled exceptions",
            file_patterns=[r".*\.py$", r".*\.js$", r".*\.ts$", r".*\.java$"],
            code_patterns=[
                r"-.*try:",
                r"-.*except\s+\w+:",
                r"-.*catch\s*\(",
                r"-.*finally:",
            ],
            incident_ids=["INC-3892", "INC-3756"],
            severity_distribution={"SEV1": 0, "SEV2": 2, "SEV3": 0},
            total_incidents=2,
            avg_resolution_minutes=35,
        )

        self._patterns["timeout_change"] = IncidentPattern(
            pattern_id="timeout_change",
            name="Timeout Configuration Change",
            description="Changing timeouts can cause cascading failures",
            file_patterns=[r".*config.*", r".*\.yaml$", r".*\.yml$", r".*\.json$"],
            code_patterns=[
                r"timeout",
                r"deadline",
                r"max_wait",
                r"connection_timeout",
            ],
            incident_ids=["INC-4012", "INC-3901", "INC-3855"],
            severity_distribution={"SEV1": 2, "SEV2": 1, "SEV3": 0},
            total_incidents=3,
            avg_resolution_minutes=90,
        )

        self._patterns["retry_logic_change"] = IncidentPattern(
            pattern_id="retry_logic_change",
            name="Retry Logic Change",
            description="Changing retry logic can cause thundering herd",
            file_patterns=[r".*client.*", r".*service.*", r".*api.*"],
            code_patterns=[
                r"retry",
                r"backoff",
                r"max_retries",
                r"exponential",
            ],
            incident_ids=["INC-3712", "INC-3501"],
            severity_distribution={"SEV1": 1, "SEV2": 1, "SEV3": 0},
            total_incidents=2,
            avg_resolution_minutes=60,
        )

        self._patterns["database_query_change"] = IncidentPattern(
            pattern_id="database_query_change",
            name="Database Query Change",
            description="Query changes can cause performance degradation",
            file_patterns=[r".*\.sql$", r".*repository.*", r".*dao.*", r".*model.*"],
            code_patterns=[
                r"SELECT.*FROM",
                r"UPDATE.*SET",
                r"DELETE.*FROM",
                r"JOIN",
                r"WHERE",
            ],
            incident_ids=["INC-4201", "INC-4055", "INC-3998", "INC-3876"],
            severity_distribution={"SEV1": 1, "SEV2": 2, "SEV3": 1},
            total_incidents=4,
            avg_resolution_minutes=75,
        )

    def register_pattern(self, pattern: IncidentPattern) -> None:
        """Register a new incident pattern."""
        self._patterns[pattern.pattern_id] = pattern
        log.info("Registered pattern: %s", pattern.pattern_id)

    def learn_from_incident(
        self,
        incident_id: str,
        files_changed: list[str],
        diff_content: str,
        severity: str,
        resolution_minutes: float,
    ) -> IncidentPattern | None:
        """Learn a new pattern from an incident."""
        for pattern in self._patterns.values():
            file_match = any(
                re.search(fp, f)
                for fp in pattern.file_patterns
                for f in files_changed
            )
            code_match = any(
                re.search(cp, diff_content, re.IGNORECASE)
                for cp in pattern.code_patterns
            )

            if file_match and code_match:
                if incident_id not in pattern.incident_ids:
                    pattern.incident_ids.append(incident_id)
                    pattern.total_incidents += 1
                    pattern.severity_distribution[severity] = (
                        pattern.severity_distribution.get(severity, 0) + 1
                    )

                    total_time = pattern.avg_resolution_minutes * (pattern.total_incidents - 1)
                    pattern.avg_resolution_minutes = (
                        (total_time + resolution_minutes) / pattern.total_incidents
                    )

                log.info("Updated pattern %s with incident %s", pattern.pattern_id, incident_id)
                return pattern

        return None

    def match_pr(
        self,
        files: list[str],
        diff_content: str,
    ) -> list[PatternMatch]:
        """Match a PR against known patterns."""
        matches = []

        for pattern in self._patterns.values():
            matched_files = []
            matched_code = []
            file_score = 0.0
            code_score = 0.0

            for f in files:
                for fp in pattern.file_patterns:
                    if re.search(fp, f, re.IGNORECASE):
                        matched_files.append(f)
                        file_score += 1
                        break

            for cp in pattern.code_patterns:
                match = re.search(cp, diff_content, re.IGNORECASE | re.MULTILINE)
                if match:
                    matched_code.append(match.group(0)[:100])
                    code_score += 1

            if matched_files and matched_code:
                max_file_score = len(pattern.file_patterns)
                max_code_score = len(pattern.code_patterns)

                file_pct = (file_score / max_file_score) * 100 if max_file_score > 0 else 0
                code_pct = (code_score / max_code_score) * 100 if max_code_score > 0 else 0

                similarity = (file_pct * 0.3 + code_pct * 0.7)

                if similarity >= 40:
                    related = [
                        {"incident_id": iid, "similarity": similarity}
                        for iid in pattern.incident_ids[:3]
                    ]

                    sev1_count = pattern.severity_distribution.get("SEV1", 0)
                    sev2_count = pattern.severity_distribution.get("SEV2", 0)
                    total = pattern.total_incidents

                    if total > 0:
                        incident_rate = ((sev1_count + sev2_count) / total) * 100
                        risk_msg = (
                            f"PRs matching this pattern have a {incident_rate:.0f}% "
                            f"incident rate ({total} historical incidents)"
                        )
                    else:
                        risk_msg = "Pattern matches historical incident signature"

                    matches.append(PatternMatch(
                        pattern=pattern,
                        similarity_percent=similarity,
                        matched_files=matched_files,
                        matched_code=matched_code,
                        related_incidents=related,
                        risk_message=risk_msg,
                    ))

        matches.sort(key=lambda m: m.similarity_percent, reverse=True)
        return matches

    def get_pattern(self, pattern_id: str) -> IncidentPattern | None:
        """Get a pattern by ID."""
        return self._patterns.get(pattern_id)

    def get_all_patterns(self) -> list[IncidentPattern]:
        """Get all patterns."""
        return list(self._patterns.values())

    def get_pattern_stats(self) -> dict[str, Any]:
        """Get statistics about patterns."""
        total_patterns = len(self._patterns)
        total_incidents = sum(p.total_incidents for p in self._patterns.values())

        by_severity: dict[str, int] = {}
        for pattern in self._patterns.values():
            for sev, count in pattern.severity_distribution.items():
                by_severity[sev] = by_severity.get(sev, 0) + count

        top_patterns: list[dict[str, str | int]] = [
            {"id": p.pattern_id, "incidents": p.total_incidents}
            for p in self._patterns.values()
        ]
        top_patterns.sort(key=lambda x: x["incidents"], reverse=True)  # type: ignore[arg-type]

        return {
            "total_patterns": total_patterns,
            "total_incidents_tracked": total_incidents,
            "incidents_by_severity": by_severity,
            "top_patterns": top_patterns[:5],
        }


_matcher: PatternMatcher | None = None


def get_pattern_matcher() -> PatternMatcher:
    """Get the default pattern matcher."""
    global _matcher
    if _matcher is None:
        _matcher = PatternMatcher()
    return _matcher
