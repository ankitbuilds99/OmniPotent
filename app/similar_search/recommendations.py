"""Resolution recommendations and fix effectiveness tracking."""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from app.similar_search.models import (
    IncidentRecord,
    ResolutionRecommendation,
    RunbookSuggestion,
    FixEffectiveness,
    RootCauseCategory,
)

log = logging.getLogger("devcopilot.similar_search.recommendations")


@dataclass
class FixRecord:
    """Record of a fix application."""

    fix_id: UUID = field(default_factory=uuid4)
    incident_id: UUID = field(default_factory=uuid4)
    fix_type: str = ""
    applied_at: datetime = field(default_factory=datetime.utcnow)
    resolution_time_minutes: int = 0
    successful: bool = True
    recurred: bool = False
    recurrence_date: datetime | None = None
    notes: str = ""


class ResolutionEngine:
    """Generate resolution recommendations from past incidents."""

    def __init__(self):
        self._resolution_cache: dict[str, list[IncidentRecord]] = defaultdict(list)

    def index_incident(self, incident: IncidentRecord) -> None:
        """Index an incident for resolution matching."""
        if not incident.resolution_summary:
            return

        key = self._create_resolution_key(incident)
        self._resolution_cache[key].append(incident)

    def _create_resolution_key(self, incident: IncidentRecord) -> str:
        """Create key for resolution grouping."""
        services = "_".join(sorted(incident.services)[:2])
        root_cause = incident.root_cause_category.value
        return f"{services}:{root_cause}"

    def generate_recommendations(
        self,
        current_incident: IncidentRecord,
        similar_incidents: list[IncidentRecord],
        limit: int = 5,
    ) -> list[ResolutionRecommendation]:
        """Generate resolution recommendations."""
        recommendations = []

        resolution_groups: dict[str, list[IncidentRecord]] = defaultdict(list)
        for inc in similar_incidents:
            if inc.resolution_summary:
                normalized = self._normalize_resolution(inc.resolution_summary)
                resolution_groups[normalized].append(inc)

        for resolution_key, incidents in resolution_groups.items():
            if not incidents:
                continue

            base_incident = incidents[0]
            avg_resolution_time = sum(
                i.duration_minutes for i in incidents
            ) / len(incidents)

            diagnostic_steps = self._extract_diagnostic_steps(incidents)
            fix_steps = self._consolidate_fix_steps(incidents)
            code_example = self._find_code_example(incidents)

            confidence = self._calculate_confidence(
                current_incident, incidents
            )

            recommendation = ResolutionRecommendation(
                recommendation_id=uuid4(),
                title=self._generate_title(base_incident),
                description=base_incident.resolution_summary,
                confidence=confidence,
                based_on_incidents=[i.incident_id for i in incidents],
                diagnostic_steps=diagnostic_steps,
                fix_steps=fix_steps,
                code_example=code_example,
                success_rate=self._estimate_success_rate(incidents),
                avg_resolution_time_minutes=int(avg_resolution_time),
                is_temporary_fix=self._is_temporary_fix(base_incident),
                warning=self._generate_warning(incidents),
            )
            recommendations.append(recommendation)

        recommendations.sort(key=lambda r: r.confidence, reverse=True)
        return recommendations[:limit]

    def _normalize_resolution(self, resolution: str) -> str:
        """Normalize resolution text for grouping."""
        normalized = resolution.lower()
        normalized = re.sub(r"\d+", "N", normalized)
        normalized = re.sub(r"[^\w\s]", "", normalized)
        words = normalized.split()[:10]
        return " ".join(words)

    def _extract_diagnostic_steps(
        self,
        incidents: list[IncidentRecord],
    ) -> list[str]:
        """Extract common diagnostic steps."""
        all_steps: dict[str, int] = defaultdict(int)

        diagnostic_patterns = [
            r"check\s+\w+",
            r"verify\s+\w+",
            r"look\s+at\s+\w+",
            r"examine\s+\w+",
            r"review\s+\w+",
        ]

        for inc in incidents:
            text = f"{inc.description} {inc.resolution_summary}"
            for pattern in diagnostic_patterns:
                matches = re.findall(pattern, text.lower())
                for match in matches:
                    all_steps[match] += 1

        sorted_steps = sorted(all_steps.items(), key=lambda x: x[1], reverse=True)
        return [step.capitalize() for step, _ in sorted_steps[:5]]

    def _consolidate_fix_steps(
        self,
        incidents: list[IncidentRecord],
    ) -> list[str]:
        """Consolidate fix steps from multiple incidents."""
        step_counts: dict[str, int] = defaultdict(int)

        for inc in incidents:
            for step in inc.resolution_steps:
                normalized = step.lower().strip()
                step_counts[normalized] += 1

        sorted_steps = sorted(step_counts.items(), key=lambda x: x[1], reverse=True)
        return [step.capitalize() for step, _ in sorted_steps[:10]]

    def _find_code_example(
        self,
        incidents: list[IncidentRecord],
    ) -> str | None:
        """Find code example from incidents."""
        for inc in incidents:
            if inc.code_changes:
                return f"Files changed: {', '.join(inc.code_changes[:3])}"
        return None

    def _calculate_confidence(
        self,
        current: IncidentRecord,
        similar: list[IncidentRecord],
    ) -> float:
        """Calculate confidence score for recommendation."""
        base_confidence = min(len(similar) * 0.2, 0.6)

        service_overlap = 0
        for inc in similar:
            common = set(current.services) & set(inc.services)
            if common:
                service_overlap += 1

        service_factor = (service_overlap / len(similar)) * 0.2 if similar else 0

        root_cause_match = sum(
            1 for i in similar if i.root_cause_category == current.root_cause_category
        )
        rc_factor = (root_cause_match / len(similar)) * 0.2 if similar else 0

        return min(base_confidence + service_factor + rc_factor, 1.0)

    def _estimate_success_rate(
        self,
        incidents: list[IncidentRecord],
    ) -> float:
        """Estimate success rate of resolution."""
        resolved_count = sum(
            1 for i in incidents if i.status.value in ["resolved", "closed"]
        )
        return resolved_count / len(incidents) if incidents else 0.0

    def _is_temporary_fix(self, incident: IncidentRecord) -> bool:
        """Check if fix is temporary."""
        temp_keywords = ["temporary", "workaround", "hotfix", "quick fix", "band-aid"]
        resolution_lower = incident.resolution_summary.lower()
        return any(kw in resolution_lower for kw in temp_keywords)

    def _generate_warning(
        self,
        incidents: list[IncidentRecord],
    ) -> str | None:
        """Generate warning based on incident history."""
        recurrence_count = 0
        for inc in incidents:
            for item in inc.action_items:
                if item.get("status") not in ["completed", "done"]:
                    recurrence_count += 1
                    break

        if recurrence_count > len(incidents) // 2:
            return "This fix has recurring issues - ensure action items are completed"

        return None

    def _generate_title(self, incident: IncidentRecord) -> str:
        """Generate recommendation title."""
        if incident.root_cause_description:
            return f"Fix for: {incident.root_cause_description[:50]}"
        return f"Resolution from {incident.title[:40]}"


