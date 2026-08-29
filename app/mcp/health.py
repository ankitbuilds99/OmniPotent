"""MCP subprocess health monitoring.

Provides health checks for MCP server subprocesses to detect:
- Crashed or hung processes
- Memory leaks
- Connection failures
- Tool execution timeouts

Integrates with the main health endpoints and alerting system.

Who feeds this: `app/mcp/client.py::load_mcp_tools` is the only place that opens
MCP sessions, so it is the only producer. It calls `register_session` for every
server that completes the stdio handshake, `record_start_failure` for every one
that does not (with the reason), `release_session` when a CopilotSession tears
its transports down, and `record_tool_call` from the wrapper it puts around each
loaded tool. Nothing else may invent entries here — an unfed monitor reports
UNKNOWN with an empty server list, which is the truthful answer for "no MCP
session is open", and that is deliberately different from "every server is fine".

Two liveness sources exist, and they are not equivalent:
- `register_process` gives a real subprocess handle, so a crash between tool
  calls is detected directly (returncode) and memory can be sampled.
- `register_session` is what the stdio path can actually offer: langchain-mcp
  -adapters and the MCP SDK never surface the spawned process, so liveness is
  inferred from a completed handshake plus the outcome of subsequent tool calls.
`MCPServerHealth.liveness_source` says which one produced a given row, so a
consumer can tell a measured-live process from an inferred-live session instead
of reading both as the same claim.

`check_all` splits what it knows into two lists rather than flattening them,
because its rows become status claims on surfaces that read them as claims:
/healthz counts them and app/status_page/store.py turns each one into a public
component.
- `servers` holds rows something is currently measuring: a live process handle,
  an open stdio session, or a server that was recently asked to start and
  refused. Every one carries a real verdict, so healthy + degraded + unhealthy
  == len(servers). "Recently" is load-bearing: a start failure is one observation
  that nothing repeats until something next asks for MCP tools, so it expires
  (`start_failure_ttl_s`) and the row joins the unmeasured ones rather than
  publishing an outage forever on the strength of a single old attempt.
- `unmeasured` holds servers this process has run before but that nothing is
  running now — STOPPED, with their accumulated counters intact. They are still
  reported, so "this server exists and nothing is measuring it" is sayable, but
  they are kept out of the verdict counts and out of anything that renders a row
  as a live claim.
Flattening the two made the status page (which maps STOPPED to MAJOR_OUTAGE)
announce a major outage for every server the deployment had ever used, in the
ordinary idle steady state after the last CopilotSession closed. Dropping the
second list entirely would have been the opposite error — a server that ran a
hundred tool calls this morning would simply vanish.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
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
    # Failures that say something about the server's health: the transport broke,
    # or it never answered. A call the server answered with an error result is a
    # fault, counted separately — see MCPHealthMonitor.record_tool_call.
    tool_errors_total: int = 0
    tool_faults_total: int = 0
    avg_latency_ms: float = 0.0
    # None = not sampled (no process handle to read RSS from, or psutil missing).
    # A stdio session reports no memory at all rather than a plausible-looking 0.
    memory_mb: float | None = None
    last_check: datetime = field(default_factory=datetime.utcnow)
    error_message: str | None = None
    # The last fault the server itself reported (a refusal, a rejected argument,
    # a tool that raised). It does not move the status, but it is the only place
    # the reason survives, and a row reading HEALTHY with a non-zero fault count
    # and no explanation is the "fake success" this repo does not ship.
    last_fault_message: str | None = None
    # How this row's liveness was established: "process" (a real subprocess
    # handle we polled), "session" (a completed stdio handshake plus tool-call
    # outcomes) or "none" (nothing measured it). Never guess from pid: a stdio
    # session has no pid to report and would otherwise read as unmeasured.
    liveness_source: str = "none"
    open_sessions: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status.value,
            "pid": self.pid,
            "liveness_source": self.liveness_source,
            "open_sessions": self.open_sessions,
            "uptime_seconds": self.uptime_seconds,
            "last_tool_call": self.last_tool_call.isoformat() if self.last_tool_call else None,
            "tool_calls_total": self.tool_calls_total,
            "tool_errors_total": self.tool_errors_total,
            "tool_faults_total": self.tool_faults_total,
            "error_rate": (self.tool_errors_total / self.tool_calls_total * 100
                          if self.tool_calls_total > 0 else 0.0),
            "avg_latency_ms": round(self.avg_latency_ms, 2),
            "memory_mb": round(self.memory_mb, 2) if self.memory_mb is not None else None,
            "last_check": self.last_check.isoformat(),
            "error_message": self.error_message,
            "last_fault_message": self.last_fault_message,
        }


@dataclass
class MCPHealthSummary:
    """Summary health status across all MCP servers.

    `servers` and `unmeasured` are disjoint and mean different things, and a
    consumer that collapses them will publish one of the two mistakes this class
    exists to avoid. `servers` is what is being measured right now: the three
    counts partition it exactly, so `healthy_count + degraded_count +
    unhealthy_count == len(servers)` always holds and a caller can render them as
    a verdict. `unmeasured` is every server this process has run whose sessions
    have all closed — real history, no current observation — so it carries the
    accumulated counters but no verdict and must not be rendered as one.

    An empty `servers` with `overall_status` UNKNOWN means "no MCP session is
    open". It does not mean zero servers are healthy, and `measured_count` is
    there so a caller can tell those apart without re-deriving it.
    """
    overall_status: MCPServerStatus
    servers: list[MCPServerHealth]
    healthy_count: int
    degraded_count: int
    unhealthy_count: int
    total_tool_calls: int
    total_errors: int
    checked_at: datetime = field(default_factory=datetime.utcnow)
    # Defaulted, so the many places that build a summary from measured rows alone
    # keep working; check_all() always fills it.
    unmeasured: list[MCPServerHealth] = field(default_factory=list)

    @property
    def measured_count(self) -> int:
        """Servers with a live verdict behind them. Zero means nothing measured."""
        return len(self.servers)

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall_status": self.overall_status.value,
            "measured_count": self.measured_count,
            "healthy_count": self.healthy_count,
            "degraded_count": self.degraded_count,
            "unhealthy_count": self.unhealthy_count,
            "unmeasured_count": len(self.unmeasured),
            "total_tool_calls": self.total_tool_calls,
            "total_errors": self.total_errors,
            "error_rate": (self.total_errors / self.total_tool_calls * 100
                          if self.total_tool_calls > 0 else 0.0),
            "servers": [s.to_dict() for s in self.servers],
            "unmeasured": [s.to_dict() for s in self.unmeasured],
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
        start_failure_ttl_s: float = 900.0,
    ):
        self._error_rate_degraded = error_rate_degraded
        self._error_rate_unhealthy = error_rate_unhealthy
        self._latency_degraded_ms = latency_degraded_ms
        self._memory_degraded_mb = memory_degraded_mb
        self._idle_timeout_s = idle_timeout_s
        self._start_failure_ttl_s = start_failure_ttl_s

        # Per-server stats, keyed by server name
        self._stats: dict[str, _ServerStats] = {}

        # Process handles (populated by register_process)
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self._start_times: dict[str, float] = {}

        # Handshake-only registrations (populated by register_session). Counted,
        # not flagged: several CopilotSessions run the same server at once, and
        # one of them closing must not report the server stopped while the others
        # are still using it.
        self._sessions: dict[str, int] = {}

        # Why a server is not up, from the code that tried to start it, with the
        # moment the attempt was made. Kept so "no tools" has a reason attached
        # instead of looking like a healthy deployment with nothing to do; timed
        # because nothing refreshes it. Only `load_mcp_tools` writes here, and it
        # runs when a CopilotSession opens, so between sessions this is the one
        # observation in the monitor that nobody is re-taking.
        self._start_failures: dict[str, _StartFailure] = {}

    def register_process(self, name: str, process: asyncio.subprocess.Process) -> None:
        """Register an MCP subprocess for monitoring."""
        self._processes[name] = process
        self._start_times[name] = time.time()
        if name not in self._stats:
            self._stats[name] = _ServerStats()
        self._start_failures.pop(name, None)
        log.debug("Registered MCP process: %s (pid=%s)", name, process.pid)

    def unregister_process(self, name: str) -> None:
        """Unregister an MCP subprocess."""
        self._processes.pop(name, None)
        self._start_times.pop(name, None)
        log.debug("Unregistered MCP process: %s", name)

    def register_session(self, name: str, tool_count: int = 0) -> None:
        """Record that an MCP session for `name` completed its handshake.

        The stdio path cannot hand us a process object — neither
        langchain-mcp-adapters nor the MCP SDK exposes the subprocess it spawned
        — so this is the strongest liveness evidence available there: the server
        started, answered `initialize` and listed its tools. Tool-call outcomes
        recorded against the same name carry it from there.
        """
        self._sessions[name] = self._sessions.get(name, 0) + 1
        self._start_times.setdefault(name, time.time())
        if name not in self._stats:
            self._stats[name] = _ServerStats()
        # It came up, so any earlier failure reason is stale.
        self._start_failures.pop(name, None)
        log.debug(
            "MCP server %r session up (%d tools, %d open)",
            name, tool_count, self._sessions[name],
        )

    def release_session(self, name: str) -> None:
        """Drop one open session for `name` (its owner is tearing it down)."""
        remaining = self._sessions.get(name, 0) - 1
        if remaining > 0:
            self._sessions[name] = remaining
            return
        self._sessions.pop(name, None)
        if name not in self._processes:
            self._start_times.pop(name, None)
        log.debug("MCP server %r session released", name)

    def record_start_failure(self, name: str, error: str) -> None:
        """Record that a server failed to start, and why.

        This is the answer to "why does the agent have no tools for X?", so it is
        kept verbatim and surfaced as UNHEALTHY rather than being folded into the
        silence of an unregistered server.

        The verdict it carries expires (see `_start_failure_is_current`): the
        attempt happened at one instant and nothing repeats it until something
        asks for MCP tools again, so an hour later it is history rather than a
        measurement of how the server is now.
        """
        self._start_failures[name] = _StartFailure(at=time.time(), reason=error)
        if name not in self._stats:
            self._stats[name] = _ServerStats()
        log.debug("MCP server %r failed to start: %s", name, error)

    def _start_failure_is_current(self, name: str) -> bool:
        """Whether `name`'s recorded start failure is still a live verdict.

        A start failure is a single observation with no one refreshing it, so
        past `start_failure_ttl_s` it stops being a claim about the server's
        current state and the row moves to `unmeasured` — the same treatment
        every other idle server gets. Left as a permanent UNHEALTHY it published
        a major outage on the public status page, and an unhealthy count on
        /healthz, for a server nothing had looked at since the failure.
        """
        failure = self._start_failures.get(name)
        if failure is None:
            return False
        # A clock step backwards must not resurrect an expired failure or expire
        # a fresh one, so a negative age counts as fresh (the attempt cannot have
        # happened in the future).
        return (time.time() - failure.at) < self._start_failure_ttl_s

    def record_tool_call(
        self,
        server_name: str,
        tool_name: str,
        latency_ms: float,
        success: bool,
        error: str | None = None,
        *,
        health_signal: bool = True,
    ) -> None:
        """Record a tool call for health tracking.

        `health_signal=False` marks a failure the server itself produced: it read
        the request, decided, and answered — a policy refusal, a path the sandbox
        would not open, an argument it rejected. That is the server working, so it
        is counted as a call and as a fault but never as an error, and it can
        never move the server off HEALTHY. Only failures that say the server did
        not answer (a dead transport, an expired read deadline) are errors.

        The distinction is not cosmetic: MCP returns both as `isError`, so
        langchain-mcp-adapters raises ToolException for both, and counting them
        together made a couple of correctly refused agent actions push a perfectly
        healthy server past the 50% error-rate threshold and into UNHEALTHY —
        MAJOR_OUTAGE on the public status page — for the life of the process.
        """
        if server_name not in self._stats:
            self._stats[server_name] = _ServerStats()

        stats = self._stats[server_name]
        stats.tool_calls += 1
        stats.last_call_time = time.time()
        stats.latency_samples.append(latency_ms)

        # Keep only last 100 latency samples
        if len(stats.latency_samples) > 100:
            stats.latency_samples = stats.latency_samples[-100:]

        is_error = not success and health_signal
        # Every call lands in the window, refusals included, so they count in the
        # denominator: a server that answers a hundred requests and drops one must
        # not read as 100% broken because the answers were "no".
        stats.recent_errors.append(is_error)

        if is_error:
            stats.errors += 1
            if error:
                stats.last_error = error
        elif not success:
            stats.tool_faults += 1
            # Kept even though it does not move the status. Otherwise a server
            # that refused every call this hour reports HEALTHY with a fault
            # count and no reason, and the operator has nowhere to go next.
            if error:
                stats.last_fault = error

    async def check_server(self, name: str) -> MCPServerHealth:
        """Check health of a single MCP server.

        STOPPED means "nothing in this process is running it". That is an honest
        answer about a named server, but it is not a health verdict, so
        `check_all` reports those rows under `unmeasured` and leaves them out of
        the counts and out of `overall_status`.
        """
        stats = self._stats.get(name, _ServerStats())
        process = self._processes.get(name)
        open_sessions = self._sessions.get(name, 0)

        # Basic status
        health = MCPServerHealth(
            name=name,
            status=MCPServerStatus.UNKNOWN,
            tool_calls_total=stats.tool_calls,
            tool_errors_total=stats.errors,
            tool_faults_total=stats.tool_faults,
            last_fault_message=stats.last_fault,
            open_sessions=open_sessions,
        )

        # Nothing is holding this server open. Either it was tried recently and
        # refused to start — in which case the reason is the whole point of the
        # check and is reported as unhealthy — or nobody has asked for it (or
        # nobody has for a long time), which is STOPPED rather than a health claim.
        if process is None and open_sessions <= 0:
            # Presence of the record, not truthiness of its reason: `check_all`
            # puts every name whose start failure is still current in `servers`,
            # and every row there has to carry a verdict for healthy + degraded +
            # unhealthy == len(servers) to hold. A caller that recorded a blank
            # reason would otherwise land a STOPPED row in the measured list and
            # quietly break that sum for /healthz. The row still says it is
            # unhealthy, and says that the reason is the thing that went missing.
            failure = self._start_failures.get(name)
            if failure is not None and self._start_failure_is_current(name):
                health.status = MCPServerStatus.UNHEALTHY
                health.error_message = (
                    failure.reason or "failed to start (no reason recorded)"
                )
            else:
                health.status = MCPServerStatus.STOPPED
                if failure is not None:
                    # The verdict expired, the reason did not. Phrased in the past
                    # tense because the row no longer claims the server is broken
                    # — it says nothing is measuring it and what happened last
                    # time anything tried.
                    health.error_message = (
                        f"last start attempt failed: {failure.reason}"
                        if failure.reason
                        else "last start attempt failed (no reason recorded)"
                    )
            return health

        if process is not None:
            health.liveness_source = "process"
            # Check if process is alive
            if process.returncode is not None:
                health.status = MCPServerStatus.UNHEALTHY
                health.error_message = f"Process exited with code {process.returncode}"
                return health
            health.pid = process.pid
        else:
            # An open stdio session: the handshake succeeded and tool calls are
            # the ongoing evidence. There is no pid to poll and no memory to
            # sample, and both stay unset rather than being reported as zero.
            health.liveness_source = "session"

        health.uptime_seconds = time.time() - self._start_times.get(name, time.time())

        # Last tool call time
        if stats.last_call_time:
            health.last_tool_call = datetime.utcfromtimestamp(stats.last_call_time)

        # Average latency
        if stats.latency_samples:
            health.avg_latency_ms = sum(stats.latency_samples) / len(stats.latency_samples)

        # Memory usage (if psutil available)
        if process is not None:
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

        # Error rate check, over the recent window rather than the lifetime
        # totals, so a server that has recovered is reported as recovered.
        if stats.recent_errors:
            error_rate = sum(stats.recent_errors) / len(stats.recent_errors)
            if error_rate >= self._error_rate_unhealthy:
                return MCPServerStatus.UNHEALTHY
            if error_rate >= self._error_rate_degraded:
                issues.append("high_error_rate")

        # Latency check
        if health.avg_latency_ms > self._latency_degraded_ms:
            issues.append("high_latency")

        # Memory check — only when it was actually sampled; unmeasured memory is
        # not evidence of a healthy footprint either way.
        if health.memory_mb is not None and health.memory_mb > self._memory_degraded_mb:
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
        """Report every MCP server this process knows about, split by whether
        anything is measuring it.

        `servers` gets a row per live process handle, open stdio session, or
        server that was asked to start and refused within `start_failure_ttl_s`
        (that last one would otherwise vanish from the report, and it is exactly
        the case an operator is looking for; past the TTL nothing has re-tried it,
        so it is no longer a measurement and moves to `unmeasured` carrying the
        reason). Every one of those rows carries a real verdict, so healthy +
        degraded + unhealthy always equals len(servers), and an empty list with
        overall UNKNOWN means "no MCP session is open" — not "zero servers are
        healthy".

        `unmeasured` gets the rest: servers that ran earlier in this process and
        whose sessions have all closed. They are STOPPED, which is true, but
        STOPPED is not a health verdict — /healthz counts these rows and
        app/status_page/store.py turns each one into a public component, and that
        page maps STOPPED to MAJOR_OUTAGE, so putting them in `servers` announced
        a major outage for every server the deployment had ever used the moment it
        went idle. Keeping them in their own list means the report can still say
        the server exists and what it did, without claiming it is broken.

        `overall_status` is computed from `servers` alone, for the same reason.
        The totals cover both lists: those tool calls really happened, and
        dropping them when a session closes would report a measured number as
        zero.
        """
        servers: list[MCPServerHealth] = []
        unmeasured: list[MCPServerHealth] = []

        measured_names = (
            set(self._processes)
            | set(self._sessions)
            | {n for n in self._start_failures if self._start_failure_is_current(n)}
        )
        # Anything with stats but nothing holding it open: it ran, it is not
        # running now. Sorted so two consecutive reports list servers the same way.
        unmeasured_names = set(self._stats) - measured_names

        for name in sorted(measured_names):
            servers.append(await self.check_server(name))
        for name in sorted(unmeasured_names):
            unmeasured.append(await self.check_server(name))

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

        total_calls = sum(s.tool_calls_total for s in servers + unmeasured)
        total_errors = sum(s.tool_errors_total for s in servers + unmeasured)

        return MCPHealthSummary(
            overall_status=overall,
            servers=servers,
            unmeasured=unmeasured,
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


@dataclass(frozen=True)
class _StartFailure:
    """One failed attempt to start a server: when it happened, and why.

    The timestamp is what stops the verdict outliving the observation — see
    `MCPHealthMonitor._start_failure_is_current`.
    """
    at: float
    reason: str


@dataclass
class _ServerStats:
    """Internal stats tracking for a server."""
    tool_calls: int = 0
    errors: int = 0
    # Calls the server answered with an error result (a refusal, a rejected
    # argument). Kept so the number is not lost, but it never feeds the verdict.
    tool_faults: int = 0
    last_call_time: float | None = None
    latency_samples: list[float] = field(default_factory=list)
    last_error: str | None = None
    last_fault: str | None = None
    # One entry per call, True when it was a health-relevant error. Bounded and
    # recent on purpose: the totals are cumulative for the life of the process, so
    # a verdict computed from them could never recover from an outage that ended
    # hours ago. The status answers "how is this server doing now".
    recent_errors: deque[bool] = field(default_factory=lambda: deque(maxlen=100))


# Global singleton
_monitor: MCPHealthMonitor | None = None


def get_health_monitor() -> MCPHealthMonitor:
    """Get the global MCP health monitor."""
    global _monitor
    if _monitor is None:
        _monitor = MCPHealthMonitor()
    return _monitor
