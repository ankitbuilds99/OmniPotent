"""Error handling and resilience module.

Provides:
- Standardized exception hierarchy
- Retry decorators with exponential backoff
- Circuit breaker pattern
- Graceful degradation
- Error classification and user-friendly messages
"""

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
from app.error_handling.retry import (
    with_retry,
    async_with_retry,
    RetryConfig,
)
from app.error_handling.circuit_breaker import (
    CircuitBreaker,
    CircuitState,
    CircuitOpenError,
)
from app.error_handling.handlers import (
    handle_exception,
    to_user_message,
    classify_error,
    ErrorClassification,
)

__all__ = [
    # Exceptions
    "CopilotError",
    "ValidationError",
    "AuthenticationError",
    "AuthorizationError",
    "RateLimitError",
    "ExternalServiceError",
    "MCPError",
    "LLMError",
    "TimeoutError",
    "ConfigurationError",
    "ResourceNotFoundError",
    "ConflictError",
    # Retry
    "with_retry",
    "async_with_retry",
    "RetryConfig",
    # Circuit breaker
    "CircuitBreaker",
    "CircuitState",
    "CircuitOpenError",
    # Handlers
    "handle_exception",
    "to_user_message",
    "classify_error",
    "ErrorClassification",
]