@dataclass
class RunbookEntry:
    """Runbook entry for matching."""

    runbook_id: str
    title: str
    description: str
    url: str | None
    services: list[str]
    root_causes: list[RootCauseCategory]
    error_patterns: list[str]
    steps: list[str]


class RunbookMatcher:
    """Match incidents to runbooks."""

    def __init__(self):
        self._runbooks: dict[str, RunbookEntry] = {}
        self._usage_stats: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"used_count": 0, "success_count": 0, "total_time": 0}
        )

    def register_runbook(self, runbook: RunbookEntry) -> None:
        """Register a runbook for matching."""
        self._runbooks[runbook.runbook_id] = runbook

    def find_matching_runbooks(
        self,
        incident: IncidentRecord,
        limit: int = 5,
    ) -> list[RunbookSuggestion]:
        """Find runbooks matching an incident."""
        scored_runbooks = []

        for runbook in self._runbooks.values():
            score = self._calculate_match_score(incident, runbook)
            if score > 0.3:
                scored_runbooks.append((runbook, score))

        scored_runbooks.sort(key=lambda x: x[1], reverse=True)

        suggestions = []
        for runbook, score in scored_runbooks[:limit]:
            stats = self._usage_stats.get(runbook.runbook_id, {})
            used_count = stats.get("used_count", 0)
            success_count = stats.get("success_count", 0)
            total_time = stats.get("total_time", 0)

            success_rate = success_count / used_count if used_count > 0 else 0.0
            avg_time = total_time // used_count if used_count > 0 else 0

            suggestions.append(
                RunbookSuggestion(
                    runbook_id=runbook.runbook_id,
                    title=runbook.title,
                    description=runbook.description,
                    url=runbook.url,
                    relevance_score=score,
                    success_rate=success_rate,
                    avg_resolution_time_minutes=avg_time,
                    quick_steps=runbook.steps[:5],
                )
            )

        return suggestions

    def _calculate_match_score(
        self,
        incident: IncidentRecord,
        runbook: RunbookEntry,
    ) -> float:
        """Calculate match score between incident and runbook."""
        score = 0.0

        if runbook.services:
            common_services = set(incident.services) & set(runbook.services)
            if common_services:
                score += 0.4 * (len(common_services) / len(runbook.services))

        if incident.root_cause_category in runbook.root_causes:
            score += 0.3

        if runbook.error_patterns:
            error_text = " ".join(incident.error_messages).lower()
            matches = sum(
                1 for p in runbook.error_patterns if p.lower() in error_text
            )
            if matches:
                score += 0.3 * (matches / len(runbook.error_patterns))

        return min(score, 1.0)

    def record_usage(
        self,
        runbook_id: str,
        incident_id: UUID,
        successful: bool,
        resolution_time_minutes: int,
    ) -> None:
        """Record runbook usage for tracking."""
        stats = self._usage_stats[runbook_id]
        stats["used_count"] += 1
        if successful:
            stats["success_count"] += 1
        stats["total_time"] += resolution_time_minutes
        stats["last_used"] = datetime.utcnow().isoformat()
        stats.setdefault("incidents", []).append(str(incident_id))

    def get_runbook_stats(self, runbook_id: str) -> dict[str, Any]:
        """Get usage statistics for a runbook."""
        stats = self._usage_stats.get(runbook_id, {})
        used = stats.get("used_count", 0)
        success = stats.get("success_count", 0)

        return {
            "runbook_id": runbook_id,
            "total_uses": used,
            "success_rate": success / used if used > 0 else 0.0,
            "avg_resolution_time": stats.get("total_time", 0) // used if used > 0 else 0,
            "last_used": stats.get("last_used"),
        }


