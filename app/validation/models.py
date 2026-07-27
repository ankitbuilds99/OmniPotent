"""Pydantic models for API request/response validation.

All API endpoints should use these models for:
- Type safety and validation
- OpenAPI schema generation
- Consistent error messages
- Documentation
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field, field_validator


class InvestigationStatus(str, Enum):
    """Status of an investigation."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Severity(str, Enum):
    """Incident severity levels."""

    SEV1 = "SEV1"
    SEV2 = "SEV2"
    SEV3 = "SEV3"
    SEV4 = "SEV4"
    UNKNOWN = "unknown"


class ChatRequest(BaseModel):
    """Request model for starting/continuing an investigation."""

    thread_id: str = Field(
        ...,
        min_length=1,
        max_length=64,
        pattern=r"^[a-zA-Z0-9_-]+$",
        description="Unique identifier for the investigation thread",
        json_schema_extra={"example": "incident-2024-03-15-001"},
    )
    message: str = Field(
        ...,
        min_length=1,
        max_length=10000,
        description="The investigation query or follow-up message",
        json_schema_extra={
            "example": "Why is the checkout service returning 500 errors?"
        },
    )
    context: dict[str, Any] | None = Field(
        default=None,
        description="Optional context (alert data, metrics, etc.)",
    )

    @field_validator("message")
    @classmethod
    def message_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Message cannot be empty or whitespace only")
        return v.strip()

    @field_validator("thread_id")
    @classmethod
    def thread_id_valid(cls, v: str) -> str:
        if v.startswith("-") or v.endswith("-"):
            raise ValueError("Thread ID cannot start or end with hyphen")
        return v

    model_config = {
        "json_schema_extra": {
            "example": {
                "thread_id": "incident-2024-03-15-001",
                "message": "Why is the checkout service returning 500 errors?",
                "context": {
                    "service": "checkout",
                    "error_rate": "15%",
                    "started_at": "2024-03-15T10:30:00Z",
                },
            }
        }
    }


class ApprovalRequest(BaseModel):
    """Details of an action awaiting approval."""

    tool: str = Field(..., description="The tool/action requiring approval")
    args: dict[str, Any] = Field(
        default_factory=dict, description="Arguments for the tool"
    )
    risk_level: str = Field(
        default="medium", description="Risk level (low, medium, high, critical)"
    )
    rationale: str = Field(default="", description="Why this action is recommended")
    reversible: bool = Field(
        default=True, description="Whether the action can be undone"
    )


class Hypothesis(BaseModel):
    """A ranked hypothesis about the root cause."""

    rank: int = Field(..., ge=1, le=10, description="Rank (1 = most likely)")
    description: str = Field(..., description="Description of the hypothesis")
    confidence: float = Field(
        ..., ge=0.0, le=1.0, description="Confidence score (0-1)"
    )
    evidence: list[str] = Field(
        default_factory=list, description="Supporting evidence"
    )
    refuting_evidence: list[str] = Field(
        default_factory=list, description="Evidence against this hypothesis"
    )


class InvestigationReport(BaseModel):
    """Structured RCA report from a completed investigation."""

    summary: str = Field(..., description="Executive summary of findings")
    severity: Severity = Field(
        default=Severity.UNKNOWN, description="Assessed incident severity"
    )
    root_cause: str | None = Field(
        default=None, description="Identified root cause"
    )
    hypotheses: list[Hypothesis] = Field(
        default_factory=list, description="Ranked hypotheses"
    )
    timeline: list[dict[str, Any]] = Field(
        default_factory=list, description="Event timeline"
    )
    affected_services: list[str] = Field(
        default_factory=list, description="Services impacted"
    )
    recommendations: list[str] = Field(
        default_factory=list, description="Remediation recommendations"
    )
    postmortem_draft: str | None = Field(
        default=None, description="Draft postmortem document"
    )
    abstained: bool = Field(
        default=False,
        description="Whether the agent abstained due to insufficient data",
    )


class ChatResponse(BaseModel):
    """Response model for investigation endpoints."""

    thread_id: str = Field(..., description="The investigation thread ID")
    status: InvestigationStatus = Field(
        ..., description="Current status of the investigation"
    )
    answer: str = Field(default="", description="Agent's response/findings")
    approval_request: ApprovalRequest | None = Field(
        default=None, description="Pending approval request, if any"
    )
    trace: list[str] = Field(
        default_factory=list, description="Investigation steps taken"
    )
    report: InvestigationReport | None = Field(
        default=None, description="Structured RCA report (if completed)"
    )
    tokens_used: int = Field(
        default=0, ge=0, description="LLM tokens consumed"
    )
    duration_ms: float = Field(
        default=0.0, ge=0.0, description="Investigation duration in milliseconds"
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "thread_id": "incident-2024-03-15-001",
                "status": "completed",
                "answer": "The root cause was a memory leak in the connection pool...",
                "trace": [
                    "Querying Datadog for error logs...",
                    "Found 523 errors in the last hour",
                    "Analyzing stack traces...",
                ],
                "report": {
                    "summary": "Memory leak in connection pool causing 500 errors",
                    "severity": "SEV2",
                    "root_cause": "Connection pool not releasing connections on timeout",
                },
                "tokens_used": 4521,
                "duration_ms": 12340.5,
            }
        }
    }


