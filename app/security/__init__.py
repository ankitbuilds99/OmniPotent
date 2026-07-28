"""Security module for DevOps Copilot.

Provides:
- API key authentication with rate limiting
- Secrets detection and prevention
- Security headers middleware
- Input validation and sanitization
- WebSocket authentication
- Request signing and replay protection
- Secret rotation management
"""

from app.security.api_auth import (
    APIKey,
    APIKeyAuth,
    create_api_key,
    list_api_keys,
    revoke_api_key,
    rotate_api_key,
    validate_api_key,
)
from app.security.headers import SecurityHeadersMiddleware
from app.security.input_validation import (
    ValidationResult,
    normalize_unicode,
    sanitize_for_logging,
    validate_github_repo,
    validate_string,
    validate_thread_id,
    validate_url,
)
from app.security.request_signing import (
    SignatureValidation,
    check_idempotency,
    compute_signature,
    parse_signature_header,
    record_idempotency_result,
    validate_signature,
)
from app.security.secret_rotation import (
    SecretRotationManager,
    get_rotation_manager,
)
from app.security.secrets import (
    SecretsScanner,
    get_secret_patterns,
    is_safe_to_commit,
    scan_for_secrets,
)
from app.security.websocket_auth import (
    WebSocketAuthResult,
    authenticate_websocket,
    extract_token_from_subprotocol,
    get_accepted_subprotocol,
    validate_websocket_message,
)
from app.security.session_binding import (
    BindingStrength,
    BindingVerificationResult,
    BoundSession,
    DeviceFingerprint,
    SessionBindingManager,
    get_session_binding_manager,
)

__all__ = [
    # API Auth
    "APIKeyAuth",
    "APIKey",
    "create_api_key",
    "validate_api_key",
    "revoke_api_key",
    "rotate_api_key",
    "list_api_keys",
    # Secrets Scanner
    "SecretsScanner",
    "scan_for_secrets",
    "is_safe_to_commit",
    "get_secret_patterns",
    # Headers
    "SecurityHeadersMiddleware",
    # Input Validation
    "ValidationResult",
    "validate_string",
    "validate_url",
    "validate_github_repo",
    "validate_thread_id",
    "normalize_unicode",
    "sanitize_for_logging",
    # Request Signing
    "SignatureValidation",
    "compute_signature",
    "validate_signature",
    "parse_signature_header",
    "check_idempotency",
    "record_idempotency_result",
    # Secret Rotation
    "SecretRotationManager",
    "get_rotation_manager",
    # WebSocket Auth
    "WebSocketAuthResult",
    "authenticate_websocket",
    "extract_token_from_subprotocol",
    "validate_websocket_message",
    "get_accepted_subprotocol",
    # Session Binding
    "BindingStrength",
    "BindingVerificationResult",
    "BoundSession",
    "DeviceFingerprint",
    "SessionBindingManager",
    "get_session_binding_manager",
]
