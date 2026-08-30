"""Smart incident routing based on expertise and availability.

Routes incidents to the best responder based on:
- Service/technology expertise
- Recent incident history
- Current availability (online status)
- Fatigue levels
- Past resolution success
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from app.config import get_settings

log = logging.getLogger("omnipotent.oncall.routing")


class AvailabilityStatus(Enum):
    """Engineer availability status."""

    ONLINE = "online"
    AWAY = "away"
    BUSY = "busy"
    DND = "dnd"
    OFFLINE = "offline"
    PTO = "pto"


@dataclass
class ExpertiseMatch:
    """Match between an incident and an engineer's expertise."""

    engineer_id: str
    engineer_name: str
    expertise_score: float
    services: list[str]
    technologies: list[str]
    similar_incidents_resolved: int
    avg_resolution_time_minutes: float
    last_similar_incident: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "engineer_id": self.engineer_id,
            "engineer_name": self.engineer_name,
            "expertise_score": round(self.expertise_score, 2),
            "services": self.services,
            "technologies": self.technologies,
            "similar_incidents_resolved": self.similar_incidents_resolved,
            "avg_resolution_time_minutes": round(self.avg_resolution_time_minutes, 1),
            "last_similar_incident": self.last_similar_incident.isoformat() if self.last_similar_incident else None,
        }


@dataclass
class EngineerProfile:
    """Profile of an engineer's expertise and availability."""

    engineer_id: str
    name: str
    email: str
    services: list[str] = field(default_factory=list)
    technologies: list[str] = field(default_factory=list)
    incident_history: list[dict[str, Any]] = field(default_factory=list)
    availability: AvailabilityStatus = AvailabilityStatus.OFFLINE
    last_seen: datetime | None = None
    fatigue_score: float = 0.0
    is_on_call: bool = False
    timezone: str = "UTC"

    def expertise_for_service(self, service: str) -> float:
        """Calculate expertise score for a service."""
        if service in self.services:
            relevant = [
                i for i in self.incident_history
                if i.get("service") == service and i.get("resolved", False)
            ]
            base_score = 0.5
            experience_bonus = min(len(relevant) * 0.1, 0.4)
            return base_score + experience_bonus
        return 0.0

    def expertise_for_technology(self, technology: str) -> float:
        """Calculate expertise score for a technology."""
        if technology.lower() in [t.lower() for t in self.technologies]:
            return 0.6
        return 0.0


@dataclass
class IncidentRouting:
    """Incident details for routing."""

    incident_id: str
    title: str
    service: str
    severity: str
    error_signature: str | None = None
    affected_technologies: list[str] = field(default_factory=list)
    detected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    context: dict[str, Any] = field(default_factory=dict)


@dataclass
class RoutingDecision:
    """Decision on how to route an incident.

    `primary_responder` is None when nobody is registered with the router —
    a clear no-responders answer, not an exception and not an invented one.
    """

    incident_id: str
    primary_responder: ExpertiseMatch | None
    backup_responders: list[ExpertiseMatch]
    reason: str
    confidence: float
    routing_factors: dict[str, float]
    alternatives: list[ExpertiseMatch] = field(default_factory=list)
    decided_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "incident_id": self.incident_id,
            "primary_responder": (
                self.primary_responder.to_dict() if self.primary_responder else None
            ),
            "backup_responders": [r.to_dict() for r in self.backup_responders],
            "reason": self.reason,
            "confidence": round(self.confidence, 2),
            "routing_factors": {k: round(v, 2) for k, v in self.routing_factors.items()},
            "alternatives": [a.to_dict() for a in self.alternatives],
            "decided_at": self.decided_at.isoformat(),
        }


