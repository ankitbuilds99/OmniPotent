"""
Advanced Similar Incident Search System.

A multi-dimensional similarity engine that finds related incidents through:
- Semantic vector search using embeddings
- Multi-dimensional scoring (symptoms, errors, services, time patterns)
- Pattern detection for recurring incidents
- Resolution recommendations from past fixes
- Knowledge graph for incident relationships
- Effectiveness tracking for fixes

Key differentiators:
- AI-powered semantic understanding across different descriptions
- Resolution recommendations with code examples
- Pattern detection with cost calculation
- Proactive suggestions during investigation
- Tracks which fixes actually work vs band-aids
"""

from app.similar_search.models import (
    IncidentRecord,
    SimilarIncident,
    SimilarityScore,
    SimilarityDimension,
    SearchFilters,
    SearchResult,
    ResolutionRecommendation,
    RecurringPattern,
    PatternOccurrence,
    KnowledgeNode,
    KnowledgeEdge,
    FixEffectiveness,
    RunbookSuggestion,
    IncidentEmbedding,
    TimePattern,
    ServiceOverlap,
    DimensionWeights,
    IncidentSeverity,
    IncidentStatus,
    RootCauseCategory,
)

from app.similar_search.embeddings import (
    IncidentEmbedder,
    EmbeddingConfig,
    EmbeddingStore,
    HybridSearchEngine,
)

from app.similar_search.similarity import (
    MultiDimensionalScorer,
    SymptomMatcher,
    ErrorMessageMatcher,
    ServiceGraphMatcher,
    TimePatternMatcher,
    MetricsShapeMatcher,
    CodeChangeMatcher,
    RootCauseMatcher,
)

from app.similar_search.patterns import (
    PatternDetector,
    TrendAnalyzer,
    RecurrenceTracker,
    CostCalculator,
)

from app.similar_search.knowledge_graph import (
    IncidentKnowledgeGraph,
    GraphBuilder,
    GraphQuery,
    RelationshipAnalyzer,
)

from app.similar_search.recommendations import (
    ResolutionEngine,
    RunbookMatcher,
    FixTracker,
    EffectivenessAnalyzer,
)

from app.similar_search.search import (
    SimilarIncidentSearch,
    SearchConfig,
    AutoSuggestTrigger,
    ProactiveSuggester,
)

__all__ = [
    # Models
    "IncidentRecord",
    "SimilarIncident",
    "SimilarityScore",
    "SimilarityDimension",
    "SearchFilters",
    "SearchResult",
    "ResolutionRecommendation",
    "RecurringPattern",
    "PatternOccurrence",
    "KnowledgeNode",
    "KnowledgeEdge",
    "FixEffectiveness",
    "RunbookSuggestion",
    "IncidentEmbedding",
    "TimePattern",
    "ServiceOverlap",
    "DimensionWeights",
    "IncidentSeverity",
    "IncidentStatus",
    "RootCauseCategory",
    # Embeddings
    "IncidentEmbedder",
    "EmbeddingConfig",
    "EmbeddingStore",
    "HybridSearchEngine",
    # Similarity
    "MultiDimensionalScorer",
    "SymptomMatcher",
    "ErrorMessageMatcher",
    "ServiceGraphMatcher",
    "TimePatternMatcher",
    "MetricsShapeMatcher",
    "CodeChangeMatcher",
    "RootCauseMatcher",
    # Patterns
    "PatternDetector",
    "TrendAnalyzer",
    "RecurrenceTracker",
    "CostCalculator",
    # Knowledge Graph
    "IncidentKnowledgeGraph",
    "GraphBuilder",
    "GraphQuery",
    "RelationshipAnalyzer",
    # Recommendations
    "ResolutionEngine",
    "RunbookMatcher",
    "FixTracker",
    "EffectivenessAnalyzer",
    # Search
    "SimilarIncidentSearch",
    "SearchConfig",
    "AutoSuggestTrigger",
    "ProactiveSuggester",
]
