"""Knowledge Graph API Router.

REST API and GraphQL endpoint for the knowledge graph.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request, Depends, BackgroundTasks
from pydantic import BaseModel

from app.tenant_context import tenant_id
from app.tenancy.scoping import scoped
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

_graphql_app: Any = None


class _OrgGraph:
    """One graph client and sync engine for one org, connected exactly once.

    With no `copilot_neo4j_uri` — the default deployment — `Neo4jKnowledgeGraph`
    reads and writes `graph_nodes`/`graph_edges` through
    `app.knowledge_graph.graph_store`, which scopes every row by the requesting
    org. This registry keeps one client (and one sync engine) per org so that
    connect() and the sync engine's state are not shared between tenants either;
    the row-level scoping is what keeps the data apart.

    A *real* Neo4j connection is a different matter: N clients against one
    database isolate nothing, because no Cypher query in neo4j_backend.py
    carries an org filter. `require_isolated()` below refuses to serve a tenant
    in that configuration rather than leak across orgs.
    """

    def __init__(self) -> None:
        settings = get_settings()
        # The fields are copilot_neo4j_*; the previous getattr() calls named
        # settings.neo4j_* — attributes Settings has never had — so every one
        # fell through to its default. COPILOT_NEO4J_URI/_USERNAME/_PASSWORD/
        # _DATABASE were dead, and worse, the uri default was a literal
        # "bolt://localhost:7687": with no Neo4j configured at all, connect()
        # took the Cypher branch and recorded "Cannot reach the Neo4j server"
        # instead of using the database-backed graph, which is what an empty
        # uri selects. Read the real names, with no getattr default to hide a
        # future rename.
        self.graph = Neo4jKnowledgeGraph(
            uri=settings.copilot_neo4j_uri,
            username=settings.copilot_neo4j_username,
            password=settings.copilot_neo4j_password,
            database=settings.copilot_neo4j_database,
        )
        self.engine = ContinuousSyncEngine(self.graph)
        # connect() decides which backend this client uses, so it must run once
        # per instance even if several requests for a new org arrive together.
        self._lock = asyncio.Lock()
        self._ready = False
        self._live_neo4j = False

    async def ready(self) -> _OrgGraph:
        if not self._ready:
            async with self._lock:
                if not self._ready:
                    self._live_neo4j = await self.graph.connect()
                    self._ready = True
        return self

    def require_isolated(self) -> None:
        org = tenant_id()
        if self._live_neo4j and org not in ("", "-"):
            raise HTTPException(
                503,
                "The knowledge graph is backed by a shared Neo4j database with no "
                "per-org partitioning, so it cannot serve a multi-tenant deployment. "
                "Unset the neo4j settings to use the org-scoped database graph.",
            )


_GRAPHS = scoped(_OrgGraph)


async def _org_graph() -> _OrgGraph:
    holder = await _GRAPHS.get().ready()
    holder.require_isolated()
    return holder


async def get_graph() -> Neo4jKnowledgeGraph:
    """The current org's knowledge graph."""
    return (await _org_graph()).graph


async def get_sync_engine() -> ContinuousSyncEngine:
    """The current org's sync engine."""
    return (await _org_graph()).engine


