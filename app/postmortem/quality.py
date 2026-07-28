"""Postmortem quality scoring and validation.

Rates postmortem completeness and suggests improvements.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("devcopilot.postmortem.quality")


@dataclass
class QualityDimension:
    """A dimension of postmortem quality."""

    name: str
    score: float
    max_score: float
    passed: bool
    feedback: str
    suggestions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "score": round(self.score, 1),
            "max_score": self.max_score,
            "passed": self.passed,
            "feedback": self.feedback,
            "suggestions": self.suggestions,
        }


@dataclass
class BlameLanguageIssue:
    """An instance of blame language found in the postmortem."""

    original_text: str
    suggested_rewrite: str
    location: str
    severity: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "original_text": self.original_text,
            "suggested_rewrite": self.suggested_rewrite,
            "location": self.location,
            "severity": self.severity,
        }


@dataclass
class PostmortemQualityScore:
    """Complete quality assessment of a postmortem."""

    total_score: int
    max_score: int
    grade: str
    dimensions: list[QualityDimension]
    blame_language_issues: list[BlameLanguageIssue]
    missing_sections: list[str]
    weak_sections: list[str]
    strengths: list[str]
    improvement_suggestions: list[str]
    compared_to_average: str
    is_publishable: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_score": self.total_score,
            "max_score": self.max_score,
            "grade": self.grade,
            "percentage": round(self.total_score / self.max_score * 100, 1),
            "dimensions": [d.to_dict() for d in self.dimensions],
            "blame_language_issues": [i.to_dict() for i in self.blame_language_issues],
            "missing_sections": self.missing_sections,
            "weak_sections": self.weak_sections,
            "strengths": self.strengths,
            "improvement_suggestions": self.improvement_suggestions,
            "compared_to_average": self.compared_to_average,
            "is_publishable": self.is_publishable,
        }


class PostmortemQualityChecker:
    """Checks and scores postmortem quality."""

    def __init__(self, team_average_score: float = 75.0) -> None:
        self.team_average_score = team_average_score
        self._blame_patterns = self._setup_blame_patterns()

    def _setup_blame_patterns(self) -> list[tuple[str, str, str]]:
        """Set up patterns for detecting blame language."""
        return [
            (
                r"(\w+)'s (commit|change|code|PR|deploy)",
                r"The \2",
                "Avoid attributing changes to individuals",
            ),
            (
                r"(\w+) (failed to|didn't|forgot to|missed)",
                r"The process did not \2",
                "Focus on process gaps, not individual failures",
            ),
            (
                r"(code review|review|reviewer) (failed|missed|didn't catch)",
                r"The issue was not detected during code review",
                "Describe what happened without blame",
            ),
            (
                r"(\w+) (caused|broke|created) (the|this)",
                r"The \3",
                "Focus on what happened, not who caused it",
            ),
            (
                r"(should have|could have|would have) known",
                r"The information was not apparent at the time",
                "Avoid hindsight bias",
            ),
            (
                r"(obvious|clearly|should be clear)",
                r"(remove or rewrite)",
                "Avoid implying incompetence",
            ),
            (
                r"human error",
                r"process gap",
                "Frame as systemic issues, not human failures",
            ),
            (
                r"(careless|sloppy|lazy|stupid)",
                r"(remove entirely)",
                "Remove judgmental language",
            ),
        ]

    def check_quality(self, postmortem: dict[str, Any]) -> PostmortemQualityScore:
        """Check the quality of a postmortem document."""
        dimensions: list[QualityDimension] = []
        missing_sections: list[str] = []
        weak_sections: list[str] = []
        strengths: list[str] = []
        suggestions: list[str] = []

        timeline_score = self._check_timeline(postmortem)
        dimensions.append(timeline_score)
        if timeline_score.passed:
            strengths.append("Timeline complete with timestamps")
        else:
            if not postmortem.get("timeline"):
                missing_sections.append("Timeline")
            else:
                weak_sections.append("Timeline")
            suggestions.extend(timeline_score.suggestions)

        root_cause_score = self._check_root_cause(postmortem)
        dimensions.append(root_cause_score)
        if root_cause_score.passed:
            strengths.append("Root cause clearly identified")
        else:
            if not postmortem.get("root_cause"):
                missing_sections.append("Root cause")
            else:
                weak_sections.append("Root cause")
            suggestions.extend(root_cause_score.suggestions)

        factors_score = self._check_contributing_factors(postmortem)
        dimensions.append(factors_score)
        if factors_score.passed:
            strengths.append("Contributing factors analyzed")
        else:
            suggestions.extend(factors_score.suggestions)

        impact_score = self._check_impact(postmortem)
        dimensions.append(impact_score)
        if impact_score.passed:
            strengths.append("Impact quantified (customers, revenue)")
        else:
            if not postmortem.get("impact") and not postmortem.get("customer_impact"):
                missing_sections.append("Impact assessment")
            else:
                weak_sections.append("Impact")
            suggestions.extend(impact_score.suggestions)

        action_items_score = self._check_action_items(postmortem)
        dimensions.append(action_items_score)
        if action_items_score.passed:
            strengths.append("Action items have owners and due dates")
        else:
            if not postmortem.get("action_items"):
                missing_sections.append("Action items")
            else:
                weak_sections.append("Action items")
            suggestions.extend(action_items_score.suggestions)

        blame_issues = self._check_blame_language(postmortem)
        blame_score = self._score_blame_language(blame_issues)
        dimensions.append(blame_score)
        if blame_score.passed:
            strengths.append("Blame-free language used")
        else:
            suggestions.extend(blame_score.suggestions)

        detection_score = self._check_detection(postmortem)
        dimensions.append(detection_score)

        lessons_score = self._check_lessons_learned(postmortem)
        dimensions.append(lessons_score)

        total_score = sum(d.score for d in dimensions)
        max_score = sum(d.max_score for d in dimensions)

        percentage = (total_score / max_score * 100) if max_score > 0 else 0

        if percentage >= 90:
            grade = "A"
        elif percentage >= 80:
            grade = "B"
        elif percentage >= 70:
            grade = "C"
        elif percentage >= 60:
            grade = "D"
        else:
            grade = "F"

        diff = percentage - self.team_average_score
        if diff > 0:
            compared = f"+{diff:.0f} points above team average"
        elif diff < 0:
            compared = f"{diff:.0f} points below team average"
        else:
            compared = "At team average"

        is_publishable = (
            percentage >= 70
            and len(missing_sections) == 0
            and len(blame_issues) == 0
        )

        return PostmortemQualityScore(
            total_score=int(total_score),
            max_score=int(max_score),
            grade=grade,
            dimensions=dimensions,
            blame_language_issues=blame_issues,
            missing_sections=missing_sections,
            weak_sections=weak_sections,
            strengths=strengths,
            improvement_suggestions=suggestions[:5],
            compared_to_average=compared,
            is_publishable=is_publishable,
        )

    def _check_timeline(self, postmortem: dict[str, Any]) -> QualityDimension:
        """Check timeline quality."""
        timeline = postmortem.get("timeline", [])

        if not timeline:
            return QualityDimension(
                name="Timeline",
                score=0,
                max_score=15,
                passed=False,
                feedback="Timeline is missing",
                suggestions=["Add a timeline with key events and timestamps"],
            )

        has_timestamps = all(e.get("timestamp") for e in timeline)
        has_start = any(e.get("event_type") == "start" or "started" in e.get("title", "").lower() for e in timeline)
        has_detection = any(e.get("event_type") == "detection" or "detected" in e.get("title", "").lower() for e in timeline)
        has_resolution = any(e.get("event_type") == "resolution" or "resolved" in e.get("title", "").lower() for e in timeline)

        score = 0
        if has_timestamps:
            score += 5
        if has_start:
            score += 3
        if has_detection:
            score += 3
        if has_resolution:
            score += 4

        suggestions = []
        if not has_timestamps:
            suggestions.append("Add timestamps to all timeline events")
        if not has_start:
            suggestions.append("Mark when the incident started")
        if not has_detection:
            suggestions.append("Document when the incident was detected")
        if not has_resolution:
            suggestions.append("Document when the incident was resolved")

        return QualityDimension(
            name="Timeline",
            score=score,
            max_score=15,
            passed=score >= 12,
            feedback=f"Timeline has {len(timeline)} events",
            suggestions=suggestions,
        )

    def _check_root_cause(self, postmortem: dict[str, Any]) -> QualityDimension:
        """Check root cause quality."""
        root_cause = postmortem.get("root_cause", "")

        if not root_cause:
            return QualityDimension(
                name="Root Cause",
                score=0,
                max_score=20,
                passed=False,
                feedback="Root cause is missing",
                suggestions=["Identify and document the root cause"],
            )

        score = 0
        suggestions = []

        if len(root_cause) >= 50:
            score += 5
        else:
            suggestions.append("Provide more detail in root cause analysis")

        has_technical_detail = any(
            word in root_cause.lower()
            for word in ["code", "config", "database", "api", "service", "null", "error", "exception"]
        )
        if has_technical_detail:
            score += 5
        else:
            suggestions.append("Include technical details about what went wrong")

        has_why = "because" in root_cause.lower() or "due to" in root_cause.lower()
        if has_why:
            score += 5
        else:
            suggestions.append("Explain WHY the failure occurred, not just WHAT failed")

        has_code_reference = any(char in root_cause for char in [":", "/", ".py", ".ts", ".js"])
        if has_code_reference:
            score += 5
        else:
            suggestions.append("Reference specific files or code locations if applicable")

        return QualityDimension(
            name="Root Cause",
            score=score,
            max_score=20,
            passed=score >= 15,
            feedback="Root cause provided" if root_cause else "Root cause missing",
            suggestions=suggestions,
        )

    def _check_contributing_factors(self, postmortem: dict[str, Any]) -> QualityDimension:
        """Check contributing factors quality."""
        factors = postmortem.get("contributing_factors", [])

        if not factors:
            return QualityDimension(
                name="Contributing Factors",
                score=5,
                max_score=10,
                passed=False,
                feedback="No contributing factors documented",
                suggestions=["Identify factors that contributed to the incident"],
            )

        score = min(10, 5 + len(factors) * 2)

        return QualityDimension(
            name="Contributing Factors",
            score=score,
            max_score=10,
            passed=len(factors) >= 2,
            feedback=f"{len(factors)} contributing factors identified",
            suggestions=[] if len(factors) >= 2 else ["Identify additional contributing factors"],
        )

    def _check_impact(self, postmortem: dict[str, Any]) -> QualityDimension:
        """Check impact assessment quality."""
        impact = postmortem.get("impact", "")
        customer_impact = postmortem.get("customer_impact", "")
        customers_affected = postmortem.get("customers_affected", 0)
        revenue_impact = postmortem.get("revenue_impact", 0)

        if not impact and not customer_impact:
            return QualityDimension(
                name="Impact Assessment",
                score=0,
                max_score=15,
                passed=False,
                feedback="Impact assessment is missing",
                suggestions=["Document the impact of the incident"],
            )

        score = 0
        suggestions = []

        if impact or customer_impact:
            score += 5

        if customers_affected > 0 or any(
            word in (impact + customer_impact).lower()
            for word in ["customer", "user", "request"]
        ):
            score += 5
        else:
            suggestions.append("Quantify how many customers/users were affected")

        if revenue_impact > 0 or "$" in impact or "revenue" in impact.lower():
            score += 5
        else:
            suggestions.append("Estimate revenue or business impact if applicable")

        return QualityDimension(
            name="Impact Assessment",
            score=score,
            max_score=15,
            passed=score >= 10,
            feedback="Impact documented" if score > 0 else "No impact documented",
            suggestions=suggestions,
        )

    def _check_action_items(self, postmortem: dict[str, Any]) -> QualityDimension:
        """Check action items quality."""
        action_items = postmortem.get("action_items", [])

        if not action_items:
            return QualityDimension(
                name="Action Items",
                score=0,
                max_score=15,
                passed=False,
                feedback="No action items defined",
                suggestions=["Add action items to prevent recurrence"],
            )

        score = 5
        suggestions = []

        has_owners = sum(1 for item in action_items if item.get("owner"))
        if has_owners == len(action_items):
            score += 5
        elif has_owners > 0:
            score += 3
            suggestions.append("Assign owners to all action items")
        else:
            suggestions.append("Assign owners to action items")

        has_dates = sum(1 for item in action_items if item.get("due_date"))
        if has_dates == len(action_items):
            score += 5
        elif has_dates > 0:
            score += 3
            suggestions.append("Set due dates for all action items")
        else:
            suggestions.append("Set due dates for action items")

        return QualityDimension(
            name="Action Items",
            score=score,
            max_score=15,
            passed=score >= 12,
            feedback=f"{len(action_items)} action items defined",
            suggestions=suggestions,
        )

    def _check_blame_language(self, postmortem: dict[str, Any]) -> list[BlameLanguageIssue]:
        """Check for blame language in the postmortem."""
        issues: list[BlameLanguageIssue] = []

        text_fields = [
            ("summary", postmortem.get("summary", "")),
            ("root_cause", postmortem.get("root_cause", "")),
            ("impact", postmortem.get("impact", "")),
        ]

        for location, text in text_fields:
            if not text:
                continue

            for pattern, replacement, reason in self._blame_patterns:
                matches = re.finditer(pattern, text, re.IGNORECASE)
                for match in matches:
                    original = match.group(0)
                    if replacement == "(remove entirely)":
                        suggested = "[remove this phrase]"
                        severity = "high"
                    elif replacement == "(remove or rewrite)":
                        suggested = "[consider removing or rewriting]"
                        severity = "medium"
                    else:
                        suggested = re.sub(pattern, replacement, original, flags=re.IGNORECASE)
                        severity = "medium"

                    issues.append(BlameLanguageIssue(
                        original_text=original,
                        suggested_rewrite=suggested,
                        location=location,
                        severity=severity,
                    ))

        return issues

    def _score_blame_language(self, issues: list[BlameLanguageIssue]) -> QualityDimension:
        """Score blame language check."""
        if not issues:
            return QualityDimension(
                name="Blameless Language",
                score=10,
                max_score=10,
                passed=True,
                feedback="No blame language detected",
                suggestions=[],
            )

        high_severity = sum(1 for i in issues if i.severity == "high")
        medium_severity = sum(1 for i in issues if i.severity == "medium")

        deduction = high_severity * 3 + medium_severity * 1
        score = max(0, 10 - deduction)

        suggestions = [f"Rewrite: \"{i.original_text}\" → \"{i.suggested_rewrite}\"" for i in issues[:3]]

        return QualityDimension(
            name="Blameless Language",
            score=score,
            max_score=10,
            passed=score >= 8,
            feedback=f"Found {len(issues)} instances of blame language",
            suggestions=suggestions,
        )

    def _check_detection(self, postmortem: dict[str, Any]) -> QualityDimension:
        """Check detection documentation quality."""
        detection = postmortem.get("detection", "")
        ttd = postmortem.get("time_to_detect_minutes", 0)

        score = 0
        suggestions = []

        if detection:
            score += 3
            if any(word in detection.lower() for word in ["alert", "monitor", "page", "datadog", "prometheus"]):
                score += 2

        if ttd > 0:
            score += 3
        else:
            suggestions.append("Document time to detect the incident")

        return QualityDimension(
            name="Detection",
            score=score,
            max_score=8,
            passed=score >= 5,
            feedback=f"TTD: {ttd} minutes" if ttd > 0 else "Detection time not documented",
            suggestions=suggestions,
        )

    def _check_lessons_learned(self, postmortem: dict[str, Any]) -> QualityDimension:
        """Check lessons learned quality."""
        lessons = postmortem.get("lessons_learned", [])
        went_well = postmortem.get("things_that_went_well", [])
        went_poorly = postmortem.get("things_that_went_poorly", [])

        total_items = len(lessons) + len(went_well) + len(went_poorly)

        if total_items == 0:
            return QualityDimension(
                name="Lessons Learned",
                score=0,
                max_score=7,
                passed=False,
                feedback="No lessons learned documented",
                suggestions=["Document what went well and what could be improved"],
            )

        score = min(7, total_items * 2)

        return QualityDimension(
            name="Lessons Learned",
            score=score,
            max_score=7,
            passed=score >= 4,
            feedback=f"{total_items} lessons/observations documented",
            suggestions=[],
        )


_checker: PostmortemQualityChecker | None = None


def get_quality_checker() -> PostmortemQualityChecker:
    """Get the default quality checker."""
    global _checker
    if _checker is None:
        _checker = PostmortemQualityChecker()
    return _checker
