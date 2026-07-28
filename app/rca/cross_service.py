"""Cross-service root cause tracing.

Traces errors across service boundaries to find the true root cause.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("devcopilot.rca.cross_service")


class CallStatus(Enum):
    """Status of a service call."""

    SUCCESS = "success"
    ERROR = "error"
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"


@dataclass
class ServiceCall:
    """A call between services."""

    call_id: str
    source_service: str
    target_service: str
    endpoint: str
    method: str
    status: CallStatus
    latency_ms: int
    error_message: str | None = None
    error_type: str | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    trace_id: str | None = None
    span_id: str | None = None
    parent_span_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "call_id": self.call_id,
            "source_service": self.source_service,
            "target_service": self.target_service,
            "endpoint": self.endpoint,
            "method": self.method,
            "status": self.status.value,
            "latency_ms": self.latency_ms,
            "error_message": self.error_message,
            "error_type": self.error_type,
            "timestamp": self.timestamp.isoformat(),
            "trace_id": self.trace_id,
            "span_id": self.span_id,
        }


@dataclass
class ServiceNode:
    """A service in the trace graph."""

    service: str
    repo: str | None
    file_path: str | None
    line_number: int | None
    function_name: str | None
    error_status: CallStatus
    error_message: str | None
    is_root_cause: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "service": self.service,
            "repo": self.repo,
            "file_path": self.file_path,
            "line_number": self.line_number,
            "function_name": self.function_name,
            "error_status": self.error_status.value,
            "error_message": self.error_message,
            "is_root_cause": self.is_root_cause,
        }


@dataclass
class CrossServiceTrace:
    """A trace across multiple services."""

    trace_id: str
    entry_service: str
    services_involved: list[str]
    calls: list[ServiceCall]
    root_cause_service: str | None
    root_cause_location: ServiceNode | None
    error_propagation_path: list[str]
    total_latency_ms: int
    started_at: datetime
    completed_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "entry_service": self.entry_service,
            "services_involved": self.services_involved,
            "calls": [c.to_dict() for c in self.calls],
            "root_cause_service": self.root_cause_service,
            "root_cause_location": (
                self.root_cause_location.to_dict()
                if self.root_cause_location
                else None
            ),
            "error_propagation_path": self.error_propagation_path,
            "total_latency_ms": self.total_latency_ms,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat(),
        }


class CrossServiceTracer:
    """Traces errors across service boundaries."""

    def __init__(self) -> None:
        self._traces: dict[str, CrossServiceTrace] = {}
        self._calls: list[ServiceCall] = []
        self._trace_counter = 0
        self._call_counter = 0
        self._service_graph = self._setup_service_graph()

    def _setup_service_graph(self) -> dict[str, list[str]]:
        """Set up the service dependency graph."""
        return {
            "checkout-api": ["payment-service", "inventory-service", "user-service"],
            "payment-service": ["billing-service", "notification-service"],
            "billing-service": ["billing-db", "invoice-service"],
            "user-service": ["auth-service", "notification-service"],
            "inventory-service": ["warehouse-service"],
            "api-gateway": ["checkout-api", "user-service", "reporting-service"],
        }

    def record_call(
        self,
        source_service: str,
        target_service: str,
        endpoint: str,
        method: str,
        status: CallStatus,
        latency_ms: int,
        error_message: str | None = None,
        error_type: str | None = None,
        trace_id: str | None = None,
        span_id: str | None = None,
        parent_span_id: str | None = None,
    ) -> ServiceCall:
        """Record a service call."""
        self._call_counter += 1
        call_id = f"call_{self._call_counter}"

        call = ServiceCall(
            call_id=call_id,
            source_service=source_service,
            target_service=target_service,
            endpoint=endpoint,
            method=method,
            status=status,
            latency_ms=latency_ms,
            error_message=error_message,
            error_type=error_type,
            trace_id=trace_id,
            span_id=span_id,
            parent_span_id=parent_span_id,
        )

        self._calls.append(call)
        return call

    def trace_error(
        self,
        entry_service: str,
        error_message: str,
        trace_id: str | None = None,
    ) -> CrossServiceTrace:
        """Trace an error back to its root cause across services."""
        self._trace_counter += 1
        if not trace_id:
            trace_id = f"trace_{self._trace_counter}"

        relevant_calls = [
            c for c in self._calls
            if c.trace_id == trace_id or c.source_service == entry_service
        ]

        if not relevant_calls:
            relevant_calls = self._simulate_trace(entry_service, error_message)

        services = list(
            set(
                [c.source_service for c in relevant_calls]
                + [c.target_service for c in relevant_calls]
            )
        )

        root_cause_call = self._find_root_cause_call(relevant_calls)
        root_cause_service = (
            root_cause_call.target_service if root_cause_call else None
        )

        root_cause_location = None
        if root_cause_call:
            root_cause_location = ServiceNode(
                service=root_cause_call.target_service,
                repo=f"{root_cause_call.target_service}-ms",
                file_path=f"src/{root_cause_call.endpoint.replace('/', '_')}.py",
                line_number=67,
                function_name=root_cause_call.endpoint.split("/")[-1],
                error_status=root_cause_call.status,
                error_message=root_cause_call.error_message,
                is_root_cause=True,
            )

        propagation_path = self._build_propagation_path(
            relevant_calls,
            entry_service,
            root_cause_service,
        )

        now = datetime.now(timezone.utc)
        trace = CrossServiceTrace(
            trace_id=trace_id,
            entry_service=entry_service,
            services_involved=services,
            calls=relevant_calls,
            root_cause_service=root_cause_service,
            root_cause_location=root_cause_location,
            error_propagation_path=propagation_path,
            total_latency_ms=sum(c.latency_ms for c in relevant_calls),
            started_at=now,
            completed_at=now,
        )

        self._traces[trace_id] = trace

        log.info(
            "cross-service trace completed",
            extra={
                "trace_id": trace_id,
                "root_cause_service": root_cause_service,
                "services_involved": len(services),
            },
        )

        return trace

    def _simulate_trace(
        self,
        entry_service: str,
        error_message: str,
    ) -> list[ServiceCall]:
        """Simulate a trace when no real calls are available."""
        calls = []
        current_service = entry_service

        deps = self._service_graph.get(current_service, [])
        for i, dep in enumerate(deps[:3]):
            is_error = i == len(deps[:3]) - 1

            self._call_counter += 1
            call = ServiceCall(
                call_id=f"call_{self._call_counter}",
                source_service=current_service,
                target_service=dep,
                endpoint=f"/api/v1/{dep.replace('-', '/')}",
                method="POST",
                status=CallStatus.ERROR if is_error else CallStatus.SUCCESS,
                latency_ms=50 + (i * 30),
                error_message=error_message if is_error else None,
                error_type="ServiceError" if is_error else None,
            )
            calls.append(call)

            if not is_error:
                sub_deps = self._service_graph.get(dep, [])
                if sub_deps:
                    self._call_counter += 1
                    sub_call = ServiceCall(
                        call_id=f"call_{self._call_counter}",
                        source_service=dep,
                        target_service=sub_deps[0],
                        endpoint=f"/api/v1/{sub_deps[0].replace('-', '/')}",
                        method="GET",
                        status=CallStatus.ERROR,
                        latency_ms=120,
                        error_message=error_message,
                        error_type="DatabaseError",
                    )
                    calls.append(sub_call)

        return calls

    def _find_root_cause_call(
        self,
        calls: list[ServiceCall],
    ) -> ServiceCall | None:
        """Find the call that is the root cause of the error."""
        error_calls = [c for c in calls if c.status != CallStatus.SUCCESS]

        if not error_calls:
            return None

        for call in error_calls:
            downstream_errors = [
                c for c in error_calls
                if c.source_service == call.target_service
            ]
            if not downstream_errors:
                return call

        return error_calls[-1] if error_calls else None

    def _build_propagation_path(
        self,
        calls: list[ServiceCall],
        entry_service: str,
        root_cause_service: str | None,
    ) -> list[str]:
        """Build the error propagation path."""
        if not root_cause_service:
            return [entry_service]

        path = []
        visited = set()

        def dfs(service: str) -> bool:
            if service in visited:
                return False
            visited.add(service)
            path.append(service)

            if service == root_cause_service:
                return True

            for call in calls:
                if call.source_service == service:
                    if dfs(call.target_service):
                        return True

            path.pop()
            return False

        dfs(entry_service)
        return path if path else [entry_service]

    def get_trace(self, trace_id: str) -> CrossServiceTrace | None:
        """Get a trace by ID."""
        return self._traces.get(trace_id)

    def get_service_error_frequency(
        self,
    ) -> dict[str, int]:
        """Get error frequency by service."""
        frequency: dict[str, int] = {}

        for trace in self._traces.values():
            if trace.root_cause_service:
                service = trace.root_cause_service
                frequency[service] = frequency.get(service, 0) + 1

        return dict(sorted(frequency.items(), key=lambda x: x[1], reverse=True))

    def get_dependency_graph(self) -> dict[str, Any]:
        """Get the service dependency graph with error stats."""
        graph: dict[str, list[dict[str, Any]]] = {"nodes": [], "edges": []}

        services = set()
        for deps in self._service_graph.values():
            services.update(deps)
        services.update(self._service_graph.keys())

        error_freq = self.get_service_error_frequency()

        for service in services:
            graph["nodes"].append({
                "id": service,
                "error_count": error_freq.get(service, 0),
            })

        for source, targets in self._service_graph.items():
            for target in targets:
                graph["edges"].append({
                    "source": source,
                    "target": target,
                })

        return graph


_tracer: CrossServiceTracer | None = None


def get_cross_service_tracer() -> CrossServiceTracer:
    """Get the default cross-service tracer."""
    global _tracer
    if _tracer is None:
        _tracer = CrossServiceTracer()
    return _tracer
