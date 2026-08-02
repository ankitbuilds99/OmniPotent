"""Continuous Sync Engine.

Real-time synchronization from multiple sources:
- GitHub (commits, PRs, deployments)
- CloudTrail (AWS API events)
- Prometheus (metrics and alerts)
- Kubernetes (resources and events)
- Jaeger (distributed traces)
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Awaitable
import json

from app.knowledge_graph.neo4j_backend import (
    Neo4jKnowledgeGraph,
    ServiceNode,
    DeploymentNode,
    ChangeNode,
    AlertNode,
    Relationship,
    RelationType,
)

log = logging.getLogger("omnipotent.knowledge_graph.sync")


class SyncSource(str, Enum):
    GITHUB = "github"
    CLOUDTRAIL = "cloudtrail"
    PROMETHEUS = "prometheus"
    KUBERNETES = "kubernetes"
    JAEGER = "jaeger"
    DATADOG = "datadog"
    ARGOCD = "argocd"
    TERRAFORM = "terraform"


@dataclass
class SyncState:
    """State of a sync source."""
    source: SyncSource
    last_sync: datetime | None = None
    cursor: str = ""
    items_synced: int = 0
    errors: int = 0
    status: str = "idle"

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source.value,
            "last_sync": self.last_sync.isoformat() if self.last_sync else None,
            "cursor": self.cursor,
            "items_synced": self.items_synced,
            "errors": self.errors,
            "status": self.status,
        }


@dataclass
class SyncEvent:
    """Event from a sync source."""
    source: SyncSource
    event_type: str
    event_id: str
    timestamp: datetime
    data: dict[str, Any]
    raw: dict[str, Any] = field(default_factory=dict)


class BaseSyncer(ABC):
    """Base class for source syncers."""

    def __init__(self, graph: Neo4jKnowledgeGraph):
        self.graph = graph
        self.state = SyncState(source=self.source)
        self._running = False
        self._task: asyncio.Task[None] | None = None

    @property
    @abstractmethod
    def source(self) -> SyncSource:
        """The sync source type."""
        pass

    @abstractmethod
    async def fetch_events(
        self,
        since: datetime | None = None,
        cursor: str | None = None,
    ) -> tuple[list[SyncEvent], str]:
        """Fetch events from the source.

        Returns (events, new_cursor).
        """
        pass

    @abstractmethod
    async def process_event(self, event: SyncEvent) -> None:
        """Process a single event and update the graph."""
        pass

    async def sync_once(self) -> int:
        """Run one sync cycle."""
        self.state.status = "syncing"

        try:
            events, new_cursor = await self.fetch_events(
                since=self.state.last_sync,
                cursor=self.state.cursor if self.state.cursor else None,
            )

            processed = 0
            for event in events:
                try:
                    await self.process_event(event)
                    processed += 1
                except Exception as e:
                    log.error("Failed to process %s event %s: %s", self.source.value, event.event_id, e)
                    self.state.errors += 1

            self.state.cursor = new_cursor
            self.state.last_sync = datetime.now(timezone.utc)
            self.state.items_synced += processed
            self.state.status = "idle"

            return processed

        except Exception as e:
            log.error("Sync failed for %s: %s", self.source.value, e)
            self.state.status = "error"
            self.state.errors += 1
            return 0

    async def start_continuous(self, interval_seconds: int = 60) -> None:
        """Start continuous sync."""
        self._running = True
        self._task = asyncio.create_task(self._sync_loop(interval_seconds))

    async def stop(self) -> None:
        """Stop continuous sync."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _sync_loop(self, interval: int) -> None:
        """Continuous sync loop."""
        while self._running:
            try:
                await self.sync_once()
            except Exception as e:
                log.error("Sync loop error for %s: %s", self.source.value, e)
            await asyncio.sleep(interval)


