"""A page can reach a human on a deployment provisioned only through this API.

The lexicographic-priority fix stopped the dispatcher dropping every HIGH and
CRITICAL page, and the routing fix made `EscalationService` supply an address
instead of asking a process dictionary nothing wrote. Neither made the pager
work, because there was still no way to tell this product where a responder is:

* `_page_routing` read `members.email`, and nothing in `app/` wrote a `members`
  row. `/admin/members` and SCIM populate the separate aiosqlite tenancy store,
  whose ids are strings. So on a deployment set up entirely through this API,
  every targeted page resolved to no address at all.
* `POST /oncall/teams/{team_id}/members` demanded a `user_id: UUID` that no
  request in the product produced, so even a caller who knew about `members`
  could not obtain the id to put in it.
* `OncallTeamMember.contact_methods` — the one column that could hold a phone
  number or a Slack id — had no reader and no writer anywhere, so sms, voice and
  Slack steps were unaddressable by construction.
* The defaults could not deliver: `EscalationStep` defaulted to [PUSH, SMS], the
  policy templates hardcoded push/sms/phone, and the two enhanced-policy routes
  used ["push","sms"] and ["push","sms","phone"] literals. Push cannot deliver in
  this build at all, and sms/phone had no number to go to. Because
  `EscalationStep.to_dict()` writes `notification_channels` explicitly, the
  deliverable default further down never got a chance to apply.

What these pin: adding a responder by email records the row a page is addressed
from and hands back the id policies target; a phone number or Slack id given at
the same time is what an sms, voice or Slack page is addressed to; the defaults
and templates page on a channel that can be addressed from what this API can
record; and a responder nobody has recorded still fails loudly, naming the
request that would fix it rather than reporting a send that did not happen.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

import app.api.main as api
import app.config as cfg
from app import runtime, tenant_context
from app.database import engine as db_engine
from app.notifications import dispatcher as disp
from app.oncall.models import DEFAULT_ESCALATION_CHANNELS, OncallTeamMember
from app.tenancy import auth as tenant_auth


@pytest.fixture(autouse=True)
def _clean_dispatcher_state():
    disp._USER_PREFERENCES.clear()
    disp._SENT_NOTIFICATIONS.clear()
    yield
    disp._USER_PREFERENCES.clear()
    disp._SENT_NOTIFICATIONS.clear()


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


@pytest.fixture
def emails(monkeypatch):
    """Capture what the dispatcher's email channel was asked to send."""
    sent: list[dict] = []

    async def fake_email(*, to, subject, body_text, **kwargs):
        sent.append({"to": to, "subject": subject, "body": body_text})
        return True

    monkeypatch.setattr(disp, "send_email", fake_email)
    return sent


@pytest.fixture
def org_pages(monkeypatch):
    """Capture the "NOT PAGED" fallback to the org's own channels."""
    from app.notifications import delivery

    sent: list[dict] = []

    async def fake_notify(event, title, body, **kwargs):
        sent.append({"event": event, "title": title, "body": body, **kwargs})
        return {"event": event, "status": "sent", "results": []}

    monkeypatch.setattr(delivery, "notify", fake_notify)
    return sent


def _team(client) -> str:
    res = client.post(
        "/oncall/teams",
        json={"name": "Platform", "slug": f"platform-{uuid.uuid4().hex[:8]}"},
    )
    assert res.status_code == 200, res.text
    return res.json()["id"]


def _steps(client, policy_id: str) -> list[dict]:
    res = client.get(f"/oncall/escalation-policies/{policy_id}")
    assert res.status_code == 200, res.text
    return res.json()["steps"]


async def _route(user_id: str, channel: str):
    from app.database.engine import get_session
    from app.oncall.escalation_service import EscalationService

    async with get_session() as session:
        return await EscalationService(session)._page_route_for(uuid.UUID(user_id), channel)


