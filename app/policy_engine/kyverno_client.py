"""Kyverno Client - Kubernetes-native policy engine integration.

Provides:
- Kyverno policy management via Kubernetes API
- Policy validation against resources
- Mutation and generation policies
- Policy reports
"""

from __future__ import annotations

import asyncio
import logging
import yaml
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("devcopilot.policy_engine.kyverno")


class KyvernoPolicyType(str, Enum):
    """Types of Kyverno policies."""
    VALIDATE = "validate"
    MUTATE = "mutate"
    GENERATE = "generate"
    VERIFY_IMAGES = "verifyImages"


class KyvernoValidationAction(str, Enum):
    """Actions for validation failures."""
    ENFORCE = "Enforce"
    AUDIT = "Audit"


@dataclass
class KyvernoRule:
    """A rule within a Kyverno policy."""
    name: str
    match: dict[str, Any]  # Resource matching criteria
    policy_type: KyvernoPolicyType
    definition: dict[str, Any]  # The actual rule (validate/mutate/generate)
    exclude: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        rule = {
            "name": self.name,
            "match": self.match,
        }
        if self.exclude:
            rule["exclude"] = self.exclude
        rule[self.policy_type.value] = self.definition
        return rule


@dataclass
class KyvernoPolicy:
    """A Kyverno ClusterPolicy or Policy definition."""
    name: str
    rules: list[KyvernoRule]
    validation_action: KyvernoValidationAction = KyvernoValidationAction.ENFORCE
    background: bool = True
    namespace: str | None = None  # None = ClusterPolicy
    description: str = ""
    annotations: dict[str, str] = field(default_factory=dict)

    def to_k8s_manifest(self) -> dict[str, Any]:
        """Convert to Kubernetes manifest format."""
        kind = "Policy" if self.namespace else "ClusterPolicy"
        api_version = "kyverno.io/v1"

        manifest = {
            "apiVersion": api_version,
            "kind": kind,
            "metadata": {
                "name": self.name,
                "annotations": {
                    "policies.kyverno.io/description": self.description,
                    **self.annotations,
                },
            },
            "spec": {
                "validationFailureAction": self.validation_action.value,
                "background": self.background,
                "rules": [rule.to_dict() for rule in self.rules],
            },
        }

        if self.namespace:
            manifest["metadata"]["namespace"] = self.namespace

        return manifest

    def to_yaml(self) -> str:
        """Convert to YAML string."""
        return yaml.dump(self.to_k8s_manifest(), default_flow_style=False)


@dataclass
class KyvernoViolation:
    """A policy violation from Kyverno."""
    policy: str
    rule: str
    resource_kind: str
    resource_name: str
    resource_namespace: str
    message: str
    severity: str = "medium"
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy": self.policy,
            "rule": self.rule,
            "resource_kind": self.resource_kind,
            "resource_name": self.resource_name,
            "resource_namespace": self.resource_namespace,
            "message": self.message,
            "severity": self.severity,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class KyvernoResult:
    """Result from Kyverno policy evaluation."""
    allowed: bool
    policy_name: str
    violations: list[KyvernoViolation] = field(default_factory=list)
    mutations: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    evaluated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "policy_name": self.policy_name,
            "violations": [v.to_dict() for v in self.violations],
            "mutations": self.mutations,
            "warnings": self.warnings,
            "evaluated_at": self.evaluated_at.isoformat(),
        }


