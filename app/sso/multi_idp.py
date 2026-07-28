"""Multi-IdP support for different tenants and teams.

Routes authentication to the appropriate identity provider based on:
- Tenant configuration
- Email domain
- Team membership
- User preference
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from app.sso.saml import SAMLConfig, SAMLProvider

log = logging.getLogger("devcopilot.sso.multi_idp")


class IdPRoutingStrategy(Enum):
    """Strategy for routing to IdP."""

    EMAIL_DOMAIN = "email_domain"
    TENANT_DEFAULT = "tenant_default"
    USER_PREFERENCE = "user_preference"
    TEAM_BASED = "team_based"
    ROUND_ROBIN = "round_robin"
    LOAD_BALANCED = "load_balanced"


@dataclass
class IdPConfig:
    """Extended IdP configuration with routing metadata."""

    idp_id: str
    name: str
    saml_config: SAMLConfig
    enabled: bool = True
    priority: int = 100
    email_domains: list[str] = field(default_factory=list)
    allowed_tenants: list[str] = field(default_factory=list)
    allowed_teams: list[str] = field(default_factory=list)
    max_concurrent_sessions: int = 1000
    current_sessions: int = 0
    health_status: str = "healthy"
    last_health_check: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_available(self) -> bool:
        """Check if IdP is available for new sessions."""
        return (
            self.enabled
            and self.health_status == "healthy"
            and self.current_sessions < self.max_concurrent_sessions
        )


@dataclass
class TenantIdPMapping:
    """Mapping of tenants to their IdP configurations."""

    tenant_id: str
    default_idp_id: str
    fallback_idp_ids: list[str] = field(default_factory=list)
    routing_strategy: IdPRoutingStrategy = IdPRoutingStrategy.TENANT_DEFAULT
    domain_mappings: dict[str, str] = field(default_factory=dict)
    team_mappings: dict[str, str] = field(default_factory=dict)
    allow_idp_discovery: bool = True
    enforce_single_idp: bool = False


@dataclass
class IdPDiscoveryHint:
    """Hint for IdP discovery from user input."""

    email: str | None = None
    domain: str | None = None
    tenant_id: str | None = None
    team: str | None = None
    preferred_idp: str | None = None


@dataclass
class IdPRoutingResult:
    """Result of IdP routing decision."""

    idp_id: str
    idp_config: IdPConfig
    strategy_used: IdPRoutingStrategy
    alternatives: list[str] = field(default_factory=list)
    reason: str = ""


class IdPRouter:
    """Routes authentication requests to the appropriate IdP."""

    def __init__(
        self,
        default_idp_id: str | None = None,
        enable_discovery: bool = True,
    ):
        self.default_idp_id = default_idp_id
        self.enable_discovery = enable_discovery
        self._idps: dict[str, IdPConfig] = {}
        self._tenant_mappings: dict[str, TenantIdPMapping] = {}
        self._domain_cache: dict[str, str] = {}
        self._round_robin_index = 0

    def register_idp(self, config: IdPConfig) -> None:
        """Register an IdP configuration."""
        self._idps[config.idp_id] = config

        for domain in config.email_domains:
            self._domain_cache[domain.lower()] = config.idp_id

        log.info(
            "IdP registered",
            extra={
                "idp_id": config.idp_id,
                "idp_name": config.name,
                "provider": config.saml_config.provider.value,
                "domains": config.email_domains,
            },
        )

    def unregister_idp(self, idp_id: str) -> bool:
        """Unregister an IdP."""
        if idp_id in self._idps:
            config = self._idps[idp_id]
            for domain in config.email_domains:
                if self._domain_cache.get(domain.lower()) == idp_id:
                    del self._domain_cache[domain.lower()]
            del self._idps[idp_id]
            return True
        return False

    def get_idp(self, idp_id: str) -> IdPConfig | None:
        """Get IdP configuration by ID."""
        return self._idps.get(idp_id)

    def list_idps(
        self,
        tenant_id: str | None = None,
        include_disabled: bool = False,
    ) -> list[IdPConfig]:
        """List available IdPs."""
        idps = list(self._idps.values())

        if not include_disabled:
            idps = [i for i in idps if i.enabled]

        if tenant_id:
            mapping = self._tenant_mappings.get(tenant_id)
            if mapping:
                allowed = [mapping.default_idp_id] + mapping.fallback_idp_ids
                idps = [i for i in idps if i.idp_id in allowed or not i.allowed_tenants]

        return sorted(idps, key=lambda i: i.priority)

    def set_tenant_mapping(self, mapping: TenantIdPMapping) -> None:
        """Set IdP mapping for a tenant."""
        self._tenant_mappings[mapping.tenant_id] = mapping
        log.info(
            "tenant IdP mapping set",
            extra={
                "tenant_id": mapping.tenant_id,
                "default_idp": mapping.default_idp_id,
                "strategy": mapping.routing_strategy.value,
            },
        )

    def get_tenant_mapping(self, tenant_id: str) -> TenantIdPMapping | None:
        """Get IdP mapping for a tenant."""
        return self._tenant_mappings.get(tenant_id)

    def route(
        self,
        hint: IdPDiscoveryHint,
        tenant_id: str | None = None,
    ) -> IdPRoutingResult | None:
        """Route to appropriate IdP based on hints."""
        if hint.preferred_idp:
            idp = self._idps.get(hint.preferred_idp)
            if idp and idp.is_available:
                return IdPRoutingResult(
                    idp_id=idp.idp_id,
                    idp_config=idp,
                    strategy_used=IdPRoutingStrategy.USER_PREFERENCE,
                    reason="User preferred IdP",
                )

        tenant_id = tenant_id or hint.tenant_id
        if tenant_id:
            mapping = self._tenant_mappings.get(tenant_id)
            if mapping:
                result = self._route_with_tenant_mapping(hint, mapping)
                if result:
                    return result

        if hint.email:
            domain = hint.email.split("@")[-1].lower()
            idp_id = self._domain_cache.get(domain)
            if idp_id:
                idp = self._idps.get(idp_id)
                if idp and idp.is_available:
                    return IdPRoutingResult(
                        idp_id=idp.idp_id,
                        idp_config=idp,
                        strategy_used=IdPRoutingStrategy.EMAIL_DOMAIN,
                        reason=f"Email domain {domain} mapped to IdP",
                    )

        if hint.domain:
            idp_id = self._domain_cache.get(hint.domain.lower())
            if idp_id:
                idp = self._idps.get(idp_id)
                if idp and idp.is_available:
                    return IdPRoutingResult(
                        idp_id=idp.idp_id,
                        idp_config=idp,
                        strategy_used=IdPRoutingStrategy.EMAIL_DOMAIN,
                        reason=f"Domain {hint.domain} mapped to IdP",
                    )

        if self.default_idp_id:
            idp = self._idps.get(self.default_idp_id)
            if idp and idp.is_available:
                return IdPRoutingResult(
                    idp_id=idp.idp_id,
                    idp_config=idp,
                    strategy_used=IdPRoutingStrategy.TENANT_DEFAULT,
                    reason="Default IdP",
                )

        available = [i for i in self._idps.values() if i.is_available]
        if available:
            idp = min(available, key=lambda i: i.priority)
            return IdPRoutingResult(
                idp_id=idp.idp_id,
                idp_config=idp,
                strategy_used=IdPRoutingStrategy.LOAD_BALANCED,
                reason="Fallback to first available IdP",
                alternatives=[i.idp_id for i in available if i.idp_id != idp.idp_id],
            )

        return None

    def _route_with_tenant_mapping(
        self,
        hint: IdPDiscoveryHint,
        mapping: TenantIdPMapping,
    ) -> IdPRoutingResult | None:
        """Route using tenant-specific mapping."""
        if mapping.routing_strategy == IdPRoutingStrategy.TEAM_BASED and hint.team:
            idp_id = mapping.team_mappings.get(hint.team)
            if idp_id:
                idp = self._idps.get(idp_id)
                if idp and idp.is_available:
                    return IdPRoutingResult(
                        idp_id=idp.idp_id,
                        idp_config=idp,
                        strategy_used=IdPRoutingStrategy.TEAM_BASED,
                        reason=f"Team {hint.team} mapped to IdP",
                    )

        if hint.email and mapping.domain_mappings:
            domain = hint.email.split("@")[-1].lower()
            idp_id = mapping.domain_mappings.get(domain)
            if idp_id:
                idp = self._idps.get(idp_id)
                if idp and idp.is_available:
                    return IdPRoutingResult(
                        idp_id=idp.idp_id,
                        idp_config=idp,
                        strategy_used=IdPRoutingStrategy.EMAIL_DOMAIN,
                        reason=f"Tenant domain mapping for {domain}",
                    )

        idp = self._idps.get(mapping.default_idp_id)
        if idp and idp.is_available:
            return IdPRoutingResult(
                idp_id=idp.idp_id,
                idp_config=idp,
                strategy_used=IdPRoutingStrategy.TENANT_DEFAULT,
                alternatives=mapping.fallback_idp_ids,
                reason="Tenant default IdP",
            )

        for fallback_id in mapping.fallback_idp_ids:
            idp = self._idps.get(fallback_id)
            if idp and idp.is_available:
                return IdPRoutingResult(
                    idp_id=idp.idp_id,
                    idp_config=idp,
                    strategy_used=IdPRoutingStrategy.TENANT_DEFAULT,
                    reason="Fallback IdP (primary unavailable)",
                )

        return None

    def discover_idp(self, email: str) -> list[dict[str, Any]]:
        """Discover available IdPs for an email address."""
        if not self.enable_discovery:
            return []

        domain = email.split("@")[-1].lower()
        results = []

        if domain in self._domain_cache:
            idp_id = self._domain_cache[domain]
            idp = self._idps.get(idp_id)
            if idp and idp.is_available:
                results.append({
                    "idp_id": idp.idp_id,
                    "name": idp.name,
                    "provider": idp.saml_config.provider.value,
                    "match_type": "exact_domain",
                    "confidence": 1.0,
                })

        for idp in self._idps.values():
            if not idp.is_available:
                continue
            for pattern in idp.email_domains:
                if pattern.startswith("*."):
                    suffix = pattern[2:].lower()
                    if domain.endswith(suffix) and idp.idp_id not in [r["idp_id"] for r in results]:
                        results.append({
                            "idp_id": idp.idp_id,
                            "name": idp.name,
                            "provider": idp.saml_config.provider.value,
                            "match_type": "wildcard_domain",
                            "confidence": 0.8,
                        })

        return sorted(results, key=lambda r: r["confidence"], reverse=True)

    def update_health_status(
        self,
        idp_id: str,
        status: str,
        error: str | None = None,
    ) -> bool:
        """Update IdP health status."""
        idp = self._idps.get(idp_id)
        if not idp:
            return False

        idp.health_status = status
        idp.last_health_check = datetime.now(timezone.utc)

        if status != "healthy":
            log.warning(
                "IdP health degraded",
                extra={"idp_id": idp_id, "status": status, "error": error},
            )

        return True

    def increment_session_count(self, idp_id: str) -> bool:
        """Increment session count for an IdP."""
        idp = self._idps.get(idp_id)
        if idp:
            idp.current_sessions += 1
            return True
        return False

    def decrement_session_count(self, idp_id: str) -> bool:
        """Decrement session count for an IdP."""
        idp = self._idps.get(idp_id)
        if idp and idp.current_sessions > 0:
            idp.current_sessions -= 1
            return True
        return False

    def get_status(self) -> dict[str, Any]:
        """Get overall IdP router status."""
        idps_status = []
        for idp in self._idps.values():
            idps_status.append({
                "idp_id": idp.idp_id,
                "name": idp.name,
                "enabled": idp.enabled,
                "health_status": idp.health_status,
                "current_sessions": idp.current_sessions,
                "max_sessions": idp.max_concurrent_sessions,
                "is_available": idp.is_available,
                "last_health_check": idp.last_health_check.isoformat() if idp.last_health_check else None,
            })

        return {
            "total_idps": len(self._idps),
            "available_idps": len([i for i in self._idps.values() if i.is_available]),
            "total_tenants": len(self._tenant_mappings),
            "domain_mappings": len(self._domain_cache),
            "idps": idps_status,
        }


_router: IdPRouter | None = None


def get_idp_router() -> IdPRouter:
    """Get the default IdP router."""
    global _router
    if _router is None:
        _router = IdPRouter()
    return _router
