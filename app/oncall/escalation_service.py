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
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any
from uuid import UUID

import aiohttp
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models import Member
from app.oncall.models import (
    DEFAULT_ESCALATION_CHANNELS,
    OncallTeam,
    OncallTeamMember,
    OncallEscalationPolicy,
    OncallEscalationStep,
    OncallAlert,
    OncallNotification,
    contact_addresses,
)
from app.config import get_settings
from app.oncall.schedule_service import ScheduleService

if TYPE_CHECKING:
    from app.notifications.dispatcher import (
        NotificationChannel,
        NotificationPreferences,
    )

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


# Escalation policies name their channels in the product's vocabulary, which does
# not quite match the dispatcher's enum. "phone" is the case that mattered: it is
# what the built-in policy templates ask for, what `contact_methods` records a
# number under, and what plenty of stored policies name — and the dispatcher
# spells that channel VOICE, so a commonly configured escalation channel was
# rejected on every page as "unknown notification channel 'phone'". Translating
# is the fix; the stored policies cannot be rewritten out from under the
# customers who wrote them.
_CHANNEL_ALIASES = {"phone": "voice"}


# What a page on each channel needs before it can be addressed to a person, and
# the exact request that puts it there. The operator reading a failed page needs
# to know either what to go and enter or that nothing they enter will help, and
# the dispatcher's own answer ("No email configured") reads like a preference
# somebody forgot rather than an empty store.
#
# Every one of these used to end "and this build has nowhere to store one",
# because at the time nothing wrote a per-responder address of any kind. Adding
# the responder to a team now records both — an email that becomes their
# `members` row and a number or Slack id that becomes their `contact_methods` —
# so these say where to put it. Push is the one that is still genuinely
# impossible: there is no endpoint through which a browser could register a
# device, so no request an operator can make will make a push page deliver.
_NO_PHONE = (
    "no phone number on file for this responder: record one as a "
    "`contact_methods` entry of type sms/phone with a `value` on "
    "POST /oncall/teams/{team_id}/members — for a responder who is on no "
    "on-call team there is nowhere to store one"
)
_MISSING_ROUTING: dict[str, str] = {
    "email": (
        "no email address on file for this responder: there is no `members` row "
        "with this id (POST /oncall/teams/{team_id}/members with an `email` "
        "creates one) and no `contact_methods` entry of type email carries one"
    ),
    "sms": _NO_PHONE,
    "voice": _NO_PHONE,
    "whatsapp": _NO_PHONE,
    "slack": (
        "no Slack user id on file for this responder: record one as a "
        "`contact_methods` entry of type slack with a `value` on "
        "POST /oncall/teams/{team_id}/members — for a responder who is on no "
        "on-call team there is nowhere to store one"
    ),
    "push": (
        "no registered device for this responder, and this build has no endpoint "
        "through which a browser could register one"
    ),
}

# Channels addressed to the person rather than to the deployment. A webhook, a
# Teams connector or a Discord hook is configured once for the org and carries
# the same URL whoever is on call, so there is nothing to look up about the
# responder and nothing missing to report when there is no row for them.
_PERSONALLY_ADDRESSED = frozenset({"email", "sms", "voice", "whatsapp", "slack"})