# Default Kyverno policies for DevOps Copilot
DEFAULT_KYVERNO_POLICIES = [
    KyvernoPolicy(
        name="devcopilot-require-labels",
        description="Require standard labels on all deployments",
        rules=[
            KyvernoRule(
                name="require-app-label",
                match={
                    "any": [
                        {"resources": {"kinds": ["Deployment", "StatefulSet", "DaemonSet"]}}
                    ]
                },
                policy_type=KyvernoPolicyType.VALIDATE,
                definition={
                    "message": "The label 'app.kubernetes.io/name' is required",
                    "pattern": {
                        "metadata": {
                            "labels": {
                                "app.kubernetes.io/name": "?*"
                            }
                        }
                    }
                },
            ),
            KyvernoRule(
                name="require-team-label",
                match={
                    "any": [
                        {"resources": {"kinds": ["Deployment", "StatefulSet"]}}
                    ]
                },
                policy_type=KyvernoPolicyType.VALIDATE,
                definition={
                    "message": "The label 'team' is required for tracking ownership",
                    "pattern": {
                        "metadata": {
                            "labels": {
                                "team": "?*"
                            }
                        }
                    }
                },
            ),
        ],
    ),
    KyvernoPolicy(
        name="devcopilot-resource-limits",
        description="Require resource limits on containers",
        rules=[
            KyvernoRule(
                name="require-limits",
                match={
                    "any": [
                        {"resources": {"kinds": ["Deployment", "StatefulSet", "DaemonSet", "Job"]}}
                    ]
                },
                policy_type=KyvernoPolicyType.VALIDATE,
                definition={
                    "message": "CPU and memory limits are required",
                    "pattern": {
                        "spec": {
                            "template": {
                                "spec": {
                                    "containers": [
                                        {
                                            "resources": {
                                                "limits": {
                                                    "cpu": "?*",
                                                    "memory": "?*"
                                                }
                                            }
                                        }
                                    ]
                                }
                            }
                        }
                    }
                },
            ),
        ],
    ),
    KyvernoPolicy(
        name="devcopilot-no-latest-tag",
        description="Disallow 'latest' image tag in production",
        rules=[
            KyvernoRule(
                name="no-latest",
                match={
                    "any": [
                        {
                            "resources": {
                                "kinds": ["Deployment", "StatefulSet", "DaemonSet"],
                                "namespaces": ["production", "prod"],
                            }
                        }
                    ]
                },
                policy_type=KyvernoPolicyType.VALIDATE,
                definition={
                    "message": "Using 'latest' tag is not allowed in production",
                    "pattern": {
                        "spec": {
                            "template": {
                                "spec": {
                                    "containers": [
                                        {
                                            "image": "!*:latest"
                                        }
                                    ]
                                }
                            }
                        }
                    }
                },
            ),
        ],
    ),
    KyvernoPolicy(
        name="devcopilot-replica-minimum",
        description="Require minimum replicas for production deployments",
        rules=[
            KyvernoRule(
                name="min-replicas",
                match={
                    "any": [
                        {
                            "resources": {
                                "kinds": ["Deployment"],
                                "namespaces": ["production", "prod"],
                            }
                        }
                    ]
                },
                policy_type=KyvernoPolicyType.VALIDATE,
                definition={
                    "message": "Production deployments must have at least 2 replicas",
                    "deny": {
                        "conditions": {
                            "all": [
                                {
                                    "key": "{{request.object.spec.replicas}}",
                                    "operator": "LessThan",
                                    "value": 2
                                }
                            ]
                        }
                    }
                },
            ),
        ],
    ),
    KyvernoPolicy(
        name="devcopilot-add-defaults",
        description="Add default labels and annotations",
        rules=[
            KyvernoRule(
                name="add-managed-by",
                match={
                    "any": [
                        {"resources": {"kinds": ["Deployment", "StatefulSet", "Service"]}}
                    ]
                },
                policy_type=KyvernoPolicyType.MUTATE,
                definition={
                    "patchStrategicMerge": {
                        "metadata": {
                            "labels": {
                                "app.kubernetes.io/managed-by": "devcopilot"
                            },
                            "annotations": {
                                "devcopilot.io/last-modified": "{{time.Now()}}"
                            }
                        }
                    }
                },
            ),
        ],
    ),
]


