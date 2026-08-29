"""Three gates that live in app/api/main.py and are only observable over HTTP.

Each of them was a hole that no test could see, because each is a property of
the *app* rather than of the router or the service underneath it:

  * `require_auth`'s exemption list decides whether the status page — the page
    customers are sent to when the product is down — and the emailed
    verification link a mail client clicks are reachable without a credential.
  * `POST /webhooks/stripe` is the only mounted Stripe receiver, so it is the
    only place a payment can be filed into `billing_subscriptions`.
  * `POST /remediate` decides where a remediation's confidence tier comes from,
    and the answer must not be "whatever the request body said".

These drive the real ASGI app so they fail if the wiring is undone, not just if
the helper functions change shape.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

import app.api.main as api
import app.config as cfg
from app import runtime, tenant_context
from app.billing import runtime as billing_runtime
from app.billing.api import BillingUnavailableError
from app.database import engine as db_engine

TOKEN = "secret-token"
WHSEC = "whsec_owner_test"
VERIFY_PATH = "/api/status/subscribe/verify"


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


def _reset() -> None:
    cfg.get_settings.cache_clear()
    tenant_context.current_tenant.set(None)
    api._RL.clear()
    api._reset_limiter()
    runtime.reset()
    db_engine._engine = None
    db_engine._session_factory = None


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Production posture: a shared bearer is configured, so require_auth is live."""
    monkeypatch.setenv("COPILOT_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/owner.db")
    monkeypatch.setenv("COPILOT_API_TOKEN", TOKEN)
    monkeypatch.setenv("COPILOT_MULTI_TENANT", "false")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", WHSEC)
    _reset()
    with TestClient(api.app) as started:  # `with` runs lifespan, which runs init_db
        yield started
    _reset()


# --- the status page's public half ---------------------------------------- #
# A status page nobody outside the tenant can read is not a status page, and the
# verification link in a subscription email is opened by a mail client that holds
# no Authorization header at all — the single-use random token in that link's
# query string is the route's whole credential. Every one of these answered 401
# on any deployment with COPILOT_API_TOKEN set.
def test_an_anonymous_mail_client_can_open_the_verification_link(client):
    r = client.get(f"{VERIFY_PATH}?id=abc&token=xyz")

    # 400 from the handler's own token check, not 401 from the app gate: the
    # click reached the route, and the token in the link is what decides it.
    assert r.status_code == 400, r.text
    assert "verification" in r.json()["detail"].lower()


@pytest.mark.parametrize("path", ["/api/status", "/api/status/components", "/api/status/incidents"])
def test_an_anonymous_visitor_can_read_the_status_page(client, path):
    assert client.get(path).status_code == 200


def test_an_anonymous_visitor_can_reach_the_subscribe_endpoint(client):
    r = client.post("/api/status/subscribe", json={"webhook_url": "https://example.com/hook"})

    assert r.status_code == 201, r.text


@pytest.mark.parametrize(
    "method, path, body",
    [
        ("POST", "/api/status/refresh", None),
        ("POST", "/api/status/components", {"name": "api"}),
        ("POST", "/api/status/incidents", {"title": "x"}),
        ("DELETE", "/api/status/components/abc", None),
        ("DELETE", "/api/status/subscribe/abc", None),
    ],
)
def test_the_status_routers_writes_are_still_closed(client, method, path, body):
    """Only reads and the two subscriber routes are open.

    The same router hangs its admin writes off the same prefix and none of them
    carries a guard of its own, so opening the prefix wholesale would have handed
    anonymous callers the ability to declare an incident on the operator's own
    status page.
    """
    assert client.request(method, path, json=body).status_code == 401


def test_a_presented_credential_is_still_checked_on_a_public_read(client):
    """The exemption applies to callers with no credential at all, not to bad ones.

    The status page store is scoped to the org on the tenant context, so waving a
    request through *because* the path is public would serve a tenant that did
    send a key the default org's components instead of their own. A wrong key is
    a wrong key.
    """
    r = client.get("/api/status/components", headers={"Authorization": "Bearer wrong"})

    assert r.status_code == 401


def test_the_exemption_is_the_only_thing_keeping_the_link_openable(client, monkeypatch):
    """Nothing else would let the click through, so the allowlist cannot be dropped.

    Withdrawing the exemption at runtime reproduces the original defect exactly —
    the mail client is answered 401 before `verify_subscription` runs — which is
    what makes the test above a regression guard rather than a coincidence.
    """
    monkeypatch.setattr(api, "_is_public_status_page", lambda method, path: False)

    assert client.get(f"{VERIFY_PATH}?id=abc&token=xyz").status_code == 401
    assert client.get("/api/status").status_code == 401


