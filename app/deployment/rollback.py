"""Rollback Manager - Automated rollback handling."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Awaitable
from uuid import uuid4

log = logging.getLogger("omnipotent.deployment.rollback")


class RollbackReason(str, Enum):
    """Reasons for rollback."""
    ERROR_RATE = "error_rate"
    LATENCY = "latency"
    AVAILABILITY = "availability"
    HEALTH_CHECK = "health_check"
    MANUAL = "manual"
    TIMEOUT = "timeout"
    DEPENDENCY_FAILURE = "dependency_failure"
    RESOURCE_EXHAUSTION = "resource_exhaustion"


@dataclass
class RollbackResult:
    """Result of a rollback operation."""
    rollback_id: str
    deployment_id: str
    service: str
    from_version: str
    to_version: str
    reason: RollbackReason
    reason_details: str
    success: bool
    started_at: datetime
    completed_at: datetime | None = None
    error: str | None = None
    metrics_at_rollback: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rollback_id": self.rollback_id,
            "deployment_id": self.deployment_id,
            "service": self.service,
            "from_version": self.from_version,
            "to_version": self.to_version,
            "reason": self.reason.value,
            "reason_details": self.reason_details,
            "success": self.success,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "error": self.error,
            "metrics_at_rollback": self.metrics_at_rollback,
        }


@dataclass
class RollbackPolicy:
    """Policy for automated rollback decisions."""
    # Thresholds
    max_error_rate: float = 0.05
    max_latency_p99_ms: float = 2000.0
    min_success_rate: float = 0.95
    min_availability: float = 0.99

    # Timing
    evaluation_window_seconds: int = 60
    min_samples_for_decision: int = 100
    cooldown_after_rollback_seconds: int = 300

    # Behavior
    auto_rollback_enabled: bool = True
    notify_on_rollback: bool = True
    require_approval_for_redeploy: bool = True


class RollbackManager:
    """Manages automated rollback decisions and execution.

    Features:
    - Continuous metrics monitoring
    - Automatic rollback on threshold breach
    - Cooldown periods to prevent flapping
    - Integration with alerting systems
    - Audit logging of all rollback decisions
    """

    def __init__(
        self,
        policy: RollbackPolicy | None = None,
        metrics_fetcher: Callable[[str, int], Awaitable[dict[str, float]]] | None = None,
        deployer: Callable[[str, str], Awaitable[bool]] | None = None,
        notifier: Callable[[str, dict], Awaitable[None]] | None = None,
    ):
        """Initialize the rollback manager.

        Args:
            policy: Rollback policy
            metrics_fetcher: Function to fetch metrics
            deployer: Function to deploy a version
            notifier: Function to send notifications
        """
        self.policy = policy or RollbackPolicy()
        self._metrics_fetcher = metrics_fetcher
        self._deployer = deployer
        self._notifier = notifier

        # State
        self._monitoring = False
        self._last_rollback: dict[str, datetime] = {}
        self._rollback_history: list[RollbackResult] = []

    async def execute_rollback(
        self,
        deployment_id: str,
        service: str,
        from_version: str,
        to_version: str,
        reason: RollbackReason,
        reason_details: str = "",
        metrics: dict[str, float] | None = None,
    ) -> RollbackResult:
        """Execute a rollback.

        Args:
            deployment_id: ID of the deployment being rolled back
            service: Service name
            from_version: Current (bad) version
            to_version: Target (good) version
            reason: Reason for rollback
            reason_details: Additional details
            metrics: Current metrics at time of rollback

        Returns:
            RollbackResult
        """
        rollback_id = f"rollback-{uuid4().hex[:8]}"
        started_at = datetime.now(timezone.utc)

        log.warning(
            "Executing rollback %s: %s %s -> %s (%s: %s)",
            rollback_id, service, from_version, to_version,
            reason.value, reason_details,
        )

        result = RollbackResult(
            rollback_id=rollback_id,
            deployment_id=deployment_id,
            service=service,
            from_version=from_version,
            to_version=to_version,
            reason=reason,
            reason_details=reason_details,
            success=False,
            started_at=started_at,
            metrics_at_rollback=metrics or {},
        )

        try:
            # Execute rollback
            if self._deployer:
                success = await self._deployer(service, to_version)
            else:
                log.info("Would rollback %s to %s", service, to_version)
                success = True

            result.success = success
            result.completed_at = datetime.now(timezone.utc)

            # Update state
            self._last_rollback[service] = datetime.now(timezone.utc)
            self._rollback_history.append(result)

            # Send notification
            if self.policy.notify_on_rollback and self._notifier:
                await self._notifier("rollback", result.to_dict())

            if success:
                log.info("Rollback %s completed successfully", rollback_id)
            else:
                log.error("Rollback %s failed", rollback_id)
                result.error = "Deployment failed"

        except Exception as e:
            result.error = str(e)
            result.completed_at = datetime.now(timezone.utc)
            log.exception("Rollback %s failed with exception", rollback_id)

        return result

    async def should_rollback(
        self,
        service: str,
        metrics: dict[str, float],
    ) -> tuple[bool, RollbackReason | None, str]:
        """Evaluate if a rollback should be triggered.

        Args:
            service: Service to evaluate
            metrics: Current metrics

        Returns:
            Tuple of (should_rollback, reason, details)
        """
        if not self.policy.auto_rollback_enabled:
            return False, None, ""

        # Check cooldown
        if service in self._last_rollback:
            elapsed = (datetime.now(timezone.utc) - self._last_rollback[service]).total_seconds()
            if elapsed < self.policy.cooldown_after_rollback_seconds:
                return False, None, f"In cooldown ({elapsed:.0f}s remaining)"

        # Check error rate
        error_rate = metrics.get("error_rate", 0.0)
        if error_rate > self.policy.max_error_rate:
            return True, RollbackReason.ERROR_RATE, f"Error rate {error_rate:.2%} > {self.policy.max_error_rate:.2%}"

        # Check latency
        latency_p99 = metrics.get("latency_p99", 0.0)
        if latency_p99 > self.policy.max_latency_p99_ms:
            return True, RollbackReason.LATENCY, f"P99 latency {latency_p99:.0f}ms > {self.policy.max_latency_p99_ms:.0f}ms"

        # Check success rate
        success_rate = metrics.get("success_rate", 1.0)
        if success_rate < self.policy.min_success_rate:
            return True, RollbackReason.AVAILABILITY, f"Success rate {success_rate:.2%} < {self.policy.min_success_rate:.2%}"

        # Check availability
        availability = metrics.get("availability", 1.0)
        if availability < self.policy.min_availability:
            return True, RollbackReason.AVAILABILITY, f"Availability {availability:.2%} < {self.policy.min_availability:.2%}"

        return False, None, ""

    async def start_monitoring(
        self,
        service: str,
        current_version: str,
        previous_version: str,
        deployment_id: str,
        check_interval_seconds: int = 30,
    ) -> None:
        """Start continuous monitoring for automatic rollback.

        Args:
            service: Service to monitor
            current_version: Current deployed version
            previous_version: Previous version to rollback to
            deployment_id: Deployment ID
            check_interval_seconds: Check interval
        """
        self._monitoring = True
        log.info("Starting rollback monitoring for %s", service)

        while self._monitoring:
            try:
                # Fetch metrics
                if self._metrics_fetcher:
                    metrics = await self._metrics_fetcher(
                        service,
                        self.policy.evaluation_window_seconds,
                    )
                else:
                    metrics = {}

                # Evaluate
                should_rollback, reason, details = await self.should_rollback(service, metrics)

                if should_rollback and reason:
                    await self.execute_rollback(
                        deployment_id=deployment_id,
                        service=service,
                        from_version=current_version,
                        to_version=previous_version,
                        reason=reason,
                        reason_details=details,
                        metrics=metrics,
                    )
                    self._monitoring = False
                    break

            except Exception as e:
                log.exception("Monitoring check failed: %s", e)

            await asyncio.sleep(check_interval_seconds)

    def stop_monitoring(self) -> None:
        """Stop continuous monitoring."""
        self._monitoring = False
        log.info("Stopped rollback monitoring")

    def get_history(self, service: str | None = None, limit: int = 10) -> list[RollbackResult]:
        """Get rollback history.

        Args:
            service: Filter by service
            limit: Max results

        Returns:
            List of rollback results
        """
        results = self._rollback_history

        if service:
            results = [r for r in results if r.service == service]

        return sorted(results, key=lambda r: r.started_at, reverse=True)[:limit]

    def is_in_cooldown(self, service: str) -> bool:
        """Check if a service is in rollback cooldown."""
        if service not in self._last_rollback:
            return False

        elapsed = (datetime.now(timezone.utc) - self._last_rollback[service]).total_seconds()
        return elapsed < self.policy.cooldown_after_rollback_seconds
