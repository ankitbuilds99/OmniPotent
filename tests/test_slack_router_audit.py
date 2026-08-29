"""The Slack bot acted on incidents it had not been asked about, and confirmed
writes it had not made.

Two defects, both of which end with a channel believing something false while an
incident is running:

  * `_target_incident` could not tell "no id was given" from "that id is not an
    incident here". A mistyped or stale uuid — or the eight-character short id
    the bot itself prints, which is not a parseable uuid — fell through to the
    most recent unresolved incident, so `/incident resolve <wrong-id>` closed
    somebody else's incident and left the one that was meant to be closed open.

  * Every incident button and modal went through the confirmation-only action
    handler in `app/slack_bot/interactions.py`, which builds Block Kit and
    writes nothing. Clicking Acknowledge printed ":white_check_mark: Incident
    Acknowledged" while `incidents.status` never moved and escalation kept
    paging; the Resolve, Escalate, Snooze and Update modals were the same. The
    `ack_` prefix also swallowed `ack_alert_<id>` from the on-call page and
    `ack_handoff_<user>` from the shift handoff card, acknowledging "incidents"
    that never existed.

These tests pin the rule the rest of this bot already follows: act on the thing
that was named or refuse, and claim only what the store actually recorded.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

import app.api.main as api
import app.config as cfg
from app import runtime
from app.database import engine as db_engine

SECRET = "s3cr3t"


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("COPILOT_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/slack.db")
    monkeypatch.setenv("SLACK_SIGNING_SECRET", SECRET)
    monkeypatch.delenv("COPILOT_API_TOKEN", raising=False)
    cfg.get_settings.cache_clear()
    api._RL.clear()
    api._reset_limiter()
    runtime.reset()
    db_engine._engine = None
    db_engine._session_factory = None
    with TestClient(api.app, raise_server_exceptions=False) as c:
        yield c
    cfg.get_settings.cache_clear()
    api._RL.clear()
    api._reset_limiter()
    runtime.reset()
    db_engine._engine = None
    db_engine._session_factory = None


def _signed(body: bytes) -> dict[str, str]:
    ts = str(int(time.time()))
    base = b"v0:" + ts.encode() + b":" + body
    return {
        "X-Slack-Request-Timestamp": ts,
        "X-Slack-Signature": "v0=" + hmac.new(
            SECRET.encode(), base, hashlib.sha256
        ).hexdigest(),
        "Content-Type": "application/x-www-form-urlencoded",
    }


def _command(client: TestClient, path: str, text: str = "") -> dict:
    body = urlencode({
        "text": text, "user_id": "U1", "user_name": "n",
        "channel_id": "C1", "channel_name": "cn", "team_id": "T1",
        # No response_url: nothing in these tests should reach out to Slack.
        "response_url": "", "trigger_id": "t",
    }).encode()
    r = client.post(path, content=body, headers=_signed(body))
    assert r.status_code == 200, r.text
    return r.json()


def _interact(client: TestClient, payload: dict) -> dict:
    body = urlencode({"payload": json.dumps(payload)}).encode()
    r = client.post("/api/slack/interactivity", content=body, headers=_signed(body))
    assert r.status_code == 200, r.text
    return r.json()


def _click(client: TestClient, action_id: str, user_id: str = "U1") -> dict:
    return _interact(client, {
        "type": "block_actions",
        "user": {"id": user_id, "username": "responder"},
        "channel": {"id": "C1"},
        "trigger_id": "t",
        "actions": [{"action_id": action_id, "type": "button", "value": "clicked"}],
    })


def _submit(client: TestClient, callback_id: str, values: dict) -> dict:
    return _interact(client, {
        "type": "view_submission",
        "user": {"id": "U1", "username": "responder"},
        "view": {"callback_id": callback_id, "state": {"values": values}},
    })


def _select(block: str, action: str, value: str) -> dict:
    return {block: {action: {"selected_option": {"value": value}}}}


def _text_of(payload: dict) -> str:
    return json.dumps(payload)


async def _incidents():
    from app.incidents.store import get_incident_store

    return await get_incident_store().list_incidents(limit=50)


async def _by_title():
    return {i.title: i for i in await _incidents()}


async def _two_incidents(client: TestClient):
    """`payments` first, then `unrelated` — so `unrelated` is the fallback pick."""
    _command(client, "/api/slack/commands/incident", text="create payments")
    _command(client, "/api/slack/commands/incident", text="create unrelated")
    stored = await _by_title()
    return stored["payments"], stored["unrelated"]


UNKNOWN = "00000000-0000-0000-0000-000000000000"


# --------------------------------------------------------------------------- #
# An id that names no incident must stop the command, not redirect it
# --------------------------------------------------------------------------- #
class TestUnknownIncidentId:
    async def test_resolve_with_an_unknown_id_resolves_nothing(self, client):
        payments, unrelated = await _two_incidents(client)

        body = _text_of(_command(
            client,
            "/api/slack/commands/incident",
            text=f"resolve {UNKNOWN} fixed by restart",
        ))

        assert "Incident Resolved" not in body
        assert UNKNOWN in body and "no incident" in body
        after = await _by_title()
        assert after["payments"].status.value != "resolved"
        assert after["unrelated"].status.value != "resolved"

    async def test_ack_with_an_unknown_id_acknowledges_nothing(self, client):
        await _two_incidents(client)

        body = _text_of(_command(
            client, "/api/slack/commands/incident", text=f"ack {UNKNOWN}"
        ))

        assert "Acknowledged" not in body
        for incident in await _incidents():
            assert incident.acknowledged_by is None

    async def test_severity_with_an_unknown_id_changes_nothing(self, client):
        await _two_incidents(client)

        _command(client, "/api/slack/commands/incident", text=f"severity {UNKNOWN} P1")

        for incident in await _incidents():
            assert incident.severity.value == "high"

    async def test_the_short_id_the_bot_prints_targets_that_incident(self, client):
        """`_short` is the only id that ever reaches the channel, so it is the
        one a responder copies back; it is not a parseable uuid."""
        payments, unrelated = await _two_incidents(client)

        body = _text_of(_command(
            client,
            "/api/slack/commands/incident",
            text=f"resolve {str(payments.id)[:8]} rolled back",
        ))

        assert "Incident Resolved" in body
        after = await _by_title()
        assert after["payments"].status.value == "resolved"
        assert after["unrelated"].status.value != "resolved"

    async def test_prose_after_the_subcommand_still_means_the_current_incident(self, client):
        """Refusing unknown ids must not make `resolve <notes>` refuse too."""
        _command(client, "/api/slack/commands/incident", text="create payments")

        body = _text_of(_command(
            client, "/api/slack/commands/incident", text="resolve restarted the pods"
        ))

        assert "Incident Resolved" in body
        assert (await _by_title())["payments"].status.value == "resolved"

    async def test_warroom_create_refuses_an_unknown_id(self, client):
        await _two_incidents(client)

        body = _text_of(_command(
            client, "/api/slack/commands/warroom", text=f"create {UNKNOWN}"
        ))

        assert "War Room Created" not in body
        assert "no incident" in body

    async def test_postmortem_refuses_an_unknown_id(self, client):
        await _two_incidents(client)

        body = _text_of(_command(
            client, "/api/slack/commands/postmortem", text=f"generate {UNKNOWN}"
        ))

        assert "Generating the postmortem" not in body
        assert "no incident" in body


# --------------------------------------------------------------------------- #
# Buttons and modals: confirm only what the store recorded
# --------------------------------------------------------------------------- #
class TestIncidentButtons:
    async def test_the_acknowledge_button_acknowledges_the_incident(self, client):
        _command(client, "/api/slack/commands/incident", text="create payments")
        incident = (await _incidents())[0]

        body = _text_of(_click(client, f"ack_{incident.id}"))

        assert "Acknowledged" in body
        after = (await _incidents())[0]
        assert after.acknowledged_at is not None
        assert after.acknowledged_by == "responder"
        assert after.status.value == "investigating"

    async def test_the_acknowledge_button_refuses_an_unknown_incident(self, client):
        _command(client, "/api/slack/commands/incident", text="create payments")

        body = _text_of(_click(client, f"acknowledge_{UNKNOWN}"))

        assert "Acknowledged" not in body
        assert "no incident" in body
        assert (await _incidents())[0].acknowledged_at is None

    async def test_a_handoff_acknowledgement_is_not_an_incident_acknowledgement(self, client):
        """`ack_handoff_<user>` collides with the `ack_` prefix and used to be
        answered with "Incident Acknowledged / Incident: handoff_U9"."""
        _command(client, "/api/slack/commands/incident", text="create payments")

        body = _text_of(_click(client, "ack_handoff_U9"))

        assert "Incident Acknowledged" not in body
        assert "handoff" in body
        assert "not recorded" in body
        assert (await _incidents())[0].acknowledged_at is None

    async def test_the_resolve_modal_resolves_the_incident(self, client):
        _command(client, "/api/slack/commands/incident", text="create payments")
        incident = (await _incidents())[0]

        result = _submit(
            client,
            f"resolve_submit_{incident.id}",
            {
                **_select("resolution_type", "type_select", "fixed"),
                "resolution_summary": {"summary_input": {"value": "restarted the pods"}},
            },
        )

        assert result == {"response_action": "clear"}
        after = (await _incidents())[0]
        assert after.status.value == "resolved"
        assert after.resolved_at is not None
        assert any("restarted the pods" in e.message for e in after.timeline)

    async def test_the_resolve_modal_refuses_an_unknown_incident(self, client):
        _command(client, "/api/slack/commands/incident", text="create payments")

        result = _submit(
            client,
            f"resolve_submit_{UNKNOWN}",
            {
                **_select("resolution_type", "type_select", "fixed"),
                "resolution_summary": {"summary_input": {"value": "done"}},
            },
        )

        assert result["response_action"] == "errors"
        assert UNKNOWN in result["errors"]["resolution_summary"]
        assert (await _incidents())[0].status.value != "resolved"

    async def test_the_escalate_modal_raises_the_severity(self, client):
        _command(client, "/api/slack/commands/incident", text="create payments")
        incident = (await _incidents())[0]
        assert incident.severity.value == "high"

        result = _submit(
            client,
            f"escalate_submit_{incident.id}",
            {
                **_select("escalation_level", "level_select", "L3"),
                **_select("escalation_team", "team_select", "platform"),
                "escalation_reason": {"reason_input": {"value": "customer impact"}},
            },
        )

        assert result == {"response_action": "clear"}
        after = (await _incidents())[0]
        assert after.severity.value == "critical"
        assert any("L3" in e.message for e in after.timeline)

    async def test_the_snooze_modal_records_the_request_without_claiming_silence(self, client):
        """Nothing in this build reads a snooze, so the pager keeps firing."""
        _command(client, "/api/slack/commands/incident", text="create payments")
        incident = (await _incidents())[0]

        result = _submit(
            client,
            f"snooze_submit_{incident.id}",
            _select("snooze_duration", "duration_select", "60"),
        )

        assert result == {"response_action": "clear"}
        after = (await _incidents())[0]
        assert any(
            "Snooze requested" in e.message and "60" in e.message
            for e in after.timeline
        ), "the snooze was not recorded anywhere"

    async def test_the_status_update_modal_records_the_update(self, client):
        _command(client, "/api/slack/commands/incident", text="create payments")
        incident = (await _incidents())[0]

        result = _submit(
            client,
            f"status_update_{incident.id}",
            {
                **_select("update_type", "type_select", "mitigation"),
                "update_text": {"text_input": {"value": "failover completed"}},
            },
        )

        assert result == {"response_action": "clear"}
        after = (await _incidents())[0]
        assert any("failover completed" in e.message for e in after.timeline)

    async def test_the_status_update_modal_refuses_an_unknown_incident(self, client):
        _command(client, "/api/slack/commands/incident", text="create payments")

        result = _submit(
            client,
            f"status_update_{UNKNOWN}",
            {
                **_select("update_type", "type_select", "progress"),
                "update_text": {"text_input": {"value": "still looking"}},
            },
        )

        assert result["response_action"] == "errors"
        assert not (await _incidents())[0].timeline[1:], "a note was written anyway"


# --------------------------------------------------------------------------- #
# The on-call page's Acknowledge button
# --------------------------------------------------------------------------- #
class TestOncallAlertButton:
    async def _alert(self, title: str = "disk full"):
        from app.database.engine import get_session
        from app.database.orgs import ensure_org_row, org_uuid
        from app.oncall.models import OncallAlert

        alert_id = uuid.uuid4()
        async with get_session() as session:
            await ensure_org_row(session)
            session.add(OncallAlert(
                id=alert_id, org_id=org_uuid(), title=title,
                severity="critical", status="triggered",
            ))
            await session.commit()
        return alert_id

    async def _status(self, alert_id):
        from sqlalchemy import select

        from app.database.engine import get_session
        from app.oncall.models import OncallAlert

        async with get_session() as session:
            row = (await session.execute(
                select(OncallAlert).where(OncallAlert.id == alert_id)
            )).scalar_one()
            return row.status

    async def test_the_page_button_acknowledges_the_alert(self, client):
        """`ack_alert_<id>` is minted by the escalation service and used to land
        in the incident branch, which answered "Incident Acknowledged" while the
        alert stayed triggered and the escalation kept paging."""
        alert_id = await self._alert()

        body = _text_of(_click(client, f"ack_alert_{alert_id}"))

        assert "Alert acknowledged" in body
        assert "Incident Acknowledged" not in body
        assert await self._status(alert_id) == "acknowledged"

    async def test_an_unknown_alert_is_refused(self, client):
        body = _text_of(_click(client, f"ack_alert_{UNKNOWN}"))

        assert "no alert" in body
        assert "acknowledged" not in body.lower().replace("nothing was acknowledged", "")
