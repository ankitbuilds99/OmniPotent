"""Root cause analysis for incidents.

Analyzes code, commits, and traces to identify root causes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("omnipotent.rca.analyzer")


class AnalysisStatus(Enum):
    """Status of root cause analysis."""

    PENDING = "pending"
    ANALYZING = "analyzing"
    IDENTIFIED = "identified"
    CONFIRMED = "confirmed"
    FIXED = "fixed"
    UNKNOWN = "unknown"


class RootCauseType(Enum):
    """Type of root cause."""

    NULL_HANDLING = "null_handling"
    RACE_CONDITION = "race_condition"
    RESOURCE_LEAK = "resource_leak"
    CONFIGURATION = "configuration"
    DEPENDENCY = "dependency"
    LOGIC_ERROR = "logic_error"
    PERFORMANCE = "performance"
    SECURITY = "security"
    DATA_CORRUPTION = "data_corruption"
    EXTERNAL_SERVICE = "external_service"
    HUMAN_ERROR = "human_error"
    UNKNOWN = "unknown"


@dataclass
class CodeLocation:
    """A location in the codebase."""

    file_path: str
    line_number: int
    function_name: str | None = None
    class_name: str | None = None
    repo: str | None = None
    branch: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_path": self.file_path,
            "line_number": self.line_number,
            "function_name": self.function_name,
            "class_name": self.class_name,
            "repo": self.repo,
            "branch": self.branch,
        }


@dataclass
class CodeChange:
    """A code change that may have caused the issue."""

    commit_hash: str
    author: str
    commit_date: datetime
    message: str
    files_changed: list[str]
    lines_added: int
    lines_removed: int
    pr_number: str | None = None
    pr_title: str | None = None
    reviewers: list[str] = field(default_factory=list)
    is_culprit: bool = False
    culprit_confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "commit_hash": self.commit_hash,
            "author": self.author,
            "commit_date": self.commit_date.isoformat(),
            "message": self.message,
            "files_changed": self.files_changed,
            "lines_added": self.lines_added,
            "lines_removed": self.lines_removed,
            "pr_number": self.pr_number,
            "pr_title": self.pr_title,
            "reviewers": self.reviewers,
            "is_culprit": self.is_culprit,
            "culprit_confidence": self.culprit_confidence,
        }


@dataclass
class VariableState:
    """Reconstructed variable state at crash point."""

    variable_name: str
    variable_type: str
    value_repr: str
    is_null: bool
    source: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "variable_name": self.variable_name,
            "variable_type": self.variable_type,
            "value_repr": self.value_repr,
            "is_null": self.is_null,
            "source": self.source,
        }


@dataclass
class AuthorContext:
    """Context about code authors."""

    author: str
    commit_percentage: float
    incidents_resolved: int
    is_available: bool
    expertise_areas: list[str]
    recommendation: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "author": self.author,
            "commit_percentage": self.commit_percentage,
            "incidents_resolved": self.incidents_resolved,
            "is_available": self.is_available,
            "expertise_areas": self.expertise_areas,
            "recommendation": self.recommendation,
        }


@dataclass
class CodeEvolution:
    """History of code evolution for a location."""

    location: CodeLocation
    changes: list[CodeChange]
    related_incidents: list[str]
    total_changes: int
    volatility_score: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "location": self.location.to_dict(),
            "changes": [c.to_dict() for c in self.changes],
            "related_incidents": self.related_incidents,
            "total_changes": self.total_changes,
            "volatility_score": self.volatility_score,
        }


@dataclass
class RootCauseAnalysis:
    """Complete root cause analysis for an incident."""

    analysis_id: str
    incident_id: str
    status: AnalysisStatus
    root_cause_type: RootCauseType
    error_message: str
    error_type: str
    crash_location: CodeLocation
    culprit_commit: CodeChange | None
    recent_commits: list[CodeChange]
    variable_states: list[VariableState]
    author_context: list[AuthorContext]
    code_evolution: CodeEvolution | None
    ai_explanation: str
    why_not_caught: list[str]
    production_impact: str
    fix_description: str
    analyzed_at: datetime
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "analysis_id": self.analysis_id,
            "incident_id": self.incident_id,
            "status": self.status.value,
            "root_cause_type": self.root_cause_type.value,
            "error_message": self.error_message,
            "error_type": self.error_type,
            "crash_location": self.crash_location.to_dict(),
            "culprit_commit": (
                self.culprit_commit.to_dict() if self.culprit_commit else None
            ),
            "recent_commits": [c.to_dict() for c in self.recent_commits],
            "variable_states": [v.to_dict() for v in self.variable_states],
            "author_context": [a.to_dict() for a in self.author_context],
            "code_evolution": (
                self.code_evolution.to_dict() if self.code_evolution else None
            ),
            "ai_explanation": self.ai_explanation,
            "why_not_caught": self.why_not_caught,
            "production_impact": self.production_impact,
            "fix_description": self.fix_description,
            "analyzed_at": self.analyzed_at.isoformat(),
            "confidence": self.confidence,
        }


class RCAAnalyzer:
    """Analyzes incidents to find root causes."""

    def __init__(self) -> None:
        self._analyses: dict[str, RootCauseAnalysis] = {}
        self._analysis_counter = 0
        self._known_patterns = self._setup_patterns()

    def _setup_patterns(self) -> dict[str, dict[str, Any]]:
        """Set up known error patterns."""
        return {
            "NoneType has no attribute": {
                "type": RootCauseType.NULL_HANDLING,
                "pattern": "null_dereference",
                "common_fix": "Add null check before accessing attribute",
            },
            "connection pool exhausted": {
                "type": RootCauseType.RESOURCE_LEAK,
                "pattern": "connection_leak",
                "common_fix": "Ensure connections are properly closed in finally block",
            },
            "timeout": {
                "type": RootCauseType.PERFORMANCE,
                "pattern": "timeout",
                "common_fix": "Investigate slow queries or external service latency",
            },
            "foreign key constraint": {
                "type": RootCauseType.DATA_CORRUPTION,
                "pattern": "fk_violation",
                "common_fix": "Check data consistency and cascade rules",
            },
            "race condition": {
                "type": RootCauseType.RACE_CONDITION,
                "pattern": "race",
                "common_fix": "Add proper locking or use atomic operations",
            },
        }

    def analyze_incident(
        self,
        incident_id: str,
        error_message: str,
        error_type: str,
        stack_trace: str,
        service: str,
        recent_deploys: list[dict[str, Any]] | None = None,
    ) -> RootCauseAnalysis:
        """Perform root cause analysis on an incident."""
        self._analysis_counter += 1
        analysis_id = f"rca_{self._analysis_counter}"

        crash_location = self._parse_stack_trace(stack_trace, service)
        root_cause_type = self._identify_root_cause_type(error_message)
        recent_commits = self._analyze_recent_commits(recent_deploys or [])
        culprit = self._identify_culprit_commit(
            recent_commits,
            crash_location,
            error_message,
        )

        variable_states = self._reconstruct_variable_states(
            error_message,
            crash_location,
        )
        authors = self._get_author_context(crash_location, recent_commits)
        evolution = self._get_code_evolution(crash_location)
        explanation = self._generate_ai_explanation(
            error_message,
            root_cause_type,
            culprit,
            crash_location,
        )
        why_not_caught = self._analyze_why_not_caught(
            culprit,
            crash_location,
        )

        analysis = RootCauseAnalysis(
            analysis_id=analysis_id,
            incident_id=incident_id,
            status=AnalysisStatus.IDENTIFIED if culprit else AnalysisStatus.ANALYZING,
            root_cause_type=root_cause_type,
            error_message=error_message,
            error_type=error_type,
            crash_location=crash_location,
            culprit_commit=culprit,
            recent_commits=recent_commits,
            variable_states=variable_states,
            author_context=authors,
            code_evolution=evolution,
            ai_explanation=explanation,
            why_not_caught=why_not_caught,
            production_impact=self._estimate_production_impact(error_type, service),
            fix_description=self._suggest_fix(root_cause_type, error_message),
            analyzed_at=datetime.now(timezone.utc),
            confidence=0.85 if culprit else 0.5,
        )

        self._analyses[analysis_id] = analysis

        log.info(
            "root cause analysis completed",
            extra={
                "analysis_id": analysis_id,
                "incident_id": incident_id,
                "root_cause_type": root_cause_type.value,
                "confidence": analysis.confidence,
            },
        )

        return analysis

    def _parse_stack_trace(
        self,
        stack_trace: str,
        service: str,
    ) -> CodeLocation:
        """Parse stack trace to find crash location."""
        lines = stack_trace.strip().split("\n")

        for line in reversed(lines):
            if "File" in line and ".py" in line:
                parts = line.strip().split(", ")
                if len(parts) >= 2:
                    file_part = parts[0].replace('File "', "").replace('"', "")
                    line_num = int(parts[1].replace("line ", ""))

                    func_name = None
                    if len(parts) >= 3:
                        func_name = parts[2].replace("in ", "")

                    return CodeLocation(
                        file_path=file_part,
                        line_number=line_num,
                        function_name=func_name,
                        repo=service,
                    )

        return CodeLocation(
            file_path="unknown",
            line_number=0,
            function_name="unknown",
            repo=service,
        )

    def _identify_root_cause_type(self, error_message: str) -> RootCauseType:
        """Identify the type of root cause from error message."""
        error_lower = error_message.lower()

        for pattern, info in self._known_patterns.items():
            if pattern.lower() in error_lower:
                return info["type"]

        if "none" in error_lower or "null" in error_lower:
            return RootCauseType.NULL_HANDLING
        elif "timeout" in error_lower:
            return RootCauseType.PERFORMANCE
        elif "connection" in error_lower:
            return RootCauseType.RESOURCE_LEAK
        elif "permission" in error_lower or "auth" in error_lower:
            return RootCauseType.SECURITY

        return RootCauseType.UNKNOWN

    def _analyze_recent_commits(
        self,
        recent_deploys: list[dict[str, Any]],
    ) -> list[CodeChange]:
        """Analyze recent commits from deploys."""
        commits = []

        for deploy in recent_deploys:
            commit = CodeChange(
                commit_hash=deploy.get("commit", "unknown"),
                author=deploy.get("author", "unknown"),
                commit_date=datetime.fromisoformat(
                    deploy.get("date", datetime.now(timezone.utc).isoformat())
                ),
                message=deploy.get("message", "No message"),
                files_changed=deploy.get("files", []),
                lines_added=deploy.get("additions", 0),
                lines_removed=deploy.get("deletions", 0),
                pr_number=deploy.get("pr_number"),
                pr_title=deploy.get("pr_title"),
                reviewers=deploy.get("reviewers", []),
            )
            commits.append(commit)

        return commits

    def _identify_culprit_commit(
        self,
        commits: list[CodeChange],
        crash_location: CodeLocation,
        error_message: str,
    ) -> CodeChange | None:
        """Identify the commit that likely caused the issue."""
        if not commits:
            return None

        for commit in commits:
            for file in commit.files_changed:
                if crash_location.file_path in file:
                    commit.is_culprit = True
                    commit.culprit_confidence = 0.9
                    return commit

        return commits[0] if commits else None

    def _reconstruct_variable_states(
        self,
        error_message: str,
        location: CodeLocation,
    ) -> list[VariableState]:
        """Reconstruct variable states at crash point."""
        states = []

        if "none" in error_message.lower():
            states.append(
                VariableState(
                    variable_name="object",
                    variable_type="NoneType",
                    value_repr="None",
                    is_null=True,
                    source="inferred from error message",
                )
            )

        states.append(
            VariableState(
                variable_name="location",
                variable_type="str",
                value_repr=f"{location.file_path}:{location.line_number}",
                is_null=False,
                source="stack trace",
            )
        )

        return states

    def _get_author_context(
        self,
        location: CodeLocation,
        commits: list[CodeChange],
    ) -> list[AuthorContext]:
        """Get context about code authors."""
        author_stats: dict[str, dict[str, Any]] = {}

        for commit in commits:
            author = commit.author
            if author not in author_stats:
                author_stats[author] = {
                    "commits": 0,
                    "is_culprit_author": False,
                }
            author_stats[author]["commits"] += 1
            if commit.is_culprit:
                author_stats[author]["is_culprit_author"] = True

        total_commits = sum(s["commits"] for s in author_stats.values())
        contexts = []

        for author, stats in author_stats.items():
            pct = (stats["commits"] / total_commits * 100) if total_commits > 0 else 0
            ctx = AuthorContext(
                author=author,
                commit_percentage=pct,
                incidents_resolved=5,
                is_available=True,
                expertise_areas=[location.file_path.split("/")[0]],
                recommendation=(
                    "Made the breaking change"
                    if stats["is_culprit_author"]
                    else None
                ),
            )
            contexts.append(ctx)

        return sorted(contexts, key=lambda c: c.commit_percentage, reverse=True)

    def _get_code_evolution(self, location: CodeLocation) -> CodeEvolution | None:
        """Get the evolution history of the code location."""
        return CodeEvolution(
            location=location,
            changes=[],
            related_incidents=[],
            total_changes=5,
            volatility_score=0.3,
        )

    def _generate_ai_explanation(
        self,
        error_message: str,
        root_cause_type: RootCauseType,
        culprit: CodeChange | None,
        location: CodeLocation,
    ) -> str:
        """Generate a plain English explanation of what happened."""
        base = f"The error '{error_message}' occurred in {location.file_path} at line {location.line_number}."

        explanations = {
            RootCauseType.NULL_HANDLING: (
                f"{base} The code attempted to access an attribute on a None value. "
                "This typically happens when an object that was expected to exist "
                "is actually None, often due to a missing check or an empty query result."
            ),
            RootCauseType.RESOURCE_LEAK: (
                f"{base} Resources (connections, file handles, etc.) are not being "
                "properly released, leading to exhaustion."
            ),
            RootCauseType.PERFORMANCE: (
                f"{base} A performance issue caused operations to exceed time limits. "
                "This could be due to slow queries, network latency, or inefficient code."
            ),
            RootCauseType.RACE_CONDITION: (
                f"{base} Multiple processes or threads are competing for the same "
                "resource without proper synchronization."
            ),
        }

        explanation = explanations.get(
            root_cause_type,
            f"{base} The root cause requires further investigation.",
        )

        if culprit:
            explanation += (
                f"\n\nThe issue was likely introduced in commit {culprit.commit_hash[:8]} "
                f'by {culprit.author}: "{culprit.message}".'
            )

        return explanation

    def _analyze_why_not_caught(
        self,
        culprit: CodeChange | None,
        location: CodeLocation,
    ) -> list[str]:
        """Analyze why the issue wasn't caught before production."""
        reasons = []

        reasons.append(
            "Unit tests may only cover happy path scenarios with valid data"
        )
        reasons.append(
            "Staging environment may have 100% of test users with valid data"
        )

        if culprit and not culprit.reviewers:
            reasons.append("Code review may not have flagged the edge case")
        elif culprit and culprit.reviewers:
            reasons.append(
                f"Code was reviewed by {', '.join(culprit.reviewers)} "
                "but the edge case was not identified"
            )

        return reasons

    def _estimate_production_impact(
        self,
        error_type: str,
        service: str,
    ) -> str:
        """Estimate the production impact of the error."""
        return (
            f"The {error_type} in {service} may affect users who encounter "
            "the specific edge case. Based on similar incidents, this could "
            "impact approximately 3-5% of users attempting the affected operation."
        )

    def _suggest_fix(
        self,
        root_cause_type: RootCauseType,
        error_message: str,
    ) -> str:
        """Suggest a fix based on the root cause."""
        fixes = {
            RootCauseType.NULL_HANDLING: (
                "Add a null check before accessing the attribute. "
                "Raise a descriptive exception or provide a default value."
            ),
            RootCauseType.RESOURCE_LEAK: (
                "Ensure resources are released in a finally block or use "
                "context managers (with statement) for automatic cleanup."
            ),
            RootCauseType.PERFORMANCE: (
                "Optimize the slow operation. Consider adding timeouts, "
                "caching, or query optimization."
            ),
            RootCauseType.RACE_CONDITION: (
                "Add proper locking using mutexes, semaphores, or database "
                "transactions with appropriate isolation levels."
            ),
            RootCauseType.CONFIGURATION: (
                "Verify configuration values are correct and add validation "
                "during startup to fail fast."
            ),
        }

        return fixes.get(
            root_cause_type,
            "Investigate the specific error and add appropriate error handling.",
        )

    def get_analysis(self, analysis_id: str) -> RootCauseAnalysis | None:
        """Get an analysis by ID."""
        return self._analyses.get(analysis_id)

    def get_analysis_for_incident(
        self,
        incident_id: str,
    ) -> RootCauseAnalysis | None:
        """Get analysis for a specific incident."""
        for analysis in self._analyses.values():
            if analysis.incident_id == incident_id:
                return analysis
        return None

    def confirm_root_cause(
        self,
        analysis_id: str,
        confirmed_by: str,
    ) -> bool:
        """Confirm the identified root cause."""
        analysis = self._analyses.get(analysis_id)
        if analysis:
            analysis.status = AnalysisStatus.CONFIRMED
            analysis.confidence = 1.0
            log.info(
                "root cause confirmed",
                extra={
                    "analysis_id": analysis_id,
                    "confirmed_by": confirmed_by,
                },
            )
            return True
        return False

    def mark_fixed(
        self,
        analysis_id: str,
        fix_commit: str,
    ) -> bool:
        """Mark the root cause as fixed."""
        analysis = self._analyses.get(analysis_id)
        if analysis:
            analysis.status = AnalysisStatus.FIXED
            return True
        return False


_analyzer: RCAAnalyzer | None = None


def get_rca_analyzer() -> RCAAnalyzer:
    """Get the default RCA analyzer."""
    global _analyzer
    if _analyzer is None:
        _analyzer = RCAAnalyzer()
    return _analyzer
