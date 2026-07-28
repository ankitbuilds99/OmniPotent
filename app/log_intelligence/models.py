"""Models for log intelligence.

Defines log entries, queries, summaries, and trace structures.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("devcopilot.log_intelligence.models")


class LogLevel(Enum):
    """Log level."""

    TRACE = "trace"
    DEBUG = "debug"
    INFO = "info"
    WARN = "warn"
    ERROR = "error"
    FATAL = "fatal"


class AnomalyType(Enum):
    """Type of anomaly detected."""

    UNUSUAL_FREQUENCY = "unusual_frequency"
    NEW_ERROR_TYPE = "new_error_type"
    PATTERN_CHANGE = "pattern_change"
    RESOURCE_SPIKE = "resource_spike"
    TIMING_ANOMALY = "timing_anomaly"


@dataclass
class LogEntry:
    """A single log entry."""

    log_id: str
    timestamp: datetime
    level: LogLevel
    service: str
    message: str
    trace_id: str = ""
    span_id: str = ""
    attributes: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "log_id": self.log_id,
            "timestamp": self.timestamp.isoformat(),
            "level": self.level.value,
            "service": self.service,
            "message": self.message,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "attributes": self.attributes,
            "context": self.context,
        }


@dataclass
class LogQuery:
    """A natural language log query."""

    query: str
    services: list[str] = field(default_factory=list)
    levels: list[LogLevel] = field(default_factory=list)
    start_time: datetime | None = None
    end_time: datetime | None = None
    trace_id: str = ""
    limit: int = 100

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "services": self.services,
            "levels": [level.value for level in self.levels],
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "trace_id": self.trace_id,
            "limit": self.limit,
        }


@dataclass
class AnomalyHighlight:
    """An anomaly highlighted in logs."""

    anomaly_id: str
    anomaly_type: AnomalyType
    log_ids: list[str]
    description: str
    explanation: str
    severity: str
    first_seen: datetime
    count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "anomaly_id": self.anomaly_id,
            "anomaly_type": self.anomaly_type.value,
            "log_ids": self.log_ids,
            "description": self.description,
            "explanation": self.explanation,
            "severity": self.severity,
            "first_seen": self.first_seen.isoformat(),
            "count": self.count,
        }


@dataclass
class LogPattern:
    """A pattern found in logs."""

    pattern_id: str
    template: str
    example: str
    count: int
    services: list[str]
    first_seen: datetime
    last_seen: datetime
    is_error: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "pattern_id": self.pattern_id,
            "template": self.template,
            "example": self.example,
            "count": self.count,
            "services": self.services,
            "first_seen": self.first_seen.isoformat(),
            "last_seen": self.last_seen.isoformat(),
            "is_error": self.is_error,
        }


@dataclass
class ErrorCluster:
    """A cluster of similar errors."""

    cluster_id: str
    pattern: str
    sample_message: str
    count: int
    services: list[str]
    first_occurrence: datetime
    last_occurrence: datetime
    is_new: bool
    trend: str
    related_logs: list[str]
    stack_trace: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "cluster_id": self.cluster_id,
            "pattern": self.pattern,
            "sample_message": self.sample_message,
            "count": self.count,
            "services": self.services,
            "first_occurrence": self.first_occurrence.isoformat(),
            "last_occurrence": self.last_occurrence.isoformat(),
            "is_new": self.is_new,
            "trend": self.trend,
            "related_logs": self.related_logs[:5],
            "has_stack_trace": bool(self.stack_trace),
        }


@dataclass
class LogSummary:
    """AI-generated summary of log data."""

    summary: str
    total_logs: int
    error_count: int
    warning_count: int
    services_involved: list[str]
    key_patterns: list[LogPattern]
    anomalies: list[AnomalyHighlight]
    error_clusters: list[ErrorCluster]
    time_range_start: datetime
    time_range_end: datetime
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "total_logs": self.total_logs,
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "services_involved": self.services_involved,
            "key_patterns": [p.to_dict() for p in self.key_patterns],
            "anomalies": [a.to_dict() for a in self.anomalies],
            "error_clusters": [c.to_dict() for c in self.error_clusters],
            "time_range_start": self.time_range_start.isoformat(),
            "time_range_end": self.time_range_end.isoformat(),
            "generated_at": self.generated_at.isoformat(),
        }


@dataclass
class TraceSpan:
    """A span in a distributed trace."""

    span_id: str
    trace_id: str
    parent_span_id: str
    operation_name: str
    service: str
    start_time: datetime
    duration_ms: float
    status: str
    tags: dict[str, str] = field(default_factory=dict)
    logs: list[LogEntry] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "span_id": self.span_id,
            "trace_id": self.trace_id,
            "parent_span_id": self.parent_span_id,
            "operation_name": self.operation_name,
            "service": self.service,
            "start_time": self.start_time.isoformat(),
            "duration_ms": round(self.duration_ms, 2),
            "status": self.status,
            "tags": self.tags,
            "log_count": len(self.logs),
            "error": self.error,
        }


@dataclass
class TraceWaterfall:
    """A waterfall visualization of a trace."""

    trace_id: str
    spans: list[TraceSpan]
    total_duration_ms: float
    service_count: int
    critical_path: list[str]
    bottleneck_span: str | None
    error_spans: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "spans": [s.to_dict() for s in self.spans],
            "total_duration_ms": round(self.total_duration_ms, 2),
            "service_count": self.service_count,
            "critical_path": self.critical_path,
            "bottleneck_span": self.bottleneck_span,
            "error_spans": self.error_spans,
        }


@dataclass
class TraceComparison:
    """Comparison between two traces."""

    trace_id_a: str
    trace_id_b: str
    duration_diff_ms: float
    span_count_diff: int
    differences: list[dict[str, Any]]
    key_difference: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id_a": self.trace_id_a,
            "trace_id_b": self.trace_id_b,
            "duration_diff_ms": round(self.duration_diff_ms, 2),
            "span_count_diff": self.span_count_diff,
            "differences": self.differences,
            "key_difference": self.key_difference,
        }
