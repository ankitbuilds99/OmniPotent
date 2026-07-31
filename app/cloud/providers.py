"""Multi-Cloud Providers - AWS, GCP, Azure support."""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from enum import Enum

log = logging.getLogger("devcopilot.cloud.providers")


class CloudProvider(str, Enum):
    """Supported cloud providers."""
    AWS = "aws"
    GCP = "gcp"
    AZURE = "azure"
    KUBERNETES = "kubernetes"


class ResourceType(str, Enum):
    """Cloud resource types."""
    COMPUTE = "compute"
    DATABASE = "database"
    STORAGE = "storage"
    NETWORK = "network"
    CONTAINER = "container"
    SERVERLESS = "serverless"
    QUEUE = "queue"
    CACHE = "cache"


@dataclass
class CloudResource:
    """A cloud resource."""
    id: str
    name: str
    provider: CloudProvider
    resource_type: ResourceType
    region: str
    status: str = "unknown"
    tags: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None
    cost_per_hour: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "provider": self.provider.value,
            "resource_type": self.resource_type.value,
            "region": self.region,
            "status": self.status,
            "tags": self.tags,
            "cost_per_hour": self.cost_per_hour,
        }


@dataclass
class CloudMetrics:
    """Metrics from a cloud resource."""
    resource_id: str
    cpu_percent: float = 0.0
    memory_percent: float = 0.0
    network_in_bytes: float = 0.0
    network_out_bytes: float = 0.0
    disk_read_bytes: float = 0.0
    disk_write_bytes: float = 0.0
    request_count: int = 0
    error_count: int = 0
    latency_ms: float = 0.0
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class CloudConfig:
    """Cloud provider configuration."""
    provider: CloudProvider
    credentials: dict[str, str] = field(default_factory=dict)
    regions: list[str] = field(default_factory=list)
    default_region: str = ""
    enabled: bool = True


