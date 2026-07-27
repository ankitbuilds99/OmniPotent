"""Error handling utilities and user message formatting.

Provides:
- Error classification for telemetry
- User-friendly error message generation
- Exception to HTTP response conversion
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any

from app.error_handling.exceptions import (
    CopilotError,
    ValidationError,
    AuthenticationError,
    AuthorizationError,
    RateLimitError,
    ExternalServiceError,
    MCPError,
    LLMError,
    TimeoutError,
    ConfigurationError,
    ResourceNotFoundError,
    ConflictError,
)

log = logging.getLogger("devcopilot.error_handling.handlers")


class ErrorClassification(str, Enum):
    """Error classification for metrics/telemetry."""

    USER_ERROR = "user_error"
    AUTH_ERROR = "auth_error"
    RATE_LIMIT = "rate_limit"
    EXTERNAL_SERVICE = "external_service"
    LLM_ERROR = "llm_error"
    MCP_ERROR = "mcp_error"
    TIMEOUT = "timeout"
    CONFIG_ERROR = "config_error"
    INTERNAL_ERROR = "internal_error"
    UNKNOWN = "unknown"


@dataclass
class ErrorContext:
    """Context for error handling decisions."""

    classification: ErrorClassification
    status_code: int
    retryable: bool
    user_message: str
    log_level: str
    details: dict[str, Any]


def classify_error(exception: Exception) -> ErrorClassification:
    """Classify an exception for telemetry/metrics.

    Args:
        exception: The exception to classify

    Returns:
        ErrorClassification enum value
    """
    if isinstance(exception, ValidationError):
        return ErrorClassification.USER_ERROR
    if isinstance(exception, AuthenticationError):
        return ErrorClassification.AUTH_ERROR
    if isinstance(exception, AuthorizationError):
        return ErrorClassification.AUTH_ERROR
    if isinstance(exception, RateLimitError):
        return ErrorClassification.RATE_LIMIT
    if isinstance(exception, LLMError):
        return ErrorClassification.LLM_ERROR
    if isinstance(exception, MCPError):
        return ErrorClassification.MCP_ERROR
    if isinstance(exception, ExternalServiceError):
        return ErrorClassification.EXTERNAL_SERVICE
    if isinstance(exception, TimeoutError):
        return ErrorClassification.TIMEOUT
    if isinstance(exception, ConfigurationError):
        return ErrorClassification.CONFIG_ERROR
    if isinstance(exception, (ResourceNotFoundError, ConflictError)):
        return ErrorClassification.USER_ERROR
    if isinstance(exception, CopilotError):
        return ErrorClassification.INTERNAL_ERROR

    exc_text = str(exception).lower()
    if "rate_limit" in exc_text or "429" in exc_text:
        return ErrorClassification.RATE_LIMIT
    if "timeout" in exc_text or "timed out" in exc_text:
        return ErrorClassification.TIMEOUT
    if "authentication" in exc_text or "401" in exc_text:
        return ErrorClassification.AUTH_ERROR
    if "connection" in exc_text or "network" in exc_text:
        return ErrorClassification.EXTERNAL_SERVICE

    return ErrorClassification.UNKNOWN


USER_MESSAGES = {
    ErrorClassification.USER_ERROR: "The request could not be processed. Please check your input and try again.",
    ErrorClassification.AUTH_ERROR: "Authentication failed. Please check your credentials.",
    ErrorClassification.RATE_LIMIT: "Too many requests. Please wait a moment and try again.",
    ErrorClassification.EXTERNAL_SERVICE: "A connected service is temporarily unavailable. Please try again.",
    ErrorClassification.LLM_ERROR: "The AI service encountered an issue. Please try again.",
    ErrorClassification.MCP_ERROR: "A data source is temporarily unavailable. The investigation may be incomplete.",
    ErrorClassification.TIMEOUT: "The operation took too long. Please try again with a simpler query.",
    ErrorClassification.CONFIG_ERROR: "There's a configuration issue. Please contact support.",
    ErrorClassification.INTERNAL_ERROR: "Something went wrong. Please try again.",
    ErrorClassification.UNKNOWN: "An unexpected error occurred. Please try again.",
}


def to_user_message(exception: Exception) -> str:
    """Convert an exception to a user-friendly message.

    Never exposes internal details, stack traces, or sensitive information.

    Args:
        exception: The exception to convert

    Returns:
        User-friendly error message
    """
    if isinstance(exception, CopilotError):
        if not _contains_sensitive_info(exception.message):
            return exception.message

    classification = classify_error(exception)

    exc_text = str(exception).lower()

    if "rate_limit" in exc_text or "429" in exc_text:
        return (
            "The LLM provider's rate limit was reached. "
            "Please try again in a few moments or check your plan/quota."
        )

    if "authentication" in exc_text or "401" in exc_text or "api_key" in exc_text:
        return (
            "The API key was rejected. Please check your credentials "
            "for the configured provider."
        )

    if "timeout" in exc_text or "timed out" in exc_text:
        return "The request timed out. Please try again with a simpler query."

    if "connection" in exc_text or "network" in exc_text:
        return "A network error occurred. Please check your connection and try again."

    if "not found" in exc_text:
        return "The requested resource was not found."

    if "permission" in exc_text or "forbidden" in exc_text or "403" in exc_text:
        return "You don't have permission to perform this action."

    return USER_MESSAGES.get(classification, USER_MESSAGES[ErrorClassification.UNKNOWN])


def _contains_sensitive_info(message: str) -> bool:
    """Check if a message might contain sensitive information."""
    sensitive_patterns = [
        "password",
        "token",
        "api_key",
        "secret",
        "credential",
        "internal",
        "traceback",
        "stack",
        "sql",
        "query",
        "/home/",
        "/usr/",
        "\\Users\\",
        "file://",
    ]
    message_lower = message.lower()
    return any(pattern in message_lower for pattern in sensitive_patterns)


def handle_exception(
    exception: Exception,
    request_id: str | None = None,
    include_details: bool = False,
) -> ErrorContext:
    """Process an exception and return handling context.

    Args:
        exception: The exception to handle
        request_id: Optional request ID for logging
        include_details: Whether to include technical details

    Returns:
        ErrorContext with all handling information
    """
    classification = classify_error(exception)
    user_message = to_user_message(exception)

    if isinstance(exception, CopilotError):
        status_code = exception.status_code
        retryable = exception.retryable
        details = exception.details if include_details else {}
    else:
        status_code = 500
        retryable = classification in (
            ErrorClassification.RATE_LIMIT,
            ErrorClassification.EXTERNAL_SERVICE,
            ErrorClassification.TIMEOUT,
        )
        details = {}

    log_level = "warning" if status_code < 500 else "error"

    if request_id:
        details["request_id"] = request_id

    return ErrorContext(
        classification=classification,
        status_code=status_code,
        retryable=retryable,
        user_message=user_message,
        log_level=log_level,
        details=details,
    )


def log_exception(
    exception: Exception,
    context: ErrorContext,
    request_id: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Log an exception with appropriate level and context.

    Args:
        exception: The exception to log
        context: Error handling context
        request_id: Optional request ID
        extra: Additional context to log
    """
    log_data = {
        "classification": context.classification.value,
        "status_code": context.status_code,
        "retryable": context.retryable,
        **(extra or {}),
    }

    if request_id:
        log_data["request_id"] = request_id

    if isinstance(exception, CopilotError):
        log_data["error_code"] = exception.code
        log_data["error_details"] = exception.details

    message = f"Error: {context.user_message}"

    if context.log_level == "warning":
        log.warning(message, extra=log_data)
    else:
        log.error(message, extra=log_data, exc_info=True)
