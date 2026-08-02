"""Kubernetes Connector - Cluster operations and resource management."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.connectors.base import Connector, ConnectorConfig, ConnectorError, QueryResult

log = logging.getLogger("devcopilot.connectors.kubernetes")


@dataclass
class PodInfo:
    """Kubernetes pod information."""
    name: str
    namespace: str
    status: str
    node: str
    ip: str | None
    labels: dict[str, str]
    containers: list[dict[str, Any]]
    created_at: datetime
    restart_count: int = 0


@dataclass
class ServiceInfo:
    """Kubernetes service information."""
    name: str
    namespace: str
    type: str
    cluster_ip: str | None
    external_ip: str | None
    ports: list[dict[str, Any]]
    selector: dict[str, str]


@dataclass
class DeploymentInfo:
    """Kubernetes deployment information."""
    name: str
    namespace: str
    replicas: int
    available_replicas: int
    ready_replicas: int
    labels: dict[str, str]
    strategy: str
    image: str | None


class KubernetesConnector(Connector):
    """Connector for Kubernetes API.

    Supports:
    - Pod operations (list, get, delete, logs)
    - Deployment operations (list, get, scale, update)
    - Service operations (list, get)
    - Namespace operations
    - Node operations
    - Resource metrics
    """

    def __init__(self, config: ConnectorConfig | None = None):
        """Initialize the Kubernetes connector.

        Args:
            config: Optional connector config. If None, uses in-cluster config
                   or kubeconfig from environment.
        """
        if config is None:
            config = self._load_config_from_environment()
        super().__init__(config)
        self._api_version = "v1"
        self._apps_api_version = "apps/v1"

    def _load_config_from_environment(self) -> ConnectorConfig:
        """Load configuration from environment or in-cluster config."""
        if os.path.exists("/var/run/secrets/kubernetes.io/serviceaccount/token"):
            with open("/var/run/secrets/kubernetes.io/serviceaccount/token") as f:
                token = f.read().strip()
            return ConnectorConfig(
                url="https://kubernetes.default.svc",
                api_key=token,
                verify_ssl=os.path.exists("/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"),
            )

        kube_host = os.environ.get("KUBERNETES_SERVICE_HOST", "localhost")
        kube_port = os.environ.get("KUBERNETES_SERVICE_PORT", "6443")
        return ConnectorConfig(
            url=f"https://{kube_host}:{kube_port}",
            api_key=os.environ.get("KUBE_TOKEN", ""),
            verify_ssl=False,
        )

    @property
    def connector_type(self) -> str:
        return "kubernetes"

    def _default_headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        return headers

    async def health_check(self) -> bool:
        """Check Kubernetes API is reachable."""
        try:
            response = await self._get("/healthz")
            return response.status_code == 200
        except ConnectorError:
            return False

    async def list_namespaces(self) -> QueryResult:
        """List all namespaces."""
        import time
        start = time.time()

        try:
            response = await self._get(f"/api/{self._api_version}/namespaces")
            data = response.json()

            namespaces = [
                item["metadata"]["name"]
                for item in data.get("items", [])
            ]

            return QueryResult(
                success=True,
                data=namespaces,
                duration_ms=int((time.time() - start) * 1000),
            )
        except ConnectorError as e:
            return QueryResult(
                success=False,
                data=[],
                error=str(e),
                duration_ms=int((time.time() - start) * 1000),
            )

    async def list_pods(
        self,
        namespace: str = "default",
        label_selector: str | None = None,
    ) -> QueryResult:
        """List pods in a namespace.

        Args:
            namespace: Namespace to query
            label_selector: Optional label selector (e.g., "app=nginx")

        Returns:
            QueryResult with list of PodInfo
        """
        import time
        start = time.time()

        params = {}
        if label_selector:
            params["labelSelector"] = label_selector

        try:
            response = await self._get(
                f"/api/{self._api_version}/namespaces/{namespace}/pods",
                params=params,
            )
            data = response.json()

            pods = [self._parse_pod(item) for item in data.get("items", [])]

            return QueryResult(
                success=True,
                data=pods,
                duration_ms=int((time.time() - start) * 1000),
                metadata={"namespace": namespace, "count": len(pods)},
            )
        except ConnectorError as e:
            return QueryResult(
                success=False,
                data=[],
                error=str(e),
                duration_ms=int((time.time() - start) * 1000),
            )

    async def get_pod(self, name: str, namespace: str = "default") -> QueryResult:
        """Get a specific pod."""
        import time
        start = time.time()

        try:
            response = await self._get(
                f"/api/{self._api_version}/namespaces/{namespace}/pods/{name}"
            )
            data = response.json()

            return QueryResult(
                success=True,
                data=self._parse_pod(data),
                duration_ms=int((time.time() - start) * 1000),
            )
        except ConnectorError as e:
            return QueryResult(
                success=False,
                data=None,
                error=str(e),
                duration_ms=int((time.time() - start) * 1000),
            )

    async def delete_pod(self, name: str, namespace: str = "default") -> QueryResult:
        """Delete a pod (for chaos engineering or remediation)."""
        import time
        start = time.time()

        try:
            await self._request(
                "DELETE",
                f"/api/{self._api_version}/namespaces/{namespace}/pods/{name}",
            )

            return QueryResult(
                success=True,
                data={"deleted": name, "namespace": namespace},
                duration_ms=int((time.time() - start) * 1000),
            )
        except ConnectorError as e:
            return QueryResult(
                success=False,
                data=None,
                error=str(e),
                duration_ms=int((time.time() - start) * 1000),
            )

    async def get_pod_logs(
        self,
        name: str,
        namespace: str = "default",
        container: str | None = None,
        tail_lines: int = 100,
        since_seconds: int | None = None,
    ) -> QueryResult:
        """Get pod logs."""
        import time
        start = time.time()

        params: dict[str, Any] = {"tailLines": tail_lines}
        if container:
            params["container"] = container
        if since_seconds:
            params["sinceSeconds"] = since_seconds

        try:
            response = await self._get(
                f"/api/{self._api_version}/namespaces/{namespace}/pods/{name}/log",
                params=params,
            )

            return QueryResult(
                success=True,
                data=response.text,
                duration_ms=int((time.time() - start) * 1000),
            )
        except ConnectorError as e:
            return QueryResult(
                success=False,
                data="",
                error=str(e),
                duration_ms=int((time.time() - start) * 1000),
            )

    async def list_deployments(
        self,
        namespace: str = "default",
        label_selector: str | None = None,
    ) -> QueryResult:
        """List deployments in a namespace."""
        import time
        start = time.time()

        params = {}
        if label_selector:
            params["labelSelector"] = label_selector

        try:
            response = await self._get(
                f"/apis/{self._apps_api_version}/namespaces/{namespace}/deployments",
                params=params,
            )
            data = response.json()

            deployments = [
                self._parse_deployment(item)
                for item in data.get("items", [])
            ]

            return QueryResult(
                success=True,
                data=deployments,
                duration_ms=int((time.time() - start) * 1000),
                metadata={"namespace": namespace, "count": len(deployments)},
            )
        except ConnectorError as e:
            return QueryResult(
                success=False,
                data=[],
                error=str(e),
                duration_ms=int((time.time() - start) * 1000),
            )

    async def get_deployment(self, name: str, namespace: str = "default") -> QueryResult:
        """Get a specific deployment."""
        import time
        start = time.time()

        try:
            response = await self._get(
                f"/apis/{self._apps_api_version}/namespaces/{namespace}/deployments/{name}"
            )
            data = response.json()

            return QueryResult(
                success=True,
                data=self._parse_deployment(data),
                duration_ms=int((time.time() - start) * 1000),
            )
        except ConnectorError as e:
            return QueryResult(
                success=False,
                data=None,
                error=str(e),
                duration_ms=int((time.time() - start) * 1000),
            )

    async def scale_deployment(
        self,
        name: str,
        replicas: int,
        namespace: str = "default",
    ) -> QueryResult:
        """Scale a deployment."""
        import time
        start = time.time()

        patch_body = {
            "spec": {
                "replicas": replicas
            }
        }

        try:
            await self._request(
                "PATCH",
                f"/apis/{self._apps_api_version}/namespaces/{namespace}/deployments/{name}",
                json=patch_body,
                headers={
                    **self._default_headers(),
                    "Content-Type": "application/strategic-merge-patch+json",
                },
            )

            return QueryResult(
                success=True,
                data={"deployment": name, "replicas": replicas},
                duration_ms=int((time.time() - start) * 1000),
            )
        except ConnectorError as e:
            return QueryResult(
                success=False,
                data=None,
                error=str(e),
                duration_ms=int((time.time() - start) * 1000),
            )

    async def list_services(
        self,
        namespace: str = "default",
        label_selector: str | None = None,
    ) -> QueryResult:
        """List services in a namespace."""
        import time
        start = time.time()

        params = {}
        if label_selector:
            params["labelSelector"] = label_selector

        try:
            response = await self._get(
                f"/api/{self._api_version}/namespaces/{namespace}/services",
                params=params,
            )
            data = response.json()

            services = [
                self._parse_service(item)
                for item in data.get("items", [])
            ]

            return QueryResult(
                success=True,
                data=services,
                duration_ms=int((time.time() - start) * 1000),
                metadata={"namespace": namespace, "count": len(services)},
            )
        except ConnectorError as e:
            return QueryResult(
                success=False,
                data=[],
                error=str(e),
                duration_ms=int((time.time() - start) * 1000),
            )

    async def list_nodes(self) -> QueryResult:
        """List cluster nodes."""
        import time
        start = time.time()

        try:
            response = await self._get(f"/api/{self._api_version}/nodes")
            data = response.json()

            nodes = [
                {
                    "name": item["metadata"]["name"],
                    "labels": item["metadata"].get("labels", {}),
                    "status": self._get_node_status(item),
                    "capacity": item.get("status", {}).get("capacity", {}),
                    "allocatable": item.get("status", {}).get("allocatable", {}),
                }
                for item in data.get("items", [])
            ]

            return QueryResult(
                success=True,
                data=nodes,
                duration_ms=int((time.time() - start) * 1000),
                metadata={"count": len(nodes)},
            )
        except ConnectorError as e:
            return QueryResult(
                success=False,
                data=[],
                error=str(e),
                duration_ms=int((time.time() - start) * 1000),
            )

    async def get_pod_metrics(
        self,
        namespace: str = "default",
        label_selector: str | None = None,
    ) -> QueryResult:
        """Get pod resource metrics from metrics-server."""
        import time
        start = time.time()

        params = {}
        if label_selector:
            params["labelSelector"] = label_selector

        try:
            response = await self._get(
                f"/apis/metrics.k8s.io/v1beta1/namespaces/{namespace}/pods",
                params=params,
            )
            data = response.json()

            metrics = [
                {
                    "name": item["metadata"]["name"],
                    "namespace": item["metadata"]["namespace"],
                    "containers": [
                        {
                            "name": c["name"],
                            "cpu": c["usage"].get("cpu", "0"),
                            "memory": c["usage"].get("memory", "0"),
                        }
                        for c in item.get("containers", [])
                    ],
                    "timestamp": item.get("timestamp"),
                }
                for item in data.get("items", [])
            ]

            return QueryResult(
                success=True,
                data=metrics,
                duration_ms=int((time.time() - start) * 1000),
            )
        except ConnectorError as e:
            return QueryResult(
                success=False,
                data=[],
                error=str(e),
                duration_ms=int((time.time() - start) * 1000),
            )

    async def get_node_metrics(self) -> QueryResult:
        """Get node resource metrics from metrics-server."""
        import time
        start = time.time()

        try:
            response = await self._get("/apis/metrics.k8s.io/v1beta1/nodes")
            data = response.json()

            metrics = [
                {
                    "name": item["metadata"]["name"],
                    "cpu": item["usage"].get("cpu", "0"),
                    "memory": item["usage"].get("memory", "0"),
                    "timestamp": item.get("timestamp"),
                }
                for item in data.get("items", [])
            ]

            return QueryResult(
                success=True,
                data=metrics,
                duration_ms=int((time.time() - start) * 1000),
            )
        except ConnectorError as e:
            return QueryResult(
                success=False,
                data=[],
                error=str(e),
                duration_ms=int((time.time() - start) * 1000),
            )

    async def exec_command(
        self,
        pod_name: str,
        command: list[str],
        namespace: str = "default",
        container: str | None = None,
    ) -> QueryResult:
        """Execute a command in a pod container.

        Note: This is a simplified implementation. Full exec requires
        WebSocket support which is not available in basic httpx.
        """
        import time
        start = time.time()

        params: dict[str, Any] = {
            "command": command,
            "stdout": True,
            "stderr": True,
        }
        if container:
            params["container"] = container

        try:
            response = await self._post(
                f"/api/{self._api_version}/namespaces/{namespace}/pods/{pod_name}/exec",
                params=params,
            )

            return QueryResult(
                success=True,
                data=response.text,
                duration_ms=int((time.time() - start) * 1000),
            )
        except ConnectorError as e:
            return QueryResult(
                success=False,
                data="",
                error=str(e),
                duration_ms=int((time.time() - start) * 1000),
            )

    async def create_network_policy(
        self,
        name: str,
        namespace: str,
        pod_selector: dict[str, str],
        ingress_rules: list[dict] | None = None,
        egress_rules: list[dict] | None = None,
    ) -> QueryResult:
        """Create a network policy for isolation."""
        import time
        start = time.time()

        policy = {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": {
                "name": name,
                "namespace": namespace,
            },
            "spec": {
                "podSelector": {
                    "matchLabels": pod_selector,
                },
                "policyTypes": [],
            },
        }

        if ingress_rules is not None:
            policy["spec"]["policyTypes"].append("Ingress")
            policy["spec"]["ingress"] = ingress_rules

        if egress_rules is not None:
            policy["spec"]["policyTypes"].append("Egress")
            policy["spec"]["egress"] = egress_rules

        try:
            await self._post(
                f"/apis/networking.k8s.io/v1/namespaces/{namespace}/networkpolicies",
                json=policy,
            )

            return QueryResult(
                success=True,
                data={"created": name, "namespace": namespace},
                duration_ms=int((time.time() - start) * 1000),
            )
        except ConnectorError as e:
            return QueryResult(
                success=False,
                data=None,
                error=str(e),
                duration_ms=int((time.time() - start) * 1000),
            )

    async def delete_network_policy(
        self,
        name: str,
        namespace: str = "default",
    ) -> QueryResult:
        """Delete a network policy."""
        import time
        start = time.time()

        try:
            await self._request(
                "DELETE",
                f"/apis/networking.k8s.io/v1/namespaces/{namespace}/networkpolicies/{name}",
            )

            return QueryResult(
                success=True,
                data={"deleted": name, "namespace": namespace},
                duration_ms=int((time.time() - start) * 1000),
            )
        except ConnectorError as e:
            return QueryResult(
                success=False,
                data=None,
                error=str(e),
                duration_ms=int((time.time() - start) * 1000),
            )

    def _parse_pod(self, data: dict) -> dict[str, Any]:
        """Parse pod data into structured format."""
        metadata = data.get("metadata", {})
        spec = data.get("spec", {})
        status = data.get("status", {})

        restart_count = sum(
            cs.get("restartCount", 0)
            for cs in status.get("containerStatuses", [])
        )

        return {
            "name": metadata.get("name", ""),
            "namespace": metadata.get("namespace", "default"),
            "status": status.get("phase", "Unknown"),
            "node": spec.get("nodeName", ""),
            "ip": status.get("podIP"),
            "labels": metadata.get("labels", {}),
            "containers": [
                {
                    "name": c.get("name"),
                    "image": c.get("image"),
                    "ready": self._get_container_ready(c.get("name"), status),
                }
                for c in spec.get("containers", [])
            ],
            "created_at": metadata.get("creationTimestamp"),
            "restart_count": restart_count,
        }

    def _parse_deployment(self, data: dict) -> dict[str, Any]:
        """Parse deployment data into structured format."""
        metadata = data.get("metadata", {})
        spec = data.get("spec", {})
        status = data.get("status", {})

        containers = spec.get("template", {}).get("spec", {}).get("containers", [])
        image = containers[0].get("image") if containers else None

        return {
            "name": metadata.get("name", ""),
            "namespace": metadata.get("namespace", "default"),
            "replicas": spec.get("replicas", 0),
            "available_replicas": status.get("availableReplicas", 0),
            "ready_replicas": status.get("readyReplicas", 0),
            "labels": metadata.get("labels", {}),
            "strategy": spec.get("strategy", {}).get("type", "RollingUpdate"),
            "image": image,
        }

    def _parse_service(self, data: dict) -> dict[str, Any]:
        """Parse service data into structured format."""
        metadata = data.get("metadata", {})
        spec = data.get("spec", {})

        external_ips = spec.get("externalIPs", [])
        external_ip = external_ips[0] if external_ips else None

        return {
            "name": metadata.get("name", ""),
            "namespace": metadata.get("namespace", "default"),
            "type": spec.get("type", "ClusterIP"),
            "cluster_ip": spec.get("clusterIP"),
            "external_ip": external_ip,
            "ports": spec.get("ports", []),
            "selector": spec.get("selector", {}),
        }

    def _get_container_ready(self, name: str, status: dict) -> bool:
        """Check if a container is ready."""
        for cs in status.get("containerStatuses", []):
            if cs.get("name") == name:
                return cs.get("ready", False)
        return False

    def _get_node_status(self, node: dict) -> str:
        """Get node status from conditions."""
        conditions = node.get("status", {}).get("conditions", [])
        for condition in conditions:
            if condition.get("type") == "Ready":
                return "Ready" if condition.get("status") == "True" else "NotReady"
        return "Unknown"
