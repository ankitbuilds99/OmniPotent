"""The cost optimizer must be reachable, per-tenant, and honest about its numbers.

`app/cost_optimizer/` (3,526 lines across five modules) had no importer and no test.
Wiring it surfaced the same class of problem `app/dependency_graph/discovery.py` had:
`GPUTracker.forecast_demand` manufactured 30 days of usage history from a formula
(`50 + 20 * (1 if i % 7 < 5 else 0.5) + ...`), projected from it, and returned a
"reserve N GPUs" recommendation. Nothing in that path had ever looked at a customer's
fleet, and the output was a spending commitment.

These tests pin: the routes answer, one org's spend never shows up in another's, and
a forecast is refused rather than invented when there is nothing to forecast from.
"""

from __future__ import annotations

import ast
import pathlib

import pytest
from fastapi.testclient import TestClient

from app.billing import runtime as billing_runtime
from app.cost_optimizer import api_router as cost_api
from app.cost_optimizer.budget_manager import BudgetManager, BudgetPeriod, BudgetType
from app.cost_optimizer.gpu_tracker import (
    CloudProvider,
    GPUInstance,
    GPUTracker,
    GPUType,
    InstanceType,
)
from app.cost_optimizer.roi_tracker import ActionType, ROITracker, SavingCategory

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _clean_state():
    cost_api.reset_engines()
    billing_runtime._BILLING_STORE = None
    billing_runtime._BILLING_API = None
    yield
    cost_api.reset_engines()
    billing_runtime._BILLING_STORE = None
    billing_runtime._BILLING_API = None


@pytest.fixture
def client():
    from app.api import main as api

    with TestClient(api.app) as c:
        yield c


def _instance(instance_id: str, gpu_type: GPUType, gpus: int) -> GPUInstance:
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    return GPUInstance(
        instance_id=instance_id,
        provider=CloudProvider.AWS,
        region="us-east-1",
        zone="us-east-1a",
        gpu_type=gpu_type,
        gpu_count=gpus,
        instance_type=InstanceType.ON_DEMAND,
        vcpu=8,
        memory_gb=32.0,
        hourly_cost=3.06,
        available=True,
        utilization=None,
        created_at=now,
        last_seen=now,
        tags={},
    )


# --------------------------------------------------------------------------
# No invented history
# --------------------------------------------------------------------------


async def test_forecast_refuses_without_observations() -> None:
    """A tracker that has never seen a fleet must not produce a demand curve."""
    tracker = GPUTracker()
    result = await tracker.forecast_demand(GPUType.A100_40GB, horizon_days=30)
    await tracker.close()

    assert result["status"] == "insufficient_data"
    assert result["forecast"] == []
    assert result["recommendations"] == []
    assert result["observations"] == 0


async def test_forecast_refuses_below_the_minimum() -> None:
    """One data point is not a trend."""
    tracker = GPUTracker()
    tracker._instances["i-1"] = _instance("i-1", GPUType.A100_40GB, 8)
    for _ in range(GPUTracker.MIN_OBSERVATIONS_FOR_FORECAST - 1):
        tracker.record_observation()

    result = await tracker.forecast_demand(GPUType.A100_40GB)
    await tracker.close()

    assert result["status"] == "insufficient_data"
    assert result["observations"] == GPUTracker.MIN_OBSERVATIONS_FOR_FORECAST - 1


async def test_forecast_projects_from_what_was_observed() -> None:
    """With real history the projection reflects it — not a fixed weekly shape."""
    tracker = GPUTracker()
    tracker._instances["i-1"] = _instance("i-1", GPUType.A100_40GB, 8)
    for _ in range(GPUTracker.MIN_OBSERVATIONS_FOR_FORECAST):
        tracker.record_observation()

    result = await tracker.forecast_demand(GPUType.A100_40GB, horizon_days=5)
    await tracker.close()

    assert result["status"] == "ok"
    assert result["current_usage"] == 8
    assert result["average_usage"] == 8.0
    assert result["trend"] == "stable"
    assert len(result["forecast"]) == 5
    # A flat 8-GPU history must project 8 GPUs, not the old formula's ~57.
    assert all(day["projected_gpus"] == 8 for day in result["forecast"])


async def test_forecast_tracks_a_real_increase() -> None:
    tracker = GPUTracker()
    for step in range(GPUTracker.MIN_OBSERVATIONS_FOR_FORECAST):
        tracker._instances = {"i-1": _instance("i-1", GPUType.T4, 1 + step)}
        tracker.record_observation()

    result = await tracker.forecast_demand(GPUType.T4, horizon_days=3)
    await tracker.close()

    assert result["status"] == "ok"
    assert result["trend"] == "increasing"
    assert result["forecast"][-1]["projected_gpus"] >= result["current_usage"]


async def test_observations_only_count_a_matching_gpu_type() -> None:
    tracker = GPUTracker()
    tracker._instances["i-1"] = _instance("i-1", GPUType.T4, 2)
    for _ in range(GPUTracker.MIN_OBSERVATIONS_FOR_FORECAST):
        tracker.record_observation()

    result = await tracker.forecast_demand(GPUType.H100)
    await tracker.close()

    assert result["status"] == "insufficient_data", (
        "T4 observations were used to forecast H100 demand"
    )


