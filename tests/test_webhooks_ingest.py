"""Alert-ingestion webhooks, against the payloads the vendors actually send.

Every receiver here was tested only by the route smoke test, which posts `{}` to
each endpoint. `{}` takes the "not an alert" early return in every one of them,
so a whole class of failure was invisible:

  - Datadog sends `tags` as the string `"service:web,env:prod"`, and the handler
    called `.get()` on it: `AttributeError` -> 500 -> Datadog retries into the
    same 500. A numeric `alert_id` was written to the row as an int and then
    made `GET /api/incidents` fail validation for the whole org, forever.
  - The `/api/webhooks/pagerduty` receiver built a `sha256=` signature header
    (PagerDuty sends `v1=`), so genuine signed deliveries were rejected — while
    an *unsigned* body skipped verification entirely and created incidents.
  - GitHub and GitLab accepted unsigned deliveries whenever no secret was
    configured, and "auto-triage" meant writing a word into the response body.
  - `copilot_auto_investigate` wrote an investigation row with six idle agents
    and never ran anything.

These tests assert the behaviour, not the wording: what a signed delivery does,
what an unsigned one does, and whether anything is actually queued.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

import app.api.main as api
import app.config as cfg
from app import runtime, tenant_context
from app.database import engine as db_engine
from app.incidents.models import IncidentSeverity, IncidentStatus
from app.incidents.store import get_incident_store
from app.session import TurnResult

_PROVIDER_KEYS = (
    "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY", "DEEPSEEK_API_KEY",
)

PD_SECRET = "pdsecret"
GH_SECRET = "ghsecret"
GL_SECRET = "glsecret"


def _reset() -> None:
    cfg.get_settings.cache_clear()
    tenant_context.current_tenant.set(None)
    api._RL.clear()
    api._reset_limiter()
    api._SEEN_DELIVERIES.clear()  # process-global delivery dedupe, like _RL
    runtime.reset()
    db_engine._engine = None
    db_engine._session_factory = None


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Deterministic settings: a scratch DB, no LLM key, webhook secrets set."""
    monkeypatch.setenv("COPILOT_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/webhooks.db")
    monkeypatch.setenv("COPILOT_API_TOKEN", "")
    for key in _PROVIDER_KEYS:  # empty, not delenv: .env would otherwise supply one
        monkeypatch.setenv(key, "")
    monkeypatch.setenv("PAGERDUTY_WEBHOOK_SECRET", PD_SECRET)
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", GH_SECRET)
    monkeypatch.setenv("GITLAB_WEBHOOK_SECRET", GL_SECRET)
    _reset()
    yield monkeypatch
    _reset()


@pytest.fixture
def client(env):
    """`with` runs the lifespan, which runs init_db() — without it every
    incident-backed route looks like a 500."""
    with TestClient(api.app, raise_server_exceptions=False) as test_client:
        yield test_client, env


@pytest.fixture
def db(env):
    """The database, without an HTTP client, for the background-path tests."""
    asyncio.run(db_engine.init_db())
    yield env
    asyncio.run(db_engine.close_db())


def _run(coro):
    return asyncio.run(coro)


def _with_llm_key(monkeypatch) -> list[dict]:
    """Give the app a provider key and capture what gets queued instead of
    running it. Returns the list the payloads land in."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    cfg.get_settings.cache_clear()
    runtime.reset()
    queued: list[dict] = []

    async def fake_enqueue(incident: dict) -> None:
        queued.append(incident)

    monkeypatch.setattr(api, "_enqueue_investigation", fake_enqueue)
    return queued


def _pd_sig(body: bytes) -> str:
    return "v1=" + hmac.new(PD_SECRET.encode(), body, hashlib.sha256).hexdigest()


def _gh_sig(body: bytes) -> str:
    return "sha256=" + hmac.new(GH_SECRET.encode(), body, hashlib.sha256).hexdigest()


def _incident(client, incident_id: str) -> dict:
    response = client.get(f"/api/incidents/{incident_id}")
    assert response.status_code == 200, response.text
    return response.json()


# --- Datadog --------------------------------------------------------------- #
def test_datadog_comma_separated_tag_string_creates_an_incident(client):
    """The shape Datadog's `$TAGS` variable actually renders. Was a 500."""
    c, _ = client
    response = c.post("/api/webhooks/datadog", json={
        "alert_type": "error",
        "title": "checkout 5xx",
        "body": "error rate 40%",
        "tags": "service:checkout,env:prod",
        "alert_id": 812,
    })

    assert response.status_code == 200, response.text
    detail = _incident(c, response.json()["incident_id"])["incident"]
    assert detail["service"] == "checkout"
    assert detail["severity"] == "critical"
    assert detail["description"] == "error rate 40%"
    # int alert id coerced: an int here poisons every later read of the row.
    assert detail["tags"] == ["datadog", "812"]


