"""The /customers routes must work against the database, not process memory.

Two things break silently when a store moves out of the process and its accessors
become coroutines:

1. A route that forgets `await` returns a coroutine object. FastAPI cannot encode
   one, so the endpoint 500s — but only when that route is actually called, and
   nothing in the suite called these six.
2. The data stops surviving. That is the entire point of the move, so it is
   asserted through the HTTP surface too, not just at the manager level: a
   customer created through the API has to still be there after the process that
   served the request is gone.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.config import get_settings
from app.database.engine import close_db

# Every route that reads or writes customer state, as (method, path, body).
# Listed exhaustively: the failure mode is a single un-awaited call site, so
# coverage has to be per-route rather than per-module.
ROUTES = [
    ("POST", "/customers", {"email": "route@test.com", "company": "Route Co"}),
    ("GET", "/customers/stats", None),
    ("GET", "/customers/dashboard", None),
    ("GET", "/customers/trials/active", None),
    ("POST", "/customers/usage", {"customer_id": "", "event_type": "investigation"}),
    ("POST", "/customers/convert", {"customer_id": "", "tier": "starter"}),
]


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """A throwaway database per test.

    `close_db()` is driven through asyncio.run because these tests are sync (they
    drive the app through TestClient, which owns its own loop). It is a no-op
    unless a previous test left an engine behind — TestClient's lifespan disposes
    on exit — but without it a stale engine would keep pointing at the previous
    test's tmp_path.
    """
    monkeypatch.setenv("COPILOT_STATE_DIR", str(tmp_path))
    get_settings.cache_clear()
    asyncio.run(close_db())
    yield
    asyncio.run(close_db())
    get_settings.cache_clear()


def test_every_customer_route_responds_without_a_server_error():
    """No route may 500 — that is what an un-awaited coroutine looks like here."""
    with TestClient(app) as client:
        created = client.post("/customers", json={"email": "seed@test.com", "company": "Seed Co"})
        assert created.status_code == 200
        customer_id = created.json()["customer"]["id"]

        for method, path, body in ROUTES:
            payload = dict(body) if body else None
            if payload and "customer_id" in payload:
                payload["customer_id"] = customer_id
            response = client.request(method, path, json=payload)
            assert response.status_code < 500, (
                f"{method} {path} returned {response.status_code}: {response.text}"
            )


def test_a_customer_created_through_the_api_outlives_the_process():
    """Create on one app instance, read it back on the next.

    Leaving and re-entering the TestClient context runs the app's shutdown and
    startup: connections are disposed and the module state is rebuilt. Before
    this change the customer lived in a module-level dict and the second read
    returned zero customers — which is also what a second replica saw.
    """
    with TestClient(app) as client:
        response = client.post(
            "/customers",
            json={"email": "survivor@test.com", "company": "Survivor Co", "start_trial": True},
        )
        assert response.status_code == 200
        customer_id = response.json()["customer"]["id"]

        assert client.post(
            "/customers/usage",
            json={"customer_id": customer_id, "event_type": "investigation", "tokens_used": 250},
        ).status_code == 200

    # Second app instance — same database, no shared memory.
    with TestClient(app) as client:
        stats = client.get("/customers/stats").json()
        assert stats["total_customers"] == 1
        assert stats["total_investigations"] == 1

        dashboard = client.get("/customers/dashboard").json()
        assert [c["id"] for c in dashboard["recent_customers"]] == [customer_id]
        assert len(dashboard["active_trials"]) == 1

        trials = client.get("/customers/trials/active").json()
        assert trials["count"] == 1


def test_conversion_recorded_through_the_api_is_durable():
    """Revenue state in particular has to be readable by the next pod."""
    with TestClient(app) as client:
        customer_id = client.post(
            "/customers", json={"email": "paid@test.com", "company": "Paid Co"}
        ).json()["customer"]["id"]

        converted = client.post(
            "/customers/convert",
            json={"customer_id": customer_id, "tier": "pro", "mrr_cents": 49900},
        )
        assert converted.status_code == 200
        assert converted.json()["success"] is True

    with TestClient(app) as client:
        stats = client.get("/customers/stats").json()
        assert stats["paying_customers"] == 1
        assert stats["mrr_usd"] == 499.0
        assert stats["by_tier"] == {"pro": 1}


def test_usage_for_an_unknown_customer_is_still_a_404():
    """The not-found path must come from the database, not a missing dict key."""
    with TestClient(app) as client:
        response = client.post(
            "/customers/usage",
            json={"customer_id": "cust_ghost", "event_type": "investigation"},
        )
        assert response.status_code == 404
