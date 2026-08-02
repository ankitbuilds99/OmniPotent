"""Engineer expertise mapping based on incident resolution.

Maps who knows what based on incident history.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger("omnipotent.learning.expertise")


@dataclass
class ResolutionRecord:
    """Record of an incident resolution."""

    incident_id: str
    engineer: str
    service: str
    root_cause_type: str
    resolution_time_minutes: int
    successful: bool
    resolved_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "incident_id": self.incident_id,
            "engineer": self.engineer,
            "service": self.service,
            "root_cause_type": self.root_cause_type,
            "resolution_time_minutes": self.resolution_time_minutes,
            "successful": self.successful,
            "resolved_at": self.resolved_at.isoformat(),
        }


@dataclass
class ExpertiseArea:
    """An area of expertise."""

    area_type: str
    area_name: str
    incidents_resolved: int
    avg_resolution_time_minutes: float
    success_rate: float
    last_resolution: datetime

    @property
    def expertise_score(self) -> float:
        recency_factor = 1.0
        days_since = (datetime.now(timezone.utc) - self.last_resolution).days
        if days_since > 90:
            recency_factor = 0.7
        elif days_since > 180:
            recency_factor = 0.5

        return (
            self.incidents_resolved * 0.4 +
            self.success_rate * 10 * 0.3 +
            (100 / max(1, self.avg_resolution_time_minutes)) * 0.3
        ) * recency_factor

    def to_dict(self) -> dict[str, Any]:
        return {
            "area_type": self.area_type,
            "area_name": self.area_name,
            "incidents_resolved": self.incidents_resolved,
            "avg_resolution_time_minutes": round(self.avg_resolution_time_minutes, 1),
            "success_rate": round(self.success_rate, 2),
            "expertise_score": round(self.expertise_score, 2),
        }


@dataclass
class EngineerExpertise:
    """Complete expertise profile for an engineer."""

    engineer: str
    total_incidents_resolved: int
    service_expertise: list[ExpertiseArea]
    root_cause_expertise: list[ExpertiseArea]
    overall_success_rate: float
    avg_resolution_time_minutes: float
    is_available: bool = True
    current_load: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "engineer": self.engineer,
            "total_incidents_resolved": self.total_incidents_resolved,
            "service_expertise": [e.to_dict() for e in self.service_expertise],
            "root_cause_expertise": [e.to_dict() for e in self.root_cause_expertise],
            "overall_success_rate": round(self.overall_success_rate, 2),
            "avg_resolution_time_minutes": round(self.avg_resolution_time_minutes, 1),
            "is_available": self.is_available,
            "current_load": self.current_load,
        }


@dataclass
class RoutingRecommendation:
    """Recommendation for who should handle an incident."""

    engineer: str
    score: float
    reason: str
    expertise_match: list[str]
    availability: bool
    estimated_resolution_minutes: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "engineer": self.engineer,
            "score": round(self.score, 2),
            "reason": self.reason,
            "expertise_match": self.expertise_match,
            "availability": self.availability,
            "estimated_resolution_minutes": self.estimated_resolution_minutes,
        }


class ExpertiseMapper:
    """Maps engineer expertise from incident history."""

    def __init__(self) -> None:
        self._resolutions: list[ResolutionRecord] = []
        self._engineer_availability: dict[str, bool] = {}
        self._engineer_load: dict[str, int] = {}

    def record_resolution(
        self,
        incident_id: str,
        engineer: str,
        service: str,
        root_cause_type: str,
        resolution_time_minutes: int,
        successful: bool = True,
    ) -> ResolutionRecord:
        """Record an incident resolution."""
        record = ResolutionRecord(
            incident_id=incident_id,
            engineer=engineer,
            service=service,
            root_cause_type=root_cause_type,
            resolution_time_minutes=resolution_time_minutes,
            successful=successful,
            resolved_at=datetime.now(timezone.utc),
        )

        self._resolutions.append(record)

        log.info(
            "resolution recorded",
            extra={
                "incident_id": incident_id,
                "engineer": engineer,
                "service": service,
                "resolution_time": resolution_time_minutes,
            },
        )

        return record

    def get_engineer_expertise(self, engineer: str) -> EngineerExpertise:
        """Get expertise profile for an engineer."""
        records = [r for r in self._resolutions if r.engineer == engineer]

        if not records:
            return EngineerExpertise(
                engineer=engineer,
                total_incidents_resolved=0,
                service_expertise=[],
                root_cause_expertise=[],
                overall_success_rate=0.0,
                avg_resolution_time_minutes=0.0,
                is_available=self._engineer_availability.get(engineer, True),
                current_load=self._engineer_load.get(engineer, 0),
            )

        service_stats: dict[str, dict[str, Any]] = {}
        cause_stats: dict[str, dict[str, Any]] = {}

        for record in records:
            if record.service not in service_stats:
                service_stats[record.service] = {
                    "count": 0,
                    "success": 0,
                    "time": 0,
                    "last": record.resolved_at,
                }
            service_stats[record.service]["count"] += 1
            service_stats[record.service]["time"] += record.resolution_time_minutes
            if record.successful:
                service_stats[record.service]["success"] += 1
            if record.resolved_at > service_stats[record.service]["last"]:
                service_stats[record.service]["last"] = record.resolved_at

            if record.root_cause_type not in cause_stats:
                cause_stats[record.root_cause_type] = {
                    "count": 0,
                    "success": 0,
                    "time": 0,
                    "last": record.resolved_at,
                }
            cause_stats[record.root_cause_type]["count"] += 1
            cause_stats[record.root_cause_type]["time"] += record.resolution_time_minutes
            if record.successful:
                cause_stats[record.root_cause_type]["success"] += 1
            if record.resolved_at > cause_stats[record.root_cause_type]["last"]:
                cause_stats[record.root_cause_type]["last"] = record.resolved_at

        service_expertise = []
        for service, stats in service_stats.items():
            service_expertise.append(
                ExpertiseArea(
                    area_type="service",
                    area_name=service,
                    incidents_resolved=stats["count"],
                    avg_resolution_time_minutes=stats["time"] / stats["count"],
                    success_rate=stats["success"] / stats["count"],
                    last_resolution=stats["last"],
                )
            )

        cause_expertise = []
        for cause, stats in cause_stats.items():
            cause_expertise.append(
                ExpertiseArea(
                    area_type="root_cause",
                    area_name=cause,
                    incidents_resolved=stats["count"],
                    avg_resolution_time_minutes=stats["time"] / stats["count"],
                    success_rate=stats["success"] / stats["count"],
                    last_resolution=stats["last"],
                )
            )

        service_expertise.sort(key=lambda e: e.expertise_score, reverse=True)
        cause_expertise.sort(key=lambda e: e.expertise_score, reverse=True)

        total_success = sum(1 for r in records if r.successful)
        total_time = sum(r.resolution_time_minutes for r in records)

        return EngineerExpertise(
            engineer=engineer,
            total_incidents_resolved=len(records),
            service_expertise=service_expertise[:5],
            root_cause_expertise=cause_expertise[:5],
            overall_success_rate=total_success / len(records),
            avg_resolution_time_minutes=total_time / len(records),
            is_available=self._engineer_availability.get(engineer, True),
            current_load=self._engineer_load.get(engineer, 0),
        )

    def get_all_engineers(self) -> list[str]:
        """Get all engineers with resolution history."""
        return list(set(r.engineer for r in self._resolutions))

    def recommend_engineer(
        self,
        service: str,
        root_cause_type: str | None = None,
        exclude_engineers: list[str] | None = None,
    ) -> list[RoutingRecommendation]:
        """Recommend engineers to handle an incident."""
        recommendations = []
        exclude = exclude_engineers or []

        all_engineers = self.get_all_engineers()

        for engineer in all_engineers:
            if engineer in exclude:
                continue

            expertise = self.get_engineer_expertise(engineer)
            score = 0.0
            reasons = []
            matches = []

            for exp in expertise.service_expertise:
                if exp.area_name == service:
                    score += exp.expertise_score * 2
                    matches.append(f"{service} expert")
                    reasons.append(
                        f"Resolved {exp.incidents_resolved} incidents in {service}"
                    )

            if root_cause_type:
                for exp in expertise.root_cause_expertise:
                    if exp.area_name == root_cause_type:
                        score += exp.expertise_score
                        matches.append(f"{root_cause_type} experience")
                        reasons.append(
                            f"Handled {exp.incidents_resolved} {root_cause_type} issues"
                        )

            if not expertise.is_available:
                score *= 0.3
                reasons.append("Currently unavailable")

            if expertise.current_load >= 2:
                score *= 0.7
                reasons.append(f"Current load: {expertise.current_load} incidents")

            estimated_time = int(expertise.avg_resolution_time_minutes) if expertise.avg_resolution_time_minutes else 45

            recommendations.append(
                RoutingRecommendation(
                    engineer=engineer,
                    score=score,
                    reason="; ".join(reasons) if reasons else "General experience",
                    expertise_match=matches,
                    availability=expertise.is_available,
                    estimated_resolution_minutes=estimated_time,
                )
            )

        recommendations.sort(key=lambda r: r.score, reverse=True)
        return recommendations[:5]

    def set_availability(self, engineer: str, available: bool) -> None:
        """Set engineer availability."""
        self._engineer_availability[engineer] = available

    def update_load(self, engineer: str, load: int) -> None:
        """Update engineer's current incident load."""
        self._engineer_load[engineer] = load

    def get_expertise_leaderboard(self) -> list[dict[str, Any]]:
        """Get leaderboard of engineers by expertise."""
        engineers = self.get_all_engineers()
        leaderboard: list[dict[str, Any]] = []

        for engineer in engineers:
            expertise = self.get_engineer_expertise(engineer)
            top_services = [e.area_name for e in expertise.service_expertise[:3]]

            leaderboard.append({
                "engineer": engineer,
                "incidents_resolved": expertise.total_incidents_resolved,
                "success_rate": expertise.overall_success_rate,
                "avg_resolution_time": expertise.avg_resolution_time_minutes,
                "top_services": top_services,
            })

        leaderboard.sort(key=lambda x: x["incidents_resolved"], reverse=True)
        return leaderboard

    def get_dashboard_data(self) -> dict[str, Any]:
        """Get data for the expertise dashboard."""
        leaderboard = self.get_expertise_leaderboard()

        total_resolutions = len(self._resolutions)
        successful = sum(1 for r in self._resolutions if r.successful)
        success_rate = successful / total_resolutions if total_resolutions > 0 else 0

        return {
            "total_engineers": len(self.get_all_engineers()),
            "total_resolutions": total_resolutions,
            "overall_success_rate": round(success_rate, 2),
            "leaderboard": leaderboard[:10],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }


_mapper: ExpertiseMapper | None = None


def get_expertise_mapper() -> ExpertiseMapper:
    """Get the default expertise mapper."""
    global _mapper
    if _mapper is None:
        _mapper = ExpertiseMapper()
    return _mapper