def test_datadog_list_tags_create_an_incident(client):
    c, _ = client
    response = c.post("/api/webhooks/datadog", json={
        "alert_type": "warning", "title": "cart latency", "tags": ["service:cart", "env:prod"],
    })

    assert response.status_code == 200, response.text
    detail = _incident(c, response.json()["incident_id"])["incident"]
    assert detail["service"] == "cart"
    assert detail["severity"] == "high"


def test_datadog_stock_payload_template_is_not_dropped(client):
    """Datadog's default webhook body carries `event_type`, not `alert_type`."""
    c, _ = client
    response = c.post("/api/webhooks/datadog", json={
        "event_type": "error", "title": "pay 5xx", "tags": {"service": "pay"},
    })

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ok"
    assert _incident(c, response.json()["incident_id"])["incident"]["service"] == "pay"


def test_datadog_recovery_events_are_ignored(client):
    c, _ = client
    response = c.post("/api/webhooks/datadog", json={"alert_type": "success", "title": "ok now"})

    assert response.status_code == 200
    assert response.json()["status"] == "ignored"
    assert response.json()["incident_id"] is None
    assert c.get("/api/incidents").json() == []


def test_one_datadog_alert_cannot_break_the_incident_list(client):
    """A numeric alert id used to be stored as an int, and every later list read
    then failed response validation with a 422 — for every incident, not just
    the bad one."""
    c, _ = client
    assert c.post("/api/webhooks/datadog", json={
        "alert_type": "error", "title": "a", "alert_id": 99,
    }).status_code == 200

    listing = c.get("/api/incidents")
    assert listing.status_code == 200, listing.text
    assert len(listing.json()) == 1


def test_grafana_list_tags_do_not_crash(client):
    """Same coercion bug, same file: `.get()` on a non-object tags field."""
    c, _ = client
    response = c.post("/api/webhooks/grafana", json={
        "state": "alerting", "title": "disk full", "tags": ["service:db", "severity:critical"],
    })

    assert response.status_code == 200, response.text
    detail = _incident(c, response.json()["incident_id"])["incident"]
    assert detail["service"] == "db"
    assert detail["severity"] == "critical"


# --- PagerDuty ------------------------------------------------------------- #
_PD_V3 = json.dumps({"event": {"event_type": "incident.triggered", "data": {
    "id": "PINC1", "title": "database down", "urgency": "high",
    "description": "connections refused", "service": {"summary": "postgres"}}}}).encode()


def test_pagerduty_v3_signed_delivery_opens_an_incident(client):
    """A genuine PagerDuty delivery: v3 body, `v1=` signature. Was a 401 (the
    handler built a `sha256=` digest) and, when it did parse, only read the v2
    `messages` list, so a v3 body yielded "Processed 0 incidents"."""
    c, _ = client
    response = c.post("/api/webhooks/pagerduty", content=_PD_V3,
                      headers={"X-PagerDuty-Signature": _pd_sig(_PD_V3)})

    assert response.status_code == 200, response.text
    assert response.json()["message"] == "Processed 1 incidents"
    detail = _incident(c, response.json()["incident_id"])["incident"]
    assert detail["title"] == "database down"
    assert detail["service"] == "postgres"
    assert detail["severity"] == "critical"
    assert detail["tags"] == ["pagerduty", "pd-PINC1"]


def test_pagerduty_unsigned_delivery_creates_nothing(client):
    """Omitting the header used to skip verification altogether, so anyone could
    post a v2-shaped body and open an incident."""
    c, _ = client
    body = json.dumps({"messages": [{"event": "incident.triggered", "incident": {
        "id": "PD9", "title": "forged", "urgency": "high"}}]}).encode()

    response = c.post("/api/webhooks/pagerduty", content=body)

    assert response.status_code == 401
    assert c.get("/api/incidents").json() == []


def test_pagerduty_wrong_signature_is_rejected(client):
    c, _ = client
    response = c.post("/api/webhooks/pagerduty", content=_PD_V3,
                      headers={"X-PagerDuty-Signature": "v1=deadbeef"})

    assert response.status_code == 401
    assert c.get("/api/incidents").json() == []


def test_pagerduty_without_a_configured_secret_refuses_and_says_which_one(client):
    c, monkeypatch = client
    monkeypatch.setenv("PAGERDUTY_WEBHOOK_SECRET", "")
    cfg.get_settings.cache_clear()

    response = c.post("/api/webhooks/pagerduty", content=_PD_V3,
                      headers={"X-PagerDuty-Signature": _pd_sig(_PD_V3)})

    assert response.status_code == 401
    assert "PAGERDUTY_WEBHOOK_SECRET" in response.json()["detail"]


