"""GPU Availability Tracker for Cost Optimization.

Production-ready implementation for tracking GPU resources:
- Real-time GPU availability monitoring
- Multi-cloud GPU inventory (AWS, GCP, Azure)
- Spot/preemptible instance tracking
- Cost optimization recommendations
- Reservation management
- Demand forecasting
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

import httpx

log = logging.getLogger("omnipotent.cost_optimizer.gpu")


class CloudProvider(str, Enum):
    """Supported cloud providers."""
    AWS = "aws"
    GCP = "gcp"
    AZURE = "azure"
    ON_PREM = "on_prem"


class GPUType(str, Enum):
    """Common GPU types."""
    # NVIDIA Data Center GPUs
    A100_40GB = "a100-40gb"
    A100_80GB = "a100-80gb"
    H100 = "h100"
    L4 = "l4"
    T4 = "t4"
    V100 = "v100"
    A10G = "a10g"

    # AMD
    MI250 = "mi250"
    MI300X = "mi300x"

    # Other
    CUSTOM = "custom"


class InstanceType(str, Enum):
    """Instance pricing types."""
    ON_DEMAND = "on_demand"
    SPOT = "spot"
    RESERVED = "reserved"
    PREEMPTIBLE = "preemptible"
    COMMITTED = "committed"


@dataclass
class GPUInstance:
    """A GPU instance/node."""
    instance_id: str
    provider: CloudProvider
    region: str
    zone: str | None
    gpu_type: GPUType
    gpu_count: int
    instance_type: InstanceType
    vcpu: int
    memory_gb: float
    hourly_cost: float
    available: bool
    utilization: float | None  # 0-1
    created_at: datetime
    last_seen: datetime
    tags: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "provider": self.provider.value,
            "region": self.region,
            "zone": self.zone,
            "gpu_type": self.gpu_type.value,
            "gpu_count": self.gpu_count,
            "instance_type": self.instance_type.value,
            "vcpu": self.vcpu,
            "memory_gb": self.memory_gb,
            "hourly_cost": self.hourly_cost,
            "available": self.available,
            "utilization": self.utilization,
            "created_at": self.created_at.isoformat(),
            "last_seen": self.last_seen.isoformat(),
            "tags": self.tags,
        }

    @property
    def daily_cost(self) -> float:
        return self.hourly_cost * 24

    @property
    def monthly_cost(self) -> float:
        return self.hourly_cost * 24 * 30

    @property
    def cost_per_gpu_hour(self) -> float:
        return self.hourly_cost / max(1, self.gpu_count)


@dataclass
class GPUAvailability:
    """GPU availability in a region."""
    provider: CloudProvider
    region: str
    gpu_type: GPUType
    instance_type: InstanceType
    available_count: int
    total_count: int
    spot_price: float | None
    on_demand_price: float
    last_updated: datetime
    interruption_rate: float | None  # For spot instances

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider.value,
            "region": self.region,
            "gpu_type": self.gpu_type.value,
            "instance_type": self.instance_type.value,
            "available_count": self.available_count,
            "total_count": self.total_count,
            "availability_percentage": self.availability_percentage,
            "spot_price": self.spot_price,
            "on_demand_price": self.on_demand_price,
            "savings_percentage": self.savings_percentage,
            "last_updated": self.last_updated.isoformat(),
            "interruption_rate": self.interruption_rate,
        }

    @property
    def availability_percentage(self) -> float:
        return (self.available_count / max(1, self.total_count)) * 100

    @property
    def savings_percentage(self) -> float:
        if self.spot_price and self.on_demand_price > 0:
            return ((self.on_demand_price - self.spot_price) / self.on_demand_price) * 100
        return 0


@dataclass
class GPUReservation:
    """A GPU reservation/commitment."""
    reservation_id: str
    provider: CloudProvider
    region: str
    gpu_type: GPUType
    gpu_count: int
    commitment_type: str  # 1-year, 3-year
    start_date: datetime
    end_date: datetime
    monthly_commitment: float
    utilization: float  # How much of the reservation is being used
    status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "reservation_id": self.reservation_id,
            "provider": self.provider.value,
            "region": self.region,
            "gpu_type": self.gpu_type.value,
            "gpu_count": self.gpu_count,
            "commitment_type": self.commitment_type,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "monthly_commitment": self.monthly_commitment,
            "utilization": self.utilization,
            "status": self.status,
            "days_remaining": self.days_remaining,
            "waste_cost": self.waste_cost,
        }

    @property
    def days_remaining(self) -> int:
        return max(0, (self.end_date - datetime.now(timezone.utc)).days)

    @property
    def waste_cost(self) -> float:
        unused_fraction = 1 - self.utilization
        return self.monthly_commitment * unused_fraction


@dataclass
class CostOptimizationRecommendation:
    """A cost optimization recommendation."""
    id: str
    category: str
    title: str
    description: str
    estimated_savings_monthly: float
    estimated_savings_percentage: float
    effort: str  # low, medium, high
    risk: str  # low, medium, high
    action_items: list[str]
    affected_resources: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "category": self.category,
            "title": self.title,
            "description": self.description,
            "estimated_savings_monthly": self.estimated_savings_monthly,
            "estimated_savings_percentage": self.estimated_savings_percentage,
            "effort": self.effort,
            "risk": self.risk,
            "action_items": self.action_items,
            "affected_resources": self.affected_resources,
        }


class GPUPriceDatabase:
    """Static database of GPU instance pricing."""

    GPU_PRICING = {
        CloudProvider.AWS: {
            GPUType.A100_40GB: {
                "instance": "p4d.24xlarge",
                "on_demand": 32.77,
                "spot_range": (10.0, 20.0),
                "gpus_per_instance": 8,
            },
            GPUType.A100_80GB: {
                "instance": "p4de.24xlarge",
                "on_demand": 40.96,
                "spot_range": (12.0, 25.0),
                "gpus_per_instance": 8,
            },
            GPUType.H100: {
                "instance": "p5.48xlarge",
                "on_demand": 98.32,
                "spot_range": None,  # Not available as spot
                "gpus_per_instance": 8,
            },
            GPUType.T4: {
                "instance": "g4dn.xlarge",
                "on_demand": 0.526,
                "spot_range": (0.16, 0.35),
                "gpus_per_instance": 1,
            },
            GPUType.A10G: {
                "instance": "g5.xlarge",
                "on_demand": 1.006,
                "spot_range": (0.30, 0.60),
                "gpus_per_instance": 1,
            },
            GPUType.V100: {
                "instance": "p3.2xlarge",
                "on_demand": 3.06,
                "spot_range": (0.92, 1.80),
                "gpus_per_instance": 1,
            },
        },
        CloudProvider.GCP: {
            GPUType.A100_40GB: {
                "machine_type": "a2-highgpu-1g",
                "on_demand": 3.67,
                "preemptible": 1.10,
                "gpus_per_instance": 1,
            },
            GPUType.A100_80GB: {
                "machine_type": "a2-ultragpu-1g",
                "on_demand": 5.00,
                "preemptible": 1.50,
                "gpus_per_instance": 1,
            },
            GPUType.H100: {
                "machine_type": "a3-highgpu-8g",
                "on_demand": 80.00,
                "preemptible": None,
                "gpus_per_instance": 8,
            },
            GPUType.T4: {
                "machine_type": "n1-standard-4",
                "on_demand": 0.35,
                "preemptible": 0.11,
                "gpus_per_instance": 1,
            },
            GPUType.L4: {
                "machine_type": "g2-standard-4",
                "on_demand": 0.70,
                "preemptible": 0.21,
                "gpus_per_instance": 1,
            },
            GPUType.V100: {
                "machine_type": "n1-standard-8",
                "on_demand": 2.48,
                "preemptible": 0.74,
                "gpus_per_instance": 1,
            },
        },
        CloudProvider.AZURE: {
            GPUType.A100_80GB: {
                "vm_size": "Standard_ND96asr_v4",
                "on_demand": 27.20,
                "spot_range": (8.0, 15.0),
                "gpus_per_instance": 8,
            },
            GPUType.H100: {
                "vm_size": "Standard_ND96isr_H100_v5",
                "on_demand": 98.00,
                "spot_range": None,
                "gpus_per_instance": 8,
            },
            GPUType.T4: {
                "vm_size": "Standard_NC4as_T4_v3",
                "on_demand": 0.526,
                "spot_range": (0.16, 0.35),
                "gpus_per_instance": 1,
            },
            GPUType.V100: {
                "vm_size": "Standard_NC6s_v3",
                "on_demand": 3.06,
                "spot_range": (0.92, 1.50),
                "gpus_per_instance": 1,
            },
            GPUType.A10G: {
                "vm_size": "Standard_NV36ads_A10_v5",
                "on_demand": 1.80,
                "spot_range": (0.54, 1.0),
                "gpus_per_instance": 1,
            },
        },
    }

    @classmethod
    def get_pricing(
        cls,
        provider: CloudProvider,
        gpu_type: GPUType,
    ) -> dict[str, Any] | None:
        return cls.GPU_PRICING.get(provider, {}).get(gpu_type)


class GPUTracker:
    """Tracks GPU availability and costs across clouds.

    Features:
    - Real-time availability monitoring via cloud APIs
    - Spot instance price tracking
    - Reservation management
    - Cost optimization recommendations
    - Demand forecasting
    """

    # Below this many observations a forecast would be a line through noise, so
    # forecast_demand() declines instead.
    MIN_OBSERVATIONS_FOR_FORECAST = 7

    def __init__(
        self,
        aws_region: str = "us-east-1",
        gcp_project: str | None = None,
        azure_subscription: str | None = None,
        redis_client: Any = None,
    ):
        self.aws_region = aws_region
        self.gcp_project = gcp_project or os.environ.get("GCP_PROJECT_ID")
        self.azure_subscription = azure_subscription or os.environ.get("AZURE_SUBSCRIPTION_ID")
        self.redis = redis_client

        self._instances: dict[str, GPUInstance] = {}
        self._availability: dict[str, GPUAvailability] = {}
        self._reservations: dict[str, GPUReservation] = {}

        # One entry per scan per GPU type: what was actually running, and when.
        # forecast_demand() projects from these and from nothing else.
        self._usage_history: list[dict[str, Any]] = []

        self._http_client: httpx.AsyncClient | None = None

        # Cloud-specific clients
        self._aws_ec2: Any = None
        self._gcp_compute: Any = None
        self._azure_compute: Any = None

    async def _get_http_client(self) -> httpx.AsyncClient:
        if self._http_client is None:
            from app.config import get_settings

            self._http_client = httpx.AsyncClient(
                timeout=get_settings().http_timeout_default
            )
        return self._http_client

    async def close(self) -> None:
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None

    def _get_aws_client(self, service: str) -> Any:
        """Get boto3 client for AWS service."""
        try:
            import boto3
            return boto3.client(service, region_name=self.aws_region)
        except ImportError:
            log.warning("boto3 not installed")
            return None
        except Exception as e:
            log.warning("Failed to create AWS client: %s", e)
            return None

    async def scan_aws_instances(self) -> list[GPUInstance]:
        """Scan AWS for GPU instances."""
        instances: list[GPUInstance] = []
        ec2 = self._get_aws_client("ec2")
        if not ec2:
            return instances

        try:
            gpu_instance_types = [
                "p4d.24xlarge", "p4de.24xlarge", "p5.48xlarge",
                "g4dn.xlarge", "g4dn.2xlarge", "g4dn.4xlarge",
                "g5.xlarge", "g5.2xlarge", "g5.4xlarge",
                "p3.2xlarge", "p3.8xlarge", "p3.16xlarge",
            ]

            response = await asyncio.to_thread(
                ec2.describe_instances,
                Filters=[
                    {"Name": "instance-type", "Values": gpu_instance_types},
                    {"Name": "instance-state-name", "Values": ["running", "pending"]},
                ],
            )

            now = datetime.now(timezone.utc)

            for reservation in response.get("Reservations", []):
                for instance in reservation.get("Instances", []):
                    instance_type = instance.get("InstanceType", "")
                    gpu_info = self._map_aws_instance_to_gpu(instance_type)

                    gpu_instance = GPUInstance(
                        instance_id=instance["InstanceId"],
                        provider=CloudProvider.AWS,
                        region=self.aws_region,
                        zone=instance.get("Placement", {}).get("AvailabilityZone"),
                        gpu_type=gpu_info["gpu_type"],
                        gpu_count=gpu_info["gpu_count"],
                        instance_type=InstanceType.SPOT if instance.get("InstanceLifecycle") == "spot" else InstanceType.ON_DEMAND,
                        vcpu=gpu_info["vcpu"],
                        memory_gb=gpu_info["memory_gb"],
                        hourly_cost=gpu_info["hourly_cost"],
                        available=instance.get("State", {}).get("Name") == "running",
                        utilization=None,
                        created_at=instance.get("LaunchTime", now),
                        last_seen=now,
                        tags={t["Key"]: t["Value"] for t in instance.get("Tags", [])},
                    )

                    instances.append(gpu_instance)
                    self._instances[gpu_instance.instance_id] = gpu_instance

        except Exception as e:
            log.error("Failed to scan AWS GPU instances: %s", e)

        self.record_observation()
        return instances

    def _map_aws_instance_to_gpu(self, instance_type: str) -> dict[str, Any]:
        """Map AWS instance type to GPU info."""
        mappings = {
            "p4d.24xlarge": {"gpu_type": GPUType.A100_40GB, "gpu_count": 8, "vcpu": 96, "memory_gb": 1152, "hourly_cost": 32.77},
            "p4de.24xlarge": {"gpu_type": GPUType.A100_80GB, "gpu_count": 8, "vcpu": 96, "memory_gb": 1152, "hourly_cost": 40.96},
            "p5.48xlarge": {"gpu_type": GPUType.H100, "gpu_count": 8, "vcpu": 192, "memory_gb": 2048, "hourly_cost": 98.32},
            "g4dn.xlarge": {"gpu_type": GPUType.T4, "gpu_count": 1, "vcpu": 4, "memory_gb": 16, "hourly_cost": 0.526},
            "g4dn.2xlarge": {"gpu_type": GPUType.T4, "gpu_count": 1, "vcpu": 8, "memory_gb": 32, "hourly_cost": 0.752},
            "g5.xlarge": {"gpu_type": GPUType.A10G, "gpu_count": 1, "vcpu": 4, "memory_gb": 16, "hourly_cost": 1.006},
            "g5.2xlarge": {"gpu_type": GPUType.A10G, "gpu_count": 1, "vcpu": 8, "memory_gb": 32, "hourly_cost": 1.212},
            "p3.2xlarge": {"gpu_type": GPUType.V100, "gpu_count": 1, "vcpu": 8, "memory_gb": 61, "hourly_cost": 3.06},
            "p3.8xlarge": {"gpu_type": GPUType.V100, "gpu_count": 4, "vcpu": 32, "memory_gb": 244, "hourly_cost": 12.24},
        }
        return mappings.get(instance_type, {
            "gpu_type": GPUType.CUSTOM,
            "gpu_count": 1,
            "vcpu": 4,
            "memory_gb": 16,
            "hourly_cost": 1.0,
        })

    async def get_aws_spot_prices(
        self,
        gpu_types: list[GPUType] | None = None,
    ) -> list[GPUAvailability]:
        """Get current AWS spot prices for GPU instances."""
        availability: list[GPUAvailability] = []
        ec2 = self._get_aws_client("ec2")
        if not ec2:
            return availability

        instance_types = []
        if gpu_types:
            for gpu_type in gpu_types:
                pricing = GPUPriceDatabase.get_pricing(CloudProvider.AWS, gpu_type)
                if pricing:
                    instance_types.append(pricing["instance"])
        else:
            instance_types = ["p4d.24xlarge", "g4dn.xlarge", "g5.xlarge", "p3.2xlarge"]

        try:
            response = await asyncio.to_thread(
                ec2.describe_spot_price_history,
                InstanceTypes=instance_types,
                ProductDescriptions=["Linux/UNIX"],
                StartTime=datetime.now(timezone.utc) - timedelta(hours=1),
            )

            now = datetime.now(timezone.utc)
            prices: dict[str, float] = {}

            for price_data in response.get("SpotPriceHistory", []):
                instance_type = price_data["InstanceType"]
                price = float(price_data["SpotPrice"])
                if instance_type not in prices or price < prices[instance_type]:
                    prices[instance_type] = price

            for instance_type, spot_price in prices.items():
                gpu_info = self._map_aws_instance_to_gpu(instance_type)

                avail = GPUAvailability(
                    provider=CloudProvider.AWS,
                    region=self.aws_region,
                    gpu_type=gpu_info["gpu_type"],
                    instance_type=InstanceType.SPOT,
                    available_count=0,  # Would need capacity API
                    total_count=0,
                    spot_price=spot_price,
                    on_demand_price=gpu_info["hourly_cost"],
                    last_updated=now,
                    interruption_rate=None,
                )
                availability.append(avail)

                key = f"{CloudProvider.AWS.value}:{self.aws_region}:{gpu_info['gpu_type'].value}:spot"
                self._availability[key] = avail

        except Exception as e:
            log.error("Failed to get AWS spot prices: %s", e)

        return availability

    async def scan_gcp_instances(self) -> list[GPUInstance]:
        """Scan GCP for GPU instances."""
        instances: list[GPUInstance] = []
        if not self.gcp_project:
            return instances

        try:
            from google.cloud import compute_v1

            client = compute_v1.InstancesClient()
            aggregated_list = client.aggregated_list(project=self.gcp_project)

            now = datetime.now(timezone.utc)

            for zone, response in aggregated_list:
                if response.instances:
                    for instance in response.instances:
                        if instance.guest_accelerators:
                            for accelerator in instance.guest_accelerators:
                                gpu_type = self._map_gcp_accelerator_to_type(
                                    accelerator.accelerator_type
                                )

                                gpu_instance = GPUInstance(
                                    instance_id=f"gcp-{instance.id}",
                                    provider=CloudProvider.GCP,
                                    region=zone.rsplit("/", 1)[-1].rsplit("-", 1)[0],
                                    zone=zone.rsplit("/", 1)[-1],
                                    gpu_type=gpu_type,
                                    gpu_count=accelerator.accelerator_count,
                                    instance_type=InstanceType.PREEMPTIBLE if instance.scheduling.preemptible else InstanceType.ON_DEMAND,
                                    vcpu=self._get_gcp_vcpu(instance.machine_type),
                                    memory_gb=self._get_gcp_memory(instance.machine_type),
                                    hourly_cost=self._estimate_gcp_cost(
                                        instance.machine_type, gpu_type, accelerator.accelerator_count
                                    ),
                                    available=instance.status == "RUNNING",
                                    utilization=None,
                                    created_at=now,
                                    last_seen=now,
                                    tags=dict(instance.labels) if instance.labels else {},
                                )

                                instances.append(gpu_instance)
                                self._instances[gpu_instance.instance_id] = gpu_instance

        except ImportError:
            log.warning("google-cloud-compute not installed")
        except Exception as e:
            log.error("Failed to scan GCP GPU instances: %s", e)

        self.record_observation()
        return instances

    def _map_gcp_accelerator_to_type(self, accelerator_type: str) -> GPUType:
        """Map GCP accelerator type to GPUType."""
        mappings = {
            "nvidia-tesla-a100": GPUType.A100_40GB,
            "nvidia-a100-80gb": GPUType.A100_80GB,
            "nvidia-h100-80gb": GPUType.H100,
            "nvidia-l4": GPUType.L4,
            "nvidia-tesla-t4": GPUType.T4,
            "nvidia-tesla-v100": GPUType.V100,
        }
        for key, gpu_type in mappings.items():
            if key in accelerator_type.lower():
                return gpu_type
        return GPUType.CUSTOM

    def _get_gcp_vcpu(self, machine_type: str) -> int:
        """Extract vCPU count from GCP machine type."""
        try:
            parts = machine_type.rsplit("-", 1)
            return int(parts[-1]) if parts[-1].isdigit() else 4
        except (ValueError, IndexError):
            return 4

    def _get_gcp_memory(self, machine_type: str) -> float:
        """Estimate memory from GCP machine type."""
        vcpu = self._get_gcp_vcpu(machine_type)
        if "highmem" in machine_type:
            return vcpu * 6.5
        elif "highcpu" in machine_type:
            return vcpu * 0.9
        return vcpu * 3.75

    def _estimate_gcp_cost(
        self,
        machine_type: str,
        gpu_type: GPUType,
        gpu_count: int,
    ) -> float:
        """Estimate GCP instance cost."""
        pricing = GPUPriceDatabase.get_pricing(CloudProvider.GCP, gpu_type)
        if pricing:
            return pricing["on_demand"] * gpu_count
        return 1.0 * gpu_count

    async def get_recommendations(self) -> list[CostOptimizationRecommendation]:
        """Generate cost optimization recommendations."""
        recommendations = []
        rec_id = 0

        on_demand_instances = [
            i for i in self._instances.values()
            if i.instance_type == InstanceType.ON_DEMAND and i.available
        ]

        for instance in on_demand_instances:
            pricing = GPUPriceDatabase.get_pricing(instance.provider, instance.gpu_type)

            # Prefer the spot price we actually fetched over the static range in
            # GPUPriceDatabase: the range is a list-price reference that goes stale,
            # and this figure is what a customer decides a migration on.
            key = (
                f"{instance.provider.value}:{instance.region}:"
                f"{instance.gpu_type.value}:spot"
            )
            observed = self._availability.get(key)
            if observed is not None and observed.spot_price is not None:
                avg_spot: float | None = observed.spot_price
                basis = f"current spot price ${observed.spot_price:.2f}/hr"
            elif pricing and pricing.get("spot_range"):
                spot_min, spot_max = pricing["spot_range"]
                avg_spot = (spot_min + spot_max) / 2
                basis = (
                    f"midpoint of the reference spot range "
                    f"${spot_min:.2f}–${spot_max:.2f}/hr, not a live quote"
                )
            else:
                avg_spot = None
                basis = ""

            if avg_spot is not None:
                savings = (instance.hourly_cost - avg_spot) * 24 * 30

                if savings > 100:
                    rec_id += 1
                    recommendations.append(CostOptimizationRecommendation(
                        id=f"rec_{rec_id}",
                        category="spot_conversion",
                        title=f"Convert {instance.instance_id} to Spot",
                        description=f"This {instance.gpu_type.value} instance could save ${savings:.0f}/month by using spot instances (based on {basis})",
                        estimated_savings_monthly=savings,
                        estimated_savings_percentage=((instance.hourly_cost - avg_spot) / instance.hourly_cost) * 100,
                        effort="low",
                        risk="medium",
                        action_items=[
                            "Implement checkpoint/restart capability",
                            "Use Spot Fleet or Instance Fleet",
                            "Set up interruption handling",
                        ],
                        affected_resources=[instance.instance_id],
                    ))

        underutilized = [
            i for i in self._instances.values()
            if i.utilization is not None and i.utilization < 0.3 and i.monthly_cost is not None
        ]

        for instance in underutilized:
            waste = (instance.monthly_cost or 0) * (1 - (instance.utilization or 0))
            if waste > 50:
                rec_id += 1
                recommendations.append(CostOptimizationRecommendation(
                    id=f"rec_{rec_id}",
                    category="rightsizing",
                    title=f"Rightsize underutilized {instance.instance_id}",
                    description=f"GPU utilization is only {(instance.utilization or 0)*100:.0f}%. Consider smaller instance or workload consolidation",
                    estimated_savings_monthly=waste * 0.7,
                    estimated_savings_percentage=70,
                    effort="medium",
                    risk="low",
                    action_items=[
                        "Analyze workload requirements",
                        "Consider GPU sharing/MIG",
                        "Schedule workloads during peak utilization",
                    ],
                    affected_resources=[instance.instance_id],
                ))

        for reservation in self._reservations.values():
            if reservation.utilization < 0.5:
                rec_id += 1
                waste = reservation.waste_cost
                recommendations.append(CostOptimizationRecommendation(
                    id=f"rec_{rec_id}",
                    category="reservation_optimization",
                    title=f"Optimize reservation {reservation.reservation_id}",
                    description=f"Reservation is only {reservation.utilization*100:.0f}% utilized, wasting ${waste:.0f}/month",
                    estimated_savings_monthly=waste,
                    estimated_savings_percentage=(1 - reservation.utilization) * 100,
                    effort="high",
                    risk="low",
                    action_items=[
                        "Review workload scheduling",
                        "Consider selling unused capacity in marketplace",
                        "Plan migration to right-sized reservation",
                    ],
                    affected_resources=[reservation.reservation_id],
                ))

        return sorted(recommendations, key=lambda r: r.estimated_savings_monthly, reverse=True)

    def record_observation(self) -> None:
        """Snapshot current GPU counts per type as one point of usage history.

        Called at the end of each cloud scan. This is the only thing forecasts are
        built from, so a deployment that never scans has no history and gets no
        forecast — which is the correct answer, not a reason to synthesise one.
        """
        now = datetime.now(timezone.utc)
        counts: dict[str, int] = {}
        for instance in self._instances.values():
            if not instance.available:
                continue
            counts[instance.gpu_type.value] = (
                counts.get(instance.gpu_type.value, 0) + instance.gpu_count
            )

        for gpu_type, gpus in counts.items():
            self._usage_history.append({"at": now, "gpu_type": gpu_type, "gpus": gpus})

    async def forecast_demand(
        self,
        gpu_type: GPUType,
        horizon_days: int = 30,
    ) -> dict[str, Any]:
        """Project GPU demand from observed usage history.

        Returns ``status: "insufficient_data"`` rather than a forecast when too few
        observations exist. This used to manufacture 30 days of history from a
        formula (``50 + 20 * ...``) and project from that, so it produced a
        confident curve — and a "reserve N GPUs" recommendation — for customers
        whose fleet it had never looked at. A reservation is a spending commitment;
        inventing the demand behind it is worse than declining to answer.
        """
        observations = [
            o for o in self._usage_history if o["gpu_type"] == gpu_type.value
        ]

        if len(observations) < self.MIN_OBSERVATIONS_FOR_FORECAST:
            return {
                "gpu_type": gpu_type.value,
                "status": "insufficient_data",
                "observations": len(observations),
                "observations_required": self.MIN_OBSERVATIONS_FOR_FORECAST,
                "message": (
                    "Not enough observed usage to forecast. Scan the cloud accounts "
                    "regularly to build history."
                ),
                "forecast": [],
                "recommendations": [],
            }

        observations.sort(key=lambda o: o["at"])
        usage = [float(o["gpus"]) for o in observations]
        avg_usage = sum(usage) / len(usage)

        # Slope per observation, from the mean of the first half against the second.
        half = len(usage) // 2
        early = sum(usage[:half]) / half
        late = sum(usage[half:]) / (len(usage) - half)
        span = max(1, len(usage) - half)
        trend = (late - early) / span

        # The spread of what was actually observed, used as the confidence band —
        # a fixed ±20% would imply a precision this has no basis for.
        spread = max(usage) - min(usage)
        band = max(spread / 2, avg_usage * 0.1)

        # Project forward from the most recent observation, not the mean: on a
        # growing fleet the mean is already behind, so anchoring there forecasts
        # fewer GPUs than are running right now.
        latest = usage[-1]

        forecast = []
        for day in range(1, horizon_days + 1):
            projected = latest + trend * day
            forecast.append({
                "day": day,
                "projected_gpus": max(0, int(projected)),
                "confidence_low": max(0, int(projected - band)),
                "confidence_high": int(projected + band),
            })

        baseline = int(min(usage))
        return {
            "gpu_type": gpu_type.value,
            "status": "ok",
            "observations": len(observations),
            "observed_from": observations[0]["at"].isoformat(),
            "observed_to": observations[-1]["at"].isoformat(),
            "current_usage": int(usage[-1]),
            "average_usage": round(avg_usage, 1),
            "trend": "increasing" if trend > 0 else "decreasing" if trend < 0 else "stable",
            "forecast": forecast,
            "recommendations": [
                f"Baseline observed usage never fell below {baseline} GPUs; "
                f"that is the safe floor for a reservation",
                "Use spot instances for burst capacity above the baseline",
            ],
        }

    def get_inventory_summary(self) -> dict[str, Any]:
        """Get GPU inventory summary across all clouds."""
        total_gpus = 0
        total_monthly_cost = 0.0
        by_provider: dict[str, dict[str, float]] = {}
        by_gpu_type: dict[str, dict[str, float]] = {}
        by_instance_type: dict[str, dict[str, float]] = {}

        for instance in self._instances.values():
            cost = instance.monthly_cost or 0.0
            total_gpus += instance.gpu_count
            total_monthly_cost += cost

            prov = instance.provider.value
            if prov not in by_provider:
                by_provider[prov] = {"gpus": 0, "cost": 0.0}
            by_provider[prov]["gpus"] += instance.gpu_count
            by_provider[prov]["cost"] += cost

            gtype = instance.gpu_type.value
            if gtype not in by_gpu_type:
                by_gpu_type[gtype] = {"gpus": 0, "cost": 0.0}
            by_gpu_type[gtype]["gpus"] += instance.gpu_count
            by_gpu_type[gtype]["cost"] += cost

            itype = instance.instance_type.value
            if itype not in by_instance_type:
                by_instance_type[itype] = {"gpus": 0, "cost": 0.0}
            by_instance_type[itype]["gpus"] += instance.gpu_count
            by_instance_type[itype]["cost"] += cost

        return {
            "total_gpus": total_gpus,
            "total_monthly_cost": total_monthly_cost,
            "by_provider": by_provider,
            "by_gpu_type": by_gpu_type,
            "by_instance_type": by_instance_type,
        }

    def list_instances(
        self,
        provider: CloudProvider | None = None,
        gpu_type: GPUType | None = None,
        available_only: bool = False,
    ) -> list[GPUInstance]:
        """List GPU instances with optional filters."""
        instances = list(self._instances.values())

        if provider:
            instances = [i for i in instances if i.provider == provider]
        if gpu_type:
            instances = [i for i in instances if i.gpu_type == gpu_type]
        if available_only:
            instances = [i for i in instances if i.available]

        return instances

    def get_stats(self) -> dict[str, Any]:
        """Get tracker statistics."""
        return {
            "tracked_instances": len(self._instances),
            "tracked_reservations": len(self._reservations),
            "availability_records": len(self._availability),
            "inventory_summary": self.get_inventory_summary(),
        }


def create_gpu_tracker(
    aws_region: str = "us-east-1",
    gcp_project: str | None = None,
    azure_subscription: str | None = None,
    redis_client: Any = None,
) -> GPUTracker:
    """Factory function to create a GPU tracker."""
    return GPUTracker(
        aws_region=aws_region,
        gcp_project=gcp_project,
        azure_subscription=azure_subscription,
        redis_client=redis_client,
    )
