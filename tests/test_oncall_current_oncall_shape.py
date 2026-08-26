"""GET /oncall/teams/{id}/current-oncall has to describe the roster, not just IDs.

It returned exactly {"primary","secondary","shadows","schedules"}, while the
On-Call page reads `shifts`, `participants` and a schedule id. With none of
those present the roster and responder list rendered permanently empty and the
schedule id fell back to the literal string "default", which every
POST /oncall/overrides rejected with 422 "Input should be a valid UUID".
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import app.api.main as api
import app.config as cfg
from app import runtime, tenant_context
from app.database import engine as db_engine
from app.tenancy import auth as tenant_auth


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("COPILOT_MULTI_TENANT", "false")
    monkeypatch.delenv("COPILOT_API_TOKEN", raising=False)
    monkeypatch.setenv("COPILOT_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/app.db")
    cfg.get_settings.cache_clear()
    tenant_auth.reset_store_cache()
    tenant_context.current_tenant.set(None)
    api._RL.clear()
    runtime.reset()
    db_engine._engine = None
    db_engine._session_factory = None
    with TestClient(api.app) as c:
        yield c
    cfg.get_settings.cache_clear()
    tenant_context.current_tenant.set(None)
    api._RL.clear()
    runtime.reset()
    db_engine._engine = None
    db_engine._session_factory = None


async def _make_member(email: str) -> str:
    from app.database.engine import get_session
    from app.database.models import Member
    from app.database.orgs import ensure_org_row, org_uuid

    async with get_session() as session:
        await ensure_org_row(session)
        member = Member(org_id=org_uuid(), email=email, role="responder")
        session.add(member)
        await session.commit()
        await session.refresh(member)
        return str(member.id)


def _team(client) -> str:
    res = client.post(
        "/oncall/teams",
        json={"name": "Platform", "slug": f"platform-{uuid.uuid4().hex[:8]}", "timezone": "UTC"},
    )
    assert res.status_code == 200, res.text
    return res.json()["id"]


async def test_current_oncall_lists_participants_with_real_emails(client):
    team = _team(client)
    user = await _make_member("dana@acme.test")
    assert client.post(f"/oncall/teams/{team}/members", json={"user_id": user}).status_code == 200

    body = client.get(f"/oncall/teams/{team}/current-oncall").json()

    assert body["participants"] == [
        {
            "user_id": user,
            "user_email": "dana@acme.test",
            "role": "member",
            "timezone": None,
            "skills": [],
        }
    ]


async def test_current_oncall_exposes_a_real_schedule_id_before_any_shift_exists(client):
    team = _team(client)
    created = client.post(
        "/oncall/schedules", json={"team_id": team, "name": "Primary", "timezone": "UTC"}
    )
    assert created.status_code == 200, created.text
    schedule_id = created.json()["id"]

    body = client.get(f"/oncall/teams/{team}/current-oncall").json()

    # Nobody is on call yet, so `schedules` (coverage) is empty...
    assert body["schedules"] == []
    # ...but the schedule itself is still addressable, which is what the
    # override flow needs. Previously the UI had to invent "default" here.
    assert [s["id"] for s in body["team_schedules"]] == [schedule_id]
    assert body["shifts"] == []


async def test_current_oncall_returns_the_shift_rows_behind_primary(client):
    team = _team(client)
    user = await _make_member("ada@acme.test")
    client.post(f"/oncall/teams/{team}/members", json={"user_id": user})
    schedule_id = client.post(
        "/oncall/schedules",
        json={"team_id": team, "name": "Primary", "timezone": "UTC", "participants": [user]},
    ).json()["id"]
    today = date.today()
    gen = client.post(
        f"/oncall/schedules/{schedule_id}/generate-shifts",
        json={
            "start_date": str(today - timedelta(days=1)),
            "end_date": str(today + timedelta(days=14)),
        },
    )
    assert gen.status_code == 200, gen.text

    body = client.get(f"/oncall/teams/{team}/current-oncall").json()

    assert body["primary"] == user
    assert len(body["shifts"]) == 1
    shift = body["shifts"][0]
    assert shift["schedule_id"] == schedule_id
    assert shift["user_id"] == user
    assert shift["user_email"] == "ada@acme.test"
    assert shift["role"] == "primary"
    assert shift["source"] == "shift"
    assert shift["start_time"] and shift["end_time"]


async def test_an_override_shows_up_as_the_current_shift(client):
    team = _team(client)
    regular = await _make_member("ada@acme.test")
    cover = await _make_member("grace@acme.test")
    for u in (regular, cover):
        client.post(f"/oncall/teams/{team}/members", json={"user_id": u})
    schedule_id = client.post(
        "/oncall/schedules",
        json={"team_id": team, "name": "Primary", "timezone": "UTC", "participants": [regular]},
    ).json()["id"]

    now = datetime.now(timezone.utc)
    res = client.post(
        "/oncall/overrides",
        json={
            "schedule_id": schedule_id,
            "override_user_id": cover,
            "start_time": (now - timedelta(hours=1)).isoformat(),
            "end_time": (now + timedelta(hours=4)).isoformat(),
        },
    )
    assert res.status_code == 200, res.text

    body = client.get(f"/oncall/teams/{team}/current-oncall").json()
    assert body["primary"] == cover
    assert [s["source"] for s in body["shifts"]] == ["override"]
    assert body["shifts"][0]["user_email"] == "grace@acme.test"


def test_the_literal_default_is_still_not_a_schedule(client):
    """Pins the failure the UI used to hit, so the guard stays meaningful."""
    now = datetime.now(timezone.utc)
    res = client.post(
        "/oncall/overrides",
        json={
            "schedule_id": "default",
            "override_user_id": str(uuid.uuid4()),
            "start_time": now.isoformat(),
            "end_time": (now + timedelta(hours=1)).isoformat(),
        },
    )
    assert res.status_code == 422
