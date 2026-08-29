"""What was still true after the pager "started delivering": it reached nobody.

The lexicographic-priority fix made the dispatcher stop discarding CRITICAL and
HIGH notifications, and the routing fix made `EscalationService` supply the
recipient's address instead of asking a process dictionary with no writers. On a
real deployment neither was enough to wake a human up, for two reasons that these
tests pin:

1. **The address store has no writers.** `_page_routing` reads `members.email`,
   which is the column the on-call foreign keys point at — and nothing in `app/`
   inserts a `members` row (`/admin/members` writes the separate aiosqlite
   tenancy store). So the default state of every deployment is "no address", and
   the recorded reason has to say that rather than "No email configured", which
   reads like an unset preference.

2. **The default channels could not deliver at all.** An escalation step created
   without channels defaulted to ["push", "sms", "phone"]: push needs a device
   registration this build has no endpoint for, and sms/phone need a responder
   phone number no table in this schema has a column for. The out-of-the-box
   policy was guaranteed to page nobody.

3. **A page that failed was called a duplicate.** The dispatcher opened its
   five-minute dedupe window before trying a single channel, so the retry of a
   page that had just failed — no provider configured, provider refused, no
   address — was answered with an empty result list and recorded as
   "deduplicated". A recoverable failure became a five-minute hole with a
   reassuring reason on it.

What is guaranteed now: a step that reaches nobody says so at ERROR with the
per-channel reason, and the alert goes out over the channels the org configured
on its Notification Preferences page — the one store in this build that both
holds a deliverable destination and is written by a real request. A step that
does reach somebody does not raise that alarm, and only a page a channel
accepted starts the dedupe window.
"""

from __future__ import annotations

import logging
import uuid

import pytest

import app.config as cfg
from app.database import engine as db_engine
from app.notifications import dispatcher as disp
from app.oncall.escalation_service import EscalationService
from app.oncall.models import (
    DEFAULT_ESCALATION_CHANNELS,
    OncallAlert,
    OncallEscalationPolicy,
    OncallEscalationStep,
    OncallNotification,
    OncallTeam,
)

USER = uuid.UUID("44444444-4444-4444-4444-444444444444")


@pytest.fixture(autouse=True)
def _clean_dispatcher_state():
    disp._USER_PREFERENCES.clear()
    disp._SENT_NOTIFICATIONS.clear()
    yield
    disp._USER_PREFERENCES.clear()
    disp._SENT_NOTIFICATIONS.clear()


@pytest.fixture
async def db(tmp_path, monkeypatch):
    monkeypatch.setenv("COPILOT_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/remainder.db")
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    cfg.get_settings.cache_clear()
    db_engine._engine = None
    db_engine._session_factory = None
    await db_engine.init_db()
    yield
    await db_engine.close_db()
    cfg.get_settings.cache_clear()
    db_engine._engine = None
    db_engine._session_factory = None


@pytest.fixture
def org_pages(monkeypatch):
    """Capture what goes to the org's own configured channels."""
    from app.notifications import delivery

    sent: list[dict] = []

    async def fake_notify(event, title, body, **kwargs):
        sent.append({"event": event, "title": title, "body": body, **kwargs})
        return {"event": event, "status": "sent", "results": []}

    monkeypatch.setattr(delivery, "notify", fake_notify)
    return sent


async def _fixture_policy(session, *, channels: list[str], targets: list[dict], member=False):
    from app.database.models import Member
    from app.database.orgs import ensure_org_row, org_uuid

    await ensure_org_row(session)
    team = OncallTeam(org_id=org_uuid(), name="SRE", slug=f"sre-{uuid.uuid4().hex[:8]}")
    session.add(team)
    if member:
        session.add(Member(id=USER, org_id=org_uuid(), email="oncall@example.com"))
    await session.flush()

    policy = OncallEscalationPolicy(team_id=team.id, name="p")
    session.add(policy)
    await session.flush()
    session.add(OncallEscalationStep(
        policy_id=policy.id, step_number=0, delay_minutes=0,
        targets=targets, notification_channels=channels,
    ))
    alert = OncallAlert(
        org_id=org_uuid(), team_id=team.id, title="disk full", severity="critical",
        escalation_policy_id=policy.id, current_escalation_step=0,
    )
    session.add(alert)
    await session.commit()
    await session.refresh(policy, ["steps"])
    return policy, alert


