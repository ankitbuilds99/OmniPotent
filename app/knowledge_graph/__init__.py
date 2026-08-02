"""Knowledge Graph module.

Provides:
- Neo4j-backed graph database for infrastructure knowledge
- GraphQL API with Strawberry
- Real-time subscriptions
- Continuous sync engine for GitHub, CloudTrail, Prometheus, K8s, Jaeger
"""

from app.knowledge_graph.neo4j_backend import (
    Neo4jKnowledgeGraph,
    ServiceNode,
    DeploymentNode,
    IncidentNode,
    ChangeNode,
    AlertNode,
    Relationship,
)
from app.knowledge_graph.sync_engine import (
    ContinuousSyncEngine,
    SyncSource,
    GitHubSyncer,
    CloudTrailSyncer,
    PrometheusSyncer,
    KubernetesSyncer,
    JaegerSyncer,
)

__all__ = [
    "Neo4jKnowledgeGraph",
    "ServiceNode",
    "DeploymentNode",
    "IncidentNode",
    "ChangeNode",
    "AlertNode",
    "Relationship",
    "ContinuousSyncEngine",
    "SyncSource",
    "GitHubSyncer",
    "CloudTrailSyncer",
    "PrometheusSyncer",
    "KubernetesSyncer",
    "JaegerSyncer",
]
