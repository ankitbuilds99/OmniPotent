"""The model router's two numbers that were confidently wrong.

Both produced a plausible answer rather than an error, which is why neither
showed up as a failure: a declared P0 got routed to a mid-tier model, and every
call the product actually makes was billed at exactly $0.00.
"""

from __future__ import annotations

import pytest

from app import llm
from app.model_router.complexity_analyzer import ComplexityAnalyzer, ComplexityLevel
from app.model_router.cost_optimizer import CostOptimizer, ModelCost

# Nothing in this text matches a high-stakes keyword, so the severity is the
# only thing raising the stakes score — which is the case that was broken.
PLAIN_QUERY = "the checkout service is returning errors"


class TestSeverityRouting:
    def test_a_declared_p0_is_critical(self):
        """`stakes > 0.5` when a P0 contributes exactly 0.50.

        The comparison excluded the value it was written for, so a P0 fell
        through to the next branch and scored MEDIUM — which
        COMPLEXITY_TIER_MAP routes to the balanced model, not the premium one.
        """
        analyzer = ComplexityAnalyzer()

        score = analyzer.analyze(PLAIN_QUERY, incident_severity="p0")

        assert score.stakes_factor == 0.5
        assert score.level is ComplexityLevel.CRITICAL

    def test_every_spelling_of_the_top_severity_is_critical(self):
        analyzer = ComplexityAnalyzer()

        for severity in ("p0", "P0", "sev0", "critical"):
            score = analyzer.analyze(PLAIN_QUERY, incident_severity=severity)
            assert score.level is ComplexityLevel.CRITICAL, severity

    def test_a_declared_p1_is_not_treated_as_no_severity_at_all(self):
        """A P1 contributes exactly 0.30 and `stakes > 0.3` excluded it.

        It landed on the score-based branch, so a short P1 query scored
        TRIVIAL — the same level as passing no severity — and went to the
        fast model.
        """
        analyzer = ComplexityAnalyzer()

        p1 = analyzer.analyze(PLAIN_QUERY, incident_severity="p1")
        none = analyzer.analyze(PLAIN_QUERY)

        assert p1.stakes_factor == 0.3
        assert none.level is ComplexityLevel.TRIVIAL
        assert p1.level is not none.level
        assert p1.level is ComplexityLevel.MEDIUM

    def test_severity_still_orders_the_levels(self):
        analyzer = ComplexityAnalyzer()
        levels = [
            analyzer.analyze(PLAIN_QUERY, incident_severity=s).level
            for s in (None, "p2", "p1", "p0")
        ]

        order = list(ComplexityLevel)
        assert [order.index(x) for x in levels] == sorted(order.index(x) for x in levels)


