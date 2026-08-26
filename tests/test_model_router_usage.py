"""LLM spend is recorded by the product, persisted, and actually enforced.

Three separate defects met in `CostOptimizer`:

1. **Nothing recorded real traffic.** `app/graph/nodes.py:_log_usage` read
   `usage_metadata` off every model response, wrote one `log.info` line and threw
   the numbers away. `CostOptimizer.record_usage` had no caller anywhere in
   `app/`, so the only rows /usage/daily, /usage/monthly and /stats ever had were
   the ones a client POSTed by hand.
2. **Storage was process memory.** `self._usage_history` (a list) and
   `self._customer_budgets` (a dict) meant a restart zeroed the bill and a second
   worker disagreed with the first about what had been spent.
3. **The budget check enforced nothing.** `check_budget` had no callers outside
   its own HTTP handler, so `can_proceed: false` was an opinion, and the only
   settable cap was the monthly one — `daily_remaining` was `null` forever.

These tests cover all three against the real tables (`llm_usage_records`,
`llm_customer_budgets`), with a fresh engine standing in for a fresh process.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

import app.config as cfg
from app import tenant_context
from app.database import engine as db_engine
from app.database.orgs import org_uuid_for
from app.graph.nodes import _log_usage
from app.model_router import usage_ledger, usage_store
from app.tenant_context import TenantConfig

ORG_A = TenantConfig(org_id="org-a")
ORG_B = TenantConfig(org_id="org-b")


@pytest.fixture
def db(tmp_path, monkeypatch):
    """A real database, torn down and rebuilt around each test."""
    monkeypatch.setenv("COPILOT_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/app.db")
    cfg.get_settings.cache_clear()
    db_engine._engine = None
    db_engine._session_factory = None
    tenant_context.current_tenant.set(None)
    usage_ledger.reset()

    asyncio.run(db_engine.init_db())
    yield

    asyncio.run(db_engine.close_db())
    usage_ledger.reset()
    cfg.get_settings.cache_clear()
    db_engine._engine = None
    db_engine._session_factory = None
    tenant_context.current_tenant.set(None)


def _run(coro_factory, tenant: TenantConfig | None = None):
    """One call on its own loop, the way a separate request would make it."""

    async def go():
        token = tenant_context.set_tenant(tenant)
        try:
            return await coro_factory()
        finally:
            tenant_context.reset_tenant(token)

    return asyncio.run(go())


def _restart() -> None:
    """Drop the engine and session factory: the next call builds its own, which
    is what a second worker (or the same process after a restart) does."""
    asyncio.run(db_engine.close_db())
    db_engine._engine = None
    db_engine._session_factory = None


class _Resp:
    """The shape of a LangChain chat response as `_log_usage` reads it."""

    def __init__(self, model: str, input_tokens: int, output_tokens: int):
        self.response_metadata = {"model_name": model}
        self.usage_metadata = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        }


# --------------------------------------------------------------------------- #
# 1. The product records what it spends.
# --------------------------------------------------------------------------- #
class TestTheGraphRecordsItsOwnSpend:
    def test_a_model_call_in_a_node_is_queued_for_the_ledger(self):
        """`_log_usage` used to only write a log line. A log line is not accounting."""
        usage_ledger.reset()

        returned = _log_usage("plan", _Resp("claude-haiku-4-5", 100_000, 20_000))

        assert returned == 120_000  # still returns tokens for the run budget
        assert usage_ledger.pending_count() == 1
        (queued,) = usage_ledger.drain()
        assert queued.model_id == "claude-haiku-4-5"
        assert queued.input_tokens == 100_000
        assert queued.output_tokens == 20_000
        assert queued.request_type == "plan"  # which node made the call
        # $1/$5 per MTok = 0.1 + 0.1
        assert queued.cost == pytest.approx(0.2)

    def test_a_response_without_usage_metadata_records_nothing(self):
        """Some providers omit it; inventing a cost would be worse than none."""
        usage_ledger.reset()

        assert _log_usage("agent", object()) == 0
        assert usage_ledger.pending_count() == 0

    def test_an_unknown_model_is_filed_as_unknown_not_guessed(self):
        usage_ledger.reset()
        resp = _Resp("claude-haiku-4-5", 10, 10)
        resp.response_metadata = {}

        _log_usage("agent", resp)

        (queued,) = usage_ledger.drain()
        assert queued.model_id == "unknown"

    def test_flush_writes_the_queue_to_the_ledger_table(self, db):
        """The end of a turn (`_meter_and_spend`) is where the queue lands."""
        _run(lambda: _note_and_flush("gpt-4o", 1_000, 1_000, "agent"), ORG_A)

        usage = _run(lambda: usage_store.monthly_usage(), ORG_A)
        assert usage["total_requests"] == 1
        assert usage["total_cost"] == pytest.approx(0.0125)  # $2.50/$10 per MTok
        assert usage["by_model"]["gpt-4o"]["requests"] == 1

    def test_a_flush_attributes_spend_to_the_org_that_made_the_call(self, db):
        """The queue is process-wide and flushed later, so the org has to be
        captured when the call happens — not read off whoever is flushing."""
        _run(lambda: _note_only("gpt-4o", 1_000, 1_000), ORG_A)
        # ORG_B is the one that happens to flush.
        _run(lambda: usage_ledger.flush(), ORG_B)

        assert _run(lambda: usage_store.monthly_usage(), ORG_A)["total_requests"] == 1
        assert _run(lambda: usage_store.monthly_usage(), ORG_B)["total_requests"] == 0

    def test_a_failed_flush_does_not_break_the_turn(self, db, monkeypatch):
        """Bookkeeping must not turn an investigation that answered into an error."""
        async def boom(_batch):
            raise RuntimeError("database is down")

        monkeypatch.setattr(usage_store, "write", boom)
        usage_ledger.note("gpt-4o", 10, 10)

        assert _run(lambda: usage_ledger.flush(), ORG_A) == 0
        assert usage_ledger.pending_count() == 0


async def _note_and_flush(model: str, tin: int, tout: int, node: str) -> None:
    usage_ledger.note(model, tin, tout, request_type=node)
    await usage_ledger.flush()


async def _note_only(model: str, tin: int, tout: int) -> None:
    usage_ledger.note(model, tin, tout)


# --------------------------------------------------------------------------- #
# 2. Spend and budgets outlive the process.
# --------------------------------------------------------------------------- #
class TestSpendOutlivesTheProcess:
    def test_recorded_usage_survives_a_restart(self, db):
        _run(
            lambda: usage_store.record("gpt-4o", 1_000, 1_000, cost=0.02),
            ORG_A,
        )
        _restart()

        usage = _run(lambda: usage_store.monthly_usage(), ORG_A)
        assert usage["total_cost"] == pytest.approx(0.02)
        assert usage["total_requests"] == 1

    def test_a_budget_survives_a_restart(self, db):
        _run(lambda: usage_store.set_budget(monthly_limit=100.0), ORG_A)
        _restart()

        verdict = _run(lambda: usage_store.check_budget(), ORG_A)
        assert verdict["monthly_remaining"] == pytest.approx(100.0)

    def test_one_orgs_spend_is_invisible_to_another(self, db):
        _run(lambda: usage_store.record("gpt-4o", 1_000, 1_000, cost=0.02), ORG_A)

        assert _run(lambda: usage_store.monthly_usage(), ORG_B)["total_cost"] == 0.0
        assert _run(lambda: usage_store.check_budget(), ORG_B)["monthly_remaining"] is None

    def test_usage_is_stored_under_the_orgs_own_id(self, db):
        """Not the request body's customer_id: that is a label inside the org."""
        record = _run(
            lambda: usage_store.record(
                "gpt-4o", 10, 10, cost=0.001, customer_id="somebody-elses-org"
            ),
            ORG_A,
        )

        assert record.org == org_uuid_for("org-a")
        # ...and asking as another org sees nothing, whatever label was passed.
        assert (
            _run(
                lambda: usage_store.monthly_usage("somebody-elses-org"), ORG_B
            )["total_requests"]
            == 0
        )

    def test_a_sub_customers_spend_is_separable_within_the_org(self, db):
        _run(lambda: usage_store.record("gpt-4o", 10, 10, cost=0.5, customer_id="acme"), ORG_A)
        _run(lambda: usage_store.record("gpt-4o", 10, 10, cost=0.25, customer_id="globex"), ORG_A)

        assert _run(lambda: usage_store.monthly_usage("acme"), ORG_A)["total_cost"] == 0.5
        # No customer means the whole org.
        assert _run(lambda: usage_store.monthly_usage(), ORG_A)["total_cost"] == 0.75


