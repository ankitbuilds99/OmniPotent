"""The two reasons an on-call page could never reach a human.

1. **Severity ordering was alphabetical.** `NotificationDispatcher._should_send`
   compared `priority.value` against `min_priority.value` — the enum's *strings* —
   and "critical" and "high" both sort before "low". With the default floor of LOW
   every CRITICAL and HIGH notification was dropped before a channel was tried,
   while MEDIUM and LOW went out. A SEV1 page was the one page guaranteed not to
   be sent.

2. **The pager asked a dictionary nobody writes.** `EscalationService._deliver`
   let the dispatcher resolve the responder from `_USER_PREFERENCES`, a
   process-local dict with no writers, so every responder came back as the same
   anonymous default — no address, no phone number, EMAIL and PUSH only. The
   channel the escalation policy chose was filtered out unattempted and the page
   was recorded as "suppressed by the recipient's notification preferences", a
   reason that described nothing anyone had configured.

Both defects are silent: they produce an empty result list, not an error, so
every assertion here is about a page that either goes out or explains itself.
"""

from __future__ import annotations

import uuid

import pytest

import app.config as cfg
from app.database import engine as db_engine
from app.notifications import dispatcher as disp
from app.oncall.escalation_service import EscalationService
from app.oncall.models import OncallAlert, OncallTeam

USER = uuid.UUID("33333333-3333-3333-3333-333333333333")


@pytest.fixture(autouse=True)
def _clean_dispatcher_state():
    """The dispatcher keeps preferences and dedupe marks in module globals."""
    disp._USER_PREFERENCES.clear()
    disp._SENT_NOTIFICATIONS.clear()
    yield
    disp._USER_PREFERENCES.clear()
    disp._SENT_NOTIFICATIONS.clear()


# --------------------------------------------------------------------------- #
# 1. The priority floor is a rank, not a string compare
# --------------------------------------------------------------------------- #
class TestPriorityFloor:
    @pytest.mark.parametrize(
        "priority",
        [
            disp.NotificationPriority.LOW,
            disp.NotificationPriority.MEDIUM,
            disp.NotificationPriority.HIGH,
            disp.NotificationPriority.CRITICAL,
        ],
        ids=lambda p: p.value,
    )
    def test_every_priority_clears_the_default_floor(self, priority):
        """HIGH and CRITICAL are the two that used to fail this."""
        prefs = disp.NotificationPreferences(user_id="u")
        assert prefs.min_priority is disp.NotificationPriority.LOW

        notification = disp.Notification(
            id="n", type="alert", title="t", body="b", priority=priority
        )

        assert disp.get_dispatcher()._should_send(notification, prefs) is True

    def test_a_raised_floor_still_suppresses_what_is_below_it(self):
        """The fix must not turn the filter off, only order it correctly."""
        prefs = disp.NotificationPreferences(
            user_id="u", min_priority=disp.NotificationPriority.HIGH
        )
        dispatcher = disp.get_dispatcher()

        def sends(priority):
            return dispatcher._should_send(
                disp.Notification(
                    id="n", type="alert", title="t", body="b", priority=priority
                ),
                prefs,
            )

        assert sends(disp.NotificationPriority.LOW) is False
        assert sends(disp.NotificationPriority.MEDIUM) is False
        assert sends(disp.NotificationPriority.HIGH) is True
        assert sends(disp.NotificationPriority.CRITICAL) is True

    async def test_a_critical_notification_reaches_a_channel(self, monkeypatch):
        """End to end through the public `notify`, not just the private filter."""
        sent: list[str] = []

        async def fake_email(*, to, subject, body_text, **kwargs):
            sent.append(to)
            return True

        monkeypatch.setattr(disp, "send_email", fake_email)

        results = await disp.notify(
            user_id="u",
            notification_type="oncall_alert",
            title="disk full",
            body="page",
            priority=disp.NotificationPriority.CRITICAL,
            channels={disp.NotificationChannel.EMAIL},
            preferences=disp.NotificationPreferences(
                user_id="u",
                email="sre@example.com",
                enabled_channels={disp.NotificationChannel.EMAIL},
            ),
        )

        assert sent == ["sre@example.com"]
        assert [r.success for r in results] == [True]


# --------------------------------------------------------------------------- #
# Database-backed fixtures — the real escalation path
# --------------------------------------------------------------------------- #
@pytest.fixture
async def db(tmp_path, monkeypatch):
    monkeypatch.setenv("COPILOT_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/paging.db")
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


