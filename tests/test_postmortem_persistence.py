"""A postmortem is a document, not a render.

The page let an operator rewrite any section and set a status on any action
item, and both handlers were `setPostmortem(...)` calls into React state with
nothing behind them — there was no endpoint to call, because there was no table
to write to. Every visit re-POSTed and re-ran the model, so the rewrite of a
root cause and every action item marked done were replaced by a freshly
generated draft that had never seen them.

These tests pin the four properties that make the fix real:
  * a generated document is stored, and reading it back returns the same thing;
  * an edited section survives a reload, and is marked as human-written;
  * an action item's status survives a reload;
  * regenerating one section leaves every other section's edits and every
    action item's status alone.
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
    def __init__(self, payload: dict | str) -> None:
        self.payload = payload
        self.calls: list[list] = []

    def invoke(self, messages):
        self.calls.append(messages)
        body = self.payload if isinstance(self.payload, str) else json.dumps(self.payload)
        return _StubResponse(body)


DOCUMENT = {
    "summary": "checkout-api was unavailable for 30 minutes.",
    "impact": "Checkout requests failed for the duration.",
    "root_cause": "The connection pool was exhausted by a leaked transaction.",
    "lessons": "Pool saturation had no alert.",
    "action_items": [
        {"title": "Alert on pool saturation", "owner": "alice", "priority": "P1", "type": "detect"},
        {"title": "Cap transaction lifetime", "owner": "bob", "priority": "P2", "type": "prevent"},
    ],
}


@pytest.fixture
def stub_llm(monkeypatch):
    monkeypatch.setattr(runtime, "provider", lambda: "anthropic")
    monkeypatch.setattr(runtime, "provider_key", lambda p=None: "sk-test")
    llm = _StubLLM(DOCUMENT)
    monkeypatch.setattr(generator, "_require_model", lambda: llm)
    return llm


def _incident(client) -> str:
    res = client.post(
        "/api/incidents",
        json={
            "title": "Checkout API returning 500s",
            "severity": "critical",
            "service": "checkout-api",
        },
    )
    assert res.status_code == 200, res.text
    return res.json()["id"]


def _sections(body: dict) -> dict[str, str]:
    return {s["id"]: s["content"] for s in body["sections"]}


def test_generating_stores_the_document(client, stub_llm):
    incident_id = _incident(client)
    generated = client.post(f"/api/incidents/{incident_id}/postmortem", json={}).json()

    stored = client.get(f"/api/incidents/{incident_id}/postmortem")
    assert stored.status_code == 200, stored.text
    body = stored.json()

    assert _sections(body) == _sections(generated)
    assert [i["title"] for i in body["action_items"]] == [
        "Alert on pool saturation",
        "Cap transaction lifetime",
    ]
    # Reading it back must not cost another model call — the whole point is that
    # the page stops re-generating on every visit.
    assert len(stub_llm.calls) == 1


def test_no_postmortem_yet_is_a_404_not_an_empty_document(client):
    incident_id = _incident(client)
    res = client.get(f"/api/incidents/{incident_id}/postmortem")
    assert res.status_code == 404
    assert "No postmortem has been generated" in res.json()["detail"]


def test_an_edited_section_survives_a_reload(client, stub_llm):
    incident_id = _incident(client)
    client.post(f"/api/incidents/{incident_id}/postmortem", json={})

    rewritten = "A leaked transaction in the settlement job held all 40 connections."
    res = client.patch(
        f"/api/incidents/{incident_id}/postmortem/sections/root_cause",
        json={"content": rewritten},
    )
    assert res.status_code == 200, res.text

    body = client.get(f"/api/incidents/{incident_id}/postmortem").json()
    assert _sections(body)["root_cause"] == rewritten
    # Badged as human-written: a later reader needs to know this paragraph is
    # not model output.
    source = {s["id"]: s.get("source") for s in body["sections"]}
    assert source["root_cause"] == "edited"
    assert source["summary"] == "model"
    # No new model call, and the other sections are untouched.
    assert len(stub_llm.calls) == 1
    assert _sections(body)["impact"] == DOCUMENT["impact"]


def test_an_action_item_status_survives_a_reload(client, stub_llm):
    incident_id = _incident(client)
    generated = client.post(f"/api/incidents/{incident_id}/postmortem", json={}).json()
    item_id = generated["action_items"][0]["id"]

    res = client.patch(
        f"/api/incidents/{incident_id}/postmortem/action-items/{item_id}",
        json={"status": "completed"},
    )
    assert res.status_code == 200, res.text

    body = client.get(f"/api/incidents/{incident_id}/postmortem").json()
    statuses = {i["id"]: i["status"] for i in body["action_items"]}
    assert statuses[item_id] == "completed"
    # The other item is left alone.
    assert set(statuses.values()) == {"completed", "open"}


def test_an_unknown_action_status_is_rejected(client, stub_llm):
    incident_id = _incident(client)
    generated = client.post(f"/api/incidents/{incident_id}/postmortem", json={}).json()
    item_id = generated["action_items"][0]["id"]

    res = client.patch(
        f"/api/incidents/{incident_id}/postmortem/action-items/{item_id}",
        json={"status": "done-ish"},
    )
    assert res.status_code == 422
    assert "done-ish" in res.json()["detail"]


def test_patching_a_section_that_does_not_exist_is_a_404(client, stub_llm):
    incident_id = _incident(client)
    client.post(f"/api/incidents/{incident_id}/postmortem", json={})

    res = client.patch(
        f"/api/incidents/{incident_id}/postmortem/sections/conclusion",
        json={"content": "..."},
    )
    assert res.status_code == 404
    assert "conclusion" in res.json()["detail"]


def test_patching_before_anything_is_generated_is_a_404(client):
    incident_id = _incident(client)
    res = client.patch(
        f"/api/incidents/{incident_id}/postmortem/sections/root_cause",
        json={"content": "..."},
    )
    assert res.status_code == 404
    assert "No postmortem has been generated" in res.json()["detail"]


def test_regenerating_one_section_preserves_the_rest(client, stub_llm):
    """The failure this guards against is the expensive one.

    Regenerate is a per-section button. If it overwrote the stored document,
    pressing it on Impact would silently discard the operator's rewrite of Root
    Cause and reset every action item to open — a destructive action behind a
    button labelled with the name of a different section.
    """
    incident_id = _incident(client)
    generated = client.post(f"/api/incidents/{incident_id}/postmortem", json={}).json()
    item_id = generated["action_items"][0]["id"]

    client.patch(
        f"/api/incidents/{incident_id}/postmortem/sections/root_cause",
        json={"content": "Operator's own analysis."},
    )
    client.patch(
        f"/api/incidents/{incident_id}/postmortem/action-items/{item_id}",
        json={"status": "in_progress"},
    )

    stub_llm.payload = {"impact": "Rewritten impact from the model."}
    res = client.post(
        f"/api/incidents/{incident_id}/postmortem", json={"sections": ["impact"]}
    )
    assert res.status_code == 200, res.text
    body = res.json()

    sections = _sections(body)
    assert sections["impact"] == "Rewritten impact from the model."
    assert sections["root_cause"] == "Operator's own analysis."
    assert sections["summary"] == DOCUMENT["summary"]
    # Section order is preserved rather than the regenerated one jumping to the
    # front — the document reads top to bottom.
    assert [s["id"] for s in body["sections"]] == [
        "summary",
        "impact",
        "root_cause",
        "lessons",
    ]
    assert {i["id"]: i["status"] for i in body["action_items"]}[item_id] == "in_progress"


def test_a_full_regeneration_replaces_the_document(client, stub_llm):
    """The other half of the contract: asking for the whole thing means the
    whole thing. An operator who presses the top-level regenerate is asking for
    a new draft, and quietly keeping their old paragraphs would be its own kind
    of lie."""
    incident_id = _incident(client)
    client.post(f"/api/incidents/{incident_id}/postmortem", json={})
    client.patch(
        f"/api/incidents/{incident_id}/postmortem/sections/root_cause",
        json={"content": "Operator's own analysis."},
    )

    stub_llm.payload = {**DOCUMENT, "root_cause": "A second look: the pool was fine."}
    body = client.post(f"/api/incidents/{incident_id}/postmortem", json={}).json()
    assert _sections(body)["root_cause"] == "A second look: the pool was fine."
    assert {s["id"]: s["source"] for s in body["sections"]}["root_cause"] == "model"


def test_one_row_per_incident(client, stub_llm):
    """Generating twice must update the row, not accumulate documents."""
    incident_id = _incident(client)
    client.post(f"/api/incidents/{incident_id}/postmortem", json={})
    client.post(f"/api/incidents/{incident_id}/postmortem", json={})

    body = client.get(f"/api/incidents/{incident_id}/postmortem").json()
    assert [s["id"] for s in body["sections"]] == ["summary", "impact", "root_cause", "lessons"]
    assert len(body["action_items"]) == 2
