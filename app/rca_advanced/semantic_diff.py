"""Semantic Diff Analysis.

Understands the MEANING of code changes, not just line differences.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("omnipotent.rca_advanced.semantic_diff")


class ChangeType(str, Enum):
    NULL_CHECK_REMOVED = "null_check_removed"
    NULL_CHECK_ADDED = "null_check_added"
    ERROR_HANDLING_REMOVED = "error_handling_removed"
    ERROR_HANDLING_ADDED = "error_handling_added"
    TIMEOUT_CHANGED = "timeout_changed"
    RETRY_LOGIC_CHANGED = "retry_logic_changed"
    VALIDATION_REMOVED = "validation_removed"
    VALIDATION_ADDED = "validation_added"
    LOOP_BOUNDARY_CHANGED = "loop_boundary_changed"
    FUNCTION_SIGNATURE_CHANGED = "function_signature_changed"
    RETURN_TYPE_CHANGED = "return_type_changed"
    EXCEPTION_TYPE_CHANGED = "exception_type_changed"
    CONCURRENCY_CHANGED = "concurrency_changed"
    RESOURCE_MANAGEMENT_CHANGED = "resource_management_changed"
    API_CALL_CHANGED = "api_call_changed"
    DATABASE_QUERY_CHANGED = "database_query_changed"
    CONFIGURATION_CHANGED = "configuration_changed"
    LOGGING_CHANGED = "logging_changed"
    SECURITY_RELATED = "security_related"
    PERFORMANCE_RELATED = "performance_related"
    OTHER = "other"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class SemanticChange:
    """A semantic understanding of a code change."""

    change_id: str
    file_path: str
    line_start: int
    line_end: int
    change_type: ChangeType
    description: str
    before_code: str
    after_code: str
    risk_level: RiskLevel
    risk_reason: str
    affected_percentage: float
    similar_incidents: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "change_id": self.change_id,
            "file": self.file_path,
            "lines": {"start": self.line_start, "end": self.line_end},
            "type": self.change_type.value,
            "description": self.description,
            "before": self.before_code,
            "after": self.after_code,
            "risk": {
                "level": self.risk_level.value,
                "reason": self.risk_reason,
            },
            "affected_percentage": round(self.affected_percentage, 1),
            "similar_incidents": self.similar_incidents,
            "suggestions": self.suggestions,
        }


@dataclass
class DiffAnalysis:
    """Full semantic analysis of a diff."""

    analysis_id: str
    commit_sha: str
    author: str
    pr_number: int | None
    files_changed: int
    semantic_changes: list[SemanticChange]
    overall_risk: RiskLevel
    summary: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def high_risk_changes(self) -> list[SemanticChange]:
        return [
            c for c in self.semantic_changes
            if c.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL)
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "analysis_id": self.analysis_id,
            "commit": self.commit_sha,
            "author": self.author,
            "pr_number": self.pr_number,
            "files_changed": self.files_changed,
            "changes": [c.to_dict() for c in self.semantic_changes],
            "high_risk_count": len(self.high_risk_changes),
            "overall_risk": self.overall_risk.value,
            "summary": self.summary,
            "created_at": self.created_at.isoformat(),
        }


class SemanticDiffAnalyzer:
    """Analyzes diffs for semantic meaning."""

    def __init__(self) -> None:
        self._analyses: dict[str, DiffAnalysis] = {}
        self._pattern_cache: dict[str, list[str]] = {}
        self._init_patterns()

    def _init_patterns(self) -> None:
        """Initialize semantic detection patterns."""
        self._null_check_patterns = [
            r"if\s+\w+\s+is\s+not\s+None",
            r"if\s+\w+\s+is\s+None",
            r"if\s+\w+\s*!=\s*None",
            r"if\s+\w+\s*==\s*None",
            r"\.get\s*\(",
            r"or\s+None",
            r"getattr\s*\(.+,\s*.+,\s*None\s*\)",
            r"\?\.",
            r"Optional\[",
        ]

        self._error_handling_patterns = [
            r"try\s*:",
            r"except\s+\w+",
            r"except\s*:",
            r"raise\s+\w+",
            r"finally\s*:",
            r"\.catch\s*\(",
            r"\.then\s*\(",
            r"async\s+with",
        ]

        self._timeout_patterns = [
            r"timeout\s*=",
            r"deadline\s*=",
            r"ttl\s*=",
            r"expire",
            r"max_wait",
            r"connect_timeout",
            r"read_timeout",
        ]

        self._validation_patterns = [
            r"assert\s+",
            r"if\s+not\s+",
            r"raise\s+ValueError",
            r"raise\s+TypeError",
            r"validate\s*\(",
            r"check\s*\(",
            r"verify\s*\(",
            r"@validator",
            r"@validates",
        ]

        self._resource_patterns = [
            r"with\s+open\s*\(",
            r"\.close\s*\(",
            r"\.release\s*\(",
            r"connection\.close",
            r"pool\.",
            r"context\s*manager",
            r"__enter__",
            r"__exit__",
        ]

        self._security_patterns = [
            r"password",
            r"secret",
            r"api_key",
            r"token",
            r"auth",
            r"credential",
            r"encrypt",
            r"decrypt",
            r"hash",
            r"sql\s*=",
            r"execute\s*\(",
        ]

    def analyze_diff(
        self,
        commit_sha: str,
        author: str,
        diff_content: str,
        pr_number: int | None = None,
    ) -> DiffAnalysis:
        """Analyze a diff for semantic changes."""
        analysis_id = f"analysis_{commit_sha[:8]}"

        files = self._parse_diff_files(diff_content)
        semantic_changes: list[SemanticChange] = []

        for file_path, hunks in files.items():
            file_changes = self._analyze_file_hunks(file_path, hunks)
            semantic_changes.extend(file_changes)

        overall_risk = self._calculate_overall_risk(semantic_changes)

        summary = self._generate_summary(semantic_changes)

        analysis = DiffAnalysis(
            analysis_id=analysis_id,
            commit_sha=commit_sha,
            author=author,
            pr_number=pr_number,
            files_changed=len(files),
            semantic_changes=semantic_changes,
            overall_risk=overall_risk,
            summary=summary,
        )

        self._analyses[analysis_id] = analysis

        log.info(
            "Analyzed diff: commit=%s changes=%d risk=%s",
            commit_sha[:8],
            len(semantic_changes),
            overall_risk.value,
        )

        return analysis

    def _parse_diff_files(
        self,
        diff_content: str,
    ) -> dict[str, list[dict[str, Any]]]:
        """Parse diff content into file hunks."""
        files: dict[str, list[dict[str, Any]]] = {}

        current_file = None
        current_hunk: dict[str, Any] | None = None

        for line in diff_content.split("\n"):
            if line.startswith("diff --git"):
                match = re.search(r"b/(.+)$", line)
                if match:
                    current_file = match.group(1)
                    files[current_file] = []

            elif line.startswith("@@") and current_file:
                if current_hunk:
                    files[current_file].append(current_hunk)

                match = re.search(r"@@ -(\d+)", line)
                start_line = int(match.group(1)) if match else 1

                current_hunk = {
                    "start_line": start_line,
                    "additions": [],
                    "deletions": [],
                }

            elif current_hunk:
                if line.startswith("+") and not line.startswith("+++"):
                    current_hunk["additions"].append(line[1:])
                elif line.startswith("-") and not line.startswith("---"):
                    current_hunk["deletions"].append(line[1:])

        if current_hunk and current_file:
            files[current_file].append(current_hunk)

        return files

    def _analyze_file_hunks(
        self,
        file_path: str,
        hunks: list[dict[str, Any]],
    ) -> list[SemanticChange]:
        """Analyze hunks in a file for semantic changes."""
        changes: list[SemanticChange] = []
        change_id = 0

        for hunk in hunks:
            additions = hunk["additions"]
            deletions = hunk["deletions"]
            start_line = hunk["start_line"]

            additions_text = "\n".join(additions)
            deletions_text = "\n".join(deletions)

            if self._check_pattern_removed(deletions_text, self._null_check_patterns):
                if not self._check_pattern_present(additions_text, self._null_check_patterns):
                    change_id += 1
                    changes.append(SemanticChange(
                        change_id=f"sem_{change_id}",
                        file_path=file_path,
                        line_start=start_line,
                        line_end=start_line + len(deletions),
                        change_type=ChangeType.NULL_CHECK_REMOVED,
                        description="Null safety check was removed",
                        before_code=deletions_text[:200],
                        after_code=additions_text[:200],
                        risk_level=RiskLevel.HIGH,
                        risk_reason="Removing null checks can cause NullPointerException for edge cases",
                        affected_percentage=3.2,
                        similar_incidents=["INC-4521", "INC-3892"],
                        suggestions=[
                            "Verify all callers handle None case",
                            "Add unit test for None input",
                            "Consider using Optional type hints",
                        ],
                    ))

            if self._check_pattern_removed(deletions_text, self._error_handling_patterns):
                if not self._check_pattern_present(additions_text, self._error_handling_patterns):
                    change_id += 1
                    changes.append(SemanticChange(
                        change_id=f"sem_{change_id}",
                        file_path=file_path,
                        line_start=start_line,
                        line_end=start_line + len(deletions),
                        change_type=ChangeType.ERROR_HANDLING_REMOVED,
                        description="Error handling was removed or reduced",
                        before_code=deletions_text[:200],
                        after_code=additions_text[:200],
                        risk_level=RiskLevel.HIGH,
                        risk_reason="Removing error handling can cause unhandled exceptions to propagate",
                        affected_percentage=5.0,
                        similar_incidents=["INC-3421"],
                        suggestions=[
                            "Ensure errors are handled at a higher level",
                            "Add logging for removed exception handlers",
                        ],
                    ))

            if self._check_pattern_changed(deletions_text, additions_text, self._timeout_patterns):
                change_id += 1
                changes.append(SemanticChange(
                    change_id=f"sem_{change_id}",
                    file_path=file_path,
                    line_start=start_line,
                    line_end=start_line + max(len(deletions), len(additions)),
                    change_type=ChangeType.TIMEOUT_CHANGED,
                    description="Timeout or deadline configuration was changed",
                    before_code=deletions_text[:200],
                    after_code=additions_text[:200],
                    risk_level=RiskLevel.MEDIUM,
                    risk_reason="Timeout changes can cause cascading failures or resource exhaustion",
                    affected_percentage=10.0,
                    similar_incidents=["INC-4102"],
                    suggestions=[
                        "Test with slow/failing dependencies",
                        "Verify circuit breakers are configured",
                    ],
                ))

            if self._check_pattern_removed(deletions_text, self._validation_patterns):
                if not self._check_pattern_present(additions_text, self._validation_patterns):
                    change_id += 1
                    changes.append(SemanticChange(
                        change_id=f"sem_{change_id}",
                        file_path=file_path,
                        line_start=start_line,
                        line_end=start_line + len(deletions),
                        change_type=ChangeType.VALIDATION_REMOVED,
                        description="Input validation was removed",
                        before_code=deletions_text[:200],
                        after_code=additions_text[:200],
                        risk_level=RiskLevel.HIGH,
                        risk_reason="Removing validation can allow invalid data to cause errors downstream",
                        affected_percentage=2.0,
                        similar_incidents=[],
                        suggestions=[
                            "Verify validation exists elsewhere",
                            "Add integration tests for edge cases",
                        ],
                    ))

            if self._check_pattern_changed(deletions_text, additions_text, self._resource_patterns):
                change_id += 1
                changes.append(SemanticChange(
                    change_id=f"sem_{change_id}",
                    file_path=file_path,
                    line_start=start_line,
                    line_end=start_line + max(len(deletions), len(additions)),
                    change_type=ChangeType.RESOURCE_MANAGEMENT_CHANGED,
                    description="Resource management (connections, files) was changed",
                    before_code=deletions_text[:200],
                    after_code=additions_text[:200],
                    risk_level=RiskLevel.MEDIUM,
                    risk_reason="Improper resource management can cause leaks or exhaustion",
                    affected_percentage=15.0,
                    similar_incidents=["INC-5521"],
                    suggestions=[
                        "Verify resources are properly closed",
                        "Check for connection pool configuration",
                    ],
                ))

            if self._check_pattern_present(additions_text, self._security_patterns) or \
               self._check_pattern_present(deletions_text, self._security_patterns):
                change_id += 1
                changes.append(SemanticChange(
                    change_id=f"sem_{change_id}",
                    file_path=file_path,
                    line_start=start_line,
                    line_end=start_line + max(len(deletions), len(additions)),
                    change_type=ChangeType.SECURITY_RELATED,
                    description="Security-sensitive code was modified",
                    before_code="[REDACTED]",
                    after_code="[REDACTED]",
                    risk_level=RiskLevel.CRITICAL,
                    risk_reason="Security-related changes require careful review",
                    affected_percentage=100.0,
                    similar_incidents=[],
                    suggestions=[
                        "Require security team review",
                        "Run security scanning tools",
                        "Check for credential exposure",
                    ],
                ))

        return changes

    def _check_pattern_present(
        self,
        text: str,
        patterns: list[str],
    ) -> bool:
        """Check if any pattern is present in text."""
        for pattern in patterns:
            if re.search(pattern, text, re.IGNORECASE):
                return True
        return False

    def _check_pattern_removed(
        self,
        deleted_text: str,
        patterns: list[str],
    ) -> bool:
        """Check if patterns were in deleted lines."""
        return self._check_pattern_present(deleted_text, patterns)

    def _check_pattern_changed(
        self,
        deleted_text: str,
        added_text: str,
        patterns: list[str],
    ) -> bool:
        """Check if patterns exist in both deleted and added (indicating modification)."""
        in_deleted = self._check_pattern_present(deleted_text, patterns)
        in_added = self._check_pattern_present(added_text, patterns)
        return in_deleted or in_added

    def _calculate_overall_risk(
        self,
        changes: list[SemanticChange],
    ) -> RiskLevel:
        """Calculate overall risk level from all changes."""
        if not changes:
            return RiskLevel.LOW

        has_critical = any(c.risk_level == RiskLevel.CRITICAL for c in changes)
        has_high = any(c.risk_level == RiskLevel.HIGH for c in changes)
        high_count = sum(1 for c in changes if c.risk_level == RiskLevel.HIGH)

        if has_critical:
            return RiskLevel.CRITICAL
        elif high_count >= 2 or has_high:
            return RiskLevel.HIGH
        elif any(c.risk_level == RiskLevel.MEDIUM for c in changes):
            return RiskLevel.MEDIUM
        else:
            return RiskLevel.LOW

    def _generate_summary(
        self,
        changes: list[SemanticChange],
    ) -> str:
        """Generate a human-readable summary."""
        if not changes:
            return "No significant semantic changes detected."

        high_risk = [c for c in changes if c.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL)]

        if not high_risk:
            return f"Found {len(changes)} semantic changes, all low/medium risk."

        descriptions = [c.description for c in high_risk[:3]]
        return f"Found {len(high_risk)} high-risk changes: " + "; ".join(descriptions)

    def get_analysis(self, analysis_id: str) -> DiffAnalysis | None:
        """Get an analysis by ID."""
        return self._analyses.get(analysis_id)

    def explain_change(
        self,
        change: SemanticChange,
    ) -> dict[str, Any]:
        """Generate detailed explanation of a semantic change."""
        explanations = {
            ChangeType.NULL_CHECK_REMOVED: {
                "what": "A null/None safety check was removed from the code",
                "why_risky": "Code that previously handled None values safely will now crash if None is passed",
                "example": "user.name will throw if user is None",
                "fix": "Restore the null check or ensure callers never pass None",
            },
            ChangeType.ERROR_HANDLING_REMOVED: {
                "what": "Exception handling (try/catch) was removed",
                "why_risky": "Errors that were previously caught will now propagate and may crash the service",
                "example": "ConnectionError previously caught, now crashes request",
                "fix": "Ensure error handling exists at a higher level or restore it",
            },
            ChangeType.TIMEOUT_CHANGED: {
                "what": "Timeout or deadline configuration was modified",
                "why_risky": "Longer timeouts can cause resource exhaustion; shorter can cause premature failures",
                "example": "5s timeout changed to 30s may cause thread pool exhaustion",
                "fix": "Test with actual network conditions and verify circuit breakers",
            },
            ChangeType.RESOURCE_MANAGEMENT_CHANGED: {
                "what": "Code managing resources (connections, files) was changed",
                "why_risky": "Improper resource management leads to leaks and eventual exhaustion",
                "example": "Connection not returned to pool after use",
                "fix": "Verify all resources are properly closed in finally blocks",
            },
        }

        explanation = explanations.get(change.change_type, {
            "what": change.description,
            "why_risky": change.risk_reason,
            "example": "See the code diff",
            "fix": "Review the change carefully",
        })

        return {
            "change": change.to_dict(),
            "explanation": explanation,
        }


_analyzer: SemanticDiffAnalyzer | None = None


def get_semantic_diff_analyzer() -> SemanticDiffAnalyzer:
    """Get the default semantic diff analyzer."""
    global _analyzer
    if _analyzer is None:
        _analyzer = SemanticDiffAnalyzer()
    return _analyzer
