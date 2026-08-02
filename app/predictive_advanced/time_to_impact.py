"""Time-to-Impact Countdown Tracking.

Provides real-time countdown to predicted incidents with confidence intervals.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("omnipotent.predictive_advanced.time_to_impact")


class CountdownStatus(str, Enum):
    ACTIVE = "active"
    AVERTED = "averted"
    OCCURRED = "occurred"
    EXPIRED = "expired"
    PAUSED = "paused"


class UrgencyLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class ConfidenceInterval:
    """Confidence interval for time estimate."""

    lower_bound_minutes: float
    expected_minutes: float
    upper_bound_minutes: float
    confidence_level: float

    @property
    def range_minutes(self) -> float:
        return self.upper_bound_minutes - self.lower_bound_minutes

    def to_dict(self) -> dict[str, Any]:
        return {
            "lower_minutes": round(self.lower_bound_minutes, 1),
            "expected_minutes": round(self.expected_minutes, 1),
            "upper_minutes": round(self.upper_bound_minutes, 1),
            "confidence": round(self.confidence_level, 2),
            "range": round(self.range_minutes, 1),
        }


@dataclass
class ImpactCountdown:
    """A countdown tracker for a predicted incident."""

    countdown_id: str
    prediction_id: str
    service: str
    predicted_event: str
    started_at: datetime
    initial_tti_minutes: float
    current_tti_minutes: float
    confidence_interval: ConfidenceInterval
    status: CountdownStatus = CountdownStatus.ACTIVE
    confidence: float = 0.9
    updates: list[dict[str, Any]] = field(default_factory=list)
    actions_taken: list[str] = field(default_factory=list)

    @property
    def elapsed_minutes(self) -> float:
        return (datetime.now(timezone.utc) - self.started_at).total_seconds() / 60

    @property
    def remaining_minutes(self) -> float:
        return max(0, self.current_tti_minutes - self.elapsed_minutes)

    @property
    def progress_percent(self) -> float:
        if self.initial_tti_minutes <= 0:
            return 100
        return min(100, (self.elapsed_minutes / self.initial_tti_minutes) * 100)

    @property
    def urgency(self) -> UrgencyLevel:
        remaining = self.remaining_minutes
        if remaining <= 10:
            return UrgencyLevel.CRITICAL
        elif remaining <= 30:
            return UrgencyLevel.HIGH
        elif remaining <= 60:
            return UrgencyLevel.MEDIUM
        else:
            return UrgencyLevel.LOW

    @property
    def formatted_remaining(self) -> str:
        minutes = self.remaining_minutes
        if minutes <= 0:
            return "IMMINENT"
        hours = int(minutes // 60)
        mins = int(minutes % 60)
        secs = int((minutes * 60) % 60)
        if hours > 0:
            return f"{hours}h {mins}m {secs}s"
        elif mins > 0:
            return f"{mins}m {secs}s"
        else:
            return f"{secs}s"

    def to_dict(self) -> dict[str, Any]:
        return {
            "countdown_id": self.countdown_id,
            "prediction_id": self.prediction_id,
            "service": self.service,
            "predicted_event": self.predicted_event,
            "started_at": self.started_at.isoformat(),
            "initial_tti_minutes": round(self.initial_tti_minutes, 1),
            "current_tti_minutes": round(self.current_tti_minutes, 1),
            "remaining_minutes": round(self.remaining_minutes, 1),
            "formatted_remaining": self.formatted_remaining,
            "elapsed_minutes": round(self.elapsed_minutes, 1),
            "progress_percent": round(self.progress_percent, 1),
            "confidence_interval": self.confidence_interval.to_dict(),
            "status": self.status.value,
            "urgency": self.urgency.value,
            "confidence": round(self.confidence, 2),
            "updates_count": len(self.updates),
            "actions_taken": self.actions_taken,
        }


class CountdownTracker:
    """Tracks time-to-impact countdowns for predicted incidents."""

    def __init__(self) -> None:
        self._countdowns: dict[str, ImpactCountdown] = {}
        self._history: dict[str, list[ImpactCountdown]] = {}

    def create_countdown(
        self,
        org_id: str,
        prediction_id: str,
        service: str,
        predicted_event: str,
        time_to_impact_minutes: float,
        confidence: float = 0.9,
        variance_factor: float = 0.2,
    ) -> ImpactCountdown:
        """Create a new countdown for a predicted incident."""
        countdown_id = f"countdown_{prediction_id}"

        lower = time_to_impact_minutes * (1 - variance_factor)
        upper = time_to_impact_minutes * (1 + variance_factor)

        confidence_interval = ConfidenceInterval(
            lower_bound_minutes=lower,
            expected_minutes=time_to_impact_minutes,
            upper_bound_minutes=upper,
            confidence_level=confidence,
        )

        countdown = ImpactCountdown(
            countdown_id=countdown_id,
            prediction_id=prediction_id,
            service=service,
            predicted_event=predicted_event,
            started_at=datetime.now(timezone.utc),
            initial_tti_minutes=time_to_impact_minutes,
            current_tti_minutes=time_to_impact_minutes,
            confidence_interval=confidence_interval,
            status=CountdownStatus.ACTIVE,
            confidence=confidence,
        )

        self._countdowns[countdown_id] = countdown

        log.info(
            "Created countdown: id=%s event=%s tti=%.1fm",
            countdown_id,
            predicted_event,
            time_to_impact_minutes,
        )

        return countdown

    def update_countdown(
        self,
        countdown_id: str,
        new_tti_minutes: float | None = None,
        new_confidence: float | None = None,
        reason: str = "",
    ) -> ImpactCountdown | None:
        """Update a countdown with new predictions."""
        countdown = self._countdowns.get(countdown_id)
        if not countdown:
            return None

        update_record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "previous_tti": countdown.current_tti_minutes,
            "previous_confidence": countdown.confidence,
        }

        if new_tti_minutes is not None:
            countdown.current_tti_minutes = new_tti_minutes

            elapsed = countdown.elapsed_minutes
            remaining = max(0, new_tti_minutes - elapsed)

            variance_factor = 0.2
            countdown.confidence_interval = ConfidenceInterval(
                lower_bound_minutes=remaining * (1 - variance_factor),
                expected_minutes=remaining,
                upper_bound_minutes=remaining * (1 + variance_factor),
                confidence_level=countdown.confidence,
            )

        if new_confidence is not None:
            countdown.confidence = new_confidence
            countdown.confidence_interval.confidence_level = new_confidence

        update_record["new_tti"] = countdown.current_tti_minutes
        update_record["new_confidence"] = countdown.confidence
        update_record["reason"] = reason

        countdown.updates.append(update_record)

        log.info(
            "Updated countdown: id=%s tti=%.1fm confidence=%.2f reason=%s",
            countdown_id,
            countdown.current_tti_minutes,
            countdown.confidence,
            reason,
        )

        return countdown

    def record_action(
        self,
        countdown_id: str,
        action: str,
    ) -> bool:
        """Record a prevention action taken."""
        countdown = self._countdowns.get(countdown_id)
        if not countdown:
            return False

        countdown.actions_taken.append(action)

        countdown.updates.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "type": "action_taken",
        })

        log.info("Recorded action: countdown=%s action=%s", countdown_id, action)

        return True

    def mark_averted(
        self,
        countdown_id: str,
        reason: str = "",
    ) -> ImpactCountdown | None:
        """Mark a predicted incident as averted."""
        countdown = self._countdowns.get(countdown_id)
        if not countdown:
            return None

        countdown.status = CountdownStatus.AVERTED

        countdown.updates.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status_change": "averted",
            "reason": reason,
        })

        self._archive_countdown(countdown)

        log.info("Marked averted: countdown=%s reason=%s", countdown_id, reason)

        return countdown

    def mark_occurred(
        self,
        countdown_id: str,
        incident_id: str = "",
    ) -> ImpactCountdown | None:
        """Mark that the predicted incident occurred."""
        countdown = self._countdowns.get(countdown_id)
        if not countdown:
            return None

        countdown.status = CountdownStatus.OCCURRED

        countdown.updates.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status_change": "occurred",
            "incident_id": incident_id,
        })

        self._archive_countdown(countdown)

        log.info(
            "Marked occurred: countdown=%s incident=%s",
            countdown_id,
            incident_id,
        )

        return countdown

    def expire_countdown(
        self,
        countdown_id: str,
    ) -> ImpactCountdown | None:
        """Expire a countdown (prediction window passed without incident)."""
        countdown = self._countdowns.get(countdown_id)
        if not countdown:
            return None

        countdown.status = CountdownStatus.EXPIRED

        countdown.updates.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status_change": "expired",
        })

        self._archive_countdown(countdown)

        log.info("Expired countdown: countdown=%s", countdown_id)

        return countdown

    def _archive_countdown(self, countdown: ImpactCountdown) -> None:
        """Archive a completed countdown."""
        org_key = countdown.countdown_id.split("_")[1]

        if org_key not in self._history:
            self._history[org_key] = []

        self._history[org_key].append(countdown)

        if len(self._history[org_key]) > 100:
            self._history[org_key] = self._history[org_key][-50:]

        del self._countdowns[countdown.countdown_id]

    def get_countdown(self, countdown_id: str) -> ImpactCountdown | None:
        """Get a countdown by ID."""
        return self._countdowns.get(countdown_id)

    def get_active_countdowns(
        self,
        org_id: str | None = None,
        service: str | None = None,
        urgency: UrgencyLevel | None = None,
    ) -> list[ImpactCountdown]:
        """Get all active countdowns."""
        countdowns = []

        for countdown in self._countdowns.values():
            if countdown.status != CountdownStatus.ACTIVE:
                continue

            if org_id and not countdown.prediction_id.startswith(f"pred_{org_id}"):
                continue

            if service and countdown.service != service:
                continue

            if urgency and countdown.urgency != urgency:
                continue

            countdowns.append(countdown)

        countdowns.sort(key=lambda c: c.remaining_minutes)

        return countdowns

    def get_critical_countdowns(
        self,
        org_id: str | None = None,
    ) -> list[ImpactCountdown]:
        """Get countdowns in critical state (< 10 minutes remaining)."""
        return [
            c for c in self.get_active_countdowns(org_id)
            if c.urgency in (UrgencyLevel.CRITICAL, UrgencyLevel.HIGH)
        ]

    def check_expired(self) -> list[ImpactCountdown]:
        """Check and expire countdowns that have passed their window."""
        expired = []

        for countdown_id, countdown in list(self._countdowns.items()):
            if countdown.status != CountdownStatus.ACTIVE:
                continue

            grace_period = countdown.confidence_interval.upper_bound_minutes * 1.5
            if countdown.elapsed_minutes > grace_period:
                self.expire_countdown(countdown_id)
                expired.append(countdown)

        return expired

    def get_countdown_stats(
        self,
        org_id: str,
    ) -> dict[str, Any]:
        """Get statistics about countdown accuracy."""
        history = self._history.get(org_id, [])

        if not history:
            return {
                "total": 0,
                "averted": 0,
                "occurred": 0,
                "expired": 0,
                "accuracy": 0,
                "avg_lead_time_minutes": 0,
            }

        averted = sum(1 for c in history if c.status == CountdownStatus.AVERTED)
        occurred = sum(1 for c in history if c.status == CountdownStatus.OCCURRED)
        expired = sum(1 for c in history if c.status == CountdownStatus.EXPIRED)

        correct = averted + occurred
        total_with_outcome = correct + expired

        accuracy = correct / total_with_outcome if total_with_outcome > 0 else 0

        lead_times = [
            c.initial_tti_minutes
            for c in history
            if c.status in (CountdownStatus.AVERTED, CountdownStatus.OCCURRED)
        ]
        avg_lead_time = sum(lead_times) / len(lead_times) if lead_times else 0

        return {
            "total": len(history),
            "averted": averted,
            "occurred": occurred,
            "expired": expired,
            "accuracy": round(accuracy, 2),
            "aversion_rate": round(averted / correct, 2) if correct > 0 else 0,
            "avg_lead_time_minutes": round(avg_lead_time, 1),
        }

    def get_countdown_for_display(
        self,
        countdown_id: str,
    ) -> dict[str, Any] | None:
        """Get countdown formatted for UI display."""
        countdown = self._countdowns.get(countdown_id)
        if not countdown:
            return None

        return {
            "id": countdown.countdown_id,
            "event": countdown.predicted_event,
            "service": countdown.service,
            "display": {
                "time_remaining": countdown.formatted_remaining,
                "progress_bar": self._generate_progress_bar(countdown),
                "urgency_color": self._get_urgency_color(countdown.urgency),
                "confidence_display": f"{countdown.confidence:.0%}",
            },
            "interval": {
                "earliest": f"{countdown.confidence_interval.lower_bound_minutes:.0f}m",
                "expected": f"{countdown.confidence_interval.expected_minutes:.0f}m",
                "latest": f"{countdown.confidence_interval.upper_bound_minutes:.0f}m",
            },
            "status": countdown.status.value,
            "actions_taken": len(countdown.actions_taken),
        }

    def _generate_progress_bar(
        self,
        countdown: ImpactCountdown,
        width: int = 40,
    ) -> str:
        """Generate ASCII progress bar."""
        filled = int(countdown.progress_percent / 100 * width)
        empty = width - filled
        return f"{'█' * filled}{'░' * empty}"

    def _get_urgency_color(self, urgency: UrgencyLevel) -> str:
        """Get color code for urgency level."""
        colors = {
            UrgencyLevel.LOW: "green",
            UrgencyLevel.MEDIUM: "yellow",
            UrgencyLevel.HIGH: "orange",
            UrgencyLevel.CRITICAL: "red",
        }
        return colors.get(urgency, "gray")


_tracker: CountdownTracker | None = None


def get_countdown_tracker() -> CountdownTracker:
    """Get the default countdown tracker."""
    global _tracker
    if _tracker is None:
        _tracker = CountdownTracker()
    return _tracker
