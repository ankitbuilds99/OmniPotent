"""Gap detection must ask which shifts *overlap* the window, not which it contains.

`ScheduleService.detect_gaps` selected shifts with `start_time >= from` AND
`end_time <= to`. That is containment, and it throws away the shift that is in
progress across the edge of the window — precisely the one holding the pager.
A weekly rotation running Mon 09:00 → Mon 09:00 is contained in no Wed–Fri
window, so `GET /oncall/schedules/{id}/gaps` answered "72 hours with nobody on
call" for three days that were fully staffed, and reported the edges of every
other window against a truncated shift set.

The router had already learnt this and migrated its own queries to
`_overlaps()`, whose docstring names this exact failure ("the gap analyser
reported a gap where somebody was on call"); `detect_gaps` was the one query
never converted, on both its shift and its override lookup.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

import app.api.main as api
import app.config as cfg
from app import runtime
from app.database import engine as db_engine
from app.oncall.models import (
    OncallOverride,
    OncallRotationParticipant,
    OncallSchedule,
    OncallShift,
    OncallTeam,
)
from app.oncall.schedule_service import ScheduleService


@pytest.fixture
async def db(tmp_path, monkeypatch):
    monkeypatch.setenv("COPILOT_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/gaps.db")
    cfg.get_settings.cache_clear()
    db_engine._engine = None
    db_engine._session_factory = None
    await db_engine.init_db()
    yield
    await db_engine.close_db()
    cfg.get_settings.cache_clear()
    db_engine._engine = None
    db_engine._session_factory = None


async def _schedule(session, *, rotation: str = "weekly", coverage: str = "24x7"):
    from app.database.orgs import ensure_org_row, org_uuid

    await ensure_org_row(session)
    team = OncallTeam(org_id=org_uuid(), name="SRE", slug=f"sre-{uuid.uuid4().hex[:8]}")
    session.add(team)
    await session.flush()

    schedule = OncallSchedule(
        team_id=team.id,
        name="primary",
        timezone="UTC",
        coverage_type=coverage,
        rotation_config={"type": rotation, "handoff_time": "09:00", "handoff_day": "monday"},
    )
    session.add(schedule)
    await session.flush()
    session.add(OncallRotationParticipant(
        schedule_id=schedule.id, user_id=uuid.uuid4(), position=0
    ))
    await session.commit()
    return schedule


def _shift(schedule_id, start: datetime, end: datetime, **kw) -> OncallShift:
    return OncallShift(
        schedule_id=schedule_id,
        user_id=uuid.uuid4(),
        start_time=start,
        end_time=end,
        shift_type=kw.get("shift_type", "primary"),
        status=kw.get("status", "scheduled"),
    )


class TestInProgressShiftIsVisible:
    async def test_a_week_long_shift_covers_the_midweek_window_it_straddles(self, db):
        """The whole finding. One shift, Mon 09:00 → Mon 09:00, and a Wed–Fri
        query that sits entirely inside it. Containment matched nothing and the
        answer was a single 72-hour hole for a fully staffed window."""
        from app.database.engine import get_session

        async with get_session() as session:
            schedule = await _schedule(session)
            session.add(_shift(
                schedule.id,
                datetime(2026, 6, 1, 9, 0),
                datetime(2026, 6, 8, 9, 0),
            ))
            await session.commit()

            gaps = await ScheduleService(session).detect_gaps(
                schedule.id, date(2026, 6, 3), date(2026, 6, 5)
            )

        assert gaps == [], f"nobody is missing here, yet: {gaps}"

    async def test_the_leading_edge_is_measured_against_the_real_previous_shift(self, db):
        """The outgoing shift runs to 09:00 on the first day of the window. It
        is not contained in the window, so it used to vanish and its nine hours
        of coverage were reported as a hole."""
        from app.database.engine import get_session

        async with get_session() as session:
            schedule = await _schedule(session)
            session.add(_shift(
                schedule.id,
                datetime(2026, 5, 25, 9, 0),
                datetime(2026, 6, 1, 9, 0),
            ))
            session.add(_shift(
                schedule.id,
                datetime(2026, 6, 1, 9, 0),
                datetime(2026, 6, 8, 9, 0),
            ))
            await session.commit()

            gaps = await ScheduleService(session).detect_gaps(
                schedule.id, date(2026, 6, 1), date(2026, 6, 2)
            )

        assert gaps == [], f"the handoff is seamless, yet: {gaps}"

    async def test_a_real_hole_at_the_trailing_edge_still_reports_its_true_size(self, db):
        """Overlap must not paper over genuine gaps: coverage stops at noon on
        the last day and the missing twelve hours are still reported, unpadded."""
        from app.database.engine import get_session

        async with get_session() as session:
            schedule = await _schedule(session)
            session.add(_shift(
                schedule.id,
                datetime(2026, 5, 25, 0, 0),
                datetime(2026, 6, 2, 12, 0),
            ))
            await session.commit()

            gaps = await ScheduleService(session).detect_gaps(
                schedule.id, date(2026, 6, 1), date(2026, 6, 2)
            )

        assert len(gaps) == 1, gaps
        assert gaps[0]["gap_hours"] == 12.0
        assert gaps[0]["gap_start"].replace(tzinfo=None) == datetime(2026, 6, 2, 12, 0)

    async def test_a_shift_that_only_touches_the_boundary_covers_nothing(self, db):
        """Half-open, both ends: a shift ending exactly when the window opens
        contributes no covered second, so the window is entirely a gap."""
        from app.database.engine import get_session

        async with get_session() as session:
            schedule = await _schedule(session)
            session.add(_shift(
                schedule.id,
                datetime(2026, 5, 25, 0, 0),
                datetime(2026, 6, 1, 0, 0),
            ))
            await session.commit()

            gaps = await ScheduleService(session).detect_gaps(
                schedule.id, date(2026, 6, 1), date(2026, 6, 1)
            )

        assert len(gaps) == 1, gaps
        assert gaps[0]["gap_hours"] == 24.0

    async def test_an_override_straddling_the_window_still_counts_as_coverage(self, db):
        """The override query had the identical containment shape, so a
        long-running override — the PTO cover for exactly this week — was
        dropped and the hole it fills was reported as uncovered."""
        from app.database.engine import get_session

        async with get_session() as session:
            schedule = await _schedule(session)
            session.add(_shift(
                schedule.id,
                datetime(2026, 6, 1, 0, 0),
                datetime(2026, 6, 2, 6, 0),
            ))
            session.add(OncallOverride(
                schedule_id=schedule.id,
                override_user_id=uuid.uuid4(),
                created_by=uuid.uuid4(),
                start_time=datetime(2026, 6, 2, 6, 0),
                end_time=datetime(2026, 6, 9, 0, 0),
                status="active",
            ))
            await session.commit()

            gaps = await ScheduleService(session).detect_gaps(
                schedule.id, date(2026, 6, 1), date(2026, 6, 2)
            )

        assert gaps == [], f"the override covers the rest of the window, yet: {gaps}"


# --------------------------------------------------------------------------- #
# Over HTTP: the endpoint the finding is actually about
# --------------------------------------------------------------------------- #
@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("COPILOT_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/gaps-http.db")
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


class TestGapsEndpointOnAWeeklyRotation:
    def test_the_default_week_view_of_a_weekly_rotation_is_not_all_hole(self, client):
        team = client.post("/oncall/teams", json={
            "name": "Platform", "slug": f"platform-{uuid.uuid4().hex[:8]}",
        })
        assert team.status_code == 200, team.text

        schedule = client.post("/oncall/schedules", json={
            "team_id": team.json()["id"],
            "name": "primary",
            "timezone": "UTC",
            "rotation_type": "weekly",
            "coverage_type": "24x7",
            "participants": [str(uuid.uuid4()), str(uuid.uuid4())],
        })
        assert schedule.status_code == 200, schedule.text
        schedule_id = schedule.json()["id"]

        created = client.post(
            f"/oncall/schedules/{schedule_id}/generate-shifts",
            json={"start_date": "2026-06-01", "end_date": "2026-06-29"},
        )
        assert created.status_code == 200, created.text

        listed = client.get(
            f"/oncall/schedules/{schedule_id}/shifts",
            params={"start_date": "2026-06-01", "end_date": "2026-06-29"},
        )
        assert listed.status_code == 200, listed.text
        primaries = sorted(
            (s for s in listed.json() if s["shift_type"] == "primary"),
            key=lambda s: s["start_time"],
        )
        assert primaries, "no shifts were generated, the probe proves nothing"

        # A window strictly inside one generated shift: the operator's "what
        # about the middle of next week" question, which is where containment
        # produced a full-window phantom gap.
        covering = primaries[0]
        start = datetime.fromisoformat(covering["start_time"]).replace(tzinfo=None)
        end = datetime.fromisoformat(covering["end_time"]).replace(tzinfo=None)
        inner_start = (start + timedelta(days=1)).date()
        inner_end = (end - timedelta(days=2)).date()
        assert inner_start <= inner_end, "generated shift is too short to probe"

        response = client.get(
            f"/oncall/schedules/{schedule_id}/gaps",
            params={"start_date": str(inner_start), "end_date": str(inner_end)},
        )

        assert response.status_code == 200, response.text
        assert response.json() == [], (
            f"{inner_start}..{inner_end} is inside shift "
            f"{covering['start_time']}..{covering['end_time']}"
        )