class CloudProviderBase(ABC):
    """Base class for cloud providers."""

    def __init__(self, config: CloudConfig):
        self.config = config
        self.provider = config.provider

    @abstractmethod
    async def list_resources(
        self,
        resource_type: ResourceType | None = None,
        region: str | None = None,
    ) -> list[CloudResource]:
        """List resources."""
        pass

    @abstractmethod
    async def get_resource(self, resource_id: str) -> CloudResource | None:
        """Get a resource by ID."""
        pass

    @abstractmethod
    async def get_metrics(
        self,
        resource_id: str,
        start_time: datetime,
        end_time: datetime,
    ) -> list[CloudMetrics]:
        """Get metrics for a resource."""
        pass

    @abstractmethod
    async def execute_action(
        self,
        resource_id: str,
        action: str,
        parameters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute an action on a resource."""
        pass


class AWSProvider(CloudProviderBase):
    """AWS cloud provider."""

    def __init__(self, config: CloudConfig):
        super().__init__(config)
        self._client = None
        log.info("AWSProvider initialized for regions: %s", config.regions)

    async def list_resources(
        self,
        resource_type: ResourceType | None = None,
        region: str | None = None,
    ) -> list[CloudResource]:
        """List AWS resources."""
        resources = []
        regions = [region] if region else self.config.regions

        for r in regions:
            if resource_type in (None, ResourceType.COMPUTE):
                resources.extend(await self._list_ec2_instances(r))
            if resource_type in (None, ResourceType.DATABASE):
                resources.extend(await self._list_rds_instances(r))
            if resource_type in (None, ResourceType.CONTAINER):
                resources.extend(await self._list_ecs_services(r))
            if resource_type in (None, ResourceType.SERVERLESS):
                resources.extend(await self._list_lambda_functions(r))

        return resources

    async def _list_ec2_instances(self, region: str) -> list[CloudResource]:
        """List EC2 instances."""
        # In production: use boto3
        return [
            CloudResource(
                id="i-1234567890abcdef0",
                name="web-server-1",
                provider=CloudProvider.AWS,
                resource_type=ResourceType.COMPUTE,
                region=region,
                status="running",
                tags={"env": "production"},
                cost_per_hour=0.0416,
            ),
        ]

    async def _list_rds_instances(self, region: str) -> list[CloudResource]:
        """List RDS instances."""
        return [
            CloudResource(
                id="db-1234567890",
                name="postgres-primary",
                provider=CloudProvider.AWS,
                resource_type=ResourceType.DATABASE,
                region=region,
                status="available",
                tags={"env": "production"},
                cost_per_hour=0.096,
            ),
        ]

    async def _list_ecs_services(self, region: str) -> list[CloudResource]:
        """List ECS services."""
        return []

    async def _list_lambda_functions(self, region: str) -> list[CloudResource]:
        """List Lambda functions."""
        return []

    async def get_resource(self, resource_id: str) -> CloudResource | None:
        """Get a resource by ID."""
        resources = await self.list_resources()
        for r in resources:
            if r.id == resource_id:
                return r
        return None

    async def get_metrics(
        self,
        resource_id: str,
        start_time: datetime,
        end_time: datetime,
    ) -> list[CloudMetrics]:
        """Get CloudWatch metrics."""
        # In production: use boto3 CloudWatch client
        return [
            CloudMetrics(
                resource_id=resource_id,
                cpu_percent=45.2,
                memory_percent=62.1,
            ),
        ]

    async def execute_action(
        self,
        resource_id: str,
        action: str,
        parameters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute AWS action."""
        log.info("Executing AWS action: %s on %s", action, resource_id)

        if action == "stop":
            return {"status": "stopping", "resource_id": resource_id}
        elif action == "start":
            return {"status": "starting", "resource_id": resource_id}
        elif action == "restart":
            return {"status": "restarting", "resource_id": resource_id}
        elif action == "scale":
            return {"status": "scaling", "new_count": parameters.get("count", 1) if parameters else 1}

        return {"error": f"Unknown action: {action}"}


class GCPProvider(CloudProviderBase):
    """GCP cloud provider."""

    def __init__(self, config: CloudConfig):
        super().__init__(config)
        self._client = None
        log.info("GCPProvider initialized for regions: %s", config.regions)

    async def list_resources(
        self,
        resource_type: ResourceType | None = None,
        region: str | None = None,
    ) -> list[CloudResource]:
        """List GCP resources."""
        resources = []

        if resource_type in (None, ResourceType.COMPUTE):
            resources.append(CloudResource(
                id="gce-instance-1",
                name="web-server-gcp",
                provider=CloudProvider.GCP,
                resource_type=ResourceType.COMPUTE,
                region=region or "us-central1",
                status="RUNNING",
            ))

        if resource_type in (None, ResourceType.DATABASE):
            resources.append(CloudResource(
                id="cloud-sql-1",
                name="postgres-gcp",
                provider=CloudProvider.GCP,
                resource_type=ResourceType.DATABASE,
                region=region or "us-central1",
                status="RUNNING",
            ))

        if resource_type in (None, ResourceType.CONTAINER):
            resources.append(CloudResource(
                id="gke-cluster-1",
                name="prod-cluster",
                provider=CloudProvider.GCP,
                resource_type=ResourceType.CONTAINER,
                region=region or "us-central1",
                status="RUNNING",
            ))

        return resources

    async def get_resource(self, resource_id: str) -> CloudResource | None:
        resources = await self.list_resources()
        for r in resources:
            if r.id == resource_id:
                return r
        return None

    async def get_metrics(
        self,
        resource_id: str,
        start_time: datetime,
        end_time: datetime,
    ) -> list[CloudMetrics]:
        """Get Cloud Monitoring metrics."""
        return [
            CloudMetrics(
                resource_id=resource_id,
                cpu_percent=38.5,
                memory_percent=55.2,
            ),
        ]

    async def execute_action(
        self,
        resource_id: str,
        action: str,
        parameters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        log.info("Executing GCP action: %s on %s", action, resource_id)
        return {"status": "success", "action": action, "resource_id": resource_id}


class AzureProvider(CloudProviderBase):
    """Azure cloud provider."""

    def __init__(self, config: CloudConfig):
        super().__init__(config)
        self._client = None
        log.info("AzureProvider initialized for regions: %s", config.regions)

    async def list_resources(
        self,
        resource_type: ResourceType | None = None,
        region: str | None = None,
    ) -> list[CloudResource]:
        """List Azure resources."""
        resources = []

        if resource_type in (None, ResourceType.COMPUTE):
            resources.append(CloudResource(
                id="vm-azure-1",
                name="web-server-azure",
                provider=CloudProvider.AZURE,
                resource_type=ResourceType.COMPUTE,
                region=region or "eastus",
                status="Running",
            ))

        if resource_type in (None, ResourceType.DATABASE):
            resources.append(CloudResource(
                id="sql-azure-1",
                name="postgres-azure",
                provider=CloudProvider.AZURE,
                resource_type=ResourceType.DATABASE,
                region=region or "eastus",
                status="Online",
            ))

        if resource_type in (None, ResourceType.CONTAINER):
            resources.append(CloudResource(
                id="aks-cluster-1",
                name="aks-prod",
                provider=CloudProvider.AZURE,
                resource_type=ResourceType.CONTAINER,
                region=region or "eastus",
                status="Running",
            ))

        return resources

    async def get_resource(self, resource_id: str) -> CloudResource | None:
        resources = await self.list_resources()
        for r in resources:
            if r.id == resource_id:
                return r
        return None

    async def get_metrics(
        self,
        resource_id: str,
        start_time: datetime,
        end_time: datetime,
    ) -> list[CloudMetrics]:
        """Get Azure Monitor metrics."""
        return [
            CloudMetrics(
                resource_id=resource_id,
                cpu_percent=42.0,
                memory_percent=58.3,
            ),
        ]

    async def execute_action(
        self,
        resource_id: str,
        action: str,
        parameters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        log.info("Executing Azure action: %s on %s", action, resource_id)
        return {"status": "success", "action": action, "resource_id": resource_id}


class MultiCloudManager:
    """Manages multiple cloud providers.

    Features:
    - Unified resource listing across clouds
    - Cross-cloud metrics aggregation
    - Multi-cloud action execution
    - Cost tracking
    """

    def __init__(self):
        self._providers: dict[CloudProvider, CloudProviderBase] = {}
        log.info("MultiCloudManager initialized")

    def register_provider(
        self,
        provider: CloudProvider,
        config: CloudConfig,
    ) -> None:
        """Register a cloud provider."""
        if provider == CloudProvider.AWS:
            self._providers[provider] = AWSProvider(config)
        elif provider == CloudProvider.GCP:
            self._providers[provider] = GCPProvider(config)
        elif provider == CloudProvider.AZURE:
            self._providers[provider] = AzureProvider(config)

        log.info("Registered provider: %s", provider.value)

    async def list_all_resources(
        self,
        resource_type: ResourceType | None = None,
    ) -> list[CloudResource]:
        """List resources from all providers."""
        all_resources = []

        tasks = [
            provider.list_resources(resource_type)
            for provider in self._providers.values()
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, list):
                all_resources.extend(result)

        return all_resources

    async def get_resource(
        self,
        resource_id: str,
        provider: CloudProvider | None = None,
    ) -> CloudResource | None:
        """Get a resource by ID."""
        if provider and provider in self._providers:
            return await self._providers[provider].get_resource(resource_id)

        # Search all providers
        for p in self._providers.values():
            resource = await p.get_resource(resource_id)
            if resource:
                return resource

        return None

    async def execute_action(
        self,
        provider: CloudProvider,
        resource_id: str,
        action: str,
        parameters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute action on a specific provider."""
        if provider not in self._providers:
            return {"error": f"Provider not registered: {provider}"}

        return await self._providers[provider].execute_action(
            resource_id, action, parameters
        )

    def get_total_cost(self, resources: list[CloudResource]) -> dict[str, float]:
        """Calculate total hourly cost by provider."""
        costs: dict[str, float] = {}

        for resource in resources:
            provider = resource.provider.value
            if provider not in costs:
                costs[provider] = 0.0
            costs[provider] += resource.cost_per_hour

        costs["total"] = sum(costs.values())
        return costs


def create_multi_cloud_manager(
    aws_regions: list[str] | None = None,
    gcp_regions: list[str] | None = None,
    azure_regions: list[str] | None = None,
) -> MultiCloudManager:
    """Factory function to create a multi-cloud manager."""
    manager = MultiCloudManager()

    if aws_regions:
        manager.register_provider(
            CloudProvider.AWS,
            CloudConfig(
                provider=CloudProvider.AWS,
                regions=aws_regions,
                default_region=aws_regions[0],
            ),
        )

    if gcp_regions:
        manager.register_provider(
            CloudProvider.GCP,
            CloudConfig(
                provider=CloudProvider.GCP,
                regions=gcp_regions,
                default_region=gcp_regions[0],
            ),
        )

    if azure_regions:
        manager.register_provider(
            CloudProvider.AZURE,
            CloudConfig(
                provider=CloudProvider.AZURE,
                regions=azure_regions,
                default_region=azure_regions[0],
            ),
        )

    return manager
