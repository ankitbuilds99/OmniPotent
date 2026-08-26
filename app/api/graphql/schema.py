"""GraphQL Schema - Main schema definition with queries, mutations, and subscriptions."""
# mypy: disable-error-code="call-arg"

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import AsyncGenerator, Optional
from uuid import uuid4

import strawberry
from strawberry.fastapi import GraphQLRouter
from strawberry.scalars import JSON

from app.api.graphql.types import (
    Investigation,
    InvestigationStatus,
    Incident,
    Service,
    Metric,
    MetricPoint,
    Alert,
    AlertState,
    Severity,
    Hypothesis,
    Evidence,
    User,
    Organization,
    UsageStats,
    EvalResult,
    AnomalyDetection,
    StartInvestigationInput,
    ApprovalDecisionInput,
    CreateIncidentInput,
    MetricQueryInput,
    InvestigationUpdate,
    AlertUpdate,
)

log = logging.getLogger("omnipotent.api.graphql")


# In-memory stores for demo (replace with real data sources)
_investigations: dict[str, dict] = {}
_incidents: dict[str, dict] = {}
_services: dict[str, dict] = {
    "api-server": {
        "id": "svc-001",
        "name": "api-server",
        "description": "Main API Gateway",
        "team": "platform",
        "tier": 1,
        "dependencies": ["auth-service", "database"],
        "dependents": ["web-frontend", "mobile-app"],
        "slo_target": 0.999,
        "current_slo": 0.9985,
    },
    "auth-service": {
        "id": "svc-002",
        "name": "auth-service",
        "description": "Authentication service",
        "team": "security",
        "tier": 1,
        "dependencies": ["database", "redis"],
        "dependents": ["api-server"],
        "slo_target": 0.9999,
        "current_slo": 0.9998,
    },
}