# --------------------------------------------------------------------------- #
# 1. The default channels are ones this build can address
# --------------------------------------------------------------------------- #
class TestDefaultChannels:
    def test_no_default_channel_is_one_this_build_cannot_address(self):
        """push/sms/phone were all three of the old defaults, and all three are
        unaddressable here: no device registry, no responder phone column."""
        from app.oncall.escalation_service import _MISSING_ROUTING

        unaddressable = {"push", "sms", "phone", "voice", "whatsapp", "slack"}
        assert unaddressable.issubset(set(_MISSING_ROUTING) | {"phone"})
        assert not set(DEFAULT_ESCALATION_CHANNELS) & unaddressable
        assert DEFAULT_ESCALATION_CHANNELS

    def test_the_column_default_and_the_service_default_are_the_same_list(self):
        """They were two literals that had already drifted from what delivers."""
        column_default = OncallEscalationStep.__table__.c.notification_channels.default
        assert column_default.arg(None) == list(DEFAULT_ESCALATION_CHANNELS)

    def test_each_step_gets_its_own_channel_list(self):
        """A literal default is one list shared by every row the mapper builds."""
        a = OncallEscalationStep(policy_id=uuid.uuid4(), step_number=0, targets=[])
        b = OncallEscalationStep(policy_id=uuid.uuid4(), step_number=1, targets=[])
        column_default = OncallEscalationStep.__table__.c.notification_channels.default
        a.notification_channels = column_default.arg(None)
        b.notification_channels = column_default.arg(None)
        a.notification_channels.append("sms")
        assert b.notification_channels == list(DEFAULT_ESCALATION_CHANNELS)

    async def test_a_policy_created_without_channels_uses_the_deliverable_default(self, db):
        from app.database.engine import get_session
        from app.database.orgs import ensure_org_row, org_uuid

        async with get_session() as session:
            await ensure_org_row(session)
            team = OncallTeam(org_id=org_uuid(), name="SRE", slug=f"sre-{uuid.uuid4().hex[:8]}")
            session.add(team)
            await session.flush()

            policy = await EscalationService(session).create_escalation_policy(
                team_id=team.id,
                name="default",
                steps=[{"targets": [{"target_type": "user", "target_id": str(USER)}]}],
            )
            await session.refresh(policy, ["steps"])

            assert policy.steps[0].notification_channels == list(DEFAULT_ESCALATION_CHANNELS)


# --------------------------------------------------------------------------- #
# 2. An unaddressable page names the store that is empty
# --------------------------------------------------------------------------- #
class TestTheReasonIsTheRealOne:
    async def test_the_email_failure_names_the_members_table(self, db):
        """"No email configured" describes a preference; the truth is a table
        with no application writer."""
        from app.database.engine import get_session

        async with get_session() as session:
            _, alert = await _fixture_policy(
                session, channels=["email"],
                targets=[{"target_type": "user", "target_id": str(USER)}],
            )
            route = await EscalationService(session)._page_route_for(USER, "email")

        assert route.destination == ""
        assert "members" in route.missing
        assert "no email address on file" in route.missing

    async def test_a_phone_page_says_the_build_has_nowhere_to_keep_a_number(self, db):
        from app.database.engine import get_session

        async with get_session() as session:
            service = EscalationService(session)
            route = await service._page_route_for(USER, "phone")

        # "phone" is the product's word for the dispatcher's VOICE channel.
        assert route.channel is disp.NotificationChannel.VOICE
        assert "nowhere to store one" in route.missing

    async def test_the_row_records_the_address_the_page_went_to(self, db, monkeypatch):
        """`contact_value` exists for this and was never written, so "we sent an
        email" was unverifiable after the fact."""
        from app.database.engine import get_session
        from sqlalchemy import select

        async def fake_email(*, to, subject, body_text, **kwargs):
            return True

        monkeypatch.setattr(disp, "send_email", fake_email)

        async with get_session() as session:
            _, alert = await _fixture_policy(
                session, channels=["email"], member=True,
                targets=[{"target_type": "user", "target_id": str(USER)}],
            )
            await EscalationService(session)._send_alert_notification(
                alert=alert, user_id=USER, channel="email", escalation_step=0
            )
            row = (await session.execute(select(OncallNotification))).scalars().one()

        assert row.status == "sent"
        assert row.contact_value == "oncall@example.com"


