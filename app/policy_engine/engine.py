"""Policy Engine - Orchestrates policy evaluation.

Central engine that combines:
- OPA policy evaluation
- Kyverno Kubernetes policies
- Change window management
- Entry/exit criteria evaluation
- Policy versioning and audit
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
import hashlib
import json

log = logging.getLogger("omnipotent.policy_engine")


class PolicyDecision(str, Enum):
    """Policy decision outcomes."""
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"
    WARN = "warn"


class ViolationSeverity(str, Enum):
    """Severity levels for policy violations."""
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


@dataclass
class PolicyViolation:
    """A policy violation."""
    policy_name: str
    message: str
    severity: ViolationSeverity
    source: str  # "opa", "kyverno", "window", "criteria"
    resource: str | None = None
    remediation: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_name": self.policy_name,
            "message": self.message,
            "severity": self.severity.value,
            "source": self.source,
            "resource": self.resource,
            "remediation": self.remediation,
        }


@dataclass
class PolicyEvaluationResult:
    """Complete result of policy evaluation."""
    decision: PolicyDecision
    violations: list[PolicyViolation]
    warnings: list[PolicyViolation]

    # Component results
    opa_allowed: bool | None
    kyverno_allowed: bool | None
    window_allowed: bool | None
    criteria_passed: bool | None

    # Metadata
    evaluated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    evaluation_id: str = ""
    duration_ms: float = 0.0
    requires_approval: bool = False
    approval_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.value,
            "violations": [v.to_dict() for v in self.violations],
            "warnings": [w.to_dict() for w in self.warnings],
            "opa_allowed": self.opa_allowed,
            "kyverno_allowed": self.kyverno_allowed,
            "window_allowed": self.window_allowed,
            "criteria_passed": self.criteria_passed,
            "evaluated_at": self.evaluated_at.isoformat(),
            "evaluation_id": self.evaluation_id,
            "duration_ms": self.duration_ms,
            "requires_approval": self.requires_approval,
            "approval_reasons": self.approval_reasons,
        }


@dataclass
class PolicyVersion:
    """A versioned policy snapshot."""
    version: str
    policy_name: str
    policy_type: str  # "opa", "kyverno"
    content: str
    created_at: datetime
    created_by: str
    checksum: str
    is_active: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "policy_name": self.policy_name,
            "policy_type": self.policy_type,
            "checksum": self.checksum,
            "created_at": self.created_at.isoformat(),
            "created_by": self.created_by,
            "is_active": self.is_active,
        }


@dataclass
class AuditRecord:
    """Audit record for policy evaluation."""
    evaluation_id: str
    timestamp: datetime
    action: str
    resource: str
    decision: PolicyDecision
    violations_count: int
    requested_by: str
    approved_by: str | None
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "evaluation_id": self.evaluation_id,
            "timestamp": self.timestamp.isoformat(),
            "action": self.action,
            "resource": self.resource,
            "decision": self.decision.value,
            "violations_count": self.violations_count,
            "requested_by": self.requested_by,
            "approved_by": self.approved_by,
            "metadata": self.metadata,
        }


class PolicyEngine:
    """Central policy evaluation engine.

    Orchestrates multiple policy backends:
    - OPA for general policies (Rego)
    - Kyverno for Kubernetes-native policies
    - Change windows for time-based controls
    - Entry/exit criteria for operational gates

    Features:
    - Policy versioning
    - Audit logging
    - Caching
    - Approval workflows
    """

    def __init__(
        self,
        opa_client: Any = None,
        kyverno_client: Any = None,
        change_window_manager: Any = None,
        criteria_evaluator: Any = None,
        redis_client: Any = None,
    ):
        self.opa = opa_client
        self.kyverno = kyverno_client
        self.windows = change_window_manager
        self.criteria = criteria_evaluator
        self.redis = redis_client

        self._policy_versions: dict[str, list[PolicyVersion]] = {}
        self._audit_log: list[AuditRecord] = []
        self._pending_approvals: dict[str, dict[str, Any]] = {}
        self._evaluation_counter = 0

        # Configuration
        self.deny_on_error = True  # Fail closed
        self.require_all_backends = False  # OR vs AND for backends
        self.cache_ttl = 60  # seconds

    async def evaluate(
        self,
        action: str,
        resource: dict[str, Any],
        context: dict[str, Any] | None = None,
        requested_by: str = "system",
    ) -> PolicyEvaluationResult:
        """Evaluate all policies for an action.

        Args:
            action: The action being performed (e.g., "deploy", "scale", "restart")
            resource: The resource being acted upon
            context: Additional context for evaluation
            requested_by: Who is requesting the action

        Returns:
            PolicyEvaluationResult with decision and details
        """
        import time
        start_time = time.monotonic()

        context = context or {}
        violations: list[PolicyViolation] = []
        warnings: list[PolicyViolation] = []
        approval_reasons: list[str] = []

        # Generate evaluation ID
        self._evaluation_counter += 1
        evaluation_id = f"eval_{self._evaluation_counter}_{int(time.time())}"

        # Build input for policy evaluation
        policy_input = {
            "action": action,
            "resource": resource,
            "context": context,
            "requested_by": requested_by,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        # Evaluate all backends in parallel
        results = await asyncio.gather(
            self._evaluate_opa(action, policy_input),
            self._evaluate_kyverno(resource),
            self._evaluate_windows(action, resource, context),
            self._evaluate_entry_criteria(action, context),
            return_exceptions=True,
        )

        opa_raw, kyverno_raw, window_raw, criteria_raw = results

        # Convert raw results to proper types
        opa_result: dict[str, Any] | None = opa_raw if isinstance(opa_raw, dict) else None
        kyverno_result: dict[str, Any] | None = kyverno_raw if isinstance(kyverno_raw, dict) else None
        window_result: dict[str, Any] | None = window_raw if isinstance(window_raw, dict) else None
        criteria_result: dict[str, Any] | None = criteria_raw if isinstance(criteria_raw, dict) else None

        # Process OPA result
        opa_allowed = None
        if isinstance(opa_raw, Exception):
            log.error("OPA evaluation failed: %s", opa_raw)
            if self.deny_on_error:
                violations.append(PolicyViolation(
                    policy_name="opa_error",
                    message=str(opa_raw),
                    severity=ViolationSeverity.CRITICAL,
                    source="opa",
                ))
        elif opa_result:
            opa_allowed = opa_result.get("allowed", True)
            if not opa_allowed:
                for v in opa_result.get("violations", []):
                    violations.append(PolicyViolation(
                        policy_name=v.get("policy", "unknown"),
                        message=v.get("message", "OPA policy violation"),
                        severity=ViolationSeverity(v.get("severity", "high")),
                        source="opa",
                        resource=v.get("resource"),
                        remediation=v.get("remediation"),
                    ))
            for w in opa_result.get("warnings", []):
                warnings.append(PolicyViolation(
                    policy_name=w.get("policy", "unknown"),
                    message=w.get("message", "OPA warning"),
                    severity=ViolationSeverity.LOW,
                    source="opa",
                ))

        # Process Kyverno result
        kyverno_allowed = None
        if isinstance(kyverno_raw, Exception):
            log.error("Kyverno evaluation failed: %s", kyverno_raw)
            if self.deny_on_error:
                violations.append(PolicyViolation(
                    policy_name="kyverno_error",
                    message=str(kyverno_result),
                    severity=ViolationSeverity.CRITICAL,
                    source="kyverno",
                ))
        elif kyverno_result:
            kyverno_allowed = kyverno_result.get("allowed", True)
            for v in kyverno_result.get("violations", []):
                violations.append(PolicyViolation(
                    policy_name=v.get("policy", "unknown"),
                    message=v.get("message", "Kyverno policy violation"),
                    severity=ViolationSeverity(v.get("severity", "high")),
                    source="kyverno",
                    resource=v.get("resource"),
                    remediation=v.get("remediation"),
                ))

        # Process window result
        window_allowed = None
        if isinstance(window_raw, Exception):
            log.error("Window check failed: %s", window_raw)
            if self.deny_on_error:
                violations.append(PolicyViolation(
                    policy_name="window_error",
                    message=str(window_result),
                    severity=ViolationSeverity.CRITICAL,
                    source="window",
                ))
        elif window_result:
            window_allowed = window_result.get("allowed", True)
            if not window_allowed:
                violations.append(PolicyViolation(
                    policy_name="change_window",
                    message=window_result.get("reason", "Change not allowed in current window"),
                    severity=ViolationSeverity.HIGH,
                    source="window",
                    remediation="Wait for change window or request emergency override",
                ))
            if window_result.get("requires_approval"):
                approval_reasons.append("Change window requires approval")

        # Process criteria result
        criteria_passed = None
        if isinstance(criteria_raw, Exception):
            log.error("Criteria evaluation failed: %s", criteria_raw)
            if self.deny_on_error:
                violations.append(PolicyViolation(
                    policy_name="criteria_error",
                    message=str(criteria_result),
                    severity=ViolationSeverity.CRITICAL,
                    source="criteria",
                ))
        elif criteria_result:
            criteria_passed = criteria_result.get("passed", True)
            if not criteria_passed:
                for e in criteria_result.get("failed_criteria", []):
                    violations.append(PolicyViolation(
                        policy_name=f"criteria_{e.get('name', 'unknown')}",
                        message=e.get("message", "Entry criterion not met"),
                        severity=ViolationSeverity.HIGH,
                        source="criteria",
                        remediation=e.get("remediation_hint"),
                    ))

        # Determine final decision
        has_critical = any(v.severity == ViolationSeverity.CRITICAL for v in violations)
        has_high = any(v.severity == ViolationSeverity.HIGH for v in violations)

        if has_critical:
            decision = PolicyDecision.DENY
        elif has_high:
            decision = PolicyDecision.DENY
        elif approval_reasons:
            decision = PolicyDecision.REQUIRE_APPROVAL
        elif warnings:
            decision = PolicyDecision.WARN
        else:
            decision = PolicyDecision.ALLOW

        duration_ms = (time.monotonic() - start_time) * 1000

        result = PolicyEvaluationResult(
            decision=decision,
            violations=violations,
            warnings=warnings,
            opa_allowed=opa_allowed,
            kyverno_allowed=kyverno_allowed,
            window_allowed=window_allowed,
            criteria_passed=criteria_passed,
            evaluation_id=evaluation_id,
            duration_ms=duration_ms,
            requires_approval=bool(approval_reasons),
            approval_reasons=approval_reasons,
        )

        # Record audit
        await self._record_audit(
            evaluation_id=evaluation_id,
            action=action,
            resource=json.dumps(resource),
            decision=decision,
            violations_count=len(violations),
            requested_by=requested_by,
            metadata={"duration_ms": duration_ms},
        )

        log.info(
            "Policy evaluation %s: %s for %s -> %s (%d violations, %.1fms)",
            evaluation_id, action, resource.get("name", "unknown"),
            decision.value, len(violations), duration_ms
        )

        return result

    async def _evaluate_opa(
        self,
        action: str,
        input_data: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Evaluate OPA policies."""
        if not self.opa:
            return None

        try:
            result = await self.opa.evaluate(
                policy_path=f"omnipotent/{action}",
                input_data=input_data,
            )
            return result.to_dict() if hasattr(result, "to_dict") else result
        except Exception as e:
            log.error("OPA evaluation error: %s", e)
            raise

    async def _evaluate_kyverno(
        self,
        resource: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Evaluate Kyverno policies for Kubernetes resources."""
        if not self.kyverno:
            return None

        # Only evaluate if resource looks like K8s resource
        if "kind" not in resource and "apiVersion" not in resource:
            return None

        try:
            result = await self.kyverno.evaluate(resource)
            return result.to_dict() if hasattr(result, "to_dict") else result
        except Exception as e:
            log.error("Kyverno evaluation error: %s", e)
            raise

    async def _evaluate_windows(
        self,
        action: str,
        resource: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Evaluate change window policies."""
        if not self.windows:
            return None

        try:
            result = self.windows.check_change_allowed(
                action_type=action,
                risk_score=context.get("risk_score", 0.5),
                environment=resource.get("environment") or context.get("environment"),
                service=resource.get("service") or resource.get("name"),
                team=context.get("team"),
            )
            return result.to_dict() if hasattr(result, "to_dict") else result
        except Exception as e:
            log.error("Window check error: %s", e)
            raise

    async def _evaluate_entry_criteria(
        self,
        action: str,
        context: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Evaluate entry criteria."""
        if not self.criteria:
            return None

        # Get criteria template for action
        template = self.criteria.get_entry_template(action)
        if not template:
            return {"passed": True}

        try:
            result = await self.criteria.evaluate_entry_criteria(template, context)
            return result.to_dict() if hasattr(result, "to_dict") else result
        except Exception as e:
            log.error("Criteria evaluation error: %s", e)
            raise

    async def request_approval(
        self,
        evaluation_id: str,
        reason: str,
        requested_by: str,
        approvers: list[str] | None = None,
    ) -> str:
        """Request approval for a policy decision.

        Returns:
            Approval request ID
        """
        approval_id = hashlib.sha256(
            f"{evaluation_id}:{datetime.now(timezone.utc).isoformat()}".encode()
        ).hexdigest()[:16]

        self._pending_approvals[approval_id] = {
            "evaluation_id": evaluation_id,
            "reason": reason,
            "requested_by": requested_by,
            "requested_at": datetime.now(timezone.utc).isoformat(),
            "approvers": approvers or [],
            "status": "pending",
        }

        log.info("Approval requested: %s for evaluation %s", approval_id, evaluation_id)
        return approval_id

    async def approve(
        self,
        approval_id: str,
        approved_by: str,
        notes: str = "",
    ) -> bool:
        """Approve a pending request."""
        if approval_id not in self._pending_approvals:
            return False

        approval = self._pending_approvals[approval_id]
        approval["status"] = "approved"
        approval["approved_by"] = approved_by
        approval["approved_at"] = datetime.now(timezone.utc).isoformat()
        approval["notes"] = notes

        # Update audit record
        await self._record_audit(
            evaluation_id=approval["evaluation_id"],
            action="approval",
            resource=approval_id,
            decision=PolicyDecision.ALLOW,
            violations_count=0,
            requested_by=approval["requested_by"],
            approved_by=approved_by,
            metadata={"notes": notes},
        )

        log.info("Approval granted: %s by %s", approval_id, approved_by)
        return True

    async def reject(
        self,
        approval_id: str,
        rejected_by: str,
        reason: str = "",
    ) -> bool:
        """Reject a pending request."""
        if approval_id not in self._pending_approvals:
            return False

        approval = self._pending_approvals[approval_id]
        approval["status"] = "rejected"
        approval["rejected_by"] = rejected_by
        approval["rejected_at"] = datetime.now(timezone.utc).isoformat()
        approval["rejection_reason"] = reason

        log.info("Approval rejected: %s by %s - %s", approval_id, rejected_by, reason)
        return True

    def get_approval_status(self, approval_id: str) -> dict[str, Any] | None:
        """Get status of an approval request."""
        return self._pending_approvals.get(approval_id)

    async def _record_audit(
        self,
        evaluation_id: str,
        action: str,
        resource: str,
        decision: PolicyDecision,
        violations_count: int,
        requested_by: str,
        approved_by: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Record an audit entry."""
        record = AuditRecord(
            evaluation_id=evaluation_id,
            timestamp=datetime.now(timezone.utc),
            action=action,
            resource=resource,
            decision=decision,
            violations_count=violations_count,
            requested_by=requested_by,
            approved_by=approved_by,
            metadata=metadata or {},
        )

        self._audit_log.append(record)

        # Persist to Redis if available
        if self.redis:
            try:
                await self.redis.lpush(
                    "policy_audit_log",
                    json.dumps(record.to_dict())
                )
            except Exception as e:
                log.warning("Failed to persist audit record: %s", e)

    def version_policy(
        self,
        policy_name: str,
        policy_type: str,
        content: str,
        created_by: str,
    ) -> PolicyVersion:
        """Create a new version of a policy."""
        checksum = hashlib.sha256(content.encode()).hexdigest()[:12]

        # Get current version number
        versions = self._policy_versions.get(policy_name, [])
        version_num = len(versions) + 1
        version_str = f"v{version_num}"

        # Deactivate previous versions
        for v in versions:
            v.is_active = False

        version = PolicyVersion(
            version=version_str,
            policy_name=policy_name,
            policy_type=policy_type,
            content=content,
            created_at=datetime.now(timezone.utc),
            created_by=created_by,
            checksum=checksum,
            is_active=True,
        )

        if policy_name not in self._policy_versions:
            self._policy_versions[policy_name] = []
        self._policy_versions[policy_name].append(version)

        log.info("Created policy version: %s %s (%s)", policy_name, version_str, checksum)
        return version

    def get_policy_version(
        self,
        policy_name: str,
        version: str | None = None,
    ) -> PolicyVersion | None:
        """Get a specific version of a policy."""
        versions = self._policy_versions.get(policy_name, [])

        if version:
            return next((v for v in versions if v.version == version), None)

        # Return active version
        return next((v for v in versions if v.is_active), None)

    def list_policy_versions(self, policy_name: str) -> list[PolicyVersion]:
        """List all versions of a policy."""
        return self._policy_versions.get(policy_name, [])

    def rollback_policy(
        self,
        policy_name: str,
        to_version: str,
    ) -> PolicyVersion | None:
        """Rollback a policy to a previous version."""
        versions = self._policy_versions.get(policy_name, [])
        target = next((v for v in versions if v.version == to_version), None)

        if not target:
            return None

        # Deactivate all, activate target
        for v in versions:
            v.is_active = False
        target.is_active = True

        log.info("Rolled back policy %s to %s", policy_name, to_version)
        return target

    def get_audit_log(
        self,
        limit: int = 100,
        action: str | None = None,
        decision: PolicyDecision | None = None,
    ) -> list[AuditRecord]:
        """Get audit log entries."""
        records = self._audit_log

        if action:
            records = [r for r in records if r.action == action]
        if decision:
            records = [r for r in records if r.decision == decision]

        return records[-limit:]

    def get_stats(self) -> dict[str, Any]:
        """Get engine statistics."""
        decisions: dict[str, int] = {}
        for record in self._audit_log:
            key = record.decision.value
            decisions[key] = decisions.get(key, 0) + 1

        return {
            "total_evaluations": self._evaluation_counter,
            "audit_records": len(self._audit_log),
            "pending_approvals": len([
                a for a in self._pending_approvals.values()
                if a["status"] == "pending"
            ]),
            "policy_versions": sum(
                len(v) for v in self._policy_versions.values()
            ),
            "decisions": decisions,
        }


def create_policy_engine(
    opa_client: Any = None,
    kyverno_client: Any = None,
    change_window_manager: Any = None,
    criteria_evaluator: Any = None,
    redis_client: Any = None,
) -> PolicyEngine:
    """Factory function to create a policy engine."""
    return PolicyEngine(
        opa_client=opa_client,
        kyverno_client=kyverno_client,
        change_window_manager=change_window_manager,
        criteria_evaluator=criteria_evaluator,
        redis_client=redis_client,
    )
