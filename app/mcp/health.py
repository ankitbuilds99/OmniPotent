"""MCP subprocess health monitoring.

Provides health checks for MCP server subprocesses to detect:
- Crashed or hung processes
- Memory leaks
- Connection failures
- Tool execution timeouts

Integrates with the main health endpoints and alerting system.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

log = logging.getLogger("omnipotent.mcp.health")


class MCPServerStatus(str, Enum):
    """Status of an MCP server subprocess."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"
    STOPPED = "stopped"


@dataclass
class MCPServerHealth:
    """Health status of a single MCP server."""
    name: str
    status: MCPServerStatus
    pid: int | None = None
    uptime_seconds: float = 0.0
    last_tool_call: datetime | None = None
    tool_calls_total: int = 0
    tool_errors_total: int = 0
    avg_latency_ms: float = 0.0
    memory_mb: float = 0.0
    last_check: datetime = field(default_factory=datetime.utcnow)
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status.value,
            "pid": self.pid,
            "uptime_seconds": self.uptime_seconds,
            "last_tool_call": self.last_tool_call.isoformat() if self.last_tool_call else None,
            "tool_calls_total": self.tool_calls_total,
            "tool_errors_total": self.tool_errors_total,
            "error_rate": (self.tool_errors_total / self.tool_calls_total * 100
                          if self.tool_calls_total > 0 else 0.0),
            "avg_latency_ms": round(self.avg_latency_ms, 2),
            "memory_mb": round(self.memory_mb, 2),
            "last_check": self.last_check.isoformat(),
            "error_message": self.error_message,
        }


@dataclass
class MCPHealthSummary:
    """Summary health status across all MCP servers."""
    overall_status: MCPServerStatus
    servers: list[MCPServerHealth]
    healthy_count: int
    degraded_count: int
    unhealthy_count: int
    total_tool_calls: int
    total_errors: int
    checked_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall_status": self.overall_status.value,
            "healthy_count": self.healthy_count,
            "degraded_count": self.degraded_count,
            "unhealthy_count": self.unhealthy_count,
            "total_tool_calls": self.total_tool_calls,
            "total_errors": self.total_errors,
            "error_rate": (self.total_errors / self.total_tool_calls * 100
                          if self.total_tool_calls > 0 else 0.0),
            "servers": [s.to_dict() for s in self.servers],
            "checked_at": self.checked_at.isoformat(),
        }


