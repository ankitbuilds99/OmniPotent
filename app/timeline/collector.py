"""Timeline event collection and correlation.

Collects events from multiple sources:
- Monitoring systems (Prometheus, Datadog, etc.)
- Logging platforms (Elasticsearch, CloudWatch)
- Deployment tools (GitHub, ArgoCD, Spinnaker)
- Communication tools (Slack, PagerDuty)
- Manual annotations
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable, Awaitable

log = logging.getLogger("devcopilot.timeline.collector")


class EventType(Enum):
    """Types of timeline events."""

    ALERT = "alert"
    METRIC = "metric"
    LOG = "log"
    DEPLOY = "deploy"
    CONFIG_CHANGE = "config_change"
    SCALE = "scale"
    RESTART = "restart"
    COMMUNICATION = "communication"
    ANNOTATION = "annotation"
    INVESTIGATION = "investigation"
    MITIGATION = "mitigation"
    RESOLUTION = "resolution"
    OTHER = "other"


class EventSeverity(Enum):
    """Event severity levels."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class EventSource(Enum):
    """Sources of timeline events."""

    PROMETHEUS = "prometheus"
    DATADOG = "datadog"
    CLOUDWATCH = "cloudwatch"
    ELASTICSEARCH = "elasticsearch"
    GITHUB = "github"
    ARGOCD = "argocd"
    KUBERNETES = "kubernetes"
    SLACK = "slack"
    PAGERDUTY = "pagerduty"
    MANUAL = "manual"
    COPILOT = "copilot"
    OTHER = "other"


@dataclass
class TimelineEvent:
    """A single event in the incident timeline."""

    id: str
    timestamp: datetime
    title: str
    description: str = ""
    event_type: EventType = EventType.OTHER
    severity: EventSeverity = EventSeverity.INFO
    source: EventSource = EventSource.OTHER
    service: str = ""
    host: str = ""
    user: str = ""
    url: str = ""
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    correlation_id: str = ""
    parent_id: str = ""
    duration_seconds: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "timestamp": self.timestamp.isoformat(),
            "title": self.title,
            "description": self.description,
            "event_type": self.event_type.value,
            "severity": self.severity.value,
            "source": self.source.value,
            "service": self.service,
            "host": self.host,
            "user": self.user,
            "url": self.url,
            "tags": self.tags,
            "metadata": self.metadata,
            "correlation_id": self.correlation_id,
            "parent_id": self.parent_id,
            "duration_seconds": self.duration_seconds,
        }


@dataclass
class TimelineFilter:
    """Filter for timeline queries."""

    start_time: datetime | None = None
    end_time: datetime | None = None
    event_types: list[EventType] | None = None
    severities: list[EventSeverity] | None = None
    sources: list[EventSource] | None = None
    services: list[str] | None = None
    tags: list[str] | None = None
    search_query: str = ""
    limit: int = 1000

    def matches(self, event: TimelineEvent) -> bool:
        """Check if event matches filter."""
        if self.start_time and event.timestamp < self.start_time:
            return False
        if self.end_time and event.timestamp > self.end_time:
            return False
        if self.event_types and event.event_type not in self.event_types:
            return False
        if self.severities and event.severity not in self.severities:
            return False
        if self.sources and event.source not in self.sources:
            return False
        if self.services and event.service not in self.services:
            return False
        if self.tags:
            if not any(t in event.tags for t in self.tags):
                return False
        if self.search_query:
            query = self.search_query.lower()
            searchable = f"{event.title} {event.description} {' '.join(event.tags)}".lower()
            if query not in searchable:
                return False
        return True


EventCollectorFunc = Callable[
    [datetime, datetime, dict[str, Any]],
    Awaitable[list[TimelineEvent]],
]


