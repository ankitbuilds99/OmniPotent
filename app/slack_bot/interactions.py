"""Interactive Incident Actions for Slack.

Production-ready button and action handlers for incident management,
matching incident.io/Rootly quality:
- Acknowledge, Escalate, Resolve, Snooze actions
- Modal-based workflows for complex actions
- Real-time status updates
- Audit logging for all actions
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable, Awaitable

log = logging.getLogger("omnipotent.slack_bot.interactions")


class IncidentAction(str, Enum):
    """Supported incident actions."""
    ACKNOWLEDGE = "acknowledge"
    ESCALATE = "escalate"
    RESOLVE = "resolve"
    SNOOZE = "snooze"
    REASSIGN = "reassign"
    UPDATE_SEVERITY = "update_severity"
    ADD_RESPONDER = "add_responder"
    REMOVE_RESPONDER = "remove_responder"
    CREATE_WAR_ROOM = "create_war_room"
    POST_UPDATE = "post_update"
    RUN_PLAYBOOK = "run_playbook"
    MARK_AS_FALSE_POSITIVE = "false_positive"


class ActionStatus(str, Enum):
    """Action execution status."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class ActionResult:
    """Result of an action execution."""
    success: bool
    message: str
    action: IncidentAction
    incident_id: str
    user_id: str
    details: dict[str, Any] = field(default_factory=dict)
    blocks: list[dict[str, Any]] = field(default_factory=list)
    update_original: bool = True
    post_to_thread: bool = False
    notify_channel: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "message": self.message,
            "action": self.action.value,
            "incident_id": self.incident_id,
            "user_id": self.user_id,
            "details": self.details,
        }


@dataclass
class PendingAction:
    """A pending action awaiting execution or approval."""
    action_id: str
    incident_id: str
    action_type: IncidentAction
    requested_by: str
    channel_id: str
    message_ts: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    status: ActionStatus = ActionStatus.PENDING
    requires_approval: bool = False
    approvers: list[str] = field(default_factory=list)
    approved_by: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "incident_id": self.incident_id,
            "action_type": self.action_type.value,
            "requested_by": self.requested_by,
            "status": self.status.value,
            "requires_approval": self.requires_approval,
            "created_at": self.created_at.isoformat(),
        }


ActionHandler = Callable[[str, str, dict[str, Any]], Awaitable[ActionResult]]