def _check_sync_url(field: str, url: str) -> None:
    """Reject a sync-source URL the operator has not allow-listed.

    These URLs are fetched server-side from inside the deployment network, so
    accepting an arbitrary one lets any authenticated user — a viewer included —
    reach internal services and the cloud metadata endpoint. Blocking private
    ranges is not an option here: a Prometheus at 10.x or `localhost:9090` is
    the *normal* configuration. So the allow-list is the control, and it is
    empty until an operator names their own hosts.
    """
    if not url:
        return

    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise HTTPException(400, f"{field} must be an http(s) URL")

    allowed = get_settings().sync_allowed_hosts
    host = parsed.hostname.lower()
    candidates = {host, f"{host}:{parsed.port}" if parsed.port else host}
    if not allowed or not (candidates & allowed):
        raise HTTPException(
            403,
            f"{field} host '{host}' is not allowed. Set COPILOT_SYNC_ALLOWED_HOSTS "
            "to the observability hosts this deployment may fetch from.",
        )


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
    # The lifecycle timestamps. Absent means "has not happened yet", which is
    # what time-to-detect and time-to-resolve are computed against; there was
    # previously no way to set any of them, and the reader minted a value for
    # each on the way out, so every incident looked instantly detected and
    # instantly resolved. `started_at` defaults to now.
    started_at: datetime | None = None
    detected_at: datetime | None = None
    acknowledged_at: datetime | None = None
    resolved_at: datetime | None = None


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
        # 404, not a 200 carrying {"error": ...}: a caller doing
        # `if (res.ok) render(await res.json())` rendered an object with no
        # id and no name, so a missing service was indistinguishable from a
        # present one at the HTTP layer.
        raise HTTPException(404, {"error": "service_not_found", "service_id": service_id})
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


# --------------------------------------------------------------------------- #
# Topology + live metrics — the two calls the Service Map page needs.
# --------------------------------------------------------------------------- #

# Built here, not accepted from the caller: these are executed server-side against
# an operator-configured Prometheus, and taking PromQL from a browser would turn
# this route into an arbitrary-query proxy for anyone holding a viewer key.
_SERVICE_METRIC_QUERIES: dict[str, str] = {
    "requests_per_sec": "sum(rate(http_requests_total[5m])) by (service)",
    "error_rate": (
        'sum(rate(http_requests_total{status=~"5.."}[5m])) by (service) '
        "/ sum(rate(http_requests_total[5m])) by (service) * 100"
    ),
    "latency_p99": (
        "histogram_quantile(0.99, sum(rate(http_request_duration_seconds_bucket[5m])) "
        "by (le, service)) * 1000"
    ),
}


@router.get("/topology")
async def get_topology(
    limit: int = 200,
    graph: Neo4jKnowledgeGraph = Depends(get_graph),
) -> dict[str, Any]:
    """Services plus their recorded dependency edges, in one call.

    The Service Map page used to ask for `/api/kubernetes/services` — a route
    that has never existed — and then drew a topology it made up: a star from
    whichever node was named "gateway" to every other service, plus an edge from
    every service to every database. These edges are the graph's own
    DEPENDS_ON/CALLS relationships and nothing else, so an empty graph renders an
    empty map rather than an invented one.
    """
    services = await graph.list_services(limit=limit)
    known = {s.id for s in services}
    edges: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for service in services:
        # depth=1 keeps this to direct edges; transitive hops would draw
        # dependencies that do not exist as links.
        found = await graph.find_dependencies(service.id, depth=1, direction="downstream")
        for dep in found.get("dependencies", []):
            target = dep.get("service")
            target_id = getattr(target, "id", "")
            if not target_id or target_id == service.id or target_id not in known:
                continue
            pair = (service.id, target_id)
            if pair in seen:
                continue
            seen.add(pair)
            edges.append({"source": service.id, "target": target_id})
    return {
        "nodes": [s.to_cypher_properties() for s in services],
        "edges": edges,
        "count": len(services),
    }


