"""In-memory incident store for demo purposes."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from app.incidents.models import (
    Incident,
    IncidentSeverity,
    IncidentStatus,
    Investigation,
    AgentState,
    AgentStatus,
    AgentFinding,
    SafetyGate,
    SafetyGateStatus,
    TimelineEntry,
)
from app.tenancy.scoping import scoped

AGENT_TYPES = ["knowledge", "reasoning", "action", "learning", "collaboration", "ambient"]


class IncidentStore:
    def __init__(self) -> None:
        self._incidents: dict[UUID, Incident] = {}
        self._investigations: dict[UUID, Investigation] = {}
        self._init_demo_data()

    def _init_demo_data(self) -> None:
        now = datetime.utcnow()

        inc1_id = uuid4()
        inv1 = Investigation(
            id=uuid4(),
            incident_id=inc1_id,
            started_at=now - timedelta(minutes=15),
            overall_confidence=0.87,
            root_cause="Database connection pool exhaustion due to long-running queries",
            recommended_actions=[
                "Increase connection pool size from 20 to 50",
                "Add query timeout of 30 seconds",
                "Review and optimize slow queries",
            ],
            agents={
                "knowledge": AgentState(
                    agent_type="knowledge",
                    status=AgentStatus.COMPLETE,
                    progress=100,
                    findings=[
                        AgentFinding(
                            agent_type="knowledge",
                            finding="Similar incident occurred 2 weeks ago - resolved by restarting DB",
                            confidence=0.92,
                            timestamp=now - timedelta(minutes=10),
                        )
                    ],
                    started_at=now - timedelta(minutes=15),
                    completed_at=now - timedelta(minutes=10),
                ),
                "reasoning": AgentState(
                    agent_type="reasoning",
                    status=AgentStatus.COMPLETE,
                    progress=100,
                    findings=[
                        AgentFinding(
                            agent_type="reasoning",
                            finding="Connection pool exhaustion correlates with spike in checkout requests",
                            confidence=0.89,
                            timestamp=now - timedelta(minutes=8),
                        )
                    ],
                    started_at=now - timedelta(minutes=14),
                    completed_at=now - timedelta(minutes=8),
                ),
                "action": AgentState(
                    agent_type="action",
                    status=AgentStatus.RUNNING,
                    progress=65,
                    current_action="Preparing connection pool resize command",
                    started_at=now - timedelta(minutes=5),
                ),
                "learning": AgentState(
                    agent_type="learning",
                    status=AgentStatus.COMPLETE,
                    progress=100,
                    findings=[
                        AgentFinding(
                            agent_type="learning",
                            finding="Pattern detected: 3 similar incidents in past month",
                            confidence=0.85,
                            timestamp=now - timedelta(minutes=7),
                        )
                    ],
                    started_at=now - timedelta(minutes=12),
                    completed_at=now - timedelta(minutes=7),
                ),
                "collaboration": AgentState(
                    agent_type="collaboration",
                    status=AgentStatus.COMPLETE,
                    progress=100,
                    findings=[
                        AgentFinding(
                            agent_type="collaboration",
                            finding="Notified #platform-oncall and created Jira ticket PLAT-1234",
                            confidence=1.0,
                            timestamp=now - timedelta(minutes=9),
                        )
                    ],
                    started_at=now - timedelta(minutes=13),
                    completed_at=now - timedelta(minutes=9),
                ),
                "ambient": AgentState(
                    agent_type="ambient",
                    status=AgentStatus.RUNNING,
                    progress=80,
                    current_action="Monitoring system metrics for recovery signals",
                    started_at=now - timedelta(minutes=14),
                ),
            },
            safety_gates=[
                SafetyGate(
                    gate_type="confidence",
                    status=SafetyGateStatus.PASSED,
                    threshold=0.8,
                    current_value=0.87,
                    message="Confidence threshold met",
                    checked_at=now - timedelta(minutes=5),
                ),
                SafetyGate(
                    gate_type="policy",
                    status=SafetyGateStatus.PASSED,
                    message="Action complies with change management policy",
                    checked_at=now - timedelta(minutes=4),
                ),
                SafetyGate(
                    gate_type="sandbox",
                    status=SafetyGateStatus.PENDING,
                    message="Awaiting sandbox test completion",
                ),
            ],
        )

        inc1 = Incident(
            id=inc1_id,
            title="Checkout API 500 errors",
            description="High rate of 500 errors on /api/checkout endpoint affecting customer transactions",
            severity=IncidentSeverity.CRITICAL,
            status=IncidentStatus.INVESTIGATING,
            service="checkout-service",
            triggered_at=now - timedelta(minutes=20),
            acknowledged_at=now - timedelta(minutes=18),
            acknowledged_by="oncall-bot",
            assignee="platform-team",
            tags=["api", "checkout", "database"],
            customer_impact=True,
            impact="~15% of checkout transactions failing",
            investigation=inv1,
            timeline=[
                TimelineEntry(
                    timestamp=now - timedelta(minutes=20),
                    type="triggered",
                    message="Alert triggered: checkout-api-error-rate > 5%",
                ),
                TimelineEntry(
                    timestamp=now - timedelta(minutes=18),
                    type="acknowledged",
                    message="Auto-acknowledged by OmniPotent",
                    user="oncall-bot",
                ),
                TimelineEntry(
                    timestamp=now - timedelta(minutes=15),
                    type="investigation_started",
                    message="6-agent parallel investigation initiated",
                ),
                TimelineEntry(
                    timestamp=now - timedelta(minutes=10),
                    type="finding",
                    message="Knowledge agent found similar past incident",
                ),
                TimelineEntry(
                    timestamp=now - timedelta(minutes=5),
                    type="root_cause",
                    message="Root cause identified: DB connection pool exhaustion",
                ),
            ],
        )

        inc2 = Incident(
            id=uuid4(),
            title="High latency on API Gateway",
            description="P99 latency exceeding 2s threshold",
            severity=IncidentSeverity.HIGH,
            status=IncidentStatus.MONITORING,
            service="api-gateway",
            triggered_at=now - timedelta(hours=1),
            acknowledged_at=now - timedelta(minutes=55),
            acknowledged_by="jane.doe",
            assignee="jane.doe",
            tags=["latency", "gateway"],
            timeline=[
                TimelineEntry(
                    timestamp=now - timedelta(hours=1),
                    type="triggered",
                    message="Alert: P99 latency > 2000ms",
                ),
                TimelineEntry(
                    timestamp=now - timedelta(minutes=55),
                    type="acknowledged",
                    message="Acknowledged by Jane Doe",
                    user="jane.doe",
                ),
                TimelineEntry(
                    timestamp=now - timedelta(minutes=30),
                    type="action",
                    message="Scaled up API gateway replicas from 3 to 5",
                    user="jane.doe",
                ),
                TimelineEntry(
                    timestamp=now - timedelta(minutes=15),
                    type="status_change",
                    message="Status changed to Monitoring",
                    user="jane.doe",
                ),
            ],
        )

        inc3 = Incident(
            id=uuid4(),
            title="Memory leak in worker pods",
            description="Gradual memory increase in worker-pool pods",
            severity=IncidentSeverity.MEDIUM,
            status=IncidentStatus.IDENTIFIED,
            service="worker-pool",
            triggered_at=now - timedelta(hours=3),
            acknowledged_at=now - timedelta(hours=2, minutes=50),
            acknowledged_by="john.smith",
            assignee="backend-team",
            tags=["memory", "kubernetes", "worker"],
            timeline=[],
        )

        self._incidents[inc1.id] = inc1
        self._incidents[inc2.id] = inc2
        self._incidents[inc3.id] = inc3

        if inc1.investigation:
            self._investigations[inc1.investigation.id] = inc1.investigation

    def list_incidents(
        self,
        status: IncidentStatus | None = None,
        severity: IncidentSeverity | None = None,
        service: str | None = None,
        limit: int = 50,
    ) -> list[Incident]:
        incidents = list(self._incidents.values())

        if status:
            incidents = [i for i in incidents if i.status == status]
        if severity:
            incidents = [i for i in incidents if i.severity == severity]
        if service:
            incidents = [i for i in incidents if i.service == service]

        incidents.sort(key=lambda i: i.triggered_at, reverse=True)
        return incidents[:limit]

    def get_incident(self, incident_id: UUID) -> Incident | None:
        return self._incidents.get(incident_id)

    def create_incident(
        self,
        title: str,
        severity: IncidentSeverity,
        service: str,
        description: str | None = None,
        tags: list[str] | None = None,
        impact: str | None = None,
        customer_impact: bool = False,
    ) -> Incident:
        incident = Incident(
            title=title,
            description=description,
            severity=severity,
            service=service,
            tags=tags or [],
            impact=impact,
            customer_impact=customer_impact,
            timeline=[
                TimelineEntry(
                    timestamp=datetime.utcnow(),
                    type="triggered",
                    message=f"Incident created: {title}",
                )
            ],
        )
        self._incidents[incident.id] = incident
        return incident

    def update_incident(
        self,
        incident_id: UUID,
        updates: dict[str, Any],
    ) -> Incident | None:
        incident = self._incidents.get(incident_id)
        if not incident:
            return None

        now = datetime.utcnow()

        if "status" in updates and updates["status"] != incident.status:
            new_status = updates["status"]
            incident.status = new_status
            incident.timeline.append(
                TimelineEntry(
                    timestamp=now,
                    type="status_change",
                    message=f"Status changed to {new_status.value}",
                )
            )
            if new_status == IncidentStatus.RESOLVED:
                incident.resolved_at = now

        if "severity" in updates:
            incident.severity = updates["severity"]
        if "assignee" in updates:
            incident.assignee = updates["assignee"]
        if "title" in updates:
            incident.title = updates["title"]
        if "description" in updates:
            incident.description = updates["description"]

        return incident

    def acknowledge_incident(
        self, incident_id: UUID, user: str
    ) -> Incident | None:
        incident = self._incidents.get(incident_id)
        if not incident:
            return None

        incident.acknowledged_at = datetime.utcnow()
        incident.acknowledged_by = user
        incident.status = IncidentStatus.INVESTIGATING
        incident.timeline.append(
            TimelineEntry(
                timestamp=datetime.utcnow(),
                type="acknowledged",
                message=f"Acknowledged by {user}",
                user=user,
            )
        )
        return incident

    def resolve_incident(
        self, incident_id: UUID, user: str, notes: str | None = None
    ) -> Incident | None:
        incident = self._incidents.get(incident_id)
        if not incident:
            return None

        incident.resolved_at = datetime.utcnow()
        incident.resolved_by = user
        incident.status = IncidentStatus.RESOLVED
        message = f"Resolved by {user}"
        if notes:
            message += f": {notes}"
        incident.timeline.append(
            TimelineEntry(
                timestamp=datetime.utcnow(),
                type="resolved",
                message=message,
                user=user,
            )
        )
        return incident

    def start_investigation(self, incident_id: UUID) -> Investigation | None:
        incident = self._incidents.get(incident_id)
        if not incident:
            return None

        investigation = Investigation(
            incident_id=incident_id,
            agents={
                agent: AgentState(agent_type=agent, status=AgentStatus.IDLE)
                for agent in AGENT_TYPES
            },
            safety_gates=[
                SafetyGate(gate_type="confidence", threshold=0.8),
                SafetyGate(gate_type="policy"),
                SafetyGate(gate_type="sandbox"),
            ],
        )

        incident.investigation = investigation
        incident.timeline.append(
            TimelineEntry(
                timestamp=datetime.utcnow(),
                type="investigation_started",
                message="6-agent parallel investigation initiated",
            )
        )
        self._investigations[investigation.id] = investigation

        return investigation

    def get_investigation(self, investigation_id: UUID) -> Investigation | None:
        return self._investigations.get(investigation_id)

    def update_agent_status(
        self,
        investigation_id: UUID,
        agent_type: str,
        status: AgentStatus,
        progress: int | None = None,
        current_action: str | None = None,
        finding: AgentFinding | None = None,
    ) -> Investigation | None:
        investigation = self._investigations.get(investigation_id)
        if not investigation or agent_type not in investigation.agents:
            return None

        agent = investigation.agents[agent_type]
        agent.status = status

        if progress is not None:
            agent.progress = progress
        if current_action is not None:
            agent.current_action = current_action
        if finding:
            agent.findings.append(finding)

        if status == AgentStatus.RUNNING and not agent.started_at:
            agent.started_at = datetime.utcnow()
        if status == AgentStatus.COMPLETE:
            agent.completed_at = datetime.utcnow()
            agent.progress = 100

        completed_agents = [
            a for a in investigation.agents.values()
            if a.status == AgentStatus.COMPLETE
        ]
        if completed_agents:
            confidences = [
                f.confidence
                for a in completed_agents
                for f in a.findings
            ]
            if confidences:
                investigation.overall_confidence = sum(confidences) / len(confidences)

        return investigation


# One store per org. A single process-wide instance let any authenticated tenant
# read, acknowledge and resolve every other tenant's incidents — the store has no
# org column to filter on, so isolation comes from the instance itself.
_stores = scoped(IncidentStore)


def get_incident_store() -> IncidentStore:
    """The incident store for the org on the current request."""
    return _stores.get()