@strawberry.type
class Query:
    """GraphQL Query operations."""

    @strawberry.field
    def investigation(self, id: str) -> Optional[Investigation]:
        """Get an investigation by ID."""
        data = _investigations.get(id)
        if not data:
            return None
        return _to_investigation(data)

    @strawberry.field
    def investigations(
        self,
        status: Optional[InvestigationStatus] = None,
        service: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[Investigation]:
        """List investigations with optional filters."""
        results = list(_investigations.values())

        if status:
            results = [r for r in results if r.get("status") == status.value]

        if service:
            results = [r for r in results if service in r.get("affected_services", [])]

        # Sort by started_at descending
        results.sort(key=lambda x: x.get("started_at", ""), reverse=True)

        return [_to_investigation(r) for r in results[offset:offset + limit]]

    @strawberry.field
    def incident(self, id: str) -> Optional[Incident]:
        """Get an incident by ID."""
        data = _incidents.get(id)
        if not data:
            return None
        return _to_incident(data)

    @strawberry.field
    def incidents(
        self,
        status: Optional[str] = None,
        severity: Optional[Severity] = None,
        service: Optional[str] = None,
        limit: int = 20,
    ) -> list[Incident]:
        """List incidents with optional filters."""
        results = list(_incidents.values())

        if status:
            results = [r for r in results if r.get("status") == status]

        if severity:
            results = [r for r in results if r.get("severity") == severity.value]

        if service:
            results = [r for r in results if r.get("service") == service]

        results.sort(key=lambda x: x.get("created_at", ""), reverse=True)

        return [_to_incident(r) for r in results[:limit]]

    @strawberry.field
    def service(self, name: str) -> Optional[Service]:
        """Get a service by name."""
        data = _services.get(name)
        if not data:
            return None
        return _to_service(data)

    @strawberry.field
    def services(self, team: Optional[str] = None, tier: Optional[int] = None) -> list[Service]:
        """List all services."""
        results = list(_services.values())

        if team:
            results = [r for r in results if r.get("team") == team]

        if tier:
            results = [r for r in results if r.get("tier") == tier]

        return [_to_service(r) for r in results]

    @strawberry.field
    def alerts(
        self,
        state: Optional[AlertState] = None,
        severity: Optional[Severity] = None,
        service: Optional[str] = None,
    ) -> list[Alert]:
        """Get current alerts."""
        # In production: query Prometheus/Alertmanager
        alerts = [
            {
                "id": "alert-001",
                "name": "HighErrorRate",
                "severity": "high",
                "state": "firing",
                "service": "api-server",
                "description": "Error rate above 5%",
                "labels": {"job": "api-server", "instance": "pod-abc"},
                "annotations": {"summary": "High error rate detected"},
                "starts_at": datetime.now(timezone.utc) - timedelta(minutes=15),
            },
        ]

        results = alerts

        if state:
            results = [a for a in results if a.get("state") == state.value]

        if severity:
            results = [a for a in results if a.get("severity") == severity.value]

        if service:
            results = [a for a in results if a.get("service") == service]

        return [_to_alert(a) for a in results]

    @strawberry.field
    def metrics(self, input: MetricQueryInput) -> Metric:
        """Query metrics for a time range."""
        # In production: query Prometheus/Datadog
        now = datetime.now(timezone.utc)
        values = [
            MetricPoint(timestamp=now - timedelta(minutes=i), value=100 + i * 0.5)
            for i in range(60)
        ]

        return Metric(
            name=input.name,
            service=input.service or "unknown",
            labels=JSON({"job": input.service or "unknown"}),
            values=values,
            unit="ms",
        )

    @strawberry.field
    def me(self, info: strawberry.Info) -> User:
        """Get current user from auth context."""
        # Try to get user from request context (set by auth middleware)
        request = info.context.get("request") if hasattr(info, "context") else None
        if request and hasattr(request, "state") and hasattr(request.state, "user"):
            user = request.state.user
            return User(
                id=user.get("id", ""),
                email=user.get("email", ""),
                name=user.get("name", ""),
                role=user.get("role", "viewer"),
                team=user.get("team", ""),
            )

        # Return empty user if not authenticated (let frontend handle auth redirect)
        return User(
            id="",
            email="",
            name="",
            role="viewer",
            team="",
        )

    @strawberry.field
    def organization(self) -> Organization:
        """Get current organization."""
        return Organization(
            id="org-001",
            name="Acme Corp",
            plan="team",
            member_count=10,
            api_key_count=3,
        )

    @strawberry.field
    def usage(self) -> UsageStats:
        """Get usage statistics."""
        return UsageStats(
            period="2024-03",
            investigations_used=45,
            investigations_quota=100,
            tokens_used=125000,
            cost_usd=3.75,
        )

    @strawberry.field
    def eval_results(self, limit: int = 10) -> list[EvalResult]:
        """Get recent evaluation results."""
        # In production: query from eval results store
        return [
            EvalResult(
                eval_id="eval-abc123",
                started_at=datetime.now(timezone.utc) - timedelta(hours=8),
                completed_at=datetime.now(timezone.utc) - timedelta(hours=7, minutes=45),
                scenarios_total=50,
                scenarios_passed=47,
                accuracy=0.94,
                mean_latency_ms=25000,
                mean_cost_usd=0.15,
                passed=True,
            ),
        ]

    @strawberry.field
    def anomalies(
        self,
        service: Optional[str] = None,
        severity: Optional[Severity] = None,
        limit: int = 20,
    ) -> list[AnomalyDetection]:
        """Get detected anomalies from ambient monitoring."""
        # In production: query from ambient monitor
        return [
            AnomalyDetection(
                id="anomaly-001",
                anomaly_type="latency_spike",
                metric_name="latency_p99",
                service="api-server",
                severity=Severity.HIGH,
                confidence=0.92,
                current_value=1500.0,
                expected_value=200.0,
                description="Latency spike: 1500ms vs expected 200ms",
                detected_at=datetime.now(timezone.utc) - timedelta(minutes=5),
            ),
        ]


@strawberry.type
class Mutation:
    """GraphQL Mutation operations."""

    @strawberry.mutation
    async def start_investigation(self, input: StartInvestigationInput) -> Investigation:
        """Start a new investigation."""
        investigation_id = f"inv-{uuid4().hex[:8]}"
        now = datetime.now(timezone.utc)

        data = {
            "id": investigation_id,
            "thread_id": input.thread_id,
            "status": "running",
            "incident_summary": input.message,
            "root_cause": None,
            "confidence": 0.0,
            "severity": input.severity.value if input.severity else "medium",
            "hypotheses": [],
            "evidence": [],
            "affected_services": [input.service] if input.service else [],
            "trace": ["Investigation started"],
            "tokens_used": 0,
            "cost_usd": 0.0,
            "started_at": now.isoformat(),
            "completed_at": None,
        }

        _investigations[investigation_id] = data

        # In production: trigger actual investigation via session
        log.info("Started investigation %s", investigation_id)

        return _to_investigation(data)

    @strawberry.mutation
    async def approve_action(self, input: ApprovalDecisionInput) -> Investigation:
        """Approve or reject a pending action."""
        # Find investigation by thread_id
        investigation = None
        for data in _investigations.values():
            if data.get("thread_id") == input.thread_id:
                investigation = data
                break

        if not investigation:
            raise ValueError(f"No investigation found for thread {input.thread_id}")

        if input.approved:
            investigation["trace"].append(f"Action approved: {input.reason or 'No reason'}")
        else:
            investigation["trace"].append(f"Action rejected: {input.reason or 'No reason'}")

        investigation["status"] = "completed"
        investigation["completed_at"] = datetime.now(timezone.utc).isoformat()

        return _to_investigation(investigation)

    @strawberry.mutation
    async def create_incident(self, input: CreateIncidentInput) -> Incident:
        """Create a new incident."""
        incident_id = f"INC-{uuid4().hex[:6].upper()}"
        now = datetime.now(timezone.utc)

        data = {
            "id": incident_id,
            "title": input.title,
            "description": input.description,
            "severity": input.severity.value,
            "status": "open",
            "service": input.service,
            "source": input.source,
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }

        _incidents[incident_id] = data

        return _to_incident(data)

    @strawberry.mutation
    async def acknowledge_incident(self, id: str) -> Incident:
        """Acknowledge an incident."""
        if id not in _incidents:
            raise ValueError(f"Incident {id} not found")

        _incidents[id]["status"] = "acknowledged"
        _incidents[id]["acknowledged_at"] = datetime.now(timezone.utc).isoformat()
        _incidents[id]["updated_at"] = datetime.now(timezone.utc).isoformat()

        return _to_incident(_incidents[id])

    @strawberry.mutation
    async def resolve_incident(self, id: str, resolution: str = "") -> Incident:
        """Resolve an incident."""
        if id not in _incidents:
            raise ValueError(f"Incident {id} not found")

        _incidents[id]["status"] = "resolved"
        _incidents[id]["resolved_at"] = datetime.now(timezone.utc).isoformat()
        _incidents[id]["updated_at"] = datetime.now(timezone.utc).isoformat()
        _incidents[id]["resolution"] = resolution

        return _to_incident(_incidents[id])

    @strawberry.mutation
    async def trigger_eval(self) -> EvalResult:
        """Trigger an evaluation run."""
        # In production: trigger actual eval pipeline
        return EvalResult(
            eval_id=f"eval-{uuid4().hex[:8]}",
            started_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc) + timedelta(minutes=15),
            scenarios_total=50,
            scenarios_passed=48,
            accuracy=0.96,
            mean_latency_ms=22000,
            mean_cost_usd=0.14,
            passed=True,
        )


