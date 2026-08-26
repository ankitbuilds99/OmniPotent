"""Remediation Task Handlers.

Real integrations for incident remediation:
- Kubernetes scaling
- Pod restarts
- Deployment rollbacks
- Configuration updates
- Verification
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from app.config import get_settings
from app.coordinator.dag_executor import TaskNode

log = logging.getLogger("omnipotent.coordinator.handlers.remediation")

# Below this, a hypothesis is too weak to keep digging on and goes to a human.
# Unlike the "act on it" bar (COPILOT_SAFETY_CONFIDENCE_THRESHOLD) this gates no
# change to production — it only decides investigate-more vs escalate — so it
# stays a constant rather than becoming another knob to tune.
_INVESTIGATE_FLOOR = 0.5

# Production wiring (app/coordinator/api_router.py) constructs these handlers
# with k8s_client=None, and nothing downstream inspects the "simulated" flag —
# so a fabricated success here is indistinguishable from a real remediation.
# Unless the operator opted into demo mode, an unconfigured handler reports the
# failure instead of the outcome it would have liked to produce.
_NO_K8S = (
    "kubernetes client not configured (set KUBE_CONFIG_PATH and pass a client to "
    "create_remediation_handlers); refusing to report success for an action that "
    "was not performed"
)


def _no_backend(result: dict[str, Any], operation: str, detail: str) -> dict[str, Any]:
    """Mark ``result`` as not-really-executed and return it.

    Keeps the ``simulated`` marker (the payload never came from a real backend)
    but only claims success when the operator asked for a simulation.
    """
    result["simulated"] = True

    if get_settings().copilot_demo_mode:
        log.warning(
            "COPILOT_DEMO_MODE=true - returning a SIMULATED %s result", operation
        )
        result["success"] = True
        result["message"] = f"{operation} (simulated)"
        return result

    log.warning("%s not performed: %s", operation, detail)
    result["success"] = False
    result["error"] = detail
    return result


class ScaleHandler:
    """Handler for Kubernetes scaling operations."""

    def __init__(self, k8s_client: Any = None):
        self.k8s = k8s_client

    async def __call__(
        self,
        task: TaskNode,
        inputs: dict[str, Any],
    ) -> dict[str, Any]:
        """Scale a deployment."""
        deployment = inputs.get("deployment")
        namespace = inputs.get("namespace", "default")
        replicas = inputs.get("replicas")
        service = inputs.get("service")

        if not deployment:
            deployment = service

        if not deployment or replicas is None:
            return {
                "success": False,
                "error": "Missing deployment or replicas parameter",
            }

        result: dict[str, Any] = {
            "deployment": deployment,
            "namespace": namespace,
            "target_replicas": replicas,
            "previous_replicas": None,
            "success": False,
        }

        if not self.k8s:
            return _no_backend(
                result,
                f"Scaled {deployment} to {replicas} replicas",
                _NO_K8S,
            )

        try:
            current = await self.k8s.read_namespaced_deployment(
                name=deployment,
                namespace=namespace,
            )
            result["previous_replicas"] = current.spec.replicas

            body = {"spec": {"replicas": replicas}}
            await self.k8s.patch_namespaced_deployment_scale(
                name=deployment,
                namespace=namespace,
                body=body,
            )

            success = await self._wait_for_scale(
                deployment, namespace, replicas, timeout_seconds=120
            )

            result["success"] = success
            if not success:
                result["error"] = "Timeout waiting for scale operation"

        except Exception as e:
            log.error("Scale operation failed: %s", e)
            result["error"] = str(e)

        return result

    async def _wait_for_scale(
        self,
        deployment: str,
        namespace: str,
        target_replicas: int,
        timeout_seconds: int = 120,
    ) -> bool:
        """Wait for deployment to reach target replica count."""
        deadline = datetime.now(timezone.utc) + timedelta(seconds=timeout_seconds)

        while datetime.now(timezone.utc) < deadline:
            try:
                status = await self.k8s.read_namespaced_deployment_status(
                    name=deployment,
                    namespace=namespace,
                )

                ready = status.status.ready_replicas or 0
                if ready >= target_replicas:
                    return True

            except Exception as e:
                log.warning("Error checking scale status: %s", e)

            await asyncio.sleep(5)

        return False


class RestartHandler:
    """Handler for pod restart operations."""

    def __init__(self, k8s_client: Any = None):
        self.k8s = k8s_client

    async def __call__(
        self,
        task: TaskNode,
        inputs: dict[str, Any],
    ) -> dict[str, Any]:
        """Restart pods for a deployment."""
        deployment = inputs.get("deployment")
        namespace = inputs.get("namespace", "default")
        service = inputs.get("service")
        rolling = inputs.get("rolling", True)

        if not deployment:
            deployment = service

        if not deployment:
            return {
                "success": False,
                "error": "Missing deployment parameter",
            }

        result: dict[str, Any] = {
            "deployment": deployment,
            "namespace": namespace,
            "rolling": rolling,
            "success": False,
            "restarted_pods": [],
        }

        if not self.k8s:
            return _no_backend(
                result,
                f"Restarted pods for {deployment}",
                _NO_K8S,
            )

        try:
            if rolling:
                now = datetime.now(timezone.utc).isoformat()
                patch = {
                    "spec": {
                        "template": {
                            "metadata": {
                                "annotations": {
                                    "kubectl.kubernetes.io/restartedAt": now
                                }
                            }
                        }
                    }
                }

                await self.k8s.patch_namespaced_deployment(
                    name=deployment,
                    namespace=namespace,
                    body=patch,
                )

                success = await self._wait_for_rollout(
                    deployment, namespace, timeout_seconds=180
                )
                result["success"] = success

            else:
                pods = await self.k8s.list_namespaced_pod(
                    namespace=namespace,
                    label_selector=f"app={deployment}",
                )

                for pod in pods.items:
                    pod_name = pod.metadata.name
                    try:
                        await self.k8s.delete_namespaced_pod(
                            name=pod_name,
                            namespace=namespace,
                        )
                        result["restarted_pods"].append(pod_name)
                    except Exception as e:
                        log.warning("Failed to delete pod %s: %s", pod_name, e)

                result["success"] = len(result["restarted_pods"]) > 0

        except Exception as e:
            log.error("Restart operation failed: %s", e)
            result["error"] = str(e)

        return result

    async def _wait_for_rollout(
        self,
        deployment: str,
        namespace: str,
        timeout_seconds: int = 180,
    ) -> bool:
        """Wait for rollout to complete."""
        deadline = datetime.now(timezone.utc) + timedelta(seconds=timeout_seconds)

        while datetime.now(timezone.utc) < deadline:
            try:
                status = await self.k8s.read_namespaced_deployment_status(
                    name=deployment,
                    namespace=namespace,
                )

                if status.status.updated_replicas == status.status.replicas:
                    if status.status.available_replicas == status.status.replicas:
                        return True

            except Exception as e:
                log.warning("Error checking rollout status: %s", e)

            await asyncio.sleep(5)

        return False


class RollbackHandler:
    """Handler for deployment rollback operations."""

    def __init__(
        self,
        k8s_client: Any = None,
        knowledge_graph: Any = None,
    ):
        self.k8s = k8s_client
        self.graph = knowledge_graph

    async def __call__(
        self,
        task: TaskNode,
        inputs: dict[str, Any],
    ) -> dict[str, Any]:
        """Rollback a deployment to a previous version."""
        deployment = inputs.get("deployment")
        namespace = inputs.get("namespace", "default")
        service = inputs.get("service")
        target_revision = inputs.get("revision")
        target_version = inputs.get("version")

        if not deployment:
            deployment = service

        if not deployment:
            return {
                "success": False,
                "error": "Missing deployment parameter",
            }

        result: dict[str, Any] = {
            "deployment": deployment,
            "namespace": namespace,
            "target_revision": target_revision,
            "target_version": target_version,
            "success": False,
        }

        if not self.k8s:
            return _no_backend(
                result,
                f"Rolled back {deployment}",
                _NO_K8S,
            )

        try:
            if target_revision is None:
                history = await self._get_rollout_history(deployment, namespace)
                if len(history) < 2:
                    result["error"] = "No previous revision available"
                    return result
                target_revision = history[-2]["revision"]

            result["target_revision"] = target_revision

            current = await self.k8s.read_namespaced_deployment(
                name=deployment,
                namespace=namespace,
            )
            result["current_revision"] = current.metadata.annotations.get(
                "deployment.kubernetes.io/revision"
            )

            from kubernetes.client import V1DeploymentRollback, V1RollbackConfig

            rollback = V1DeploymentRollback(
                api_version="apps/v1",
                kind="DeploymentRollback",
                name=deployment,
                rollback_to=V1RollbackConfig(revision=target_revision),
            )

            await self.k8s.create_namespaced_deployment_rollback(
                name=deployment,
                namespace=namespace,
                body=rollback,
            )

            success = await self._wait_for_rollout(deployment, namespace)
            result["success"] = success

            if success and self.graph:
                await self._record_rollback(deployment, result)

        except Exception as e:
            log.error("Rollback operation failed: %s", e)
            result["error"] = str(e)

        return result

    async def _get_rollout_history(
        self,
        deployment: str,
        namespace: str,
    ) -> list[dict[str, Any]]:
        """Get rollout history for a deployment."""
        try:
            replicasets = await self.k8s.list_namespaced_replica_set(
                namespace=namespace,
                label_selector=f"app={deployment}",
            )

            history = []
            for rs in replicasets.items:
                revision = rs.metadata.annotations.get(
                    "deployment.kubernetes.io/revision"
                )
                if revision:
                    history.append({
                        "revision": int(revision),
                        "name": rs.metadata.name,
                        "image": rs.spec.template.spec.containers[0].image,
                        "created": rs.metadata.creation_timestamp,
                    })

            history.sort(key=lambda h: h["revision"])
            return history

        except Exception as e:
            log.error("Failed to get rollout history: %s", e)
            return []

    async def _wait_for_rollout(
        self,
        deployment: str,
        namespace: str,
        timeout_seconds: int = 180,
    ) -> bool:
        """Wait for rollout to complete."""
        deadline = datetime.now(timezone.utc) + timedelta(seconds=timeout_seconds)

        while datetime.now(timezone.utc) < deadline:
            try:
                status = await self.k8s.read_namespaced_deployment_status(
                    name=deployment,
                    namespace=namespace,
                )

                if (
                    status.status.updated_replicas == status.status.replicas
                    and status.status.available_replicas == status.status.replicas
                ):
                    return True

            except Exception as e:
                log.warning("Error checking rollout status: %s", e)

            await asyncio.sleep(5)

        return False

    async def _record_rollback(
        self,
        deployment: str,
        result: dict[str, Any],
    ) -> None:
        """Record rollback in knowledge graph."""
        if not self.graph:
            return

        try:
            from app.knowledge_graph.neo4j_backend import DeploymentNode

            deploy_node = DeploymentNode(
                id=f"rollback_{deployment}_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
                service_id=deployment,
                version=f"rollback_to_rev_{result.get('target_revision')}",
                environment="production",
                status="success" if result["success"] else "failed",
                rollback_of=str(result.get("current_revision", "")),
            )

            await self.graph.create_deployment(deploy_node)

        except Exception as e:
            log.warning("Failed to record rollback: %s", e)


class ConfigUpdateHandler:
    """Handler for configuration update operations."""

    def __init__(self, k8s_client: Any = None):
        self.k8s = k8s_client

    async def __call__(
        self,
        task: TaskNode,
        inputs: dict[str, Any],
    ) -> dict[str, Any]:
        """Update a ConfigMap or Secret."""
        name = inputs.get("name")
        namespace = inputs.get("namespace", "default")
        config_type = inputs.get("type", "configmap")
        updates = inputs.get("updates", {})

        if not name or not updates:
            return {
                "success": False,
                "error": "Missing name or updates parameter",
            }

        result: dict[str, Any] = {
            "name": name,
            "namespace": namespace,
            "type": config_type,
            "updates": list(updates.keys()),
            "success": False,
            "previous_values": {},
        }

        if not self.k8s:
            return _no_backend(
                result,
                f"Updated {config_type} {name}",
                _NO_K8S,
            )

        try:
            if config_type == "configmap":
                current = await self.k8s.read_namespaced_config_map(
                    name=name,
                    namespace=namespace,
                )

                result["previous_values"] = {
                    k: current.data.get(k)
                    for k in updates.keys()
                    if current.data
                }

                new_data = dict(current.data or {})
                new_data.update(updates)

                await self.k8s.patch_namespaced_config_map(
                    name=name,
                    namespace=namespace,
                    body={"data": new_data},
                )

            elif config_type == "secret":
                import base64

                current = await self.k8s.read_namespaced_secret(
                    name=name,
                    namespace=namespace,
                )

                encoded_updates = {
                    k: base64.b64encode(v.encode()).decode()
                    for k, v in updates.items()
                }

                await self.k8s.patch_namespaced_secret(
                    name=name,
                    namespace=namespace,
                    body={"data": encoded_updates},
                )

            result["success"] = True

        except Exception as e:
            log.error("Config update failed: %s", e)
            result["error"] = str(e)

        return result


class VerificationHandler:
    """Handler for verifying remediation success."""

    def __init__(
        self,
        prometheus_url: str | None = None,
        k8s_client: Any = None,
    ):
        self.prometheus_url = prometheus_url
        self.k8s = k8s_client

    async def __call__(
        self,
        task: TaskNode,
        inputs: dict[str, Any],
    ) -> dict[str, Any]:
        """Verify that remediation was successful."""
        service: str = inputs.get("service", "")
        deployment: str = inputs.get("deployment") or service
        checks = inputs.get("checks", ["health", "errors", "latency"])
        wait_seconds = inputs.get("wait_seconds", 60)

        result: dict[str, Any] = {
            "service": service,
            "checks_passed": [],
            "checks_failed": [],
            "success": False,
        }

        # With neither backend wired up there is nothing to observe, so no
        # amount of waiting turns into evidence. Say so up front rather than
        # sleeping a minute and then declaring the remediation verified.
        if not self.k8s and not self.prometheus_url:
            return _no_backend(
                result,
                f"Verified remediation of {service or deployment}",
                "no verification backend configured (pass k8s_client and/or "
                "prometheus_url to create_remediation_handlers); refusing to report "
                "a remediation as verified when nothing was checked",
            )

        log.info("Waiting %d seconds before verification", wait_seconds)
        await asyncio.sleep(wait_seconds)

        for check in checks:
            try:
                if check == "health":
                    passed = await self._check_health(deployment)
                elif check == "errors":
                    passed = await self._check_error_rate(service)
                elif check == "latency":
                    passed = await self._check_latency(service)
                elif check == "pods":
                    passed = await self._check_pods_ready(deployment)
                else:
                    # A check we don't know how to run is not a check we passed.
                    passed = self._unchecked(check, "unknown check type")

                if passed:
                    result["checks_passed"].append(check)
                else:
                    result["checks_failed"].append(check)

            except Exception as e:
                log.warning("Verification check %s failed: %s", check, e)
                result["checks_failed"].append(check)

        result["success"] = len(result["checks_failed"]) == 0

        return result

    def _unchecked(self, check: str, missing: str) -> bool:
        """Outcome for a check whose backend is missing — not a pass.

        These booleans decide whether a remediation is declared verified, so an
        un-run check has to count against verification; otherwise a half-wired
        deployment verifies every remediation it never observed.
        """
        if get_settings().copilot_demo_mode:
            log.warning(
                "COPILOT_DEMO_MODE=true - reporting %s check as passed (simulated)",
                check,
            )
            return True

        log.warning("Verification check '%s' could not run: %s", check, missing)
        return False

    async def _check_health(self, deployment: str) -> bool:
        """Check if deployment is healthy."""
        if not self.k8s:
            return self._unchecked("health", "no kubernetes client configured")

        try:
            status = await self.k8s.read_namespaced_deployment_status(
                name=deployment,
                namespace="default",
            )

            return (
                status.status.available_replicas == status.status.replicas
                and status.status.updated_replicas == status.status.replicas
            )

        except Exception:
            return False

    async def _check_error_rate(self, service: str) -> bool:
        """Check if error rate is below threshold."""
        if not self.prometheus_url:
            return self._unchecked("errors", "no prometheus_url configured")

        try:
            import httpx

            query = f'sum(rate(http_requests_total{{service="{service}",status=~"5.."}}[5m])) / sum(rate(http_requests_total{{service="{service}"}}[5m]))'

            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.prometheus_url}/api/v1/query",
                    params={"query": query},
                )
                response.raise_for_status()

                data = response.json()
                result = data.get("data", {}).get("result", [])

                if result:
                    error_rate = float(result[0]["value"][1])
                    return error_rate < 0.01

            # An empty result set means the 5xx rate query matched no samples,
            # which for a ratio of counters is genuinely "no errors seen".
            return True

        except Exception as e:
            # Prometheus is configured but unreachable: we have no measurement,
            # so the check cannot pass.
            log.warning("Error-rate check for %s could not reach Prometheus: %s", service, e)
            return False

    async def _check_latency(self, service: str) -> bool:
        """Check if latency is below threshold."""
        if not self.prometheus_url:
            return self._unchecked("latency", "no prometheus_url configured")

        try:
            import httpx

            query = f'histogram_quantile(0.99, sum(rate(http_request_duration_seconds_bucket{{service="{service}"}}[5m])) by (le))'

            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.prometheus_url}/api/v1/query",
                    params={"query": query},
                )
                response.raise_for_status()

                data = response.json()
                result = data.get("data", {}).get("result", [])

                if result:
                    latency = float(result[0]["value"][1])
                    return latency < 1.0

            # No histogram buckets matched: no traffic to be slow.
            return True

        except Exception as e:
            # Same as the error-rate check: unreachable Prometheus is not a pass.
            log.warning("Latency check for %s could not reach Prometheus: %s", service, e)
            return False

    async def _check_pods_ready(self, deployment: str) -> bool:
        """Check if all pods are ready."""
        if not self.k8s:
            return self._unchecked("pods", "no kubernetes client configured")

        try:
            pods = await self.k8s.list_namespaced_pod(
                namespace="default",
                label_selector=f"app={deployment}",
            )

            for pod in pods.items:
                for condition in pod.status.conditions or []:
                    if condition.type == "Ready" and condition.status != "True":
                        return False

            return len(pods.items) > 0

        except Exception:
            return False


class DecisionGateHandler:
    """Handler for decision gates in workflows."""

    def __init__(self, llm_client: Any = None):
        self.llm = llm_client

    async def __call__(
        self,
        task: TaskNode,
        inputs: dict[str, Any],
    ) -> dict[str, Any]:
        """Make a decision based on upstream task outputs."""
        hypotheses = inputs.get("dep_hypothesize", {})

        if isinstance(hypotheses.get("hypotheses"), str):
            import json
            try:
                hyp_data = json.loads(hypotheses["hypotheses"])
                hypotheses_list = hyp_data.get("hypotheses", [])
            except Exception:
                hypotheses_list = []
        else:
            hypotheses_list = hypotheses.get("hypotheses", [])

        if not hypotheses_list:
            return {
                "_branch": "investigate_more",
                "decision": "investigate_more",
                "reason": "No hypotheses generated",
            }

        top_hypothesis = hypotheses_list[0] if hypotheses_list else {}
        confidence = top_hypothesis.get("confidence", 0)

        # The bar for acting on a hypothesis is the same one the safety gates use
        # (COPILOT_SAFETY_CONFIDENCE_THRESHOLD). It was a literal 0.8 here, so
        # raising the safety threshold tightened the gates but left this router
        # still branching to "fix" at 0.8 — the two disagreed about what counts
        # as confident enough to change production.
        fix_bar = get_settings().safety_confidence_threshold
        # Never above the fix bar: if an operator lowers the safety threshold
        # below this floor, "investigate more" would be unreachable and every
        # hypothesis above it would route straight to a fix.
        investigate_bar = min(_INVESTIGATE_FLOOR, fix_bar)

        if confidence >= fix_bar:
            return {
                "_branch": "fix",
                "decision": "fix",
                "hypothesis": top_hypothesis,
                "reason": f"High confidence hypothesis ({confidence:.0%})",
            }
        elif confidence >= investigate_bar:
            return {
                "_branch": "investigate_more",
                "decision": "investigate_more",
                "hypothesis": top_hypothesis,
                "reason": f"Medium confidence, need more investigation ({confidence:.0%})",
            }
        else:
            return {
                "_branch": "escalate",
                "decision": "escalate",
                "hypothesis": top_hypothesis,
                "reason": f"Low confidence, escalating to human ({confidence:.0%})",
            }


def build_k8s_client() -> Any | None:
    """An async Kubernetes AppsV1/CoreV1 facade, or None if there is no cluster.

    These handlers `await` their client, so they need `kubernetes_asyncio`
    rather than the synchronous `kubernetes` package the runbook executor uses;
    awaiting a sync client's return value is a TypeError, not a remediation.
    Both the SDK and a kubeconfig are optional, and when either is missing the
    handlers fall back to simulating — which is fine as long as the caller is
    told, which is what `simulated` in the task output is for.

    The coordinator hardcoded `k8s_client=None`, so there was no setting,
    request field or environment in which remediation was anything but a
    simulation that reported success.
    """
    settings = get_settings()
    if not settings.kube_config_path:
        return None
    try:
        from kubernetes_asyncio import client, config  # lazy: optional dependency
    except ImportError:
        log.warning(
            "KUBE_CONFIG_PATH is set but `kubernetes_asyncio` is not installed; "
            "remediation tasks will be simulated"
        )
        return None

    try:
        config.load_kube_config(config_file=settings.kube_config_path)
    except Exception:
        log.exception("Could not load kubeconfig %s; remediation tasks will be simulated",
                      settings.kube_config_path)
        return None

    return _K8sFacade(client.AppsV1Api(), client.CoreV1Api())


class _K8sFacade:
    """One object exposing both AppsV1 and CoreV1, which is what the handlers assume.

    They call `read_namespaced_deployment` and `list_namespaced_pod` on the same
    attribute; the SDK splits those across two API classes.
    """

    def __init__(self, apps: Any, core: Any) -> None:
        self._apps = apps
        self._core = core

    def __getattr__(self, name: str) -> Any:
        for api in (self._apps, self._core):
            attr = getattr(api, name, None)
            if attr is not None:
                return attr
        raise AttributeError(name)


def create_remediation_handlers(
    k8s_client: Any = None,
    prometheus_url: str | None = None,
    knowledge_graph: Any = None,
    llm_client: Any = None,
) -> dict[str, Any]:
    """Create all remediation handlers with configured clients.

    `k8s_client=None` means "work it out from settings", not "simulate": pass a
    client explicitly to override, which is what the tests do.
    """
    if k8s_client is None:
        k8s_client = build_k8s_client()
    return {
        "scale_deployment": ScaleHandler(k8s_client=k8s_client),
        "restart_pods": RestartHandler(k8s_client=k8s_client),
        "rollback_deployment": RollbackHandler(
            k8s_client=k8s_client,
            knowledge_graph=knowledge_graph,
        ),
        "update_config": ConfigUpdateHandler(k8s_client=k8s_client),
        "verify_resolution": VerificationHandler(
            prometheus_url=prometheus_url,
            k8s_client=k8s_client,
        ),
        "decision_gate": DecisionGateHandler(llm_client=llm_client),
        "execute_remediation": ScaleHandler(k8s_client=k8s_client),
    }
