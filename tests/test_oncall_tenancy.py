"""On-call is tenant-isolated, and reachable at all.

Two bugs met here. Every /oncall/* endpoint answered 500 because the router used
`get_session` — an @asynccontextmanager — directly as a FastAPI dependency, and
the tables it wanted never existed because nothing ever called `init_db()`. Once
both were fixed the isolation hole underneath showed: rows were written with the
nil org UUID and no query filtered on org, so any tenant could read and edit
another's teams, schedules and alerts — including paging their responders.

These run the real app with lifespan (so init_db runs) against a tmp SQLite file
and two real tenants holding real API keys.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

import app.api.main as api
import app.config as cfg
from app import runtime, tenant_context
from app.database import engine as db_engine
from app.tenancy import auth as tenant_auth


async def _provision(store):
    await store.setup()
    org_a = await store.create_org("Acme", plan="team")
    key_a, _ = await store.issue_api_key(org_a.id, "k-a", role="admin")
    org_b = await store.create_org("Umbrella", plan="team")
    key_b, _ = await store.issue_api_key(org_b.id, "k-b", role="admin")
    return {"a": key_a, "b": key_b, "org_a": org_a.id, "org_b": org_b.id}


@pytest.fixture
def oncall(tmp_path, monkeypatch):
    monkeypatch.setenv("COPILOT_MULTI_TENANT", "true")
    monkeypatch.setenv("COPILOT_TENANT_DB", str(tmp_path / "tenants.sqlite"))
    monkeypatch.setenv("COPILOT_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/oncall.db")
    cfg.get_settings.cache_clear()
    tenant_auth.reset_store_cache()
    tenant_context.current_tenant.set(None)
    api._RL.clear()
    runtime.reset()
    # The engine is a module global cached across tests; drop it so this test
    # gets the tmp database rather than whichever one ran first.
    db_engine._engine = None
    db_engine._session_factory = None

    data = asyncio.run(_provision(tenant_auth.get_store()))
    with TestClient(api.app) as client:  # `with` runs lifespan, which runs init_db
        data["client"] = client
        yield data

    cfg.get_settings.cache_clear()
    tenant_auth.reset_store_cache()
    tenant_context.current_tenant.set(None)
    api._RL.clear()
    runtime.reset()
    db_engine._engine = None
    db_engine._session_factory = None


def _auth(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"}


def _team(client, key, slug="platform") -> str:
    r = client.post(
        "/oncall/teams", headers=_auth(key), json={"name": "Platform", "slug": slug}
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_creating_a_team_works_at_all(oncall):
    """The regression guard for the 500: the endpoint has to reach the database."""
    r = oncall["client"].post(
        "/oncall/teams",
        headers=_auth(oncall["a"]),
        json={"name": "Platform", "slug": "platform"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["slug"] == "platform"


def test_two_tenants_can_hold_the_same_team_slug(oncall):
    """Slug uniqueness is per org. Checking it globally let one tenant's naming
    choice deny the name to everyone else."""
    a = _team(oncall["client"], oncall["a"])
    b = _team(oncall["client"], oncall["b"])
    assert a != b


def test_teams_are_not_visible_across_tenants(oncall):
    client = oncall["client"]
    team_a = _team(client, oncall["a"])
    _team(client, oncall["b"], slug="other")

    listed_b = client.get("/oncall/teams", headers=_auth(oncall["b"])).json()
    assert all(t["id"] != team_a for t in listed_b)

    assert client.get(f"/oncall/teams/{team_a}", headers=_auth(oncall["b"])).status_code == 404
    assert client.get(f"/oncall/teams/{team_a}", headers=_auth(oncall["a"])).status_code == 200


def test_another_tenants_schedule_is_not_readable(oncall):
    client = oncall["client"]
    team_a = _team(client, oncall["a"])
    r = client.post(
        "/oncall/schedules",
        headers=_auth(oncall["a"]),
        json={"team_id": team_a, "name": "Primary"},
    )
    assert r.status_code == 200, r.text
    schedule = r.json()["id"]

    b = _auth(oncall["b"])
    assert client.get(f"/oncall/schedules/{schedule}", headers=b).status_code == 404
    assert client.get(f"/oncall/schedules/{schedule}/overrides", headers=b).status_code == 404
    # The iCal feed is the sneakiest one: it hands out a whole roster as a file.
    assert client.get(f"/oncall/schedules/{schedule}/ical", headers=b).status_code == 404
    assert client.get(f"/oncall/schedules/{schedule}/ical", headers=_auth(oncall["a"])).status_code == 200


def test_alerts_do_not_cross_tenants(oncall):
    client = oncall["client"]
    team_a = _team(client, oncall["a"])
    r = client.post(
        "/oncall/alerts",
        headers=_auth(oncall["a"]),
        json={"team_id": team_a, "title": "db down", "severity": "critical", "source": "test"},
    )
    assert r.status_code == 200, r.text

    assert len(client.get("/oncall/alerts", headers=_auth(oncall["a"])).json()) == 1
    assert client.get("/oncall/alerts", headers=_auth(oncall["b"])).json() == []


def test_a_tenant_cannot_raise_an_alert_against_another_tenants_team(oncall):
    """Alert creation pages whoever is on call for the named team, so accepting
    a team_id without an ownership check let any tenant page any other's."""
    client = oncall["client"]
    team_a = _team(client, oncall["a"])
    r = client.post(
        "/oncall/alerts",
        headers=_auth(oncall["b"]),
        json={"team_id": team_a, "title": "noise", "severity": "low", "source": "test"},
    )
    assert r.status_code == 404