class FixTracker:
    """Track fix applications and their outcomes."""

    def __init__(self):
        self._fixes: dict[UUID, FixRecord] = {}
        self._incident_fixes: dict[UUID, list[UUID]] = defaultdict(list)
        self._fix_type_stats: dict[str, dict[str, Any]] = defaultdict(
            lambda: {
                "total": 0,
                "successful": 0,
                "recurred": 0,
                "total_time": 0,
            }
        )

    def record_fix(
        self,
        incident_id: UUID,
        fix_type: str,
        resolution_time_minutes: int,
        successful: bool = True,
        notes: str = "",
    ) -> FixRecord:
        """Record a fix application."""
        fix = FixRecord(
            fix_id=uuid4(),
            incident_id=incident_id,
            fix_type=fix_type,
            applied_at=datetime.utcnow(),
            resolution_time_minutes=resolution_time_minutes,
            successful=successful,
            notes=notes,
        )

        self._fixes[fix.fix_id] = fix
        self._incident_fixes[incident_id].append(fix.fix_id)

        stats = self._fix_type_stats[fix_type]
        stats["total"] += 1
        if successful:
            stats["successful"] += 1
        stats["total_time"] += resolution_time_minutes

        return fix

    def record_recurrence(
        self,
        original_incident_id: UUID,
        new_incident_id: UUID,
    ) -> None:
        """Record that an incident recurred."""
        fix_ids = self._incident_fixes.get(original_incident_id, [])
        for fix_id in fix_ids:
            fix = self._fixes.get(fix_id)
            if fix and not fix.recurred:
                fix.recurred = True
                fix.recurrence_date = datetime.utcnow()
                self._fix_type_stats[fix.fix_type]["recurred"] += 1

    def get_fix_history(self, incident_id: UUID) -> list[FixRecord]:
        """Get fix history for an incident."""
        fix_ids = self._incident_fixes.get(incident_id, [])
        return [self._fixes[fid] for fid in fix_ids if fid in self._fixes]

    def get_fix_type_stats(self, fix_type: str) -> dict[str, Any]:
        """Get statistics for a fix type."""
        stats = self._fix_type_stats.get(fix_type, {})
        total = stats.get("total", 0)
        successful = stats.get("successful", 0)
        recurred = stats.get("recurred", 0)
        total_time = stats.get("total_time", 0)

        return {
            "fix_type": fix_type,
            "total_applied": total,
            "success_rate": successful / total if total > 0 else 0.0,
            "recurrence_rate": recurred / successful if successful > 0 else 0.0,
            "avg_resolution_time": total_time // total if total > 0 else 0,
        }

    def get_all_fix_types(self) -> list[str]:
        """Get all recorded fix types."""
        return list(self._fix_type_stats.keys())