# --------------------------------------------------------------------------- #
# 3. A step that reaches nobody is loud, and the alert still goes somewhere
# --------------------------------------------------------------------------- #
class TestNobodyWasReached:
    async def test_an_unpaged_step_falls_back_to_the_orgs_own_channels(
        self, db, org_pages, caplog
    ):
        """The default deployment: no `members` row, so no page can be addressed.
        The alert must not evaporate — it goes to the channels the org configured
        on its Notification Preferences page."""
        from app.database.engine import get_session

        async with get_session() as session:
            policy, alert = await _fixture_policy(
                session, channels=list(DEFAULT_ESCALATION_CHANNELS),
                targets=[{"target_type": "user", "target_id": str(USER)}],
            )
            with caplog.at_level(logging.ERROR, logger="omnipotent.oncall.escalation"):
                await EscalationService(session)._execute_escalation_step(alert, policy, 0)

        assert [p["event"] for p in org_pages] == ["escalation"]
        # Critical, or quiet hours and digest batching would swallow the one
        # notification saying the pager did not work.
        assert org_pages[0]["critical"] is True
        assert "NOT PAGED" in org_pages[0]["title"]
        assert "members" in org_pages[0]["body"]
        assert any("reached nobody" in r.getMessage() for r in caplog.records)

    async def test_a_step_that_pages_somebody_raises_no_alarm(
        self, db, org_pages, monkeypatch
    ):
        """The fallback must not fire on a healthy page, or it becomes noise
        nobody reads and the real one hides in it."""
        from app.database.engine import get_session

        async def fake_email(*, to, subject, body_text, **kwargs):
            return True

        monkeypatch.setattr(disp, "send_email", fake_email)

        async with get_session() as session:
            policy, alert = await _fixture_policy(
                session, channels=["email"], member=True,
                targets=[{"target_type": "user", "target_id": str(USER)}],
            )
            await EscalationService(session)._execute_escalation_step(alert, policy, 0)

        assert org_pages == []

    async def test_targets_that_resolve_to_nobody_are_reported(self, db, org_pages):
        """A schedule with nobody on it produced no page, no row and no log line
        — the alert just sat there looking triggered."""
        from app.database.engine import get_session

        async with get_session() as session:
            policy, alert = await _fixture_policy(
                session, channels=["email"],
                targets=[{"target_type": "schedule", "target_id": str(uuid.uuid4())}],
            )
            await EscalationService(session)._execute_escalation_step(alert, policy, 0)

        assert len(org_pages) == 1
        assert "resolved to a person" in org_pages[0]["body"]

    async def test_no_policy_and_nobody_on_call_is_reported(self, db, org_pages):
        """`_notify_current_oncall` returned silently when the rotation was
        empty, which is precisely when somebody needs to be told."""
        from app.database.engine import get_session
        from app.database.orgs import ensure_org_row, org_uuid

        async with get_session() as session:
            await ensure_org_row(session)
            team = OncallTeam(org_id=org_uuid(), name="SRE", slug=f"sre-{uuid.uuid4().hex[:8]}")
            session.add(team)
            await session.flush()
            alert = OncallAlert(
                org_id=org_uuid(), team_id=team.id, title="disk full", severity="critical",
            )
            session.add(alert)
            await session.commit()

            await EscalationService(session).start_escalation(alert)

        assert len(org_pages) == 1
        assert "nobody is currently on call" in org_pages[0]["body"]

    async def test_the_fallback_never_takes_the_escalation_down_with_it(
        self, db, monkeypatch, caplog
    ):
        """An escalation that has already happened must not be rolled back
        because the org's webhook is down."""
        from app.database.engine import get_session
        from app.notifications import delivery

        async def boom(*args, **kwargs):
            raise RuntimeError("notification backend down")

        monkeypatch.setattr(delivery, "notify", boom)

        async with get_session() as session:
            policy, alert = await _fixture_policy(
                session, channels=["email"],
                targets=[{"target_type": "user", "target_id": str(USER)}],
            )
            with caplog.at_level(logging.ERROR, logger="omnipotent.oncall.escalation"):
                await EscalationService(session)._execute_escalation_step(alert, policy, 0)

        assert any("could not be reached" in r.getMessage() for r in caplog.records)

    async def test_a_webhook_target_that_is_rejected_does_not_count_as_reached(
        self, db, monkeypatch, org_pages
    ):
        """The POST's status code used to be discarded, so a 500 from the
        customer's endpoint was logged as "Sent"."""
        from app.database.engine import get_session
        from app.oncall import escalation_service as svc

        class _Response:
            status = 500

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

        class _Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            def post(self, *args, **kwargs):
                return _Response()

        monkeypatch.setattr(svc.aiohttp, "ClientSession", lambda *a, **k: _Session())

        async with get_session() as session:
            policy, alert = await _fixture_policy(
                session, channels=[],
                targets=[{"target_type": "webhook", "url": "https://example.invalid/hook"}],
            )
            service = EscalationService(session)
            assert await service._send_webhook("https://example.invalid/hook", alert) is False
            await service._execute_escalation_step(alert, policy, 0)

        assert len(org_pages) == 1
        assert "did not accept the alert" in org_pages[0]["body"]


