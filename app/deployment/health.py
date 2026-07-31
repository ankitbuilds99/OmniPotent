"""Health Checker - Service health validation for deployments."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import httpx

log = logging.getLogger("devcopilot.deployment.health")


class HealthStatus(str, Enum):
    """Health status of a service."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


@dataclass
class HealthCheckResult:
    """Result of a health check."""
    service: str
    version: str
    status: HealthStatus
    latency_ms: float
    checks: dict[str, bool] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "service": self.service,
            "version": self.version,
            "status": self.status.value,
            "latency_ms": self.latency_ms,
            "checks": self.checks,
            "details": self.details,
            "checked_at": self.checked_at.isoformat(),
            "error": self.error,
        }


@dataclass
class HealthCheckConfig:
    """Configuration for health checks."""
    # Endpoints
    health_path: str = "/healthz"
    ready_path: str = "/readyz"
    live_path: str = "/livez"

    # Timeouts
    timeout_seconds: float = 10.0
    retries: int = 3
    retry_delay_seconds: float = 2.0

    # Thresholds
    max_latency_ms: float = 1000.0
    min_success_rate: float = 0.9

    # Dependencies
    check_dependencies: bool = True
    dependency_timeout_seconds: float = 5.0


class HealthChecker:
    """Performs health checks on services during deployments.

    Features:
    - HTTP health endpoint checks
    - Dependency health verification
    - Kubernetes readiness/liveness integration
    - Retry logic with backoff
    - Comprehensive health reports
    """

    def __init__(self, config: HealthCheckConfig | None = None):
        """Initialize the health checker.

        Args:
            config: Health check configuration
        """
        self.config = config or HealthCheckConfig()
        self._client: httpx.AsyncClient | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.config.timeout_seconds)
        return self._client

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def check_health(
        self,
        service_url: str,
        service_name: str,
        version: str = "",
    ) -> HealthCheckResult:
        """Perform a comprehensive health check.

        Args:
            service_url: Base URL of the service
            service_name: Name of the service
            version: Version being checked

        Returns:
            HealthCheckResult
        """
        start_time = datetime.now(timezone.utc)
        checks: dict[str, bool] = {}
        details: dict[str, Any] = {}
        total_latency = 0.0

        # Health endpoint check
        health_ok, health_latency, health_details = await self._check_endpoint(
            f"{service_url}{self.config.health_path}",
            "health",
        )
        checks["health"] = health_ok
        details["health"] = health_details
        total_latency += health_latency

        # Readiness check
        ready_ok, ready_latency, ready_details = await self._check_endpoint(
            f"{service_url}{self.config.ready_path}",
            "ready",
        )
        checks["ready"] = ready_ok
        details["ready"] = ready_details
        total_latency += ready_latency

        # Liveness check
        live_ok, live_latency, live_details = await self._check_endpoint(
            f"{service_url}{self.config.live_path}",
            "live",
        )
        checks["live"] = live_ok
        details["live"] = live_details
        total_latency += live_latency

        # Determine overall status
        if all(checks.values()):
            if total_latency / 3 > self.config.max_latency_ms:
                status = HealthStatus.DEGRADED
            else:
                status = HealthStatus.HEALTHY
        elif checks.get("live", False):
            status = HealthStatus.DEGRADED
        else:
            status = HealthStatus.UNHEALTHY

        return HealthCheckResult(
            service=service_name,
            version=version,
            status=status,
            latency_ms=total_latency / 3,
            checks=checks,
            details=details,
            checked_at=start_time,
        )

    async def _check_endpoint(
        self,
        url: str,
        check_name: str,
    ) -> tuple[bool, float, dict[str, Any]]:
        """Check a single health endpoint with retries.

        Returns:
            Tuple of (success, latency_ms, details)
        """
        for attempt in range(self.config.retries):
            try:
                start = datetime.now(timezone.utc)
                response = await self.client.get(url)
                latency = (datetime.now(timezone.utc) - start).total_seconds() * 1000

                success = response.status_code == 200

                details = {
                    "status_code": response.status_code,
                    "latency_ms": latency,
                    "attempt": attempt + 1,
                }

                # Try to parse JSON response
                try:
                    body = response.json()
                    details["body"] = body
                except Exception:
                    pass

                if success:
                    return True, latency, details

            except httpx.RequestError as e:
                details = {
                    "error": str(e),
                    "attempt": attempt + 1,
                }

            # Retry with delay
            if attempt < self.config.retries - 1:
                await asyncio.sleep(self.config.retry_delay_seconds)

        return False, 0.0, details

    async def wait_for_healthy(
        self,
        service_url: str,
        service_name: str,
        version: str = "",
        timeout_seconds: float = 300.0,
        check_interval_seconds: float = 10.0,
    ) -> HealthCheckResult:
        """Wait for a service to become healthy.

        Args:
            service_url: Base URL of the service
            service_name: Name of the service
            version: Version being checked
            timeout_seconds: Max time to wait
            check_interval_seconds: Time between checks

        Returns:
            Final HealthCheckResult
        """
        deadline = datetime.now(timezone.utc).timestamp() + timeout_seconds

        while datetime.now(timezone.utc).timestamp() < deadline:
            result = await self.check_health(service_url, service_name, version)

            if result.status == HealthStatus.HEALTHY:
                return result

            log.info(
                "Service %s not yet healthy (status=%s), retrying...",
                service_name, result.status.value,
            )

            await asyncio.sleep(check_interval_seconds)

        # Timeout - return last result
        result = await self.check_health(service_url, service_name, version)
        result.error = "Timeout waiting for healthy status"
        return result

    async def check_dependencies(
        self,
        dependencies: list[dict[str, str]],
    ) -> dict[str, HealthCheckResult]:
        """Check health of service dependencies.

        Args:
            dependencies: List of {"name": ..., "url": ...}

        Returns:
            Dict of service name -> HealthCheckResult
        """
        results = {}

        async def check_one(dep: dict[str, str]) -> tuple[str, HealthCheckResult]:
            name = dep["name"]
            url = dep["url"]
            result = await self.check_health(url, name)
            return name, result

        tasks = [check_one(dep) for dep in dependencies]
        completed = await asyncio.gather(*tasks, return_exceptions=True)

        for item in completed:
            if isinstance(item, Exception):
                continue
            name, result = item
            results[name] = result

        return results

    async def validate_deployment(
        self,
        service_url: str,
        service_name: str,
        version: str,
        dependencies: list[dict[str, str]] | None = None,
    ) -> tuple[bool, dict[str, Any]]:
        """Validate a deployment is healthy and ready for traffic.

        Args:
            service_url: Base URL of the service
            service_name: Service name
            version: Deployed version
            dependencies: Optional list of dependencies to check

        Returns:
            Tuple of (valid, details)
        """
        details: dict[str, Any] = {}

        # Check service health
        service_result = await self.check_health(service_url, service_name, version)
        details["service"] = service_result.to_dict()

        if service_result.status == HealthStatus.UNHEALTHY:
            return False, details

        # Check dependencies
        if self.config.check_dependencies and dependencies:
            dep_results = await self.check_dependencies(dependencies)
            details["dependencies"] = {
                name: result.to_dict()
                for name, result in dep_results.items()
            }

            # Check if any dependency is unhealthy
            unhealthy_deps = [
                name for name, result in dep_results.items()
                if result.status == HealthStatus.UNHEALTHY
            ]

            if unhealthy_deps:
                details["unhealthy_dependencies"] = unhealthy_deps
                return False, details

        return True, details