@router.get("/service-metrics")
async def get_service_metrics() -> dict[str, Any]:
    """Live per-service request rate, error rate and p99 latency from Prometheus.

    The Service Map used to run these same three PromQL queries from the
    *browser* against `http://localhost:9090`. A deployed browser cannot reach a
    cluster-internal Prometheus and CORS blocks it besides, so every query
    returned `[]` and the page fell back to inventing an error rate from incident
    severities. Proxying server-side is the only way the numbers can be real —
    and when no Prometheus is configured we say so instead of reporting zeros
    that look like a healthy fleet.
    """
    base = get_settings().prometheus_url.strip().rstrip("/")
    if not base:
        raise HTTPException(
            503,
            "Live service metrics require a Prometheus endpoint. Set PROMETHEUS_URL "
            "(e.g. http://prometheus:9090) on the API to enable them.",
        )

    import httpx

    metrics: dict[str, dict[str, float]] = {}
    async with httpx.AsyncClient(timeout=get_settings().http_timeout_default) as client:
        for field, promql in _SERVICE_METRIC_QUERIES.items():
            try:
                response = await client.get(
                    f"{base}/api/v1/query", params={"query": promql}
                )
                response.raise_for_status()
                payload = response.json()
            except httpx.HTTPError as exc:
                raise HTTPException(
                    502, f"Prometheus at {base} did not answer the {field} query: {exc}"
                ) from exc
            except ValueError as exc:
                raise HTTPException(
                    502, f"Prometheus at {base} returned a non-JSON {field} response"
                ) from exc
            if payload.get("status") != "success":
                raise HTTPException(
                    502,
                    f"Prometheus rejected the {field} query: "
                    f"{payload.get('error') or 'unknown error'}",
                )
            for row in payload.get("data", {}).get("result", []):
                labels = row.get("metric") or {}
                name = labels.get("service") or labels.get("job")
                if not name:
                    continue
                try:
                    value = float(row["value"][1])
                except (KeyError, IndexError, TypeError, ValueError):
                    continue
                # Prometheus emits NaN for a 0/0 error ratio; a missing key is the
                # honest representation, a 0.0 would claim "no errors observed".
                if value != value:
                    continue
                metrics.setdefault(str(name), {})[field] = value
    return {"source": "prometheus", "url": base, "services": metrics}


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
        detected_at=input.detected_at,
        acknowledged_at=input.acknowledged_at,
        resolved_at=input.resolved_at,
    )
    if input.started_at is not None:
        incident.started_at = input.started_at
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
        raise HTTPException(404, {"error": "incident_not_found", "incident_id": incident_id})
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
        # Request-supplied URLs go through the SSRF allow-list; absent ones fall
        # back to the operator's own settings (PROMETHEUS_URL / ALERTMANAGER_URL),
        # which need no allow-listing. With neither, refuse: a localhost default
        # here only manufactured connection errors that looked like an outage.
        settings = get_settings()
        prometheus_url = cfg.get("prometheus_url", "")
        alertmanager_url = cfg.get("alertmanager_url", "")
        _check_sync_url("prometheus_url", prometheus_url)
        _check_sync_url("alertmanager_url", alertmanager_url)
        prometheus_url = prometheus_url or settings.prometheus_url
        alertmanager_url = alertmanager_url or settings.alertmanager_url
        if not (prometheus_url or alertmanager_url):
            raise HTTPException(
                400,
                "prometheus sync needs prometheus_url/alertmanager_url in the "
                "request config, or PROMETHEUS_URL / ALERTMANAGER_URL set on the API",
            )
        engine.add_prometheus(
            prometheus_url=prometheus_url,
            alertmanager_url=alertmanager_url,
        )
    elif source == "kubernetes":
        engine.add_kubernetes(
            kubeconfig=cfg.get("kubeconfig"),
            context=cfg.get("context"),
            namespace=cfg.get("namespace"),
        )
    elif source == "jaeger":
        jaeger_url = cfg.get("jaeger_url", "")
        _check_sync_url("jaeger_url", jaeger_url)
        jaeger_url = jaeger_url or get_settings().traces_api_url
        if not jaeger_url:
            raise HTTPException(
                400,
                "jaeger sync needs jaeger_url in the request config, or "
                "TRACES_API_URL set on the API",
            )
        engine.add_jaeger(jaeger_url=jaeger_url)
    else:
        # A typo'd source used to answer 200, so the caller believed sync was
        # configured and then wondered why nothing ever synced.
        raise HTTPException(
            400,
            {
                "error": "unknown_source",
                "source": source,
                "valid_sources": [
                    "github", "cloudtrail", "prometheus", "kubernetes", "jaeger",
                ],
            },
        )

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
