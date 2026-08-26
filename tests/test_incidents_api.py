"""The incident HTTP surface, driven the way the UI drives it.

Everything here goes through `TestClient` with the lifespan running, because the
store is database-backed and every route looks like a 500 without `init_db()`.

Two whole features were broken behind this surface and the suite could not see
either of them:

  * `POST /{id}/acknowledge` and `/{id}/resolve` took `user` as a *required*
    query parameter. The page sends no query string, so every Acknowledge and
    Resolve click was a 422 that the page swallowed — the drawer closed and the
    incident was untouched. The only coverage was the route smoke test, which
    posts to a nonexistent id and accepts the 422 as "not a 5xx".
  * `POST /{id}/investigate` wrote a row with six `idle` agents, a timeline entry
    claiming "6-agent parallel investigation initiated", and three `pending`
    safety gates — then returned. Nothing was queued, no LLM was called, and no
    code anywhere could move an agent out of `idle`.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

import app.api.main as api
import app.config as cfg
from app import runtime, tenant_context
from app.database import engine as db_engine
from app.incidents.models import IncidentSeverity
from app.incidents.store import get_incident_store
from app.session import TurnResult

_PROVIDER_KEYS = (
    "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY", "DEEPSEEK_API_KEY",
)


def _reset() -> None:
    cfg.get_settings.cache_clear()
    tenant_context.current_tenant.set(None)
    api._RL.clear()
    api._reset_limiter()
    runtime.reset()
    db_engine._engine = None
    db_engine._session_factory = None


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A scratch database, auth off, and no provider key unless a test sets one."""
    monkeypatch.setenv("COPILOT_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/incidents.db")
    monkeypatch.setenv("COPILOT_API_TOKEN", "")
    for key in _PROVIDER_KEYS:  # empty, not delenv: .env would otherwise supply one
        monkeypatch.setenv(key, "")
    _reset()
    yield monkeypatch
    _reset()


@pytest.fixture
def client(env):
    with TestClient(api.app, raise_server_exceptions=False) as test_client:
        yield test_client, env


@pytest.fixture
def db(env):
    """The database without an HTTP client, for the background write-back path."""
    asyncio.run(db_engine.init_db())
    yield env
    asyncio.run(db_engine.close_db())


def _run(coro):
    return asyncio.run(coro)


