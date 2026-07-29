"""Main search interface for similar incidents."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from app.similar_search.models import (
    IncidentRecord,
    SimilarIncident,
    SimilarityScore,
    SearchFilters,
    SearchResult,
    ResolutionRecommendation,
    RecurringPattern,
    IncidentSeverity,
)
from app.similar_search.embeddings import (
    IncidentEmbedder,
    EmbeddingConfig,
    EmbeddingStore,
    HybridSearchEngine,
)
from app.similar_search.similarity import MultiDimensionalScorer
from app.similar_search.patterns import PatternDetector, TrendAnalyzer, CostCalculator
from app.similar_search.knowledge_graph import (
    IncidentKnowledgeGraph,
    GraphBuilder,
    GraphQuery,
)
from app.similar_search.recommendations import (
    ResolutionEngine,
    RunbookMatcher,
    FixTracker,
)

log = logging.getLogger("devcopilot.similar_search.search")


@dataclass
class SearchConfig:
    """Configuration for similar incident search."""

    embedding_config: EmbeddingConfig = field(default_factory=EmbeddingConfig)
    default_limit: int = 10
    min_similarity_threshold: float = 0.5
    enable_patterns: bool = True
    enable_recommendations: bool = True
    enable_knowledge_graph: bool = True
    pattern_lookback_days: int = 90
    auto_suggest_threshold: float = 0.8
    proactive_check_interval_minutes: int = 5


class SimilarIncidentSearch:
    """Main interface for similar incident search."""

    def __init__(self, config: SearchConfig | None = None):
        self._config = config or SearchConfig()

        self._embedder = IncidentEmbedder(self._config.embedding_config)
        self._store = EmbeddingStore()
        self._search_engine = HybridSearchEngine(self._embedder, self._store)

        self._scorer = MultiDimensionalScorer()

        self._pattern_detector = PatternDetector(
            lookback_days=self._config.pattern_lookback_days
        )
        self._trend_analyzer = TrendAnalyzer()
        self._cost_calculator = CostCalculator()

        self._graph = IncidentKnowledgeGraph()
        self._graph_builder = GraphBuilder(self._graph)
        self._graph_query = GraphQuery(self._graph)

        self._resolution_engine = ResolutionEngine()
        self._runbook_matcher = RunbookMatcher()
        self._fix_tracker = FixTracker()

        self._incidents: dict[UUID, IncidentRecord] = {}

    def index_incident(self, incident: IncidentRecord) -> None:
        """Index an incident for searching."""
        self._incidents[incident.incident_id] = incident

        self._search_engine.index_incident(incident)

        if self._config.enable_knowledge_graph:
            self._graph_builder.add_incident(incident)

        if self._config.enable_recommendations:
            self._resolution_engine.index_incident(incident)

    def index_many(self, incidents: list[IncidentRecord]) -> int:
        """Index multiple incidents."""
        for incident in incidents:
            self.index_incident(incident)
        return len(incidents)

    def search(
        self,
        query: str | None = None,
        current_incident: IncidentRecord | None = None,
        filters: SearchFilters | None = None,
        limit: int | None = None,
        include_recommendations: bool = True,
    ) -> SearchResult:
        """Search for similar incidents."""
        start_time = time.time()

        limit = limit or self._config.default_limit
        filters = filters or SearchFilters()

        if filters.min_similarity < self._config.min_similarity_threshold:
            filters.min_similarity = self._config.min_similarity_threshold

        vector_results = self._search_engine.search(
            query=query,
            current_incident=current_incident,
            filters=filters,
            limit=limit * 2,
        )

        similar_incidents = []
        for incident, vector_score in vector_results:
            if current_incident:
                similarity = self._scorer.calculate_similarity(
                    current_incident, incident
                )
            else:
                similarity = SimilarityScore(
                    overall_score=vector_score,
                    confidence=vector_score,
                )

            suggested_actions = []
            if current_incident and include_recommendations:
                suggested_actions = self._extract_suggested_actions(
                    current_incident, incident
                )

            resolution_applicable = self._is_resolution_applicable(
                current_incident, incident
            )

            similar = SimilarIncident(
                incident=incident,
                similarity=similarity,
                suggested_actions=suggested_actions,
                resolution_applicable=resolution_applicable,
                resolution_confidence=similarity.overall_score if resolution_applicable else 0.0,
            )
            similar_incidents.append(similar)

        similar_incidents.sort(
            key=lambda s: s.similarity.overall_score, reverse=True
        )
        similar_incidents = similar_incidents[:limit]

        suggestions = []
        if current_incident:
            suggestions = self._generate_suggestions(
                current_incident, similar_incidents
            )

        search_time = (time.time() - start_time) * 1000

        return SearchResult(
            query=query or "",
            current_incident=current_incident,
            similar_incidents=similar_incidents,
            total_found=len(similar_incidents),
            search_time_ms=search_time,
            filters_applied=filters,
            suggestions=suggestions,
        )

    def _extract_suggested_actions(
        self,
        current: IncidentRecord,
        similar: IncidentRecord,
    ) -> list[str]:
        """Extract suggested actions from similar incident."""
        actions = []

        if similar.resolution_steps:
            actions.extend(similar.resolution_steps[:3])

        if similar.resolution_summary:
            actions.append(f"Consider: {similar.resolution_summary[:100]}")

        return actions[:5]

    def _is_resolution_applicable(
        self,
        current: IncidentRecord | None,
        similar: IncidentRecord,
    ) -> bool:
        """Check if resolution is applicable to current incident."""
        if not similar.resolution_summary:
            return False

        if not current:
            return True

        if current.root_cause_category == similar.root_cause_category:
            return True

        common_services = set(current.services) & set(similar.services)
        if common_services:
            return True

        return False

    def _generate_suggestions(
        self,
        current: IncidentRecord,
        similar: list[SimilarIncident],
    ) -> list[str]:
        """Generate suggestions based on search results."""
        suggestions = []

        if not similar:
            suggestions.append("No similar incidents found. This may be a new issue.")
            return suggestions

        high_confidence = [s for s in similar if s.similarity.overall_score > 0.8]
        if high_confidence:
            suggestions.append(
                f"Found {len(high_confidence)} highly similar incident(s). "
                "Review their resolutions."
            )

        root_causes: dict[str, int] = {}
        for s in similar:
            rc = s.incident.root_cause_category.value
            root_causes[rc] = root_causes.get(rc, 0) + 1

        if root_causes:
            top_cause = max(root_causes.items(), key=lambda x: x[1])
            suggestions.append(
                f"Most common root cause in similar incidents: {top_cause[0]}"
            )

        avg_duration = sum(
            s.incident.duration_minutes for s in similar
        ) / len(similar)
        suggestions.append(
            f"Similar incidents resolved in ~{int(avg_duration)} minutes on average"
        )

        return suggestions

    def get_recommendations(
        self,
        incident: IncidentRecord,
        limit: int = 5,
    ) -> list[ResolutionRecommendation]:
        """Get resolution recommendations for an incident."""
        search_result = self.search(
            current_incident=incident,
            limit=20,
            include_recommendations=False,
        )

        similar_incidents = [s.incident for s in search_result.similar_incidents]

        return self._resolution_engine.generate_recommendations(
            incident, similar_incidents, limit
        )

    def detect_patterns(
        self,
        incidents: list[IncidentRecord] | None = None,
    ) -> list[RecurringPattern]:
        """Detect recurring patterns in incidents."""
        if incidents is None:
            incidents = list(self._incidents.values())

        patterns = self._pattern_detector.detect_patterns(incidents)

        for pattern in patterns:
            self._cost_calculator.calculate_pattern_cost(pattern)

        return patterns

    def get_pattern_for_incident(
        self,
        incident_id: UUID,
    ) -> RecurringPattern | None:
        """Get pattern associated with an incident."""
        return self._pattern_detector.get_pattern_for_incident(incident_id)

    def get_trends(
        self,
        incidents: list[IncidentRecord] | None = None,
    ) -> dict[str, Any]:
        """Get incident trends."""
        if incidents is None:
            incidents = list(self._incidents.values())

        return self._trend_analyzer.analyze_trends(incidents)

    def find_related_through_graph(
        self,
        incident_id: UUID,
        max_depth: int = 2,
    ) -> list[tuple[str, float]]:
        """Find related incidents through knowledge graph."""
        return self._graph_query.find_related_incidents(incident_id, max_depth)

    def get_service_history(
        self,
        service: str,
        days: int = 30,
    ) -> dict[str, Any]:
        """Get incident history for a service."""
        return self._graph_query.get_service_incident_history(service, days)

    def set_service_dependencies(
        self,
        dependencies: dict[str, list[str]],
    ) -> None:
        """Set service dependency graph."""
        self._scorer.set_dependency_graph(dependencies)

        for upstream, downstreams in dependencies.items():
            for downstream in downstreams:
                self._graph_builder.add_service_dependency(upstream, downstream)

    def get_stats(self) -> dict[str, Any]:
        """Get search engine statistics."""
        return {
            "indexed_incidents": len(self._incidents),
            "search_engine": self._search_engine.get_stats(),
            "knowledge_graph": self._graph.get_stats().__dict__,
        }


@dataclass
class TriggerCondition:
    """Condition for auto-suggest trigger."""

    min_alerts: int = 3
    min_services: int = 2
    severity_threshold: IncidentSeverity = IncidentSeverity.P2
    error_pattern_match: str | None = None
    time_since_last_incident_hours: float | None = None


class AutoSuggestTrigger:
    """Trigger auto-suggestions based on conditions."""

    def __init__(
        self,
        search: SimilarIncidentSearch,
        conditions: list[TriggerCondition] | None = None,
    ):
        self._search = search
        self._conditions = conditions or [TriggerCondition()]
        self._last_triggered: dict[UUID, datetime] = {}

    def check_trigger(
        self,
        incident: IncidentRecord,
    ) -> SearchResult | None:
        """Check if auto-suggest should trigger."""
        if incident.incident_id in self._last_triggered:
            last_time = self._last_triggered[incident.incident_id]
            if datetime.utcnow() - last_time < timedelta(minutes=5):
                return None

        should_trigger = False
        for condition in self._conditions:
            if self._matches_condition(incident, condition):
                should_trigger = True
                break

        if not should_trigger:
            return None

        self._last_triggered[incident.incident_id] = datetime.utcnow()

        return self._search.search(
            current_incident=incident,
            limit=5,
            include_recommendations=True,
        )

    def _matches_condition(
        self,
        incident: IncidentRecord,
        condition: TriggerCondition,
    ) -> bool:
        """Check if incident matches trigger condition."""
        if len(incident.alerts) < condition.min_alerts:
            return False

        if len(incident.services) < condition.min_services:
            return False

        severity_order = {
            IncidentSeverity.P1: 1,
            IncidentSeverity.P2: 2,
            IncidentSeverity.P3: 3,
            IncidentSeverity.P4: 4,
        }

        incident_priority = severity_order.get(incident.severity, 4)
        threshold_priority = severity_order.get(condition.severity_threshold, 2)
        if incident_priority > threshold_priority:
            return False

        if condition.error_pattern_match:
            error_text = " ".join(incident.error_messages).lower()
            if condition.error_pattern_match.lower() not in error_text:
                return False

        return True

    def add_condition(self, condition: TriggerCondition) -> None:
        """Add a trigger condition."""
        self._conditions.append(condition)


class ProactiveSuggester:
    """Proactively suggest similar incidents during investigation."""

    def __init__(
        self,
        search: SimilarIncidentSearch,
        check_interval_minutes: int = 5,
    ):
        self._search = search
        self._check_interval = timedelta(minutes=check_interval_minutes)
        self._active_incidents: dict[UUID, datetime] = {}
        self._last_suggestions: dict[UUID, SearchResult] = {}

    def register_active_incident(
        self,
        incident: IncidentRecord,
    ) -> SearchResult:
        """Register an incident and get initial suggestions."""
        self._active_incidents[incident.incident_id] = datetime.utcnow()

        result = self._search.search(
            current_incident=incident,
            limit=5,
            include_recommendations=True,
        )
        self._last_suggestions[incident.incident_id] = result

        return result

    def update_incident(
        self,
        incident: IncidentRecord,
    ) -> SearchResult | None:
        """Update incident data and check for new suggestions."""
        if incident.incident_id not in self._active_incidents:
            return self.register_active_incident(incident)

        last_check = self._active_incidents[incident.incident_id]
        if datetime.utcnow() - last_check < self._check_interval:
            return None

        self._active_incidents[incident.incident_id] = datetime.utcnow()

        result = self._search.search(
            current_incident=incident,
            limit=5,
            include_recommendations=True,
        )

        last_result = self._last_suggestions.get(incident.incident_id)
        if last_result:
            new_incidents = self._find_new_suggestions(last_result, result)
            if not new_incidents:
                return None

        self._last_suggestions[incident.incident_id] = result
        return result

    def _find_new_suggestions(
        self,
        old_result: SearchResult,
        new_result: SearchResult,
    ) -> list[SimilarIncident]:
        """Find new suggestions not in previous result."""
        old_ids = {
            s.incident.incident_id for s in old_result.similar_incidents
        }

        return [
            s
            for s in new_result.similar_incidents
            if s.incident.incident_id not in old_ids
        ]

    def resolve_incident(self, incident_id: UUID) -> None:
        """Mark incident as resolved and stop monitoring."""
        self._active_incidents.pop(incident_id, None)
        self._last_suggestions.pop(incident_id, None)

    def get_active_incidents(self) -> list[UUID]:
        """Get list of actively monitored incidents."""
        return list(self._active_incidents.keys())

    def get_suggestions_for_all(self) -> dict[UUID, SearchResult]:
        """Get current suggestions for all active incidents."""
        return dict(self._last_suggestions)
