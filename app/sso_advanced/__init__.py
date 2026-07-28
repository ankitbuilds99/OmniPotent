"""Advanced Enterprise SSO Features.

Goes beyond basic SAML with:
- SCIM 2.0 user provisioning
- Group and role mapping
- Comprehensive audit logging
- Session management and limits
- Just-in-time provisioning
"""

from app.sso_advanced.scim import (
    SCIMUser,
    SCIMGroup,
    SCIMProvisioningEvent,
    SCIMProvider,
    get_scim_provider,
)
from app.sso_advanced.group_mapping import (
    GroupMapping,
    RoleMapping,
    GroupSyncResult,
    GroupMapper,
    get_group_mapper,
)
from app.sso_advanced.audit import (
    AuditEvent,
    AuditEventType,
    AuditLog,
    SSOAuditLogger,
    get_sso_audit_logger,
)
from app.sso_advanced.session_management import (
    SessionPolicy,
    UserSession,
    SessionManager,
    get_session_manager,
)

__all__ = [
    # SCIM
    "SCIMUser",
    "SCIMGroup",
    "SCIMProvisioningEvent",
    "SCIMProvider",
    "get_scim_provider",
    # Group Mapping
    "GroupMapping",
    "RoleMapping",
    "GroupSyncResult",
    "GroupMapper",
    "get_group_mapper",
    # Audit
    "AuditEvent",
    "AuditEventType",
    "AuditLog",
    "SSOAuditLogger",
    "get_sso_audit_logger",
    # Session Management
    "SessionPolicy",
    "UserSession",
    "SessionManager",
    "get_session_manager",
]