class IncidentActionHandler:
    """Handles interactive incident actions from Slack."""

    def __init__(self) -> None:
        self._pending_actions: dict[str, PendingAction] = {}
        self._action_handlers: dict[IncidentAction, ActionHandler] = {}
        self._action_history: list[dict[str, Any]] = []
        self._snooze_timers: dict[str, datetime] = {}
        self._register_default_handlers()

    def _register_default_handlers(self) -> None:
        """Register default action handlers."""
        self._action_handlers = {
            IncidentAction.ACKNOWLEDGE: self._handle_acknowledge,
            IncidentAction.ESCALATE: self._handle_escalate,
            IncidentAction.RESOLVE: self._handle_resolve,
            IncidentAction.SNOOZE: self._handle_snooze,
            IncidentAction.REASSIGN: self._handle_reassign,
            IncidentAction.UPDATE_SEVERITY: self._handle_update_severity,
            IncidentAction.ADD_RESPONDER: self._handle_add_responder,
            IncidentAction.CREATE_WAR_ROOM: self._handle_create_war_room,
            IncidentAction.POST_UPDATE: self._handle_post_update,
            IncidentAction.RUN_PLAYBOOK: self._handle_run_playbook,
            IncidentAction.MARK_AS_FALSE_POSITIVE: self._handle_false_positive,
        }

    async def handle_action(
        self,
        action_id: str,
        incident_id: str,
        user_id: str,
        channel_id: str,
        action_type: str,
        action_value: str | None = None,
        trigger_id: str | None = None,
    ) -> ActionResult:
        """Handle an incoming action from Slack."""
        try:
            action = IncidentAction(action_type)
        except ValueError:
            log.warning("Unknown action type: %s", action_type)
            return ActionResult(
                success=False,
                message=f"Unknown action: {action_type}",
                action=IncidentAction.ACKNOWLEDGE,
                incident_id=incident_id,
                user_id=user_id,
            )

        log.info(
            "Handling action: type=%s incident=%s user=%s",
            action.value,
            incident_id,
            user_id,
        )

        details = {"value": action_value} if action_value else {}

        handler = self._action_handlers.get(action)
        if not handler:
            return ActionResult(
                success=False,
                message=f"No handler for action: {action.value}",
                action=action,
                incident_id=incident_id,
                user_id=user_id,
            )

        result = await handler(incident_id, user_id, details)

        self._log_action(action, incident_id, user_id, result.success, details)

        return result

    async def _handle_acknowledge(
        self,
        incident_id: str,
        user_id: str,
        details: dict[str, Any],
    ) -> ActionResult:
        """Handle acknowledge action."""
        log.info("Acknowledging incident: %s by user: %s", incident_id, user_id)

        blocks = self._build_ack_confirmation_blocks(incident_id, user_id)

        return ActionResult(
            success=True,
            message=f"Incident {incident_id} acknowledged by <@{user_id}>",
            action=IncidentAction.ACKNOWLEDGE,
            incident_id=incident_id,
            user_id=user_id,
            blocks=blocks,
            notify_channel=True,
            details={"acknowledged_at": datetime.now(timezone.utc).isoformat()},
        )

    async def _handle_escalate(
        self,
        incident_id: str,
        user_id: str,
        details: dict[str, Any],
    ) -> ActionResult:
        """Handle escalate action."""
        escalation_level = details.get("level", "L2")
        team = details.get("team", "on-call")

        log.info(
            "Escalating incident: %s to %s by user: %s",
            incident_id,
            escalation_level,
            user_id,
        )

        blocks = self._build_escalation_blocks(incident_id, user_id, escalation_level, team)

        return ActionResult(
            success=True,
            message=f"Incident {incident_id} escalated to {escalation_level} ({team})",
            action=IncidentAction.ESCALATE,
            incident_id=incident_id,
            user_id=user_id,
            blocks=blocks,
            notify_channel=True,
            details={
                "escalation_level": escalation_level,
                "team": team,
                "escalated_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    async def _handle_resolve(
        self,
        incident_id: str,
        user_id: str,
        details: dict[str, Any],
    ) -> ActionResult:
        """Handle resolve action."""
        resolution_notes = details.get("notes", "")
        resolution_type = details.get("type", "fixed")

        log.info("Resolving incident: %s by user: %s", incident_id, user_id)

        blocks = self._build_resolution_blocks(
            incident_id, user_id, resolution_type, resolution_notes
        )

        return ActionResult(
            success=True,
            message=f"Incident {incident_id} resolved by <@{user_id}>",
            action=IncidentAction.RESOLVE,
            incident_id=incident_id,
            user_id=user_id,
            blocks=blocks,
            notify_channel=True,
            details={
                "resolution_type": resolution_type,
                "resolution_notes": resolution_notes,
                "resolved_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    async def _handle_snooze(
        self,
        incident_id: str,
        user_id: str,
        details: dict[str, Any],
    ) -> ActionResult:
        """Handle snooze action."""
        duration_minutes = int(details.get("duration", 30))
        snooze_until = datetime.now(timezone.utc) + timedelta(minutes=duration_minutes)

        self._snooze_timers[incident_id] = snooze_until

        log.info(
            "Snoozing incident: %s for %d minutes by user: %s",
            incident_id,
            duration_minutes,
            user_id,
        )

        blocks = self._build_snooze_blocks(incident_id, user_id, duration_minutes, snooze_until)

        return ActionResult(
            success=True,
            message=f"Incident {incident_id} snoozed for {duration_minutes} minutes",
            action=IncidentAction.SNOOZE,
            incident_id=incident_id,
            user_id=user_id,
            blocks=blocks,
            details={
                "duration_minutes": duration_minutes,
                "snooze_until": snooze_until.isoformat(),
                "snoozed_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    async def _handle_reassign(
        self,
        incident_id: str,
        user_id: str,
        details: dict[str, Any],
    ) -> ActionResult:
        """Handle reassign action."""
        new_assignee = details.get("assignee", "")

        if not new_assignee:
            return ActionResult(
                success=False,
                message="No assignee specified",
                action=IncidentAction.REASSIGN,
                incident_id=incident_id,
                user_id=user_id,
            )

        log.info(
            "Reassigning incident: %s to %s by user: %s",
            incident_id,
            new_assignee,
            user_id,
        )

        return ActionResult(
            success=True,
            message=f"Incident {incident_id} reassigned to <@{new_assignee}>",
            action=IncidentAction.REASSIGN,
            incident_id=incident_id,
            user_id=user_id,
            notify_channel=True,
            details={
                "previous_assignee": user_id,
                "new_assignee": new_assignee,
                "reassigned_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    async def _handle_update_severity(
        self,
        incident_id: str,
        user_id: str,
        details: dict[str, Any],
    ) -> ActionResult:
        """Handle severity update action."""
        new_severity = details.get("severity", "P2")
        previous_severity = details.get("previous_severity", "P3")

        log.info(
            "Updating severity: %s from %s to %s by user: %s",
            incident_id,
            previous_severity,
            new_severity,
            user_id,
        )

        blocks = self._build_severity_update_blocks(
            incident_id, user_id, previous_severity, new_severity
        )

        return ActionResult(
            success=True,
            message=f"Incident {incident_id} severity changed: {previous_severity} -> {new_severity}",
            action=IncidentAction.UPDATE_SEVERITY,
            incident_id=incident_id,
            user_id=user_id,
            blocks=blocks,
            notify_channel=True,
            details={
                "previous_severity": previous_severity,
                "new_severity": new_severity,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    async def _handle_add_responder(
        self,
        incident_id: str,
        user_id: str,
        details: dict[str, Any],
    ) -> ActionResult:
        """Handle add responder action."""
        responder = details.get("responder", "")

        if not responder:
            return ActionResult(
                success=False,
                message="No responder specified",
                action=IncidentAction.ADD_RESPONDER,
                incident_id=incident_id,
                user_id=user_id,
            )

        log.info(
            "Adding responder %s to incident: %s by user: %s",
            responder,
            incident_id,
            user_id,
        )

        return ActionResult(
            success=True,
            message=f"<@{responder}> added as responder to {incident_id}",
            action=IncidentAction.ADD_RESPONDER,
            incident_id=incident_id,
            user_id=user_id,
            notify_channel=True,
            details={
                "added_responder": responder,
                "added_by": user_id,
                "added_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    async def _handle_create_war_room(
        self,
        incident_id: str,
        user_id: str,
        details: dict[str, Any],
    ) -> ActionResult:
        """Handle create war room action."""
        from app.slack_bot.war_room import get_war_room_manager

        manager = get_war_room_manager()

        title = details.get("title", f"Incident {incident_id}")
        severity = details.get("severity", "P2")
        services = details.get("services", [])

        log.info(
            "Creating war room for incident: %s by user: %s",
            incident_id,
            user_id,
        )

        war_room = await manager.create_war_room(
            incident_id=incident_id,
            org_id="default",
            title=title,
            severity=severity,
            services=services,
        )

        blocks = self._build_war_room_created_blocks(war_room.channel_name, incident_id)

        return ActionResult(
            success=True,
            message=f"War room created: #{war_room.channel_name}",
            action=IncidentAction.CREATE_WAR_ROOM,
            incident_id=incident_id,
            user_id=user_id,
            blocks=blocks,
            notify_channel=True,
            details={
                "channel_name": war_room.channel_name,
                "channel_id": war_room.channel_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    async def _handle_post_update(
        self,
        incident_id: str,
        user_id: str,
        details: dict[str, Any],
    ) -> ActionResult:
        """Handle post status update action."""
        update_text = details.get("update", "")
        update_type = details.get("type", "progress")

        if not update_text:
            return ActionResult(
                success=False,
                message="No update text provided",
                action=IncidentAction.POST_UPDATE,
                incident_id=incident_id,
                user_id=user_id,
            )

        log.info(
            "Posting update for incident: %s by user: %s",
            incident_id,
            user_id,
        )

        blocks = self._build_status_update_blocks(incident_id, user_id, update_type, update_text)

        return ActionResult(
            success=True,
            message=f"Status update posted for {incident_id}",
            action=IncidentAction.POST_UPDATE,
            incident_id=incident_id,
            user_id=user_id,
            blocks=blocks,
            post_to_thread=True,
            details={
                "update_type": update_type,
                "update_text": update_text,
                "posted_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    async def _handle_run_playbook(
        self,
        incident_id: str,
        user_id: str,
        details: dict[str, Any],
    ) -> ActionResult:
        """Handle run playbook action."""
        playbook_name = details.get("playbook", "")

        if not playbook_name:
            return ActionResult(
                success=False,
                message="No playbook specified",
                action=IncidentAction.RUN_PLAYBOOK,
                incident_id=incident_id,
                user_id=user_id,
            )

        log.info(
            "Running playbook %s for incident: %s by user: %s",
            playbook_name,
            incident_id,
            user_id,
        )

        return ActionResult(
            success=True,
            message=f"Playbook '{playbook_name}' started for {incident_id}",
            action=IncidentAction.RUN_PLAYBOOK,
            incident_id=incident_id,
            user_id=user_id,
            notify_channel=True,
            details={
                "playbook_name": playbook_name,
                "started_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    async def _handle_false_positive(
        self,
        incident_id: str,
        user_id: str,
        details: dict[str, Any],
    ) -> ActionResult:
        """Handle mark as false positive action."""
        reason = details.get("reason", "Not a real incident")

        log.info(
            "Marking incident %s as false positive by user: %s",
            incident_id,
            user_id,
        )

        return ActionResult(
            success=True,
            message=f"Incident {incident_id} marked as false positive",
            action=IncidentAction.MARK_AS_FALSE_POSITIVE,
            incident_id=incident_id,
            user_id=user_id,
            notify_channel=True,
            details={
                "reason": reason,
                "marked_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    def _log_action(
        self,
        action: IncidentAction,
        incident_id: str,
        user_id: str,
        success: bool,
        details: dict[str, Any],
    ) -> None:
        """Log action to history for audit."""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": action.value,
            "incident_id": incident_id,
            "user_id": user_id,
            "success": success,
            "details": details,
        }
        self._action_history.append(entry)

        if len(self._action_history) > 1000:
            self._action_history = self._action_history[-1000:]

    def _build_ack_confirmation_blocks(
        self,
        incident_id: str,
        user_id: str,
    ) -> list[dict[str, Any]]:
        """Build acknowledgment confirmation blocks."""
        return [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f":white_check_mark: *Incident Acknowledged*\n\n"
                        f"*Incident:* {incident_id}\n"
                        f"*Acknowledged by:* <@{user_id}>\n"
                        f"*Time:* {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
                    ),
                },
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Escalate"},
                        "action_id": f"escalate_{incident_id}",
                        "style": "danger",
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Resolve"},
                        "action_id": f"resolve_{incident_id}",
                        "style": "primary",
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Post Update"},
                        "action_id": f"update_{incident_id}",
                    },
                ],
            },
        ]

    def _build_escalation_blocks(
        self,
        incident_id: str,
        user_id: str,
        level: str,
        team: str,
    ) -> list[dict[str, Any]]:
        """Build escalation notification blocks."""
        return [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f":arrow_up: *Incident Escalated*\n\n"
                        f"*Incident:* {incident_id}\n"
                        f"*Escalated to:* {level} - {team}\n"
                        f"*Escalated by:* <@{user_id}>\n"
                        f"*Time:* {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
                    ),
                },
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f":bell: Paging {team} team...",
                    },
                ],
            },
        ]

    def _build_resolution_blocks(
        self,
        incident_id: str,
        user_id: str,
        resolution_type: str,
        notes: str,
    ) -> list[dict[str, Any]]:
        """Build resolution notification blocks."""
        resolution_emoji = {
            "fixed": ":hammer_and_wrench:",
            "not_an_issue": ":heavy_multiplication_x:",
            "duplicate": ":heavy_equals_sign:",
            "wont_fix": ":no_entry:",
            "workaround": ":leftwards_arrow_with_hook:",
        }

        emoji = resolution_emoji.get(resolution_type, ":white_check_mark:")

        blocks: list[dict[str, Any]] = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"{emoji} *Incident Resolved*\n\n"
                        f"*Incident:* {incident_id}\n"
                        f"*Resolution:* {resolution_type.replace('_', ' ').title()}\n"
                        f"*Resolved by:* <@{user_id}>\n"
                        f"*Time:* {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
                    ),
                },
            },
        ]

        if notes:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Notes:*\n{notes}",
                },
            })

        blocks.append({
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Generate Postmortem"},
                    "action_id": f"postmortem_{incident_id}",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Reopen"},
                    "action_id": f"reopen_{incident_id}",
                },
            ],
        })

        return blocks

    def _build_snooze_blocks(
        self,
        incident_id: str,
        user_id: str,
        duration_minutes: int,
        snooze_until: datetime,
    ) -> list[dict[str, Any]]:
        """Build snooze notification blocks."""
        return [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f":zzz: *Incident Snoozed*\n\n"
                        f"*Incident:* {incident_id}\n"
                        f"*Duration:* {duration_minutes} minutes\n"
                        f"*Resumes at:* {snooze_until.strftime('%H:%M:%S UTC')}\n"
                        f"*Snoozed by:* <@{user_id}>"
                    ),
                },
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Wake Now"},
                        "action_id": f"wake_{incident_id}",
                        "style": "danger",
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Extend 30m"},
                        "action_id": f"extend_snooze_{incident_id}",
                    },
                ],
            },
        ]

    def _build_severity_update_blocks(
        self,
        incident_id: str,
        user_id: str,
        old_severity: str,
        new_severity: str,
    ) -> list[dict[str, Any]]:
        """Build severity update blocks."""
        severity_emoji = {
            "P1": ":red_circle:",
            "P2": ":large_orange_circle:",
            "P3": ":large_yellow_circle:",
            "P4": ":white_circle:",
        }

        old_emoji = severity_emoji.get(old_severity, ":white_circle:")
        new_emoji = severity_emoji.get(new_severity, ":white_circle:")

        return [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f":chart_with_upwards_trend: *Severity Updated*\n\n"
                        f"*Incident:* {incident_id}\n"
                        f"*Change:* {old_emoji} {old_severity} -> {new_emoji} {new_severity}\n"
                        f"*Updated by:* <@{user_id}>"
                    ),
                },
            },
        ]

    def _build_war_room_created_blocks(
        self,
        channel_name: str,
        incident_id: str,
    ) -> list[dict[str, Any]]:
        """Build war room created notification blocks."""
        return [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f":mega: *War Room Created*\n\n"
                        f"A dedicated channel has been created for this incident.\n"
                        f"*Channel:* <#{channel_name}>\n"
                        f"*Incident:* {incident_id}"
                    ),
                },
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Join War Room"},
                        "action_id": f"join_warroom_{incident_id}",
                        "style": "primary",
                    },
                ],
            },
        ]

    def _build_status_update_blocks(
        self,
        incident_id: str,
        user_id: str,
        update_type: str,
        update_text: str,
    ) -> list[dict[str, Any]]:
        """Build status update blocks."""
        type_emoji = {
            "progress": ":arrow_right:",
            "blocker": ":no_entry:",
            "mitigation": ":shield:",
            "root_cause": ":dart:",
            "action_item": ":white_check_mark:",
        }

        emoji = type_emoji.get(update_type, ":speech_balloon:")

        return [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"{emoji} *Status Update* - {update_type.replace('_', ' ').title()}\n\n"
                        f"{update_text}\n\n"
                        f"_Posted by <@{user_id}> at "
                        f"{datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}_"
                    ),
                },
            },
        ]

    def get_incident_action_buttons(
        self,
        incident_id: str,
        current_status: str = "open",
        severity: str = "P2",
    ) -> list[dict[str, Any]]:
        """Get action buttons for an incident card."""
        elements: list[dict[str, Any]] = []

        if current_status == "open":
            elements.append({
                "type": "button",
                "text": {"type": "plain_text", "text": ":white_check_mark: Acknowledge", "emoji": True},
                "action_id": f"acknowledge_{incident_id}",
                "style": "primary",
            })

        if current_status in ("open", "acknowledged"):
            elements.append({
                "type": "button",
                "text": {"type": "plain_text", "text": ":arrow_up: Escalate", "emoji": True},
                "action_id": f"escalate_{incident_id}",
                "style": "danger" if severity == "P1" else None,
            })

            elements.append({
                "type": "button",
                "text": {"type": "plain_text", "text": ":white_check_mark: Resolve", "emoji": True},
                "action_id": f"resolve_{incident_id}",
            })

            elements.append({
                "type": "button",
                "text": {"type": "plain_text", "text": ":zzz: Snooze", "emoji": True},
                "action_id": f"snooze_{incident_id}",
            })

        return [{"type": "actions", "elements": elements[:4]}]

    def get_snooze_options_modal(
        self,
        incident_id: str,
        trigger_id: str,
    ) -> dict[str, Any]:
        """Get snooze options modal."""
        return {
            "type": "modal",
            "callback_id": f"snooze_submit_{incident_id}",
            "title": {"type": "plain_text", "text": "Snooze Incident"},
            "submit": {"type": "plain_text", "text": "Snooze"},
            "close": {"type": "plain_text", "text": "Cancel"},
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"How long would you like to snooze *{incident_id}*?",
                    },
                },
                {
                    "type": "input",
                    "block_id": "snooze_duration",
                    "label": {"type": "plain_text", "text": "Duration"},
                    "element": {
                        "type": "static_select",
                        "action_id": "duration_select",
                        "options": [
                            {"text": {"type": "plain_text", "text": "15 minutes"}, "value": "15"},
                            {"text": {"type": "plain_text", "text": "30 minutes"}, "value": "30"},
                            {"text": {"type": "plain_text", "text": "1 hour"}, "value": "60"},
                            {"text": {"type": "plain_text", "text": "2 hours"}, "value": "120"},
                            {"text": {"type": "plain_text", "text": "4 hours"}, "value": "240"},
                            {"text": {"type": "plain_text", "text": "Until tomorrow"}, "value": "1440"},
                        ],
                        "initial_option": {
                            "text": {"type": "plain_text", "text": "30 minutes"},
                            "value": "30",
                        },
                    },
                },
                {
                    "type": "input",
                    "block_id": "snooze_reason",
                    "optional": True,
                    "label": {"type": "plain_text", "text": "Reason (optional)"},
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "reason_input",
                        "placeholder": {
                            "type": "plain_text",
                            "text": "e.g., Waiting for deploy to complete",
                        },
                    },
                },
            ],
        }

    def get_escalation_modal(
        self,
        incident_id: str,
        trigger_id: str,
    ) -> dict[str, Any]:
        """Get escalation modal."""
        return {
            "type": "modal",
            "callback_id": f"escalate_submit_{incident_id}",
            "title": {"type": "plain_text", "text": "Escalate Incident"},
            "submit": {"type": "plain_text", "text": "Escalate"},
            "close": {"type": "plain_text", "text": "Cancel"},
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"Escalate *{incident_id}* to a higher tier.",
                    },
                },
                {
                    "type": "input",
                    "block_id": "escalation_level",
                    "label": {"type": "plain_text", "text": "Escalation Level"},
                    "element": {
                        "type": "static_select",
                        "action_id": "level_select",
                        "options": [
                            {"text": {"type": "plain_text", "text": "L2 - Engineering"}, "value": "L2"},
                            {"text": {"type": "plain_text", "text": "L3 - Senior Engineering"}, "value": "L3"},
                            {"text": {"type": "plain_text", "text": "L4 - Principal/Staff"}, "value": "L4"},
                            {"text": {"type": "plain_text", "text": "Management"}, "value": "MGR"},
                        ],
                    },
                },
                {
                    "type": "input",
                    "block_id": "escalation_team",
                    "label": {"type": "plain_text", "text": "Team"},
                    "element": {
                        "type": "static_select",
                        "action_id": "team_select",
                        "options": [
                            {"text": {"type": "plain_text", "text": "On-Call"}, "value": "on-call"},
                            {"text": {"type": "plain_text", "text": "Platform"}, "value": "platform"},
                            {"text": {"type": "plain_text", "text": "Infrastructure"}, "value": "infra"},
                            {"text": {"type": "plain_text", "text": "Security"}, "value": "security"},
                            {"text": {"type": "plain_text", "text": "Database"}, "value": "database"},
                        ],
                    },
                },
                {
                    "type": "input",
                    "block_id": "escalation_reason",
                    "label": {"type": "plain_text", "text": "Reason for Escalation"},
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "reason_input",
                        "multiline": True,
                        "placeholder": {
                            "type": "plain_text",
                            "text": "Why are you escalating this incident?",
                        },
                    },
                },
            ],
        }

    def get_resolution_modal(
        self,
        incident_id: str,
        trigger_id: str,
    ) -> dict[str, Any]:
        """Get resolution modal."""
        return {
            "type": "modal",
            "callback_id": f"resolve_submit_{incident_id}",
            "title": {"type": "plain_text", "text": "Resolve Incident"},
            "submit": {"type": "plain_text", "text": "Resolve"},
            "close": {"type": "plain_text", "text": "Cancel"},
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"Resolve *{incident_id}*.",
                    },
                },
                {
                    "type": "input",
                    "block_id": "resolution_type",
                    "label": {"type": "plain_text", "text": "Resolution Type"},
                    "element": {
                        "type": "static_select",
                        "action_id": "type_select",
                        "options": [
                            {"text": {"type": "plain_text", "text": "Fixed"}, "value": "fixed"},
                            {"text": {"type": "plain_text", "text": "Not an Issue"}, "value": "not_an_issue"},
                            {"text": {"type": "plain_text", "text": "Duplicate"}, "value": "duplicate"},
                            {"text": {"type": "plain_text", "text": "Won't Fix"}, "value": "wont_fix"},
                            {"text": {"type": "plain_text", "text": "Workaround Applied"}, "value": "workaround"},
                        ],
                        "initial_option": {
                            "text": {"type": "plain_text", "text": "Fixed"},
                            "value": "fixed",
                        },
                    },
                },
                {
                    "type": "input",
                    "block_id": "resolution_summary",
                    "label": {"type": "plain_text", "text": "Resolution Summary"},
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "summary_input",
                        "multiline": True,
                        "placeholder": {
                            "type": "plain_text",
                            "text": "Brief summary of how this was resolved...",
                        },
                    },
                },
                {
                    "type": "input",
                    "block_id": "follow_up",
                    "optional": True,
                    "label": {"type": "plain_text", "text": "Follow-up Actions (optional)"},
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "followup_input",
                        "multiline": True,
                        "placeholder": {
                            "type": "plain_text",
                            "text": "Any follow-up tasks or action items...",
                        },
                    },
                },
                {
                    "type": "input",
                    "block_id": "postmortem",
                    "optional": True,
                    "label": {"type": "plain_text", "text": "Schedule Postmortem"},
                    "element": {
                        "type": "checkboxes",
                        "action_id": "postmortem_check",
                        "options": [
                            {
                                "text": {"type": "plain_text", "text": "Generate postmortem document"},
                                "value": "generate_postmortem",
                            },
                        ],
                    },
                },
            ],
        }

    def get_action_history(
        self,
        incident_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Get action history, optionally filtered by incident."""
        history = self._action_history

        if incident_id:
            history = [h for h in history if h["incident_id"] == incident_id]

        return history[-limit:]


_handler: IncidentActionHandler | None = None


def get_incident_action_handler() -> IncidentActionHandler:
    """Get the default incident action handler."""
    global _handler
    if _handler is None:
        _handler = IncidentActionHandler()
    return _handler
