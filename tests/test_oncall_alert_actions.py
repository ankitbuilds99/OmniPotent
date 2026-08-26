"""Acknowledge and Resolve on an on-call alert.

The dashboard rendered both buttons on every unresolved alert and neither one
could ever work: `user_id` was a required *query* parameter, the page sent none,
so FastAPI rejected the request with a 422 before any handler ran. The click
handler was `if (res.ok) { ... }` with no else branch, so nothing appeared on
screen — the operator pressed Acknowledge, saw the row stay exactly as it was,
and the escalation policy kept paging.

Two properties are pinned here:

* the routes the dashboard actually calls work with no `user_id` at all, and
  attribute the action to the authenticated principal when one can be resolved;
* losing a race is a 409, not a 500. `acknowledge_alert` raises ValueError for
  an alert somebody else already picked up, and an unhandled ValueError out of a
  route handler is an internal server error — a scary result for the benign case
  of two responders reaching for the same alert at once.

Covering the routes for the first time also exposed a third defect behind them:
resolution notes were assigned into the `custom_fields` dict in place, which
SQLAlchemy does not treat as a change, so they were never written.
"""

from __future__ import annotations

import json
import sqlite3
import uuid

import pytest
from fastapi.testclient import TestClient

import app.api.main as api
import app.config as cfg
from app import tenant_context
from app.database import engine as db_engine


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "alerts.db"


@pytest.fixture
def client(db_path, monkeypatch):
    monkeypatch.setenv("COPILOT_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.delenv("COPILOT_MULTI_TENANT", raising=False)
    monkeypatch.delenv("COPILOT_API_TOKEN", raising=False)
    cfg.get_settings.cache_clear()
    db_engine._engine = None
    db_engine._session_factory = None
    tenant_context.current_tenant.set(None)
    api._RL.clear()
    with TestClient(api.app) as c:
        yield c
    cfg.get_settings.cache_clear()
    db_engine._engine = None
    db_engine._session_factory = None
    tenant_context.current_tenant.set(None)


def _team(client) -> str:
    res = client.post(
        "/oncall/teams",
        json={"name": "SRE", "slug": f"sre-{uuid.uuid4().hex[:8]}"},
    )
    assert res.status_code == 200, res.text
    return res.json()["id"]


def _stored(db_path, alert_id: str) -> sqlite3.Row:
    """The alert row itself.

    `AlertResponse` carries only id/title/severity/status and the three
    timestamps, so `acknowledged_by` and `custom_fields` are not observable
    through the API and have to be read from the table. `sa.Uuid` stores as
    CHAR(32) with the dashes removed on SQLite.
    """
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            "SELECT * FROM oncall_alerts WHERE id = ?", (alert_id.replace("-", ""),)
        ).fetchone()
    finally:
        con.close()
    assert row is not None, f"no oncall_alerts row for {alert_id}"
    return row


def _alert(client, team_id: str) -> str:
    res = client.post(
        "/oncall/alerts",
        json={
            "team_id": team_id,
            "title": "Checkout API returning 500s",
            "severity": "critical",
            "source": "test",
        },
    )
    assert res.status_code == 200, res.text
    return res.json()["id"]


def test_acknowledge_without_a_user_id(client):
    """The exact request the dashboard sends: POST, no query string, no body."""
    alert_id = _alert(client, _team(client))

    res = client.post(f"/oncall/alerts/{alert_id}/acknowledge")

    assert res.status_code == 200, res.text
    assert res.json()["status"] == "acknowledged"


def test_resolve_without_a_user_id(client):
    alert_id = _alert(client, _team(client))

    res = client.post(f"/oncall/alerts/{alert_id}/resolve")

    assert res.status_code == 200, res.text
    assert res.json()["status"] == "resolved"


def test_acknowledging_twice_is_a_conflict_not_a_server_error(client):
    """Two responders reaching for the same alert is ordinary, not a crash."""
    alert_id = _alert(client, _team(client))
    assert client.post(f"/oncall/alerts/{alert_id}/acknowledge").status_code == 200

    res = client.post(f"/oncall/alerts/{alert_id}/acknowledge")

    assert res.status_code == 409, res.text
    # The message has to name the state the alert is actually in — "it failed"
    # tells the second responder nothing about whether the alert is covered.
    assert "acknowledged" in res.json()["detail"]


def test_resolving_twice_is_a_conflict_not_a_server_error(client):
    alert_id = _alert(client, _team(client))
    assert client.post(f"/oncall/alerts/{alert_id}/resolve").status_code == 200

    res = client.post(f"/oncall/alerts/{alert_id}/resolve")

    assert res.status_code == 409, res.text
    assert "already resolved" in res.json()["detail"]


def test_an_alert_that_does_not_exist_is_a_404(client):
    res = client.post(f"/oncall/alerts/{uuid.uuid4()}/acknowledge")
    assert res.status_code == 404


def test_resolution_notes_are_saved(client, db_path):
    """`notes` stayed a query parameter; only `user_id` became optional.

    Writing this turned up a second defect underneath the first: `resolve_alert`
    did `alert.custom_fields["resolution_notes"] = notes` on a plain JSON column,
    an in-place mutation SQLAlchemy does not track, so the commit wrote nothing
    and the responder's note vanished. It had never been reachable to notice —
    the route it arrives through was a 422 for every caller.
    """
    alert_id = _alert(client, _team(client))

    res = client.post(
        f"/oncall/alerts/{alert_id}/resolve", params={"notes": "Rolled back deploy 4821."}
    )

    assert res.status_code == 200, res.text
    custom = json.loads(_stored(db_path, alert_id)["custom_fields"] or "{}")
    assert custom["resolution_notes"] == "Rolled back deploy 4821."


def test_an_explicit_user_id_is_still_honoured(client, db_path):
    """The parameter did not go away — an integration that knows who is acting
    can still say so, and a caller passing one must not now be ignored."""
    alert_id = _alert(client, _team(client))
    member_id = uuid.uuid4()

    res = client.post(
        f"/oncall/alerts/{alert_id}/acknowledge", params={"user_id": str(member_id)}
    )

    assert res.status_code == 200, res.text
    assert _stored(db_path, alert_id)["acknowledged_by"] == member_id.hex


def test_an_unattributable_acknowledge_still_acknowledges(client, db_path):
    """No signed-in principal to resolve, so `acknowledged_by` stays NULL.

    Deliberately: the column is a foreign key into `members`, and inventing an
    id to fill it would be a constraint violation on Postgres — i.e. back to a
    button that cannot work. An alert nobody is named against is still an alert
    that has stopped escalating, which is the entire point of the click.
    """
    alert_id = _alert(client, _team(client))

    assert client.post(f"/oncall/alerts/{alert_id}/acknowledge").status_code == 200

    row = _stored(db_path, alert_id)
    assert row["status"] == "acknowledged"
    assert row["acknowledged_at"] is not None
    assert row["acknowledged_by"] is None
