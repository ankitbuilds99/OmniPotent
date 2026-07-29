"""Slack Block Kit builders.

Advanced interactive components for investigations,
approvals, metrics, and more.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger("devcopilot.slack_bot.blocks")


@dataclass
class InvestigationCard:
    """Investigation card data."""

    incident_id: str
    title: str
    status: str
    duration: str
    severity: str
    responders: list[str]
    services: list[str]
    root_cause: str | None
    confidence: float
    evidence_count: int
    suggested_actions: list[dict[str, str]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "incident_id": self.incident_id,
            "title": self.title,
            "status": self.status,
            "severity": self.severity,
            "root_cause": self.root_cause,
            "confidence": self.confidence,
        }


@dataclass
class ApprovalCard:
    """Approval card data."""

    approval_id: str
    action_type: str
    description: str
    details: dict[str, Any]
    risk_level: str
    requested_by: str
    approvers: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "approval_id": self.approval_id,
            "action_type": self.action_type,
            "risk_level": self.risk_level,
            "requested_by": self.requested_by,
        }


@dataclass
class MetricsCard:
    """Metrics card data."""

    service: str
    metrics: dict[str, dict[str, Any]]
    timerange: str = "1h"

    def to_dict(self) -> dict[str, Any]:
        return {
            "service": self.service,
            "metrics": self.metrics,
            "timerange": self.timerange,
        }


@dataclass
class TimelineCard:
    """Timeline card data."""

    incident_id: str
    events: list[dict[str, str]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "incident_id": self.incident_id,
            "event_count": len(self.events),
        }


class BlockBuilder:
    """Builds Slack Block Kit components."""

    def __init__(self) -> None:
        self._severity_emoji = {
            "P1": ":red_circle:",
            "P2": ":large_orange_circle:",
            "P3": ":large_yellow_circle:",
            "P4": ":white_circle:",
        }

        self._risk_emoji = {
            "low": ":large_green_circle:",
            "medium": ":large_yellow_circle:",
            "high": ":large_orange_circle:",
            "critical": ":red_circle:",
        }

        self._trend_emoji = {
            "up": ":arrow_up:",
            "down": ":arrow_down:",
            "stable": ":left_right_arrow:",
        }

    def investigation_card(
        self,
        incident_id: str,
        title: str,
        status: str,
        duration: str,
        severity: str,
        responders: list[str],
        services: list[str],
        root_cause: str | None = None,
        confidence: float = 0.0,
        evidence_count: int = 0,
        suggested_actions: list[dict[str, str]] | None = None,
    ) -> list[dict[str, Any]]:
        """Build an investigation card."""
        severity_emoji = self._severity_emoji.get(severity, ":white_circle:")

        responders_text = ", ".join(f"<@{r}>" for r in responders) if responders else "None"
        services_text = ", ".join(services) if services else "Unknown"

        blocks: list[dict[str, Any]] = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{severity_emoji} {severity} INCIDENT: {title}",
                },
            },
            {"type": "divider"},
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Status:* {status}"},
                    {"type": "mrkdwn", "text": f"*Duration:* {duration}"},
                    {"type": "mrkdwn", "text": f"*Severity:* {severity}"},
                    {"type": "mrkdwn", "text": f"*Responders:* {responders_text}"},
                ],
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Affected:* {services_text}",
                },
            },
        ]

        if root_cause:
            confidence_pct = int(confidence * 100)

            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*AI Analysis*\n"
                        f"Root Cause ({confidence_pct}% confidence):\n"
                        f"_{root_cause}_\n\n"
                        f"Evidence: {evidence_count} logs, metrics, traces analyzed"
                    ),
                },
            })

        if suggested_actions:
            action_buttons = []

            for action in suggested_actions[:4]:
                style = "danger" if action.get("risk") == "high" else "primary"

                action_buttons.append({
                    "type": "button",
                    "text": {"type": "plain_text", "text": action["label"]},
                    "action_id": action["action_id"],
                    "style": style,
                })

            blocks.append({
                "type": "actions",
                "elements": action_buttons,
            })

        blocks.append({
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"Incident ID: {incident_id} | Last updated: {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}",
                },
            ],
        })

        return blocks

    def approval_card(
        self,
        approval_id: str,
        action_type: str,
        description: str,
        details: dict[str, Any],
        risk_level: str,
        requested_by: str,
        approvers: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Build an approval card."""
        risk_emoji = self._risk_emoji.get(risk_level, ":white_circle:")

        details_text = "\n".join(
            f"*{k}:* {v}" for k, v in details.items()
        )

        approvers_text = ""
        if approvers:
            approvers_list = ", ".join(f"<@{a}>" for a in approvers)
            approvers_text = f"\n*Approvers needed:* 1 of [{approvers_list}]"

        blocks: list[dict[str, Any]] = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": "ACTION REQUIRES APPROVAL",
                },
            },
            {"type": "divider"},
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*Action:* {action_type}\n"
                        f"*Description:* {description}\n\n"
                        f"{details_text}"
                    ),
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*Risk Assessment:* {risk_emoji} {risk_level.title()}\n"
                        f"*Requested by:* <@{requested_by}>"
                        f"{approvers_text}"
                    ),
                },
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Approve"},
                        "action_id": f"approve_{approval_id}",
                        "style": "primary",
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Reject"},
                        "action_id": f"reject_{approval_id}",
                        "style": "danger",
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "View Details"},
                        "action_id": f"details_{approval_id}",
                    },
                ],
            },
        ]

        return blocks

    def metrics_card(
        self,
        service: str,
        metrics: dict[str, dict[str, Any]],
        timerange: str = "1h",
    ) -> list[dict[str, Any]]:
        """Build a metrics card."""
        metrics_fields = []

        for name, data in metrics.items():
            value = data.get("value", "N/A")
            trend = data.get("trend", "stable")
            change = data.get("change", "")

            trend_emoji = self._trend_emoji.get(trend, "")
            name_formatted = name.replace("_", " ").title()

            change_text = f" ({change})" if change else ""

            metrics_fields.append({
                "type": "mrkdwn",
                "text": f"*{name_formatted}:*\n{value} {trend_emoji}{change_text}",
            })

        blocks: list[dict[str, Any]] = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{service} Metrics",
                },
            },
            {
                "type": "section",
                "fields": metrics_fields[:8],
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"Timerange: {timerange} | Updated: {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}",
                    },
                ],
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Expand Dashboard"},
                        "action_id": f"dashboard_{service}",
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Set Alert"},
                        "action_id": f"alert_{service}",
                    },
                ],
            },
        ]

        return blocks

    def timeline_card(
        self,
        incident_id: str,
        events: list[dict[str, str]],
    ) -> list[dict[str, Any]]:
        """Build a timeline card."""
        timeline_text = "\n".join(
            f"*{e['time']}* - {e['event']}" for e in events[-10:]
        )

        blocks: list[dict[str, Any]] = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"Timeline - {incident_id}",
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": timeline_text,
                },
            },
        ]

        if len(events) > 10:
            blocks.append({
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"_Showing last 10 of {len(events)} events_",
                    },
                ],
            })

        return blocks

    def status_card(
        self,
        incident_id: str,
        status: str,
        duration: str,
        responders: list[str],
        services: list[str],
        root_cause: str | None = None,
        confidence: float = 0.0,
    ) -> list[dict[str, Any]]:
        """Build a status card."""
        responders_text = ", ".join(f"<@{r}>" for r in responders)
        services_text = ", ".join(services)

        status_text = (
            f"*Status:* {status}\n"
            f"*Duration:* {duration}\n"
            f"*Responders:* {responders_text}\n"
            f"*Affected Services:* {services_text}"
        )

        if root_cause:
            status_text += f"\n\n*Root Cause ({int(confidence * 100)}% confidence):*\n_{root_cause}_"

        blocks: list[dict[str, Any]] = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": status_text,
                },
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "View Timeline"},
                        "action_id": f"timeline_{incident_id}",
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Open Dashboard"},
                        "action_id": f"dashboard_{incident_id}",
                    },
                ],
            },
        ]

        return blocks

    def investigation_started(
        self,
        query: str,
        user_id: str,
    ) -> list[dict[str, Any]]:
        """Build investigation started card."""
        return [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"Starting AI-powered investigation...\n\n"
                        f"*Query:* {query}\n"
                        f"*Requested by:* <@{user_id}>"
                    ),
                },
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": ":mag: Analyzing logs, metrics, and traces...",
                    },
                ],
            },
        ]

    def investigation_progress(
        self,
        incident_id: str,
        status: str,
        steps_completed: int,
        total_steps: int,
    ) -> list[dict[str, Any]]:
        """Build investigation progress card."""
        progress_bar = self._build_progress_bar(steps_completed, total_steps)

        return [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Investigation in Progress*\n{status}",
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"Progress: {progress_bar} {steps_completed}/{total_steps}",
                },
            },
        ]

    def _build_progress_bar(self, completed: int, total: int) -> str:
        """Build an ASCII progress bar."""
        filled = int((completed / total) * 10) if total > 0 else 0
        empty = 10 - filled

        return "[" + "=" * filled + " " * empty + "]"

    def investigation_result(
        self,
        incident_id: str,
        root_cause: str,
        confidence: float,
        evidence: list[str],
        code_snippet: str | None = None,
        file_path: str | None = None,
        line_number: int | None = None,
    ) -> list[dict[str, Any]]:
        """Build investigation result card."""
        confidence_pct = int(confidence * 100)

        evidence_text = "\n".join(f"- {e}" for e in evidence[:5])

        blocks: list[dict[str, Any]] = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": "Root Cause Identified",
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*Root Cause ({confidence_pct}% confidence):*\n"
                        f"{root_cause}\n\n"
                        f"*Evidence:*\n{evidence_text}"
                    ),
                },
            },
        ]

        if code_snippet and file_path:
            location = f"{file_path}:{line_number}" if line_number else file_path

            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Location:* `{location}`\n```{code_snippet}```",
                },
            })

        blocks.append({
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Generate Fix PR"},
                    "action_id": f"fix_{incident_id}",
                    "style": "primary",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Rollback Deploy"},
                    "action_id": f"rollback_{incident_id}",
                    "style": "danger",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "View Full Evidence"},
                    "action_id": f"evidence_{incident_id}",
                },
            ],
        })

        return blocks

    def incident_created(
        self,
        incident_id: str,
        title: str,
        severity: str,
        created_by: str,
    ) -> list[dict[str, Any]]:
        """Build incident created card."""
        severity_emoji = self._severity_emoji.get(severity, ":white_circle:")

        return [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"{severity_emoji} *New Incident Created*\n\n"
                        f"*ID:* {incident_id}\n"
                        f"*Title:* {title}\n"
                        f"*Severity:* {severity}\n"
                        f"*Created by:* <@{created_by}>"
                    ),
                },
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Start Investigation"},
                        "action_id": f"investigate_{incident_id}",
                        "style": "primary",
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Open War Room"},
                        "action_id": f"warroom_{incident_id}",
                    },
                ],
            },
        ]

    def service_analysis(
        self,
        service: str,
        health: str,
        metrics: dict[str, str],
        recent_deploys: list[dict[str, str]],
    ) -> list[dict[str, Any]]:
        """Build service analysis card."""
        health_emoji = {
            "healthy": ":large_green_circle:",
            "degraded": ":large_yellow_circle:",
            "critical": ":red_circle:",
        }

        emoji = health_emoji.get(health, ":white_circle:")

        metrics_text = "\n".join(f"*{k}:* {v}" for k, v in metrics.items())

        deploys_text = "\n".join(
            f"- {d['version']} at {d['time']} by @{d['author']}"
            for d in recent_deploys[:3]
        )

        return [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"Service Analysis: {service}",
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"{emoji} *Health:* {health.title()}",
                },
            },
            {
                "type": "section",
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": f"*Current Metrics:*\n{metrics_text}",
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Recent Deploys:*\n{deploys_text}",
                    },
                ],
            },
        ]

    def similar_incidents(
        self,
        incidents: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Build similar incidents card."""
        incidents_text = ""

        for inc in incidents[:5]:
            similarity = int(inc.get("similarity", 0) * 100)

            incidents_text += (
                f"*{inc['id']}* ({similarity}% match)\n"
                f"{inc['title']}\n"
                f"_Resolved: {inc.get('resolution', 'N/A')}_\n\n"
            )

        return [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": "Similar Past Incidents",
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": incidents_text,
                },
            },
        ]

    def handoff_card(
        self,
        from_user: str,
        to_user: str,
        active_incidents: list[dict[str, str]],
        monitoring: list[str],
        notes: str,
    ) -> list[dict[str, Any]]:
        """Build shift handoff card."""
        incidents_text = "\n".join(
            f"- *{inc['id']}*: {inc['title']} ({inc['status']})"
            for inc in active_incidents
        )

        monitoring_text = "\n".join(f"- {m}" for m in monitoring)

        return [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"Shift Handoff: <@{from_user}> -> <@{to_user}>",
                },
            },
            {"type": "divider"},
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Active Incidents:*\n{incidents_text or '_None_'}",
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Monitoring:*\n{monitoring_text or '_Nothing specific_'}",
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Notes from <@{from_user}>:*\n_{notes}_",
                },
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Acknowledge Handoff"},
                        "action_id": f"ack_handoff_{to_user}",
                        "style": "primary",
                    },
                ],
            },
        ]

    def suggest_responders(
        self,
        primary: list[dict[str, str]],
        escalation: list[dict[str, str]],
        historical: list[dict[str, str]],
    ) -> list[dict[str, Any]]:
        """Build responder suggestion card."""
        primary_text = "\n".join(
            f"- <@{r['user']}> - {r['reason']}" for r in primary
        )

        escalation_text = "\n".join(
            f"- <@{r['user']}> - {r['reason']}" for r in escalation
        )

        historical_text = "\n".join(
            f"- <@{r['user']}> - {r['reason']}" for r in historical
        )

        action_elements: list[dict[str, Any]] = []
        for r in primary[:2]:
            action_elements.append({
                "type": "button",
                "text": {"type": "plain_text", "text": f"Page @{r['user']}"},
                "action_id": f"page_{r['user']}",
            })

        if len(primary) == 2:
            action_elements.append({
                "type": "button",
                "text": {"type": "plain_text", "text": "Page Both"},
                "action_id": "page_both",
                "style": "primary",
            })

        blocks: list[dict[str, Any]] = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": "Suggested Responders",
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Primary:*\n{primary_text}",
                },
            },
        ]

        if escalation:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Escalation:*\n{escalation_text}",
                },
            })

        if historical:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Historical (resolved similar):*\n{historical_text}",
                },
            })

        if action_elements:
            blocks.append({
                "type": "actions",
                "elements": action_elements,
            })

        return blocks

    def proactive_alert(
        self,
        title: str,
        description: str,
        metrics: dict[str, str],
        correlation: str | None = None,
        similar_incident: str | None = None,
        oncall: str | None = None,
    ) -> list[dict[str, Any]]:
        """Build proactive alert card."""
        metrics_text = "\n".join(f"- {k}: {v}" for k, v in metrics.items())

        context_parts = []
        if correlation:
            context_parts.append(f"Correlates with {correlation}")
        if similar_incident:
            context_parts.append(f"Similar to {similar_incident}")
        if oncall:
            context_parts.append(f"On-call: <@{oncall}>")

        context_text = " | ".join(context_parts)

        return [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f":warning: *{title}*\n\n"
                        f"{description}\n\n"
                        f"*Quick context:*\n{metrics_text}"
                    ),
                },
            },
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": context_text},
                ],
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Start Investigation"},
                        "action_id": "investigate_alert",
                        "style": "primary",
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "View Deploy"},
                        "action_id": "view_deploy",
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "See Similar"},
                        "action_id": "see_similar",
                    },
                ],
            },
        ]

    def predictive_warning(
        self,
        service: str,
        metric: str,
        current_value: str,
        predicted_threshold: str,
        eta: str,
        pattern_match: str | None = None,
        suggested_actions: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Build predictive warning card."""
        warning_text = (
            f":warning: *Heads up* - seeing early warning signs:\n\n"
            f"*{service}* {metric}: {current_value}\n"
            f"At this rate, will hit {predicted_threshold} in ~{eta}"
        )

        if pattern_match:
            warning_text += f"\nPattern matches pre-incident behavior from {pattern_match}"

        blocks: list[dict[str, Any]] = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": warning_text,
                },
            },
        ]

        if suggested_actions:
            actions_text = "\n".join(
                f"{i+1}. {action}" for i, action in enumerate(suggested_actions)
            )

            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Suggested preventive actions:*\n{actions_text}",
                },
            })

        blocks.append({
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Investigate Now"},
                    "action_id": f"investigate_{service}",
                    "style": "primary",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Schedule for Later"},
                    "action_id": f"schedule_{service}",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Snooze 2h"},
                    "action_id": f"snooze_{service}",
                },
            ],
        })

        return blocks

    def rollback_complete(
        self,
        service: str,
        from_version: str,
        to_version: str,
        verification_results: dict[str, dict[str, str]],
    ) -> list[dict[str, Any]]:
        """Build rollback complete card."""
        results_text = ""

        for metric, data in verification_results.items():
            before = data.get("before", "N/A")
            after = data.get("after", "N/A")
            status = ":white_check_mark:" if data.get("ok") else ":x:"

            results_text += f"{status} {metric}: {before} -> {after}\n"

        return [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f":white_check_mark: *Rollback Complete*\n\n"
                        f"*Service:* {service}\n"
                        f"*Version:* {from_version} -> {to_version}\n\n"
                        f"*Verification Results:*\n{results_text}"
                    ),
                },
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Mark as Resolved"},
                        "action_id": "resolve_incident",
                        "style": "primary",
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Continue Monitoring"},
                        "action_id": "continue_monitoring",
                    },
                ],
            },
        ]


_builder: BlockBuilder | None = None


def get_block_builder() -> BlockBuilder:
    """Get the default block builder."""
    global _builder
    if _builder is None:
        _builder = BlockBuilder()
    return _builder
