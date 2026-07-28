"""Multi-Incident Correlation module.

AI-powered alert correlation with semantic analysis, root cause finding,
and real-time correlation streaming.
"""

from app.correlation.engine import (
    CorrelationEngine,
    get_correlation_engine,
)
from app.correlation.models import (
    Alert,
    AlertGroup,
    CorrelatedIncident,
    CorrelationConfidence,
    CorrelationExplanation,
    CorrelationResult,
    ImpactGraph,
    RootCauseAnalysis,
)
from app.correlation.semantic import (
    SemanticAnalyzer,
    get_semantic_analyzer,
)
from app.correlation.storm import (
    cluster_by_time,
    correlate_storm,
    rank_root_causes,
)

__all__ = [
    "Alert",
    "AlertGroup",
    "CorrelatedIncident",
    "CorrelationConfidence",
    "CorrelationEngine",
    "CorrelationExplanation",
    "CorrelationResult",
    "ImpactGraph",
    "RootCauseAnalysis",
    "SemanticAnalyzer",
    "cluster_by_time",
    "correlate_storm",
    "get_correlation_engine",
    "get_semantic_analyzer",
    "rank_root_causes",
]