@dataclass(frozen=True)
class _PageRoute:
    """Where one page is about to be addressed, and what is stopping it.

    `missing` is filled in *before* the send is attempted, from what the build
    stores rather than from what the send returned. It exists because the
    dispatcher's own answer for an unaddressable page ("No email configured") is
    indistinguishable from a mis-set preference: it names a symptom the operator
    would go looking for a setting to fix, when the real answer is that nothing
    in this deployment has ever written the row it reads.
    """

    channel: "NotificationChannel | None"
    preferences: "NotificationPreferences | None" = None
    destination: str = ""
    missing: str = ""


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
                "notification_channels": ["email", "sms", "phone"],
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
                    "notification_channels", list(DEFAULT_ESCALATION_CHANNELS)
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
        await self._announce_escalation(alert, next_step % total, total)
        return True

    async def _announce_escalation(
        self, alert: OncallAlert, step: int, total: int
    ) -> None:
        """Tell the org's own channels that an alert moved up a level.

        Distinct from the step's targets, which page a specific responder chosen
        by the policy: an alert reaching level 3 is a fact about the incident,
        and the team asks to hear it on the channels it picked on the
        Notification Preferences page rather than by being paged individually.

        The sweeper runs on a timer with no request behind it, so there is no
        tenant in the context and `org_uuid()` would fall through to the
        single-tenant org — which on a multi-tenant deployment means reading
        somebody else's routing table. The alert's own `org_id` is the authority,
        so it is what gets established here. Never raises: an escalation that
        fired must not be rolled back because a webhook was down.
        """
        from app.notifications.delivery import notify
        from app.tenant_context import TenantConfig, run_with_tenant

        cfg = TenantConfig(org_id=str(alert.org_id)) if alert.org_id else None
        try:
            await run_with_tenant(
                cfg,
                notify(
                    "escalation",
                    f"Escalated to level {step + 1}: {alert.title}",
                    f"{alert.title} is still unacknowledged and has escalated to "
                    f"level {step + 1} of {total}.",
                    critical=str(alert.severity).lower() in ("critical", "sev1", "p1"),
                    data={
                        "alert_id": str(alert.id),
                        "severity": alert.severity,
                        "step": step + 1,
                        "url": alert.source_url,
                    },
                    dedupe_key=f"oncall-escalation:{alert.id}:{step}",
                ),
            )
        except Exception:  # noqa: BLE001
            log.warning("Could not announce escalation of alert %s", alert.id, exc_info=True)

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

        reached = False
        reasons: list[str] = []

        if not target_users and not step.targets:
            reasons.append("the step has no targets")
        elif not target_users:
            reasons.append(
                "none of the step's targets resolved to a person (empty schedule, "
                "no team members, or an unusable target id)"
            )

        for user_id in target_users:
            for channel in step.notification_channels:
                note = await self._send_alert_notification(
                    alert=alert,
                    user_id=user_id,
                    channel=channel,
                    escalation_step=step_number,
                )
                if note.status == "sent":
                    reached = True
                elif note.error_message:
                    reasons.append(f"{channel} to {user_id}: {note.error_message}")

        for target in step.targets:
            if not isinstance(target, Mapping):
                continue
            target_type = _target_type(target)
            if target_type == "webhook":
                url = _target_value(target, "url", "webhook_url", "target_id", "id")
                if url:
                    if await self._send_webhook(url, alert):
                        reached = True
                    else:
                        reasons.append(f"webhook {url} did not accept the alert")
                else:
                    log.warning("Webhook target on step %d has no URL", step_number)
                    reasons.append("a webhook target on this step has no URL")
            elif target_type == "slack_channel":
                channel = _target_value(
                    target, "channel_id", "slack_channel_id", "target_id", "id"
                )
                if channel:
                    if await self._send_slack_alert(channel, alert):
                        reached = True
                    else:
                        reasons.append(f"Slack channel {channel} was not posted to")
                else:
                    log.warning("Slack target on step %d has no channel", step_number)
                    reasons.append("a Slack target on this step has no channel id")

        if not reached:
            await self._nobody_was_reached(alert, step_number, reasons)

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
        """Fallback: notify current on-call when no policy exists.

        Every branch that ends without a page ends in `_nobody_was_reached`
        instead of a bare `return`. An alert whose team has no schedule, or whose
        rotation has nobody on it right now, used to leave no page, no log line
        and no notification row — the alert simply sat there looking triggered.
        """
        if not alert.team_id:
            await self._nobody_was_reached(
                alert, 0, ["the alert has no team, so there is no rotation to page"]
            )
            return

        oncall = await self.schedule_service.get_current_oncall(alert.team_id)

        if not oncall.get("primary"):
            await self._nobody_was_reached(
                alert,
                0,
                [
                    f"no policy for this alert and nobody is currently on call for "
                    f"team {alert.team_id}"
                ],
            )
            return

        reasons: list[str] = []
        reached = False
        for channel in DEFAULT_ESCALATION_CHANNELS:
            note = await self._send_alert_notification(
                alert=alert,
                user_id=oncall["primary"],
                channel=channel,
                escalation_step=0,
            )
            if note.status == "sent":
                reached = True
            elif note.error_message:
                reasons.append(f"{channel} to {oncall['primary']}: {note.error_message}")

        if not reached:
            await self._nobody_was_reached(alert, 0, reasons)

    async def _nobody_was_reached(
        self,
        alert: OncallAlert,
        step_number: int,
        reasons: list[str],
    ) -> None:
        """An escalation step paged nobody. Say so, and try the org's channels.

        A page that reaches no human is the failure this whole subsystem exists
        to prevent, so it is logged at error level with every reason collected —
        not left to be inferred from a row in `oncall_notifications` that nothing
        watches.

        Then the alert is put out over the channels the org configured on its
        Notification Preferences page (`app/notifications/delivery.py`), which is
        the one place in this build where a *deliverable* destination — an email
        address, a Slack webhook, a PagerDuty routing key — is both stored and
        written by a real request. Those channels belong to the org rather than
        to the chosen responder, so this is not the page it replaces and does not
        make the notification rows read as sent; it is the difference between an
        incident nobody hears about and one the team hears about on the channels
        it asked to hear about things on.

        Sent as critical so quiet hours and digest batching cannot swallow it:
        the notification the org most needs at 3am is the one saying its pager
        did not work. Never raises — a broken webhook must not roll back an
        escalation that has already happened.
        """
        detail = "; ".join(r for r in reasons if r) or "no reason was recorded"
        log.error(
            "Escalation step %d for alert %s reached nobody: %s",
            step_number,
            alert.id,
            detail,
        )

        from app.notifications.delivery import notify
        from app.tenant_context import TenantConfig, run_with_tenant

        cfg = TenantConfig(org_id=str(alert.org_id)) if alert.org_id else None
        try:
            result = await run_with_tenant(
                cfg,
                notify(
                    "escalation",
                    f"[{str(alert.severity).upper()}] NOT PAGED: {alert.title}",
                    f"Escalation step {step_number + 1} for alert {alert.id} could "
                    f"not page anyone, so this is going to the org's own channels "
                    f"instead. Reason: {detail}.",
                    critical=True,
                    data={
                        "alert_id": str(alert.id),
                        "severity": alert.severity,
                        "step": step_number + 1,
                        "url": alert.source_url,
                        "reason": detail,
                    },
                    dedupe_key=f"oncall-unpaged:{alert.id}:{step_number}",
                ),
            )
        except Exception:  # noqa: BLE001
            log.error(
                "Alert %s paged nobody and the org's channels could not be reached "
                "either", alert.id, exc_info=True,
            )
            return

        if result.get("status") != "sent":
            # `deliver()` reports its own outcome per channel; repeating it here
            # is what makes "we told nobody, twice" visible in the log of the
            # service that owns the page rather than only in the notifier's.
            log.error(
                "Alert %s paged nobody and the org's own channels delivered "
                "nothing either (%s): %s",
                alert.id,
                result.get("status"),
                result.get("results"),
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

        # Resolved once and passed down, so the row can record the address the
        # page was addressed to. `contact_value` is on this table for exactly
        # that and was never written: "we sent an email" with no record of where
        # is not something anyone can check after the fact.
        route = await self._page_route_for(user_id, channel)
        notification.contact_value = route.destination[:255] or None

        delivered, error = await self._deliver(alert, user_id, channel, route=route)

        notification.status = "sent" if delivered else "failed"
        notification.sent_at = datetime.now(timezone.utc) if delivered else None
        if error:
            notification.error_message = error[:1000]

        await self.db.commit()
        if delivered:
            log.info("Sent %s notification to user %s for alert %s", channel, user_id, alert.id)
        else:
            # Error, not warning: this is a page that did not reach the person it
            # was aimed at, which is the failure the on-call product exists to
            # prevent. A warning is what the pre-existing code logged, and it is
            # below the level most deployments alert on.
            log.error(
                "Failed to send %s notification to user %s for alert %s: %s",
                channel, user_id, alert.id, error,
            )
        return notification

    async def _deliver(
        self,
        alert: OncallAlert,
        user_id: UUID,
        channel: str,
        route: _PageRoute | None = None,
    ) -> tuple[bool, str | None]:
        """Hand the page to the notification dispatcher.

        Returns (delivered, error). Never raises: a channel that is not
        configured is a delivery failure to be recorded, not an exception that
        aborts the remaining targets on this escalation step.

        The recipient's routing is resolved here and handed to the dispatcher.
        It used to be left to the dispatcher's own `_USER_PREFERENCES`, a
        process-local dictionary that nothing in the application ever writes: every
        responder came back as the same anonymous default — no address, no phone
        number, and only EMAIL and PUSH enabled — so the channel the escalation
        policy had chosen was filtered out before it was ever attempted and the
        page died as "suppressed by the recipient's notification preferences",
        whatever the deployment or the customer had configured. `_page_routing`
        below reads what is actually stored about the person instead.

        The send is attempted even when the routing is known to be incomplete —
        the dispatcher is the authority on whether a channel can deliver, and a
        deployment that has wired one up differently must not be pre-empted here
        — but a failure is reported with what `_page_routing` already knows is
        missing rather than with the dispatcher's terse symptom.
        """
        from app.notifications.dispatcher import NotificationPriority, notify

        if route is None:
            route = await self._page_route_for(user_id, channel)
        if route.channel is None:
            return False, route.missing

        dispatch_channel = route.channel
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
                preferences=route.preferences,
            )
        except Exception as e:  # noqa: BLE001 — recorded, not raised
            return False, str(e)

        if not results:
            # The routing handed over above enables exactly the requested channel
            # and sets no quiet hours or filters, so the dispatcher has only one
            # remaining reason to return nothing, and naming it beats the old
            # guess at the recipient's preferences. "Delivered", not "attempted":
            # the dispatcher only opens its dedupe window on a send a channel
            # accepted, so this reason can no longer stand in for a page that
            # failed a few seconds ago — that one is retried and reports its own
            # failure.
            return False, (
                f"deduplicated: a {dispatch_channel.value} page for this alert at "
                f"escalation step {alert.current_escalation_step} was already "
                f"delivered inside the dispatcher's dedupe window"
            )

        if any(r.success for r in results):
            return True, None

        attempted = "; ".join(r.error or "delivery failed" for r in results)
        if route.missing:
            # What the dispatcher reports for an unaddressed page ("No email
            # configured") reads like a setting somebody forgot. Lead with the
            # store that is actually empty and keep the dispatcher's answer after
            # it, so the log line says both what failed and what to do about it.
            return False, f"{route.missing} (dispatcher: {attempted})"
        return False, attempted

    async def _page_route_for(self, user_id: UUID, channel: str) -> _PageRoute:
        """Translate a policy's channel name and resolve the responder's routing."""
        from app.notifications.dispatcher import NotificationChannel

        requested = str(channel or "").strip().lower()
        try:
            dispatch_channel = NotificationChannel(
                _CHANNEL_ALIASES.get(requested, requested)
            )
        except ValueError:
            return _PageRoute(
                channel=None, missing=f"unknown notification channel {channel!r}"
            )
        return await self._page_routing(user_id, dispatch_channel)

    async def _page_routing(
        self,
        user_id: UUID,
        dispatch_channel: NotificationChannel,
    ) -> _PageRoute:
        """Where this responder can be reached, from what the deployment stores.

        Only the channel the escalation policy asked for is enabled: the policy is
        the authority on how this step pages, and the dispatcher's job here is to
        deliver on that channel or to say why it could not — not to substitute a
        default set of channels nobody chose.

        Two stores hold what a responder can be reached at, and both are read:

        * `members.email`, which is what every on-call `user_id` foreign key
          points at. This used to be the only one consulted, and nothing in
          `app/` wrote it — `/admin/members` and SCIM add people to the separate
          aiosqlite tenancy store — so on a deployment provisioned only through
          this product's own API the select returned nothing for every responder
          and every targeted page went unaddressed. Adding a responder to an
          on-call team by email now writes that row.
        * `OncallTeamMember.contact_methods`, which is where a phone number or a
          Slack id goes. It had neither reader nor writer before, which is why
          sms, voice, whatsapp and Slack pages could not be addressed at all,
          however the deployment was configured. Memberships are read across all
          of the responder's teams: a phone number is a property of the person,
          not of one team, and a page must not fail because it was entered
          against a different team of theirs.

        Push remains unaddressable and says so — no endpoint in this build lets a
        browser register a device — and channels that are not per-responder at all
        (webhook, teams, discord, telegram) are addressed by deployment
        configuration, so nothing is claimed to be missing about the person.

        Whatever is absent is reported through `_PageRoute.missing`, which becomes
        the recorded failure reason on the notification row; a step where no target
        could be reached then goes through `_nobody_was_reached`, so the alert
        still reaches the org's own configured channels rather than nobody. What
        this will not do is invent a personal address: a responder nobody has
        recorded an address for produces the reason, never a reported send that
        did not happen.
        """
        from app.notifications.dispatcher import NotificationChannel, NotificationPreferences

        # Skipped entirely for webhook/teams/discord/telegram: those are addressed
        # by deployment configuration, so there is nothing about the responder to
        # look up and no reason to spend a query on every page.
        addresses: dict[str, str] = {}
        if dispatch_channel.value in _PERSONALLY_ADDRESSED:
            addresses = await self._recorded_contact_addresses(user_id)

        email = ""
        if dispatch_channel is NotificationChannel.EMAIL:
            email = (
                await self.db.execute(select(Member.email).where(Member.id == user_id))
            ).scalar_one_or_none() or ""
            # `members.email` is the address of record; a contact method of type
            # email is a per-team override for people who page to a different
            # mailbox than the one they sign in with.
            email = email or addresses.get("email", "")

        phone = ""
        if dispatch_channel in (
            NotificationChannel.SMS,
            NotificationChannel.VOICE,
            NotificationChannel.WHATSAPP,
        ):
            phone = addresses.get("phone", "")

        slack_user_id = ""
        if dispatch_channel is NotificationChannel.SLACK:
            slack_user_id = addresses.get("slack", "")

        destination = email or phone or slack_user_id
        missing = ""
        if not destination:
            # Channels absent from the table — webhook, teams, discord, telegram —
            # are addressed by deployment configuration rather than by anything
            # about the responder, so there is nothing missing here to report and
            # the dispatcher's own answer is the accurate one.
            missing = _MISSING_ROUTING.get(dispatch_channel.value, "")
            if missing:
                log.warning("Cannot address a %s page to user %s: %s",
                            dispatch_channel.value, user_id, missing)

        return _PageRoute(
            channel=dispatch_channel,
            preferences=NotificationPreferences(
                user_id=str(user_id),
                email=email or None,
                phone=phone or None,
                slack_user_id=slack_user_id or None,
                enabled_channels={dispatch_channel},
            ),
            destination=destination,
            missing=missing,
        )

    async def _recorded_contact_addresses(self, user_id: UUID) -> dict[str, str]:
        """Every address recorded for this responder, by kind, first one wins.

        One query per page rather than per channel, and the memberships are
        ordered oldest-first so that which address answers is stable rather than
        whatever the database happened to return.
        """
        rows = (
            await self.db.execute(
                select(OncallTeamMember.contact_methods)
                .where(
                    OncallTeamMember.user_id == user_id,
                    OncallTeamMember.is_active.is_(True),
                )
                .order_by(OncallTeamMember.joined_at)
            )
        ).scalars().all()

        addresses: dict[str, str] = {}
        for methods in rows:
            for kind, value in contact_addresses(methods).items():
                addresses.setdefault(kind, value)
        return addresses

    async def _send_webhook(self, url: str, alert: OncallAlert) -> bool:
        """Send alert to webhook. True when the far end accepted it.

        The answer matters beyond the log line: a webhook target is one of the
        ways an escalation step can reach a human, so whether it worked is part
        of deciding whether this step reached anybody at all. The status code
        used to be discarded — the response was entered and left without being
        read — so a 500 from the customer's endpoint was logged as "Sent".
        """
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
                ) as response:
                    accepted = response.status < 400
                    status = response.status
        except Exception as e:
            log.error("Failed to send webhook for alert %s: %s", alert.id, e)
            return False

        if accepted:
            log.info("Sent webhook for alert %s to %s", alert.id, url)
        else:
            log.error(
                "Webhook for alert %s to %s was rejected with HTTP %s",
                alert.id, url, status,
            )
        return accepted

    async def _send_slack_alert(self, channel_id: str, alert: OncallAlert) -> bool:
        """Send alert to Slack channel. True when Slack accepted the message.

        As with `_send_webhook`, the caller needs the answer to know whether this
        escalation step reached anybody; an unset bot token or a rejected post
        must not count as somebody having been told.
        """
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
            return False

        try:
            response = await post_message(
                token,
                channel_id,
                text=f"{emoji} {alert.severity.upper()}: {alert.title}",
                blocks=blocks,
            )
        except Exception as e:  # noqa: BLE001 — one target must not stop the page
            log.error("Slack alert for %s failed: %s", alert.id, e)
            return False

        if response.get("ok"):
            log.info("Sent Slack alert for %s to channel %s", alert.id, channel_id)
            return True

        log.error(
            "Slack rejected the alert for %s to channel %s: %s",
            alert.id, channel_id, response.get("error") or response.get("skipped"),
        )
        return False

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
        user_id: UUID | None,
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
        user_id: UUID | None,
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
            # Rebound, not mutated in place: `custom_fields` is a plain JSON
            # column with no mutation tracking, so assigning into the existing
            # dict left the attribute clean and the commit wrote nothing. The
            # note the responder typed to explain what they did was discarded
            # every time, silently.
            alert.custom_fields = {
                **(alert.custom_fields or {}),
                "resolution_notes": resolution_notes,
            }

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
