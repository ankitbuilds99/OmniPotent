"""API surface tests via FastAPI TestClient — no LLM keys or MCP subprocesses
needed for any of these (they exercise probes, validation, auth, and the
production-hardening middleware, which all run before the agent is invoked)."""

import asyncio

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import app.api.main as api
import app.config as cfg
from app import investigation_history, runtime
from app.database import engine as db_engine


@pytest.fixture
def client():
    cfg.get_settings.cache_clear()
    api._RL.clear()
    runtime.reset()
    yield TestClient(api.app)
    cfg.get_settings.cache_clear()
    api._RL.clear()
    runtime.reset()


@pytest.fixture
def investigated(tmp_path, monkeypatch):
    """A client backed by a real investigations table, plus a way to fill it.

    /remediate no longer believes a confidence tier declared in the request body;
    it reads the tier off the stored investigation named by `thread_id`. So these
    tests need a store that actually has rows in it: a tmp SQLite file, and the
    app entered as a context manager so the lifespan's `init_db` creates the
    schema. Yields `(client, seed)`, where `seed(thread_id, **report)` writes one
    investigation under the same org key `_history_org_id()` reads back.
    """
    monkeypatch.setenv("COPILOT_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/app.db")
    monkeypatch.setenv("COPILOT_AUTONOMY", "true")
    cfg.get_settings.cache_clear()
    api._RL.clear()
    runtime.reset()
    db_engine._engine = None
    db_engine._session_factory = None

    def seed(thread_id: str, **report) -> None:
        asyncio.run(
            investigation_history.record(
                thread_id, "why are we 500ing?", status="completed",
                summary="root cause", report=report,
            )
        )

    with TestClient(api.app) as started:  # `with` runs lifespan, which runs init_db
        yield started, seed

    cfg.get_settings.cache_clear()
    api._RL.clear()
    runtime.reset()
    db_engine._engine = None
    db_engine._session_factory = None


# --- probes / read-only endpoints ---------------------------------------- #
def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert "active_sessions" in r.json()


def test_readyz_ready_in_dev(client):
    r = client.get("/readyz")
    assert r.status_code == 200
    assert r.json()["status"] == "ready"


def test_config(client):
    r = client.get("/config")
    assert r.status_code == 200
    body = r.json()
    for key in ("provider", "model", "fast_model", "servers", "github"):
        assert key in body


def test_metrics(client):
    r = client.get("/metrics")
    assert r.status_code == 200
    assert "services" in r.json()


def test_usage_single_tenant_reports_disabled(client):
    r = client.get("/usage")
    assert r.status_code == 200
    assert r.json()["multi_tenant"] is False


def test_audit_verify_endpoint(client):
    r = client.get("/audit/verify")
    assert r.status_code == 200
    assert "valid" in r.json()


def test_webhook_delivery_idempotency():
    api._SEEN_DELIVERIES.clear()
    assert api._claim_delivery(b"delivery-1") is True
    assert api._claim_delivery(b"delivery-1") is False  # redelivery deduped
    assert api._claim_delivery(b"delivery-2") is True


def test_request_id_header_on_every_response(client):
    assert client.get("/healthz").headers.get("x-request-id")


# --- validation ----------------------------------------------------------- #
def test_model_configure_bad_provider(client):
    assert client.post("/model/configure", json={"provider": "bogus"}).status_code == 400


def test_github_connect_bad_body(client):
    assert client.post("/github/connect", json={"token": "", "repo": "noslash"}).status_code == 400


def test_normalize_repo_accepts_urls_and_owner_repo():
    assert api._normalize_repo("https://github.com/acme/app") == "acme/app"
    assert api._normalize_repo("https://github.com/acme/app.git") == "acme/app"
    assert api._normalize_repo("https://github.com/acme/app/tree/main") == "acme/app"
    assert api._normalize_repo("git@github.com:acme/app.git") == "acme/app"
    assert api._normalize_repo("acme/app") == "acme/app"
    assert api._normalize_repo("  acme/app/  ") == "acme/app"


def test_signup_requires_multi_tenant(client):
    # Self-serve signup is a no-op in single-tenant mode (the offline demo default).
    assert client.post("/signup", json={"org_name": "X", "email": "a@b.com"}).status_code == 400


def test_empty_message_rejected(client):
    assert client.post("/chat", json={"thread_id": "t", "message": "   "}).status_code == 400


def test_message_too_long(client, monkeypatch):
    monkeypatch.setenv("COPILOT_MAX_MESSAGE_CHARS", "10")
    cfg.get_settings.cache_clear()
    r = client.post("/chat", json={"thread_id": "t", "message": "x" * 50})
    assert r.status_code == 413


def test_body_too_large(client, monkeypatch):
    monkeypatch.setenv("COPILOT_MAX_BODY_BYTES", "20")
    cfg.get_settings.cache_clear()
    r = client.post("/chat", json={"thread_id": "t", "message": "a reasonably long body here"})
    assert r.status_code == 413


# --- middleware: rate limiting ------------------------------------------- #
def test_rate_limit_returns_429(client, monkeypatch):
    monkeypatch.setenv("COPILOT_RATE_LIMIT_PER_MIN", "2")
    cfg.get_settings.cache_clear()
    api._RL.clear()
    codes = [client.post("/model/configure", json={"provider": "bogus"}).status_code for _ in range(4)]
    assert 429 in codes  # counted before routing, so even 400s trip the limiter


