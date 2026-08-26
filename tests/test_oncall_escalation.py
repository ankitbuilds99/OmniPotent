"""Escalation reached nobody, and then never advanced past the step that reached nobody.

Two independent defects that compounded into "the on-call system does not page":

1. **Every policy created through the API raised `KeyError: 'type'`.** Escalation
   targets are plain dicts in a JSONB column, and three producers write three
   different key sets. `EscalationTarget.to_dict()` — which is what *both* policy
   routes and all three built-in templates persist — emits `target_type`/
   `target_id`, while `EscalationService` only ever read `type`/`user_id`. The
   first alert to escalate blew up before notifying anyone.

2. **`_schedule_next_escalation` was a log line.** Nothing in the codebase called
   `escalate_to_next_step`, so every alert stopped at step 0: a policy's second
   and third levels were unreachable however long it went unacknowledged, and
   `delay_minutes` was decorative. Worse, `_send_alert_notification` wrote
   `status = "sent"` without sending anything, so the notification log showed a
   full page trail for an alert no human ever saw.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

import app.config as cfg
from app.database import engine as db_engine
from app.oncall.escalation_service import (
    EscalationService,
    EscalationSweeper,
    _target_type,
    _target_uuid,
    _target_value,
)
from app.oncall.escalations import (
    EscalationPolicyBuilder,
    EscalationStep,
    EscalationTarget,
    NotificationChannel,
    TargetType,
)
from app.oncall.models import (
    OncallAlert,
    OncallEscalationPolicy,
    OncallEscalationStep,
    OncallNotification,
    OncallTeam,
)

USER = uuid.UUID("11111111-1111-1111-1111-111111111111")
OTHER = uuid.UUID("22222222-2222-2222-2222-222222222222")


# --------------------------------------------------------------------------- #
# 1. Target shapes — the KeyError
# --------------------------------------------------------------------------- #
class TestTargetKeys:
    """`_resolve_targets` has to read every shape that was ever persisted."""

    @pytest.mark.parametrize(
        "target",
        [
            {"type": "user", "user_id": str(USER)},          # this module's docstring
            {"target_type": "user", "target_id": str(USER)},  # what to_dict() writes
            {"type": "user", "id": str(USER)},                # the request body shape
        ],
        ids=["service-shape", "persisted-shape", "request-shape"],
    )
    def test_a_user_target_resolves_in_every_shape(self, target):
        assert _target_type(target) == "user"
        assert _target_uuid(target, "user_id", "target_id", "id") == USER

    def test_the_shape_the_routes_persist_is_the_one_that_used_to_crash(self):
        """Pin the actual regression: `target["type"]` on a to_dict() target."""
        persisted = EscalationTarget(
            target_type=TargetType.USER, target_id=str(USER)
        ).to_dict()

        with pytest.raises(KeyError):
            persisted["type"]          # what the service used to do
        assert _target_type(persisted) == "user"   # what it does now

    def test_target_type_is_case_and_space_tolerant(self):
        assert _target_type({"type": "  SCHEDULE "}) == "schedule"

    def test_a_target_with_no_type_at_all_is_not_a_crash(self):
        assert _target_type({}) == ""

    def test_value_lookup_prefers_the_most_specific_key(self):
        target = {"user_id": "specific", "target_id": "generic", "id": "vague"}
        assert _target_value(target, "user_id", "target_id", "id") == "specific"

    def test_value_lookup_falls_through_empties(self):
        assert _target_value({"url": "", "webhook_url": "https://x.test"},
                             "url", "webhook_url") == "https://x.test"

    def test_an_unparseable_id_is_skipped_not_raised(self):
        """One bad target must not take down the rest of the page."""
        assert _target_uuid({"user_id": "not-a-uuid"}, "user_id") is None
        assert _target_uuid({}, "user_id") is None


class TestTargetSerialisation:
    """`to_dict()` is what lands in JSONB, so anything it drops is lost."""

    def test_a_webhook_target_keeps_its_url(self):
        target = EscalationTarget(
            target_type=TargetType.WEBHOOK,
            target_id="https://hooks.test/x",
            webhook_url="https://hooks.test/x",
            webhook_headers={"X-Key": "v"},
        ).to_dict()

        assert target["webhook_url"] == "https://hooks.test/x"
        assert target["webhook_headers"] == {"X-Key": "v"}

    def test_a_slack_target_keeps_its_channel(self):
        target = EscalationTarget(
            target_type=TargetType.SLACK_CHANNEL,
            target_id="C123",
            slack_channel_id="C123",
        ).to_dict()

        assert target["slack_channel_id"] == "C123"

    def test_a_step_carries_its_skip_conditions(self):
        """`create_escalation_policy` reads `step_config["skip_if"]`; this key was
        never emitted, so "stop paging once acknowledged" never fired."""
        step = EscalationStep(
            step_number=0,
            delay_minutes=0,
            targets=[],
            skip_if_acknowledged=True,
            skip_if_resolved=False,
        ).to_dict()

        assert step["skip_if"] == {"acknowledged": True, "resolved": False}

    def test_a_built_template_survives_the_round_trip(self):
        """End to end: builder -> to_dict -> the keys the service reads."""
        policy = (
            EscalationPolicyBuilder("p", uuid.uuid4())
            .add_step(delay_minutes=0,
                      targets=[EscalationTarget(TargetType.USER, str(USER))],
                      channels=[NotificationChannel.PUSH])
            .add_slack_step("C999", delay_minutes=5)
            .build()
        )
        steps = [s.to_dict() for s in policy.steps]

        assert _target_type(steps[0]["targets"][0]) == "user"
        assert _target_uuid(steps[0]["targets"][0], "user_id", "target_id") == USER
        assert _target_type(steps[1]["targets"][0]) == "slack_channel"
        assert _target_value(steps[1]["targets"][0],
                             "channel_id", "slack_channel_id") == "C999"


# --------------------------------------------------------------------------- #
# Database-backed fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture
async def db(tmp_path, monkeypatch):
    monkeypatch.setenv("COPILOT_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/oncall.db")
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


async def _fixture_policy(session, *, steps: list[dict], repeat=False, limit=3):
    """A team + a policy with `steps`, persisted in the shape the routes write."""
    from app.database.orgs import ensure_org_row, org_uuid

    await ensure_org_row(session)
    team = OncallTeam(org_id=org_uuid(), name="SRE", slug=f"sre-{uuid.uuid4().hex[:8]}")
    session.add(team)
    await session.flush()

    policy = OncallEscalationPolicy(
        team_id=team.id, name="p", repeat_enabled=repeat, repeat_limit=limit
    )
    session.add(policy)
    await session.flush()

    for i, config in enumerate(steps):
        session.add(OncallEscalationStep(
            policy_id=policy.id,
            step_number=i,
            delay_minutes=config.get("delay_minutes", 0),
            targets=config["targets"],
            notification_channels=config.get("channels", ["push"]),
        ))

    alert = OncallAlert(
        org_id=org_uuid(), team_id=team.id, title="disk full", severity="critical",
        escalation_policy_id=policy.id, current_escalation_step=0,
    )
    session.add(alert)
    await session.commit()
    return team, policy, alert


# --------------------------------------------------------------------------- #
# 2. Escalation advances, and says truthfully whether it paged
# --------------------------------------------------------------------------- #
class TestNotificationHonesty:
    async def test_an_undeliverable_page_is_recorded_as_failed(self, db):
        """It used to say "sent" unconditionally — the worst possible lie for an
        on-call system, because every dashboard reads it as success."""
        from app.database.engine import get_session
        from sqlalchemy import select

        async with get_session() as session:
            _, _, alert = await _fixture_policy(
                session, steps=[{"targets": [{"target_type": "user",
                                              "target_id": str(USER)}]}]
            )
            service = EscalationService(session)

            note = await service._send_alert_notification(
                alert=alert, user_id=USER, channel="push", escalation_step=0
            )

            assert note.status == "failed"
            assert note.sent_at is None
            assert note.error_message

            rows = (await session.execute(select(OncallNotification))).scalars().all()
            assert [r.status for r in rows] == ["failed"]

    async def test_a_delivered_page_is_recorded_as_sent(self, db, monkeypatch):
        from app.database.engine import get_session
        from app.notifications import dispatcher as disp

        async def ok(**kwargs):
            return [disp.DeliveryResult(notification_id="n",
                                        channel=disp.NotificationChannel.PUSH,
                                        success=True)]

        monkeypatch.setattr("app.notifications.dispatcher.notify", ok)

        async with get_session() as session:
            _, _, alert = await _fixture_policy(
                session, steps=[{"targets": [{"target_type": "user",
                                              "target_id": str(USER)}]}]
            )
            service = EscalationService(session)

            note = await service._send_alert_notification(
                alert=alert, user_id=USER, channel="push", escalation_step=0
            )

            assert note.status == "sent"
            assert note.sent_at is not None

    async def test_an_unknown_channel_is_a_failure_not_an_exception(self, db):
        from app.database.engine import get_session

        async with get_session() as session:
            _, _, alert = await _fixture_policy(
                session, steps=[{"targets": [{"target_type": "user",
                                              "target_id": str(USER)}]}]
            )
            service = EscalationService(session)

            delivered, error = await service._deliver(alert, USER, "carrier-pigeon")

            assert delivered is False
            assert "carrier-pigeon" in error


class TestStepAdvancement:
    async def test_starting_an_escalation_arms_the_next_step(self, db):
        """`next_escalation_at` is what gives `escalate_to_next_step` a caller."""
        from app.database.engine import get_session

        async with get_session() as session:
            _, policy, alert = await _fixture_policy(session, steps=[
                {"targets": [{"target_type": "user", "target_id": str(USER)}]},
                {"delay_minutes": 5,
                 "targets": [{"target_type": "user", "target_id": str(OTHER)}]},
            ])
            await session.refresh(policy, ["steps"])
            service = EscalationService(session)

            await service.start_escalation(alert, policy)

            assert alert.next_escalation_at is not None
            # ~5 minutes out, per the *next* step's delay.
            delta = alert.next_escalation_at - datetime.now(timezone.utc)
            assert timedelta(minutes=4) < delta <= timedelta(minutes=5)

    async def test_a_zero_delay_next_step_is_armed_immediately(self, db):
        """`delay_minutes == 0` used to mean "never schedule it" rather than
        "run it now", so a 0-delay follow-up step never ran."""
        from app.database.engine import get_session

        async with get_session() as session:
            _, policy, alert = await _fixture_policy(session, steps=[
                {"targets": [{"target_type": "user", "target_id": str(USER)}]},
                {"delay_minutes": 0,
                 "targets": [{"target_type": "user", "target_id": str(OTHER)}]},
            ])
            await session.refresh(policy, ["steps"])

            await EscalationService(session).start_escalation(alert, policy)

            assert alert.next_escalation_at is not None
            assert alert.next_escalation_at <= datetime.now(timezone.utc) + timedelta(seconds=1)

    async def test_the_last_step_of_a_non_repeating_policy_disarms(self, db):
        from app.database.engine import get_session

        async with get_session() as session:
            _, policy, alert = await _fixture_policy(session, steps=[
                {"targets": [{"target_type": "user", "target_id": str(USER)}]},
            ])
            await session.refresh(policy, ["steps"])

            await EscalationService(session).start_escalation(alert, policy)

            assert alert.next_escalation_at is None

    async def test_escalating_advances_the_step(self, db):
        from app.database.engine import get_session

        async with get_session() as session:
            _, policy, alert = await _fixture_policy(session, steps=[
                {"targets": [{"target_type": "user", "target_id": str(USER)}]},
                {"targets": [{"target_type": "user", "target_id": str(OTHER)}]},
            ])
            alert_id = alert.id
            service = EscalationService(session)

            assert await service.escalate_to_next_step(alert_id) is True

            await session.refresh(alert)
            assert alert.current_escalation_step == 1

    async def test_an_acknowledged_alert_stops_escalating_and_disarms(self, db):
        """Otherwise the sweeper re-reads the same row on every tick, forever."""
        from app.database.engine import get_session

        async with get_session() as session:
            _, policy, alert = await _fixture_policy(session, steps=[
                {"targets": [{"target_type": "user", "target_id": str(USER)}]},
                {"targets": [{"target_type": "user", "target_id": str(OTHER)}]},
            ])
            alert.status = "acknowledged"
            alert.next_escalation_at = datetime.now(timezone.utc)
            await session.commit()

            assert await EscalationService(session).escalate_to_next_step(alert.id) is False

            await session.refresh(alert)
            assert alert.next_escalation_at is None

    async def test_the_repeat_limit_is_actually_reachable(self, db):
        """`current_escalation_step` used to be written back wrapped, so a
        2-step policy went 0->1->0->1->... and `next_step // len(steps)` never
        grew past 1. The page repeated forever, ignoring repeat_limit."""
        from app.database.engine import get_session

        async with get_session() as session:
            _, policy, alert = await _fixture_policy(
                session,
                steps=[
                    {"targets": [{"target_type": "user", "target_id": str(USER)}]},
                    {"targets": [{"target_type": "user", "target_id": str(OTHER)}]},
                ],
                repeat=True, limit=2,
            )
            service = EscalationService(session)

            # 2 steps x 2 repeats = 4 executions, i.e. 3 advances after step 0.
            advances = 0
            for _ in range(20):
                if not await service.escalate_to_next_step(alert.id):
                    break
                advances += 1

            assert advances == 3, "repeat_limit was not enforced"
            await session.refresh(alert)
            assert alert.next_escalation_at is None


# --------------------------------------------------------------------------- #
# 3. The sweeper — the caller that did not exist
# --------------------------------------------------------------------------- #
class TestSweeper:
    async def test_a_due_alert_is_escalated(self, db):
        from app.database.engine import _get_session_factory, get_session

        async with get_session() as session:
            _, policy, alert = await _fixture_policy(session, steps=[
                {"targets": [{"target_type": "user", "target_id": str(USER)}]},
                {"targets": [{"target_type": "user", "target_id": str(OTHER)}]},
            ])
            alert.next_escalation_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            await session.commit()
            alert_id = alert.id

        escalated = await EscalationSweeper(_get_session_factory()).tick()

        assert escalated == [alert_id]

    async def test_an_alert_that_is_not_due_yet_is_left_alone(self, db):
        from app.database.engine import _get_session_factory, get_session

        async with get_session() as session:
            _, policy, alert = await _fixture_policy(session, steps=[
                {"targets": [{"target_type": "user", "target_id": str(USER)}]},
                {"targets": [{"target_type": "user", "target_id": str(OTHER)}]},
            ])
            alert.next_escalation_at = datetime.now(timezone.utc) + timedelta(minutes=10)
            await session.commit()

        assert await EscalationSweeper(_get_session_factory()).tick() == []

    async def test_an_unarmed_alert_is_never_swept(self, db):
        from app.database.engine import _get_session_factory, get_session

        async with get_session() as session:
            await _fixture_policy(session, steps=[
                {"targets": [{"target_type": "user", "target_id": str(USER)}]},
            ])

        assert await EscalationSweeper(_get_session_factory()).tick() == []

    async def test_an_acknowledged_alert_is_not_swept(self, db):
        from app.database.engine import _get_session_factory, get_session

        async with get_session() as session:
            _, policy, alert = await _fixture_policy(session, steps=[
                {"targets": [{"target_type": "user", "target_id": str(USER)}]},
                {"targets": [{"target_type": "user", "target_id": str(OTHER)}]},
            ])
            alert.status = "acknowledged"
            alert.next_escalation_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            await session.commit()

        assert await EscalationSweeper(_get_session_factory()).tick() == []

    async def test_one_broken_alert_does_not_stop_the_sweep(self, db, monkeypatch):
        from app.database.engine import _get_session_factory, get_session

        async with get_session() as session:
            _, policy, first = await _fixture_policy(session, steps=[
                {"targets": [{"target_type": "user", "target_id": str(USER)}]},
                {"targets": [{"target_type": "user", "target_id": str(OTHER)}]},
            ])
            first.next_escalation_at = datetime.now(timezone.utc) - timedelta(minutes=2)
            _, policy2, second = await _fixture_policy(session, steps=[
                {"targets": [{"target_type": "user", "target_id": str(USER)}]},
                {"targets": [{"target_type": "user", "target_id": str(OTHER)}]},
            ])
            second.next_escalation_at = datetime.now(timezone.utc) - timedelta(minutes=1)
            await session.commit()
            first_id, second_id = first.id, second.id

        real = EscalationService.escalate_to_next_step

        async def boom(self, alert_id):
            if alert_id == first_id:
                raise RuntimeError("this row is poison")
            return await real(self, alert_id)

        monkeypatch.setattr(EscalationService, "escalate_to_next_step", boom)

        escalated = await EscalationSweeper(_get_session_factory()).tick()

        assert escalated == [second_id]

    async def test_the_batch_size_is_honoured(self, db):
        from app.database.engine import _get_session_factory, get_session

        async with get_session() as session:
            for _ in range(3):
                _, _, alert = await _fixture_policy(session, steps=[
                    {"targets": [{"target_type": "user", "target_id": str(USER)}]},
                    {"targets": [{"target_type": "user", "target_id": str(OTHER)}]},
                ])
                alert.next_escalation_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            await session.commit()

        assert len(await EscalationSweeper(_get_session_factory(), batch=2).tick()) == 2

    def test_the_sweep_interval_has_a_floor(self):
        from app.database.engine import _get_session_factory

        assert EscalationSweeper(_get_session_factory, interval=0).interval == 5
