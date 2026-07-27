"""Audit logging system.

Provides:
- Comprehensive action tracking
- Compliance-ready audit trails
- Search and filtering
- Export capabilities

This module combines the legacy audit functions (record, verify_chain, recent, clear)
with the new advanced audit logging system (AuditLogger, log_action, etc.).
"""

from app.audit_legacy import (
    record,
    verify_chain,
    recent,
    clear,
    _BUFFER,
)

from app.audit.logger import (
    AuditLogger,
    AuditEntry,
    AuditAction,
    AuditCategory,
    AuditSeverity,
    log_action,
    log_auth_event,
    log_data_access,
    log_config_change,
    log_security_event,
    search_audit_logs,
    export_audit_logs,
    get_audit_summary,
)

__all__ = [
    # Legacy functions (for backwards compatibility)
    "record",
    "verify_chain",
    "recent",
    "clear",
    "_BUFFER",
    # New advanced audit system
    "AuditLogger",
    "AuditEntry",
    "AuditAction",
    "AuditCategory",
    "AuditSeverity",
    "log_action",
    "log_auth_event",
    "log_data_access",
    "log_config_change",
    "log_security_event",
    "search_audit_logs",
    "export_audit_logs",
    "get_audit_summary",
]
