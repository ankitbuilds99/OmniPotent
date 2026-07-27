"""Input sanitization and validation utilities.

Provides defense against:
- SQL injection
- XSS attacks
- Command injection
- Path traversal
- Prompt injection (basic)
"""

from __future__ import annotations

import html
import logging
import re
import unicodedata
from typing import Any

log = logging.getLogger("devcopilot.validation.sanitizer")


class SanitizationError(Exception):
    """Raised when input fails sanitization."""

    def __init__(self, message: str, field: str | None = None):
        super().__init__(message)
        self.field = field
        self.message = message


DANGEROUS_PATTERNS = [
    re.compile(r"[<>'\";]"),
    re.compile(r"(--|;|\/\*|\*\/)"),
    re.compile(r"(\bunion\b|\bselect\b|\binsert\b|\bdelete\b|\bdrop\b)", re.I),
    re.compile(r"[`$]"),
    re.compile(r"\.\.\/|\.\.\\"),
    re.compile(r"(%00|%0a|%0d)", re.I),
]

PROMPT_INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(previous|above|all)\s+instructions?", re.I),
    re.compile(r"(system|assistant):\s*", re.I),
    re.compile(r"<\|?(system|user|assistant)\|?>", re.I),
    re.compile(r"\\n\\n(Human|Assistant):", re.I),
    re.compile(r"STOP|END|TERMINATE", re.I),
]

THREAD_ID_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,62}[a-zA-Z0-9]$|^[a-zA-Z0-9]$")
EMAIL_PATTERN = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")
API_KEY_PATTERN = re.compile(r"^dcp_[a-zA-Z0-9_-]{20,64}$")


def sanitize_input(
    value: str,
    max_length: int = 10000,
    allow_newlines: bool = True,
    strip_html: bool = True,
    check_injection: bool = True,
    field_name: str = "input",
) -> str:
    """Sanitize a string input.

    Args:
        value: The input string to sanitize
        max_length: Maximum allowed length
        allow_newlines: Whether to preserve newlines
        strip_html: Whether to escape HTML entities
        check_injection: Whether to check for injection patterns
        field_name: Field name for error messages

    Returns:
        Sanitized string

    Raises:
        SanitizationError: If input fails validation
    """
    if not isinstance(value, str):
        raise SanitizationError(f"{field_name} must be a string", field_name)

    value = unicodedata.normalize("NFKC", value)

    value = "".join(char for char in value if char.isprintable() or char in "\n\r\t")

    if not allow_newlines:
        value = value.replace("\n", " ").replace("\r", " ")

    value = re.sub(r"[ \t]+", " ", value)
    value = value.strip()

    if len(value) > max_length:
        raise SanitizationError(
            f"{field_name} exceeds maximum length of {max_length}",
            field_name,
        )

    if strip_html:
        value = html.escape(value)

    if check_injection:
        for pattern in DANGEROUS_PATTERNS:
            if pattern.search(value):
                log.warning(
                    "Potential injection detected in %s: %s",
                    field_name,
                    value[:100],
                )
                raise SanitizationError(
                    f"{field_name} contains potentially dangerous characters",
                    field_name,
                )

    return value


def sanitize_query(
    query: str,
    max_length: int = 10000,
    check_prompt_injection: bool = True,
) -> str:
    """Sanitize an investigation query.

    More permissive than sanitize_input since queries may contain
    code snippets, error messages, etc.

    Args:
        query: The query string to sanitize
        max_length: Maximum allowed length
        check_prompt_injection: Whether to check for prompt injection

    Returns:
        Sanitized query string

    Raises:
        SanitizationError: If query fails validation
    """
    if not isinstance(query, str):
        raise SanitizationError("Query must be a string", "query")

    query = unicodedata.normalize("NFKC", query)
    query = "".join(char for char in query if char.isprintable() or char in "\n\r\t")
    query = query.strip()

    if not query:
        raise SanitizationError("Query cannot be empty", "query")

    if len(query) > max_length:
        raise SanitizationError(
            f"Query exceeds maximum length of {max_length}",
            "query",
        )

    if check_prompt_injection:
        for pattern in PROMPT_INJECTION_PATTERNS:
            if pattern.search(query):
                log.warning("Potential prompt injection detected: %s", query[:100])
                from app import audit_legacy as audit
                audit.record(
                    "security.prompt_injection_detected",
                    pattern=pattern.pattern,
                    query_preview=query[:200],
                )

    return query


