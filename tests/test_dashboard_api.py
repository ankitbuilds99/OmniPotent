"""The dashboard must report investigations the API actually ran.

Before this, nothing wrote investigation history and nothing served it: the
records lived in a module-level dict that only the dashboard's own unit tests
ever populated. So the failure mode being guarded here is not a wrong number, it
is a permanently empty one — every route completing an investigation has to
record it, and the read routes have to return it.

The other failure mode is structural. `/dashboard/*` is declared after the SPA
catch-all mount in source order but must be matched before it, and both handlers
are async: a missing `await` on either returns a coroutine FastAPI cannot encode,
which shows up only when the route is called.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import app.api.main as api
import app.config as cfg
from app import runtime
from app.database.engine import close_db
from app.session import TurnResult


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A TestClient over a throwaway database.

    The state dir is repointed before the first `get_settings()` of the test so
    the recorded investigations land in tmp_path rather than the developer's
    working `data/` directory.
    """
    monkeypatch.setenv("COPILOT_STATE_DIR", str(tmp_path))
    cfg.get_settings.cache_clear()
    api._RL.clear()
    runtime.reset()
    # The engine is a module global. Without disposing it here, a test that ran
    # earlier leaves one pointing at *its* tmp_path and this test reads the
    # previous test's investigations.
    asyncio.run(close_db())
    yield TestClient(api.app)
    asyncio.run(close_db())
    api._RL.clear()
    runtime.reset()
    cfg.get_settings.cache_clear()


class _StubSession:
    """A session that returns a canned turn without an LLM or MCP subprocess."""

    def __init__(self, result: TurnResult):
        self._result = result
        self.asked: list[str] = []

    async def pending_interrupt(self) -> bool:
        return False

    async def ask(self, message: str) -> TurnResult:
        self.asked.append(message)
        return self._result


def _stub_turn(monkeypatch, result: TurnResult) -> None:
    async def _fake_get_session(thread_id: str):
        return _StubSession(result)

    monkeypatch.setattr(api, "_get_session", _fake_get_session)


COMPLETED = TurnResult(
    status="completed",
    final_text="The checkout pool was exhausted.",
    report={"summary": "pool exhaustion", "root_cause": "connection leak", "severity": "SEV2"},
    tokens_used=4521,
)


def test_dashboard_routes_respond_without_a_server_error(client):
    """A 500 here is what an un-awaited coroutine looks like."""
    for path in ("/dashboard/metrics", "/dashboard/trends"):
        response = client.get(path)
        assert response.status_code == 200, f"{path}: {response.text}"
        # JSON, not the SPA's index.html — proves the route is matched ahead of
        # the catch-all static mount.
        assert response.headers["content-type"].startswith("application/json")


def test_empty_dashboard_reports_zeroes_and_no_invented_baseline(client):
    """A deployment that has investigated nothing gets nothing, not a sample."""
    body = client.get("/dashboard/metrics").json()

    assert body["summary"]["total"] == 0
    assert body["usage"]["total_investigations"] == 0
    assert body["roi"]["estimated_cost_savings_usd"] == 0
    # No previous period, so no improvement figure and 25 unscoreable points.
    assert body["mttr"]["comparison_available"] is False
    assert body["mttr"]["improvement_percent"] is None
    assert body["mttr"]["improved"] is False
    assert body["health_score_max"] == 75


def test_a_completed_chat_turn_lands_on_the_dashboard(client, monkeypatch):
    """The end-to-end path: POST /chat -> investigations table -> GET /dashboard."""
    _stub_turn(monkeypatch, COMPLETED)

    chat = client.post("/chat", json={"thread_id": "inv-dash-1", "message": "why 500s?"})
    assert chat.status_code == 200
    assert chat.json()["status"] == "completed"

    body = client.get("/dashboard/metrics").json()

    assert body["summary"]["total"] == 1
    assert body["usage"]["total_investigations"] == 1
    assert body["usage"]["tokens_used"] == 4521
    # `report.root_cause` is present, so the outcome is evidenced, not assumed.
    assert body["usage"]["investigations_by_outcome"] == {"root_cause_found": 1}
    assert body["summary"]["recent_investigations"][0]["id"] == "inv-dash-1"


def test_a_multi_turn_conversation_is_one_investigation(client, monkeypatch):
    """Three turns on one thread must not read as three investigations.

    The recording hook fires per completed turn, which is the only place it can
    fire from. Counting is therefore the table's job, via the unique index on
    (org_id, thread_id).
    """
    _stub_turn(monkeypatch, COMPLETED)

    for _ in range(3):
        assert client.post(
            "/chat", json={"thread_id": "inv-dash-2", "message": "and now?"}
        ).status_code == 200

    body = client.get("/dashboard/metrics").json()

    assert body["summary"]["total"] == 1
    # Tokens still accumulate across the turns; only the count is deduplicated.
    assert body["usage"]["tokens_used"] == 3 * 4521