# --------------------------------------------------------------------------- #
# 1. The API can record a responder at all
# --------------------------------------------------------------------------- #
class TestRecordingAResponder:
    def test_adding_a_responder_by_email_creates_the_row_a_page_reads(self, client):
        """`members` had no application writer, so this row never existed."""
        team = _team(client)

        res = client.post(
            f"/oncall/teams/{team}/members", json={"email": "ada@acme.test"}
        )

        assert res.status_code == 200, res.text
        body = res.json()
        assert body["email"] == "ada@acme.test"
        # The id is in the response because nothing else in the product produces
        # one, and every escalation target is named by it.
        assert uuid.UUID(body["user_id"])

    def test_the_ids_escalation_targets_are_named_by_can_be_read_back(self, client):
        team = _team(client)
        created = client.post(
            f"/oncall/teams/{team}/members",
            json={"email": "ada@acme.test", "phone": "+15550001111"},
        ).json()

        listed = client.get(f"/oncall/teams/{team}/members")

        assert listed.status_code == 200, listed.text
        assert [(m["user_id"], m["email"]) for m in listed.json()] == [
            (created["user_id"], "ada@acme.test")
        ]
        assert [m["type"] for m in listed.json()[0]["contact_methods"]] == [
            "sms", "phone", "email",
        ]

    def test_a_membership_naming_nobody_is_refused(self, client):
        """A membership with no responder behind it can never be paged, and
        used to be accepted — `user_id` was required, but any UUID satisfied it."""
        team = _team(client)

        res = client.post(f"/oncall/teams/{team}/members", json={})

        assert res.status_code == 400
        assert "email" in res.json()["detail"]

    def test_a_user_id_with_no_member_row_is_refused_not_silently_stored(self, client):
        team = _team(client)

        res = client.post(
            f"/oncall/teams/{team}/members", json={"user_id": str(uuid.uuid4())}
        )

        assert res.status_code == 404
        assert "email" in res.json()["detail"]

    def test_re_adding_a_responder_corrects_their_number_instead_of_500ing(self, client):
        """`uq_oncall_member` made the second post an IntegrityError, so a wrong
        phone number could never be fixed."""
        team = _team(client)
        first = client.post(
            f"/oncall/teams/{team}/members",
            json={"email": "ada@acme.test", "phone": "+15550001111"},
        )
        assert first.status_code == 200, first.text

        second = client.post(
            f"/oncall/teams/{team}/members",
            json={"email": "ada@acme.test", "phone": "+15550002222"},
        )

        assert second.status_code == 200, second.text
        assert second.json()["user_id"] == first.json()["user_id"]
        listed = client.get(f"/oncall/teams/{team}/members").json()
        assert len(listed) == 1
        assert [m.get("value") for m in listed[0]["contact_methods"]] == [
            "+15550002222", "+15550002222", None,
        ]

    def test_changing_a_role_does_not_erase_the_number_pages_go_to(self, client):
        """Making the update a full replacement would have traded one silent
        unreachability for another: a request that only says "Ada is the lead"
        would empty the `contact_methods` her sms pages are addressed from, and
        nothing would say so until a page failed."""
        team = _team(client)
        user = client.post(
            f"/oncall/teams/{team}/members",
            json={"email": "ada@acme.test", "phone": "+15550001111"},
        ).json()["user_id"]

        promoted = client.post(
            f"/oncall/teams/{team}/members", json={"user_id": user, "role": "lead"}
        )

        assert promoted.status_code == 200, promoted.text
        assert promoted.json()["role"] == "lead"
        assert [m.get("value") for m in promoted.json()["contact_methods"]] == [
            "+15550001111", "+15550001111", None,
        ]

    async def test_a_number_survives_an_unrelated_edit_all_the_way_to_the_page(
        self, client
    ):
        """The same thing read back through the code that addresses a page,
        rather than through the response that reports the write."""
        team = _team(client)
        user = client.post(
            f"/oncall/teams/{team}/members",
            json={"email": "ada@acme.test", "phone": "+15550001111"},
        ).json()["user_id"]

        client.post(
            f"/oncall/teams/{team}/members",
            json={"user_id": user, "skills": ["postgres"]},
        )

        assert (await _route(user, "sms")).destination == "+15550001111"

    def test_one_responder_however_the_email_was_capitalised(self, client):
        """Two rows for one person would hand out two ids and page whichever
        one the caller last quoted."""
        team = _team(client)
        first = client.post(f"/oncall/teams/{team}/members", json={"email": "Ada@acme.test"})
        second = client.post(f"/oncall/teams/{team}/members", json={"email": "ada@ACME.test"})

        assert second.json()["user_id"] == first.json()["user_id"]


