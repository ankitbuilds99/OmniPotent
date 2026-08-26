"""HTTP contract for generating a postmortem.

There was no postmortem route at all (`GET /api/postmortems -> 404`, and the
only "postmortem" path in the route dump was the Slack slash command, which
replies "Generating Postmortem…" and generates nothing). The page therefore
built the document in the browser, where Root Cause Analysis was always the
literal "The incident was caused by an issue that requires further
investigation…", Lessons Learned was always the same block, and both action
items were hardcoded — all of it badged "AI Generated".

These tests pin the two things that make the replacement honest:
  * the facts (timeline, durations) come off the incident record, and an
    unmeasurable duration is null rather than 0;
  * with no LLM configured the route fails with a 503 that names the missing
    variable, instead of falling back to a template.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import app.api.main as api
import app.config as cfg
from app import runtime, tenant_context
from app.database import engine as db_engine
from app.postmortems import generator


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("COPILOT_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/app.db")
    monkeypatch.delenv("COPILOT_MULTI_TENANT", raising=False)
    monkeypatch.delenv("COPILOT_API_TOKEN", raising=False)
    cfg.get_settings.cache_clear()
    db_engine._engine = None
    db_engine._session_factory = None
    tenant_context.current_tenant.set(None)
    api._RL.clear()
    with TestClient(api.app) as c:
        yield c
    cfg.get_settings.cache_clear()
    db_engine._engine = None
    db_engine._session_factory = None
    tenant_context.current_tenant.set(None)


class _StubResponse:
    def __init__(self, content: str) -> None:
        self.content = content


class _StubLLM:
    """Stands in for the provider client inside the test only.

    It records what it was asked so the tests can assert the model is given the
    incident's real facts, and returns a fixed document so the assertions are
    about the route, not about a live model.
    """

    def __init__(self, payload: dict | str) -> None:
        self.payload = payload
        self.calls: list[list] = []

    def invoke(self, messages):
        self.calls.append(messages)
        body = self.payload if isinstance(self.payload, str) else json.dumps(self.payload)
        return _StubResponse(body)


FULL_DOCUMENT = {
    "summary": "checkout-api was unavailable for 30 minutes.",
    "impact": "Checkout requests failed for the duration.",
    "root_cause": "The connection pool was exhausted by a leaked transaction.",
    "lessons": "Pool saturation had no alert.",
    "action_items": [
        {
            "title": "Alert on connection pool saturation",
            "owner": "alice",
            "priority": "P1",
            "type": "detect",
        },
        {"title": "", "owner": "bob"},
    ],
}


@pytest.fixture
def stub_llm(monkeypatch):
    """Configure a provider key and swap the client out for the stub."""
    monkeypatch.setattr(runtime, "provider", lambda: "anthropic")
    monkeypatch.setattr(runtime, "provider_key", lambda p=None: "sk-test")
    llm = _StubLLM(FULL_DOCUMENT)
    monkeypatch.setattr(generator, "_require_model", lambda: llm)
    return llm


def _incident(client, **over):
    body = {
        "title": "Checkout API returning 500s",
        "description": "Elevated error rate on checkout",
        "severity": "critical",
        "service": "checkout-api",
        "tags": ["payments"],
    }
    body.update(over)
    res = client.post("/api/incidents", json=body)
    assert res.status_code == 200, res.text
    return res.json()["id"]


def test_generates_from_the_incident_not_from_a_template(client, stub_llm):
    incident_id = _incident(client)
    client.post(f"/api/incidents/{incident_id}/acknowledge", params={"user": "alice"})
    client.post(f"/api/incidents/{incident_id}/resolve", params={"user": "bob"})

    res = client.post(f"/api/incidents/{incident_id}/postmortem", json={})
    assert res.status_code == 200, res.text
    body = res.json()

    sections = {s["id"]: s["content"] for s in body["sections"]}
    assert sections["root_cause"] == FULL_DOCUMENT["root_cause"]
    # The literal the page used to print for every incident, forever.
    assert "requires further investigation" not in sections["root_cause"]
    assert [s["id"] for s in body["sections"]] == ["summary", "impact", "root_cause", "lessons"]

    # Authors are the people the record shows worked it, not "On-Call Engineer".
    assert body["authors"] == ["alice", "bob"]
    assert body["reviewers"] == []

    # The timeline is the stored one, including the events the old page never
    # synthesised (a status change on acknowledge, the resolution note).
    events = [e["event"] for e in body["timeline"]]
    assert any("Incident created" in e for e in events)
    assert any("Acknowledged by alice" in e for e in events)
    assert body["timeline"][0]["type"] == "detection"

    # The model was handed the incident's real facts.
    (system, human), = [tuple(c) for c in stub_llm.calls]
    facts = json.loads(str(human.content))
    assert facts["incident_id"] == incident_id
    assert facts["service"] == "checkout-api"
    assert facts["acknowledged_by"] == "alice"
    assert "Do not invent" in str(system.content)


def test_unmeasurable_durations_are_null_not_zero(client, stub_llm):
    incident_id = _incident(client)  # never acknowledged, never resolved

    res = client.post(f"/api/incidents/{incident_id}/postmortem", json={})
    assert res.status_code == 200, res.text
    metrics = res.json()["metrics"]
    # The page printed "0m" for each of these, which reads as "instant".
    assert metrics["time_to_detect"] is None
    assert metrics["time_to_acknowledge"] is None
    assert metrics["time_to_mitigate"] is None
    assert metrics["time_to_resolve"] is None
    assert metrics["affected_services"] == ["checkout-api"]


def test_action_items_come_from_the_model_and_carry_no_invented_due_date(client, stub_llm):
    incident_id = _incident(client)
    body = client.post(f"/api/incidents/{incident_id}/postmortem", json={}).json()

    items = body["action_items"]
    assert [i["title"] for i in items] == ["Alert on connection pool saturation"]
    assert items[0]["owner"] == "alice"
    assert items[0]["priority"] == "P1"
    # Nobody agreed a date, so none is stamped on. The page used now+7d/now+14d.
    assert items[0]["due_date"] is None
    assert not any("Review and update monitoring alerts" == i["title"] for i in items)


def test_regenerating_one_section_asks_for_only_that_section(client, stub_llm):
    incident_id = _incident(client)
    stub_llm.payload = {"root_cause": "A leaked transaction held every connection."}

    res = client.post(
        f"/api/incidents/{incident_id}/postmortem", json={"sections": ["root_cause"]}
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert [s["id"] for s in body["sections"]] == ["root_cause"]
    assert body["sections"][0]["content"] == "A leaked transaction held every connection."
    # A partial regeneration must not re-issue the action items.
    assert body["action_items"] == []
    assert len(stub_llm.calls) == 1


def test_unknown_section_is_rejected(client, stub_llm):
    incident_id = _incident(client)
    res = client.post(
        f"/api/incidents/{incident_id}/postmortem", json={"sections": ["conclusion"]}
    )
    assert res.status_code == 422
    assert "conclusion" in json.dumps(res.json())
    assert stub_llm.calls == []


def test_unknown_incident_is_404(client, stub_llm):
    res = client.post(
        "/api/incidents/00000000-0000-0000-0000-000000000000/postmortem", json={}
    )
    assert res.status_code == 404


def test_no_llm_configured_fails_with_503_naming_the_variable(client, monkeypatch):
    monkeypatch.setattr(runtime, "provider", lambda: "anthropic")
    monkeypatch.setattr(runtime, "provider_key", lambda p=None: "")
    incident_id = _incident(client)

    res = client.post(f"/api/incidents/{incident_id}/postmortem", json={})
    assert res.status_code == 503, res.text
    detail = res.json()["detail"]
    assert detail["error"] == "llm_not_configured"
    assert detail["missing_configuration"] == ["ANTHROPIC_API_KEY"]
    assert "ANTHROPIC_API_KEY" in detail["message"]


def test_unusable_model_reply_is_a_502_not_a_half_document(client, stub_llm):
    incident_id = _incident(client)
    stub_llm.payload = "I'm afraid I can't do that."

    res = client.post(f"/api/incidents/{incident_id}/postmortem", json={})
    assert res.status_code == 502, res.text
    assert res.json()["detail"]["error"] == "generation_failed"


def test_model_that_omits_a_section_is_a_502(client, stub_llm):
    incident_id = _incident(client)
    stub_llm.payload = {"summary": "ok", "impact": "ok", "root_cause": "  ", "lessons": "ok"}

    res = client.post(f"/api/incidents/{incident_id}/postmortem", json={})
    assert res.status_code == 502, res.text
    assert "root_cause" in res.json()["detail"]["message"]
