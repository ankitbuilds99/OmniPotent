"""Playbook Catalog.

Manages the catalog of available playbooks.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("omnipotent.playbook_marketplace.catalog")


class PlaybookCategory(str, Enum):
    KUBERNETES = "kubernetes"
    DATABASE = "database"
    AWS = "aws"
    ALERTING = "alerting"
    SECURITY = "security"
    NETWORKING = "networking"
    CI_CD = "ci_cd"
    CACHE = "cache"
    MESSAGING = "messaging"
    GENERAL = "general"


class PlaybookStatus(str, Enum):
    DRAFT = "draft"
    PENDING_REVIEW = "pending_review"
    PUBLISHED = "published"
    DEPRECATED = "deprecated"


@dataclass
class PlaybookStep:
    """A step in a playbook."""

    step_id: str
    title: str
    description: str
    action_type: str
    action_config: dict[str, Any]
    timeout_seconds: int = 300
    continue_on_error: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.step_id,
            "title": self.title,
            "description": self.description,
            "action_type": self.action_type,
            "config": self.action_config,
            "timeout_seconds": self.timeout_seconds,
            "continue_on_error": self.continue_on_error,
        }


@dataclass
class Playbook:
    """A runbook playbook."""

    playbook_id: str
    name: str
    description: str
    category: PlaybookCategory
    author: str
    version: str
    steps: list[PlaybookStep] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    required_integrations: list[str] = field(default_factory=list)
    variables: list[dict[str, str]] = field(default_factory=list)
    status: PlaybookStatus = PlaybookStatus.DRAFT
    is_verified: bool = False
    is_premium: bool = False
    price_monthly: float = 0.0
    installs: int = 0
    avg_rating: float = 0.0
    total_reviews: int = 0
    total_runs: int = 0
    success_rate: float = 0.0
    avg_run_time_minutes: float = 0.0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.playbook_id,
            "name": self.name,
            "description": self.description,
            "category": self.category.value,
            "author": self.author,
            "version": self.version,
            "steps": [s.to_dict() for s in self.steps],
            "tags": self.tags,
            "required_integrations": self.required_integrations,
            "variables": self.variables,
            "status": self.status.value,
            "verified": self.is_verified,
            "premium": self.is_premium,
            "price": self.price_monthly,
            "installs": self.installs,
            "rating": round(self.avg_rating, 1),
            "reviews": self.total_reviews,
            "runs": self.total_runs,
            "success_rate": round(self.success_rate, 1),
            "avg_time_minutes": round(self.avg_run_time_minutes, 1),
            "created_at": self.created_at.isoformat(),
        }


class PlaybookCatalog:
    """Manages the playbook catalog."""

    def __init__(self) -> None:
        self._playbooks: dict[str, Playbook] = {}
        self._user_installs: dict[str, list[str]] = {}
        self._init_featured_playbooks()

    def _init_featured_playbooks(self) -> None:
        """Initialize featured playbooks."""
        self._playbooks["k8s-oom-recovery"] = Playbook(
            playbook_id="k8s-oom-recovery",
            name="Kubernetes Pod OOMKilled Recovery",
            description=(
                "Automatically diagnose and recover from OOMKilled pods. "
                "Checks memory trends, recent deploys, and scales if needed."
            ),
            category=PlaybookCategory.KUBERNETES,
            author="@kubernetes-experts",
            version="2.3.1",
            steps=[
                PlaybookStep(
                    "check-pod-status",
                    "Check Pod Status",
                    "Get current pod status and OOM events",
                    "kubernetes_query",
                    {"query": "kubectl get pods -o wide"},
                ),
                PlaybookStep(
                    "analyze-memory",
                    "Analyze Memory Usage",
                    "Check historical memory usage trends",
                    "metrics_query",
                    {"metric": "container_memory_usage_bytes"},
                ),
                PlaybookStep(
                    "check-recent-deploys",
                    "Check Recent Deployments",
                    "Look for recent deployments that may have caused the issue",
                    "kubernetes_query",
                    {"query": "kubectl rollout history"},
                ),
                PlaybookStep(
                    "scale-if-needed",
                    "Scale Deployment",
                    "Increase replicas if current load requires it",
                    "kubernetes_action",
                    {"action": "scale", "replicas": "+1"},
                    continue_on_error=True,
                ),
            ],
            tags=["kubernetes", "memory", "oom", "auto-recovery"],
            required_integrations=["kubernetes", "datadog"],
            variables=[
                {"name": "NAMESPACE", "type": "string", "default": "default"},
                {"name": "DEPLOYMENT", "type": "string"},
            ],
            status=PlaybookStatus.PUBLISHED,
            is_verified=True,
            installs=2341,
            avg_rating=4.9,
            total_reviews=127,
            total_runs=8923,
            success_rate=98.2,
            avg_run_time_minutes=4.2,
        )

        self._playbooks["postgres-connection-pool"] = Playbook(
            playbook_id="postgres-connection-pool",
            name="PostgreSQL Connection Pool Exhaustion",
            description=(
                "Diagnose and resolve PostgreSQL connection pool exhaustion. "
                "Identifies leaking connections and optimizes pool settings."
            ),
            category=PlaybookCategory.DATABASE,
            author="@db-reliability",
            version="1.8.0",
            steps=[
                PlaybookStep(
                    "check-connections",
                    "Check Active Connections",
                    "Query active connections and their state",
                    "database_query",
                    {"query": "SELECT * FROM pg_stat_activity"},
                ),
                PlaybookStep(
                    "identify-leaks",
                    "Identify Connection Leaks",
                    "Find long-running idle connections",
                    "database_query",
                    {"query": "SELECT * FROM pg_stat_activity WHERE state = 'idle'"},
                ),
                PlaybookStep(
                    "terminate-idle",
                    "Terminate Idle Connections",
                    "Kill connections idle for more than 10 minutes",
                    "database_action",
                    {"action": "terminate_idle", "idle_minutes": 10},
                ),
            ],
            tags=["postgresql", "database", "connections", "pool"],
            required_integrations=["postgresql"],
            variables=[
                {"name": "DATABASE_HOST", "type": "string"},
                {"name": "MAX_IDLE_MINUTES", "type": "number", "default": "10"},
            ],
            status=PlaybookStatus.PUBLISHED,
            is_verified=True,
            installs=1567,
            avg_rating=4.7,
            total_reviews=89,
            total_runs=4521,
            success_rate=94.5,
            avg_run_time_minutes=3.8,
        )

        self._playbooks["aws-rds-failover"] = Playbook(
            playbook_id="aws-rds-failover",
            name="AWS RDS Automated Failover",
            description=(
                "Enterprise-grade RDS failover with zero-downtime DNS updates, "
                "connection draining, and automated verification."
            ),
            category=PlaybookCategory.AWS,
            author="@aws-heroes",
            version="3.0.0",
            steps=[
                PlaybookStep(
                    "pre-checks",
                    "Pre-Failover Checks",
                    "Verify replica is in sync and ready",
                    "aws_rds_query",
                    {"action": "check_replica_status"},
                ),
                PlaybookStep(
                    "drain-connections",
                    "Drain Connections",
                    "Gracefully drain existing connections",
                    "aws_rds_action",
                    {"action": "drain_connections", "timeout": 60},
                ),
                PlaybookStep(
                    "failover",
                    "Execute Failover",
                    "Promote replica to primary",
                    "aws_rds_action",
                    {"action": "failover"},
                ),
                PlaybookStep(
                    "update-dns",
                    "Update DNS",
                    "Update Route53 records",
                    "aws_route53_action",
                    {"action": "update_record"},
                ),
                PlaybookStep(
                    "verify",
                    "Verify Failover",
                    "Confirm new primary is operational",
                    "aws_rds_query",
                    {"action": "verify_primary"},
                ),
            ],
            tags=["aws", "rds", "failover", "database", "enterprise"],
            required_integrations=["aws"],
            status=PlaybookStatus.PUBLISHED,
            is_verified=True,
            is_premium=True,
            price_monthly=29.0,
            installs=456,
            avg_rating=5.0,
            total_reviews=34,
            total_runs=892,
            success_rate=99.8,
            avg_run_time_minutes=2.5,
        )

    def get_playbook(self, playbook_id: str) -> Playbook | None:
        """Get a playbook by ID."""
        return self._playbooks.get(playbook_id)

    def search_playbooks(
        self,
        query: str = "",
        category: PlaybookCategory | None = None,
        tags: list[str] | None = None,
        verified_only: bool = False,
        free_only: bool = False,
    ) -> list[Playbook]:
        """Search playbooks."""
        results = []
        for playbook in self._playbooks.values():
            if playbook.status != PlaybookStatus.PUBLISHED:
                continue

            if query and query.lower() not in playbook.name.lower():
                if query.lower() not in playbook.description.lower():
                    continue

            if category and playbook.category != category:
                continue

            if tags:
                if not any(t in playbook.tags for t in tags):
                    continue

            if verified_only and not playbook.is_verified:
                continue

            if free_only and playbook.is_premium:
                continue

            results.append(playbook)

        results.sort(key=lambda p: p.installs, reverse=True)
        return results

    def get_featured(self, limit: int = 6) -> list[Playbook]:
        """Get featured playbooks."""
        playbooks = [
            p for p in self._playbooks.values()
            if p.status == PlaybookStatus.PUBLISHED and p.is_verified
        ]
        playbooks.sort(key=lambda p: (p.avg_rating, p.installs), reverse=True)
        return playbooks[:limit]

    def get_recommended(
        self,
        org_id: str,
        stack: list[str],
        limit: int = 5,
    ) -> list[Playbook]:
        """Get recommended playbooks based on user's stack."""
        installed = self._user_installs.get(org_id, [])

        results = []
        for playbook in self._playbooks.values():
            if playbook.status != PlaybookStatus.PUBLISHED:
                continue

            if playbook.playbook_id in installed:
                continue

            score: float = 0
            for integration in playbook.required_integrations:
                if integration in stack:
                    score += 10

            for tag in playbook.tags:
                if tag in stack:
                    score += 5

            score += playbook.avg_rating
            score += min(float(playbook.installs) / 1000, 5)

            if score > 0:
                results.append((playbook, score))

        results.sort(key=lambda x: x[1], reverse=True)
        return [p for p, _ in results[:limit]]

    def install_playbook(
        self,
        org_id: str,
        playbook_id: str,
    ) -> dict[str, Any]:
        """Install a playbook for an org."""
        playbook = self._playbooks.get(playbook_id)
        if not playbook:
            return {"error": "Playbook not found"}

        if playbook.status != PlaybookStatus.PUBLISHED:
            return {"error": "Playbook not available"}

        if org_id not in self._user_installs:
            self._user_installs[org_id] = []

        if playbook_id not in self._user_installs[org_id]:
            self._user_installs[org_id].append(playbook_id)
            playbook.installs += 1

        log.info("Installed playbook: org=%s playbook=%s", org_id, playbook_id)

        return {
            "success": True,
            "playbook": playbook.to_dict(),
        }

    def get_installed(self, org_id: str) -> list[Playbook]:
        """Get installed playbooks for an org."""
        installed_ids = self._user_installs.get(org_id, [])
        return [
            self._playbooks[pid]
            for pid in installed_ids
            if pid in self._playbooks
        ]

    def record_run(
        self,
        playbook_id: str,
        success: bool,
        duration_minutes: float,
    ) -> None:
        """Record a playbook run."""
        playbook = self._playbooks.get(playbook_id)
        if not playbook:
            return

        playbook.total_runs += 1

        old_total = playbook.total_runs - 1
        if old_total > 0:
            old_success_count = playbook.success_rate * old_total / 100
            new_success_count = old_success_count + (1 if success else 0)
            playbook.success_rate = (new_success_count / playbook.total_runs) * 100

            old_time_sum = playbook.avg_run_time_minutes * old_total
            playbook.avg_run_time_minutes = (old_time_sum + duration_minutes) / playbook.total_runs
        else:
            playbook.success_rate = 100 if success else 0
            playbook.avg_run_time_minutes = duration_minutes

    def create_playbook(
        self,
        name: str,
        description: str,
        category: PlaybookCategory,
        author: str,
        steps: list[PlaybookStep],
    ) -> Playbook:
        """Create a new playbook."""
        playbook_id = name.lower().replace(" ", "-")

        playbook = Playbook(
            playbook_id=playbook_id,
            name=name,
            description=description,
            category=category,
            author=author,
            version="1.0.0",
            steps=steps,
            status=PlaybookStatus.DRAFT,
        )

        self._playbooks[playbook_id] = playbook

        log.info("Created playbook: id=%s author=%s", playbook_id, author)

        return playbook


_catalog: PlaybookCatalog | None = None


def get_playbook_catalog() -> PlaybookCatalog:
    """Get the default playbook catalog."""
    global _catalog
    if _catalog is None:
        _catalog = PlaybookCatalog()
    return _catalog
