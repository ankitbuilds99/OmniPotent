"""The Notification Preferences backend.

There was no route at all behind the page: `GET /api/notifications/preferences`
returned 404 and nothing in the route dump matched "notif". The page hardcoded
nine channels and eight routing rules as component literals — with
`configured: true` baked into the first two — and its Save button was

    const saveConfig = async () => { setSaved(true); setTimeout(...) }

which issued zero requests. Toggling a channel, pasting a Slack webhook or
switching off deploy notifications survived exactly until the next reload.

These tests pin the properties that make the replacement real rather than
another convincing-looking literal: values survive a second request, secrets are
never echoed back, unknown ids are rejected instead of silently dropped, one org
cannot read another's routing, and a viewer cannot rewrite it.
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

PREFS = "/api/notifications/preferences"


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Single-tenant app, which is the deployment the page defaults to."""
    monkeypatch.setenv("COPILOT_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/notif.db")
    monkeypatch.setenv("COPILOT_TENANT_DB", str(tmp_path / "tenants.sqlite"))
    cfg.get_settings.cache_clear()
    tenant_auth.reset_store_cache()
    tenant_context.current_tenant.set(None)
    db_engine._engine = None
    db_engine._session_factory = None
    with TestClient(api.app) as c:
        yield c
    cfg.get_settings.cache_clear()
    tenant_auth.reset_store_cache()
    tenant_context.current_tenant.set(None)
    db_engine._engine = None
    db_engine._session_factory = None


async def _provision(store):
    await store.setup()
    acme = await store.create_org("Acme", plan="team")
    admin_key, _ = await store.issue_api_key(acme.id, "k-admin", role="admin")
    viewer_key, _ = await store.issue_api_key(acme.id, "k-view", role="viewer")
    beta = await store.create_org("Beta", plan="free")
    beta_key, _ = await store.issue_api_key(beta.id, "k-beta", role="admin")
    return {"admin": admin_key, "viewer": viewer_key, "beta": beta_key}


@pytest.fixture
def mt(tmp_path, monkeypatch):
    """Multi-tenant app, for the isolation and RBAC properties."""
    monkeypatch.setenv("COPILOT_MULTI_TENANT", "true")
    monkeypatch.setenv("COPILOT_TENANT_DB", str(tmp_path / "tenants.sqlite"))
    monkeypatch.setenv("COPILOT_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/app.db")
    cfg.get_settings.cache_clear()
    tenant_auth.reset_store_cache()
    tenant_context.current_tenant.set(None)
    api._RL.clear()
    runtime.reset()
    db_engine._engine = None
    db_engine._session_factory = None
    data = asyncio.run(_provision(tenant_auth.get_store()))
    with TestClient(api.app) as c:
        data["client"] = c
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


def _channel(body: dict, cid: str) -> dict:
    return next(c for c in body["channels"] if c["id"] == cid)


def _rule(body: dict, event: str) -> dict:
    return next(r for r in body["rules"] if r["event"] == event)


def test_preferences_route_exists_and_describes_the_catalogue(client):
    """The route the page calls used to be a 404."""
    res = client.get(PREFS)
    assert res.status_code == 200, res.text
    body = res.json()
    assert {c["id"] for c in body["channels"]} >= {"email", "slack", "pagerduty", "webhook"}
    assert {r["event"] for r in body["rules"]} >= {"incident_created", "deployment_failed"}
    # Nothing has been saved, so nothing may claim to be configured. The old page
    # shipped `configured: true` on Email and Slack as a component literal.
    assert all(c["configured"] is False for c in body["channels"])
    assert body["customised"] is False


def test_a_toggle_survives_a_reload(client):
    """The whole point: the second GET reflects the PUT, not the defaults."""
    before = client.get(PREFS).json()
    assert _channel(before, "slack")["enabled"] is True

    saved = client.put(PREFS, json={"channels": [{"id": "slack", "enabled": False}]})
    assert saved.status_code == 200, saved.text
    assert _channel(saved.json(), "slack")["enabled"] is False

    after = client.get(PREFS).json()
    assert _channel(after, "slack")["enabled"] is False
    assert after["customised"] is True


def test_a_routing_rule_survives_a_reload(client):
    """Per-event routing is persisted per (event, channel), not defaulted back."""
    assert _rule(client.get(PREFS).json(), "deployment_started")["channels"]["email"] is False

    res = client.put(
        PREFS,
        json={"rules": [{"event": "deployment_started", "channels": {"email": True}}]},
    )
    assert res.status_code == 200, res.text

    reloaded = _rule(client.get(PREFS).json(), "deployment_started")["channels"]
    assert reloaded["email"] is True
    # Only the channel that was sent changed; the rest of the row is untouched.
    assert reloaded["slack"] is True


def test_quiet_hours_and_digest_mode_are_persisted(client):
    res = client.put(
        PREFS,
        json={
            "quiet_hours": {"enabled": True, "start": "9:5", "end": "17:00"},
            "digest_mode": "hourly",
        },
    )
    assert res.status_code == 200, res.text
    body = client.get(PREFS).json()
    # Normalised on the way in, so "9:5" cannot come back as an unparseable time.
    assert body["quiet_hours"] == {"enabled": True, "start": "09:05", "end": "17:00"}
    assert body["digest_mode"] == "hourly"