# --- auth ----------------------------------------------------------------- #
def test_auth_required_when_token_set(client, monkeypatch):
    monkeypatch.setenv("COPILOT_API_TOKEN", "secret")
    cfg.get_settings.cache_clear()
    assert client.get("/config").status_code == 401
    assert client.get("/config", headers={"Authorization": "Bearer secret"}).status_code == 200
    assert client.get("/config", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/healthz").status_code == 200  # probe is exempt


# --- source-path lockdown (unit) ----------------------------------------- #
def test_source_lockdown_rejects_outside_root():
    with pytest.raises(HTTPException) as ei:
        api._set_source("/etc", "repo", lambda p: None)
    assert ei.value.status_code in (403, 404)


# --- friendly error coercion --------------------------------------------- #
def test_friendly_error_branches():
    assert "rate limit" in api._friendly_error(Exception("429 rate_limit")).lower()
    assert "key" in api._friendly_error(Exception("401 authentication error")).lower()
    assert "went wrong" in api._friendly_error(Exception("boom")).lower()


# --- autonomous remediation (#4) ------------------------------------------ #
# The confidence tier is read server-side from the stored investigation named by
# `thread_id` (`_investigation_confidence`), never from the request body — the
# old body field was the entire safety check, so typing "high" authorised a
# cluster write. These post no `confidence` because a body that declares one is
# now refused; that refusal has a test of its own below.
def test_remediate_forbidden_when_autonomy_disabled(client):
    r = client.post("/remediate", json={
        "action": "rollback_deployment", "target": "checkout-svc", "thread_id": "t-1"})
    assert r.status_code == 403


def test_remediate_rejects_non_reversible_action(client, monkeypatch):
    monkeypatch.setenv("COPILOT_AUTONOMY", "true")
    cfg.get_settings.cache_clear()
    r = client.post("/remediate", json={
        "action": "scale_deployment", "target": "checkout-svc", "thread_id": "t-1"})
    assert r.status_code == 400
    assert "reversible" in r.json()["detail"]


def test_remediate_dry_run_when_enabled(investigated):
    client, seed = investigated  # dry-run defaults on
    seed("inv-high", confidence="high", root_cause="bad deploy")

    r = client.post("/remediate", json={
        "action": "restart_deployment", "target": "checkout-svc", "thread_id": "inv-high"})

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "dry_run"
    # The tier and where it was read from travel back with the result: that pair
    # is the audit trail separating an authorised remediation from a guessed one.
    assert body["confidence"] == "high"
    assert "inv-high" in body["confidence_source"]


def test_remediate_refuses_a_body_declared_confidence(investigated):
    """Even with a genuinely high-confidence investigation behind it.

    The point of the fix is that the body field is not a credential, so it is
    refused rather than ignored — an integration that keeps sending it is told
    what now authorises the write instead of being silently overruled.
    """
    client, seed = investigated
    seed("inv-high", confidence="high")

    r = client.post("/remediate", json={
        "action": "restart_deployment", "target": "checkout-svc",
        "thread_id": "inv-high", "confidence": "high"})

    assert r.status_code == 400
    assert "confidence" in r.json()["detail"]
    assert "thread_id" in r.json()["detail"]


def test_remediate_requires_high_confidence(investigated):
    client, seed = investigated
    seed("inv-low", confidence="low")

    r = client.post("/remediate", json={
        "action": "restart_deployment", "target": "checkout-svc", "thread_id": "inv-low"})

    assert r.status_code == 400
    assert "'low'" in r.json()["detail"]


def test_remediate_uses_the_calibrated_tier_not_the_self_reported_one(investigated):
    """A model that called itself confident cannot outrank its own calibration."""
    client, seed = investigated
    seed("inv-downgraded", confidence="high", calibrated_confidence="medium")

    r = client.post("/remediate", json={
        "action": "restart_deployment", "target": "checkout-svc",
        "thread_id": "inv-downgraded"})

    assert r.status_code == 400
    assert "'medium'" in r.json()["detail"]


@pytest.mark.parametrize(
    "thread_id, expected",
    [("", "no thread_id"), ("nonexistent", "no investigation 'nonexistent'")],
)
def test_remediate_refuses_when_no_tier_was_measured(investigated, thread_id, expected):
    """Unmeasured is reported as unmeasured, not silently demoted to 'low'.

    "nothing measured a confidence" and "we measured it and it was weak" are
    different facts, and the refusal has to say which one happened.
    """
    client, _seed = investigated

    r = client.post("/remediate", json={
        "action": "restart_deployment", "target": "checkout-svc", "thread_id": thread_id})

    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "no investigation confidence was measured" in detail
    assert expected in detail


def test_global_spend_cap_blocks_new_investigation(client, monkeypatch):
    monkeypatch.setenv("COPILOT_GLOBAL_TOKEN_CAP", "1000")
    cfg.get_settings.cache_clear()

    class _Full:
        async def total(self):
            return 5000  # already over the cap

        async def record(self, tokens):
            return 5000

    monkeypatch.setattr(api, "_SPEND", _Full())
    r = client.post("/chat", json={"thread_id": "t", "message": "why 500s?"})
    assert r.status_code == 429
    assert "budget" in r.json()["detail"].lower()
    monkeypatch.setattr(api, "_SPEND", None)


def test_me_single_tenant_returns_operator(client):
    r = client.get("/me")
    assert r.status_code == 200
    body = r.json()
    assert body["multi_tenant"] is False
    assert body["authenticated"] is True
    assert body["label"] == "Operator"
