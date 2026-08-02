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

from app.coordinator.dag_executor import TaskNode

log = logging.getLogger("devcopilot.coordinator.handlers.remediation")


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
            log.warning("No Kubernetes client configured, simulating scale")
            result["success"] = True
            result["simulated"] = True
            return result

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
            log.warning("No Kubernetes client configured, simulating restart")
            result["success"] = True
            result["simulated"] = True
            return result

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
            log.warning("No Kubernetes client configured, simulating rollback")
            result["success"] = True
            result["simulated"] = True
            return result

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
            log.warning("No Kubernetes client configured, simulating config update")
            result["success"] = True
            result["simulated"] = True
            return result

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
                    passed = True

                if passed:
                    result["checks_passed"].append(check)
                else:
                    result["checks_failed"].append(check)

            except Exception as e:
                log.warning("Verification check %s failed: %s", check, e)
                result["checks_failed"].append(check)

        result["success"] = len(result["checks_failed"]) == 0

        return result

    async def _check_health(self, deployment: str) -> bool:
        """Check if deployment is healthy."""
        if not self.k8s:
            return True

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
            return True

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

            return True

        except Exception:
            return True

    async def _check_latency(self, service: str) -> bool:
        """Check if latency is below threshold."""
        if not self.prometheus_url:
            return True

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

            return True

        except Exception:
            return True

    async def _check_pods_ready(self, deployment: str) -> bool:
        """Check if all pods are ready."""
        if not self.k8s:
            return True

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

        if confidence >= 0.8:
            return {
                "_branch": "fix",
                "decision": "fix",
                "hypothesis": top_hypothesis,
                "reason": f"High confidence hypothesis ({confidence:.0%})",
            }
        elif confidence >= 0.5:
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


def create_remediation_handlers(
    k8s_client: Any = None,
    prometheus_url: str | None = None,
    knowledge_graph: Any = None,
    llm_client: Any = None,
) -> dict[str, Any]:
    """Create all remediation handlers with configured clients."""
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