# --------------------------------------------------------------------------- #
# 2. A page is addressed from what was recorded
# --------------------------------------------------------------------------- #
class TestThePageIsAddressed:
    async def test_an_email_page_goes_to_the_address_the_api_recorded(self, client):
        team = _team(client)
        user = client.post(
            f"/oncall/teams/{team}/members", json={"email": "ada@acme.test"}
        ).json()["user_id"]

        route = await _route(user, "email")

        assert route.destination == "ada@acme.test"
        assert route.missing == ""
        assert route.preferences.email == "ada@acme.test"

    async def test_a_recorded_number_addresses_sms_and_voice_alike(self, client):
        """`contact_methods` had no reader, so an sms step could not be
        addressed however the deployment was configured. One number has to
        answer for both channels or "phone" fails next to a stored "sms"."""
        team = _team(client)
        user = client.post(
            f"/oncall/teams/{team}/members",
            json={"email": "ada@acme.test", "phone": "+15550001111"},
        ).json()["user_id"]

        sms = await _route(user, "sms")
        voice = await _route(user, "phone")

        assert sms.channel is disp.NotificationChannel.SMS
        assert sms.destination == "+15550001111"
        assert sms.preferences.phone == "+15550001111"
        assert sms.missing == ""
        assert voice.channel is disp.NotificationChannel.VOICE
        assert voice.preferences.phone == "+15550001111"

    async def test_a_recorded_slack_id_addresses_a_slack_page(self, client):
        team = _team(client)
        user = client.post(
            f"/oncall/teams/{team}/members",
            json={"email": "ada@acme.test", "slack_user_id": "U0ADA"},
        ).json()["user_id"]

        route = await _route(user, "slack")

        assert route.destination == "U0ADA"
        assert route.preferences.slack_user_id == "U0ADA"
        assert route.missing == ""

    async def test_a_number_recorded_on_one_team_pages_from_any_of_them(self, client):
        """A phone number belongs to the person, not to a team; a page must not
        fail because it was entered against their other rotation."""
        first, second = _team(client), _team(client)
        user = client.post(
            f"/oncall/teams/{first}/members",
            json={"email": "ada@acme.test", "phone": "+15550001111"},
        ).json()["user_id"]
        client.post(f"/oncall/teams/{second}/members", json={"user_id": user})

        assert (await _route(user, "sms")).destination == "+15550001111"

    async def test_a_responder_with_no_number_is_told_where_to_put_one(self, client):
        """The honesty must survive the fix: an unaddressable page still names
        the store that is empty, and now the request that fills it."""
        team = _team(client)
        user = client.post(
            f"/oncall/teams/{team}/members", json={"email": "ada@acme.test"}
        ).json()["user_id"]

        route = await _route(user, "sms")

        assert route.destination == ""
        assert "no phone number on file" in route.missing
        assert "contact_methods" in route.missing
        assert "/oncall/teams/{team_id}/members" in route.missing

    async def test_a_push_page_still_says_nothing_will_make_it_work(self, client):
        """Unlike sms, there is no request that fixes push: no endpoint in this
        build lets a browser register a device."""
        team = _team(client)
        user = client.post(
            f"/oncall/teams/{team}/members", json={"email": "ada@acme.test"}
        ).json()["user_id"]

        route = await _route(user, "push")

        assert "no registered device" in route.missing
        assert "no endpoint" in route.missing

    async def test_an_unknown_responder_names_members_as_the_empty_store(self, client):
        _team(client)

        route = await _route(str(uuid.uuid4()), "email")

        assert route.destination == ""
        assert "no email address on file" in route.missing
        assert "members" in route.missing