def _with_llm_key(monkeypatch) -> list[dict]:
    """Configure a provider key and capture what gets queued instead of running
    it. Returns the list the queued payloads land in."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    cfg.get_settings.cache_clear()
    runtime.reset()
    queued: list[dict] = []

    async def fake_enqueue(incident: dict) -> None:
        queued.append(incident)

    monkeypatch.setattr(api, "_enqueue_investigation", fake_enqueue)
    return queued


def _declare(client, title: str = "checkout 5xx", service: str = "checkout") -> dict:
    response = client.post(
        "/api/incidents",
        json={"title": title, "severity": "critical", "service": service},
    )
    assert response.status_code == 200, response.text
    return response.json()


# --- the lifecycle the drawer drives --------------------------------------- #
def test_the_whole_incident_lifecycle_over_http(client):
    c, _ = client
    incident = _declare(c)
    incident_id = incident["id"]

    listed = c.get("/api/incidents")
    assert listed.status_code == 200, listed.text
    assert [i["id"] for i in listed.json()] == [incident_id]

    patched = c.patch(f"/api/incidents/{incident_id}", json={"assignee": "ada"})
    assert patched.status_code == 200, patched.text
    assert patched.json()["assignee"] == "ada"

    # No `user` query parameter — exactly what the Incidents page sends.
    ack = c.post(f"/api/incidents/{incident_id}/acknowledge")
    assert ack.status_code == 200, ack.text
    assert ack.json()["status"] == "investigating"

    resolve = c.post(f"/api/incidents/{incident_id}/resolve", params={"notes": "rolled back"})
    assert resolve.status_code == 200, resolve.text
    assert resolve.json()["status"] == "resolved"

    detail = c.get(f"/api/incidents/{incident_id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["incident"]["status"] == "resolved"

    timeline = c.get(f"/api/incidents/{incident_id}/timeline")
    assert [e["type"] for e in timeline.json()] == ["triggered", "acknowledged", "resolved"]
    assert "rolled back" in timeline.json()[-1]["message"]


def test_acknowledge_and_resolve_do_not_require_a_user_query_parameter(client):
    """The exact 422 the UI hit on every click."""
    c, _ = client
    incident_id = _declare(c)["id"]

    assert c.post(f"/api/incidents/{incident_id}/acknowledge").status_code == 200
    assert c.post(f"/api/incidents/{incident_id}/resolve").status_code == 200


def test_an_unattributable_acknowledge_says_so_rather_than_inventing_a_name(client):
    """Auth is off here, so there is genuinely no principal to credit."""
    c, _ = client
    incident_id = _declare(c)["id"]

    body = c.post(f"/api/incidents/{incident_id}/acknowledge").json()

    assert body["acknowledged_by"] == "unknown"
    detail = c.get(f"/api/incidents/{incident_id}").json()["incident"]
    assert detail["acknowledged_by"] == "unknown"


def test_an_explicit_user_is_the_one_recorded(client):
    c, _ = client
    incident_id = _declare(c)["id"]

    c.post(f"/api/incidents/{incident_id}/acknowledge", params={"user": "ada@acme.com"})
    c.post(f"/api/incidents/{incident_id}/resolve", params={"user": "ada@acme.com"})

    detail = c.get(f"/api/incidents/{incident_id}").json()["incident"]
    assert detail["acknowledged_by"] == "ada@acme.com"
    assert detail["resolved_by"] == "ada@acme.com"
    assert "Acknowledged by ada@acme.com" in [e["message"] for e in detail["timeline"]]


def test_acknowledging_a_missing_incident_is_a_404(client):
    c, _ = client
    missing = "00000000-0000-0000-0000-0000000000ff"

    assert c.post(f"/api/incidents/{missing}/acknowledge").status_code == 404
    assert c.post(f"/api/incidents/{missing}/resolve").status_code == 404


# --- investigate ----------------------------------------------------------- #
def test_investigate_queues_the_real_agent_run(client):
    c, monkeypatch = client
    queued = _with_llm_key(monkeypatch)
    incident_id = _declare(c, title="checkout 5xx", service="checkout")["id"]

    response = c.post(f"/api/incidents/{incident_id}/investigate")

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "running"
    # The job that actually drives the agent graph, with the incident attached so
    # the verdict can be written back onto it.
    assert len(queued) == 1, queued
    assert queued[0]["incident_id"] == incident_id
    assert queued[0]["title"] == "checkout 5xx"
    assert queued[0]["service"] == "checkout"


def test_a_fresh_investigation_claims_no_agents_and_no_gates(client):
    """It used to answer with six agents that do not exist, parked at `idle`,
    and three safety gates that nothing ever evaluated."""
    c, monkeypatch = client
    _with_llm_key(monkeypatch)
    incident_id = _declare(c)["id"]

    c.post(f"/api/incidents/{incident_id}/investigate")
    investigation = c.get(f"/api/incidents/{incident_id}/investigation").json()

    assert investigation["status"] == "running"
    assert investigation["agents"] == {}
    assert investigation["safety_gates"] == []
    assert investigation["root_cause"] is None
    assert investigation["overall_confidence"] == 0.0

    messages = [e["message"] for e in c.get(f"/api/incidents/{incident_id}/timeline").json()]
    assert not any("6-agent" in m for m in messages), messages


def test_investigate_without_a_provider_key_is_a_503_and_records_nothing(client):
    """Nothing can run without a key, so nothing is allowed to look like it is."""
    c, _ = client
    incident_id = _declare(c)["id"]

    response = c.post(f"/api/incidents/{incident_id}/investigate")

    assert response.status_code == 503, response.text
    detail = response.json()["detail"]
    assert detail["error"] == "llm_not_configured"
    assert "COPILOT_PROVIDER" in detail["message"]
    # No placeholder investigation left behind.
    assert c.get(f"/api/incidents/{incident_id}/investigation").status_code == 404
    assert c.get(f"/api/incidents/{incident_id}").json()["investigation"] is None


def test_investigating_twice_does_not_queue_a_second_run(client):
    c, monkeypatch = client
    queued = _with_llm_key(monkeypatch)
    incident_id = _declare(c)["id"]

    first = c.post(f"/api/incidents/{incident_id}/investigate").json()
    second = c.post(f"/api/incidents/{incident_id}/investigate").json()

    assert first["investigation_id"] == second["investigation_id"]
    assert len(queued) == 1, queued


def test_investigating_a_missing_incident_is_a_404(client):
    c, monkeypatch = client
    _with_llm_key(monkeypatch)

    missing = "00000000-0000-0000-0000-0000000000ff"
    assert c.post(f"/api/incidents/{missing}/investigate").status_code == 404


def test_the_agent_verdict_lands_on_the_incident(db, monkeypatch):
    """The other half of the loop: what the queued run concludes is written back,
    including the one safety gate this codebase actually measures."""
    store = get_incident_store()
    incident = _run(store.create_incident(
        title="checkout 5xx", severity=IncidentSeverity.CRITICAL, service="checkout"))
    _run(store.start_investigation(incident.id))

    class _StubSession:
        async def ask(self, question: str) -> TurnResult:
            return TurnResult(status="completed", final_text="done", report={
                "root_cause": "connection pool exhausted",
                "confidence": "high",
                "recommended_actions": ["raise pool size"],
            })

    async def fake_get_session(thread_id: str):
        return _StubSession()

    monkeypatch.setattr(api, "_get_session", fake_get_session)
    _run(api._run_triggered_investigation({
        "id": str(incident.id), "source": "incident", "title": "checkout 5xx",
        "service": "checkout", "incident_id": str(incident.id), "org": "",
    }))

    investigation = _run(store.get_incident(incident.id)).investigation
    assert investigation.root_cause == "connection pool exhausted"
    assert investigation.recommended_actions == ["raise pool size"]
    assert investigation.completed_at is not None
    gate = investigation.safety_gates[0]
    assert gate.gate_type == "confidence"
    assert gate.current_value == pytest.approx(0.9)
    assert gate.status.value == "passed"
