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
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
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
from app.config import get_settings
from app.oncall.schedule_service import ScheduleService

log = logging.getLogger("omnipotent.oncall.escalation")


# Escalation targets arrive as plain dicts out of a JSONB column, and three
# different producers write three different key sets into it:
#
#   * this module's own docstring/API shape:  {"type": "user", "user_id": ...}
#   * `EscalationTarget.to_dict()`, which is what BOTH policy routes persist:
#                                             {"target_type": "user", "target_id": ...}
#   * the request body those routes accept:   {"type": "user", "id": ...}
#
# The service only ever read the first, so every policy created through the API
# — including all three built-in templates — raised KeyError: 'type' the moment
# an alert tried to escalate, and the page went to nobody. Reading all three is
# the fix rather than picking a winner, because policies persisted by the old
# code are still sitting in the table in the second shape.
def _target_type(target: Mapping[str, Any]) -> str:
    """The kind of thing to notify, however the producer spelled the key."""
    raw = target.get("type") or target.get("target_type") or ""
    return str(raw).strip().lower()


def _target_value(target: Mapping[str, Any], *keys: str) -> str:
    """First non-empty value among `keys`. Aliases, in order of specificity."""
    for key in keys:
        value = target.get(key)
        if value:
            return str(value).strip()
    return ""


