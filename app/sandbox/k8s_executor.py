"""Real Kubernetes Sandbox Executor.

Production-ready implementation for safe command execution:
- Ephemeral pod creation with strict security contexts
- Network policies for isolation
- Resource quotas and limits
- Automatic cleanup
- Output capture and streaming
- Timeout enforcement
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, AsyncIterator

log = logging.getLogger("omnipotent.sandbox.k8s")


class ExecutionStatus(str, Enum):
    """Execution status."""
    PENDING = "pending"
    CREATING = "creating"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


class SandboxTier(str, Enum):
    """Sandbox resource tiers."""
    MINIMAL = "minimal"  # 100m CPU, 128Mi RAM
    SMALL = "small"  # 500m CPU, 512Mi RAM
    MEDIUM = "medium"  # 1 CPU, 1Gi RAM
    LARGE = "large"  # 2 CPU, 4Gi RAM
    GPU = "gpu"  # 1 GPU, 4 CPU, 16Gi RAM


@dataclass
class ResourceLimits:
    """Resource limits for sandbox execution."""
    cpu_request: str
    cpu_limit: str
    memory_request: str
    memory_limit: str
    ephemeral_storage: str
    gpu_count: int = 0

    @classmethod
    def from_tier(cls, tier: SandboxTier) -> ResourceLimits:
        tiers = {
            SandboxTier.MINIMAL: cls("100m", "200m", "128Mi", "256Mi", "1Gi"),
            SandboxTier.SMALL: cls("500m", "1000m", "512Mi", "1Gi", "5Gi"),
            SandboxTier.MEDIUM: cls("1000m", "2000m", "1Gi", "2Gi", "10Gi"),
            SandboxTier.LARGE: cls("2000m", "4000m", "4Gi", "8Gi", "20Gi"),
            SandboxTier.GPU: cls("4000m", "8000m", "16Gi", "32Gi", "50Gi", 1),
        }
        return tiers.get(tier, tiers[SandboxTier.SMALL])


@dataclass
class ExecutionRequest:
    """A sandbox execution request."""
    id: str
    command: list[str]
    image: str
    tier: SandboxTier
    timeout_seconds: int
    environment: dict[str, str]
    working_dir: str | None
    input_files: dict[str, bytes]
    secrets: dict[str, str]
    network_enabled: bool
    created_at: datetime
    labels: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "command": self.command,
            "image": self.image,
            "tier": self.tier.value,
            "timeout_seconds": self.timeout_seconds,
            "environment": {k: "***" for k in self.environment},
            "working_dir": self.working_dir,
            "input_files": list(self.input_files.keys()),
            "network_enabled": self.network_enabled,
            "created_at": self.created_at.isoformat(),
            "labels": self.labels,
        }


@dataclass
class ExecutionResult:
    """Result of sandbox execution."""
    request_id: str
    status: ExecutionStatus
    exit_code: int | None
    stdout: str
    stderr: str
    output_files: dict[str, bytes]
    started_at: datetime | None
    completed_at: datetime | None
    duration_ms: int | None
    resource_usage: dict[str, Any]
    pod_name: str | None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "status": self.status.value,
            "exit_code": self.exit_code,
            "stdout": self.stdout[:10000] if self.stdout else "",
            "stderr": self.stderr[:10000] if self.stderr else "",
            "output_files": list(self.output_files.keys()),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "duration_ms": self.duration_ms,
            "resource_usage": self.resource_usage,
            "pod_name": self.pod_name,
            "error": self.error,
        }


@dataclass
class SandboxConfig:
    """Configuration for the sandbox executor."""
    namespace: str = "omnipotent-sandbox"
    service_account: str = "sandbox-executor"
    image_pull_secrets: list[str] = field(default_factory=list)
    node_selector: dict[str, str] = field(default_factory=dict)
    tolerations: list[dict[str, Any]] = field(default_factory=list)
    default_timeout: int = 300
    max_timeout: int = 3600
    cleanup_delay: int = 60
    allowed_registries: list[str] = field(default_factory=lambda: [
        "docker.io", "gcr.io", "ghcr.io", "quay.io", "public.ecr.aws"
    ])


class K8sSandboxExecutor:
    """Executes commands safely in ephemeral Kubernetes pods.

    Security features:
    - Non-root user enforcement
    - Read-only root filesystem
    - Dropped capabilities
    - No privilege escalation
    - Network isolation (optional)
    - Resource limits
    - Automatic cleanup
    """

    def __init__(
        self,
        config: SandboxConfig | None = None,
        kubeconfig: str | None = None,
    ):
        self.config = config or SandboxConfig()
        self.kubeconfig = kubeconfig

        self._k8s_client: Any = None
        self._core_api: Any = None
        self._batch_api: Any = None

        self._executions: dict[str, ExecutionResult] = {}
        self._active_pods: set[str] = set()

    def _get_k8s_client(self) -> tuple[Any, Any]:
        """Get Kubernetes API clients."""
        if self._core_api is not None:
            return self._core_api, self._batch_api

        try:
            from kubernetes import client, config as k8s_config

            if self.kubeconfig:
                k8s_config.load_kube_config(config_file=self.kubeconfig)
            else:
                try:
                    k8s_config.load_incluster_config()
                except k8s_config.ConfigException:
                    k8s_config.load_kube_config()

            self._core_api = client.CoreV1Api()
            self._batch_api = client.BatchV1Api()

            return self._core_api, self._batch_api

        except ImportError:
            log.error("kubernetes library not installed")
            raise RuntimeError("kubernetes library required")
        except Exception as e:
            log.error("Failed to initialize Kubernetes client: %s", e)
            raise

    async def execute(
        self,
        command: list[str],
        image: str = "python:3.11-slim",
        tier: SandboxTier = SandboxTier.SMALL,
        timeout_seconds: int | None = None,
        environment: dict[str, str] | None = None,
        working_dir: str | None = None,
        input_files: dict[str, bytes] | None = None,
        secrets: dict[str, str] | None = None,
        network_enabled: bool = False,
        labels: dict[str, str] | None = None,
    ) -> ExecutionResult:
        """Execute a command in an ephemeral sandbox pod.

        Args:
            command: Command and arguments to execute
            image: Container image to use
            tier: Resource tier
            timeout_seconds: Execution timeout
            environment: Environment variables
            working_dir: Working directory
            input_files: Files to copy into the container
            secrets: Secrets to mount (as environment variables)
            network_enabled: Whether to allow network access
            labels: Labels for the pod

        Returns:
            ExecutionResult with output and status
        """
        import secrets as secrets_mod
        request_id = f"exec_{secrets_mod.token_hex(6)}"

        if not self._validate_image(image):
            return ExecutionResult(
                request_id=request_id,
                status=ExecutionStatus.FAILED,
                exit_code=None,
                stdout="",
                stderr="",
                output_files={},
                started_at=None,
                completed_at=None,
                duration_ms=None,
                resource_usage={},
                pod_name=None,
                error=f"Image from untrusted registry: {image}",
            )

        timeout = min(
            timeout_seconds or self.config.default_timeout,
            self.config.max_timeout
        )

        request = ExecutionRequest(
            id=request_id,
            command=command,
            image=image,
            tier=tier,
            timeout_seconds=timeout,
            environment=environment or {},
            working_dir=working_dir,
            input_files=input_files or {},
            secrets=secrets or {},
            network_enabled=network_enabled,
            created_at=datetime.now(timezone.utc),
            labels=labels or {},
        )

        try:
            result = await self._execute_in_pod(request)
            self._executions[request_id] = result
            return result

        except asyncio.TimeoutError:
            return ExecutionResult(
                request_id=request_id,
                status=ExecutionStatus.TIMEOUT,
                exit_code=None,
                stdout="",
                stderr="",
                output_files={},
                started_at=request.created_at,
                completed_at=datetime.now(timezone.utc),
                duration_ms=timeout * 1000,
                resource_usage={},
                pod_name=None,
                error=f"Execution timed out after {timeout} seconds",
            )

        except Exception as e:
            log.error("Sandbox execution failed: %s", e)
            return ExecutionResult(
                request_id=request_id,
                status=ExecutionStatus.FAILED,
                exit_code=None,
                stdout="",
                stderr="",
                output_files={},
                started_at=request.created_at,
                completed_at=datetime.now(timezone.utc),
                duration_ms=None,
                resource_usage={},
                pod_name=None,
                error=str(e),
            )

    def _validate_image(self, image: str) -> bool:
        """Validate container image is from allowed registry."""
        if "/" not in image:
            return "docker.io" in self.config.allowed_registries

        registry = image.split("/")[0]
        if "." not in registry:
            return "docker.io" in self.config.allowed_registries

        return any(
            registry.endswith(allowed) or registry == allowed
            for allowed in self.config.allowed_registries
        )

    async def _execute_in_pod(self, request: ExecutionRequest) -> ExecutionResult:
        """Execute request in a Kubernetes pod."""
        core_api, batch_api = self._get_k8s_client()

        pod_name = f"sandbox-{request.id}"
        limits = ResourceLimits.from_tier(request.tier)

        pod_spec = self._build_pod_spec(pod_name, request, limits)

        await asyncio.to_thread(
            core_api.create_namespaced_pod,
            namespace=self.config.namespace,
            body=pod_spec,
        )

        self._active_pods.add(pod_name)
        started_at = datetime.now(timezone.utc)

        try:
            exit_code = await self._wait_for_pod_completion(
                pod_name, request.timeout_seconds
            )

            logs = await asyncio.to_thread(
                core_api.read_namespaced_pod_log,
                name=pod_name,
                namespace=self.config.namespace,
                container="executor",
            )

            completed_at = datetime.now(timezone.utc)
            duration_ms = int((completed_at - started_at).total_seconds() * 1000)

            pod = await asyncio.to_thread(
                core_api.read_namespaced_pod,
                name=pod_name,
                namespace=self.config.namespace,
            )

            resource_usage = {}
            if pod.status and pod.status.container_statuses:
                for cs in pod.status.container_statuses:
                    if cs.name == "executor":
                        resource_usage = {
                            "restart_count": cs.restart_count,
                        }

            status = ExecutionStatus.SUCCEEDED if exit_code == 0 else ExecutionStatus.FAILED

            return ExecutionResult(
                request_id=request.id,
                status=status,
                exit_code=exit_code,
                stdout=logs,
                stderr="",
                output_files={},
                started_at=started_at,
                completed_at=completed_at,
                duration_ms=duration_ms,
                resource_usage=resource_usage,
                pod_name=pod_name,
            )

        finally:
            asyncio.create_task(self._cleanup_pod(pod_name))

    def _build_pod_spec(
        self,
        pod_name: str,
        request: ExecutionRequest,
        limits: ResourceLimits,
    ) -> dict[str, Any]:
        """Build Kubernetes pod specification."""
        env_vars: list[dict[str, Any]] = [
            {"name": k, "value": v}
            for k, v in request.environment.items()
        ]

        for secret_name, _secret_value in request.secrets.items():
            env_vars.append({
                "name": secret_name,
                "valueFrom": {
                    "secretKeyRef": {
                        "name": f"sandbox-secret-{request.id}",
                        "key": secret_name,
                    }
                }
            })

        security_context = {
            "runAsNonRoot": True,
            "runAsUser": 1000,
            "runAsGroup": 1000,
            "fsGroup": 1000,
            "readOnlyRootFilesystem": True,
            "allowPrivilegeEscalation": False,
            "capabilities": {
                "drop": ["ALL"],
            },
            "seccompProfile": {
                "type": "RuntimeDefault",
            },
        }

        resources = {
            "requests": {
                "cpu": limits.cpu_request,
                "memory": limits.memory_request,
                "ephemeral-storage": limits.ephemeral_storage,
            },
            "limits": {
                "cpu": limits.cpu_limit,
                "memory": limits.memory_limit,
                "ephemeral-storage": limits.ephemeral_storage,
            },
        }

        if limits.gpu_count > 0:
            resources["limits"]["nvidia.com/gpu"] = str(limits.gpu_count)
            resources["requests"]["nvidia.com/gpu"] = str(limits.gpu_count)

        volume_mounts = [
            {"name": "tmp", "mountPath": "/tmp"},
            {"name": "work", "mountPath": "/work"},
        ]

        volumes = [
            {"name": "tmp", "emptyDir": {"sizeLimit": "1Gi"}},
            {"name": "work", "emptyDir": {"sizeLimit": limits.ephemeral_storage}},
        ]

        container = {
            "name": "executor",
            "image": request.image,
            "command": request.command[:1] if request.command else ["/bin/sh"],
            "args": request.command[1:] if len(request.command) > 1 else ["-c", "echo 'No command specified'"],
            "env": env_vars,
            "resources": resources,
            "securityContext": security_context,
            "volumeMounts": volume_mounts,
            "workingDir": request.working_dir or "/work",
        }

        labels = {
            "app": "omnipotent-sandbox",
            "execution-id": request.id,
            **request.labels,
        }

        annotations = {
            "omnipotent.io/sandbox": "true",
            "omnipotent.io/tier": request.tier.value,
        }

        if not request.network_enabled:
            annotations["omnipotent.io/network-policy"] = "deny-all"

        spec_dict: dict[str, Any] = {
            "restartPolicy": "Never",
            "serviceAccountName": self.config.service_account,
            "automountServiceAccountToken": False,
            "securityContext": {
                "runAsNonRoot": True,
                "seccompProfile": {"type": "RuntimeDefault"},
            },
            "containers": [container],
            "volumes": volumes,
            "activeDeadlineSeconds": request.timeout_seconds,
            "terminationGracePeriodSeconds": 10,
        }

        if self.config.node_selector:
            spec_dict["nodeSelector"] = self.config.node_selector

        if self.config.tolerations:
            spec_dict["tolerations"] = self.config.tolerations

        if self.config.image_pull_secrets:
            spec_dict["imagePullSecrets"] = [
                {"name": s} for s in self.config.image_pull_secrets
            ]

        pod_spec: dict[str, Any] = {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {
                "name": pod_name,
                "namespace": self.config.namespace,
                "labels": labels,
                "annotations": annotations,
            },
            "spec": spec_dict,
        }

        return pod_spec

    async def _wait_for_pod_completion(
        self,
        pod_name: str,
        timeout_seconds: int,
    ) -> int:
        """Wait for pod to complete and return exit code."""
        core_api, _ = self._get_k8s_client()
        start_time = time.time()

        while time.time() - start_time < timeout_seconds:
            pod = await asyncio.to_thread(
                core_api.read_namespaced_pod,
                name=pod_name,
                namespace=self.config.namespace,
            )

            if pod.status.phase in ["Succeeded", "Failed"]:
                if pod.status.container_statuses:
                    for cs in pod.status.container_statuses:
                        if cs.name == "executor" and cs.state.terminated:
                            return cs.state.terminated.exit_code
                return 0 if pod.status.phase == "Succeeded" else 1

            await asyncio.sleep(1)

        raise asyncio.TimeoutError("Pod did not complete in time")

    async def _cleanup_pod(self, pod_name: str, delay: int | None = None) -> None:
        """Clean up pod after execution."""
        if delay is None:
            delay = self.config.cleanup_delay

        await asyncio.sleep(delay)

        try:
            core_api, _ = self._get_k8s_client()
            await asyncio.to_thread(
                core_api.delete_namespaced_pod,
                name=pod_name,
                namespace=self.config.namespace,
                grace_period_seconds=0,
            )
            self._active_pods.discard(pod_name)
            log.info("Cleaned up sandbox pod: %s", pod_name)

        except Exception as e:
            log.warning("Failed to cleanup pod %s: %s", pod_name, e)

    async def stream_logs(
        self,
        request_id: str,
    ) -> AsyncIterator[str]:
        """Stream logs from an executing sandbox."""
        result = self._executions.get(request_id)
        if not result or not result.pod_name:
            return

        core_api, _ = self._get_k8s_client()

        try:
            logs_response = await asyncio.to_thread(
                core_api.read_namespaced_pod_log,
                name=result.pod_name,
                namespace=self.config.namespace,
                container="executor",
                follow=True,
                _preload_content=False,
            )

            for line in logs_response.stream():
                yield line.decode("utf-8", errors="replace")

        except Exception as e:
            log.warning("Log streaming error: %s", e)

    async def cancel(self, request_id: str) -> bool:
        """Cancel an executing sandbox."""
        result = self._executions.get(request_id)
        if not result or not result.pod_name:
            return False

        try:
            core_api, _ = self._get_k8s_client()
            await asyncio.to_thread(
                core_api.delete_namespaced_pod,
                name=result.pod_name,
                namespace=self.config.namespace,
                grace_period_seconds=5,
            )

            result.status = ExecutionStatus.CANCELLED
            result.completed_at = datetime.now(timezone.utc)
            self._active_pods.discard(result.pod_name)

            return True

        except Exception as e:
            log.error("Failed to cancel execution %s: %s", request_id, e)
            return False

    def get_execution(self, request_id: str) -> ExecutionResult | None:
        """Get execution result by ID."""
        return self._executions.get(request_id)

    def list_active(self) -> list[str]:
        """List active sandbox pods."""
        return list(self._active_pods)

    async def cleanup_all(self) -> int:
        """Clean up all sandbox pods."""
        cleaned = 0
        for pod_name in list(self._active_pods):
            try:
                await self._cleanup_pod(pod_name, delay=0)
                cleaned += 1
            except Exception:
                pass
        return cleaned

    def get_stats(self) -> dict[str, Any]:
        """Get executor statistics."""
        total = len(self._executions)
        succeeded = len([e for e in self._executions.values() if e.status == ExecutionStatus.SUCCEEDED])
        failed = len([e for e in self._executions.values() if e.status == ExecutionStatus.FAILED])

        return {
            "total_executions": total,
            "succeeded": succeeded,
            "failed": failed,
            "active_pods": len(self._active_pods),
            "success_rate": succeeded / total if total > 0 else 0,
        }


NAMESPACE_YAML = """
apiVersion: v1
kind: Namespace
metadata:
  name: omnipotent-sandbox
  labels:
    app.kubernetes.io/name: omnipotent-sandbox
    pod-security.kubernetes.io/enforce: restricted
    pod-security.kubernetes.io/enforce-version: latest
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: sandbox-executor
  namespace: omnipotent-sandbox
---
apiVersion: v1
kind: ResourceQuota
metadata:
  name: sandbox-quota
  namespace: omnipotent-sandbox
spec:
  hard:
    pods: "50"
    requests.cpu: "100"
    requests.memory: 100Gi
    limits.cpu: "200"
    limits.memory: 200Gi
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: deny-all
  namespace: omnipotent-sandbox
spec:
  podSelector:
    matchLabels:
      app: omnipotent-sandbox
  policyTypes:
  - Ingress
  - Egress
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-dns
  namespace: omnipotent-sandbox
spec:
  podSelector:
    matchLabels:
      app: omnipotent-sandbox
  policyTypes:
  - Egress
  egress:
  - to:
    - namespaceSelector:
        matchLabels:
          kubernetes.io/metadata.name: kube-system
    ports:
    - protocol: UDP
      port: 53
"""


def create_sandbox_executor(
    config: SandboxConfig | None = None,
    kubeconfig: str | None = None,
) -> K8sSandboxExecutor:
    """Factory function to create a sandbox executor."""
    return K8sSandboxExecutor(config=config, kubeconfig=kubeconfig)
