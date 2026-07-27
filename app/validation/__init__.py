"""Request validation and sanitization module.

Provides:
- Pydantic models for all API requests/responses
- Input sanitization against injection attacks
- Rate limit configuration per endpoint
- Schema validation with detailed errors
"""

from app.validation.models import (
    ChatRequest,
    ChatResponse,
    ApproveRequest,
    ApproveResponse,
    InvestigationReport,
    ErrorResponse,
    PaginatedResponse,
    HealthResponse,
    ConfigResponse,
)
from app.validation.sanitizer import (
    sanitize_input,
    sanitize_query,
    validate_thread_id,
    validate_email,
    validate_api_key_format,
    SanitizationError,
)

__all__ = [
    # Request/Response models
    "ChatRequest",
    "ChatResponse",
    "ApproveRequest",
    "ApproveResponse",
    "InvestigationReport",
    "ErrorResponse",
    "PaginatedResponse",
    "HealthResponse",
    "ConfigResponse",
    # Sanitization
    "sanitize_input",
    "sanitize_query",
    "validate_thread_id",
    "validate_email",
    "validate_api_key_format",
    "SanitizationError",
]