def test_an_abstention_is_recorded_as_an_abstention(client, monkeypatch):
    """Abstention rate is a headline number, so it may not be inferred loosely."""
    _stub_turn(
        monkeypatch,
        TurnResult(
            status="completed",
            final_text="Not enough signal to say.",
            report={"abstained": True, "summary": "insufficient evidence"},
            tokens_used=800,
        ),
    )

    assert client.post(
        "/chat", json={"thread_id": "inv-abstained", "message": "why?"}
    ).status_code == 200

    body = client.get("/dashboard/metrics").json()
    assert body["usage"]["investigations_by_outcome"] == {"abstained": 1}
    assert body["summary"]["abstained"] == 1
    assert body["summary"]["abstention_rate"] == 1.0


def test_a_turn_without_a_root_cause_is_only_a_partial_diagnosis(client, monkeypatch):
    """The default is the weakest claim the result supports."""
    _stub_turn(
        monkeypatch,
        TurnResult(
            status="completed",
            final_text="Here is what I found.",
            report={"summary": "elevated latency on checkout"},
            tokens_used=300,
        ),
    )

    assert client.post(
        "/chat", json={"thread_id": "inv-partial", "message": "why?"}
    ).status_code == 200

    body = client.get("/dashboard/metrics").json()
    assert body["usage"]["investigations_by_outcome"] == {"partial_diagnosis": 1}
    # RESOLVED is never emitted: nothing observes whether anything got fixed, so
    # "incidents auto-resolved" stays honest at zero.
    assert body["roi"]["incidents_auto_resolved"] == 0


def test_reported_roi_is_zero_until_something_measures_time_saved(client, monkeypatch):
    """A dollar figure shown to a customer may not be extrapolated."""
    _stub_turn(monkeypatch, COMPLETED)
    client.post("/chat", json={"thread_id": "inv-roi", "message": "why 500s?"})

    roi = client.get("/dashboard/metrics").json()["roi"]

    assert roi["investigations_completed"] == 1
    assert roi["time_saved_hours"] == 0
    assert roi["estimated_cost_savings_usd"] == 0


def test_dashboard_history_outlives_the_process(client, monkeypatch, tmp_path):
    """Recorded on one app instance, served by the next."""
    _stub_turn(monkeypatch, COMPLETED)
    client.post("/chat", json={"thread_id": "inv-durable", "message": "why 500s?"})

    # Second instance against the same state dir: shutdown disposed the engine
    # and cleared the session pool, so nothing is carried in memory.
    runtime.reset()
    with TestClient(api.app) as second:
        body = second.get("/dashboard/metrics").json()

    assert body["summary"]["total"] == 1
    assert body["summary"]["recent_investigations"][0]["id"] == "inv-durable"


def test_trends_report_direction_in_terms_of_health(client, monkeypatch):
    """Rising token spend is a decline even though "tokens" reads neutral."""
    from app.dashboard.metrics import InvestigationOutcome, record_investigation

    org_id = "default"
    now = datetime.now(timezone.utc)

    async def _seed():
        # 14 days: spend climbing steadily, duration falling steadily.
        for day in range(14, 0, -1):
            started = now - timedelta(days=day)
            await record_investigation(
                org_id=org_id,
                investigation_id=f"trend_{day}",
                started_at=started,
                completed_at=started + timedelta(minutes=day),
                outcome=InvestigationOutcome.ROOT_CAUSE_FOUND,
                tokens_used=(15 - day) * 500,
            )

    # Seeded on its own loop, then the engine is disposed: aiosqlite connections
    # are bound to the loop that opened them, and TestClient serves requests on a
    # different one.
    asyncio.run(_seed())
    asyncio.run(close_db())

    body = client.get("/dashboard/trends").json()
    trends = body["trends"]

    assert trends["tokens_per_day"]["polarity"] == "lower_is_better"
    assert trends["tokens_per_day"]["direction"] == "declining"
    # MTTR falling over the window is an improvement.
    assert trends["mttr_minutes"]["polarity"] == "lower_is_better"
    assert trends["mttr_minutes"]["direction"] == "improving"
    assert body["summary"]["total_metrics"] == 3


@pytest.mark.parametrize("path", ["/dashboard/metrics", "/dashboard/trends"])
@pytest.mark.parametrize("period_days", [0, -1, 366])
def test_period_days_is_bounded(client, path, period_days):
    """An unbounded period is an unbounded scan of one org's whole history."""
    response = client.get(path, params={"period_days": period_days})
    assert response.status_code == 422
