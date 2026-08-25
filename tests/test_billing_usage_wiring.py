"""The Billing page's usage counters had no writer, and a typo'd metric 500'd.

`billing_usage_records` / `billing_usage_summaries` — the tables behind the Usage
tab — had exactly one writer: `POST /billing/track`, which no part of the product
and no part of the frontend ever called. So every counter and every bar on that
tab read zero forever, while the *enforced* meter at `GET /usage` counted the
same investigations correctly. Two meters, one of them permanently blank.

And `MetricType(metric_type)` was constructed straight from a query parameter, so
`?metric=ai_investigations` — plural, the spelling the response body itself uses
— raised ValueError out of the handler and returned 500 "An unexpected error
occurred", which reads as an outage rather than as a typo.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.api.main as api
import app.config as cfg
from app import runtime, tenant_context
from app.api import billing_router
from app.billing.models import MetricType
from app.database import engine as db_engine
from app.database.orgs import org_uuid

AI = MetricType.AI_INVESTIGATION.value


def _reset() -> None:
    cfg.get_settings.cache_clear()
    tenant_context.current_tenant.set(None)
    api._RL.clear()
    api._reset_limiter()
    runtime.reset()
    db_engine._engine = None
    db_engine._session_factory = None
    billing_router._BILLING_STORE = None
    billing_router._STRIPE_SERVICE = None
    billing_router._BILLING_API = None


@pytest.fixture
def client(tmp_path, monkeypatch):
    """HTTP-level, for the routes."""
    monkeypatch.setenv("COPILOT_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/billing.db")
    monkeypatch.delenv("STRIPE_API_KEY", raising=False)
    _reset()
    with TestClient(api.app, raise_server_exceptions=False) as c:
        yield c
    _reset()


@pytest.fixture
async def db(tmp_path, monkeypatch):
    """In-loop, for the completion hook — it and the read must share an engine."""
    monkeypatch.setenv("COPILOT_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/billing.db")
    monkeypatch.delenv("STRIPE_API_KEY", raising=False)
    _reset()
    await db_engine.init_db()
    yield
    await db_engine.close_db()
    _reset()


async def _investigations() -> int:
    """How many AI investigations the billing meter has recorded this period.

    Note the plural: the metric is `ai_investigation`, but `get_usage` reports it
    under `limits.ai_investigations` — the same singular/plural split that made
    `?metric=ai_investigations` such an easy 500 to trigger.
    """
    usage = await billing_router._get_billing_api().get_usage(org_uuid())
    return int(usage["limits"]["ai_investigations"]["used"])


# --------------------------------------------------------------------------- #
# An unknown metric is the caller's mistake, not an outage
# --------------------------------------------------------------------------- #
class TestUnknownMetric:
    @pytest.mark.parametrize("metric", ["ai_investigations", "nonsense", ""])
    def test_check_limit_rejects_it_with_400(self, client, metric):
        r = client.get("/billing/usage/limit", params={"metric": metric})
        assert r.status_code == 400, r.text
        assert "unknown metric" in r.json()["detail"].lower()

    def test_the_error_lists_what_would_have_worked(self, client):
        r = client.get("/billing/usage/limit", params={"metric": "ai_investigations"})
        assert AI in r.json()["detail"]

    def test_track_rejects_it_with_400(self, client):
        r = client.post("/billing/track", json={"metric_type": "ai_investigations"})
        assert r.status_code == 400, r.text
        assert "unknown metric" in r.json()["detail"].lower()

    def test_a_real_metric_still_works(self, client):
        """The 400 is for genuinely unknown metrics, not a blanket refusal."""
        r = client.get("/billing/usage/limit", params={"metric": AI})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["within_limit"] is True
        assert body["limit"] == 25  # the starter allowance
        assert body["current_usage"] == 0


# --------------------------------------------------------------------------- #
# A completed investigation reaches the billing meter
# --------------------------------------------------------------------------- #
class TestInvestigationMetering:
    async def test_the_completion_hook_records_billing_usage(self, db):
        """`_meter_and_spend` is what runs when an investigation finishes."""
        before = await _investigations()

        await api._meter_and_spend(1234, idem="inv-1")

        assert await _investigations() == before + 1, "the Usage tab still has no writer"

    async def test_each_investigation_counts_once(self, db):
        for i in range(3):
            await api._meter_and_spend(100, idem=f"inv-{i}")

        assert await _investigations() == 3

    async def test_a_billing_failure_does_not_fail_the_investigation(self, db, monkeypatch):
        """Accounting runs after the work already succeeded; it must never undo it."""
        class Boom:
            async def track_usage(self, *a, **kw):
                raise RuntimeError("billing tables are on fire")

        monkeypatch.setattr(billing_router, "_get_billing_api", lambda: Boom())

        await billing_router.record_ai_investigation(10, "inv-x")  # must not raise

    async def test_the_token_count_travels_with_the_record(self, db, monkeypatch):
        seen: list[tuple] = []

        class Spy:
            async def track_usage(self, org_id, metric, metadata):
                seen.append((metric, metadata))

        monkeypatch.setattr(billing_router, "_get_billing_api", lambda: Spy())

        await billing_router.record_ai_investigation(4242, "inv-y")

        assert seen == [
            (AI, {"source": "investigation", "tokens": 4242, "idempotency_key": "inv-y"})
        ]

    async def test_a_tokenless_investigation_still_counts(self, db, monkeypatch):
        seen: list[tuple] = []

        class Spy:
            async def track_usage(self, org_id, metric, metadata):
                seen.append((metric, metadata))

        monkeypatch.setattr(billing_router, "_get_billing_api", lambda: Spy())

        await billing_router.record_ai_investigation(0, "")

        assert seen == [(AI, {"source": "investigation"})]
