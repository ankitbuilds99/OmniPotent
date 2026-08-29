"""Stripe webhook — signature verification + subscription-event → plan mapping."""

import hashlib
import hmac

import pytest

from app.billing import UnknownPlan, plan_from_stripe_event, verify_stripe_signature


def _sig(payload: bytes, secret: str, ts: int) -> str:
    signed = f"{ts}.".encode() + payload
    v1 = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return f"t={ts},v1={v1}"


def test_verify_signature_accepts_valid():
    body = b'{"type":"x"}'
    header = _sig(body, "whsec_test", 1000)
    assert verify_stripe_signature(body, header, "whsec_test", now=1000) is True


def test_verify_signature_rejects_wrong_secret_and_missing():
    body = b'{"a":1}'
    header = _sig(body, "right", 1000)
    assert verify_stripe_signature(body, header, "wrong", now=1000) is False
    assert verify_stripe_signature(body, header, "", now=1000) is False   # fail closed
    assert verify_stripe_signature(body, "", "right", now=1000) is False


def test_verify_signature_rejects_stale_timestamp():
    body = b'{"a":1}'
    header = _sig(body, "s", 1000)
    assert verify_stripe_signature(body, header, "s", now=1000 + 10_000) is False  # outside tolerance


def test_plan_mapping_created_updated_deleted():
    def evt(t, meta):
        return {"type": t, "data": {"object": {"metadata": meta}}}

    assert plan_from_stripe_event(evt("customer.subscription.updated",
                                     {"org_id": "o1", "plan": "team"})) == ("o1", "team")
    assert plan_from_stripe_event(evt("customer.subscription.created",
                                     {"org_id": "o1", "plan": "enterprise"})) == ("o1", "enterprise")
    assert plan_from_stripe_event(evt("customer.subscription.deleted",
                                     {"org_id": "o1"})) == ("o1", "free")


def test_plan_mapping_translates_catalog_tiers():
    """Stripe metadata carries a catalog tier; `orgs.plan` holds a quota plan.

    Growth and Business are sold tiers and `PLAN_QUOTAS` now has a bracket for
    each, so they are kept. This used to assert that Growth arrived as "team":
    the enforced vocabulary had three values, so the tier the customer paid for
    was destroyed on the way in and every entitlement above Team was withheld
    from them. Only Starter and Scale are still translated — Starter is spelled
    `free` in the enforcement vocabulary, and Scale shares Enterprise's bracket,
    which is lossy only upwards.
    """
    def evt(t, meta):
        return {"type": t, "data": {"object": {"metadata": meta}}}

    assert plan_from_stripe_event(evt("customer.subscription.updated",
                                      {"org_id": "o1", "plan": "growth"})) == ("o1", "growth")
    assert plan_from_stripe_event(evt("customer.subscription.updated",
                                      {"org_id": "o1", "plan": "business"})) == ("o1", "business")
    assert plan_from_stripe_event(evt("customer.subscription.updated",
                                      {"org_id": "o1", "plan": "scale"})) == ("o1", "enterprise")
    assert plan_from_stripe_event(evt("customer.subscription.updated",
                                      {"org_id": "o1", "plan": "starter"})) == ("o1", "free")


def test_plan_mapping_ignores_incomplete_and_unhandled_events():
    def evt(t, meta):
        return {"type": t, "data": {"object": {"metadata": meta}}}

    assert plan_from_stripe_event(evt("customer.subscription.updated", {"plan": "team"})) is None  # no org
    assert plan_from_stripe_event(evt("invoice.paid", {"org_id": "o1"})) is None   # unhandled type


@pytest.mark.parametrize("plan", ["bogus", "pro_monthly", "starter_yearly", ""])
def test_plan_mapping_raises_on_a_plan_we_do_not_sell(plan):
    """This returned None, which the receiver reported as `status: ignored` —
    indistinguishable from an event type we don't handle. `pro_monthly` was even
    in the old allowlist, so it reported `applied` and then normalised to `free`.
    Raising makes the receiver answer 400 so Stripe retries and surfaces it."""
    event = {
        "type": "customer.subscription.updated",
        "data": {"object": {"metadata": {"org_id": "o1", "plan": plan}}},
    }
    with pytest.raises(UnknownPlan) as exc:
        plan_from_stripe_event(event)
    assert exc.value.plan == plan


def test_endpoint_rejects_bad_signature(monkeypatch):
    import app.api.main as api
    import app.config as cfg
    from fastapi.testclient import TestClient

    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    cfg.get_settings.cache_clear()
    client = TestClient(api.app)
    r = client.post("/webhooks/stripe", content=b'{"type":"x"}',
                    headers={"stripe-signature": "t=1,v1=deadbeef"})
    assert r.status_code == 401
    cfg.get_settings.cache_clear()
