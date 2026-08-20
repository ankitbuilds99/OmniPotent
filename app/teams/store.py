"""Database-backed storage for teams, memberships and invitations.

Replaces `TeamStore`, whose four dicts (`_teams`, `_members`, `_invitations`,
`_user_teams`) lived on a module-level singleton behind a router that has been
wired into the API all along. A team created on one pod did not exist on any
other, and everything — the team, who was in it, who had been invited — was
destroyed on every restart, deploy and scale-down. The invitations were the
worst of it: an invitation is a promise made to somebody outside the system,
who has already been handed a link that then stops resolving.

Two things are now enforced by the schema rather than by a read-then-write
check that concurrency defeats:

  A slug identifies exactly one team. The dict version de-duplicated
  auto-generated slugs by scanning, but took a caller-supplied slug verbatim,
  so two teams could share one and `get_team_by_slug` returned whichever the
  scan reached first.

  A user is in a team at most once. `(team_id, user_id)` is unique, so two
  invitation accepts racing each other cannot both add the same person.

`_user_teams` has no replacement table on purpose. It was an index from user to
team ids maintained by hand at four call sites, which is the sort of bookkeeping
that drifts out of step with the thing it indexes; "which teams is this user in"
is now a query over `team_members`.

Not tenant-scoped by `org_id`: a team *is* the tenant boundary this package
offers, and its id is the scope. The store deals in the Pydantic models from
`app/teams/models.py`, so callers see the same shapes they did before.
"""

from __future__ import annotations

import logging
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Iterable, Sequence, cast
from uuid import uuid4

from sqlalchemy import CursorResult, ColumnElement, delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.engine import ensure_schema, get_session
from app.database.models import TeamInvitationRecord, TeamMemberRecord, TeamRecord
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


class SlugTaken(RuntimeError):
    """A slug the caller asked for already belongs to another team."""

    def __init__(self, slug: str) -> None:
        super().__init__(f"Slug already in use: {slug}")
        self.slug = slug


@asynccontextmanager
async def _session() -> AsyncGenerator[AsyncSession, None]:
    """A session against a schema that is guaranteed to exist.

    Callers reach this outside the API lifespan (a worker, a script, a test), so
    the schema check cannot be assumed to have happened at startup.
    """
    await ensure_schema()
    async with get_session() as session:
        yield session


