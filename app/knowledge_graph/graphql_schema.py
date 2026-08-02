"""GraphQL Schema for Knowledge Graph.

Strawberry-based GraphQL API with real-time subscriptions.
"""
# mypy: disable-error-code="call-arg"
# Strawberry's @strawberry.type decorator transforms classes in ways mypy can't follow.
# The decorator generates __init__ from the class attributes, but mypy sees the raw class.

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import AsyncGenerator

import strawberry
from strawberry.types import Info

from app.knowledge_graph.neo4j_backend import (
    Neo4jKnowledgeGraph,
    ServiceNode as ServiceNodeModel,
    DeploymentNode as DeploymentNodeModel,
    IncidentNode as IncidentNodeModel,
    ChangeNode as ChangeNodeModel,
    AlertNode as AlertNodeModel,
    RelationType,
)

log = logging.getLogger("devcopilot.knowledge_graph.graphql")


@strawberry.type
class Service:
    """Service in the infrastructure graph."""

    id: str
    name: str
    namespace: str
    tier: int
    team: str
    repository: str
    language: str
    framework: str
    health_status: str
    criticality: float
    created_at: datetime
    updated_at: datetime
    error_rate: float
    latency_p99: float
    requests_per_sec: float

    @classmethod
    def from_model(cls, model: ServiceNodeModel) -> Service:
        return cls(
            id=model.id,
            name=model.name,
            namespace=model.namespace,
            tier=model.tier,
            team=model.team,
            repository=model.repository,
            language=model.language,
            framework=model.framework,
            health_status=model.health_status,
            criticality=model.criticality,
            created_at=model.created_at,
            updated_at=model.updated_at,
            error_rate=model.metrics.get("error_rate", 0.0),
            latency_p99=model.metrics.get("latency_p99", 0.0),
            requests_per_sec=model.metrics.get("requests_per_sec", 0.0),
        )

    @strawberry.field
    async def dependencies(self, info: Info, depth: int = 3) -> list[ServiceDependency]:
        """Get services this service depends on."""
        graph: Neo4jKnowledgeGraph = info.context["graph"]
        result = await graph.find_dependencies(self.id, depth, direction="downstream")
        return [
            ServiceDependency(
                service=Service.from_model(d["service"]),
                distance=d["distance"],
            )
            for d in result.get("dependencies", [])
        ]

    @strawberry.field
    async def dependents(self, info: Info, depth: int = 3) -> list[ServiceDependency]:
        """Get services that depend on this service."""
        graph: Neo4jKnowledgeGraph = info.context["graph"]
        result = await graph.find_dependencies(self.id, depth, direction="upstream")
        return [
            ServiceDependency(
                service=Service.from_model(d["service"]),
                distance=d["distance"],
            )
            for d in result.get("dependencies", [])
        ]

    @strawberry.field
    async def recent_deployments(self, info: Info, hours: int = 24) -> list[Deployment]:
        """Get recent deployments for this service."""
        graph: Neo4jKnowledgeGraph = info.context["graph"]
        deployments = await graph.get_recent_deployments(self.id, hours)
        return [Deployment.from_model(d) for d in deployments]

    @strawberry.field
    async def timeline(self, info: Info, hours: int = 24) -> list[TimelineEvent]:
        """Get timeline of events for this service."""
        graph: Neo4jKnowledgeGraph = info.context["graph"]
        events = await graph.get_service_timeline(self.id, hours)
        return [
            TimelineEvent(
                type=e["type"],
                timestamp=datetime.fromisoformat(e["timestamp"]),
                data=str(e["data"]),
            )
            for e in events
        ]

    @strawberry.field
    async def blast_radius(self, info: Info) -> BlastRadius:
        """Calculate blast radius if this service fails."""
        graph: Neo4jKnowledgeGraph = info.context["graph"]
        result = await graph.find_blast_radius(self.id)
        return BlastRadius(
            source_service_id=result["source_service"],
            affected_count=result["affected_count"],
            total_impact_score=result["total_impact_score"],
            affected_services=[
                AffectedService(
                    service=Service.from_model(a["service"]),
                    distance=a["distance"],
                    impact_score=a["impact_score"],
                )
                for a in result.get("affected_services", [])
            ],
        )