# --------------------------------------------------------------------------- #
# 4. Only a page that went out starts the dedupe window
# --------------------------------------------------------------------------- #
class TestDedupeFollowsDelivery:
    async def test_a_page_that_failed_is_retried_not_called_a_duplicate(
        self, db, monkeypatch
    ):
        """The sweeper comes back every 30 seconds and the window is 5 minutes,
        so marking the key before the send turned one refused page into five
        minutes of "deduplicated" — a duplicate of something never delivered."""
        from app.database.engine import get_session

        attempts: list[str] = []

        async def refuses(*, to, subject, body_text, **kwargs):
            attempts.append(to)
            return False

        monkeypatch.setattr(disp, "send_email", refuses)

        async with get_session() as session:
            _, alert = await _fixture_policy(
                session, channels=["email"], member=True,
                targets=[{"target_type": "user", "target_id": str(USER)}],
            )
            service = EscalationService(session)
            first = await service._deliver(alert, USER, "email")
            second = await service._deliver(alert, USER, "email")

        assert attempts == ["oncall@example.com", "oncall@example.com"]
        assert first[0] is False and second[0] is False
        assert "deduplicated" not in second[1]
        # The reason stays the real one on the retry, not the window's.
        assert first[1] == second[1]

    async def test_a_page_that_was_delivered_is_deduplicated(self, db, monkeypatch):
        """The window still does its job: one page per alert, channel and step."""
        from app.database.engine import get_session

        attempts: list[str] = []

        async def accepts(*, to, subject, body_text, **kwargs):
            attempts.append(to)
            return True

        monkeypatch.setattr(disp, "send_email", accepts)

        async with get_session() as session:
            _, alert = await _fixture_policy(
                session, channels=["email"], member=True,
                targets=[{"target_type": "user", "target_id": str(USER)}],
            )
            service = EscalationService(session)
            assert await service._deliver(alert, USER, "email") == (True, None)
            delivered, error = await service._deliver(alert, USER, "email")

        assert attempts == ["oncall@example.com"]
        assert delivered is False
        assert "deduplicated" in error and "delivered" in error

    async def test_an_unaddressable_page_does_not_consume_the_window(self):
        """Straight at the dispatcher: no channel accepted it, so nothing is
        remembered and the next attempt is a real attempt."""
        disp._SENT_NOTIFICATIONS.clear()
        prefs = disp.NotificationPreferences(
            user_id="u", enabled_channels={disp.NotificationChannel.EMAIL}
        )

        results = await disp.notify(
            user_id="u", notification_type="oncall_alert", title="disk full",
            body="page", priority=disp.NotificationPriority.CRITICAL,
            channels={disp.NotificationChannel.EMAIL},
            dedupe_key="alert:1:email:0", preferences=prefs,
        )

        assert [r.success for r in results] == [False]
        assert "No email address on file" in results[0].error
        assert disp._SENT_NOTIFICATIONS == {}


