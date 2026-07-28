"""Graceful degradation for feature-level fallbacks under load.

Provides automatic degradation of non-critical features when the system
is under stress, maintaining core functionality.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Coroutine, TypeVar

log = logging.getLogger("devcopilot.resilience.degradation")

T = TypeVar("T")


class FeaturePriority(Enum):
    """Priority levels for features."""

    CRITICAL = 1  # Never degrade
    HIGH = 2  # Degrade under severe load
    MEDIUM = 3  # Degrade under moderate load
    LOW = 4  # Degrade first
    OPTIONAL = 5  # Disabled by default under any load


class DegradationLevel(Enum):
    """System degradation levels."""

    NORMAL = 0  # All features enabled
    ELEVATED = 1  # Optional features disabled
    HIGH = 2  # Low priority features disabled
    SEVERE = 3  # Only high/critical features
    CRITICAL = 4  # Only critical features


@dataclass
class FeatureConfig:
    """Configuration for a degradable feature."""

    name: str
    priority: FeaturePriority
    fallback_value: Any = None
    fallback_func: Callable[..., Any] | None = None
    timeout_ms: float = 5000
    degraded_timeout_ms: float = 1000
    is_async: bool = True
    track_metrics: bool = True


@dataclass
class FeatureState:
    """Current state of a feature."""

    config: FeatureConfig
    is_degraded: bool = False
    degraded_at: datetime | None = None
    call_count: int = 0
    error_count: int = 0
    timeout_count: int = 0
    total_latency_ms: float = 0.0
    degraded_call_count: int = 0

    @property
    def error_rate(self) -> float:
        """Get error rate."""
        if self.call_count == 0:
            return 0.0
        return self.error_count / self.call_count

    @property
    def avg_latency_ms(self) -> float:
        """Get average latency."""
        if self.call_count == 0:
            return 0.0
        return self.total_latency_ms / self.call_count


@dataclass
class SystemLoad:
    """Current system load metrics."""

    cpu_percent: float = 0.0
    memory_percent: float = 0.0
    request_rate: float = 0.0
    error_rate: float = 0.0
    avg_latency_ms: float = 0.0
    active_connections: int = 0
    queue_depth: int = 0

    def severity_score(self) -> float:
        """Calculate overall severity (0-1)."""
        scores = [
            min(1.0, self.cpu_percent / 90),
            min(1.0, self.memory_percent / 90),
            min(1.0, self.error_rate / 0.1),
            min(1.0, self.avg_latency_ms / 5000),
            min(1.0, self.queue_depth / 1000),
        ]
        return sum(scores) / len(scores)


@dataclass
class DegradationEvent:
    """Record of a degradation event."""

    timestamp: datetime
    feature: str
    previous_level: DegradationLevel
    new_level: DegradationLevel
    reason: str
    system_load: SystemLoad


class GracefulDegradation:
    """Manages graceful degradation of features."""

    LEVEL_THRESHOLDS: dict[DegradationLevel, float] = {
        DegradationLevel.NORMAL: 0.0,
        DegradationLevel.ELEVATED: 0.3,
        DegradationLevel.HIGH: 0.5,
        DegradationLevel.SEVERE: 0.7,
        DegradationLevel.CRITICAL: 0.9,
    }

    def __init__(
        self,
        auto_degrade: bool = True,
        check_interval_sec: float = 5.0,
        recovery_delay_sec: float = 30.0,
    ):
        self._features: dict[str, FeatureState] = {}
        self._current_level = DegradationLevel.NORMAL
        self._auto_degrade = auto_degrade
        self._check_interval = check_interval_sec
        self._recovery_delay = recovery_delay_sec
        self._last_load = SystemLoad()
        self._events: list[DegradationEvent] = []
        self._level_changed_at: datetime | None = None
        self._monitor_task: asyncio.Task[None] | None = None

    def register_feature(
        self,
        name: str,
        priority: FeaturePriority,
        fallback_value: Any = None,
        fallback_func: Callable[..., Any] | None = None,
        timeout_ms: float = 5000,
        degraded_timeout_ms: float = 1000,
    ) -> FeatureConfig:
        """Register a feature for degradation management."""
        config = FeatureConfig(
            name=name,
            priority=priority,
            fallback_value=fallback_value,
            fallback_func=fallback_func,
            timeout_ms=timeout_ms,
            degraded_timeout_ms=degraded_timeout_ms,
        )
        self._features[name] = FeatureState(config=config)
        return config

    def is_feature_enabled(self, name: str) -> bool:
        """Check if a feature is currently enabled."""
        state = self._features.get(name)
        if not state:
            return True

        priority_threshold = {
            DegradationLevel.NORMAL: FeaturePriority.OPTIONAL,
            DegradationLevel.ELEVATED: FeaturePriority.LOW,
            DegradationLevel.HIGH: FeaturePriority.MEDIUM,
            DegradationLevel.SEVERE: FeaturePriority.HIGH,
            DegradationLevel.CRITICAL: FeaturePriority.CRITICAL,
        }

        threshold = priority_threshold.get(
            self._current_level, FeaturePriority.OPTIONAL
        )
        return state.config.priority.value <= threshold.value

    async def execute(
        self,
        name: str,
        func: Callable[..., Coroutine[Any, Any, T]],
        *args: Any,
        **kwargs: Any,
    ) -> T:
        """Execute a feature with degradation handling."""
        state = self._features.get(name)
        if not state:
            return await func(*args, **kwargs)

        state.call_count += 1

        if not self.is_feature_enabled(name):
            state.degraded_call_count += 1
            state.is_degraded = True
            if state.degraded_at is None:
                state.degraded_at = datetime.now(timezone.utc)

            if state.config.fallback_func:
                result = state.config.fallback_func(*args, **kwargs)
                if asyncio.iscoroutine(result):
                    return await result
                return result  # type: ignore
            return state.config.fallback_value

        timeout = (
            state.config.degraded_timeout_ms
            if state.is_degraded
            else state.config.timeout_ms
        ) / 1000.0

        start = time.perf_counter()
        try:
            result = await asyncio.wait_for(func(*args, **kwargs), timeout=timeout)
            elapsed = (time.perf_counter() - start) * 1000
            state.total_latency_ms += elapsed

            if state.is_degraded:
                state.is_degraded = False
                state.degraded_at = None

            return result

        except asyncio.TimeoutError:
            state.timeout_count += 1
            state.error_count += 1

            if state.config.fallback_func:
                result = state.config.fallback_func(*args, **kwargs)
                if asyncio.iscoroutine(result):
                    return await result
                return result  # type: ignore
            return state.config.fallback_value

        except Exception:
            state.error_count += 1
            raise

    def update_load(self, load: SystemLoad) -> None:
        """Update system load metrics."""
        self._last_load = load

        if self._auto_degrade:
            self._evaluate_degradation_level()

    def _evaluate_degradation_level(self) -> None:
        """Evaluate and update degradation level."""
        severity = self._last_load.severity_score()

        new_level = DegradationLevel.NORMAL
        for level, threshold in sorted(
            self.LEVEL_THRESHOLDS.items(), key=lambda x: x[1], reverse=True
        ):
            if severity >= threshold:
                new_level = level
                break

        if new_level != self._current_level:
            now = datetime.now(timezone.utc)

            if new_level.value < self._current_level.value:
                if self._level_changed_at:
                    elapsed = (now - self._level_changed_at).total_seconds()
                    if elapsed < self._recovery_delay:
                        return

            self._record_level_change(new_level, f"severity={severity:.2f}")
            self._current_level = new_level
            self._level_changed_at = now

    def _record_level_change(self, new_level: DegradationLevel, reason: str) -> None:
        """Record a degradation level change."""
        event = DegradationEvent(
            timestamp=datetime.now(timezone.utc),
            feature="system",
            previous_level=self._current_level,
            new_level=new_level,
            reason=reason,
            system_load=self._last_load,
        )
        self._events.append(event)

        if len(self._events) > 1000:
            self._events = self._events[-500:]

        log.info(
            "degradation level changed",
            extra={
                "previous": self._current_level.name,
                "new": new_level.name,
                "reason": reason,
            },
        )

    def force_level(self, level: DegradationLevel) -> None:
        """Force a specific degradation level."""
        if level != self._current_level:
            self._record_level_change(level, "forced")
            self._current_level = level
            self._level_changed_at = datetime.now(timezone.utc)

    def get_status(self) -> dict[str, Any]:
        """Get current degradation status."""
        features_status = {}
        for name, state in self._features.items():
            features_status[name] = {
                "enabled": self.is_feature_enabled(name),
                "priority": state.config.priority.name,
                "is_degraded": state.is_degraded,
                "call_count": state.call_count,
                "error_rate": round(state.error_rate, 3),
                "avg_latency_ms": round(state.avg_latency_ms, 2),
                "degraded_calls": state.degraded_call_count,
            }

        return {
            "level": self._current_level.name,
            "severity_score": round(self._last_load.severity_score(), 3),
            "auto_degrade": self._auto_degrade,
            "features": features_status,
            "system_load": {
                "cpu_percent": self._last_load.cpu_percent,
                "memory_percent": self._last_load.memory_percent,
                "error_rate": self._last_load.error_rate,
                "avg_latency_ms": self._last_load.avg_latency_ms,
            },
        }

    def get_recent_events(self, limit: int = 50) -> list[dict[str, Any]]:
        """Get recent degradation events."""
        events = self._events[-limit:]
        return [
            {
                "timestamp": e.timestamp.isoformat(),
                "feature": e.feature,
                "previous_level": e.previous_level.name,
                "new_level": e.new_level.name,
                "reason": e.reason,
            }
            for e in reversed(events)
        ]

    async def start_monitoring(
        self,
        load_provider: Callable[[], Coroutine[Any, Any, SystemLoad]],
    ) -> None:
        """Start background load monitoring."""
        async def monitor_loop() -> None:
            while True:
                try:
                    load = await load_provider()
                    self.update_load(load)
                except Exception:
                    log.exception("load monitoring failed")
                await asyncio.sleep(self._check_interval)

        self._monitor_task = asyncio.create_task(monitor_loop())

    def stop_monitoring(self) -> None:
        """Stop background monitoring."""
        if self._monitor_task:
            self._monitor_task.cancel()
            self._monitor_task = None


_degradation: GracefulDegradation | None = None


def get_graceful_degradation() -> GracefulDegradation:
    """Get the default graceful degradation manager."""
    global _degradation
    if _degradation is None:
        _degradation = GracefulDegradation()
    return _degradation