class KyvernoClient:
    """Client for Kyverno policy engine.

    Supports:
    - Policy CRUD via Kubernetes API
    - Dry-run validation
    - Policy reports
    - Embedded evaluation (for testing)
    """

    def __init__(
        self,
        k8s_client: Any = None,
        embedded_mode: bool = True,
    ):
        """Initialize Kyverno client.

        Args:
            k8s_client: Kubernetes client (kubernetes.client)
            embedded_mode: If True, evaluate policies locally when K8s is unavailable
        """
        self.k8s = k8s_client
        self.embedded_mode = embedded_mode
        self._policies: dict[str, KyvernoPolicy] = {}
        self._eval_count = 0

        # Load default policies
        self._load_default_policies()

    def _load_default_policies(self) -> None:
        """Load default Kyverno policies."""
        for policy in DEFAULT_KYVERNO_POLICIES:
            self._policies[policy.name] = policy

    async def evaluate(
        self,
        resource: dict[str, Any],
        namespace: str = "default",
    ) -> list[KyvernoResult]:
        """Evaluate all applicable policies against a resource.

        Args:
            resource: Kubernetes resource manifest
            namespace: Namespace context for evaluation

        Returns:
            List of KyvernoResult for each applicable policy
        """
        self._eval_count += 1
        results = []

        resource_kind = resource.get("kind", "")
        resource_name = resource.get("metadata", {}).get("name", "unknown")
        resource_namespace = resource.get("metadata", {}).get("namespace", namespace)

        for policy in self._policies.values():
            # Check if policy applies to this resource
            if not self._policy_matches(policy, resource_kind, resource_namespace):
                continue

            # Evaluate policy
            if self.k8s and not self.embedded_mode:
                result = await self._evaluate_via_k8s(policy, resource)
            else:
                result = self._evaluate_embedded(policy, resource, resource_name, resource_namespace)

            results.append(result)

        return results

    def _policy_matches(
        self,
        policy: KyvernoPolicy,
        resource_kind: str,
        resource_namespace: str,
    ) -> bool:
        """Check if a policy applies to a resource."""
        for rule in policy.rules:
            match = rule.match
            any_matches = match.get("any", [])

            for condition in any_matches:
                resources = condition.get("resources", {})
                kinds = resources.get("kinds", [])
                namespaces = resources.get("namespaces", [])

                if kinds and resource_kind not in kinds:
                    continue

                if namespaces and resource_namespace not in namespaces:
                    continue

                return True

        return False

    async def _evaluate_via_k8s(
        self,
        policy: KyvernoPolicy,
        resource: dict[str, Any],
    ) -> KyvernoResult:
        """Evaluate policy via Kubernetes API (dry-run)."""
        # In production, this would use the K8s API with dry-run
        # to trigger Kyverno's admission controller
        # For now, fall back to embedded
        return self._evaluate_embedded(
            policy,
            resource,
            resource.get("metadata", {}).get("name", "unknown"),
            resource.get("metadata", {}).get("namespace", "default"),
        )

    def _evaluate_embedded(
        self,
        policy: KyvernoPolicy,
        resource: dict[str, Any],
        resource_name: str,
        resource_namespace: str,
    ) -> KyvernoResult:
        """Evaluate policy using embedded logic."""
        violations = []
        mutations = []
        warnings = []

        for rule in policy.rules:
            if rule.policy_type == KyvernoPolicyType.VALIDATE:
                violation = self._check_validation_rule(
                    policy.name, rule, resource, resource_name, resource_namespace
                )
                if violation:
                    violations.append(violation)

            elif rule.policy_type == KyvernoPolicyType.MUTATE:
                mutation = self._apply_mutation_rule(rule, resource)
                if mutation:
                    mutations.append(mutation)

        allowed = len(violations) == 0 or policy.validation_action == KyvernoValidationAction.AUDIT

        if policy.validation_action == KyvernoValidationAction.AUDIT and violations:
            warnings.extend([v.message for v in violations])
            violations = []  # Don't block in audit mode

        return KyvernoResult(
            allowed=allowed,
            policy_name=policy.name,
            violations=violations,
            mutations=mutations,
            warnings=warnings,
        )

    def _check_validation_rule(
        self,
        policy_name: str,
        rule: KyvernoRule,
        resource: dict[str, Any],
        resource_name: str,
        resource_namespace: str,
    ) -> KyvernoViolation | None:
        """Check a validation rule against a resource."""
        definition = rule.definition
        message = definition.get("message", f"Validation failed for rule {rule.name}")

        # Pattern matching
        if "pattern" in definition:
            pattern = definition["pattern"]
            if not self._matches_pattern(resource, pattern):
                return KyvernoViolation(
                    policy=policy_name,
                    rule=rule.name,
                    resource_kind=resource.get("kind", ""),
                    resource_name=resource_name,
                    resource_namespace=resource_namespace,
                    message=message,
                )

        # Deny conditions
        if "deny" in definition:
            deny = definition["deny"]
            conditions = deny.get("conditions", {})
            if self._evaluate_conditions(conditions, resource):
                return KyvernoViolation(
                    policy=policy_name,
                    rule=rule.name,
                    resource_kind=resource.get("kind", ""),
                    resource_name=resource_name,
                    resource_namespace=resource_namespace,
                    message=message,
                )

        return None

    def _matches_pattern(self, resource: dict[str, Any], pattern: dict[str, Any]) -> bool:
        """Check if resource matches a pattern."""
        for key, expected in pattern.items():
            actual = resource.get(key)

            if isinstance(expected, dict) and isinstance(actual, dict):
                if not self._matches_pattern(actual, expected):
                    return False
            elif isinstance(expected, list) and isinstance(actual, list):
                # For lists, check if all patterns are satisfied
                for exp_item in expected:
                    if isinstance(exp_item, dict):
                        matched = any(
                            self._matches_pattern(act_item, exp_item)
                            for act_item in actual
                            if isinstance(act_item, dict)
                        )
                        if not matched:
                            return False
            elif isinstance(expected, str):
                if expected == "?*":
                    # Required field - must exist and be non-empty
                    if not actual:
                        return False
                elif expected.startswith("!"):
                    # Negation pattern
                    pattern_str = expected[1:]
                    if pattern_str.endswith("*"):
                        # Wildcard
                        prefix = pattern_str[:-1]
                        if isinstance(actual, str) and actual.startswith(prefix):
                            return False
                    elif actual == pattern_str:
                        return False
                elif actual != expected:
                    return False

        return True

    def _evaluate_conditions(
        self,
        conditions: dict[str, Any],
        resource: dict[str, Any],
    ) -> bool:
        """Evaluate deny conditions."""
        all_conditions = conditions.get("all", [])

        for cond in all_conditions:
            key = cond.get("key", "")
            operator = cond.get("operator", "Equals")
            value = cond.get("value")

            # Simple key extraction (real Kyverno has JMESPath)
            actual_value = self._extract_value(key, resource)

            if operator == "LessThan":
                if actual_value is not None and actual_value < value:
                    return True
            elif operator == "Equals":
                if actual_value == value:
                    return True

        return False

    def _extract_value(self, key: str, resource: dict[str, Any]) -> Any:
        """Extract value from resource using key path."""
        # Simple extraction - real Kyverno uses JMESPath
        # Handle {{request.object.spec.replicas}} format
        if key.startswith("{{") and key.endswith("}}"):
            path = key[2:-2].replace("request.object.", "")
        else:
            path = key

        parts = path.split(".")
        current = resource

        for part in parts:
            if isinstance(current, dict):
                current = current.get(part)
            else:
                return None

        return current

    def _apply_mutation_rule(
        self,
        rule: KyvernoRule,
        resource: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Apply a mutation rule to a resource."""
        definition = rule.definition

        if "patchStrategicMerge" in definition:
            patch = definition["patchStrategicMerge"]
            return {
                "rule": rule.name,
                "patch_type": "strategic_merge",
                "patch": patch,
            }

        return None

    def register_policy(self, policy: KyvernoPolicy) -> None:
        """Register a Kyverno policy."""
        self._policies[policy.name] = policy
        log.info("Registered Kyverno policy: %s", policy.name)

        # Apply to cluster if K8s client available
        if self.k8s:
            asyncio.create_task(self._apply_to_cluster(policy))

    async def _apply_to_cluster(self, policy: KyvernoPolicy) -> None:
        """Apply a policy to the Kubernetes cluster."""
        try:
            # In production, use k8s client to create/update ClusterPolicy
            log.info("Would apply policy %s to cluster", policy.name)
        except Exception as e:
            log.warning("Failed to apply policy to cluster: %s", e)

    def get_policy(self, name: str) -> KyvernoPolicy | None:
        """Get a policy by name."""
        return self._policies.get(name)

    def list_policies(self) -> list[KyvernoPolicy]:
        """List all registered policies."""
        return list(self._policies.values())

    def delete_policy(self, name: str) -> bool:
        """Delete a policy."""
        if name in self._policies:
            del self._policies[name]
            log.info("Deleted Kyverno policy: %s", name)
            return True
        return False

    def get_stats(self) -> dict[str, Any]:
        """Get client statistics."""
        return {
            "evaluations": self._eval_count,
            "policies_registered": len(self._policies),
            "embedded_mode": self.embedded_mode,
            "k8s_connected": self.k8s is not None,
        }


def create_kyverno_client(
    k8s_client: Any = None,
    embedded_mode: bool = True,
) -> KyvernoClient:
    """Factory function to create a Kyverno client."""
    return KyvernoClient(k8s_client=k8s_client, embedded_mode=embedded_mode)