# --------------------------------------------------------------------------- #
# 3. Both caps are settable, and the budget is enforced.
# --------------------------------------------------------------------------- #
class TestBudgetsAreSettableAndEnforced:
    def test_a_daily_cap_can_be_set_and_is_reported(self, db):
        """`daily_remaining` was permanently null: the only way to set a daily
        cap was a constructor kwarg no endpoint ever passed."""
        _run(lambda: usage_store.set_budget(daily_limit=5.0, monthly_limit=50.0), ORG_A)
        _run(lambda: usage_store.record("gpt-4o", 1_000, 1_000, cost=1.0), ORG_A)

        verdict = _run(lambda: usage_store.check_budget(), ORG_A)
        assert verdict["daily_remaining"] == pytest.approx(4.0)
        assert verdict["monthly_remaining"] == pytest.approx(49.0)
        assert verdict["can_proceed"] is True

    def test_exceeding_the_daily_cap_blocks_even_under_the_monthly_one(self, db):
        _run(lambda: usage_store.set_budget(daily_limit=1.0, monthly_limit=1000.0), ORG_A)
        _run(lambda: usage_store.record("gpt-4o", 1_000, 1_000, cost=1.5), ORG_A)

        assert _run(lambda: usage_store.over_budget(), ORG_A).startswith("Daily budget exceeded")

    def test_no_budget_row_means_no_cap_rather_than_a_made_up_one(self, db):
        assert _run(lambda: usage_store.over_budget(), ORG_A) == ""
        verdict = _run(lambda: usage_store.check_budget(), ORG_A)
        assert verdict["can_proceed"] is True
        assert verdict["daily_remaining"] is None
        assert verdict["monthly_remaining"] is None

    def test_a_zero_budget_is_a_real_cap(self, db):
        """`if self.budget_limit_daily:` read 0 as 'unset' and let spend through."""
        _run(lambda: usage_store.set_budget(monthly_limit=0.0), ORG_A)

        assert _run(lambda: usage_store.over_budget(0.01), ORG_A) != ""

    def test_setting_a_budget_twice_updates_the_row(self, db):
        _run(lambda: usage_store.set_budget(monthly_limit=10.0), ORG_A)
        _run(lambda: usage_store.set_budget(monthly_limit=20.0), ORG_A)

        assert _run(lambda: usage_store.get_budget(), ORG_A)["monthly_limit"] == 20.0

    def test_one_orgs_cap_does_not_stop_another(self, db):
        _run(lambda: usage_store.set_budget(monthly_limit=0.001), ORG_A)
        _run(lambda: usage_store.record("gpt-4o", 1_000, 1_000, cost=5.0), ORG_A)

        assert _run(lambda: usage_store.over_budget(), ORG_A) != ""
        assert _run(lambda: usage_store.over_budget(), ORG_B) == ""

    def test_the_investigation_gate_refuses_an_over_budget_org(self, db):
        """`check_budget` existed but nothing called it — this is the enforcement."""
        from fastapi import HTTPException

        from app.api.main import quota_gate

        _run(lambda: usage_store.set_budget(monthly_limit=0.001), ORG_A)
        _run(lambda: usage_store.record("gpt-4o", 1_000, 1_000, cost=0.02), ORG_A)

        with pytest.raises(HTTPException) as excinfo:
            _run(lambda: quota_gate(), ORG_A)
        assert excinfo.value.status_code == 402
        assert "budget" in excinfo.value.detail.lower()

        # An org that never set a cap is unaffected.
        _run(lambda: quota_gate(), ORG_B)

    def test_an_unreadable_ledger_does_not_refuse_every_investigation(
        self, db, monkeypatch
    ):
        """Failing closed here would take the product down for every deployment
        that never set a budget, which is worse than briefly over-spending."""
        async def boom(*_a, **_kw):
            raise RuntimeError("database is down")

        monkeypatch.setattr(usage_store, "_budget_row", boom)

        assert _run(lambda: usage_store.over_budget(), ORG_A) == ""


