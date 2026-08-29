"""RBAC on /billing: the money routes are owner-only, the documents admin-only.

`manage_billing` was defined in the permission matrix (app/tenancy/models.py)
and enforced nowhere. The app-wide dependency is `require_auth`, which proves
who you are and says nothing about what you may do, so a viewer-role tenant key
reached `POST /billing/subscription/cancel`, `/subscription/change`,
`/checkout`, `/portal` and `/track` and read `/invoices` and `/payment-methods`
— on a Stripe-configured deployment that is a viewer cancelling the org's paid
plan or minting a customer-portal URL that hands over the card on file.

These run the real app with COPILOT_MULTI_TENANT=true, exactly as
tests/test_tenancy_api.py does, so what is asserted is the deployed dependency
chain rather than a hand-called function. The assertions are on the 403 itself,
not on Stripe, so they hold whether or not billing is configured.
"""

import asyncio

import pytest
from fastapi.testclient import TestClient

import app.api.main as api
import app.config as cfg
from app import runtime, tenant_context
from app.database import engine as db_engine
from app.tenancy import auth as tenant_auth

# Every mutation that moves money or hands out control of the payment method.
OWNER_ONLY = [
    ("post", "/billing/subscription/change", {"plan_name": "team"}),
    ("post", "/billing/subscription/cancel", {"reason": "x"}),
    ("post", "/billing/subscription/reactivate", {}),
    ("post", "/billing/checkout", {"plan_name": "team", "billing_cycle": "monthly"}),
    ("post", "/billing/portal", {"return_url": "https://example.test/billing"}),
    ("post", "/billing/track", {"metric_type": "ai_investigation"}),
]

# Reads that expose what the org was charged and the card on file.
ADMIN_ONLY = [
    ("get", "/billing/invoices", None),
    ("get", "/billing/payment-methods", None),
]


async def _provision(store):
    await store.setup()
    org = await store.create_org("Acme", plan="team")
    owner, _ = await store.issue_api_key(org.id, "k-owner", role="owner")
    admin, _ = await store.issue_api_key(org.id, "k-admin", role="admin")
    viewer, _ = await store.issue_api_key(org.id, "k-view", role="viewer")
    responder, _ = await store.issue_api_key(org.id, "k-resp", role="responder")
    return {"owner": owner, "admin": admin, "viewer": viewer, "responder": responder}


def _reset(tmp_path):
    cfg.get_settings.cache_clear()
    tenant_auth.reset_store_cache()
    tenant_context.current_tenant.set(None)
    api._RL.clear()
    runtime.reset()
    db_engine._engine = None
    db_engine._session_factory = None


@pytest.fixture
def mt(tmp_path, monkeypatch):
    monkeypatch.setenv("COPILOT_MULTI_TENANT", "true")
    monkeypatch.setenv("COPILOT_TENANT_DB", str(tmp_path / "tenants.sqlite"))
    monkeypatch.setenv("COPILOT_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/app.db")
    _reset(tmp_path)
    data = asyncio.run(_provision(tenant_auth.get_store()))
    with TestClient(api.app) as client:
        data["client"] = client
        yield data
    _reset(tmp_path)


def _call(client, method, path, body, key):
    headers = {"Authorization": f"Bearer {key}"}
    if method == "get":
        return client.get(path, headers=headers)
    return client.post(path, json=body, headers=headers)


@pytest.mark.parametrize("method,path,body", OWNER_ONLY)
def test_viewer_is_refused_every_billing_mutation(mt, method, path, body):
    r = _call(mt["client"], method, path, body, mt["viewer"])
    assert r.status_code == 403, f"{path} -> {r.status_code} {r.text}"
    assert "manage billing" in r.json()["detail"]


@pytest.mark.parametrize("method,path,body", OWNER_ONLY)
def test_responder_is_refused_every_billing_mutation(mt, method, path, body):
    r = _call(mt["client"], method, path, body, mt["responder"])
    assert r.status_code == 403, f"{path} -> {r.status_code} {r.text}"


@pytest.mark.parametrize("method,path,body", OWNER_ONLY)
def test_admin_is_refused_too_because_the_matrix_says_owner(mt, method, path, body):
    """`manage_billing` is "owner" in _PERMISSIONS; admin is one rank below."""
    r = _call(mt["client"], method, path, body, mt["admin"])
    assert r.status_code == 403, f"{path} -> {r.status_code} {r.text}"


@pytest.mark.parametrize("method,path,body", OWNER_ONLY)
def test_the_owner_still_reaches_the_handler(mt, method, path, body):
    """The gate is a role check, not a blanket refusal.

    Stripe is unconfigured in the test deployment, so most of these answer 503
    ("Billing is not configured") — which is the handler running and saying so.
    What matters is that the owner is never turned away by RBAC.
    """
    r = _call(mt["client"], method, path, body, mt["owner"])
    assert r.status_code != 403, f"{path} -> {r.status_code} {r.text}"


@pytest.mark.parametrize("method,path,body", ADMIN_ONLY)
def test_viewer_cannot_read_invoices_or_payment_methods(mt, method, path, body):
    r = _call(mt["client"], method, path, body, mt["viewer"])
    assert r.status_code == 403, f"{path} -> {r.status_code} {r.text}"
    assert "view audit" in r.json()["detail"]


@pytest.mark.parametrize("method,path,body", ADMIN_ONLY)
def test_admin_can_read_invoices_and_payment_methods(mt, method, path, body):
    r = _call(mt["client"], method, path, body, mt["admin"])
    assert r.status_code != 403, f"{path} -> {r.status_code} {r.text}"


@pytest.mark.parametrize(
    "path", ["/billing/plans", "/billing/subscription", "/billing/usage", "/billing/features"]
)
def test_plain_reads_stay_open_to_a_viewer(mt, path):
    """The fix gates the mutations and the documents, not the whole page.

    A viewer still has to be able to see which plan the org is on and how much
    of it has been used, or the Billing page renders empty for most of the org.
    """
    r = _call(mt["client"], "get", path, None, mt["viewer"])
    assert r.status_code == 200, f"{path} -> {r.status_code} {r.text}"


def test_an_unauthenticated_caller_is_still_401_not_403(mt):
    """Authentication runs first; the RBAC dependency does not mask it."""
    r = mt["client"].post("/billing/subscription/cancel", json={})
    assert r.status_code == 401, r.text


# --------------------------------------------------------------------------- #
# Single-tenant deployments have no roles, so nothing here may fire.
# --------------------------------------------------------------------------- #
@pytest.fixture
def single(tmp_path, monkeypatch):
    monkeypatch.delenv("COPILOT_MULTI_TENANT", raising=False)
    monkeypatch.setenv("COPILOT_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/app.db")
    _reset(tmp_path)
    with TestClient(api.app) as client:
        yield client
    _reset(tmp_path)


@pytest.mark.parametrize("method,path,body", OWNER_ONLY + ADMIN_ONLY)
def test_single_tenant_mode_is_unaffected(single, method, path, body):
    r = single.get(path) if method == "get" else single.post(path, json=body)
    assert r.status_code != 403, f"{path} -> {r.status_code} {r.text}"
