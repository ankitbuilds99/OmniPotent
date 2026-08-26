"""SQLAlchemy models for team/workspace persistence.

This module existed but could not be imported: it did `from app.database.base
import Base`, and there is no `app/database/base.py` — the declarative `Base`
lives in `app.database.models`. So nothing imported it, no migration created its
tables, and `app/teams/store.py` stayed a process-memory dict whose teams,
rosters and pending invitations vanished on every restart and deploy.

Two changes beyond fixing the import:

* Every table carries `org_id`. The in-memory store got its tenant isolation
  from `scoped()` — one dict per org — which does not survive the move to a
  shared table. Here the org is a column, filtered on every read and every
  write, the same way `incidents` does it.
* The `AuditLogDB` class that used to live here is gone. It claimed
  `__tablename__ = "audit_logs"`, which `app.database.models.AuditLog` already
  owns with an entirely different, hash-chained shape (`actor`/`event_type`/
  `data`/`prev_hash`/`entry_hash`) — the one migration 001 actually creates.
  Importing this module with both classes present would have been a duplicate
  table error on the shared metadata. It had zero usages.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.models import Base
from app.database.types import JSONB, UUID


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TeamDB(Base):
    """A team/workspace owned by one organization."""

    __tablename__ = "teams"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False)
    owner_id: Mapped[str] = mapped_column(String(255), nullable=False)
    settings: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )

    members: Mapped[list["TeamMemberDB"]] = relationship(
        "TeamMemberDB", back_populates="team", cascade="all, delete-orphan"
    )
    invitations: Mapped[list["TeamInvitationDB"]] = relationship(
        "TeamInvitationDB", back_populates="team", cascade="all, delete-orphan"
    )

    __table_args__ = (
        # Slugs were globally unique. That is wrong for a multi-tenant table on
        # two counts: one org taking "platform" would deny it to every other
        # org, and the 409 would tell them it was taken — a slow enumeration of
        # other tenants' team names. Unique per org is what the URL needs.
        UniqueConstraint("org_id", "slug", name="uq_team_org_slug"),
        Index("ix_teams_org_owner", "org_id", "owner_id"),
    )


class TeamMemberDB(Base):
    """A user's membership in a team."""

    __tablename__ = "team_members"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    team_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    # Plain string, not a native Enum: `APIKey.role` and every other role column
    # in this schema is a String, and a Postgres enum has to be ALTERed by hand
    # to add a value.
    role: Mapped[str] = mapped_column(String(16), default="member", nullable=False)
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
    last_active: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    team: Mapped["TeamDB"] = relationship("TeamDB", back_populates="members")

    __table_args__ = (
        UniqueConstraint("team_id", "user_id", name="uq_team_member"),
        Index("ix_team_members_org_user", "org_id", "user_id"),
    )


class TeamInvitationDB(Base):
    """An outstanding invitation to join a team."""

    __tablename__ = "team_invitations"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    team_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(16), default="member", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    invited_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    team: Mapped["TeamDB"] = relationship("TeamDB", back_populates="invitations")

    __table_args__ = (
        Index("ix_team_invitations_org_email", "org_id", "email"),
        Index("ix_team_invitations_team_status", "team_id", "status"),
    )
