"""AI-powered action item prioritization for postmortems.

Ranks action items by impact, effort, and prevention potential.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("devcopilot.postmortem.action_items")


class ActionPriority(Enum):
    """Priority level for action items."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ActionCategory(Enum):
    """Category of action item."""

    PREVENTION = "prevention"
    DETECTION = "detection"
    MITIGATION = "mitigation"
    DOCUMENTATION = "documentation"
    PROCESS = "process"
    TOOLING = "tooling"


class ActionEffort(Enum):
    """Effort estimate for action item."""

    TRIVIAL = "trivial"
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"
    EXTRA_LARGE = "extra_large"


@dataclass
class ActionItem:
    """A prioritized action item from postmortem analysis."""

    action_id: str
    title: str
    description: str
    priority: ActionPriority
    category: ActionCategory
    effort: ActionEffort
    effort_hours: float
    impact_score: float
    prevention_potential: str
    owner: str = ""
    due_date: datetime | None = None
    ticket_url: str = ""
    status: str = "open"
    prevents_similar: bool = False
    related_incidents: list[str] = field(default_factory=list)
    rationale: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "title": self.title,
            "description": self.description,
            "priority": self.priority.value,
            "category": self.category.value,
            "effort": self.effort.value,
            "effort_hours": self.effort_hours,
            "impact_score": round(self.impact_score, 2),
            "prevention_potential": self.prevention_potential,
            "owner": self.owner,
            "due_date": self.due_date.isoformat() if self.due_date else None,
            "ticket_url": self.ticket_url,
            "status": self.status,
            "prevents_similar": self.prevents_similar,
            "related_incidents": self.related_incidents,
            "rationale": self.rationale,
        }


@dataclass
class PrioritizationResult:
    """Result of action item prioritization."""

    action_items: list[ActionItem]
    total_effort_hours: float
    high_value_items: int
    quick_wins: list[ActionItem]
    strategic_items: list[ActionItem]
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_items": [a.to_dict() for a in self.action_items],
            "total_effort_hours": self.total_effort_hours,
            "high_value_items": self.high_value_items,
            "quick_wins": [a.to_dict() for a in self.quick_wins],
            "strategic_items": [a.to_dict() for a in self.strategic_items],
            "summary": self.summary,
        }


