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
import uuid
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
            "pre_action_state": self.pre_action_state,
            "expected_post_state": self.expected_post_state,
        }


@dataclass
class PostActionValidation:
    """Outcome of comparing observed state against a check's expected state.

    `validated` is deliberately tri-state. None means "could not be
    determined" - no expected post-state was recorded, or the cluster could not
    be observed at all - which is not the same as "the action succeeded". The
    endpoint that used to answer `{"validation": "pending"}` to every request
    now reports one of these.
    """

    action_id: str
    action_type: str
    target: str
    validated: bool | None
    reason: str
    expected_post_state: dict[str, Any] | None = None
    observed_state: dict[str, Any] | None = None
    waited_seconds: float = 0.0
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "action_type": self.action_type,
            "target": self.target,
            "validated": self.validated,
            "reason": self.reason,
            "expected_post_state": self.expected_post_state,
            "observed_state": self.observed_state,
            "waited_seconds": round(self.waited_seconds, 3),
            "checked_at": self.checked_at.isoformat(),
        }


# Verbs that destroy state. Matched as whole tokens anywhere in the action
# name, so `delete_deployment`, `terraform_destroy` and `drop_database` all land
# on the delete checker instead of sliding past an exact-string lookup.
DESTRUCTIVE_VERBS = frozenset({
    "delete", "destroy", "drop", "remove", "terminate", "purge",
    "truncate", "wipe", "uninstall", "prune", "evict",
})

# Action names this checker actually knows how to reason about. Anything else
# is refused rather than waved through a permissive generic check.
KNOWN_ACTIONS = frozenset({"scale", "restart", "rollback", "config_update", "delete"})


