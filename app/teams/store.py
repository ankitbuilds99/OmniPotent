"""Durable team store.

This was "In-memory store for teams (swap for database in production)" —
four dicts on a `TeamStore` instance. Every team a customer created, every
member they added and every invitation they sent was lost on the next restart or
deploy, and the API gave no sign of it: create returned 200, the roster read back
correctly, and then the process recycled.

`scoped(TeamStore)` gave each org its own dict, which stopped one tenant reading
another's roster but did nothing about the loss. It is now the `teams` /
`team_members` / `team_invitations` tables, and the isolation is an `org_id`
filter on every statement rather than a matter of which in-process instance the
caller happened to be handed.

Every method is async and opens its own session; there is no per-org instance to
keep, so `get_team_store()` returns one stateless object.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.database.orgs import ensure_org_row, org_uuid
from app.teams.models import (
    InvitationStatus,
    Team,
    TeamInvitation,
    TeamMember,
    TeamRole,
)


class SlugTaken(ValueError):
    """The caller asked for a slug another team in this org already holds."""


# How long an invitation stays redeemable. Mirrors the default on
# `TeamInvitation.expires_at`, which is what the API documents.
INVITATION_TTL = timedelta(days=7)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(value: datetime | None) -> Any:
    """Read a timestamp back as UTC-aware.

    SQLite, the default database, has no timezone type: a
    `DateTime(timezone=True)` column round trips to a naive datetime. Everything
    written here is UTC, but without this an `expires_at` read from SQLite
    cannot be compared against `_now()` at all — naive-vs-aware raises TypeError.
    """
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def _to_team(row: Any) -> Team:
    return Team(
        id=row.id,
        name=row.name,
        slug=row.slug,
        owner_id=row.owner_id,
        created_at=_utc(row.created_at),
        updated_at=_utc(row.updated_at),
        settings=dict(row.settings or {}),
    )


def _to_member(row: Any) -> TeamMember:
    return TeamMember(
        id=row.id,
        team_id=row.team_id,
        user_id=row.user_id,
        email=row.email,
        role=TeamRole(row.role),
        joined_at=_utc(row.joined_at),
        last_active=_utc(row.last_active),
    )


def _to_invitation(row: Any) -> TeamInvitation:
    return TeamInvitation(
        id=row.id,
        team_id=row.team_id,
        email=row.email,
        role=TeamRole(row.role),
        invited_by=row.invited_by,
        status=InvitationStatus(row.status),
        created_at=_utc(row.created_at),
        expires_at=_utc(row.expires_at),
        accepted_at=_utc(row.accepted_at),
    )


class TeamStore:
    """Team persistence for the org on the current request.

    Stateless: every method opens its own session and filters on `org_uuid()`.
    """

    # --- teams ------------------------------------------------------------ #

    async def _generate_slug(self, session: Any, org: UUID, name: str) -> str:
        """A URL-safe slug that no other team in this org holds."""
        from app.database.team_models import TeamDB

        base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "team"
        taken = set(
            (
                await session.execute(
                    select(TeamDB.slug).where(
                        TeamDB.org_id == org, TeamDB.slug.startswith(base)
                    )
                )
            )
            .scalars()
            .all()
        )
        slug = base
        counter = 1
        while slug in taken:
            slug = f"{base}-{counter}"
            counter += 1
        return slug

    async def create_team(
        self, name: str, owner_id: str, owner_email: str, slug: str | None = None
    ) -> Team:
        """Create a team with the caller as its owner.

        Raises `SlugTaken` if an explicitly requested slug is already in use.
        The in-memory version only de-duplicated slugs it generated itself, so a
        caller-supplied duplicate was accepted and produced two teams sharing a
        URL.
        """
        from app.database.engine import get_session
        from app.database.team_models import TeamDB, TeamMemberDB

        async with get_session() as session:
            org = await ensure_org_row(session)
            team_id = str(uuid4())
            resolved_slug = slug or await self._generate_slug(session, org, name)
            team = TeamDB(
                id=team_id,
                org_id=org,
                name=name,
                slug=resolved_slug,
                owner_id=owner_id,
                settings={},
            )
            session.add(team)
            session.add(
                TeamMemberDB(
                    org_id=org,
                    team_id=team_id,
                    user_id=owner_id,
                    email=owner_email or "",
                    role=TeamRole.OWNER.value,
                )
            )
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                raise SlugTaken(f"A team with the slug {resolved_slug!r} already exists.") from None
            await session.refresh(team)
            return _to_team(team)

    async def get_team(self, team_id: str) -> Team | None:
        from app.database.engine import get_session

        async with get_session() as session:
            row = await self._team_row(session, team_id)
            return _to_team(row) if row else None

    async def _team_row(self, session: Any, team_id: str) -> Any:
        from app.database.team_models import TeamDB

        if not _looks_like_uuid(team_id):
            # The id is a UUID column; a non-UUID path parameter is a 404, not a
            # DataError that surfaces as a 500.
            return None
        return (
            await session.execute(
                select(TeamDB).where(TeamDB.id == team_id, TeamDB.org_id == org_uuid())
            )
        ).scalar_one_or_none()

    async def update_team(self, team_id: str, updates: dict[str, Any]) -> Team | None:
        from app.database.engine import get_session

        async with get_session() as session:
            row = await self._team_row(session, team_id)
            if row is None:
                return None
            for key, value in updates.items():
                if value is not None and key in ("name", "settings"):
                    setattr(row, key, value)
            row.updated_at = _now()
            await session.commit()
            await session.refresh(row)
            return _to_team(row)

    async def delete_team(self, team_id: str) -> bool:
        """Delete a team along with its roster and invitations."""
        from app.database.engine import get_session
        from app.database.team_models import TeamDB

        async with get_session() as session:
            if not _looks_like_uuid(team_id):
                return False
            row = (
                await session.execute(
                    select(TeamDB)
                    .options(selectinload(TeamDB.members), selectinload(TeamDB.invitations))
                    .where(TeamDB.id == team_id, TeamDB.org_id == org_uuid())
                )
            ).scalar_one_or_none()
            if row is None:
                return False
            # Deleting through the ORM rather than issuing a bare DELETE: the FK
            # is ON DELETE CASCADE, but SQLite does not enforce foreign keys
            # unless PRAGMA foreign_keys is on, so on the default database the
            # children would be orphaned rather than removed.
            await session.delete(row)
            await session.commit()
            return True

    async def get_user_teams(self, user_id: str) -> list[tuple[Team, TeamRole, int]]:
        """Every team the user belongs to, with their role and the member count.

        The count comes back in the same query. The router used to call
        `get_members()` once per team purely to call `len()` on it, which is a
        query per row on the team list page.
        """
        from app.database.engine import get_session
        from app.database.team_models import TeamDB, TeamMemberDB

        org = org_uuid()
        counts = (
            select(TeamMemberDB.team_id, func.count().label("n"))
            .where(TeamMemberDB.org_id == org)
            .group_by(TeamMemberDB.team_id)
            .subquery()
        )
        async with get_session() as session:
            rows = (
                await session.execute(
                    select(TeamDB, TeamMemberDB.role, func.coalesce(counts.c.n, 0))
                    .join(TeamMemberDB, TeamMemberDB.team_id == TeamDB.id)
                    .outerjoin(counts, counts.c.team_id == TeamDB.id)
                    .where(
                        TeamDB.org_id == org,
                        TeamMemberDB.org_id == org,
                        TeamMemberDB.user_id == user_id,
                    )
                    .order_by(TeamDB.created_at)
                )
            ).all()
            return [(_to_team(team), TeamRole(role), int(count)) for team, role, count in rows]

    # --- members ----------------------------------------------------------- #

    async def count_members(self, team_id: str) -> int:
        from app.database.engine import get_session
        from app.database.team_models import TeamMemberDB

        if not _looks_like_uuid(team_id):
            return 0
        async with get_session() as session:
            return int(
                (
                    await session.execute(
                        select(func.count())
                        .select_from(TeamMemberDB)
                        .where(
                            TeamMemberDB.team_id == team_id,
                            TeamMemberDB.org_id == org_uuid(),
                        )
                    )
                ).scalar_one()
            )

    async def get_members(self, team_id: str) -> list[TeamMember]:
        from app.database.engine import get_session
        from app.database.team_models import TeamMemberDB

        if not _looks_like_uuid(team_id):
            return []
        async with get_session() as session:
            rows = (
                (
                    await session.execute(
                        select(TeamMemberDB)
                        .where(
                            TeamMemberDB.team_id == team_id,
                            TeamMemberDB.org_id == org_uuid(),
                        )
                        .order_by(TeamMemberDB.joined_at)
                    )
                )
                .scalars()
                .all()
            )
            return [_to_member(row) for row in rows]

    async def get_member(self, team_id: str, user_id: str) -> TeamMember | None:
        from app.database.engine import get_session

        async with get_session() as session:
            row = await self._member_row(session, team_id, user_id)
            return _to_member(row) if row else None

    async def _member_row(self, session: Any, team_id: str, user_id: str) -> Any:
        from app.database.team_models import TeamMemberDB

        if not _looks_like_uuid(team_id):
            return None
        return (
            await session.execute(
                select(TeamMemberDB).where(
                    TeamMemberDB.team_id == team_id,
                    TeamMemberDB.user_id == user_id,
                    TeamMemberDB.org_id == org_uuid(),
                )
            )
        ).scalar_one_or_none()

    async def get_member_by_email(self, team_id: str, email: str) -> TeamMember | None:
        from app.database.engine import get_session
        from app.database.team_models import TeamMemberDB

        if not _looks_like_uuid(team_id):
            return None
        async with get_session() as session:
            row = (
                await session.execute(
                    select(TeamMemberDB).where(
                        TeamMemberDB.team_id == team_id,
                        func.lower(TeamMemberDB.email) == email.strip().lower(),
                        TeamMemberDB.org_id == org_uuid(),
                    )
                )
            ).scalar_one_or_none()
            return _to_member(row) if row else None

    async def add_member(
        self, team_id: str, user_id: str, email: str, role: TeamRole = TeamRole.MEMBER
    ) -> TeamMember | None:
        from app.database.engine import get_session
        from app.database.team_models import TeamMemberDB

        async with get_session() as session:
            team = await self._team_row(session, team_id)
            if team is None:
                return None
            if await self._member_row(session, team_id, user_id) is not None:
                return None
            row = TeamMemberDB(
                org_id=team.org_id,
                team_id=team_id,
                user_id=user_id,
                email=email or "",
                role=role.value,
            )
            session.add(row)
            try:
                await session.commit()
            except IntegrityError:
                # Two accepts of the same invitation raced; uq_team_member won.
                await session.rollback()
                return None
            await session.refresh(row)
            return _to_member(row)

    async def update_member_role(
        self, team_id: str, user_id: str, role: TeamRole
    ) -> TeamMember | None:
        from app.database.engine import get_session

        async with get_session() as session:
            row = await self._member_row(session, team_id, user_id)
            if row is None or row.role == TeamRole.OWNER.value:
                return None  # the owner's role is not changeable
            row.role = role.value
            await session.commit()
            await session.refresh(row)
            return _to_member(row)

    async def remove_member(self, team_id: str, user_id: str) -> bool:
        from app.database.engine import get_session

        async with get_session() as session:
            row = await self._member_row(session, team_id, user_id)
            if row is None or row.role == TeamRole.OWNER.value:
                return False  # the owner cannot be removed
            await session.delete(row)
            await session.commit()
            return True

    async def update_member_activity(self, team_id: str, user_id: str) -> None:
        from app.database.engine import get_session

        async with get_session() as session:
            row = await self._member_row(session, team_id, user_id)
            if row is not None:
                row.last_active = _now()
                await session.commit()

    # --- invitations ------------------------------------------------------- #

    async def _expire_stale(self, session: Any) -> None:
        """Flip pending invitations that have run out of time.

        The in-memory store did this on read by mutating the object it was about
        to return, so an invitation only expired if somebody happened to list
        it. Doing it as one UPDATE means `accept_invitation` sees the same truth
        as `get_invitations`.
        """
        from app.database.team_models import TeamInvitationDB

        await session.execute(
            update(TeamInvitationDB)
            .where(
                TeamInvitationDB.org_id == org_uuid(),
                TeamInvitationDB.status == InvitationStatus.PENDING.value,
                TeamInvitationDB.expires_at < _now(),
            )
            .values(status=InvitationStatus.EXPIRED.value)
        )
        await session.commit()

    async def get_invitations(self, team_id: str) -> list[TeamInvitation]:
        """Pending invitations for a team."""
        from app.database.engine import get_session
        from app.database.team_models import TeamInvitationDB

        if not _looks_like_uuid(team_id):
            return []
        async with get_session() as session:
            await self._expire_stale(session)
            rows = (
                (
                    await session.execute(
                        select(TeamInvitationDB)
                        .where(
                            TeamInvitationDB.team_id == team_id,
                            TeamInvitationDB.org_id == org_uuid(),
                            TeamInvitationDB.status == InvitationStatus.PENDING.value,
                        )
                        .order_by(TeamInvitationDB.created_at)
                    )
                )
                .scalars()
                .all()
            )
            return [_to_invitation(row) for row in rows]

    async def get_invitation(self, invitation_id: str) -> TeamInvitation | None:
        from app.database.engine import get_session

        async with get_session() as session:
            row = await self._invitation_row(session, invitation_id)
            return _to_invitation(row) if row else None

    async def _invitation_row(self, session: Any, invitation_id: str) -> Any:
        from app.database.team_models import TeamInvitationDB

        if not _looks_like_uuid(invitation_id):
            return None
        return (
            await session.execute(
                select(TeamInvitationDB).where(
                    TeamInvitationDB.id == invitation_id,
                    TeamInvitationDB.org_id == org_uuid(),
                )
            )
        ).scalar_one_or_none()

    async def get_invitation_by_email(self, team_id: str, email: str) -> TeamInvitation | None:
        from app.database.engine import get_session
        from app.database.team_models import TeamInvitationDB

        if not _looks_like_uuid(team_id):
            return None
        async with get_session() as session:
            row = (
                await session.execute(
                    select(TeamInvitationDB).where(
                        TeamInvitationDB.team_id == team_id,
                        func.lower(TeamInvitationDB.email) == email.strip().lower(),
                        TeamInvitationDB.org_id == org_uuid(),
                        TeamInvitationDB.status == InvitationStatus.PENDING.value,
                    )
                )
            ).scalar_one_or_none()
            return _to_invitation(row) if row else None

    async def create_invitation(
        self, team_id: str, email: str, role: TeamRole, invited_by: str
    ) -> TeamInvitation | None:
        from app.database.engine import get_session
        from app.database.team_models import TeamInvitationDB

        if await self.get_member_by_email(team_id, email) is not None:
            return None
        if await self.get_invitation_by_email(team_id, email) is not None:
            return None

        async with get_session() as session:
            team = await self._team_row(session, team_id)
            if team is None:
                return None
            row = TeamInvitationDB(
                org_id=team.org_id,
                team_id=team_id,
                email=email,
                role=role.value,
                status=InvitationStatus.PENDING.value,
                invited_by=invited_by,
                expires_at=_now() + INVITATION_TTL,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return _to_invitation(row)

    async def accept_invitation(
        self, invitation_id: str, user_id: str
    ) -> tuple[TeamInvitation | None, TeamMember | None]:
        """Redeem an invitation and add the user to the team."""
        from app.database.engine import get_session

        async with get_session() as session:
            await self._expire_stale(session)
            row = await self._invitation_row(session, invitation_id)
            if row is None:
                return None, None
            invitation = _to_invitation(row)

        if invitation.status != InvitationStatus.PENDING:
            return invitation, None

        member = await self.add_member(
            invitation.team_id, user_id, invitation.email, invitation.role
        )
        if member is None:
            return invitation, None

        async with get_session() as session:
            fresh = await self._invitation_row(session, invitation_id)
            if fresh is None:
                return invitation, member
            fresh.status = InvitationStatus.ACCEPTED.value
            fresh.accepted_at = _now()
            await session.commit()
            return _to_invitation(fresh), member

    async def cancel_invitation(self, invitation_id: str) -> bool:
        from app.database.engine import get_session

        async with get_session() as session:
            row = await self._invitation_row(session, invitation_id)
            if row is None or row.status != InvitationStatus.PENDING.value:
                return False
            row.status = InvitationStatus.CANCELLED.value
            await session.commit()
            return True

    async def get_user_pending_invitations(
        self, email: str
    ) -> list[tuple[TeamInvitation, Team]]:
        """Every pending invitation addressed to an email, with its team."""
        from app.database.engine import get_session
        from app.database.team_models import TeamDB, TeamInvitationDB

        if not email:
            return []
        async with get_session() as session:
            await self._expire_stale(session)
            org = org_uuid()
            rows = (
                await session.execute(
                    select(TeamInvitationDB, TeamDB)
                    .join(TeamDB, TeamDB.id == TeamInvitationDB.team_id)
                    .where(
                        TeamInvitationDB.org_id == org,
                        TeamDB.org_id == org,
                        func.lower(TeamInvitationDB.email) == email.strip().lower(),
                        TeamInvitationDB.status == InvitationStatus.PENDING.value,
                    )
                    .order_by(TeamInvitationDB.created_at)
                )
            ).all()
            return [(_to_invitation(inv), _to_team(team)) for inv, team in rows]


def _looks_like_uuid(value: str) -> bool:
    """Whether a path parameter can be compared against a UUID column at all.

    Postgres raises `invalid input syntax for type uuid` on a malformed literal,
    which reaches the client as a 500. The id came from the URL, so a caller can
    trigger that with any string.
    """
    try:
        UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return False
    return True


_STORE = TeamStore()


def get_team_store() -> TeamStore:
    """The team store.

    Stateless and shared: the org comes from `org_uuid()` inside each method, so
    there is no per-org instance to hand out any more.
    """
    return _STORE
