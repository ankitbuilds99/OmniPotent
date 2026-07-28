"""SSO/SAML module with advanced enterprise features.

Provides:
- SAML 2.0 authentication with multiple IdP support
- Attribute-Based Access Control (ABAC)
- Session intelligence with anomaly detection
- Emergency bypass for IdP outages
- Multi-IdP support per tenant
"""

from app.sso.saml import (
    SAMLConfig,
    SAMLProvider,
    SAMLAssertion,
    SAMLService,
    get_saml_service,
)
from app.sso.abac import (
    Permission,
    ABACRule,
    ABACPolicy,
    ABACPermissionResolver,
    get_abac_resolver,
)
from app.sso.session_intelligence import (
    DeviceInfo,
    LocationInfo,
    SessionRiskScore,
    AnomalyType,
    SessionIntelligence,
    get_session_intelligence,
)
from app.sso.emergency_bypass import (
    EmergencyCode,
    BypassReason,
    EmergencyBypassManager,
    get_emergency_bypass,
)
from app.sso.multi_idp import (
    IdPConfig,
    IdPRouter,
    TenantIdPMapping,
    get_idp_router,
)

__all__ = [
    # SAML
    "SAMLConfig",
    "SAMLProvider",
    "SAMLAssertion",
    "SAMLService",
    "get_saml_service",
    # ABAC
    "Permission",
    "ABACRule",
    "ABACPolicy",
    "ABACPermissionResolver",
    "get_abac_resolver",
    # Session Intelligence
    "DeviceInfo",
    "LocationInfo",
    "SessionRiskScore",
    "AnomalyType",
    "SessionIntelligence",
    "get_session_intelligence",
    # Emergency Bypass
    "EmergencyCode",
    "BypassReason",
    "EmergencyBypassManager",
    "get_emergency_bypass",
    # Multi-IdP
    "IdPConfig",
    "IdPRouter",
    "TenantIdPMapping",
    "get_idp_router",
]
