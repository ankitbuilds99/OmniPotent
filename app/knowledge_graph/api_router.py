"""Knowledge Graph API Router.

REST API and GraphQL endpoint for the knowledge graph.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request, Depends, BackgroundTasks
from pydantic import BaseModel

from app.knowledge_graph.neo4j_backend import (
    Neo4jKnowledgeGraph,
    ServiceNode,
    DeploymentNode,
    IncidentNode,
    ChangeNode,
    AlertNode,
    Relationship,
    RelationType,
)
from app.knowledge_graph.sync_engine import ContinuousSyncEngine
from app.config import get_settings

log = logging.getLogger("omnipotent.knowledge_graph.api")

router = APIRouter(prefix="/knowledge-graph", tags=["knowledge-graph"])

_graph: Neo4jKnowledgeGraph | None = None
_sync_engine: ContinuousSyncEngine | None = None
_graphql_app: Any = None


async def get_graph() -> Neo4jKnowledgeGraph:
    """Get or create knowledge graph instance."""
    global _graph
    if _graph is None:
        settings = get_settings()
        _graph = Neo4jKnowledgeGraph(
            uri=getattr(settings, "neo4j_uri", "bolt://localhost:7687"),
            username=getattr(settings, "neo4j_username", "neo4j"),
            password=getattr(settings, "neo4j_password", ""),
            database=getattr(settings, "neo4j_database", "neo4j"),
        )
        await _graph.connect()
    return _graph


async def get_sync_engine() -> ContinuousSyncEngine:
    """Get or create sync engine instance."""
    global _sync_engine
    if _sync_engine is None:
        graph = await get_graph()
        _sync_engine = ContinuousSyncEngine(graph)
    return _sync_engine


async def get_graphql_context(request: Request) -> dict[str, Any]:
    """Get context for GraphQL resolvers."""
    return {"graph": await get_graph(), "request": request}


def _init_graphql() -> Any:
    """Initialize GraphQL router if strawberry is available."""
    global _graphql_app
    if _graphql_app is not None:
        return _graphql_app

    try:
        from strawberry.fastapi import GraphQLRouter
        from app.knowledge_graph.graphql_schema import create_schema

        graphql_schema = create_schema()
        _graphql_app = GraphQLRouter(
            schema=graphql_schema,
            context_getter=get_graphql_context,
        )
        return _graphql_app
    except ImportError:
        log.warning("strawberry-graphql not installed, GraphQL endpoint unavailable")
        return None


class ServiceInput(BaseModel):
    id: str
    name: str
    namespace: str = "default"
    tier: int = 1
    team: str = ""
    repository: str = ""
    language: str = ""
    framework: str = ""
    health_status: str = "unknown"
    criticality: float = 0.5


class DeploymentInput(BaseModel):
    id: str
    service_id: str
    version: str
    commit_sha: str = ""
    deployed_by: str = ""
    environment: str = "production"
    status: str = "success"
    duration_seconds: int = 0
    rollback_of: str = ""
    changes: list[str] = []


class IncidentInput(BaseModel):
    id: str
    title: str
    severity: str = "P3"
    status: str = "open"
    affected_services: list[str] = []
    root_cause: str = ""
    impact: str = ""


class ChangeInput(BaseModel):
    id: str
    change_type: str
    title: str
    description: str = ""
    author: str = ""
    source: str = ""
    affected_resources: list[str] = []
    risk_score: float = 0.0
    diff_url: str = ""


class AlertInput(BaseModel):
    id: str
    name: str
    severity: str = "warning"
    source: str = ""
    service: str = ""
    fingerprint: str = ""
    value: float = 0.0
    threshold: float = 0.0


class RelationshipInput(BaseModel):
    source_id: str
    target_id: str
    rel_type: str


class SyncSourceConfig(BaseModel):
    source: str
    config: dict[str, Any]


@router.post("/services")
async def create_service(
    input: ServiceInput,
    graph: Neo4jKnowledgeGraph = Depends(get_graph),
) -> dict[str, Any]:
    """Create or update a service."""
    service = ServiceNode(
        id=input.id,
        name=input.name,
        namespace=input.namespace,
        tier=input.tier,
        team=input.team,
        repository=input.repository,
        language=input.language,
        framework=input.framework,
        health_status=input.health_status,
        criticality=input.criticality,
    )
    created = await graph.create_service(service)
    return created.to_cypher_properties()


@router.get("/services/{service_id}")
async def get_service(
    service_id: str,
    graph: Neo4jKnowledgeGraph = Depends(get_graph),
) -> dict[str, Any]:
    """Get a service by ID."""
    service = await graph.get_service(service_id)
    if not service:
        return {"error": "Service not found"}
    return service.to_cypher_properties()


@router.get("/services")
async def list_services(
    namespace: str | None = None,
    team: str | None = None,
    health_status: str | None = None,
    limit: int = 100,
    graph: Neo4jKnowledgeGraph = Depends(get_graph),
) -> dict[str, Any]:
    """List services with filters."""
    services = await graph.list_services(namespace, team, health_status, limit)
    return {
        "services": [s.to_cypher_properties() for s in services],
        "count": len(services),
    }


@router.post("/deployments")
async def create_deployment(
    input: DeploymentInput,
    graph: Neo4jKnowledgeGraph = Depends(get_graph),
) -> dict[str, Any]:
    """Create a deployment."""
    deployment = DeploymentNode(
        id=input.id,
        service_id=input.service_id,
        version=input.version,
        commit_sha=input.commit_sha,
        deployed_by=input.deployed_by,
        environment=input.environment,
        status=input.status,
        duration_seconds=input.duration_seconds,
        rollback_of=input.rollback_of,
        changes=input.changes,
    )
    created = await graph.create_deployment(deployment)
    return created.to_cypher_properties()


@router.get("/deployments/recent")
async def get_recent_deployments(
    service_id: str | None = None,
    hours: int = 24,
    limit: int = 50,
    graph: Neo4jKnowledgeGraph = Depends(get_graph),
) -> dict[str, Any]:
    """Get recent deployments."""
    deployments = await graph.get_recent_deployments(service_id, hours, limit)
    return {
        "deployments": [d.to_cypher_properties() for d in deployments],
        "count": len(deployments),
    }


@router.post("/incidents")
async def create_incident(
    input: IncidentInput,
    graph: Neo4jKnowledgeGraph = Depends(get_graph),
) -> dict[str, Any]:
    """Create an incident."""
    incident = IncidentNode(
        id=input.id,
        title=input.title,
        severity=input.severity,
        status=input.status,
        affected_services=input.affected_services,
        root_cause=input.root_cause,
        impact=input.impact,
    )
    created = await graph.create_incident(incident)
    return created.to_cypher_properties()


@router.get("/incidents/{incident_id}")
async def get_incident(
    incident_id: str,
    graph: Neo4jKnowledgeGraph = Depends(get_graph),
) -> dict[str, Any]:
    """Get an incident by ID."""
    incident = await graph.get_incident(incident_id)
    if not incident:
        return {"error": "Incident not found"}
    return incident.to_cypher_properties()


@router.post("/changes")
async def create_change(
    input: ChangeInput,
    graph: Neo4jKnowledgeGraph = Depends(get_graph),
) -> dict[str, Any]:
    """Create a change record."""
    change = ChangeNode(
        id=input.id,
        change_type=input.change_type,
        title=input.title,
        description=input.description,
        author=input.author,
        source=input.source,
        affected_resources=input.affected_resources,
        risk_score=input.risk_score,
        diff_url=input.diff_url,
    )
    created = await graph.create_change(change)
    return created.to_cypher_properties()


@router.post("/alerts")
async def create_alert(
    input: AlertInput,
    graph: Neo4jKnowledgeGraph = Depends(get_graph),
) -> dict[str, Any]:
    """Create an alert."""
    alert = AlertNode(
        id=input.id,
        name=input.name,
        severity=input.severity,
        source=input.source,
        service=input.service,
        fingerprint=input.fingerprint,
        value=input.value,
        threshold=input.threshold,
    )
    created = await graph.create_alert(alert)
    return created.to_cypher_properties()


@router.post("/relationships")
async def create_relationship(
    input: RelationshipInput,
    graph: Neo4jKnowledgeGraph = Depends(get_graph),
) -> dict[str, Any]:
    """Create a relationship between nodes."""
    try:
        rel_type = RelationType(input.rel_type)
    except ValueError:
        rel_type = RelationType.RELATED_TO

    rel = Relationship(
        source_id=input.source_id,
        target_id=input.target_id,
        rel_type=rel_type,
    )
    created = await graph.create_relationship(rel)
    return {
        "source_id": created.source_id,
        "target_id": created.target_id,
        "type": created.rel_type.value,
    }


@router.get("/dependencies/{service_id}")
async def get_dependencies(
    service_id: str,
    depth: int = 3,
    direction: str = "both",
    graph: Neo4jKnowledgeGraph = Depends(get_graph),
) -> dict[str, Any]:
    """Get service dependencies."""
    return await graph.find_dependencies(service_id, depth, direction)


@router.get("/blast-radius/{service_id}")
async def get_blast_radius(
    service_id: str,
    graph: Neo4jKnowledgeGraph = Depends(get_graph),
) -> dict[str, Any]:
    """Calculate blast radius for a service failure."""
    result = await graph.find_blast_radius(service_id)
    return {
        "source_service": result["source_service"],
        "affected_count": result["affected_count"],
        "total_impact_score": result["total_impact_score"],
        "affected_services": [
            {
                "service": a["service"].to_cypher_properties(),
                "distance": a["distance"],
                "impact_score": a["impact_score"],
            }
            for a in result.get("affected_services", [])
        ],
    }


@router.get("/changes-before-incident/{incident_id}")
async def get_changes_before_incident(
    incident_id: str,
    window_hours: int = 24,
    graph: Neo4jKnowledgeGraph = Depends(get_graph),
) -> dict[str, Any]:
    """Find changes before an incident."""
    changes = await graph.find_changes_before_incident(incident_id, window_hours)
    return {
        "incident_id": incident_id,
        "window_hours": window_hours,
        "changes": [c.to_cypher_properties() for c in changes],
        "count": len(changes),
    }


@router.get("/timeline/{service_id}")
async def get_service_timeline(
    service_id: str,
    hours: int = 24,
    graph: Neo4jKnowledgeGraph = Depends(get_graph),
) -> dict[str, Any]:
    """Get timeline of events for a service."""
    timeline = await graph.get_service_timeline(service_id, hours)
    return {
        "service_id": service_id,
        "hours": hours,
        "events": timeline,
        "count": len(timeline),
    }


@router.get("/stats")
async def get_stats(
    graph: Neo4jKnowledgeGraph = Depends(get_graph),
) -> dict[str, Any]:
    """Get graph statistics."""
    return await graph.get_stats()


@router.post("/sync/configure")
async def configure_sync_source(
    config: SyncSourceConfig,
    background_tasks: BackgroundTasks,
    engine: ContinuousSyncEngine = Depends(get_sync_engine),
) -> dict[str, Any]:
    """Configure a sync source."""
    source = config.source.lower()
    cfg = config.config

    if source == "github":
        engine.add_github(
            token=cfg.get("token", ""),
            owner=cfg.get("owner", ""),
            repos=cfg.get("repos", []),
        )
    elif source == "cloudtrail":
        engine.add_cloudtrail(
            region=cfg.get("region", "us-east-1"),
            trail_name=cfg.get("trail_name"),
        )
    elif source == "prometheus":
        engine.add_prometheus(
            prometheus_url=cfg.get("prometheus_url", "http://localhost:9090"),
            alertmanager_url=cfg.get("alertmanager_url", "http://localhost:9093"),
        )
    elif source == "kubernetes":
        engine.add_kubernetes(
            kubeconfig=cfg.get("kubeconfig"),
            context=cfg.get("context"),
            namespace=cfg.get("namespace"),
        )
    elif source == "jaeger":
        engine.add_jaeger(
            jaeger_url=cfg.get("jaeger_url", "http://localhost:16686"),
        )
    else:
        return {"error": f"Unknown source: {source}"}

    return {
        "status": "configured",
        "source": source,
    }


@router.post("/sync/start")
async def start_sync(
    background_tasks: BackgroundTasks,
    engine: ContinuousSyncEngine = Depends(get_sync_engine),
) -> dict[str, Any]:
    """Start continuous sync."""
    background_tasks.add_task(engine.start_continuous)
    return {"status": "started"}


@router.post("/sync/stop")
async def stop_sync(
    engine: ContinuousSyncEngine = Depends(get_sync_engine),
) -> dict[str, Any]:
    """Stop continuous sync."""
    await engine.stop()
    return {"status": "stopped"}


@router.post("/sync/once")
async def sync_once(
    engine: ContinuousSyncEngine = Depends(get_sync_engine),
) -> dict[str, Any]:
    """Run one sync cycle."""
    results = await engine.sync_all_once()
    return {
        "status": "completed",
        "results": results,
    }


@router.get("/sync/status")
async def get_sync_status(
    engine: ContinuousSyncEngine = Depends(get_sync_engine),
) -> dict[str, Any]:
    """Get sync engine status."""
    return engine.get_status()


@router.get("/graphql/status")
async def graphql_status() -> dict[str, Any]:
    """Check if GraphQL is available."""
    gql = _init_graphql()
    return {
        "available": gql is not None,
        "message": "GraphQL endpoint available at /api/knowledge-graph/graphql" if gql else "Install strawberry-graphql[fastapi] to enable GraphQL",
    }


def include_graphql_router(parent_router: APIRouter) -> None:
    """Include GraphQL router if available."""
    gql = _init_graphql()
    if gql:
        parent_router.include_router(gql, prefix="/graphql")