# --------------------------------------------------------------------------- #
# 3. The defaults and templates page on something addressable
# --------------------------------------------------------------------------- #
class TestDefaultsCanDeliver:
    #: Channels no request to this product can supply an address for.
    UNADDRESSABLE = {"push"}

    def test_an_enhanced_policy_without_channels_gets_the_deliverable_default(
        self, client
    ):
        """This route substituted ["push","sms"], and `to_dict()` wrote it out
        explicitly, so the column's deliverable default never applied."""
        team = _team(client)
        user = client.post(
            f"/oncall/teams/{team}/members", json={"email": "ada@acme.test"}
        ).json()["user_id"]

        created = client.post(
            "/oncall/escalation-policies/enhanced",
            json={
                "team_id": team,
                "name": "no channels named",
                "steps": [{"targets": [{"type": "user", "id": user}]}],
            },
        )

        assert created.status_code == 200, created.text
        steps = _steps(client, created.json()["id"])
        assert steps
        for step in steps:
            assert step["notification_channels"] == list(DEFAULT_ESCALATION_CHANNELS)

    def test_the_builders_own_default_is_the_same_one(self):
        """`escalations.py` restated the default as [PUSH, SMS] in three places,
        which is how it drifted from what delivers in the first place."""
        from app.oncall.escalations import EscalationStep, NotificationChannel

        step = EscalationStep(step_number=0, delay_minutes=0, targets=[])

        assert step.notification_channels == [
            NotificationChannel(c) for c in DEFAULT_ESCALATION_CHANNELS
        ]

    def test_an_after_hours_path_that_cannot_be_stored_is_refused(self, client):
        """It was accepted, dropped, and reported back as stored: the response
        said `has_high_urgency_path: true` for steps this schema has nowhere to
        keep, and `time_rules_count: 0` for rules the caller had just sent. A
        path that silently never fires is discovered at 3am."""
        team = _team(client)

        res = client.post(
            "/oncall/escalation-policies/enhanced",
            json={
                "team_id": team,
                "name": "after hours",
                "steps": [{"targets": []}],
                "high_urgency_steps": [{"targets": [], "channels": ["phone"]}],
                "time_rules": [{"name": "after hours"}],
            },
        )

        assert res.status_code == 400
        assert "high_urgency_steps" in res.json()["detail"]
        assert "time_rules" in res.json()["detail"]

    def test_the_step_count_is_the_number_of_rows_written(self, client):
        """It was counted off the in-memory builder, which held steps that were
        never persisted."""
        team = _team(client)

        created = client.post(
            "/oncall/escalation-policies/enhanced",
            json={
                "team_id": team,
                "name": "two steps",
                "steps": [{"targets": []}, {"targets": [], "delay_minutes": 5}],
            },
        ).json()

        assert created["steps_count"] == len(_steps(client, created["id"])) == 2

    def test_an_unknown_channel_name_is_a_400_naming_the_real_ones(self, client):
        """It was an unhandled ValueError out of the enum: a typo in one channel
        of one step answered 500 without saying which word was wrong."""
        team = _team(client)

        res = client.post(
            "/oncall/escalation-policies/enhanced",
            json={
                "team_id": team,
                "name": "typo",
                "steps": [{"targets": [], "channels": ["e-mail"]}],
            },
        )

        assert res.status_code == 400
        assert "e-mail" in res.json()["detail"]
        assert "email" in res.json()["detail"]

    @pytest.mark.parametrize("template", ["simple", "critical", "business_hours"])
    def test_every_template_step_pages_on_a_channel_that_can_be_addressed(
        self, client, template
    ):
        """The critical template opened with push/sms/phone and the business
        hours one with slack/push — on the day a template is created nobody has
        entered a number, so every one of those steps paged nobody."""
        team = _team(client)
        schedule = client.post(
            "/oncall/schedules", json={"team_id": team, "name": "Primary"}
        ).json()["id"]

        created = client.post(
            "/oncall/escalation-policies/from-template",
            params={
                "template_type": template,
                "team_id": team,
                "schedule_id": schedule,
                "slack_channel_id": "C0DEADBEEF",
            },
        )

        assert created.status_code == 200, created.text
        steps = _steps(client, created.json()["id"])
        assert steps
        for step in steps:
            channels = set(step["notification_channels"])
            assert not channels & self.UNADDRESSABLE, step
            # A step aimed at a person has to include the channel that works
            # with nothing entered; a step aimed at a Slack channel or a webhook
            # is addressed by deployment configuration instead.
            targets = {t.get("target_type") for t in step["targets"]}
            if targets <= {"slack_channel", "webhook"}:
                continue
            assert channels & set(DEFAULT_ESCALATION_CHANNELS), step


