"""The SLA `min_quality` floor in AdaptiveModelRouter.route().

`ModelTier` is a `str` Enum, so `sla.min_quality.value > target_tier.value`
compared tier *names* alphabetically:

    "quality"  > "premium"  -> True   (a floor of QUALITY *downgraded* a P0
                                       from Opus to gpt-4o)
    "balanced" > "fast"     -> False  (the API's own default floor of BALANCED
                                       could never lift a FAST route)

So the one knob a customer has for "never answer me with the cheap model" did
the opposite of what it says on half the tier pairs.
"""

from __future__ import annotations

import itertools

from app.model_router.adaptive_router import (
    TIER_ORDER,
    AdaptiveModelRouter,
    CustomerTier,
    ModelTier,
    SLARequirements,
    tier_rank,
)

P0_QUERY = "the checkout service is returning errors"
TRIVIAL_QUERY = "what is the deploy command"

# FAST is the weakest tier, so a FAST floor is the same as "no floor" — that is
# the honest baseline to compare against, since SLARequirements() itself
# defaults to a BALANCED floor.
NO_FLOOR = SLARequirements(min_quality=ModelTier.FAST)


class TestTierOrdering:
    def test_tier_rank_orders_weakest_to_strongest(self):
        ranks = [tier_rank(t) for t in TIER_ORDER]
        assert ranks == sorted(ranks)
        assert tier_rank(ModelTier.FAST) < tier_rank(ModelTier.BALANCED)
        assert tier_rank(ModelTier.BALANCED) < tier_rank(ModelTier.QUALITY)
        assert tier_rank(ModelTier.QUALITY) < tier_rank(ModelTier.PREMIUM)

    def test_string_comparison_of_tiers_is_not_the_ordering(self):
        """Pins the reason the bug existed: name order != capability order."""
        assert ModelTier.QUALITY.value > ModelTier.PREMIUM.value  # alphabetical
        assert tier_rank(ModelTier.QUALITY) < tier_rank(ModelTier.PREMIUM)


class TestMinQualityFloor:
    def test_a_quality_floor_does_not_downgrade_a_p0(self):
        """Asking for *more* quality on a P0 used to demote Opus to gpt-4o."""
        router = AdaptiveModelRouter()

        without = router.route(
            P0_QUERY, customer_tier=CustomerTier.ENTERPRISE, incident_severity="p0"
        )
        with_floor = router.route(
            P0_QUERY,
            customer_tier=CustomerTier.ENTERPRISE,
            incident_severity="p0",
            sla=SLARequirements(min_quality=ModelTier.QUALITY),
        )

        assert without.model_tier is ModelTier.PREMIUM
        assert tier_rank(with_floor.model_tier) >= tier_rank(without.model_tier)
        assert with_floor.model_id == without.model_id

    def test_a_balanced_floor_lifts_a_trivial_query_off_the_fast_model(self):
        """The API's default floor (min_quality='balanced') was completely inert."""
        router = AdaptiveModelRouter()

        without = router.route(
            TRIVIAL_QUERY, customer_tier=CustomerTier.ENTERPRISE, sla=NO_FLOOR
        )
        with_floor = router.route(
            TRIVIAL_QUERY,
            customer_tier=CustomerTier.ENTERPRISE,
            sla=SLARequirements(min_quality=ModelTier.BALANCED),
        )

        assert without.model_tier is ModelTier.FAST
        assert tier_rank(with_floor.model_tier) >= tier_rank(ModelTier.BALANCED)
        assert with_floor.factors["min_quality_floor_applied"] is True
        assert "min_quality" in with_floor.reason

    def test_the_default_sla_floor_is_the_one_the_api_documents(self):
        """SLARequirements() (and RouteRequest's default) promise balanced-or-better."""
        router = AdaptiveModelRouter()

        decision = router.route(TRIVIAL_QUERY, customer_tier=CustomerTier.ENTERPRISE)

        assert tier_rank(decision.model_tier) >= tier_rank(ModelTier.BALANCED)

    def test_the_floor_never_lowers_the_tier_for_any_pair(self):
        """Exhaustive over (floor, severity): a floor is a floor, in both directions."""
        router = AdaptiveModelRouter()
        severities = (None, "p3", "p1", "p0")

        for floor, severity in itertools.product(TIER_ORDER, severities):
            baseline = router.route(
                P0_QUERY,
                customer_tier=CustomerTier.ENTERPRISE,
                incident_severity=severity,
                sla=NO_FLOOR,
            )
            floored = router.route(
                P0_QUERY,
                customer_tier=CustomerTier.ENTERPRISE,
                incident_severity=severity,
                sla=SLARequirements(min_quality=floor),
            )
            expected = max(tier_rank(baseline.model_tier), tier_rank(floor))
            assert tier_rank(floored.model_tier) >= expected, (floor, severity)

    def test_the_customer_tier_cap_still_wins_over_the_floor(self):
        """A free-plan customer cannot buy Opus by asking for a PREMIUM floor."""
        router = AdaptiveModelRouter()

        decision = router.route(
            P0_QUERY,
            customer_tier=CustomerTier.FREE,
            incident_severity="p0",
            sla=SLARequirements(min_quality=ModelTier.PREMIUM),
        )

        assert decision.model_tier is ModelTier.FAST
