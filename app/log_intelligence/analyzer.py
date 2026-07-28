"""AI-powered log analyzer.

Provides log summarization, anomaly detection, and natural language querying.
"""

from __future__ import annotations

import logging
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from app.log_intelligence.models import (
    AnomalyHighlight,
    AnomalyType,
    ErrorCluster,
    LogEntry,
    LogLevel,
    LogPattern,
    LogQuery,
    LogSummary,
    TraceComparison,
    TraceSpan,
    TraceWaterfall,
)

log = logging.getLogger("devcopilot.log_intelligence.analyzer")


class LogAnalyzer:
    """AI-powered log analysis engine."""

    def __init__(self) -> None:
        self._logs: list[LogEntry] = []
        self._patterns: dict[str, LogPattern] = {}
        self._traces: dict[str, list[TraceSpan]] = {}
        self._baseline_patterns: dict[str, int] = {}
        self._anomaly_counter = 0

    def ingest_log(self, entry: LogEntry) -> None:
        """Ingest a log entry."""
        self._logs.append(entry)

        pattern = self._extract_pattern(entry.message)
        pattern_id = f"pat_{hash(pattern) % 100000}"

        if pattern_id in self._patterns:
            self._patterns[pattern_id].count += 1
            self._patterns[pattern_id].last_seen = entry.timestamp
            if entry.service not in self._patterns[pattern_id].services:
                self._patterns[pattern_id].services.append(entry.service)
        else:
            self._patterns[pattern_id] = LogPattern(
                pattern_id=pattern_id,
                template=pattern,
                example=entry.message[:200],
                count=1,
                services=[entry.service],
                first_seen=entry.timestamp,
                last_seen=entry.timestamp,
                is_error=entry.level in [LogLevel.ERROR, LogLevel.FATAL],
            )

        if entry.trace_id:
            span = TraceSpan(
                span_id=entry.span_id or f"span_{secrets.token_hex(4)}",
                trace_id=entry.trace_id,
                parent_span_id="",
                operation_name=entry.attributes.get("operation", "unknown"),
                service=entry.service,
                start_time=entry.timestamp,
                duration_ms=entry.attributes.get("duration_ms", 0),
                status="ok" if entry.level != LogLevel.ERROR else "error",
                logs=[entry],
                error=entry.message if entry.level == LogLevel.ERROR else "",
            )

            if entry.trace_id not in self._traces:
                self._traces[entry.trace_id] = []
            self._traces[entry.trace_id].append(span)

    def _extract_pattern(self, message: str) -> str:
        """Extract a pattern from a log message by normalizing variables."""
        pattern = message

        pattern = re.sub(r'\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b', '<UUID>', pattern)
        pattern = re.sub(r'\b[0-9a-f]{24,}\b', '<ID>', pattern)
        pattern = re.sub(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b', '<IP>', pattern)
        pattern = re.sub(r'\b\d+\b', '<NUM>', pattern)
        pattern = re.sub(r'"[^"]{20,}"', '"<STRING>"', pattern)

        return pattern

    def query(self, query: LogQuery) -> list[LogEntry]:
        """Execute a natural language log query."""
        results = self._logs.copy()

        if query.services:
            results = [e for e in results if e.service in query.services]

        if query.levels:
            results = [e for e in results if e.level in query.levels]

        if query.start_time:
            results = [e for e in results if e.timestamp >= query.start_time]

        if query.end_time:
            results = [e for e in results if e.timestamp <= query.end_time]

        if query.trace_id:
            results = [e for e in results if e.trace_id == query.trace_id]

        query_lower = query.query.lower()
        keywords = [w for w in query_lower.split() if len(w) > 2]

        if keywords:
            scored_results: list[tuple[LogEntry, int]] = []
            for entry in results:
                entry_text = f"{entry.message} {entry.service}".lower()
                score = sum(1 for kw in keywords if kw in entry_text)
                if score > 0:
                    scored_results.append((entry, score))

            scored_results.sort(key=lambda x: (-x[1], x[0].timestamp), reverse=True)
            results = [r[0] for r in scored_results]

        return results[:query.limit]

    def summarize(
        self,
        logs: list[LogEntry] | None = None,
        time_range_minutes: int = 60,
    ) -> LogSummary:
        """Generate an AI summary of logs."""
        if logs is None:
            cutoff = datetime.now(timezone.utc) - timedelta(minutes=time_range_minutes)
            logs = [e for e in self._logs if e.timestamp >= cutoff]

        if not logs:
            now = datetime.now(timezone.utc)
            return LogSummary(
                summary="No logs found in the specified time range.",
                total_logs=0,
                error_count=0,
                warning_count=0,
                services_involved=[],
                key_patterns=[],
                anomalies=[],
                error_clusters=[],
                time_range_start=now - timedelta(minutes=time_range_minutes),
                time_range_end=now,
            )

        error_count = sum(1 for e in logs if e.level in [LogLevel.ERROR, LogLevel.FATAL])
        warning_count = sum(1 for e in logs if e.level == LogLevel.WARN)
        services = list(set(e.service for e in logs))

        patterns = list(self._patterns.values())
        patterns.sort(key=lambda p: p.count, reverse=True)
        key_patterns = patterns[:5]

        anomalies = self._detect_anomalies(logs)

        error_logs = [e for e in logs if e.level in [LogLevel.ERROR, LogLevel.FATAL]]
        error_clusters = self._cluster_errors(error_logs)

        summary_text = self._generate_summary_text(
            logs,
            error_count,
            services,
            key_patterns,
            anomalies,
        )

        time_range_start = min(e.timestamp for e in logs)
        time_range_end = max(e.timestamp for e in logs)

        return LogSummary(
            summary=summary_text,
            total_logs=len(logs),
            error_count=error_count,
            warning_count=warning_count,
            services_involved=services,
            key_patterns=key_patterns,
            anomalies=anomalies,
            error_clusters=error_clusters,
            time_range_start=time_range_start,
            time_range_end=time_range_end,
        )

    def _generate_summary_text(
        self,
        logs: list[LogEntry],
        error_count: int,
        services: list[str],
        patterns: list[LogPattern],
        anomalies: list[AnomalyHighlight],
    ) -> str:
        """Generate human-readable summary text."""
        parts: list[str] = []

        parts.append(f"Analyzed {len(logs)} logs from {len(services)} services.")

        if error_count > 0:
            error_patterns = [p for p in patterns if p.is_error]
            if error_patterns:
                top_error = error_patterns[0]
                parts.append(
                    f"Found {error_count} errors. The most common error pattern "
                    f"({top_error.count} occurrences) originates from {top_error.services[0]}."
                )

        if anomalies:
            parts.append(f"Detected {len(anomalies)} anomalies requiring attention.")

        return " ".join(parts)

    def _detect_anomalies(self, logs: list[LogEntry]) -> list[AnomalyHighlight]:
        """Detect anomalies in logs."""
        anomalies: list[AnomalyHighlight] = []

        service_error_counts: dict[str, list[LogEntry]] = {}
        for entry in logs:
            if entry.level in [LogLevel.ERROR, LogLevel.FATAL]:
                if entry.service not in service_error_counts:
                    service_error_counts[entry.service] = []
                service_error_counts[entry.service].append(entry)

        for service, errors in service_error_counts.items():
            baseline = self._baseline_patterns.get(f"{service}_errors", 10)
            if len(errors) > baseline * 2:
                self._anomaly_counter += 1
                anomalies.append(AnomalyHighlight(
                    anomaly_id=f"anomaly_{self._anomaly_counter}",
                    anomaly_type=AnomalyType.UNUSUAL_FREQUENCY,
                    log_ids=[e.log_id for e in errors[:10]],
                    description=f"Unusual error spike in {service}",
                    explanation=(
                        f"Error count ({len(errors)}) is significantly higher than "
                        f"baseline ({baseline}). This may indicate a new issue."
                    ),
                    severity="high" if len(errors) > baseline * 5 else "medium",
                    first_seen=errors[0].timestamp,
                    count=len(errors),
                ))

        seen_patterns: set[str] = set()
        for entry in logs:
            if entry.level in [LogLevel.ERROR, LogLevel.FATAL]:
                pattern = self._extract_pattern(entry.message)
                pattern_key = f"{entry.service}:{pattern[:50]}"

                if pattern_key not in seen_patterns:
                    pattern_data = self._patterns.get(f"pat_{hash(pattern) % 100000}")
                    if pattern_data and pattern_data.count == 1:
                        self._anomaly_counter += 1
                        anomalies.append(AnomalyHighlight(
                            anomaly_id=f"anomaly_{self._anomaly_counter}",
                            anomaly_type=AnomalyType.NEW_ERROR_TYPE,
                            log_ids=[entry.log_id],
                            description=f"New error type in {entry.service}",
                            explanation=(
                                f"This error pattern has not been seen before. "
                                f"Message: {entry.message[:100]}"
                            ),
                            severity="medium",
                            first_seen=entry.timestamp,
                            count=1,
                        ))
                    seen_patterns.add(pattern_key)

        return anomalies

    def _cluster_errors(self, error_logs: list[LogEntry]) -> list[ErrorCluster]:
        """Cluster similar errors."""
        clusters: dict[str, list[LogEntry]] = {}

        for entry in error_logs:
            pattern = self._extract_pattern(entry.message)
            pattern_key = pattern[:100]

            if pattern_key not in clusters:
                clusters[pattern_key] = []
            clusters[pattern_key].append(entry)

        result: list[ErrorCluster] = []
        for pattern, entries in clusters.items():
            if not entries:
                continue

            services = list(set(e.service for e in entries))
            first = min(entries, key=lambda e: e.timestamp)
            last = max(entries, key=lambda e: e.timestamp)

            is_new = (datetime.now(timezone.utc) - first.timestamp).total_seconds() < 3600

            trend = "stable"
            if len(entries) >= 10:
                mid = len(entries) // 2
                first_half = entries[:mid]
                second_half = entries[mid:]
                if len(second_half) > len(first_half) * 1.5:
                    trend = "increasing"
                elif len(first_half) > len(second_half) * 1.5:
                    trend = "decreasing"

            stack_trace = ""
            for entry in entries:
                if "traceback" in entry.message.lower() or "at " in entry.message:
                    stack_trace = entry.message
                    break

            result.append(ErrorCluster(
                cluster_id=f"err_cluster_{len(result)+1}",
                pattern=pattern,
                sample_message=entries[0].message,
                count=len(entries),
                services=services,
                first_occurrence=first.timestamp,
                last_occurrence=last.timestamp,
                is_new=is_new,
                trend=trend,
                related_logs=[e.log_id for e in entries[:10]],
                stack_trace=stack_trace,
            ))

        result.sort(key=lambda c: c.count, reverse=True)
        return result

    def get_trace(self, trace_id: str) -> TraceWaterfall | None:
        """Get a trace waterfall visualization."""
        spans = self._traces.get(trace_id, [])
        if not spans:
            return None

        spans.sort(key=lambda s: s.start_time)

        total_duration = sum(s.duration_ms for s in spans)
        services = list(set(s.service for s in spans))

        critical_path = [s.service for s in spans]

        bottleneck = max(spans, key=lambda s: s.duration_ms) if spans else None
        error_spans = [s.span_id for s in spans if s.status == "error"]

        return TraceWaterfall(
            trace_id=trace_id,
            spans=spans,
            total_duration_ms=total_duration,
            service_count=len(services),
            critical_path=critical_path,
            bottleneck_span=bottleneck.span_id if bottleneck else None,
            error_spans=error_spans,
        )

    def compare_traces(
        self,
        trace_id_a: str,
        trace_id_b: str,
    ) -> TraceComparison | None:
        """Compare two traces."""
        trace_a = self.get_trace(trace_id_a)
        trace_b = self.get_trace(trace_id_b)

        if not trace_a or not trace_b:
            return None

        duration_diff = trace_a.total_duration_ms - trace_b.total_duration_ms
        span_diff = len(trace_a.spans) - len(trace_b.spans)

        differences: list[dict[str, Any]] = []

        if abs(duration_diff) > 100:
            differences.append({
                "type": "duration",
                "description": f"Duration differs by {abs(duration_diff):.0f}ms",
                "trace_a_value": trace_a.total_duration_ms,
                "trace_b_value": trace_b.total_duration_ms,
            })

        if span_diff != 0:
            differences.append({
                "type": "span_count",
                "description": f"Span count differs by {abs(span_diff)}",
                "trace_a_value": len(trace_a.spans),
                "trace_b_value": len(trace_b.spans),
            })

        if trace_a.error_spans and not trace_b.error_spans:
            differences.append({
                "type": "errors",
                "description": "Trace A has errors, Trace B does not",
                "trace_a_value": len(trace_a.error_spans),
                "trace_b_value": 0,
            })

        key_diff = "No significant differences"
        if differences:
            key_diff = differences[0]["description"]

        return TraceComparison(
            trace_id_a=trace_id_a,
            trace_id_b=trace_id_b,
            duration_diff_ms=duration_diff,
            span_count_diff=span_diff,
            differences=differences,
            key_difference=key_diff,
        )

    def set_baseline(self, service: str, metric: str, value: int) -> None:
        """Set baseline for anomaly detection."""
        self._baseline_patterns[f"{service}_{metric}"] = value


_analyzer: LogAnalyzer | None = None


def get_log_analyzer() -> LogAnalyzer:
    """Get the default log analyzer."""
    global _analyzer
    if _analyzer is None:
        _analyzer = LogAnalyzer()
    return _analyzer
