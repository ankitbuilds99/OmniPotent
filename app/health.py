"""Production health check system for Kubernetes.

Provides:
- Liveness probe: Is the process alive?
- Readiness probe: Can the service handle requests?
- Startup probe: Has initialization completed?
- Dependency health: Database, Redis, external services
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Awaitable

log = logging.getLogger("omnipotent.health")


class HealthStatus(Enum):
    """Health check status values."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


@dataclass
class ComponentHealth:
    """Health status of a single component."""

    name: str
    status: HealthStatus
    latency_ms: float = 0.0
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    last_check: datetime = field(default_factory=datetime.now)


@dataclass
class SystemHealth:
    """Overall system health status."""

    status: HealthStatus
    components: dict[str, ComponentHealth] = field(default_factory=dict)
    version: str = ""
    uptime_seconds: float = 0.0
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "version": self.version,
            "uptime_seconds": round(self.uptime_seconds, 2),
            "timestamp": self.timestamp.isoformat(),
            "components": {
                name: {
                    "status": c.status.value,
                    "latency_ms": round(c.latency_ms, 2),
                    "message": c.message,
                    "details": c.details,
                }
                for name, c in self.components.items()
            },
        }


HealthCheckFn = Callable[[], Awaitable[ComponentHealth]]


class HealthChecker:
    """Central health check manager.

    Registers component health checks and provides aggregated status
    for Kubernetes probes.
    """

    def __init__(self) -> None:
        self._checks: dict[str, HealthCheckFn] = {}
        self._start_time = time.monotonic()
        self._version = "1.0.0"
        self._startup_complete = False
        self._last_results: dict[str, ComponentHealth] = {}

    def register(self, name: str, check: HealthCheckFn) -> None:
        """Register a health check function."""
        self._checks[name] = check

    def set_version(self, version: str) -> None:
        """Set application version for health responses."""
        self._version = version

    def mark_startup_complete(self) -> None:
        """Mark startup as complete (for startup probe)."""
        self._startup_complete = True
        log.info("Startup complete")

    @property
    def startup_complete(self) -> bool:
        return self._startup_complete

    async def check_component(self, name: str) -> ComponentHealth:
        """Run a single component health check."""
        check = self._checks.get(name)
        if not check:
            return ComponentHealth(
                name=name,
                status=HealthStatus.UNHEALTHY,
                message=f"Unknown component: {name}",
            )

        start = time.monotonic()
        try:
            result = await asyncio.wait_for(check(), timeout=5.0)
            result.latency_ms = (time.monotonic() - start) * 1000
            self._last_results[name] = result
            return result
        except asyncio.TimeoutError:
            return ComponentHealth(
                name=name,
                status=HealthStatus.UNHEALTHY,
                latency_ms=(time.monotonic() - start) * 1000,
                message="Health check timed out",
            )
        except Exception as e:
            log.warning("Health check failed for %s: %s", name, e)
            return ComponentHealth(
                name=name,
                status=HealthStatus.UNHEALTHY,
                latency_ms=(time.monotonic() - start) * 1000,
                message=str(e),
            )

    async def check_all(self, timeout: float = 10.0) -> SystemHealth:
        """Run all health checks and aggregate status."""
        uptime = time.monotonic() - self._start_time

        if not self._checks:
            return SystemHealth(
                status=HealthStatus.HEALTHY,
                version=self._version,
                uptime_seconds=uptime,
            )

        try:
            results = await asyncio.wait_for(
                asyncio.gather(*[
                    self.check_component(name)
                    for name in self._checks
                ], return_exceptions=True),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            return SystemHealth(
                status=HealthStatus.UNHEALTHY,
                version=self._version,
                uptime_seconds=uptime,
                components={
                    name: ComponentHealth(
                        name=name,
                        status=HealthStatus.UNHEALTHY,
                        message="Overall health check timed out",
                    )
                    for name in self._checks
                },
            )

        components: dict[str, ComponentHealth] = {}
        for name, result in zip(self._checks.keys(), results):
            if isinstance(result, BaseException):
                components[name] = ComponentHealth(
                    name=name,
                    status=HealthStatus.UNHEALTHY,
                    message=str(result),
                )
            elif isinstance(result, ComponentHealth):
                components[name] = result
            else:
                components[name] = ComponentHealth(
                    name=name,
                    status=HealthStatus.UNHEALTHY,
                    message="Unexpected result type",
                )

        unhealthy = sum(1 for c in components.values() if c.status == HealthStatus.UNHEALTHY)
        degraded = sum(1 for c in components.values() if c.status == HealthStatus.DEGRADED)

        if unhealthy > 0:
            status = HealthStatus.UNHEALTHY
        elif degraded > 0:
            status = HealthStatus.DEGRADED
        else:
            status = HealthStatus.HEALTHY

        return SystemHealth(
            status=status,
            version=self._version,
            uptime_seconds=uptime,
            components=components,
        )

    async def liveness(self) -> tuple[bool, dict[str, Any]]:
        """Liveness probe: Is the process alive?

        Returns (alive, details). Always returns True unless the process
        is deadlocked or critically broken.
        """
        return True, {
            "status": "alive",
            "uptime_seconds": round(time.monotonic() - self._start_time, 2),
        }

    async def readiness(self) -> tuple[bool, dict[str, Any]]:
        """Readiness probe: Can the service handle requests?

        Returns (ready, details). Checks all registered dependencies.
        """
        health = await self.check_all()
        ready = health.status != HealthStatus.UNHEALTHY
        return ready, health.to_dict()

    async def startup(self) -> tuple[bool, dict[str, Any]]:
        """Startup probe: Has initialization completed?

        Returns (started, details). Returns True only after mark_startup_complete().
        """
        return self._startup_complete, {
            "started": self._startup_complete,
            "uptime_seconds": round(time.monotonic() - self._start_time, 2),
        }


_checker: HealthChecker | None = None


def get_health_checker() -> HealthChecker:
    """Get the global health checker instance."""
    global _checker
    if _checker is None:
        _checker = HealthChecker()
    return _checker


async def check_database() -> ComponentHealth:
    """Health check for database connectivity."""
    try:
        from app.database import check_db_health
        result = await check_db_health()
        return ComponentHealth(
            name="database",
            status=HealthStatus.HEALTHY if result.healthy else HealthStatus.UNHEALTHY,
            latency_ms=result.latency_ms,
            message=result.error or "Connected",
            details={
                "pool_size": result.connection_pool_size,
                "pool_checked_out": result.connection_pool_checked_out,
            },
        )
    except Exception as e:
        return ComponentHealth(
            name="database",
            status=HealthStatus.UNHEALTHY,
            message=str(e),
        )


async def check_redis() -> ComponentHealth:
    """Health check for Redis connectivity."""
    try:
        from app.config import get_settings
        url = get_settings().copilot_redis_url
        if not url:
            return ComponentHealth(
                name="redis",
                status=HealthStatus.HEALTHY,
                message="Not configured (using in-memory)",
            )

        import redis.asyncio as redis
        start = time.monotonic()
        client = redis.from_url(url)
        await client.ping()
        latency = (time.monotonic() - start) * 1000
        await client.aclose()

        return ComponentHealth(
            name="redis",
            status=HealthStatus.HEALTHY,
            latency_ms=latency,
            message="Connected",
        )
    except Exception as e:
        return ComponentHealth(
            name="redis",
            status=HealthStatus.DEGRADED,
            message=str(e),
        )


async def check_llm() -> ComponentHealth:
    """Health check for LLM API connectivity."""
    try:
        from app.llm import active_api_key
        from app.config import get_settings

        s = get_settings()
        has_key = bool(active_api_key())

        if s.copilot_multi_tenant:
            return ComponentHealth(
                name="llm",
                status=HealthStatus.HEALTHY,
                message="Multi-tenant mode (per-tenant keys)",
            )

        if not has_key:
            return ComponentHealth(
                name="llm",
                status=HealthStatus.UNHEALTHY,
                message="No API key configured",
            )

        return ComponentHealth(
            name="llm",
            status=HealthStatus.HEALTHY,
            message=f"Provider: {s.copilot_provider}",
        )
    except Exception as e:
        return ComponentHealth(
            name="llm",
            status=HealthStatus.UNHEALTHY,
            message=str(e),
        )


def setup_health_checks() -> HealthChecker:
    """Register all health checks."""
    checker = get_health_checker()
    checker.register("database", check_database)
    checker.register("redis", check_redis)
    checker.register("llm", check_llm)
    return checker