class EffectivenessAnalyzer:
    """Analyze fix effectiveness over time."""

    def __init__(self, fix_tracker: FixTracker):
        self._fix_tracker = fix_tracker

    def analyze_fix_type(self, fix_type: str) -> FixEffectiveness:
        """Analyze effectiveness of a fix type."""
        stats = self._fix_tracker.get_fix_type_stats(fix_type)

        total = stats["total_applied"]
        success_rate = stats["success_rate"]
        recurrence_rate = stats["recurrence_rate"]

        is_permanent = recurrence_rate < 0.1

        if recurrence_rate > 0.5:
            recommendation = "Consider replacing with a more permanent solution"
        elif recurrence_rate > 0.2:
            recommendation = "Monitor for recurring issues"
        elif success_rate < 0.7:
            recommendation = "Review fix procedure for improvements"
        else:
            recommendation = "Effective fix type"

        return FixEffectiveness(
            fix_type=fix_type,
            total_applied=total,
            success_count=int(total * success_rate),
            recurrence_count=int(total * success_rate * recurrence_rate),
            is_permanent_fix=is_permanent,
            recommendation=recommendation,
        )

    def rank_fix_types(self) -> list[FixEffectiveness]:
        """Rank all fix types by effectiveness."""
        fix_types = self._fix_tracker.get_all_fix_types()
        effectiveness_list = [self.analyze_fix_type(ft) for ft in fix_types]

        return sorted(
            effectiveness_list,
            key=lambda x: (x.success_rate, -x.recurrence_rate),
            reverse=True,
        )

    def suggest_improvements(
        self,
        root_cause: RootCauseCategory,
    ) -> list[dict[str, Any]]:
        """Suggest improvements based on fix effectiveness."""
        improvements = []

        for fix_type in self._fix_tracker.get_all_fix_types():
            effectiveness = self.analyze_fix_type(fix_type)

            if effectiveness.recurrence_rate > 0.3:
                improvements.append(
                    {
                        "fix_type": fix_type,
                        "issue": "High recurrence rate",
                        "recurrence_rate": effectiveness.recurrence_rate,
                        "suggestion": "Investigate root cause more deeply",
                        "priority": "high" if effectiveness.recurrence_rate > 0.5 else "medium",
                    }
                )

            if effectiveness.success_rate < 0.6:
                improvements.append(
                    {
                        "fix_type": fix_type,
                        "issue": "Low success rate",
                        "success_rate": effectiveness.success_rate,
                        "suggestion": "Review and update fix procedure",
                        "priority": "high",
                    }
                )

        return sorted(
            improvements,
            key=lambda x: 0 if x["priority"] == "high" else 1,
        )

    def generate_report(self) -> dict[str, Any]:
        """Generate effectiveness report."""
        ranked = self.rank_fix_types()

        if not ranked:
            return {"status": "no_data", "message": "No fix data available"}

        top_fixes = ranked[:5]
        worst_fixes = [f for f in ranked if f.success_rate < 0.7][:5]

        total_fixes = sum(f.total_applied for f in ranked)
        avg_success_rate = (
            sum(f.success_rate * f.total_applied for f in ranked) / total_fixes
            if total_fixes > 0
            else 0
        )

        return {
            "summary": {
                "total_fix_types": len(ranked),
                "total_fixes_applied": total_fixes,
                "avg_success_rate": round(avg_success_rate, 2),
            },
            "top_performing": [
                {
                    "fix_type": f.fix_type,
                    "success_rate": round(f.success_rate, 2),
                    "total_applied": f.total_applied,
                }
                for f in top_fixes
            ],
            "needs_improvement": [
                {
                    "fix_type": f.fix_type,
                    "success_rate": round(f.success_rate, 2),
                    "recurrence_rate": round(f.recurrence_rate, 2),
                    "recommendation": f.recommendation,
                }
                for f in worst_fixes
            ],
        }
