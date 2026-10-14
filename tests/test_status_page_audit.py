"""The status page's emailed verification link, on a deployment that has auth on.

`GET /api/status/subscribe/verify` is the URL the verification email carries, and
it is opened from a mail client, which sends no Authorization header. The API app
gates every route with `require_auth`, so on any deployment with COPILOT_API_TOKEN
set — which production always has — that click was answered 401 before the handler
ran. The subscription stayed unverified forever, `notify_subscribers` skips
unverified subscribers, and the response to the subscribe call had said "check
your email".

The repair is app/api/main.py's `_is_public_status_page` exemption, so most of what
is worth pinning here is a property of the *app*, not of this router. An earlier
version of this file monkeypatched that exemption into place before asserting the
loop worked, and subtracted it before asserting the loop was refused — which meant
every assertion held whether or not the app was ever repaired, and no test in the
repo failed while the defect was live. These tests instead drive the real app with
its real allowlist: the end-to-end loop below fails the moment that exemption is
weakened, and the tests that need the old, gated deployment reproduce it by
neutralising the exemption at runtime rather than by asserting a doctored one.

The second half is this router's own guard: it refuses an email subscription whose
verification link its deployment's auth gate would reject, instead of banking an
address and mailing a dead link. Pinned here are the cases where that guard must
*not* fire (a status page served from another host, a probe that cannot run) and
what it is allowed to say when it does — the subscribe form is anonymous and the
frontend renders `detail` verbatim, so the diagnosis belongs in the log.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
from fastapi import Depends, HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

import app.api.main as api
import app.config as cfg
import app.status_page.api_router as status_api
from app import runtime, tenant_context
from app.database import engine as db_engine

TOKEN = "secret-token"
VERIFY_PATH = "/api/status/subscribe/verify"


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A single-tenant deployment with the shared bearer token configured."""
    monkeypatch.setenv("COPILOT_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/status.db")
    monkeypatch.setenv("COPILOT_API_TOKEN", TOKEN)
    monkeypatch.setenv("COPILOT_MULTI_TENANT", "false")
    # Production, because that is the deployment the finding is about: the gate is
    # live only where a credential is configured.
    monkeypatch.setenv("COPILOT_ENV", "production")
    monkeypatch.setenv("COPILOT_SECRET_KEY", "0" * 32)
    monkeypatch.setenv("COPILOT_CHECKPOINT_DB", "postgresql://localhost/test")
    monkeypatch.setenv("COPILOT_REDIS_URL", "redis://localhost/0")
    cfg.get_settings.cache_clear()
    tenant_context.current_tenant.set(None)
    api._RL.clear()
    api._reset_limiter()
    runtime.reset()
    db_engine._engine = None
    db_engine._session_factory = None

    with TestClient(api.app) as test_client:
        yield test_client

    cfg.get_settings.cache_clear()
    tenant_context.current_tenant.set(None)
    api._RL.clear()
    api._reset_limiter()
    runtime.reset()
    db_engine._engine = None
    db_engine._session_factory = None


@pytest.fixture
def sent(monkeypatch):
    """A working email transport that records what it was asked to deliver."""
    delivered: list[tuple[str, str]] = []

    async def _send(email: str, verify_url: str) -> bool:
        delivered.append((email, verify_url))
        return True

    monkeypatch.setattr(status_api, "email_delivery_configured", lambda: True)
    monkeypatch.setattr(status_api, "send_verification_email", _send)
    return delivered


def _auth() -> dict:
    return {"Authorization": f"Bearer {TOKEN}"}


def _regate_the_status_page(monkeypatch, replacement=None) -> dict:
    """Put this deployment back in the state the audit found it in.

    Neutralises app/api/main.py's public-status-page exemption in the running
    process, so `require_auth` refuses an anonymous click exactly as it did before
    the repair. Nothing on disk is touched, and if that exemption is ever renamed
    or deleted `monkeypatch.setattr` raises rather than quietly leaving the test
    asserting against a deployment that no longer exists.

    Returns a handle whose "exempt" flag puts the real exemption back mid-test —
    `monkeypatch.undo()` cannot be used for that, as the same monkeypatch instance
    also holds this module's fixtures together.
    """
    real = api._is_public_status_page
    switch = {"exempt": False}

    def _gate(method: str, path: str) -> bool:
        if not switch["exempt"]:
            return False if replacement is None else replacement(method, path)
        return real(method, path)

    monkeypatch.setattr(api, "_is_public_status_page", _gate)
    return switch


