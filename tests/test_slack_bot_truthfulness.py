"""The Slack bot answered incident questions with invented data.

Three commands returned hardcoded fiction, and each is dangerous in a different
way at exactly the moment it gets used — during an incident, in a channel full
of responders who are acting on what it says:

  * `/oncall` listed "Alice Smith", "Bob Jones", "Charlie Brown" for three teams
    that need not exist. The responder pages a person who was never real while
    the actual on-call keeps sleeping.
  * `/status` reported "checkout" as permanently degraded at 2.3% errors and
    "api" as permanently healthy at 45ms, whatever was actually happening — so a
    real outage reads as green.
  * `@mention investigate` slept three seconds and posted a fixed root cause —
    "connection leak in payment-service v2.4.1" — at a hardcoded 87% confidence,
    with four invented pieces of evidence, a recommendation to roll that service
    back, and a green check reading "Verified".

Four more commands lied in the other direction — not about the world, but about
what they had just done:

  * `/incident create` minted an id from `timestamp % 100000` and stored nothing,
    so the incident it announced could never be looked up again. `update`,
    `resolve`, `escalate` and `severity` posted a confident line into the channel
    with no incident behind it — a channel reading "Incident Resolved" while the
    incident is open and still paging.
  * `/warroom create` announced a channel it had not created, then `/warroom
    list` failed to find it one line later.
  * `/postmortem generate` promised "You'll be notified when ready" and started
    nothing; `/postmortem actions` listed four invented action items under
    "Action Items from Recent Incidents".
  * `/stakeholder` said it posts "to stakeholder channels" and echoed into
    whichever channel you typed it in.

The rule these pin: answer from the real backend, or say you don't know; and
claim only what actually happened. Never invent. A command that admits it has no
data costs a responder five seconds; one that invents data costs them the
incident.
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

# Names and figures that only ever existed in the hardcoded dicts. If any of
# these ever appears in a response again, the fabrication is back.
INVENTED_PEOPLE = [
    "Alice Smith", "Bob Jones", "Charlie Brown",
    "Diana Prince", "Eve Wilson", "Frank Miller",
]
INVENTED_FIGURES = ["45ms", "230ms", "120ms", "12ms", "2.3%", "0.01%", "0.05%"]


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
        "response_url": "https://hooks.slack.com/x", "trigger_id": "t",
    }).encode()
    r = client.post(path, content=body, headers=_signed(body))
    assert r.status_code == 200, r.text
    return r.json()


def _text_of(payload: dict) -> str:
    """All rendered text in a Block Kit response, flattened."""
    return json.dumps(payload)


# --------------------------------------------------------------------------- #
# /oncall
# --------------------------------------------------------------------------- #
class TestOncallCommand:
    def test_it_does_not_invent_a_roster(self, client):
        body = _text_of(_command(client, "/api/slack/commands/oncall"))
        for name in INVENTED_PEOPLE:
            assert name not in body, f"{name} is fabricated"

    def test_no_teams_configured_says_so(self, client):
        """The honest answer to "who is on call" with no schedules is "nobody"."""
        body = _text_of(_command(client, "/api/slack/commands/oncall"))
        assert "No on-call teams are configured" in body

    def test_an_unknown_team_is_not_answered_with_a_different_one(self, client):
        body = _text_of(_command(client, "/api/slack/commands/oncall", text="platform"))
        assert "No on-call" in body
        for name in INVENTED_PEOPLE:
            assert name not in body

    async def test_a_real_team_is_reported_from_the_schedule(self, client):
        """With a real team in the database, the command reads it."""
        from app.database.engine import get_session
        from app.database.orgs import ensure_org_row, org_uuid
        from app.oncall.models import OncallTeam

        async with get_session() as session:
            await ensure_org_row(session)
            session.add(OncallTeam(
                org_id=org_uuid(), name="payments", slug=f"p-{uuid.uuid4().hex[:6]}"
            ))
            await session.commit()

        body = _text_of(_command(client, "/api/slack/commands/oncall"))

        assert "payments" in body
        # No shift generated, so there is genuinely nobody on call — and it says
        # that rather than filling the slot with a plausible name.
        assert "nobody on call" in body

    async def test_a_team_with_no_shift_is_not_given_a_responder(self, client):
        from app.database.engine import get_session
        from app.database.orgs import ensure_org_row, org_uuid
        from app.oncall.models import OncallTeam

        async with get_session() as session:
            await ensure_org_row(session)
            session.add(OncallTeam(
                org_id=org_uuid(), name="infra", slug=f"i-{uuid.uuid4().hex[:6]}"
            ))
            await session.commit()

        body = _text_of(_command(client, "/api/slack/commands/oncall", text="infra"))

        assert "infra" in body
        assert "Charlie Brown" not in body
        assert "Diana Prince" not in body


# --------------------------------------------------------------------------- #
# /status
# --------------------------------------------------------------------------- #
class TestStatusCommand:
    def test_it_does_not_invent_component_health(self, client):
        body = _text_of(_command(client, "/api/slack/commands/status"))
        for figure in INVENTED_FIGURES:
            assert figure not in body, f"{figure} is fabricated"

    def test_an_empty_registry_reports_nothing_configured(self, client, monkeypatch):
        """Nothing registered means nothing to say — not "everything is fine"."""
        from app.status_page import components as comp

        empty = comp.StatusRegistry()
        empty._components.clear()
        monkeypatch.setattr(comp, "get_status_registry", lambda: empty)

        body = _text_of(_command(client, "/api/slack/commands/status"))
        assert "No status components are configured" in body

    def test_it_does_not_claim_a_named_service_is_healthy(self, client):
        """`/status checkout` used to answer "degraded, 230ms, 2.3%" from thin air."""
        body = _text_of(_command(client, "/api/slack/commands/status", text="checkout"))
        assert "230ms" not in body
        assert "2.3%" not in body

    def test_a_components_real_status_is_what_gets_reported(self, client, monkeypatch):
        """The hardcoded dict called checkout degraded forever. Flip the real
        component to a major outage and the command has to follow it."""
        from app.status_page import components as comp

        registry = comp.StatusRegistry()
        monkeypatch.setattr(comp, "get_status_registry", lambda: registry)

        registry.update_status("checkout", comp.ComponentStatus.MAJOR_OUTAGE)
        body = _text_of(_command(client, "/api/slack/commands/status", text="checkout"))
        assert "Major Outage" in body

        registry.update_status("checkout", comp.ComponentStatus.OPERATIONAL)
        body = _text_of(_command(client, "/api/slack/commands/status", text="checkout"))
        assert "Operational" in body
        assert "Major Outage" not in body

    def test_an_unknown_component_is_not_answered_with_a_guess(self, client, monkeypatch):
        from app.status_page import components as comp

        registry = comp.StatusRegistry()
        monkeypatch.setattr(comp, "get_status_registry", lambda: registry)

        body = _text_of(_command(
            client, "/api/slack/commands/status", text="no-such-service"
        ))
        assert "No component called" in body


# --------------------------------------------------------------------------- #
# @mention investigate
# --------------------------------------------------------------------------- #
class TestInvestigation:
    def test_the_fabricating_renderer_is_gone(self):
        """It hardcoded "Verified" and a placeholder report URL alongside the
        invented verdict, so nothing should be able to call it again."""
        from app.slack_bot import router

        assert not hasattr(router, "build_rca_result_blocks")

    def test_no_invented_verdict_can_reach_a_channel(self):
        """Scan the strings the module can actually emit.

        Docstrings are excluded deliberately — several of them describe the
        fiction that was removed, and that history is worth keeping. What must
        not survive is a *literal* that some code path could still render into
        Slack.
        """
        import ast
        from pathlib import Path

        tree = ast.parse(Path("app/slack_bot/router.py").read_text())
        docstrings = {
            id(node.body[0].value)
            for node in ast.walk(tree)
            if isinstance(
                node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
            )
            and node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
        }
        emittable = " ".join(
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        )

        for lie in (
            "payment-service",       # the invented root cause and its follow-up
            "connection leak",
            "omnipotent.example.com",  # the placeholder report link
            "Alice Smith",           # the invented on-call roster
            "circuit breaker for",   # the invented postmortem action items
        ):
            assert lie not in emittable, f"{lie!r} can still be rendered into Slack"

    async def test_it_runs_the_real_investigation_and_renders_the_real_report(
        self, monkeypatch
    ):
        """The query reaches the shared investigation path, and what comes back
        is what gets posted — no fixed root cause, no fixed confidence."""
        from app.slack_bot import router

        asked: list[str] = []
        posted: list[tuple] = []

        class FakeResult:
            status = "completed"
            report = {"severity": "SEV2", "confidence": "medium",
                      "summary": "real finding", "root_cause": "real cause"}
            final_text = "real finding"
            approval_request = None

        class FakeSession:
            async def ask(self, query):
                asked.append(query)
                return FakeResult()

        async def fake_get_session(thread_id):
            return FakeSession()

        monkeypatch.setattr("app.api.main._get_session", fake_get_session)

        async def fake_update(channel, ts, text, blocks):
            posted.append((text, json.dumps(blocks)))

        async def fake_reaction(channel, ts, name):
            return None

        monkeypatch.setattr(router, "update_slack_message", fake_update)
        monkeypatch.setattr(router, "add_reaction", fake_reaction)

        await router.run_investigation("why is checkout failing", "C1", "1.0", "2.0")

        assert asked == ["why is checkout failing"]
        final_text, final_blocks = posted[-1]
        assert final_text == "Investigation complete"
        assert "real finding" in final_blocks
        assert "87" not in final_blocks
        assert "payment-service" not in final_blocks

    async def test_a_failed_investigation_says_so_instead_of_posting_a_verdict(
        self, monkeypatch
    ):
        from app.slack_bot import router

        posted: list[tuple] = []
        reactions: list[str] = []

        async def boom(thread_id):
            raise RuntimeError("the LLM is down")

        monkeypatch.setattr("app.api.main._get_session", boom)

        async def fake_update(channel, ts, text, blocks):
            posted.append((text, json.dumps(blocks)))

        async def fake_reaction(channel, ts, name):
            reactions.append(name)

        monkeypatch.setattr(router, "update_slack_message", fake_update)
        monkeypatch.setattr(router, "add_reaction", fake_reaction)

        await router.run_investigation("why", "C1", "1.0", "2.0")

        final_text, _ = posted[-1]
        assert "failed" in final_text.lower()
        assert "the LLM is down" in final_text
        assert reactions[-1] == "x"

    async def test_an_approval_gated_run_is_not_reported_as_complete(
        self, monkeypatch
    ):
        """A run paused for approval has produced no verdict yet."""
        from app.slack_bot import router

        posted: list[tuple] = []

        class Paused:
            status = "awaiting_approval"
            report = None
            final_text = ""
            approval_request = {"actions": [{"tool": "rollback", "write": True}]}

        class FakeSession:
            async def ask(self, query):
                return Paused()

        async def fake_get_session(thread_id):
            return FakeSession()

        monkeypatch.setattr("app.api.main._get_session", fake_get_session)

        async def fake_update(channel, ts, text, blocks):
            posted.append((text, json.dumps(blocks)))

        async def fake_reaction(channel, ts, name):
            return None

        monkeypatch.setattr(router, "update_slack_message", fake_update)
        monkeypatch.setattr(router, "add_reaction", fake_reaction)

        await router.run_investigation("why", "C1", "1.0", "2.0")

        final_text, final_blocks = posted[-1]
        assert final_text == "Approval needed"
        assert "rollback" in final_blocks


# --------------------------------------------------------------------------- #
# /incident — claims about what was recorded
# --------------------------------------------------------------------------- #
async def _incidents():
    from app.incidents.store import get_incident_store

    return await get_incident_store().list_incidents(limit=50)


class TestIncidentCommand:
    async def test_create_actually_persists_the_incident(self, client):
        """The old id was `timestamp % 100000` against an empty store, so the
        incident it announced could not be found by anything afterwards."""
        assert await _incidents() == []

        body = _text_of(_command(
            client, "/api/slack/commands/incident", text="create checkout is down"
        ))

        stored = await _incidents()
        assert len(stored) == 1
        assert stored[0].title == "checkout is down"
        # And the id in the message is the one you can look up.
        assert str(stored[0].id) in body

    async def test_create_does_not_mint_a_clock_derived_id(self, client):
        body = _text_of(_command(
            client, "/api/slack/commands/incident", text="create db failover"
        ))
        assert "INC-" not in body

    async def test_resolve_with_no_incident_does_not_claim_a_resolution(self, client):
        """This is the dangerous one: a channel told the incident is closed
        stops working it."""
        body = _text_of(_command(client, "/api/slack/commands/incident", text="resolve"))

        assert "Incident Resolved" not in body
        assert "no open incident to act on" in body

    async def test_resolve_actually_resolves(self, client):
        from app.incidents.models import IncidentStatus

        _command(client, "/api/slack/commands/incident", text="create checkout down")
        body = _text_of(_command(
            client, "/api/slack/commands/incident", text="resolve rolled back"
        ))

        assert "Incident Resolved" in body
        stored = await _incidents()
        assert stored[0].status == IncidentStatus.RESOLVED
        assert stored[0].resolved_at is not None
        # The resolution notes land on the timeline, not just in scrollback.
        assert any("rolled back" in e.message for e in stored[0].timeline)

    async def test_update_lands_on_the_timeline(self, client):
        _command(client, "/api/slack/commands/incident", text="create checkout down")

        _command(
            client,
            "/api/slack/commands/incident",
            text="update failover completed, watching error rate",
        )

        stored = await _incidents()
        assert any(
            "failover completed" in e.message for e in stored[0].timeline
        ), "the status update was dropped"

    async def test_update_with_no_incident_records_nothing_and_says_so(self, client):
        body = _text_of(_command(
            client, "/api/slack/commands/incident", text="update anything"
        ))
        assert "no open incident to act on" in body
        assert await _incidents() == []

    async def test_severity_changes_the_record(self, client):
        _command(client, "/api/slack/commands/incident", text="create checkout down")

        body = _text_of(_command(
            client, "/api/slack/commands/incident", text="severity P1"
        ))

        assert "critical" in body
        stored = await _incidents()
        assert stored[0].severity.value == "critical"

    async def test_an_unknown_severity_is_refused_rather_than_defaulted(self, client):
        """It used to fall back to "P2" and report that as the new severity."""
        _command(client, "/api/slack/commands/incident", text="create checkout down")

        body = _text_of(_command(
            client, "/api/slack/commands/incident", text="severity urgent"
        ))

        assert "Unknown severity" in body
        stored = await _incidents()
        assert stored[0].severity.value == "high"  # unchanged

    async def test_escalate_raises_severity_one_level(self, client):
        _command(client, "/api/slack/commands/incident", text="create checkout down")

        body = _text_of(_command(client, "/api/slack/commands/incident", text="escalate"))

        assert "Escalated" in body
        stored = await _incidents()
        assert stored[0].severity.value == "critical"

    async def test_escalating_at_the_top_does_not_claim_a_change(self, client):
        _command(client, "/api/slack/commands/incident", text="create checkout down")
        _command(client, "/api/slack/commands/incident", text="severity P1")

        body = _text_of(_command(client, "/api/slack/commands/incident", text="escalate"))

        assert "already at the highest severity" in body
        stored = await _incidents()
        assert stored[0].severity.value == "critical"

    async def test_an_explicit_id_targets_that_incident(self, client):
        _command(client, "/api/slack/commands/incident", text="create first")
        _command(client, "/api/slack/commands/incident", text="create second")

        by_title = {i.title: i for i in await _incidents()}
        first = by_title["first"]

        _command(
            client, "/api/slack/commands/incident", text=f"resolve {first.id} done"
        )

        after = {i.title: i for i in await _incidents()}
        assert after["first"].status.value == "resolved"
        assert after["second"].status.value != "resolved"


# --------------------------------------------------------------------------- #
# /warroom
# --------------------------------------------------------------------------- #
class TestWarRoomCommand:
    def test_it_does_not_announce_a_channel_it_could_not_create(self, client):
        """With no bot token there is no way to create a channel, so the old
        "*War Room Created*  *Channel:* #inc-..." was pure assertion."""
        body = _text_of(_command(client, "/api/slack/commands/warroom", text="create"))
        assert "War Room Created" not in body

    async def test_it_refuses_without_an_incident(self, client):
        body = _text_of(_command(client, "/api/slack/commands/warroom", text="create"))
        assert "needs an incident" in body

    async def test_it_reports_the_slack_failure_instead_of_a_channel(self, client):
        _command(client, "/api/slack/commands/incident", text="create checkout down")

        body = _text_of(_command(client, "/api/slack/commands/warroom", text="create"))

        assert "Could not create the war room channel" in body
        assert "SLACK_BOT_TOKEN is not configured" in body

    async def test_a_created_room_is_the_one_list_finds(self, client, monkeypatch):
        """The old pair contradicted each other: create announced a room, list
        never saw it."""
        from app.slack_bot import router

        async def fake_channel(name, invite=""):
            return "C0FFEE", ""

        monkeypatch.setattr(router, "_create_slack_channel", fake_channel)
        _command(client, "/api/slack/commands/incident", text="create checkout down")

        created = _text_of(_command(client, "/api/slack/commands/warroom", text="create"))
        assert "War Room Created" in created
        assert "C0FFEE" in created

        listed = _text_of(_command(client, "/api/slack/commands/warroom", text="list"))
        assert "C0FFEE" in listed
        assert "checkout down" in listed

    async def test_a_second_create_does_not_make_a_duplicate(self, client, monkeypatch):
        from app.slack_bot import router

        calls: list[str] = []

        async def fake_channel(name, invite=""):
            calls.append(name)
            return "C0FFEE", ""

        monkeypatch.setattr(router, "_create_slack_channel", fake_channel)
        _command(client, "/api/slack/commands/incident", text="create checkout down")

        _command(client, "/api/slack/commands/warroom", text="create")
        body = _text_of(_command(client, "/api/slack/commands/warroom", text="create"))

        assert "already the war room" in body
        assert len(calls) == 1


# --------------------------------------------------------------------------- #
# /postmortem
# --------------------------------------------------------------------------- #
class TestPostmortemCommand:
    def test_actions_does_not_list_invented_action_items(self, client):
        body = _text_of(_command(client, "/api/slack/commands/postmortem", text="actions"))

        assert "circuit breaker" not in body
        assert "connection pool monitoring" not in body
        assert "Update runbook for DB failover" not in body

    def test_with_no_incidents_it_says_there_is_nothing_to_write_about(self, client):
        body = _text_of(_command(client, "/api/slack/commands/postmortem", text="actions"))
        assert "no incidents on record" in body

    async def test_generate_actually_starts_the_generator(self, client, monkeypatch):
        """It used to say "You'll take a few moments" and start nothing."""
        from app.slack_bot import router

        started: list = []

        async def fake_run(incident, response_url, want_actions):
            started.append((incident.title, want_actions))

        monkeypatch.setattr(router, "_run_postmortem", fake_run)
        _command(client, "/api/slack/commands/incident", text="create checkout down")

        body = _text_of(_command(
            client, "/api/slack/commands/postmortem", text="generate"
        ))

        assert "Generating" in body
        assert started == [("checkout down", False)]

    async def test_actions_asks_the_generator_for_action_items(self, client, monkeypatch):
        from app.slack_bot import router

        started: list = []

        async def fake_run(incident, response_url, want_actions):
            started.append((incident.title, want_actions))

        monkeypatch.setattr(router, "_run_postmortem", fake_run)
        _command(client, "/api/slack/commands/incident", text="create checkout down")

        _command(client, "/api/slack/commands/postmortem", text="actions")

        assert started == [("checkout down", True)]

    async def test_a_missing_llm_is_reported_not_papered_over(self, monkeypatch):
        """The generator refuses without a model; that refusal has to reach the
        channel rather than becoming a canned document."""
        from app.postmortems import PostmortemUnavailable
        from app.slack_bot import router

        sent: list = []

        async def fake_send(response_url, blocks):
            sent.append(json.dumps(blocks))

        async def unavailable(incident, sections=None):
            raise PostmortemUnavailable("no API key for provider 'anthropic'", ["KEY"])

        monkeypatch.setattr(router, "_send_response", fake_send)
        monkeypatch.setattr("app.postmortems.generate_postmortem", unavailable)

        class FakeIncident:
            id = uuid.uuid4()
            title = "checkout down"

        await router._run_postmortem(FakeIncident(), "https://hooks.slack/x", False)

        assert "no API key" in sent[0]
        assert "Could not generate the postmortem" in sent[0]

    async def test_share_admits_it_is_not_wired_up(self, client):
        body = _text_of(_command(
            client, "/api/slack/commands/postmortem", text="share abc"
        ))
        assert "not wired up yet" in body
        assert "Choose destination channel" not in body


# --------------------------------------------------------------------------- #
# /stakeholder
# --------------------------------------------------------------------------- #
class TestStakeholderCommand:
    def test_it_does_not_claim_to_reach_stakeholder_channels(self, client):
        body = _text_of(_command(client, "/api/slack/commands/stakeholder"))
        assert "stakeholder channels" not in body
        assert "in this channel" in body

    async def test_the_update_is_recorded_on_the_incident(self, client):
        _command(client, "/api/slack/commands/incident", text="create checkout down")

        body = _text_of(_command(
            client,
            "/api/slack/commands/stakeholder",
            text="payments restored, monitoring",
        ))

        assert "payments restored" in body
        stored = await _incidents()
        assert any(
            "payments restored" in e.message for e in stored[0].timeline
        ), "the stakeholder update was not recorded"

    async def test_with_no_incident_it_still_posts_and_claims_nothing(self, client):
        body = _text_of(_command(
            client, "/api/slack/commands/stakeholder", text="all clear"
        ))
        assert "all clear" in body
        assert "recorded on incident" not in body
