"""Deployment Strategies - Blue-Green and Canary implementations."""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Awaitable
from uuid import uuid4

log = logging.getLogger("devcopilot.deployment.strategies")


class DeploymentStatus(str, Enum):
    """Status of a deployment."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    VALIDATING = "validating"
    PROMOTING = "promoting"
    COMPLETED = "completed"
    ROLLING_BACK = "rolling_back"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"


@dataclass
class DeploymentConfig:
    """Configuration for a deployment."""
    # Target
    service: str
    namespace: str = "default"
    target_version: str = ""
    current_version: str = ""

    # Strategy-specific
    strategy: str = "canary"  # canary | blue-green

    # Canary settings
    canary_steps: list[int] = field(default_factory=lambda: [10, 25, 50, 75, 100])
    canary_interval_seconds: int = 300
    canary_metrics_window_seconds: int = 60

    # Blue-green settings
    blue_green_switch_delay_seconds: int = 60

    # Health checks
    health_check_path: str = "/healthz"
    health_check_timeout_seconds: int = 10
    health_check_retries: int = 3

    # Rollback thresholds
    max_error_rate: float = 0.05
    max_latency_p99_ms: float = 2000.0
    min_success_rate: float = 0.95

    # Timeouts
    deployment_timeout_seconds: int = 1800
    validation_timeout_seconds: int = 300


@dataclass
class DeploymentResult:
    """Result of a deployment operation."""
    deployment_id: str
    service: str
    strategy: str
    status: DeploymentStatus
    target_version: str
    previous_version: str
    started_at: datetime
    completed_at: datetime | None = None
    current_step: int = 0
    total_steps: int = 0
    traffic_percentage: int = 0
    error: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)
    rollback_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "deployment_id": self.deployment_id,
            "service": self.service,
            "strategy": self.strategy,
            "status": self.status.value,
            "target_version": self.target_version,
            "previous_version": self.previous_version,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "current_step": self.current_step,
            "total_steps": self.total_steps,
            "traffic_percentage": self.traffic_percentage,
            "error": self.error,
            "metrics": self.metrics,
            "rollback_reason": self.rollback_reason,
        }


class DeploymentStrategy(ABC):
    """Base class for deployment strategies."""

    def __init__(
        self,
        config: DeploymentConfig,
        metrics_fetcher: Callable[[str, int], Awaitable[dict[str, float]]] | None = None,
        deployer: Callable[[str, str, int], Awaitable[bool]] | None = None,
    ):
        """Initialize the strategy.

        Args:
            config: Deployment configuration
            metrics_fetcher: Function to fetch metrics (service, window_seconds) -> metrics
            deployer: Function to deploy (service, version, traffic_percent) -> success
        """
        self.config = config
        self._metrics_fetcher = metrics_fetcher
        self._deployer = deployer
        self._deployment_id = f"deploy-{uuid4().hex[:8]}"

    @abstractmethod
    async def deploy(self) -> DeploymentResult:
        """Execute the deployment."""
        pass

    @abstractmethod
    async def rollback(self, reason: str = "") -> DeploymentResult:
        """Rollback the deployment."""
        pass

    async def _fetch_metrics(self) -> dict[str, float]:
        """Fetch current metrics for validation."""
        if self._metrics_fetcher:
            return await self._metrics_fetcher(
                self.config.service,
                self.config.canary_metrics_window_seconds,
            )
        return {"error_rate": 0.01, "latency_p99": 100.0, "success_rate": 0.99}

    async def _deploy_version(self, version: str, traffic_percent: int) -> bool:
        """Deploy a version with specified traffic percentage."""
        if self._deployer:
            return await self._deployer(self.config.service, version, traffic_percent)
        log.info(
            "Would deploy %s version %s at %d%% traffic",
            self.config.service, version, traffic_percent,
        )
        return True

    def _check_metrics(self, metrics: dict[str, float]) -> tuple[bool, str]:
        """Check if metrics are within acceptable thresholds."""
        error_rate = metrics.get("error_rate", 0.0)
        latency_p99 = metrics.get("latency_p99", 0.0)
        success_rate = metrics.get("success_rate", 1.0)

        if error_rate > self.config.max_error_rate:
            return False, f"Error rate {error_rate:.2%} > {self.config.max_error_rate:.2%}"

        if latency_p99 > self.config.max_latency_p99_ms:
            return False, f"P99 latency {latency_p99:.0f}ms > {self.config.max_latency_p99_ms:.0f}ms"

        if success_rate < self.config.min_success_rate:
            return False, f"Success rate {success_rate:.2%} < {self.config.min_success_rate:.2%}"

        return True, ""


class CanaryStrategy(DeploymentStrategy):
    """Canary deployment with progressive traffic shifting.

    Gradually shifts traffic to the new version in steps:
    1. Deploy new version with 0% traffic
    2. Shift traffic incrementally (10% -> 25% -> 50% -> 75% -> 100%)
    3. Validate metrics at each step
    4. Rollback automatically if metrics degrade
    """

    async def deploy(self) -> DeploymentResult:
        """Execute canary deployment."""
        started_at = datetime.now(timezone.utc)
        steps = self.config.canary_steps
        total_steps = len(steps)

        result = DeploymentResult(
            deployment_id=self._deployment_id,
            service=self.config.service,
            strategy="canary",
            status=DeploymentStatus.IN_PROGRESS,
            target_version=self.config.target_version,
            previous_version=self.config.current_version,
            started_at=started_at,
            total_steps=total_steps,
        )

        log.info(
            "Starting canary deployment %s: %s -> %s",
            self._deployment_id,
            self.config.current_version,
            self.config.target_version,
        )

        try:
            # Deploy new version with 0% traffic
            success = await self._deploy_version(self.config.target_version, 0)
            if not success:
                result.status = DeploymentStatus.FAILED
                result.error = "Failed to deploy new version"
                return result

            # Progressive traffic shift
            for i, traffic_percent in enumerate(steps):
                result.current_step = i + 1
                result.traffic_percentage = traffic_percent

                log.info(
                    "Canary step %d/%d: shifting to %d%% traffic",
                    i + 1, total_steps, traffic_percent,
                )

                # Shift traffic
                success = await self._deploy_version(self.config.target_version, traffic_percent)
                if not success:
                    await self.rollback(f"Failed to shift traffic to {traffic_percent}%")
                    result.status = DeploymentStatus.ROLLED_BACK
                    result.rollback_reason = f"Traffic shift failed at {traffic_percent}%"
                    return result

                # Wait for metrics to stabilize
                await asyncio.sleep(self.config.canary_interval_seconds)

                # Validate metrics
                result.status = DeploymentStatus.VALIDATING
                metrics = await self._fetch_metrics()
                result.metrics = metrics

                healthy, reason = self._check_metrics(metrics)
                if not healthy:
                    log.warning("Canary validation failed: %s", reason)
                    await self.rollback(reason)
                    result.status = DeploymentStatus.ROLLED_BACK
                    result.rollback_reason = reason
                    return result

                log.info("Canary step %d validated successfully", i + 1)

            # Complete
            result.status = DeploymentStatus.COMPLETED
            result.completed_at = datetime.now(timezone.utc)
            result.traffic_percentage = 100

            log.info(
                "Canary deployment %s completed successfully",
                self._deployment_id,
            )

            return result

        except asyncio.TimeoutError:
            result.status = DeploymentStatus.FAILED
            result.error = "Deployment timed out"
            await self.rollback("Timeout")
            return result

        except Exception as e:
            result.status = DeploymentStatus.FAILED
            result.error = str(e)
            await self.rollback(str(e))
            return result

    async def rollback(self, reason: str = "") -> DeploymentResult:
        """Rollback to previous version."""
        log.warning(
            "Rolling back canary deployment %s: %s",
            self._deployment_id, reason,
        )

        # Shift all traffic back to current version
        await self._deploy_version(self.config.current_version, 100)

        # Scale down new version
        await self._deploy_version(self.config.target_version, 0)

        return DeploymentResult(
            deployment_id=self._deployment_id,
            service=self.config.service,
            strategy="canary",
            status=DeploymentStatus.ROLLED_BACK,
            target_version=self.config.target_version,
            previous_version=self.config.current_version,
            started_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
            rollback_reason=reason,
        )


class BlueGreenStrategy(DeploymentStrategy):
    """Blue-green deployment with instant traffic switch.

    Maintains two identical environments:
    1. Deploy new version to inactive environment (green)
    2. Validate green environment health
    3. Switch all traffic from blue to green instantly
    4. Keep blue environment for instant rollback
    """

    async def deploy(self) -> DeploymentResult:
        """Execute blue-green deployment."""
        started_at = datetime.now(timezone.utc)

        result = DeploymentResult(
            deployment_id=self._deployment_id,
            service=self.config.service,
            strategy="blue-green",
            status=DeploymentStatus.IN_PROGRESS,
            target_version=self.config.target_version,
            previous_version=self.config.current_version,
            started_at=started_at,
            total_steps=3,  # Deploy, Validate, Switch
        )

        log.info(
            "Starting blue-green deployment %s: %s -> %s",
            self._deployment_id,
            self.config.current_version,
            self.config.target_version,
        )

        try:
            # Step 1: Deploy to green (inactive) environment
            result.current_step = 1
            log.info("Step 1/3: Deploying to green environment")

            success = await self._deploy_version(self.config.target_version, 0)
            if not success:
                result.status = DeploymentStatus.FAILED
                result.error = "Failed to deploy to green environment"
                return result

            # Step 2: Validate green environment
            result.current_step = 2
            result.status = DeploymentStatus.VALIDATING
            log.info("Step 2/3: Validating green environment")

            # Send test traffic to green
            await self._deploy_version(self.config.target_version, 5)  # 5% test traffic
            await asyncio.sleep(self.config.canary_metrics_window_seconds)

            metrics = await self._fetch_metrics()
            result.metrics = metrics

            healthy, reason = self._check_metrics(metrics)
            if not healthy:
                log.warning("Green environment validation failed: %s", reason)
                await self.rollback(reason)
                result.status = DeploymentStatus.ROLLED_BACK
                result.rollback_reason = reason
                return result

            # Step 3: Switch traffic
            result.current_step = 3
            result.status = DeploymentStatus.PROMOTING
            log.info("Step 3/3: Switching traffic to green")

            # Brief delay before full switch
            await asyncio.sleep(self.config.blue_green_switch_delay_seconds)

            # Full traffic switch
            success = await self._deploy_version(self.config.target_version, 100)
            if not success:
                await self.rollback("Traffic switch failed")
                result.status = DeploymentStatus.ROLLED_BACK
                result.rollback_reason = "Traffic switch failed"
                return result

            result.status = DeploymentStatus.COMPLETED
            result.completed_at = datetime.now(timezone.utc)
            result.traffic_percentage = 100

            log.info(
                "Blue-green deployment %s completed successfully",
                self._deployment_id,
            )

            return result

        except Exception as e:
            result.status = DeploymentStatus.FAILED
            result.error = str(e)
            await self.rollback(str(e))
            return result

    async def rollback(self, reason: str = "") -> DeploymentResult:
        """Rollback to blue environment."""
        log.warning(
            "Rolling back blue-green deployment %s: %s",
            self._deployment_id, reason,
        )

        # Instant switch back to blue
        await self._deploy_version(self.config.current_version, 100)

        return DeploymentResult(
            deployment_id=self._deployment_id,
            service=self.config.service,
            strategy="blue-green",
            status=DeploymentStatus.ROLLED_BACK,
            target_version=self.config.target_version,
            previous_version=self.config.current_version,
            started_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
            rollback_reason=reason,
        )


def create_deployment(config: DeploymentConfig, **kwargs) -> DeploymentStrategy:
    """Factory function to create the appropriate deployment strategy."""
    if config.strategy == "canary":
        return CanaryStrategy(config, **kwargs)
    elif config.strategy == "blue-green":
        return BlueGreenStrategy(config, **kwargs)
    else:
        raise ValueError(f"Unknown deployment strategy: {config.strategy}")
