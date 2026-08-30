"""OPA (Open Policy Agent) Client.

Provides integration with OPA for policy evaluation:
- HTTP API client for OPA server
- Policy management (create, update, delete)
- Batch evaluation
- Policy caching
- Fallback to embedded evaluation
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
import aiohttp

log = logging.getLogger("omnipotent.policy_engine.opa")


class OPAPolicyPackage(str, Enum):
    """Standard OPA policy packages."""
    REMEDIATION = "omnipotent.remediation"
    DEPLOYMENT = "omnipotent.deployment"
    ACCESS = "omnipotent.access"
    COMPLIANCE = "omnipotent.compliance"
    INCIDENT = "omnipotent.incident"
    CHANGE = "omnipotent.change"


@dataclass
class OPAPolicy:
    """An OPA policy definition."""
    id: str
    name: str
    package: str
    rego: str
    description: str = ""
    version: str = "1.0.0"
    enabled: bool = True
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "package": self.package,
            "description": self.description,
            "version": self.version,
            "enabled": self.enabled,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


@dataclass
class OPAInput:
    """Input for OPA policy evaluation."""
    action: str
    resource: dict[str, Any]
    context: dict[str, Any]
    user: dict[str, Any] | None = None
    environment: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "resource": self.resource,
            "context": self.context,
            "user": self.user or {},
            "environment": self.environment or {},
        }


@dataclass
class OPAResult:
    """Result from OPA policy evaluation."""
    allowed: bool
    policy_id: str
    decision_id: str
    violations: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    evaluated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    evaluation_time_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "policy_id": self.policy_id,
            "decision_id": self.decision_id,
            "violations": self.violations,
            "warnings": self.warnings,
            "metadata": self.metadata,
            "evaluated_at": self.evaluated_at.isoformat(),
            "evaluation_time_ms": self.evaluation_time_ms,
        }


# Default policies in Rego
DEFAULT_POLICIES = {
    "remediation_safety": '''
package omnipotent.remediation

default allow = false

# Allow if action is reversible and risk is acceptable
allow {
    input.resource.is_reversible == true
    input.resource.risk_score < 0.8
}

# Allow low-risk actions regardless of reversibility
allow {
    input.resource.risk_score < 0.3
}

# Deny high-risk actions during change freeze
deny[msg] {
    input.context.is_change_freeze == true
    input.resource.risk_score > 0.5
    not input.context.is_emergency
    msg := "High-risk actions blocked during change freeze"
}

# Deny actions without approval for critical services
deny[msg] {
    input.resource.service_criticality == "critical"
    not input.resource.has_approval
    input.resource.risk_score > 0.4
    msg := "Critical services require approval for risky actions"
}

# Warn about peak traffic
warn[msg] {
    input.context.is_peak_traffic == true
    input.resource.requires_downtime == true
    msg := "Action requires downtime during peak traffic"
}

violations[v] {
    deny[msg]
    v := {"type": "deny", "message": msg}
}

warnings[w] {
    warn[msg]
    w := msg
}
''',

    "deployment_policy": '''
package omnipotent.deployment

default allow = false

# Allow deployments within change windows
allow {
    input.context.in_change_window == true
    input.resource.passed_tests == true
}

# Allow emergency deployments with approval
allow {
    input.context.is_emergency == true
    input.resource.has_approval == true
}

# Deny deployment without test passage
deny[msg] {
    input.resource.passed_tests == false
    msg := "Deployment blocked: tests have not passed"
}

# Deny deployment outside change window without emergency
deny[msg] {
    input.context.in_change_window == false
    not input.context.is_emergency
    msg := "Deployment blocked: outside change window"
}

# Require canary for production
deny[msg] {
    input.resource.environment == "production"
    input.resource.deployment_strategy == "direct"
    msg := "Direct deployment to production not allowed, use canary or blue-green"
}

violations[v] {
    deny[msg]
    v := {"type": "deny", "message": msg}
}
''',

    "compliance_policy": '''
package omnipotent.compliance

default allow = true

# Deny if action affects PII without audit logging
deny[msg] {
    input.resource.affects_pii == true
    input.context.audit_logging_enabled == false
    msg := "PII-affecting actions require audit logging"
}

# Deny if action affects financial data without approval
deny[msg] {
    input.resource.affects_financial_data == true
    not input.resource.has_compliance_approval
    msg := "Financial data changes require compliance approval"
}

# Require encryption for data at rest changes
deny[msg] {
    input.resource.type == "database_change"
    input.resource.encryption_enabled == false
    msg := "Database changes must maintain encryption at rest"
}

allow {
    count(deny) == 0
}

violations[v] {
    deny[msg]
    v := {"type": "compliance", "message": msg}
}
''',
}


class OPAClient:
    """Client for Open Policy Agent.

    Supports:
    - Remote OPA server via HTTP API
    - Local policy evaluation (embedded mode)
    - Policy management
    - Result caching
    """

    def __init__(
        self,
        opa_url: str | None = None,
        timeout_seconds: float = 5.0,
        cache_ttl_seconds: int = 60,
        embedded_mode: bool = True,
    ):
        """Initialize OPA client.

        Args:
            opa_url: URL of OPA server (e.g., http://localhost:8181)
            timeout_seconds: Timeout for OPA API calls
            cache_ttl_seconds: How long to cache evaluation results
            embedded_mode: If True, evaluate policies locally when OPA is unavailable
        """
        self.opa_url = opa_url
        self.timeout = timeout_seconds
        self.cache_ttl = cache_ttl_seconds
        self.embedded_mode = embedded_mode

        # Policy storage
        self._policies: dict[str, OPAPolicy] = {}
        self._policy_cache: dict[str, tuple[OPAResult, datetime]] = {}

        # Statistics
        self._eval_count = 0
        self._cache_hits = 0
        self._opa_calls = 0
        self._fallback_count = 0

        # Load default policies
        self._load_default_policies()

    def _load_default_policies(self) -> None:
        """Load default built-in policies."""
        for name, rego in DEFAULT_POLICIES.items():
            policy = OPAPolicy(
                id=name,
                name=name.replace("_", " ").title(),
                package=f"omnipotent.{name.split('_')[0]}",
                rego=rego,
                description=f"Default {name} policy",
            )
            self._policies[name] = policy

    async def evaluate(
        self,
        policy_id: str,
        input_data: OPAInput,
        use_cache: bool = True,
    ) -> OPAResult:
        """Evaluate a policy against input.

        Args:
            policy_id: ID of the policy to evaluate
            input_data: Input data for evaluation
            use_cache: Whether to use cached results

        Returns:
            OPAResult with allow/deny decision and violations
        """
        self._eval_count += 1
        start_time = datetime.now(timezone.utc)

        # Check cache
        cache_key = self._cache_key(policy_id, input_data)
        if use_cache and cache_key in self._policy_cache:
            cached, cached_at = self._policy_cache[cache_key]
            age = (datetime.now(timezone.utc) - cached_at).total_seconds()
            if age < self.cache_ttl:
                self._cache_hits += 1
                return cached

        # Try OPA server first
        if self.opa_url:
            try:
                result = await self._evaluate_remote(policy_id, input_data)
                self._opa_calls += 1
            except Exception as e:
                log.warning("OPA server call failed: %s, falling back to embedded", e)
                if self.embedded_mode:
                    result = await self._evaluate_embedded(policy_id, input_data)
                    self._fallback_count += 1
                else:
                    raise
        else:
            # No OPA server, use embedded
            result = await self._evaluate_embedded(policy_id, input_data)

        # Calculate evaluation time
        end_time = datetime.now(timezone.utc)
        result.evaluation_time_ms = int((end_time - start_time).total_seconds() * 1000)

        # Cache result
        self._policy_cache[cache_key] = (result, datetime.now(timezone.utc))

        return result

    async def _evaluate_remote(
        self,
        policy_id: str,
        input_data: OPAInput,
    ) -> OPAResult:
        """Evaluate policy against remote OPA server."""
        policy = self._policies.get(policy_id)
        if not policy:
            raise ValueError(f"Policy not found: {policy_id}")

        # OPA v1 Data API
        url = f"{self.opa_url}/v1/data/{policy.package.replace('.', '/')}"

        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                json={"input": input_data.to_dict()},
                timeout=aiohttp.ClientTimeout(total=self.timeout),
            ) as response:
                if response.status != 200:
                    raise Exception(f"OPA returned status {response.status}")

                data = await response.json()
                result_data = data.get("result", {})

                return OPAResult(
                    allowed=result_data.get("allow", False),
                    policy_id=policy_id,
                    decision_id=self._generate_decision_id(policy_id, input_data),
                    violations=result_data.get("violations", []),
                    warnings=result_data.get("warnings", []),
                    metadata={"source": "opa_server", "engine": "opa_server"},
                )

    async def _evaluate_embedded(
        self,
        policy_id: str,
        input_data: OPAInput,
    ) -> OPAResult:
        """Evaluate policy using embedded logic (Python implementation).

        This is a fallback when OPA server is not available.
        Implements a subset of the policy logic in Python.
        """
        policy = self._policies.get(policy_id)
        if not policy:
            raise ValueError(f"Policy not found: {policy_id}")

        decision_id = self._generate_decision_id(policy_id, input_data)
        violations = []
        warnings = []
        allowed = True

        input_dict = input_data.to_dict()
        resource = input_dict.get("resource", {})
        context = input_dict.get("context", {})

        # Embedded evaluation based on policy package
        if "remediation" in policy.package:
            # Remediation safety policy
            is_reversible = resource.get("is_reversible", False)
            risk_score = resource.get("risk_score", 0.5)
            is_change_freeze = context.get("is_change_freeze", False)
            is_emergency = context.get("is_emergency", False)
            service_criticality = resource.get("service_criticality", "normal")
            has_approval = resource.get("has_approval", False)
            is_peak_traffic = context.get("is_peak_traffic", False)
            requires_downtime = resource.get("requires_downtime", False)

            # Allow logic
            if is_reversible and risk_score < 0.8:
                allowed = True
            elif risk_score < 0.3:
                allowed = True
            else:
                allowed = False

            # Deny logic
            if is_change_freeze and risk_score > 0.5 and not is_emergency:
                violations.append({
                    "type": "deny",
                    "message": "High-risk actions blocked during change freeze",
                })
                allowed = False

            if service_criticality == "critical" and not has_approval and risk_score > 0.4:
                violations.append({
                    "type": "deny",
                    "message": "Critical services require approval for risky actions",
                })
                allowed = False

            # Warn logic
            if is_peak_traffic and requires_downtime:
                warnings.append("Action requires downtime during peak traffic")

        elif "deployment" in policy.package:
            # Deployment policy
            in_change_window = context.get("in_change_window", False)
            passed_tests = resource.get("passed_tests", False)
            is_emergency = context.get("is_emergency", False)
            has_approval = resource.get("has_approval", False)
            environment = resource.get("environment", "staging")
            strategy = resource.get("deployment_strategy", "canary")

            # Allow logic
            if in_change_window and passed_tests:
                allowed = True
            elif is_emergency and has_approval:
                allowed = True
            else:
                allowed = False

            # Deny logic
            if not passed_tests:
                violations.append({
                    "type": "deny",
                    "message": "Deployment blocked: tests have not passed",
                })
                allowed = False

            if not in_change_window and not is_emergency:
                violations.append({
                    "type": "deny",
                    "message": "Deployment blocked: outside change window",
                })
                allowed = False

            if environment == "production" and strategy == "direct":
                violations.append({
                    "type": "deny",
                    "message": "Direct deployment to production not allowed",
                })
                allowed = False

        elif "compliance" in policy.package:
            # Compliance policy
            affects_pii = resource.get("affects_pii", False)
            audit_logging = context.get("audit_logging_enabled", True)
            affects_financial = resource.get("affects_financial_data", False)
            compliance_approval = resource.get("has_compliance_approval", False)
            resource_type = resource.get("type", "")
            encryption_enabled = resource.get("encryption_enabled", True)

            if affects_pii and not audit_logging:
                violations.append({
                    "type": "compliance",
                    "message": "PII-affecting actions require audit logging",
                })
                allowed = False

            if affects_financial and not compliance_approval:
                violations.append({
                    "type": "compliance",
                    "message": "Financial data changes require compliance approval",
                })
                allowed = False

            if resource_type == "database_change" and not encryption_enabled:
                violations.append({
                    "type": "compliance",
                    "message": "Database changes must maintain encryption at rest",
                })
                allowed = False

            if not violations:
                allowed = True

        return OPAResult(
            allowed=allowed,
            policy_id=policy_id,
            decision_id=decision_id,
            violations=violations,
            warnings=warnings,
            # "engine" marks which evaluator answered: this Python subset of
            # the Rego policies ("embedded") vs a real OPA server
            # ("opa_server"). The fallback used to be indistinguishable from
            # an authoritative server evaluation.
            metadata={"source": "embedded", "engine": "embedded"},
        )

    async def evaluate_batch(
        self,
        policy_id: str,
        inputs: list[OPAInput],
    ) -> list[OPAResult]:
        """Evaluate policy against multiple inputs in parallel."""
        tasks = [self.evaluate(policy_id, inp) for inp in inputs]
        return await asyncio.gather(*tasks)

    def register_policy(self, policy: OPAPolicy) -> None:
        """Register a new policy."""
        self._policies[policy.id] = policy
        log.info("Registered policy: %s", policy.id)

        # Push to OPA server if available
        if self.opa_url:
            asyncio.create_task(self._push_policy_to_opa(policy))

    async def _push_policy_to_opa(self, policy: OPAPolicy) -> None:
        """Push a policy to the OPA server."""
        url = f"{self.opa_url}/v1/policies/{policy.id}"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.put(
                    url,
                    data=policy.rego,
                    headers={"Content-Type": "text/plain"},
                    timeout=aiohttp.ClientTimeout(total=self.timeout),
                ) as response:
                    if response.status in (200, 201):
                        log.info("Policy %s pushed to OPA server", policy.id)
                    else:
                        log.warning("Failed to push policy to OPA: %s", response.status)
        except Exception as e:
            log.warning("Failed to push policy to OPA: %s", e)

    def get_policy(self, policy_id: str) -> OPAPolicy | None:
        """Get a policy by ID."""
        return self._policies.get(policy_id)

    def list_policies(self) -> list[OPAPolicy]:
        """List all registered policies."""
        return list(self._policies.values())

    def delete_policy(self, policy_id: str) -> bool:
        """Delete a policy."""
        if policy_id in self._policies:
            del self._policies[policy_id]
            log.info("Deleted policy: %s", policy_id)
            return True
        return False

    def _cache_key(self, policy_id: str, input_data: OPAInput) -> str:
        """Generate cache key for evaluation result."""
        content = json.dumps({
            "policy": policy_id,
            "input": input_data.to_dict(),
        }, sort_keys=True)
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    def _generate_decision_id(self, policy_id: str, input_data: OPAInput) -> str:
        """Generate unique decision ID."""
        content = f"{policy_id}:{datetime.now(timezone.utc).isoformat()}"
        return f"opa_{hashlib.sha256(content.encode()).hexdigest()[:12]}"

    def get_stats(self) -> dict[str, Any]:
        """Get client statistics."""
        return {
            "evaluations": self._eval_count,
            "cache_hits": self._cache_hits,
            "cache_hit_rate": self._cache_hits / max(self._eval_count, 1),
            "opa_server_calls": self._opa_calls,
            "fallback_evaluations": self._fallback_count,
            "policies_registered": len(self._policies),
            "cache_size": len(self._policy_cache),
            "opa_url": self.opa_url,
            "embedded_mode": self.embedded_mode,
        }

    def clear_cache(self) -> None:
        """Clear the evaluation cache."""
        self._policy_cache.clear()


def create_opa_client(
    opa_url: str | None = None,
    embedded_mode: bool = True,
) -> OPAClient:
    """Factory function to create an OPA client."""
    return OPAClient(opa_url=opa_url, embedded_mode=embedded_mode)
