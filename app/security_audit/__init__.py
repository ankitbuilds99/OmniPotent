"""Security audit module for code and configuration scanning.

Provides:
- Static code analysis for security vulnerabilities
- Secret detection in source code
- Dependency vulnerability scanning
- OWASP Top 10 pattern detection
"""

from app.security_audit.auditor import (
    SecurityAuditor,
    Finding,
    AuditResult,
    create_auditor,
)

__all__ = [
    "SecurityAuditor",
    "Finding",
    "AuditResult",
    "create_auditor",
]
