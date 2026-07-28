"""Advanced input validation and sanitization.

Defense-in-depth validation for all untrusted input:
- Type coercion attacks
- Unicode normalization bypass
- Path traversal
- Command injection
- SQL injection patterns
- SSRF protection
"""

from __future__ import annotations

import ipaddress
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from urllib.parse import urlparse

log = logging.getLogger("devcopilot.security.input_validation")


class ValidationSeverity(Enum):
    """Severity of validation failure."""

    BLOCK = "block"
    SANITIZE = "sanitize"
    WARN = "warn"


@dataclass
class ValidationResult:
    """Result of input validation."""

    valid: bool
    sanitized_value: str | None = None
    violations: list[str] = field(default_factory=list)
    severity: ValidationSeverity = ValidationSeverity.BLOCK


_SQL_INJECTION_PATTERNS = [
    r"(\b(SELECT|INSERT|UPDATE|DELETE|DROP|UNION|ALTER|CREATE|TRUNCATE)\b)",
    r"(--|\#|/\*)",
    r"(\bOR\b\s+\d+\s*=\s*\d+)",
    r"(\bAND\b\s+\d+\s*=\s*\d+)",
    r"(;\s*(SELECT|INSERT|DROP))",
    r"(\bEXEC\b|\bEXECUTE\b)",
    r"(\bxp_\w+)",
    r"(WAITFOR\s+DELAY)",
    r"(BENCHMARK\s*\()",
]

_COMMAND_INJECTION_PATTERNS = [
    r"(;|\||&&|\$\(|\`)",
    r"(\b(cat|ls|rm|chmod|chown|wget|curl|nc|bash|sh|python|perl|ruby)\b)",
    r"(>\s*/dev/|<\s*/etc/)",
    r"(\.\./)",
    r"(%00|%0a|%0d)",
]

_PATH_TRAVERSAL_PATTERNS = [
    r"(\.\.[\\/])",
    r"(%2e%2e[\\/])",
    r"(\.\.%2f)",
    r"(%252e%252e)",
    r"(\.\.%c0%af)",
    r"(/etc/passwd|/etc/shadow|/proc/)",
    r"(c:\\windows|c:\\system32)",
]

_SSRF_BLOCKED_HOSTS = {
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "[::]",
    "[::1]",
    "metadata.google.internal",
    "169.254.169.254",
    "metadata.azure.com",
}

_SSRF_BLOCKED_SCHEMES = {"file", "ftp", "gopher", "dict", "ldap", "tftp"}


def normalize_unicode(text: str) -> str:
    """Normalize Unicode to prevent bypass attacks."""
    if not text:
        return text

    normalized = unicodedata.normalize("NFKC", text)

    cleaned = "".join(
        c
        for c in normalized
        if unicodedata.category(c) not in ("Cf", "Cc", "Co", "Cs") or c in "\n\r\t "
    )

    return cleaned


def validate_string(
    value: str,
    max_length: int = 10000,
    allow_newlines: bool = True,
    allow_html: bool = False,
    context: str = "general",
) -> ValidationResult:
    """Validate a string input."""
    violations = []

    if not isinstance(value, str):
        return ValidationResult(
            valid=False,
            violations=["Value must be a string"],
            severity=ValidationSeverity.BLOCK,
        )

    if len(value) > max_length:
        violations.append(f"Exceeds maximum length ({len(value)} > {max_length})")

    sanitized = normalize_unicode(value)

    if not allow_newlines:
        sanitized = sanitized.replace("\n", " ").replace("\r", " ")

    if not allow_html:
        sanitized = re.sub(r"<script[^>]*>.*?</script>", "", sanitized, flags=re.I | re.S)
        sanitized = re.sub(r"\bon\w+\s*=", "", sanitized, flags=re.I)

    if context == "sql":
        for pattern in _SQL_INJECTION_PATTERNS:
            if re.search(pattern, sanitized, re.I):
                violations.append(f"SQL injection pattern detected: {pattern[:30]}")

    elif context == "command":
        for pattern in _COMMAND_INJECTION_PATTERNS:
            if re.search(pattern, sanitized, re.I):
                violations.append("Command injection pattern detected")

    elif context == "path":
        for pattern in _PATH_TRAVERSAL_PATTERNS:
            if re.search(pattern, sanitized, re.I):
                violations.append("Path traversal pattern detected")

    if violations:
        return ValidationResult(
            valid=False,
            sanitized_value=sanitized,
            violations=violations,
            severity=ValidationSeverity.BLOCK,
        )

    return ValidationResult(valid=True, sanitized_value=sanitized)


