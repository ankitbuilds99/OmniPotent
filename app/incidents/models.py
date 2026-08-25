"""Incident models and schemas."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class IncidentSeverity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class IncidentStatus(str, Enum):
    TRIGGERED = "triggered"
    INVESTIGATING = "investigating"
    IDENTIFIED = "identified"
    MONITORING = "monitoring"
    RESOLVED = "resolved"


class AgentStatus(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    COMPLETE = "complete"
    ERROR = "error"


class SafetyGateStatus(str, Enum):
    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"
    BYPASSED = "bypassed"


class AgentFinding(BaseModel):
    agent_type: str
    finding: str
    confidence: float
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    details: dict[str, Any] = {}


class AgentState(BaseModel):
    agent_type: str
    status: AgentStatus = AgentStatus.IDLE
    progress: int = 0
    current_action: str | None = None
    findings: list[AgentFinding] = []
    started_at: datetime | None = None
    completed_at: datetime | None = None


class SafetyGate(BaseModel):
    gate_type: str
    status: SafetyGateStatus = SafetyGateStatus.PENDING
    threshold: float | None = None
    current_value: float | None = None
    message: str | None = None
    checked_at: datetime | None = None


class Investigation(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    incident_id: UUID
    started_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: datetime | None = None
    overall_confidence: float = 0.0
    root_cause: str | None = None
    recommended_actions: list[str] = []
    agents: dict[str, AgentState] = {}
    safety_gates: list[SafetyGate] = []


class TimelineEntry(BaseModel):
    timestamp: datetime
    type: str
    message: str
    user: str | None = None
    details: dict[str, Any] = {}


class Incident(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    title: str
    description: str | None = None
    severity: IncidentSeverity
    status: IncidentStatus = IncidentStatus.TRIGGERED
    service: str
    triggered_at: datetime = Field(default_factory=datetime.utcnow)
    acknowledged_at: datetime | None = None
    resolved_at: datetime | None = None
    acknowledged_by: str | None = None
    resolved_by: str | None = None
    assignee: str | None = None
    tags: list[str] = []
    timeline: list[TimelineEntry] = []
    investigation: Investigation | None = None
    related_alerts: list[UUID] = []
    impact: str | None = None
    customer_impact: bool = False
    # The alerting platform's own id for the underlying alert, when it has one
    # (Alertmanager's `fingerprint`). Used to recognise a re-delivery of an alert
    # we already have an incident for, and to close that incident when the same
    # alert resolves.
    dedupe_key: str | None = None


class CreateIncidentRequest(BaseModel):
    title: str
    description: str | None = None
    severity: IncidentSeverity
    service: str
    tags: list[str] = []
    impact: str | None = None
    customer_impact: bool = False


class UpdateIncidentRequest(BaseModel):
    status: IncidentStatus | None = None
    severity: IncidentSeverity | None = None
    assignee: str | None = None
    title: str | None = None
    description: str | None = None


class IncidentResponse(BaseModel):
    id: UUID
    title: str
    description: str | None
    severity: IncidentSeverity
    status: IncidentStatus
    service: str
    triggered_at: datetime
    acknowledged_at: datetime | None
    resolved_at: datetime | None
    assignee: str | None
    tags: list[str]
    has_investigation: bool
    investigation_confidence: float | None


class IncidentDetailResponse(BaseModel):
    incident: Incident
    investigation: Investigation | None
