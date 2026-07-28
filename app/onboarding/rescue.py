"""Rescue campaigns for stuck users.

Automated re-engagement for users who stall during onboarding.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("devcopilot.onboarding.rescue")


class RescueTriggerType(str, Enum):
    NO_INVESTIGATION = "no_investigation"
    INTEGRATION_FAILED = "integration_failed"
    LIMIT_REACHED = "limit_reached"
    NO_LOGIN = "no_login"
    NO_TEAMMATE = "no_teammate"
    STALLED_ONBOARDING = "stalled_onboarding"


class RescueActionType(str, Enum):
    EMAIL = "email"
    IN_APP = "in_app"
    SLACK = "slack"
    LIVE_CHAT = "live_chat"
    BONUS = "bonus"


class RescueStatus(str, Enum):
    PENDING = "pending"
    SENT = "sent"
    OPENED = "opened"
    CLICKED = "clicked"
    CONVERTED = "converted"
    DISMISSED = "dismissed"


@dataclass
class RescueTrigger:
    """A trigger condition for rescue campaign."""

    trigger_type: RescueTriggerType
    condition: str
    delay_hours: int
    priority: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.trigger_type.value,
            "condition": self.condition,
            "delay_hours": self.delay_hours,
            "priority": self.priority,
        }


@dataclass
class RescueAction:
    """An action to take for rescue."""

    action_type: RescueActionType
    template: str
    subject: str = ""
    bonus_value: int = 0
    cta_text: str = ""
    cta_url: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.action_type.value,
            "template": self.template,
            "subject": self.subject,
            "bonus_value": self.bonus_value,
            "cta_text": self.cta_text,
        }


@dataclass
class RescueCampaign:
    """A rescue campaign configuration."""

    campaign_id: str
    name: str
    trigger: RescueTrigger
    actions: list[RescueAction]
    is_active: bool = True
    max_sends_per_user: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.campaign_id,
            "name": self.name,
            "trigger": self.trigger.to_dict(),
            "actions": [a.to_dict() for a in self.actions],
            "is_active": self.is_active,
        }


@dataclass
class RescueRecord:
    """Record of a rescue action sent to a user."""

    record_id: str
    user_id: str
    campaign_id: str
    action_type: RescueActionType
    status: RescueStatus = RescueStatus.PENDING
    sent_at: datetime | None = None
    opened_at: datetime | None = None
    clicked_at: datetime | None = None
    converted_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.record_id,
            "user_id": self.user_id,
            "campaign_id": self.campaign_id,
            "action_type": self.action_type.value,
            "status": self.status.value,
            "sent_at": self.sent_at.isoformat() if self.sent_at else None,
        }


class RescueEngine:
    """Engine for rescue campaigns."""

    def __init__(self) -> None:
        self._campaigns: dict[str, RescueCampaign] = {}
        self._records: dict[str, list[RescueRecord]] = {}
        self._user_state: dict[str, dict[str, Any]] = {}
        self._init_default_campaigns()

    def _init_default_campaigns(self) -> None:
        """Initialize default rescue campaigns."""
        self._campaigns["no_investigation_24h"] = RescueCampaign(
            campaign_id="no_investigation_24h",
            name="No Investigation After 24 Hours",
            trigger=RescueTrigger(
                RescueTriggerType.NO_INVESTIGATION,
                "signup > 24h AND investigations = 0",
                delay_hours=24,
                priority=1,
            ),
            actions=[
                RescueAction(
                    RescueActionType.EMAIL,
                    "no_investigation",
                    subject="Ready to investigate? Your setup is complete!",
                    cta_text="Run First Investigation",
                    cta_url="/investigate",
                ),
            ],
        )

        self._campaigns["integration_failed"] = RescueCampaign(
            campaign_id="integration_failed",
            name="Integration Failed",
            trigger=RescueTrigger(
                RescueTriggerType.INTEGRATION_FAILED,
                "integration_error",
                delay_hours=0,
                priority=2,
            ),
            actions=[
                RescueAction(
                    RescueActionType.EMAIL,
                    "integration_help",
                    subject="Need help connecting? We're here!",
                    cta_text="Get Help",
                    cta_url="/help/integrations",
                ),
                RescueAction(
                    RescueActionType.IN_APP,
                    "integration_help_banner",
                    cta_text="Chat with Support",
                ),
            ],
        )

        self._campaigns["limit_reached_bonus"] = RescueCampaign(
            campaign_id="limit_reached_bonus",
            name="Limit Reached - Bonus Offer",
            trigger=RescueTrigger(
                RescueTriggerType.LIMIT_REACHED,
                "investigations = limit AND tier = free",
                delay_hours=0,
                priority=1,
            ),
            actions=[
                RescueAction(
                    RescueActionType.EMAIL,
                    "limit_bonus",
                    subject="Here's 3 extra investigations on us!",
                    bonus_value=3,
                    cta_text="Claim Bonus",
                    cta_url="/claim-bonus",
                ),
                RescueAction(
                    RescueActionType.BONUS,
                    "grant_bonus",
                    bonus_value=3,
                ),
            ],
        )

        self._campaigns["no_login_7d"] = RescueCampaign(
            campaign_id="no_login_7d",
            name="No Login for 7 Days",
            trigger=RescueTrigger(
                RescueTriggerType.NO_LOGIN,
                "last_login > 7d",
                delay_hours=168,
                priority=3,
            ),
            actions=[
                RescueAction(
                    RescueActionType.EMAIL,
                    "weekly_digest",
                    subject="What's happening in incident response this week",
                    cta_text="See Trends",
                    cta_url="/trends",
                ),
            ],
        )

        self._campaigns["no_teammate_14d"] = RescueCampaign(
            campaign_id="no_teammate_14d",
            name="Active User Without Teammates",
            trigger=RescueTrigger(
                RescueTriggerType.NO_TEAMMATE,
                "investigations >= 5 AND teammates = 0 AND signup > 14d",
                delay_hours=336,
                priority=2,
            ),
            actions=[
                RescueAction(
                    RescueActionType.IN_APP,
                    "invite_prompt",
                    cta_text="Invite gets +5 investigations",
                ),
            ],
        )

    def update_user_state(
        self,
        user_id: str,
        **kwargs: Any,
    ) -> None:
        """Update user state for trigger evaluation."""
        if user_id not in self._user_state:
            self._user_state[user_id] = {
                "signup_at": datetime.now(timezone.utc),
                "last_login_at": datetime.now(timezone.utc),
                "investigations": 0,
                "teammates": 0,
                "tier": "free",
                "integration_error": False,
            }

        for key, value in kwargs.items():
            self._user_state[user_id][key] = value

    def check_triggers(self, user_id: str) -> list[RescueCampaign]:
        """Check which campaigns should trigger for a user."""
        state = self._user_state.get(user_id, {})
        triggered = []

        for campaign in self._campaigns.values():
            if not campaign.is_active:
                continue

            if self._should_trigger(user_id, campaign, state):
                triggered.append(campaign)

        triggered.sort(key=lambda c: c.trigger.priority)
        return triggered

    def _should_trigger(
        self,
        user_id: str,
        campaign: RescueCampaign,
        state: dict[str, Any],
    ) -> bool:
        """Check if a campaign should trigger."""
        if self._already_sent(user_id, campaign.campaign_id, campaign.max_sends_per_user):
            return False

        trigger = campaign.trigger
        now = datetime.now(timezone.utc)

        if trigger.trigger_type == RescueTriggerType.NO_INVESTIGATION:
            signup = state.get("signup_at")
            if signup and (now - signup).total_seconds() / 3600 >= trigger.delay_hours:
                if state.get("investigations", 0) == 0:
                    return True

        elif trigger.trigger_type == RescueTriggerType.INTEGRATION_FAILED:
            if state.get("integration_error"):
                return True

        elif trigger.trigger_type == RescueTriggerType.LIMIT_REACHED:
            if state.get("tier") == "free":
                investigations = state.get("investigations", 0)
                if investigations >= 10:
                    return True

        elif trigger.trigger_type == RescueTriggerType.NO_LOGIN:
            last_login = state.get("last_login_at")
            if last_login and (now - last_login).total_seconds() / 3600 >= trigger.delay_hours:
                return True

        elif trigger.trigger_type == RescueTriggerType.NO_TEAMMATE:
            signup = state.get("signup_at")
            if signup and (now - signup).total_seconds() / 3600 >= trigger.delay_hours:
                if state.get("investigations", 0) >= 5 and state.get("teammates", 0) == 0:
                    return True

        return False

    def _already_sent(
        self,
        user_id: str,
        campaign_id: str,
        max_sends: int,
    ) -> bool:
        """Check if campaign was already sent to user."""
        records = self._records.get(user_id, [])
        sent_count = sum(
            1 for r in records
            if r.campaign_id == campaign_id and r.status != RescueStatus.DISMISSED
        )
        return sent_count >= max_sends

    def execute_campaign(
        self,
        user_id: str,
        campaign_id: str,
    ) -> list[RescueRecord]:
        """Execute a rescue campaign for a user."""
        campaign = self._campaigns.get(campaign_id)
        if not campaign:
            return []

        records = []
        now = datetime.now(timezone.utc)

        for action in campaign.actions:
            record = RescueRecord(
                record_id=f"rescue_{user_id}_{campaign_id}_{action.action_type.value}",
                user_id=user_id,
                campaign_id=campaign_id,
                action_type=action.action_type,
                status=RescueStatus.SENT,
                sent_at=now,
            )

            if user_id not in self._records:
                self._records[user_id] = []
            self._records[user_id].append(record)
            records.append(record)

            log.info(
                "Rescue action sent: user=%s campaign=%s action=%s",
                user_id,
                campaign_id,
                action.action_type.value,
            )

        return records

    def record_event(
        self,
        user_id: str,
        record_id: str,
        event: str,
    ) -> RescueRecord | None:
        """Record an event on a rescue record."""
        records = self._records.get(user_id, [])
        record = next((r for r in records if r.record_id == record_id), None)

        if not record:
            return None

        now = datetime.now(timezone.utc)

        if event == "opened":
            record.status = RescueStatus.OPENED
            record.opened_at = now
        elif event == "clicked":
            record.status = RescueStatus.CLICKED
            record.clicked_at = now
        elif event == "converted":
            record.status = RescueStatus.CONVERTED
            record.converted_at = now
        elif event == "dismissed":
            record.status = RescueStatus.DISMISSED

        return record

    def get_campaign_stats(self, campaign_id: str) -> dict[str, Any]:
        """Get statistics for a campaign."""
        total_sent = 0
        opened = 0
        clicked = 0
        converted = 0

        for user_records in self._records.values():
            for record in user_records:
                if record.campaign_id == campaign_id:
                    total_sent += 1
                    if record.status in [RescueStatus.OPENED, RescueStatus.CLICKED, RescueStatus.CONVERTED]:
                        opened += 1
                    if record.status in [RescueStatus.CLICKED, RescueStatus.CONVERTED]:
                        clicked += 1
                    if record.status == RescueStatus.CONVERTED:
                        converted += 1

        return {
            "campaign_id": campaign_id,
            "total_sent": total_sent,
            "opened": opened,
            "clicked": clicked,
            "converted": converted,
            "open_rate": opened / total_sent if total_sent > 0 else 0,
            "click_rate": clicked / total_sent if total_sent > 0 else 0,
            "conversion_rate": converted / total_sent if total_sent > 0 else 0,
        }

    def get_user_rescues(self, user_id: str) -> list[RescueRecord]:
        """Get all rescue records for a user."""
        return self._records.get(user_id, [])


_engine: RescueEngine | None = None


def get_rescue_engine() -> RescueEngine:
    """Get the default rescue engine."""
    global _engine
    if _engine is None:
        _engine = RescueEngine()
    return _engine