async def _fixture_alert(session, *, with_member: bool = True):
    """A critical alert plus, optionally, the responder's member record."""
    from app.database.models import Member
    from app.database.orgs import ensure_org_row, org_uuid

    await ensure_org_row(session)
    team = OncallTeam(org_id=org_uuid(), name="SRE", slug=f"sre-{uuid.uuid4().hex[:8]}")
    session.add(team)
    if with_member:
        session.add(Member(id=USER, org_id=org_uuid(), email="oncall@example.com"))
    await session.flush()

    alert = OncallAlert(
        org_id=org_uuid(),
        team_id=team.id,
        title="disk full",
        severity="critical",
        current_escalation_step=0,
    )
    session.add(alert)
    await session.commit()
    return alert


class TestPagingUsesStoredRouting:
    async def test_a_page_goes_to_the_responders_stored_address(self, db, monkeypatch):
        """The persisted `members.email`, not the process dictionary's `None`."""
        from app.database.engine import get_session

        sent: list[str] = []

        async def fake_email(*, to, subject, body_text, **kwargs):
            sent.append(to)
            return True

        monkeypatch.setattr(disp, "send_email", fake_email)

        async with get_session() as session:
            alert = await _fixture_alert(session)
            service = EscalationService(session)

            delivered, error = await service._deliver(alert, USER, "email")

        assert (delivered, error) == (True, None)
        assert sent == ["oncall@example.com"]

    async def test_the_notification_row_records_the_page_as_sent(self, db, monkeypatch):
        """`_send_alert_notification` is the caller the escalation steps use."""
        from app.database.engine import get_session

        async def fake_email(*, to, subject, body_text, **kwargs):
            return True

        monkeypatch.setattr(disp, "send_email", fake_email)

        async with get_session() as session:
            alert = await _fixture_alert(session)
            service = EscalationService(session)

            note = await service._send_alert_notification(
                alert=alert, user_id=USER, channel="email", escalation_step=0
            )

            assert note.status == "sent"
            assert note.sent_at is not None
            assert note.error_message is None

    async def test_phone_is_the_channel_the_dispatcher_calls_voice(self, db):
        """The default escalation channel is named "phone", and it used to be
        rejected as unknown because the dispatcher's enum spells it VOICE."""
        from app.database.engine import get_session

        async with get_session() as session:
            alert = await _fixture_alert(session)
            service = EscalationService(session)

            delivered, error = await service._deliver(alert, USER, "phone")

        assert delivered is False
        assert "unknown notification channel" not in error
        # This responder is on no on-call team, so no `contact_methods` row
        # carries a number for them; the failure has to name the store that is
        # empty rather than a preference nobody set.
        assert "phone number" in error

    async def test_a_channel_the_policy_chose_is_attempted_not_filtered(self, db):
        """SMS is outside the hardcoded default channel set that used to win."""
        from app.database.engine import get_session

        async with get_session() as session:
            alert = await _fixture_alert(session)
            service = EscalationService(session)

            delivered, error = await service._deliver(alert, USER, "sms")

        assert delivered is False
        assert "suppressed by the recipient's notification preferences" not in error
        assert "phone number" in error

    async def test_a_missing_member_record_is_named_as_such(self, db, monkeypatch):
        """No address on file must not read as a delivery-provider failure.

        This used to be the *default* state of a real deployment rather than an
        edge case, because nothing in `app/` wrote the `members` table at all;
        `POST /oncall/teams/{team_id}/members` writes it now, so what this pins
        is the responder nobody ran that request for — a policy can still name a
        user id that no member row backs. The message has to name the empty store
        rather than say "No email configured", which reads like a setting
        somebody forgot to fill in.
        """
        from app.database.engine import get_session

        async def fake_email(*, to, subject, body_text, **kwargs):  # pragma: no cover
            raise AssertionError("nothing should have been sent")

        monkeypatch.setattr(disp, "send_email", fake_email)

        async with get_session() as session:
            alert = await _fixture_alert(session, with_member=False)
            service = EscalationService(session)

            delivered, error = await service._deliver(alert, USER, "email")

        assert delivered is False
        assert "members" in error
        assert "no email address on file" in error

    async def test_a_repeat_page_says_it_was_deduplicated(self, db, monkeypatch):
        """The empty-result branch now names the one cause that remains."""
        from app.database.engine import get_session

        async def fake_email(*, to, subject, body_text, **kwargs):
            return True

        monkeypatch.setattr(disp, "send_email", fake_email)

        async with get_session() as session:
            alert = await _fixture_alert(session)
            service = EscalationService(session)

            assert await service._deliver(alert, USER, "email") == (True, None)
            delivered, error = await service._deliver(alert, USER, "email")

        assert delivered is False
        assert "deduplicated" in error
