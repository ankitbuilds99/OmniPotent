"""Kyverno Client - Kubernetes-native policy engine integration.

Provides:
- Kyverno policy management via Kubernetes API
- Policy validation against resources
- Mutation and generation policies
- Policy reports
"""

from __future__ import annotations

import asyncio
import functools
import logging
import yaml
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("omnipotent.policy_engine.kyverno")


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

        metadata: dict[str, Any] = {
            "name": self.name,
            "annotations": {
                "policies.kyverno.io/description": self.description,
                **self.annotations,
            },
        }

        if self.namespace:
            metadata["namespace"] = self.namespace

        manifest: dict[str, Any] = {
            "apiVersion": api_version,
            "kind": kind,
            "metadata": metadata,
            "spec": {
                "validationFailureAction": self.validation_action.value,
                "background": self.background,
                "rules": [rule.to_dict() for rule in self.rules],
            },
        }

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
    # Which evaluator produced this result. "embedded" is the local Python
    # subset of Kyverno's semantics; "cluster" would be a real apiserver
    # dry-run through Kyverno's admission controller. The embedded evaluator
    # used to substitute silently for the cluster path, so a caller had no way
    # to tell an approximate local answer from an authoritative one.
    engine: str = "embedded"

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "policy_name": self.policy_name,
            "violations": [v.to_dict() for v in self.violations],
            "mutations": self.mutations,
            "warnings": self.warnings,
            "evaluated_at": self.evaluated_at.isoformat(),
            "engine": self.engine,
        }


# Default Kyverno policies for OmniPotent
DEFAULT_KYVERNO_POLICIES = [
    KyvernoPolicy(
        name="omnipotent-require-labels",
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
        name="omnipotent-resource-limits",
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
        name="omnipotent-no-latest-tag",
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
        name="omnipotent-replica-minimum",
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
        name="omnipotent-add-defaults",
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
                                "app.kubernetes.io/managed-by": "omnipotent"
                            },
                            "annotations": {
                                "omnipotent.io/last-modified": "{{time.Now()}}"
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
        # Policies whose cluster apply failed, by name. `register_policy`
        # schedules the apply as a background task, so this is where the
        # failure is surfaced (alongside an ERROR log) instead of vanishing
        # into an unawaited coroutine.
        self._cluster_apply_errors: dict[str, str] = {}

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
        """Evaluate policy via Kubernetes API (dry-run).

        A cluster dry-run through Kyverno's admission controller is not
        implemented yet, so this substitutes the embedded subset evaluator.
        The substitution is no longer silent: the result carries
        ``engine="embedded"`` (a real dry-run would say ``"cluster"``), so a
        caller can tell an approximate local answer from an authoritative one.
        """
        log.warning(
            "Cluster dry-run not implemented; evaluating policy %s with the "
            "embedded subset evaluator instead",
            policy.name,
        )
        result = self._evaluate_embedded(
            policy,
            resource,
            resource.get("metadata", {}).get("name", "unknown"),
            resource.get("metadata", {}).get("namespace", "default"),
        )
        result.engine = "embedded"
        return result

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
            engine="embedded",
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
        current: Any = resource

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
        """Register a Kyverno policy.

        With a k8s client configured the policy is also applied to the cluster
        in a background task. That apply used to be a log-only no-op ("Would
        apply policy..."), so every caller believed a ClusterPolicy was
        enforcing when nothing had been created. It is real now, and a failed
        apply is logged at ERROR and recorded in ``cluster_apply_errors``
        (see ``get_stats``) rather than swallowed.
        """
        self._policies[policy.name] = policy
        log.info("Registered Kyverno policy: %s", policy.name)

        # Apply to cluster if K8s client available
        if self.k8s:
            task = asyncio.create_task(self._apply_to_cluster(policy))
            task.add_done_callback(functools.partial(self._record_apply_result, policy.name))

    def _record_apply_result(self, policy_name: str, task: asyncio.Task[Any]) -> None:
        """Surface the outcome of a background cluster apply."""
        if task.cancelled():
            self._cluster_apply_errors[policy_name] = "apply task cancelled"
            log.error("Cluster apply of Kyverno policy %s was cancelled", policy_name)
            return
        exc = task.exception()
        if exc is not None:
            self._cluster_apply_errors[policy_name] = str(exc)
            log.error(
                "Failed to apply Kyverno policy %s to cluster: %s — the policy "
                "is registered locally but is NOT enforcing in the cluster",
                policy_name,
                exc,
            )
        else:
            self._cluster_apply_errors.pop(policy_name, None)

    def _custom_objects_api(self) -> Any:
        """Resolve a CustomObjectsApi from whatever k8s client we were given.

        ``k8s_client`` is documented as ``kubernetes.client`` — but accept the
        module, an ``ApiClient`` instance, or an already-constructed
        ``CustomObjectsApi``, and refuse anything else rather than guessing.
        """
        if self.k8s is None:
            raise RuntimeError("no Kubernetes client configured")

        # The kubernetes.client module (or anything exposing the API class).
        if hasattr(self.k8s, "CustomObjectsApi"):
            return self.k8s.CustomObjectsApi()

        # Already a CustomObjectsApi (or compatible facade).
        if hasattr(self.k8s, "create_cluster_custom_object"):
            return self.k8s

        # An ApiClient instance: wrap it with the official API class.
        if hasattr(self.k8s, "call_api"):
            from kubernetes import client as kubernetes_client

            return kubernetes_client.CustomObjectsApi(self.k8s)

        raise RuntimeError(
            f"unsupported Kubernetes client {type(self.k8s).__name__}: expected "
            "the kubernetes.client module, an ApiClient, or a CustomObjectsApi"
        )

    async def _apply_to_cluster(self, policy: KyvernoPolicy) -> None:
        """Create or update the policy in the Kubernetes cluster.

        Creates the ``kyverno.io/v1`` ClusterPolicy (or namespaced Policy) via
        the CustomObjectsApi; a 409 means it already exists and is patched with
        the new manifest instead. Any failure is raised to the caller — this
        method never pretends the policy is enforcing when it is not.
        """
        api = self._custom_objects_api()
        manifest = policy.to_k8s_manifest()
        group, version = "kyverno.io", "v1"
        plural = "policies" if policy.namespace else "clusterpolicies"

        def _apply() -> str:
            try:
                if policy.namespace:
                    api.create_namespaced_custom_object(
                        group, version, policy.namespace, plural, manifest
                    )
                else:
                    api.create_cluster_custom_object(group, version, plural, manifest)
                return "created"
            except Exception as e:
                # kubernetes.client.rest.ApiException carries .status; a 409
                # means the policy already exists — update it in place.
                if getattr(e, "status", None) != 409:
                    raise
                if policy.namespace:
                    api.patch_namespaced_custom_object(
                        group, version, policy.namespace, plural, policy.name, manifest
                    )
                else:
                    api.patch_cluster_custom_object(
                        group, version, plural, policy.name, manifest
                    )
                return "patched"

        # The official kubernetes client is synchronous; keep it off the loop.
        outcome = await asyncio.to_thread(_apply)
        log.info(
            "Applied Kyverno %s %s to cluster (%s)",
            "Policy" if policy.namespace else "ClusterPolicy",
            policy.name,
            outcome,
        )

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
            "cluster_apply_errors": dict(self._cluster_apply_errors),
        }


def create_kyverno_client(
    k8s_client: Any = None,
    embedded_mode: bool = True,
) -> KyvernoClient:
    """Factory function to create a Kyverno client."""
    return KyvernoClient(k8s_client=k8s_client, embedded_mode=embedded_mode)
