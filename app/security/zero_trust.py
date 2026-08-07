"""Zero Trust Security Framework.

Production-ready implementation of Zero Trust Architecture:
- Never trust, always verify
- Continuous authentication and authorization
- Micro-segmentation support
- Device posture assessment
- Context-aware access control
- Just-in-time (JIT) privilege escalation
- Session management with continuous validation
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Protocol

import httpx

log = logging.getLogger("omnipotent.security.zerotrust")


class TrustLevel(str, Enum):
    """Trust levels for access decisions."""
    NONE = "none"  # No trust, access denied
    MINIMAL = "minimal"  # Basic access only
    LOW = "low"  # Limited functionality
    MEDIUM = "medium"  # Standard access
    HIGH = "high"  # Elevated access
    FULL = "full"  # Complete access (rare, time-limited)


class AccessContext(str, Enum):
    """Context factors for access decisions."""
    USER_IDENTITY = "user_identity"
    DEVICE_POSTURE = "device_posture"
    NETWORK_LOCATION = "network_location"
    TIME_OF_ACCESS = "time_of_access"
    RESOURCE_SENSITIVITY = "resource_sensitivity"
    BEHAVIOR_PATTERN = "behavior_pattern"
    RISK_SCORE = "risk_score"


class DeviceStatus(str, Enum):
    """Device security posture status."""
    COMPLIANT = "compliant"
    NON_COMPLIANT = "non_compliant"
    UNKNOWN = "unknown"
    COMPROMISED = "compromised"


@dataclass
class DevicePosture:
    """Device security posture assessment."""
    device_id: str
    os_type: str
    os_version: str
    security_software_enabled: bool
    encryption_enabled: bool
    patch_level: str
    last_security_scan: datetime | None
    is_managed: bool
    is_jailbroken: bool
    certificates_valid: bool
    status: DeviceStatus
    risk_factors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "os_type": self.os_type,
            "os_version": self.os_version,
            "security_software_enabled": self.security_software_enabled,
            "encryption_enabled": self.encryption_enabled,
            "patch_level": self.patch_level,
            "last_security_scan": self.last_security_scan.isoformat() if self.last_security_scan else None,
            "is_managed": self.is_managed,
            "is_jailbroken": self.is_jailbroken,
            "certificates_valid": self.certificates_valid,
            "status": self.status.value,
            "risk_factors": self.risk_factors,
        }

    @property
    def compliance_score(self) -> float:
        """Calculate device compliance score (0-1)."""
        score = 1.0
        if not self.security_software_enabled:
            score -= 0.2
        if not self.encryption_enabled:
            score -= 0.25
        if self.is_jailbroken:
            score -= 0.4
        if not self.is_managed:
            score -= 0.1
        if not self.certificates_valid:
            score -= 0.15
        if self.last_security_scan:
            days_since_scan = (datetime.now(timezone.utc) - self.last_security_scan).days
            if days_since_scan > 30:
                score -= 0.1
        return max(0, score)


@dataclass
class NetworkContext:
    """Network context for access decision."""
    ip_address: str
    is_vpn: bool
    is_corporate_network: bool
    is_known_network: bool
    geo_location: str | None
    asn: str | None
    threat_intel_match: bool
    proxy_detected: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "ip_address": self.ip_address,
            "is_vpn": self.is_vpn,
            "is_corporate_network": self.is_corporate_network,
            "is_known_network": self.is_known_network,
            "geo_location": self.geo_location,
            "asn": self.asn,
            "threat_intel_match": self.threat_intel_match,
            "proxy_detected": self.proxy_detected,
        }

    @property
    def network_trust_score(self) -> float:
        """Calculate network trust score (0-1)."""
        score = 0.5  # Start neutral
        if self.is_corporate_network:
            score += 0.3
        if self.is_vpn:
            score += 0.1
        if self.is_known_network:
            score += 0.1
        if self.threat_intel_match:
            score -= 0.5
        if self.proxy_detected and not self.is_vpn:
            score -= 0.2
        return max(0, min(1, score))


@dataclass
class UserContext:
    """User identity and behavior context."""
    user_id: str
    email: str
    roles: list[str]
    groups: list[str]
    mfa_verified: bool
    last_authentication: datetime
    authentication_method: str
    session_duration: timedelta
    behavior_score: float  # 0-1, anomaly detection score
    failed_auth_attempts: int
    is_service_account: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "email": self.email,
            "roles": self.roles,
            "groups": self.groups,
            "mfa_verified": self.mfa_verified,
            "last_authentication": self.last_authentication.isoformat(),
            "authentication_method": self.authentication_method,
            "session_duration_minutes": self.session_duration.total_seconds() / 60,
            "behavior_score": self.behavior_score,
            "failed_auth_attempts": self.failed_auth_attempts,
            "is_service_account": self.is_service_account,
        }

    @property
    def identity_trust_score(self) -> float:
        """Calculate identity trust score (0-1)."""
        score = 0.6  # Base score for authenticated user
        if self.mfa_verified:
            score += 0.2
        if self.authentication_method in ["hardware_key", "biometric"]:
            score += 0.1
        if self.failed_auth_attempts > 3:
            score -= 0.2
        if self.behavior_score < 0.5:
            score -= 0.3
        session_hours = self.session_duration.total_seconds() / 3600
        if session_hours > 8:
            score -= 0.1
        return max(0, min(1, score))


@dataclass
class ResourceContext:
    """Resource being accessed."""
    resource_id: str
    resource_type: str
    sensitivity_level: str  # public, internal, confidential, restricted
    owner: str
    environment: str
    data_classification: str
    compliance_requirements: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "resource_id": self.resource_id,
            "resource_type": self.resource_type,
            "sensitivity_level": self.sensitivity_level,
            "owner": self.owner,
            "environment": self.environment,
            "data_classification": self.data_classification,
            "compliance_requirements": self.compliance_requirements,
        }

    @property
    def required_trust_level(self) -> TrustLevel:
        """Determine required trust level for this resource."""
        level_map = {
            "public": TrustLevel.MINIMAL,
            "internal": TrustLevel.LOW,
            "confidential": TrustLevel.MEDIUM,
            "restricted": TrustLevel.HIGH,
        }
        return level_map.get(self.sensitivity_level, TrustLevel.MEDIUM)


@dataclass
class AccessRequest:
    """A request for resource access."""
    request_id: str
    user: UserContext
    device: DevicePosture
    network: NetworkContext
    resource: ResourceContext
    action: str
    timestamp: datetime
    justification: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "user": self.user.to_dict(),
            "device": self.device.to_dict(),
            "network": self.network.to_dict(),
            "resource": self.resource.to_dict(),
            "action": self.action,
            "timestamp": self.timestamp.isoformat(),
            "justification": self.justification,
        }


@dataclass
class AccessDecision:
    """Result of an access request evaluation."""
    request_id: str
    allowed: bool
    trust_level: TrustLevel
    computed_risk_score: float
    factors: dict[str, float]
    required_actions: list[str]
    session_constraints: dict[str, Any]
    expires_at: datetime
    audit_trail: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "allowed": self.allowed,
            "trust_level": self.trust_level.value,
            "computed_risk_score": self.computed_risk_score,
            "factors": self.factors,
            "required_actions": self.required_actions,
            "session_constraints": self.session_constraints,
            "expires_at": self.expires_at.isoformat(),
        }


@dataclass
class JITPrivilege:
    """Just-in-time privilege grant."""
    grant_id: str
    user_id: str
    privilege: str
    resource: str
    granted_at: datetime
    expires_at: datetime
    justification: str
    approved_by: str | None
    auto_approved: bool
    usage_count: int = 0
    max_usage: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "grant_id": self.grant_id,
            "user_id": self.user_id,
            "privilege": self.privilege,
            "resource": self.resource,
            "granted_at": self.granted_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "justification": self.justification,
            "approved_by": self.approved_by,
            "auto_approved": self.auto_approved,
            "usage_count": self.usage_count,
            "max_usage": self.max_usage,
        }

    @property
    def is_valid(self) -> bool:
        if datetime.now(timezone.utc) >= self.expires_at:
            return False
        if self.max_usage and self.usage_count >= self.max_usage:
            return False
        return True


class IdentityProvider(Protocol):
    """Protocol for identity providers."""
    async def verify_user(self, token: str) -> UserContext | None: ...
    async def verify_mfa(self, user_id: str, code: str) -> bool: ...


class DeviceManager(Protocol):
    """Protocol for device management."""
    async def get_device_posture(self, device_id: str) -> DevicePosture | None: ...
    async def is_device_compliant(self, device_id: str) -> bool: ...


class ThreatIntelligence(Protocol):
    """Protocol for threat intelligence feeds."""
    async def check_ip(self, ip: str) -> dict[str, Any]: ...
    async def check_user(self, user_id: str) -> dict[str, Any]: ...


class ZeroTrustEngine:
    """Core Zero Trust policy decision engine.

    Implements continuous verification:
    - Every access request is evaluated
    - Context is gathered from multiple sources
    - Trust is calculated dynamically
    - Decisions are audited
    """

    def __init__(
        self,
        identity_provider: IdentityProvider | None = None,
        device_manager: DeviceManager | None = None,
        threat_intel: ThreatIntelligence | None = None,
        redis_client: Any = None,
        db_client: Any = None,
    ):
        self.identity_provider = identity_provider
        self.device_manager = device_manager
        self.threat_intel = threat_intel
        self.redis = redis_client
        self.db = db_client

        self._sessions: dict[str, AccessDecision] = {}
        self._jit_privileges: dict[str, JITPrivilege] = {}
        self._policy_rules: list[dict[str, Any]] = []

        self._http_client: httpx.AsyncClient | None = None

        self._load_default_policies()

    async def _get_http_client(self) -> httpx.AsyncClient:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=10.0)
        return self._http_client

    async def close(self) -> None:
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None

    def _load_default_policies(self) -> None:
        """Load default Zero Trust policies."""
        self._policy_rules = [
            {
                "name": "require_mfa_for_sensitive",
                "condition": lambda req: req.resource.sensitivity_level in ["confidential", "restricted"],
                "requirement": lambda req: req.user.mfa_verified,
                "deny_reason": "MFA required for sensitive resources",
            },
            {
                "name": "block_compromised_devices",
                "condition": lambda req: True,
                "requirement": lambda req: req.device.status != DeviceStatus.COMPROMISED,
                "deny_reason": "Access denied from compromised device",
            },
            {
                "name": "block_threat_intel_matches",
                "condition": lambda req: True,
                "requirement": lambda req: not req.network.threat_intel_match,
                "deny_reason": "Access denied from threat-flagged network",
            },
            {
                "name": "require_managed_device_for_restricted",
                "condition": lambda req: req.resource.sensitivity_level == "restricted",
                "requirement": lambda req: req.device.is_managed,
                "deny_reason": "Managed device required for restricted resources",
            },
            {
                "name": "block_jailbroken_devices",
                "condition": lambda req: True,
                "requirement": lambda req: not req.device.is_jailbroken,
                "deny_reason": "Access denied from jailbroken/rooted device",
            },
            {
                "name": "require_encryption_for_confidential",
                "condition": lambda req: req.resource.sensitivity_level in ["confidential", "restricted"],
                "requirement": lambda req: req.device.encryption_enabled,
                "deny_reason": "Device encryption required for confidential data",
            },
        ]

    async def evaluate_access(self, request: AccessRequest) -> AccessDecision:
        """Evaluate an access request using Zero Trust principles.

        Never trust, always verify:
        1. Check policy rules
        2. Calculate composite trust score
        3. Determine appropriate access level
        4. Apply constraints and timeouts
        """
        now = datetime.now(timezone.utc)
        factors: dict[str, float] = {}
        required_actions: list[str] = []
        denied_reasons: list[str] = []

        factors["identity"] = request.user.identity_trust_score
        factors["device"] = request.device.compliance_score
        factors["network"] = request.network.network_trust_score

        time_factor = self._calculate_time_factor(now)
        factors["time"] = time_factor

        if request.user.behavior_score < 0.7:
            factors["behavior"] = request.user.behavior_score
        else:
            factors["behavior"] = 1.0

        for rule in self._policy_rules:
            if rule["condition"](request):
                if not rule["requirement"](request):
                    denied_reasons.append(rule["deny_reason"])

        if denied_reasons:
            return AccessDecision(
                request_id=request.request_id,
                allowed=False,
                trust_level=TrustLevel.NONE,
                computed_risk_score=1.0,
                factors=factors,
                required_actions=denied_reasons,
                session_constraints={},
                expires_at=now,
                audit_trail=self._create_audit_trail(request, False, denied_reasons),
            )

        weights = {
            "identity": 0.3,
            "device": 0.25,
            "network": 0.2,
            "time": 0.1,
            "behavior": 0.15,
        }

        composite_trust = sum(factors[k] * weights[k] for k in factors)
        risk_score = 1.0 - composite_trust

        trust_level = self._score_to_trust_level(composite_trust)
        required_trust = request.resource.required_trust_level

        if self._trust_level_value(trust_level) < self._trust_level_value(required_trust):
            required_actions.append(f"Elevate trust to {required_trust.value}")

            if not request.user.mfa_verified:
                required_actions.append("Complete MFA verification")
            if request.device.compliance_score < 0.8:
                required_actions.append("Remediate device compliance issues")

        session_duration = self._calculate_session_duration(trust_level, request.resource)
        expires_at = now + session_duration

        constraints = {
            "max_session_duration_minutes": session_duration.total_seconds() / 60,
            "require_re_auth_on_ip_change": True,
            "require_re_auth_on_device_change": True,
            "allowed_actions": self._get_allowed_actions(trust_level, request.action),
            "rate_limit_per_minute": self._get_rate_limit(trust_level),
        }

        if trust_level in [TrustLevel.HIGH, TrustLevel.FULL]:
            constraints["continuous_validation_interval_minutes"] = 5
        else:
            constraints["continuous_validation_interval_minutes"] = 15

        decision = AccessDecision(
            request_id=request.request_id,
            allowed=True,
            trust_level=trust_level,
            computed_risk_score=risk_score,
            factors=factors,
            required_actions=required_actions,
            session_constraints=constraints,
            expires_at=expires_at,
            audit_trail=self._create_audit_trail(request, True, []),
        )

        self._sessions[request.request_id] = decision

        if self.redis:
            await self.redis.setex(
                f"zt:session:{request.request_id}",
                int(session_duration.total_seconds()),
                json.dumps(decision.to_dict()),
            )

        log.info(
            "Access %s for user %s to %s (trust=%s, risk=%.2f)",
            "GRANTED" if decision.allowed else "DENIED",
            request.user.user_id,
            request.resource.resource_id,
            trust_level.value,
            risk_score,
        )

        return decision

    def _calculate_time_factor(self, now: datetime) -> float:
        """Calculate trust factor based on time of access."""
        hour = now.hour
        weekday = now.weekday()

        if weekday >= 5:
            return 0.7

        if 9 <= hour <= 17:
            return 1.0
        elif 6 <= hour <= 21:
            return 0.9
        else:
            return 0.6

    def _score_to_trust_level(self, score: float) -> TrustLevel:
        """Convert composite score to trust level."""
        if score >= 0.9:
            return TrustLevel.HIGH
        elif score >= 0.75:
            return TrustLevel.MEDIUM
        elif score >= 0.5:
            return TrustLevel.LOW
        elif score >= 0.3:
            return TrustLevel.MINIMAL
        else:
            return TrustLevel.NONE

    def _trust_level_value(self, level: TrustLevel) -> int:
        """Convert trust level to numeric value for comparison."""
        values = {
            TrustLevel.NONE: 0,
            TrustLevel.MINIMAL: 1,
            TrustLevel.LOW: 2,
            TrustLevel.MEDIUM: 3,
            TrustLevel.HIGH: 4,
            TrustLevel.FULL: 5,
        }
        return values.get(level, 0)

    def _calculate_session_duration(
        self,
        trust_level: TrustLevel,
        resource: ResourceContext,
    ) -> timedelta:
        """Calculate appropriate session duration based on trust and resource."""
        base_durations = {
            TrustLevel.NONE: timedelta(minutes=0),
            TrustLevel.MINIMAL: timedelta(minutes=15),
            TrustLevel.LOW: timedelta(hours=1),
            TrustLevel.MEDIUM: timedelta(hours=4),
            TrustLevel.HIGH: timedelta(hours=8),
            TrustLevel.FULL: timedelta(hours=12),
        }

        duration = base_durations.get(trust_level, timedelta(hours=1))

        if resource.sensitivity_level == "restricted":
            duration = min(duration, timedelta(hours=1))
        elif resource.sensitivity_level == "confidential":
            duration = min(duration, timedelta(hours=4))

        return duration

    def _get_allowed_actions(self, trust_level: TrustLevel, requested: str) -> list[str]:
        """Get allowed actions based on trust level."""
        action_tiers = {
            TrustLevel.MINIMAL: ["read"],
            TrustLevel.LOW: ["read", "list"],
            TrustLevel.MEDIUM: ["read", "list", "create", "update"],
            TrustLevel.HIGH: ["read", "list", "create", "update", "delete"],
            TrustLevel.FULL: ["read", "list", "create", "update", "delete", "admin"],
        }
        return action_tiers.get(trust_level, [])

    def _get_rate_limit(self, trust_level: TrustLevel) -> int:
        """Get rate limit based on trust level."""
        limits = {
            TrustLevel.MINIMAL: 10,
            TrustLevel.LOW: 30,
            TrustLevel.MEDIUM: 100,
            TrustLevel.HIGH: 500,
            TrustLevel.FULL: 1000,
        }
        return limits.get(trust_level, 30)

    def _create_audit_trail(
        self,
        request: AccessRequest,
        allowed: bool,
        reasons: list[str],
    ) -> dict[str, Any]:
        """Create audit trail for the access decision."""
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "request_id": request.request_id,
            "user_id": request.user.user_id,
            "resource_id": request.resource.resource_id,
            "action": request.action,
            "allowed": allowed,
            "denial_reasons": reasons,
            "device_id": request.device.device_id,
            "ip_address": request.network.ip_address,
            "user_agent": None,
        }

    async def continuous_validation(self, session_id: str) -> bool:
        """Perform continuous validation of an active session.

        Called periodically to ensure the session should remain valid.
        """
        decision = self._sessions.get(session_id)
        if not decision:
            if self.redis:
                data = await self.redis.get(f"zt:session:{session_id}")
                if not data:
                    return False
            else:
                return False
            return False

        if decision.expires_at and datetime.now(timezone.utc) >= decision.expires_at:
            log.info("Session %s expired", session_id)
            return False

        return True

    async def request_jit_privilege(
        self,
        user_id: str,
        privilege: str,
        resource: str,
        justification: str,
        duration_minutes: int = 60,
        max_usage: int | None = None,
    ) -> JITPrivilege | None:
        """Request just-in-time privilege elevation.

        Grants temporary elevated access with strict time limits.
        """
        now = datetime.now(timezone.utc)
        grant_id = f"jit_{secrets.token_hex(8)}"

        auto_approve = await self._can_auto_approve_jit(
            user_id, privilege, resource, justification
        )

        if not auto_approve:
            log.info(
                "JIT request %s requires manual approval (user=%s, privilege=%s)",
                grant_id, user_id, privilege
            )
            return None

        jit = JITPrivilege(
            grant_id=grant_id,
            user_id=user_id,
            privilege=privilege,
            resource=resource,
            granted_at=now,
            expires_at=now + timedelta(minutes=duration_minutes),
            justification=justification,
            approved_by=None,
            auto_approved=True,
            usage_count=0,
            max_usage=max_usage,
        )

        self._jit_privileges[grant_id] = jit

        if self.redis:
            await self.redis.setex(
                f"zt:jit:{grant_id}",
                duration_minutes * 60,
                json.dumps(jit.to_dict()),
            )

        log.info(
            "JIT privilege granted: %s -> %s on %s (expires in %d min)",
            user_id, privilege, resource, duration_minutes
        )

        return jit

    async def _can_auto_approve_jit(
        self,
        user_id: str,
        privilege: str,
        resource: str,
        justification: str,
    ) -> bool:
        """Determine if a JIT request can be auto-approved."""
        low_risk_privileges = ["read", "list", "view", "export"]
        if privilege in low_risk_privileges:
            return True

        if len(justification) > 50 and "incident" in justification.lower():
            return True

        return False

    async def use_jit_privilege(self, grant_id: str) -> bool:
        """Use a JIT privilege, incrementing usage count."""
        jit = self._jit_privileges.get(grant_id)
        if not jit:
            return False

        if not jit.is_valid:
            log.warning("Attempted to use invalid JIT privilege: %s", grant_id)
            return False

        jit.usage_count += 1

        if self.redis:
            ttl = int((jit.expires_at - datetime.now(timezone.utc)).total_seconds())
            if ttl > 0:
                await self.redis.setex(
                    f"zt:jit:{grant_id}",
                    ttl,
                    json.dumps(jit.to_dict()),
                )

        return True

    async def revoke_jit_privilege(self, grant_id: str) -> bool:
        """Revoke a JIT privilege before expiration."""
        if grant_id in self._jit_privileges:
            del self._jit_privileges[grant_id]

        if self.redis:
            await self.redis.delete(f"zt:jit:{grant_id}")

        log.info("JIT privilege revoked: %s", grant_id)
        return True

    async def assess_network_context(self, ip_address: str) -> NetworkContext:
        """Assess network context for an IP address."""
        is_private = False
        try:
            ip_obj = ipaddress.ip_address(ip_address)
            is_private = ip_obj.is_private
        except ValueError:
            pass

        threat_match = False
        geo_location = None
        asn = None

        if self.threat_intel:
            intel = await self.threat_intel.check_ip(ip_address)
            threat_match = intel.get("malicious", False)
            geo_location = intel.get("geo_location")
            asn = intel.get("asn")

        return NetworkContext(
            ip_address=ip_address,
            is_vpn=False,  # Would need VPN detection service
            is_corporate_network=is_private,
            is_known_network=is_private,
            geo_location=geo_location,
            asn=asn,
            threat_intel_match=threat_match,
            proxy_detected=False,
        )

    async def check_ip_reputation(self, ip_address: str) -> dict[str, Any]:
        """Check IP reputation against threat intelligence feeds."""
        result: dict[str, Any] = {
            "ip": ip_address,
            "is_malicious": False,
            "threat_types": [],
            "reputation_score": 100,
            "sources_checked": [],
        }

        try:
            abuseipdb_key = os.environ.get("ABUSEIPDB_API_KEY")
            if abuseipdb_key:
                client = await self._get_http_client()
                response = await client.get(
                    "https://api.abuseipdb.com/api/v2/check",
                    params={"ipAddress": ip_address, "maxAgeInDays": 90},
                    headers={"Key": abuseipdb_key, "Accept": "application/json"},
                )
                if response.status_code == 200:
                    data = response.json().get("data", {})
                    result["sources_checked"].append("abuseipdb")
                    abuse_score = data.get("abuseConfidenceScore", 0)
                    if abuse_score > 50:
                        result["is_malicious"] = True
                        result["threat_types"].append("abuse_reported")
                    result["reputation_score"] = 100 - abuse_score
        except Exception as e:
            log.warning("IP reputation check failed: %s", e)

        return result

    def list_active_sessions(self, user_id: str | None = None) -> list[AccessDecision]:
        """List active sessions, optionally filtered by user."""
        now = datetime.now(timezone.utc)
        active = []

        for decision in self._sessions.values():
            if decision.expires_at > now:
                if user_id is None or decision.audit_trail.get("user_id") == user_id:
                    active.append(decision)

        return active

    def list_jit_privileges(self, user_id: str | None = None) -> list[JITPrivilege]:
        """List active JIT privileges."""
        active = []

        for jit in self._jit_privileges.values():
            if jit.is_valid:
                if user_id is None or jit.user_id == user_id:
                    active.append(jit)

        return active

    async def terminate_session(self, session_id: str) -> bool:
        """Terminate an active session."""
        if session_id in self._sessions:
            del self._sessions[session_id]

        if self.redis:
            await self.redis.delete(f"zt:session:{session_id}")

        log.info("Session terminated: %s", session_id)
        return True

    def get_stats(self) -> dict[str, Any]:
        """Get Zero Trust engine statistics."""
        now = datetime.now(timezone.utc)
        active_sessions = len([s for s in self._sessions.values() if s.expires_at > now])
        active_jit = len([j for j in self._jit_privileges.values() if j.is_valid])

        return {
            "active_sessions": active_sessions,
            "active_jit_privileges": active_jit,
            "policy_rules_count": len(self._policy_rules),
            "total_sessions_tracked": len(self._sessions),
        }


class MicroSegmentationPolicy:
    """Network micro-segmentation policy manager.

    Defines fine-grained network access rules between workloads.
    """

    def __init__(self):
        self._segments: dict[str, set[str]] = {}
        self._rules: list[dict[str, Any]] = []

    def define_segment(self, name: str, members: list[str]) -> None:
        """Define a network segment."""
        self._segments[name] = set(members)

    def add_rule(
        self,
        source_segment: str,
        dest_segment: str,
        ports: list[int],
        protocol: str = "tcp",
        action: str = "allow",
    ) -> None:
        """Add a micro-segmentation rule."""
        self._rules.append({
            "source": source_segment,
            "destination": dest_segment,
            "ports": ports,
            "protocol": protocol,
            "action": action,
        })

    def check_allowed(
        self,
        source: str,
        destination: str,
        port: int,
        protocol: str = "tcp",
    ) -> bool:
        """Check if communication is allowed."""
        source_segments = [
            name for name, members in self._segments.items()
            if source in members
        ]
        dest_segments = [
            name for name, members in self._segments.items()
            if destination in members
        ]

        for rule in self._rules:
            if (
                rule["source"] in source_segments and
                rule["destination"] in dest_segments and
                port in rule["ports"] and
                rule["protocol"] == protocol
            ):
                return rule["action"] == "allow"

        return False  # Deny by default

    def get_policy_matrix(self) -> dict[str, dict[str, list[int]]]:
        """Get the policy matrix showing allowed communications."""
        matrix: dict[str, dict[str, list[int]]] = {}

        for rule in self._rules:
            if rule["action"] == "allow":
                source = rule["source"]
                dest = rule["destination"]

                if source not in matrix:
                    matrix[source] = {}
                if dest not in matrix[source]:
                    matrix[source][dest] = []

                matrix[source][dest].extend(rule["ports"])

        return matrix


def create_zero_trust_engine(
    identity_provider: IdentityProvider | None = None,
    device_manager: DeviceManager | None = None,
    threat_intel: ThreatIntelligence | None = None,
    redis_client: Any = None,
) -> ZeroTrustEngine:
    """Factory function to create a Zero Trust engine."""
    return ZeroTrustEngine(
        identity_provider=identity_provider,
        device_manager=device_manager,
        threat_intel=threat_intel,
        redis_client=redis_client,
    )
