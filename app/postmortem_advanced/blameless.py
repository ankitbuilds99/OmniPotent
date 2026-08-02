"""Blameless language enforcement and rewriting."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

log = logging.getLogger("omnipotent.postmortem_advanced.blameless")


@dataclass
class BlamelessViolation:
    """A detected blameless language violation."""

    violation_id: str
    original_text: str
    location: str
    violation_type: str
    explanation: str
    severity: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.violation_id,
            "original": self.original_text,
            "location": self.location,
            "type": self.violation_type,
            "explanation": self.explanation,
            "severity": self.severity,
        }


@dataclass
class BlamelessRewrite:
    """A suggested rewrite for blameless language."""

    violation: BlamelessViolation
    suggested_text: str
    reasoning: str
    principle: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "violation": self.violation.to_dict(),
            "suggested": self.suggested_text,
            "reasoning": self.reasoning,
            "principle": self.principle,
        }


@dataclass
class BlamelessScore:
    """Overall blameless score for a postmortem."""

    score: float
    violations_found: int
    violations_fixed: int
    by_category: dict[str, int]
    recommendations: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "score_percent": round(self.score * 100, 1),
            "violations_found": self.violations_found,
            "violations_fixed": self.violations_fixed,
            "by_category": self.by_category,
            "recommendations": self.recommendations,
        }


class BlamelessChecker:
    """Checks and rewrites text for blameless language."""

    def __init__(self) -> None:
        self._violation_counter = 0

        self._blame_patterns: list[dict[str, Any]] = [
            {
                "pattern": r"@?\w+['']s?\s+(fault|mistake|error|bug|broke|caused)",
                "type": "direct_blame",
                "severity": "high",
                "explanation": "Directly attributes fault to an individual",
            },
            {
                "pattern": r"@?\w+\s+(should have|failed to|forgot to|didn['']t)",
                "type": "should_have",
                "severity": "high",
                "explanation": "Implies individual should have known better",
            },
            {
                "pattern": r"(who|someone|they)\s+(broke|caused|introduced)",
                "type": "blame_seeking",
                "severity": "medium",
                "explanation": "Seeks to identify who caused the issue",
            },
            {
                "pattern": r"(careless|negligent|lazy|incompetent|stupid)",
                "type": "pejorative",
                "severity": "critical",
                "explanation": "Uses pejorative language about individuals",
            },
            {
                "pattern": r"(the|this)\s+(developer|engineer|author)\s+(caused|broke)",
                "type": "role_blame",
                "severity": "medium",
                "explanation": "Blames by role rather than addressing system",
            },
            {
                "pattern": r"(obvious|clearly|should be obvious|basic mistake)",
                "type": "hindsight_bias",
                "severity": "medium",
                "explanation": "Applies hindsight bias",
            },
            {
                "pattern": r"(always|never)\s+\w+\s+(the|this|that)",
                "type": "absolutist",
                "severity": "low",
                "explanation": "Uses absolutist language that implies negligence",
            },
            {
                "pattern": r"@\w+\s+(is|was)\s+(responsible|to blame)",
                "type": "direct_attribution",
                "severity": "high",
                "explanation": "Directly attributes blame to named individual",
            },
        ]

        self._rewrite_rules: dict[str, dict[str, str]] = {
            "direct_blame": {
                "template": "A {component} change introduced {issue}",
                "principle": "Focus on systems, not individuals",
            },
            "should_have": {
                "template": "The {process} did not {action}",
                "principle": "Frame as process gap, not individual failure",
            },
            "blame_seeking": {
                "template": "A change in {component} caused {issue}",
                "principle": "Ask 'what' and 'how', not 'who'",
            },
            "role_blame": {
                "template": "The {process} allowed {issue} to occur",
                "principle": "Address system gaps, not roles",
            },
            "hindsight_bias": {
                "template": "{issue} was not detected by existing {safeguard}",
                "principle": "Avoid hindsight bias",
            },
        }

    def check_text(
        self,
        text: str,
        location: str = "content",
    ) -> list[BlamelessViolation]:
        """Check text for blameless language violations."""
        violations = []

        for rule in self._blame_patterns:
            matches = re.finditer(
                rule["pattern"],
                text,
                re.IGNORECASE
            )

            for match in matches:
                self._violation_counter += 1
                violations.append(BlamelessViolation(
                    violation_id=f"BV-{self._violation_counter}",
                    original_text=match.group(),
                    location=location,
                    violation_type=rule["type"],
                    explanation=rule["explanation"],
                    severity=rule["severity"],
                ))

        return violations

    def check_postmortem(
        self,
        postmortem_dict: dict[str, Any],
    ) -> list[BlamelessViolation]:
        """Check entire postmortem for violations."""
        violations = []

        if "executive_summary" in postmortem_dict:
            violations.extend(self.check_text(
                postmortem_dict["executive_summary"],
                "Executive Summary"
            ))

        if "root_cause_analysis" in postmortem_dict:
            rca = postmortem_dict["root_cause_analysis"]
            if isinstance(rca, dict):
                if "summary" in rca:
                    violations.extend(self.check_text(
                        rca["summary"],
                        "Root Cause Summary"
                    ))
                if "technical_details" in rca:
                    violations.extend(self.check_text(
                        rca["technical_details"],
                        "Technical Details"
                    ))

        if "contributing_factors" in postmortem_dict:
            for i, factor in enumerate(postmortem_dict["contributing_factors"]):
                if isinstance(factor, dict) and "description" in factor:
                    violations.extend(self.check_text(
                        factor["description"],
                        f"Contributing Factor {i + 1}"
                    ))

        if "action_items" in postmortem_dict:
            for i, item in enumerate(postmortem_dict["action_items"]):
                if isinstance(item, dict):
                    if "title" in item:
                        violations.extend(self.check_text(
                            item["title"],
                            f"Action Item {i + 1} Title"
                        ))
                    if "description" in item:
                        violations.extend(self.check_text(
                            item["description"],
                            f"Action Item {i + 1} Description"
                        ))

        return violations

    def suggest_rewrite(
        self,
        violation: BlamelessViolation,
        context: str | None = None,
    ) -> BlamelessRewrite:
        """Suggest a rewrite for a violation."""
        rule = self._rewrite_rules.get(violation.violation_type)

        if rule:
            principle = rule["principle"]
            suggested = self._generate_rewrite(
                violation.original_text,
                violation.violation_type,
                context,
            )
        else:
            principle = "Focus on systems and processes"
            suggested = self._generic_rewrite(violation.original_text)

        reasoning = self._generate_reasoning(violation, suggested)

        return BlamelessRewrite(
            violation=violation,
            suggested_text=suggested,
            reasoning=reasoning,
            principle=principle,
        )

    def _generate_rewrite(
        self,
        original: str,
        violation_type: str,
        context: str | None,
    ) -> str:
        """Generate a rewritten version of the text."""
        if violation_type == "direct_blame":
            match = re.search(r"@?(\w+)['']s?\s+\w+", original, re.IGNORECASE)
            if match:
                return "A code change introduced the issue"

        if violation_type == "should_have":
            match = re.search(
                r"@?(\w+)\s+(should have|failed to|forgot to)",
                original,
                re.IGNORECASE
            )
            if match:
                return (
                    "The process did not include checks for this type of issue"
                )

        if violation_type == "blame_seeking":
            return "A system change caused the issue"

        if violation_type == "role_blame":
            return "The code review process did not catch the issue"

        if violation_type == "hindsight_bias":
            return "This issue was not detected by existing safeguards"

        if violation_type == "pejorative":
            return "[Content removed - inappropriate language]"

        return self._generic_rewrite(original)

    def _generic_rewrite(self, original: str) -> str:
        """Generate a generic rewrite."""
        names = re.findall(r"@\w+", original)
        result = original

        for name in names:
            result = result.replace(name, "the system")

        blame_words = [
            ("broke", "affected"),
            ("caused", "led to"),
            ("fault", "factor"),
            ("mistake", "gap"),
            ("failed", "did not"),
        ]

        for old, new in blame_words:
            result = re.sub(old, new, result, flags=re.IGNORECASE)

        return result

    def _generate_reasoning(
        self,
        violation: BlamelessViolation,
        suggested: str,
    ) -> str:
        """Generate reasoning for the rewrite."""
        reasoning_templates = {
            "direct_blame": (
                "Focus on the code/system change, not the person who made it"
            ),
            "should_have": (
                "Shift from 'should have known' to 'wasn't documented/required'"
            ),
            "blame_seeking": (
                "Address 'what happened' instead of 'who did it'"
            ),
            "role_blame": (
                "Blame the process gap, not the role/person"
            ),
            "hindsight_bias": (
                "Acknowledge that the outcome wasn't predictable at the time"
            ),
            "pejorative": (
                "Remove language that could be hurtful or demotivating"
            ),
            "absolutist": (
                "Avoid implying that someone should 'always' or 'never' do something"
            ),
        }

        return reasoning_templates.get(
            violation.violation_type,
            "Reframe to focus on systems and processes"
        )

    def rewrite_all(
        self,
        text: str,
        location: str = "content",
    ) -> tuple[str, list[BlamelessRewrite]]:
        """Check text and return rewritten version with all fixes applied."""
        violations = self.check_text(text, location)
        rewrites = []

        result = text

        for violation in violations:
            rewrite = self.suggest_rewrite(violation)
            rewrites.append(rewrite)

            result = result.replace(
                violation.original_text,
                rewrite.suggested_text
            )

        return result, rewrites

    def calculate_score(
        self,
        postmortem_dict: dict[str, Any],
        rewrites_applied: int = 0,
    ) -> BlamelessScore:
        """Calculate blameless score for a postmortem."""
        violations = self.check_postmortem(postmortem_dict)

        by_category: dict[str, int] = {}
        for violation in violations:
            cat = violation.violation_type
            by_category[cat] = by_category.get(cat, 0) + 1

        if not violations:
            score = 1.0
        else:
            severity_weights = {
                "critical": 3,
                "high": 2,
                "medium": 1,
                "low": 0.5,
            }

            total_weight = sum(
                severity_weights.get(v.severity, 1) for v in violations
            )

            max_weight = len(violations) * 3
            score = max(0, 1 - (total_weight / max_weight))

        recommendations = []

        if by_category.get("direct_blame", 0) > 0:
            recommendations.append(
                "Replace individual names with system/process references"
            )

        if by_category.get("should_have", 0) > 0:
            recommendations.append(
                "Frame gaps as process issues, not individual failures"
            )

        if by_category.get("hindsight_bias", 0) > 0:
            recommendations.append(
                "Acknowledge information wasn't available at decision time"
            )

        if not recommendations and violations:
            recommendations.append(
                "Review highlighted text and apply suggested rewrites"
            )

        if not violations:
            recommendations.append(
                "Postmortem follows blameless principles"
            )

        return BlamelessScore(
            score=score,
            violations_found=len(violations),
            violations_fixed=rewrites_applied,
            by_category=by_category,
            recommendations=recommendations,
        )

    def get_principles(self) -> list[dict[str, str]]:
        """Get blameless principles used for checking."""
        return [
            {
                "principle": "Focus on systems, not individuals",
                "description": (
                    "Address what systems/processes allowed the issue, "
                    "not who was involved"
                ),
            },
            {
                "principle": "Ask 'what' and 'how', not 'who'",
                "description": (
                    "Investigate the mechanics of the incident, "
                    "not individual blame"
                ),
            },
            {
                "principle": "Assume good intentions",
                "description": (
                    "People make mistakes despite best efforts - "
                    "focus on preventing recurrence"
                ),
            },
            {
                "principle": "Frame as learning opportunity",
                "description": (
                    "Every incident is a chance to improve systems, "
                    "not punish individuals"
                ),
            },
            {
                "principle": "Avoid hindsight bias",
                "description": (
                    "Don't assume what's obvious now was obvious then"
                ),
            },
        ]


_checker: BlamelessChecker | None = None


def get_blameless_checker() -> BlamelessChecker:
    """Get the default blameless checker."""
    global _checker
    if _checker is None:
        _checker = BlamelessChecker()
    return _checker
