"""Neo4j Knowledge Graph Backend.

Real Neo4j integration for infrastructure knowledge graph.
Stores services, deployments, incidents, changes, and their relationships.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, AsyncIterator, Iterable, Mapping, Sequence
from contextlib import asynccontextmanager

from app.knowledge_graph import graph_store

log = logging.getLogger("omnipotent.knowledge_graph.neo4j")

# The Cypher blast-radius query walks `*1..5`; the relational traversal below
# has to agree with it or the same graph would answer differently depending on
# which backend happened to be configured.
BLAST_RADIUS_MAX_DEPTH = 5


class NodeType(str, Enum):
    SERVICE = "Service"
    DEPLOYMENT = "Deployment"
    INCIDENT = "Incident"
    CHANGE = "Change"
    ALERT = "Alert"
    TEAM = "Team"
    REPOSITORY = "Repository"
    INFRASTRUCTURE = "Infrastructure"
    ENDPOINT = "Endpoint"
    DATABASE = "Database"
    QUEUE = "Queue"
    TRACE = "Trace"


class RelationType(str, Enum):
    DEPENDS_ON = "DEPENDS_ON"
    CAUSED_BY = "CAUSED_BY"
    TRIGGERED = "TRIGGERED"
    DEPLOYED_TO = "DEPLOYED_TO"
    OWNS = "OWNS"
    RELATED_TO = "RELATED_TO"
    AFFECTED = "AFFECTED"
    RESOLVED_BY = "RESOLVED_BY"
    CALLS = "CALLS"
    TRIGGERED_ON = "TRIGGERED_ON"
    PRODUCES = "PRODUCES"
    CONSUMES = "CONSUMES"
    RUNS_ON = "RUNS_ON"


@dataclass
class ServiceNode:
    """Service node in the knowledge graph."""
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
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    labels: dict[str, str] = field(default_factory=dict)
    metrics: dict[str, float] = field(default_factory=dict)

    def to_cypher_properties(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "namespace": self.namespace,
            "tier": self.tier,
            "team": self.team,
            "repository": self.repository,
            "language": self.language,
            "framework": self.framework,
            "health_status": self.health_status,
            "criticality": self.criticality,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "labels": str(self.labels),
            "error_rate": self.metrics.get("error_rate", 0.0),
            "latency_p99": self.metrics.get("latency_p99", 0.0),
            "requests_per_sec": self.metrics.get("requests_per_sec", 0.0),
        }


@dataclass
class DeploymentNode:
    """Deployment node in the knowledge graph."""
    id: str
    service_id: str
    version: str
    commit_sha: str = ""
    deployed_by: str = ""
    deployed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    environment: str = "production"
    status: str = "success"
    duration_seconds: int = 0
    rollback_of: str = ""
    changes: list[str] = field(default_factory=list)
    config_changes: dict[str, Any] = field(default_factory=dict)

    def to_cypher_properties(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "service_id": self.service_id,
            "version": self.version,
            "commit_sha": self.commit_sha,
            "deployed_by": self.deployed_by,
            "deployed_at": self.deployed_at.isoformat(),
            "environment": self.environment,
            "status": self.status,
            "duration_seconds": self.duration_seconds,
            "rollback_of": self.rollback_of,
            "changes": self.changes,
        }


@dataclass
class IncidentNode:
    """Incident node in the knowledge graph."""
    id: str
    title: str
    severity: str = "P3"
    status: str = "open"
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    resolved_at: datetime | None = None
    detected_at: datetime | None = None
    acknowledged_at: datetime | None = None
    affected_services: list[str] = field(default_factory=list)
    root_cause: str = ""
    impact: str = ""
    timeline: list[dict[str, Any]] = field(default_factory=list)
    responders: list[str] = field(default_factory=list)
    postmortem_url: str = ""

    def to_cypher_properties(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "severity": self.severity,
            "status": self.status,
            "started_at": self.started_at.isoformat(),
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
            "detected_at": self.detected_at.isoformat() if self.detected_at else None,
            "acknowledged_at": self.acknowledged_at.isoformat() if self.acknowledged_at else None,
            "affected_services": self.affected_services,
            "root_cause": self.root_cause,
            "impact": self.impact,
            "responders": self.responders,
            "postmortem_url": self.postmortem_url,
        }

    @property
    def ttd_seconds(self) -> int | None:
        """Time to detect."""
        if self.detected_at:
            return int((self.detected_at - self.started_at).total_seconds())
        return None

    @property
    def ttr_seconds(self) -> int | None:
        """Time to resolve."""
        if self.resolved_at:
            return int((self.resolved_at - self.started_at).total_seconds())
        return None


@dataclass
class ChangeNode:
    """Change node in the knowledge graph."""
    id: str
    change_type: str  # code, config, infrastructure, database
    title: str
    description: str = ""
    author: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    source: str = ""  # github, terraform, cloudtrail
    affected_resources: list[str] = field(default_factory=list)
    risk_score: float = 0.0
    approved_by: str = ""
    ticket_id: str = ""
    diff_url: str = ""

    def to_cypher_properties(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "change_type": self.change_type,
            "title": self.title,
            "description": self.description,
            "author": self.author,
            "timestamp": self.timestamp.isoformat(),
            "source": self.source,
            "affected_resources": self.affected_resources,
            "risk_score": self.risk_score,
            "approved_by": self.approved_by,
            "ticket_id": self.ticket_id,
            "diff_url": self.diff_url,
        }


@dataclass
class AlertNode:
    """Alert node in the knowledge graph."""
    id: str
    name: str
    severity: str = "warning"
    source: str = ""
    service: str = ""
    fired_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    resolved_at: datetime | None = None
    fingerprint: str = ""
    labels: dict[str, str] = field(default_factory=dict)
    annotations: dict[str, str] = field(default_factory=dict)
    value: float = 0.0
    threshold: float = 0.0

    def to_cypher_properties(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "severity": self.severity,
            "source": self.source,
            "service": self.service,
            "fired_at": self.fired_at.isoformat(),
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
            "fingerprint": self.fingerprint,
            "labels": str(self.labels),
            "value": self.value,
            "threshold": self.threshold,
        }


@dataclass
class Relationship:
    """Relationship between nodes."""
    source_id: str
    target_id: str
    rel_type: RelationType
    properties: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_cypher_properties(self) -> dict[str, Any]:
        return {
            "created_at": self.created_at.isoformat(),
            **self.properties,
        }


# The relationships that mean "this service needs that service in order to
# work". The Cypher traversals match `[:DEPENDS_ON|CALLS]`; the relational ones
# below filter on the same pair, so a RELATED_TO or an OWNS edge does not
# inflate a blast radius.
DEPENDENCY_RELATIONS = frozenset({RelationType.DEPENDS_ON.value, RelationType.CALLS.value})


def _node_key(label: str, node_id: str) -> str:
    """`"Label:id"`, the key `graph_store.snapshot()` returns nodes under."""
    return graph_store.node_key(label, node_id)


def _nodes_of(
    nodes: Mapping[str, dict[str, Any]], label: str
) -> list[dict[str, Any]]:
    """Every node carrying `label`, in the order the store returned them."""
    prefix = f"{label}:"
    return [node for key, node in nodes.items() if key.startswith(prefix)]


def _sources_pointing_at(
    edges: Iterable[Mapping[str, Any]], rel_type: str, target_id: str
) -> set[str]:
    """Ids of the nodes joined to `target_id` by `rel_type`, following the arrow.

    The Cypher equivalent is `MATCH (x)-[:REL]->(target) RETURN x.id`.
    """
    return {
        str(edge["source"])
        for edge in edges
        if edge.get("type") == rel_type and edge.get("target") == target_id
    }


def _walk_services(
    nodes: Mapping[str, dict[str, Any]],
    edges: Sequence[Mapping[str, Any]],
    service_id: str,
    *,
    direction: str = "both",
    max_depth: int = 3,
) -> dict[str, int]:
    """Breadth-first walk of the dependency edges out of one service.

    Returns `{service_id: hop distance}` for every *Service* reachable within
    `max_depth` hops, which is what both `find_dependencies` and
    `find_blast_radius` need and what the Cypher branch computes with
    `min(length(path))`. Breadth-first order means the first time a node is
    reached is by its shortest path, so no relaxation pass is needed.

    `direction` uses this module's existing vocabulary, taken from the Cypher:

    * `upstream` follows `<-[:DEPENDS_ON|CALLS]-`, i.e. the services that depend
      on this one. That is the blast radius.
    * `downstream` follows `-[:DEPENDS_ON|CALLS]->`, i.e. the services this one
      depends on.
    * anything else walks both, as the undirected Cypher pattern does.

    Intermediate hops may be any node type — a service can reach another through
    a Database or Queue node, exactly as the unlabelled variable-length Cypher
    pattern allows — but only Service nodes are reported, and the start service
    is never reported as its own dependency even if the graph has a cycle.
    """
    if _node_key(NodeType.SERVICE.value, service_id) not in nodes:
        return {}

    depth = max(0, int(max_depth))
    if depth == 0:
        return {}

    outgoing: dict[str, set[str]] = {}
    incoming: dict[str, set[str]] = {}
    for edge in edges:
        if edge.get("type") not in DEPENDENCY_RELATIONS:
            continue
        source, target = str(edge["source"]), str(edge["target"])
        outgoing.setdefault(source, set()).add(target)
        incoming.setdefault(target, set()).add(source)

    adjacency: tuple[dict[str, set[str]], ...]
    if direction == "upstream":
        adjacency = (incoming,)
    elif direction == "downstream":
        adjacency = (outgoing,)
    else:
        adjacency = (outgoing, incoming)

    service_ids = {
        str(node.get("id", ""))
        for node in _nodes_of(nodes, NodeType.SERVICE.value)
    }

    distances: dict[str, int] = {}
    seen = {service_id}
    frontier = [service_id]
    for hop in range(1, depth + 1):
        next_frontier: list[str] = []
        for node_id in frontier:
            for adjacent in adjacency:
                for neighbour in sorted(adjacent.get(node_id, ())):
                    if neighbour in seen:
                        continue
                    seen.add(neighbour)
                    next_frontier.append(neighbour)
                    if neighbour in service_ids:
                        distances[neighbour] = hop
        if not next_frontier:
            break
        frontier = next_frontier

    return distances


class Neo4jKnowledgeGraph:
    """Knowledge graph for infrastructure, over Neo4j or the relational store.

    Two backends, chosen by whether a Neo4j URI is configured:

    * `uri` set — Cypher against that server, and if it cannot be reached
      `connect()` records `connect_error` and every operation refuses. It used
      to swallow the failure and write to a process-local dict instead, which is
      the worst possible outcome: an operator who configured a production Neo4j
      got HTTP 200s, an empty blast radius and no indication that any of it was
      fiction.
    * `uri` empty (the default) — the `graph_nodes` / `graph_edges` tables via
      `app.knowledge_graph.graph_store`, scoped to the requesting org. Durable
      across restarts and shared between workers; the traversals below are
      implemented directly instead of delegating to Cypher.
    """

    def __init__(
        self,
        uri: str = "",
        username: str = "neo4j",
        password: str = "",
        database: str = "neo4j",
    ):
        self.uri = uri
        self.username = username
        self.password = password
        self.database = database
        self._driver: Any = None
        self._connected = False
        # Non-empty only when a Neo4j server was configured and is unusable.
        # The router turns it into a 503 rather than serving a half-graph.
        self.connect_error = ""
        self._subscriptions: dict[str, list[asyncio.Queue[dict[str, Any]]]] = {}

    @property
    def connected(self) -> bool:
        """True when Cypher is being used; False for the relational store."""
        return self._connected

    async def connect(self) -> bool:
        """Connect to Neo4j, if one is configured. Returns True only for Cypher."""
        self.connect_error = ""

        if not self.uri:
            self._connected = False
            log.info("No COPILOT_NEO4J_URI set; knowledge graph uses the database")
            return False

        try:
            from neo4j import AsyncGraphDatabase

            self._driver = AsyncGraphDatabase.driver(
                self.uri,
                auth=(self.username, self.password),
            )
            await self._driver.verify_connectivity()
            self._connected = True
            log.info("Connected to Neo4j at %s", self.uri)

            await self._ensure_indexes()
            return True

        except ImportError:
            self._connected = False
            self.connect_error = (
                f"COPILOT_NEO4J_URI is set to {self.uri} but the 'neo4j' driver is not "
                "installed. Install the 'knowledge-graph' extra, or unset "
                "COPILOT_NEO4J_URI to use the database-backed graph."
            )
            log.error("%s", self.connect_error)
            return False

        except Exception as e:
            self._connected = False
            self.connect_error = (
                f"Cannot reach the Neo4j server at {self.uri}: {e}. Fix the server or "
                "unset COPILOT_NEO4J_URI to use the database-backed graph."
            )
            log.error("%s", self.connect_error)
            return False

    async def close(self) -> None:
        """Close the connection."""
        if self._driver:
            await self._driver.close()
            self._connected = False

    async def _ensure_indexes(self) -> None:
        """Create indexes for efficient queries."""
        indexes = [
            "CREATE INDEX IF NOT EXISTS FOR (s:Service) ON (s.id)",
            "CREATE INDEX IF NOT EXISTS FOR (s:Service) ON (s.name)",
            "CREATE INDEX IF NOT EXISTS FOR (d:Deployment) ON (d.id)",
            "CREATE INDEX IF NOT EXISTS FOR (d:Deployment) ON (d.service_id)",
            "CREATE INDEX IF NOT EXISTS FOR (i:Incident) ON (i.id)",
            "CREATE INDEX IF NOT EXISTS FOR (i:Incident) ON (i.severity)",
            "CREATE INDEX IF NOT EXISTS FOR (c:Change) ON (c.id)",
            "CREATE INDEX IF NOT EXISTS FOR (c:Change) ON (c.timestamp)",
            "CREATE INDEX IF NOT EXISTS FOR (a:Alert) ON (a.id)",
            "CREATE INDEX IF NOT EXISTS FOR (a:Alert) ON (a.fingerprint)",
        ]

        if self._connected and self._driver:
            async with self._driver.session(database=self.database) as session:
                for idx in indexes:
                    try:
                        await session.run(idx)
                    except Exception as e:
                        log.debug("Index creation: %s", e)

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[Any]:
        """Get a database session."""
        if self._connected and self._driver:
            async with self._driver.session(database=self.database) as session:
                yield session
        else:
            yield None

    async def create_service(self, service: ServiceNode) -> ServiceNode:
        """Create or update a service node."""
        async with self._session() as session:
            if session:
                query = """
                MERGE (s:Service {id: $id})
                SET s += $props
                RETURN s
                """
                await session.run(
                    query,
                    id=service.id,
                    props=service.to_cypher_properties(),
                )
            else:
                await graph_store.put_node(
                    NodeType.SERVICE.value, service.id, service.to_cypher_properties()
                )

        await self._notify_subscribers("Service", "create", service.id)
        return service

    async def get_service(self, service_id: str) -> ServiceNode | None:
        """Get a service by ID."""
        async with self._session() as session:
            if session:
                result = await session.run(
                    "MATCH (s:Service {id: $id}) RETURN s",
                    id=service_id,
                )
                record = await result.single()
                if record:
                    return self._node_to_service(record["s"])
            else:
                node = await graph_store.get_node(NodeType.SERVICE.value, service_id)
                if node is not None:
                    return self._dict_to_service(node)
        return None

    async def list_services(
        self,
        namespace: str | None = None,
        team: str | None = None,
        health_status: str | None = None,
        limit: int = 100,
    ) -> list[ServiceNode]:
        """List services with optional filters."""
        async with self._session() as session:
            if session:
                conditions = []
                params: dict[str, Any] = {"limit": limit}

                if namespace:
                    conditions.append("s.namespace = $namespace")
                    params["namespace"] = namespace
                if team:
                    conditions.append("s.team = $team")
                    params["team"] = team
                if health_status:
                    conditions.append("s.health_status = $health_status")
                    params["health_status"] = health_status

                where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
                query = f"MATCH (s:Service) {where} RETURN s LIMIT $limit"

                result = await session.run(query, **params)
                records = await result.data()
                return [self._node_to_service(r["s"]) for r in records]
            else:
                services = []
                for node in await graph_store.list_nodes(NodeType.SERVICE.value):
                    if namespace and node.get("namespace") != namespace:
                        continue
                    if team and node.get("team") != team:
                        continue
                    if health_status and node.get("health_status") != health_status:
                        continue
                    services.append(self._dict_to_service(node))
                    if len(services) >= limit:
                        break
                return services

    async def create_deployment(self, deployment: DeploymentNode) -> DeploymentNode:
        """Create a deployment node and link to service."""
        async with self._session() as session:
            if session:
                query = """
                MATCH (s:Service {id: $service_id})
                MERGE (d:Deployment {id: $id})
                SET d += $props
                MERGE (d)-[:DEPLOYED_TO]->(s)
                RETURN d
                """
                await session.run(
                    query,
                    id=deployment.id,
                    service_id=deployment.service_id,
                    props=deployment.to_cypher_properties(),
                )
            else:
                await graph_store.put_node(
                    NodeType.DEPLOYMENT.value,
                    deployment.id,
                    deployment.to_cypher_properties(),
                )
                await graph_store.put_edge(
                    deployment.id,
                    deployment.service_id,
                    RelationType.DEPLOYED_TO.value,
                )

        await self._notify_subscribers("Deployment", "create", deployment.id)
        return deployment

    async def get_recent_deployments(
        self,
        service_id: str | None = None,
        hours: int = 24,
        limit: int = 50,
    ) -> list[DeploymentNode]:
        """Get recent deployments."""
        from datetime import timedelta

        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

        async with self._session() as session:
            if session:
                if service_id:
                    query = """
                    MATCH (d:Deployment)-[:DEPLOYED_TO]->(s:Service {id: $service_id})
                    WHERE d.deployed_at >= $cutoff
                    RETURN d ORDER BY d.deployed_at DESC LIMIT $limit
                    """
                    params = {
                        "service_id": service_id,
                        "cutoff": cutoff.isoformat(),
                        "limit": limit,
                    }
                else:
                    query = """
                    MATCH (d:Deployment)
                    WHERE d.deployed_at >= $cutoff
                    RETURN d ORDER BY d.deployed_at DESC LIMIT $limit
                    """
                    params = {"cutoff": cutoff.isoformat(), "limit": limit}

                result = await session.run(query, **params)
                records = await result.data()
                return [self._node_to_deployment(r["d"]) for r in records]
            else:
                deployments = []
                for node in await graph_store.list_nodes(NodeType.DEPLOYMENT.value):
                    deployed_at = self._parse_datetime(node.get("deployed_at"))
                    if deployed_at < cutoff:
                        continue
                    if service_id and node.get("service_id") != service_id:
                        continue
                    deployments.append(self._dict_to_deployment(node))

                deployments.sort(
                    key=lambda d: d.deployed_at,
                    reverse=True,
                )
                return deployments[:limit]

    async def create_incident(self, incident: IncidentNode) -> IncidentNode:
        """Create an incident node."""
        async with self._session() as session:
            if session:
                query = """
                MERGE (i:Incident {id: $id})
                SET i += $props
                RETURN i
                """
                await session.run(
                    query,
                    id=incident.id,
                    props=incident.to_cypher_properties(),
                )

                for service_id in incident.affected_services:
                    await session.run(
                        """
                        MATCH (i:Incident {id: $incident_id})
                        MATCH (s:Service {id: $service_id})
                        MERGE (i)-[:AFFECTED]->(s)
                        """,
                        incident_id=incident.id,
                        service_id=service_id,
                    )
            else:
                await graph_store.put_node(
                    NodeType.INCIDENT.value, incident.id, incident.to_cypher_properties()
                )
                for service_id in incident.affected_services:
                    await graph_store.put_edge(
                        incident.id, service_id, RelationType.AFFECTED.value
                    )

        await self._notify_subscribers("Incident", "create", incident.id)
        return incident

    async def get_incident(self, incident_id: str) -> IncidentNode | None:
        """Get an incident by ID."""
        async with self._session() as session:
            if session:
                result = await session.run(
                    "MATCH (i:Incident {id: $id}) RETURN i",
                    id=incident_id,
                )
                record = await result.single()
                if record:
                    return self._node_to_incident(record["i"])
            else:
                node = await graph_store.get_node(NodeType.INCIDENT.value, incident_id)
                if node is not None:
                    return self._dict_to_incident(node)
        return None

    async def create_change(self, change: ChangeNode) -> ChangeNode:
        """Create a change node."""
        async with self._session() as session:
            if session:
                query = """
                MERGE (c:Change {id: $id})
                SET c += $props
                RETURN c
                """
                await session.run(
                    query,
                    id=change.id,
                    props=change.to_cypher_properties(),
                )
            else:
                await graph_store.put_node(
                    NodeType.CHANGE.value, change.id, change.to_cypher_properties()
                )

        await self._notify_subscribers("Change", "create", change.id)
        return change

    async def create_alert(self, alert: AlertNode) -> AlertNode:
        """Create an alert node."""
        async with self._session() as session:
            if session:
                query = """
                MERGE (a:Alert {id: $id})
                SET a += $props
                WITH a
                OPTIONAL MATCH (s:Service {id: $service})
                FOREACH (_ IN CASE WHEN s IS NOT NULL THEN [1] ELSE [] END |
                    MERGE (a)-[:TRIGGERED_ON]->(s)
                )
                RETURN a
                """
                await session.run(
                    query,
                    id=alert.id,
                    service=alert.service,
                    props=alert.to_cypher_properties(),
                )
            else:
                await graph_store.put_node(
                    NodeType.ALERT.value, alert.id, alert.to_cypher_properties()
                )
                # The Cypher branch links the alert to its service when one
                # exists; the fallback used to skip this, so `get_service_timeline`
                # had nothing to join on.
                if alert.service and await graph_store.get_node(
                    NodeType.SERVICE.value, alert.service
                ):
                    await graph_store.put_edge(
                        alert.id, alert.service, RelationType.TRIGGERED_ON.value
                    )

        await self._notify_subscribers("Alert", "create", alert.id)
        return alert

    async def create_relationship(self, rel: Relationship) -> Relationship:
        """Create a relationship between nodes."""
        async with self._session() as session:
            if session:
                query = f"""
                MATCH (a {{id: $source_id}})
                MATCH (b {{id: $target_id}})
                MERGE (a)-[r:{rel.rel_type.value}]->(b)
                SET r += $props
                RETURN r
                """
                await session.run(
                    query,
                    source_id=rel.source_id,
                    target_id=rel.target_id,
                    props=rel.to_cypher_properties(),
                )
            else:
                await graph_store.put_edge(
                    rel.source_id,
                    rel.target_id,
                    rel.rel_type.value,
                    rel.properties,
                )

        return rel

    async def find_dependencies(
        self,
        service_id: str,
        depth: int = 3,
        direction: str = "both",
    ) -> dict[str, Any]:
        """Find service dependencies up to a certain depth."""
        async with self._session() as session:
            if session:
                if direction == "upstream":
                    pattern = "<-[:DEPENDS_ON|CALLS*1..%d]-" % depth
                elif direction == "downstream":
                    pattern = "-[:DEPENDS_ON|CALLS*1..%d]->" % depth
                else:
                    pattern = "-[:DEPENDS_ON|CALLS*1..%d]-" % depth

                query = f"""
                MATCH path = (s:Service {{id: $service_id}}){pattern}(dep:Service)
                RETURN dep, length(path) as distance
                ORDER BY distance
                """
                result = await session.run(query, service_id=service_id)
                records = await result.data()

                dependencies = []
                for r in records:
                    dependencies.append({
                        "service": self._node_to_service(r["dep"]),
                        "distance": r["distance"],
                    })

                return {
                    "service_id": service_id,
                    "direction": direction,
                    "max_depth": depth,
                    "dependencies": dependencies,
                }
            else:
                nodes, edges = await graph_store.snapshot()
                distances = _walk_services(
                    nodes, edges, service_id, direction=direction, max_depth=depth
                )
                dependencies = [
                    {
                        "service": self._dict_to_service(
                            nodes[_node_key(NodeType.SERVICE.value, dep_id)]
                        ),
                        "distance": distance,
                    }
                    for dep_id, distance in sorted(
                        distances.items(), key=lambda item: (item[1], item[0])
                    )
                ]
                return {
                    "service_id": service_id,
                    "direction": direction,
                    "max_depth": depth,
                    "dependencies": dependencies,
                }

    async def find_blast_radius(
        self,
        service_id: str,
        include_metrics: bool = True,
    ) -> dict[str, Any]:
        """Calculate blast radius for a service failure."""
        async with self._session() as session:
            if session:
                query = """
                MATCH path = (s:Service {id: $service_id})<-[:DEPENDS_ON|CALLS*1..5]-(affected:Service)
                WITH affected, min(length(path)) as distance
                RETURN affected, distance
                ORDER BY distance
                """
                result = await session.run(query, service_id=service_id)
                records = await result.data()

                affected_services = []
                total_impact_score = 0.0

                for r in records:
                    svc = self._node_to_service(r["affected"])
                    distance = r["distance"]
                    impact = svc.criticality / distance
                    total_impact_score += impact

                    affected_services.append({
                        "service": svc,
                        "distance": distance,
                        "impact_score": impact,
                    })

                return {
                    "source_service": service_id,
                    "affected_count": len(affected_services),
                    "total_impact_score": total_impact_score,
                    "affected_services": affected_services,
                }
            else:
                nodes, edges = await graph_store.snapshot()
                distances = _walk_services(
                    nodes,
                    edges,
                    service_id,
                    direction="upstream",
                    max_depth=BLAST_RADIUS_MAX_DEPTH,
                )

                affected_services = []
                total_impact_score = 0.0
                for affected_id, distance in sorted(
                    distances.items(), key=lambda item: (item[1], item[0])
                ):
                    svc = self._dict_to_service(
                        nodes[_node_key(NodeType.SERVICE.value, affected_id)]
                    )
                    impact = svc.criticality / distance
                    total_impact_score += impact
                    affected_services.append({
                        "service": svc,
                        "distance": distance,
                        "impact_score": impact,
                    })

                return {
                    "source_service": service_id,
                    "affected_count": len(affected_services),
                    "total_impact_score": total_impact_score,
                    "affected_services": affected_services,
                }

    async def find_changes_before_incident(
        self,
        incident_id: str,
        window_hours: int = 24,
    ) -> list[ChangeNode]:
        """Find changes that occurred before an incident."""
        incident = await self.get_incident(incident_id)
        if not incident:
            return []

        from datetime import timedelta

        window_start = incident.started_at - timedelta(hours=window_hours)

        async with self._session() as session:
            if session:
                query = """
                MATCH (i:Incident {id: $incident_id})
                MATCH (c:Change)
                WHERE c.timestamp >= $window_start AND c.timestamp <= i.started_at
                OPTIONAL MATCH (c)-[:AFFECTED]->(s:Service)<-[:AFFECTED]-(i)
                WITH c, count(s) as affected_overlap
                RETURN c, affected_overlap
                ORDER BY affected_overlap DESC, c.timestamp DESC
                """
                result = await session.run(
                    query,
                    incident_id=incident_id,
                    window_start=window_start.isoformat(),
                )
                records = await result.data()
                return [self._node_to_change(r["c"]) for r in records]
            else:
                changes = []
                for node in await graph_store.list_nodes(NodeType.CHANGE.value):
                    ts = self._parse_datetime(node.get("timestamp"))
                    if window_start <= ts <= incident.started_at:
                        changes.append(self._dict_to_change(node))
                changes.sort(key=lambda c: c.timestamp, reverse=True)
                return changes

    async def get_service_timeline(
        self,
        service_id: str,
        hours: int = 24,
    ) -> list[dict[str, Any]]:
        """Get timeline of events for a service."""
        from datetime import timedelta

        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

        async with self._session() as session:
            if session:
                query = """
                MATCH (s:Service {id: $service_id})
                OPTIONAL MATCH (d:Deployment)-[:DEPLOYED_TO]->(s)
                WHERE d.deployed_at >= $cutoff
                WITH s, collect(d) as deployments
                OPTIONAL MATCH (a:Alert)-[:TRIGGERED_ON]->(s)
                WHERE a.fired_at >= $cutoff
                WITH s, deployments, collect(a) as alerts
                OPTIONAL MATCH (i:Incident)-[:AFFECTED]->(s)
                WHERE i.started_at >= $cutoff
                RETURN s, deployments, alerts, collect(i) as incidents
                """
                result = await session.run(
                    query,
                    service_id=service_id,
                    cutoff=cutoff.isoformat(),
                )
                record = await result.single()

                if not record:
                    return []

                timeline = []

                for d in record["deployments"] or []:
                    if d:
                        dep = self._node_to_deployment(d)
                        timeline.append({
                            "type": "deployment",
                            "timestamp": dep.deployed_at.isoformat(),
                            "data": dep,
                        })

                for a in record["alerts"] or []:
                    if a:
                        alert = self._node_to_alert(a)
                        timeline.append({
                            "type": "alert",
                            "timestamp": alert.fired_at.isoformat(),
                            "data": alert,
                        })

                for i in record["incidents"] or []:
                    if i:
                        inc = self._node_to_incident(i)
                        timeline.append({
                            "type": "incident",
                            "timestamp": inc.started_at.isoformat(),
                            "data": inc,
                        })

                timeline.sort(key=lambda x: str(x["timestamp"]), reverse=True)
                return timeline
            else:
                nodes, edges = await graph_store.snapshot()
                if _node_key(NodeType.SERVICE.value, service_id) not in nodes:
                    return []

                timeline = []

                # A node belongs on this service's timeline if it is joined to
                # the service by the same relationship the Cypher branch walks,
                # or if it names the service in the property the writer set.
                # Both, because the sync engine writes the property and the API
                # writes the edge.
                deployed = _sources_pointing_at(
                    edges, RelationType.DEPLOYED_TO.value, service_id
                )
                for node in _nodes_of(nodes, NodeType.DEPLOYMENT.value):
                    if node.get("id") not in deployed and node.get("service_id") != service_id:
                        continue
                    dep = self._dict_to_deployment(node)
                    if dep.deployed_at < cutoff:
                        continue
                    timeline.append({
                        "type": "deployment",
                        "timestamp": dep.deployed_at.isoformat(),
                        "data": dep,
                    })

                fired = _sources_pointing_at(
                    edges, RelationType.TRIGGERED_ON.value, service_id
                )
                for node in _nodes_of(nodes, NodeType.ALERT.value):
                    if node.get("id") not in fired and node.get("service") != service_id:
                        continue
                    alert = self._node_to_alert(node)
                    if alert.fired_at < cutoff:
                        continue
                    timeline.append({
                        "type": "alert",
                        "timestamp": alert.fired_at.isoformat(),
                        "data": alert,
                    })

                impacted = _sources_pointing_at(
                    edges, RelationType.AFFECTED.value, service_id
                )
                for node in _nodes_of(nodes, NodeType.INCIDENT.value):
                    if node.get("id") not in impacted and service_id not in (
                        node.get("affected_services") or []
                    ):
                        continue
                    inc = self._dict_to_incident(node)
                    if inc.started_at < cutoff:
                        continue
                    timeline.append({
                        "type": "incident",
                        "timestamp": inc.started_at.isoformat(),
                        "data": inc,
                    })

                timeline.sort(key=lambda x: str(x["timestamp"]), reverse=True)
                return timeline

    async def subscribe(
        self,
        node_type: str,
    ) -> asyncio.Queue[dict[str, Any]]:
        """Subscribe to node changes."""
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        if node_type not in self._subscriptions:
            self._subscriptions[node_type] = []
        self._subscriptions[node_type].append(queue)
        return queue

    async def unsubscribe(
        self,
        node_type: str,
        queue: asyncio.Queue[dict[str, Any]],
    ) -> None:
        """Unsubscribe from node changes."""
        if node_type in self._subscriptions:
            try:
                self._subscriptions[node_type].remove(queue)
            except ValueError:
                pass

    async def _notify_subscribers(
        self,
        node_type: str,
        action: str,
        node_id: str,
    ) -> None:
        """Notify subscribers of changes."""
        if node_type not in self._subscriptions:
            return

        event = {
            "type": node_type,
            "action": action,
            "id": node_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        for queue in self._subscriptions[node_type]:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass

    def _node_to_service(self, node: Any) -> ServiceNode:
        """Convert Neo4j node to ServiceNode."""
        props = dict(node)
        return ServiceNode(
            id=props.get("id", ""),
            name=props.get("name", ""),
            namespace=props.get("namespace", "default"),
            tier=props.get("tier", 1),
            team=props.get("team", ""),
            repository=props.get("repository", ""),
            language=props.get("language", ""),
            framework=props.get("framework", ""),
            health_status=props.get("health_status", "unknown"),
            criticality=props.get("criticality", 0.5),
            created_at=self._parse_datetime(props.get("created_at")),
            updated_at=self._parse_datetime(props.get("updated_at")),
            metrics={
                "error_rate": props.get("error_rate", 0.0),
                "latency_p99": props.get("latency_p99", 0.0),
                "requests_per_sec": props.get("requests_per_sec", 0.0),
            },
        )

    def _dict_to_service(self, d: dict[str, Any]) -> ServiceNode:
        """Convert dict to ServiceNode."""
        return ServiceNode(
            id=d.get("id", ""),
            name=d.get("name", ""),
            namespace=d.get("namespace", "default"),
            tier=d.get("tier", 1),
            team=d.get("team", ""),
            repository=d.get("repository", ""),
            language=d.get("language", ""),
            framework=d.get("framework", ""),
            health_status=d.get("health_status", "unknown"),
            criticality=d.get("criticality", 0.5),
            created_at=self._parse_datetime(d.get("created_at")),
            updated_at=self._parse_datetime(d.get("updated_at")),
            metrics={
                "error_rate": d.get("error_rate", 0.0),
                "latency_p99": d.get("latency_p99", 0.0),
                "requests_per_sec": d.get("requests_per_sec", 0.0),
            },
        )

    def _node_to_deployment(self, node: Any) -> DeploymentNode:
        """Convert Neo4j node to DeploymentNode."""
        props = dict(node)
        return DeploymentNode(
            id=props.get("id", ""),
            service_id=props.get("service_id", ""),
            version=props.get("version", ""),
            commit_sha=props.get("commit_sha", ""),
            deployed_by=props.get("deployed_by", ""),
            deployed_at=self._parse_datetime(props.get("deployed_at")),
            environment=props.get("environment", "production"),
            status=props.get("status", "success"),
            duration_seconds=props.get("duration_seconds", 0),
            rollback_of=props.get("rollback_of", ""),
            changes=props.get("changes", []),
        )

    def _dict_to_deployment(self, d: dict[str, Any]) -> DeploymentNode:
        """Convert dict to DeploymentNode."""
        return DeploymentNode(
            id=d.get("id", ""),
            service_id=d.get("service_id", ""),
            version=d.get("version", ""),
            commit_sha=d.get("commit_sha", ""),
            deployed_by=d.get("deployed_by", ""),
            deployed_at=self._parse_datetime(d.get("deployed_at")),
            environment=d.get("environment", "production"),
            status=d.get("status", "success"),
            duration_seconds=d.get("duration_seconds", 0),
            rollback_of=d.get("rollback_of", ""),
            changes=d.get("changes", []),
        )

    def _node_to_incident(self, node: Any) -> IncidentNode:
        """Convert Neo4j node to IncidentNode."""
        props = dict(node)
        return IncidentNode(
            id=props.get("id", ""),
            title=props.get("title", ""),
            severity=props.get("severity", "P3"),
            status=props.get("status", "open"),
            started_at=self._parse_datetime(props.get("started_at")),
            resolved_at=self._parse_optional_datetime(props.get("resolved_at")),
            detected_at=self._parse_optional_datetime(props.get("detected_at")),
            acknowledged_at=self._parse_optional_datetime(props.get("acknowledged_at")),
            affected_services=props.get("affected_services", []),
            root_cause=props.get("root_cause", ""),
            impact=props.get("impact", ""),
            responders=props.get("responders", []),
            postmortem_url=props.get("postmortem_url", ""),
        )

    def _dict_to_incident(self, d: dict[str, Any]) -> IncidentNode:
        """Convert dict to IncidentNode."""
        return IncidentNode(
            id=d.get("id", ""),
            title=d.get("title", ""),
            severity=d.get("severity", "P3"),
            status=d.get("status", "open"),
            started_at=self._parse_datetime(d.get("started_at")),
            resolved_at=self._parse_optional_datetime(d.get("resolved_at")),
            detected_at=self._parse_optional_datetime(d.get("detected_at")),
            acknowledged_at=self._parse_optional_datetime(d.get("acknowledged_at")),
            affected_services=d.get("affected_services", []),
            root_cause=d.get("root_cause", ""),
            impact=d.get("impact", ""),
            responders=d.get("responders", []),
            postmortem_url=d.get("postmortem_url", ""),
        )

    def _node_to_change(self, node: Any) -> ChangeNode:
        """Convert Neo4j node to ChangeNode."""
        props = dict(node)
        return ChangeNode(
            id=props.get("id", ""),
            change_type=props.get("change_type", ""),
            title=props.get("title", ""),
            description=props.get("description", ""),
            author=props.get("author", ""),
            timestamp=self._parse_datetime(props.get("timestamp")),
            source=props.get("source", ""),
            affected_resources=props.get("affected_resources", []),
            risk_score=props.get("risk_score", 0.0),
            approved_by=props.get("approved_by", ""),
            ticket_id=props.get("ticket_id", ""),
            diff_url=props.get("diff_url", ""),
        )

    def _dict_to_change(self, d: dict[str, Any]) -> ChangeNode:
        """Convert dict to ChangeNode."""
        return ChangeNode(
            id=d.get("id", ""),
            change_type=d.get("change_type", ""),
            title=d.get("title", ""),
            description=d.get("description", ""),
            author=d.get("author", ""),
            timestamp=self._parse_datetime(d.get("timestamp")),
            source=d.get("source", ""),
            affected_resources=d.get("affected_resources", []),
            risk_score=d.get("risk_score", 0.0),
            approved_by=d.get("approved_by", ""),
            ticket_id=d.get("ticket_id", ""),
            diff_url=d.get("diff_url", ""),
        )

    def _node_to_alert(self, node: Any) -> AlertNode:
        """Convert Neo4j node to AlertNode."""
        props = dict(node)
        return AlertNode(
            id=props.get("id", ""),
            name=props.get("name", ""),
            severity=props.get("severity", "warning"),
            source=props.get("source", ""),
            service=props.get("service", ""),
            fired_at=self._parse_datetime(props.get("fired_at")),
            resolved_at=self._parse_optional_datetime(props.get("resolved_at")),
            fingerprint=props.get("fingerprint", ""),
            value=props.get("value", 0.0),
            threshold=props.get("threshold", 0.0),
        )

    def _parse_datetime(self, value: Any) -> datetime:
        """Parse a required timestamp, falling back to now.

        Only for fields that are never null -- started_at, fired_at, created_at
        and friends. For anything typed `datetime | None`, use
        `_parse_optional_datetime`: the now() fallback there turned "this has
        not happened yet" into "this happened just now".
        """
        parsed = self._parse_optional_datetime(value)
        return parsed if parsed is not None else datetime.now(timezone.utc)

    def _parse_optional_datetime(self, value: Any) -> datetime | None:
        """Parse a timestamp that is allowed to be absent.

        A missing value means the event has not happened, so it stays None. It
        used to come back as now(), which made every open incident read as
        resolved the moment it was fetched -- with a fresh resolved_at on each
        request, a time-to-detect of zero and a time-to-resolve of zero.
        """
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            if not value.strip():
                return None
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None
        return None

    async def get_stats(self) -> dict[str, Any]:
        """Get graph statistics."""
        async with self._session() as session:
            if session:
                result = await session.run("""
                    MATCH (n)
                    WITH labels(n)[0] as label, count(*) as count
                    RETURN collect({label: label, count: count}) as node_counts
                """)
                record = await result.single()
                node_counts = {}
                if record:
                    for item in record["node_counts"]:
                        node_counts[item["label"]] = item["count"]

                result = await session.run("MATCH ()-[r]->() RETURN count(r) as rel_count")
                rel_record = await result.single()
                rel_count = rel_record["rel_count"] if rel_record else 0

                return {
                    "connected": True,
                    "node_counts": node_counts,
                    "relationship_count": rel_count,
                }
            else:
                node_counts, rel_count = await graph_store.counts()

                return {
                    "connected": False,
                    "mode": "database",
                    "node_counts": node_counts,
                    "relationship_count": rel_count,
                }
