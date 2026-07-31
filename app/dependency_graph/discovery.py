"""Service Discovery - Auto-discover services and their dependencies."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from enum import Enum

import httpx

log = logging.getLogger("devcopilot.dependency_graph.discovery")


class ServiceType(str, Enum):
    """Types of services."""
    API = "api"
    WEB = "web"
    WORKER = "worker"
    DATABASE = "database"
    CACHE = "cache"
    QUEUE = "queue"
    STORAGE = "storage"
    EXTERNAL = "external"
    UNKNOWN = "unknown"


class DiscoverySource(str, Enum):
    """Sources for service discovery."""
    KUBERNETES = "kubernetes"
    CONSUL = "consul"
    EUREKA = "eureka"
    AWS_ECS = "aws_ecs"
    PROMETHEUS = "prometheus"
    ISTIO = "istio"
    MANUAL = "manual"


@dataclass
class ServiceEndpoint:
    """A service endpoint."""
    url: str
    port: int
    protocol: str = "http"
    path: str = "/"
    health_check_path: str | None = None


@dataclass
class DiscoveredService:
    """A discovered service."""
    name: str
    namespace: str
    service_type: ServiceType
    endpoints: list[ServiceEndpoint] = field(default_factory=list)
    labels: dict[str, str] = field(default_factory=dict)
    annotations: dict[str, str] = field(default_factory=dict)
    dependencies: list[str] = field(default_factory=list)
    dependents: list[str] = field(default_factory=list)
    discovered_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    discovery_source: DiscoverySource = DiscoverySource.MANUAL
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def fqdn(self) -> str:
        """Fully qualified domain name."""
        return f"{self.name}.{self.namespace}"


@dataclass
class DiscoveryConfig:
    """Service discovery configuration."""
    # Kubernetes
    kubernetes_enabled: bool = True
    kubernetes_namespaces: list[str] = field(default_factory=lambda: ["default"])
    kubernetes_label_selector: str | None = None

    # Consul
    consul_enabled: bool = False
    consul_url: str = "http://localhost:8500"

    # Prometheus
    prometheus_enabled: bool = True
    prometheus_url: str = "http://localhost:9090"

    # Istio
    istio_enabled: bool = False

    # Discovery settings
    discovery_interval_seconds: int = 300
    include_external: bool = True
    auto_detect_dependencies: bool = True


class ServiceDiscovery:
    """Discovers services from multiple sources.

    Features:
    - Kubernetes service discovery
    - Consul service catalog
    - Prometheus service discovery
    - Istio service mesh
    - Automatic dependency detection
    """

    def __init__(self, config: DiscoveryConfig | None = None):
        """Initialize service discovery.

        Args:
            config: Discovery configuration
        """
        self.config = config or DiscoveryConfig()
        self._client = httpx.AsyncClient(timeout=30.0)
        self._services: dict[str, DiscoveredService] = {}
        self._last_discovery: datetime | None = None

        log.info("ServiceDiscovery initialized")

    async def close(self) -> None:
        """Close HTTP client."""
        await self._client.aclose()

    async def discover_all(self) -> list[DiscoveredService]:
        """Discover services from all enabled sources.

        Returns:
            List of discovered services
        """
        services: list[DiscoveredService] = []

        # Discover from each source
        if self.config.kubernetes_enabled:
            k8s_services = await self._discover_kubernetes()
            services.extend(k8s_services)

        if self.config.consul_enabled:
            consul_services = await self._discover_consul()
            services.extend(consul_services)

        if self.config.prometheus_enabled:
            prom_services = await self._discover_prometheus()
            services.extend(prom_services)

        if self.config.istio_enabled:
            istio_deps = await self._discover_istio_dependencies()
            self._merge_istio_dependencies(services, istio_deps)

        # Auto-detect dependencies
        if self.config.auto_detect_dependencies:
            await self._detect_dependencies(services)

        # Update cache
        self._services = {s.fqdn: s for s in services}
        self._last_discovery = datetime.now(timezone.utc)

        log.info("Discovered %d services", len(services))
        return services

    async def _discover_kubernetes(self) -> list[DiscoveredService]:
        """Discover services from Kubernetes."""
        services = []

        try:
            # In production: use kubernetes client
            # This is a simulation
            for namespace in self.config.kubernetes_namespaces:
                # Simulate K8s API response
                k8s_services = await self._get_k8s_services(namespace)

                for svc in k8s_services:
                    service_type = self._infer_service_type(svc)

                    discovered = DiscoveredService(
                        name=svc.get("name", ""),
                        namespace=namespace,
                        service_type=service_type,
                        endpoints=[
                            ServiceEndpoint(
                                url=f"{svc.get('name')}.{namespace}.svc.cluster.local",
                                port=svc.get("port", 80),
                                protocol=svc.get("protocol", "http"),
                            )
                        ],
                        labels=svc.get("labels", {}),
                        annotations=svc.get("annotations", {}),
                        discovery_source=DiscoverySource.KUBERNETES,
                        metadata={"cluster_ip": svc.get("cluster_ip")},
                    )
                    services.append(discovered)

        except Exception as e:
            log.warning("Kubernetes discovery failed: %s", e)

        return services

    async def _get_k8s_services(self, namespace: str) -> list[dict[str, Any]]:
        """Get services from Kubernetes API."""
        # In production: use kubernetes.client
        # Simulated response
        return [
            {
                "name": "api-gateway",
                "port": 8080,
                "labels": {"app": "api-gateway", "tier": "frontend"},
            },
            {
                "name": "user-service",
                "port": 8081,
                "labels": {"app": "user-service", "tier": "backend"},
            },
            {
                "name": "order-service",
                "port": 8082,
                "labels": {"app": "order-service", "tier": "backend"},
            },
            {
                "name": "payment-service",
                "port": 8083,
                "labels": {"app": "payment-service", "tier": "backend"},
            },
            {
                "name": "postgres",
                "port": 5432,
                "labels": {"app": "postgres", "tier": "database"},
            },
            {
                "name": "redis",
                "port": 6379,
                "labels": {"app": "redis", "tier": "cache"},
            },
        ]

    async def _discover_consul(self) -> list[DiscoveredService]:
        """Discover services from Consul."""
        services = []

        try:
            response = await self._client.get(
                f"{self.config.consul_url}/v1/catalog/services"
            )

            if response.status_code == 200:
                catalog = response.json()

                for service_name, tags in catalog.items():
                    # Get service details
                    detail_response = await self._client.get(
                        f"{self.config.consul_url}/v1/catalog/service/{service_name}"
                    )

                    if detail_response.status_code == 200:
                        instances = detail_response.json()
                        if instances:
                            instance = instances[0]
                            discovered = DiscoveredService(
                                name=service_name,
                                namespace="consul",
                                service_type=self._infer_service_type_from_tags(tags),
                                endpoints=[
                                    ServiceEndpoint(
                                        url=instance.get("ServiceAddress", ""),
                                        port=instance.get("ServicePort", 80),
                                    )
                                ],
                                labels={"tags": ",".join(tags)},
                                discovery_source=DiscoverySource.CONSUL,
                            )
                            services.append(discovered)

        except Exception as e:
            log.warning("Consul discovery failed: %s", e)

        return services

    async def _discover_prometheus(self) -> list[DiscoveredService]:
        """Discover services from Prometheus targets."""
        services = []

        try:
            response = await self._client.get(
                f"{self.config.prometheus_url}/api/v1/targets"
            )

            if response.status_code == 200:
                data = response.json()
                targets = data.get("data", {}).get("activeTargets", [])

                seen = set()
                for target in targets:
                    labels = target.get("labels", {})
                    job = labels.get("job", "")

                    if job and job not in seen:
                        seen.add(job)
                        discovered = DiscoveredService(
                            name=job,
                            namespace="prometheus",
                            service_type=ServiceType.API,
                            endpoints=[
                                ServiceEndpoint(
                                    url=target.get("scrapeUrl", ""),
                                    port=80,
                                )
                            ],
                            labels=labels,
                            discovery_source=DiscoverySource.PROMETHEUS,
                            metadata={"health": target.get("health")},
                        )
                        services.append(discovered)

        except Exception as e:
            log.warning("Prometheus discovery failed: %s", e)

        return services

    async def _discover_istio_dependencies(self) -> dict[str, list[str]]:
        """Discover dependencies from Istio telemetry."""
        dependencies: dict[str, list[str]] = {}

        # In production: query Istio telemetry/Kiali API
        # Simulated response
        dependencies = {
            "api-gateway": ["user-service", "order-service"],
            "order-service": ["payment-service", "postgres", "redis"],
            "user-service": ["postgres", "redis"],
            "payment-service": ["postgres"],
        }

        return dependencies

    def _merge_istio_dependencies(
        self,
        services: list[DiscoveredService],
        dependencies: dict[str, list[str]],
    ) -> None:
        """Merge Istio dependency data into services."""
        service_map = {s.name: s for s in services}

        for service_name, deps in dependencies.items():
            if service_name in service_map:
                service_map[service_name].dependencies = deps

                # Update dependents
                for dep in deps:
                    if dep in service_map:
                        if service_name not in service_map[dep].dependents:
                            service_map[dep].dependents.append(service_name)

    async def _detect_dependencies(self, services: list[DiscoveredService]) -> None:
        """Auto-detect dependencies based on common patterns."""
        # Common dependency patterns
        patterns = {
            r".*-api$": ["postgres", "redis", "kafka"],
            r".*-service$": ["postgres", "redis"],
            r".*-gateway$": [r".*-service$", r".*-api$"],
            r".*-worker$": ["postgres", "redis", "kafka", "rabbitmq"],
        }

        for service in services:
            if service.dependencies:
                continue  # Already has dependencies

            for pattern, dep_patterns in patterns.items():
                if re.match(pattern, service.name):
                    for dep_pattern in dep_patterns:
                        for other in services:
                            if other.name == service.name:
                                continue
                            if re.match(dep_pattern, other.name):
                                if other.name not in service.dependencies:
                                    service.dependencies.append(other.name)

    def _infer_service_type(self, svc: dict[str, Any]) -> ServiceType:
        """Infer service type from metadata."""
        labels = svc.get("labels", {})
        name = svc.get("name", "").lower()

        # Check tier label
        tier = labels.get("tier", "")
        if tier == "database":
            return ServiceType.DATABASE
        if tier == "cache":
            return ServiceType.CACHE
        if tier == "frontend":
            return ServiceType.WEB

        # Check name patterns
        if any(db in name for db in ["postgres", "mysql", "mongo", "redis", "elastic"]):
            return ServiceType.DATABASE
        if any(q in name for q in ["kafka", "rabbitmq", "sqs", "nats"]):
            return ServiceType.QUEUE
        if any(c in name for c in ["redis", "memcached", "cache"]):
            return ServiceType.CACHE
        if "worker" in name or "job" in name:
            return ServiceType.WORKER
        if "gateway" in name or "nginx" in name or "envoy" in name:
            return ServiceType.WEB

        return ServiceType.API

    def _infer_service_type_from_tags(self, tags: list[str]) -> ServiceType:
        """Infer service type from Consul tags."""
        tags_lower = [t.lower() for t in tags]

        if "database" in tags_lower:
            return ServiceType.DATABASE
        if "cache" in tags_lower:
            return ServiceType.CACHE
        if "queue" in tags_lower:
            return ServiceType.QUEUE
        if "web" in tags_lower:
            return ServiceType.WEB

        return ServiceType.API

    def get_service(self, name: str) -> DiscoveredService | None:
        """Get a cached service by name."""
        for fqdn, service in self._services.items():
            if service.name == name or fqdn == name:
                return service
        return None

    def get_all_services(self) -> list[DiscoveredService]:
        """Get all cached services."""
        return list(self._services.values())


def create_discovery(
    kubernetes_enabled: bool = True,
    prometheus_enabled: bool = True,
    **kwargs,
) -> ServiceDiscovery:
    """Factory function to create service discovery."""
    config = DiscoveryConfig(
        kubernetes_enabled=kubernetes_enabled,
        prometheus_enabled=prometheus_enabled,
        **kwargs,
    )
    return ServiceDiscovery(config)