@strawberry.type
class ServiceDependency:
    """A service dependency with distance."""

    service: Service
    distance: int


@strawberry.type
class AffectedService:
    """A service affected by blast radius."""

    service: Service
    distance: int
    impact_score: float


@strawberry.type
class BlastRadius:
    """Blast radius analysis result."""

    source_service_id: str
    affected_count: int
    total_impact_score: float
    affected_services: list[AffectedService]


@strawberry.type
class Deployment:
    """A deployment event."""

    id: str
    service_id: str
    version: str
    commit_sha: str
    deployed_by: str
    deployed_at: datetime
    environment: str
    status: str
    duration_seconds: int
    rollback_of: str
    changes: list[str]

    @classmethod
    def from_model(cls, model: DeploymentNodeModel) -> Deployment:
        return cls(
            id=model.id,
            service_id=model.service_id,
            version=model.version,
            commit_sha=model.commit_sha,
            deployed_by=model.deployed_by,
            deployed_at=model.deployed_at,
            environment=model.environment,
            status=model.status,
            duration_seconds=model.duration_seconds,
            rollback_of=model.rollback_of,
            changes=model.changes,
        )


@strawberry.type
class Incident:
    """An incident."""

    id: str
    title: str
    severity: str
    status: str
    started_at: datetime
    resolved_at: datetime | None
    detected_at: datetime | None
    acknowledged_at: datetime | None
    affected_services: list[str]
    root_cause: str
    impact: str
    responders: list[str]
    postmortem_url: str
    ttd_seconds: int | None
    ttr_seconds: int | None

    @classmethod
    def from_model(cls, model: IncidentNodeModel) -> Incident:
        return cls(
            id=model.id,
            title=model.title,
            severity=model.severity,
            status=model.status,
            started_at=model.started_at,
            resolved_at=model.resolved_at,
            detected_at=model.detected_at,
            acknowledged_at=model.acknowledged_at,
            affected_services=model.affected_services,
            root_cause=model.root_cause,
            impact=model.impact,
            responders=model.responders,
            postmortem_url=model.postmortem_url,
            ttd_seconds=model.ttd_seconds,
            ttr_seconds=model.ttr_seconds,
        )

    @strawberry.field
    async def related_changes(
        self,
        info: Info,
        window_hours: int = 24,
    ) -> list[Change]:
        """Get changes that occurred before this incident."""
        graph: Neo4jKnowledgeGraph = info.context["graph"]
        changes = await graph.find_changes_before_incident(self.id, window_hours)
        return [Change.from_model(c) for c in changes]


@strawberry.type
class Change:
    """A change event."""

    id: str
    change_type: str
    title: str
    description: str
    author: str
    timestamp: datetime
    source: str
    affected_resources: list[str]
    risk_score: float
    approved_by: str
    ticket_id: str
    diff_url: str

    @classmethod
    def from_model(cls, model: ChangeNodeModel) -> Change:
        return cls(
            id=model.id,
            change_type=model.change_type,
            title=model.title,
            description=model.description,
            author=model.author,
            timestamp=model.timestamp,
            source=model.source,
            affected_resources=model.affected_resources,
            risk_score=model.risk_score,
            approved_by=model.approved_by,
            ticket_id=model.ticket_id,
            diff_url=model.diff_url,
        )


@strawberry.type
class Alert:
    """An alert."""

    id: str
    name: str
    severity: str
    source: str
    service: str
    fired_at: datetime
    resolved_at: datetime | None
    fingerprint: str
    value: float
    threshold: float

    @classmethod
    def from_model(cls, model: AlertNodeModel) -> Alert:
        return cls(
            id=model.id,
            name=model.name,
            severity=model.severity,
            source=model.source,
            service=model.service,
            fired_at=model.fired_at,
            resolved_at=model.resolved_at,
            fingerprint=model.fingerprint,
            value=model.value,
            threshold=model.threshold,
        )


@strawberry.type
class TimelineEvent:
    """An event in a timeline."""

    type: str
    timestamp: datetime
    data: str


@strawberry.type
class GraphStats:
    """Knowledge graph statistics."""

    connected: bool
    mode: str
    node_counts: str
    relationship_count: int


@strawberry.type
class GraphChange:
    """A change notification from the graph."""

    node_type: str
    action: str
    node_id: str
    timestamp: datetime


