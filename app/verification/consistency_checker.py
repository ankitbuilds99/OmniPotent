"""Data Consistency Checking for Actions.

Verifies data consistency before and after actions:
1. Pre-conditions are met
2. Resources exist and are modifiable
3. No conflicting operations
4. Post-conditions are validated
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

log = logging.getLogger("omnipotent.verification.consistency_checker")


@dataclass
class ActionSafetyCheck:
    """Pre-action safety verification result."""
    action_id: str
    action_type: str
    target: str

    is_safe: bool = False
    safety_score: float = 0.0

    preconditions_met: bool = False
    failed_preconditions: list[str] = field(default_factory=list)

    pre_action_state: dict[str, Any] | None = None
    expected_post_state: dict[str, Any] | None = None

    identified_risks: list[str] = field(default_factory=list)
    risk_mitigations: list[str] = field(default_factory=list)

    rollback_plan: str | None = None
    is_reversible: bool = True

    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "action_type": self.action_type,
            "target": self.target,
            "is_safe": self.is_safe,
            "safety_score": self.safety_score,
            "preconditions_met": self.preconditions_met,
            "failed_preconditions": self.failed_preconditions,
            "identified_risks": self.identified_risks,
            "risk_mitigations": self.risk_mitigations,
            "rollback_plan": self.rollback_plan,
            "is_reversible": self.is_reversible,
            "checked_at": self.checked_at.isoformat(),
        }


class DataConsistencyChecker:
    """Verifies data consistency before and after actions.

    Integrates with Kubernetes and metrics to perform real safety checks.
    """

    def __init__(
        self,
        k8s_client: Any = None,
        prometheus_url: str | None = None,
        knowledge_graph: Any = None,
    ):
        self.k8s = k8s_client
        self.prometheus_url = prometheus_url
        self.graph = knowledge_graph

    async def pre_action_check(
        self,
        action_type: str,
        target: str,
        params: dict[str, Any],
    ) -> ActionSafetyCheck:
        """Perform pre-action safety checks."""
        action_id = f"check_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"

        check = ActionSafetyCheck(
            action_id=action_id,
            action_type=action_type,
            target=target,
        )

        checkers = {
            "scale": self._check_scale_action,
            "restart": self._check_restart_action,
            "rollback": self._check_rollback_action,
            "config_update": self._check_config_action,
            "delete": self._check_delete_action,
        }

        checker = checkers.get(action_type, self._check_generic_action)
        await checker(check, target, params)

        check.safety_score = self._calculate_safety_score(check)
        # One safety bar for the whole product: this is the same gate the
        # remediation path applies (SAFETY_CONFIDENCE_THRESHOLD), so an operator
        # who tightens it tightens it everywhere. Read per check, not at import,
        # so a mid-incident config change actually takes effect.
        from app.config import get_settings

        check.is_safe = (
            check.preconditions_met
            and check.safety_score >= get_settings().safety_confidence_threshold
            and len(check.failed_preconditions) == 0
        )

        return check

    async def post_action_validate(
        self,
        check: ActionSafetyCheck,
        timeout_seconds: int = 60,
    ) -> bool:
        """Validate post-action state matches expected."""
        if not check.expected_post_state:
            return True

        deadline = datetime.now(timezone.utc) + timedelta(seconds=timeout_seconds)

        while datetime.now(timezone.utc) < deadline:
            current_state = await self._capture_state(check.target)

            if current_state and self._states_match(current_state, check.expected_post_state):
                return True

            await asyncio.sleep(5)

        return False

    async def _check_scale_action(
        self,
        check: ActionSafetyCheck,
        target: str,
        params: dict[str, Any],
    ) -> None:
        """Check safety of a scaling action."""
        desired_replicas = params.get("replicas", 0)
        namespace = params.get("namespace", "default")

        check.pre_action_state = await self._capture_state(target, namespace)

        if not check.pre_action_state:
            check.failed_preconditions.append(f"Deployment {target} not found")
            return

        current_replicas = check.pre_action_state.get("replicas", 0)

        if desired_replicas == 0 and not params.get("allow_zero", False):
            check.failed_preconditions.append(
                "Scaling to 0 replicas requires explicit allow_zero=true"
            )
            check.identified_risks.append("Service will be completely unavailable")

        if desired_replicas > current_replicas * 3:
            check.identified_risks.append(
                f"Large scale-up from {current_replicas} to {desired_replicas} may cause resource issues"
            )
            check.risk_mitigations.append("Consider gradual scaling in steps")

        if desired_replicas > current_replicas:
            has_capacity = await self._check_cluster_capacity(
                target, namespace, desired_replicas - current_replicas
            )
            if not has_capacity:
                check.identified_risks.append(
                    "Cluster may not have capacity for additional replicas"
                )

        if desired_replicas < current_replicas:
            hpa_exists = await self._check_hpa_exists(target, namespace)
            if hpa_exists:
                check.identified_risks.append(
                    "HPA exists - manual scaling may be overridden"
                )

        check.expected_post_state = {
            "replicas": desired_replicas,
            "available_replicas": desired_replicas,
        }

        check.rollback_plan = (
            f"kubectl scale deployment {target} -n {namespace} --replicas={current_replicas}"
        )
        check.is_reversible = True
        check.preconditions_met = len(check.failed_preconditions) == 0

    async def _check_restart_action(
        self,
        check: ActionSafetyCheck,
        target: str,
        params: dict[str, Any],
    ) -> None:
        """Check safety of a restart action."""
        namespace = params.get("namespace", "default")

        check.pre_action_state = await self._capture_state(target, namespace)

        if not check.pre_action_state:
            check.failed_preconditions.append(f"Deployment {target} not found")
            return

        replicas = check.pre_action_state.get("replicas", 0)

        if replicas < 2:
            check.identified_risks.append(
                f"Only {replicas} replica(s). Restart will cause downtime."
            )
            check.risk_mitigations.append("Scale up before restarting")

        if check.pre_action_state.get("updating", False):
            check.failed_preconditions.append("Deployment is already updating")

        pdb = await self._check_pdb_exists(target, namespace)
        if pdb:
            check.risk_mitigations.append(
                f"PDB exists ({pdb}) - rolling restart will respect availability"
            )

        check.rollback_plan = None
        check.is_reversible = True
        check.preconditions_met = len(check.failed_preconditions) == 0

    async def _check_rollback_action(
        self,
        check: ActionSafetyCheck,
        target: str,
        params: dict[str, Any],
    ) -> None:
        """Check safety of a rollback action."""
        target_version = params.get("version")
        namespace = params.get("namespace", "default")

        check.pre_action_state = await self._capture_state(target, namespace)

        if not check.pre_action_state:
            check.failed_preconditions.append(f"Deployment {target} not found")
            return

        if self.graph:
            try:
                deployments = await self.graph.get_recent_deployments(
                    service_id=target,
                    hours=168,
                    limit=10,
                )

                if target_version:
                    version_exists = any(
                        d.version == target_version for d in deployments
                    )
                    if not version_exists:
                        check.failed_preconditions.append(
                            f"Version {target_version} not found in history"
                        )

                for deploy in deployments:
                    props = deploy.to_cypher_properties()
                    if props.get("caused_incident"):
                        if props.get("version") == target_version:
                            check.identified_risks.append(
                                f"Version {target_version} previously caused an incident"
                            )

            except Exception as e:
                log.warning("Failed to check deployment history: %s", e)

        current_version = check.pre_action_state.get("current_version", "unknown")
        check.rollback_plan = f"Roll forward to {current_version}"
        check.is_reversible = True
        check.preconditions_met = len(check.failed_preconditions) == 0

    async def _check_config_action(
        self,
        check: ActionSafetyCheck,
        target: str,
        params: dict[str, Any],
    ) -> None:
        """Check safety of a config update action."""
        config_key = params.get("key", "")
        namespace = params.get("namespace", "default")

        if not self.k8s:
            check.preconditions_met = True
            check.is_reversible = True
            return

        try:
            configmap = await self.k8s.read_namespaced_config_map(
                name=target,
                namespace=namespace,
            )
            current_data = dict(configmap.data or {})
            old_value = current_data.get(config_key)

            check.pre_action_state = {config_key: old_value}

        except Exception:
            check.failed_preconditions.append(f"ConfigMap {target} not found")
            return

        sensitive_keys = ["password", "secret", "key", "token", "credential", "api_key"]
        if any(s in config_key.lower() for s in sensitive_keys):
            check.identified_risks.append(f"Modifying sensitive config key: {config_key}")

        check.rollback_plan = f"Restore {config_key} to previous value"
        check.is_reversible = True
        check.preconditions_met = len(check.failed_preconditions) == 0

    async def _check_delete_action(
        self,
        check: ActionSafetyCheck,
        target: str,
        params: dict[str, Any],
    ) -> None:
        """Check safety of a delete action."""
        # Delete actions are high-risk and require explicit confirmation
        # regardless of namespace or resource type
        _ = params.get("namespace", "default")
        _ = params.get("resource_type", "deployment")

        check.failed_preconditions.append(
            "Delete actions require explicit confirmation"
        )
        check.identified_risks.append("Data loss - ensure backups exist")
        check.identified_risks.append("Dependent services may be affected")

        check.is_reversible = False
        check.preconditions_met = False

    async def _check_generic_action(
        self,
        check: ActionSafetyCheck,
        target: str,
        params: dict[str, Any],
    ) -> None:
        """Generic action check."""
        namespace = params.get("namespace", "default")

        check.pre_action_state = await self._capture_state(target, namespace)
        check.preconditions_met = check.pre_action_state is not None

        if not check.preconditions_met:
            check.failed_preconditions.append(f"Could not verify state of {target}")

        check.is_reversible = True

    async def _capture_state(
        self,
        target: str,
        namespace: str = "default",
    ) -> dict[str, Any] | None:
        """Capture current state of a target."""
        if not self.k8s:
            return {"replicas": 2, "available_replicas": 2, "updating": False}

        try:
            deployment = await self.k8s.read_namespaced_deployment(
                name=target,
                namespace=namespace,
            )

            return {
                "replicas": deployment.spec.replicas,
                "ready_replicas": deployment.status.ready_replicas,
                "available_replicas": deployment.status.available_replicas,
                "updated_replicas": deployment.status.updated_replicas,
                "current_version": (
                    deployment.spec.template.spec.containers[0].image.split(":")[-1]
                    if deployment.spec.template.spec.containers
                    else "unknown"
                ),
                "updating": (
                    deployment.status.updated_replicas != deployment.spec.replicas
                ),
            }

        except Exception as e:
            log.debug("Failed to capture state for %s: %s", target, e)
            return None

    async def _check_cluster_capacity(
        self,
        target: str,
        namespace: str,
        additional_replicas: int,
    ) -> bool:
        """Check if cluster has capacity for more replicas."""
        if not self.k8s:
            return True

        try:
            nodes = await self.k8s.list_node()

            total_allocatable_cpu = 0.0
            total_allocatable_memory = 0

            for node in nodes.items:
                if node.status.allocatable:
                    cpu_str = node.status.allocatable.get("cpu", "0")
                    if cpu_str.endswith("m"):
                        total_allocatable_cpu += float(cpu_str[:-1]) / 1000
                    else:
                        total_allocatable_cpu += float(cpu_str)

                    mem_str = node.status.allocatable.get("memory", "0")
                    if mem_str.endswith("Ki"):
                        total_allocatable_memory += int(mem_str[:-2]) // 1024
                    elif mem_str.endswith("Mi"):
                        total_allocatable_memory += int(mem_str[:-2])
                    elif mem_str.endswith("Gi"):
                        total_allocatable_memory += int(mem_str[:-2]) * 1024

            return total_allocatable_cpu > additional_replicas * 0.5

        except Exception as e:
            log.debug("Failed to check cluster capacity: %s", e)
            return True

    async def _check_hpa_exists(
        self,
        target: str,
        namespace: str,
    ) -> bool:
        """Check if HPA exists for deployment."""
        if not self.k8s:
            return False

        try:
            hpas = await self.k8s.list_namespaced_horizontal_pod_autoscaler(
                namespace=namespace,
            )

            for hpa in hpas.items:
                if hpa.spec.scale_target_ref.name == target:
                    return True

            return False

        except Exception:
            return False

    async def _check_pdb_exists(
        self,
        target: str,
        namespace: str,
    ) -> str | None:
        """Check if PDB exists for deployment."""
        if not self.k8s:
            return None

        try:
            pdbs = await self.k8s.list_namespaced_pod_disruption_budget(
                namespace=namespace,
            )

            for pdb in pdbs.items:
                selector = pdb.spec.selector
                if selector and selector.match_labels:
                    if selector.match_labels.get("app") == target:
                        return pdb.metadata.name

            return None

        except Exception:
            return None

    def _states_match(
        self,
        current: dict[str, Any],
        expected: dict[str, Any],
    ) -> bool:
        """Check if current state matches expected."""
        for key, value in expected.items():
            if key not in current:
                return False
            if current[key] != value:
                if isinstance(value, (int, float)) and isinstance(current[key], (int, float)):
                    if abs(current[key] - value) > 0.01:
                        return False
                else:
                    return False
        return True

    def _calculate_safety_score(self, check: ActionSafetyCheck) -> float:
        """Calculate overall safety score."""
        score = 1.0

        score -= len(check.failed_preconditions) * 0.3
        score -= len(check.identified_risks) * 0.1
        score += len(check.risk_mitigations) * 0.05

        if check.is_reversible:
            score += 0.1

        if check.rollback_plan:
            score += 0.05

        return max(0.0, min(1.0, score))


class ResourceLockManager:
    """Manages resource locks to prevent conflicting operations."""

    def __init__(self, redis_client: Any = None):
        self.redis = redis_client
        self._local_locks: dict[str, datetime] = {}
        self._lock_ttl_seconds = 300

    async def acquire_lock(
        self,
        resource_id: str,
        owner: str,
        ttl_seconds: int | None = None,
    ) -> bool:
        """Acquire a lock on a resource."""
        ttl = ttl_seconds or self._lock_ttl_seconds
        key = f"resource_lock:{resource_id}"

        if self.redis:
            try:
                result = await self.redis.set(
                    key,
                    owner,
                    nx=True,
                    ex=ttl,
                )
                return result is not None
            except Exception as e:
                log.warning("Failed to acquire Redis lock: %s", e)

        if resource_id in self._local_locks:
            if datetime.now(timezone.utc) < self._local_locks[resource_id]:
                return False

        self._local_locks[resource_id] = (
            datetime.now(timezone.utc) + timedelta(seconds=ttl)
        )
        return True

    async def release_lock(
        self,
        resource_id: str,
        owner: str,
    ) -> bool:
        """Release a lock on a resource."""
        key = f"resource_lock:{resource_id}"

        if self.redis:
            try:
                current_owner = await self.redis.get(key)
                if current_owner and current_owner.decode() == owner:
                    await self.redis.delete(key)
                    return True
                return False
            except Exception as e:
                log.warning("Failed to release Redis lock: %s", e)

        if resource_id in self._local_locks:
            del self._local_locks[resource_id]
            return True

        return False

    async def check_lock(self, resource_id: str) -> str | None:
        """Check who holds a lock on a resource."""
        key = f"resource_lock:{resource_id}"

        if self.redis:
            try:
                owner = await self.redis.get(key)
                if owner:
                    return owner.decode()
            except Exception:
                pass

        if resource_id in self._local_locks:
            if datetime.now(timezone.utc) < self._local_locks[resource_id]:
                return "local"

        return None
