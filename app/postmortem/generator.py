"""Postmortem document generator.

Generates structured postmortem documents from incident data with:
- Automated timeline construction
- Root cause analysis synthesis
- Action item generation
- Multiple template formats (Google, PagerDuty, custom)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("devcopilot.postmortem.generator")


class ActionItemStatus(Enum):
    """Action item status."""

    OPEN = "open"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    WONT_FIX = "wont_fix"


class ActionItemPriority(Enum):
    """Action item priority."""

    P0 = "p0"
    P1 = "p1"
    P2 = "p2"
    P3 = "p3"


@dataclass
class ActionItem:
    """A postmortem action item."""

    id: str
    title: str
    description: str
    owner: str = ""
    priority: ActionItemPriority = ActionItemPriority.P2
    status: ActionItemStatus = ActionItemStatus.OPEN
    due_date: datetime | None = None
    ticket_url: str = ""
    category: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class TimelineEvent:
    """An event in the incident timeline."""

    timestamp: datetime
    title: str
    description: str = ""
    author: str = ""
    event_type: str = "info"
    source: str = ""


@dataclass
class PostmortemDocument:
    """A complete postmortem document."""

    id: str
    incident_id: str
    title: str
    status: str = "draft"
    severity: str = ""
    summary: str = ""
    impact: str = ""
    trigger: str = ""
    detection: str = ""
    root_cause: str = ""
    contributing_factors: list[str] = field(default_factory=list)
    timeline: list[TimelineEvent] = field(default_factory=list)
    action_items: list[ActionItem] = field(default_factory=list)
    lessons_learned: list[str] = field(default_factory=list)
    things_that_went_well: list[str] = field(default_factory=list)
    things_that_went_poorly: list[str] = field(default_factory=list)
    authors: list[str] = field(default_factory=list)
    reviewers: list[str] = field(default_factory=list)
    affected_services: list[str] = field(default_factory=list)
    customer_impact: str = ""
    duration_minutes: int = 0
    time_to_detect_minutes: int = 0
    time_to_resolve_minutes: int = 0
    started_at: datetime | None = None
    detected_at: datetime | None = None
    mitigated_at: datetime | None = None
    resolved_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    published_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PostmortemTemplate:
    """Postmortem document template."""

    id: str
    name: str
    description: str
    sections: list[str]
    required_fields: list[str]
    example_content: dict[str, str] = field(default_factory=dict)


TEMPLATES = {
    "google": PostmortemTemplate(
        id="google",
        name="Google SRE",
        description="Google SRE-style blameless postmortem",
        sections=[
            "summary",
            "impact",
            "trigger",
            "detection",
            "root_cause",
            "contributing_factors",
            "timeline",
            "action_items",
            "lessons_learned",
        ],
        required_fields=["summary", "impact", "root_cause", "action_items"],
        example_content={
            "summary": "Brief description of what happened and why it matters.",
            "impact": "Quantify user impact: % of users affected, duration, revenue impact if applicable.",
            "root_cause": "The fundamental cause(s) that led to the incident.",
        },
    ),
    "pagerduty": PostmortemTemplate(
        id="pagerduty",
        name="PagerDuty",
        description="PagerDuty incident response postmortem format",
        sections=[
            "summary",
            "customer_impact",
            "timeline",
            "root_cause",
            "resolution",
            "prevention",
            "action_items",
        ],
        required_fields=["summary", "timeline", "root_cause"],
    ),
    "minimal": PostmortemTemplate(
        id="minimal",
        name="Minimal",
        description="Quick postmortem for minor incidents",
        sections=[
            "summary",
            "root_cause",
            "action_items",
        ],
        required_fields=["summary", "root_cause"],
    ),
    "detailed": PostmortemTemplate(
        id="detailed",
        name="Detailed",
        description="Comprehensive postmortem for major incidents",
        sections=[
            "summary",
            "severity",
            "impact",
            "customer_impact",
            "affected_services",
            "trigger",
            "detection",
            "timeline",
            "root_cause",
            "contributing_factors",
            "resolution",
            "things_that_went_well",
            "things_that_went_poorly",
            "lessons_learned",
            "action_items",
            "metrics",
            "appendix",
        ],
        required_fields=[
            "summary",
            "impact",
            "timeline",
            "root_cause",
            "action_items",
        ],
    ),
}


def get_postmortem_templates() -> dict[str, PostmortemTemplate]:
    """Get available postmortem templates."""
    return TEMPLATES.copy()


class PostmortemGenerator:
    """Generates postmortem documents from incident data."""

    def __init__(self, template_id: str = "google"):
        self.template = TEMPLATES.get(template_id, TEMPLATES["google"])

    async def generate(
        self,
        incident_id: str,
        incident_data: dict[str, Any],
        investigation_results: dict[str, Any] | None = None,
        auto_generate_action_items: bool = True,
    ) -> PostmortemDocument:
        """Generate postmortem from incident data."""
        import secrets

        doc_id = f"pm_{secrets.token_hex(8)}"

        title = incident_data.get("title", f"Incident {incident_id}")

        started_at = self._parse_datetime(incident_data.get("started_at"))
        detected_at = self._parse_datetime(incident_data.get("detected_at"))
        mitigated_at = self._parse_datetime(incident_data.get("mitigated_at"))
        resolved_at = self._parse_datetime(incident_data.get("resolved_at"))

        duration_minutes = 0
        ttd_minutes = 0
        ttr_minutes = 0

        if started_at and resolved_at:
            duration_minutes = int((resolved_at - started_at).total_seconds() / 60)

        if started_at and detected_at:
            ttd_minutes = int((detected_at - started_at).total_seconds() / 60)

        if detected_at and resolved_at:
            ttr_minutes = int((resolved_at - detected_at).total_seconds() / 60)

        timeline = self._build_timeline(incident_data, investigation_results)

        root_cause = incident_data.get("root_cause", "")
        if not root_cause and investigation_results:
            root_cause = investigation_results.get("root_cause_analysis", "")

        contributing_factors = incident_data.get("contributing_factors", [])
        if not contributing_factors and investigation_results:
            contributing_factors = investigation_results.get("contributing_factors", [])

        action_items: list[ActionItem] = []
        if auto_generate_action_items:
            action_items = self._generate_action_items(
                incident_data,
                investigation_results,
                root_cause,
            )

        existing_items = incident_data.get("action_items", [])
        for item in existing_items:
            action_items.append(ActionItem(
                id=item.get("id", f"ai_{secrets.token_hex(4)}"),
                title=item.get("title", ""),
                description=item.get("description", ""),
                owner=item.get("owner", ""),
                priority=ActionItemPriority(item.get("priority", "p2")),
                status=ActionItemStatus(item.get("status", "open")),
                ticket_url=item.get("ticket_url", ""),
            ))

        lessons = self._extract_lessons_learned(
            incident_data,
            investigation_results,
            timeline,
        )

        doc = PostmortemDocument(
            id=doc_id,
            incident_id=incident_id,
            title=f"Postmortem: {title}",
            severity=incident_data.get("severity", ""),
            summary=incident_data.get("summary", ""),
            impact=incident_data.get("impact", ""),
            trigger=incident_data.get("trigger", ""),
            detection=incident_data.get("detection", ""),
            root_cause=root_cause,
            contributing_factors=contributing_factors,
            timeline=timeline,
            action_items=action_items,
            lessons_learned=lessons.get("lessons", []),
            things_that_went_well=lessons.get("went_well", []),
            things_that_went_poorly=lessons.get("went_poorly", []),
            authors=incident_data.get("authors", []),
            affected_services=incident_data.get("affected_services", []),
            customer_impact=incident_data.get("customer_impact", ""),
            duration_minutes=duration_minutes,
            time_to_detect_minutes=ttd_minutes,
            time_to_resolve_minutes=ttr_minutes,
            started_at=started_at,
            detected_at=detected_at,
            mitigated_at=mitigated_at,
            resolved_at=resolved_at,
            metadata=incident_data.get("metadata", {}),
        )

        log.info("Generated postmortem %s for incident %s", doc_id, incident_id)
        return doc

    def _parse_datetime(self, value: Any) -> datetime | None:
        """Parse datetime from various formats."""
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                if "T" in value:
                    return datetime.fromisoformat(value.replace("Z", "+00:00"))
                return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(
                    tzinfo=timezone.utc
                )
            except ValueError:
                return None
        return None

    def _build_timeline(
        self,
        incident_data: dict[str, Any],
        investigation_results: dict[str, Any] | None,
    ) -> list[TimelineEvent]:
        """Build incident timeline from available data."""
        events: list[TimelineEvent] = []

        raw_timeline = incident_data.get("timeline", [])
        for item in raw_timeline:
            ts = self._parse_datetime(item.get("timestamp"))
            if ts:
                events.append(TimelineEvent(
                    timestamp=ts,
                    title=item.get("title", ""),
                    description=item.get("description", ""),
                    author=item.get("author", ""),
                    event_type=item.get("type", "info"),
                    source=item.get("source", "manual"),
                ))

        started_at = self._parse_datetime(incident_data.get("started_at"))
        if started_at:
            events.append(TimelineEvent(
                timestamp=started_at,
                title="Incident started",
                event_type="start",
            ))

        detected_at = self._parse_datetime(incident_data.get("detected_at"))
        if detected_at:
            events.append(TimelineEvent(
                timestamp=detected_at,
                title="Incident detected",
                description=incident_data.get("detection", ""),
                event_type="detection",
            ))

        mitigated_at = self._parse_datetime(incident_data.get("mitigated_at"))
        if mitigated_at:
            events.append(TimelineEvent(
                timestamp=mitigated_at,
                title="Incident mitigated",
                event_type="mitigation",
            ))

        resolved_at = self._parse_datetime(incident_data.get("resolved_at"))
        if resolved_at:
            events.append(TimelineEvent(
                timestamp=resolved_at,
                title="Incident resolved",
                event_type="resolution",
            ))

        if investigation_results:
            inv_timeline = investigation_results.get("timeline", [])
            for item in inv_timeline:
                ts = self._parse_datetime(item.get("timestamp"))
                if ts:
                    events.append(TimelineEvent(
                        timestamp=ts,
                        title=item.get("title", ""),
                        description=item.get("description", ""),
                        event_type="investigation",
                        source="copilot",
                    ))

        events.sort(key=lambda e: e.timestamp)

        return events

    def _generate_action_items(
        self,
        incident_data: dict[str, Any],
        investigation_results: dict[str, Any] | None,
        root_cause: str,
    ) -> list[ActionItem]:
        """Generate action items from incident analysis."""
        import secrets

        items: list[ActionItem] = []

        root_cause_lower = root_cause.lower()

        if any(word in root_cause_lower for word in ["config", "configuration", "setting"]):
            items.append(ActionItem(
                id=f"ai_{secrets.token_hex(4)}",
                title="Add configuration validation",
                description="Implement validation for configuration changes to prevent misconfigurations",
                priority=ActionItemPriority.P1,
                category="prevention",
            ))

        if any(word in root_cause_lower for word in ["capacity", "scale", "load", "memory", "cpu"]):
            items.append(ActionItem(
                id=f"ai_{secrets.token_hex(4)}",
                title="Review capacity planning",
                description="Review and update capacity thresholds and auto-scaling policies",
                priority=ActionItemPriority.P1,
                category="capacity",
            ))

        if any(word in root_cause_lower for word in ["deploy", "release", "rollout"]):
            items.append(ActionItem(
                id=f"ai_{secrets.token_hex(4)}",
                title="Improve deployment validation",
                description="Add pre-deployment checks and canary analysis",
                priority=ActionItemPriority.P1,
                category="deployment",
            ))

        if any(word in root_cause_lower for word in ["monitor", "alert", "detect"]):
            items.append(ActionItem(
                id=f"ai_{secrets.token_hex(4)}",
                title="Improve monitoring coverage",
                description="Add or tune alerts for earlier detection of similar issues",
                priority=ActionItemPriority.P2,
                category="observability",
            ))

        if any(word in root_cause_lower for word in ["database", "query", "connection"]):
            items.append(ActionItem(
                id=f"ai_{secrets.token_hex(4)}",
                title="Review database performance",
                description="Audit database queries and connection pool settings",
                priority=ActionItemPriority.P2,
                category="database",
            ))

        if investigation_results:
            recommendations = investigation_results.get("recommendations", [])
            for rec in recommendations[:5]:
                items.append(ActionItem(
                    id=f"ai_{secrets.token_hex(4)}",
                    title=rec.get("title", rec[:100] if isinstance(rec, str) else "Review recommendation"),
                    description=rec.get("description", rec if isinstance(rec, str) else ""),
                    priority=ActionItemPriority.P2,
                    category="recommendation",
                ))

        items.append(ActionItem(
            id=f"ai_{secrets.token_hex(4)}",
            title="Update runbook",
            description="Update or create runbook for handling similar incidents",
            priority=ActionItemPriority.P3,
            category="documentation",
        ))

        return items

    def _extract_lessons_learned(
        self,
        incident_data: dict[str, Any],
        investigation_results: dict[str, Any] | None,
        timeline: list[TimelineEvent],
    ) -> dict[str, list[str]]:
        """Extract lessons learned from incident."""
        lessons: list[str] = []
        went_well: list[str] = []
        went_poorly: list[str] = []

        if incident_data.get("lessons_learned"):
            lessons.extend(incident_data["lessons_learned"])
        if incident_data.get("things_that_went_well"):
            went_well.extend(incident_data["things_that_went_well"])
        if incident_data.get("things_that_went_poorly"):
            went_poorly.extend(incident_data["things_that_went_poorly"])

        started_at = self._parse_datetime(incident_data.get("started_at"))
        detected_at = self._parse_datetime(incident_data.get("detected_at"))
        resolved_at = self._parse_datetime(incident_data.get("resolved_at"))

        if started_at and detected_at:
            ttd = (detected_at - started_at).total_seconds() / 60
            if ttd < 5:
                went_well.append(f"Fast detection time ({int(ttd)} minutes)")
            elif ttd > 30:
                went_poorly.append(f"Slow detection time ({int(ttd)} minutes)")
                lessons.append("Improve alerting to detect this class of issues faster")

        if detected_at and resolved_at:
            ttr = (resolved_at - detected_at).total_seconds() / 60
            if ttr < 15:
                went_well.append(f"Quick resolution ({int(ttr)} minutes)")
            elif ttr > 120:
                went_poorly.append(f"Long resolution time ({int(ttr)} minutes)")
                lessons.append("Create runbook to speed up resolution of similar incidents")

        if investigation_results:
            if investigation_results.get("lessons"):
                lessons.extend(investigation_results["lessons"])

        return {
            "lessons": list(set(lessons)),
            "went_well": list(set(went_well)),
            "went_poorly": list(set(went_poorly)),
        }

    def to_markdown(self, doc: PostmortemDocument) -> str:
        """Convert postmortem to Markdown format."""
        lines = []

        lines.append(f"# {doc.title}")
        lines.append("")
        lines.append(f"**Incident ID:** {doc.incident_id}")
        lines.append(f"**Status:** {doc.status}")
        lines.append(f"**Severity:** {doc.severity}")
        lines.append(f"**Created:** {doc.created_at.strftime('%Y-%m-%d %H:%M UTC')}")
        lines.append("")

        if doc.authors:
            lines.append(f"**Authors:** {', '.join(doc.authors)}")
        if doc.affected_services:
            lines.append(f"**Affected Services:** {', '.join(doc.affected_services)}")
        lines.append("")

        lines.append("## Summary")
        lines.append(doc.summary or "_To be filled_")
        lines.append("")

        lines.append("## Impact")
        lines.append(doc.impact or "_To be filled_")
        if doc.customer_impact:
            lines.append("")
            lines.append(f"**Customer Impact:** {doc.customer_impact}")
        lines.append("")

        lines.append("## Key Metrics")
        lines.append("")
        lines.append("| Metric | Value |")
        lines.append("|--------|-------|")
        lines.append(f"| Duration | {doc.duration_minutes} minutes |")
        lines.append(f"| Time to Detect | {doc.time_to_detect_minutes} minutes |")
        lines.append(f"| Time to Resolve | {doc.time_to_resolve_minutes} minutes |")
        lines.append("")

        if doc.timeline:
            lines.append("## Timeline")
            lines.append("")
            for event in doc.timeline:
                ts = event.timestamp.strftime("%Y-%m-%d %H:%M UTC")
                lines.append(f"- **{ts}** - {event.title}")
                if event.description:
                    lines.append(f"  - {event.description}")
            lines.append("")

        lines.append("## Root Cause")
        lines.append(doc.root_cause or "_To be analyzed_")
        lines.append("")

        if doc.contributing_factors:
            lines.append("## Contributing Factors")
            for factor in doc.contributing_factors:
                lines.append(f"- {factor}")
            lines.append("")

        if doc.trigger:
            lines.append("## Trigger")
            lines.append(doc.trigger)
            lines.append("")

        if doc.detection:
            lines.append("## Detection")
            lines.append(doc.detection)
            lines.append("")

        if doc.action_items:
            lines.append("## Action Items")
            lines.append("")
            lines.append("| ID | Priority | Title | Owner | Status |")
            lines.append("|----|----------|-------|-------|--------|")
            for item in doc.action_items:
                lines.append(
                    f"| {item.id} | {item.priority.value.upper()} | "
                    f"{item.title} | {item.owner or 'TBD'} | {item.status.value} |"
                )
            lines.append("")

        if doc.lessons_learned:
            lines.append("## Lessons Learned")
            for lesson in doc.lessons_learned:
                lines.append(f"- {lesson}")
            lines.append("")

        if doc.things_that_went_well:
            lines.append("## What Went Well")
            for well_item in doc.things_that_went_well:
                lines.append(f"- ✅ {well_item}")
            lines.append("")

        if doc.things_that_went_poorly:
            lines.append("## What Went Poorly")
            for poor_item in doc.things_that_went_poorly:
                lines.append(f"- ❌ {poor_item}")
            lines.append("")

        return "\n".join(lines)


async def generate_postmortem(
    incident_id: str,
    incident_data: dict[str, Any],
    investigation_results: dict[str, Any] | None = None,
    template_id: str = "google",
) -> PostmortemDocument:
    """Generate a postmortem document."""
    generator = PostmortemGenerator(template_id)
    return await generator.generate(
        incident_id,
        incident_data,
        investigation_results,
    )
