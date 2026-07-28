"""Bi-Directional Integration Sync.

Enables read and write operations across integrations.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("devcopilot.integrations_advanced.bidirectional")


class SyncDirection(str, Enum):
    READ = "read"
    WRITE = "write"
    BIDIRECTIONAL = "bidirectional"


class SyncActionType(str, Enum):
    CREATE_TICKET = "create_ticket"
    UPDATE_TICKET = "update_ticket"
    LINK_INCIDENT = "link_incident"
    CREATE_ALERT = "create_alert"
    ACK_ALERT = "ack_alert"
    RESOLVE_ALERT = "resolve_alert"
    POST_MESSAGE = "post_message"
    CREATE_PR = "create_pr"
    CREATE_COMMENT = "create_comment"
    UPDATE_STATUS = "update_status"


@dataclass
class SyncAction:
    """A sync action to write to an integration."""

    action_id: str
    integration: str
    action_type: SyncActionType
    payload: dict[str, Any]
    triggered_by: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    executed_at: datetime | None = None
    status: str = "pending"
    result: dict[str, Any] = field(default_factory=dict)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "integration": self.integration,
            "action_type": self.action_type.value,
            "payload": self.payload,
            "triggered_by": self.triggered_by,
            "created_at": self.created_at.isoformat(),
            "executed_at": self.executed_at.isoformat() if self.executed_at else None,
            "status": self.status,
            "result": self.result,
            "error": self.error,
        }


@dataclass
class SyncConfig:
    """Configuration for bi-directional sync."""

    integration: str
    direction: SyncDirection
    read_capabilities: list[str] = field(default_factory=list)
    write_capabilities: list[str] = field(default_factory=list)
    auto_actions: dict[str, bool] = field(default_factory=dict)
    webhook_url: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "integration": self.integration,
            "direction": self.direction.value,
            "read": self.read_capabilities,
            "write": self.write_capabilities,
            "auto_actions": self.auto_actions,
        }


class BidirectionalSync:
    """Manages bi-directional integration sync."""

    def __init__(self) -> None:
        self._configs: dict[str, SyncConfig] = {}
        self._actions: dict[str, list[SyncAction]] = {}
        self._action_history: dict[str, list[SyncAction]] = {}
        self._init_default_configs()

    def _init_default_configs(self) -> None:
        """Initialize default sync configurations."""
        self._configs["jira"] = SyncConfig(
            integration="jira",
            direction=SyncDirection.BIDIRECTIONAL,
            read_capabilities=[
                "Open incidents/bugs",
                "Sprint context for changes",
                "Recent tickets for correlation",
                "Team assignments",
            ],
            write_capabilities=[
                "Create post-incident action items",
                "Create bug tickets from root cause",
                "Link incidents to existing tickets",
                "Update ticket priority based on impact",
            ],
            auto_actions={
                "create_bug_on_resolve": True,
                "create_action_items_from_postmortem": True,
                "link_related_tickets": True,
                "auto_close_on_fix_deploy": False,
            },
        )

        self._configs["pagerduty"] = SyncConfig(
            integration="pagerduty",
            direction=SyncDirection.BIDIRECTIONAL,
            read_capabilities=[
                "Active alerts and incidents",
                "On-call schedules",
                "Escalation policies",
                "Service dependencies",
            ],
            write_capabilities=[
                "Acknowledge alerts",
                "Resolve incidents",
                "Trigger incidents",
                "Update incident priority",
            ],
            auto_actions={
                "auto_ack_on_investigation": True,
                "auto_resolve_on_fix": True,
                "escalate_if_no_response": True,
            },
        )

        self._configs["slack"] = SyncConfig(
            integration="slack",
            direction=SyncDirection.BIDIRECTIONAL,
            read_capabilities=[
                "Channel messages for context",
                "Thread replies",
                "User presence",
            ],
            write_capabilities=[
                "Create incident channels",
                "Post status updates",
                "Send notifications",
                "Add reactions",
            ],
            auto_actions={
                "create_channel_on_incident": True,
                "post_status_updates": True,
                "archive_on_resolve": False,
            },
        )

        self._configs["github"] = SyncConfig(
            integration="github",
            direction=SyncDirection.BIDIRECTIONAL,
            read_capabilities=[
                "Recent commits and PRs",
                "Deployment events",
                "Code owners",
                "Branch protection status",
            ],
            write_capabilities=[
                "Create fix PRs",
                "Add PR comments",
                "Create issues",
                "Trigger deployments",
            ],
            auto_actions={
                "create_fix_pr": False,
                "comment_on_related_prs": True,
                "create_issue_for_action_items": True,
            },
        )

    def get_config(self, integration: str) -> SyncConfig | None:
        """Get sync config for an integration."""
        return self._configs.get(integration)

    def update_config(
        self,
        org_id: str,
        integration: str,
        auto_actions: dict[str, bool],
    ) -> SyncConfig | None:
        """Update sync configuration."""
        config = self._configs.get(integration)
        if not config:
            return None

        config.auto_actions.update(auto_actions)
        log.info(
            "Updated sync config: org=%s integration=%s",
            org_id,
            integration,
        )
        return config

    def queue_action(
        self,
        org_id: str,
        integration: str,
        action_type: SyncActionType,
        payload: dict[str, Any],
        triggered_by: str,
    ) -> SyncAction:
        """Queue a write action."""
        action = SyncAction(
            action_id=f"action_{datetime.now(timezone.utc).timestamp()}",
            integration=integration,
            action_type=action_type,
            payload=payload,
            triggered_by=triggered_by,
        )

        key = f"{org_id}:{integration}"
        if key not in self._actions:
            self._actions[key] = []
        self._actions[key].append(action)

        log.info(
            "Queued action: org=%s integration=%s type=%s",
            org_id,
            integration,
            action_type.value,
        )

        return action

    def execute_action(self, org_id: str, action_id: str) -> dict[str, Any]:
        """Execute a queued action."""
        for key, actions in self._actions.items():
            if not key.startswith(f"{org_id}:"):
                continue

            for action in actions:
                if action.action_id != action_id:
                    continue

                result = self._execute_integration_action(action)
                action.executed_at = datetime.now(timezone.utc)
                action.status = "completed" if "error" not in result else "failed"
                action.result = result
                action.error = result.get("error", "")

                if org_id not in self._action_history:
                    self._action_history[org_id] = []
                self._action_history[org_id].append(action)
                actions.remove(action)

                return action.to_dict()

        return {"error": "Action not found"}

    def _execute_integration_action(self, action: SyncAction) -> dict[str, Any]:
        """Execute action against integration (simulated)."""
        if action.action_type == SyncActionType.CREATE_TICKET:
            return {
                "ticket_id": f"{action.integration.upper()}-1234",
                "url": f"https://{action.integration}.example.com/ticket/1234",
                "title": action.payload.get("title", ""),
            }

        elif action.action_type == SyncActionType.POST_MESSAGE:
            return {
                "message_id": f"msg_{action.action_id}",
                "channel": action.payload.get("channel", ""),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

        elif action.action_type == SyncActionType.ACK_ALERT:
            return {
                "alert_id": action.payload.get("alert_id", ""),
                "acknowledged_by": action.triggered_by,
                "acknowledged_at": datetime.now(timezone.utc).isoformat(),
            }

        elif action.action_type == SyncActionType.RESOLVE_ALERT:
            return {
                "alert_id": action.payload.get("alert_id", ""),
                "resolved_by": action.triggered_by,
                "resolved_at": datetime.now(timezone.utc).isoformat(),
            }

        elif action.action_type == SyncActionType.CREATE_PR:
            return {
                "pr_number": 893,
                "url": "https://github.com/org/repo/pull/893",
                "title": action.payload.get("title", ""),
                "branch": action.payload.get("branch", "fix/incident-fix"),
            }

        elif action.action_type == SyncActionType.LINK_INCIDENT:
            return {
                "linked": True,
                "incident_id": action.payload.get("incident_id", ""),
                "ticket_id": action.payload.get("ticket_id", ""),
            }

        return {"status": "executed"}

    def trigger_auto_actions(
        self,
        org_id: str,
        event_type: str,
        context: dict[str, Any],
    ) -> list[SyncAction]:
        """Trigger auto-actions based on events."""
        triggered: list[SyncAction] = []

        for integration, config in self._configs.items():
            if event_type == "incident_resolved":
                if config.auto_actions.get("create_bug_on_resolve"):
                    action = self.queue_action(
                        org_id,
                        integration,
                        SyncActionType.CREATE_TICKET,
                        {
                            "title": f"Bug: {context.get('incident_title', 'Incident')}",
                            "description": context.get("root_cause", ""),
                            "priority": "high",
                        },
                        "auto_action",
                    )
                    triggered.append(action)

                if config.auto_actions.get("auto_resolve_on_fix"):
                    if integration == "pagerduty":
                        action = self.queue_action(
                            org_id,
                            integration,
                            SyncActionType.RESOLVE_ALERT,
                            {"alert_id": context.get("alert_id", "")},
                            "auto_action",
                        )
                        triggered.append(action)

            elif event_type == "incident_created":
                if config.auto_actions.get("create_channel_on_incident"):
                    if integration == "slack":
                        action = self.queue_action(
                            org_id,
                            integration,
                            SyncActionType.POST_MESSAGE,
                            {
                                "channel": "#incidents",
                                "text": f"Incident created: {context.get('incident_title', '')}",
                            },
                            "auto_action",
                        )
                        triggered.append(action)

            elif event_type == "postmortem_completed":
                if config.auto_actions.get("create_action_items_from_postmortem"):
                    action_items = context.get("action_items", [])
                    for item in action_items[:5]:
                        action = self.queue_action(
                            org_id,
                            integration,
                            SyncActionType.CREATE_TICKET,
                            {
                                "title": item.get("title", "Action item"),
                                "description": item.get("description", ""),
                                "assignee": item.get("owner", ""),
                            },
                            "auto_action",
                        )
                        triggered.append(action)

        return triggered

    def get_pending_actions(self, org_id: str) -> list[dict[str, Any]]:
        """Get all pending actions for an org."""
        pending = []
        for key, actions in self._actions.items():
            if key.startswith(f"{org_id}:"):
                for action in actions:
                    pending.append(action.to_dict())
        return pending

    def get_action_history(
        self,
        org_id: str,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Get action history for an org."""
        history = self._action_history.get(org_id, [])
        return [a.to_dict() for a in history[-limit:]]


_sync: BidirectionalSync | None = None


def get_bidirectional_sync() -> BidirectionalSync:
    """Get the default bi-directional sync."""
    global _sync
    if _sync is None:
        _sync = BidirectionalSync()
    return _sync
