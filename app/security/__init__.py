"""Security module for DevOps Copilot.

Provides:
- API key authentication with rate limiting
- Secrets detection and prevention
- Security headers middleware
- Input validation and sanitization
"""

from app.security.api_auth import (
    APIKeyAuth,
    APIKey,
    create_api_key,
    validate_api_key,
    revoke_api_key,
    rotate_api_key,
    list_api_keys,
)
from app.security.secrets import (
    SecretsScanner,
    scan_for_secrets,
    is_safe_to_commit,
    get_secret_patterns,
)
from app.security.headers import SecurityHeadersMiddleware

__all__ = [
    "APIKeyAuth",
    "APIKey",
    "create_api_key",
    "validate_api_key",
    "revoke_api_key",
    "rotate_api_key",
    "list_api_keys",
    "SecretsScanner",
    "scan_for_secrets",
    "is_safe_to_commit",
    "get_secret_patterns",
    "SecurityHeadersMiddleware",
]