def _utc(value: datetime | None) -> datetime | None:
    """Re-attach UTC to a datetime read back from SQLite.

    SQLite has no timezone type, so a value written as aware comes back naive
    and would serialize without an offset — which a client reads as local time.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _slugify(value: str) -> str:
    """Reduce a name (or a caller's proposed slug) to URL-safe characters.

    Applied to caller-supplied slugs too, unlike the dict version, which stored
    them verbatim: this string is the team's URL, and one containing a space or
    a slash is not one.
    """
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


# --------------------------------------------------------------------------
# Row -> model
# --------------------------------------------------------------------------


def _to_role(value: str, *, context: str) -> TeamRole:
    """Read a role back, degrading to the least privilege we can grant.

    A role we cannot parse is a row somebody wrote by hand or a value from a
    future revision. Treating it as VIEWER shows the member in listings without
    letting an unreadable string authorize anything.
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


def _to_team(row: TeamRecord) -> Team:
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


def _to_member(row: TeamMemberRecord) -> TeamMember:
    return TeamMember(
        id=row.id,
        team_id=row.team_id,
        user_id=row.user_id,
        email=row.email,
        role=_to_role(row.role, context=f"member {row.id}"),
        joined_at=_utc(row.joined_at) or _now(),
        last_active=_utc(row.last_active),
    )


def _to_invitation(row: TeamInvitationRecord) -> TeamInvitation:
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


# --------------------------------------------------------------------------
# Teams
# --------------------------------------------------------------------------


async def _free_slug(session: AsyncSession, base: str) -> str:
    """The first of `base`, `base-1`, `base-2`, ... that no team holds.

    Only slugs sharing the prefix are read, rather than every team's, which is
    what the dict version scanned. `base` comes out of `_slugify`, so it holds
    no LIKE wildcards to escape.
    """
    taken = set(
        (
            await session.execute(
                select(TeamRecord.slug).where(
                    (TeamRecord.slug == base) | (TeamRecord.slug.startswith(f"{base}-"))
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
    name: str, owner_id: str, owner_email: str, slug: str | None = None
) -> Team:
    """Create a team with its owner as the first member, in one transaction.

    A caller-supplied slug is normalised but never disambiguated: raises
    `SlugTaken` if it belongs to another team, because handing back a different
    slug than the one asked for silently breaks whatever link the caller was
    about to publish. A slug we generate ourselves *is* disambiguated, with a
    counted suffix, and the whole insert is retried if another writer claims it
    first.
    """
    requested = _slugify(slug) if slug else ""
    # A name of pure punctuation slugifies to nothing; "team-1", "team-2" is a
    # worse URL than the name deserved but a better one than "".
    base = _slugify(name) or "team"

    for attempt in range(_SLUG_RETRIES):
        async with _session() as session:
            if requested:
                chosen = requested
            elif attempt < _SLUG_RETRIES - 1:
                chosen = await _free_slug(session, base)
            else:
                # Every counted suffix was claimed before we could write it.
                # Stop competing for a tidy slug and take one nobody is racing.
                chosen = f"{base}-{uuid4().hex[:8]}"

            now = _now()
            team_id = str(uuid4())
            session.add(
                TeamRecord(
                    id=team_id,
                    name=name,
                    slug=chosen,
                    owner_id=owner_id,
                    settings={},
                    created_at=now,
                    updated_at=now,
                )
            )
            session.add(
                TeamMemberRecord(
                    id=str(uuid4()),
                    team_id=team_id,
                    user_id=owner_id,
                    email=owner_email,
                    role=TeamRole.OWNER.value,
                    joined_at=now,
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

            return Team(
                id=team_id,
                name=name,
                slug=chosen,
                owner_id=owner_id,
                created_at=now,
                updated_at=now,
                settings={},
            )

    log.error("Gave up assigning a team slug for %r after %d attempts", name, _SLUG_RETRIES)
    raise SlugTaken(base)


async def get_team(team_id: str) -> Team | None:
    """Get team by ID."""
    async with _session() as session:
        row = await session.get(TeamRecord, team_id)
        return _to_team(row) if row is not None else None


async def get_team_by_slug(slug: str) -> Team | None:
    """Get team by slug. Unique, so this can no longer return an arbitrary one."""
    async with _session() as session:
        row = (
            await session.execute(select(TeamRecord).where(TeamRecord.slug == slug))
        ).scalars().first()
        return _to_team(row) if row is not None else None


async def update_team(team_id: str, updates: dict[str, Any]) -> Team | None:
    """Update a team's name and/or settings. Other keys are ignored."""
    async with _session() as session:
        row = await session.get(TeamRecord, team_id)
        if row is None:
            return None
        for key, value in updates.items():
            if key in _UPDATABLE_TEAM_FIELDS and value is not None:
                # Reassigned rather than mutated: SQLAlchemy only marks a JSON
                # column dirty on assignment, so an in-place edit is dropped.
                setattr(row, key, dict(value) if key == "settings" else value)
        row.updated_at = _now()
        await session.commit()
        return _to_team(row)


async def delete_team(team_id: str) -> bool:
    """Delete a team with its members and invitations, in one transaction."""
    async with _session() as session:
        row = await session.get(TeamRecord, team_id)
        if row is None:
            return False
        await session.execute(
            delete(TeamMemberRecord).where(TeamMemberRecord.team_id == team_id)
        )
        await session.execute(
            delete(TeamInvitationRecord).where(TeamInvitationRecord.team_id == team_id)
        )
        await session.delete(row)
        await session.commit()
        return True


async def get_user_teams(user_id: str) -> list[tuple[Team, TeamRole]]:
    """Every team this user belongs to, with their role, oldest membership first.

    Derived from `team_members` rather than from a parallel user→teams index, so
    it cannot disagree with the membership rows themselves.
    """
    stmt = (
        select(TeamRecord, TeamMemberRecord.role)
        .join(TeamMemberRecord, TeamMemberRecord.team_id == TeamRecord.id)
        .where(TeamMemberRecord.user_id == user_id)
        .order_by(TeamMemberRecord.joined_at.asc(), TeamRecord.id.asc())
    )
    async with _session() as session:
        rows = (await session.execute(stmt)).all()

    return [
        (_to_team(team), _to_role(role, context=f"team {team.id}")) for team, role in rows
    ]


async def member_counts(team_ids: Sequence[str]) -> dict[str, int]:
    """Member counts for several teams at once, as one grouped query.

    Exists because the team listing shows a count per team, and asking for it
    one team at a time makes a listing of N teams cost N+1 round trips.
    """
    ids = list(team_ids)
    if not ids:
        return {}
    stmt = (
        select(TeamMemberRecord.team_id, func.count())
        .where(TeamMemberRecord.team_id.in_(ids))
        .group_by(TeamMemberRecord.team_id)
    )
    async with _session() as session:
        counted = {team_id: int(n) for team_id, n in (await session.execute(stmt)).all()}
    return {team_id: counted.get(team_id, 0) for team_id in ids}


async def count_members(team_id: str) -> int:
    """How many members a team has, without loading them."""
    async with _session() as session:
        total = (
            await session.execute(
                select(func.count())
                .select_from(TeamMemberRecord)
                .where(TeamMemberRecord.team_id == team_id)
            )
        ).scalar()
        return int(total or 0)


# --------------------------------------------------------------------------
# Members
# --------------------------------------------------------------------------


async def get_members(team_id: str) -> list[TeamMember]:
    """All members of a team, in the order they joined — owner first."""
    stmt = (
        select(TeamMemberRecord)
        .where(TeamMemberRecord.team_id == team_id)
        .order_by(TeamMemberRecord.joined_at.asc(), TeamMemberRecord.id.asc())
    )
    async with _session() as session:
        rows = (await session.execute(stmt)).scalars().all()
    return [_to_member(row) for row in rows]


async def get_member(team_id: str, user_id: str) -> TeamMember | None:
    """Get a specific member."""
    async with _session() as session:
        row = await _member_row(session, team_id, user_id)
        return _to_member(row) if row is not None else None


async def _member_row(
    session: AsyncSession, team_id: str, user_id: str
) -> TeamMemberRecord | None:
    return (
        await session.execute(
            select(TeamMemberRecord).where(
                TeamMemberRecord.team_id == team_id,
                TeamMemberRecord.user_id == user_id,
            )
        )
    ).scalars().first()


async def get_member_by_email(team_id: str, email: str) -> TeamMember | None:
    """Get member by email, matched case-insensitively as addresses are."""
    async with _session() as session:
        row = (
            await session.execute(
                select(TeamMemberRecord).where(
                    TeamMemberRecord.team_id == team_id,
                    func.lower(TeamMemberRecord.email) == email.lower(),
                )
            )
        ).scalars().first()
        return _to_member(row) if row is not None else None


async def add_member(
    team_id: str, user_id: str, email: str, role: TeamRole = TeamRole.MEMBER
) -> TeamMember | None:
    """Add a member to a team. None if the team is gone or they are already in it.

    "Already a member" is answered by the unique constraint rather than by a
    prior SELECT, so two accepts racing each other produce one membership and
    one None instead of two rows.
    """
    now = _now()
    row = TeamMemberRecord(
        id=str(uuid4()),
        team_id=team_id,
        user_id=user_id,
        email=email,
        role=role.value,
        joined_at=now,
        # Set explicitly, though NULL is what it would be anyway: a column left
        # out of the INSERT is not loaded on the instance afterwards, and
        # reading it back to build the response would emit a lazy SELECT that
        # raises under asyncio.
        last_active=None,
    )
    async with _session() as session:
        if await session.get(TeamRecord, team_id) is None:
            return None
        session.add(row)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            return None
        return _to_member(row)


async def update_member_role(
    team_id: str, user_id: str, role: TeamRole
) -> TeamMember | None:
    """Update a member's role. The owner's role cannot be changed."""
    async with _session() as session:
        row = await _member_row(session, team_id, user_id)
        if row is None or row.role == TeamRole.OWNER.value:
            return None
        row.role = role.value
        await session.commit()
        return _to_member(row)


async def remove_member(team_id: str, user_id: str) -> bool:
    """Remove a member. The owner cannot be removed; delete the team instead."""
    async with _session() as session:
        row = await _member_row(session, team_id, user_id)
        if row is None or row.role == TeamRole.OWNER.value:
            return False
        await session.delete(row)
        await session.commit()
        return True


async def update_member_activity(team_id: str, user_id: str) -> None:
    """Stamp a member's last-active time. Silent if they are not a member."""
    async with _session() as session:
        await session.execute(
            update(TeamMemberRecord)
            .where(
                TeamMemberRecord.team_id == team_id,
                TeamMemberRecord.user_id == user_id,
            )
            .values(last_active=_now())
            .execution_options(synchronize_session=False)
        )
        await session.commit()


# --------------------------------------------------------------------------
# Invitations
# --------------------------------------------------------------------------


async def _expire_stale(
    session: AsyncSession, *where: ColumnElement[bool]
) -> None:
    """Flip pending invitations past their expiry to EXPIRED, and persist it.

    Expiry is evaluated on read, as it was in memory: nothing sweeps this table
    on a timer, so a row becomes EXPIRED the first time somebody looks at it.
    `accept_invitation` re-checks `expires_at` regardless of status, so an
    invitation nobody has looked at is still not acceptable.
    """
    await session.execute(
        update(TeamInvitationRecord)
        .where(
            TeamInvitationRecord.status == InvitationStatus.PENDING.value,
            TeamInvitationRecord.expires_at < _now(),
            *where,
        )
        .values(status=InvitationStatus.EXPIRED.value)
        # Nothing is loaded in this session yet, so there is no in-memory state
        # to reconcile — and the criteria here are not all Python-evaluable.
        .execution_options(synchronize_session=False)
    )


async def get_invitations(team_id: str) -> list[TeamInvitation]:
    """Pending invitations for a team, oldest first."""
    async with _session() as session:
        await _expire_stale(session, TeamInvitationRecord.team_id == team_id)
        rows = (
            await session.execute(
                select(TeamInvitationRecord)
                .where(
                    TeamInvitationRecord.team_id == team_id,
                    TeamInvitationRecord.status == InvitationStatus.PENDING.value,
                )
                .order_by(
                    TeamInvitationRecord.created_at.asc(),
                    TeamInvitationRecord.id.asc(),
                )
            )
        ).scalars().all()
        await session.commit()
    return [_to_invitation(row) for row in rows]


async def get_invitation(invitation_id: str) -> TeamInvitation | None:
    """Get invitation by ID, whatever its status."""
    async with _session() as session:
        row = await session.get(TeamInvitationRecord, invitation_id)
        return _to_invitation(row) if row is not None else None


async def get_invitation_by_email(team_id: str, email: str) -> TeamInvitation | None:
    """Get a team's pending invitation for one address."""
    async with _session() as session:
        await _expire_stale(session, TeamInvitationRecord.team_id == team_id)
        row = (
            await session.execute(
                select(TeamInvitationRecord).where(
                    TeamInvitationRecord.team_id == team_id,
                    func.lower(TeamInvitationRecord.email) == email.lower(),
                    TeamInvitationRecord.status == InvitationStatus.PENDING.value,
                )
            )
        ).scalars().first()
        await session.commit()
        return _to_invitation(row) if row is not None else None


async def create_invitation(
    team_id: str, email: str, role: TeamRole, invited_by: str
) -> TeamInvitation | None:
    """Invite an address to a team.

    None if the team is gone, the address is already a member, or it already has
    a pending invitation. Those last two are read-then-write checks, as they were
    before: a duplicate invitation is not enforceable by a unique constraint
    without forbidding a second invitation after a first was cancelled. Losing
    that race sends one person two emails; whichever they accept, the other stays
    pending and expires.
    """
    if await get_member_by_email(team_id, email) is not None:
        return None
    if await get_invitation_by_email(team_id, email) is not None:
        return None

    now = _now()
    row = TeamInvitationRecord(
        id=str(uuid4()),
        team_id=team_id,
        email=email,
        role=role.value,
        invited_by=invited_by,
        status=InvitationStatus.PENDING.value,
        created_at=now,
        expires_at=now + INVITATION_TTL,
        accepted_at=None,
    )
    async with _session() as session:
        if await session.get(TeamRecord, team_id) is None:
            return None
        session.add(row)
        await session.commit()
        return _to_invitation(row)


async def accept_invitation(
    invitation_id: str, user_id: str
) -> tuple[TeamInvitation | None, TeamMember | None]:
    """Accept an invitation and join the team.

    Returns the invitation alongside the membership it produced, or `(inv, None)`
    when it cannot be accepted — expired, already resolved, or the user is
    already in the team — so the caller can say which.
    """
    async with _session() as session:
        row = await session.get(TeamInvitationRecord, invitation_id)
        if row is None:
            return None, None

        if row.status != InvitationStatus.PENDING.value:
            return _to_invitation(row), None

        # Checked against the timestamp, not the status: expiry is only written
        # when somebody reads the invitation, and this may be the first read.
        if (_utc(row.expires_at) or _now()) < _now():
            row.status = InvitationStatus.EXPIRED.value
            await session.commit()
            return _to_invitation(row), None

        team_id, email, role = row.team_id, row.email, row.role

    member = await add_member(
        team_id, user_id, email, _to_role(role, context=f"invitation {invitation_id}")
    )

    async with _session() as session:
        row = await session.get(TeamInvitationRecord, invitation_id)
        if row is None:
            return None, member
        if member is not None:
            row.status = InvitationStatus.ACCEPTED.value
            row.accepted_at = _now()
            await session.commit()
        return _to_invitation(row), member


async def cancel_invitation(invitation_id: str) -> bool:
    """Cancel a pending invitation. False if it was already resolved."""
    async with _session() as session:
        result = await session.execute(
            update(TeamInvitationRecord)
            .where(
                TeamInvitationRecord.id == invitation_id,
                TeamInvitationRecord.status == InvitationStatus.PENDING.value,
            )
            .values(status=InvitationStatus.CANCELLED.value)
            .execution_options(synchronize_session=False)
        )
        await session.commit()
        return cast("CursorResult[Any]", result).rowcount > 0


async def get_user_pending_invitations(email: str) -> list[tuple[TeamInvitation, Team]]:
    """Every team currently waiting on this address, newest invitation first."""
    async with _session() as session:
        await _expire_stale(
            session, func.lower(TeamInvitationRecord.email) == email.lower()
        )
        rows = (
            await session.execute(
                select(TeamInvitationRecord, TeamRecord)
                .join(TeamRecord, TeamRecord.id == TeamInvitationRecord.team_id)
                .where(
                    func.lower(TeamInvitationRecord.email) == email.lower(),
                    TeamInvitationRecord.status == InvitationStatus.PENDING.value,
                )
                .order_by(
                    TeamInvitationRecord.created_at.desc(),
                    TeamInvitationRecord.id.asc(),
                )
            )
        ).all()
        await session.commit()
    return [(_to_invitation(inv), _to_team(team)) for inv, team in rows]


async def clear(team_ids: Iterable[str] | None = None) -> None:
    """Delete teams — the named ones, or all of them. For tests and teardown."""
    ids = None if team_ids is None else list(team_ids)
    async with _session() as session:
        invitations = delete(TeamInvitationRecord)
        members = delete(TeamMemberRecord)
        teams = delete(TeamRecord)
        if ids is not None:
            invitations = invitations.where(TeamInvitationRecord.team_id.in_(ids))
            members = members.where(TeamMemberRecord.team_id.in_(ids))
            teams = teams.where(TeamRecord.id.in_(ids))
        await session.execute(invitations)
        await session.execute(members)
        await session.execute(teams)
        await session.commit()
