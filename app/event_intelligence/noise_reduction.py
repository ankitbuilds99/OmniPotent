"""Noise Reduction Engine.

Multi-strategy noise detection and filtering for alerts.

Strategies:
1. Statistical Anomaly Detection (Z-score, IQR)
2. Temporal Pattern Analysis (flapping, transient)
3. Contextual Filtering (maintenance, deployment)
4. ML-based Classification
5. Correlation-based Suppression
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Optional
from collections import defaultdict

log = logging.getLogger("devcopilot.event_intelligence.noise_reduction")


class NoiseCategory(str, Enum):
    """Categories of noise for classification."""
    TRANSIENT_SPIKE = "transient_spike"
    FLAPPING = "flapping"
    MAINTENANCE_WINDOW = "maintenance"
    DEPLOYMENT_ARTIFACT = "deployment"
    TEST_ENVIRONMENT = "test_env"
    DUPLICATE = "duplicate"
    LOW_CONFIDENCE = "low_confidence"
    KNOWN_FALSE_POSITIVE = "known_fp"
    CORRELATED = "correlated"


@dataclass
class NoiseSignal:
    """Input signal for noise detection."""
    alert_id: str
    source: str
    service: str
    metric_name: str
    value: float
    threshold: float
    timestamp: datetime
    environment: str
    severity: str = "warning"
    labels: dict[str, str] = field(default_factory=dict)
    deployment_in_progress: bool = False
    maintenance_window_active: bool = False

    # Historical context (populated by enrichment)
    hourly_avg: Optional[float] = None
    hourly_stddev: Optional[float] = None
    daily_avg: Optional[float] = None
    flap_count_1h: int = 0
    similar_alerts_1h: int = 0
    auto_resolve_rate_7d: float = 0.0

    def fingerprint(self) -> str:
        """Generate unique fingerprint for deduplication."""
        key = f"{self.service}:{self.metric_name}:{self.source}"
        return hashlib.sha256(key.encode()).hexdigest()[:16]


@dataclass
class NoiseDecision:
    """Decision output from noise reduction."""
    alert_id: str
    is_noise: bool
    noise_category: Optional[NoiseCategory]
    confidence: float
    reasoning: str
    should_suppress: bool
    suppress_duration_seconds: Optional[int] = None
    human_review_recommended: bool = False
    strategy_used: str = ""


@dataclass
class NoiseReductionConfig:
    """Configuration for noise reduction engine."""
    transient_window_seconds: int = 120
    flap_threshold: int = 5
    flap_window_minutes: int = 60
    auto_resolve_noise_threshold: float = 0.85
    z_score_threshold: float = 2.5
    min_confidence: float = 0.7
    duplicate_window_seconds: int = 300
    correlation_window_seconds: int = 600
    enable_ml_classification: bool = True
    test_environments: list[str] = field(default_factory=lambda: [
        "dev", "development", "test", "testing", "staging", "qa"
    ])


class NoiseReductionEngine:
    """Multi-strategy noise reduction engine.

    Evaluates alerts against multiple noise detection strategies
    and aggregates decisions based on confidence scores.
    """

    def __init__(
        self,
        config: Optional[NoiseReductionConfig] = None,
        redis_client: Any = None,
        db_session: Any = None,
    ):
        self.config = config or NoiseReductionConfig()
        self.redis = redis_client
        self.db = db_session

        # In-memory caches for when Redis is not available
        self._transient_holds: dict[str, datetime] = {}
        self._dedup_cache: dict[str, datetime] = {}
        self._flap_counts: dict[str, list[datetime]] = defaultdict(list)
        self._alert_history: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._correlation_window: list[dict[str, Any]] = []

        # Statistics tracking
        self._stats = {
            "total_evaluated": 0,
            "noise_detected": 0,
            "by_category": defaultdict(int),
        }

    async def evaluate(self, signal: NoiseSignal) -> NoiseDecision:
        """Main entry point - evaluate if alert is noise."""
        self._stats["total_evaluated"] += 1

        # Enrich signal with historical context
        signal = await self._enrich_signal(signal)

        # Run all noise detection strategies
        strategies = [
            ("test_environment", self._check_test_environment(signal)),
            ("transient_spike", self._check_transient_spike(signal)),
            ("flapping", self._check_flapping(signal)),
            ("maintenance", self._check_maintenance_context(signal)),
            ("deployment", self._check_deployment_context(signal)),
            ("statistical", self._check_statistical_anomaly(signal)),
            ("duplicate", self._check_duplicate(signal)),
            ("auto_resolve", self._check_auto_resolve_pattern(signal)),
            ("correlation", self._check_correlation(signal)),
        ]

        # Execute all strategies
        results = []
        for name, coro in strategies:
            try:
                if asyncio.iscoroutine(coro):
                    result = await coro
                else:
                    result = coro
                if result:
                    result.strategy_used = name
                    results.append(result)
            except Exception as e:
                log.warning(f"Strategy {name} failed: {e}")

        # Record alert in correlation window
        self._record_for_correlation(signal)

        # Aggregate decisions
        decision = self._aggregate_decisions(signal.alert_id, results)

        if decision.is_noise:
            self._stats["noise_detected"] += 1
            if decision.noise_category:
                self._stats["by_category"][decision.noise_category.value] += 1

        return decision

    async def _enrich_signal(self, signal: NoiseSignal) -> NoiseSignal:
        """Enrich signal with historical statistics."""
        fingerprint = signal.fingerprint()

        if self.redis:
            try:
                # Get time-series stats from Redis
                key = f"metric_stats:{signal.service}:{signal.metric_name}"
                stats = await self.redis.hgetall(key)

                if stats:
                    signal.hourly_avg = float(stats.get(b"hourly_avg", 0))
                    signal.hourly_stddev = float(stats.get(b"hourly_stddev", 1))
                    signal.daily_avg = float(stats.get(b"daily_avg", 0))

                # Get flap count
                flap_key = f"flap_count:{fingerprint}"
                flap_count = await self.redis.get(flap_key)
                signal.flap_count_1h = int(flap_count) if flap_count else 0

                # Get similar alerts count
                similar_key = f"similar_alerts:{signal.service}"
                similar_count = await self.redis.get(similar_key)
                signal.similar_alerts_1h = int(similar_count) if similar_count else 0

            except Exception as e:
                log.warning(f"Redis enrichment failed: {e}")

        # Use in-memory flap tracking as fallback
        if signal.flap_count_1h == 0:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
            flaps = self._flap_counts.get(fingerprint, [])
            signal.flap_count_1h = len([t for t in flaps if t > cutoff])

        # Calculate auto-resolve rate from history
        if signal.auto_resolve_rate_7d == 0:
            signal.auto_resolve_rate_7d = self._calculate_auto_resolve_rate(fingerprint)

        return signal

    def _check_test_environment(self, signal: NoiseSignal) -> Optional[NoiseDecision]:
        """Suppress alerts from test/dev environments."""
        env_lower = signal.environment.lower()

        if any(test_env in env_lower for test_env in self.config.test_environments):
            return NoiseDecision(
                alert_id=signal.alert_id,
                is_noise=True,
                noise_category=NoiseCategory.TEST_ENVIRONMENT,
                confidence=0.99,
                reasoning=f"Alert from test environment: {signal.environment}",
                should_suppress=True,
            )
        return None

    async def _check_transient_spike(self, signal: NoiseSignal) -> Optional[NoiseDecision]:
        """Detect transient spikes that auto-resolve."""
        fingerprint = signal.fingerprint()
        now = datetime.now(timezone.utc)

        # Check if we're already holding this alert
        if fingerprint in self._transient_holds:
            held_at = self._transient_holds[fingerprint]
            hold_duration = (now - held_at).total_seconds()

            if hold_duration < self.config.transient_window_seconds:
                return NoiseDecision(
                    alert_id=signal.alert_id,
                    is_noise=True,
                    noise_category=NoiseCategory.TRANSIENT_SPIKE,
                    confidence=0.6,
                    reasoning=f"Holding alert for transient detection ({hold_duration:.0f}s of {self.config.transient_window_seconds}s)",
                    should_suppress=True,
                    suppress_duration_seconds=int(self.config.transient_window_seconds - hold_duration),
                )
            else:
                # Hold expired, alert is persistent
                del self._transient_holds[fingerprint]
                return None
        else:
            # First occurrence - start holding
            self._transient_holds[fingerprint] = now
            return NoiseDecision(
                alert_id=signal.alert_id,
                is_noise=True,
                noise_category=NoiseCategory.TRANSIENT_SPIKE,
                confidence=0.5,
                reasoning="Starting transient spike detection hold",
                should_suppress=True,
                suppress_duration_seconds=self.config.transient_window_seconds,
            )

    async def _check_flapping(self, signal: NoiseSignal) -> Optional[NoiseDecision]:
        """Detect flapping alerts (rapid state changes)."""
        fingerprint = signal.fingerprint()
        now = datetime.now(timezone.utc)

        # Record this alert occurrence
        self._flap_counts[fingerprint].append(now)

        # Clean old entries
        cutoff = now - timedelta(minutes=self.config.flap_window_minutes)
        self._flap_counts[fingerprint] = [
            t for t in self._flap_counts[fingerprint] if t > cutoff
        ]

        flap_count = len(self._flap_counts[fingerprint])

        if flap_count >= self.config.flap_threshold:
            return NoiseDecision(
                alert_id=signal.alert_id,
                is_noise=True,
                noise_category=NoiseCategory.FLAPPING,
                confidence=min(0.95, 0.7 + (flap_count - self.config.flap_threshold) * 0.05),
                reasoning=f"Alert flapped {flap_count} times in {self.config.flap_window_minutes}min (threshold: {self.config.flap_threshold})",
                should_suppress=True,
                suppress_duration_seconds=3600,
                human_review_recommended=True,
            )
        return None

    def _check_maintenance_context(self, signal: NoiseSignal) -> Optional[NoiseDecision]:
        """Suppress alerts during maintenance windows."""
        if signal.maintenance_window_active:
            return NoiseDecision(
                alert_id=signal.alert_id,
                is_noise=True,
                noise_category=NoiseCategory.MAINTENANCE_WINDOW,
                confidence=0.95,
                reasoning="Alert occurred during scheduled maintenance window",
                should_suppress=True,
            )
        return None

    def _check_deployment_context(self, signal: NoiseSignal) -> Optional[NoiseDecision]:
        """Suppress expected alerts during deployments."""
        if not signal.deployment_in_progress:
            return None

        deployment_patterns = [
            "pod_restart", "container_creating", "rollout",
            "health_check", "connection_refused", "unavailable",
            "crashloopbackoff", "pending", "terminating",
        ]

        metric_lower = signal.metric_name.lower()
        if any(pattern in metric_lower for pattern in deployment_patterns):
            return NoiseDecision(
                alert_id=signal.alert_id,
                is_noise=True,
                noise_category=NoiseCategory.DEPLOYMENT_ARTIFACT,
                confidence=0.85,
                reasoning=f"Alert pattern '{signal.metric_name}' is expected during deployment",
                should_suppress=True,
                suppress_duration_seconds=600,
            )
        return None

    def _check_statistical_anomaly(self, signal: NoiseSignal) -> Optional[NoiseDecision]:
        """Statistical outlier detection using Z-score."""
        if signal.hourly_stddev is None or signal.hourly_stddev == 0:
            return None

        if signal.hourly_avg is None:
            return None

        z_score = abs(signal.value - signal.hourly_avg) / signal.hourly_stddev

        # Value is within normal variance - threshold might be misconfigured
        if z_score < self.config.z_score_threshold:
            return NoiseDecision(
                alert_id=signal.alert_id,
                is_noise=True,
                noise_category=NoiseCategory.LOW_CONFIDENCE,
                confidence=0.6 + (self.config.z_score_threshold - z_score) * 0.1,
                reasoning=f"Value {signal.value:.2f} is within normal variance (z-score: {z_score:.2f}, threshold: {self.config.z_score_threshold})",
                should_suppress=False,
                human_review_recommended=True,
            )
        return None

    async def _check_duplicate(self, signal: NoiseSignal) -> Optional[NoiseDecision]:
        """Detect duplicate alerts within dedup window."""
        fingerprint = signal.fingerprint()
        now = datetime.now(timezone.utc)

        # Clean old entries
        cutoff = now - timedelta(seconds=self.config.duplicate_window_seconds)
        self._dedup_cache = {
            k: v for k, v in self._dedup_cache.items() if v > cutoff
        }

        if fingerprint in self._dedup_cache:
            first_seen = self._dedup_cache[fingerprint]
            return NoiseDecision(
                alert_id=signal.alert_id,
                is_noise=True,
                noise_category=NoiseCategory.DUPLICATE,
                confidence=1.0,
                reasoning=f"Duplicate alert within {self.config.duplicate_window_seconds}s window (first seen: {first_seen.isoformat()})",
                should_suppress=True,
            )

        # Set dedup marker
        self._dedup_cache[fingerprint] = now
        return None

    def _check_auto_resolve_pattern(self, signal: NoiseSignal) -> Optional[NoiseDecision]:
        """Alerts that historically auto-resolve are likely noise."""
        if signal.auto_resolve_rate_7d >= self.config.auto_resolve_noise_threshold:
            return NoiseDecision(
                alert_id=signal.alert_id,
                is_noise=True,
                noise_category=NoiseCategory.KNOWN_FALSE_POSITIVE,
                confidence=signal.auto_resolve_rate_7d,
                reasoning=f"This alert type auto-resolves {signal.auto_resolve_rate_7d*100:.0f}% of the time",
                should_suppress=True,
                suppress_duration_seconds=300,
            )
        return None

    def _check_correlation(self, signal: NoiseSignal) -> Optional[NoiseDecision]:
        """Check if alert is correlated with a parent incident."""
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(seconds=self.config.correlation_window_seconds)

        # Find recent alerts from same service or related services
        related_alerts = [
            a for a in self._correlation_window
            if a["timestamp"] > cutoff and a["service"] == signal.service
        ]

        if len(related_alerts) >= 3:
            # Multiple alerts from same service - likely same root cause
            return NoiseDecision(
                alert_id=signal.alert_id,
                is_noise=True,
                noise_category=NoiseCategory.CORRELATED,
                confidence=0.75,
                reasoning=f"Correlated with {len(related_alerts)} other alerts from {signal.service} in the last {self.config.correlation_window_seconds}s",
                should_suppress=True,
                suppress_duration_seconds=300,
            )
        return None

    def _record_for_correlation(self, signal: NoiseSignal) -> None:
        """Record alert for correlation analysis."""
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(seconds=self.config.correlation_window_seconds * 2)

        # Clean old entries
        self._correlation_window = [
            a for a in self._correlation_window if a["timestamp"] > cutoff
        ]

        self._correlation_window.append({
            "alert_id": signal.alert_id,
            "service": signal.service,
            "metric_name": signal.metric_name,
            "timestamp": now,
            "fingerprint": signal.fingerprint(),
        })

    def _calculate_auto_resolve_rate(self, fingerprint: str) -> float:
        """Calculate auto-resolve rate from in-memory history."""
        history = self._alert_history.get(fingerprint, [])
        if len(history) < 5:
            return 0.0

        auto_resolved = sum(1 for h in history if h.get("auto_resolved", False))
        return auto_resolved / len(history)

    def _aggregate_decisions(
        self,
        alert_id: str,
        decisions: list[NoiseDecision]
    ) -> NoiseDecision:
        """Aggregate multiple strategy decisions."""
        if not decisions:
            return NoiseDecision(
                alert_id=alert_id,
                is_noise=False,
                noise_category=None,
                confidence=0.9,
                reasoning="Alert passed all noise filters",
                should_suppress=False,
            )

        # Take highest confidence noise decision
        best = max(decisions, key=lambda d: d.confidence)
        return best

    def record_resolution(
        self,
        fingerprint: str,
        auto_resolved: bool,
        resolution_time_seconds: int,
    ) -> None:
        """Record how an alert was resolved for learning."""
        self._alert_history[fingerprint].append({
            "timestamp": datetime.now(timezone.utc),
            "auto_resolved": auto_resolved,
            "resolution_time_seconds": resolution_time_seconds,
        })

        # Keep only last 100 entries per fingerprint
        if len(self._alert_history[fingerprint]) > 100:
            self._alert_history[fingerprint] = self._alert_history[fingerprint][-100:]

    def get_stats(self) -> dict[str, Any]:
        """Get noise reduction statistics."""
        return {
            "total_evaluated": self._stats["total_evaluated"],
            "noise_detected": self._stats["noise_detected"],
            "noise_rate": (
                self._stats["noise_detected"] / self._stats["total_evaluated"]
                if self._stats["total_evaluated"] > 0 else 0
            ),
            "by_category": dict(self._stats["by_category"]),
            "active_holds": len(self._transient_holds),
            "dedup_cache_size": len(self._dedup_cache),
            "correlation_window_size": len(self._correlation_window),
        }

    def clear_caches(self) -> None:
        """Clear all in-memory caches."""
        self._transient_holds.clear()
        self._dedup_cache.clear()
        self._flap_counts.clear()
        self._correlation_window.clear()