class ActionItemPrioritizer:
    """AI-powered action item prioritization."""

    def __init__(self) -> None:
        self._counter = 0
        self._action_templates = self._setup_action_templates()

    def _setup_action_templates(self) -> dict[str, dict[str, Any]]:
        """Set up action item templates by root cause type."""
        return {
            "null_safety": {
                "title": "Add null safety checks",
                "description": "Implement null/undefined handling for affected code paths",
                "category": ActionCategory.PREVENTION,
                "effort": ActionEffort.SMALL,
                "effort_hours": 2.0,
                "prevention_potential": "This exact bug",
                "prevents_similar": True,
            },
            "type_checking": {
                "title": "Enable strict type checking",
                "description": "Enable Mypy strict mode or TypeScript strict in affected module",
                "category": ActionCategory.PREVENTION,
                "effort": ActionEffort.MEDIUM,
                "effort_hours": 8.0,
                "prevention_potential": "All null-related bugs in module",
                "prevents_similar": True,
            },
            "test_coverage": {
                "title": "Add test case for edge case",
                "description": "Write test that would have caught this specific failure mode",
                "category": ActionCategory.PREVENTION,
                "effort": ActionEffort.SMALL,
                "effort_hours": 2.0,
                "prevention_potential": "This exact scenario",
                "prevents_similar": True,
            },
            "canary_default": {
                "title": "Make canary deployment default",
                "description": "Require canary deployment for all production changes",
                "category": ActionCategory.MITIGATION,
                "effort": ActionEffort.SMALL,
                "effort_hours": 1.0,
                "prevention_potential": "Limits blast radius of future incidents",
                "prevents_similar": False,
            },
            "staging_data": {
                "title": "Add realistic test data to staging",
                "description": "Ensure staging environment has data that exercises edge cases",
                "category": ActionCategory.PREVENTION,
                "effort": ActionEffort.MEDIUM,
                "effort_hours": 4.0,
                "prevention_potential": "Edge cases discoverable in staging",
                "prevents_similar": True,
            },
            "review_checklist": {
                "title": "Add code review checklist",
                "description": "Create checklist for reviewing changes in affected area",
                "category": ActionCategory.PROCESS,
                "effort": ActionEffort.SMALL,
                "effort_hours": 2.0,
                "prevention_potential": "Improves review quality",
                "prevents_similar": False,
            },
            "alert_tuning": {
                "title": "Tune alert thresholds",
                "description": "Lower alert threshold for faster detection",
                "category": ActionCategory.DETECTION,
                "effort": ActionEffort.TRIVIAL,
                "effort_hours": 0.5,
                "prevention_potential": "Faster detection of similar issues",
                "prevents_similar": False,
            },
            "runbook_update": {
                "title": "Update runbook",
                "description": "Update or create runbook for handling similar incidents",
                "category": ActionCategory.DOCUMENTATION,
                "effort": ActionEffort.SMALL,
                "effort_hours": 2.0,
                "prevention_potential": "Faster resolution in future",
                "prevents_similar": False,
            },
            "circuit_breaker": {
                "title": "Implement circuit breaker",
                "description": "Add circuit breaker pattern for external dependencies",
                "category": ActionCategory.MITIGATION,
                "effort": ActionEffort.LARGE,
                "effort_hours": 16.0,
                "prevention_potential": "Prevents cascade failures",
                "prevents_similar": True,
            },
            "auto_rollback": {
                "title": "Configure automatic rollback",
                "description": "Set up automatic rollback on error rate spike",
                "category": ActionCategory.MITIGATION,
                "effort": ActionEffort.MEDIUM,
                "effort_hours": 4.0,
                "prevention_potential": "Limits incident duration",
                "prevents_similar": False,
            },
        }

    def generate_action_items(
        self,
        incident_data: dict[str, Any],
        root_cause: str,
        counterfactuals: list[dict[str, Any]] | None = None,
        similar_incidents: list[str] | None = None,
    ) -> PrioritizationResult:
        """Generate and prioritize action items for a postmortem."""
        items: list[ActionItem] = []
        root_cause_lower = root_cause.lower()

        if any(word in root_cause_lower for word in ["null", "none", "undefined", "nonetype"]):
            items.append(self._create_item("null_safety", incident_data, high_priority=True))
            items.append(self._create_item("type_checking", incident_data))
            items.append(self._create_item("test_coverage", incident_data, high_priority=True))

        if incident_data.get("triggered_by_deploy", False) or "deploy" in root_cause_lower:
            items.append(self._create_item("canary_default", incident_data, high_priority=True))
            items.append(self._create_item("auto_rollback", incident_data))

        if any(word in root_cause_lower for word in ["staging", "test", "edge case"]):
            items.append(self._create_item("staging_data", incident_data))

        if any(word in root_cause_lower for word in ["review", "missed", "overlooked"]):
            items.append(self._create_item("review_checklist", incident_data))

        if any(word in root_cause_lower for word in ["cascade", "timeout", "dependency"]):
            items.append(self._create_item("circuit_breaker", incident_data))

        ttd = incident_data.get("time_to_detect_minutes", 0)
        if ttd > 5:
            items.append(self._create_item("alert_tuning", incident_data))

        items.append(self._create_item("runbook_update", incident_data))

        if counterfactuals:
            for cf in counterfactuals:
                if cf.get("value") == "high" and not self._has_matching_item(items, cf):
                    items.append(self._create_item_from_counterfactual(cf, incident_data))

        if similar_incidents:
            for item in items:
                item.related_incidents = similar_incidents[:3]

        items = self._prioritize_items(items)

        total_effort = sum(item.effort_hours for item in items)
        high_value = sum(1 for item in items if item.priority in [ActionPriority.CRITICAL, ActionPriority.HIGH])

        quick_wins = [
            item for item in items
            if item.effort_hours <= 2.0 and item.impact_score >= 0.7
        ][:3]

        strategic = [
            item for item in items
            if item.effort_hours >= 8.0 and item.prevents_similar
        ][:3]

        summary = self._generate_summary(items, total_effort, high_value)

        return PrioritizationResult(
            action_items=items,
            total_effort_hours=total_effort,
            high_value_items=high_value,
            quick_wins=quick_wins,
            strategic_items=strategic,
            summary=summary,
        )

    def _create_item(
        self,
        template_key: str,
        incident_data: dict[str, Any],
        high_priority: bool = False,
    ) -> ActionItem:
        """Create an action item from a template."""
        self._counter += 1
        template = self._action_templates[template_key]

        effort_multipliers = {
            ActionEffort.TRIVIAL: 0.5,
            ActionEffort.SMALL: 1.0,
            ActionEffort.MEDIUM: 4.0,
            ActionEffort.LARGE: 16.0,
            ActionEffort.EXTRA_LARGE: 40.0,
        }

        effort_hours = template["effort_hours"]

        impact_base = 0.8 if template["prevents_similar"] else 0.5
        if template["category"] == ActionCategory.PREVENTION:
            impact_base += 0.1
        if high_priority:
            impact_base += 0.1

        impact_score = min(1.0, impact_base)

        if high_priority:
            priority = ActionPriority.HIGH
        elif impact_score >= 0.8 and effort_hours <= 4:
            priority = ActionPriority.HIGH
        elif impact_score >= 0.6:
            priority = ActionPriority.MEDIUM
        else:
            priority = ActionPriority.LOW

        severity = incident_data.get("severity", "").lower()
        if severity in ["sev1", "critical", "p0"]:
            if priority == ActionPriority.HIGH:
                priority = ActionPriority.CRITICAL
            elif priority == ActionPriority.MEDIUM:
                priority = ActionPriority.HIGH

        rationale = f"{'Prevents' if template['prevents_similar'] else 'Improves'}: {template['prevention_potential']}"
        if effort_hours <= 2:
            rationale += f" | Effort: {effort_hours} hours (quick win)"
        else:
            rationale += f" | Effort: {effort_hours} hours"

        due_days = {
            ActionPriority.CRITICAL: 3,
            ActionPriority.HIGH: 7,
            ActionPriority.MEDIUM: 14,
            ActionPriority.LOW: 30,
        }
        due_date = datetime.now(timezone.utc) + timedelta(days=due_days[priority])

        return ActionItem(
            action_id=f"action_{self._counter}",
            title=template["title"],
            description=template["description"],
            priority=priority,
            category=template["category"],
            effort=template["effort"],
            effort_hours=effort_hours,
            impact_score=impact_score,
            prevention_potential=template["prevention_potential"],
            prevents_similar=template["prevents_similar"],
            rationale=rationale,
            due_date=due_date,
        )

    def _create_item_from_counterfactual(
        self,
        counterfactual: dict[str, Any],
        incident_data: dict[str, Any],
    ) -> ActionItem:
        """Create an action item from a counterfactual analysis."""
        self._counter += 1

        title = f"Implement: {counterfactual.get('question', 'Unknown')[:50]}"
        description = counterfactual.get("description", "")

        outcome = counterfactual.get("outcome", {})
        prevented = outcome.get("prevented", False)
        impact_reduction = outcome.get("impact_reduction_percent", 0)

        if prevented:
            impact_score = 1.0
            priority = ActionPriority.CRITICAL
        elif impact_reduction >= 80:
            impact_score = 0.9
            priority = ActionPriority.HIGH
        elif impact_reduction >= 50:
            impact_score = 0.7
            priority = ActionPriority.MEDIUM
        else:
            impact_score = 0.5
            priority = ActionPriority.LOW

        effort_hours = counterfactual.get("investment_hours", 4.0)
        if effort_hours <= 1:
            effort = ActionEffort.TRIVIAL
        elif effort_hours <= 4:
            effort = ActionEffort.SMALL
        elif effort_hours <= 16:
            effort = ActionEffort.MEDIUM
        else:
            effort = ActionEffort.LARGE

        return ActionItem(
            action_id=f"action_{self._counter}",
            title=title,
            description=description,
            priority=priority,
            category=ActionCategory.PREVENTION,
            effort=effort,
            effort_hours=effort_hours,
            impact_score=impact_score,
            prevention_potential=counterfactual.get("recommendation", ""),
            prevents_similar=prevented,
            rationale=f"From counterfactual analysis: {counterfactual.get('question', '')}",
        )

    def _has_matching_item(self, items: list[ActionItem], counterfactual: dict[str, Any]) -> bool:
        """Check if an action item already covers a counterfactual."""
        cf_type = counterfactual.get("type", "")
        for item in items:
            if cf_type == "test_coverage" and "test" in item.title.lower():
                return True
            if cf_type == "type_checking" and "type" in item.title.lower():
                return True
            if cf_type == "canary_deploy" and "canary" in item.title.lower():
                return True
        return False

    def _prioritize_items(self, items: list[ActionItem]) -> list[ActionItem]:
        """Sort items by priority and impact."""
        priority_order = {
            ActionPriority.CRITICAL: 0,
            ActionPriority.HIGH: 1,
            ActionPriority.MEDIUM: 2,
            ActionPriority.LOW: 3,
        }

        return sorted(
            items,
            key=lambda i: (
                priority_order[i.priority],
                -i.impact_score,
                i.effort_hours,
            ),
        )

    def _generate_summary(
        self,
        items: list[ActionItem],
        total_effort: float,
        high_value: int,
    ) -> str:
        """Generate a summary of the action items."""
        prevention_items = sum(1 for i in items if i.prevents_similar)
        quick_wins = sum(1 for i in items if i.effort_hours <= 2 and i.impact_score >= 0.7)

        return (
            f"{len(items)} action items identified: {high_value} high priority, "
            f"{prevention_items} prevent similar incidents. "
            f"Total effort: {total_effort:.0f} hours. "
            f"{quick_wins} quick wins available."
        )


_prioritizer: ActionItemPrioritizer | None = None


def get_action_prioritizer() -> ActionItemPrioritizer:
    """Get the default action item prioritizer."""
    global _prioritizer
    if _prioritizer is None:
        _prioritizer = ActionItemPrioritizer()
    return _prioritizer