class GitHubSyncer(BaseSyncer):
    """Sync from GitHub."""

    def __init__(
        self,
        graph: Neo4jKnowledgeGraph,
        token: str,
        owner: str,
        repos: list[str],
    ):
        super().__init__(graph)
        self.token = token
        self.owner = owner
        self.repos = repos
        self._client: Any = None

    @property
    def source(self) -> SyncSource:
        return SyncSource.GITHUB

    async def _get_client(self) -> Any:
        """Get or create HTTP client."""
        if self._client is None:
            try:
                import httpx
                self._client = httpx.AsyncClient(
                    base_url="https://api.github.com",
                    headers={
                        "Authorization": f"Bearer {self.token}",
                        "Accept": "application/vnd.github.v3+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                    timeout=30.0,
                )
            except ImportError:
                log.warning("httpx not installed, GitHub sync unavailable")
                return None
        return self._client

    async def fetch_events(
        self,
        since: datetime | None = None,
        cursor: str | None = None,
    ) -> tuple[list[SyncEvent], str]:
        """Fetch GitHub events (commits, PRs, deployments)."""
        client = await self._get_client()
        if not client:
            return [], ""

        events: list[SyncEvent] = []
        new_cursor = cursor or ""

        for repo in self.repos:
            try:
                events.extend(await self._fetch_commits(client, repo, since))
                events.extend(await self._fetch_pull_requests(client, repo, since))
                events.extend(await self._fetch_deployments(client, repo, since))
            except Exception as e:
                log.error("Failed to fetch from %s/%s: %s", self.owner, repo, e)

        events.sort(key=lambda e: e.timestamp)
        if events:
            new_cursor = events[-1].event_id

        return events, new_cursor

    async def _fetch_commits(
        self,
        client: Any,
        repo: str,
        since: datetime | None,
    ) -> list[SyncEvent]:
        """Fetch recent commits."""
        params: dict[str, Any] = {"per_page": 50}
        if since:
            params["since"] = since.isoformat()

        response = await client.get(f"/repos/{self.owner}/{repo}/commits", params=params)
        if response.status_code != 200:
            return []

        events = []
        for commit in response.json():
            timestamp = datetime.fromisoformat(
                commit["commit"]["committer"]["date"].replace("Z", "+00:00")
            )
            events.append(SyncEvent(
                source=SyncSource.GITHUB,
                event_type="commit",
                event_id=f"commit:{commit['sha']}",
                timestamp=timestamp,
                data={
                    "sha": commit["sha"],
                    "message": commit["commit"]["message"],
                    "author": commit["commit"]["author"]["name"],
                    "author_email": commit["commit"]["author"]["email"],
                    "repo": repo,
                    "url": commit["html_url"],
                    "files_changed": [],
                },
                raw=commit,
            ))

        return events

    async def _fetch_pull_requests(
        self,
        client: Any,
        repo: str,
        since: datetime | None,
    ) -> list[SyncEvent]:
        """Fetch merged pull requests."""
        params = {
            "state": "closed",
            "sort": "updated",
            "direction": "desc",
            "per_page": 30,
        }

        response = await client.get(f"/repos/{self.owner}/{repo}/pulls", params=params)
        if response.status_code != 200:
            return []

        events = []
        for pr in response.json():
            if not pr.get("merged_at"):
                continue

            merged_at = datetime.fromisoformat(pr["merged_at"].replace("Z", "+00:00"))
            if since and merged_at < since:
                continue

            events.append(SyncEvent(
                source=SyncSource.GITHUB,
                event_type="pull_request",
                event_id=f"pr:{repo}:{pr['number']}",
                timestamp=merged_at,
                data={
                    "number": pr["number"],
                    "title": pr["title"],
                    "body": pr.get("body", ""),
                    "author": pr["user"]["login"],
                    "repo": repo,
                    "base_branch": pr["base"]["ref"],
                    "head_branch": pr["head"]["ref"],
                    "merge_commit_sha": pr.get("merge_commit_sha", ""),
                    "url": pr["html_url"],
                    "labels": [lbl["name"] for lbl in pr.get("labels", [])],
                    "additions": pr.get("additions", 0),
                    "deletions": pr.get("deletions", 0),
                    "changed_files": pr.get("changed_files", 0),
                },
                raw=pr,
            ))

        return events

    async def _fetch_deployments(
        self,
        client: Any,
        repo: str,
        since: datetime | None,
    ) -> list[SyncEvent]:
        """Fetch deployments."""
        response = await client.get(
            f"/repos/{self.owner}/{repo}/deployments",
            params={"per_page": 30},
        )
        if response.status_code != 200:
            return []

        events = []
        for deployment in response.json():
            created_at = datetime.fromisoformat(
                deployment["created_at"].replace("Z", "+00:00")
            )
            if since and created_at < since:
                continue

            status_response = await client.get(
                f"/repos/{self.owner}/{repo}/deployments/{deployment['id']}/statuses",
                params={"per_page": 1},
            )
            status = "pending"
            if status_response.status_code == 200:
                statuses = status_response.json()
                if statuses:
                    status = statuses[0]["state"]

            events.append(SyncEvent(
                source=SyncSource.GITHUB,
                event_type="deployment",
                event_id=f"deploy:{repo}:{deployment['id']}",
                timestamp=created_at,
                data={
                    "deployment_id": deployment["id"],
                    "environment": deployment["environment"],
                    "ref": deployment.get("ref", ""),
                    "sha": deployment.get("sha", ""),
                    "creator": deployment["creator"]["login"] if deployment.get("creator") else "",
                    "repo": repo,
                    "status": status,
                    "description": deployment.get("description", ""),
                },
                raw=deployment,
            ))

        return events

    async def process_event(self, event: SyncEvent) -> None:
        """Process GitHub event."""
        if event.event_type == "commit":
            await self._process_commit(event)
        elif event.event_type == "pull_request":
            await self._process_pr(event)
        elif event.event_type == "deployment":
            await self._process_deployment(event)

    async def _process_commit(self, event: SyncEvent) -> None:
        """Process commit event."""
        data = event.data
        change = ChangeNode(
            id=f"github:commit:{data['sha'][:12]}",
            change_type="code",
            title=data["message"].split("\n")[0][:100],
            description=data["message"],
            author=data["author"],
            timestamp=event.timestamp,
            source="github",
            affected_resources=[data["repo"]],
            risk_score=self._calculate_commit_risk(data),
            diff_url=data["url"],
        )
        await self.graph.create_change(change)

    async def _process_pr(self, event: SyncEvent) -> None:
        """Process pull request event."""
        data = event.data

        risk_labels = ["breaking-change", "high-risk", "critical"]
        has_risk_label = any(label in data["labels"] for label in risk_labels)
        risk_score = 0.3
        if has_risk_label:
            risk_score = 0.8
        elif data["changed_files"] > 20:
            risk_score = 0.6
        elif data["additions"] + data["deletions"] > 500:
            risk_score = 0.5

        change = ChangeNode(
            id=f"github:pr:{data['repo']}:{data['number']}",
            change_type="code",
            title=f"PR #{data['number']}: {data['title'][:80]}",
            description=data.get("body", ""),
            author=data["author"],
            timestamp=event.timestamp,
            source="github",
            affected_resources=[data["repo"]],
            risk_score=risk_score,
            diff_url=data["url"],
        )
        await self.graph.create_change(change)

    async def _process_deployment(self, event: SyncEvent) -> None:
        """Process deployment event."""
        data = event.data

        service_id = data["repo"].replace("-", "_")

        await self.graph.create_service(ServiceNode(
            id=service_id,
            name=data["repo"],
            repository=f"{self.owner}/{data['repo']}",
        ))

        deployment = DeploymentNode(
            id=f"github:deploy:{data['deployment_id']}",
            service_id=service_id,
            version=data.get("ref", data.get("sha", "")[:12]),
            commit_sha=data.get("sha", ""),
            deployed_by=data["creator"],
            deployed_at=event.timestamp,
            environment=data["environment"],
            status=data["status"],
        )
        await self.graph.create_deployment(deployment)

    def _calculate_commit_risk(self, data: dict[str, Any]) -> float:
        """Calculate risk score for a commit."""
        risk = 0.2
        message = data["message"].lower()

        if any(w in message for w in ["fix", "bug", "patch"]):
            risk += 0.1
        if any(w in message for w in ["breaking", "migration", "schema"]):
            risk += 0.3
        if any(w in message for w in ["revert", "rollback"]):
            risk += 0.2
        if any(w in message for w in ["config", "env", "secret"]):
            risk += 0.2

        return min(1.0, risk)


class CloudTrailSyncer(BaseSyncer):
    """Sync from AWS CloudTrail."""

    def __init__(
        self,
        graph: Neo4jKnowledgeGraph,
        region: str = "us-east-1",
        trail_name: str | None = None,
    ):
        super().__init__(graph)
        self.region = region
        self.trail_name = trail_name
        self._client: Any = None

    @property
    def source(self) -> SyncSource:
        return SyncSource.CLOUDTRAIL

    async def _get_client(self) -> Any:
        """Get boto3 CloudTrail client."""
        if self._client is None:
            try:
                import boto3
                self._client = boto3.client("cloudtrail", region_name=self.region)
            except ImportError:
                log.warning("boto3 not installed, CloudTrail sync unavailable")
                return None
            except Exception as e:
                log.warning("Failed to create CloudTrail client: %s", e)
                return None
        return self._client

    async def fetch_events(
        self,
        since: datetime | None = None,
        cursor: str | None = None,
    ) -> tuple[list[SyncEvent], str]:
        """Fetch CloudTrail events."""
        client = await self._get_client()
        if not client:
            return [], ""

        events: list[SyncEvent] = []
        new_cursor = ""

        try:
            lookup_params: dict[str, Any] = {"MaxResults": 50}

            if since:
                lookup_params["StartTime"] = since
                lookup_params["EndTime"] = datetime.now(timezone.utc)

            if cursor:
                lookup_params["NextToken"] = cursor

            high_impact_events = [
                "RunInstances", "TerminateInstances",
                "CreateDBInstance", "DeleteDBInstance",
                "UpdateFunctionCode", "UpdateFunctionConfiguration",
                "CreateLoadBalancer", "DeleteLoadBalancer",
                "PutScalingPolicy", "DeleteScalingPolicy",
                "CreateSecurityGroup", "AuthorizeSecurityGroupIngress",
                "PutBucketPolicy", "DeleteBucketPolicy",
                "CreateRole", "DeleteRole", "AttachRolePolicy",
            ]

            lookup_params["LookupAttributes"] = []

            response = await asyncio.to_thread(
                client.lookup_events,
                **lookup_params,
            )

            for event in response.get("Events", []):
                event_name = event.get("EventName", "")

                if event_name not in high_impact_events:
                    continue

                timestamp = event.get("EventTime", datetime.now(timezone.utc))
                if isinstance(timestamp, str):
                    timestamp = datetime.fromisoformat(timestamp)

                cloud_event = event.get("CloudTrailEvent", "{}")
                if isinstance(cloud_event, str):
                    try:
                        cloud_event = json.loads(cloud_event)
                    except json.JSONDecodeError:
                        cloud_event = {}

                events.append(SyncEvent(
                    source=SyncSource.CLOUDTRAIL,
                    event_type=event_name,
                    event_id=event.get("EventId", ""),
                    timestamp=timestamp,
                    data={
                        "event_name": event_name,
                        "event_source": event.get("EventSource", ""),
                        "username": event.get("Username", ""),
                        "resources": [
                            {"type": r.get("ResourceType", ""), "name": r.get("ResourceName", "")}
                            for r in event.get("Resources", [])
                        ],
                        "region": cloud_event.get("awsRegion", self.region),
                        "source_ip": cloud_event.get("sourceIPAddress", ""),
                        "user_agent": cloud_event.get("userAgent", ""),
                        "error_code": cloud_event.get("errorCode"),
                        "error_message": cloud_event.get("errorMessage"),
                    },
                    raw=event,
                ))

            new_cursor = response.get("NextToken", "")

        except Exception as e:
            log.error("CloudTrail fetch error: %s", e)

        return events, new_cursor

    async def process_event(self, event: SyncEvent) -> None:
        """Process CloudTrail event."""
        data = event.data

        change_type = "infrastructure"
        if "Lambda" in data.get("event_source", ""):
            change_type = "code"
        elif "RDS" in data.get("event_source", ""):
            change_type = "database"
        elif "IAM" in data.get("event_source", ""):
            change_type = "config"

        resources = data.get("resources", [])
        affected = [f"{r['type']}:{r['name']}" for r in resources if r.get("name")]

        risk_score = self._calculate_risk(data)

        change = ChangeNode(
            id=f"cloudtrail:{event.event_id}",
            change_type=change_type,
            title=f"{data['event_name']} by {data.get('username', 'unknown')}",
            description=f"AWS API call: {data['event_name']}",
            author=data.get("username", ""),
            timestamp=event.timestamp,
            source="cloudtrail",
            affected_resources=affected,
            risk_score=risk_score,
        )

        if data.get("error_code"):
            change.description += f"\nError: {data['error_code']}: {data.get('error_message', '')}"

        await self.graph.create_change(change)

    def _calculate_risk(self, data: dict[str, Any]) -> float:
        """Calculate risk score for CloudTrail event."""
        event_name = data.get("event_name", "")

        high_risk = ["Delete", "Terminate", "Remove", "Detach"]
        medium_risk = ["Create", "Update", "Modify", "Put", "Attach"]

        risk = 0.3

        for keyword in high_risk:
            if keyword in event_name:
                risk = 0.8
                break

        for keyword in medium_risk:
            if keyword in event_name:
                risk = max(risk, 0.5)
                break

        if "IAM" in data.get("event_source", ""):
            risk += 0.2
        if "Security" in event_name:
            risk += 0.2

        if data.get("error_code"):
            risk += 0.1

        return min(1.0, risk)


class PrometheusSyncer(BaseSyncer):
    """Sync from Prometheus/Alertmanager."""

    def __init__(
        self,
        graph: Neo4jKnowledgeGraph,
        prometheus_url: str = "http://localhost:9090",
        alertmanager_url: str = "http://localhost:9093",
    ):
        super().__init__(graph)
        self.prometheus_url = prometheus_url.rstrip("/")
        self.alertmanager_url = alertmanager_url.rstrip("/")
        self._client: Any = None

    @property
    def source(self) -> SyncSource:
        return SyncSource.PROMETHEUS

    async def _get_client(self) -> Any:
        """Get HTTP client."""
        if self._client is None:
            try:
                import httpx
                self._client = httpx.AsyncClient(timeout=30.0)
            except ImportError:
                log.warning("httpx not installed, Prometheus sync unavailable")
                return None
        return self._client

    async def fetch_events(
        self,
        since: datetime | None = None,
        cursor: str | None = None,
    ) -> tuple[list[SyncEvent], str]:
        """Fetch alerts from Alertmanager."""
        client = await self._get_client()
        if not client:
            return [], ""

        events: list[SyncEvent] = []

        try:
            response = await client.get(f"{self.alertmanager_url}/api/v2/alerts")

            if response.status_code == 200:
                alerts = response.json()

                for alert in alerts:
                    labels = alert.get("labels", {})
                    annotations = alert.get("annotations", {})

                    starts_at = alert.get("startsAt", "")
                    if starts_at:
                        timestamp = datetime.fromisoformat(starts_at.replace("Z", "+00:00"))
                    else:
                        timestamp = datetime.now(timezone.utc)

                    if since and timestamp < since:
                        continue

                    fingerprint = alert.get("fingerprint", "")
                    if not fingerprint:
                        fingerprint = hashlib.md5(
                            json.dumps(labels, sort_keys=True).encode()
                        ).hexdigest()[:16]

                    events.append(SyncEvent(
                        source=SyncSource.PROMETHEUS,
                        event_type="alert",
                        event_id=f"alert:{fingerprint}",
                        timestamp=timestamp,
                        data={
                            "alertname": labels.get("alertname", "unknown"),
                            "severity": labels.get("severity", "warning"),
                            "service": labels.get("service", labels.get("job", "")),
                            "namespace": labels.get("namespace", ""),
                            "instance": labels.get("instance", ""),
                            "labels": labels,
                            "annotations": annotations,
                            "status": alert.get("status", {}).get("state", "active"),
                            "fingerprint": fingerprint,
                            "ends_at": alert.get("endsAt"),
                            "generator_url": alert.get("generatorURL", ""),
                        },
                        raw=alert,
                    ))

            targets_response = await client.get(f"{self.prometheus_url}/api/v1/targets")
            if targets_response.status_code == 200:
                targets_data = targets_response.json()
                for target in targets_data.get("data", {}).get("activeTargets", []):
                    labels = target.get("labels", {})
                    service_name = labels.get("job", labels.get("service", ""))

                    if service_name:
                        service = ServiceNode(
                            id=service_name.replace("-", "_").replace(".", "_"),
                            name=service_name,
                            namespace=labels.get("namespace", "default"),
                            health_status="healthy" if target.get("health") == "up" else "unhealthy",
                        )
                        await self.graph.create_service(service)

        except Exception as e:
            log.error("Prometheus fetch error: %s", e)

        return events, ""

    async def process_event(self, event: SyncEvent) -> None:
        """Process Prometheus alert."""
        data = event.data

        resolved_at = None
        if data.get("ends_at"):
            try:
                resolved_at = datetime.fromisoformat(
                    data["ends_at"].replace("Z", "+00:00")
                )
            except (ValueError, TypeError):
                pass

        alert = AlertNode(
            id=f"prometheus:{data['fingerprint']}",
            name=data["alertname"],
            severity=data["severity"],
            source="prometheus",
            service=data.get("service", ""),
            fired_at=event.timestamp,
            resolved_at=resolved_at,
            fingerprint=data["fingerprint"],
            labels=data.get("labels", {}),
            annotations=data.get("annotations", {}),
        )
        await self.graph.create_alert(alert)

        if alert.service:
            service = await self.graph.get_service(alert.service)
            if service:
                if alert.severity in ("critical", "error") and not resolved_at:
                    service.health_status = "unhealthy"
                    await self.graph.create_service(service)


class KubernetesSyncer(BaseSyncer):
    """Sync from Kubernetes cluster."""

    def __init__(
        self,
        graph: Neo4jKnowledgeGraph,
        kubeconfig: str | None = None,
        context: str | None = None,
        namespace: str | None = None,
    ):
        super().__init__(graph)
        self.kubeconfig = kubeconfig
        self.context = context
        self.namespace = namespace
        self._api: Any = None
        self._apps_api: Any = None

    @property
    def source(self) -> SyncSource:
        return SyncSource.KUBERNETES

    async def _init_client(self) -> bool:
        """Initialize Kubernetes client."""
        if self._api is not None:
            return True

        try:
            from kubernetes import config
            from kubernetes.client import CoreV1Api, AppsV1Api

            if self.kubeconfig:
                config.load_kube_config(
                    config_file=self.kubeconfig,
                    context=self.context,
                )
            else:
                try:
                    config.load_incluster_config()
                except config.ConfigException:
                    config.load_kube_config(context=self.context)

            self._api = CoreV1Api()
            self._apps_api = AppsV1Api()
            return True

        except ImportError:
            log.warning("kubernetes package not installed")
            return False
        except Exception as e:
            log.warning("Failed to init K8s client: %s", e)
            return False

    async def fetch_events(
        self,
        since: datetime | None = None,
        cursor: str | None = None,
    ) -> tuple[list[SyncEvent], str]:
        """Fetch Kubernetes events."""
        if not await self._init_client():
            return [], ""

        events: list[SyncEvent] = []

        try:
            if self.namespace:
                k8s_events = await asyncio.to_thread(
                    self._api.list_namespaced_event,
                    namespace=self.namespace,
                )
            else:
                k8s_events = await asyncio.to_thread(
                    self._api.list_event_for_all_namespaces,
                )

            for event in k8s_events.items:
                last_timestamp = event.last_timestamp or event.event_time
                if last_timestamp is None:
                    continue

                if isinstance(last_timestamp, str):
                    timestamp = datetime.fromisoformat(last_timestamp.replace("Z", "+00:00"))
                else:
                    timestamp = last_timestamp.replace(tzinfo=timezone.utc)

                if since and timestamp < since:
                    continue

                involved = event.involved_object
                events.append(SyncEvent(
                    source=SyncSource.KUBERNETES,
                    event_type=event.reason or "Unknown",
                    event_id=f"k8s:{event.metadata.namespace}:{event.metadata.name}",
                    timestamp=timestamp,
                    data={
                        "reason": event.reason,
                        "message": event.message,
                        "type": event.type,
                        "count": event.count,
                        "namespace": event.metadata.namespace,
                        "involved_kind": involved.kind if involved else "",
                        "involved_name": involved.name if involved else "",
                        "involved_namespace": involved.namespace if involved else "",
                        "source_component": event.source.component if event.source else "",
                        "source_host": event.source.host if event.source else "",
                    },
                    raw={},
                ))

            await self._sync_services()

        except Exception as e:
            log.error("Kubernetes fetch error: %s", e)

        return events, ""

    async def _sync_services(self) -> None:
        """Sync Kubernetes services to graph."""
        if self._api is None:
            return

        try:
            if self.namespace:
                services = await asyncio.to_thread(
                    self._api.list_namespaced_service,
                    namespace=self.namespace,
                )
            else:
                services = await asyncio.to_thread(
                    self._api.list_service_for_all_namespaces,
                )

            for svc in services.items:
                service_node = ServiceNode(
                    id=f"k8s:{svc.metadata.namespace}:{svc.metadata.name}",
                    name=svc.metadata.name,
                    namespace=svc.metadata.namespace,
                    labels={k: v for k, v in (svc.metadata.labels or {}).items()},
                )
                await self.graph.create_service(service_node)

            if self.namespace:
                deployments = await asyncio.to_thread(
                    self._apps_api.list_namespaced_deployment,
                    namespace=self.namespace,
                )
            else:
                deployments = await asyncio.to_thread(
                    self._apps_api.list_deployment_for_all_namespaces,
                )

            for dep in deployments.items:
                service_id = f"k8s:{dep.metadata.namespace}:{dep.metadata.name}"

                conditions = dep.status.conditions or []
                available = any(
                    c.type == "Available" and c.status == "True"
                    for c in conditions
                )

                service_node = ServiceNode(
                    id=service_id,
                    name=dep.metadata.name,
                    namespace=dep.metadata.namespace,
                    health_status="healthy" if available else "degraded",
                    labels={k: v for k, v in (dep.metadata.labels or {}).items()},
                )
                await self.graph.create_service(service_node)

        except Exception as e:
            log.debug("K8s service sync: %s", e)

    async def process_event(self, event: SyncEvent) -> None:
        """Process Kubernetes event."""
        data = event.data

        if data.get("involved_kind") in ("Deployment", "ReplicaSet", "Pod"):
            if data["reason"] in ("Scaled", "ScalingReplicaSet", "SuccessfulCreate"):
                service_id = f"k8s:{data['involved_namespace']}:{data['involved_name'].rsplit('-', 1)[0]}"
                change = ChangeNode(
                    id=f"k8s:{event.event_id}",
                    change_type="infrastructure",
                    title=f"K8s {data['reason']}: {data['involved_name']}",
                    description=data.get("message", ""),
                    timestamp=event.timestamp,
                    source="kubernetes",
                    affected_resources=[service_id],
                    risk_score=0.3,
                )
                await self.graph.create_change(change)


class JaegerSyncer(BaseSyncer):
    """Sync from Jaeger tracing."""

    def __init__(
        self,
        graph: Neo4jKnowledgeGraph,
        jaeger_url: str = "http://localhost:16686",
    ):
        super().__init__(graph)
        self.jaeger_url = jaeger_url.rstrip("/")
        self._client: Any = None

    @property
    def source(self) -> SyncSource:
        return SyncSource.JAEGER

    async def _get_client(self) -> Any:
        """Get HTTP client."""
        if self._client is None:
            try:
                import httpx
                self._client = httpx.AsyncClient(timeout=30.0)
            except ImportError:
                log.warning("httpx not installed")
                return None
        return self._client

    async def fetch_events(
        self,
        since: datetime | None = None,
        cursor: str | None = None,
    ) -> tuple[list[SyncEvent], str]:
        """Fetch service dependencies from Jaeger."""
        client = await self._get_client()
        if not client:
            return [], ""

        events: list[SyncEvent] = []

        try:
            services_response = await client.get(f"{self.jaeger_url}/api/services")
            if services_response.status_code != 200:
                return [], ""

            services = services_response.json().get("data", [])

            for service_name in services:
                service = ServiceNode(
                    id=service_name.replace("-", "_").replace(".", "_"),
                    name=service_name,
                )
                await self.graph.create_service(service)

            deps_response = await client.get(f"{self.jaeger_url}/api/dependencies")
            if deps_response.status_code == 200:
                dependencies = deps_response.json().get("data", [])

                for dep in dependencies:
                    parent = dep.get("parent", "")
                    child = dep.get("child", "")
                    call_count = dep.get("callCount", 0)

                    if parent and child:
                        parent_id = parent.replace("-", "_").replace(".", "_")
                        child_id = child.replace("-", "_").replace(".", "_")

                        rel = Relationship(
                            source_id=parent_id,
                            target_id=child_id,
                            rel_type=RelationType.CALLS,
                            properties={"call_count": call_count},
                        )
                        await self.graph.create_relationship(rel)

                        events.append(SyncEvent(
                            source=SyncSource.JAEGER,
                            event_type="dependency",
                            event_id=f"jaeger:dep:{parent}:{child}",
                            timestamp=datetime.now(timezone.utc),
                            data={
                                "parent": parent,
                                "child": child,
                                "call_count": call_count,
                            },
                            raw=dep,
                        ))

            for service_name in services[:10]:
                await self._fetch_error_traces(client, service_name, since, events)

        except Exception as e:
            log.error("Jaeger fetch error: %s", e)

        return events, ""

    async def _fetch_error_traces(
        self,
        client: Any,
        service: str,
        since: datetime | None,
        events: list[SyncEvent],
    ) -> None:
        """Fetch error traces for a service."""
        params: dict[str, Any] = {
            "service": service,
            "limit": 20,
            "tags": '{"error":"true"}',
        }

        if since:
            params["start"] = int(since.timestamp() * 1_000_000)
            params["end"] = int(datetime.now(timezone.utc).timestamp() * 1_000_000)

        try:
            response = await client.get(
                f"{self.jaeger_url}/api/traces",
                params=params,
            )

            if response.status_code != 200:
                return

            traces = response.json().get("data", [])

            for trace in traces:
                spans = trace.get("spans", [])
                if not spans:
                    continue

                root_span = spans[0]
                start_time = root_span.get("startTime", 0) / 1_000_000
                timestamp = datetime.fromtimestamp(start_time, tz=timezone.utc)

                trace_id = trace.get("traceID", "")
                duration_us = sum(s.get("duration", 0) for s in spans)

                events.append(SyncEvent(
                    source=SyncSource.JAEGER,
                    event_type="error_trace",
                    event_id=f"jaeger:trace:{trace_id}",
                    timestamp=timestamp,
                    data={
                        "trace_id": trace_id,
                        "service": service,
                        "operation": root_span.get("operationName", ""),
                        "duration_ms": duration_us / 1000,
                        "span_count": len(spans),
                        "error": True,
                    },
                    raw=trace,
                ))

        except Exception as e:
            log.debug("Error fetching traces for %s: %s", service, e)

    async def process_event(self, event: SyncEvent) -> None:
        """Process Jaeger event."""
        pass


class ContinuousSyncEngine:
    """Orchestrates continuous sync from multiple sources."""

    def __init__(self, graph: Neo4jKnowledgeGraph):
        self.graph = graph
        self.syncers: dict[SyncSource, BaseSyncer] = {}
        self._running = False
        self._callbacks: list[Callable[[SyncEvent], Awaitable[None]]] = []

    def add_github(
        self,
        token: str,
        owner: str,
        repos: list[str],
    ) -> GitHubSyncer:
        """Add GitHub sync source."""
        syncer = GitHubSyncer(self.graph, token, owner, repos)
        self.syncers[SyncSource.GITHUB] = syncer
        return syncer

    def add_cloudtrail(
        self,
        region: str = "us-east-1",
        trail_name: str | None = None,
    ) -> CloudTrailSyncer:
        """Add CloudTrail sync source."""
        syncer = CloudTrailSyncer(self.graph, region, trail_name)
        self.syncers[SyncSource.CLOUDTRAIL] = syncer
        return syncer

    def add_prometheus(
        self,
        prometheus_url: str = "http://localhost:9090",
        alertmanager_url: str = "http://localhost:9093",
    ) -> PrometheusSyncer:
        """Add Prometheus sync source."""
        syncer = PrometheusSyncer(self.graph, prometheus_url, alertmanager_url)
        self.syncers[SyncSource.PROMETHEUS] = syncer
        return syncer

    def add_kubernetes(
        self,
        kubeconfig: str | None = None,
        context: str | None = None,
        namespace: str | None = None,
    ) -> KubernetesSyncer:
        """Add Kubernetes sync source."""
        syncer = KubernetesSyncer(self.graph, kubeconfig, context, namespace)
        self.syncers[SyncSource.KUBERNETES] = syncer
        return syncer

    def add_jaeger(
        self,
        jaeger_url: str = "http://localhost:16686",
    ) -> JaegerSyncer:
        """Add Jaeger sync source."""
        syncer = JaegerSyncer(self.graph, jaeger_url)
        self.syncers[SyncSource.JAEGER] = syncer
        return syncer

    def on_event(
        self,
        callback: Callable[[SyncEvent], Awaitable[None]],
    ) -> None:
        """Register callback for sync events."""
        self._callbacks.append(callback)

    async def sync_all_once(self) -> dict[str, int]:
        """Run one sync cycle for all sources."""
        results = {}
        tasks = []

        for source, syncer in self.syncers.items():
            tasks.append(self._sync_with_callback(source, syncer))

        sync_results = await asyncio.gather(*tasks, return_exceptions=True)

        for (source, _), result in zip(self.syncers.items(), sync_results):
            if isinstance(result, BaseException):
                log.error("Sync error for %s: %s", source.value, result)
                results[source.value] = -1
            else:
                results[source.value] = int(result)

        return results

    async def _sync_with_callback(
        self,
        source: SyncSource,
        syncer: BaseSyncer,
    ) -> int:
        """Sync and notify callbacks."""
        count = await syncer.sync_once()
        return count

    async def start_continuous(
        self,
        intervals: dict[SyncSource, int] | None = None,
    ) -> None:
        """Start continuous sync for all sources."""
        self._running = True

        default_intervals = {
            SyncSource.GITHUB: 300,
            SyncSource.CLOUDTRAIL: 60,
            SyncSource.PROMETHEUS: 30,
            SyncSource.KUBERNETES: 60,
            SyncSource.JAEGER: 120,
        }

        if intervals:
            default_intervals.update(intervals)

        for source, syncer in self.syncers.items():
            interval = default_intervals.get(source, 60)
            await syncer.start_continuous(interval)

    async def stop(self) -> None:
        """Stop all syncers."""
        self._running = False
        for syncer in self.syncers.values():
            await syncer.stop()

    def get_status(self) -> dict[str, Any]:
        """Get status of all syncers."""
        return {
            "running": self._running,
            "sources": {
                source.value: syncer.state.to_dict()
                for source, syncer in self.syncers.items()
            },
        }
