"""Service Discovery - Auto-discover services and their dependencies."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from enum import Enum

import httpx

log = logging.getLogger("omnipotent.dependency_graph.discovery")


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


@dataclass(frozen=True)
class CatalogValidation:
    valid: bool
    duplicate_fqdns: tuple[str, ...] = ()
    stale_fqdns: tuple[str, ...] = ()
    missing_names: tuple[str, ...] = ()


def validate_catalog(
    services: list[DiscoveredService],
    *,
    now: datetime | None = None,
    max_age_seconds: float = 900,
) -> CatalogValidation:
    """Validate identity and freshness before a catalog is used for reasoning."""
    now = now or datetime.now(timezone.utc)
    counts: dict[str, int] = {}
    stale: list[str] = []
    missing: list[str] = []
    for service in services:
        fqdn = service.fqdn
        counts[fqdn] = counts.get(fqdn, 0) + 1
        if not service.name.strip() or not service.namespace.strip():
            missing.append(fqdn)
        observed = service.discovered_at
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
        if (now - observed).total_seconds() > max_age_seconds:
            stale.append(fqdn)
    duplicates = tuple(sorted(k for k, count in counts.items() if count > 1))
    result = CatalogValidation(
        valid=not duplicates and not stale and not missing,
        duplicate_fqdns=duplicates,
        stale_fqdns=tuple(sorted(set(stale))),
        missing_names=tuple(sorted(set(missing))),
    )
    return result


@dataclass
class DiscoveryConfig:
    """Service discovery configuration."""
    # Kubernetes. Discovery reads the API server directly over the projected
    # ServiceAccount volume; kubernetes_api_url only needs setting when running
    # outside the cluster (e.g. against `kubectl proxy`).
    kubernetes_enabled: bool = True
    kubernetes_namespaces: list[str] = field(default_factory=lambda: ["default"])
    kubernetes_label_selector: str | None = None
    kubernetes_api_url: str | None = None
    kubernetes_token_path: str = "/var/run/secrets/kubernetes.io/serviceaccount/token"
    kubernetes_ca_path: str = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"

    # Consul
    consul_enabled: bool = False
    consul_url: str = "http://localhost:8500"

    # Prometheus
    prometheus_enabled: bool = True
    prometheus_url: str = "http://localhost:9090"

    # Istio. Edges come from Kiali's graph API, which reports the calls the mesh
    # actually observed. Without a URL no mesh edges are imported.
    istio_enabled: bool = False
    istio_kiali_url: str | None = None
    istio_lookback_seconds: int = 600

    # Discovery settings
    discovery_interval_seconds: int = 300
    include_external: bool = True
    # Guess edges from service naming conventions when no measured edges exist.
    # These are inferences, not observations — see _detect_dependencies.
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
        # Separate client: the Kubernetes one carries a bearer token and the
        # cluster CA, neither of which should be sent to Consul or Prometheus.
        self._k8s_http: httpx.AsyncClient | None = None
        self._services: dict[str, DiscoveredService] = {}
        self._last_discovery: datetime | None = None

        log.info("ServiceDiscovery initialized")

    async def close(self) -> None:
        """Close HTTP clients."""
        await self._client.aclose()
        if self._k8s_http is not None:
            await self._k8s_http.aclose()
            self._k8s_http = None

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

        validation = validate_catalog(services, max_age_seconds=max(1, self.config.discovery_interval_seconds * 3))
        if not validation.valid:
            log.warning("service catalog validation failed: %s", validation)
            # Do not publish ambiguous or stale identities. A stale dependency
            # is not safe evidence for blast-radius reasoning: retaining it in
            # the active catalog would turn an age warning into a confident,
            # wrong impact answer.
            invalid = set(validation.duplicate_fqdns) | set(validation.missing_names) | set(validation.stale_fqdns)
            services = [s for s in services if s.fqdn not in invalid]

        # Update cache
        self._services = {s.fqdn: s for s in services}
        self._last_discovery = datetime.now(timezone.utc)

        log.info("Discovered %d services", len(services))
        return services

    async def _discover_kubernetes(self) -> list[DiscoveredService]:
        """Discover services from Kubernetes."""
        services = []

        try:
            for namespace in self.config.kubernetes_namespaces:
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

    def _k8s_api_base(self) -> str | None:
        """Base URL of the Kubernetes API server, or None when not in a cluster."""
        if self.config.kubernetes_api_url:
            return self.config.kubernetes_api_url.rstrip("/")
        host = os.environ.get("KUBERNETES_SERVICE_HOST")
        port = os.environ.get("KUBERNETES_SERVICE_PORT_HTTPS") or os.environ.get(
            "KUBERNETES_SERVICE_PORT"
        )
        if not host:
            return None
        return f"https://{host}:{port or 443}"

    async def _k8s_client(self) -> httpx.AsyncClient | None:
        """An authenticated client for the in-cluster API, or None if unavailable.

        Built from the projected ServiceAccount volume that every pod gets. Returns
        None rather than falling back to an unverified connection: skipping CA
        verification against the API server would expose the token.
        """
        if self._k8s_http is not None:
            return self._k8s_http

        base = self._k8s_api_base()
        if base is None:
            log.info(
                "Kubernetes discovery enabled but no API server found "
                "(KUBERNETES_SERVICE_HOST unset and kubernetes_api_url not "
                "configured); skipping"
            )
            return None

        token_path = Path(self.config.kubernetes_token_path)
        try:
            token = token_path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            log.warning(
                "Kubernetes discovery enabled but the ServiceAccount token at %s is "
                "unreadable (%s); skipping",
                token_path,
                exc,
            )
            return None

        ca_path = Path(self.config.kubernetes_ca_path)
        verify: str | bool = str(ca_path) if ca_path.exists() else True

        self._k8s_http = httpx.AsyncClient(
            base_url=base,
            headers={"Authorization": f"Bearer {token}"},
            verify=verify,
            timeout=30.0,
        )
        return self._k8s_http

    async def _get_k8s_services(self, namespace: str) -> list[dict[str, Any]]:
        """List Services in `namespace` from the Kubernetes API.

        Returns [] — never invented services — when the cluster is unreachable. A
        dependency graph is used to reason about real production impact, so
        fabricated nodes here would surface as confident, wrong blast-radius
        answers about infrastructure the customer does not run.
        """
        client = await self._k8s_client()
        if client is None:
            return []

        params: dict[str, str] = {}
        if self.config.kubernetes_label_selector:
            params["labelSelector"] = self.config.kubernetes_label_selector

        try:
            response = await client.get(
                f"/api/v1/namespaces/{namespace}/services", params=params
            )
        except Exception as exc:
            log.warning("Kubernetes API request failed for %s: %s", namespace, exc)
            return []

        if response.status_code != 200:
            log.warning(
                "Kubernetes API returned %s listing services in %s",
                response.status_code,
                namespace,
            )
            return []

        services: list[dict[str, Any]] = []
        for item in response.json().get("items", []):
            metadata = item.get("metadata", {})
            spec = item.get("spec", {})
            ports = spec.get("ports") or [{}]
            first = ports[0]
            services.append(
                {
                    "name": metadata.get("name", ""),
                    "port": first.get("port", 80),
                    "protocol": (first.get("name") or first.get("appProtocol") or "http"),
                    "labels": metadata.get("labels", {}) or {},
                    "annotations": metadata.get("annotations", {}) or {},
                    "cluster_ip": spec.get("clusterIP"),
                }
            )
        return services

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
        """Read the service graph Istio observed, via Kiali's graph API.

        These are *measured* edges — Istio records the calls that actually happened —
        which is why they take precedence over the name-pattern guesses in
        `_detect_dependencies`. Returns {} when Kiali is not configured or
        unreachable, so the graph stays empty rather than becoming plausible fiction.
        """
        if not self.config.istio_kiali_url:
            log.info(
                "Istio discovery enabled but istio_kiali_url is not set; no "
                "dependency edges will be imported from the mesh"
            )
            return {}

        namespaces = ",".join(self.config.kubernetes_namespaces)
        try:
            response = await self._client.get(
                f"{self.config.istio_kiali_url.rstrip('/')}/api/namespaces/graph",
                params={
                    "namespaces": namespaces,
                    "graphType": "service",
                    "duration": str(self.config.istio_lookback_seconds),
                },
            )
        except Exception as exc:
            log.warning("Istio/Kiali discovery failed: %s", exc)
            return {}

        if response.status_code != 200:
            log.warning("Kiali returned %s for the service graph", response.status_code)
            return {}

        try:
            payload = response.json().get("elements", {})
            # Kiali returns cytoscape elements: nodes carry the service name, edges
            # reference node ids, so ids have to be resolved back to names.
            id_to_name = {
                node["data"]["id"]: node["data"].get("service")
                or node["data"].get("workload")
                or node["data"].get("app", "")
                for node in payload.get("nodes", [])
                if "data" in node and "id" in node["data"]
            }

            dependencies: dict[str, list[str]] = {}
            for edge in payload.get("edges", []):
                data = edge.get("data", {})
                source = id_to_name.get(data.get("source"))
                target = id_to_name.get(data.get("target"))
                if not source or not target or source == target:
                    continue
                dependencies.setdefault(source, [])
                if target not in dependencies[source]:
                    dependencies[source].append(target)
        except Exception as exc:
            log.warning("Could not parse the Kiali service graph: %s", exc)
            return {}

        log.info("Imported %d service(s) with mesh-observed edges", len(dependencies))
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
        """Guess edges from naming conventions, for services with no measured ones.

        This is inference, not discovery: it assumes anything matching `*-service`
        talks to postgres and redis. It only fills in services that Kiali left
        empty, and callers can switch it off entirely with
        `auto_detect_dependencies=False` when guessed edges are worse than none.
        Edges added here are marked so consumers can tell them apart.
        """
        # Common dependency patterns
        patterns = {
            r".*-api$": ["postgres", "redis", "kafka"],
            r".*-service$": ["postgres", "redis"],
            r".*-gateway$": [r".*-service$", r".*-api$"],
            r".*-worker$": ["postgres", "redis", "kafka", "rabbitmq"],
        }

        for service in services:
            if service.dependencies:
                continue  # Measured edges already present — never overwrite them.

            inferred: list[str] = []
            for pattern, dep_patterns in patterns.items():
                if re.match(pattern, service.name):
                    for dep_pattern in dep_patterns:
                        for other in services:
                            if other.name == service.name:
                                continue
                            if re.match(dep_pattern, other.name):
                                if other.name not in service.dependencies:
                                    service.dependencies.append(other.name)
                                    inferred.append(other.name)

            if inferred:
                # Consumers rendering a graph or computing blast radius need to know
                # these edges were guessed from names, not observed in traffic.
                service.metadata["inferred_dependencies"] = inferred

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
