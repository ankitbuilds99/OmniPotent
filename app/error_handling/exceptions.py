"""Custom exception hierarchy for DevOps Copilot.

All application-specific exceptions inherit from CopilotError.
This allows consistent error handling and HTTP status mapping.
"""

from __future__ import annotations

from typing import Any


class CopilotError(Exception):
    """Base exception for all DevOps Copilot errors.

    Attributes:
        message: Human-readable error message
        code: Machine-readable error code
        status_code: HTTP status code to return
        details: Additional error context
        retryable: Whether the operation can be retried
    """

    def __init__(
        self,
        message: str,
        code: str = "INTERNAL_ERROR",
        status_code: int = 500,
        details: dict[str, Any] | None = None,
        retryable: bool = False,
    ):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code
        self.details = details or {}
        self.retryable = retryable

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "error": self.code,
            "message": self.message,
            "details": self.details,
            "retryable": self.retryable,
        }


class ValidationError(CopilotError):
    """Input validation failed."""

    def __init__(
        self,
        message: str,
        field: str | None = None,
        details: dict[str, Any] | None = None,
    ):
        details = details or {}
        if field:
            details["field"] = field
        super().__init__(
            message=message,
            code="VALIDATION_ERROR",
            status_code=400,
            details=details,
            retryable=False,
        )
        self.field = field


class AuthenticationError(CopilotError):
    """Authentication failed (invalid/missing credentials)."""

    def __init__(
        self,
        message: str = "Authentication required",
        details: dict[str, Any] | None = None,
    ):
        super().__init__(
            message=message,
            code="AUTHENTICATION_ERROR",
            status_code=401,
            details=details,
            retryable=False,
        )


class AuthorizationError(CopilotError):
    """Authorization failed (insufficient permissions)."""

    def __init__(
        self,
        message: str = "Permission denied",
        required_permission: str | None = None,
        details: dict[str, Any] | None = None,
    ):
        details = details or {}
        if required_permission:
            details["required_permission"] = required_permission
        super().__init__(
            message=message,
            code="AUTHORIZATION_ERROR",
            status_code=403,
            details=details,
            retryable=False,
        )


class RateLimitError(CopilotError):
    """Rate limit exceeded."""

    def __init__(
        self,
        message: str = "Rate limit exceeded",
        retry_after: int = 60,
        details: dict[str, Any] | None = None,
    ):
        details = details or {}
        details["retry_after_seconds"] = retry_after
        super().__init__(
            message=message,
            code="RATE_LIMIT_ERROR",
            status_code=429,
            details=details,
            retryable=True,
        )
        self.retry_after = retry_after


class ResourceNotFoundError(CopilotError):
    """Requested resource was not found."""

    def __init__(
        self,
        message: str = "Resource not found",
        resource_type: str | None = None,
        resource_id: str | None = None,
        details: dict[str, Any] | None = None,
    ):
        details = details or {}
        if resource_type:
            details["resource_type"] = resource_type
        if resource_id:
            details["resource_id"] = resource_id
        super().__init__(
            message=message,
            code="NOT_FOUND",
            status_code=404,
            details=details,
            retryable=False,
        )


class ConflictError(CopilotError):
    """Resource conflict (e.g., thread in wrong state)."""

    def __init__(
        self,
        message: str = "Resource conflict",
        details: dict[str, Any] | None = None,
    ):
        super().__init__(
            message=message,
            code="CONFLICT",
            status_code=409,
            details=details,
            retryable=False,
        )


class ExternalServiceError(CopilotError):
    """External service (Datadog, GitHub, etc.) failed."""

    def __init__(
        self,
        message: str,
        service: str,
        original_error: Exception | None = None,
        details: dict[str, Any] | None = None,
    ):
        details = details or {}
        details["service"] = service
        if original_error:
            details["original_error"] = str(original_error)
        super().__init__(
            message=message,
            code="EXTERNAL_SERVICE_ERROR",
            status_code=502,
            details=details,
            retryable=True,
        )
        self.service = service
        self.original_error = original_error


class MCPError(ExternalServiceError):
    """MCP server communication error."""

    def __init__(
        self,
        message: str,
        server: str,
        tool: str | None = None,
        original_error: Exception | None = None,
        details: dict[str, Any] | None = None,
    ):
        details = details or {}
        details["mcp_server"] = server
        if tool:
            details["tool"] = tool
        super().__init__(
            message=message,
            service=f"mcp:{server}",
            original_error=original_error,
            details=details,
        )
        self.server = server
        self.tool = tool


class LLMError(ExternalServiceError):
    """LLM provider error."""

    def __init__(
        self,
        message: str,
        provider: str,
        model: str | None = None,
        original_error: Exception | None = None,
        details: dict[str, Any] | None = None,
    ):
        details = details or {}
        details["provider"] = provider
        if model:
            details["model"] = model
        super().__init__(
            message=message,
            service=f"llm:{provider}",
            original_error=original_error,
            details=details,
        )
        self.provider = provider
        self.model = model


class TimeoutError(CopilotError):
    """Operation timed out."""

    def __init__(
        self,
        message: str = "Operation timed out",
        operation: str | None = None,
        timeout_seconds: float | None = None,
        details: dict[str, Any] | None = None,
    ):
        details = details or {}
        if operation:
            details["operation"] = operation
        if timeout_seconds:
            details["timeout_seconds"] = timeout_seconds
        super().__init__(
            message=message,
            code="TIMEOUT",
            status_code=504,
            details=details,
            retryable=True,
        )
        self.operation = operation
        self.timeout_seconds = timeout_seconds


class ConfigurationError(CopilotError):
    """Configuration error (missing env vars, invalid config)."""

    def __init__(
        self,
        message: str,
        config_key: str | None = None,
        details: dict[str, Any] | None = None,
    ):
        details = details or {}
        if config_key:
            details["config_key"] = config_key
        super().__init__(
            message=message,
            code="CONFIGURATION_ERROR",
            status_code=500,
            details=details,
            retryable=False,
        )
        self.config_key = config_key
