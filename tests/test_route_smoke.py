"""Every registered route answers a well-formed request without crashing.

This is a floor, not a feature test: it says nothing about whether an endpoint
returns the *right* answer, only that it returns an answer. 4xx is a fine
outcome here — an unauthenticated request that gets a 401, or an empty body
that gets a 422, is the endpoint working. A 500 is not, because it means an
unhandled exception escaped the handler.

It exists because route regressions are the cheapest kind to introduce and the
most expensive to notice: an endpoint that imports a module that no longer
exists, or reads a table that was never created, looks perfectly healthy in the
route table and fails only when something calls it. Deleting unused code is
exactly the situation that produces those, and no other test in the suite walks
the whole surface.

`with TestClient(...)` matters — it runs the lifespan, which runs `init_db()`.
Without it every database-backed route raises OperationalError: no such table,
which reads as "44 endpoints are broken" and is really "the harness forgot to
create the schema".
"""

from __future__ import annotations

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

import app.api.main as api
import app.config as cfg
from app import runtime, tenant_context
from app.database import engine as db_engine

# Deliberate refusals: the deployment has no Stripe or Slack credentials, and
# these handlers say so instead of pretending. 503 with an explanation is the
# correct response to "this integration was never configured", so it is allowed
# here — but only for routes named below, so a new 503 still fails the test.
CONFIGURED_AWAY = {
    "/billing/subscription/cancel",
    "/billing/subscription/reactivate",
    # The portal used to answer 200 with `portal_url` set to the caller's own
    # return URL, and the frontend assigns that to window.location — so every
    # button wired to it silently reloaded the current page. Refusing by name is
    # the fix, not a regression.
    "/billing/portal",
    # /billing/webhooks/stripe is deliberately absent: it was a duplicate Stripe
    # receiver behind require_auth, which Stripe could never reach. The live one
    # is POST /webhooks/stripe (tests/test_stripe_webhook_route.py).
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
}

# The same rule for reads: a GET that can only answer from a backend the operator
# never configured refuses by name rather than serving zeros, which would read as
# a real measurement. Each entry pairs the path with the setting its 503 must
# name, so "unconfigured" stays distinguishable from "crashed".
UNCONFIGURED_READS = {
    "/api/knowledge-graph/service-metrics": "PROMETHEUS_URL",
}

# A syntactically valid UUID that owns nothing, so path-parameter routes reach
# their handler and take the not-found branch rather than failing to parse.
ABSENT = "00000000-0000-0000-0000-000000000001"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("COPILOT_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/smoke.db")
    cfg.get_settings.cache_clear()
    tenant_context.current_tenant.set(None)
    api._RL.clear()
    api._reset_limiter()
    runtime.reset()
    db_engine._engine = None
    db_engine._session_factory = None

    with TestClient(api.app, raise_server_exceptions=False) as test_client:
        yield test_client

    cfg.get_settings.cache_clear()
    tenant_context.current_tenant.set(None)
    api._RL.clear()
    api._reset_limiter()
    runtime.reset()
    db_engine._engine = None
    db_engine._session_factory = None


def _routes() -> list[tuple[str, str]]:
    """(method, path) for everything the app serves, minus HEAD/OPTIONS."""
    pairs = []
    for route in api.app.routes:
        if not isinstance(route, APIRoute):
            continue  # websockets and the SPA static mount are not HTTP verbs
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            pairs.append((method, route.path))
    return sorted(pairs)


def _concrete(path: str) -> str:
    return "/".join(
        ABSENT if seg.startswith("{") and seg.endswith("}") else seg
        for seg in path.split("/")
    )


def test_the_route_table_is_not_empty():
    """Guards the test itself: an import failure would silently pass everything."""
    assert len(_routes()) > 200


def test_no_route_raises(client):
    """No endpoint returns 5xx for a well-formed request.

    Reported in one assertion rather than parametrized, so a change that breaks
    a whole router shows the full list at once instead of one failure per route.
    """
    crashed = []
    for method, path in _routes():
        url = _concrete(path)
        response = (
            client.request(method, url)
            if method in ("GET", "DELETE")
            else client.request(method, url, json={})
        )
        if response.status_code < 500:
            continue
        if response.status_code == 503 and path in CONFIGURED_AWAY:
            continue
        if response.status_code == 503 and path in UNCONFIGURED_READS:
            continue
        crashed.append(f"{response.status_code} {method} {path} -> {response.text[:160]}")

    assert not crashed, "routes returned 5xx:\n" + "\n".join(crashed)


def test_unconfigured_integrations_explain_themselves(client):
    """The allowlist above is for *deliberate* refusals, so check they read like it.

    Without this, `CONFIGURED_AWAY` would happily hide a genuine crash that
    happened to surface as a 503.
    """
    for path in sorted(CONFIGURED_AWAY):
        response = client.request("POST", path, json={})
        assert response.status_code == 503, f"{path}: {response.status_code}"
        assert "not configured" in response.json()["detail"].lower(), path

    for path, setting in sorted(UNCONFIGURED_READS.items()):
        response = client.request("GET", path)
        assert response.status_code == 503, f"{path}: {response.status_code}"
        # Naming the setting is what makes the refusal actionable, and what a
        # genuine crash surfacing as a 503 would not do.
        assert setting in response.json()["detail"], path
