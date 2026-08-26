"""Durable team store.

This was "In-memory store for teams (swap for database in production)" —
four dicts on a `TeamStore` instance. Every team a customer created, every
member they added and every invitation they sent was lost on the next restart or
deploy, and the API gave no sign of it: create returned 200, the roster read back
correctly, and then the process recycled. The invitations were the sharpest edge:
an invitation is a promise made to somebody outside the system, who has already
been handed a link that then stops resolving.

`scoped(TeamStore)` gave each org its own dict, which stopped one tenant reading
another's roster but did nothing about the loss. It is now the `teams` /
`team_members` / `team_invitations` tables, and the isolation is an `org_id`
filter on every statement rather than a matter of which in-process instance the
caller happened to be handed.

Two invariants the dicts could not enforce are now the schema's job rather than
a read-then-write check that concurrency defeats:

  A slug identifies exactly one team *within an org*. The dict version
  de-duplicated slugs it generated itself by scanning, but took a
  caller-supplied slug verbatim, so two teams could share one and the other was
  unreachable by its own URL.

  A user is in a team at most once. `(team_id, user_id)` is unique, so two
  invitation accepts racing each other cannot both add the same person.

`_user_teams` has no replacement table on purpose. It was an index from user to
team ids maintained by hand at four call sites, which is the sort of bookkeeping
that drifts out of step with the thing it indexes; "which teams is this user in"
is a query over `team_members`.

Every method is async and opens its own session; there is no per-org instance to
keep, so `get_team_store()` returns one stateless object.
"""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncGenerator, Iterable, Sequence
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.database.orgs import ensure_org_row, org_uuid

# `INVITATION_TTL` is how long an invitation stays redeemable. It lives next to
# the model default it mirrors (`TeamInvitation.expires_at`), which is what the
# API documents, and is imported rather than restated so the row written here
# cannot drift from it.
from app.teams.models import (
    INVITATION_TTL,
    InvitationStatus,
    Team,
    TeamInvitation,
    TeamMember,
    TeamRole,
)

log = logging.getLogger("omnipotent.teams.store")

# How many times to retry a create whose slug was claimed between choosing it
# and writing it. The last attempt stops competing for a tidy slug and takes a
# random one, so contention cannot fail a team creation outright.
_SLUG_RETRIES = 4

# What `update_team` is allowed to touch. The dict version applied any key that
# happened to be an attribute of the model, which included `id` and `owner_id`.
_UPDATABLE_TEAM_FIELDS = frozenset({"name", "settings"})


class SlugTaken(ValueError):
    """The caller asked for a slug another team in this org already holds."""

    def __init__(self, slug: str) -> None:
        super().__init__(f"A team with the slug {slug!r} already exists.")
        self.slug = slug


@asynccontextmanager
async def _session() -> AsyncGenerator[Any, None]:
    """A session against a schema that is guaranteed to exist.

    Callers reach this outside the API lifespan (a worker, a script, a test), so
    the startup schema check cannot be assumed to have run. `ensure_schema()` is
    a no-op after the first call.
    """
    from app.database.engine import ensure_schema, get_session

    await ensure_schema()
    async with get_session() as session:
        yield session


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(value: datetime | None) -> Any:
    """Read a timestamp back as UTC-aware.

    SQLite, the default database, has no timezone type: a
    `DateTime(timezone=True)` column round trips to a naive datetime. Everything
    written here is UTC, but without this an `expires_at` read from SQLite
    cannot be compared against `_now()` at all — naive-vs-aware raises TypeError
    — and it would serialize without an offset, which a client reads as local
    time.
    """
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def _slugify(value: str) -> str:
    """Reduce a name (or a caller's proposed slug) to URL-safe characters.

    Applied to caller-supplied slugs too, unlike the dict version, which stored
    them verbatim: this string is the team's URL, and one containing a space or
    a slash is not one.
    """
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _to_role(value: str, *, context: str) -> TeamRole:
    """Read a role back, degrading to the least privilege we can grant.

    A role we cannot parse is a row somebody wrote by hand or a value from a
    future revision. Treating it as VIEWER shows the member in listings without
    letting an unreadable string authorize anything — and without raising
    ValueError out of a plain listing, which is a 500.
    """
    try:
        return TeamRole(value)
    except ValueError:
        log.warning("Unreadable team role %r on %s; treating as viewer", value, context)
        return TeamRole.VIEWER


def _to_status(value: str, *, context: str) -> InvitationStatus:
    """Read an invitation status back, degrading to one that grants nothing."""
    try:
        return InvitationStatus(value)
    except ValueError:
        log.warning(
            "Unreadable invitation status %r on %s; treating as expired", value, context
        )
        return InvitationStatus.EXPIRED


