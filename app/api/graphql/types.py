"""GraphQL Types - Type definitions for the schema."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional
import strawberry


@strawberry.enum
class InvestigationStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"


@strawberry.enum
class Severity(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@strawberry.enum
class AlertState(Enum):
    FIRING = "firing"
    PENDING = "pending"
    RESOLVED = "resolved"


@strawberry.type
class Hypothesis:
    """A root cause hypothesis."""
    description: str
    confidence: float
    evidence: list[str]
    rank: int


@strawberry.type
class Evidence:
    """Evidence supporting a hypothesis."""
    source: str
    description: str
    timestamp: datetime
    data: strawberry.scalars.JSON


@strawberry.type
class ApprovalRequest:
    """A pending approval request."""
    id: str
    tool_name: str
    tool_args: strawberry.scalars.JSON
    risk_level: str
    rationale: str
    requested_at: datetime


@strawberry.type
class Investigation:
    """An incident investigation."""
    id: str
    thread_id: str
    status: InvestigationStatus
    incident_summary: str
    root_cause: Optional[str]
    confidence: float
    severity: Severity
    hypotheses: list[Hypothesis]
    evidence: list[Evidence]
    affected_services: list[str]
    trace: list[str]
    tokens_used: int
    cost_usd: float
    started_at: datetime
    completed_at: Optional[datetime]
    approval_request: Optional[ApprovalRequest]


@strawberry.type
class Incident:
    """An incident from alerting systems."""
    id: str
    title: str
    description: str
    severity: Severity
    status: str
    service: str
    source: str  # pagerduty, alertmanager, etc.
    created_at: datetime
    updated_at: datetime
    acknowledged_at: Optional[datetime]
    resolved_at: Optional[datetime]
    investigation_id: Optional[str]


@strawberry.type
class Service:
    """A monitored service."""
    id: str
    name: str
    description: str
    team: str
    tier: int  # 1 = critical, 2 = important, 3 = standard
    dependencies: list[str]
    dependents: list[str]
    slo_target: float
    current_slo: float
    last_incident_at: Optional[datetime]
    metadata: strawberry.scalars.JSON


@strawberry.type
class MetricPoint:
    """A single metric data point."""
    timestamp: datetime
    value: float


@strawberry.type
class Metric:
    """A time series metric."""
    name: str
    service: str
    labels: strawberry.scalars.JSON
    values: list[MetricPoint]
    unit: str


@strawberry.type
class Alert:
    """An active alert."""
    id: str
    name: str
    severity: Severity
    state: AlertState
    service: str
    description: str
    labels: strawberry.scalars.JSON
    annotations: strawberry.scalars.JSON
    starts_at: datetime
    ends_at: Optional[datetime]


@strawberry.type
class User:
    """A user/operator."""
    id: str
    email: str
    name: str
    role: str
    team: str


@strawberry.type
class ApiKey:
    """An API key (metadata only)."""
    id: str
    prefix: str
    name: str
    role: str
    created_at: datetime
    last_used_at: Optional[datetime]
    active: bool


@strawberry.type
class Organization:
    """An organization (multi-tenant)."""
    id: str
    name: str
    plan: str
    member_count: int
    api_key_count: int


@strawberry.type
class UsageStats:
    """Usage statistics."""
    period: str
    investigations_used: int
    investigations_quota: int
    tokens_used: int
    cost_usd: float


@strawberry.type
class EvalResult:
    """Evaluation result."""
    eval_id: str
    started_at: datetime
    completed_at: datetime
    scenarios_total: int
    scenarios_passed: int
    accuracy: float
    mean_latency_ms: float
    mean_cost_usd: float
    passed: bool


@strawberry.type
class AnomalyDetection:
    """Detected anomaly from ambient monitoring."""
    id: str
    anomaly_type: str
    metric_name: str
    service: str
    severity: Severity
    confidence: float
    current_value: float
    expected_value: Optional[float]
    description: str
    detected_at: datetime


# Input types for mutations

@strawberry.input
class StartInvestigationInput:
    """Input for starting an investigation."""
    thread_id: str
    message: str
    service: Optional[str] = None
    severity: Optional[Severity] = None


@strawberry.input
class ApprovalDecisionInput:
    """Input for approval decision."""
    thread_id: str
    approved: bool
    reason: str = ""


@strawberry.input
class CreateIncidentInput:
    """Input for creating an incident."""
    title: str
    description: str
    severity: Severity
    service: str
    source: str = "manual"


@strawberry.input
class MetricQueryInput:
    """Input for querying metrics."""
    name: str
    service: Optional[str] = None
    start_time: datetime
    end_time: datetime
    step: str = "1m"


# Subscription payloads

@strawberry.type
class InvestigationUpdate:
    """Real-time investigation update."""
    investigation_id: str
    event_type: str  # trace, hypothesis, approval, complete
    data: strawberry.scalars.JSON
    timestamp: datetime


@strawberry.type
class AlertUpdate:
    """Real-time alert update."""
    alert: Alert
    event_type: str  # fired, resolved, acknowledged
    timestamp: datetime
