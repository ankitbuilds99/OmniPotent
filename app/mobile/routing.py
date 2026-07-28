"""Smart Notification Routing.

Routes notifications based on expertise, availability, and quiet hours.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, time, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("devcopilot.mobile.routing")


class RoutingReason(str, Enum):
    SCHEDULED_ONCALL = "scheduled_oncall"
    EXPERTISE_MATCH = "expertise_match"
    LOW_FATIGUE = "low_fatigue"
    PREVIOUS_RESOLVER = "previous_resolver"
    AVAILABLE = "available"
    BACKUP = "backup"


@dataclass
class QuietHoursConfig:
    """User's quiet hours configuration."""

    user_id: str
    enabled: bool = True
    start_time: time = field(default_factory=lambda: time(23, 0))
    end_time: time = field(default_factory=lambda: time(7, 0))
    sev1_override: bool = True
    sev2_vibrate_only: bool = True
    sev3_silent: bool = True
    timezone: str = "UTC"

    def is_quiet_hours(self, check_time: datetime) -> bool:
        """Check if given time is within quiet hours."""
        if not self.enabled:
            return False

        current_time = check_time.time()

        if self.start_time > self.end_time:
            return current_time >= self.start_time or current_time <= self.end_time
        else:
            return self.start_time <= current_time <= self.end_time

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "enabled": self.enabled,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat(),
            "sev1_override": self.sev1_override,
            "sev2_vibrate_only": self.sev2_vibrate_only,
            "sev3_silent": self.sev3_silent,
        }


@dataclass
class ResponderProfile:
    """Profile of a potential responder."""

    user_id: str
    expertise_areas: list[str] = field(default_factory=list)
    resolved_incidents_week: int = 0
    is_scheduled_oncall: bool = False
    is_available: bool = True
    quiet_hours: QuietHoursConfig | None = None
    fatigue_score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "expertise": self.expertise_areas,
            "incidents_this_week": self.resolved_incidents_week,
            "is_oncall": self.is_scheduled_oncall,
            "available": self.is_available,
            "fatigue_score": self.fatigue_score,
        }


@dataclass
class RoutingDecision:
    """Result of routing decision."""

    primary_recipient: str
    backup_recipient: str | None
    reasons: list[RoutingReason]
    notification_mode: str
    should_escalate_after_minutes: int
    explanation: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "primary": self.primary_recipient,
            "backup": self.backup_recipient,
            "reasons": [r.value for r in self.reasons],
            "mode": self.notification_mode,
            "escalate_after_minutes": self.should_escalate_after_minutes,
            "explanation": self.explanation,
        }