# --------------------------------------------------------------------------- #
# 4. End to end: the page lands
# --------------------------------------------------------------------------- #
class TestAPageReachesAHuman:
    def test_an_alert_pages_the_responder_added_through_the_api(
        self, client, emails, org_pages
    ):
        """The whole point. Every step of this is a request a deployment can
        make; before, the third one had no id to name and the page had no
        address to go to."""
        team = _team(client)
        user = client.post(
            f"/oncall/teams/{team}/members", json={"email": "ada@acme.test"}
        ).json()["user_id"]
        policy = client.post(
            "/oncall/escalation-policies",
            json={
                "team_id": team,
                "name": "page ada",
                "is_default": True,
                "steps": [{"delay_minutes": 0, "targets": [{"type": "user", "user_id": user}]}],
            },
        )
        assert policy.status_code == 200, policy.text

        alert = client.post(
            "/oncall/alerts",
            json={"team_id": team, "title": "disk full", "severity": "critical"},
        )

        assert alert.status_code == 200, alert.text
        assert [e["to"] for e in emails] == ["ada@acme.test"]
        assert "disk full" in emails[0]["subject"]
        # The "we paged nobody" fallback is for when the page failed; firing it
        # on a page that landed would make it noise nobody reads.
        assert [p for p in org_pages if "NOT PAGED" in p["title"]] == []

    def test_the_row_records_the_address_the_page_went_to(self, client, emails):
        """`contact_value` is what makes "we paged Ada" checkable afterwards."""
        from sqlalchemy import select

        from app.database.engine import get_session
        from app.oncall.models import OncallNotification

        team = _team(client)
        user = client.post(
            f"/oncall/teams/{team}/members", json={"email": "ada@acme.test"}
        ).json()["user_id"]
        client.post(
            "/oncall/escalation-policies",
            json={
                "team_id": team,
                "name": "page ada",
                "is_default": True,
                "steps": [{"delay_minutes": 0, "targets": [{"type": "user", "user_id": user}]}],
            },
        )
        client.post(
            "/oncall/alerts",
            json={"team_id": team, "title": "disk full", "severity": "critical"},
        )

        async def _rows():
            async with get_session() as session:
                return (
                    await session.execute(select(OncallNotification))
                ).scalars().all()

        rows = client.portal.call(_rows)  # type: ignore[attr-defined]
        assert [(r.status, r.contact_value) for r in rows] == [("sent", "ada@acme.test")]

    def test_a_responder_nobody_recorded_still_fails_loudly(self, client, org_pages):
        """The pre-existing honesty must not be traded away for the fix: a page
        with no address says so and the alert reaches the org's own channels."""
        team = _team(client)
        client.post(
            "/oncall/escalation-policies",
            json={
                "team_id": team,
                "name": "page a ghost",
                "is_default": True,
                "steps": [
                    {
                        "delay_minutes": 0,
                        "targets": [{"type": "user", "user_id": str(uuid.uuid4())}],
                    }
                ],
            },
        )

        client.post(
            "/oncall/alerts",
            json={"team_id": team, "title": "disk full", "severity": "critical"},
        )

        unpaged = [p for p in org_pages if "NOT PAGED" in p["title"]]
        assert len(unpaged) == 1
        assert "members" in unpaged[0]["body"]
        assert unpaged[0]["critical"] is True


# --------------------------------------------------------------------------- #
# 5. The stored default is one that can deliver
# --------------------------------------------------------------------------- #
def test_the_contact_methods_default_lists_only_reachable_methods():
    """It was push, then sms, then phone, then email — the first three carrying
    no address and push undeliverable in this build at all, so the stated order
    began with three methods guaranteed to fail."""
    default = OncallTeamMember.__table__.c.contact_methods.default.arg(None)

    assert [m["type"] for m in default] == list(DEFAULT_ESCALATION_CHANNELS)


def test_each_member_gets_their_own_contact_methods_list():
    """A list literal as a column default is one list shared by every row the
    mapper builds: editing one member's methods would edit everybody's."""
    column_default = OncallTeamMember.__table__.c.contact_methods.default

    first = column_default.arg(None)
    second = column_default.arg(None)
    first.append({"type": "sms", "value": "+15550001111"})

    assert len(second) == 1
