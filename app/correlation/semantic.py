"""Semantic analysis for alert correlation.

Uses NLP techniques to understand alert meaning beyond keywords.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from app.correlation.models import Alert

log = logging.getLogger("omnipotent.correlation.semantic")


@dataclass
class SemanticFeatures:
    """Semantic features extracted from an alert."""

    error_type: str
    affected_resource: str
    failure_mode: str
    component: str
    keywords: list[str]
    entities: list[str]
    severity_indicators: list[str]
    action_words: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "error_type": self.error_type,
            "affected_resource": self.affected_resource,
            "failure_mode": self.failure_mode,
            "component": self.component,
            "keywords": self.keywords,
            "entities": self.entities,
            "severity_indicators": self.severity_indicators,
            "action_words": self.action_words,
        }


@dataclass
class SemanticSimilarity:
    """Similarity score between two alerts."""

    overall_score: float
    error_type_match: bool
    resource_match: bool
    failure_mode_match: bool
    keyword_overlap: float
    explanation: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall_score": round(self.overall_score, 2),
            "error_type_match": self.error_type_match,
            "resource_match": self.resource_match,
            "failure_mode_match": self.failure_mode_match,
            "keyword_overlap": round(self.keyword_overlap, 2),
            "explanation": self.explanation,
        }


class SemanticAnalyzer:
    """Analyzes alert semantics for intelligent correlation."""

    def __init__(self) -> None:
        self._error_type_patterns = self._setup_error_patterns()
        self._resource_patterns = self._setup_resource_patterns()
        self._failure_mode_patterns = self._setup_failure_patterns()
        self._component_patterns = self._setup_component_patterns()

    def _setup_error_patterns(self) -> dict[str, list[str]]:
        """Set up patterns for error type classification."""
        return {
            "connection": ["connection", "timeout", "refused", "reset", "socket", "tcp"],
            "resource_exhaustion": ["exhausted", "full", "capacity", "limit", "quota", "exceeded"],
            "null_reference": ["null", "none", "undefined", "nil", "attributeerror", "nonetype"],
            "authentication": ["auth", "401", "403", "permission", "access denied", "unauthorized"],
            "timeout": ["timeout", "timed out", "deadline exceeded", "context deadline"],
            "database": ["database", "sql", "query", "deadlock", "lock", "transaction"],
            "network": ["network", "dns", "unreachable", "routing", "packet loss"],
            "memory": ["memory", "oom", "out of memory", "heap", "gc"],
            "disk": ["disk", "storage", "filesystem", "no space", "io error"],
            "cpu": ["cpu", "throttle", "high load", "utilization"],
        }

    def _setup_resource_patterns(self) -> dict[str, list[str]]:
        """Set up patterns for resource identification."""
        return {
            "database": ["db", "database", "postgres", "mysql", "mongo", "redis", "sql"],
            "cache": ["cache", "redis", "memcached", "caching"],
            "queue": ["queue", "kafka", "rabbitmq", "sqs", "pubsub"],
            "api": ["api", "endpoint", "rest", "graphql", "rpc"],
            "storage": ["s3", "gcs", "blob", "storage", "bucket"],
            "compute": ["pod", "container", "instance", "node", "vm"],
        }

    def _setup_failure_patterns(self) -> dict[str, list[str]]:
        """Set up patterns for failure mode classification."""
        return {
            "unavailable": ["unavailable", "down", "offline", "unreachable", "not responding"],
            "degraded": ["degraded", "slow", "latency", "delayed", "high response time"],
            "error_rate": ["error rate", "errors", "500", "5xx", "failures"],
            "saturation": ["saturation", "full", "100%", "saturated", "exhausted"],
            "cascade": ["cascade", "domino", "propagat", "spreading"],
        }

    def _setup_component_patterns(self) -> dict[str, list[str]]:
        """Set up patterns for component identification."""
        return {
            "frontend": ["frontend", "ui", "web", "client", "browser"],
            "backend": ["backend", "api", "service", "server"],
            "database": ["database", "db", "storage", "persistence"],
            "middleware": ["middleware", "gateway", "proxy", "load balancer"],
            "infrastructure": ["kubernetes", "k8s", "docker", "aws", "gcp", "azure"],
        }

    def extract_features(self, alert: Alert) -> SemanticFeatures:
        """Extract semantic features from an alert."""
        text = f"{alert.name} {alert.message}".lower()

        error_type = self._classify(text, self._error_type_patterns)
        affected_resource = self._classify(text, self._resource_patterns)
        failure_mode = self._classify(text, self._failure_mode_patterns)
        component = self._classify(text, self._component_patterns)

        keywords = self._extract_keywords(text)
        entities = self._extract_entities(text)
        severity_indicators = self._extract_severity_indicators(text)
        action_words = self._extract_action_words(text)

        return SemanticFeatures(
            error_type=error_type,
            affected_resource=affected_resource,
            failure_mode=failure_mode,
            component=component,
            keywords=keywords,
            entities=entities,
            severity_indicators=severity_indicators,
            action_words=action_words,
        )

    def _classify(
        self,
        text: str,
        patterns: dict[str, list[str]],
    ) -> str:
        """Classify text using pattern matching."""
        best_match = "unknown"
        best_score = 0

        for category, keywords in patterns.items():
            score = sum(1 for kw in keywords if kw in text)
            if score > best_score:
                best_score = score
                best_match = category

        return best_match

    def _extract_keywords(self, text: str) -> list[str]:
        """Extract significant keywords from text."""
        stopwords = {
            "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
            "have", "has", "had", "do", "does", "did", "will", "would", "could",
            "should", "may", "might", "must", "shall", "can", "need", "to", "of",
            "in", "for", "on", "with", "at", "by", "from", "up", "about", "into",
            "through", "during", "before", "after", "above", "below", "between",
            "and", "but", "or", "nor", "so", "yet", "both", "either", "neither",
            "not", "only", "own", "same", "than", "too", "very", "just", "also",
        }

        words = re.findall(r'\b[a-z]{3,}\b', text)
        keywords = [w for w in words if w not in stopwords]

        keyword_counts: dict[str, int] = {}
        for w in keywords:
            keyword_counts[w] = keyword_counts.get(w, 0) + 1

        return sorted(keyword_counts.keys(), key=lambda k: keyword_counts[k], reverse=True)[:10]

    def _extract_entities(self, text: str) -> list[str]:
        """Extract named entities (services, resources) from text."""
        entities: list[str] = []

        service_pattern = r'([a-z]+-(?:service|api|db|cache|worker|gateway))'
        entities.extend(re.findall(service_pattern, text))

        ip_pattern = r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b'
        entities.extend(re.findall(ip_pattern, text))

        path_pattern = r'/[a-z][a-z0-9_/-]+'
        entities.extend(re.findall(path_pattern, text))

        return list(set(entities))[:10]

    def _extract_severity_indicators(self, text: str) -> list[str]:
        """Extract words indicating severity."""
        severity_words = [
            "critical", "fatal", "emergency", "severe", "major",
            "high", "important", "urgent", "immediate",
            "warning", "moderate", "minor", "low",
        ]
        return [w for w in severity_words if w in text]

    def _extract_action_words(self, text: str) -> list[str]:
        """Extract action/event words."""
        action_words = [
            "failed", "crashed", "stopped", "restarted", "killed",
            "exceeded", "reached", "triggered", "detected", "started",
            "completed", "terminated", "rejected", "blocked", "delayed",
        ]
        return [w for w in action_words if w in text]

    def calculate_similarity(
        self,
        alert1: Alert,
        alert2: Alert,
    ) -> SemanticSimilarity:
        """Calculate semantic similarity between two alerts."""
        features1 = self.extract_features(alert1)
        features2 = self.extract_features(alert2)

        error_type_match = features1.error_type == features2.error_type and features1.error_type != "unknown"
        resource_match = features1.affected_resource == features2.affected_resource and features1.affected_resource != "unknown"
        failure_mode_match = features1.failure_mode == features2.failure_mode and features1.failure_mode != "unknown"

        keywords1 = set(features1.keywords)
        keywords2 = set(features2.keywords)
        if keywords1 and keywords2:
            keyword_overlap = len(keywords1 & keywords2) / len(keywords1 | keywords2)
        else:
            keyword_overlap = 0.0

        score = 0.0
        explanations: list[str] = []

        if error_type_match:
            score += 0.3
            explanations.append(f"Same error type: {features1.error_type}")

        if resource_match:
            score += 0.25
            explanations.append(f"Same resource: {features1.affected_resource}")

        if failure_mode_match:
            score += 0.25
            explanations.append(f"Same failure mode: {features1.failure_mode}")

        score += keyword_overlap * 0.2
        if keyword_overlap > 0.3:
            common = keywords1 & keywords2
            explanations.append(f"Common keywords: {', '.join(list(common)[:3])}")

        explanation = "; ".join(explanations) if explanations else "No significant semantic overlap"

        return SemanticSimilarity(
            overall_score=min(1.0, score),
            error_type_match=error_type_match,
            resource_match=resource_match,
            failure_mode_match=failure_mode_match,
            keyword_overlap=keyword_overlap,
            explanation=explanation,
        )

    def cluster_by_semantics(
        self,
        alerts: list[Alert],
        threshold: float = 0.5,
    ) -> list[list[Alert]]:
        """Cluster alerts by semantic similarity."""
        if not alerts:
            return []

        clusters: list[list[Alert]] = []
        assigned: set[str] = set()

        for alert in alerts:
            if alert.alert_id in assigned:
                continue

            cluster = [alert]
            assigned.add(alert.alert_id)

            for other in alerts:
                if other.alert_id in assigned:
                    continue

                similarity = self.calculate_similarity(alert, other)
                if similarity.overall_score >= threshold:
                    cluster.append(other)
                    assigned.add(other.alert_id)

            clusters.append(cluster)

        return clusters

    def summarize_cluster(self, alerts: list[Alert]) -> dict[str, Any]:
        """Generate a summary for a cluster of alerts."""
        if not alerts:
            return {}

        all_features = [self.extract_features(a) for a in alerts]

        error_types: dict[str, int] = {}
        resources: dict[str, int] = {}
        failure_modes: dict[str, int] = {}
        all_keywords: dict[str, int] = {}

        for features in all_features:
            error_types[features.error_type] = error_types.get(features.error_type, 0) + 1
            resources[features.affected_resource] = resources.get(features.affected_resource, 0) + 1
            failure_modes[features.failure_mode] = failure_modes.get(features.failure_mode, 0) + 1
            for kw in features.keywords:
                all_keywords[kw] = all_keywords.get(kw, 0) + 1

        primary_error = max(error_types.items(), key=lambda x: x[1])[0] if error_types else "unknown"
        primary_resource = max(resources.items(), key=lambda x: x[1])[0] if resources else "unknown"
        primary_failure = max(failure_modes.items(), key=lambda x: x[1])[0] if failure_modes else "unknown"
        top_keywords = sorted(all_keywords.keys(), key=lambda k: all_keywords[k], reverse=True)[:5]

        return {
            "alert_count": len(alerts),
            "services_affected": list(set(a.service for a in alerts)),
            "primary_error_type": primary_error,
            "primary_resource": primary_resource,
            "primary_failure_mode": primary_failure,
            "top_keywords": top_keywords,
            "summary": f"{primary_failure.title()} in {primary_resource} ({primary_error})",
        }


_analyzer: SemanticAnalyzer | None = None


def get_semantic_analyzer() -> SemanticAnalyzer:
    """Get the default semantic analyzer."""
    global _analyzer
    if _analyzer is None:
        _analyzer = SemanticAnalyzer()
    return _analyzer
