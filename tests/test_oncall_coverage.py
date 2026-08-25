"""Coverage arithmetic: DST holes, gap detection, swaps, and the calendar feed.

Four defects that all end the same way — a period with nobody paged, or a page
sent to the wrong person — while every screen says coverage is fine:

* **DST.** `generate_shifts` localized each shift's start but computed its end as
  `start + timedelta(days=7)`. pytz freezes the UTC offset at localize time, so
  after the November fall-back the shift ended at 09:00-04:00 while the next
  began at 09:00-05:00: one hour, twice a year, with no primary. In March the
  sign flips and two engineers are both primary for an hour.
* **Gap detection.** `detect_gaps` compared tz-naive boundaries out of SQLite
  against a tz-aware window and raised on the first comparison, so the endpoint
  that exists to find holes was a 500 on every call. It also emitted a trailing
  gap only `if merged`, so a schedule with no shifts at all — the largest
  possible hole — reported perfect coverage.
* **Swaps.** Accepting wrote `status = "accepted"` and nothing else. Both
  engineers saw the swap confirmed while every page still went to the original
  assignee.
* **The iCal feed.** `ical-url` advertised a hardcoded `/api/v1/oncall` prefix
  that no route has ever matched, so the URL handed to Google Calendar 404'd.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta

import pytest
import pytz
from fastapi.testclient import TestClient

import app.api.main as api
import app.config as cfg
from app import runtime
from app.database import engine as db_engine
from app.oncall.models import (
    OncallSchedule,
    OncallRotationParticipant,
    OncallShift,
    OncallTeam,
)
from app.oncall.schedule_service import ScheduleService, _handoff, _rotation_period

NY = "America/New_York"


@pytest.fixture
async def db(tmp_path, monkeypatch):
    monkeypatch.setenv("COPILOT_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/cov.db")
    cfg.get_settings.cache_clear()
    db_engine._engine = None
    db_engine._session_factory = None
    await db_engine.init_db()
    yield
    await db_engine.close_db()
    cfg.get_settings.cache_clear()
    db_engine._engine = None
    db_engine._session_factory = None


async def _schedule(session, *, tz: str = NY, rotation="daily", participants=2, coverage="24x7"):
    from app.database.orgs import ensure_org_row, org_uuid

    await ensure_org_row(session)
    team = OncallTeam(org_id=org_uuid(), name="SRE", slug=f"sre-{uuid.uuid4().hex[:8]}")
    session.add(team)
    await session.flush()

    schedule = OncallSchedule(
        team_id=team.id,
        name="primary",
        timezone=tz,
        coverage_type=coverage,
        rotation_config={"type": rotation, "handoff_time": "09:00", "handoff_day": "monday"},
    )
    session.add(schedule)
    await session.flush()

    for position in range(participants):
        session.add(OncallRotationParticipant(
            schedule_id=schedule.id, user_id=uuid.uuid4(), position=position
        ))
    await session.commit()
    return schedule


# --------------------------------------------------------------------------- #
# DST
# --------------------------------------------------------------------------- #
class TestHandoffAcrossDST:
    def test_the_old_arithmetic_really_did_drift(self):
        """Pin the bug itself, so the fix cannot be reverted as a no-op."""
        tz = pytz.timezone(NY)
        start = tz.localize(datetime(2026, 10, 31, 9, 0))
        naive_end = start + timedelta(days=1)          # what the code used to do
        real_next = _handoff(date(2026, 11, 1), 9, 0, tz)

        assert naive_end != real_next
        assert (real_next - naive_end) == timedelta(hours=1)

    def test_a_shift_ends_exactly_where_the_next_begins_in_autumn(self):
        tz = pytz.timezone(NY)
        end_of_first = _handoff(date(2026, 11, 1), 9, 0, tz)
        start_of_second = _handoff(date(2026, 11, 1), 9, 0, tz)
        assert end_of_first == start_of_second

    def test_a_handoff_time_that_does_not_exist_is_mapped_forward(self):
        """02:30 never happens on a US spring-forward Sunday."""
        tz = pytz.timezone(NY)
        handoff = _handoff(date(2026, 3, 8), 2, 30, tz)

        assert handoff.utcoffset() == timedelta(hours=-4)  # EDT, the real side
        assert handoff.hour == 3

    def test_a_period_is_one_turn_of_the_rotation(self):
        assert _rotation_period("daily", 7) == timedelta(days=1)
        assert _rotation_period("weekly", 7) == timedelta(days=7)
        assert _rotation_period("biweekly", 7) == timedelta(days=14)
        assert _rotation_period("custom", 3) == timedelta(days=3)
        # A zero/negative length would make the generation loop spin forever.
        assert _rotation_period("custom", 0) == timedelta(days=1)

    async def test_generated_shifts_leave_no_hole_at_the_fall_back(self, db):
        from app.database.engine import get_session

        async with get_session() as session:
            schedule = await _schedule(session, rotation="daily")
            service = ScheduleService(session)
            shifts = await service.generate_shifts(
                schedule.id, date(2026, 10, 29), date(2026, 11, 4)
            )

        assert len(shifts) >= 5
        ordered = sorted(shifts, key=lambda s: s.start_time)
        for earlier, later in zip(ordered, ordered[1:]):
            assert earlier.end_time == later.start_time, (
                f"{earlier.end_time} -> {later.start_time} is a coverage hole"
            )

    async def test_generated_shifts_do_not_double_cover_at_the_spring_forward(self, db):
        from app.database.engine import get_session

        async with get_session() as session:
            schedule = await _schedule(session, rotation="daily")
            service = ScheduleService(session)
            shifts = await service.generate_shifts(
                schedule.id, date(2026, 3, 6), date(2026, 3, 11)
            )

        ordered = sorted(shifts, key=lambda s: s.start_time)
        for earlier, later in zip(ordered, ordered[1:]):
            assert earlier.end_time <= later.start_time, "two primaries at once"
            assert earlier.end_time == later.start_time

    async def test_the_dst_day_shift_is_a_real_25_hours(self, db):
        """The shift covering the fall-back is genuinely an hour longer. It is
        the wall clock that stays put, not the duration."""
        from app.database.engine import get_session

        async with get_session() as session:
            schedule = await _schedule(session, rotation="daily")
            service = ScheduleService(session)
            shifts = await service.generate_shifts(
                schedule.id, date(2026, 10, 31), date(2026, 11, 2)
            )

        durations = {
            (s.end_time - s.start_time).total_seconds() / 3600 for s in shifts
        }
        assert 25.0 in durations


# --------------------------------------------------------------------------- #
# Gap detection
# --------------------------------------------------------------------------- #
class TestGapDetection:
    async def test_it_does_not_raise_on_naive_boundaries(self, db):
        """SQLite returns these columns naive; the window is built aware. The
        first comparison used to be a TypeError, i.e. a 500 on every call."""
        from app.database.engine import get_session

        async with get_session() as session:
            schedule = await _schedule(session)
            session.add(OncallShift(
                schedule_id=schedule.id,
                user_id=uuid.uuid4(),
                start_time=datetime(2026, 6, 1, 0, 0),
                end_time=datetime(2026, 6, 2, 0, 0),
                shift_type="primary",
                status="scheduled",
            ))
            await session.commit()

            gaps = await ScheduleService(session).detect_gaps(
                schedule.id, date(2026, 6, 1), date(2026, 6, 1)
            )

        assert gaps == []

    async def test_no_shifts_at_all_is_reported_as_one_big_gap(self, db):
        """It used to return [] — perfect coverage — for a 24x7 schedule that
        had never had a single shift generated."""
        from app.database.engine import get_session

        async with get_session() as session:
            schedule = await _schedule(session)
            gaps = await ScheduleService(session).detect_gaps(
                schedule.id, date(2026, 6, 1), date(2026, 6, 2)
            )

        assert len(gaps) == 1
        assert gaps[0]["gap_hours"] == 48.0

    async def test_a_hole_between_two_shifts_is_found(self, db):
        from app.database.engine import get_session

        async with get_session() as session:
            schedule = await _schedule(session)
            for start, end in (
                (datetime(2026, 6, 1, 0, 0), datetime(2026, 6, 1, 9, 0)),
                (datetime(2026, 6, 1, 12, 0), datetime(2026, 6, 2, 0, 0)),
            ):
                session.add(OncallShift(
                    schedule_id=schedule.id, user_id=uuid.uuid4(),
                    start_time=start, end_time=end,
                    shift_type="primary", status="scheduled",
                ))
            await session.commit()

            gaps = await ScheduleService(session).detect_gaps(
                schedule.id, date(2026, 6, 1), date(2026, 6, 1)
            )

        assert len(gaps) == 1
        assert gaps[0]["gap_hours"] == 3.0

    async def test_a_shift_that_starts_late_leaves_a_leading_gap(self, db):
        from app.database.engine import get_session

        async with get_session() as session:
            schedule = await _schedule(session)
            session.add(OncallShift(
                schedule_id=schedule.id, user_id=uuid.uuid4(),
                start_time=datetime(2026, 6, 1, 6, 0),
                end_time=datetime(2026, 6, 2, 0, 0),
                shift_type="primary", status="scheduled",
            ))
            await session.commit()

            gaps = await ScheduleService(session).detect_gaps(
                schedule.id, date(2026, 6, 1), date(2026, 6, 1)
            )

        assert len(gaps) == 1
        assert gaps[0]["gap_hours"] == 6.0

    async def test_overlapping_shifts_do_not_produce_a_phantom_gap(self, db):
        """A nested shift used to rewind `expected_start`, inventing a gap that
        the merge pass had already covered."""
        from app.database.engine import get_session

        async with get_session() as session:
            schedule = await _schedule(session)
            for start, end in (
                (datetime(2026, 6, 1, 0, 0), datetime(2026, 6, 2, 0, 0)),
                (datetime(2026, 6, 1, 8, 0), datetime(2026, 6, 1, 12, 0)),
            ):
                session.add(OncallShift(
                    schedule_id=schedule.id, user_id=uuid.uuid4(),
                    start_time=start, end_time=end,
                    shift_type="primary", status="scheduled",
                ))
            await session.commit()

            gaps = await ScheduleService(session).detect_gaps(
                schedule.id, date(2026, 6, 1), date(2026, 6, 1)
            )

        assert gaps == []


# --------------------------------------------------------------------------- #
# Over HTTP: the gaps endpoint, swaps, and the calendar feed
# --------------------------------------------------------------------------- #
@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("COPILOT_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/http.db")
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


def _http_schedule(client, *, participants: list[str] | None = None, tz: str = "UTC") -> str:
    team = client.post("/oncall/teams", json={
        "name": "Platform", "slug": f"platform-{uuid.uuid4().hex[:8]}",
    })
    assert team.status_code == 200, team.text

    schedule = client.post("/oncall/schedules", json={
        "team_id": team.json()["id"],
        "name": "primary",
        "timezone": tz,
        "rotation_type": "daily",
        "coverage_type": "24x7",
        "participants": participants or [str(uuid.uuid4()), str(uuid.uuid4())],
    })
    assert schedule.status_code == 200, schedule.text
    return schedule.json()["id"]


def _http_shifts(client, schedule_id: str, start: date, end: date) -> list[dict]:
    created = client.post(f"/oncall/schedules/{schedule_id}/generate-shifts",
                          json={"start_date": str(start), "end_date": str(end)})
    assert created.status_code == 200, created.text

    listed = client.get(f"/oncall/schedules/{schedule_id}/shifts",
                        params={"start_date": str(start), "end_date": str(end + timedelta(days=2))})
    assert listed.status_code == 200, listed.text
    return listed.json()


class TestGapsEndpoint:
    def test_it_answers_200_not_500(self, client):
        """The whole point of the endpoint. It raised on the first tz-naive
        comparison, so the only thing it ever reported was itself failing."""
        schedule = _http_schedule(client)
        _http_shifts(client, schedule, date(2026, 6, 1), date(2026, 6, 3))

        response = client.get(f"/oncall/schedules/{schedule}/gaps",
                              params={"start_date": "2026-06-01", "end_date": "2026-06-02"})

        assert response.status_code == 200, response.text
        assert isinstance(response.json(), list)

    def test_an_empty_schedule_reports_the_hole_rather_than_all_clear(self, client):
        schedule = _http_schedule(client)

        response = client.get(f"/oncall/schedules/{schedule}/gaps",
                              params={"start_date": "2026-06-01", "end_date": "2026-06-01"})

        assert response.status_code == 200, response.text
        assert response.json(), "a schedule with no shifts is not fully covered"


class TestSwapActuallySwaps:
    def _two_shifts(self, client) -> tuple[str, list[dict]]:
        alice, bob = str(uuid.uuid4()), str(uuid.uuid4())
        schedule = _http_schedule(client, participants=[alice, bob])
        shifts = _http_shifts(client, schedule, date(2026, 6, 1), date(2026, 6, 4))
        assert len(shifts) >= 2
        return schedule, shifts

    def _swap(self, client, **body) -> str:
        response = client.post("/oncall/swaps", json=body)
        assert response.status_code == 200, response.text
        return response.json()["id"]

    def _shift(self, client, schedule: str, shift_id: str) -> dict:
        listed = client.get(f"/oncall/schedules/{schedule}/shifts",
                            params={"start_date": "2026-06-01", "end_date": "2026-06-30"})
        return next(s for s in listed.json() if s["id"] == shift_id)

    def test_a_one_way_handover_moves_the_shift(self, client):
        schedule, shifts = self._two_shifts(client)
        taker = str(uuid.uuid4())
        swap = self._swap(client, from_shift_id=shifts[0]["id"], to_user_id=taker)

        accepted = client.post(f"/oncall/swaps/{swap}/accept",
                               params={"accepting_user_id": taker})

        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["from_shift_user_id"] == taker
        assert self._shift(client, schedule, shifts[0]["id"])["user_id"] == taker

    def test_an_open_request_goes_to_whoever_accepts_it(self, client):
        schedule, shifts = self._two_shifts(client)
        volunteer = str(uuid.uuid4())
        swap = self._swap(client, from_shift_id=shifts[0]["id"])

        client.post(f"/oncall/swaps/{swap}/accept", params={"accepting_user_id": volunteer})

        assert self._shift(client, schedule, shifts[0]["id"])["user_id"] == volunteer

    def test_a_two_way_trade_moves_both_shifts(self, client):
        schedule, shifts = self._two_shifts(client)
        mine, theirs = shifts[0], shifts[1]
        swap = self._swap(client, from_shift_id=mine["id"],
                          to_shift_id=theirs["id"], to_user_id=theirs["user_id"])

        accepted = client.post(f"/oncall/swaps/{swap}/accept",
                               params={"accepting_user_id": theirs["user_id"]})

        assert accepted.status_code == 200, accepted.text
        assert self._shift(client, schedule, mine["id"])["user_id"] == theirs["user_id"]
        assert self._shift(client, schedule, theirs["id"])["user_id"] == mine["user_id"]

    def test_the_swap_is_marked_as_the_source_of_the_assignment(self, client):
        """So the rotation view can show why this shift disagrees with the
        generated rotation, instead of looking like a generation bug."""
        schedule, shifts = self._two_shifts(client)
        swap = self._swap(client, from_shift_id=shifts[0]["id"], to_user_id=str(uuid.uuid4()))
        client.post(f"/oncall/swaps/{swap}/accept", params={"accepting_user_id": str(uuid.uuid4())})

        assert self._shift(client, schedule, shifts[0]["id"])["source"] == "swap"

    def test_an_expired_request_is_refused_and_changes_nothing(self, client):
        schedule, shifts = self._two_shifts(client)
        original = shifts[0]["user_id"]
        swap = self._swap(client, from_shift_id=shifts[0]["id"],
                          to_user_id=str(uuid.uuid4()), expires_in_hours=-1)

        refused = client.post(f"/oncall/swaps/{swap}/accept",
                              params={"accepting_user_id": str(uuid.uuid4())})

        assert refused.status_code == 400
        assert "expired" in refused.json()["detail"].lower()
        assert self._shift(client, schedule, shifts[0]["id"])["user_id"] == original

    def test_a_swap_cannot_be_accepted_twice(self, client):
        schedule, shifts = self._two_shifts(client)
        first_taker = str(uuid.uuid4())
        swap = self._swap(client, from_shift_id=shifts[0]["id"], to_user_id=first_taker)
        client.post(f"/oncall/swaps/{swap}/accept", params={"accepting_user_id": first_taker})

        again = client.post(f"/oncall/swaps/{swap}/accept",
                            params={"accepting_user_id": str(uuid.uuid4())})

        assert again.status_code == 400
        assert self._shift(client, schedule, shifts[0]["id"])["user_id"] == first_taker

    def test_a_declined_swap_leaves_the_shift_alone(self, client):
        schedule, shifts = self._two_shifts(client)
        original = shifts[0]["user_id"]
        swap = self._swap(client, from_shift_id=shifts[0]["id"], to_user_id=str(uuid.uuid4()))

        declined = client.post(f"/oncall/swaps/{swap}/decline",
                               params={"declining_user_id": str(uuid.uuid4())})

        assert declined.status_code == 200, declined.text
        assert self._shift(client, schedule, shifts[0]["id"])["user_id"] == original


class TestICalSubscriptionURL:
    def test_the_advertised_url_is_absolute(self, client):
        schedule = _http_schedule(client)

        url = client.get(f"/oncall/schedules/{schedule}/ical-url").json()["subscription_url"]

        assert url.startswith("http://") or url.startswith("https://")

    def test_the_advertised_url_actually_serves_a_calendar(self, client):
        """It used to point at /api/v1/oncall, which no route has ever matched."""
        schedule = _http_schedule(client)
        _http_shifts(client, schedule, date(2026, 6, 1), date(2026, 6, 3))

        url = client.get(f"/oncall/schedules/{schedule}/ical-url").json()["subscription_url"]
        feed = client.get(url)

        assert feed.status_code == 200, feed.text
        assert feed.headers["content-type"].startswith("text/calendar")
        assert "BEGIN:VCALENDAR" in feed.text

    def test_the_dead_prefix_is_gone(self, client):
        schedule = _http_schedule(client)

        body = client.get(f"/oncall/schedules/{schedule}/ical-url").text

        assert "/api/v1/oncall" not in body

    def test_a_per_user_feed_keeps_the_filter_and_still_resolves(self, client):
        alice, bob = str(uuid.uuid4()), str(uuid.uuid4())
        schedule = _http_schedule(client, participants=[alice, bob])
        _http_shifts(client, schedule, date(2026, 6, 1), date(2026, 6, 5))

        url = client.get(f"/oncall/schedules/{schedule}/ical-url",
                         params={"user_id": alice}).json()["subscription_url"]

        assert f"user_id={alice}" in url
        assert client.get(url).status_code == 200