def test_the_public_status_routes_are_still_rate_limited(client, monkeypatch):
    """Deliberately not in _OPEN_PATHS: that set also bypasses LimitsMiddleware.

    An unauthenticated POST with neither a per-IP rate limit nor a body cap is a
    spam endpoint, which is why the exemption is a skip of the credential check
    only.
    """
    assert VERIFY_PATH not in api._OPEN_PATHS
    monkeypatch.setenv("COPILOT_RATE_LIMIT_PER_MIN", "2")
    cfg.get_settings.cache_clear()
    api._reset_limiter()

    codes = [
        client.post("/api/status/subscribe", json={"email": f"r{i}@example.com"}).status_code
        for i in range(4)
    ]

    assert 429 in codes


# --- the Stripe receiver files a payment in both places -------------------- #
class _FakeBillingAPI:
    """Stands in for BillingAPI, recording exactly what the receiver forwarded."""

    def __init__(self, result: dict[str, Any] | None = None, raises: Exception | None = None):
        self.result = result or {"status": "processed", "event_type": "x"}
        self.raises = raises
        self.calls: list[tuple[bytes, str]] = []

    async def handle_webhook(self, payload: bytes, signature: str) -> dict[str, Any]:
        self.calls.append((payload, signature))
        if self.raises is not None:
            raise self.raises
        return self.result


class _FakeTenantStore:
    def __init__(self) -> None:
        self.plans: list[tuple[str, str]] = []

    async def set_plan(self, org_id: str, plan: str) -> bool:
        self.plans.append((org_id, plan))
        return True


