"""Persistent storage for an org's notification routing preferences.

The Notification Preferences page had no backend at all: `GET
/api/notifications/preferences` was a 404 and nothing in the 262-route dump
matched "notif". The page hardcoded its nine channels and eight routing rules
in the component, and its Save button was

    const saveConfig = async () => { setSaved(true); setTimeout(...) }

— a two-second checkmark that issued no request. Enabling PagerDuty, pasting a
Slack webhook or switching off deploy notifications all survived exactly until
the next reload.

Declared here rather than in `app/database/models.py` for the same reason
`app/oncall/models.py` and `app/model_router/models.py` are: the tables belong
to the feature, and both `init_db()` and `migrations/env.py` import this module
so they land on the shared `Base.metadata` either way.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.models import Base
from app.database.types import JSONB, UUID


class NotificationChannelPreference(Base):
    """One channel's on/off state and its delivery settings, per org.

    `config` holds the non-secret settings the channel needs (an address, a
    webhook URL, a Jira project key). Values for fields the catalogue marks
    secret are stored in `secrets` and never sent back to the browser — the API
    reports `configured` instead, so a page reload cannot exfiltrate a routing
    key that was set months ago.
    """

    __tablename__ = "notification_channel_preferences"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    channel: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    secrets: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("ix_notif_channel_org_channel", "org_id", "channel", unique=True),
    )


class NotificationRule(Base):
    """Which channels an event fans out to, per org.

    One row per (org, event, channel) rather than a JSON blob per event so that
    "does this org page PagerDuty on deployment_failed" is an indexed lookup
    rather than a document scan, and so a rule for a channel that is later
    removed from the catalogue is a row you can delete.
    """

    __tablename__ = "notification_rules"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    event: Mapped[str] = mapped_column(String(64), nullable=False)
    channel: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("ix_notif_rule_org_event_channel", "org_id", "event", "channel", unique=True),
        Index("ix_notif_rule_org_event", "org_id", "event"),
    )


class NotificationDeliverySettings(Base):
    """Org-wide quiet hours and digest batching.

    The page rendered these as `defaultValue="22:00"` / `defaultValue="realtime"`
    inputs that were never read by anything, so they looked configurable and
    were not.
    """

    __tablename__ = "notification_delivery_settings"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        primary_key=True,
    )
    quiet_hours_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    quiet_hours_start: Mapped[str] = mapped_column(String(5), default="22:00", nullable=False)
    quiet_hours_end: Mapped[str] = mapped_column(String(5), default="08:00", nullable=False)
    digest_mode: Mapped[str] = mapped_column(String(16), default="realtime", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class NotificationDigestEntry(Base):
    """A notification held back because the org batches non-critical ones.

    A digest cannot be assembled from memory: the window is an hour or a day,
    which is longer than a process is guaranteed to live, and an org's digest
    must survive a deploy in the middle of it. `channels` is resolved and stored
    when the entry is queued rather than re-derived at flush time, so editing
    the routing matrix does not silently discard everything already collected.
    """

    __tablename__ = "notification_digest_entries"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    event: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    body: Mapped[str] = mapped_column(Text, default="", nullable=False)
    channels: Mapped[list[str]] = mapped_column(JSONB, default=list)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (Index("ix_notif_digest_org_created", "org_id", "created_at"),)