@strawberry.type
class Subscription:
    """GraphQL Subscription operations for real-time updates."""

    @strawberry.subscription
    async def investigation_updates(
        self,
        thread_id: str,
    ) -> AsyncGenerator[InvestigationUpdate, None]:
        """Subscribe to investigation updates."""
        # In production: connect to actual investigation stream
        events = [
            ("trace", {"line": "Analyzing logs..."}),
            ("trace", {"line": "Found 523 errors in the last hour"}),
            ("hypothesis", {"description": "Database connection pool exhaustion", "confidence": 0.85}),
            ("complete", {"root_cause": "Connection pool exhaustion", "confidence": 0.92}),
        ]

        for event_type, data in events:
            await asyncio.sleep(1)  # Simulate work
            yield InvestigationUpdate(
                investigation_id=thread_id,
                event_type=event_type,
                data=JSON(data),
                timestamp=datetime.now(timezone.utc),
            )

    @strawberry.subscription
    async def alert_updates(
        self,
        service: Optional[str] = None,
    ) -> AsyncGenerator[AlertUpdate, None]:
        """Subscribe to alert updates."""
        # In production: connect to Alertmanager/Prometheus webhook
        while True:
            await asyncio.sleep(30)  # Check every 30 seconds
            # Simulate alert
            yield AlertUpdate(
                alert=Alert(
                    id=f"alert-{uuid4().hex[:6]}",
                    name="LatencySpike",
                    severity=Severity.MEDIUM,
                    state=AlertState.FIRING,
                    service=service or "api-server",
                    description="P99 latency above threshold",
                    labels=JSON({}),
                    annotations=JSON({}),
                    starts_at=datetime.now(timezone.utc),
                    ends_at=None,
                ),
                event_type="fired",
                timestamp=datetime.now(timezone.utc),
            )