def test_pagerduty_legacy_v2_body_still_parses_when_signed(client):
    c, _ = client
    body = json.dumps({"messages": [{"event": "incident.trigger", "incident": {
        "id": "PD7", "title": "legacy alert", "urgency": "low",
        "service": {"name": "billing"}}}]}).encode()

    response = c.post("/api/webhooks/pagerduty", content=body,
                      headers={"X-PagerDuty-Signature": _pd_sig(body)})

    assert response.status_code == 200, response.text
    detail = _incident(c, response.json()["incident_id"])["incident"]
    assert detail["service"] == "billing"
    assert detail["severity"] == "medium"


def test_both_pagerduty_receivers_accept_the_same_delivery(client):
    """The two routes used to disagree: the same signed body was a 200 on one
    and a 401 on the other."""
    c, _ = client
    headers = {"X-PagerDuty-Signature": _pd_sig(_PD_V3)}

    assert c.post("/webhooks/pagerduty", content=_PD_V3, headers=headers).status_code == 200
    assert c.post("/api/webhooks/pagerduty", content=_PD_V3, headers=headers).status_code == 200
    # ...and reject the same one.
    assert c.post("/webhooks/pagerduty", content=_PD_V3).status_code == 401
    assert c.post("/api/webhooks/pagerduty", content=_PD_V3).status_code == 401


# --- auto-investigate ------------------------------------------------------ #
def test_an_ingested_alert_queues_a_real_investigation(client):
    """`copilot_auto_investigate` used to call `start_investigation()` and stop:
    a row of six idle agents, no LLM call, no task, nothing to run them."""
    c, monkeypatch = client
    queued = _with_llm_key(monkeypatch)

    response = c.post("/api/webhooks/datadog", json={
        "alert_type": "error", "title": "queue backed up", "tags": "service:worker",
    })
    incident_id = response.json()["incident_id"]

    assert len(queued) == 1, queued
    assert queued[0]["incident_id"] == incident_id
    assert queued[0]["title"] == "queue backed up"
    assert queued[0]["service"] == "worker"
    # and the investigation row exists for the run to report back into
    assert _incident(c, incident_id)["investigation"] is not None


def test_an_alert_with_no_llm_key_leaves_no_placeholder_investigation(client):
    """Nothing can run without a provider key, so nothing claims to be running."""
    c, _ = client

    response = c.post("/api/webhooks/datadog", json={
        "alert_type": "error", "title": "queue backed up", "tags": "service:worker",
    })

    assert response.status_code == 200
    assert _incident(c, response.json()["incident_id"])["investigation"] is None


def test_a_completed_run_is_written_back_onto_the_incident(db, monkeypatch):
    """The queued run's verdict lands on the incident: before this, the
    investigation row kept the values it was created with for ever."""
    store = get_incident_store()
    incident = _run(store.create_incident(
        title="checkout 5xx", severity=IncidentSeverity.CRITICAL, service="checkout"))
    _run(store.start_investigation(incident.id))

    seen: dict[str, str] = {}

    class _StubSession:
        async def ask(self, question: str) -> TurnResult:
            seen["seed"] = question
            return TurnResult(status="completed", final_text="done", report={
                "root_cause": "connection pool exhausted",
                "confidence": "high",
                "recommended_actions": ["raise pool size", "restart api"],
            })

    async def fake_get_session(thread_id: str):
        seen["thread"] = thread_id
        return _StubSession()

    monkeypatch.setattr(api, "_get_session", fake_get_session)

    _run(api._run_triggered_investigation({
        "id": str(incident.id), "source": "incident", "title": "checkout 5xx",
        "service": "checkout", "incident_id": str(incident.id), "org": "",
    }))

    reloaded = _run(store.get_incident(incident.id))
    investigation = reloaded.investigation
    assert investigation.root_cause == "connection pool exhausted"
    assert investigation.recommended_actions == ["raise pool size", "restart api"]
    assert investigation.overall_confidence == pytest.approx(0.9)
    assert investigation.completed_at is not None
    assert reloaded.status is IncidentStatus.IDENTIFIED
    assert [e.type for e in reloaded.timeline][-1] == "investigation_completed"
    # the thread is named for where the alert came from, not always "pd-"
    assert seen["thread"] == f"incident-{incident.id}"


def test_a_failed_run_is_recorded_instead_of_looking_live_forever(db, monkeypatch):
    store = get_incident_store()
    incident = _run(store.create_incident(
        title="checkout 5xx", severity=IncidentSeverity.CRITICAL, service="checkout"))
    _run(store.start_investigation(incident.id))

    async def fake_get_session(thread_id: str):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(api, "_get_session", fake_get_session)

    _run(api._run_triggered_investigation({
        "id": str(incident.id), "source": "incident", "title": "checkout 5xx",
        "service": "checkout", "incident_id": str(incident.id), "org": "",
    }))

    reloaded = _run(store.get_incident(incident.id))
    assert reloaded.investigation.completed_at is not None
    assert reloaded.investigation.root_cause is None
    last = reloaded.timeline[-1]
    assert last.type == "investigation_failed"
    assert "provider unavailable" in last.message


