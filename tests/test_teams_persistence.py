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
"""

from __future__ import annotations

import asyncio
import base64
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import config as cfg
from app import runtime
from app.api import main as api
from app.database.engine import close_db, ensure_schema, get_session
from app.database.models import TeamInvitationRecord, TeamMemberRecord, TeamRecord
from app.teams import store
from app.teams.models import InvitationStatus, TeamRole
from app.teams.store import SlugTaken


@pytest.fixture(autouse=True)
def db(tmp_path, monkeypatch):
    """A database per test, under tmp_path.

    The engine is a module global, so it is disposed on the way in as well as
    out — otherwise a test inherits the previous test's engine, still pointing
    at *that* test's tmp_path.
    """
    monkeypatch.setenv("COPILOT_STATE_DIR", str(tmp_path))
    cfg.get_settings.cache_clear()
    asyncio.run(close_db())
    yield
    asyncio.run(close_db())
    cfg.get_settings.cache_clear()


@pytest.fixture
def client(db):
    api._RL.clear()
    runtime.reset()
    yield TestClient(api.app)
    # Closed here too: TestClient serves on its own event loop, and aiosqlite
    # connections are bound to the loop that created them.
    asyncio.run(close_db())
    api._RL.clear()


def _restart() -> None:
    """Simulate a process restart against the same database file."""
    asyncio.run(close_db())
    cfg.get_settings.cache_clear()


def _auth(user_id: str, email: str) -> dict[str, str]:
    """A bearer token shaped like the JWT the router reads claims out of.

    `_get_user_from_header` base64-decodes the payload segment without verifying
    the signature, so the other two segments can be anything.
    """
    payload = (
        base64.urlsafe_b64encode(json.dumps({"sub": user_id, "email": email}).encode())
        .decode()
        .rstrip("=")
    )
    return {"Authorization": f"Bearer header.{payload}.signature"}


def _backdate_invitation(invitation_id: str, *, days: int = 8) -> None:
    """Push an invitation's expiry into the past, without going through the store.

    Writing the column directly is the point: it produces a row that is stale but
    still marked PENDING, which is exactly the state a real invitation sits in
    until somebody looks at it.
    """

    async def run() -> None:
        await ensure_schema()
        async with get_session() as session:
            row = await session.get(TeamInvitationRecord, invitation_id)
            assert row is not None
            row.expires_at = datetime.now(timezone.utc) - timedelta(days=days)
            await session.commit()

    asyncio.run(run())


def _row_counts() -> tuple[int, int, int]:
    async def run() -> tuple[int, int, int]:
        await ensure_schema()
        async with get_session() as session:
            from sqlalchemy import func, select

            out = []
            for model in (TeamRecord, TeamMemberRecord, TeamInvitationRecord):
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
    team = asyncio.run(store.create_team("Platform", "u1", "owner@example.com"))

    _restart()

    found = asyncio.run(store.get_team(team.id))
    assert found is not None
    assert found.name == "Platform"
    assert found.slug == "platform"
    assert found.owner_id == "u1"


def test_membership_outlives_the_process():
    team = asyncio.run(store.create_team("Platform", "u1", "owner@example.com"))
    asyncio.run(store.add_member(team.id, "u2", "dev@example.com", TeamRole.ADMIN))

    _restart()

    members = asyncio.run(store.get_members(team.id))
    assert [(m.user_id, m.role) for m in members] == [
        ("u1", TeamRole.OWNER),
        ("u2", TeamRole.ADMIN),
    ]


def test_invitation_outlives_the_process_and_is_still_acceptable():
    """The promise made to somebody outside the system still holds."""
    team = asyncio.run(store.create_team("Platform", "u1", "owner@example.com"))
    invite = asyncio.run(
        store.create_invitation(team.id, "invitee@example.com", TeamRole.MEMBER, "u1")
    )
    assert invite is not None

    _restart()

    pending = asyncio.run(store.get_invitations(team.id))
    assert [i.id for i in pending] == [invite.id]

    resolved, member = asyncio.run(store.accept_invitation(invite.id, "u2"))
    assert resolved.status == InvitationStatus.ACCEPTED
    assert member is not None
    assert member.user_id == "u2"


def test_no_in_memory_store_remains():
    """The dicts are gone, not merely shadowed by the tables."""
    assert not hasattr(store, "TeamStore"), "the singleton class is still here"
    assert not hasattr(store, "get_team_store"), "the singleton accessor is still here"
    assert not hasattr(store, "_store"), "the singleton instance is still here"

    source = (store.__file__ or "").replace(".pyc", ".py")
    text = open(source, encoding="utf-8").read()
    for name in ("_teams", "_members", "_invitations", "_user_teams"):
        assert f"self.{name}" not in text, f"{name} is still instance state"


def test_timestamps_read_back_as_utc_aware():
    team = asyncio.run(store.create_team("Platform", "u1", "owner@example.com"))

    _restart()

    found = asyncio.run(store.get_team(team.id))
    assert found.created_at.tzinfo is not None
    assert found.created_at.utcoffset() == timedelta(0)


# --------------------------------------------------------------------------- #
# Slugs identify exactly one team
# --------------------------------------------------------------------------- #
def test_generated_slugs_are_disambiguated():
    first = asyncio.run(store.create_team("Platform", "u1", "a@example.com"))
    second = asyncio.run(store.create_team("Platform", "u2", "b@example.com"))
    third = asyncio.run(store.create_team("platform!", "u3", "c@example.com"))

    assert [first.slug, second.slug, third.slug] == ["platform", "platform-1", "platform-2"]
    assert asyncio.run(store.get_team_by_slug("platform-1")).id == second.id


def test_requested_slug_that_is_taken_is_rejected_not_renamed():
    """Handing back a different slug would break the link the caller is publishing."""
    asyncio.run(store.create_team("Platform", "u1", "a@example.com", slug="platform"))

    with pytest.raises(SlugTaken):
        asyncio.run(store.create_team("Other", "u2", "b@example.com", slug="platform"))

    teams, members, _ = _row_counts()
    assert (teams, members) == (1, 1), "the losing create left rows behind"


def test_requested_slug_is_normalised():
    team = asyncio.run(
        store.create_team("Anything", "u1", "a@example.com", slug="My Team!!")
    )
    assert team.slug == "my-team"


def test_unslugifiable_name_still_gets_a_slug():
    team = asyncio.run(store.create_team("!!!", "u1", "a@example.com"))
    assert team.slug == "team"


def test_concurrent_creates_of_the_same_name_get_distinct_slugs():
    async def run() -> list[str]:
        teams = await asyncio.gather(
            *(
                store.create_team("Platform", f"u{i}", f"u{i}@example.com")
                for i in range(5)
            )
        )
        return [t.slug for t in teams]

    slugs = asyncio.run(run())
    assert len(set(slugs)) == 5, f"slugs collided: {slugs}"


# --------------------------------------------------------------------------- #
# A user is in a team at most once
# --------------------------------------------------------------------------- #
def test_adding_the_same_member_twice_returns_none():
    team = asyncio.run(store.create_team("Platform", "u1", "a@example.com"))
    assert asyncio.run(store.add_member(team.id, "u2", "b@example.com")) is not None
    assert asyncio.run(store.add_member(team.id, "u2", "b@example.com")) is None
    assert len(asyncio.run(store.get_members(team.id))) == 2


def test_concurrent_adds_produce_one_membership():
    team = asyncio.run(store.create_team("Platform", "u1", "a@example.com"))

    async def run() -> list[object]:
        return list(
            await asyncio.gather(
                *(store.add_member(team.id, "u2", "b@example.com") for _ in range(4))
            )
        )

    results = asyncio.run(run())
    assert sum(1 for r in results if r is not None) == 1
    assert len(asyncio.run(store.get_members(team.id))) == 2


def test_add_member_to_a_missing_team_is_refused():
    assert asyncio.run(store.add_member("no-such-team", "u2", "b@example.com")) is None


def test_owner_role_cannot_be_changed_and_owner_cannot_be_removed():
    team = asyncio.run(store.create_team("Platform", "u1", "a@example.com"))

    assert asyncio.run(store.update_member_role(team.id, "u1", TeamRole.VIEWER)) is None
    assert asyncio.run(store.remove_member(team.id, "u1")) is False

    owner = asyncio.run(store.get_member(team.id, "u1"))
    assert owner.role == TeamRole.OWNER


def test_role_change_survives_a_restart():
    team = asyncio.run(store.create_team("Platform", "u1", "a@example.com"))
    asyncio.run(store.add_member(team.id, "u2", "b@example.com"))
    asyncio.run(store.update_member_role(team.id, "u2", TeamRole.ADMIN))

    _restart()

    assert asyncio.run(store.get_member(team.id, "u2")).role == TeamRole.ADMIN


# --------------------------------------------------------------------------- #
# "Which teams am I in" is derived, not indexed by hand
# --------------------------------------------------------------------------- #
def test_user_teams_follow_membership():
    a = asyncio.run(store.create_team("Alpha", "u1", "a@example.com"))
    b = asyncio.run(store.create_team("Beta", "u2", "b@example.com"))
    asyncio.run(store.add_member(b.id, "u1", "a@example.com", TeamRole.VIEWER))

    mine = asyncio.run(store.get_user_teams("u1"))
    assert [(t.id, r) for t, r in mine] == [(a.id, TeamRole.OWNER), (b.id, TeamRole.VIEWER)]

    assert asyncio.run(store.remove_member(b.id, "u1")) is True
    assert [t.id for t, _ in asyncio.run(store.get_user_teams("u1"))] == [a.id]


def test_deleting_a_team_takes_its_members_and_invitations_with_it():
    team = asyncio.run(store.create_team("Platform", "u1", "a@example.com"))
    asyncio.run(store.add_member(team.id, "u2", "b@example.com"))
    asyncio.run(
        store.create_invitation(team.id, "invitee@example.com", TeamRole.MEMBER, "u1")
    )

    assert asyncio.run(store.delete_team(team.id)) is True

    _restart()

    assert _row_counts() == (0, 0, 0)
    assert asyncio.run(store.get_user_teams("u2")) == []
    assert asyncio.run(store.delete_team(team.id)) is False


def test_member_counts_are_one_query_for_many_teams():
    a = asyncio.run(store.create_team("Alpha", "u1", "a@example.com"))
    b = asyncio.run(store.create_team("Beta", "u1", "a@example.com"))
    asyncio.run(store.add_member(a.id, "u2", "b@example.com"))

    counts = asyncio.run(store.member_counts([a.id, b.id, "missing"]))
    assert counts == {a.id: 2, b.id: 1, "missing": 0}


def test_update_team_ignores_fields_it_does_not_own():
    team = asyncio.run(store.create_team("Platform", "u1", "a@example.com"))

    updated = asyncio.run(
        store.update_team(
            team.id,
            {"name": "Platform Eng", "settings": {"theme": "dark"}, "owner_id": "u9"},
        )
    )
    assert updated.name == "Platform Eng"
    assert updated.settings == {"theme": "dark"}
    assert updated.owner_id == "u1", "owner_id was writable through update_team"

    _restart()

    stored = asyncio.run(store.get_team(team.id))
    assert stored.settings == {"theme": "dark"}, "the JSON column was not marked dirty"
    assert stored.name == "Platform Eng"


# --------------------------------------------------------------------------- #
# Invitations
# --------------------------------------------------------------------------- #
def test_cannot_invite_an_existing_member_or_invite_twice():
    team = asyncio.run(store.create_team("Platform", "u1", "a@example.com"))
    asyncio.run(store.add_member(team.id, "u2", "dev@example.com"))

    # Already a member, matched case-insensitively as addresses are.
    assert (
        asyncio.run(
            store.create_invitation(team.id, "DEV@example.com", TeamRole.MEMBER, "u1")
        )
        is None
    )

    first = asyncio.run(
        store.create_invitation(team.id, "new@example.com", TeamRole.MEMBER, "u1")
    )
    assert first is not None
    assert (
        asyncio.run(
            store.create_invitation(team.id, "NEW@example.com", TeamRole.MEMBER, "u1")
        )
        is None
    )


def test_reinviting_after_a_cancel_is_allowed():
    team = asyncio.run(store.create_team("Platform", "u1", "a@example.com"))
    first = asyncio.run(
        store.create_invitation(team.id, "new@example.com", TeamRole.MEMBER, "u1")
    )

    assert asyncio.run(store.cancel_invitation(first.id)) is True
    assert asyncio.run(store.cancel_invitation(first.id)) is False, "cancelled twice"

    second = asyncio.run(
        store.create_invitation(team.id, "new@example.com", TeamRole.MEMBER, "u1")
    )
    assert second is not None and second.id != first.id


def test_expiry_is_written_back_when_it_is_first_observed():
    team = asyncio.run(store.create_team("Platform", "u1", "a@example.com"))
    invite = asyncio.run(
        store.create_invitation(team.id, "late@example.com", TeamRole.MEMBER, "u1")
    )
    _backdate_invitation(invite.id)

    assert asyncio.run(store.get_invitations(team.id)) == []

    _restart()

    # Persisted, not just filtered out of one listing.
    assert asyncio.run(store.get_invitation(invite.id)).status == InvitationStatus.EXPIRED


def test_an_unobserved_expiry_is_still_not_acceptable():
    """Accept checks the timestamp, not the status — nothing sweeps this table."""
    team = asyncio.run(store.create_team("Platform", "u1", "a@example.com"))
    invite = asyncio.run(
        store.create_invitation(team.id, "late@example.com", TeamRole.MEMBER, "u1")
    )
    _backdate_invitation(invite.id)

    resolved, member = asyncio.run(store.accept_invitation(invite.id, "u2"))
    assert member is None
    assert resolved.status == InvitationStatus.EXPIRED
    assert asyncio.run(store.get_member(team.id, "u2")) is None


def test_accepting_twice_adds_one_membership():
    team = asyncio.run(store.create_team("Platform", "u1", "a@example.com"))
    invite = asyncio.run(
        store.create_invitation(team.id, "new@example.com", TeamRole.MEMBER, "u1")
    )

    _, member = asyncio.run(store.accept_invitation(invite.id, "u2"))
    assert member is not None

    resolved, again = asyncio.run(store.accept_invitation(invite.id, "u3"))
    assert again is None
    assert resolved.status == InvitationStatus.ACCEPTED
    assert len(asyncio.run(store.get_members(team.id))) == 2


def test_accept_carries_the_invited_role():
    team = asyncio.run(store.create_team("Platform", "u1", "a@example.com"))
    invite = asyncio.run(
        store.create_invitation(team.id, "boss@example.com", TeamRole.ADMIN, "u1")
    )

    _, member = asyncio.run(store.accept_invitation(invite.id, "u2"))
    assert member.role == TeamRole.ADMIN

    _restart()

    assert asyncio.run(store.get_member(team.id, "u2")).role == TeamRole.ADMIN


def test_accepting_an_unknown_invitation_returns_nothing():
    assert asyncio.run(store.accept_invitation("no-such-invite", "u2")) == (None, None)


def test_pending_invitations_for_a_user_span_teams_and_ignore_case():
    a = asyncio.run(store.create_team("Alpha", "u1", "a@example.com"))
    b = asyncio.run(store.create_team("Beta", "u2", "b@example.com"))
    asyncio.run(store.create_invitation(a.id, "Wanted@Example.com", TeamRole.MEMBER, "u1"))
    asyncio.run(store.create_invitation(b.id, "wanted@example.com", TeamRole.ADMIN, "u2"))
    stale = asyncio.run(
        store.create_invitation(b.id, "someone-else@example.com", TeamRole.MEMBER, "u2")
    )
    _backdate_invitation(stale.id)

    _restart()

    mine = asyncio.run(store.get_user_pending_invitations("WANTED@example.com"))
    assert {team.id for _, team in mine} == {a.id, b.id}
    assert all(inv.status == InvitationStatus.PENDING for inv, _ in mine)

    assert asyncio.run(store.get_user_pending_invitations("someone-else@example.com")) == []


def test_last_active_is_stamped_and_persisted():
    team = asyncio.run(store.create_team("Platform", "u1", "a@example.com"))
    assert asyncio.run(store.get_member(team.id, "u1")).last_active is None

    asyncio.run(store.update_member_activity(team.id, "u1"))
    # Nothing to stamp is not an error.
    asyncio.run(store.update_member_activity(team.id, "nobody"))

    _restart()

    stamped = asyncio.run(store.get_member(team.id, "u1")).last_active
    assert stamped is not None
    assert stamped.tzinfo is not None


# --------------------------------------------------------------------------- #
# Through the API the router actually exposes
# --------------------------------------------------------------------------- #
def test_teams_created_through_the_api_survive_a_restart(client):
    created = client.post(
        "/api/teams", json={"name": "Platform"}, headers=_auth("u1", "owner@example.com")
    )
    assert created.status_code == 200, created.text
    team_id = created.json()["id"]
    assert created.json()["slug"] == "platform"
    assert created.json()["member_count"] == 1

    listed = client.get("/api/teams", headers=_auth("u1", "owner@example.com"))
    assert [t["id"] for t in listed.json()] == [team_id]

    _restart()

    with TestClient(api.app) as restarted:
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
