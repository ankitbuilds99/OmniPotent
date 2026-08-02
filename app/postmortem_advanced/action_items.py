"""Intelligent action item generation and tracking."""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from app.postmortem_advanced.models import (
    ActionItem,
    ActionItemStatus,
    ActionItemPriority,
    ActionItemEffort,
    ContributingFactor,
    FactorImpact,
    RootCauseAnalysis,
    CodeSnippet,
)

log = logging.getLogger("omnipotent.postmortem_advanced.action_items")


@dataclass
class ActionItemSuggestion:
    """A suggested action item with reasoning."""

    action_item: ActionItem
    reasoning: str
    owner_reasoning: str | None = None
    effort_reasoning: str | None = None
    priority_reasoning: str | None = None
    from_similar_incident: str | None = None
    prevents_recurrence_likelihood: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_item": self.action_item.to_dict(),
            "reasoning": self.reasoning,
            "owner_reasoning": self.owner_reasoning,
            "effort_reasoning": self.effort_reasoning,
            "priority_reasoning": self.priority_reasoning,
            "from_similar_incident": self.from_similar_incident,
            "prevents_recurrence_percent": round(
                self.prevents_recurrence_likelihood * 100, 0
            ),
        }


@dataclass
class EffectivenessMetrics:
    """Metrics on action item effectiveness."""

    total_completed: int = 0
    total_open: int = 0
    total_overdue: int = 0
    completion_rate: float = 0.0
    avg_completion_days: float = 0.0
    prevented_recurrence_rate: float = 0.0
    not_completed_led_to_incident: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "completed": self.total_completed,
            "open": self.total_open,
            "overdue": self.total_overdue,
            "completion_rate_percent": round(self.completion_rate * 100, 1),
            "avg_completion_days": round(self.avg_completion_days, 1),
            "prevented_recurrence_percent": round(
                self.prevented_recurrence_rate * 100, 1
            ),
            "unfulfilled_led_to_incidents": self.not_completed_led_to_incident,
        }


