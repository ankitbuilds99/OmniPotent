"""Cross-incident learning engine.

Advanced features:
- Semantic incident clustering
- Root cause taxonomy
- Fix effectiveness tracking
- Knowledge graph
- Proactive recommendations
- Runbook auto-generation
- Expertise mapping
"""

from app.learning.clustering import (
    IncidentCluster,
    ClusterAnalysis,
    IncidentClusterer,
    get_incident_clusterer,
)
from app.learning.knowledge_graph import (
    KnowledgeNode,
    KnowledgeEdge,
    IncidentKnowledgeGraph,
    get_knowledge_graph,
)
from app.learning.recommendations import (
    ProactiveRecommendation,
    RecommendationEngine,
    get_recommendation_engine,
)
from app.learning.expertise import (
    EngineerExpertise,
    ExpertiseMapper,
    get_expertise_mapper,
)

__all__ = [
    "IncidentCluster",
    "ClusterAnalysis",
    "IncidentClusterer",
    "get_incident_clusterer",
    "KnowledgeNode",
    "KnowledgeEdge",
    "IncidentKnowledgeGraph",
    "get_knowledge_graph",
    "ProactiveRecommendation",
    "RecommendationEngine",
    "get_recommendation_engine",
    "EngineerExpertise",
    "ExpertiseMapper",
    "get_expertise_mapper",
]
