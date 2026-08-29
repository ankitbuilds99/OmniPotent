"""The price of a model must not depend on how the provider spelled its id.

`CostOptimizer` priced by exact dict key and consulted `model_catalog.resolve()`
only while building its default table, so the ids that actually come back from
the providers — the dated snapshot the server resolved our alias to — missed the
table and were charged at the most-expensive-known fallback. The fast model that
runs plan/reflect/report/verify was billed at $15/$75 per MTok instead of $1/$5,
which is a fifteenfold over-report on every spend dashboard and a 402 budget gate
that fires at a fifteenth of the real spend.

These tests pin the invariant the file's own comments already claim: one id, one
answer, across every path in the deployment.
"""

from __future__ import annotations

import pytest

from app import llm, model_catalog
from app.model_router import usage_ledger
from app.model_router.cost_optimizer import CostOptimizer, ModelCost

# Dated snapshots of ids the catalog itself lists as aliases. None of them is in
# `_DEFAULT_COST_MODEL_IDS`, which is the whole point: the table cannot be the
# only thing that makes a spelling priceable.
DATED_IDS = (
    "claude-haiku-4-5-20251001",
    "claude-opus-4-8-20250101",
    "gpt-4o-2024-08-06",
)


class _Resp:
    """The shape `usage_ledger.model_id_of` reads off a LangChain response."""

    def __init__(self, model: str):
        self.response_metadata = {"model_name": model}


class TestDatedIdsArePricedAtTheirRealRate:
    def test_a_snapshot_id_is_not_billed_at_the_fallback_rate(self):
        optimizer = CostOptimizer()

        for model_id in DATED_IDS:
            catalog = model_catalog.estimate_cost_usd(model_id, 1_000_000, 0)
            assert catalog is not None, model_id
            assert optimizer.price(model_id, 1_000_000, 0) == pytest.approx(catalog)

    def test_the_fast_model_snapshot_costs_a_fifteenth_of_the_fallback(self):
        """The concrete number the bug got wrong: $1/MTok, not $15/MTok."""
        optimizer = CostOptimizer()

        assert optimizer.price(
            "claude-haiku-4-5-20251001", 1_000_000, 0
        ) == pytest.approx(1.0)

    def test_a_snapshot_costs_the_same_as_the_alias_it_resolves_to(self):
        optimizer = CostOptimizer()

        for model_id in DATED_IDS:
            info = model_catalog.resolve(model_id)
            assert info is not None, model_id
            assert optimizer.price(model_id, 100_000, 20_000) == pytest.approx(
                optimizer.price(info.id, 100_000, 20_000)
            ), model_id

    def test_every_default_model_snapshot_agrees_with_the_catalog(self):
        """Whatever `app/llm.py` resolves to, however the provider spells it back."""
        optimizer = CostOptimizer()

        for models in llm._DEFAULTS.values():
            for model_id in models.values():
                info = model_catalog.resolve(model_id)
                if info is None or not info.has_pricing:
                    continue
                for spelling in (model_id, *info.aliases):
                    catalog = model_catalog.estimate_cost_usd(spelling, 100_000, 20_000)
                    assert catalog is not None, spelling
                    assert optimizer.price(
                        spelling, 100_000, 20_000
                    ) == pytest.approx(catalog), spelling

    def test_estimate_cost_uses_the_resolved_cache_read_rate(self):
        """`with_caching` came off the fallback entry too, not just the headline."""
        optimizer = CostOptimizer()

        estimate = optimizer.estimate_cost(
            "claude-haiku-4-5-20251001", 1_000_000, 0, use_cache=True
        )
        alias = optimizer.estimate_cost("claude-haiku-4-5", 1_000_000, 0, use_cache=True)

        assert estimate.with_caching == pytest.approx(alias.with_caching)
        assert estimate.with_caching < estimate.estimated_cost


class TestTheLedgerPathIsPricedTheSameWay:
    def test_a_provider_reported_snapshot_id_is_priced_correctly(self):
        """The live path: nodes hand `model_id_of(resp)` straight to `note()`.

        Anthropic answers with the dated id it resolved our alias to, so this is
        what every real plan/reflect/report/verify call looked like — filed at
        $15/MTok into `llm_usage_records.cost`, which is what `over_budget()`
        sums behind the 402 and what /stats and /usage/* render as measured spend.
        """
        usage_ledger.reset()

        model_id = usage_ledger.model_id_of(_Resp("claude-haiku-4-5-20251001"))
        pending = usage_ledger.note(model_id, 1_000_000, 0)

        assert pending.model_id == "claude-haiku-4-5-20251001"
        assert pending.cost == pytest.approx(1.0)
        usage_ledger.reset()


class TestAnUnpriceableIdStillFailsLoudly:
    def test_an_id_the_catalog_cannot_resolve_keeps_the_dear_fallback(self):
        """Resolution must not become a way to invent a rate for a fake model."""
        optimizer = CostOptimizer()
        dearest = max(optimizer.model_costs.values(), key=lambda c: c.avg_cost_per_1k)

        for fake in ("unknown-model", "claude-haiku-4-20250514", "gpt-9o-2030-01-01"):
            assert model_catalog.resolve(fake) is None, fake
            assert optimizer.get_model_cost(fake) is None, fake
            assert optimizer.price(fake, 10_000, 10_000) == pytest.approx(
                optimizer.price(dearest.model_id, 10_000, 10_000)
            ), fake

    def test_get_model_cost_reports_a_resolvable_id_as_priced(self):
        """`/model-router/models` and `AdaptiveRouter` read this.

        A verified rate reached through a snapshot suffix is a price, so it is
        reported as one; "unmeasured" is reserved for ids nobody prices.
        """
        optimizer = CostOptimizer()

        cost = optimizer.get_model_cost("claude-haiku-4-5-20251001")
        assert cost is not None
        assert cost.input_cost_per_1k == pytest.approx(0.001)
        assert optimizer.get_model_cost("no-such-model-anywhere") is None


class TestExplicitRatesStillWin:
    def test_a_registered_cost_overrides_the_catalog(self):
        optimizer = CostOptimizer()
        optimizer.add_model_cost(
            ModelCost(
                model_id="claude-haiku-4-5-20251001",
                input_cost_per_1k=0.5,
                output_cost_per_1k=0.5,
            )
        )

        assert optimizer.price("claude-haiku-4-5-20251001", 1_000, 0) == pytest.approx(0.5)

    def test_a_caller_supplied_table_is_still_consulted_first(self):
        house = ModelCost(
            model_id="house-model", input_cost_per_1k=0.002, output_cost_per_1k=0.004
        )
        optimizer = CostOptimizer(model_costs={"house-model": house})

        assert optimizer.price("house-model", 1_000, 1_000) == pytest.approx(0.006)
        # An id the catalog prices is priced by the catalog, not by the one
        # entry that happens to be in this table.
        assert optimizer.price("claude-haiku-4-5-20251001", 1_000_000, 0) == pytest.approx(1.0)
