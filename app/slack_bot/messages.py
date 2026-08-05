"""Rich Message Templates for Slack Block Kit.

Production-ready message templates matching incident.io/Rootly quality:
- Incident cards with severity, timeline, assignee
- Status update messages
- Stakeholder notifications
- Investigation summaries
- Alert correlation messages
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("omnipotent.slack_bot.messages")


class MessageType(str, Enum):
    """Types of messages."""
    INCIDENT_CREATED = "incident_created"
    INCIDENT_UPDATED = "incident_updated"
    INCIDENT_RESOLVED = "incident_resolved"
    INCIDENT_ESCALATED = "incident_escalated"
    STATUS_UPDATE = "status_update"
    STAKEHOLDER_UPDATE = "stakeholder_update"
    INVESTIGATION_STARTED = "investigation_started"
    INVESTIGATION_COMPLETE = "investigation_complete"
    ALERT_FIRED = "alert_fired"
    ALERT_CORRELATED = "alert_correlated"
    WAR_ROOM_CREATED = "war_room_created"
    SHIFT_HANDOFF = "shift_handoff"
    POSTMORTEM_READY = "postmortem_ready"


class Severity(str, Enum):
    """Incident severity levels."""
    SEV1 = "SEV1"
    SEV2 = "SEV2"
    SEV3 = "SEV3"
    SEV4 = "SEV4"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"
    P4 = "P4"


class IncidentStatus(str, Enum):
    """Incident statuses."""
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    INVESTIGATING = "investigating"
    IDENTIFIED = "identified"
    MONITORING = "monitoring"
    RESOLVED = "resolved"
    CLOSED = "closed"


@dataclass
class IncidentData:
    """Data for incident messages."""
    incident_id: str
    title: str
    severity: str = "P2"
    status: str = "open"
    description: str = ""
    services: list[str] = field(default_factory=list)
    responders: list[str] = field(default_factory=list)
    commander: str | None = None
    root_cause: str | None = None
    impact: str | None = None
    customer_impact: bool = False
    started_at: datetime | None = None
    acknowledged_at: datetime | None = None
    resolved_at: datetime | None = None
    duration_minutes: float = 0
    timeline_events: list[dict[str, str]] = field(default_factory=list)
    related_alerts: list[str] = field(default_factory=list)
    runbook_url: str | None = None
    dashboard_url: str | None = None
    confidence: float = 0.0
    evidence_count: int = 0

    @property
    def duration_str(self) -> str:
        if self.duration_minutes < 60:
            return f"{int(self.duration_minutes)}m"
        hours = int(self.duration_minutes // 60)
        mins = int(self.duration_minutes % 60)
        return f"{hours}h {mins}m"

    def to_dict(self) -> dict[str, Any]:
        return {
            "incident_id": self.incident_id,
            "title": self.title,
            "severity": self.severity,
            "status": self.status,
            "services": self.services,
            "responders": self.responders,
            "duration_minutes": self.duration_minutes,
        }


class MessageBuilder:
    """Builds rich Slack messages using Block Kit."""

    SEVERITY_EMOJI = {
        "SEV1": ":rotating_light:",
        "SEV2": ":red_circle:",
        "SEV3": ":large_orange_circle:",
        "SEV4": ":large_yellow_circle:",
        "P1": ":rotating_light:",
        "P2": ":red_circle:",
        "P3": ":large_orange_circle:",
        "P4": ":large_yellow_circle:",
    }

    SEVERITY_COLOR = {
        "SEV1": "#DC2626",
        "SEV2": "#EA580C",
        "SEV3": "#CA8A04",
        "SEV4": "#65A30D",
        "P1": "#DC2626",
        "P2": "#EA580C",
        "P3": "#CA8A04",
        "P4": "#65A30D",
    }

    STATUS_EMOJI = {
        "open": ":warning:",
        "acknowledged": ":eyes:",
        "investigating": ":mag:",
        "identified": ":dart:",
        "monitoring": ":chart_with_upwards_trend:",
        "resolved": ":white_check_mark:",
        "closed": ":heavy_check_mark:",
    }

    def __init__(self) -> None:
        self._templates: dict[MessageType, Any] = {}

    def incident_card(
        self,
        incident: IncidentData,
        show_actions: bool = True,
        show_timeline: bool = False,
        compact: bool = False,
    ) -> list[dict[str, Any]]:
        """Build a comprehensive incident card."""
        severity_emoji = self.SEVERITY_EMOJI.get(incident.severity, ":warning:")
        status_emoji = self.STATUS_EMOJI.get(incident.status, ":question:")

        blocks: list[dict[str, Any]] = []

        # Header with severity and title
        blocks.append({
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"{severity_emoji} {incident.severity}: {incident.title}",
                "emoji": True,
            },
        })

        # Status and basic info
        blocks.append({"type": "divider"})

        # Main info section with fields
        services_text = ", ".join(incident.services) if incident.services else "Unknown"
        responders_text = ", ".join(
            f"<@{r}>" for r in incident.responders
        ) if incident.responders else "None assigned"

        fields = [
            {"type": "mrkdwn", "text": f"*Status:*\n{status_emoji} {incident.status.title()}"},
            {"type": "mrkdwn", "text": f"*Severity:*\n{severity_emoji} {incident.severity}"},
            {"type": "mrkdwn", "text": f"*Services:*\n{services_text}"},
            {"type": "mrkdwn", "text": f"*Duration:*\n{incident.duration_str}"},
        ]

        blocks.append({"type": "section", "fields": fields})

        # Responders section
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Responders:* {responders_text}",
            },
        })

        # Incident commander if assigned
        if incident.commander:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f":crown: *Incident Commander:* <@{incident.commander}>",
                },
            })

        # Description if present
        if incident.description and not compact:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Description:*\n{incident.description[:500]}",
                },
            })

        # Impact section
        if incident.impact:
            impact_icon = ":rotating_light:" if incident.customer_impact else ":warning:"
            customer_text = " (Customer-facing)" if incident.customer_impact else ""
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"{impact_icon} *Impact{customer_text}:*\n{incident.impact}",
                },
            })

        # Root cause if identified
        if incident.root_cause:
            confidence_text = f" ({int(incident.confidence * 100)}% confidence)" if incident.confidence else ""
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f":dart: *Root Cause{confidence_text}:*\n{incident.root_cause}",
                },
            })

        # Timeline (last few events)
        if show_timeline and incident.timeline_events:
            timeline_text = "\n".join(
                f"*{e['time']}* - {e['event']}"
                for e in incident.timeline_events[-5:]
            )
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Recent Timeline:*\n{timeline_text}",
                },
            })

        # Quick links
        links = []
        if incident.dashboard_url:
            links.append(f"<{incident.dashboard_url}|:bar_chart: Dashboard>")
        if incident.runbook_url:
            links.append(f"<{incident.runbook_url}|:book: Runbook>")

        if links:
            blocks.append({
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": " | ".join(links)},
                ],
            })

        # Action buttons
        if show_actions:
            blocks.extend(self._build_incident_actions(incident))

        # Footer with incident ID and timestamp
        blocks.append({
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": (
                        f"Incident ID: `{incident.incident_id}` | "
                        f"Started: {incident.started_at.strftime('%Y-%m-%d %H:%M UTC') if incident.started_at else 'Unknown'}"
                    ),
                },
            ],
        })

        return blocks

    def _build_incident_actions(
        self,
        incident: IncidentData,
    ) -> list[dict[str, Any]]:
        """Build action buttons based on incident status."""
        elements: list[dict[str, Any]] = []

        if incident.status == "open":
            elements.append({
                "type": "button",
                "text": {"type": "plain_text", "text": "Acknowledge", "emoji": True},
                "action_id": f"ack_{incident.incident_id}",
                "style": "primary",
            })

        if incident.status in ("open", "acknowledged", "investigating"):
            elements.append({
                "type": "button",
                "text": {"type": "plain_text", "text": "Escalate", "emoji": True},
                "action_id": f"escalate_{incident.incident_id}",
                "style": "danger" if incident.severity in ("SEV1", "P1") else None,
            })

        if incident.status not in ("resolved", "closed"):
            elements.append({
                "type": "button",
                "text": {"type": "plain_text", "text": "Resolve", "emoji": True},
                "action_id": f"resolve_{incident.incident_id}",
            })

            elements.append({
                "type": "button",
                "text": {"type": "plain_text", "text": "Snooze", "emoji": True},
                "action_id": f"snooze_{incident.incident_id}",
            })

        # More actions overflow menu
        overflow_options = [
            {"text": {"type": "plain_text", "text": "Add Responder"}, "value": f"add_responder_{incident.incident_id}"},
            {"text": {"type": "plain_text", "text": "Update Severity"}, "value": f"severity_{incident.incident_id}"},
            {"text": {"type": "plain_text", "text": "Post Update"}, "value": f"update_{incident.incident_id}"},
            {"text": {"type": "plain_text", "text": "View Timeline"}, "value": f"timeline_{incident.incident_id}"},
            {"text": {"type": "plain_text", "text": "Create War Room"}, "value": f"warroom_{incident.incident_id}"},
        ]

        elements.append({
            "type": "overflow",
            "action_id": f"more_{incident.incident_id}",
            "options": overflow_options,
        })

        # Remove None styles (Slack API doesn't accept None)
        for elem in elements:
            if "style" in elem and elem["style"] is None:
                del elem["style"]

        return [{"type": "actions", "elements": elements[:5]}]

    def status_update(
        self,
        incident_id: str,
        update_type: str,
        message: str,
        user_id: str,
        severity: str = "P2",
        include_actions: bool = True,
    ) -> list[dict[str, Any]]:
        """Build a status update message."""
        type_config = {
            "investigating": {"emoji": ":mag:", "color": "#3B82F6"},
            "identified": {"emoji": ":dart:", "color": "#8B5CF6"},
            "monitoring": {"emoji": ":chart_with_upwards_trend:", "color": "#10B981"},
            "update": {"emoji": ":speech_balloon:", "color": "#6B7280"},
            "action": {"emoji": ":hammer_and_wrench:", "color": "#F59E0B"},
            "mitigation": {"emoji": ":shield:", "color": "#14B8A6"},
        }

        config = type_config.get(update_type, {"emoji": ":speech_balloon:", "color": "#6B7280"})
        severity_emoji = self.SEVERITY_EMOJI.get(severity, ":warning:")

        blocks: list[dict[str, Any]] = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"{config['emoji']} *Status Update* - {update_type.title()}\n\n"
                        f"{severity_emoji} *{incident_id}*\n\n"
                        f"{message}"
                    ),
                },
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": (
                            f"Posted by <@{user_id}> at "
                            f"{datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}"
                        ),
                    },
                ],
            },
        ]

        if include_actions:
            blocks.append({
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": ":thread: Reply in Thread", "emoji": True},
                        "action_id": f"thread_{incident_id}",
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": ":memo: Add Note", "emoji": True},
                        "action_id": f"note_{incident_id}",
                    },
                ],
            })

        return blocks

    def stakeholder_update(
        self,
        incident: IncidentData,
        update_message: str,
        next_update_minutes: int = 30,
        audience: str = "stakeholders",
    ) -> list[dict[str, Any]]:
        """Build a stakeholder-friendly update message."""
        severity_emoji = self.SEVERITY_EMOJI.get(incident.severity, ":warning:")
        status_emoji = self.STATUS_EMOJI.get(incident.status, ":question:")

        from datetime import timedelta
        next_update_dt = datetime.now(timezone.utc) + timedelta(minutes=next_update_minutes)
        next_update_time = next_update_dt.strftime('%H:%M UTC')

        blocks: list[dict[str, Any]] = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{severity_emoji} Incident Update: {incident.title}",
                    "emoji": True,
                },
            },
            {"type": "divider"},
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Status:*\n{status_emoji} {incident.status.title()}"},
                    {"type": "mrkdwn", "text": f"*Duration:*\n{incident.duration_str}"},
                ],
            },
        ]

        # Impact summary for stakeholders
        if incident.impact:
            customer_flag = " :rotating_light: Customer-facing" if incident.customer_impact else ""
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Current Impact{customer_flag}:*\n{incident.impact}",
                },
            })

        # Update message
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Latest Update:*\n{update_message}",
            },
        })

        # What we're doing
        if incident.status not in ("resolved", "closed"):
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*What we're doing:*\n"
                        f"Our team is actively working to resolve this issue. "
                        f"Next update expected at ~{next_update_time} (~{next_update_minutes} minutes)."
                    ),
                },
            })

        # Footer
        blocks.append({
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": (
                        f"Incident: `{incident.incident_id}` | "
                        f"Last updated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} | "
                        f"Audience: {audience}"
                    ),
                },
            ],
        })

        return blocks

    def investigation_summary(
        self,
        incident_id: str,
        root_cause: str,
        confidence: float,
        evidence: list[str],
        hypothesis_count: int,
        recommended_actions: list[str],
        related_incidents: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Build an investigation summary message."""
        confidence_pct = int(confidence * 100)
        confidence_emoji = (
            ":large_green_circle:" if confidence >= 0.8
            else ":large_yellow_circle:" if confidence >= 0.5
            else ":red_circle:"
        )

        blocks: list[dict[str, Any]] = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": ":mag: Investigation Complete",
                    "emoji": True,
                },
            },
            {"type": "divider"},
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*Root Cause Identified*\n"
                        f"{confidence_emoji} Confidence: {confidence_pct}%\n\n"
                        f":dart: {root_cause}"
                    ),
                },
            },
        ]

        # Evidence section
        if evidence:
            evidence_text = "\n".join(f"* {e}" for e in evidence[:5])
            if len(evidence) > 5:
                evidence_text += f"\n_...and {len(evidence) - 5} more_"

            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Evidence ({len(evidence)} items):*\n{evidence_text}",
                },
            })

        # Recommended actions
        if recommended_actions:
            actions_text = "\n".join(f"{i+1}. {a}" for i, a in enumerate(recommended_actions[:5]))

            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Recommended Actions:*\n{actions_text}",
                },
            })

        # Related incidents
        if related_incidents:
            related_text = "\n".join(
                f"* `{r['id']}` - {r['title']} ({int(r.get('similarity', 0) * 100)}% match)"
                for r in related_incidents[:3]
            )

            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Similar Past Incidents:*\n{related_text}",
                },
            })

        # Action buttons
        blocks.append({
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": ":hammer_and_wrench: Generate Fix PR", "emoji": True},
                    "action_id": f"fix_{incident_id}",
                    "style": "primary",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": ":arrow_backward: Rollback", "emoji": True},
                    "action_id": f"rollback_{incident_id}",
                    "style": "danger",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": ":page_facing_up: Full Report", "emoji": True},
                    "action_id": f"report_{incident_id}",
                },
            ],
        })

        # Context
        blocks.append({
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"Analyzed {hypothesis_count} hypotheses | Investigation ID: `{incident_id}`",
                },
            ],
        })

        return blocks

    def alert_correlation(
        self,
        primary_alert: dict[str, Any],
        correlated_alerts: list[dict[str, Any]],
        correlation_reason: str,
        suggested_incident_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Build an alert correlation message."""
        severity = primary_alert.get("severity", "P3")
        severity_emoji = self.SEVERITY_EMOJI.get(severity, ":warning:")

        blocks: list[dict[str, Any]] = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"{severity_emoji} *Alert Correlation Detected*\n\n"
                        f"*Primary Alert:* {primary_alert.get('title', 'Unknown')}\n"
                        f"*Correlated Alerts:* {len(correlated_alerts)}"
                    ),
                },
            },
        ]

        # List correlated alerts
        if correlated_alerts:
            alerts_text = "\n".join(
                f"* {a.get('title', 'Unknown')} ({a.get('source', 'unknown')})"
                for a in correlated_alerts[:5]
            )

            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Related Alerts:*\n{alerts_text}",
                },
            })

        # Correlation reason
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Correlation Reason:*\n{correlation_reason}",
            },
        })

        # Actions
        elements: list[dict[str, Any]] = []

        if suggested_incident_id:
            elements.append({
                "type": "button",
                "text": {"type": "plain_text", "text": "Link to Incident", "emoji": True},
                "action_id": f"link_{suggested_incident_id}",
                "style": "primary",
            })
        else:
            elements.append({
                "type": "button",
                "text": {"type": "plain_text", "text": "Create Incident", "emoji": True},
                "action_id": "create_incident",
                "style": "primary",
            })

        elements.append({
            "type": "button",
            "text": {"type": "plain_text", "text": "Investigate", "emoji": True},
            "action_id": "investigate_alerts",
        })

        elements.append({
            "type": "button",
            "text": {"type": "plain_text", "text": "Dismiss", "emoji": True},
            "action_id": "dismiss_correlation",
        })

        blocks.append({"type": "actions", "elements": elements})

        return blocks

    def war_room_welcome(
        self,
        incident: IncidentData,
        thread_links: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        """Build a war room welcome message."""
        severity_emoji = self.SEVERITY_EMOJI.get(incident.severity, ":warning:")

        blocks: list[dict[str, Any]] = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{severity_emoji} {incident.severity} INCIDENT WAR ROOM",
                    "emoji": True,
                },
            },
            {"type": "divider"},
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*{incident.title}*\n\n"
                        f"This channel was automatically created for incident response.\n"
                        f"All relevant parties have been invited."
                    ),
                },
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Incident ID:*\n`{incident.incident_id}`"},
                    {"type": "mrkdwn", "text": f"*Severity:*\n{severity_emoji} {incident.severity}"},
                    {"type": "mrkdwn", "text": f"*Status:*\n{incident.status.title()}"},
                    {"type": "mrkdwn", "text": f"*Services:*\n{', '.join(incident.services) or 'TBD'}"},
                ],
            },
        ]

        # Thread organization
        if thread_links:
            thread_text = "\n".join(
                f"* <{url}|{name}>"
                for name, url in thread_links.items()
            )
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Organized Threads:*\n{thread_text}",
                },
            })

        # Guidelines
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    "*Channel Guidelines:*\n"
                    "* Use threads for detailed discussions\n"
                    "* Post status updates in main channel\n"
                    "* Tag `@here` for critical updates only\n"
                    "* Use `/copilot` commands for actions"
                ),
            },
        })

        # Quick actions
        blocks.append({
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": ":mag: Start Investigation", "emoji": True},
                    "action_id": f"investigate_{incident.incident_id}",
                    "style": "primary",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": ":busts_in_silhouette: Add Responder", "emoji": True},
                    "action_id": f"add_responder_{incident.incident_id}",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": ":book: View Runbook", "emoji": True},
                    "action_id": f"runbook_{incident.incident_id}",
                },
            ],
        })

        return blocks

    def shift_handoff(
        self,
        from_user: str,
        to_user: str,
        active_incidents: list[IncidentData],
        notes: str,
        monitoring_items: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Build a shift handoff message."""
        blocks: list[dict[str, Any]] = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": ":arrows_counterclockwise: Shift Handoff",
                    "emoji": True,
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"<@{from_user}> -> <@{to_user}>",
                },
            },
            {"type": "divider"},
        ]

        # Active incidents
        if active_incidents:
            incidents_text = "\n".join(
                f"* {self.SEVERITY_EMOJI.get(i.severity, ':warning:')} "
                f"`{i.incident_id}` - {i.title} ({i.status})"
                for i in active_incidents
            )
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Active Incidents ({len(active_incidents)}):*\n{incidents_text}",
                },
            })
        else:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*Active Incidents:*\nNone - all clear! :white_check_mark:",
                },
            })

        # Monitoring items
        if monitoring_items:
            monitoring_text = "\n".join(f"* {item}" for item in monitoring_items)
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Items to Monitor:*\n{monitoring_text}",
                },
            })

        # Handoff notes
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Handoff Notes from <@{from_user}>:*\n{notes or 'No additional notes.'}",
            },
        })

        # Acknowledge button
        blocks.append({
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": ":white_check_mark: Acknowledge Handoff", "emoji": True},
                    "action_id": f"ack_handoff_{to_user}",
                    "style": "primary",
                },
            ],
        })

        # Timestamp
        blocks.append({
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"Handoff time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
                },
            ],
        })

        return blocks

    def postmortem_ready(
        self,
        incident: IncidentData,
        postmortem_url: str,
        key_findings: list[str],
        action_items: list[dict[str, str]],
    ) -> list[dict[str, Any]]:
        """Build a postmortem ready notification."""
        blocks: list[dict[str, Any]] = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": ":page_facing_up: Postmortem Ready for Review",
                    "emoji": True,
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*Incident:* {incident.incident_id} - {incident.title}\n"
                        f"*Duration:* {incident.duration_str}\n"
                        f"*Severity:* {incident.severity}"
                    ),
                },
            },
        ]

        # Key findings
        if key_findings:
            findings_text = "\n".join(f"* {f}" for f in key_findings[:5])
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Key Findings:*\n{findings_text}",
                },
            })

        # Action items summary
        if action_items:
            actions_text = "\n".join(
                f"* {a['title']} (Owner: <@{a.get('owner', 'TBD')}>)"
                for a in action_items[:5]
            )
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Action Items ({len(action_items)}):*\n{actions_text}",
                },
            })

        # Link and actions
        blocks.append({
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": ":page_facing_up: View Postmortem", "emoji": True},
                    "action_id": f"view_postmortem_{incident.incident_id}",
                    "url": postmortem_url,
                    "style": "primary",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": ":pencil2: Edit", "emoji": True},
                    "action_id": f"edit_postmortem_{incident.incident_id}",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": ":mega: Share", "emoji": True},
                    "action_id": f"share_postmortem_{incident.incident_id}",
                },
            ],
        })

        return blocks

    def compact_alert(
        self,
        alert_id: str,
        title: str,
        severity: str,
        source: str,
        timestamp: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Build a compact single-line alert message."""
        severity_emoji = self.SEVERITY_EMOJI.get(severity, ":warning:")
        time_str = (timestamp or datetime.now(timezone.utc)).strftime("%H:%M")

        return [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"{severity_emoji} `{time_str}` *{title}* _via {source}_",
                },
                "accessory": {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Investigate"},
                    "action_id": f"investigate_{alert_id}",
                },
            },
        ]


_builder: MessageBuilder | None = None


def get_message_builder() -> MessageBuilder:
    """Get the default message builder."""
    global _builder
    if _builder is None:
        _builder = MessageBuilder()
    return _builder