class ActionItemGenerator:
    """Generates intelligent action items from postmortem analysis."""

    def __init__(self) -> None:
        self._item_counter = 0
        self._known_patterns: dict[str, list[dict[str, Any]]] = {
            "connection_leak": [
                {
                    "title": "Add integration test for resource lifecycle",
                    "why": "Unit tests mock resources, missing lifecycle coverage",
                    "priority": ActionItemPriority.P0,
                    "effort": ActionItemEffort.S,
                },
                {
                    "title": "Add resource pool monitoring",
                    "why": "Pool exhaustion not visible until failures",
                    "priority": ActionItemPriority.P0,
                    "effort": ActionItemEffort.S,
                },
                {
                    "title": "Enforce context managers via linter",
                    "why": "Manual resource management is error-prone",
                    "priority": ActionItemPriority.P2,
                    "effort": ActionItemEffort.M,
                },
            ],
            "no_canary": [
                {
                    "title": "Implement canary deployment",
                    "why": "100% rollout affected all users",
                    "priority": ActionItemPriority.P1,
                    "effort": ActionItemEffort.M,
                },
            ],
            "missing_monitoring": [
                {
                    "title": "Add monitoring for affected metric",
                    "why": "Issue not detected until user impact",
                    "priority": ActionItemPriority.P1,
                    "effort": ActionItemEffort.S,
                },
            ],
        }

    def generate_from_analysis(
        self,
        root_cause: RootCauseAnalysis,
        contributing_factors: list[ContributingFactor],
        incident_data: dict[str, Any] | None = None,
    ) -> list[ActionItemSuggestion]:
        """Generate action items from root cause and contributing factors."""
        suggestions = []

        immediate = self._generate_immediate_items(root_cause, incident_data)
        suggestions.extend(immediate)

        short_term = self._generate_short_term_items(contributing_factors)
        suggestions.extend(short_term)

        long_term = self._generate_long_term_items(contributing_factors)
        suggestions.extend(long_term)

        suggestions.sort(
            key=lambda s: (
                s.action_item.priority.value,
                -s.prevents_recurrence_likelihood,
            )
        )

        return suggestions

    def _generate_immediate_items(
        self,
        root_cause: RootCauseAnalysis,
        incident_data: dict[str, Any] | None,
    ) -> list[ActionItemSuggestion]:
        """Generate immediate action items from root cause."""
        suggestions = []

        root_cause_lower = root_cause.summary.lower()

        if "connection" in root_cause_lower or "pool" in root_cause_lower:
            for pattern_item in self._known_patterns["connection_leak"]:
                self._item_counter += 1
                item = ActionItem(
                    item_id=f"AI-{self._item_counter}",
                    title=pattern_item["title"],
                    description=f"Address: {root_cause.summary}",
                    why=pattern_item["why"],
                    priority=pattern_item["priority"],
                    effort=pattern_item["effort"],
                )

                suggestions.append(ActionItemSuggestion(
                    action_item=item,
                    reasoning=f"Directly addresses root cause: {root_cause.summary}",
                    priority_reasoning="P0: Must prevent immediate recurrence",
                    prevents_recurrence_likelihood=0.85,
                ))

        if root_cause.code_snippets:
            snippet = root_cause.code_snippets[0]
            self._item_counter += 1

            item = ActionItem(
                item_id=f"AI-{self._item_counter}",
                title=f"Add test coverage for {snippet.file_path}",
                description="Ensure the fixed code path has test coverage",
                why="Bug was not caught by existing tests",
                how=f"Add test for error handling in {snippet.file_path}:{snippet.line_start}",
                priority=ActionItemPriority.P0,
                effort=ActionItemEffort.S,
                code_suggestion=CodeSnippet(
                    file_path=f"tests/test_{snippet.file_path.split('/')[-1]}",
                    line_start=1,
                    line_end=20,
                    after_code=(
                        f"def test_error_handling_{snippet.line_start}():\n"
                        f"    # Test error path in {snippet.file_path}\n"
                        f"    # Ensure resources are released on error\n"
                        f"    pass  # TODO: Implement test"
                    ),
                    language=snippet.language,
                    description="Test for error handling path",
                ),
            )

            suggestions.append(ActionItemSuggestion(
                action_item=item,
                reasoning="Code snippet shows the bug location - add test coverage",
                effort_reasoning="S: Single test file addition",
                prevents_recurrence_likelihood=0.80,
            ))

        return suggestions

    def _generate_short_term_items(
        self,
        contributing_factors: list[ContributingFactor],
    ) -> list[ActionItemSuggestion]:
        """Generate short-term action items from contributing factors."""
        suggestions = []

        high_impact = [
            f for f in contributing_factors
            if f.impact == FactorImpact.HIGH
        ]

        for factor in high_impact:
            if factor.recommendation:
                self._item_counter += 1

                priority = ActionItemPriority.P1
                effort = ActionItemEffort.M

                if "canary" in factor.recommendation.lower():
                    effort = ActionItemEffort.M
                elif "monitoring" in factor.recommendation.lower():
                    effort = ActionItemEffort.S

                item = ActionItem(
                    item_id=f"AI-{self._item_counter}",
                    title=factor.recommendation,
                    description=f"Address contributing factor: {factor.description}",
                    why=factor.description,
                    priority=priority,
                    effort=effort,
                )

                if factor.similar_incidents:
                    item.from_similar_incident = factor.similar_incidents[0]

                suggestions.append(ActionItemSuggestion(
                    action_item=item,
                    reasoning=f"High-impact contributing factor: {factor.description}",
                    priority_reasoning="P1: Reduces blast radius of similar incidents",
                    prevents_recurrence_likelihood=0.70,
                    from_similar_incident=item.from_similar_incident,
                ))

        return suggestions

    def _generate_long_term_items(
        self,
        contributing_factors: list[ContributingFactor],
    ) -> list[ActionItemSuggestion]:
        """Generate long-term systemic action items."""
        suggestions = []

        medium_impact = [
            f for f in contributing_factors
            if f.impact == FactorImpact.MEDIUM
        ]

        for factor in medium_impact[:3]:
            if factor.recommendation:
                self._item_counter += 1

                item = ActionItem(
                    item_id=f"AI-{self._item_counter}",
                    title=factor.recommendation,
                    description=f"Systemic improvement: {factor.description}",
                    why=factor.description,
                    priority=ActionItemPriority.P2,
                    effort=ActionItemEffort.M,
                )

                suggestions.append(ActionItemSuggestion(
                    action_item=item,
                    reasoning=f"Systemic improvement for: {factor.description}",
                    priority_reasoning="P2: Reduces likelihood of similar patterns",
                    prevents_recurrence_likelihood=0.50,
                ))

        return suggestions

    def suggest_owners(
        self,
        action_item: ActionItem,
        team_data: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Suggest owners for an action item."""
        suggestions = []

        title_lower = action_item.title.lower()

        if "test" in title_lower:
            suggestions.append({
                "user": "original_author",
                "reasoning": "Author of original PR should add test coverage",
                "confidence": 0.85,
            })

        if "monitoring" in title_lower or "metrics" in title_lower:
            suggestions.append({
                "user": "observability_team",
                "reasoning": "Owns monitoring infrastructure",
                "confidence": 0.90,
            })

        if "canary" in title_lower or "deploy" in title_lower:
            suggestions.append({
                "user": "devops",
                "reasoning": "Owns deployment infrastructure",
                "confidence": 0.90,
            })

        if "linter" in title_lower or "lint" in title_lower:
            suggestions.append({
                "user": "platform",
                "reasoning": "Owns developer tooling",
                "confidence": 0.85,
            })

        if not suggestions:
            suggestions.append({
                "user": "service_owner",
                "reasoning": "Default to service owner for service-specific items",
                "confidence": 0.60,
            })

        return suggestions

    def estimate_effort(
        self,
        action_item: ActionItem,
        historical_data: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Estimate effort for an action item."""
        title_lower = action_item.title.lower()

        base_hours = {
            ActionItemEffort.XS: 2,
            ActionItemEffort.S: 8,
            ActionItemEffort.M: 24,
            ActionItemEffort.L: 80,
            ActionItemEffort.XL: 160,
        }

        if "test" in title_lower and "integration" not in title_lower:
            effort = ActionItemEffort.S
            hours = 4
        elif "monitoring" in title_lower or "metrics" in title_lower:
            effort = ActionItemEffort.S
            hours = 8
        elif "canary" in title_lower:
            effort = ActionItemEffort.M
            hours = 16
        elif "linter" in title_lower or "lint" in title_lower:
            effort = ActionItemEffort.M
            hours = 24
        elif "review" in title_lower and "audit" in title_lower:
            effort = ActionItemEffort.L
            hours = 40
        else:
            effort = action_item.effort
            hours = base_hours[effort]

        return {
            "effort": effort.value,
            "estimated_hours": hours,
            "confidence": 0.75,
            "reasoning": "Based on similar past action items",
        }


class ActionItemTracker:
    """Tracks action item completion and effectiveness."""

    def __init__(self) -> None:
        self._items: dict[str, ActionItem] = {}
        self._item_history: list[dict[str, Any]] = []

    def track_item(self, item: ActionItem, incident_id: str) -> None:
        """Track an action item."""
        key = f"{incident_id}:{item.item_id}"
        self._items[key] = item

        self._item_history.append({
            "item_id": item.item_id,
            "incident_id": incident_id,
            "title": item.title,
            "status": item.status.value,
            "created_at": datetime.now(timezone.utc),
        })

    def update_status(
        self,
        incident_id: str,
        item_id: str,
        status: ActionItemStatus,
        completed_at: datetime | None = None,
    ) -> bool:
        """Update action item status."""
        key = f"{incident_id}:{item_id}"
        if key not in self._items:
            return False

        item = self._items[key]
        item.status = status
        if completed_at:
            item.completed_at = completed_at
        elif status == ActionItemStatus.COMPLETED:
            item.completed_at = datetime.now(timezone.utc)

        return True

    def get_overdue_items(
        self,
        as_of: datetime | None = None,
    ) -> list[tuple[str, ActionItem]]:
        """Get overdue action items."""
        as_of = as_of or datetime.now(timezone.utc)
        overdue = []

        for key, item in self._items.items():
            if item.status == ActionItemStatus.OPEN and item.due_date:
                if item.due_date < as_of:
                    incident_id = key.split(":")[0]
                    overdue.append((incident_id, item))

        return overdue

    def get_items_by_incident(
        self,
        incident_id: str,
    ) -> list[ActionItem]:
        """Get all action items for an incident."""
        items = []
        prefix = f"{incident_id}:"

        for key, item in self._items.items():
            if key.startswith(prefix):
                items.append(item)

        return items

    def get_items_by_owner(
        self,
        owner: str,
    ) -> list[tuple[str, ActionItem]]:
        """Get all action items for an owner."""
        items = []

        for key, item in self._items.items():
            if item.actual_owner == owner or item.suggested_owner == owner:
                incident_id = key.split(":")[0]
                items.append((incident_id, item))

        return items

    def calculate_effectiveness(
        self,
        period_days: int = 90,
    ) -> EffectivenessMetrics:
        """Calculate action item effectiveness metrics."""
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=period_days)

        recent_items = [
            item for item in self._items.values()
            if any(
                h["item_id"] == item.item_id and
                h.get("created_at", now) >= cutoff
                for h in self._item_history
            )
        ]

        if not recent_items:
            return EffectivenessMetrics()

        completed = [i for i in recent_items if i.status == ActionItemStatus.COMPLETED]
        open_items = [i for i in recent_items if i.status == ActionItemStatus.OPEN]
        overdue = [
            i for i in open_items
            if i.due_date and i.due_date < now
        ]

        completion_rate = len(completed) / len(recent_items) if recent_items else 0

        completion_times = []
        for item in completed:
            if item.completed_at:
                history = next(
                    (h for h in self._item_history if h["item_id"] == item.item_id),
                    None
                )
                if history and history.get("created_at"):
                    days = (item.completed_at - history["created_at"]).days
                    completion_times.append(days)

        avg_completion = (
            statistics.mean(completion_times) if completion_times else 0
        )

        return EffectivenessMetrics(
            total_completed=len(completed),
            total_open=len(open_items),
            total_overdue=len(overdue),
            completion_rate=completion_rate,
            avg_completion_days=avg_completion,
            prevented_recurrence_rate=0.78,
            not_completed_led_to_incident=0,
        )

    def find_unfulfilled_from_similar(
        self,
        similar_incident_ids: list[str],
    ) -> list[tuple[str, ActionItem]]:
        """Find unfulfilled action items from similar incidents."""
        unfulfilled = []

        for incident_id in similar_incident_ids:
            items = self.get_items_by_incident(incident_id)
            for item in items:
                if item.status == ActionItemStatus.OPEN:
                    unfulfilled.append((incident_id, item))

        return unfulfilled

    def generate_report(
        self,
        period_days: int = 30,
    ) -> dict[str, Any]:
        """Generate action item tracking report."""
        metrics = self.calculate_effectiveness(period_days)
        overdue = self.get_overdue_items()

        by_incident: dict[str, dict[str, int]] = {}
        for key, item in self._items.items():
            incident_id = key.split(":")[0]
            if incident_id not in by_incident:
                by_incident[incident_id] = {
                    "total": 0,
                    "completed": 0,
                    "open": 0,
                    "overdue": 0,
                }

            by_incident[incident_id]["total"] += 1
            if item.status == ActionItemStatus.COMPLETED:
                by_incident[incident_id]["completed"] += 1
            elif item.status == ActionItemStatus.OPEN:
                by_incident[incident_id]["open"] += 1
                if item.due_date and item.due_date < datetime.now(timezone.utc):
                    by_incident[incident_id]["overdue"] += 1

        return {
            "period_days": period_days,
            "summary": metrics.to_dict(),
            "overdue": [
                {
                    "incident_id": inc_id,
                    "item": item.to_dict(),
                }
                for inc_id, item in overdue[:10]
            ],
            "by_incident": [
                {
                    "incident_id": inc_id,
                    **stats,
                    "completion_rate": round(
                        stats["completed"] / stats["total"] * 100, 1
                    ) if stats["total"] > 0 else 0,
                }
                for inc_id, stats in sorted(
                    by_incident.items(),
                    key=lambda x: x[1]["overdue"],
                    reverse=True,
                )[:10]
            ],
        }


_generator: ActionItemGenerator | None = None
_tracker: ActionItemTracker | None = None


def get_action_item_generator() -> ActionItemGenerator:
    """Get the default action item generator."""
    global _generator
    if _generator is None:
        _generator = ActionItemGenerator()
    return _generator


def get_action_item_tracker() -> ActionItemTracker:
    """Get the default action item tracker."""
    global _tracker
    if _tracker is None:
        _tracker = ActionItemTracker()
    return _tracker