# --------------------------------------------------------------------------- #
# 4. The HTTP surface reads the same rows.
# --------------------------------------------------------------------------- #
class TestTheEndpointsReadThePersistedLedger:
    def test_usage_and_stats_report_persisted_rows_after_a_restart(self, db):
        from fastapi.testclient import TestClient

        import app.api.main as api

        _run(lambda: usage_store.set_budget(daily_limit=9.0, monthly_limit=90.0))
        _run(lambda: usage_store.record("gpt-4o", 1_000, 1_000, cost=0.02))
        _restart()

        with TestClient(api.app) as client:
            monthly = client.get("/api/model-router/usage/monthly").json()
            daily = client.get("/api/model-router/usage/daily").json()
            check = client.get("/api/model-router/budget/check").json()
            stats = client.get("/api/model-router/stats").json()

        assert monthly["total_cost"] == pytest.approx(0.02)
        assert daily["total_requests"] == 1
        # Both caps come back as numbers, not null.
        assert check["daily_remaining"] == pytest.approx(8.98)
        assert check["monthly_remaining"] == pytest.approx(89.98)
        assert stats["cost_analytics"]["total_cost"] == pytest.approx(0.02)
        assert stats["cost_analytics"]["total_tokens"] == 2_000

    def test_record_usage_prices_the_call_from_the_cost_table(self, db):
        from fastapi.testclient import TestClient

        import app.api.main as api

        with TestClient(api.app) as client:
            body = {
                "model_id": "claude-haiku-4-5",
                "input_tokens": 100_000,
                "output_tokens": 20_000,
            }
            posted = client.post("/api/model-router/record-usage", json=body).json()
            monthly = client.get("/api/model-router/usage/monthly").json()

        # $1/$5 per MTok — not the $3.00 the most-expensive-known fallback charged.
        assert posted["cost"] == pytest.approx(0.2)
        assert monthly["total_cost"] == pytest.approx(0.2)

    def test_budget_set_accepts_a_daily_cap(self, db):
        from fastapi.testclient import TestClient

        import app.api.main as api

        with TestClient(api.app) as client:
            resp = client.post(
                "/api/model-router/budget/set",
                json={"monthly_budget": 100.0, "daily_budget": 10.0},
            ).json()
            check = client.get("/api/model-router/budget/check").json()

        assert resp["daily_limit"] == 10.0
        assert resp["monthly_limit"] == 100.0
        assert check["daily_remaining"] == pytest.approx(10.0)


