"""Activation tracking for onboarding.

Tracks user progress through activation milestones:
- First investigation
- Integration connected
- Team invited
- Alert configured
- Workflow created
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Protocol

log = logging.getLogger("omnipotent.onboarding.flow")


class ActivationMilestone(str, Enum):
    SIGNUP = "signup"
    FIRST_LOGIN = "first_login"
    FIRST_INVESTIGATION = "first_investigation"
    INTEGRATION_CONNECTED = "integration_connected"
    TEAM_INVITED = "team_invited"
    ALERT_CONFIGURED = "alert_configured"
    WORKFLOW_CREATED = "workflow_created"
    FIRST_VALUE = "first_value"


MILESTONE_CONFIG: dict[ActivationMilestone, dict[str, Any]] = {
    ActivationMilestone.SIGNUP: {
        "order": 1,
        "points": 10,
        "description": "Created account",
        "required": True,
    },
    ActivationMilestone.FIRST_LOGIN: {
        "order": 2,
        "points": 10,
        "description": "Logged in for the first time",
        "required": True,
    },
    ActivationMilestone.FIRST_INVESTIGATION: {
        "order": 3,
        "points": 30,
        "description": "Ran first investigation",
        "required": True,
    },
    ActivationMilestone.INTEGRATION_CONNECTED: {
        "order": 4,
        "points": 20,
        "description": "Connected a data source",
        "required": True,
    },
    ActivationMilestone.TEAM_INVITED: {
        "order": 5,
        "points": 15,
        "description": "Invited team member",
        "required": False,
    },
    ActivationMilestone.ALERT_CONFIGURED: {
        "order": 6,
        "points": 10,
        "description": "Set up an alert",
        "required": False,
    },
    ActivationMilestone.WORKFLOW_CREATED: {
        "order": 7,
        "points": 5,
        "description": "Created a workflow",
        "required": False,
    },
    ActivationMilestone.FIRST_VALUE: {
        "order": 8,
        "points": 0,
        "description": "Experienced aha moment",
        "required": True,
    },
}


@dataclass
class ActivationState:
    """User's activation progress."""

    user_id: str
    org_id: str

    completed_milestones: dict[str, datetime] = field(default_factory=dict)
    current_milestone: ActivationMilestone = ActivationMilestone.SIGNUP

    activation_score: int = 0
    is_activated: bool = False

    first_value_at: datetime | None = None
    time_to_value_seconds: int | None = None

    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def completion_percentage(self) -> float:
        required = sum(
            1
            for m, cfg in MILESTONE_CONFIG.items()
            if cfg["required"]
        )
        completed_required = sum(
            1
            for m, cfg in MILESTONE_CONFIG.items()
            if cfg["required"] and m.value in self.completed_milestones
        )
        return (completed_required / required) * 100 if required else 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "org_id": self.org_id,
            "completed_milestones": {
                k: v.isoformat() for k, v in self.completed_milestones.items()
            },
            "current_milestone": self.current_milestone.value,
            "activation_score": self.activation_score,
            "is_activated": self.is_activated,
            "completion_percentage": self.completion_percentage,
            "time_to_value_seconds": self.time_to_value_seconds,
        }


class ActivationStore(Protocol):
    """Protocol for activation data storage."""

    async def get_activation(self, user_id: str) -> ActivationState | None: ...
    async def save_activation(self, state: ActivationState) -> None: ...
    async def get_org_activation_stats(self, org_id: str) -> dict[str, Any]: ...


