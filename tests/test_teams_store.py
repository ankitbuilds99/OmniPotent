"""Teams survive a restart, and do not cross tenants.

`app/teams/store.py` was four dicts on an object — "In-memory store for teams
(swap for database in production)". Everything a customer built there was lost
on the next deploy, and the API gave no sign of it: create returned 200 and the
roster read back correctly right up until the process recycled. The module
written to back it (`app/database/team_models.py`) imported `app.database.base`,
which does not exist, so it could not even be imported and no migration created
its tables.

It is the `teams` / `team_members` / `team_invitations` tables now, so these
tests assert both halves: durability across a fresh store instance, and an
org_id filter on every read and write.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

import app.config as cfg
from app import tenant_context
from app.database import engine as db_engine
from app.teams import store as store_mod
from app.teams.models import InvitationStatus, TeamRole
from app.teams.store import SlugTaken, TeamStore, get_team_store


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("COPILOT_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/app.db")
    cfg.get_settings.cache_clear()
    db_engine._engine = None
    db_engine._session_factory = None
    tenant_context.current_tenant.set(None)

    asyncio.run(db_engine.init_db())
    yield

    asyncio.run(db_engine.close_db())
    cfg.get_settings.cache_clear()
    db_engine._engine = None
    db_engine._session_factory = None
    tenant_context.current_tenant.set(None)


def _as(org: str, coro_factory):
    """Run one coroutine as `org`, on its own loop, like a separate request."""

    async def run():
        token = tenant_context.set_tenant(tenant_context.TenantConfig(org_id=org))
        try:
            return await coro_factory()
        finally:
            tenant_context.reset_tenant(token)

    return asyncio.run(run())


def _create(org: str, name: str, owner: str = "u1", email: str = "u1@acme.test", slug=None):
    return _as(
        org,
        lambda: get_team_store().create_team(
            name=name, owner_id=owner, owner_email=email, slug=slug
        ),
    )


# --- durability -------------------------------------------------------------


def test_a_team_survives_a_fresh_store_instance(db):
    """The whole point: state that outlives the object that wrote it."""
    team = _create("acme", "Platform")

    # A different instance, as a redeployed process would have.
    reloaded = _as("acme", lambda: TeamStore().get_team(team.id))

    assert reloaded is not None
    assert reloaded.name == "Platform"
    assert reloaded.slug == "platform"
    assert reloaded.owner_id == "u1"


def test_the_owner_is_a_member_from_the_start(db):
    team = _create("acme", "Platform")

    members = _as("acme", lambda: get_team_store().get_members(team.id))

    assert [(m.user_id, m.role) for m in members] == [("u1", TeamRole.OWNER)]


def test_the_team_list_carries_its_member_count(db):
    team = _create("acme", "Platform")
    _as(
        "acme",
        lambda: get_team_store().add_member(team.id, "u2", "u2@acme.test"),
    )

    listed = _as("acme", lambda: get_team_store().get_user_teams("u1"))

    assert [(t.name, role, count) for t, role, count in listed] == [
        ("Platform", TeamRole.OWNER, 2)
    ]


# --- tenant isolation -------------------------------------------------------


def test_one_orgs_team_is_invisible_to_another(db):
    """Isolation is now an org_id filter, not which dict the caller was handed."""
    team = _create("acme", "Platform")

    assert _as("umbrella", lambda: get_team_store().get_team(team.id)) is None
    assert _as("umbrella", lambda: get_team_store().get_user_teams("u1")) == []
    assert _as("umbrella", lambda: get_team_store().get_members(team.id)) == []


def test_another_org_cannot_delete_or_update_the_team(db):
    team = _create("acme", "Platform")

    assert _as("umbrella", lambda: get_team_store().delete_team(team.id)) is False
    assert (
        _as("umbrella", lambda: get_team_store().update_team(team.id, {"name": "Pwned"}))
        is None
    )

    still_there = _as("acme", lambda: get_team_store().get_team(team.id))
    assert still_there is not None and still_there.name == "Platform"


def test_two_orgs_can_hold_the_same_slug(db):
    """Slugs used to be globally unique, which let one org deny a name to all."""
    a = _create("acme", "Platform")
    b = _create("umbrella", "Platform")

    assert a.slug == b.slug == "platform"
    assert a.id != b.id


# --- slugs ------------------------------------------------------------------


def test_a_generated_slug_collision_gets_a_suffix(db):
    first = _create("acme", "Platform")
    second = _create("acme", "Platform")

    assert first.slug == "platform"
    assert second.slug == "platform-1"


def test_an_explicitly_requested_duplicate_slug_is_refused(db):
    """The in-memory store only de-duplicated slugs it generated itself, so a
    caller-supplied duplicate produced two teams sharing one URL."""
    _create("acme", "Platform", slug="platform")

    with pytest.raises(SlugTaken):
        _create("acme", "Something Else", slug="platform")


# --- members ----------------------------------------------------------------


def test_the_owner_cannot_be_removed_or_demoted(db):
    team = _create("acme", "Platform")

    assert _as("acme", lambda: get_team_store().remove_member(team.id, "u1")) is False
    assert (
        _as(
            "acme",
            lambda: get_team_store().update_member_role(team.id, "u1", TeamRole.VIEWER),
        )
        is None
    )

    members = _as("acme", lambda: get_team_store().get_members(team.id))
    assert [(m.user_id, m.role) for m in members] == [("u1", TeamRole.OWNER)]


def test_a_member_is_added_once(db):
    team = _create("acme", "Platform")
    s = get_team_store()

    assert _as("acme", lambda: s.add_member(team.id, "u2", "u2@acme.test")) is not None
    assert _as("acme", lambda: s.add_member(team.id, "u2", "u2@acme.test")) is None

    members = _as("acme", lambda: s.get_members(team.id))
    assert sorted(m.user_id for m in members) == ["u1", "u2"]


def test_deleting_a_team_takes_its_roster_and_invitations_with_it(db):
    team = _create("acme", "Platform")
    s = get_team_store()
    _as("acme", lambda: s.add_member(team.id, "u2", "u2@acme.test"))
    invite = _as(
        "acme",
        lambda: s.create_invitation(team.id, "new@acme.test", TeamRole.MEMBER, "u1"),
    )

    assert _as("acme", lambda: s.delete_team(team.id)) is True

    assert _as("acme", lambda: s.get_team(team.id)) is None
    assert _as("acme", lambda: s.get_members(team.id)) == []
    assert _as("acme", lambda: s.get_invitation(invite.id)) is None


# --- invitations ------------------------------------------------------------


def test_an_invitation_is_redeemed_once(db):
    team = _create("acme", "Platform")
    s = get_team_store()
    invite = _as(
        "acme",
        lambda: s.create_invitation(team.id, "new@acme.test", TeamRole.ADMIN, "u1"),
    )

    accepted, member = _as("acme", lambda: s.accept_invitation(invite.id, "u2"))
    assert accepted.status is InvitationStatus.ACCEPTED
    assert member is not None and member.role is TeamRole.ADMIN

    again, second = _as("acme", lambda: s.accept_invitation(invite.id, "u3"))
    assert again.status is InvitationStatus.ACCEPTED
    assert second is None


def test_an_expired_invitation_cannot_be_redeemed(db, monkeypatch):
    """Expiry used to happen only if somebody happened to list the invitation;
    `accept_invitation` never checked, so a stale invite stayed redeemable."""
    team = _create("acme", "Platform")
    s = get_team_store()
    monkeypatch.setattr(store_mod, "INVITATION_TTL", timedelta(seconds=-1))
    invite = _as(
        "acme",
        lambda: s.create_invitation(team.id, "late@acme.test", TeamRole.MEMBER, "u1"),
    )

    expired, member = _as("acme", lambda: s.accept_invitation(invite.id, "u9"))

    assert member is None
    assert expired.status is InvitationStatus.EXPIRED
    assert _as("acme", lambda: s.get_invitations(team.id)) == []


def test_an_existing_member_is_not_invited_again(db):
    team = _create("acme", "Platform")
    s = get_team_store()

    assert (
        _as(
            "acme",
            lambda: s.create_invitation(team.id, "U1@ACME.TEST", TeamRole.MEMBER, "u1"),
        )
        is None
    )


def test_a_second_pending_invitation_to_the_same_address_is_refused(db):
    team = _create("acme", "Platform")
    s = get_team_store()
    assert _as(
        "acme", lambda: s.create_invitation(team.id, "new@acme.test", TeamRole.MEMBER, "u1")
    )
    assert (
        _as(
            "acme",
            lambda: s.create_invitation(team.id, "NEW@acme.test", TeamRole.MEMBER, "u1"),
        )
        is None
    )


def test_a_cancelled_invitation_cannot_be_redeemed(db):
    team = _create("acme", "Platform")
    s = get_team_store()
    invite = _as(
        "acme",
        lambda: s.create_invitation(team.id, "new@acme.test", TeamRole.MEMBER, "u1"),
    )

    assert _as("acme", lambda: s.cancel_invitation(invite.id)) is True
    assert _as("acme", lambda: s.cancel_invitation(invite.id)) is False

    _, member = _as("acme", lambda: s.accept_invitation(invite.id, "u2"))
    assert member is None


def test_pending_invitations_are_listed_for_the_invited_address(db):
    team = _create("acme", "Platform")
    s = get_team_store()
    _as("acme", lambda: s.create_invitation(team.id, "new@acme.test", TeamRole.MEMBER, "u1"))

    mine = _as("acme", lambda: s.get_user_pending_invitations("NEW@ACME.TEST"))
    assert [(inv.email, t.name) for inv, t in mine] == [("new@acme.test", "Platform")]

    assert _as("umbrella", lambda: s.get_user_pending_invitations("new@acme.test")) == []


# --- hostile input ----------------------------------------------------------


def test_a_malformed_id_is_a_miss_not_a_crash(db):
    """Team ids are UUID columns and arrive from the URL. Postgres answers
    `invalid input syntax for type uuid` on a malformed literal, which reaches
    the caller as a 500 unless it is filtered out first."""
    s = get_team_store()

    assert _as("acme", lambda: s.get_team("not-a-uuid")) is None
    assert _as("acme", lambda: s.delete_team("not-a-uuid")) is False
    assert _as("acme", lambda: s.get_members("not-a-uuid")) == []
    assert _as("acme", lambda: s.count_members("not-a-uuid")) == 0
    assert _as("acme", lambda: s.get_member("not-a-uuid", "u1")) is None
    assert _as("acme", lambda: s.get_invitation("not-a-uuid")) is None
    assert _as("acme", lambda: s.get_invitations("not-a-uuid")) == []
