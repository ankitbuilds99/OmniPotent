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
from typing import Any, AsyncIterator
from contextlib import asynccontextmanager

log = logging.getLogger("omnipotent.knowledge_graph.neo4j")


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


class Neo4jKnowledgeGraph:
    """Neo4j-backed knowledge graph for infrastructure.

    Features:
    - Real Neo4j connection with async driver
    - CRUD operations for all node types
    - Complex graph queries for RCA
    - Real-time change tracking
    - Efficient path finding for blast radius
    """

    def __init__(
        self,
        uri: str = "bolt://localhost:7687",
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
        self._subscriptions: dict[str, list[asyncio.Queue[dict[str, Any]]]] = {}

    async def connect(self) -> bool:
        """Connect to Neo4j database."""
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
            log.warning("neo4j package not installed, using in-memory fallback")
            self._connected = False
            self._in_memory_nodes: dict[str, dict[str, Any]] = {}
            self._in_memory_rels: list[dict[str, Any]] = []
            return False

        except Exception as e:
            log.warning("Failed to connect to Neo4j: %s", e)
            self._connected = False
            self._in_memory_nodes = {}
            self._in_memory_rels = []
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
                self._in_memory_nodes[f"Service:{service.id}"] = {
                    "type": "Service",
                    **service.to_cypher_properties(),
                }

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
                key = f"Service:{service_id}"
                if key in self._in_memory_nodes:
                    return self._dict_to_service(self._in_memory_nodes[key])
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
                for key, node in self._in_memory_nodes.items():
                    if not key.startswith("Service:"):
                        continue
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
                self._in_memory_nodes[f"Deployment:{deployment.id}"] = {
                    "type": "Deployment",
                    **deployment.to_cypher_properties(),
                }
                self._in_memory_rels.append({
                    "source": f"Deployment:{deployment.id}",
                    "target": f"Service:{deployment.service_id}",
                    "type": "DEPLOYED_TO",
                })

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
                for key, node in self._in_memory_nodes.items():
                    if not key.startswith("Deployment:"):
                        continue
                    deployed_at = datetime.fromisoformat(node["deployed_at"])
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
                self._in_memory_nodes[f"Incident:{incident.id}"] = {
                    "type": "Incident",
                    **incident.to_cypher_properties(),
                }
                for service_id in incident.affected_services:
                    self._in_memory_rels.append({
                        "source": f"Incident:{incident.id}",
                        "target": f"Service:{service_id}",
                        "type": "AFFECTED",
                    })

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
                key = f"Incident:{incident_id}"
                if key in self._in_memory_nodes:
                    return self._dict_to_incident(self._in_memory_nodes[key])
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
                self._in_memory_nodes[f"Change:{change.id}"] = {
                    "type": "Change",
                    **change.to_cypher_properties(),
                }

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
                self._in_memory_nodes[f"Alert:{alert.id}"] = {
                    "type": "Alert",
                    **alert.to_cypher_properties(),
                }

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
                self._in_memory_rels.append({
                    "source": rel.source_id,
                    "target": rel.target_id,
                    "type": rel.rel_type.value,
                    "properties": rel.properties,
                })

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
                return {
                    "service_id": service_id,
                    "direction": direction,
                    "max_depth": depth,
                    "dependencies": [],
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
                return {
                    "source_service": service_id,
                    "affected_count": 0,
                    "total_impact_score": 0.0,
                    "affected_services": [],
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
                for key, node in self._in_memory_nodes.items():
                    if not key.startswith("Change:"):
                        continue
                    ts = datetime.fromisoformat(node["timestamp"])
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
                return []

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
            resolved_at=self._parse_datetime(props.get("resolved_at")),
            detected_at=self._parse_datetime(props.get("detected_at")),
            acknowledged_at=self._parse_datetime(props.get("acknowledged_at")),
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
            resolved_at=self._parse_datetime(d.get("resolved_at")),
            detected_at=self._parse_datetime(d.get("detected_at")),
            acknowledged_at=self._parse_datetime(d.get("acknowledged_at")),
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
            resolved_at=self._parse_datetime(props.get("resolved_at")),
            fingerprint=props.get("fingerprint", ""),
            value=props.get("value", 0.0),
            threshold=props.get("threshold", 0.0),
        )

    def _parse_datetime(self, value: Any) -> datetime:
        """Parse datetime from various formats."""
        if value is None:
            return datetime.now(timezone.utc)
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return datetime.now(timezone.utc)
        return datetime.now(timezone.utc)

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
                mem_node_counts: dict[str, int] = {}
                for key in self._in_memory_nodes:
                    node_type = key.split(":")[0]
                    mem_node_counts[node_type] = mem_node_counts.get(node_type, 0) + 1

                return {
                    "connected": False,
                    "mode": "in-memory",
                    "node_counts": mem_node_counts,
                    "relationship_count": len(self._in_memory_rels),
                }