def validate_url(
    url: str,
    allowed_schemes: set[str] | None = None,
    allowed_hosts: set[str] | None = None,
    block_internal: bool = True,
) -> ValidationResult:
    """Validate a URL for SSRF protection."""
    violations = []
    allowed_schemes = allowed_schemes or {"https", "http"}

    try:
        parsed = urlparse(url)
    except Exception:
        return ValidationResult(valid=False, violations=["Invalid URL format"])

    if parsed.scheme.lower() not in allowed_schemes:
        violations.append(f"Scheme not allowed: {parsed.scheme}")

    if parsed.scheme.lower() in _SSRF_BLOCKED_SCHEMES:
        violations.append(f"Dangerous scheme blocked: {parsed.scheme}")

    host = parsed.hostname or ""
    host_lower = host.lower()

    if host_lower in _SSRF_BLOCKED_HOSTS:
        violations.append(f"Internal host blocked: {host}")

    if block_internal:
        try:
            ip = ipaddress.ip_address(host)
            if ip.is_private or ip.is_loopback or ip.is_reserved:
                violations.append(f"Private/internal IP blocked: {host}")
            if ip.is_link_local:
                violations.append(f"Link-local IP blocked: {host}")
        except ValueError:
            pass

    if allowed_hosts and host_lower not in allowed_hosts:
        violations.append(f"Host not in allowlist: {host}")

    if re.search(r"\d+\.\d+\.\d+\.\d+\.xip\.io", host_lower):
        violations.append("DNS rebinding pattern detected")

    if violations:
        return ValidationResult(valid=False, sanitized_value=url, violations=violations)

    return ValidationResult(valid=True, sanitized_value=url)


def validate_github_repo(repo: str) -> ValidationResult:
    """Validate a GitHub repository identifier."""
    violations = []

    cleaned = repo.strip()
    for prefix in [
        "https://github.com/",
        "http://github.com/",
        "github.com/",
        "git@github.com:",
    ]:
        if cleaned.lower().startswith(prefix.lower()):
            cleaned = cleaned[len(prefix) :]
            break

    cleaned = cleaned.rstrip("/").removesuffix(".git")

    if "/" not in cleaned:
        violations.append("Repository must be in owner/repo format")
    else:
        parts = cleaned.split("/")
        if len(parts) != 2:
            violations.append("Repository must be in owner/repo format")
        else:
            owner, name = parts
            if not re.match(r"^[a-zA-Z0-9]([a-zA-Z0-9\-]*[a-zA-Z0-9])?$", owner):
                violations.append("Invalid owner name")
            if not re.match(r"^[a-zA-Z0-9._\-]+$", name):
                violations.append("Invalid repository name")
            if len(owner) > 39 or len(name) > 100:
                violations.append("Owner or repository name too long")

    path_result = validate_string(cleaned, context="path")
    if not path_result.valid:
        violations.extend(path_result.violations)

    if violations:
        return ValidationResult(valid=False, sanitized_value=cleaned, violations=violations)

    return ValidationResult(valid=True, sanitized_value=cleaned)


def validate_thread_id(thread_id: str) -> ValidationResult:
    """Validate a thread/session ID."""
    violations = []

    if not thread_id or not isinstance(thread_id, str):
        violations.append("Thread ID is required")
    elif len(thread_id) > 256:
        violations.append("Thread ID too long")
    elif not re.match(r"^[a-zA-Z0-9_\-:.]+$", thread_id):
        violations.append("Thread ID contains invalid characters")

    if ".." in thread_id or "/" in thread_id or "\\" in thread_id:
        violations.append("Thread ID contains path traversal characters")

    if violations:
        return ValidationResult(valid=False, violations=violations)

    return ValidationResult(valid=True, sanitized_value=thread_id)


def sanitize_for_logging(value: str, max_length: int = 500) -> str:
    """Sanitize a value for safe logging."""
    if not value:
        return ""

    safe = value.replace("\n", "\\n").replace("\r", "\\r")
    safe = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", safe)

    if len(safe) > max_length:
        safe = safe[:max_length] + "...[truncated]"

    return safe