class SmartRouter:
    """AI-powered incident routing engine."""

    def __init__(
        self,
        expertise_weight: float = 0.4,
        availability_weight: float = 0.25,
        fatigue_weight: float = 0.2,
        recency_weight: float = 0.15,
    ):
        self.expertise_weight = expertise_weight
        self.availability_weight = availability_weight
        self.fatigue_weight = fatigue_weight
        self.recency_weight = recency_weight

        self._engineers: dict[str, EngineerProfile] = {}
        self._routing_history: list[RoutingDecision] = []
        # The four fictional profiles below ("alice@company.com" and friends)
        # used to be seeded unconditionally into a router that assigns real
        # incidents. Outside demo mode the router starts empty and engineers
        # come from register_engineer(); with nobody registered, route()
        # returns a no-responders decision rather than inventing one.
        if get_settings().copilot_demo_mode:
            log.warning(
                "COPILOT_DEMO_MODE=true - seeding the smart router with "
                "fictional demo engineers"
            )
            self._setup_demo_engineers()

    def _setup_demo_engineers(self) -> None:
        """Set up FICTIONAL demo engineer profiles (COPILOT_DEMO_MODE only)."""
        now = datetime.now(timezone.utc)

        engineers = [
            EngineerProfile(
                engineer_id="alice",
                name="Alice Chen",
                email="alice@company.com",
                services=["checkout-api", "payment-service", "api-gateway"],
                technologies=["Python", "PostgreSQL", "Redis", "Kubernetes"],
                incident_history=[
                    {"service": "checkout-api", "resolved": True, "resolution_time_minutes": 45},
                    {"service": "api-gateway", "resolved": True, "resolution_time_minutes": 30},
                    {"service": "checkout-api", "resolved": True, "resolution_time_minutes": 25},
                ],
                availability=AvailabilityStatus.ONLINE,
                last_seen=now,
                fatigue_score=3.2,
                is_on_call=True,
            ),
            EngineerProfile(
                engineer_id="dave",
                name="Dave Martinez",
                email="dave@company.com",
                services=["user-service", "billing-db", "payment-service"],
                technologies=["PostgreSQL", "MySQL", "Elasticsearch", "Python"],
                incident_history=[
                    {"service": "billing-db", "resolved": True, "resolution_time_minutes": 20},
                    {"service": "billing-db", "resolved": True, "resolution_time_minutes": 15},
                    {"service": "user-service", "resolved": True, "resolution_time_minutes": 35},
                    {"service": "billing-db", "resolved": True, "resolution_time_minutes": 18},
                ],
                availability=AvailabilityStatus.ONLINE,
                last_seen=now,
                fatigue_score=2.1,
                is_on_call=False,
            ),
            EngineerProfile(
                engineer_id="carol",
                name="Carol Wilson",
                email="carol@company.com",
                services=["billing-service", "checkout-api", "notification-service"],
                technologies=["Python", "Kafka", "Redis", "AWS"],
                incident_history=[
                    {"service": "billing-service", "resolved": True, "resolution_time_minutes": 40},
                    {"service": "checkout-api", "resolved": True, "resolution_time_minutes": 55},
                ],
                availability=AvailabilityStatus.PTO,
                last_seen=now - timedelta(days=1),
                fatigue_score=0.0,
                is_on_call=False,
            ),
            EngineerProfile(
                engineer_id="bob",
                name="Bob Johnson",
                email="bob@company.com",
                services=["frontend", "api-gateway", "cdn"],
                technologies=["JavaScript", "React", "Node.js", "Cloudflare"],
                incident_history=[
                    {"service": "frontend", "resolved": True, "resolution_time_minutes": 30},
                    {"service": "cdn", "resolved": True, "resolution_time_minutes": 25},
                ],
                availability=AvailabilityStatus.ONLINE,
                last_seen=now,
                fatigue_score=5.5,
                is_on_call=False,
            ),
        ]

        for eng in engineers:
            self._engineers[eng.engineer_id] = eng

    def register_engineer(self, profile: EngineerProfile) -> None:
        """Register or update an engineer profile."""
        self._engineers[profile.engineer_id] = profile

    def update_availability(
        self,
        engineer_id: str,
        status: AvailabilityStatus,
    ) -> bool:
        """Update an engineer's availability."""
        eng = self._engineers.get(engineer_id)
        if eng:
            eng.availability = status
            eng.last_seen = datetime.now(timezone.utc)
            return True
        return False

    def update_fatigue(self, engineer_id: str, score: float) -> bool:
        """Update an engineer's fatigue score."""
        eng = self._engineers.get(engineer_id)
        if eng:
            eng.fatigue_score = score
            return True
        return False

    def route(self, incident: IncidentRouting) -> RoutingDecision:
        """Route an incident to the best responder."""
        candidates = self._find_candidates(incident)

        if not candidates:
            on_call = next(
                (e for e in self._engineers.values() if e.is_on_call),
                next(iter(self._engineers.values()), None),
            )
            if not on_call:
                # Nobody is registered: say so instead of raising or inventing
                # a responder. Not recorded in history — there is no assignment
                # to account for.
                log.warning(
                    "No engineers registered with the smart router; incident %s "
                    "has no responder to route to",
                    incident.incident_id,
                )
                return RoutingDecision(
                    incident_id=incident.incident_id,
                    primary_responder=None,
                    backup_responders=[],
                    reason=(
                        "No engineers are registered with the router; register "
                        "engineer profiles or escalate this incident manually"
                    ),
                    confidence=0.0,
                    routing_factors={},
                )

            match = ExpertiseMatch(
                engineer_id=on_call.engineer_id,
                engineer_name=on_call.name,
                expertise_score=0.0,
                services=[],
                technologies=[],
                similar_incidents_resolved=0,
                avg_resolution_time_minutes=60.0,
            )

            return RoutingDecision(
                incident_id=incident.incident_id,
                primary_responder=match,
                backup_responders=[],
                reason="Fallback to on-call (no expertise match)",
                confidence=0.3,
                routing_factors={},
            )

        scored = self._score_candidates(candidates, incident)
        scored.sort(key=lambda x: x[1], reverse=True)

        primary = scored[0][0]
        backups = [s[0] for s in scored[1:3]]
        alternatives = [s[0] for s in scored[3:6]]

        confidence = scored[0][1]
        factors = scored[0][2]

        reason = self._generate_reason(primary, incident, factors)

        decision = RoutingDecision(
            incident_id=incident.incident_id,
            primary_responder=primary,
            backup_responders=backups,
            reason=reason,
            confidence=confidence,
            routing_factors=factors,
            alternatives=alternatives,
        )

        self._routing_history.append(decision)

        log.info(
            "incident routed",
            extra={
                "incident_id": incident.incident_id,
                "primary": primary.engineer_id,
                "confidence": confidence,
            },
        )

        return decision

    def _find_candidates(self, incident: IncidentRouting) -> list[EngineerProfile]:
        """Find engineers who could handle this incident."""
        candidates = []

        for eng in self._engineers.values():
            if eng.availability in (AvailabilityStatus.PTO, AvailabilityStatus.DND):
                continue

            has_service = incident.service in eng.services
            has_tech = any(
                t.lower() in [e.lower() for e in eng.technologies]
                for t in incident.affected_technologies
            )

            if has_service or has_tech or eng.is_on_call:
                candidates.append(eng)

        return candidates

    def _score_candidates(
        self,
        candidates: list[EngineerProfile],
        incident: IncidentRouting,
    ) -> list[tuple[ExpertiseMatch, float, dict[str, float]]]:
        """Score candidates for routing."""
        results = []

        for eng in candidates:
            expertise_score = eng.expertise_for_service(incident.service)
            for tech in incident.affected_technologies:
                expertise_score = max(expertise_score, eng.expertise_for_technology(tech))

            similar = [
                i for i in eng.incident_history
                if i.get("service") == incident.service and i.get("resolved", False)
            ]
            if similar:
                avg_time = sum(i.get("resolution_time_minutes", 60) for i in similar) / len(similar)
                last_similar = datetime.now(timezone.utc) - timedelta(days=len(similar) * 7)
            else:
                avg_time = 60.0
                last_similar = None

            availability_score = {
                AvailabilityStatus.ONLINE: 1.0,
                AvailabilityStatus.AWAY: 0.6,
                AvailabilityStatus.BUSY: 0.4,
                AvailabilityStatus.OFFLINE: 0.2,
            }.get(eng.availability, 0.0)

            fatigue_score = max(0, 1.0 - (eng.fatigue_score / 10.0))

            recency_score = 0.5
            if similar:
                recency_score = 0.8

            factors = {
                "expertise": expertise_score,
                "availability": availability_score,
                "fatigue": fatigue_score,
                "recency": recency_score,
            }

            total_score = (
                expertise_score * self.expertise_weight
                + availability_score * self.availability_weight
                + fatigue_score * self.fatigue_weight
                + recency_score * self.recency_weight
            )

            match = ExpertiseMatch(
                engineer_id=eng.engineer_id,
                engineer_name=eng.name,
                expertise_score=expertise_score,
                services=[s for s in eng.services if s == incident.service],
                technologies=[t for t in eng.technologies if t.lower() in [x.lower() for x in incident.affected_technologies]],
                similar_incidents_resolved=len(similar),
                avg_resolution_time_minutes=avg_time,
                last_similar_incident=last_similar,
            )

            results.append((match, total_score, factors))

        return results

    def _generate_reason(
        self,
        match: ExpertiseMatch,
        incident: IncidentRouting,
        factors: dict[str, float],
    ) -> str:
        """Generate explanation for routing decision."""
        parts = []

        if match.expertise_score > 0.6:
            parts.append(f"{incident.service} expert")
        elif match.similar_incidents_resolved > 0:
            parts.append(f"resolved {match.similar_incidents_resolved} similar incidents")

        if factors.get("fatigue", 0) > 0.7:
            parts.append("low fatigue")

        if factors.get("availability", 0) == 1.0:
            parts.append("currently online")

        if not parts:
            parts.append("best available match")

        return " + ".join(parts)

    def get_routing_stats(self) -> dict[str, Any]:
        """Get routing statistics."""
        by_engineer: dict[str, int] = {}
        avg_confidence = 0.0

        for decision in self._routing_history:
            # No-responder decisions never enter the history, but the field is
            # Optional, so guard rather than assume.
            if decision.primary_responder is not None:
                eng_id = decision.primary_responder.engineer_id
                by_engineer[eng_id] = by_engineer.get(eng_id, 0) + 1
            avg_confidence += decision.confidence

        if self._routing_history:
            avg_confidence /= len(self._routing_history)

        return {
            "total_routings": len(self._routing_history),
            "avg_confidence": round(avg_confidence, 2),
            "by_engineer": by_engineer,
            "recent_decisions": [d.to_dict() for d in self._routing_history[-5:]],
        }

    def override_routing(
        self,
        incident_id: str,
        new_responder_id: str,
        reason: str,
    ) -> RoutingDecision | None:
        """Override automatic routing with manual selection."""
        eng = self._engineers.get(new_responder_id)
        if not eng:
            return None

        match = ExpertiseMatch(
            engineer_id=eng.engineer_id,
            engineer_name=eng.name,
            expertise_score=0.0,
            services=eng.services,
            technologies=eng.technologies,
            similar_incidents_resolved=len(eng.incident_history),
            avg_resolution_time_minutes=45.0,
        )

        decision = RoutingDecision(
            incident_id=incident_id,
            primary_responder=match,
            backup_responders=[],
            reason=f"Manual override: {reason}",
            confidence=1.0,
            routing_factors={"manual": 1.0},
        )

        self._routing_history.append(decision)
        return decision


_router: SmartRouter | None = None


def get_smart_router() -> SmartRouter:
    """Get the default smart router."""
    global _router
    if _router is None:
        _router = SmartRouter()
    return _router