class TimelineCollector:
    """Collects and aggregates timeline events from multiple sources."""

    def __init__(self):
        self._collectors: dict[EventSource, EventCollectorFunc] = {}
        self._events: list[TimelineEvent] = []

    def register_collector(
        self,
        source: EventSource,
        collector: EventCollectorFunc,
    ) -> None:
        """Register an event collector for a source."""
        self._collectors[source] = collector
        log.info("Registered collector for %s", source.value)

    async def collect(
        self,
        start_time: datetime,
        end_time: datetime,
        sources: list[EventSource] | None = None,
        context: dict[str, Any] | None = None,
    ) -> list[TimelineEvent]:
        """Collect events from all registered sources."""
        all_events: list[TimelineEvent] = []
        context = context or {}

        target_sources = sources or list(self._collectors.keys())

        for source in target_sources:
            if source not in self._collectors:
                continue

            try:
                events = await self._collectors[source](start_time, end_time, context)
                all_events.extend(events)
                log.info("Collected %d events from %s", len(events), source.value)
            except Exception as e:
                log.error("Failed to collect from %s: %s", source.value, e)

        all_events.sort(key=lambda e: e.timestamp)
        self._events = all_events
        return all_events

    def add_event(self, event: TimelineEvent) -> None:
        """Add a single event to the timeline."""
        self._events.append(event)
        self._events.sort(key=lambda e: e.timestamp)

    def get_events(self, filter: TimelineFilter | None = None) -> list[TimelineEvent]:
        """Get events with optional filtering."""
        events = self._events

        if filter:
            events = [e for e in events if filter.matches(e)]
            events = events[: filter.limit]

        return events

    def get_services(self) -> list[str]:
        """Get unique services from events."""
        return sorted(set(e.service for e in self._events if e.service))

    def get_event_counts_by_type(self) -> dict[str, int]:
        """Get event counts grouped by type."""
        counts: dict[str, int] = {}
        for event in self._events:
            key = event.event_type.value
            counts[key] = counts.get(key, 0) + 1
        return counts

    def get_event_counts_by_severity(self) -> dict[str, int]:
        """Get event counts grouped by severity."""
        counts: dict[str, int] = {}
        for event in self._events:
            key = event.severity.value
            counts[key] = counts.get(key, 0) + 1
        return counts

    def get_timeline_summary(self) -> dict[str, Any]:
        """Get summary statistics for the timeline."""
        if not self._events:
            return {
                "total_events": 0,
                "start_time": None,
                "end_time": None,
                "duration_minutes": 0,
                "services": [],
                "by_type": {},
                "by_severity": {},
            }

        return {
            "total_events": len(self._events),
            "start_time": self._events[0].timestamp.isoformat(),
            "end_time": self._events[-1].timestamp.isoformat(),
            "duration_minutes": int(
                (self._events[-1].timestamp - self._events[0].timestamp).total_seconds() / 60
            ),
            "services": self.get_services(),
            "by_type": self.get_event_counts_by_type(),
            "by_severity": self.get_event_counts_by_severity(),
        }


def aggregate_events(
    events: list[TimelineEvent],
    window_seconds: int = 60,
) -> list[dict[str, Any]]:
    """Aggregate events into time windows for visualization."""
    if not events:
        return []

    start = events[0].timestamp
    end = events[-1].timestamp

    windows: list[dict[str, Any]] = []
    current = start

    while current <= end:
        window_end = current + timedelta(seconds=window_seconds)
        window_events = [
            e for e in events
            if current <= e.timestamp < window_end
        ]

        severity_counts = {s.value: 0 for s in EventSeverity}
        for e in window_events:
            severity_counts[e.severity.value] += 1

        windows.append({
            "start": current.isoformat(),
            "end": window_end.isoformat(),
            "count": len(window_events),
            "by_severity": severity_counts,
            "services": list(set(e.service for e in window_events if e.service)),
        })

        current = window_end

    return windows


def correlate_events(
    events: list[TimelineEvent],
    correlation_window_seconds: int = 120,
) -> list[list[TimelineEvent]]:
    """Group related events based on time proximity and attributes."""
    if not events:
        return []

    groups: list[list[TimelineEvent]] = []
    used: set[str] = set()

    for event in events:
        if event.id in used:
            continue

        group = [event]
        used.add(event.id)

        window_start = event.timestamp - timedelta(seconds=correlation_window_seconds)
        window_end = event.timestamp + timedelta(seconds=correlation_window_seconds)

        for other in events:
            if other.id in used:
                continue

            if window_start <= other.timestamp <= window_end:
                related = False

                if event.service and event.service == other.service:
                    related = True

                if event.correlation_id and event.correlation_id == other.correlation_id:
                    related = True

                if event.tags and other.tags:
                    if set(event.tags) & set(other.tags):
                        related = True

                if related:
                    group.append(other)
                    used.add(other.id)

        groups.append(sorted(group, key=lambda e: e.timestamp))

    return groups


async def collect_prometheus_events(
    start_time: datetime,
    end_time: datetime,
    context: dict[str, Any],
) -> list[TimelineEvent]:
    """Collect alert events from Prometheus."""
    import secrets

    events: list[TimelineEvent] = []

    prometheus_url = context.get("prometheus_url")
    if not prometheus_url:
        return events

    try:
        import httpx

        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{prometheus_url}/api/v1/query",
                params={"query": "ALERTS{alertstate='firing'}"},
                timeout=30,
            )
            if resp.status_code != 200:
                return events

            data = resp.json()
            for result in data.get("data", {}).get("result", []):
                metric = result.get("metric", {})
                value = result.get("value", [])

                if len(value) >= 2:
                    ts = datetime.fromtimestamp(float(value[0]), tz=timezone.utc)

                    if start_time <= ts <= end_time:
                        events.append(TimelineEvent(
                            id=f"prom_{secrets.token_hex(8)}",
                            timestamp=ts,
                            title=metric.get("alertname", "Unknown Alert"),
                            description=metric.get("description", ""),
                            event_type=EventType.ALERT,
                            severity=EventSeverity.WARNING,
                            source=EventSource.PROMETHEUS,
                            service=metric.get("service", ""),
                            tags=list(metric.keys()),
                            metadata=metric,
                        ))
    except Exception as e:
        log.error("Prometheus collection failed: %s", e)

    return events