def validate_thread_id(thread_id: str) -> str:
    """Validate and sanitize a thread ID.

    Args:
        thread_id: The thread ID to validate

    Returns:
        Validated thread ID

    Raises:
        SanitizationError: If thread ID is invalid
    """
    if not isinstance(thread_id, str):
        raise SanitizationError("Thread ID must be a string", "thread_id")

    thread_id = thread_id.strip()

    if not thread_id:
        raise SanitizationError("Thread ID cannot be empty", "thread_id")

    if len(thread_id) > 64:
        raise SanitizationError("Thread ID exceeds maximum length of 64", "thread_id")

    if not THREAD_ID_PATTERN.match(thread_id):
        raise SanitizationError(
            "Thread ID must contain only alphanumeric characters, hyphens, and underscores",
            "thread_id",
        )

    return thread_id


def validate_email(email: str) -> str:
    """Validate an email address.

    Args:
        email: The email to validate

    Returns:
        Validated and normalized email

    Raises:
        SanitizationError: If email is invalid
    """
    if not isinstance(email, str):
        raise SanitizationError("Email must be a string", "email")

    email = email.strip().lower()

    if not email:
        raise SanitizationError("Email cannot be empty", "email")

    if len(email) > 254:
        raise SanitizationError("Email exceeds maximum length", "email")

    if not EMAIL_PATTERN.match(email):
        raise SanitizationError("Invalid email format", "email")

    return email


def validate_api_key_format(api_key: str) -> str:
    """Validate API key format (not the key itself).

    Args:
        api_key: The API key to validate

    Returns:
        Validated API key

    Raises:
        SanitizationError: If format is invalid
    """
    if not isinstance(api_key, str):
        raise SanitizationError("API key must be a string", "api_key")

    api_key = api_key.strip()

    if not api_key:
        raise SanitizationError("API key cannot be empty", "api_key")

    if not API_KEY_PATTERN.match(api_key):
        raise SanitizationError(
            "Invalid API key format (expected dcp_...)",
            "api_key",
        )

    return api_key


def sanitize_dict(
    data: dict[str, Any],
    max_depth: int = 10,
    max_string_length: int = 10000,
    _current_depth: int = 0,
) -> dict[str, Any]:
    """Recursively sanitize a dictionary.

    Args:
        data: Dictionary to sanitize
        max_depth: Maximum nesting depth
        max_string_length: Maximum string length
        _current_depth: Internal tracking of current depth

    Returns:
        Sanitized dictionary

    Raises:
        SanitizationError: If validation fails
    """
    if _current_depth > max_depth:
        raise SanitizationError("Data structure too deeply nested", "data")

    result: dict[str, Any] = {}

    for key, value in data.items():
        if not isinstance(key, str):
            raise SanitizationError(f"Dictionary key must be string, got {type(key)}", "key")

        sanitized_key = sanitize_input(
            key,
            max_length=256,
            allow_newlines=False,
            check_injection=True,
            field_name=f"key:{key}",
        )

        if isinstance(value, str):
            result[sanitized_key] = sanitize_input(
                value,
                max_length=max_string_length,
                check_injection=False,
                field_name=f"value:{key}",
            )
        elif isinstance(value, dict):
            result[sanitized_key] = sanitize_dict(
                value,
                max_depth=max_depth,
                max_string_length=max_string_length,
                _current_depth=_current_depth + 1,
            )
        elif isinstance(value, list):
            result[sanitized_key] = [
                sanitize_dict(item, max_depth, max_string_length, _current_depth + 1)
                if isinstance(item, dict)
                else sanitize_input(str(item), max_length=max_string_length)
                if isinstance(item, str)
                else item
                for item in value
            ]
        else:
            result[sanitized_key] = value

    return result


def redact_sensitive_data(
    data: dict[str, Any],
    sensitive_keys: set[str] | None = None,
) -> dict[str, Any]:
    """Redact sensitive fields from a dictionary for logging.

    Args:
        data: Dictionary to redact
        sensitive_keys: Keys to redact (defaults to common sensitive keys)

    Returns:
        Redacted dictionary (copy)
    """
    if sensitive_keys is None:
        sensitive_keys = {
            "password",
            "token",
            "api_key",
            "apikey",
            "secret",
            "credential",
            "authorization",
            "auth",
            "key",
            "private",
        }

    def _redact(obj: Any, key: str = "") -> Any:
        if isinstance(obj, dict):
            return {
                k: _redact(v, k.lower())
                for k, v in obj.items()
            }
        elif isinstance(obj, list):
            return [_redact(item) for item in obj]
        elif isinstance(obj, str) and any(s in key for s in sensitive_keys):
            if len(obj) > 8:
                return obj[:4] + "..." + obj[-4:]
            return "***"
        return obj

    return _redact(data)
