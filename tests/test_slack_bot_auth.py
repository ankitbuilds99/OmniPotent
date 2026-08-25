"""The Slack bot router must be reachable BY SLACK and by nobody else.

Slack signs every request it sends with `X-Slack-Signature` and never sends an
`Authorization` header. Two failure modes are possible and both are real bugs:

  * requiring the shared bearer token on /api/slack/* — then Slack itself gets a
    401 and the whole integration is dead in production, while the signature gate
    that was supposed to protect it never runs;
  * dropping the signature gate — then anyone on the internet can post to routes
    that open incidents, page on-call and kick off investigations.

These tests pin both ends: a correctly signed request with NO bearer must reach
the handler, and an unsigned request WITH a valid bearer must still be refused.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest
from fastapi.testclient import TestClient

import app.api.main as api
import app.config as cfg
from app import runtime

SECRET = "s3cr3t"
TOKEN = "prodtoken"

# Every route the Slack bot router serves. If a new one is added without the
# router-level signature dependency, test_every_slack_route_rejects_unsigned
# catches it.
SLACK_ROUTES = [
    "/api/slack/events",
    "/api/slack/interactivity",
    "/api/slack/commands/rca",
    "/api/slack/commands/status",
    "/api/slack/commands/oncall",
    "/api/slack/commands/incident",
    "/api/slack/commands/warroom",
    "/api/slack/commands/postmortem",
    "/api/slack/commands/stakeholder",
    "/api/slack/commands/help",
]


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("COPILOT_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/slack.db")
    # Production posture: the shared API token IS set, so require_auth is live.
    monkeypatch.setenv("COPILOT_API_TOKEN", TOKEN)
    monkeypatch.setenv("SLACK_SIGNING_SECRET", SECRET)
    cfg.get_settings.cache_clear()
    api._RL.clear()
    api._reset_limiter()
    runtime.reset()
    with TestClient(api.app, raise_server_exceptions=False) as c:
        yield c, monkeypatch
    cfg.get_settings.cache_clear()
    api._RL.clear()
    api._reset_limiter()
    runtime.reset()


def _signed(body: bytes, secret: str = SECRET, ts: str | None = None) -> dict[str, str]:
    ts = ts or str(int(time.time()))
    base = b"v0:" + ts.encode() + b":" + body
    return {
        "X-Slack-Request-Timestamp": ts,
        "X-Slack-Signature": "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest(),
    }


def test_signed_request_without_bearer_reaches_the_handler(client):
    """What Slack actually sends: a signature and no Authorization header."""
    c, _ = client
    body = json.dumps({"type": "url_verification", "challenge": "abc123"}).encode()
    r = c.post(
        "/api/slack/events",
        content=body,
        headers={**_signed(body), "Content-Type": "application/json"},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"challenge": "abc123"}


def test_signed_slash_command_without_bearer_reaches_the_handler(client):
    c, _ = client
    form = (
        b"command=%2Fincident&text=&response_url=https%3A%2F%2Fhooks.slack.com%2Fx"
        b"&trigger_id=t&user_id=U1&user_name=n&channel_id=C1&channel_name=cn"
        b"&team_id=T1&team_domain=d"
    )
    r = c.post(
        "/api/slack/commands/incident",
        content=form,
        headers={**_signed(form), "Content-Type": "application/x-www-form-urlencoded"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["response_type"] == "ephemeral"


@pytest.mark.parametrize("path", SLACK_ROUTES)
def test_every_slack_route_rejects_unsigned(client, path):
    """A valid bearer is NOT a substitute for a Slack signature."""
    c, _ = client
    r = c.post(
        path,
        content=b"payload=%7B%7D",
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "X-Slack-Request-Timestamp": str(int(time.time())),
            "X-Slack-Signature": "v0=deadbeef",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    assert r.status_code == 401, f"{path} -> {r.status_code} {r.text}"
    assert r.json()["detail"] == "Invalid Slack signature"


@pytest.mark.parametrize("path", SLACK_ROUTES)
def test_every_slack_route_rejects_a_bare_request(client, path):
    """No signature headers at all: still refused, never a 200."""
    c, _ = client
    r = c.post(path, content=b"{}", headers={"Content-Type": "application/json"})
    assert r.status_code == 401, f"{path} -> {r.status_code} {r.text}"


def test_replayed_signature_is_rejected(client):
    """Correct HMAC, stale timestamp — outside the 5 minute window."""
    c, _ = client
    body = json.dumps({"type": "url_verification", "challenge": "abc123"}).encode()
    old = str(int(time.time()) - 3600)
    r = c.post(
        "/api/slack/events",
        content=body,
        headers={**_signed(body, ts=old), "Content-Type": "application/json"},
    )
    assert r.status_code == 401
    assert r.json()["detail"] == "Invalid Slack signature"


def test_signature_from_the_wrong_secret_is_rejected(client):
    c, _ = client
    body = json.dumps({"type": "url_verification", "challenge": "abc123"}).encode()
    r = c.post(
        "/api/slack/events",
        content=body,
        headers={**_signed(body, secret="not-the-secret"), "Content-Type": "application/json"},
    )
    assert r.status_code == 401


def test_unconfigured_secret_fails_closed(client):
    """No SLACK_SIGNING_SECRET must mean 503, never 'accept everything'."""
    c, mp = client
    mp.setenv("SLACK_SIGNING_SECRET", "")
    cfg.get_settings.cache_clear()
    body = json.dumps({"type": "url_verification", "challenge": "abc123"}).encode()
    r = c.post(
        "/api/slack/events",
        content=body,
        headers={**_signed(body), "Content-Type": "application/json"},
    )
    assert r.status_code == 503
    assert "not configured" in r.json()["detail"]


def test_the_auth_exemption_is_scoped_to_the_slack_router(client):
    """Exempting /api/slack/ must not leak into neighbouring /api routes."""
    c, _ = client
    r = c.get("/api/investigations")
    assert r.status_code == 401
    assert r.json()["detail"] == "Missing or invalid API token."
