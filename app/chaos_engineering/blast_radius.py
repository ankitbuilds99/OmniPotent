"""Blast Radius Containment.

Monitors and contains the impact of chaos experiments.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("devcopilot.chaos_engineering.blast_radius")


class ContainmentAction(str, Enum):
    ABORT = "abort"
    PAUSE = "pause"
    SCALE_DOWN = "scale_down"
    ALERT = "alert"
    NONE = "none"


@dataclass
class BlastRadiusConfig:
    """Configuration for blast radius containment."""

    config_id: str
    error_rate_threshold: float = 5.0
    user_impact_threshold: int = 50
    latency_threshold_ms: float = 5000
    cascade_detection: bool = True
    auto_abort: bool = True
    alert_channels: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.config_id,
            "error_rate_threshold": self.error_rate_threshold,
            "user_impact_threshold": self.user_impact_threshold,
            "latency_threshold_ms": self.latency_threshold_ms,
            "cascade_detection": self.cascade_detection,
            "auto_abort": self.auto_abort,
            "alert_channels": self.alert_channels,
        }


@dataclass
class ImpactMetric:
    """A metric tracking impact."""

    metric_name: str
    baseline: float
    current: float
    threshold: float
    breached: bool
    trend: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric_name,
            "baseline": self.baseline,
            "current": self.current,
            "threshold": self.threshold,
            "breached": self.breached,
            "trend": self.trend,
        }


@dataclass
class BlastRadiusStatus:
    """Current blast radius status."""

    experiment_id: str
    is_contained: bool
    metrics: list[ImpactMetric]
    recommended_action: ContainmentAction
    affected_services: list[str]
    user_impact_estimate: int
    checked_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "contained": self.is_contained,
            "metrics": [m.to_dict() for m in self.metrics],
            "action": self.recommended_action.value,
            "affected_services": self.affected_services,
            "user_impact": self.user_impact_estimate,
            "checked_at": self.checked_at.isoformat(),
        }


class BlastRadiusMonitor:
    """Monitors blast radius during chaos experiments."""

    def __init__(self) -> None:
        self._configs: dict[str, BlastRadiusConfig] = {}
        self._active_experiments: dict[str, dict[str, Any]] = {}
        self._history: dict[str, list[BlastRadiusStatus]] = {}

    def set_config(self, config: BlastRadiusConfig) -> None:
        """Set blast radius configuration."""
        self._configs[config.config_id] = config

    def get_config(self, config_id: str) -> BlastRadiusConfig:
        """Get blast radius configuration."""
        return self._configs.get(config_id, BlastRadiusConfig(config_id="default"))

    def start_monitoring(
        self,
        experiment_id: str,
        target_service: str,
        config_id: str = "default",
    ) -> None:
        """Start monitoring blast radius for an experiment."""
        config = self.get_config(config_id)

        self._active_experiments[experiment_id] = {
            "target_service": target_service,
            "config": config,
            "started_at": datetime.now(timezone.utc),
            "baseline": self._capture_baseline(target_service),
        }

        log.info(
            "Started blast radius monitoring: experiment=%s service=%s",
            experiment_id,
            target_service,
        )

    def _capture_baseline(self, service: str) -> dict[str, float]:
        """Capture baseline metrics."""
        return {
            "error_rate": 0.1,
            "latency_p99": 150.0,
            "throughput": 1000.0,
            "user_sessions": 5000,
        }

    def check_blast_radius(self, experiment_id: str) -> BlastRadiusStatus:
        """Check current blast radius status."""
        experiment = self._active_experiments.get(experiment_id)
        if not experiment:
            return BlastRadiusStatus(
                experiment_id=experiment_id,
                is_contained=True,
                metrics=[],
                recommended_action=ContainmentAction.NONE,
                affected_services=[],
                user_impact_estimate=0,
                checked_at=datetime.now(timezone.utc),
            )

        config = experiment["config"]
        baseline = experiment["baseline"]

        current = self._get_current_metrics(experiment["target_service"])

        metrics = []
        breached = False

        error_change = current["error_rate"] - baseline["error_rate"]
        metrics.append(ImpactMetric(
            metric_name="Error Rate",
            baseline=baseline["error_rate"],
            current=current["error_rate"],
            threshold=config.error_rate_threshold,
            breached=current["error_rate"] > config.error_rate_threshold,
            trend="up" if error_change > 0 else "stable",
        ))
        if current["error_rate"] > config.error_rate_threshold:
            breached = True

        latency_change = current["latency_p99"] - baseline["latency_p99"]
        metrics.append(ImpactMetric(
            metric_name="Latency P99",
            baseline=baseline["latency_p99"],
            current=current["latency_p99"],
            threshold=config.latency_threshold_ms,
            breached=current["latency_p99"] > config.latency_threshold_ms,
            trend="up" if latency_change > 100 else "stable",
        ))
        if current["latency_p99"] > config.latency_threshold_ms:
            breached = True

        user_impact = int((1 - current["throughput"] / baseline["throughput"]) * baseline["user_sessions"])
        user_impact = max(0, user_impact)

        metrics.append(ImpactMetric(
            metric_name="User Impact",
            baseline=0,
            current=float(user_impact),
            threshold=float(config.user_impact_threshold),
            breached=user_impact > config.user_impact_threshold,
            trend="up" if user_impact > 0 else "stable",
        ))
        if user_impact > config.user_impact_threshold:
            breached = True

        if breached:
            if config.auto_abort:
                action = ContainmentAction.ABORT
            else:
                action = ContainmentAction.ALERT
        else:
            action = ContainmentAction.NONE

        affected = [experiment["target_service"]]
        if config.cascade_detection and breached:
            affected.extend(self._detect_cascade(experiment["target_service"]))

        status = BlastRadiusStatus(
            experiment_id=experiment_id,
            is_contained=not breached,
            metrics=metrics,
            recommended_action=action,
            affected_services=affected,
            user_impact_estimate=user_impact,
            checked_at=datetime.now(timezone.utc),
        )

        if experiment_id not in self._history:
            self._history[experiment_id] = []
        self._history[experiment_id].append(status)

        return status

    def _get_current_metrics(self, service: str) -> dict[str, float]:
        """Get current metrics for a service (simulated)."""
        import random
        return {
            "error_rate": random.uniform(0.1, 2.0),
            "latency_p99": random.uniform(150, 400),
            "throughput": random.uniform(900, 1000),
            "user_sessions": 5000,
        }

    def _detect_cascade(self, service: str) -> list[str]:
        """Detect cascade failures to dependent services."""
        dependencies = {
            "checkout-api": ["payment-service", "inventory-api"],
            "payment-service": ["billing-api", "fraud-detection"],
            "user-service": ["auth-api", "profile-api"],
        }
        return dependencies.get(service, [])

    def stop_monitoring(self, experiment_id: str) -> dict[str, Any]:
        """Stop monitoring an experiment."""
        experiment = self._active_experiments.pop(experiment_id, None)
        if not experiment:
            return {"error": "Experiment not found"}

        history = self._history.get(experiment_id, [])

        breaches = sum(1 for s in history if not s.is_contained)
        max_impact = max((s.user_impact_estimate for s in history), default=0)

        return {
            "experiment_id": experiment_id,
            "monitoring_duration_seconds": (
                datetime.now(timezone.utc) - experiment["started_at"]
            ).total_seconds(),
            "total_checks": len(history),
            "breach_count": breaches,
            "max_user_impact": max_impact,
        }

    def get_containment_summary(self, experiment_id: str) -> dict[str, Any]:
        """Get summary of blast radius containment."""
        history = self._history.get(experiment_id, [])

        if not history:
            return {"experiment_id": experiment_id, "no_data": True}

        latest = history[-1]
        breaches = sum(1 for s in history if not s.is_contained)

        return {
            "experiment_id": experiment_id,
            "current_status": "contained" if latest.is_contained else "breached",
            "total_checks": len(history),
            "breach_count": breaches,
            "current_metrics": [m.to_dict() for m in latest.metrics],
            "affected_services": latest.affected_services,
            "recommended_action": latest.recommended_action.value,
        }


_monitor: BlastRadiusMonitor | None = None


def get_blast_radius_monitor() -> BlastRadiusMonitor:
    """Get the default blast radius monitor."""
    global _monitor
    if _monitor is None:
        _monitor = BlastRadiusMonitor()
    return _monitor