def normalize_action_type(action_type: str) -> str:
    """Map a raw action name onto a checker key, or "" if unrecognised.

    Fail-closed: an unknown verb returns "" so the caller refuses the action.
    """
    normalized = action_type.strip().lower().replace("-", "_").replace(" ", "_")
    if not normalized:
        return ""

    tokens = [t for t in normalized.split("_") if t]
    if any(t in DESTRUCTIVE_VERBS for t in tokens):
        return "delete"

    if normalized in KNOWN_ACTIONS:
        return normalized
    if normalized.startswith("config_update") or normalized.startswith("update_config"):
        return "config_update"
    if tokens and tokens[0] in KNOWN_ACTIONS:
        return tokens[0]

    return ""


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
        # Second-resolution timestamps collide, and the id is what callers hand
        # back to POST /actions/{id}/validate, so make it unique.
        action_id = (
            f"check_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
            f"_{uuid.uuid4().hex[:8]}"
        )

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

        checker = checkers.get(normalize_action_type(action_type))
        if checker is None:
            await self._refuse_unrecognized_action(check, target, params)
        else:
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
            # Nothing irreversible without a way back is ever auto-cleared.
            and (check.is_reversible or bool(check.rollback_plan))
        )

        return check

    async def post_action_validate(
        self,
        check: ActionSafetyCheck,
        timeout_seconds: int = 60,
    ) -> bool:
        """Validate post-action state matches expected.

        True only when the cluster was actually observed to reach the expected
        state (or there was nothing to check). "Could not observe" is False.
        """
        result = await self.post_action_validate_detailed(check, timeout_seconds)
        if result.validated is None:
            # Nothing to compare against: the caller asked a yes/no question, so
            # answer the only safe way and keep the reason in the log.
            return check.expected_post_state is None or not check.expected_post_state
        return result.validated

    async def post_action_validate_detailed(
        self,
        check: ActionSafetyCheck,
        timeout_seconds: int = 60,
    ) -> PostActionValidation:
        """Validate post-action state, reporting what was actually observed."""
        started = datetime.now(timezone.utc)

        def _elapsed() -> float:
            return (datetime.now(timezone.utc) - started).total_seconds()

        if not check.expected_post_state:
            return PostActionValidation(
                action_id=check.action_id,
                action_type=check.action_type,
                target=check.target,
                validated=None,
                reason=(
                    f"The pre-action check for '{check.action_type}' recorded no "
                    "expected post-action state, so there is nothing to compare "
                    "the cluster against."
                ),
                waited_seconds=_elapsed(),
            )

        if not self.k8s:
            # Without a client, _capture_state returns None forever; polling
            # until the deadline would just be an expensive way to say "no".
            log.warning(
                "post_action_validate(%s): no Kubernetes client configured, cannot "
                "observe the result of the action",
                check.action_id,
            )
            return PostActionValidation(
                action_id=check.action_id,
                action_type=check.action_type,
                target=check.target,
                validated=None,
                reason=(
                    "No Kubernetes client is configured, so the state of "
                    f"'{check.target}' cannot be observed and the action's result "
                    "cannot be validated."
                ),
                expected_post_state=check.expected_post_state,
                waited_seconds=_elapsed(),
            )

        deadline = started + timedelta(seconds=timeout_seconds)
        current_state: dict[str, Any] | None = None

        while True:
            current_state = await self._capture_state(check.target)

            if current_state and self._states_match(current_state, check.expected_post_state):
                return PostActionValidation(
                    action_id=check.action_id,
                    action_type=check.action_type,
                    target=check.target,
                    validated=True,
                    reason="Observed state matches the expected post-action state.",
                    expected_post_state=check.expected_post_state,
                    observed_state=current_state,
                    waited_seconds=_elapsed(),
                )

            if datetime.now(timezone.utc) >= deadline:
                break

            await asyncio.sleep(min(5, max(0.0, (deadline - datetime.now(timezone.utc)).total_seconds())))

        if current_state is None:
            reason = (
                f"'{check.target}' could not be read from the cluster within "
                f"{timeout_seconds}s, so the action's result is unknown."
            )
        else:
            reason = (
                f"Observed state did not reach the expected post-action state "
                f"within {timeout_seconds}s."
            )

        return PostActionValidation(
            action_id=check.action_id,
            action_type=check.action_type,
            target=check.target,
            validated=False if current_state is not None else None,
            reason=reason,
            expected_post_state=check.expected_post_state,
            observed_state=current_state,
            waited_seconds=_elapsed(),
        )

    async def _check_scale_action(
        self,
        check: ActionSafetyCheck,
        target: str,
        params: dict[str, Any],
    ) -> None:
        """Check safety of a scaling action."""
        desired_replicas = params.get("replicas", 0)
        namespace = params.get("namespace", "default")

        # Parameter-level rules do not depend on cluster state, so they are
        # evaluated even when the state cannot be read.
        if desired_replicas == 0 and not params.get("allow_zero", False):
            check.failed_preconditions.append(
                "Scaling to 0 replicas requires explicit allow_zero=true"
            )
            check.identified_risks.append("Service will be completely unavailable")

        check.expected_post_state = {
            "replicas": desired_replicas,
            "available_replicas": desired_replicas,
        }
        check.is_reversible = True

        check.pre_action_state = await self._capture_state(target, namespace)

        if not check.pre_action_state:
            check.failed_preconditions.append(self._state_unavailable_reason(target))
            check.preconditions_met = False
            return

        current_replicas = check.pre_action_state.get("replicas", 0)

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

        check.rollback_plan = (
            f"kubectl scale deployment {target} -n {namespace} --replicas={current_replicas}"
        )
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
            check.failed_preconditions.append(self._state_unavailable_reason(target))
            check.preconditions_met = False
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
            check.failed_preconditions.append(self._state_unavailable_reason(target))
            check.preconditions_met = False
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

        check.is_reversible = True

        if not self.k8s:
            # Nothing was read, so nothing was verified. Reporting
            # preconditions_met=True here claimed a check that never happened.
            check.failed_preconditions.append(self._state_unavailable_reason(target))
            check.preconditions_met = False
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
            check.preconditions_met = False
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
        """Check safety of a destructive action (delete/destroy/drop/...)."""
        # Destructive actions are high-risk regardless of namespace or resource
        # type. They are refused unless the caller passes confirmed=true, which
        # is what "require explicit confirmation" has to mean to be true.
        _ = params.get("namespace", "default")
        _ = params.get("resource_type", "deployment")

        confirmed = bool(params.get("confirmed") or params.get("confirm"))

        if not confirmed:
            check.failed_preconditions.append(
                "Delete actions require explicit confirmation (params.confirmed=true)"
            )

        check.identified_risks.append("Data loss - ensure backups exist")
        check.identified_risks.append("Dependent services may be affected")

        check.is_reversible = False
        check.preconditions_met = len(check.failed_preconditions) == 0

    async def _refuse_unrecognized_action(
        self,
        check: ActionSafetyCheck,
        target: str,
        params: dict[str, Any],
    ) -> None:
        """Refuse an action this checker has no rules for.

        The previous behaviour routed anything unknown through a permissive
        generic check that only asked "can I read some state?", so
        `drop_database` and `terraform_destroy` came back is_safe=True with an
        empty risk list. An action nobody wrote a safety rule for is not known
        to be safe.
        """
        check.failed_preconditions.append(
            f"Unrecognized action type '{check.action_type}' - no safety rules are "
            f"defined for it, so it cannot be cleared automatically. "
            f"Known types: {', '.join(sorted(KNOWN_ACTIONS))}. Manual review required."
        )
        check.identified_risks.append(
            "Unknown blast radius: effects and reversibility have not been assessed"
        )
        check.is_reversible = False
        check.preconditions_met = False

    def _state_unavailable_reason(self, target: str) -> str:
        """Why pre-action state could not be read (config gap vs missing object)."""
        if not self.k8s:
            return (
                "No Kubernetes client is configured, so the current state of "
                f"'{target}' could not be read and preconditions cannot be verified"
            )
        return f"Deployment {target} not found"

    async def _capture_state(
        self,
        target: str,
        namespace: str = "default",
    ) -> dict[str, Any] | None:
        """Capture current state of a target, or None if it cannot be read.

        Returns None (not a placeholder) when there is no Kubernetes client:
        this value is compared against expected post-action state, and inventing
        `{"replicas": 2, ...}` made every caller believe it had read the cluster.
        """
        if not self.k8s:
            log.debug(
                "No Kubernetes client configured; cannot capture state for %s", target
            )
            return None

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
        """Calculate overall safety score.

        A flagged risk has to move the number. The old weights subtracted 0.1
        per risk and then handed 0.1 straight back for is_reversible, so an
        action with an identified risk still scored a clean 1.0. Mitigations can
        only offset risks that were actually identified, never push a risky
        action above a clean one.
        """
        score = 1.0

        score -= len(check.failed_preconditions) * 0.5
        score -= len(check.identified_risks) * 0.25
        score += min(len(check.risk_mitigations), len(check.identified_risks)) * 0.05

        if not check.is_reversible:
            score -= 0.3

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