def test_the_write_back_is_scoped_to_the_alerts_org(db, monkeypatch):
    """The background run has no request context, so it carries the org with it —
    it must not be able to write onto another tenant's incident."""
    async def as_org(org: str, factory):
        token = tenant_context.set_tenant(tenant_context.TenantConfig(org_id=org))
        try:
            return await factory()
        finally:
            tenant_context.reset_tenant(token)

    store = get_incident_store()
    incident = _run(as_org("acme", lambda: store.create_incident(
        title="checkout 5xx", severity=IncidentSeverity.CRITICAL, service="checkout")))
    _run(as_org("acme", lambda: store.start_investigation(incident.id)))

    class _StubSession:
        async def ask(self, question: str) -> TurnResult:
            return TurnResult(status="completed", report={
                "root_cause": "leaked into the wrong org", "confidence": "high",
                "recommended_actions": []})

    async def fake_get_session(thread_id: str):
        return _StubSession()

    monkeypatch.setattr(api, "_get_session", fake_get_session)

    # A job claiming a different org must not touch acme's incident...
    _run(api._run_triggered_investigation({
        "id": str(incident.id), "source": "incident", "title": "checkout 5xx",
        "service": "checkout", "incident_id": str(incident.id), "org": "umbrella",
    }))
    untouched = _run(as_org("acme", lambda: store.get_incident(incident.id)))
    assert untouched.investigation.root_cause is None

    # ...and the org that owns it does.
    _run(api._run_triggered_investigation({
        "id": str(incident.id), "source": "incident", "title": "checkout 5xx",
        "service": "checkout", "incident_id": str(incident.id), "org": "acme",
    }))
    updated = _run(as_org("acme", lambda: store.get_incident(incident.id)))
    assert updated.investigation.root_cause == "leaked into the wrong org"


# --- GitHub ---------------------------------------------------------------- #
_GH_ISSUE = json.dumps({
    "action": "opened",
    "issue": {"number": 41, "title": "prod is down", "labels": [{"name": "incident"}]},
    "repository": {"full_name": "acme/api"},
}).encode()


def test_github_without_a_configured_secret_refuses_and_says_which_one(client):
    c, monkeypatch = client
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "")
    cfg.get_settings.cache_clear()

    response = c.post("/webhooks/github", content=_GH_ISSUE,
                      headers={"X-GitHub-Event": "issues", "X-GitHub-Delivery": "d-1"})

    assert response.status_code == 401
    assert "GITHUB_WEBHOOK_SECRET" in response.json()["detail"]


def test_github_unsigned_delivery_is_rejected(client):
    c, _ = client
    response = c.post("/webhooks/github", content=_GH_ISSUE,
                      headers={"X-GitHub-Event": "issues", "X-GitHub-Delivery": "d-1"})

    assert response.status_code == 401


def test_github_incident_issue_opens_an_investigation(client):
    c, monkeypatch = client
    queued = _with_llm_key(monkeypatch)

    response = c.post("/webhooks/github", content=_GH_ISSUE, headers={
        "X-GitHub-Event": "issues", "X-GitHub-Delivery": "d-1",
        "X-Hub-Signature-256": _gh_sig(_GH_ISSUE)})

    assert response.status_code == 200, response.text
    assert response.json()["auto_triage"] == "incident_detected"
    assert len(queued) == 1
    assert queued[0]["title"] == "GitHub issue #41: prod is down"
    assert queued[0]["service"] == "acme/api"


def test_github_deployment_failure_opens_an_investigation(client):
    c, monkeypatch = client
    queued = _with_llm_key(monkeypatch)
    body = json.dumps({
        "deployment": {"id": 9, "environment": "production"},
        "deployment_status": {"state": "failure"},
        "repository": {"full_name": "acme/api"},
    }).encode()

    response = c.post("/webhooks/github", content=body, headers={
        "X-GitHub-Event": "deployment_status", "X-GitHub-Delivery": "d-2",
        "X-Hub-Signature-256": _gh_sig(body)})

    assert response.status_code == 200, response.text
    assert response.json()["auto_triage"] == "deployment_failure_detected"
    assert len(queued) == 1
    assert "production" in queued[0]["title"]


def test_github_triage_without_an_llm_key_says_nothing_ran(client):
    """The old handler answered "auto_triage": "incident_detected" whether or
    not anything could possibly be triaged."""
    c, _ = client

    response = c.post("/webhooks/github", content=_GH_ISSUE, headers={
        "X-GitHub-Event": "issues", "X-GitHub-Delivery": "d-1",
        "X-Hub-Signature-256": _gh_sig(_GH_ISSUE)})

    assert response.status_code == 200, response.text
    body = response.json()
    assert "auto_triage" not in body
    assert "no LLM provider key" in body["auto_triage_skipped"]


