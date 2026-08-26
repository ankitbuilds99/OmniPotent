"""Notification preferences API.

There was no route at all behind the Notification Preferences page:
`GET /api/notifications/preferences -> 404`, and nothing in the route dump
matched "notif". Everything the page showed was a component literal and its Save
button issued zero requests, so a Slack webhook pasted into it lived exactly as
long as the tab.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.notifications.preferences import (
    CHANNELS_BY_ID,
    DIGEST_MODES,
    EVENTS_BY_ID,
    get_preferences,
    save_preferences,
)

log = logging.getLogger("omnipotent.notifications.router")

router = APIRouter(prefix="/notifications", tags=["notifications"])


def _require_perm(action: str) -> Any:
    """The app's RBAC dependency, imported lazily to avoid a circular import.

    `app.api.main` imports this router, so importing `require_perm` from it at
    module scope would be a cycle.
    """

    async def _dep() -> None:
        from app.api.main import require_perm

        await require_perm(action)()

    return _dep


class ChannelUpdate(BaseModel):
    id: str
    enabled: bool | None = None
    #: Only the keys present are written; a secret field sent as "" is left
    #: untouched (see CLEAR_SECRET in app/notifications/preferences.py).
    config: dict[str, str] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def _known_channel(cls, v: str) -> str:
        if v not in CHANNELS_BY_ID:
            raise ValueError(
                f"unknown channel '{v}'; supported: {', '.join(sorted(CHANNELS_BY_ID))}"
            )
        return v


class RuleUpdate(BaseModel):
    event: str
    channels: dict[str, bool] = Field(default_factory=dict)

    @field_validator("event")
    @classmethod
    def _known_event(cls, v: str) -> str:
        if v not in EVENTS_BY_ID:
            raise ValueError(
                f"unknown event '{v}'; supported: {', '.join(sorted(EVENTS_BY_ID))}"
            )
        return v

    @field_validator("channels")
    @classmethod
    def _known_channels(cls, v: dict[str, bool]) -> dict[str, bool]:
        unknown = sorted(set(v) - set(CHANNELS_BY_ID))
        if unknown:
            raise ValueError(f"unknown channel(s): {', '.join(unknown)}")
        return v


class QuietHours(BaseModel):
    enabled: bool = False
    start: str = "22:00"
    end: str = "08:00"

    @field_validator("start", "end")
    @classmethod
    def _hhmm(cls, v: str) -> str:
        parts = v.split(":")
        if len(parts) != 2 or not all(p.isdigit() for p in parts):
            raise ValueError("expected HH:MM")
        hour, minute = int(parts[0]), int(parts[1])
        if not (0 <= hour < 24 and 0 <= minute < 60):
            raise ValueError("expected a valid 24-hour HH:MM time")
        return f"{hour:02d}:{minute:02d}"


class PreferencesUpdate(BaseModel):
    channels: list[ChannelUpdate] = Field(default_factory=list)
    rules: list[RuleUpdate] = Field(default_factory=list)
    quiet_hours: QuietHours | None = None
    digest_mode: str | None = None

    @field_validator("digest_mode")
    @classmethod
    def _known_digest(cls, v: str | None) -> str | None:
        if v is not None and v not in DIGEST_MODES:
            raise ValueError(f"digest_mode must be one of {', '.join(DIGEST_MODES)}")
        return v


@router.get(
    "/preferences",
    summary="Get notification channels and per-event routing rules",
    description="The org's notification channels, the per-event routing matrix and "
    "the delivery settings. Values for secret fields are never returned — each "
    "field reports `is_set` instead.",
    dependencies=[Depends(_require_perm("view"))],
)
async def read_preferences() -> dict[str, Any]:
    return await get_preferences()


@router.put(
    "/preferences",
    summary="Save notification channels and per-event routing rules",
    description="Persists the channels, routing rules, quiet hours and digest mode "
    "for the current org and returns the stored result. Unknown channel or event "
    "ids are rejected rather than silently dropped.",
    dependencies=[Depends(_require_perm("manage_integrations"))],
)
async def write_preferences(update: PreferencesUpdate) -> dict[str, Any]:
    duplicate_channels = [
        cid
        for cid in {c.id for c in update.channels}
        if sum(1 for c in update.channels if c.id == cid) > 1
    ]
    if duplicate_channels:
        raise HTTPException(
            422, f"channel listed more than once: {', '.join(sorted(duplicate_channels))}"
        )
    return await save_preferences(
        channels=[c.model_dump() for c in update.channels],
        rules=[r.model_dump() for r in update.rules],
        quiet_hours=update.quiet_hours.model_dump() if update.quiet_hours else None,
        digest_mode=update.digest_mode,
    )
