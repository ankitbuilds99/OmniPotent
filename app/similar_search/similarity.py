"""Multi-dimensional similarity scoring for incidents."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from app.similar_search.models import (
    IncidentRecord,
    SimilarityScore,
    SimilarityDimension,
    DimensionWeights,
    TimePattern,
    ServiceOverlap,
    RootCauseCategory,
)

log = logging.getLogger("devcopilot.similar_search.similarity")


class SymptomMatcher:
    """Match incidents by symptoms (alerts)."""

    def calculate_score(
        self,
        incident1: IncidentRecord,
        incident2: IncidentRecord,
    ) -> tuple[float, str]:
        """Calculate symptom similarity using Jaccard index."""
        alerts1 = set(self._normalize_alert(a) for a in incident1.alerts)
        alerts2 = set(self._normalize_alert(a) for a in incident2.alerts)

        if not alerts1 and not alerts2:
            return 0.5, "No alerts to compare"

        if not alerts1 or not alerts2:
            return 0.0, "One incident has no alerts"

        intersection = alerts1 & alerts2
        union = alerts1 | alerts2

        score = len(intersection) / len(union) if union else 0.0

        if intersection:
            detail = f"Common alerts: {', '.join(list(intersection)[:3])}"
        else:
            detail = "No common alerts"

        return score, detail

    def _normalize_alert(self, alert: str) -> str:
        """Normalize alert name for comparison."""
        normalized = alert.lower()
        normalized = re.sub(r"[^a-z0-9_]", "_", normalized)
        normalized = re.sub(r"_+", "_", normalized)
        return normalized.strip("_")


class ErrorMessageMatcher:
    """Match incidents by error messages using semantic similarity."""

    def __init__(self):
        self._common_patterns = [
            (r"connection.*pool.*exhaust", "connection_pool_exhaustion"),
            (r"timeout|timed?\s*out", "timeout"),
            (r"out\s*of\s*memory|oom", "oom"),
            (r"500|internal.*server.*error", "server_error"),
            (r"503|service.*unavailable", "service_unavailable"),
            (r"429|rate.*limit|throttl", "rate_limit"),
            (r"connection.*refuse|econnrefused", "connection_refused"),
            (r"dns.*fail|resolve.*fail", "dns_failure"),
            (r"ssl|tls|certificate", "ssl_error"),
            (r"auth.*fail|unauthorized|403", "auth_failure"),
        ]

    def calculate_score(
        self,
        incident1: IncidentRecord,
        incident2: IncidentRecord,
    ) -> tuple[float, str]:
        """Calculate error message similarity."""
        errors1 = incident1.error_messages
        errors2 = incident2.error_messages

        if not errors1 and not errors2:
            return 0.5, "No error messages to compare"

        if not errors1 or not errors2:
            return 0.0, "One incident has no error messages"

        patterns1 = self._extract_patterns(errors1)
        patterns2 = self._extract_patterns(errors2)

        common = patterns1 & patterns2
        if common:
            score = len(common) / max(len(patterns1), len(patterns2))
            detail = f"Common patterns: {', '.join(common)}"
            return min(score + 0.3, 1.0), detail

        text1 = " ".join(errors1).lower()
        text2 = " ".join(errors2).lower()
        word_score = self._word_overlap_score(text1, text2)

        return word_score, "Partial text similarity"

    def _extract_patterns(self, errors: list[str]) -> set[str]:
        """Extract known error patterns."""
        combined = " ".join(errors).lower()
        patterns = set()

        for regex, pattern_name in self._common_patterns:
            if re.search(regex, combined):
                patterns.add(pattern_name)

        return patterns

    def _word_overlap_score(self, text1: str, text2: str) -> float:
        """Calculate word overlap score."""
        words1 = set(text1.split())
        words2 = set(text2.split())

        stopwords = {"the", "a", "an", "is", "was", "were", "in", "on", "at", "to"}
        words1 -= stopwords
        words2 -= stopwords

        if not words1 or not words2:
            return 0.0

        intersection = words1 & words2
        return len(intersection) / max(len(words1), len(words2))


class ServiceGraphMatcher:
    """Match incidents by service overlap and dependencies."""

    def __init__(self, dependency_graph: dict[str, list[str]] | None = None):
        self._dependency_graph = dependency_graph or {}

    def set_dependency_graph(self, graph: dict[str, list[str]]) -> None:
        """Set service dependency graph."""
        self._dependency_graph = graph

    def calculate_score(
        self,
        incident1: IncidentRecord,
        incident2: IncidentRecord,
    ) -> tuple[float, str]:
        """Calculate service overlap score."""
        overlap = ServiceOverlap.calculate(
            incident1.services,
            incident2.services,
            self._dependency_graph,
        )

        if not incident1.services and not incident2.services:
            return 0.5, "No services to compare"

        score = overlap.overlap_score

        if overlap.dependency_related:
            score = min(score + 0.2, 1.0)

        if overlap.common_services:
            detail = f"Common: {', '.join(overlap.common_services[:3])}"
        elif overlap.dependency_related:
            detail = "Services are related through dependencies"
        else:
            detail = "Different services affected"

        return score, detail


class TimePatternMatcher:
    """Match incidents by time patterns."""

    def calculate_score(
        self,
        incident1: IncidentRecord,
        incident2: IncidentRecord,
    ) -> tuple[float, str]:
        """Calculate time pattern similarity."""
        pattern1 = TimePattern.from_datetime(incident1.created_at)
        pattern2 = TimePattern.from_datetime(incident2.created_at)

        score = 0.0
        reasons = []

        if pattern1.day_of_week == pattern2.day_of_week:
            score += 0.3
            day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
            reasons.append(f"Same day ({day_names[pattern1.day_of_week or 0]})")

        if pattern1.hour_of_day is not None and pattern2.hour_of_day is not None:
            hour_diff = abs(pattern1.hour_of_day - pattern2.hour_of_day)
            if hour_diff <= 2:
                score += 0.3
                reasons.append(f"Similar time ({pattern1.hour_of_day}:00)")
            elif hour_diff <= 4:
                score += 0.15

        if pattern1.is_business_hours == pattern2.is_business_hours:
            score += 0.2
            if pattern1.is_business_hours:
                reasons.append("Both during business hours")
            else:
                reasons.append("Both outside business hours")

        if pattern1.is_deploy_window and pattern2.is_deploy_window:
            score += 0.2
            reasons.append("Both during deploy window")

        detail = ", ".join(reasons) if reasons else "Different time patterns"
        return min(score, 1.0), detail


class MetricsShapeMatcher:
    """Match incidents by metrics shape using DTW-like comparison."""

    def calculate_score(
        self,
        incident1: IncidentRecord,
        incident2: IncidentRecord,
    ) -> tuple[float, str]:
        """Calculate metrics shape similarity."""
        metrics1 = set(incident1.metrics_affected)
        metrics2 = set(incident2.metrics_affected)

        if not metrics1 and not metrics2:
            return 0.5, "No metrics data to compare"

        if not metrics1 or not metrics2:
            return 0.0, "One incident has no metrics data"

        common = metrics1 & metrics2
        union = metrics1 | metrics2

        base_score = len(common) / len(union) if union else 0.0

        metric_categories = {
            "latency": ["latency", "duration", "response_time", "p50", "p95", "p99"],
            "error": ["error", "5xx", "failure", "exception"],
            "throughput": ["rps", "qps", "requests", "throughput"],
            "resource": ["cpu", "memory", "disk", "network"],
        }

        cat1 = self._categorize_metrics(metrics1, metric_categories)
        cat2 = self._categorize_metrics(metrics2, metric_categories)

        cat_common = cat1 & cat2
        cat_union = cat1 | cat2

        cat_score = len(cat_common) / len(cat_union) if cat_union else 0.0

        final_score = (base_score * 0.6) + (cat_score * 0.4)

        if common:
            detail = f"Common metrics: {', '.join(list(common)[:3])}"
        elif cat_common:
            detail = f"Similar metric types: {', '.join(cat_common)}"
        else:
            detail = "Different metric patterns"

        return final_score, detail

    def _categorize_metrics(
        self,
        metrics: set[str],
        categories: dict[str, list[str]],
    ) -> set[str]:
        """Categorize metrics."""
        found_categories = set()
        for metric in metrics:
            metric_lower = metric.lower()
            for cat_name, keywords in categories.items():
                if any(kw in metric_lower for kw in keywords):
                    found_categories.add(cat_name)
        return found_categories


class CodeChangeMatcher:
    """Match incidents by code changes."""

    def calculate_score(
        self,
        incident1: IncidentRecord,
        incident2: IncidentRecord,
    ) -> tuple[float, str]:
        """Calculate code change similarity."""
        changes1 = set(self._normalize_path(c) for c in incident1.code_changes)
        changes2 = set(self._normalize_path(c) for c in incident2.code_changes)

        if not changes1 and not changes2:
            return 0.5, "No code changes to compare"

        if not changes1 or not changes2:
            return 0.0, "One incident has no code changes"

        exact_matches = changes1 & changes2
        if exact_matches:
            score = len(exact_matches) / max(len(changes1), len(changes2))
            detail = f"Same files: {', '.join(list(exact_matches)[:2])}"
            return min(score + 0.3, 1.0), detail

        dir1 = set(self._get_directory(c) for c in changes1)
        dir2 = set(self._get_directory(c) for c in changes2)
        common_dirs = dir1 & dir2

        if common_dirs:
            score = len(common_dirs) / max(len(dir1), len(dir2)) * 0.7
            detail = f"Same directories: {', '.join(list(common_dirs)[:2])}"
            return score, detail

        return 0.0, "Different code areas modified"

    def _normalize_path(self, path: str) -> str:
        """Normalize file path."""
        return path.lower().replace("\\", "/").strip("/")

    def _get_directory(self, path: str) -> str:
        """Get directory from path."""
        parts = self._normalize_path(path).split("/")
        return "/".join(parts[:-1]) if len(parts) > 1 else ""


class RootCauseMatcher:
    """Match incidents by root cause taxonomy."""

    CATEGORY_DISTANCES = {
        (RootCauseCategory.CODE_DEFECT, RootCauseCategory.CONFIGURATION): 0.7,
        (RootCauseCategory.INFRASTRUCTURE, RootCauseCategory.CAPACITY): 0.8,
        (RootCauseCategory.EXTERNAL_DEPENDENCY, RootCauseCategory.INFRASTRUCTURE): 0.6,
        (RootCauseCategory.HUMAN_ERROR, RootCauseCategory.CONFIGURATION): 0.7,
    }

    def calculate_score(
        self,
        incident1: IncidentRecord,
        incident2: IncidentRecord,
    ) -> tuple[float, str]:
        """Calculate root cause similarity."""
        cat1 = incident1.root_cause_category
        cat2 = incident2.root_cause_category

        if cat1 == RootCauseCategory.UNKNOWN or cat2 == RootCauseCategory.UNKNOWN:
            return 0.3, "Root cause unknown for one or both incidents"

        if cat1 == cat2:
            return 1.0, f"Same root cause category: {cat1.value}"

        pair = (cat1, cat2) if cat1.value < cat2.value else (cat2, cat1)
        distance = self.CATEGORY_DISTANCES.get(pair, 0.3)

        detail = f"Different causes: {cat1.value} vs {cat2.value}"
        return distance, detail


@dataclass
class MultiDimensionalScorer:
    """Score incident similarity across multiple dimensions."""

    weights: DimensionWeights | None = None

    def __post_init__(self):
        if self.weights is None:
            self.weights = DimensionWeights()

        self._symptom_matcher = SymptomMatcher()
        self._error_matcher = ErrorMessageMatcher()
        self._service_matcher = ServiceGraphMatcher()
        self._time_matcher = TimePatternMatcher()
        self._metrics_matcher = MetricsShapeMatcher()
        self._code_matcher = CodeChangeMatcher()
        self._root_cause_matcher = RootCauseMatcher()

    def set_dependency_graph(self, graph: dict[str, list[str]]) -> None:
        """Set service dependency graph."""
        self._service_matcher.set_dependency_graph(graph)

    def calculate_similarity(
        self,
        incident1: IncidentRecord,
        incident2: IncidentRecord,
        custom_weights: DimensionWeights | None = None,
    ) -> SimilarityScore:
        """Calculate multi-dimensional similarity score."""
        base_weights = custom_weights or self.weights or DimensionWeights()
        weights = base_weights.normalize()

        dimension_scores: dict[SimilarityDimension, float] = {}
        dimension_details: dict[SimilarityDimension, str] = {}

        symptom_score, symptom_detail = self._symptom_matcher.calculate_score(
            incident1, incident2
        )
        dimension_scores[SimilarityDimension.SYMPTOMS] = symptom_score
        dimension_details[SimilarityDimension.SYMPTOMS] = symptom_detail

        error_score, error_detail = self._error_matcher.calculate_score(
            incident1, incident2
        )
        dimension_scores[SimilarityDimension.ERROR_MESSAGES] = error_score
        dimension_details[SimilarityDimension.ERROR_MESSAGES] = error_detail

        service_score, service_detail = self._service_matcher.calculate_score(
            incident1, incident2
        )
        dimension_scores[SimilarityDimension.SERVICES] = service_score
        dimension_details[SimilarityDimension.SERVICES] = service_detail

        rc_score, rc_detail = self._root_cause_matcher.calculate_score(
            incident1, incident2
        )
        dimension_scores[SimilarityDimension.ROOT_CAUSE] = rc_score
        dimension_details[SimilarityDimension.ROOT_CAUSE] = rc_detail

        time_score, time_detail = self._time_matcher.calculate_score(
            incident1, incident2
        )
        dimension_scores[SimilarityDimension.TIME_PATTERN] = time_score
        dimension_details[SimilarityDimension.TIME_PATTERN] = time_detail

        metrics_score, metrics_detail = self._metrics_matcher.calculate_score(
            incident1, incident2
        )
        dimension_scores[SimilarityDimension.METRICS_SHAPE] = metrics_score
        dimension_details[SimilarityDimension.METRICS_SHAPE] = metrics_detail

        code_score, code_detail = self._code_matcher.calculate_score(
            incident1, incident2
        )
        dimension_scores[SimilarityDimension.CODE_CHANGES] = code_score
        dimension_details[SimilarityDimension.CODE_CHANGES] = code_detail

        overall = (
            symptom_score * weights.symptoms
            + error_score * weights.error_messages
            + service_score * weights.services
            + rc_score * weights.root_cause
            + time_score * weights.time_pattern
            + metrics_score * weights.metrics_shape
            + code_score * weights.code_changes
        )

        match_reasons = []
        difference_reasons = []

        threshold = 0.7
        for dim, score in dimension_scores.items():
            if score >= threshold:
                match_reasons.append(dimension_details[dim])
            elif score < 0.3:
                difference_reasons.append(dimension_details[dim])

        high_scores = sum(1 for s in dimension_scores.values() if s >= threshold)
        confidence = high_scores / len(dimension_scores)

        return SimilarityScore(
            overall_score=overall,
            dimension_scores=dimension_scores,
            dimension_details=dimension_details,
            confidence=confidence,
            match_reasons=match_reasons,
            difference_reasons=difference_reasons,
        )

    def explain_similarity(
        self,
        incident1: IncidentRecord,
        incident2: IncidentRecord,
    ) -> dict[str, Any]:
        """Generate detailed similarity explanation."""
        similarity = self.calculate_similarity(incident1, incident2)

        dimensions_data: dict[str, dict[str, str]] = {}
        for dim, score in similarity.dimension_scores.items():
            dimensions_data[dim.value] = {
                "score": f"{score:.0%}",
                "detail": similarity.dimension_details.get(dim, ""),
            }

        return {
            "overall_similarity": f"{similarity.overall_score:.0%}",
            "confidence": f"{similarity.confidence:.0%}",
            "dimensions": dimensions_data,
            "key_similarities": similarity.match_reasons,
            "key_differences": similarity.difference_reasons,
        }