def _target_uuid(target: Mapping[str, Any], *keys: str) -> UUID | None:
    """Parse a target id, or None. A malformed id in one target must not take
    down the whole page — the other targets on the step still have to fire."""
    raw = _target_value(target, *keys)
    if not raw:
        return None
    try:
        return UUID(raw)
    except (ValueError, AttributeError, TypeError):
        log.warning("Escalation target has an unusable id %r; skipping it", raw)
        return None


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
        Called by the sweeper when the delay expires and the alert is still
        unacknowledged. Returns True if escalated, False if no more steps.

        `current_escalation_step` is an *absolute* counter and is only reduced
        modulo the step count when indexing into the policy. It used to be
        written back as the wrapped value, which reset the repeat count on every
        wrap: a 2-step policy with `repeat_limit=3` went 0→1→0→1→… and paged the
        on-call forever, because `next_step // len(steps)` never grew past 1.
        """
        result = await self.db.execute(
            select(OncallAlert)
            .where(OncallAlert.id == alert_id)
        )
        alert = result.scalar_one_or_none()

        if not alert:
            return False

        if alert.status in ("acknowledged", "resolved", "suppressed"):
            # Stop the sweeper from picking it up again every tick.
            await self._set_next_escalation(alert, None)
            return False

        policy = await self.get_policy(alert.escalation_policy_id) if alert.escalation_policy_id else None
        if not policy or not policy.steps:
            await self._set_next_escalation(alert, None)
            return False

        total = len(policy.steps)
        next_step = alert.current_escalation_step + 1

        if next_step >= total:
            completed_repeats = next_step // total
            if not policy.repeat_enabled or completed_repeats >= policy.repeat_limit:
                await self._set_next_escalation(alert, None)
                log.info("Alert %s exhausted its escalation policy", alert_id)
                return False

        alert.current_escalation_step = next_step
        await self.db.commit()

        await self._execute_escalation_step(alert, policy, next_step % total)
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
            if not isinstance(target, Mapping):
                continue
            target_type = _target_type(target)
            if target_type == "webhook":
                url = _target_value(target, "url", "webhook_url", "target_id", "id")
                if url:
                    await self._send_webhook(url, alert)
                else:
                    log.warning("Webhook target on step %d has no URL", step_number)
            elif target_type == "slack_channel":
                channel = _target_value(
                    target, "channel_id", "slack_channel_id", "target_id", "id"
                )
                if channel:
                    await self._send_slack_alert(channel, alert)
                else:
                    log.warning("Slack target on step %d has no channel", step_number)

        # Arm the next step. Two bugs lived here: the arming was skipped entirely
        # once `step_number` reached the last step — so `repeat_enabled` policies
        # never repeated — and a next step with `delay_minutes == 0` was treated
        # as "do not schedule" rather than "run immediately".
        await self._arm_next_step(alert, policy, step_number)

    async def _arm_next_step(
        self,
        alert: OncallAlert,
        policy: OncallEscalationPolicy,
        step_number: int,
    ) -> None:
        """Record when the step after `step_number` becomes due, or clear the
        due time when this policy has nothing left to do."""
        total = len(policy.steps)
        next_index = step_number + 1

        if next_index >= total:
            # Wrapping is only allowed while repeats remain. `escalate_to_next_step`
            # re-derives the repeat count from the absolute step number, so the
            # limit is enforced there; here we only decide whether to arm at all.
            if not policy.repeat_enabled or total == 0:
                await self._set_next_escalation(alert, None)
                return
            completed_repeats = (alert.current_escalation_step + 1) // total
            if completed_repeats >= policy.repeat_limit:
                await self._set_next_escalation(alert, None)
                return

        delay = policy.steps[next_index % total].delay_minutes if total else 0
        due = datetime.now(timezone.utc) + timedelta(minutes=max(0, delay))
        await self._set_next_escalation(alert, due)

    async def _set_next_escalation(
        self,
        alert: OncallAlert,
        due: datetime | None,
    ) -> None:
        alert.next_escalation_at = due
        await self.db.commit()
        if due is None:
            log.info("Escalation for alert %s has no further steps", alert.id)
        else:
            log.info("Alert %s escalates again at %s", alert.id, due.isoformat())

    async def _resolve_targets(
        self,
        targets: list[dict[str, Any]],
        team_id: UUID | None,
    ) -> list[UUID]:
        """Resolve targets to actual user IDs."""
        user_ids: set[UUID] = set()

        for target in targets:
            if not isinstance(target, Mapping):
                log.warning("Ignoring malformed escalation target %r", target)
                continue

            target_type = _target_type(target)

            if target_type == "user":
                user_id = _target_uuid(target, "user_id", "target_id", "id")
                if user_id:
                    user_ids.add(user_id)

            elif target_type == "schedule":
                schedule_id = _target_uuid(target, "schedule_id", "target_id", "id")
                if not schedule_id:
                    continue
                layer = _target_value(target, "layer", "schedule_layer") or "primary"

                schedule = await self.schedule_service.get_schedule(schedule_id)
                if schedule:
                    oncall = await self.schedule_service.get_current_oncall(schedule.team_id)

                    # "all" is documented on EscalationTarget.schedule_layer and
                    # was silently treated as "notify nobody".
                    if layer in ("primary", "all") and oncall.get("primary"):
                        user_ids.add(oncall["primary"])
                    if layer in ("secondary", "all") and oncall.get("secondary"):
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
        """Send notification for an alert, and record what actually happened.

        This used to set `status = "sent"` unconditionally without sending
        anything, so the notification log showed a full page trail for an alert
        that never reached a human — the worst possible failure for an on-call
        system, because it looks like success on every dashboard that reads it.
        """
        notification = OncallNotification(
            alert_id=alert.id,
            user_id=user_id,
            channel=channel,
            escalation_step=escalation_step,
            status="pending",
        )
        self.db.add(notification)
        await self.db.flush()

        delivered, error = await self._deliver(alert, user_id, channel)

        notification.status = "sent" if delivered else "failed"
        notification.sent_at = datetime.now(timezone.utc) if delivered else None
        if error:
            notification.error_message = error[:1000]

        await self.db.commit()
        if delivered:
            log.info("Sent %s notification to user %s for alert %s", channel, user_id, alert.id)
        else:
            log.warning(
                "Failed to send %s notification to user %s for alert %s: %s",
                channel, user_id, alert.id, error,
            )
        return notification

    async def _deliver(
        self,
        alert: OncallAlert,
        user_id: UUID,
        channel: str,
    ) -> tuple[bool, str | None]:
        """Hand the page to the notification dispatcher.

        Returns (delivered, error). Never raises: a channel that is not
        configured is a delivery failure to be recorded, not an exception that
        aborts the remaining targets on this escalation step.
        """
        from app.notifications.dispatcher import (
            NotificationChannel,
            NotificationPriority,
            notify,
        )

        try:
            dispatch_channel = NotificationChannel(channel)
        except ValueError:
            return False, f"unknown notification channel {channel!r}"

        priority = (
            NotificationPriority.CRITICAL
            if alert.severity in ("critical", "high")
            else NotificationPriority.MEDIUM
        )

        try:
            results = await notify(
                user_id=str(user_id),
                notification_type="oncall_alert",
                title=f"[{alert.severity.upper()}] {alert.title}",
                body=alert.description or alert.title,
                priority=priority,
                channels={dispatch_channel},
                data={
                    "alert_id": str(alert.id),
                    "severity": alert.severity,
                    "source": alert.source,
                    "url": alert.source_url,
                },
                # One page per alert per channel inside the dispatcher's dedupe
                # window; the escalation step is part of the key so a genuine
                # escalation is never mistaken for a duplicate.
                dedupe_key=f"alert:{alert.id}:{channel}:{alert.current_escalation_step}",
            )
        except Exception as e:  # noqa: BLE001 — recorded, not raised
            return False, str(e)

        if not results:
            # The dispatcher filtered it (quiet hours, disabled channel,
            # duplicate). Nothing was delivered, so it must not read as sent.
            return False, "suppressed by the recipient's notification preferences"

        if any(r.success for r in results):
            return True, None
        return False, "; ".join(r.error or "delivery failed" for r in results)

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

        elements: list[dict[str, Any]] = [
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "Acknowledge"},
                "style": "primary",
                "action_id": f"ack_alert_{alert.id}",
            },
        ]
        # Slack rejects the whole message when a button carries a relative URL,
        # so the "View Details" button only appears once we can build an
        # absolute one.
        base = get_settings().frontend_url.rstrip("/")
        if base:
            elements.append({
                "type": "button",
                "text": {"type": "plain_text", "text": "View Details"},
                "url": f"{base}/alerts/{alert.id}",
            })

        blocks.append({"type": "actions", "elements": elements})

        # This used to build `blocks` and then log "Would send Slack alert",
        # discarding them — a Slack escalation target notified nobody.
        from app.integrations.slack import post_message

        token = get_settings().slack_bot_token
        if not token:
            log.warning(
                "Slack escalation target for alert %s skipped: SLACK_BOT_TOKEN is not set",
                alert.id,
            )
            return

        try:
            response = await post_message(
                token,
                channel_id,
                text=f"{emoji} {alert.severity.upper()}: {alert.title}",
                blocks=blocks,
            )
        except Exception as e:  # noqa: BLE001 — one target must not stop the page
            log.error("Slack alert for %s failed: %s", alert.id, e)
            return

        if response.get("ok"):
            log.info("Sent Slack alert for %s to channel %s", alert.id, channel_id)
        else:
            log.error(
                "Slack rejected the alert for %s to channel %s: %s",
                alert.id, channel_id, response.get("error") or response.get("skipped"),
            )

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


class EscalationSweeper:
    """Advances alerts whose next escalation step has come due.

    Escalation delays were previously "scheduled" by a log line and nothing
    else, so `escalate_to_next_step` had no caller in the entire codebase and
    every alert stopped at step 0 — a policy's second and third levels were
    unreachable no matter how long nobody acknowledged.

    A polled sweep over a persisted due time is deliberate: an in-process timer
    would lose every pending escalation on the restart that is most likely to
    happen during an incident, and would fire steps twice when more than one
    worker is running.
    """

    def __init__(self, session_factory: Any, interval: int = 30, batch: int = 100):
        self._session_factory = session_factory
        self.interval = max(5, interval)
        self.batch = max(1, batch)

    async def tick(self, now: datetime | None = None) -> list[UUID]:
        """One sweep. Returns the alerts escalated. Never raises for one alert."""
        now = now or datetime.now(timezone.utc)

        async with self._session_factory() as db:
            result = await db.execute(
                select(OncallAlert.id)
                .where(
                    OncallAlert.next_escalation_at.is_not(None),
                    OncallAlert.next_escalation_at <= now,
                    OncallAlert.status == "triggered",
                )
                .order_by(OncallAlert.next_escalation_at)
                .limit(self.batch)
            )
            due = list(result.scalars().all())

        escalated: list[UUID] = []
        for alert_id in due:
            # A session per alert, so one poisoned row cannot roll back the
            # escalations that already succeeded in this sweep.
            try:
                async with self._session_factory() as db:
                    service = EscalationService(db)
                    if await service.escalate_to_next_step(alert_id):
                        escalated.append(alert_id)
            except Exception:  # noqa: BLE001 — one bad alert must not stop the sweep
                log.exception("Escalation failed for alert %s", alert_id)
        return escalated

    async def run(self) -> None:
        """Background loop: tick, sleep, forever (until cancelled)."""
        import asyncio

        log.info("Escalation sweeper started (interval=%ss)", self.interval)
        while True:
            try:
                escalated = await self.tick()
                if escalated:
                    log.info("Escalated %d alert(s)", len(escalated))
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("Escalation sweep failed")
            await asyncio.sleep(self.interval)
