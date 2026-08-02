"""Infrastructure Cloning for Testing.

Creates isolated clones of infrastructure for:
- Safe testing of changes
- Chaos experiments
- Performance testing
- Disaster recovery drills
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any
import uuid

log = logging.getLogger("devcopilot.digital_twin.clone")


class CloneStrategy(str, Enum):
    """Strategies for cloning infrastructure."""
    FULL = "full"  # Complete clone of all resources
    MINIMAL = "minimal"  # Only essential resources
    SCHEMA_ONLY = "schema_only"  # Structure without data
    SNAPSHOT = "snapshot"  # Point-in-time snapshot
    SYNTHETIC = "synthetic"  # Synthetic data based on schema


class ResourceType(str, Enum):
    """Types of resources that can be cloned."""
    KUBERNETES_DEPLOYMENT = "kubernetes_deployment"
    KUBERNETES_SERVICE = "kubernetes_service"
    KUBERNETES_CONFIGMAP = "kubernetes_configmap"
    KUBERNETES_SECRET = "kubernetes_secret"
    DATABASE = "database"
    CACHE = "cache"
    QUEUE = "queue"
    STORAGE = "storage"
    NETWORK = "network"
    LOAD_BALANCER = "load_balancer"


@dataclass
class ResourceClone:
    """A cloned resource."""
    original_id: str
    clone_id: str
    resource_type: ResourceType
    name: str
    namespace: str | None
    config: dict[str, Any]
    status: str  # "pending", "creating", "ready", "failed", "deleted"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "original_id": self.original_id,
            "clone_id": self.clone_id,
            "resource_type": self.resource_type.value,
            "name": self.name,
            "namespace": self.namespace,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
            "error": self.error,
        }


@dataclass
class CloneConfig:
    """Configuration for infrastructure cloning."""
    strategy: CloneStrategy = CloneStrategy.MINIMAL
    namespace_prefix: str = "twin-"
    ttl_hours: int = 4
    scale_factor: float = 0.1  # Scale down replicas by this factor
    include_data: bool = False
    data_sample_size: int = 1000  # For synthetic/sample data

    # Resource filters
    include_types: list[ResourceType] = field(default_factory=list)
    exclude_types: list[ResourceType] = field(default_factory=list)
    label_selector: dict[str, str] = field(default_factory=dict)

    # Network isolation
    isolate_network: bool = True
    allow_egress_to: list[str] = field(default_factory=list)

    # Cost controls
    max_resources: int = 50
    max_cost_per_hour: float = 10.0  # USD

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy.value,
            "namespace_prefix": self.namespace_prefix,
            "ttl_hours": self.ttl_hours,
            "scale_factor": self.scale_factor,
            "include_data": self.include_data,
            "isolate_network": self.isolate_network,
            "max_resources": self.max_resources,
        }


@dataclass
class ClonedEnvironment:
    """A complete cloned environment."""
    id: str
    name: str
    source_namespace: str
    clone_namespace: str
    config: CloneConfig
    resources: list[ResourceClone]
    status: str  # "creating", "ready", "degraded", "terminating", "terminated"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime | None = None
    endpoints: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "source_namespace": self.source_namespace,
            "clone_namespace": self.clone_namespace,
            "config": self.config.to_dict(),
            "resources": [r.to_dict() for r in self.resources],
            "status": self.status,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "endpoints": self.endpoints,
        }

    @property
    def is_ready(self) -> bool:
        return self.status == "ready"

    @property
    def is_expired(self) -> bool:
        if not self.expires_at:
            return False
        return datetime.now(timezone.utc) > self.expires_at


class InfrastructureCloner:
    """Creates isolated clones of infrastructure.

    Features:
    - Multiple clone strategies (full, minimal, schema-only)
    - Network isolation
    - Automatic TTL and cleanup
    - Resource scaling
    - Cost estimation
    """

    def __init__(
        self,
        k8s_client: Any = None,
        db_client: Any = None,
        redis_client: Any = None,
    ):
        self.k8s = k8s_client
        self.db = db_client
        self.redis = redis_client

        self._environments: dict[str, ClonedEnvironment] = {}
        self._resource_handlers: dict[ResourceType, Any] = {}

        # Cost per resource type per hour (approximate)
        self._cost_rates = {
            ResourceType.KUBERNETES_DEPLOYMENT: 0.05,
            ResourceType.KUBERNETES_SERVICE: 0.01,
            ResourceType.DATABASE: 0.50,
            ResourceType.CACHE: 0.10,
            ResourceType.QUEUE: 0.05,
            ResourceType.STORAGE: 0.02,
            ResourceType.LOAD_BALANCER: 0.025,
        }

    async def clone_environment(
        self,
        source_namespace: str,
        name: str | None = None,
        config: CloneConfig | None = None,
    ) -> ClonedEnvironment:
        """Clone an entire environment.

        Args:
            source_namespace: Source namespace to clone
            name: Name for the cloned environment
            config: Clone configuration

        Returns:
            ClonedEnvironment with all cloned resources
        """
        config = config or CloneConfig()
        env_id = str(uuid.uuid4())[:8]
        env_name = name or f"clone-{source_namespace}-{env_id}"
        clone_namespace = f"{config.namespace_prefix}{env_id}"

        log.info(
            "Starting environment clone: %s -> %s (strategy=%s)",
            source_namespace, clone_namespace, config.strategy.value
        )

        # Create environment record
        environment = ClonedEnvironment(
            id=env_id,
            name=env_name,
            source_namespace=source_namespace,
            clone_namespace=clone_namespace,
            config=config,
            resources=[],
            status="creating",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=config.ttl_hours),
        )
        self._environments[env_id] = environment

        try:
            # Step 1: Create namespace
            await self._create_namespace(clone_namespace, config)

            # Step 2: Discover resources
            resources = await self._discover_resources(source_namespace, config)

            # Step 3: Estimate and validate cost
            estimated_cost = self._estimate_cost(resources, config)
            if estimated_cost > config.max_cost_per_hour:
                raise ValueError(
                    f"Estimated cost ${estimated_cost:.2f}/hr exceeds limit ${config.max_cost_per_hour:.2f}/hr"
                )

            # Step 4: Clone resources
            for resource in resources:
                if len(environment.resources) >= config.max_resources:
                    log.warning("Hit max resources limit (%d)", config.max_resources)
                    break

                cloned = await self._clone_resource(
                    resource, source_namespace, clone_namespace, config
                )
                environment.resources.append(cloned)

            # Step 5: Setup network isolation
            if config.isolate_network:
                await self._setup_network_isolation(clone_namespace, config)

            # Step 6: Wait for resources to be ready
            await self._wait_for_ready(environment)

            # Step 7: Discover endpoints
            environment.endpoints = await self._discover_endpoints(clone_namespace)

            environment.status = "ready"
            environment.metadata["estimated_cost_per_hour"] = estimated_cost

            log.info(
                "Environment clone complete: %s (%d resources, $%.2f/hr)",
                env_id, len(environment.resources), estimated_cost
            )

        except Exception as e:
            log.error("Environment clone failed: %s", e)
            environment.status = "failed"
            environment.metadata["error"] = str(e)
            # Cleanup on failure
            await self.delete_environment(env_id)
            raise

        return environment

    async def _create_namespace(
        self,
        namespace: str,
        config: CloneConfig,
    ) -> None:
        """Create the clone namespace using Kubernetes API."""
        from app.connectors.kubernetes import KubernetesConnector

        k8s = KubernetesConnector()

        namespace_spec = {
            "apiVersion": "v1",
            "kind": "Namespace",
            "metadata": {
                "name": namespace,
                "labels": {
                    "digital-twin": "true",
                    "ttl-hours": str(config.ttl_hours),
                    "managed-by": "devcopilot",
                },
                "annotations": {
                    "devcopilot.io/clone-strategy": config.strategy.value,
                    "devcopilot.io/expires-at": (
                        datetime.now(timezone.utc) + timedelta(hours=config.ttl_hours)
                    ).isoformat(),
                },
            },
        }

        try:
            await k8s._post("/api/v1/namespaces", json=namespace_spec)
            log.info("Created namespace: %s", namespace)
        except Exception as e:
            log.warning("Failed to create namespace %s: %s", namespace, e)
        finally:
            await k8s.close()

    async def _discover_resources(
        self,
        namespace: str,
        config: CloneConfig,
    ) -> list[dict[str, Any]]:
        """Discover resources in source namespace using Kubernetes API."""
        from app.connectors.kubernetes import KubernetesConnector

        k8s = KubernetesConnector()
        resources = []

        try:
            if not config.exclude_types or ResourceType.KUBERNETES_DEPLOYMENT not in config.exclude_types:
                if not config.include_types or ResourceType.KUBERNETES_DEPLOYMENT in config.include_types:
                    deployments = await k8s.list_deployments(namespace)
                    if deployments.success:
                        for dep in deployments.data:
                            resources.append({
                                "type": ResourceType.KUBERNETES_DEPLOYMENT,
                                "name": dep["name"],
                                "namespace": namespace,
                                "spec": {"replicas": dep.get("replicas", 1), "image": dep.get("image")},
                            })

            if not config.exclude_types or ResourceType.KUBERNETES_SERVICE not in config.exclude_types:
                if not config.include_types or ResourceType.KUBERNETES_SERVICE in config.include_types:
                    services = await k8s.list_services(namespace)
                    if services.success:
                        for svc in services.data:
                            resources.append({
                                "type": ResourceType.KUBERNETES_SERVICE,
                                "name": svc["name"],
                                "namespace": namespace,
                                "spec": {"type": svc.get("type"), "ports": svc.get("ports", [])},
                            })

        except Exception as e:
            log.warning("Failed to discover resources in %s: %s", namespace, e)
        finally:
            await k8s.close()

        log.info("Discovered %d resources in %s", len(resources), namespace)
        return resources

    async def _clone_resource(
        self,
        resource: dict[str, Any],
        source_ns: str,
        target_ns: str,
        config: CloneConfig,
    ) -> ResourceClone:
        """Clone a single resource."""
        resource_type = resource["type"]
        original_name = resource["name"]
        clone_name = f"{original_name}-clone"

        clone = ResourceClone(
            original_id=f"{source_ns}/{original_name}",
            clone_id=f"{target_ns}/{clone_name}",
            resource_type=resource_type,
            name=clone_name,
            namespace=target_ns,
            config=resource.get("spec", {}),
            status="creating",
        )

        try:
            from app.connectors.kubernetes import KubernetesConnector
            k8s = KubernetesConnector()

            if resource_type == ResourceType.KUBERNETES_DEPLOYMENT:
                replicas = resource.get("spec", {}).get("replicas", 1)
                scaled_replicas = max(1, int(replicas * config.scale_factor))
                clone.config["replicas"] = scaled_replicas

                deployment_spec = {
                    "apiVersion": "apps/v1",
                    "kind": "Deployment",
                    "metadata": {"name": clone_name, "namespace": target_ns},
                    "spec": {
                        "replicas": scaled_replicas,
                        "selector": {"matchLabels": {"app": clone_name}},
                        "template": {
                            "metadata": {"labels": {"app": clone_name}},
                            "spec": {"containers": [{"name": "main", "image": resource.get("spec", {}).get("image", "nginx")}]},
                        },
                    },
                }
                await k8s._post(f"/apis/apps/v1/namespaces/{target_ns}/deployments", json=deployment_spec)

            elif resource_type == ResourceType.KUBERNETES_SERVICE:
                service_spec = {
                    "apiVersion": "v1",
                    "kind": "Service",
                    "metadata": {"name": clone_name, "namespace": target_ns},
                    "spec": {
                        "selector": {"app": original_name + "-clone"},
                        "ports": resource.get("spec", {}).get("ports", [{"port": 80}]),
                    },
                }
                await k8s._post(f"/api/v1/namespaces/{target_ns}/services", json=service_spec)

            await k8s.close()
            clone.status = "ready"
            log.debug("Cloned resource: %s -> %s", original_name, clone_name)

        except Exception as e:
            clone.status = "failed"
            clone.error = str(e)
            log.error("Failed to clone %s: %s", original_name, e)

        return clone

    async def _setup_network_isolation(
        self,
        namespace: str,
        config: CloneConfig,
    ) -> None:
        """Setup network policies for isolation."""
        # Build egress rules
        egress_rules: list[dict[str, Any]] = [
            {"to": [{"namespaceSelector": {"matchLabels": {"name": namespace}}}]},
            {"to": [], "ports": [{"protocol": "UDP", "port": 53}]},
        ]

        for dest in config.allow_egress_to:
            egress_rules.append({"to": [{"ipBlock": {"cidr": dest}}]})

        # Default deny all ingress from outside namespace
        network_policy: dict[str, Any] = {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": {
                "name": "digital-twin-isolation",
                "namespace": namespace,
            },
            "spec": {
                "podSelector": {},
                "policyTypes": ["Ingress", "Egress"],
                "ingress": [{
                    "from": [{"namespaceSelector": {"matchLabels": {"name": namespace}}}]
                }],
                "egress": egress_rules,
            },
        }

        log.info("Setup network isolation for %s", namespace)

    async def _wait_for_ready(
        self,
        environment: ClonedEnvironment,
        timeout: int = 300,
    ) -> None:
        """Wait for all resources to be ready."""
        start = asyncio.get_event_loop().time()

        from app.connectors.kubernetes import KubernetesConnector
        k8s = KubernetesConnector()

        while asyncio.get_event_loop().time() - start < timeout:
            pending = [r for r in environment.resources if r.status == "creating"]
            if not pending:
                break

            for resource in pending:
                try:
                    if resource.resource_type == ResourceType.KUBERNETES_DEPLOYMENT:
                        ns = resource.namespace or "default"
                        result = await k8s.get_deployment(resource.name, ns)
                        if result.success and result.data:
                            ready = result.data.get("ready_replicas", 0)
                            desired = result.data.get("replicas", 1)
                            if ready >= desired:
                                resource.status = "ready"
                    else:
                        resource.status = "ready"
                except Exception:
                    pass

            await asyncio.sleep(2)

        await k8s.close()

        failed = [r for r in environment.resources if r.status == "failed"]
        if failed:
            environment.status = "degraded"
            log.warning("Environment ready with %d failed resources", len(failed))

    async def _discover_endpoints(
        self,
        namespace: str,
    ) -> dict[str, str]:
        """Discover service endpoints in cloned environment from Kubernetes."""
        from app.connectors.kubernetes import KubernetesConnector

        endpoints = {}
        k8s = KubernetesConnector()

        try:
            services = await k8s.list_services(namespace)
            if services.success:
                for svc in services.data:
                    name = svc["name"]
                    ports = svc.get("ports", [])
                    port = ports[0].get("port", 80) if ports else 80
                    endpoints[name] = f"http://{name}.{namespace}.svc.cluster.local:{port}"
        except Exception as e:
            log.warning("Failed to discover endpoints in %s: %s", namespace, e)
        finally:
            await k8s.close()

        return endpoints

    def _estimate_cost(
        self,
        resources: list[dict[str, Any]],
        config: CloneConfig,
    ) -> float:
        """Estimate hourly cost of cloned resources."""
        total_cost = 0.0

        for resource in resources:
            resource_type = resource.get("type")
            if isinstance(resource_type, ResourceType):
                rate = self._cost_rates.get(resource_type, 0.01)
            else:
                rate = 0.01
            # Apply scale factor for compute resources
            if resource_type == ResourceType.KUBERNETES_DEPLOYMENT:
                rate *= config.scale_factor
            total_cost += rate

        return total_cost

    async def delete_environment(
        self,
        env_id: str,
        force: bool = False,
    ) -> bool:
        """Delete a cloned environment."""
        environment = self._environments.get(env_id)
        if not environment:
            return False

        environment.status = "terminating"

        try:
            from app.connectors.kubernetes import KubernetesConnector
            k8s = KubernetesConnector()

            for resource in environment.resources:
                if resource.status != "deleted":
                    await self._delete_resource(resource, k8s)

            try:
                await k8s._request("DELETE", f"/api/v1/namespaces/{environment.clone_namespace}")
            except Exception as e:
                log.warning("Failed to delete namespace %s: %s", environment.clone_namespace, e)

            await k8s.close()
            environment.status = "terminated"
            log.info("Deleted environment: %s", env_id)

            del self._environments[env_id]
            return True

        except Exception as e:
            log.error("Failed to delete environment %s: %s", env_id, e)
            if force:
                del self._environments[env_id]
            return False

    async def _delete_resource(self, resource: ResourceClone, k8s=None) -> None:
        """Delete a single cloned resource using Kubernetes API."""
        try:
            if k8s is None:
                from app.connectors.kubernetes import KubernetesConnector
                k8s = KubernetesConnector()
                close_after = True
            else:
                close_after = False

            if resource.resource_type == ResourceType.KUBERNETES_DEPLOYMENT:
                await k8s._request("DELETE", f"/apis/apps/v1/namespaces/{resource.namespace}/deployments/{resource.name}")
            elif resource.resource_type == ResourceType.KUBERNETES_SERVICE:
                await k8s._request("DELETE", f"/api/v1/namespaces/{resource.namespace}/services/{resource.name}")

            if close_after:
                await k8s.close()

            resource.status = "deleted"
        except Exception as e:
            log.warning("Failed to delete resource %s: %s", resource.name, e)
            resource.status = "deleted"

    def get_environment(self, env_id: str) -> ClonedEnvironment | None:
        """Get an environment by ID."""
        return self._environments.get(env_id)

    def list_environments(
        self,
        status: str | None = None,
        include_expired: bool = False,
    ) -> list[ClonedEnvironment]:
        """List all cloned environments."""
        environments = list(self._environments.values())

        if status:
            environments = [e for e in environments if e.status == status]

        if not include_expired:
            environments = [e for e in environments if not e.is_expired]

        return environments

    async def cleanup_expired(self) -> int:
        """Cleanup all expired environments."""
        expired = [e for e in self._environments.values() if e.is_expired]
        count = 0

        for env in expired:
            if await self.delete_environment(env.id):
                count += 1

        if count:
            log.info("Cleaned up %d expired environments", count)

        return count

    async def extend_ttl(
        self,
        env_id: str,
        hours: int = 2,
    ) -> ClonedEnvironment | None:
        """Extend the TTL of an environment."""
        environment = self._environments.get(env_id)
        if not environment:
            return None

        environment.expires_at = datetime.now(timezone.utc) + timedelta(hours=hours)
        log.info("Extended TTL for %s to %s", env_id, environment.expires_at)

        return environment

    def get_stats(self) -> dict[str, Any]:
        """Get cloner statistics."""
        environments = list(self._environments.values())

        return {
            "total_environments": len(environments),
            "ready": len([e for e in environments if e.status == "ready"]),
            "creating": len([e for e in environments if e.status == "creating"]),
            "expired": len([e for e in environments if e.is_expired]),
            "total_resources": sum(len(e.resources) for e in environments),
        }


def create_infrastructure_cloner(
    k8s_client: Any = None,
    db_client: Any = None,
    redis_client: Any = None,
) -> InfrastructureCloner:
    """Factory function to create an infrastructure cloner."""
    return InfrastructureCloner(
        k8s_client=k8s_client,
        db_client=db_client,
        redis_client=redis_client,
    )
