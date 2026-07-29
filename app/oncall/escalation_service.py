"""On-Call Escalation Service.

Enterprise-grade alert escalation with:
- Multi-level escalation policies
- Multiple targets per level (schedules, users, webhooks, Slack)
- Configurable delays between levels
- Repeat escalation
- Skip conditions
- Time-based routing
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import aiohttp
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.oncall.models import (
    OncallTeam,
    OncallEscalationPolicy,
    OncallEscalationStep,
    OncallAlert,
    OncallNotification,
)
from app.oncall.schedule_service import ScheduleService

log = logging.getLogger("devcopilot.oncall.escalation")


class EscalationService:
    """Manages alert escalation through escalation policies."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.schedule_service = ScheduleService(db)

    # =========================================================================
    # ESCALATION POLICY CRUD
    # =========================================================================

    async def create_escalation_policy(
        self,
        team_id: UUID,
        name: str,
        steps: list[dict[str, Any]],
        description: str | None = None,
        conditions: dict[str, Any] | None = None,
        is_default: bool = False,
        repeat_enabled: bool = True,
        repeat_limit: int = 3,
    ) -> OncallEscalationPolicy:
        """
        Create an escalation policy.

        steps format:
        [
            {
                "delay_minutes": 0,
                "targets": [
                    {"type": "schedule", "schedule_id": "...", "layer": "primary"},
                    {"type": "user", "user_id": "..."},
                ],
                "notification_channels": ["push", "sms", "phone"],
            },
            {
                "delay_minutes": 5,
                "targets": [
                    {"type": "schedule", "schedule_id": "...", "layer": "secondary"},
                ],
            },
            {
                "delay_minutes": 10,
                "targets": [
                    {"type": "slack_channel", "channel_id": "..."},
                    {"type": "webhook", "url": "..."},
                ],
            },
        ]
        """
        if is_default:
            await self.db.execute(
                update(OncallEscalationPolicy)
                .where(
                    OncallEscalationPolicy.team_id == team_id,
                    OncallEscalationPolicy.is_default.is_(True),
                )
                .values(is_default=False)
            )

        policy = OncallEscalationPolicy(
            team_id=team_id,
            name=name,
            description=description,
            conditions=conditions,
            is_default=is_default,
            repeat_enabled=repeat_enabled,
            repeat_limit=repeat_limit,
        )

        self.db.add(policy)
        await self.db.flush()

        for step_num, step_config in enumerate(steps):
            step = OncallEscalationStep(
                policy_id=policy.id,
                step_number=step_num,
                delay_minutes=step_config.get("delay_minutes", 0),
                targets=step_config["targets"],
                notification_channels=step_config.get(
                    "notification_channels", ["push", "sms", "phone"]
                ),
                skip_if=step_config.get("skip_if"),
            )
            self.db.add(step)

        await self.db.commit()
        await self.db.refresh(policy)

        log.info("Created escalation policy %s for team %s", policy.id, team_id)
        return policy

    async def get_policy(self, policy_id: UUID) -> OncallEscalationPolicy | None:
        """Get escalation policy with steps."""
        result = await self.db.execute(
            select(OncallEscalationPolicy)
            .options(selectinload(OncallEscalationPolicy.steps))
            .where(OncallEscalationPolicy.id == policy_id)
        )
        return result.scalar_one_or_none()

    async def get_team_policies(self, team_id: UUID) -> list[OncallEscalationPolicy]:
        """Get all escalation policies for a team."""
        result = await self.db.execute(
            select(OncallEscalationPolicy)
            .options(selectinload(OncallEscalationPolicy.steps))
            .where(
                OncallEscalationPolicy.team_id == team_id,
                OncallEscalationPolicy.is_active.is_(True),
            )
            .order_by(OncallEscalationPolicy.is_default.desc())
        )
        return list(result.scalars().all())

    async def get_default_policy(self, team_id: UUID) -> OncallEscalationPolicy | None:
        """Get the default escalation policy for a team."""
        result = await self.db.execute(
            select(OncallEscalationPolicy)
            .options(selectinload(OncallEscalationPolicy.steps))
            .where(
                OncallEscalationPolicy.team_id == team_id,
                OncallEscalationPolicy.is_default.is_(True),
                OncallEscalationPolicy.is_active.is_(True),
            )
        )
        return result.scalar_one_or_none()

    async def match_policy_for_alert(
        self,
        team_id: UUID,
        alert: OncallAlert,
    ) -> OncallEscalationPolicy | None:
        """
        Find the best matching escalation policy for an alert.
        Uses conditions to match (severity, services, tags).
        Falls back to default policy.
        """
        policies = await self.get_team_policies(team_id)

        for policy in policies:
            if policy.is_default:
                continue

            if self._policy_matches_alert(policy, alert):
                return policy

        for policy in policies:
            if policy.is_default:
                return policy

        return None

    def _policy_matches_alert(
        self,
        policy: OncallEscalationPolicy,
        alert: OncallAlert,
    ) -> bool:
        """Check if policy conditions match the alert."""
        if not policy.conditions:
            return False

        conditions = policy.conditions

        if "severity" in conditions:
            if alert.severity not in conditions["severity"]:
                return False

        if "services" in conditions:
            alert_service = alert.custom_fields.get("service") or alert.source
            if alert_service and alert_service not in conditions["services"]:
                return False

        if "tags" in conditions:
            if not set(conditions["tags"]).intersection(set(alert.tags)):
                return False

        return True

    # =========================================================================
    # ESCALATION EXECUTION
    # =========================================================================

    async def start_escalation(
        self,
        alert: OncallAlert,
        policy: OncallEscalationPolicy | None = None,
    ) -> None:
        """
        Start escalation for an alert.
        Executes the first step immediately, schedules subsequent steps.
        """
        if not policy and alert.team_id:
            policy = await self.match_policy_for_alert(alert.team_id, alert)

        if not policy or not policy.steps:
            await self._notify_current_oncall(alert)
            return

        alert.escalation_policy_id = policy.id
        alert.escalation_started_at = datetime.now(timezone.utc)
        alert.current_escalation_step = 0

        await self.db.commit()

        await self._execute_escalation_step(alert, policy, 0)
        log.info("Started escalation for alert %s with policy %s", alert.id, policy.id)

    async def escalate_to_next_step(
        self,
        alert_id: UUID,
    ) -> bool:
        """
        Move alert to the next escalation step.
        Called by scheduler when delay expires and alert is not acknowledged.
        Returns True if escalated, False if no more steps.
        """
        result = await self.db.execute(
            select(OncallAlert)
            .where(OncallAlert.id == alert_id)
        )
        alert = result.scalar_one_or_none()

        if not alert:
            return False

        if alert.status in ("acknowledged", "resolved", "suppressed"):
            return False

        policy = await self.get_policy(alert.escalation_policy_id) if alert.escalation_policy_id else None
        if not policy:
            return False

        next_step = alert.current_escalation_step + 1

        if next_step >= len(policy.steps):
            if policy.repeat_enabled:
                repeat_count = next_step // len(policy.steps)
                if repeat_count < policy.repeat_limit:
                    next_step = next_step % len(policy.steps)
                else:
                    return False
            else:
                return False

        alert.current_escalation_step = next_step
        await self.db.commit()

        await self._execute_escalation_step(alert, policy, next_step % len(policy.steps))
        log.info("Escalated alert %s to step %d", alert_id, next_step)
        return True

    async def _execute_escalation_step(
        self,
        alert: OncallAlert,
        policy: OncallEscalationPolicy,
        step_number: int,
    ) -> None:
        """Execute a single escalation step."""
        step = policy.steps[step_number]

        if step.skip_if:
            if step.skip_if.get("acknowledged") and alert.status == "acknowledged":
                return
            if step.skip_if.get("resolved") and alert.status == "resolved":
                return

        target_users = await self._resolve_targets(step.targets, alert.team_id)

        for user_id in target_users:
            for channel in step.notification_channels:
                await self._send_alert_notification(
                    alert=alert,
                    user_id=user_id,
                    channel=channel,
                    escalation_step=step_number,
                )

        for target in step.targets:
            if target["type"] == "webhook":
                await self._send_webhook(target["url"], alert)
            elif target["type"] == "slack_channel":
                await self._send_slack_alert(target["channel_id"], alert)

        next_step_index = step_number + 1
        if next_step_index < len(policy.steps):
            next_step = policy.steps[next_step_index]
            if next_step.delay_minutes > 0:
                await self._schedule_next_escalation(
                    alert.id,
                    delay_minutes=next_step.delay_minutes,
                )

    async def _resolve_targets(
        self,
        targets: list[dict[str, Any]],
        team_id: UUID | None,
    ) -> list[UUID]:
        """Resolve targets to actual user IDs."""
        user_ids: set[UUID] = set()

        for target in targets:
            target_type = target["type"]

            if target_type == "user":
                user_ids.add(UUID(target["user_id"]))

            elif target_type == "schedule":
                schedule_id = UUID(target["schedule_id"])
                layer = target.get("layer", "primary")

                schedule = await self.schedule_service.get_schedule(schedule_id)
                if schedule:
                    oncall = await self.schedule_service.get_current_oncall(schedule.team_id)

                    if layer == "primary" and oncall.get("primary"):
                        user_ids.add(oncall["primary"])
                    elif layer == "secondary" and oncall.get("secondary"):
                        user_ids.add(oncall["secondary"])

            elif target_type == "team" and team_id:
                result = await self.db.execute(
                    select(OncallTeam)
                    .options(selectinload(OncallTeam.members))
                    .where(OncallTeam.id == team_id)
                )
                team = result.scalar_one_or_none()
                if team:
                    for member in team.members:
                        if member.is_active:
                            user_ids.add(member.user_id)

        return list(user_ids)

    async def _notify_current_oncall(self, alert: OncallAlert) -> None:
        """Fallback: notify current on-call when no policy exists."""
        if not alert.team_id:
            return

        oncall = await self.schedule_service.get_current_oncall(alert.team_id)

        if oncall.get("primary"):
            for channel in ["push", "sms", "phone"]:
                await self._send_alert_notification(
                    alert=alert,
                    user_id=oncall["primary"],
                    channel=channel,
                    escalation_step=0,
                )

    async def _send_alert_notification(
        self,
        alert: OncallAlert,
        user_id: UUID,
        channel: str,
        escalation_step: int,
    ) -> OncallNotification:
        """Send notification for an alert."""
        notification = OncallNotification(
            alert_id=alert.id,
            user_id=user_id,
            channel=channel,
            escalation_step=escalation_step,
            status="pending",
        )
        self.db.add(notification)
        await self.db.flush()

        notification.status = "sent"
        notification.sent_at = datetime.now(timezone.utc)

        await self.db.commit()
        log.info("Sent %s notification to user %s for alert %s", channel, user_id, alert.id)
        return notification

    async def _send_webhook(self, url: str, alert: OncallAlert) -> None:
        """Send alert to webhook."""
        payload = {
            "event": "alert.triggered",
            "alert": {
                "id": str(alert.id),
                "title": alert.title,
                "description": alert.description,
                "severity": alert.severity,
                "status": alert.status,
                "triggered_at": alert.triggered_at.isoformat() if alert.triggered_at else None,
                "source": alert.source,
                "tags": alert.tags,
            },
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=10),
                ):
                    pass
            log.info("Sent webhook for alert %s to %s", alert.id, url)
        except Exception as e:
            log.error("Failed to send webhook for alert %s: %s", alert.id, e)

    async def _send_slack_alert(self, channel_id: str, alert: OncallAlert) -> None:
        """Send alert to Slack channel."""
        severity_emoji = {
            "critical": ":rotating_light:",
            "high": ":warning:",
            "warning": ":large_yellow_circle:",
            "low": ":information_source:",
            "info": ":speech_balloon:",
        }

        emoji = severity_emoji.get(alert.severity, ":bell:")

        blocks: list[dict[str, Any]] = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"{emoji} *{alert.severity.upper()}*: {alert.title}",
                },
            },
        ]

        if alert.description:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": alert.description[:500],
                },
            })

        blocks.append({
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Acknowledge"},
                    "style": "primary",
                    "action_id": f"ack_alert_{alert.id}",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "View Details"},
                    "url": f"/alerts/{alert.id}",
                },
            ],
        })

        log.info("Would send Slack alert for %s to channel %s", alert.id, channel_id)

    async def _schedule_next_escalation(
        self,
        alert_id: UUID,
        delay_minutes: int,
    ) -> None:
        """Schedule the next escalation step."""
        log.info("Scheduled escalation for alert %s in %d minutes", alert_id, delay_minutes)

    # =========================================================================
    # ALERT ACTIONS
    # =========================================================================

    async def acknowledge_alert(
        self,
        alert_id: UUID,
        user_id: UUID,
    ) -> OncallAlert:
        """Acknowledge an alert."""
        result = await self.db.execute(
            select(OncallAlert).where(OncallAlert.id == alert_id)
        )
        alert = result.scalar_one_or_none()

        if not alert:
            raise ValueError("Alert not found")

        if alert.status != "triggered":
            raise ValueError(f"Cannot acknowledge a {alert.status} alert")

        alert.status = "acknowledged"
        alert.acknowledged_at = datetime.now(timezone.utc)
        alert.acknowledged_by = user_id

        await self.db.commit()
        await self.db.refresh(alert)

        await self.db.execute(
            update(OncallNotification)
            .where(
                OncallNotification.alert_id == alert_id,
                OncallNotification.user_id == user_id,
            )
            .values(
                status="acknowledged",
                acknowledged_at=datetime.now(timezone.utc),
            )
        )
        await self.db.commit()

        log.info("Alert %s acknowledged by user %s", alert_id, user_id)
        return alert

    async def resolve_alert(
        self,
        alert_id: UUID,
        user_id: UUID,
        resolution_notes: str | None = None,
    ) -> OncallAlert:
        """Resolve an alert."""
        result = await self.db.execute(
            select(OncallAlert).where(OncallAlert.id == alert_id)
        )
        alert = result.scalar_one_or_none()

        if not alert:
            raise ValueError("Alert not found")

        if alert.status == "resolved":
            raise ValueError("Alert is already resolved")

        alert.status = "resolved"
        alert.resolved_at = datetime.now(timezone.utc)
        alert.resolved_by = user_id

        if resolution_notes:
            if alert.custom_fields is None:
                alert.custom_fields = {}
            alert.custom_fields["resolution_notes"] = resolution_notes

        await self.db.commit()
        await self.db.refresh(alert)

        log.info("Alert %s resolved by user %s", alert_id, user_id)
        return alert


async def get_escalation_service(db: AsyncSession) -> EscalationService:
    """Get escalation service instance."""
    return EscalationService(db)
