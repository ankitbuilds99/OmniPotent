"""Error clustering for log intelligence.

Groups similar errors for easier triage.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.log_intelligence.models import ErrorCluster, LogEntry, LogLevel

log = logging.getLogger("omnipotent.log_intelligence.clustering")


@dataclass
class ClusterStats:
    """Statistics about error clusters."""

    total_clusters: int
    total_errors: int
    new_clusters: int
    increasing_clusters: int
    top_services: list[str]
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_clusters": self.total_clusters,
            "total_errors": self.total_errors,
            "new_clusters": self.new_clusters,
            "increasing_clusters": self.increasing_clusters,
            "top_services": self.top_services,
            "updated_at": self.updated_at.isoformat(),
        }


class ErrorClusterer:
    """Clusters similar errors for triage."""

    def __init__(self) -> None:
        self._clusters: dict[str, ErrorCluster] = {}
        self._cluster_counter = 0
        self._similarity_threshold = 0.6

    def cluster(self, logs: list[LogEntry]) -> list[ErrorCluster]:
        """Cluster error logs."""
        error_logs = [
            e for e in logs
            if e.level in [LogLevel.ERROR, LogLevel.FATAL]
        ]

        if not error_logs:
            return []

        clusters: dict[str, list[LogEntry]] = {}

        for entry in error_logs:
            signature = self._get_error_signature(entry)

            best_cluster = None
            best_similarity = 0.0

            for cluster_sig in clusters.keys():
                similarity = self._calculate_similarity(signature, cluster_sig)
                if similarity > best_similarity and similarity >= self._similarity_threshold:
                    best_similarity = similarity
                    best_cluster = cluster_sig

            if best_cluster:
                clusters[best_cluster].append(entry)
            else:
                clusters[signature] = [entry]

        result: list[ErrorCluster] = []
        for signature, entries in clusters.items():
            cluster = self._create_cluster(signature, entries)
            result.append(cluster)
            self._clusters[cluster.cluster_id] = cluster

        result.sort(key=lambda c: c.count, reverse=True)
        return result

    def _get_error_signature(self, entry: LogEntry) -> str:
        """Get a signature for an error for clustering."""
        message = entry.message

        message = re.sub(r'\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b', '', message)
        message = re.sub(r'\b[0-9a-f]{24,}\b', '', message)
        message = re.sub(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b', '', message)
        message = re.sub(r'\b\d+\b', '', message)
        message = re.sub(r'"[^"]*"', '""', message)
        message = re.sub(r"'[^']*'", "''", message)

        words = message.lower().split()
        key_words = [w for w in words if len(w) > 3][:10]

        return f"{entry.service}:{':'.join(key_words)}"

    def _calculate_similarity(self, sig1: str, sig2: str) -> float:
        """Calculate similarity between two signatures."""
        parts1 = sig1.split(":")
        parts2 = sig2.split(":")

        if parts1[0] != parts2[0]:
            return 0.0

        words1 = set(parts1[1:])
        words2 = set(parts2[1:])

        if not words1 or not words2:
            return 0.0

        intersection = len(words1 & words2)
        union = len(words1 | words2)

        return intersection / union if union > 0 else 0.0

    def _create_cluster(
        self,
        signature: str,
        entries: list[LogEntry],
    ) -> ErrorCluster:
        """Create an error cluster from entries."""
        self._cluster_counter += 1

        services = list(set(e.service for e in entries))
        first = min(entries, key=lambda e: e.timestamp)
        last = max(entries, key=lambda e: e.timestamp)

        is_new = (datetime.now(timezone.utc) - first.timestamp).total_seconds() < 3600

        trend = "stable"
        if len(entries) >= 10:
            mid = len(entries) // 2
            first_half_count = mid
            second_half_count = len(entries) - mid
            if second_half_count > first_half_count * 1.5:
                trend = "increasing"
            elif first_half_count > second_half_count * 1.5:
                trend = "decreasing"

        stack_trace = ""
        for entry in entries:
            if "traceback" in entry.message.lower():
                stack_trace = entry.message
                break
            elif "at " in entry.message and "(" in entry.message:
                stack_trace = entry.message
                break

        pattern = self._extract_pattern(entries[0].message)

        return ErrorCluster(
            cluster_id=f"cluster_{self._cluster_counter}",
            pattern=pattern,
            sample_message=entries[0].message,
            count=len(entries),
            services=services,
            first_occurrence=first.timestamp,
            last_occurrence=last.timestamp,
            is_new=is_new,
            trend=trend,
            related_logs=[e.log_id for e in entries],
            stack_trace=stack_trace,
        )

    def _extract_pattern(self, message: str) -> str:
        """Extract a pattern from error message."""
        lines = message.split("\n")
        first_line = lines[0] if lines else message

        pattern = re.sub(r'\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b', '<UUID>', first_line)
        pattern = re.sub(r'\b\d+\b', '<N>', pattern)

        return pattern[:100]

    def get_cluster(self, cluster_id: str) -> ErrorCluster | None:
        """Get a cluster by ID."""
        return self._clusters.get(cluster_id)

    def get_stats(self) -> ClusterStats:
        """Get clustering statistics."""
        clusters = list(self._clusters.values())

        total_errors = sum(c.count for c in clusters)
        new_clusters = sum(1 for c in clusters if c.is_new)
        increasing = sum(1 for c in clusters if c.trend == "increasing")

        service_counts: dict[str, int] = {}
        for cluster in clusters:
            for service in cluster.services:
                service_counts[service] = service_counts.get(service, 0) + cluster.count

        top_services = sorted(
            service_counts.keys(),
            key=lambda s: service_counts[s],
            reverse=True,
        )[:5]

        return ClusterStats(
            total_clusters=len(clusters),
            total_errors=total_errors,
            new_clusters=new_clusters,
            increasing_clusters=increasing,
            top_services=top_services,
        )

    def merge_clusters(
        self,
        cluster_id_a: str,
        cluster_id_b: str,
    ) -> ErrorCluster | None:
        """Merge two clusters."""
        cluster_a = self._clusters.get(cluster_id_a)
        cluster_b = self._clusters.get(cluster_id_b)

        if not cluster_a or not cluster_b:
            return None

        merged_logs = list(set(cluster_a.related_logs + cluster_b.related_logs))
        merged_services = list(set(cluster_a.services + cluster_b.services))

        first_occurrence = min(cluster_a.first_occurrence, cluster_b.first_occurrence)
        last_occurrence = max(cluster_a.last_occurrence, cluster_b.last_occurrence)

        self._cluster_counter += 1
        merged = ErrorCluster(
            cluster_id=f"cluster_{self._cluster_counter}",
            pattern=cluster_a.pattern,
            sample_message=cluster_a.sample_message,
            count=cluster_a.count + cluster_b.count,
            services=merged_services,
            first_occurrence=first_occurrence,
            last_occurrence=last_occurrence,
            is_new=cluster_a.is_new or cluster_b.is_new,
            trend="increasing" if cluster_a.trend == "increasing" or cluster_b.trend == "increasing" else "stable",
            related_logs=merged_logs,
            stack_trace=cluster_a.stack_trace or cluster_b.stack_trace,
        )

        del self._clusters[cluster_id_a]
        del self._clusters[cluster_id_b]
        self._clusters[merged.cluster_id] = merged

        return merged

    def find_similar_clusters(
        self,
        cluster_id: str,
        threshold: float = 0.5,
    ) -> list[ErrorCluster]:
        """Find clusters similar to a given cluster."""
        target = self._clusters.get(cluster_id)
        if not target:
            return []

        target_sig = f"{target.services[0]}:{target.pattern}"
        similar: list[tuple[ErrorCluster, float]] = []

        for cid, cluster in self._clusters.items():
            if cid == cluster_id:
                continue

            cluster_sig = f"{cluster.services[0]}:{cluster.pattern}"
            similarity = self._calculate_similarity(target_sig, cluster_sig)

            if similarity >= threshold:
                similar.append((cluster, similarity))

        similar.sort(key=lambda x: x[1], reverse=True)
        return [c for c, _ in similar[:5]]


_clusterer: ErrorClusterer | None = None


def get_error_clusterer() -> ErrorClusterer:
    """Get the default error clusterer."""
    global _clusterer
    if _clusterer is None:
        _clusterer = ErrorClusterer()
    return _clusterer