class NotificationRouter:
    """Routes notifications intelligently."""

    def __init__(self) -> None:
        self._responders: dict[str, ResponderProfile] = {}
        self._quiet_hours: dict[str, QuietHoursConfig] = {}
        self._incident_history: dict[str, list[str]] = {}

        self._expertise_mappings: dict[str, list[str]] = {}

    def register_responder(self, profile: ResponderProfile) -> None:
        """Register a responder profile."""
        self._responders[profile.user_id] = profile
        if profile.quiet_hours:
            self._quiet_hours[profile.user_id] = profile.quiet_hours

    def set_quiet_hours(
        self,
        user_id: str,
        config: QuietHoursConfig,
    ) -> None:
        """Set quiet hours for a user."""
        self._quiet_hours[user_id] = config
        if user_id in self._responders:
            self._responders[user_id].quiet_hours = config

    def register_expertise(
        self,
        service: str,
        experts: list[str],
    ) -> None:
        """Register experts for a service."""
        self._expertise_mappings[service] = experts

    def record_incident_resolution(
        self,
        service: str,
        resolver: str,
    ) -> None:
        """Record who resolved an incident for a service."""
        if service not in self._incident_history:
            self._incident_history[service] = []

        if resolver not in self._incident_history[service]:
            self._incident_history[service].append(resolver)
            if len(self._incident_history[service]) > 10:
                self._incident_history[service] = self._incident_history[service][-10:]

    def route(
        self,
        service: str,
        severity: str,
        scheduled_oncall: str,
    ) -> RoutingDecision:
        """Route a notification to the best responder."""
        now = datetime.now(timezone.utc)

        candidates = self._score_candidates(service, severity, scheduled_oncall)

        if not candidates:
            return RoutingDecision(
                primary_recipient=scheduled_oncall,
                backup_recipient=None,
                reasons=[RoutingReason.SCHEDULED_ONCALL],
                notification_mode="full",
                should_escalate_after_minutes=5,
                explanation=f"Routed to scheduled on-call {scheduled_oncall}",
            )

        candidates.sort(key=lambda x: x["score"], reverse=True)
        primary = candidates[0]

        backup = None
        if len(candidates) > 1:
            backup = candidates[1]["user_id"]

        notification_mode = self._determine_notification_mode(
            primary["user_id"],
            severity,
            now,
        )

        escalate_minutes = 5 if severity == "SEV1" else 10 if severity == "SEV2" else 15

        explanation = self._build_explanation(primary, scheduled_oncall)

        return RoutingDecision(
            primary_recipient=primary["user_id"],
            backup_recipient=backup,
            reasons=primary["reasons"],
            notification_mode=notification_mode,
            should_escalate_after_minutes=escalate_minutes,
            explanation=explanation,
        )

    def _score_candidates(
        self,
        service: str,
        severity: str,
        scheduled_oncall: str,
    ) -> list[dict[str, Any]]:
        """Score all candidates for routing."""
        candidates = []

        for user_id, profile in self._responders.items():
            if not profile.is_available:
                continue

            score = 0.0
            reasons: list[RoutingReason] = []

            if user_id == scheduled_oncall:
                score += 3.0
                reasons.append(RoutingReason.SCHEDULED_ONCALL)

            if service in self._expertise_mappings:
                if user_id in self._expertise_mappings[service]:
                    score += 4.0
                    reasons.append(RoutingReason.EXPERTISE_MATCH)

            if service in self._incident_history:
                if user_id in self._incident_history[service]:
                    score += 3.0
                    reasons.append(RoutingReason.PREVIOUS_RESOLVER)

            if profile.resolved_incidents_week < 5:
                score += 2.0
                reasons.append(RoutingReason.LOW_FATIGUE)

            quiet_config = self._quiet_hours.get(user_id)
            if quiet_config and quiet_config.is_quiet_hours(datetime.now(timezone.utc)):
                if severity == "SEV3" and quiet_config.sev3_silent:
                    continue
                score -= 1.0

            if score > 0:
                candidates.append({
                    "user_id": user_id,
                    "score": score,
                    "reasons": reasons,
                    "profile": profile,
                })

        return candidates

    def _determine_notification_mode(
        self,
        user_id: str,
        severity: str,
        check_time: datetime,
    ) -> str:
        """Determine notification mode based on quiet hours."""
        quiet_config = self._quiet_hours.get(user_id)

        if not quiet_config or not quiet_config.is_quiet_hours(check_time):
            return "full"

        if severity == "SEV1" and quiet_config.sev1_override:
            return "full_override"
        elif severity == "SEV2" and quiet_config.sev2_vibrate_only:
            return "vibrate_only"
        elif severity == "SEV3" and quiet_config.sev3_silent:
            return "silent"

        return "full"

    def _build_explanation(
        self,
        candidate: dict[str, Any],
        scheduled_oncall: str,
    ) -> str:
        """Build explanation for routing decision."""
        user_id = candidate["user_id"]
        reasons = candidate["reasons"]

        parts = []

        if RoutingReason.EXPERTISE_MATCH in reasons:
            parts.append(f"{user_id} has expertise in this service")
        if RoutingReason.PREVIOUS_RESOLVER in reasons:
            parts.append(f"{user_id} has resolved similar incidents")
        if RoutingReason.LOW_FATIGUE in reasons:
            parts.append(f"{user_id} has low fatigue this week")
        if RoutingReason.SCHEDULED_ONCALL in reasons and user_id == scheduled_oncall:
            parts.append(f"{user_id} is scheduled on-call")

        if not parts:
            return f"Routed to {user_id}"

        return "; ".join(parts)

    def get_escalation_chain(
        self,
        primary: str,
        service: str,
    ) -> list[str]:
        """Get escalation chain for a service."""
        chain = [primary]

        if service in self._expertise_mappings:
            for expert in self._expertise_mappings[service]:
                if expert not in chain:
                    chain.append(expert)
                    if len(chain) >= 3:
                        break

        for user_id, profile in self._responders.items():
            if user_id not in chain and profile.is_scheduled_oncall:
                chain.append(user_id)
                break

        return chain

    def simulate_routing(
        self,
        service: str,
        severity: str,
        scheduled_oncall: str,
    ) -> dict[str, Any]:
        """Simulate routing for testing."""
        decision = self.route(service, severity, scheduled_oncall)

        candidates = self._score_candidates(service, severity, scheduled_oncall)
        candidates.sort(key=lambda x: x["score"], reverse=True)

        return {
            "decision": decision.to_dict(),
            "all_candidates": [
                {
                    "user_id": c["user_id"],
                    "score": c["score"],
                    "reasons": [r.value for r in c["reasons"]],
                }
                for c in candidates
            ],
            "escalation_chain": self.get_escalation_chain(
                decision.primary_recipient,
                service,
            ),
        }


_router: NotificationRouter | None = None


def get_notification_router() -> NotificationRouter:
    """Get the default notification router."""
    global _router
    if _router is None:
        _router = NotificationRouter()
    return _router
