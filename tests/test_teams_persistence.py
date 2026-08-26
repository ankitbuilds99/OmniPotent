"""Teams, memberships and invitations survive a restart — and stay consistent.

`TeamStore` kept four dicts on a module-level singleton while its router was
wired into the API at `/api/teams`. So every team, every membership and every
outstanding invitation was destroyed on each restart, deploy and scale-down, and
a team created on one pod did not exist on any other. The invitations were the
sharpest edge: an invitation is a promise made to somebody outside the system,
who has already been handed a link that then stopped resolving.

Two invariants that the dicts could not enforce are covered here as well,
because moving the data without them would leave the same bugs in a table:

  * a slug identifies exactly one team. Auto-generated slugs were de-duplicated
    by scanning, but a caller-supplied slug was stored verbatim, so two teams
    could hold one and `get_team_by_slug` returned whichever came first;
  * a user is in a team at most once. "Already a member" was a read-then-write
    check that two concurrent accepts both passed.

`tests/test_teams_store.py` covers the same tables from the tenant-isolation
angle — one org's rows are invisible to another. This file is the durability
half: every assertion here that says "and it is still there" runs across a real
engine dispose, which is the failure the dicts had and a fresh store instance
does not reproduce.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import app.config as cfg
from app import runtime, tenant_context
from app.api import main as api
from app.database import engine as db_engine
from app.teams import store as store_mod
from app.teams.models import InvitationStatus, TeamRole
from app.teams.store import SlugTaken, get_team_store

ORG = "acme"
API_TOKEN = "prodtoken"
SECRET = "k" * 32


@pytest.fixture(autouse=True)
def db(tmp_path, monkeypatch):
    """One database file per test, reused across the restarts a test simulates.

    The engine is a module global, so it is reset on the way in as well as out —
    otherwise a test inherits the previous test's engine, still pointing at
    *that* test's tmp_path.
    """
    monkeypatch.setenv("COPILOT_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/app.db")
    monkeypatch.delenv("COPILOT_MULTI_TENANT", raising=False)
    _reset_process()
    yield
    _reset_process()


def _reset_process() -> None:
    asyncio.run(db_engine.close_db())
    cfg.get_settings.cache_clear()
    db_engine._engine = None
    db_engine._session_factory = None
    tenant_context.current_tenant.set(None)


def _restart() -> None:
    """Simulate a process restart against the same database file.

    Disposing the engine is the point: it drops every pooled connection and the
    "schema already created" flag, so anything read afterwards came off disk
    rather than out of this process.
    """
    _reset_process()


def _as(coro_factory, org: str = ORG):
    """Run one coroutine as `org`, on its own loop, like a separate request."""

    async def run():
        token = tenant_context.set_tenant(tenant_context.TenantConfig(org_id=org))
        try:
            return await coro_factory()
        finally:
            tenant_context.reset_tenant(token)

    return asyncio.run(run())


def _create(name: str, owner: str = "u1", email: str = "a@example.com", slug=None):
    return _as(
        lambda: get_team_store().create_team(
            name=name, owner_id=owner, owner_email=email, slug=slug
        )
    )


def _backdate_invitation(invitation_id: str, *, days: int = 8) -> None:
    """Push an invitation's expiry into the past, without going through the store.

    Writing the column directly is the point: it produces a row that is stale but
    still marked PENDING, which is exactly the state a real invitation sits in
    until somebody looks at it.
    """

    async def run() -> None:
        from app.database.team_models import TeamInvitationDB

        await db_engine.ensure_schema()
        async with db_engine.get_session() as session:
            row = await session.get(TeamInvitationDB, invitation_id)
            assert row is not None
            row.expires_at = datetime.now(timezone.utc) - timedelta(days=days)
            await session.commit()

    asyncio.run(run())


def _row_counts() -> tuple[int, int, int]:
    """Rows in the three tables, counted without an org filter.

    Deliberately unscoped: a delete that leaves orphans behind under some other
    org id is still a leak, and a scoped count would not see it.
    """

    async def run() -> tuple[int, int, int]:
        from sqlalchemy import func, select

        from app.database.team_models import TeamDB, TeamInvitationDB, TeamMemberDB

        await db_engine.ensure_schema()
        async with db_engine.get_session() as session:
            out = []
            for model in (TeamDB, TeamMemberDB, TeamInvitationDB):
                total = (
                    await session.execute(select(func.count()).select_from(model))
                ).scalar()
                out.append(int(total or 0))
            return out[0], out[1], out[2]

    return asyncio.run(run())


# --------------------------------------------------------------------------- #
# Durability — the thing the dicts could not do
# --------------------------------------------------------------------------- #
def test_team_outlives_the_process():
    team = _create("Platform", "u1", "owner@example.com")

    _restart()

    found = _as(lambda: get_team_store().get_team(team.id))
    assert found is not None
    assert found.name == "Platform"
    assert found.slug == "platform"
    assert found.owner_id == "u1"


def test_membership_outlives_the_process():
    team = _create("Platform", "u1", "owner@example.com")
    _as(lambda: get_team_store().add_member(team.id, "u2", "dev@example.com", TeamRole.ADMIN))

    _restart()

    members = _as(lambda: get_team_store().get_members(team.id))
    assert [(m.user_id, m.role) for m in members] == [
        ("u1", TeamRole.OWNER),
        ("u2", TeamRole.ADMIN),
    ]


def test_invitation_outlives_the_process_and_is_still_acceptable():
    """The promise made to somebody outside the system still holds."""
    team = _create("Platform", "u1", "owner@example.com")
    invite = _as(
        lambda: get_team_store().create_invitation(
            team.id, "invitee@example.com", TeamRole.MEMBER, "u1"
        )
    )
    assert invite is not None

    _restart()

    pending = _as(lambda: get_team_store().get_invitations(team.id))
    assert [i.id for i in pending] == [invite.id]

    resolved, member = _as(lambda: get_team_store().accept_invitation(invite.id, "u2"))
    assert resolved.status == InvitationStatus.ACCEPTED
    assert member is not None
    assert member.user_id == "u2"


def test_no_in_memory_state_remains():
    """The dicts are gone, not merely shadowed by the tables.

    The original of this test also demanded that `TeamStore` and
    `get_team_store` themselves be gone, because origin/main replaced them with
    module-level functions. The merged store keeps the class and the accessor on
    purpose — the org scoping and `app/teams/router.py` hang off the instance —
    so what is pinned here is the property that actually mattered: no team state
    lives in this process. A stateless singleton satisfies that; a dict of
    rosters does not.
    """
    fresh = store_mod.TeamStore()
    assert vars(fresh) == {}, "TeamStore carries instance state again"
    assert vars(get_team_store()) == {}, "the shared store carries instance state"

    source = (store_mod.__file__ or "").replace(".pyc", ".py")
    with open(source, encoding="utf-8") as handle:
        text = handle.read()
    for name in ("_teams", "_members", "_invitations", "_user_teams"):
        assert f"self.{name}" not in text, f"{name} is still instance state"


def test_timestamps_read_back_as_utc_aware():
    team = _create("Platform", "u1", "owner@example.com")

    _restart()

    found = _as(lambda: get_team_store().get_team(team.id))
    assert found.created_at.tzinfo is not None
    assert found.created_at.utcoffset() == timedelta(0)


# --------------------------------------------------------------------------- #
# Slugs identify exactly one team
# --------------------------------------------------------------------------- #
def test_generated_slugs_are_disambiguated():
    first = _create("Platform", "u1", "a@example.com")
    second = _create("Platform", "u2", "b@example.com")
    third = _create("platform!", "u3", "c@example.com")

    assert [first.slug, second.slug, third.slug] == ["platform", "platform-1", "platform-2"]
    assert _as(lambda: get_team_store().get_team_by_slug("platform-1")).id == second.id


def test_requested_slug_that_is_taken_is_rejected_not_renamed():
    """Handing back a different slug would break the link the caller is publishing."""
    _create("Platform", "u1", "a@example.com", slug="platform")

    with pytest.raises(SlugTaken):
        _create("Other", "u2", "b@example.com", slug="platform")

    teams, members, _ = _row_counts()
    assert (teams, members) == (1, 1), "the losing create left rows behind"


def test_requested_slug_is_normalised():
    team = _create("Anything", "u1", "a@example.com", slug="My Team!!")
    assert team.slug == "my-team"


def test_unslugifiable_name_still_gets_a_slug():
    team = _create("!!!", "u1", "a@example.com")
    assert team.slug == "team"


def test_concurrent_creates_of_the_same_name_get_distinct_slugs():
    def run():
        return asyncio.gather(
            *(
                get_team_store().create_team(
                    name="Platform", owner_id=f"u{i}", owner_email=f"u{i}@example.com"
                )
                for i in range(5)
            )
        )

    slugs = [t.slug for t in _as(run)]
    assert len(set(slugs)) == 5, f"slugs collided: {slugs}"


# --------------------------------------------------------------------------- #
# A user is in a team at most once
# --------------------------------------------------------------------------- #
def test_adding_the_same_member_twice_returns_none():
    team = _create("Platform")
    s = get_team_store()
    assert _as(lambda: s.add_member(team.id, "u2", "b@example.com")) is not None
    assert _as(lambda: s.add_member(team.id, "u2", "b@example.com")) is None
    assert len(_as(lambda: s.get_members(team.id))) == 2


def test_concurrent_adds_produce_one_membership():
    team = _create("Platform")
    s = get_team_store()

    results = list(
        _as(
            lambda: asyncio.gather(
                *(s.add_member(team.id, "u2", "b@example.com") for _ in range(4))
            )
        )
    )

    assert sum(1 for r in results if r is not None) == 1
    assert len(_as(lambda: s.get_members(team.id))) == 2


def test_add_member_to_a_missing_team_is_refused():
    s = get_team_store()
    # A syntactically valid id nothing holds, so the refusal is the team lookup
    # rather than the malformed-id guard `test_teams_store.py` covers.
    assert _as(lambda: s.add_member("11111111-1111-1111-1111-111111111111", "u2", "b@x")) is None


def test_role_change_survives_a_restart():
    team = _create("Platform")
    s = get_team_store()
    _as(lambda: s.add_member(team.id, "u2", "b@example.com"))
    _as(lambda: s.update_member_role(team.id, "u2", TeamRole.ADMIN))

    _restart()

    assert _as(lambda: get_team_store().get_member(team.id, "u2")).role == TeamRole.ADMIN


# --------------------------------------------------------------------------- #
# "Which teams am I in" is derived, not indexed by hand
# --------------------------------------------------------------------------- #
def test_user_teams_follow_membership():
    a = _create("Alpha", "u1", "a@example.com")
    b = _create("Beta", "u2", "b@example.com")
    s = get_team_store()
    _as(lambda: s.add_member(b.id, "u1", "a@example.com", TeamRole.VIEWER))

    mine = _as(lambda: s.get_user_teams("u1"))
    assert [(t.id, role) for t, role, _ in mine] == [
        (a.id, TeamRole.OWNER),
        (b.id, TeamRole.VIEWER),
    ]

    assert _as(lambda: s.remove_member(b.id, "u1")) is True
    assert [t.id for t, _, _ in _as(lambda: s.get_user_teams("u1"))] == [a.id]


def test_deleting_a_team_takes_its_members_and_invitations_with_it():
    team = _create("Platform")
    s = get_team_store()
    _as(lambda: s.add_member(team.id, "u2", "b@example.com"))
    _as(
        lambda: s.create_invitation(team.id, "invitee@example.com", TeamRole.MEMBER, "u1")
    )

    assert _as(lambda: s.delete_team(team.id)) is True

    _restart()

    assert _row_counts() == (0, 0, 0)
    assert _as(lambda: get_team_store().get_user_teams("u2")) == []
    assert _as(lambda: get_team_store().delete_team(team.id)) is False


def test_member_counts_are_one_query_for_many_teams():
    a = _create("Alpha", "u1", "a@example.com")
    b = _create("Beta", "u1", "a@example.com")
    s = get_team_store()
    _as(lambda: s.add_member(a.id, "u2", "b@example.com"))

    missing = "11111111-1111-1111-1111-111111111111"
    counts = _as(lambda: s.member_counts([a.id, b.id, missing]))
    assert counts == {a.id: 2, b.id: 1, missing: 0}


def test_update_team_ignores_fields_it_does_not_own():
    team = _create("Platform")
    s = get_team_store()

    updated = _as(
        lambda: s.update_team(
            team.id,
            {"name": "Platform Eng", "settings": {"theme": "dark"}, "owner_id": "u9"},
        )
    )
    assert updated.name == "Platform Eng"
    assert updated.settings == {"theme": "dark"}
    assert updated.owner_id == "u1", "owner_id was writable through update_team"

    _restart()

    stored = _as(lambda: get_team_store().get_team(team.id))
    assert stored.settings == {"theme": "dark"}, "the JSON column was not marked dirty"
    assert stored.name == "Platform Eng"


# --------------------------------------------------------------------------- #
# Invitations
# --------------------------------------------------------------------------- #
def test_cannot_invite_an_existing_member_or_invite_twice():
    team = _create("Platform")
    s = get_team_store()
    _as(lambda: s.add_member(team.id, "u2", "dev@example.com"))

    # Already a member, matched case-insensitively as addresses are.
    assert (
        _as(lambda: s.create_invitation(team.id, "DEV@example.com", TeamRole.MEMBER, "u1"))
        is None
    )

    first = _as(
        lambda: s.create_invitation(team.id, "new@example.com", TeamRole.MEMBER, "u1")
    )
    assert first is not None
    assert (
        _as(lambda: s.create_invitation(team.id, "NEW@example.com", TeamRole.MEMBER, "u1"))
        is None
    )


def test_reinviting_after_a_cancel_is_allowed():
    team = _create("Platform")
    s = get_team_store()
    first = _as(
        lambda: s.create_invitation(team.id, "new@example.com", TeamRole.MEMBER, "u1")
    )

    assert _as(lambda: s.cancel_invitation(first.id)) is True
    assert _as(lambda: s.cancel_invitation(first.id)) is False, "cancelled twice"

    second = _as(
        lambda: s.create_invitation(team.id, "new@example.com", TeamRole.MEMBER, "u1")
    )
    assert second is not None and second.id != first.id


def test_expiry_is_written_back_when_it_is_first_observed():
    team = _create("Platform")
    s = get_team_store()
    invite = _as(
        lambda: s.create_invitation(team.id, "late@example.com", TeamRole.MEMBER, "u1")
    )
    _backdate_invitation(invite.id)

    assert _as(lambda: s.get_invitations(team.id)) == []

    _restart()

    # Persisted, not just filtered out of one listing.
    assert (
        _as(lambda: get_team_store().get_invitation(invite.id)).status
        == InvitationStatus.EXPIRED
    )


def test_an_unobserved_expiry_is_still_not_acceptable():
    """Accept checks the timestamp, not the status — nothing sweeps this table."""
    team = _create("Platform")
    s = get_team_store()
    invite = _as(
        lambda: s.create_invitation(team.id, "late@example.com", TeamRole.MEMBER, "u1")
    )
    _backdate_invitation(invite.id)

    resolved, member = _as(lambda: s.accept_invitation(invite.id, "u2"))
    assert member is None
    assert resolved.status == InvitationStatus.EXPIRED
    assert _as(lambda: s.get_member(team.id, "u2")) is None


def test_accepting_twice_adds_one_membership():
    team = _create("Platform")
    s = get_team_store()
    invite = _as(
        lambda: s.create_invitation(team.id, "new@example.com", TeamRole.MEMBER, "u1")
    )

    _, member = _as(lambda: s.accept_invitation(invite.id, "u2"))
    assert member is not None

    resolved, again = _as(lambda: s.accept_invitation(invite.id, "u3"))
    assert again is None
    assert resolved.status == InvitationStatus.ACCEPTED
    assert len(_as(lambda: s.get_members(team.id))) == 2


def test_accept_carries_the_invited_role():
    team = _create("Platform")
    s = get_team_store()
    invite = _as(
        lambda: s.create_invitation(team.id, "boss@example.com", TeamRole.ADMIN, "u1")
    )

    _, member = _as(lambda: s.accept_invitation(invite.id, "u2"))
    assert member.role == TeamRole.ADMIN

    _restart()

    assert _as(lambda: get_team_store().get_member(team.id, "u2")).role == TeamRole.ADMIN


def test_accepting_an_unknown_invitation_returns_nothing():
    unknown = "11111111-1111-1111-1111-111111111111"
    assert _as(lambda: get_team_store().accept_invitation(unknown, "u2")) == (None, None)


def test_pending_invitations_for_a_user_span_teams_and_ignore_case():
    a = _create("Alpha", "u1", "a@example.com")
    b = _create("Beta", "u2", "b@example.com")
    s = get_team_store()
    _as(lambda: s.create_invitation(a.id, "Wanted@Example.com", TeamRole.MEMBER, "u1"))
    _as(lambda: s.create_invitation(b.id, "wanted@example.com", TeamRole.ADMIN, "u2"))
    stale = _as(
        lambda: s.create_invitation(b.id, "someone-else@example.com", TeamRole.MEMBER, "u2")
    )
    _backdate_invitation(stale.id)

    _restart()

    s = get_team_store()
    mine = _as(lambda: s.get_user_pending_invitations("WANTED@example.com"))
    assert {team.id for _, team in mine} == {a.id, b.id}
    assert all(inv.status == InvitationStatus.PENDING for inv, _ in mine)

    assert _as(lambda: s.get_user_pending_invitations("someone-else@example.com")) == []


def test_last_active_is_stamped_and_persisted():
    team = _create("Platform")
    s = get_team_store()
    assert _as(lambda: s.get_member(team.id, "u1")).last_active is None

    _as(lambda: s.update_member_activity(team.id, "u1"))
    # Nothing to stamp is not an error.
    _as(lambda: s.update_member_activity(team.id, "nobody"))

    _restart()

    stamped = _as(lambda: get_team_store().get_member(team.id, "u1")).last_active
    assert stamped is not None
    assert stamped.tzinfo is not None


# --------------------------------------------------------------------------- #
# Through the API the router actually exposes
# --------------------------------------------------------------------------- #
@pytest.fixture
def api_env(db, monkeypatch):
    """A single-tenant deployment whose users hold OAuth session tokens.

    The original of this file forged a bearer token by base64-encoding a claims
    dict, which is exactly the hole `_get_user_from_header` was fixed to close
    (see tests/test_teams_auth.py). These tests need two distinct user
    identities, so they carry real signed session tokens instead.
    """
    monkeypatch.setenv("COPILOT_API_TOKEN", API_TOKEN)
    monkeypatch.setenv("COPILOT_SECRET_KEY", SECRET)
    monkeypatch.delenv("SUPABASE_JWKS_URL", raising=False)
    _reset_api()
    yield
    # Closed here too: TestClient serves on its own event loop, and aiosqlite
    # connections are bound to the loop that created them.
    asyncio.run(db_engine.close_db())
    _reset_api()


def _reset_api() -> None:
    cfg.get_settings.cache_clear()
    api._RL.clear()
    api._reset_limiter()
    runtime.reset()


def _serve() -> TestClient:
    """A running app on its own event loop.

    Never nested: the status-page poller and the job worker are tasks owned by
    the lifespan that started them, and a second overlapping lifespan tears them
    down from the wrong loop.
    """
    return TestClient(api.app, raise_server_exceptions=False)


@pytest.fixture
def client(api_env):
    with _serve() as c:
        yield c


def _auth(user_id: str, email: str) -> dict[str, str]:
    from app.auth.oauth import create_session_token

    return {"Authorization": f"Bearer {create_session_token(user_id, '-', email)}"}


def test_teams_created_through_the_api_survive_a_restart(api_env):
    with _serve() as client:
        created = client.post(
            "/api/teams",
            json={"name": "Platform"},
            headers=_auth("u1", "owner@example.com"),
        )
        assert created.status_code == 200, created.text
        team_id = created.json()["id"]
        assert created.json()["slug"] == "platform"
        assert created.json()["member_count"] == 1

        listed = client.get("/api/teams", headers=_auth("u1", "owner@example.com"))
        assert [t["id"] for t in listed.json()] == [team_id]

    _restart()
    _reset_api()

    with _serve() as restarted:
        again = restarted.get("/api/teams", headers=_auth("u1", "owner@example.com"))
        assert [t["id"] for t in again.json()] == [team_id]
        assert again.json()[0]["role"] == "owner"


def test_api_rejects_a_slug_another_team_holds(client):
    first = client.post(
        "/api/teams",
        json={"name": "Platform", "slug": "platform"},
        headers=_auth("u1", "a@example.com"),
    )
    assert first.status_code == 200, first.text

    clash = client.post(
        "/api/teams",
        json={"name": "Other", "slug": "platform"},
        headers=_auth("u2", "b@example.com"),
    )
    assert clash.status_code == 409
    assert "platform" in clash.json()["detail"]


def test_invitation_round_trip_through_the_api(client):
    owner = _auth("u1", "owner@example.com")
    team_id = client.post("/api/teams", json={"name": "Platform"}, headers=owner).json()["id"]

    invited = client.post(
        f"/api/teams/{team_id}/invitations",
        json={"email": "invitee@example.com", "role": "member"},
        headers=owner,
    )
    assert invited.status_code == 200, invited.text
    invitation_id = invited.json()["id"]

    invitee = _auth("u2", "invitee@example.com")
    pending = client.get("/api/teams/invitations/pending", headers=invitee)
    assert [p["invitation"]["id"] for p in pending.json()] == [invitation_id]

    accepted = client.post(f"/api/teams/invitations/{invitation_id}/accept", headers=invitee)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["team"]["id"] == team_id

    members = client.get(f"/api/teams/{team_id}/members", headers=invitee)
    assert sorted(m["user_id"] for m in members.json()) == ["u1", "u2"]

    assert client.get("/api/teams/invitations/pending", headers=invitee).json() == []


def test_an_admin_cannot_cancel_another_teams_invitation(client):
    owner_a = _auth("u1", "a@example.com")
    owner_b = _auth("u2", "b@example.com")
    team_a = client.post("/api/teams", json={"name": "Alpha"}, headers=owner_a).json()["id"]
    team_b = client.post("/api/teams", json={"name": "Beta"}, headers=owner_b).json()["id"]

    invitation_id = client.post(
        f"/api/teams/{team_a}/invitations",
        json={"email": "invitee@example.com", "role": "member"},
        headers=owner_a,
    ).json()["id"]

    # u2 owns team_b, so the permission check on the path passes — the id must
    # still be scoped to the team it belongs to.
    stolen = client.delete(
        f"/api/teams/{team_b}/invitations/{invitation_id}", headers=owner_b
    )
    assert stolen.status_code == 404

    still_open = client.get(f"/api/teams/{team_a}/invitations", headers=owner_a)
    assert [i["id"] for i in still_open.json()] == [invitation_id]


def test_api_requires_authorization(client):
    assert client.get("/api/teams").status_code == 401
    assert client.post("/api/teams", json={"name": "Platform"}).status_code == 401
