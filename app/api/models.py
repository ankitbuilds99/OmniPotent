"""Pydantic response models with OpenAPI examples for the OmniPotent API.

These models provide complete type safety and auto-generated documentation
for all API endpoints. They include detailed examples for the OpenAPI spec
that enable client SDK generation.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
# Enums for API response types
# --------------------------------------------------------------------------- #
class TurnStatus(str, Enum):
    """Status of an investigation turn."""
    completed = "completed"
    awaiting_approval = "awaiting_approval"
    error = "error"


class RiskTier(str, Enum):
    """Risk level for proposed actions."""
    low = "low"
    medium = "medium"
    high = "high"


class ActionDecision(str, Enum):
    """Decision type for action gating."""
    allow = "allow"
    notify = "notify"
    approve = "approve"


class Severity(str, Enum):
    """Incident severity classification."""
    SEV1 = "SEV1"
    SEV2 = "SEV2"
    SEV3 = "SEV3"
    SEV4 = "SEV4"
    INFO = "INFO"


class Confidence(str, Enum):
    """Confidence level for hypotheses and analysis."""
    high = "high"
    medium = "medium"
    low = "low"


class Verdict(str, Enum):
    """Hypothesis verdict after analysis."""
    validated = "validated"
    invalidated = "invalidated"
    inconclusive = "inconclusive"


# --------------------------------------------------------------------------- #
# Nested models for structured responses
# --------------------------------------------------------------------------- #
class ProposedAction(BaseModel):
    """A proposed action requiring approval."""
    tool: str = Field(..., description="MCP tool name to execute")
    args: dict[str, Any] = Field(default_factory=dict, description="Tool arguments")
    write: bool = Field(default=False, description="Whether this is a write operation")
    decision: ActionDecision | None = Field(default=None, description="Gating decision")
    risk: RiskTier | None = Field(default=None, description="Risk assessment")
    why: str | None = Field(default=None, description="Explanation for the action")
    preview: str | None = Field(default=None, description="Preview of the action effect")

    model_config = {
        "json_schema_extra": {
            "example": {
                "tool": "rollback_deployment",
                "args": {"deployment": "api-server", "namespace": "production"},
                "write": True,
                "decision": "approve",
                "risk": "high",
                "why": "Rollback to previous version to restore service",
                "preview": "kubectl rollout undo deployment/api-server -n production"
            }
        }
    }


class ApprovalRequestModel(BaseModel):
    """Request for human approval before executing risky actions."""
    type: str = Field(..., description="Type of approval request")
    message: str = Field(..., description="Human-readable description of requested action")
    risk: RiskTier | None = Field(default=None, description="Overall risk level")
    evidence_count: int | None = Field(default=None, description="Number of evidence items")
    confidence: Confidence | None = Field(default=None, description="Confidence in the action")
    auto_approve_blocked: bool | None = Field(
        default=None,
        description="Whether automatic approval is blocked due to thin evidence"
    )
    gate_reason: str | None = Field(default=None, description="Reason for blocking auto-approval")
    actions: list[ProposedAction] = Field(default_factory=list, description="Actions to execute")

    model_config = {
        "json_schema_extra": {
            "example": {
                "type": "write_action",
                "message": "Rollback api-server deployment to restore service",
                "risk": "high",
                "evidence_count": 5,
                "confidence": "high",
                "auto_approve_blocked": False,
                "actions": [{
                    "tool": "rollback_deployment",
                    "args": {"deployment": "api-server"},
                    "write": True,
                    "risk": "high"
                }]
            }
        }
    }


class Hypothesis(BaseModel):
    """A hypothesis about the root cause with verdict."""
    cause: str = Field(..., description="Description of the potential cause")
    verdict: Verdict = Field(..., description="Verdict after investigation")
    confidence: Confidence = Field(..., description="Confidence in the verdict")
    evidence: list[str] = Field(default_factory=list, description="Supporting evidence")

    model_config = {
        "json_schema_extra": {
            "example": {
                "cause": "Memory leak in connection pool",
                "verdict": "validated",
                "confidence": "high",
                "evidence": [
                    "Memory usage increased 300% over 2 hours",
                    "Connection count stable, memory per connection growing",
                    "Recent PR #123 changed connection pooling logic"
                ]
            }
        }
    }


class RcaReport(BaseModel):
    """Structured Root Cause Analysis report."""
    summary: str = Field(..., description="Executive summary of the incident")
    severity: Severity = Field(..., description="Incident severity")
    confidence: Confidence = Field(..., description="Confidence in the analysis")
    root_cause: str | None = Field(default=None, description="Identified root cause")
    affected_services: list[str] = Field(default_factory=list, description="Affected services")
    hypotheses: list[Hypothesis] = Field(default_factory=list, description="Tested hypotheses")
    evidence: list[str] = Field(default_factory=list, description="Key evidence collected")
    recommended_actions: list[str] = Field(default_factory=list, description="Recommended fixes")
    postmortem: str = Field(default="", description="Full postmortem in Markdown")
    calibrated_confidence: Confidence | None = Field(
        default=None, description="Calibrated confidence after self-assessment"
    )
    abstained: bool | None = Field(
        default=None, description="Whether the agent abstained due to insufficient evidence"
    )
    needs: list[str] | None = Field(
        default=None, description="Additional data needed for higher confidence"
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "summary": "API latency spike caused by memory leak in connection pool",
                "severity": "SEV2",
                "confidence": "high",
                "root_cause": "Memory leak in connection pool introduced in commit abc123",
                "affected_services": ["api-server", "auth-service"],
                "hypotheses": [{
                    "cause": "Memory leak in connection pool",
                    "verdict": "validated",
                    "confidence": "high",
                    "evidence": ["Memory usage increased 300% over 2 hours"]
                }],
                "evidence": [
                    "Error rate spiked from 0.1% to 5.2% at 14:30 UTC",
                    "Memory usage on api-server pods grew to 95%",
                    "Commit abc123 deployed 15 minutes before incident"
                ],
                "recommended_actions": [
                    "Rollback deployment to previous version",
                    "Fix memory leak in PR #456",
                    "Add memory limit alerts"
                ],
                "postmortem": "# Incident Postmortem\n\n## Summary\n..."
            }
        }
    }


# --------------------------------------------------------------------------- #
# API Response Models
# --------------------------------------------------------------------------- #
class HealthResponse(BaseModel):
    """Health check response for liveness probes."""
    status: str = Field(..., description="Health status", examples=["ok"])
    active_sessions: int = Field(..., description="Number of active investigation sessions")

    model_config = {
        "json_schema_extra": {
            "example": {
                "status": "ok",
                "active_sessions": 3
            }
        }
    }


class ReadinessResponse(BaseModel):
    """Readiness check response."""
    status: str = Field(..., description="Readiness status", examples=["ready", "not_ready"])
    reasons: list[str] = Field(
        default_factory=list,
        description="Reasons for not being ready (empty if ready)"
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "status": "ready",
                "reasons": []
            }
        }
    }


class ChatResponse(BaseModel):
    """Response from a chat/investigation turn."""
    thread_id: str = Field(..., description="Unique identifier for the investigation thread")
    status: TurnStatus = Field(..., description="Current status of the investigation")
    answer: str = Field(default="", description="The agent's response text")
    approval_request: ApprovalRequestModel | None = Field(
        default=None, description="Approval request if status is awaiting_approval"
    )
    trace: list[str] = Field(default_factory=list, description="Investigation trace/steps taken")
    report: RcaReport | None = Field(
        default=None, description="Structured RCA report if investigation completed"
    )
    tokens_used: int = Field(default=0, description="Total LLM tokens consumed in this turn")

    model_config = {
        "json_schema_extra": {
            "example": {
                "thread_id": "inv-abc123",
                "status": "completed",
                "answer": "The root cause was a memory leak in the connection pool...",
                "approval_request": None,
                "trace": [
                    "Querying Datadog for error logs...",
                    "Found 523 errors in the last hour",
                    "Checking recent deployments...",
                    "Found commit abc123 deployed 15 minutes before incident"
                ],
                "report": {
                    "summary": "Memory leak caused API latency spike",
                    "severity": "SEV2",
                    "confidence": "high",
                    "root_cause": "Memory leak in connection pool"
                },
                "tokens_used": 4521
            }
        }
    }


class ConfigResponse(BaseModel):
    """Agent configuration and capabilities."""
    provider: str = Field(..., description="LLM provider (anthropic, openai, groq, etc.)")
    model: str = Field(..., description="Primary model for investigations")
    fast_model: str = Field(..., description="Fast model for simple tasks")
    offline_mode: bool = Field(..., description="Whether GitHub is in offline/demo mode")
    servers: list[dict] = Field(..., description="Available MCP servers and their tools")
    github: dict = Field(..., description="GitHub connection status")
    sources: dict = Field(..., description="Configured source paths")
    has_key: bool = Field(..., description="Whether an LLM API key is configured")

    model_config = {
        "json_schema_extra": {
            "example": {
                "provider": "anthropic",
                "model": "claude-sonnet-4-20250514",
                "fast_model": "claude-haiku-4-20250514",
                "offline_mode": False,
                "servers": [
                    {"name": "datadog", "label": "Datadog", "tools": ["search_logs", "get_metric"]},
                    {"name": "kubernetes", "label": "Kubernetes", "tools": ["list_pods", "describe_pod"]}
                ],
                "github": {"connected": True, "repo": "acme/api-server", "mode": "live"},
                "sources": {"repo_path": "/app/repo", "logs_path": "/app/logs"},
                "has_key": True
            }
        }
    }


class MeResponse(BaseModel):
    """Current user identity and permissions."""
    multi_tenant: bool = Field(..., description="Whether multi-tenant mode is enabled")
    authenticated: bool = Field(..., description="Whether the user is authenticated")
    org: str | None = Field(default=None, description="Organization ID if multi-tenant")
    role: str | None = Field(default=None, description="User role (owner, admin, responder, viewer)")
    plan: str | None = Field(default=None, description="Organization plan tier")
    label: str = Field(..., description="Display label for the user")

    model_config = {
        "json_schema_extra": {
            "example": {
                "multi_tenant": True,
                "authenticated": True,
                "org": "acme-corp",
                "role": "admin",
                "plan": "team",
                "label": "acme-corp"
            }
        }
    }


class GithubStatusResponse(BaseModel):
    """GitHub connection status."""
    connected: bool = Field(..., description="Whether GitHub is connected")
    repo: str | None = Field(default=None, description="Connected repository (owner/repo)")
    mode: str = Field(..., description="Mode: 'live' or 'offline'")
    full_name: str | None = Field(default=None, description="Full repository name")
    private: bool | None = Field(default=None, description="Whether the repository is private")

    model_config = {
        "json_schema_extra": {
            "example": {
                "connected": True,
                "repo": "acme/api-server",
                "mode": "live",
                "full_name": "acme/api-server",
                "private": True
            }
        }
    }


class ModelConfigResponse(BaseModel):
    """Response after configuring the LLM model."""
    provider: str = Field(..., description="Active LLM provider")
    model: str = Field(..., description="Primary model")
    fast_model: str = Field(..., description="Fast model")

    model_config = {
        "json_schema_extra": {
            "example": {
                "provider": "anthropic",
                "model": "claude-sonnet-4-20250514",
                "fast_model": "claude-haiku-4-20250514"
            }
        }
    }


class UsageResponse(BaseModel):
    """Usage statistics and quota information."""
    multi_tenant: bool | None = Field(default=None, description="Whether multi-tenant mode is active")
    detail: str | None = Field(default=None, description="Detail message if not applicable")
    plan: str | None = Field(default=None, description="Current plan tier")
    period: str | None = Field(default=None, description="Current billing period")
    investigations_used: int | None = Field(default=None, description="Investigations used this period")
    investigations_quota: int | None = Field(default=None, description="Total investigation quota")
    investigations_remaining: int | None = Field(default=None, description="Remaining investigations")
    tokens_used: int | None = Field(default=None, description="Total tokens consumed")
    warning: bool | None = Field(default=None, description="Whether quota warning threshold reached")

    model_config = {
        "json_schema_extra": {
            "example": {
                "multi_tenant": True,
                "plan": "team",
                "period": "2024-03",
                "investigations_used": 45,
                "investigations_quota": 100,
                "investigations_remaining": 55,
                "tokens_used": 125000,
                "warning": False
            }
        }
    }


class FeedbackResponse(BaseModel):
    """Response after submitting feedback."""
    status: str = Field(..., description="Feedback submission status")
    feedback: dict = Field(..., description="Recorded feedback entry")

    model_config = {
        "json_schema_extra": {
            "example": {
                "status": "recorded",
                "feedback": {
                    "thread_id": "inv-abc123",
                    "rating": "up",
                    "comment": "Accurate root cause identification",
                    "ts": "2024-03-15T10:30:00Z"
                }
            }
        }
    }


class AuditResponse(BaseModel):
    """Audit trail events."""
    events: list[dict] = Field(..., description="List of audit events")

    model_config = {
        "json_schema_extra": {
            "example": {
                "events": [{
                    "ts": "2024-03-15T10:30:00Z",
                    "event": "approval.decided",
                    "org_id": "acme-corp",
                    "actor": "user@acme.com",
                    "thread": "inv-abc123",
                    "approved": True
                }]
            }
        }
    }


class AuditVerifyResponse(BaseModel):
    """Audit chain integrity verification result."""
    valid: bool = Field(..., description="Whether the audit chain is intact")
    count: int | None = Field(default=None, description="Number of verified entries")
    broken_at: int | None = Field(default=None, description="Index of first broken entry if invalid")

    model_config = {
        "json_schema_extra": {
            "example": {
                "valid": True,
                "count": 1542
            }
        }
    }


class MetricsResponse(BaseModel):
    """Real-time metrics from configured sources."""
    services: dict = Field(..., description="Metrics by service and metric name")
    error_summary: dict = Field(..., description="Error counts and breakdown")

    model_config = {
        "json_schema_extra": {
            "example": {
                "services": {
                    "api-server": {
                        "latency_p99": [{"ts": "2024-03-15T10:00:00Z", "value": 125.5}],
                        "error_rate": [{"ts": "2024-03-15T10:00:00Z", "value": 0.02}]
                    }
                },
                "error_summary": {
                    "total_errors": 523,
                    "breakdown": [
                        {"message": "Connection timeout", "count": 312},
                        {"message": "Memory limit exceeded", "count": 211}
                    ]
                }
            }
        }
    }


class WebhookResponse(BaseModel):
    """Generic webhook acknowledgment response."""
    status: str = Field(..., description="Webhook processing status")
    incident: str | None = Field(default=None, description="Incident ID if applicable")
    event: str | None = Field(default=None, description="Event type if ignored")

    model_config = {
        "json_schema_extra": {
            "example": {
                "status": "accepted",
                "incident": "PD-12345"
            }
        }
    }


class SignupResponse(BaseModel):
    """Response after self-serve organization signup."""
    org_id: str = Field(..., description="Created organization ID")
    org_name: str = Field(..., description="Organization name")
    plan: str = Field(..., description="Assigned plan (always 'free' for signup)")
    role: str = Field(..., description="User role (always 'owner' for signup)")
    api_key: str = Field(..., description="API key for authentication (shown only once)")
    note: str = Field(..., description="Important note about storing the API key")

    model_config = {
        "json_schema_extra": {
            "example": {
                "org_id": "org_abc123",
                "org_name": "Acme Corp",
                "plan": "free",
                "role": "owner",
                "api_key": "dcp_xxxxxxxxxxxxxxxxxxxx",
                "note": "Store this key now - it is shown only once. Use it as a Bearer token."
            }
        }
    }


class AdminOrgResponse(BaseModel):
    """Organization details for admin view."""
    id: str = Field(..., description="Organization ID")
    name: str = Field(..., description="Organization name")
    plan: str = Field(..., description="Current plan tier")
    members: int = Field(..., description="Number of members")
    active_api_keys: int = Field(..., description="Number of active API keys")
    integrations: int = Field(..., description="Number of configured integrations")

    model_config = {
        "json_schema_extra": {
            "example": {
                "id": "org_abc123",
                "name": "Acme Corp",
                "plan": "team",
                "members": 5,
                "active_api_keys": 3,
                "integrations": 4
            }
        }
    }


class ApiKeyResponse(BaseModel):
    """API key information (without the secret)."""
    id: str = Field(..., description="API key ID")
    prefix: str = Field(..., description="Key prefix for identification")
    name: str = Field(..., description="Human-readable key name")
    role: str = Field(..., description="Associated role")
    created_at: str = Field(..., description="Creation timestamp")
    last_used_at: str = Field(..., description="Last usage timestamp")
    active: bool = Field(..., description="Whether the key is active")

    model_config = {
        "json_schema_extra": {
            "example": {
                "id": "key_abc123",
                "prefix": "dcp_abc",
                "name": "CI/CD Pipeline",
                "role": "responder",
                "created_at": "2024-03-01T00:00:00Z",
                "last_used_at": "2024-03-15T10:30:00Z",
                "active": True
            }
        }
    }


class ApiKeyListResponse(BaseModel):
    """List of API keys for an organization."""
    api_keys: list[ApiKeyResponse] = Field(..., description="List of API keys")


class ApiKeyCreateResponse(BaseModel):
    """Response after creating a new API key."""
    api_key: str = Field(..., description="The full API key (shown only once)")
    id: str = Field(..., description="API key ID")
    role: str = Field(..., description="Associated role")
    name: str = Field(..., description="Human-readable key name")
    note: str = Field(..., description="Important storage reminder")

    model_config = {
        "json_schema_extra": {
            "example": {
                "api_key": "dcp_xxxxxxxxxxxxxxxxxxxx",
                "id": "key_abc123",
                "role": "responder",
                "name": "CI/CD Pipeline",
                "note": "Store this now - the secret is shown only once."
            }
        }
    }


class RemediateResponse(BaseModel):
    """Response from autonomous remediation action."""
    status: str = Field(..., description="Remediation status (applied, reverted, dry_run, etc.)")
    action: str = Field(..., description="Action that was taken")
    target: str = Field(..., description="Target of the action")
    dry_run: bool = Field(..., description="Whether this was a dry run")
    result: dict | None = Field(default=None, description="Action result details")
    observation: dict | None = Field(default=None, description="Post-action observation")
    reverted: bool | None = Field(default=None, description="Whether action was auto-reverted")

    model_config = {
        "json_schema_extra": {
            "example": {
                "status": "applied",
                "action": "rollback_deployment",
                "target": "api-server",
                "dry_run": False,
                "result": {"previous_revision": 5, "current_revision": 4},
                "observation": {"burn_rate": 0.02, "verdict": "healthy"},
                "reverted": False
            }
        }
    }


class ErrorResponse(BaseModel):
    """Standard error response."""
    detail: str = Field(..., description="Error message")

    model_config = {
        "json_schema_extra": {
            "example": {
                "detail": "Missing or invalid API token."
            }
        }
    }