def test_a_secret_is_stored_but_never_echoed_back(client):
    """A stored Slack webhook must not be readable by everyone who opens the page."""
    webhook = "https://hooks.slack.example/T000/B000/xoxb-not-a-real-secret"
    res = client.put(
        PREFS,
        json={"channels": [{"id": "slack", "enabled": True, "config": {"webhook": webhook}}]},
    )
    assert res.status_code == 200, res.text

    body = client.get(PREFS)
    assert webhook not in body.text
    slack = _channel(body.json(), "slack")
    field = next(f for f in slack["fields"] if f["key"] == "webhook")
    assert field["secret"] is True
    assert field["value"] == ""
    # ...but the page can still tell the user it is set.
    assert field["is_set"] is True
    assert slack["configured"] is True


def test_a_blank_secret_does_not_silently_wipe_the_stored_one(client):
    """The form cannot echo a secret back, so a blank submit must mean "unchanged"."""
    client.put(
        PREFS,
        json={"channels": [{"id": "slack", "config": {"webhook": "https://hooks/x"}}]},
    )
    # What the page sends when the user edits some other field and leaves the
    # (necessarily empty) secret input alone.
    client.put(PREFS, json={"channels": [{"id": "slack", "enabled": True, "config": {"webhook": ""}}]})
    assert _channel(client.get(PREFS).json(), "slack")["configured"] is True

    # Erasing is explicit.
    client.put(PREFS, json={"channels": [{"id": "slack", "config": {"webhook": "__clear__"}}]})
    assert _channel(client.get(PREFS).json(), "slack")["configured"] is False


def test_a_non_secret_field_round_trips_its_value(client):
    """An email address is not a credential, so the form can prefill it."""
    client.put(PREFS, json={"channels": [{"id": "email", "config": {"address": "sre@acme.test"}}]})
    email = _channel(client.get(PREFS).json(), "email")
    field = next(f for f in email["fields"] if f["key"] == "address")
    assert field["value"] == "sre@acme.test"
    assert email["configured"] is True


def test_an_unknown_channel_is_rejected_not_dropped(client):
    """Silently dropping it is how a save reports success and changes nothing."""
    res = client.put(PREFS, json={"channels": [{"id": "carrier-pigeon", "enabled": True}]})
    assert res.status_code == 422, res.text
    assert "carrier-pigeon" in res.text


def test_an_unknown_event_is_rejected_not_dropped(client):
    res = client.put(PREFS, json={"rules": [{"event": "server_caught_fire", "channels": {}}]})
    assert res.status_code == 422, res.text
    assert "server_caught_fire" in res.text


def test_a_rule_naming_an_unknown_channel_is_rejected(client):
    res = client.put(
        PREFS, json={"rules": [{"event": "escalation", "channels": {"telepathy": True}}]}
    )
    assert res.status_code == 422, res.text
    assert "telepathy" in res.text


def test_a_bad_quiet_hours_time_is_rejected(client):
    for bad in ("25:00", "noon", "10:75"):
        res = client.put(PREFS, json={"quiet_hours": {"enabled": True, "start": bad}})
        assert res.status_code == 422, f"{bad!r} was accepted: {res.text}"


def test_an_unknown_digest_mode_is_rejected(client):
    res = client.put(PREFS, json={"digest_mode": "fortnightly"})
    assert res.status_code == 422, res.text


def test_the_same_channel_twice_is_rejected(client):
    """Two entries for one channel means the save's outcome depends on list order."""
    res = client.put(
        PREFS,
        json={"channels": [{"id": "slack", "enabled": True}, {"id": "slack", "enabled": False}]},
    )
    assert res.status_code == 422, res.text
    assert "slack" in res.text


def test_one_org_cannot_read_or_write_anothers_routing(mt):
    """Routing carries a PagerDuty routing key; it must not leak across tenants."""
    c = mt["client"]
    res = c.put(
        PREFS,
        json={"channels": [{"id": "slack", "enabled": False}]},
        headers=_auth(mt["admin"]),
    )
    assert res.status_code == 200, res.text

    beta = c.get(PREFS, headers=_auth(mt["beta"]))
    assert beta.status_code == 200, beta.text
    # Beta has saved nothing, so it sees the defaults, not Acme's edit.
    assert _channel(beta.json(), "slack")["enabled"] is True
    assert beta.json()["customised"] is False

    # And Acme still sees its own.
    acme = c.get(PREFS, headers=_auth(mt["admin"]))
    assert _channel(acme.json(), "slack")["enabled"] is False


def test_a_viewer_may_read_but_not_rewrite_the_routing(mt):
    c = mt["client"]
    assert c.get(PREFS, headers=_auth(mt["viewer"])).status_code == 200

    res = c.put(
        PREFS, json={"channels": [{"id": "slack", "enabled": False}]}, headers=_auth(mt["viewer"])
    )
    assert res.status_code == 403, res.text
    # The rejection has to be real, not cosmetic.
    assert _channel(c.get(PREFS, headers=_auth(mt["admin"])).json(), "slack")["enabled"] is True


def test_preferences_require_a_credential(mt):
    c = mt["client"]
    assert c.get(PREFS).status_code == 401
    assert c.put(PREFS, json={"channels": []}).status_code == 401


def test_channels_for_event_respects_both_switches(client):
    """A channel switched off at the top of the page receives nothing, however
    the per-event matrix is set — the read the dispatcher actually makes."""
    from app.notifications.preferences import channels_for_event

    client.put(
        PREFS,
        json={
            "channels": [{"id": "slack", "enabled": False}, {"id": "email", "enabled": True}],
            "rules": [
                {"event": "incident_created", "channels": {"slack": True, "email": True}}
            ],
        },
    )
    with TestClient(api.app):
        routed = asyncio.run(channels_for_event("incident_created"))
    assert "email" in routed
    assert "slack" not in routed
