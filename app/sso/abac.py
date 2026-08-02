"""Attribute-Based Access Control (ABAC) for dynamic permissions.

Resolves permissions from IdP attributes dynamically, supporting:
- Department-based access
- Team/role-based permissions
- Level/seniority escalation
- Time-based access (on-call, incident escalation)
- Custom attribute rules
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, time, timezone
from enum import Enum, auto
from typing import Any, Callable

log = logging.getLogger("omnipotent.sso.abac")


class Permission(Enum):
    """Available permissions in the system."""

    # Viewing permissions
    VIEW_INCIDENTS = auto()
    VIEW_LOGS = auto()
    VIEW_METRICS = auto()
    VIEW_TRACES = auto()
    VIEW_DASHBOARDS = auto()
    VIEW_RUNBOOKS = auto()
    VIEW_CONFIGS = auto()
    VIEW_BILLING = auto()
    VIEW_AUDIT_LOGS = auto()

    # Investigation permissions
    INVESTIGATE = auto()
    SEARCH_LOGS = auto()
    QUERY_METRICS = auto()
    ANALYZE_TRACES = auto()

    # Action permissions
    APPROVE_RUNBOOKS = auto()
    EXECUTE_ACTIONS = auto()
    RESTART_SERVICES = auto()
    SCALE_DEPLOYMENTS = auto()
    ROLLBACK_DEPLOYMENTS = auto()

    # Production access
    PRODUCTION_ACCESS = auto()
    APPROVE_DEPLOYS = auto()
    MODIFY_ALERTS = auto()
    MANAGE_ONCALL = auto()

    # Admin permissions
    MANAGE_USERS = auto()
    MANAGE_TEAMS = auto()
    MANAGE_API_KEYS = auto()
    MANAGE_INTEGRATIONS = auto()
    MANAGE_SSO = auto()

    # Elevated permissions
    ELEVATED_ACCESS = auto()
    EMERGENCY_ACCESS = auto()
    BREAK_GLASS = auto()


class ConditionOperator(Enum):
    """Operators for ABAC conditions."""

    EQUALS = "eq"
    NOT_EQUALS = "ne"
    IN = "in"
    NOT_IN = "not_in"
    CONTAINS = "contains"
    STARTS_WITH = "starts_with"
    ENDS_WITH = "ends_with"
    MATCHES = "matches"
    GREATER_THAN = "gt"
    LESS_THAN = "lt"
    EXISTS = "exists"
    NOT_EXISTS = "not_exists"


@dataclass
class ABACCondition:
    """A single ABAC condition."""

    attribute: str
    operator: ConditionOperator
    value: Any

    def evaluate(self, attributes: dict[str, Any]) -> bool:
        """Evaluate this condition against given attributes."""
        attr_value = self._get_nested_attribute(attributes, self.attribute)

        if self.operator == ConditionOperator.EXISTS:
            return attr_value is not None
        if self.operator == ConditionOperator.NOT_EXISTS:
            return attr_value is None

        if attr_value is None:
            return False

        if self.operator == ConditionOperator.EQUALS:
            return attr_value == self.value
        elif self.operator == ConditionOperator.NOT_EQUALS:
            return attr_value != self.value
        elif self.operator == ConditionOperator.IN:
            if isinstance(attr_value, list):
                return any(v in self.value for v in attr_value)
            return attr_value in self.value
        elif self.operator == ConditionOperator.NOT_IN:
            if isinstance(attr_value, list):
                return not any(v in self.value for v in attr_value)
            return attr_value not in self.value
        elif self.operator == ConditionOperator.CONTAINS:
            if isinstance(attr_value, str):
                return self.value in attr_value
            if isinstance(attr_value, list):
                return self.value in attr_value
            return False
        elif self.operator == ConditionOperator.STARTS_WITH:
            return isinstance(attr_value, str) and attr_value.startswith(self.value)
        elif self.operator == ConditionOperator.ENDS_WITH:
            return isinstance(attr_value, str) and attr_value.endswith(self.value)
        elif self.operator == ConditionOperator.MATCHES:
            return isinstance(attr_value, str) and bool(re.match(self.value, attr_value))
        elif self.operator == ConditionOperator.GREATER_THAN:
            return attr_value > self.value
        elif self.operator == ConditionOperator.LESS_THAN:
            return attr_value < self.value

        return False

    def _get_nested_attribute(self, attributes: dict[str, Any], path: str) -> Any:
        """Get a nested attribute using dot notation."""
        parts = path.split(".")
        current: Any = attributes
        for part in parts:
            if isinstance(current, dict):
                current = current.get(part)
            else:
                return None
            if current is None:
                return None
        return current


@dataclass
class ABACRule:
    """An ABAC rule that grants permissions based on conditions."""

    id: str
    name: str
    description: str
    conditions: list[ABACCondition]
    permissions: list[Permission]
    priority: int = 100
    enabled: bool = True
    require_all_conditions: bool = True
    time_restrictions: dict[str, Any] | None = None
    resource_restrictions: dict[str, Any] | None = None

    def evaluate(self, attributes: dict[str, Any], context: dict[str, Any] | None = None) -> bool:
        """Evaluate if this rule applies to given attributes and context."""
        if not self.enabled:
            return False

        if self.time_restrictions and not self._check_time_restrictions():
            return False

        if context and self.resource_restrictions:
            if not self._check_resource_restrictions(context):
                return False

        if self.require_all_conditions:
            return all(cond.evaluate(attributes) for cond in self.conditions)
        else:
            return any(cond.evaluate(attributes) for cond in self.conditions)

    def _check_time_restrictions(self) -> bool:
        """Check if current time falls within allowed time window."""
        if not self.time_restrictions:
            return True

        now = datetime.now(timezone.utc)
        current_time = now.time()

        start = self.time_restrictions.get("start_time")
        end = self.time_restrictions.get("end_time")
        if start and end:
            start_time = time.fromisoformat(start)
            end_time = time.fromisoformat(end)
            if start_time <= end_time:
                if not (start_time <= current_time <= end_time):
                    return False
            else:
                if not (current_time >= start_time or current_time <= end_time):
                    return False

        days = self.time_restrictions.get("days_of_week")
        if days:
            if now.weekday() not in days:
                return False

        return True

    def _check_resource_restrictions(self, context: dict[str, Any]) -> bool:
        """Check if access to specific resource is allowed."""
        if not self.resource_restrictions:
            return True

        allowed_services = self.resource_restrictions.get("services")
        if allowed_services:
            resource_service = context.get("service")
            if resource_service and resource_service not in allowed_services:
                return False

        allowed_envs = self.resource_restrictions.get("environments")
        if allowed_envs:
            resource_env = context.get("environment")
            if resource_env and resource_env not in allowed_envs:
                return False

        return True


@dataclass
class ABACPolicy:
    """A collection of ABAC rules forming a policy."""

    id: str
    name: str
    description: str
    rules: list[ABACRule] = field(default_factory=list)
    default_permissions: list[Permission] = field(default_factory=list)
    deny_rules: list[ABACRule] = field(default_factory=list)

    def add_rule(self, rule: ABACRule) -> None:
        """Add a rule to the policy."""
        self.rules.append(rule)
        self.rules.sort(key=lambda r: r.priority)

    def add_deny_rule(self, rule: ABACRule) -> None:
        """Add a deny rule (evaluated first)."""
        self.deny_rules.append(rule)

    def evaluate(
        self,
        attributes: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> set[Permission]:
        """Evaluate policy and return granted permissions."""
        permissions = set(self.default_permissions)

        for deny_rule in self.deny_rules:
            if deny_rule.evaluate(attributes, context):
                for perm in deny_rule.permissions:
                    permissions.discard(perm)

        for rule in self.rules:
            if rule.evaluate(attributes, context):
                permissions.update(rule.permissions)

        return permissions


class ABACPermissionResolver:
    """Resolves permissions from IdP attributes using ABAC rules."""

    def __init__(self) -> None:
        self._policies: dict[str, ABACPolicy] = {}
        self._dynamic_rules: list[Callable[[dict[str, Any]], set[Permission]]] = []
        self._active_incidents: dict[str, datetime] = {}
        self._setup_default_policy()

    def _setup_default_policy(self) -> None:
        """Set up default ABAC policy with common rules."""
        policy = ABACPolicy(
            id="default",
            name="Default Policy",
            description="Default ABAC policy for DevOps Copilot",
            default_permissions=[Permission.VIEW_INCIDENTS, Permission.VIEW_DASHBOARDS],
        )

        policy.add_rule(ABACRule(
            id="engineering-view",
            name="Engineering View Access",
            description="All engineering department members can view incidents and logs",
            conditions=[
                ABACCondition("department", ConditionOperator.EQUALS, "engineering"),
            ],
            permissions=[
                Permission.VIEW_INCIDENTS,
                Permission.VIEW_LOGS,
                Permission.VIEW_METRICS,
                Permission.VIEW_TRACES,
                Permission.INVESTIGATE,
                Permission.SEARCH_LOGS,
            ],
            priority=100,
        ))

        policy.add_rule(ABACRule(
            id="sre-actions",
            name="SRE Action Permissions",
            description="SRE team can approve runbooks and execute actions",
            conditions=[
                ABACCondition("team", ConditionOperator.IN, ["sre", "platform", "devops"]),
            ],
            permissions=[
                Permission.APPROVE_RUNBOOKS,
                Permission.EXECUTE_ACTIONS,
                Permission.RESTART_SERVICES,
                Permission.SCALE_DEPLOYMENTS,
                Permission.MANAGE_ONCALL,
            ],
            priority=90,
        ))

        policy.add_rule(ABACRule(
            id="senior-production",
            name="Senior Engineer Production Access",
            description="Senior engineers can access production",
            conditions=[
                ABACCondition("level", ConditionOperator.IN, ["senior", "staff", "principal", "director"]),
            ],
            permissions=[
                Permission.PRODUCTION_ACCESS,
                Permission.APPROVE_DEPLOYS,
                Permission.ROLLBACK_DEPLOYMENTS,
            ],
            priority=80,
        ))

        policy.add_rule(ABACRule(
            id="oncall-elevated",
            name="On-Call Elevated Access",
            description="On-call engineers get elevated access",
            conditions=[
                ABACCondition("oncall", ConditionOperator.EQUALS, True),
            ],
            permissions=[
                Permission.ELEVATED_ACCESS,
                Permission.EXECUTE_ACTIONS,
                Permission.PRODUCTION_ACCESS,
            ],
            priority=70,
        ))

        policy.add_rule(ABACRule(
            id="finance-billing",
            name="Finance Billing Access",
            description="Finance team can view billing and usage",
            conditions=[
                ABACCondition("department", ConditionOperator.IN, ["finance", "accounting"]),
            ],
            permissions=[
                Permission.VIEW_BILLING,
                Permission.VIEW_DASHBOARDS,
            ],
            priority=100,
        ))

        policy.add_rule(ABACRule(
            id="platform-admin",
            name="Platform Admin",
            description="Platform team admins have full management access",
            conditions=[
                ABACCondition("team", ConditionOperator.EQUALS, "platform"),
                ABACCondition("role", ConditionOperator.IN, ["admin", "owner", "manager"]),
            ],
            permissions=[
                Permission.MANAGE_USERS,
                Permission.MANAGE_TEAMS,
                Permission.MANAGE_API_KEYS,
                Permission.MANAGE_INTEGRATIONS,
                Permission.VIEW_AUDIT_LOGS,
            ],
            priority=50,
        ))

        policy.add_rule(ABACRule(
            id="security-sso-admin",
            name="Security SSO Admin",
            description="Security team can manage SSO configuration",
            conditions=[
                ABACCondition("team", ConditionOperator.EQUALS, "security"),
            ],
            permissions=[
                Permission.MANAGE_SSO,
                Permission.VIEW_AUDIT_LOGS,
            ],
            priority=60,
        ))

        self._policies["default"] = policy

    def register_policy(self, policy: ABACPolicy) -> None:
        """Register a custom ABAC policy."""
        self._policies[policy.id] = policy

    def add_dynamic_rule(
        self,
        rule: Callable[[dict[str, Any]], set[Permission]],
    ) -> None:
        """Add a dynamic rule function that is evaluated at runtime."""
        self._dynamic_rules.append(rule)

    def set_active_incident(self, user_email: str, incident_id: str) -> None:
        """Mark a user as actively working an incident."""
        self._active_incidents[user_email] = datetime.now(timezone.utc)

    def clear_active_incident(self, user_email: str) -> None:
        """Clear a user's active incident status."""
        self._active_incidents.pop(user_email, None)

    def resolve_permissions(
        self,
        attributes: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> set[Permission]:
        """Resolve all permissions for given attributes."""
        permissions: set[Permission] = set()

        for policy in self._policies.values():
            permissions.update(policy.evaluate(attributes, context))

        for dynamic_rule in self._dynamic_rules:
            try:
                permissions.update(dynamic_rule(attributes))
            except Exception as e:
                log.warning("dynamic rule failed", extra={"error": str(e)})

        email = attributes.get("email")
        if email and email in self._active_incidents:
            permissions.add(Permission.ELEVATED_ACCESS)
            log.info(
                "elevated access granted for active incident",
                extra={"email": email},
            )

        return permissions

    def check_permission(
        self,
        attributes: dict[str, Any],
        permission: Permission,
        context: dict[str, Any] | None = None,
    ) -> bool:
        """Check if a specific permission is granted."""
        permissions = self.resolve_permissions(attributes, context)
        return permission in permissions

    def explain_permissions(
        self,
        attributes: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Explain which rules granted which permissions."""
        explanations: list[dict[str, Any]] = []
        all_permissions: set[Permission] = set()

        for policy_id, policy in self._policies.items():
            default_granted = list(policy.default_permissions)
            if default_granted:
                explanations.append({
                    "policy": policy_id,
                    "rule": "default",
                    "matched": True,
                    "permissions": [p.name for p in default_granted],
                    "reason": "Default permissions",
                })
                all_permissions.update(default_granted)

            for rule in policy.rules:
                matched = rule.evaluate(attributes, context)
                if matched:
                    explanations.append({
                        "policy": policy_id,
                        "rule": rule.id,
                        "name": rule.name,
                        "matched": True,
                        "permissions": [p.name for p in rule.permissions],
                        "reason": rule.description,
                        "conditions": [
                            {
                                "attribute": c.attribute,
                                "operator": c.operator.value,
                                "value": c.value,
                                "actual": attributes.get(c.attribute),
                            }
                            for c in rule.conditions
                        ],
                    })
                    all_permissions.update(rule.permissions)

        return {
            "granted_permissions": [p.name for p in all_permissions],
            "explanations": explanations,
            "attributes_evaluated": attributes,
        }


_resolver: ABACPermissionResolver | None = None


def get_abac_resolver() -> ABACPermissionResolver:
    """Get the default ABAC permission resolver."""
    global _resolver
    if _resolver is None:
        _resolver = ABACPermissionResolver()
    return _resolver