@strawberry.input
class ServiceInput:
    """Input for creating/updating a service."""

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


@strawberry.input
class DeploymentInput:
    """Input for creating a deployment."""

    id: str
    service_id: str
    version: str
    commit_sha: str = ""
    deployed_by: str = ""
    environment: str = "production"
    status: str = "success"
    duration_seconds: int = 0
    rollback_of: str = ""
    changes: list[str] = strawberry.field(default_factory=list)


@strawberry.input
class IncidentInput:
    """Input for creating an incident."""

    id: str
    title: str
    severity: str = "P3"
    status: str = "open"
    affected_services: list[str] = strawberry.field(default_factory=list)
    root_cause: str = ""
    impact: str = ""


@strawberry.input
class RelationshipInput:
    """Input for creating a relationship."""

    source_id: str
    target_id: str
    rel_type: str


@strawberry.type
class Query:
    """Root query type."""

    @strawberry.field
    async def service(self, info: Info, id: str) -> Service | None:
        """Get a service by ID."""
        graph: Neo4jKnowledgeGraph = info.context["graph"]
        model = await graph.get_service(id)
        return Service.from_model(model) if model else None

    @strawberry.field
    async def services(
        self,
        info: Info,
        namespace: str | None = None,
        team: str | None = None,
        health_status: str | None = None,
        limit: int = 100,
    ) -> list[Service]:
        """List services with filters."""
        graph: Neo4jKnowledgeGraph = info.context["graph"]
        models = await graph.list_services(namespace, team, health_status, limit)
        return [Service.from_model(m) for m in models]

    @strawberry.field
    async def incident(self, info: Info, id: str) -> Incident | None:
        """Get an incident by ID."""
        graph: Neo4jKnowledgeGraph = info.context["graph"]
        model = await graph.get_incident(id)
        return Incident.from_model(model) if model else None

    @strawberry.field
    async def recent_deployments(
        self,
        info: Info,
        service_id: str | None = None,
        hours: int = 24,
        limit: int = 50,
    ) -> list[Deployment]:
        """Get recent deployments."""
        graph: Neo4jKnowledgeGraph = info.context["graph"]
        models = await graph.get_recent_deployments(service_id, hours, limit)
        return [Deployment.from_model(m) for m in models]

    @strawberry.field
    async def blast_radius(
        self,
        info: Info,
        service_id: str,
    ) -> BlastRadius:
        """Calculate blast radius for a service."""
        graph: Neo4jKnowledgeGraph = info.context["graph"]
        result = await graph.find_blast_radius(service_id)
        return BlastRadius(
            source_service_id=result["source_service"],
            affected_count=result["affected_count"],
            total_impact_score=result["total_impact_score"],
            affected_services=[
                AffectedService(
                    service=Service.from_model(a["service"]),
                    distance=a["distance"],
                    impact_score=a["impact_score"],
                )
                for a in result.get("affected_services", [])
            ],
        )

    @strawberry.field
    async def changes_before_incident(
        self,
        info: Info,
        incident_id: str,
        window_hours: int = 24,
    ) -> list[Change]:
        """Find changes before an incident."""
        graph: Neo4jKnowledgeGraph = info.context["graph"]
        models = await graph.find_changes_before_incident(incident_id, window_hours)
        return [Change.from_model(m) for m in models]

    @strawberry.field
    async def graph_stats(self, info: Info) -> GraphStats:
        """Get graph statistics."""
        graph: Neo4jKnowledgeGraph = info.context["graph"]
        stats = await graph.get_stats()
        return GraphStats(
            connected=stats.get("connected", False),
            mode=stats.get("mode", "neo4j"),
            node_counts=str(stats.get("node_counts", {})),
            relationship_count=stats.get("relationship_count", 0),
        )