def _to_team(row: Any) -> Team:
    created = _utc(row.created_at) or _now()
    return Team(
        id=row.id,
        name=row.name,
        slug=row.slug,
        owner_id=row.owner_id,
        created_at=created,
        updated_at=_utc(row.updated_at) or created,
        settings=dict(row.settings or {}),
    )


def _to_member(row: Any) -> TeamMember:
    return TeamMember(
        id=row.id,
        team_id=row.team_id,
        user_id=row.user_id,
        email=row.email,
        role=_to_role(row.role, context=f"member {row.id}"),
        joined_at=_utc(row.joined_at) or _now(),
        last_active=_utc(row.last_active),
    )


def _to_invitation(row: Any) -> TeamInvitation:
    return TeamInvitation(
        id=row.id,
        team_id=row.team_id,
        email=row.email,
        role=_to_role(row.role, context=f"invitation {row.id}"),
        invited_by=row.invited_by,
        status=_to_status(row.status, context=f"invitation {row.id}"),
        created_at=_utc(row.created_at) or _now(),
        expires_at=_utc(row.expires_at) or _now(),
        accepted_at=_utc(row.accepted_at),
    )


class TeamStore:
    """Team persistence for the org on the current request.

    Stateless: every method opens its own session and filters on `org_uuid()`.
    """

    # --- teams ------------------------------------------------------------ #

    async def _generate_slug(self, session: Any, org: UUID, base: str) -> str:
        """The first of `base`, `base-1`, `base-2`, … no team in this org holds.

        Only slugs sharing the prefix are read, rather than every team's, which
        is what the dict version scanned. `base` comes out of `_slugify`, so it
        holds no LIKE wildcards to escape.
        """
        from app.database.team_models import TeamDB

        taken = set(
            (
                await session.execute(
                    select(TeamDB.slug).where(
                        TeamDB.org_id == org,
                        (TeamDB.slug == base) | (TeamDB.slug.startswith(f"{base}-")),
                    )
                )
            )
            .scalars()
            .all()
        )
        if base not in taken:
            return base
        counter = 1
        while f"{base}-{counter}" in taken:
            counter += 1
        return f"{base}-{counter}"

    async def create_team(
        self, name: str, owner_id: str, owner_email: str, slug: str | None = None
    ) -> Team:
        """Create a team with the caller as its owner.

        A caller-supplied slug is normalised but never disambiguated: it raises
        `SlugTaken` if another team in this org holds it, because handing back a
        different slug than the one asked for silently breaks whatever link the
        caller was about to publish. The in-memory version only de-duplicated
        slugs it generated itself, so a caller-supplied duplicate was accepted
        and produced two teams sharing a URL.

        A slug we generate ourselves *is* disambiguated with a counted suffix,
        and the whole insert is retried if another writer claims it first —
        nobody is holding a link to it yet.
        """
        from app.database.team_models import TeamDB, TeamMemberDB

        requested = _slugify(slug) if slug else ""
        # A name of pure punctuation slugifies to nothing; "team-1", "team-2" is
        # a worse URL than the name deserved but a better one than "".
        base = _slugify(name) or "team"

        for attempt in range(_SLUG_RETRIES):
            async with _session() as session:
                org = await ensure_org_row(session)
                if requested:
                    chosen = requested
                elif attempt < _SLUG_RETRIES - 1:
                    chosen = await self._generate_slug(session, org, base)
                else:
                    # Every counted suffix was claimed before we could write it.
                    # Stop competing for a tidy slug and take one nobody races.
                    chosen = f"{base}-{uuid4().hex[:8]}"

                team_id = str(uuid4())
                team = TeamDB(
                    id=team_id,
                    org_id=org,
                    name=name,
                    slug=chosen,
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
                    if requested:
                        raise SlugTaken(requested) from None
                    log.info(
                        "Team slug %r was claimed concurrently; retrying (%d/%d)",
                        chosen,
                        attempt + 1,
                        _SLUG_RETRIES,
                    )
                    continue
                await session.refresh(team)
                return _to_team(team)

        log.error(
            "Gave up assigning a team slug for %r after %d attempts", name, _SLUG_RETRIES
        )
        raise SlugTaken(base)

    async def get_team(self, team_id: str) -> Team | None:
        async with _session() as session:
            row = await self._team_row(session, team_id)
            return _to_team(row) if row else None

    async def get_team_by_slug(self, slug: str) -> Team | None:
        """The team holding a slug in this org.

        The dict version scanned every team and returned whichever one it
        reached first when two shared a slug; `uq_team_org_slug` makes that
        ambiguity impossible.
        """
        from app.database.team_models import TeamDB

        if not slug:
            return None
        async with _session() as session:
            row = (
                (
                    await session.execute(
                        select(TeamDB).where(
                            TeamDB.slug == slug, TeamDB.org_id == org_uuid()
                        )
                    )
                )
                .scalars()
                .first()
            )
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
        """Update a team's name and/or settings. Other keys are ignored."""
        async with _session() as session:
            row = await self._team_row(session, team_id)
            if row is None:
                return None
            for key, value in updates.items():
                if value is not None and key in _UPDATABLE_TEAM_FIELDS:
                    # Reassigned rather than mutated: SQLAlchemy only marks a
                    # JSON column dirty on assignment, so an in-place edit of
                    # `settings` is dropped on commit.
                    setattr(row, key, dict(value) if key == "settings" else value)
            row.updated_at = _now()
            await session.commit()
            await session.refresh(row)
            return _to_team(row)

    async def delete_team(self, team_id: str) -> bool:
        """Delete a team along with its roster and invitations."""
        from app.database.team_models import TeamDB

        async with _session() as session:
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
        from app.database.team_models import TeamDB, TeamMemberDB

        org = org_uuid()
        counts = (
            select(TeamMemberDB.team_id, func.count().label("n"))
            .where(TeamMemberDB.org_id == org)
            .group_by(TeamMemberDB.team_id)
            .subquery()
        )
        async with _session() as session:
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
            return [
                (_to_team(team), _to_role(role, context=f"team {team.id}"), int(count))
                for team, role, count in rows
            ]

    # --- members ----------------------------------------------------------- #

    async def member_counts(self, team_ids: Sequence[str]) -> dict[str, int]:
        """Member counts for several teams at once, as one grouped query.

        `get_user_teams` already carries the count for the listing it returns;
        this is for a caller holding ids from somewhere else, and exists because
        asking for a count one team at a time makes a listing of N teams cost
        N+1 round trips. Ids that cannot be a UUID count zero rather than
        reaching the database.
        """
        from app.database.team_models import TeamMemberDB

        ids = list(team_ids)
        usable = [team_id for team_id in ids if _looks_like_uuid(team_id)]
        counted: dict[str, int] = {}
        if usable:
            async with _session() as session:
                rows = (
                    await session.execute(
                        select(TeamMemberDB.team_id, func.count())
                        .where(
                            TeamMemberDB.team_id.in_(usable),
                            TeamMemberDB.org_id == org_uuid(),
                        )
                        .group_by(TeamMemberDB.team_id)
                    )
                ).all()
            counted = {str(team_id): int(total) for team_id, total in rows}
        return {team_id: counted.get(team_id, 0) for team_id in ids}

    async def count_members(self, team_id: str) -> int:
        from app.database.team_models import TeamMemberDB

        if not _looks_like_uuid(team_id):
            return 0
        async with _session() as session:
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
        from app.database.team_models import TeamMemberDB

        if not _looks_like_uuid(team_id):
            return []
        async with _session() as session:
            rows = (
                (
                    await session.execute(
                        select(TeamMemberDB)
                        .where(
                            TeamMemberDB.team_id == team_id,
                            TeamMemberDB.org_id == org_uuid(),
                        )
                        # `id` breaks the tie: two members added in the same
                        # transaction share a `joined_at` to the microsecond,
                        # and an unordered pair makes the roster shuffle
                        # between requests.
                        .order_by(TeamMemberDB.joined_at, TeamMemberDB.id)
                    )
                )
                .scalars()
                .all()
            )
            return [_to_member(row) for row in rows]

    async def get_member(self, team_id: str, user_id: str) -> TeamMember | None:
        async with _session() as session:
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
        from app.database.team_models import TeamMemberDB

        if not _looks_like_uuid(team_id):
            return None
        async with _session() as session:
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
        from app.database.team_models import TeamMemberDB

        async with _session() as session:
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
                # Set explicitly, though NULL is what it would be anyway: a
                # column left out of the INSERT is not loaded on the instance
                # afterwards, and reading it back to build the response would
                # emit a lazy SELECT that raises under asyncio.
                last_active=None,
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
        async with _session() as session:
            row = await self._member_row(session, team_id, user_id)
            if row is None or row.role == TeamRole.OWNER.value:
                return None  # the owner's role is not changeable
            row.role = role.value
            await session.commit()
            await session.refresh(row)
            return _to_member(row)

    async def remove_member(self, team_id: str, user_id: str) -> bool:
        async with _session() as session:
            row = await self._member_row(session, team_id, user_id)
            if row is None or row.role == TeamRole.OWNER.value:
                return False  # the owner cannot be removed
            await session.delete(row)
            await session.commit()
            return True

    async def update_member_activity(self, team_id: str, user_id: str) -> None:
        """Stamp a member's last-active time. Silent if they are not a member."""
        async with _session() as session:
            row = await self._member_row(session, team_id, user_id)
            if row is not None:
                row.last_active = _now()
                await session.commit()

    # --- invitations ------------------------------------------------------- #

    async def _expire_stale(self, session: Any) -> None:
        """Flip pending invitations that have run out of time.

        The in-memory store did this on read by mutating the object it was about
        to return, so an invitation only expired if somebody happened to list
        it, and it was never written down. Doing it as one UPDATE means
        `accept_invitation` sees the same truth as `get_invitations`.
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
            # Nothing is loaded in this session yet, so there is no in-memory
            # state to reconcile.
            .execution_options(synchronize_session=False)
        )
        await session.commit()

    async def get_invitations(self, team_id: str) -> list[TeamInvitation]:
        """Pending invitations for a team, oldest first."""
        from app.database.team_models import TeamInvitationDB

        if not _looks_like_uuid(team_id):
            return []
        async with _session() as session:
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
                        .order_by(TeamInvitationDB.created_at, TeamInvitationDB.id)
                    )
                )
                .scalars()
                .all()
            )
            return [_to_invitation(row) for row in rows]

    async def get_invitation(self, invitation_id: str) -> TeamInvitation | None:
        async with _session() as session:
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
        from app.database.team_models import TeamInvitationDB

        if not _looks_like_uuid(team_id):
            return None
        async with _session() as session:
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
        """Invite an address to a team.

        None if the team is gone, the address is already a member, or it already
        has a pending invitation. Those last two are read-then-write checks, as
        they were before: a duplicate invitation is not enforceable by a unique
        constraint without forbidding a second invitation after a first was
        cancelled. Losing that race sends one person two emails; whichever they
        accept, the other stays pending and expires.
        """
        from app.database.team_models import TeamInvitationDB

        if await self.get_member_by_email(team_id, email) is not None:
            return None
        if await self.get_invitation_by_email(team_id, email) is not None:
            return None

        async with _session() as session:
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
                accepted_at=None,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return _to_invitation(row)

    async def accept_invitation(
        self, invitation_id: str, user_id: str
    ) -> tuple[TeamInvitation | None, TeamMember | None]:
        """Redeem an invitation and add the user to the team.

        Returns the invitation alongside the membership it produced, or
        `(inv, None)` when it cannot be accepted — expired, already resolved, or
        the user is already in the team — so the caller can say which.
        """
        async with _session() as session:
            # Expiry is evaluated on read: nothing sweeps this table on a timer,
            # so this may be the first look at a row that is already stale.
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

        async with _session() as session:
            fresh = await self._invitation_row(session, invitation_id)
            if fresh is None:
                return invitation, member
            fresh.status = InvitationStatus.ACCEPTED.value
            fresh.accepted_at = _now()
            await session.commit()
            return _to_invitation(fresh), member

    async def cancel_invitation(self, invitation_id: str) -> bool:
        """Cancel a pending invitation. False if it was already resolved."""
        async with _session() as session:
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
        from app.database.team_models import TeamDB, TeamInvitationDB

        if not email:
            return []
        async with _session() as session:
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
                    .order_by(TeamInvitationDB.created_at, TeamInvitationDB.id)
                )
            ).all()
            return [(_to_invitation(inv), _to_team(team)) for inv, team in rows]

    # --- teardown ---------------------------------------------------------- #

    async def clear(self, team_ids: Iterable[str] | None = None) -> None:
        """Delete teams — the named ones, or all of them. For tests and teardown.

        Scoped to the current org like every other statement here, so it cannot
        reach another tenant's rows.
        """
        from app.database.team_models import TeamDB, TeamInvitationDB, TeamMemberDB

        org = org_uuid()
        ids = (
            None
            if team_ids is None
            else [team_id for team_id in team_ids if _looks_like_uuid(team_id)]
        )
        async with _session() as session:
            invitations = delete(TeamInvitationDB).where(TeamInvitationDB.org_id == org)
            members = delete(TeamMemberDB).where(TeamMemberDB.org_id == org)
            teams = delete(TeamDB).where(TeamDB.org_id == org)
            if ids is not None:
                invitations = invitations.where(TeamInvitationDB.team_id.in_(ids))
                members = members.where(TeamMemberDB.team_id.in_(ids))
                teams = teams.where(TeamDB.id.in_(ids))
            await session.execute(invitations)
            await session.execute(members)
            await session.execute(teams)
            await session.commit()


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

    origin/main (#33/#34) made the same move to tables but exposed it as
    module-level functions and dropped this accessor. The class is kept because
    the org scoping and `app/teams/router.py` hang off the instance; the
    behaviours that PR added — fail-closed role/status parsing, slug
    normalisation and retry, `get_team_by_slug`, `member_counts`, `clear` — are
    all above.
    """
    return _STORE
