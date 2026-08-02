"""Resource Pool for Task Scheduling.

Manages resource allocation for tasks including:
- CPU cores
- Memory
- GPU access
- Network bandwidth
- Exclusive resource locks
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

log = logging.getLogger("devcopilot.coordinator.resource_pool")


@dataclass
class ResourceRequirements:
    """Resource requirements for a task."""
    cpu_cores: float = 1.0
    memory_mb: int = 512
    gpu: bool = False
    network_bandwidth_mbps: int = 100
    exclusive: bool = False


@dataclass
class ResourceAllocation:
    """An active resource allocation."""
    id: str
    requirements: ResourceRequirements
    task_id: str | None = None
    allocated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime | None = None

    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return datetime.now(timezone.utc) > self.expires_at


class ResourcePool:
    """Manages resource allocation for tasks.

    Features:
    - Thread-safe async resource acquisition
    - Automatic expiration of stale allocations
    - Resource usage tracking
    - Kubernetes integration for real resource metrics
    """

    def __init__(
        self,
        cpu_cores: int = 8,
        memory_mb: int = 16384,
        gpus: int = 0,
        network_bandwidth_mbps: int = 1000,
        k8s_client: Any = None,
    ):
        self._total = ResourceRequirements(
            cpu_cores=float(cpu_cores),
            memory_mb=memory_mb,
            gpu=gpus > 0,
            network_bandwidth_mbps=network_bandwidth_mbps,
        )
        self._available = ResourceRequirements(
            cpu_cores=float(cpu_cores),
            memory_mb=memory_mb,
            gpu=gpus > 0,
            network_bandwidth_mbps=network_bandwidth_mbps,
        )
        self._gpu_count = gpus
        self._available_gpus = gpus

        self._lock = asyncio.Lock()
        self._allocations: dict[str, ResourceAllocation] = {}
        self._exclusive_lock = asyncio.Lock()
        self._has_exclusive = False

        self._k8s = k8s_client
        self._metrics_cache: dict[str, Any] = {}
        self._cache_ttl = 30

    async def acquire(
        self,
        requirements: ResourceRequirements,
        task_id: str | None = None,
        timeout: float = 60,
        ttl_seconds: int | None = None,
    ) -> str | None:
        """Acquire resources, returns allocation ID or None on timeout."""
        deadline = datetime.now(timezone.utc) + timedelta(seconds=timeout)

        while datetime.now(timezone.utc) < deadline:
            await self._cleanup_expired()

            if requirements.exclusive:
                acquired = await self._try_acquire_exclusive(requirements, task_id, ttl_seconds)
            else:
                acquired = await self._try_acquire(requirements, task_id, ttl_seconds)

            if acquired:
                return acquired

            await asyncio.sleep(0.1)

        log.warning("Resource acquisition timed out for task %s", task_id)
        return None

    async def _try_acquire(
        self,
        requirements: ResourceRequirements,
        task_id: str | None,
        ttl_seconds: int | None,
    ) -> str | None:
        """Try to acquire non-exclusive resources."""
        async with self._lock:
            if self._has_exclusive:
                return None

            if not self._can_allocate(requirements):
                return None

            allocation_id = f"alloc_{uuid.uuid4().hex[:8]}"
            expires_at = None
            if ttl_seconds:
                expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)

            allocation = ResourceAllocation(
                id=allocation_id,
                requirements=requirements,
                task_id=task_id,
                expires_at=expires_at,
            )

            self._allocations[allocation_id] = allocation
            self._available.cpu_cores -= requirements.cpu_cores
            self._available.memory_mb -= requirements.memory_mb
            self._available.network_bandwidth_mbps -= requirements.network_bandwidth_mbps

            if requirements.gpu and self._available_gpus > 0:
                self._available_gpus -= 1

            log.debug(
                "Allocated resources %s for task %s: cpu=%.1f, memory=%dMB",
                allocation_id, task_id, requirements.cpu_cores, requirements.memory_mb,
            )

            return allocation_id

    async def _try_acquire_exclusive(
        self,
        requirements: ResourceRequirements,
        task_id: str | None,
        ttl_seconds: int | None,
    ) -> str | None:
        """Try to acquire exclusive resources."""
        try:
            await asyncio.wait_for(
                self._exclusive_lock.acquire(),
                timeout=0.01,
            )
        except asyncio.TimeoutError:
            return None

        try:
            async with self._lock:
                if self._allocations:
                    self._exclusive_lock.release()
                    return None

                if not self._can_allocate(requirements):
                    self._exclusive_lock.release()
                    return None

                self._has_exclusive = True

                allocation_id = f"excl_{uuid.uuid4().hex[:8]}"
                expires_at = None
                if ttl_seconds:
                    expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)

                allocation = ResourceAllocation(
                    id=allocation_id,
                    requirements=requirements,
                    task_id=task_id,
                    expires_at=expires_at,
                )

                self._allocations[allocation_id] = allocation
                self._available.cpu_cores -= requirements.cpu_cores
                self._available.memory_mb -= requirements.memory_mb

                log.info("Exclusive allocation %s for task %s", allocation_id, task_id)
                return allocation_id

        except Exception:
            self._exclusive_lock.release()
            raise

    async def release(self, allocation_id: str) -> bool:
        """Release allocated resources."""
        async with self._lock:
            allocation = self._allocations.pop(allocation_id, None)
            if not allocation:
                return False

            req = allocation.requirements
            self._available.cpu_cores += req.cpu_cores
            self._available.memory_mb += req.memory_mb
            self._available.network_bandwidth_mbps += req.network_bandwidth_mbps

            if req.gpu:
                self._available_gpus += 1

            if req.exclusive:
                self._has_exclusive = False
                try:
                    self._exclusive_lock.release()
                except RuntimeError:
                    pass

            log.debug("Released resources %s", allocation_id)
            return True

    def _can_allocate(self, requirements: ResourceRequirements) -> bool:
        """Check if resources are available."""
        if self._available.cpu_cores < requirements.cpu_cores:
            return False
        if self._available.memory_mb < requirements.memory_mb:
            return False
        if self._available.network_bandwidth_mbps < requirements.network_bandwidth_mbps:
            return False
        if requirements.gpu and self._available_gpus < 1:
            return False
        return True

    async def _cleanup_expired(self) -> None:
        """Clean up expired allocations."""
        async with self._lock:
            expired = [
                aid for aid, alloc in self._allocations.items()
                if alloc.is_expired()
            ]

            for allocation_id in expired:
                allocation = self._allocations.pop(allocation_id)
                req = allocation.requirements

                self._available.cpu_cores += req.cpu_cores
                self._available.memory_mb += req.memory_mb
                self._available.network_bandwidth_mbps += req.network_bandwidth_mbps

                if req.gpu:
                    self._available_gpus += 1

                if req.exclusive:
                    self._has_exclusive = False
                    try:
                        self._exclusive_lock.release()
                    except RuntimeError:
                        pass

                log.info("Expired allocation %s released", allocation_id)

    async def get_usage(self) -> dict[str, Any]:
        """Get current resource usage."""
        async with self._lock:
            return {
                "total": {
                    "cpu_cores": self._total.cpu_cores,
                    "memory_mb": self._total.memory_mb,
                    "gpus": self._gpu_count,
                    "network_bandwidth_mbps": self._total.network_bandwidth_mbps,
                },
                "available": {
                    "cpu_cores": self._available.cpu_cores,
                    "memory_mb": self._available.memory_mb,
                    "gpus": self._available_gpus,
                    "network_bandwidth_mbps": self._available.network_bandwidth_mbps,
                },
                "used": {
                    "cpu_cores": self._total.cpu_cores - self._available.cpu_cores,
                    "memory_mb": self._total.memory_mb - self._available.memory_mb,
                    "gpus": self._gpu_count - self._available_gpus,
                    "network_bandwidth_mbps": (
                        self._total.network_bandwidth_mbps -
                        self._available.network_bandwidth_mbps
                    ),
                },
                "allocations": len(self._allocations),
                "has_exclusive": self._has_exclusive,
            }

    async def get_allocation(self, allocation_id: str) -> ResourceAllocation | None:
        """Get details of an allocation."""
        return self._allocations.get(allocation_id)

    async def list_allocations(self) -> list[ResourceAllocation]:
        """List all active allocations."""
        return list(self._allocations.values())

    async def sync_from_kubernetes(self) -> bool:
        """Sync available resources from Kubernetes cluster."""
        if not self._k8s:
            return False

        try:
            nodes = await self._k8s.list_node()

            total_cpu = 0.0
            total_memory = 0
            total_gpus = 0

            for node in nodes.items:
                allocatable = node.status.allocatable
                if allocatable:
                    cpu_str = allocatable.get("cpu", "0")
                    if cpu_str.endswith("m"):
                        total_cpu += float(cpu_str[:-1]) / 1000
                    else:
                        total_cpu += float(cpu_str)

                    mem_str = allocatable.get("memory", "0")
                    if mem_str.endswith("Ki"):
                        total_memory += int(mem_str[:-2]) // 1024
                    elif mem_str.endswith("Mi"):
                        total_memory += int(mem_str[:-2])
                    elif mem_str.endswith("Gi"):
                        total_memory += int(mem_str[:-2]) * 1024

                    gpu_str = allocatable.get("nvidia.com/gpu", "0")
                    total_gpus += int(gpu_str)

            async with self._lock:
                allocated_cpu = self._total.cpu_cores - self._available.cpu_cores
                allocated_memory = self._total.memory_mb - self._available.memory_mb

                self._total.cpu_cores = total_cpu
                self._total.memory_mb = total_memory
                self._gpu_count = total_gpus

                self._available.cpu_cores = max(0, total_cpu - allocated_cpu)
                self._available.memory_mb = max(0, total_memory - allocated_memory)
                self._available_gpus = total_gpus

            log.info(
                "Synced resources from K8s: cpu=%.1f, memory=%dMB, gpus=%d",
                total_cpu, total_memory, total_gpus,
            )
            return True

        except Exception as e:
            log.error("Failed to sync from Kubernetes: %s", e)
            return False


class ResourcePredictor:
    """Predicts resource requirements based on historical data."""

    def __init__(self, redis_client: Any = None):
        self.redis = redis_client
        self._history: dict[str, list[dict[str, Any]]] = {}

    async def record_usage(
        self,
        task_type: str,
        actual_usage: dict[str, float],
        duration_seconds: float,
    ) -> None:
        """Record actual resource usage for a task type."""
        entry = {
            "usage": actual_usage,
            "duration": duration_seconds,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        if task_type not in self._history:
            self._history[task_type] = []

        self._history[task_type].append(entry)

        if len(self._history[task_type]) > 100:
            self._history[task_type] = self._history[task_type][-100:]

        if self.redis:
            key = f"resource_history:{task_type}"
            await self.redis.lpush(key, str(entry))
            await self.redis.ltrim(key, 0, 99)

    async def predict_requirements(
        self,
        task_type: str,
        safety_margin: float = 1.2,
    ) -> ResourceRequirements:
        """Predict resource requirements for a task type."""
        history = self._history.get(task_type, [])

        if not history:
            return ResourceRequirements()

        avg_cpu = sum(h["usage"].get("cpu", 1.0) for h in history) / len(history)
        avg_memory = sum(h["usage"].get("memory", 512) for h in history) / len(history)
        max_cpu = max(h["usage"].get("cpu", 1.0) for h in history)
        max_memory = max(h["usage"].get("memory", 512) for h in history)

        predicted_cpu = (avg_cpu + max_cpu) / 2 * safety_margin
        predicted_memory = int((avg_memory + max_memory) / 2 * safety_margin)

        return ResourceRequirements(
            cpu_cores=min(predicted_cpu, 8.0),
            memory_mb=min(predicted_memory, 16384),
        )
