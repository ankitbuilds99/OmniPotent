"""Who may read the audit trail, and how much of it they see.

The trail records key ids, actor ids, approval decisions and security events, so
two properties have to hold on a multi-tenant deployment:

  * reading it is an admin action — a `viewer` key must not be able to enumerate
    the org's API-key ids and the identities that used them;
  * every answer is scoped to the caller's org. `/audit` filters, but
    `/audit/verify` used to run over the process-wide buffer, so its `count` was
    the whole fleet's activity and a broken link could name another tenant's event.

Each entry's hash folds in the entry before it, whoever recorded it, so the
*verification walk* still has to cover every entry in the list handed to it —
only the report is scoped. That is what the unit tests at the bottom pin, against
`verify_chain`, which is a pure function over a list of entries now that the
trail lives in a table rather than a process-local buffer.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

import app.api.main as api
import app.config as cfg
from app import audit, runtime, tenant_context
from app.audit_legacy import chain_hash  # not re-exported through app.audit
from app.database import engine as db_engine
from app.tenancy import auth as tenant_auth
from app.tenancy import models as tenancy_models
from app.tenancy import scoping


def _reset() -> None:
    cfg.get_settings.cache_clear()
    tenant_auth.reset_store_cache()
    tenant_context.current_tenant.set(None)
    tenant_context.actor_var.set("-")
    scoping.reset_all()
    api._RL.clear()
    api._reset_limiter()
    runtime.reset()
    asyncio.run(_reset_audit_and_db())


async def _reset_audit_and_db() -> None:
    """Drop the audit trail, then the engine that holds it.

    `clear()` is a coroutine now that the trail is a table. The bare, unawaited
    call this replaces reset nothing at all, so every test in this file ran
    against whatever the previous one left behind. `close_db()` rather than
    nulling the module globals: it also clears the "schema already created" flag,
    which would otherwise stay true while the URL is repointed at the next test's
    empty database.
    """
    await audit.clear()
    await db_engine.close_db()


async def _provision(store) -> dict[str, str]:
    await store.setup()
    org_a = await store.create_org("Acme", plan="team")
    org_b = await store.create_org("Beta", plan="team")
    a_admin, _ = await store.issue_api_key(org_a.id, "a-admin", role="admin")
    a_viewer, _ = await store.issue_api_key(org_a.id, "a-view", role="viewer")
    b_admin, _ = await store.issue_api_key(org_b.id, "b-admin", role="admin")
    return {
        "a_admin": a_admin,
        "a_viewer": a_viewer,
        "b_admin": b_admin,
        "org_a": org_a.id,
        "org_b": org_b.id,
    }


@pytest.fixture
def mt(tmp_path, monkeypatch):
    monkeypatch.setenv("COPILOT_MULTI_TENANT", "true")
    monkeypatch.setenv("COPILOT_TENANT_DB", str(tmp_path / "tenants.sqlite"))
    monkeypatch.setenv("COPILOT_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/app.db")
    _reset()
    data = asyncio.run(_provision(tenant_auth.get_store()))
    with TestClient(api.app, raise_server_exceptions=False) as client:
        data["client"] = client
        yield data
    _reset()


def _auth(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _make_an_event(client: TestClient, key: str, name: str) -> None:
    """Any audited admin action; issuing an API key records `apikey.created`."""
    r = client.post("/admin/api-keys", json={"name": name, "role": "viewer"}, headers=_auth(key))
    assert r.status_code == 200, r.text


# --- RBAC ------------------------------------------------------------------ #


def test_viewer_cannot_read_the_audit_trail(mt):
    c = mt["client"]
    _make_an_event(c, mt["a_admin"], "ASECRET")

    denied = c.get("/audit?limit=20", headers=_auth(mt["a_viewer"]))
    assert denied.status_code == 403, denied.text
    assert "viewer" in denied.json()["detail"]


def test_viewer_cannot_verify_the_audit_chain(mt):
    denied = mt["client"].get("/audit/verify", headers=_auth(mt["a_viewer"]))
    assert denied.status_code == 403, denied.text


def test_admin_can_read_the_audit_trail(mt):
    c = mt["client"]
    _make_an_event(c, mt["a_admin"], "ASECRET")

    allowed = c.get("/audit?limit=20", headers=_auth(mt["a_admin"]))
    assert allowed.status_code == 200, allowed.text
    assert [e["event"] for e in allowed.json()["events"]] == ["apikey.created"]


def test_view_audit_is_an_admin_action_in_the_matrix():
    assert "view_audit" in tenancy_models.ACTIONS
    assert tenancy_models.can("admin", "view_audit") is True
    assert tenancy_models.can("owner", "view_audit") is True
    assert tenancy_models.can("responder", "view_audit") is False
    assert tenancy_models.can("viewer", "view_audit") is False


# --- tenant scoping of /audit/verify --------------------------------------- #


def test_verify_counts_only_the_callers_own_events(mt):
    c = mt["client"]
    _make_an_event(c, mt["a_admin"], "ASECRET")
    _make_an_event(c, mt["b_admin"], "BSECRET")
    _make_an_event(c, mt["b_admin"], "BSECRET2")

    a = c.get("/audit/verify", headers=_auth(mt["a_admin"]))
    b = c.get("/audit/verify", headers=_auth(mt["b_admin"]))
    assert a.status_code == 200 and b.status_code == 200
    # The verdict names the chain it is about, and that is the caller's own org
    # — it discloses nothing they did not authenticate as, and it stops a verdict
    # pasted into an incident channel from being read as the wrong tenant's.
    # Asserted by exact equality so `org_id` is also pinned to *their* org.
    assert a.json() == {"valid": True, "count": 1, "org_id": mt["org_a"]}, a.text
    assert b.json() == {"valid": True, "count": 2, "org_id": mt["org_b"]}, b.text


def test_audit_read_is_scoped_to_the_caller(mt):
    c = mt["client"]
    _make_an_event(c, mt["a_admin"], "ASECRET")
    _make_an_event(c, mt["b_admin"], "BSECRET")

    body = c.get("/audit?limit=50", headers=_auth(mt["a_admin"])).text
    assert "BSECRET" not in body
    assert mt["org_b"] not in body


# --- the chain maths, unit level ------------------------------------------- #
#
# Straight against `verify_chain`, over lists built here. Recording through the
# store and reaching in to tamper would test the store's insert path as much as
# the scoped walk, and the walk is the piece that decides what one tenant is
# told about another's entries.


def _chained(*entries: dict) -> list[dict]:
    """Hash-chain entries the way `app.audit.store.append` does on insert."""
    out: list[dict] = []
    prev = ""
    for entry in entries:
        linked = {"ts": "2026-01-01T00:00:00Z", "actor": "-", **entry, "prev_hash": prev}
        linked["hash"] = chain_hash(linked, prev)
        out.append(linked)
        prev = linked["hash"]
    return out


def test_scoped_verify_still_validates_across_the_whole_chain():
    """Filtering first would report a false 'tampered' — entries interleave."""
    events = _chained(
        {"event": "a.one", "org_id": "A"},
        {"event": "b.one", "org_id": "B"},
        {"event": "a.two", "org_id": "A"},
    )

    assert audit.verify_chain(events, org_id="A") == {"valid": True, "count": 2}
    assert audit.verify_chain(events, org_id="B") == {"valid": True, "count": 1}
    assert audit.verify_chain(events) == {"valid": True, "count": 3}


def test_tampering_with_another_orgs_entry_does_not_name_it():
    events = _chained(
        {"event": "a.one", "org_id": "A"},
        {"event": "b.secret_event", "org_id": "B"},
    )
    events[1]["stolen"] = True  # tamper with B's entry

    seen_by_a = audit.verify_chain(events, org_id="A")
    assert seen_by_a["valid"] is False  # A is still told the chain is broken
    assert seen_by_a["event"] is None  # B's event name is not leaked to A
    assert seen_by_a["broken_at"] is None

    seen_by_b = audit.verify_chain(events, org_id="B")
    assert seen_by_b["valid"] is False
    assert seen_by_b["event"] == "b.secret_event"
    assert seen_by_b["broken_at"] == 0


def test_unscoped_verify_keeps_its_contract():
    events = _chained(
        {"event": "a", "org_id": "A"},
        {"event": "b", "org_id": "A", "x": 1},
        {"event": "c", "org_id": "A"},
    )
    events[1]["x"] = 999

    res = audit.verify_chain(events)
    assert res["valid"] is False
    assert res["broken_at"] == 1
    assert res["event"] == "b"
    assert res["count"] == 3