class TestPricing:
    def test_every_model_the_llm_factory_can_pick_has_its_own_price(self):
        """The table is only useful if it covers what `app/llm.py` resolves to.

        COPILOT_MODEL / COPILOT_FAST_MODEL default to "", so `_DEFAULTS` is what
        real traffic runs on — and none of those ids were priced. Each one fell
        through to the most-expensive-known fallback, which billed the cheap
        fast model (plan/reflect/report/verify) at ~15x its real rate. This is
        the drift guard: adding a provider default without a price fails here.
        """
        optimizer = CostOptimizer()

        for provider, models in llm._DEFAULTS.items():
            for which, model_id in models.items():
                assert optimizer.get_model_cost(model_id) is not None, (
                    f"{provider}/{which} = {model_id} has no price"
                )

    def test_the_fast_model_is_cheaper_than_the_main_one(self):
        """A fallback price makes every unpriced model cost the same, which is
        how 'fast' ended up billing more than Opus. Ordering must be real."""
        optimizer = CostOptimizer()

        for provider, models in llm._DEFAULTS.items():
            main = optimizer.get_model_cost(models["main"])
            fast = optimizer.get_model_cost(models["fast"])
            assert main is not None and fast is not None, provider
            if models["main"] == models["fast"]:
                continue  # deepseek uses one model for both
            assert fast.avg_cost_per_1k < main.avg_cost_per_1k, provider

    def test_the_default_anthropic_models_are_priced_at_their_list_rate(self):
        """100k in / 20k out, the shape of one investigation turn."""
        optimizer = CostOptimizer()

        opus = optimizer.estimate_cost("claude-opus-4-8", 100_000, 20_000)
        haiku = optimizer.estimate_cost("claude-haiku-4-5", 100_000, 20_000)

        # $5/$25 per MTok and $1/$5 per MTok respectively — not $3.00 apiece,
        # which is what the Opus-3 fallback rate charged for both.
        assert opus.estimated_cost == pytest.approx(1.0)
        assert haiku.estimated_cost == pytest.approx(0.2)

    def test_the_models_the_product_actually_calls_have_a_price(self):
        """The table shipped with only `-latest` aliases.

        Everything else in app/ passes dated ids to the SDK, so every real
        call missed the table and was priced at zero.
        """
        optimizer = CostOptimizer()

        for model_id in (
            "claude-opus-4-20250514",
            "claude-sonnet-4-20250514",
            "claude-haiku-4-20250514",
            "claude-3-5-sonnet-20241022",
            "claude-3-haiku-20240307",
        ):
            assert optimizer.get_model_cost(model_id) is not None, model_id
            estimate = optimizer.estimate_cost(model_id, input_tokens=10_000)
            assert estimate.estimated_cost > 0, model_id

    def test_an_unpriced_model_is_not_free(self):
        """A miss used to cost $0.00, which reads as an answer rather than a gap."""
        optimizer = CostOptimizer()

        estimate = optimizer.estimate_cost("some-model-we-have-never-heard-of", 10_000)

        assert estimate.estimated_cost > 0

    def test_an_unpriced_model_is_charged_at_the_most_expensive_known_rate(self):
        """Guess high: this number is what a budget limit is compared against."""
        optimizer = CostOptimizer()
        dearest = max(optimizer.model_costs.values(), key=lambda c: c.avg_cost_per_1k)

        unknown = optimizer.estimate_cost("unknown-model", 10_000, 10_000)
        known = optimizer.estimate_cost(dearest.model_id, 10_000, 10_000)

        assert unknown.estimated_cost == known.estimated_cost

    def test_get_model_cost_still_reports_a_miss_as_a_miss(self):
        """The fallback prices a call; it must not claim the model is known.

        `AdaptiveRouter` drops the cost term from its score and reports
        `cost_per_1k_tokens: None` rather than a made-up figure, which is the
        honest thing to do when ranking models.
        """
        assert CostOptimizer().get_model_cost("unknown-model") is None

    def test_usage_on_an_unpriced_model_counts_against_the_budget(self):
        """The bug that mattered: recorded spend fed check_budget as zero.

        A customer whose model was not in the table could never hit their cap,
        because every call they made was filed as costing nothing.
        """
        optimizer = CostOptimizer(budget_limit_daily=1.0)

        record = optimizer.record_usage(
            "unknown-model", input_tokens=1_000_000, output_tokens=1_000_000,
            customer_id="acme",
        )

        assert record.cost > 1.0
        assert optimizer.get_daily_usage("acme")["total_cost"] == record.cost
        assert optimizer.check_budget("acme")["can_proceed"] is False

    def test_a_caller_supplied_table_is_still_honoured(self):
        """Passing model_costs replaces the defaults, fallback included."""
        only = ModelCost(
            model_id="house-model", input_cost_per_1k=0.002, output_cost_per_1k=0.004
        )
        optimizer = CostOptimizer(model_costs={"house-model": only})

        assert optimizer.estimate_cost("house-model", 1_000, 1_000).estimated_cost == (
            0.002 + 0.004
        )
        # The default fallback key is not in this table; the dearest entry is used.
        assert optimizer.estimate_cost("other", 1_000, 1_000).estimated_cost == (
            0.002 + 0.004
        )