class MCPHealthMonitor:
    """Monitors health of MCP server subprocesses.

    Tracks:
    - Process liveness (is the subprocess still running?)
    - Tool call latency and error rates
    - Memory consumption
    - Idle time (time since last tool call)

    Thresholds:
    - Error rate > 10% = degraded
    - Error rate > 50% = unhealthy
    - Latency > 5000ms = degraded
    - Memory > 500MB = degraded
    - Process dead = unhealthy
    """

    def __init__(
        self,
        error_rate_degraded: float = 0.10,
        error_rate_unhealthy: float = 0.50,
        latency_degraded_ms: float = 5000.0,
        memory_degraded_mb: float = 500.0,
        idle_timeout_s: float = 3600.0,
    ):
        self._error_rate_degraded = error_rate_degraded
        self._error_rate_unhealthy = error_rate_unhealthy
        self._latency_degraded_ms = latency_degraded_ms
        self._memory_degraded_mb = memory_degraded_mb
        self._idle_timeout_s = idle_timeout_s

        # Per-server stats, keyed by server name
        self._stats: dict[str, _ServerStats] = {}

        # Process handles (populated by register_process)
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self._start_times: dict[str, float] = {}

    def register_process(self, name: str, process: asyncio.subprocess.Process) -> None:
        """Register an MCP subprocess for monitoring."""
        self._processes[name] = process
        self._start_times[name] = time.time()
        if name not in self._stats:
            self._stats[name] = _ServerStats()
        log.debug("Registered MCP process: %s (pid=%s)", name, process.pid)

    def unregister_process(self, name: str) -> None:
        """Unregister an MCP subprocess."""
        self._processes.pop(name, None)
        self._start_times.pop(name, None)
        log.debug("Unregistered MCP process: %s", name)

    def record_tool_call(
        self,
        server_name: str,
        tool_name: str,
        latency_ms: float,
        success: bool,
        error: str | None = None,
    ) -> None:
        """Record a tool call for health tracking."""
        if server_name not in self._stats:
            self._stats[server_name] = _ServerStats()

        stats = self._stats[server_name]
        stats.tool_calls += 1
        stats.last_call_time = time.time()
        stats.latency_samples.append(latency_ms)

        # Keep only last 100 latency samples
        if len(stats.latency_samples) > 100:
            stats.latency_samples = stats.latency_samples[-100:]

        if not success:
            stats.errors += 1
            if error:
                stats.last_error = error

    async def check_server(self, name: str) -> MCPServerHealth:
        """Check health of a single MCP server."""
        stats = self._stats.get(name, _ServerStats())
        process = self._processes.get(name)

        # Basic status
        health = MCPServerHealth(
            name=name,
            status=MCPServerStatus.UNKNOWN,
            tool_calls_total=stats.tool_calls,
            tool_errors_total=stats.errors,
        )

        # Process status
        if process is None:
            health.status = MCPServerStatus.STOPPED
            return health

        # Check if process is alive
        if process.returncode is not None:
            health.status = MCPServerStatus.UNHEALTHY
            health.error_message = f"Process exited with code {process.returncode}"
            return health

        health.pid = process.pid
        health.uptime_seconds = time.time() - self._start_times.get(name, time.time())

        # Last tool call time
        if stats.last_call_time:
            health.last_tool_call = datetime.utcfromtimestamp(stats.last_call_time)

        # Average latency
        if stats.latency_samples:
            health.avg_latency_ms = sum(stats.latency_samples) / len(stats.latency_samples)

        # Memory usage (if psutil available)
        try:
            import psutil
            proc = psutil.Process(process.pid)
            health.memory_mb = proc.memory_info().rss / (1024 * 1024)
        except (ImportError, Exception):
            pass

        # Determine status based on metrics
        health.status = self._evaluate_status(health, stats)

        if stats.last_error:
            health.error_message = stats.last_error

        return health

    def _evaluate_status(self, health: MCPServerHealth, stats: _ServerStats) -> MCPServerStatus:
        """Evaluate overall health status from metrics."""
        issues = []

        # Error rate check
        if stats.tool_calls > 0:
            error_rate = stats.errors / stats.tool_calls
            if error_rate >= self._error_rate_unhealthy:
                return MCPServerStatus.UNHEALTHY
            if error_rate >= self._error_rate_degraded:
                issues.append("high_error_rate")

        # Latency check
        if health.avg_latency_ms > self._latency_degraded_ms:
            issues.append("high_latency")

        # Memory check
        if health.memory_mb > self._memory_degraded_mb:
            issues.append("high_memory")

        # Idle timeout check
        if stats.last_call_time:
            idle_time = time.time() - stats.last_call_time
            if idle_time > self._idle_timeout_s:
                issues.append("idle_timeout")

        if issues:
            return MCPServerStatus.DEGRADED

        return MCPServerStatus.HEALTHY

    async def check_all(self) -> MCPHealthSummary:
        """Check health of all registered MCP servers."""
        servers: list[MCPServerHealth] = []

        for name in set(self._processes.keys()) | set(self._stats.keys()):
            health = await self.check_server(name)
            servers.append(health)

        # Calculate counts
        healthy = sum(1 for s in servers if s.status == MCPServerStatus.HEALTHY)
        degraded = sum(1 for s in servers if s.status == MCPServerStatus.DEGRADED)
        unhealthy = sum(1 for s in servers if s.status == MCPServerStatus.UNHEALTHY)

        # Overall status
        if unhealthy > 0:
            overall = MCPServerStatus.UNHEALTHY
        elif degraded > 0:
            overall = MCPServerStatus.DEGRADED
        elif healthy > 0:
            overall = MCPServerStatus.HEALTHY
        else:
            overall = MCPServerStatus.UNKNOWN

        total_calls = sum(s.tool_calls_total for s in servers)
        total_errors = sum(s.tool_errors_total for s in servers)

        return MCPHealthSummary(
            overall_status=overall,
            servers=servers,
            healthy_count=healthy,
            degraded_count=degraded,
            unhealthy_count=unhealthy,
            total_tool_calls=total_calls,
            total_errors=total_errors,
        )

    async def run_periodic_check(
        self,
        interval_s: float = 60.0,
        callback: Any = None,
    ) -> None:
        """Run periodic health checks in the background."""
        while True:
            try:
                summary = await self.check_all()

                # Log any unhealthy servers
                for server in summary.servers:
                    if server.status == MCPServerStatus.UNHEALTHY:
                        log.error(
                            "MCP server %s is unhealthy: %s",
                            server.name,
                            server.error_message or "unknown error",
                        )
                    elif server.status == MCPServerStatus.DEGRADED:
                        log.warning(
                            "MCP server %s is degraded (latency=%.0fms, errors=%d/%d)",
                            server.name,
                            server.avg_latency_ms,
                            server.tool_errors_total,
                            server.tool_calls_total,
                        )

                if callback:
                    await callback(summary)

            except Exception as e:
                log.error("MCP health check failed: %s", e)

            await asyncio.sleep(interval_s)


@dataclass
class _ServerStats:
    """Internal stats tracking for a server."""
    tool_calls: int = 0
    errors: int = 0
    last_call_time: float | None = None
    latency_samples: list[float] = field(default_factory=list)
    last_error: str | None = None


# Global singleton
_monitor: MCPHealthMonitor | None = None


def get_health_monitor() -> MCPHealthMonitor:
    """Get the global MCP health monitor."""
    global _monitor
    if _monitor is None:
        _monitor = MCPHealthMonitor()
    return _monitor