def _subscribe(client, email="reader@example.com", **kwargs):
    return client.post("/api/status/subscribe", json={"email": email}, **kwargs)


# --- The app-level repair: the emailed link is openable by a mail client ---


def test_an_anonymous_visitor_can_subscribe_and_open_the_emailed_link(client, sent):
    """The whole loop, over HTTP, with no credential and nothing monkeypatched.

    This is the assertion the audit finding was missing: no fixture opens the
    path, so it holds only while the real app really does let an anonymous GET of
    the verification route through.
    """
    created = _subscribe(client)
    assert created.status_code == 201, created.text
    assert created.json()["verified"] is False
    assert len(sent) == 1
    address, link = sent[0]
    assert address == "reader@example.com"
    assert VERIFY_PATH in link

    # Opened the way a mail client opens it: no Authorization header at all.
    opened = client.get(link)
    assert opened.status_code == 200, opened.text
    assert opened.json()["status"] == "verified"

    # And the row really is verified now, so an incident would reach it.
    again = _subscribe(client)
    assert again.json()["verified"] is True
    assert again.json()["message"] == "Already subscribed."
    assert len(sent) == 1


def test_an_anonymous_click_reaches_the_handler_rather_than_the_auth_gate(client):
    """A bad link must fail on the token, not on the missing bearer.

    401 here is the audited bug; 400 means `verify_subscription` itself ran and
    rejected the made-up single-use token, which is the only credential that
    route has.
    """
    clicked = client.get(f"{VERIFY_PATH}?id=nope&token=nope")
    assert clicked.status_code == 400, clicked.text
    assert clicked.json()["detail"] == "Invalid verification link"


def test_the_apps_exemption_is_the_only_thing_carrying_that_click(client, monkeypatch):
    """Shows the two tests above are load-bearing and not vacuously green.

    With the exemption neutralised the same request is 401 again — the original
    defect, reproduced in-process. So if app/api/main.py loses that exemption,
    those tests fail rather than keep passing.
    """
    _regate_the_status_page(monkeypatch)
    assert client.get(f"{VERIFY_PATH}?id=nope&token=nope").status_code == 401


def test_opening_the_status_page_did_not_open_its_admin_writes(client):
    """The exemption is scoped; the write routes on the same prefix stay shut."""
    assert client.post("/api/status/components", json={"name": "db"}).status_code == 401
    assert client.post("/api/status/refresh").status_code == 401
    assert client.delete("/api/status/subscribe/abc").status_code == 401
    # A credential that is presented still has to be a valid one: the exemption
    # applies only to a caller carrying none.
    wrong = client.get("/api/status", headers={"Authorization": "Bearer wrong"})
    assert wrong.status_code == 401


# --- This router's guard: never bank an address whose link cannot be opened ---


def test_a_working_deployment_is_not_refused_by_its_own_guard(client, sent):
    """The guard measures the live gate, so on a repaired app it stays quiet."""
    assert _subscribe(client, headers=_auth()).status_code == 201
    assert len(sent) == 1


def test_email_subscription_is_refused_while_the_link_is_unreachable(
    client, sent, monkeypatch
):
    """On a deployment whose gate refuses the click, refuse instead of mailing."""
    switch = _regate_the_status_page(monkeypatch)

    response = _subscribe(client, headers=_auth())
    assert response.status_code == 503, response.text

    # Nothing was mailed, and no address was kept: an unverifiable row is exactly
    # the state this endpoint exists to avoid. Proved over HTTP — if the refused
    # attempt had stored anything, the retry below would come back "already
    # subscribed" with no second email instead of creating a fresh subscription.
    assert sent == []
    switch["exempt"] = True
    retry = _subscribe(client, headers=_auth())
    assert retry.status_code == 201, retry.text
    assert retry.json()["message"].startswith("Subscription created")
    assert len(sent) == 1