@strawberry.type
class Mutation:
    """Root mutation type."""

    @strawberry.mutation
    async def create_service(self, info: Info, input: ServiceInput) -> Service:
        """Create or update a service."""
        graph: Neo4jKnowledgeGraph = info.context["graph"]
        model = ServiceNodeModel(
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
        created = await graph.create_service(model)
        return Service.from_model(created)

    @strawberry.mutation
    async def create_deployment(self, info: Info, input: DeploymentInput) -> Deployment:
        """Create a deployment."""
        graph: Neo4jKnowledgeGraph = info.context["graph"]
        model = DeploymentNodeModel(
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
        created = await graph.create_deployment(model)
        return Deployment.from_model(created)

    @strawberry.mutation
    async def create_incident(self, info: Info, input: IncidentInput) -> Incident:
        """Create an incident."""
        graph: Neo4jKnowledgeGraph = info.context["graph"]
        model = IncidentNodeModel(
            id=input.id,
            title=input.title,
            severity=input.severity,
            status=input.status,
            affected_services=input.affected_services,
            root_cause=input.root_cause,
            impact=input.impact,
        )
        created = await graph.create_incident(model)
        return Incident.from_model(created)

    @strawberry.mutation
    async def create_relationship(
        self,
        info: Info,
        input: RelationshipInput,
    ) -> bool:
        """Create a relationship between nodes."""
        graph: Neo4jKnowledgeGraph = info.context["graph"]
        from app.knowledge_graph.neo4j_backend import Relationship

        try:
            rel_type = RelationType(input.rel_type)
        except ValueError:
            rel_type = RelationType.RELATED_TO

        rel = Relationship(
            source_id=input.source_id,
            target_id=input.target_id,
            rel_type=rel_type,
        )
        await graph.create_relationship(rel)
        return True


@strawberry.type
class Subscription:
    """Root subscription type."""

    @strawberry.subscription
    async def service_changes(
        self,
        info: Info,
    ) -> AsyncGenerator[GraphChange, None]:
        """Subscribe to service changes."""
        graph: Neo4jKnowledgeGraph = info.context["graph"]
        queue = await graph.subscribe("Service")

        try:
            while True:
                event = await queue.get()
                yield GraphChange(
                    node_type=event["type"],
                    action=event["action"],
                    node_id=event["id"],
                    timestamp=datetime.fromisoformat(event["timestamp"]),
                )
        finally:
            await graph.unsubscribe("Service", queue)

    @strawberry.subscription
    async def deployment_changes(
        self,
        info: Info,
    ) -> AsyncGenerator[GraphChange, None]:
        """Subscribe to deployment changes."""
        graph: Neo4jKnowledgeGraph = info.context["graph"]
        queue = await graph.subscribe("Deployment")

        try:
            while True:
                event = await queue.get()
                yield GraphChange(
                    node_type=event["type"],
                    action=event["action"],
                    node_id=event["id"],
                    timestamp=datetime.fromisoformat(event["timestamp"]),
                )
        finally:
            await graph.unsubscribe("Deployment", queue)

    @strawberry.subscription
    async def incident_changes(
        self,
        info: Info,
    ) -> AsyncGenerator[GraphChange, None]:
        """Subscribe to incident changes."""
        graph: Neo4jKnowledgeGraph = info.context["graph"]
        queue = await graph.subscribe("Incident")

        try:
            while True:
                event = await queue.get()
                yield GraphChange(
                    node_type=event["type"],
                    action=event["action"],
                    node_id=event["id"],
                    timestamp=datetime.fromisoformat(event["timestamp"]),
                )
        finally:
            await graph.unsubscribe("Incident", queue)

    @strawberry.subscription
    async def all_changes(
        self,
        info: Info,
    ) -> AsyncGenerator[GraphChange, None]:
        """Subscribe to all graph changes."""
        graph: Neo4jKnowledgeGraph = info.context["graph"]

        queues = []
        for node_type in ["Service", "Deployment", "Incident", "Change", "Alert"]:
            queue = await graph.subscribe(node_type)
            queues.append((node_type, queue))

        try:
            while True:
                for node_type, queue in queues:
                    try:
                        event = queue.get_nowait()
                        yield GraphChange(
                            node_type=event["type"],
                            action=event["action"],
                            node_id=event["id"],
                            timestamp=datetime.fromisoformat(event["timestamp"]),
                        )
                    except asyncio.QueueEmpty:
                        pass
                await asyncio.sleep(0.1)
        finally:
            for node_type, queue in queues:
                await graph.unsubscribe(node_type, queue)


def create_schema() -> strawberry.Schema:
    """Create the GraphQL schema."""
    return strawberry.Schema(
        query=Query,
        mutation=Mutation,
        subscription=Subscription,
    )