def _signed(body: bytes) -> dict[str, str]:
    ts = str(int(time.time()))
    mac = hmac.new(WHSEC.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return {"Stripe-Signature": f"t={ts},v1={mac}", "Content-Type": "application/json"}


def _subscription_event() -> bytes:
    return json.dumps(
        {
            "type": "customer.subscription.updated",
            "data": {
                "object": {
                    "id": "sub_1",
                    "metadata": {"org_id": "org_123", "plan": "team"},
                }
            },
        }
    ).encode()


@pytest.fixture
def billing(monkeypatch):
    """Stripe configured, with the billing API replaced by a recorder."""
    fake = _FakeBillingAPI()
    monkeypatch.setattr(billing_runtime, "get_stripe_service", lambda: object())
    monkeypatch.setattr(billing_runtime, "get_billing_api", lambda: fake)
    store = _FakeTenantStore()
    monkeypatch.setattr(api.tenant_auth, "get_store", lambda: store)
    return fake, store


def test_a_paid_subscription_reaches_the_billing_store_and_the_tenant_plan(client, billing):
    """One payment, two records — and the receiver was only ever making one.

    `BillingAPI.handle_webhook` is the sole entry point to `billing_subscriptions`
    and no route reached it, so after a real payment `GET /billing/subscription`
    answered null and change/cancel/reactivate raised "No subscription for org".
    """
    fake, store = billing
    fake.result = {"status": "processed", "event_type": "customer.subscription.updated"}
    body = _subscription_event()
    headers = _signed(body)

    r = client.post("/webhooks/stripe", content=body, headers=headers)

    assert r.status_code == 200, r.text
    assert r.json()["status"] == "applied"
    assert store.plans == [("org_123", "team")]
    assert r.json()["billing_store"] == {
        "status": "processed",
        "event_type": "customer.subscription.updated",
    }
    # The raw body and the header, not the parsed event: the billing API
    # re-verifies the signature through the SDK, which needs both verbatim.
    assert fake.calls == [(body, headers["Stripe-Signature"])]


def test_an_event_the_plan_mapping_ignores_still_reaches_the_billing_store(client, billing):
    """checkout.session.completed / invoice.paid have no tenant-plan meaning.

    They are exactly the deliveries the billing store *does* have a handler for,
    so the forward happens before the plan mapping returns early.
    """
    fake, _store = billing
    fake.result = {"status": "processed", "event_type": "invoice.paid"}
    body = json.dumps({"type": "invoice.paid", "data": {"object": {"id": "in_1"}}}).encode()

    r = client.post("/webhooks/stripe", content=body, headers=_signed(body))

    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ignored"
    assert r.json()["billing_store"]["status"] == "processed"
    assert len(fake.calls) == 1


def test_an_unconfigured_billing_store_is_reported_not_raised(client, monkeypatch):
    """BillingUnavailableError must not 500 a delivery Stripe correctly signed.

    A 500 makes Stripe retry the whole event, including the tenant-plan half that
    already landed. The reply says the store leg did not happen instead.
    """
    fake = _FakeBillingAPI(raises=BillingUnavailableError("Billing is not configured."))
    store = _FakeTenantStore()
    monkeypatch.setattr(billing_runtime, "get_stripe_service", lambda: object())
    monkeypatch.setattr(billing_runtime, "get_billing_api", lambda: fake)
    monkeypatch.setattr(api.tenant_auth, "get_store", lambda: store)
    body = _subscription_event()

    r = client.post("/webhooks/stripe", content=body, headers=_signed(body))

    assert r.status_code == 200, r.text
    assert r.json()["status"] == "applied"
    assert store.plans == [("org_123", "team")]
    assert r.json()["billing_store"]["status"] == "not_configured"


def test_a_broken_billing_store_is_named_rather_than_read_as_a_clean_success(client, monkeypatch):
    """A delivery that moved one store and not the other has to be visible."""
    fake = _FakeBillingAPI(raises=RuntimeError("subscriptions table is gone"))
    store = _FakeTenantStore()
    monkeypatch.setattr(billing_runtime, "get_stripe_service", lambda: object())
    monkeypatch.setattr(billing_runtime, "get_billing_api", lambda: fake)
    monkeypatch.setattr(api.tenant_auth, "get_store", lambda: store)
    body = _subscription_event()

    r = client.post("/webhooks/stripe", content=body, headers=_signed(body))

    assert r.status_code == 200, r.text
    leg = r.json()["billing_store"]
    assert leg["status"] == "failed"
    assert "RuntimeError" in leg["detail"] and "subscriptions table is gone" in leg["detail"]
    # The tenant-plan half still landed and still says so.
    assert r.json()["status"] == "applied"


def test_a_deployment_without_stripe_says_so_instead_of_claiming_it_stored_anything(
    client, monkeypatch
):
    """"Not configured" is not "processed", and it is not an error either."""
    fake = _FakeBillingAPI()
    monkeypatch.setattr(billing_runtime, "get_stripe_service", lambda: None)
    monkeypatch.setattr(billing_runtime, "get_billing_api", lambda: fake)
    monkeypatch.setattr(api.tenant_auth, "get_store", lambda: _FakeTenantStore())
    body = _subscription_event()

    r = client.post("/webhooks/stripe", content=body, headers=_signed(body))

    assert r.status_code == 200, r.text
    assert r.json()["billing_store"]["status"] == "not_configured"
    assert fake.calls == []  # nothing to forward to


def test_an_unsigned_delivery_never_reaches_the_billing_store(client, billing):
    """The forward sits behind the signature check, not in front of it."""
    fake, _store = billing
    body = _subscription_event()

    r = client.post("/webhooks/stripe", content=body, headers={"Stripe-Signature": "t=1,v1=nope"})

    assert r.status_code == 401
    assert fake.calls == []


# --- /remediate draws its confidence from the store, not the caller -------- #
def test_no_body_field_can_outrank_the_stored_investigation(client, monkeypatch):
    """The old gate read `req.confidence`, so typing "high" was the safety check.

    Pinned against the tier the server actually resolved: with the investigation
    store saying "low", no combination of body fields may produce a 200.
    """
    monkeypatch.setenv("COPILOT_AUTONOMY", "true")
    cfg.get_settings.cache_clear()

    async def _low(thread_id: str) -> tuple[str, str]:
        return "low", f"read from the stored report of investigation {thread_id!r}"

    monkeypatch.setattr(api, "_investigation_confidence", _low)

    claimed = client.post("/remediate", headers=_auth(), json={
        "action": "restart_deployment", "target": "checkout-svc",
        "thread_id": "inv-1", "confidence": "high"})
    silent = client.post("/remediate", headers=_auth(), json={
        "action": "restart_deployment", "target": "checkout-svc", "thread_id": "inv-1"})

    assert claimed.status_code == 400
    assert "no longer accepted" in claimed.json()["detail"]
    assert silent.status_code == 400
    assert "'low'" in silent.json()["detail"]


def test_the_tier_the_server_resolved_is_the_one_that_authorises_the_write(client, monkeypatch):
    """And it travels back with the result, with the investigation it came from."""
    monkeypatch.setenv("COPILOT_AUTONOMY", "true")
    cfg.get_settings.cache_clear()
    seen: list[str] = []

    async def _high(thread_id: str) -> tuple[str, str]:
        seen.append(thread_id)
        return "high", f"read from the stored report of investigation {thread_id!r}"

    monkeypatch.setattr(api, "_investigation_confidence", _high)

    r = client.post("/remediate", headers=_auth(), json={
        "action": "restart_deployment", "target": "checkout-svc", "thread_id": "inv-1"})

    assert r.status_code == 200, r.text
    assert seen == ["inv-1"]
    body = r.json()
    assert body["confidence"] == "high"
    assert "inv-1" in body["confidence_source"]