class ActivationTracker:
    """Tracks user activation through milestones."""

    def __init__(self, store: ActivationStore):
        self._store = store

    async def start_activation(self, user_id: str, org_id: str) -> ActivationState:
        """Initialize activation tracking for new user."""
        now = datetime.now(timezone.utc)

        state = ActivationState(
            user_id=user_id,
            org_id=org_id,
            completed_milestones={ActivationMilestone.SIGNUP.value: now},
            current_milestone=ActivationMilestone.FIRST_LOGIN,
            activation_score=MILESTONE_CONFIG[ActivationMilestone.SIGNUP]["points"],
        )

        await self._store.save_activation(state)
        log.info("Started activation tracking for user %s", user_id)
        return state

    async def complete_milestone(
        self,
        user_id: str,
        milestone: ActivationMilestone,
    ) -> ActivationState:
        """Mark a milestone as completed."""
        state = await self._store.get_activation(user_id)
        if not state:
            raise ValueError(f"No activation state for user: {user_id}")

        if milestone.value in state.completed_milestones:
            return state

        now = datetime.now(timezone.utc)
        state.completed_milestones[milestone.value] = now
        state.activation_score += MILESTONE_CONFIG[milestone]["points"]
        state.updated_at = now

        next_milestone = self._get_next_milestone(state)
        if next_milestone:
            state.current_milestone = next_milestone

        if milestone == ActivationMilestone.FIRST_VALUE:
            state.first_value_at = now
            signup_time = state.completed_milestones.get(ActivationMilestone.SIGNUP.value)
            if signup_time:
                state.time_to_value_seconds = int((now - signup_time).total_seconds())
            state.is_activated = True

        await self._store.save_activation(state)
        await self._emit_milestone_event(user_id, milestone)

        log.info(
            "User %s completed milestone %s (score=%d)",
            user_id,
            milestone.value,
            state.activation_score,
        )
        return state

    def _get_next_milestone(
        self, state: ActivationState
    ) -> ActivationMilestone | None:
        """Get next uncompleted required milestone."""
        for milestone, config in sorted(
            MILESTONE_CONFIG.items(), key=lambda x: x[1]["order"]
        ):
            if config["required"] and milestone.value not in state.completed_milestones:
                return milestone
        return None

    async def _emit_milestone_event(
        self, user_id: str, milestone: ActivationMilestone
    ) -> None:
        """Emit analytics event for milestone completion."""
        log.info(
            "Milestone event: user=%s milestone=%s",
            user_id,
            milestone.value,
        )

    async def get_progress(self, user_id: str) -> dict[str, Any]:
        """Get user's activation progress."""
        state = await self._store.get_activation(user_id)
        if not state:
            return {"error": "No activation state found"}

        milestones = []
        for milestone, config in sorted(
            MILESTONE_CONFIG.items(), key=lambda x: x[1]["order"]
        ):
            completed_at = state.completed_milestones.get(milestone.value)
            milestones.append({
                "id": milestone.value,
                "description": config["description"],
                "points": config["points"],
                "required": config["required"],
                "completed": completed_at is not None,
                "completed_at": completed_at.isoformat() if completed_at else None,
            })

        return {
            "user_id": user_id,
            "milestones": milestones,
            "current": state.current_milestone.value,
            "score": state.activation_score,
            "percentage": state.completion_percentage,
            "is_activated": state.is_activated,
            "time_to_value_seconds": state.time_to_value_seconds,
        }

    async def check_auto_milestones(self, user_id: str, event: str) -> None:
        """Auto-complete milestones based on events."""
        event_to_milestone = {
            "login": ActivationMilestone.FIRST_LOGIN,
            "investigation.started": ActivationMilestone.FIRST_INVESTIGATION,
            "integration.connected": ActivationMilestone.INTEGRATION_CONNECTED,
            "team.member_invited": ActivationMilestone.TEAM_INVITED,
            "alert.created": ActivationMilestone.ALERT_CONFIGURED,
            "workflow.created": ActivationMilestone.WORKFLOW_CREATED,
        }

        milestone = event_to_milestone.get(event)
        if milestone:
            try:
                await self.complete_milestone(user_id, milestone)
            except ValueError:
                pass

    async def get_org_stats(self, org_id: str) -> dict[str, Any]:
        """Get activation statistics for an organization."""
        return await self._store.get_org_activation_stats(org_id)