# Helper functions to convert data to types

def _to_investigation(data: dict) -> Investigation:
    return Investigation(
        id=data["id"],
        thread_id=data.get("thread_id", ""),
        status=InvestigationStatus(data.get("status", "pending")),
        incident_summary=data.get("incident_summary", ""),
        root_cause=data.get("root_cause"),
        confidence=data.get("confidence", 0.0),
        severity=Severity(data.get("severity", "medium")),
        hypotheses=[
            Hypothesis(
                description=h.get("description", ""),
                confidence=h.get("confidence", 0.0),
                evidence=h.get("evidence", []),
                rank=h.get("rank", 0),
            )
            for h in data.get("hypotheses", [])
        ],
        evidence=[
            Evidence(
                source=e.get("source", ""),
                description=e.get("description", ""),
                timestamp=datetime.fromisoformat(e["timestamp"]) if e.get("timestamp") else datetime.now(timezone.utc),
                data=e.get("data", {}),
            )
            for e in data.get("evidence", [])
        ],
        affected_services=data.get("affected_services", []),
        trace=data.get("trace", []),
        tokens_used=data.get("tokens_used", 0),
        cost_usd=data.get("cost_usd", 0.0),
        started_at=datetime.fromisoformat(data["started_at"]) if data.get("started_at") else datetime.now(timezone.utc),
        completed_at=datetime.fromisoformat(data["completed_at"]) if data.get("completed_at") else None,
        approval_request=None,
    )


def _to_incident(data: dict) -> Incident:
    return Incident(
        id=data["id"],
        title=data.get("title", ""),
        description=data.get("description", ""),
        severity=Severity(data.get("severity", "medium")),
        status=data.get("status", "open"),
        service=data.get("service", ""),
        source=data.get("source", "unknown"),
        created_at=datetime.fromisoformat(data["created_at"]) if data.get("created_at") else datetime.now(timezone.utc),
        updated_at=datetime.fromisoformat(data["updated_at"]) if data.get("updated_at") else datetime.now(timezone.utc),
        acknowledged_at=datetime.fromisoformat(data["acknowledged_at"]) if data.get("acknowledged_at") else None,
        resolved_at=datetime.fromisoformat(data["resolved_at"]) if data.get("resolved_at") else None,
        investigation_id=data.get("investigation_id"),
    )


def _to_service(data: dict) -> Service:
    return Service(
        id=data["id"],
        name=data["name"],
        description=data.get("description", ""),
        team=data.get("team", ""),
        tier=data.get("tier", 3),
        dependencies=data.get("dependencies", []),
        dependents=data.get("dependents", []),
        slo_target=data.get("slo_target", 0.99),
        current_slo=data.get("current_slo", 0.99),
        last_incident_at=datetime.fromisoformat(data["last_incident_at"]) if data.get("last_incident_at") else None,
        metadata=data.get("metadata", {}),
    )


def _to_alert(data: dict) -> Alert:
    return Alert(
        id=data["id"],
        name=data["name"],
        severity=Severity(data.get("severity", "medium")),
        state=AlertState(data.get("state", "firing")),
        service=data.get("service", ""),
        description=data.get("description", ""),
        labels=data.get("labels", {}),
        annotations=data.get("annotations", {}),
        starts_at=data["starts_at"] if isinstance(data.get("starts_at"), datetime) else datetime.now(timezone.utc),
        ends_at=data.get("ends_at"),
    )


# Create schema
schema = strawberry.Schema(
    query=Query,
    mutation=Mutation,
    subscription=Subscription,
)

# Create FastAPI router
graphql_app = GraphQLRouter(schema)