def test_the_refusal_tells_the_operator_everything_and_the_stranger_nothing(
    client, sent, monkeypatch, caplog
):
    """`detail` is rendered verbatim in the subscribe form by the frontend.

    The form is anonymous, so the response may not name source files or internal
    symbols; the operator, who is the only one who can act on it, reads the log.
    """
    _regate_the_status_page(monkeypatch)
    with caplog.at_level(logging.ERROR, logger="omnipotent.status_page.api"):
        detail = _subscribe(client, headers=_auth()).json()["detail"]

    assert "app/api/main.py" not in detail
    assert "require_auth" not in detail
    assert "_PUBLIC_PATHS" not in detail and "_OPEN_PATHS" not in detail
    assert "401" not in detail
    # Still an honest answer to the person who submitted it: not stored, not sent.
    assert "not stored" in detail and "temporarily unavailable" in detail

    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert VERIFY_PATH in logged
    assert "app/api/main.py" in logged
    assert "401" in logged and "Missing or invalid API token" in logged


def test_a_status_page_served_from_another_host_is_not_refused(
    client, sent, monkeypatch
):
    """The local gate is not evidence about a link this app does not answer.

    With `status_page_url` pointing elsewhere the mailed link is served by that
    host — possibly by an edge that supplies the credential — so a local 401 says
    nothing, and refusing on it would break a subscription loop that works.
    """
    monkeypatch.setenv("STATUS_PAGE_URL", "https://status.example.com")
    cfg.get_settings.cache_clear()
    _regate_the_status_page(monkeypatch)

    created = _subscribe(client, headers=_auth())
    assert created.status_code == 201, created.text
    assert len(sent) == 1
    assert sent[0][1].startswith(f"https://status.example.com{VERIFY_PATH}?")


def test_a_probe_that_cannot_run_does_not_kill_email_subscriptions(
    client, sent, monkeypatch, caplog
):
    """An erroring gate is unmeasured, not a refusal, and is reported as itself."""

    def _explode(*_args):
        raise RuntimeError("gate blew up on the synthetic connection")

    _regate_the_status_page(monkeypatch, replacement=_explode)
    with caplog.at_level(logging.WARNING, logger="omnipotent.status_page.api"):
        created = _subscribe(client, headers=_auth())

    assert created.status_code == 201, created.text
    assert len(sent) == 1
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "could not run" in logged
    assert any(r.exc_info for r in caplog.records), "the cause has to be recoverable"


def test_webhook_subscriptions_are_untouched_by_the_check(client, sent, monkeypatch):
    """Webhooks carry their own credential (the URL) and are never emailed."""
    _regate_the_status_page(monkeypatch)
    response = client.post(
        "/api/status/subscribe",
        json={"webhook_url": "https://hooks.example.com/status"},
        headers=_auth(),
    )
    assert response.status_code == 201, response.text
    assert response.json()["verified"] is True
    assert sent == []


async def test_the_diagnosis_quotes_whichever_gate_actually_refused(monkeypatch):
    """The reason is read off the live gate, not restated from this router.

    A multi-tenant deployment's `require_auth` refuses a credential-less
    connection with different wording ("Invalid or revoked credential.") than the
    single-tenant one every test above drives, and reaching that branch over HTTP
    needs a real tenant key. Driving the probe directly against a gate that speaks
    it shows the diagnosis carries whatever the gate said — and that none of it
    can reach the subscriber, whose message is a fixed string with no
    interpolation at all.
    """

    async def _multi_tenant_gate(_conn):
        raise HTTPException(401, "Invalid or revoked credential.")

    monkeypatch.delenv("STATUS_PAGE_URL", raising=False)
    cfg.get_settings.cache_clear()
    monkeypatch.setattr(api, "require_auth", _multi_tenant_gate)
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/status/subscribe",
            "root_path": "",
            "scheme": "http",
            "server": ("testserver", 80),
            "query_string": b"",
            "headers": [(b"host", b"testserver")],
            # What the probe looks for before it trusts its own verdict: this app
            # really is gated by the `require_auth` it is about to call.
            "app": SimpleNamespace(
                router=SimpleNamespace(dependencies=[Depends(_multi_tenant_gate)])
            ),
        }
    )

    diagnosis = await status_api._verification_link_rejected(request)

    assert diagnosis is not None
    assert "Invalid or revoked credential." in diagnosis
    assert VERIFY_PATH in diagnosis
    assert "credential" not in status_api._EMAIL_SUBSCRIPTIONS_UNAVAILABLE