async def collect_kubernetes_events(
    start_time: datetime,
    end_time: datetime,
    context: dict[str, Any],
) -> list[TimelineEvent]:
    """Collect events from Kubernetes."""
    import secrets

    events: list[TimelineEvent] = []

    namespace = context.get("namespace", "default")

    try:
        from kubernetes import client, config

        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()

        v1 = client.CoreV1Api()
        k8s_events = v1.list_namespaced_event(namespace=namespace)

        for k8s_event in k8s_events.items:
            event_time = k8s_event.last_timestamp or k8s_event.event_time
            if not event_time:
                continue

            if isinstance(event_time, str):
                event_time = datetime.fromisoformat(event_time.replace("Z", "+00:00"))

            if event_time.tzinfo is None:
                event_time = event_time.replace(tzinfo=timezone.utc)

            if start_time <= event_time <= end_time:
                severity = EventSeverity.INFO
                if k8s_event.type == "Warning":
                    severity = EventSeverity.WARNING

                event_type = EventType.OTHER
                reason = k8s_event.reason or ""
                if "Scaled" in reason:
                    event_type = EventType.SCALE
                elif "Restart" in reason or "Backoff" in reason:
                    event_type = EventType.RESTART
                elif "Pull" in reason or "Creat" in reason:
                    event_type = EventType.DEPLOY

                events.append(TimelineEvent(
                    id=f"k8s_{secrets.token_hex(8)}",
                    timestamp=event_time,
                    title=f"{k8s_event.reason}: {k8s_event.involved_object.name}",
                    description=k8s_event.message or "",
                    event_type=event_type,
                    severity=severity,
                    source=EventSource.KUBERNETES,
                    service=k8s_event.involved_object.name,
                    host=k8s_event.source.host if k8s_event.source else "",
                    metadata={
                        "kind": k8s_event.involved_object.kind,
                        "namespace": k8s_event.involved_object.namespace,
                        "reason": k8s_event.reason,
                        "count": k8s_event.count,
                    },
                ))
    except Exception as e:
        log.error("Kubernetes collection failed: %s", e)

    return events


async def collect_github_events(
    start_time: datetime,
    end_time: datetime,
    context: dict[str, Any],
) -> list[TimelineEvent]:
    """Collect deployment events from GitHub."""
    import secrets

    events: list[TimelineEvent] = []

    github_token = context.get("github_token")
    repo = context.get("github_repo")

    if not github_token or not repo:
        return events

    try:
        import httpx

        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"https://api.github.com/repos/{repo}/deployments",
                headers={
                    "Authorization": f"token {github_token}",
                    "Accept": "application/vnd.github.v3+json",
                },
                timeout=30,
            )
            if resp.status_code != 200:
                return events

            for deploy in resp.json():
                created = datetime.fromisoformat(
                    deploy["created_at"].replace("Z", "+00:00")
                )

                if start_time <= created <= end_time:
                    events.append(TimelineEvent(
                        id=f"gh_{secrets.token_hex(8)}",
                        timestamp=created,
                        title=f"Deployment: {deploy.get('environment', 'unknown')}",
                        description=deploy.get("description", ""),
                        event_type=EventType.DEPLOY,
                        severity=EventSeverity.INFO,
                        source=EventSource.GITHUB,
                        service=repo.split("/")[-1],
                        user=deploy.get("creator", {}).get("login", ""),
                        url=deploy.get("url", ""),
                        metadata={
                            "environment": deploy.get("environment"),
                            "ref": deploy.get("ref"),
                            "sha": deploy.get("sha"),
                        },
                    ))
    except Exception as e:
        log.error("GitHub collection failed: %s", e)

    return events


async def collect_incident_timeline(
    incident_id: str,
    start_time: datetime,
    end_time: datetime,
    sources: list[EventSource] | None = None,
    context: dict[str, Any] | None = None,
) -> tuple[list[TimelineEvent], dict[str, Any]]:
    """Collect complete timeline for an incident."""
    collector = TimelineCollector()

    collector.register_collector(EventSource.PROMETHEUS, collect_prometheus_events)
    collector.register_collector(EventSource.KUBERNETES, collect_kubernetes_events)
    collector.register_collector(EventSource.GITHUB, collect_github_events)

    context = context or {}
    context["incident_id"] = incident_id

    events = await collector.collect(start_time, end_time, sources, context)
    summary = collector.get_timeline_summary()

    return events, summary
