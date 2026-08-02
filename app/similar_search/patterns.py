"""Pattern detection and trend analysis for recurring incidents."""

from __future__ import annotations

import logging
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from app.similar_search.models import (
    IncidentRecord,
    RecurringPattern,
    PatternOccurrence,
    RootCauseCategory,
    IncidentSeverity,
)

log = logging.getLogger("omnipotent.similar_search.patterns")


@dataclass
class PatternSignature:
    """Signature for grouping similar incidents into patterns."""

    services_hash: str
    error_pattern: str
    root_cause: RootCauseCategory
    alert_types: frozenset[str]

    @classmethod
    def from_incident(cls, incident: IncidentRecord) -> PatternSignature:
        """Create signature from incident."""
        services_hash = "_".join(sorted(incident.services)[:3])

        error_pattern = ""
        if incident.error_messages:
            first_error = incident.error_messages[0].lower()
            if "timeout" in first_error:
                error_pattern = "timeout"
            elif "connection" in first_error:
                error_pattern = "connection"
            elif "memory" in first_error or "oom" in first_error:
                error_pattern = "memory"
            elif "cpu" in first_error:
                error_pattern = "cpu"
            elif "5xx" in first_error or "500" in first_error:
                error_pattern = "server_error"
            else:
                error_pattern = "other"

        alert_types = frozenset(a.split(":")[0].lower() for a in incident.alerts[:5])

        return cls(
            services_hash=services_hash,
            error_pattern=error_pattern,
            root_cause=incident.root_cause_category,
            alert_types=alert_types,
        )

    def to_key(self) -> str:
        """Convert to hashable key."""
        return f"{self.services_hash}|{self.error_pattern}|{self.root_cause.value}"


class PatternDetector:
    """Detect recurring incident patterns."""

    def __init__(
        self,
        min_occurrences: int = 3,
        similarity_threshold: float = 0.7,
        lookback_days: int = 90,
    ):
        self._min_occurrences = min_occurrences
        self._similarity_threshold = similarity_threshold
        self._lookback_days = lookback_days
        self._patterns: dict[str, RecurringPattern] = {}
        self._incident_to_pattern: dict[UUID, str] = {}

    def detect_patterns(
        self,
        incidents: list[IncidentRecord],
    ) -> list[RecurringPattern]:
        """Detect recurring patterns in incidents."""
        cutoff = datetime.utcnow() - timedelta(days=self._lookback_days)
        recent = [i for i in incidents if i.created_at >= cutoff]

        signature_groups: dict[str, list[IncidentRecord]] = defaultdict(list)
        for incident in recent:
            sig = PatternSignature.from_incident(incident)
            signature_groups[sig.to_key()].append(incident)

        patterns = []
        for key, group in signature_groups.items():
            if len(group) >= self._min_occurrences:
                pattern = self._create_pattern(key, group)
                patterns.append(pattern)
                self._patterns[str(pattern.pattern_id)] = pattern
                for incident in group:
                    self._incident_to_pattern[incident.incident_id] = str(
                        pattern.pattern_id
                    )

        patterns.sort(key=lambda p: p.occurrence_count, reverse=True)
        return patterns

    def _create_pattern(
        self,
        key: str,
        incidents: list[IncidentRecord],
    ) -> RecurringPattern:
        """Create a pattern from a group of incidents."""
        services = set()
        root_causes: dict[str, int] = defaultdict(int)
        action_items = []

        occurrences = []
        for inc in incidents:
            services.update(inc.services)
            root_causes[inc.root_cause_category.value] += 1

            for item in inc.action_items:
                if item.get("status") not in ["completed", "done"]:
                    action_items.append(item)

            occurrences.append(
                PatternOccurrence(
                    incident_id=inc.incident_id,
                    occurred_at=inc.created_at,
                    service=inc.services[0] if inc.services else "unknown",
                    root_cause=inc.root_cause_category.value,
                    duration_minutes=inc.duration_minutes,
                    resolved=inc.status.value in ["resolved", "closed"],
                )
            )

        parts = key.split("|")
        name = f"{parts[0]}: {parts[1]} ({parts[2]})" if len(parts) >= 3 else key

        first_seen = min(inc.created_at for inc in incidents)
        last_seen = max(inc.created_at for inc in incidents)

        return RecurringPattern(
            pattern_id=uuid4(),
            name=name,
            description=f"Recurring pattern affecting {', '.join(list(services)[:3])}",
            occurrences=occurrences,
            services_affected=list(services),
            root_causes=dict(root_causes),
            trend=self._calculate_trend(occurrences),
            unfulfilled_action_items=action_items[:10],
            recommendation=self._generate_recommendation(root_causes, action_items),
            first_seen=first_seen,
            last_seen=last_seen,
        )

    def _calculate_trend(self, occurrences: list[PatternOccurrence]) -> str:
        """Calculate trend from occurrence timestamps."""
        if len(occurrences) < 3:
            return "insufficient_data"

        sorted_occs = sorted(occurrences, key=lambda o: o.occurred_at)
        midpoint = len(sorted_occs) // 2

        first_half = sorted_occs[:midpoint]
        second_half = sorted_occs[midpoint:]

        if not first_half or not second_half:
            return "stable"

        first_span = (first_half[-1].occurred_at - first_half[0].occurred_at).days
        second_span = (second_half[-1].occurred_at - second_half[0].occurred_at).days

        if first_span == 0 or second_span == 0:
            return "stable"

        first_rate = len(first_half) / max(first_span, 1)
        second_rate = len(second_half) / max(second_span, 1)

        if second_rate > first_rate * 1.5:
            return "increasing"
        elif second_rate < first_rate * 0.5:
            return "decreasing"
        else:
            return "stable"

    def _generate_recommendation(
        self,
        root_causes: dict[str, int],
        action_items: list[dict[str, Any]],
    ) -> str:
        """Generate recommendation based on pattern analysis."""
        top_cause = max(root_causes.items(), key=lambda x: x[1])[0]

        if action_items:
            return (
                f"Complete {len(action_items)} outstanding action items. "
                f"Primary root cause: {top_cause}"
            )

        recommendations = {
            RootCauseCategory.CAPACITY.value: "Consider capacity planning and auto-scaling",
            RootCauseCategory.CONFIGURATION.value: "Review configuration management and validation",
            RootCauseCategory.CODE_DEFECT.value: "Increase test coverage and code review rigor",
            RootCauseCategory.INFRASTRUCTURE.value: "Review infrastructure reliability and redundancy",
            RootCauseCategory.EXTERNAL_DEPENDENCY.value: "Add circuit breakers and fallback mechanisms",
            RootCauseCategory.HUMAN_ERROR.value: "Improve runbooks and automation",
        }

        return recommendations.get(
            top_cause, f"Investigate recurring {top_cause} issues"
        )

    def get_pattern_for_incident(
        self,
        incident_id: UUID,
    ) -> RecurringPattern | None:
        """Get pattern associated with an incident."""
        pattern_id = self._incident_to_pattern.get(incident_id)
        if pattern_id:
            return self._patterns.get(pattern_id)
        return None

    def match_to_pattern(
        self,
        incident: IncidentRecord,
    ) -> RecurringPattern | None:
        """Match a new incident to existing patterns."""
        sig = PatternSignature.from_incident(incident)
        key = sig.to_key()

        for pattern in self._patterns.values():
            for occ in pattern.occurrences:
                if key in str(pattern.name):
                    return pattern

        return None