class ApproveRequest(BaseModel):
    """Request model for approval/rejection of pending actions."""

    thread_id: str = Field(
        ...,
        min_length=1,
        max_length=64,
        pattern=r"^[a-zA-Z0-9_-]+$",
        description="The investigation thread ID",
    )
    approved: bool = Field(..., description="Whether to approve the action")
    reason: str = Field(
        default="",
        max_length=1000,
        description="Reason for approval/rejection",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "thread_id": "incident-2024-03-15-001",
                "approved": True,
                "reason": "Confirmed rollback is safe during low traffic window",
            }
        }
    }


class ApproveResponse(BaseModel):
    """Response after processing an approval decision."""

    thread_id: str
    approved: bool
    continuation: ChatResponse | None = Field(
        default=None, description="Investigation continuation after approval"
    )


class ErrorDetail(BaseModel):
    """Detailed error information."""

    code: str = Field(..., description="Error code (e.g., VALIDATION_ERROR)")
    message: str = Field(..., description="Human-readable error message")
    field: str | None = Field(default=None, description="Field that caused the error")
    details: dict[str, Any] | None = Field(
        default=None, description="Additional error context"
    )


class ErrorResponse(BaseModel):
    """Standardized error response format."""

    error: str = Field(..., description="Error type")
    message: str = Field(..., description="Human-readable error message")
    details: list[ErrorDetail] = Field(
        default_factory=list, description="Detailed error information"
    )
    request_id: str | None = Field(
        default=None, description="Request ID for debugging"
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), description="Error timestamp"
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "error": "ValidationError",
                "message": "Request validation failed",
                "details": [
                    {
                        "code": "INVALID_THREAD_ID",
                        "message": "Thread ID contains invalid characters",
                        "field": "thread_id",
                    }
                ],
                "request_id": "req_abc123",
                "timestamp": "2024-03-15T10:30:00Z",
            }
        }
    }


T = TypeVar("T")


class PaginatedResponse(BaseModel, Generic[T]):
    """Generic paginated response wrapper."""

    items: list[T] = Field(..., description="Page items")
    total: int = Field(..., ge=0, description="Total number of items")
    page: int = Field(..., ge=1, description="Current page number")
    page_size: int = Field(..., ge=1, le=100, description="Items per page")
    has_next: bool = Field(..., description="Whether there are more pages")
    has_prev: bool = Field(..., description="Whether there are previous pages")


class HealthResponse(BaseModel):
    """Health check response."""

    status: str = Field(..., description="Health status (ok, degraded, unhealthy)")
    version: str = Field(default="1.0.0", description="Application version")
    uptime_seconds: float = Field(..., ge=0, description="Uptime in seconds")
    components: dict[str, dict[str, Any]] = Field(
        default_factory=dict, description="Component health details"
    )
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class MCPServer(BaseModel):
    """MCP server configuration."""

    name: str
    label: str
    custom: bool = True
    tools: list[str] = Field(default_factory=list)


class ConfigResponse(BaseModel):
    """Agent configuration response."""

    provider: str = Field(..., description="LLM provider (anthropic, openai, etc.)")
    model: str = Field(..., description="Main model name")
    fast_model: str = Field(..., description="Fast/cheap model name")
    offline_mode: bool = Field(..., description="Whether running in offline mode")
    servers: list[MCPServer] = Field(..., description="Available MCP servers")
    github: dict[str, Any] = Field(..., description="GitHub connection status")
    sources: dict[str, str] = Field(..., description="Data source paths")
    has_key: bool = Field(..., description="Whether LLM API key is configured")


class FeedbackRequest(BaseModel):
    """Feedback submission request."""

    thread_id: str = Field(..., min_length=1, max_length=64)
    rating: str = Field(..., pattern=r"^(up|down)$", description="Thumbs up or down")
    comment: str = Field(default="", max_length=2000)
    question: str = Field(
        default="", max_length=1000, description="Original question (for context)"
    )

    @field_validator("rating")
    @classmethod
    def rating_valid(cls, v: str) -> str:
        if v not in ("up", "down"):
            raise ValueError("Rating must be 'up' or 'down'")
        return v


class GithubConnectRequest(BaseModel):
    """GitHub connection request."""

    token: str = Field(
        ...,
        min_length=1,
        description="GitHub personal access token",
    )
    repo: str = Field(
        ...,
        min_length=1,
        description="Repository (owner/repo or full URL)",
    )

    @field_validator("token")
    @classmethod
    def token_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Token cannot be empty")
        return v


class ModelConfigRequest(BaseModel):
    """LLM provider configuration request."""

    provider: str = Field(
        ...,
        description="Provider name (anthropic, openai, gemini, groq, deepseek)",
    )
    api_key: str = Field(default="", description="API key for the provider")
    model: str = Field(default="", description="Optional model override")
    fast_model: str = Field(default="", description="Optional fast model override")

    @field_validator("provider")
    @classmethod
    def provider_valid(cls, v: str) -> str:
        valid = {"anthropic", "openai", "gemini", "groq", "deepseek"}
        v = v.strip().lower()
        if v not in valid:
            raise ValueError(f"Provider must be one of: {', '.join(sorted(valid))}")
        return v