# --------------------------------------------------------------------------- #
# 5. The dispatcher stops answering "nothing happened" three different ways
# --------------------------------------------------------------------------- #
class TestDispatcherSaysWhy:
    async def test_a_channel_the_recipient_has_off_is_reported_not_dropped(self):
        """An empty result list meant deduplicated, suppressed or channel-off,
        and the caller could not tell which."""
        results = await disp.notify(
            user_id="u",
            notification_type="oncall_alert",
            title="disk full",
            body="page",
            priority=disp.NotificationPriority.CRITICAL,
            channels={disp.NotificationChannel.SMS},
            preferences=disp.NotificationPreferences(
                user_id="u",
                phone="+15550000000",
                enabled_channels={disp.NotificationChannel.EMAIL},
            ),
        )

        assert [r.success for r in results] == [False]
        assert "not one of this recipient's enabled channels" in results[0].error

    async def test_a_suppressed_notification_names_the_rule_that_suppressed_it(self):
        dispatcher = disp.get_dispatcher()
        note = disp.Notification(
            id="n", type="alert", title="t", body="b",
            priority=disp.NotificationPriority.LOW,
        )
        prefs = disp.NotificationPreferences(
            user_id="u", min_priority=disp.NotificationPriority.HIGH
        )

        reason = dispatcher._suppression_reason(note, prefs)

        assert "below the recipient's floor" in reason
        # The boolean the rest of the code (and the priority tests) uses is the
        # same answer, not a second implementation of it.
        assert dispatcher._should_send(note, prefs) is False
        assert dispatcher._should_send(
            disp.Notification(id="n", type="alert", title="t", body="b",
                              priority=disp.NotificationPriority.CRITICAL),
            prefs,
        ) is True

    async def test_an_unsendable_email_says_the_provider_is_not_configured(
        self, monkeypatch
    ):
        """`send_email` answers False for both "no provider" and "provider said
        no", and a result with no error reads as the generic "delivery failed"."""
        async def refuses(*, to, subject, body_text, **kwargs):
            return False

        monkeypatch.setattr(disp, "send_email", refuses)
        monkeypatch.delenv("SENDGRID_API_KEY", raising=False)
        monkeypatch.delenv("AWS_SES_ENABLED", raising=False)
        monkeypatch.delenv("SMTP_HOST", raising=False)
        monkeypatch.setattr("app.notifications.email._provider", None, raising=False)

        results = await disp.notify(
            user_id="u",
            notification_type="oncall_alert",
            title="disk full",
            body="page",
            priority=disp.NotificationPriority.CRITICAL,
            channels={disp.NotificationChannel.EMAIL},
            preferences=disp.NotificationPreferences(
                user_id="u", email="sre@example.com",
                enabled_channels={disp.NotificationChannel.EMAIL},
            ),
        )

        assert [r.success for r in results] == [False]
        assert "no email provider is configured" in results[0].error