class TrendAnalyzer:
    """Analyze incident trends over time."""

    def __init__(self, window_days: int = 30):
        self._window_days = window_days

    def analyze_trends(
        self,
        incidents: list[IncidentRecord],
    ) -> dict[str, Any]:
        """Analyze overall incident trends."""
        now = datetime.utcnow()
        current_window = now - timedelta(days=self._window_days)
        previous_window = current_window - timedelta(days=self._window_days)

        current = [i for i in incidents if i.created_at >= current_window]
        previous = [
            i
            for i in incidents
            if previous_window <= i.created_at < current_window
        ]

        current_count = len(current)
        previous_count = len(previous)

        if previous_count == 0:
            count_change = 100.0 if current_count > 0 else 0.0
        else:
            count_change = ((current_count - previous_count) / previous_count) * 100

        current_mttr = self._calculate_mttr(current)
        previous_mttr = self._calculate_mttr(previous)

        if previous_mttr == 0:
            mttr_change = 0.0
        else:
            mttr_change = ((current_mttr - previous_mttr) / previous_mttr) * 100

        return {
            "period_days": self._window_days,
            "current_period": {
                "incident_count": current_count,
                "mttr_minutes": current_mttr,
                "by_severity": self._count_by_severity(current),
                "by_root_cause": self._count_by_root_cause(current),
                "top_services": self._top_services(current),
            },
            "previous_period": {
                "incident_count": previous_count,
                "mttr_minutes": previous_mttr,
            },
            "trends": {
                "incident_count_change_pct": round(count_change, 1),
                "mttr_change_pct": round(mttr_change, 1),
                "direction": "improving"
                if count_change < 0 and mttr_change < 0
                else "worsening"
                if count_change > 0 and mttr_change > 0
                else "mixed",
            },
        }

    def _calculate_mttr(self, incidents: list[IncidentRecord]) -> float:
        """Calculate mean time to resolve."""
        durations = [i.duration_minutes for i in incidents if i.duration_minutes > 0]
        return statistics.mean(durations) if durations else 0.0

    def _count_by_severity(
        self,
        incidents: list[IncidentRecord],
    ) -> dict[str, int]:
        """Count incidents by severity."""
        counts: dict[str, int] = defaultdict(int)
        for inc in incidents:
            counts[inc.severity.value] += 1
        return dict(counts)

    def _count_by_root_cause(
        self,
        incidents: list[IncidentRecord],
    ) -> dict[str, int]:
        """Count incidents by root cause."""
        counts: dict[str, int] = defaultdict(int)
        for inc in incidents:
            counts[inc.root_cause_category.value] += 1
        return dict(counts)

    def _top_services(
        self,
        incidents: list[IncidentRecord],
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Get top affected services."""
        counts: dict[str, int] = defaultdict(int)
        for inc in incidents:
            for svc in inc.services:
                counts[svc] += 1

        sorted_services = sorted(counts.items(), key=lambda x: x[1], reverse=True)
        return [
            {"service": s, "incident_count": c} for s, c in sorted_services[:limit]
        ]

    def detect_anomalies(
        self,
        incidents: list[IncidentRecord],
        service: str | None = None,
    ) -> list[dict[str, Any]]:
        """Detect anomalous incident patterns."""
        filtered = incidents
        if service:
            filtered = [i for i in incidents if service in i.services]

        if len(filtered) < 10:
            return []

        by_day: dict[str, int] = defaultdict(int)
        for inc in filtered:
            day = inc.created_at.strftime("%Y-%m-%d")
            by_day[day] += 1

        values = list(by_day.values())
        if len(values) < 5:
            return []

        mean = statistics.mean(values)
        stdev = statistics.stdev(values) if len(values) > 1 else 0

        anomalies = []
        threshold = mean + (2 * stdev)

        for day, count in by_day.items():
            if count > threshold:
                anomalies.append(
                    {
                        "date": day,
                        "incident_count": count,
                        "expected": round(mean, 1),
                        "deviation": round((count - mean) / stdev, 1)
                        if stdev > 0
                        else 0,
                    }
                )

        return sorted(anomalies, key=lambda x: x["deviation"], reverse=True)


class RecurrenceTracker:
    """Track incident recurrence and time between incidents."""

    def __init__(self, max_gap_days: int = 30):
        self._max_gap_days = max_gap_days

    def track_recurrence(
        self,
        incidents: list[IncidentRecord],
    ) -> dict[str, Any]:
        """Track recurrence metrics."""
        by_service: dict[str, list[IncidentRecord]] = defaultdict(list)
        for inc in incidents:
            for svc in inc.services:
                by_service[svc].append(inc)

        results: list[dict[str, Any]] = []
        for service, service_incidents in by_service.items():
            if len(service_incidents) < 2:
                continue

            sorted_incs = sorted(service_incidents, key=lambda i: i.created_at)
            gaps: list[float] = []

            for i in range(1, len(sorted_incs)):
                gap = (
                    sorted_incs[i].created_at - sorted_incs[i - 1].created_at
                ).total_seconds() / 3600

                if gap <= self._max_gap_days * 24:
                    gaps.append(gap)

            if gaps:
                recurrence_count = len(gaps)
                results.append(
                    {
                        "service": service,
                        "incident_count": len(service_incidents),
                        "recurrence_count": recurrence_count,
                        "avg_gap_hours": round(statistics.mean(gaps), 1),
                        "min_gap_hours": round(min(gaps), 1),
                        "max_gap_hours": round(max(gaps), 1),
                    }
                )

        results.sort(key=lambda x: x.get("recurrence_count", 0), reverse=True)  # type: ignore[return-value]
        return {
            "services_with_recurrence": len(results),
            "details": results[:20],
        }

    def find_rapid_recurrence(
        self,
        incidents: list[IncidentRecord],
        threshold_hours: float = 24,
    ) -> list[dict[str, Any]]:
        """Find incidents that recurred rapidly."""
        by_signature: dict[str, list[IncidentRecord]] = defaultdict(list)
        for inc in incidents:
            sig = PatternSignature.from_incident(inc)
            by_signature[sig.to_key()].append(inc)

        rapid = []
        for key, group in by_signature.items():
            if len(group) < 2:
                continue

            sorted_group = sorted(group, key=lambda i: i.created_at)
            for i in range(1, len(sorted_group)):
                gap_hours = (
                    sorted_group[i].created_at - sorted_group[i - 1].created_at
                ).total_seconds() / 3600

                if gap_hours <= threshold_hours:
                    rapid.append(
                        {
                            "pattern_key": key,
                            "first_incident_id": str(sorted_group[i - 1].incident_id),
                            "second_incident_id": str(sorted_group[i].incident_id),
                            "gap_hours": round(gap_hours, 1),
                            "first_at": sorted_group[i - 1].created_at.isoformat(),
                            "second_at": sorted_group[i].created_at.isoformat(),
                        }
                    )

        return sorted(rapid, key=lambda x: x["gap_hours"])


@dataclass
class CostConfig:
    """Configuration for incident cost calculation."""

    hourly_engineer_cost: float = 150.0
    hourly_revenue_impact: float = 10000.0
    p1_multiplier: float = 4.0
    p2_multiplier: float = 2.0
    p3_multiplier: float = 1.0
    p4_multiplier: float = 0.5
    customer_impact_multiplier: float = 1.5
    reputation_cost_per_incident: float = 5000.0


class CostCalculator:
    """Calculate incident costs for patterns."""

    def __init__(self, config: CostConfig | None = None):
        self._config = config or CostConfig()

    def calculate_incident_cost(self, incident: IncidentRecord) -> dict[str, float]:
        """Calculate cost for a single incident."""
        severity_multipliers = {
            IncidentSeverity.P1: self._config.p1_multiplier,
            IncidentSeverity.P2: self._config.p2_multiplier,
            IncidentSeverity.P3: self._config.p3_multiplier,
            IncidentSeverity.P4: self._config.p4_multiplier,
        }

        duration_hours = incident.duration_minutes / 60

        responder_count = max(len(incident.responders), 1)
        engineer_cost = (
            duration_hours * self._config.hourly_engineer_cost * responder_count
        )

        multiplier = severity_multipliers.get(incident.severity, 1.0)
        revenue_cost = duration_hours * self._config.hourly_revenue_impact * multiplier

        has_customer_impact = bool(incident.customer_impact)
        if has_customer_impact:
            revenue_cost *= self._config.customer_impact_multiplier

        reputation_cost = 0.0
        if incident.severity in [IncidentSeverity.P1, IncidentSeverity.P2]:
            reputation_cost = self._config.reputation_cost_per_incident * multiplier

        total = engineer_cost + revenue_cost + reputation_cost

        return {
            "engineer_cost": round(engineer_cost, 2),
            "revenue_cost": round(revenue_cost, 2),
            "reputation_cost": round(reputation_cost, 2),
            "total_cost": round(total, 2),
        }

    def calculate_pattern_cost(self, pattern: RecurringPattern) -> dict[str, Any]:
        """Calculate total cost for a recurring pattern."""
        occurrence_costs = []

        for occ in pattern.occurrences:
            duration_hours = occ.duration_minutes / 60
            base_cost = duration_hours * self._config.hourly_engineer_cost
            base_cost += duration_hours * self._config.hourly_revenue_impact
            occurrence_costs.append(base_cost)

        total_cost = sum(occurrence_costs)
        avg_cost = total_cost / len(occurrence_costs) if occurrence_costs else 0

        projected_annual = 0.0
        if len(pattern.occurrences) >= 2:
            time_span = (pattern.last_seen - pattern.first_seen).days
            if time_span > 0:
                rate_per_day = len(pattern.occurrences) / time_span
                projected_annual = rate_per_day * 365 * avg_cost

        pattern.estimated_cost = total_cost

        return {
            "pattern_id": str(pattern.pattern_id),
            "pattern_name": pattern.name,
            "occurrence_count": len(pattern.occurrences),
            "total_historical_cost": round(total_cost, 2),
            "avg_cost_per_occurrence": round(avg_cost, 2),
            "projected_annual_cost": round(projected_annual, 2),
            "trend": pattern.trend,
            "roi_of_fixing": self._calculate_fix_roi(total_cost, projected_annual),
        }

    def _calculate_fix_roi(
        self,
        historical_cost: float,
        projected_annual: float,
    ) -> dict[str, Any]:
        """Calculate ROI of fixing the pattern."""
        estimated_fix_effort_hours = 40
        fix_cost = estimated_fix_effort_hours * self._config.hourly_engineer_cost

        annual_savings = projected_annual
        roi_pct = ((annual_savings - fix_cost) / fix_cost * 100) if fix_cost else 0
        payback_months = (fix_cost / (annual_savings / 12)) if annual_savings > 0 else 0

        return {
            "estimated_fix_cost": round(fix_cost, 2),
            "annual_savings": round(annual_savings, 2),
            "roi_percentage": round(roi_pct, 1),
            "payback_months": round(payback_months, 1),
            "recommendation": "Fix immediately"
            if payback_months < 3
            else "Consider fixing"
            if payback_months < 12
            else "Low priority",
        }

    def rank_patterns_by_cost(
        self,
        patterns: list[RecurringPattern],
    ) -> list[dict[str, Any]]:
        """Rank patterns by cost impact."""
        costs = []
        for pattern in patterns:
            cost_data = self.calculate_pattern_cost(pattern)
            costs.append(cost_data)

        return sorted(costs, key=lambda x: x["projected_annual_cost"], reverse=True)