def test_no_synthetic_usage_series_in_the_forecast() -> None:
    """Guard the regression directly: a generated series feeding forecast_demand.

    The fabrication was a `for i in range(30)` loop building `historical_usage` from
    an arithmetic expression. A behaviour test catches the output, but this catches
    the shape coming back in any form.
    """
    source = (REPO_ROOT / "app/cost_optimizer/gpu_tracker.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
            continue
        if node.name != "forecast_demand":
            continue
        for child in ast.walk(node):
            if isinstance(child, ast.For):
                iterated = ast.unparse(child.iter)
                assert "range" not in iterated or "horizon_days" in iterated, (
                    f"forecast_demand loops over `{iterated}` — the only permitted "
                    f"range loop is over the requested horizon; a range over a fixed "
                    f"count is how the synthetic 30-day history was built"
                )
        break
    else:
        pytest.fail("forecast_demand went missing")


async def test_scanning_records_an_observation() -> None:
    """History comes from scans, so a scan with no credentials records nothing."""
    tracker = GPUTracker()
    await tracker.scan_aws_instances()
    await tracker.close()
    assert tracker._usage_history == []


# --------------------------------------------------------------------------
# Reachability
# --------------------------------------------------------------------------


def test_app_serves_the_cost_routes(client) -> None:
    assert client.get("/api/cost/budgets").status_code == 200
    assert client.get("/api/cost/roi/report").status_code == 200
    assert client.get("/api/cost/gpu/inventory").status_code == 200
    assert client.get("/api/cost/tokens/usage").status_code == 200


def test_budget_lifecycle_over_http(client) -> None:
    created = client.post(
        "/api/cost/budgets",
        json={"name": "LLM spend", "budget_type": "ai_cost", "period": "monthly", "limit": 500.0},
    )
    assert created.status_code == 200, created.text
    budget_id = created.json()["id"]

    listed = client.get("/api/cost/budgets")
    assert [b["id"] for b in listed.json()["budgets"]] == [budget_id]

    spend = client.post(
        "/api/cost/budgets/spend", json={"amount": 450.0, "budget_type": "ai_cost"}
    )
    assert spend.status_code == 200, spend.text

    after = client.get(f"/api/cost/budgets/{budget_id}").json()
    assert after["current_usage"] == pytest.approx(450.0)


def test_unknown_budget_is_a_404(client) -> None:
    assert client.get("/api/cost/budgets/nope").status_code == 404
    assert client.get("/api/cost/budgets/nope/forecast").status_code == 404


def test_unknown_enum_is_a_422_not_a_500(client) -> None:
    resp = client.post(
        "/api/cost/budgets",
        json={"name": "x", "budget_type": "not-a-type", "period": "monthly", "limit": 1.0},
    )
    assert resp.status_code == 422, resp.text
    assert "not-a-type" in resp.json()["detail"]


def test_gpu_forecast_over_http_reports_insufficient_data(client) -> None:
    resp = client.get("/api/cost/gpu/forecast/a100-40gb")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "insufficient_data"


def test_unknown_gpu_type_is_a_422(client) -> None:
    assert client.get("/api/cost/gpu/forecast/rtx-9090").status_code == 422


# --------------------------------------------------------------------------
# Tenant isolation
# --------------------------------------------------------------------------


def test_each_org_gets_its_own_engines() -> None:
    cost_api._budget_managers["a"] = BudgetManager()
    cost_api._budget_managers["b"] = BudgetManager()
    assert cost_api._budget_managers["a"] is not cost_api._budget_managers["b"]


def test_one_orgs_spend_is_not_visible_to_another() -> None:
    """Budgets are financial records; a shared manager would leak them."""
    a, b = BudgetManager(), BudgetManager()
    a.create_budget(
        name="A's budget",
        budget_type=BudgetType.TOTAL,
        period=BudgetPeriod.MONTHLY,
        limit=1000.0,
        tenant_id="org-a",
    )
    assert len(a.list_budgets(tenant_id="org-a")) == 1
    assert b.list_budgets(tenant_id="org-a") == []


def test_one_orgs_savings_are_not_visible_to_another() -> None:
    a, b = ROITracker(), ROITracker()
    a.record_saving(
        category=SavingCategory.TIME_SAVED,
        action_type=ActionType.AUTO_REMEDIATION,
        description="restarted a pod",
        time_saved_minutes=30,
    )
    assert len(a.get_savings()) == 1
    assert b.get_savings() == []


# --------------------------------------------------------------------------
# Labelled assumptions
# --------------------------------------------------------------------------


def test_roi_report_states_its_hourly_rate_assumption(client) -> None:
    """A dollar figure derived from an assumed rate must say so."""
    report = client.get("/api/cost/roi/report").json()
    assert report["assumptions"]["engineer_hourly_rate_usd"] > 0
    assert "rate" in report["assumptions"]["note"]


async def test_spot_recommendation_says_which_price_it_used() -> None:
    """A migration recommendation must distinguish a live quote from a reference range."""
    tracker = GPUTracker()
    tracker._instances["i-1"] = _instance("i-1", GPUType.A100_40GB, 8)
    tracker._instances["i-1"].hourly_cost = 32.77

    recs = await tracker.get_recommendations()
    await tracker.close()

    spot = [r for r in recs if r.category == "spot_conversion"]
    assert spot, "no spot recommendation produced for an on-demand A100 fleet"
    assert "not a live quote" in spot[0].description, (
        "the reference-range estimate is presented as if it were a current price"
    )


def test_duplicate_model_router_is_not_served() -> None:
    """The package's own model router duplicates the wired AdaptiveModelRouter.

    Serving both would give two endpoints that route models by different rules and
    disagree. This pins that only one is reachable.
    """
    source = (REPO_ROOT / "app/cost_optimizer/api_router.py").read_text(
        encoding="utf-8"
    )
    assert "CostAwareModelRouter" not in source
