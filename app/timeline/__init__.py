"""Incident timeline module.

Provides:
- Timeline event collection and aggregation
- Multi-source event correlation
- Timeline visualization data
- Event filtering and search
"""

from app.timeline.collector import (
    TimelineCollector,
    TimelineEvent,
    EventSource,
    EventType,
    EventSeverity,
    TimelineFilter,
    collect_incident_timeline,
    aggregate_events,
    correlate_events,
)

__all__ = [
    "TimelineCollector",
    "TimelineEvent",
    "EventSource",
    "EventType",
    "EventSeverity",
    "TimelineFilter",
    "collect_incident_timeline",
    "aggregate_events",
    "correlate_events",
]