def test_github_redelivery_does_not_investigate_twice(client):
    c, monkeypatch = client
    queued = _with_llm_key(monkeypatch)
    headers = {"X-GitHub-Event": "issues", "X-GitHub-Delivery": "d-1",
               "X-Hub-Signature-256": _gh_sig(_GH_ISSUE)}

    first = c.post("/webhooks/github", content=_GH_ISSUE, headers=headers)
    second = c.post("/webhooks/github", content=_GH_ISSUE, headers=headers)

    assert first.json()["auto_triage"] == "incident_detected"
    assert "duplicate" in second.json()["auto_triage_skipped"]
    assert len(queued) == 1


# --- GitLab ---------------------------------------------------------------- #
@pytest.mark.parametrize("object_kind", ["merge_request", "issue", "pipeline"])
def test_gitlab_null_object_attributes_do_not_crash(client, object_kind):
    """GitLab sends `"object_attributes": null` on some deliveries, and
    `.get(k, {})` returns None for a present-but-null key — a 500 on all three."""
    c, _ = client

    response = c.post("/webhooks/gitlab",
                      json={"object_kind": object_kind, "object_attributes": None},
                      headers={"X-Gitlab-Token": GL_SECRET})

    assert response.status_code == 200, response.text
    assert response.json()["event"] == object_kind


def test_gitlab_without_a_configured_secret_refuses_and_says_which_one(client):
    c, monkeypatch = client
    monkeypatch.setenv("GITLAB_WEBHOOK_SECRET", "")
    cfg.get_settings.cache_clear()

    response = c.post("/webhooks/gitlab", json={"object_kind": "pipeline"})

    assert response.status_code == 401
    assert "GITLAB_WEBHOOK_SECRET" in response.json()["detail"]


def test_gitlab_wrong_token_is_rejected(client):
    c, _ = client
    response = c.post("/webhooks/gitlab", json={"object_kind": "pipeline"},
                      headers={"X-Gitlab-Token": "nope"})

    assert response.status_code == 401


def test_gitlab_pipeline_failure_opens_an_investigation(client):
    c, monkeypatch = client
    queued = _with_llm_key(monkeypatch)

    response = c.post("/webhooks/gitlab", json={
        "object_kind": "pipeline",
        "project": {"path_with_namespace": "acme/web"},
        "object_attributes": {"id": 77, "status": "failed", "ref": "main"},
    }, headers={"X-Gitlab-Token": GL_SECRET})

    assert response.status_code == 200, response.text
    assert response.json()["auto_triage"] == "pipeline_failure_detected"
    assert len(queued) == 1
    assert queued[0]["service"] == "acme/web"
    assert "77" in queued[0]["title"]


def test_gitlab_incident_issue_opens_an_investigation(client):
    c, monkeypatch = client
    queued = _with_llm_key(monkeypatch)

    response = c.post("/webhooks/gitlab", json={
        "object_kind": "issue",
        "project": {"path_with_namespace": "acme/web"},
        "object_attributes": {"iid": 12, "action": "open", "title": "checkout broken"},
        "labels": [{"title": "Bug"}],
    }, headers={"X-Gitlab-Token": GL_SECRET})

    assert response.status_code == 200, response.text
    assert response.json()["auto_triage"] == "incident_detected"
    assert queued[0]["title"] == "GitLab issue #12: checkout broken"


def test_gitlab_deployment_failure_opens_an_investigation(client):
    c, monkeypatch = client
    queued = _with_llm_key(monkeypatch)

    response = c.post("/webhooks/gitlab", json={
        "object_kind": "deployment", "deployment_id": 5,
        "environment": "production", "status": "failed",
        "project": {"path_with_namespace": "acme/web"},
    }, headers={"X-Gitlab-Token": GL_SECRET})

    assert response.status_code == 200, response.text
    assert response.json()["auto_triage"] == "deployment_failure_detected"
    assert "production" in queued[0]["title"]


def test_gitlab_pipeline_success_investigates_nothing(client):
    c, monkeypatch = client
    queued = _with_llm_key(monkeypatch)

    response = c.post("/webhooks/gitlab", json={
        "object_kind": "pipeline",
        "object_attributes": {"id": 78, "status": "success", "ref": "main"},
    }, headers={"X-Gitlab-Token": GL_SECRET})

    assert response.status_code == 200
    assert "auto_triage" not in response.json()
    assert queued == []


# -----------------------------------------------------------------------------
# Reachability and authentication of the alert receivers
# -----------------------------------------------------------------------------


