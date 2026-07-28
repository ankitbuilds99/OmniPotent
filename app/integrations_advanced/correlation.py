"""Cross-Integration Correlation Engine.

Correlates data across all connected tools to build unified incident timelines.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("devcopilot.integrations_advanced.correlation")


class EventType(str, Enum):
    DEPLOY = "deploy"
    ALERT = "alert"
    ERROR = "error"
    CONFIG_CHANGE = "config_change"
    PR_MERGED = "pr_merged"
    INCIDENT_CREATED = "incident_created"
    METRIC_ANOMALY = "metric_anomaly"
    LOG_PATTERN = "log_pattern"
    USER_ACTION = "user_action"
    ROLLBACK = "rollback"


@dataclass
class CorrelatedEvent:
    """An event from an integration."""

    event_id: str
    integration: str
    event_type: EventType
    timestamp: datetime
    title: str
    description: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0
    link_url: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "integration": self.integration,
            "type": self.event_type.value,
            "timestamp": self.timestamp.isoformat(),
            "title": self.title,
            "description": self.description,
            "metadata": self.metadata,
            "confidence": round(self.confidence, 2),
            "link": self.link_url,
        }


@dataclass
class CorrelationLink:
    """A causal link between two events."""

    source_event: str
    target_event: str
    relationship: str
    confidence: float
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source_event,
            "target": self.target_event,
            "relationship": self.relationship,
            "confidence": round(self.confidence, 2),
            "reason": self.reason,
        }


@dataclass
class CorrelationChain:
    """A chain of correlated events forming an incident timeline."""

    chain_id: str
    incident_id: str | None
    events: list[CorrelatedEvent]
    links: list[CorrelationLink]
    root_cause_event: str | None = None
    ai_summary: str = ""
    total_duration_minutes: float = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "chain_id": self.chain_id,
            "incident_id": self.incident_id,
            "events": [e.to_dict() for e in self.events],
            "links": [link.to_dict() for link in self.links],
            "root_cause": self.root_cause_event,
            "ai_summary": self.ai_summary,
            "duration_minutes": round(self.total_duration_minutes, 1),
            "integrations_involved": list({e.integration for e in self.events}),
        }


class CrossIntegrationCorrelator:
    """Correlates events across all integrations."""

    def __init__(self) -> None:
        self._events: dict[str, list[CorrelatedEvent]] = {}
        self._chains: dict[str, CorrelationChain] = {}
        self._correlation_rules: list[dict[str, Any]] = []
        self._init_default_rules()

    def _init_default_rules(self) -> None:
        """Initialize default correlation rules."""
        self._correlation_rules = [
            {
                "name": "deploy_causes_alert",
                "source_type": EventType.DEPLOY,
                "target_type": EventType.ALERT,
                "max_time_delta_minutes": 30,
                "confidence": 0.85,
                "relationship": "caused_by",
            },
            {
                "name": "pr_merge_to_deploy",
                "source_type": EventType.PR_MERGED,
                "target_type": EventType.DEPLOY,
                "max_time_delta_minutes": 60,
                "confidence": 0.95,
                "relationship": "triggers",
            },
            {
                "name": "alert_to_incident",
                "source_type": EventType.ALERT,
                "target_type": EventType.INCIDENT_CREATED,
                "max_time_delta_minutes": 15,
                "confidence": 0.90,
                "relationship": "triggers",
            },
            {
                "name": "error_to_alert",
                "source_type": EventType.ERROR,
                "target_type": EventType.ALERT,
                "max_time_delta_minutes": 10,
                "confidence": 0.80,
                "relationship": "triggers",
            },
            {
                "name": "metric_anomaly_to_alert",
                "source_type": EventType.METRIC_ANOMALY,
                "target_type": EventType.ALERT,
                "max_time_delta_minutes": 5,
                "confidence": 0.75,
                "relationship": "triggers",
            },
            {
                "name": "incident_to_rollback",
                "source_type": EventType.INCIDENT_CREATED,
                "target_type": EventType.ROLLBACK,
                "max_time_delta_minutes": 120,
                "confidence": 0.70,
                "relationship": "leads_to",
            },
        ]

    def ingest_event(self, org_id: str, event: CorrelatedEvent) -> None:
        """Ingest an event for correlation."""
        if org_id not in self._events:
            self._events[org_id] = []
        self._events[org_id].append(event)
        self._events[org_id].sort(key=lambda e: e.timestamp)

        if len(self._events[org_id]) > 10000:
            self._events[org_id] = self._events[org_id][-5000:]

        log.info(
            "Ingested event: org=%s integration=%s type=%s",
            org_id,
            event.integration,
            event.event_type.value,
        )

    def correlate_events(
        self,
        org_id: str,
        time_window_hours: int = 24,
        incident_id: str | None = None,
    ) -> list[CorrelationChain]:
        """Find correlated events and build chains."""
        events = self._events.get(org_id, [])
        if not events:
            return []

        cutoff = datetime.now(timezone.utc) - timedelta(hours=time_window_hours)
        recent_events = [e for e in events if e.timestamp >= cutoff]

        links: list[CorrelationLink] = []
        for source in recent_events:
            for target in recent_events:
                if source.event_id == target.event_id:
                    continue

                link = self._try_correlate(source, target)
                if link:
                    links.append(link)

        chains = self._build_chains(recent_events, links, incident_id)

        for chain in chains:
            self._chains[chain.chain_id] = chain

        return chains

    def _try_correlate(
        self,
        source: CorrelatedEvent,
        target: CorrelatedEvent,
    ) -> CorrelationLink | None:
        """Try to correlate two events using rules."""
        if source.timestamp > target.timestamp:
            return None

        for rule in self._correlation_rules:
            if (
                source.event_type == rule["source_type"]
                and target.event_type == rule["target_type"]
            ):
                time_delta = (target.timestamp - source.timestamp).total_seconds() / 60

                if time_delta <= rule["max_time_delta_minutes"]:
                    confidence = rule["confidence"]

                    if time_delta < 5:
                        confidence = min(1.0, confidence + 0.1)

                    if self._share_context(source, target):
                        confidence = min(1.0, confidence + 0.15)

                    return CorrelationLink(
                        source_event=source.event_id,
                        target_event=target.event_id,
                        relationship=rule["relationship"],
                        confidence=confidence,
                        reason=rule["name"],
                    )

        return None

    def _share_context(
        self,
        source: CorrelatedEvent,
        target: CorrelatedEvent,
    ) -> bool:
        """Check if events share contextual identifiers."""
        source_meta = source.metadata
        target_meta = target.metadata

        shared_keys = ["service", "deployment_id", "pr_number", "trace_id", "user_id"]
        for key in shared_keys:
            if key in source_meta and key in target_meta:
                if source_meta[key] == target_meta[key]:
                    return True

        return False

    def _build_chains(
        self,
        events: list[CorrelatedEvent],
        links: list[CorrelationLink],
        incident_id: str | None,
    ) -> list[CorrelationChain]:
        """Build correlation chains from events and links."""
        event_map = {e.event_id: e for e in events}
        adjacency: dict[str, list[str]] = {}

        for link in links:
            if link.source_event not in adjacency:
                adjacency[link.source_event] = []
            adjacency[link.source_event].append(link.target_event)

        visited: set[str] = set()
        chains: list[CorrelationChain] = []

        for event in events:
            if event.event_id in visited:
                continue

            chain_events: list[CorrelatedEvent] = []
            chain_links: list[CorrelationLink] = []
            stack = [event.event_id]

            while stack:
                eid = stack.pop()
                if eid in visited:
                    continue
                visited.add(eid)

                if eid in event_map:
                    chain_events.append(event_map[eid])

                for link in links:
                    if link.source_event == eid and link.target_event not in visited:
                        chain_links.append(link)
                        stack.append(link.target_event)

            if len(chain_events) >= 2:
                chain_events.sort(key=lambda e: e.timestamp)
                root_cause = chain_events[0].event_id

                duration = (
                    chain_events[-1].timestamp - chain_events[0].timestamp
                ).total_seconds() / 60

                chain = CorrelationChain(
                    chain_id=f"chain_{chain_events[0].event_id}",
                    incident_id=incident_id,
                    events=chain_events,
                    links=chain_links,
                    root_cause_event=root_cause,
                    ai_summary=self._generate_summary(chain_events, chain_links),
                    total_duration_minutes=duration,
                )
                chains.append(chain)

        return chains

    def _generate_summary(
        self,
        events: list[CorrelatedEvent],
        links: list[CorrelationLink],
    ) -> str:
        """Generate AI summary of correlation chain."""
        if not events:
            return ""

        integrations = list({e.integration for e in events})
        first = events[0]
        last = events[-1]

        summary = (
            f"Data from {len(integrations)} tools shows clear causation chain: "
            f"{first.title} ({first.integration}) at {first.timestamp.strftime('%H:%M')} "
            f"led to {last.title} ({last.integration}) at {last.timestamp.strftime('%H:%M')}. "
        )

        if len(events) > 2:
            summary += f"Chain includes {len(events)} events across {', '.join(integrations)}."

        return summary

    def get_chain(self, chain_id: str) -> CorrelationChain | None:
        """Get a correlation chain by ID."""
        return self._chains.get(chain_id)

    def get_incident_timeline(
        self,
        org_id: str,
        incident_id: str,
        time_range_hours: int = 2,
    ) -> dict[str, Any]:
        """Get a unified timeline for an incident."""
        chains = self.correlate_events(org_id, time_range_hours, incident_id)

        if not chains:
            return {
                "incident_id": incident_id,
                "timeline": [],
                "integrations": [],
                "ai_insight": "No correlated events found.",
            }

        primary_chain = max(chains, key=lambda c: len(c.events))

        timeline = []
        for event in primary_chain.events:
            timeline.append({
                "timestamp": event.timestamp.strftime("%H:%M"),
                "integration": event.integration,
                "title": event.title,
                "type": event.event_type.value,
                "link": event.link_url,
            })

        return {
            "incident_id": incident_id,
            "timeline": timeline,
            "integrations": list({e.integration for e in primary_chain.events}),
            "root_cause": primary_chain.root_cause_event,
            "duration_minutes": primary_chain.total_duration_minutes,
            "ai_insight": primary_chain.ai_summary,
        }


_correlator: CrossIntegrationCorrelator | None = None


def get_correlator() -> CrossIntegrationCorrelator:
    """Get the default correlator."""
    global _correlator
    if _correlator is None:
        _correlator = CrossIntegrationCorrelator()
    return _correlator