# --------------------------------------------------------------------------- #
# 5. An actual investigation's spend reaches the table.
# --------------------------------------------------------------------------- #
class _SpendingSession:
    """A session that makes one LLM call, the way a graph node does.

    `usage_ledger.note` is exactly what `app/graph/nodes.py:_log_usage` calls;
    what this exercises is the other half — that the end of a chat turn flushes
    the queue to the database. Stubbing the session (rather than running the
    real graph, which needs an LLM key and ~20 MCP subprocesses) keeps the test
    hermetic while still driving the real endpoint end to end.
    """

    def __init__(self, thread_id: str = "default") -> None:
        self.thread_id = thread_id

    async def __aenter__(self) -> "_SpendingSession":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def pending_interrupt(self) -> bool:
        return False

    async def ask_stream(self, question: str):
        usage_ledger.note("gpt-4o", 1_000, 1_000, request_type="agent")
        yield {
            "type": "done",
            "final_text": "the checkout deploy at 12:00 did it",
            "trace": [],
            "report": None,
            "tokens_used": 2_000,
        }


class TestAnInvestigationsSpendIsBilled:
    def test_a_chat_turn_writes_its_llm_cost_to_the_ledger(self, db, monkeypatch):
        """Before this, an investigation's cost existed only as a log line: the
        cost dashboard reported on hand-POSTed rows and nothing else."""
        from fastapi.testclient import TestClient

        import app.api.main as api

        monkeypatch.setattr(api, "CopilotSession", _SpendingSession)
        api._SESSIONS.clear()
        api._RL.clear()

        with TestClient(api.app) as client:
            resp = client.post(
                "/chat/stream", json={"thread_id": "spend-1", "message": "why 500s?"}
            )
            assert resp.status_code == 200, resp.text
            usage = client.get("/api/model-router/usage/monthly").json()
            stats = client.get("/api/model-router/stats").json()

        api._SESSIONS.clear()

        assert usage["total_requests"] == 1, usage
        assert usage["total_cost"] == pytest.approx(0.0125)  # $2.50/$10 per MTok
        assert usage["by_model"]["gpt-4o"]["requests"] == 1
        assert stats["cost_analytics"]["total_tokens"] == 2_000
        assert usage_ledger.pending_count() == 0  # flushed, not left on the queue


def test_a_random_org_id_maps_to_a_stable_uuid():
    """Two workers must derive the same org id from the same tenant string."""
    tid = f"tenant-{uuid4()}"
    assert org_uuid_for(tid) == org_uuid_for(tid)