ALERTMANAGER_FIRING = {
    "alerts": [
        {
            "status": "firing",
            "labels": {"alertname": "HighErrorRate", "severity": "critical", "service": "api"},
            "annotations": {"description": "5xx rate above 10%"},
        }
    ]
}


def test_alert_webhooks_are_reachable_when_the_app_requires_a_token(client):
    """An alert delivery must not be rejected before it reaches its own check.

    `require_auth` skipped `/webhooks/`, but `app/webhooks/router.py` is included
    with `prefix="/api"` on top of its own `/webhooks` prefix — it lives at
    `/api/webhooks/*`. So on every deployment that set COPILOT_API_TOKEN (i.e.
    every production one) Alertmanager, Grafana and Datadog got a 401, no alert
    ever became an incident, and the product's whole ingestion path was dead.
    """
    c, monkeypatch = client
    monkeypatch.setenv("COPILOT_API_TOKEN", "prod-token")
    monkeypatch.setenv("COPILOT_WEBHOOK_SECRET", "hook-secret")
    cfg.get_settings.cache_clear()

    response = c.post(
        "/api/webhooks/alertmanager",
        json=ALERTMANAGER_FIRING,
        headers={"X-Webhook-Secret": "hook-secret"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["incident_id"]


def test_unsigned_receivers_reject_an_unauthenticated_delivery(client):
    """Opening the path prefix must not open the endpoint.

    Alertmanager, Datadog and Grafana do not sign their payloads, so once
    `require_auth` stops gating them the shared secret is the only credential
    left. Without this, anyone on the internet could fabricate incidents — and
    each one spends the operator's LLM budget on an auto-investigation.
    """
    c, monkeypatch = client
    monkeypatch.setenv("COPILOT_API_TOKEN", "prod-token")
    monkeypatch.setenv("COPILOT_WEBHOOK_SECRET", "hook-secret")
    cfg.get_settings.cache_clear()

    for path, payload in (
        ("/api/webhooks/alertmanager", ALERTMANAGER_FIRING),
        ("/api/webhooks/datadog", {"alert_type": "error", "title": "spoofed"}),
        ("/api/webhooks/grafana", {"state": "alerting", "title": "spoofed"}),
        ("/api/webhooks/generic", {"title": "spoofed"}),
    ):
        assert c.post(path, json=payload).status_code == 401, path
        assert c.post(
            path, json=payload, headers={"X-Webhook-Secret": "wrong"}
        ).status_code == 401, path


def test_a_sender_may_present_the_secret_as_bearer_or_basic_auth(client):
    """Alertmanager offers `authorization` and `basic_auth`, not a custom header."""
    c, monkeypatch = client
    monkeypatch.setenv("COPILOT_API_TOKEN", "prod-token")
    monkeypatch.setenv("COPILOT_WEBHOOK_SECRET", "hook-secret")
    cfg.get_settings.cache_clear()

    bearer = c.post(
        "/api/webhooks/grafana",
        json={"state": "alerting", "title": "bearer alert"},
        headers={"Authorization": "Bearer hook-secret"},
    )
    assert bearer.status_code == 200, bearer.text

    basic = base64.b64encode(b"alertmanager:hook-secret").decode()
    basic_response = c.post(
        "/api/webhooks/alertmanager",
        json=ALERTMANAGER_FIRING,
        headers={"Authorization": f"Basic {basic}"},
    )
    assert basic_response.status_code == 200, basic_response.text


def test_the_app_token_also_satisfies_the_receivers(client):
    """A deployment that already handed a sender its API token needs no second secret."""
    c, monkeypatch = client
    monkeypatch.setenv("COPILOT_API_TOKEN", "prod-token")
    monkeypatch.setenv("COPILOT_WEBHOOK_SECRET", "")
    cfg.get_settings.cache_clear()

    response = c.post(
        "/api/webhooks/datadog",
        json={"alert_type": "error", "title": "Datadog Alert", "alert_id": 7},
        headers={"Authorization": "Bearer prod-token"},
    )

    assert response.status_code == 200, response.text


def test_an_unauthenticated_deployment_leaves_the_receivers_open(client):
    """No token and no secret is the local-dev default, where nothing is gated."""
    c, monkeypatch = client
    monkeypatch.setenv("COPILOT_API_TOKEN", "")
    monkeypatch.setenv("COPILOT_WEBHOOK_SECRET", "")
    cfg.get_settings.cache_clear()

    response = c.post("/api/webhooks/alertmanager", json=ALERTMANAGER_FIRING)

    assert response.status_code == 200, response.text


# -----------------------------------------------------------------------------
# Alertmanager is a state feed, not an event feed
# -----------------------------------------------------------------------------


def _am(status: str, *, fingerprint: str = "fp-1", alertname: str = "HighErrorRate") -> dict:
    return {
        "alerts": [
            {
                "status": status,
                "fingerprint": fingerprint,
                "labels": {"alertname": alertname, "severity": "critical", "service": "api"},
                "annotations": {"description": "5xx rate above 10%"},
            }
        ]
    }


def _incidents(client) -> list[dict]:
    listing = client.get("/api/incidents")
    assert listing.status_code == 200, listing.text
    return listing.json()


def test_a_repeated_firing_delivery_does_not_open_a_second_incident(client):
    """Alertmanager re-sends every firing group on `repeat_interval` — 4h in the
    stock config. Each re-delivery used to open a fresh incident, so one alert
    that stayed on over a weekend produced a dozen of them."""
    c, _ = client
    first = c.post("/api/webhooks/alertmanager", json=_am("firing"))
    second = c.post("/api/webhooks/alertmanager", json=_am("firing"))

    assert first.status_code == 200 and second.status_code == 200, second.text
    assert len(_incidents(c)) == 1
    assert second.json()["incident_id"] is None
    assert "already open" in second.json()["message"]


def test_a_re_delivery_is_recorded_on_the_incident_it_matched(client):
    """Deduping must not mean discarding: "still firing" is information the
    responder needs, and it is the difference between a quiet alert and one
    nobody has fixed."""
    c, _ = client
    created = c.post("/api/webhooks/alertmanager", json=_am("firing")).json()["incident_id"]
    c.post("/api/webhooks/alertmanager", json=_am("firing"))

    timeline = _incident(c, created)["incident"]["timeline"]

    assert any(entry.get("type") == "alert_repeat" for entry in timeline), timeline
    assert any("still firing" in entry.get("message", "") for entry in timeline), timeline


def test_a_resolved_delivery_closes_the_incident_it_opened(client):
    """The matching `status: "resolved"` delivery was dropped on the floor, so
    an incident opened from an alert could only ever be closed by hand."""
    c, _ = client
    created = c.post("/api/webhooks/alertmanager", json=_am("firing")).json()["incident_id"]

    resolved = c.post("/api/webhooks/alertmanager", json=_am("resolved"))

    assert resolved.status_code == 200, resolved.text
    assert "1 resolved" in resolved.json()["message"]
    assert _incident(c, created)["incident"]["status"] == IncidentStatus.RESOLVED.value


def test_an_alert_that_fires_again_after_resolving_is_a_new_incident(client):
    """Not a reopening. Reopening would rewrite the first incident's duration
    and splice a week of silence into its postmortem timeline."""
    c, _ = client
    first = c.post("/api/webhooks/alertmanager", json=_am("firing")).json()["incident_id"]
    c.post("/api/webhooks/alertmanager", json=_am("resolved"))
    second = c.post("/api/webhooks/alertmanager", json=_am("firing")).json()["incident_id"]

    assert second and second != first
    assert len(_incidents(c)) == 2


def test_two_different_alerts_are_not_deduped_into_one(client):
    """The fingerprint is the alert's identity. Deduping on anything coarser —
    the alert name, the service — merges unrelated outages."""
    c, _ = client
    c.post("/api/webhooks/alertmanager", json=_am("firing", fingerprint="fp-a", alertname="A"))
    c.post("/api/webhooks/alertmanager", json=_am("firing", fingerprint="fp-b", alertname="B"))

    assert len(_incidents(c)) == 2


def test_a_delivery_with_no_fingerprint_still_dedupes_on_its_labels(client):
    """Older Alertmanager versions and hand-rolled senders omit `fingerprint`;
    the label set is what the fingerprint is derived from anyway."""
    c, _ = client
    payload = {"alerts": [{"status": "firing", "labels": {"alertname": "X", "service": "api"}}]}
    c.post("/api/webhooks/alertmanager", json=payload)
    c.post("/api/webhooks/alertmanager", json=payload)

    assert len(_incidents(c)) == 1


def test_a_resolved_delivery_for_an_unknown_alert_is_harmless(client):
    """Alertmanager sends resolutions for alerts that fired before this service
    existed, or before the dedupe key column did."""
    c, _ = client
    response = c.post("/api/webhooks/alertmanager", json=_am("resolved", fingerprint="never-seen"))

    assert response.status_code == 200, response.text
    assert _incidents(c) == []


# -----------------------------------------------------------------------------
# Malformed deliveries are a 400, not a 500
# -----------------------------------------------------------------------------


@pytest.mark.parametrize("path", [
    "/api/webhooks/alertmanager",
    "/api/webhooks/datadog",
    "/api/webhooks/grafana",
    "/api/webhooks/generic",
])
def test_a_top_level_array_is_refused_not_crashed(client, path):
    """Every receiver opens with `data.get(...)`, which is an AttributeError —
    a 500 — for anything that is not an object. The sender reads a 500 as
    "retry", so one malformed template became a retry loop into the same crash.
    A 400 is both accurate and terminal."""
    c, _ = client
    response = c.post(path, json=[{"title": "not an object"}])

    assert response.status_code == 400, f"{path}: {response.status_code}"
    assert "object" in response.json()["detail"].lower()


@pytest.mark.parametrize("path", [
    "/api/webhooks/alertmanager",
    "/api/webhooks/datadog",
    "/api/webhooks/grafana",
    "/api/webhooks/generic",
])
def test_a_bare_json_scalar_is_refused_not_crashed(client, path):
    c, _ = client
    assert c.post(path, json="just a string").status_code == 400
    assert c.post(path, json=7).status_code == 400


@pytest.mark.parametrize("path", [
    "/api/webhooks/alertmanager",
    "/api/webhooks/datadog",
    "/api/webhooks/grafana",
    "/api/webhooks/generic",
])
def test_a_body_that_is_not_json_at_all_is_refused_not_crashed(client, path):
    c, _ = client
    response = c.post(path, content=b"<html>gateway error</html>",
                      headers={"Content-Type": "application/json"})

    assert response.status_code == 400, f"{path}: {response.status_code}"


def test_alertmanager_null_alerts_is_not_a_crash(client):
    """`"alerts": null` is what a template renders for a group that emptied out
    between the decision to notify and the render. `.get("alerts", [])` does not
    help — the default only applies when the key is missing."""
    c, _ = client
    response = c.post("/api/webhooks/alertmanager", json={"alerts": None})

    assert response.status_code == 200, response.text
    assert response.json()["incident_id"] is None


def test_alertmanager_null_labels_and_annotations_are_not_a_crash(client):
    """An alert with no labels renders as `null`, not `{}`."""
    c, _ = client
    response = c.post("/api/webhooks/alertmanager", json={
        "alerts": [{"status": "firing", "labels": None, "annotations": None}]
    })

    assert response.status_code == 200, response.text
    assert _incident(c, response.json()["incident_id"])["incident"]["title"] == "Prometheus Alert"


def test_alertmanager_non_object_entries_in_alerts_are_skipped(client):
    c, _ = client
    response = c.post("/api/webhooks/alertmanager", json={
        "alerts": ["oops", None, {"status": "firing", "labels": {"alertname": "real"}}]
    })

    assert response.status_code == 200, response.text
    assert len(_incidents(c)) == 1


def test_generic_null_fields_fall_through_to_the_next_candidate(client):
    """A templating layer emits `"title": null` for an unset variable far more
    often than it omits the key, and a `.get` default never applies to null."""
    c, _ = client
    response = c.post("/api/webhooks/generic", json={
        "title": None, "name": None, "alert": "disk pressure",
        "service": None, "tags": None, "severity": None,
    })

    assert response.status_code == 200, response.text
    detail = _incident(c, response.json()["incident_id"])["incident"]
    assert detail["title"] == "disk pressure"
    assert detail["service"] == "unknown"


def test_generic_scalar_tags_do_not_crash(client):
    """`tags` arrives as a bare string from half the senders that set it."""
    c, _ = client
    response = c.post("/api/webhooks/generic", json={"title": "t", "tags": "prod"})

    assert response.status_code == 200, response.text
    assert "prod" in _incident(c, response.json()["incident_id"])["incident"]["tags"]


def test_datadog_and_grafana_null_tags_do_not_crash(client):
    c, _ = client
    datadog = c.post("/api/webhooks/datadog", json={
        "alert_type": "error", "title": "dd", "tags": None,
    })
    grafana = c.post("/api/webhooks/grafana", json={
        "state": "alerting", "title": "gf", "tags": None,
    })

    assert datadog.status_code == 200, datadog.text
    assert grafana.status_code == 200, grafana.text


def test_pagerduty_signed_non_object_body_is_refused_not_crashed(client):
    """The signature check passes — the body is genuinely from PagerDuty — and
    the parse is what breaks. A 500 here is a retry loop from a signed sender."""
    c, _ = client
    body = json.dumps(["not", "an", "object"]).encode()

    response = c.post("/api/webhooks/pagerduty", content=body,
                      headers={"X-PagerDuty-Signature": _pd_sig(body),
                               "Content-Type": "application/json"})

    assert response.status_code == 400, response.text


def test_pagerduty_legacy_messages_with_junk_entries_are_skipped(client):
    c, _ = client
    body = json.dumps({"messages": ["oops", None, {
        "event": "incident.trigger",
        "incident": {"id": "PX", "title": "real one", "service": None},
    }]}).encode()

    response = c.post("/api/webhooks/pagerduty", content=body,
                      headers={"X-PagerDuty-Signature": _pd_sig(body),
                               "Content-Type": "application/json"})

    assert response.status_code == 200, response.text
    assert _incident(c, response.json()["incident_id"])["incident"]["service"] == "unknown"
