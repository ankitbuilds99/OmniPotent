"""Model catalog: resolution, pricing unification, and the substring-match bug.

The catalog replaced six disagreeing pricing tables and ~10 hand-written model
lists. These tests pin the behaviours that made those tables wrong.
"""

import pytest

from app import model_catalog as mc


def test_ids_and_aliases_are_unique():
    ids = [m.id for m in mc.MODELS]
    assert len(ids) == len(set(ids))
    aliases = [a for m in mc.MODELS for a in m.aliases]
    assert len(aliases) == len(set(aliases)), "an alias is declared twice"
    assert not (set(ids) & set(aliases)), "an alias shadows a real model id"


def test_exact_and_alias_resolution():
    assert mc.resolve("gpt-4o").id == "gpt-4o"
    assert mc.resolve("claude-3-5-sonnet-20241022").id == "claude-3-5-sonnet"
    assert mc.resolve("claude-haiku-4-5-20251001").id == "claude-haiku-4-5"


def test_dated_snapshot_resolves_by_prefix():
    assert mc.resolve("claude-sonnet-4-20250514").id == "claude-sonnet-4"
    assert mc.resolve("claude-opus-4-20250514").id == "claude-opus-4"


def test_prefix_match_does_not_swallow_a_longer_sibling():
    # The bug this replaces: observability/metrics.py matched with `m in model`,
    # so claude-opus-4-8 hit the claude-opus-4 row and its cost was reported at
    # the Opus-4 rate — a 3x over-report. Longest-prefix + a `-` boundary fixes it.
    assert mc.resolve("claude-opus-4-8").id == "claude-opus-4-8"
    assert mc.resolve("claude-opus-4-1").id == "claude-opus-4-1"
    assert mc.resolve("claude-haiku-4-5").id == "claude-haiku-4-5"


def test_unknown_and_fabricated_ids_resolve_to_none():
    # There is no Claude Haiku 4 — this ID was referenced in four places.
    assert mc.resolve("claude-haiku-4-20250514") is None
    assert mc.resolve("gpt-5-ultra") is None
    assert mc.resolve("") is None
    assert mc.resolve(None) is None
    assert mc.is_known("gpt-4o") and not mc.is_known("nope")


def test_get_raises_with_an_actionable_message():
    with pytest.raises(mc.UnknownModelError) as exc:
        mc.get("not-a-model")
    assert "not-a-model" in str(exc.value)
    assert "gpt-4o" in str(exc.value)  # lists the known IDs


def test_gpt4o_price_is_the_current_one_not_the_launch_price():
    # Three modules carried the May-2024 launch price of $5/$15.
    assert mc.price_per_1m("gpt-4o") == (2.50, 10.00)


def test_per_1k_helper_is_exactly_the_per_1m_price_over_1000():
    per_1m = mc.price_per_1m("gpt-4o")
    per_1k = mc.price_per_1k("gpt-4o")
    assert per_1k == (per_1m[0] / 1000, per_1m[1] / 1000)


def test_estimate_cost_is_arithmetically_right():
    # 1M in + 1M out at $2.50/$10.
    assert mc.estimate_cost_usd("gpt-4o", 1_000_000, 1_000_000) == pytest.approx(12.50)
    assert mc.estimate_cost_usd("gpt-4o", 0, 0) == 0.0


def test_cached_input_is_billed_at_the_cache_rate():
    # 1M input of which 1M is cached, at the $1.25 cache-read rate.
    cached = mc.estimate_cost_usd("gpt-4o", 1_000_000, 0, cached_input_tokens=1_000_000)
    assert cached == pytest.approx(1.25)
    # Cached tokens are not double-billed as fresh input.
    assert cached < mc.estimate_cost_usd("gpt-4o", 1_000_000, 0)


def test_unpriced_model_returns_none_rather_than_a_guess():
    # The whole point: callers must handle "price unknown" explicitly instead of
    # silently recording a number derived from some other model's rate.
    unpriced = next(m for m in mc.MODELS if not m.has_pricing)
    assert mc.estimate_cost_usd(unpriced.id, 1000, 1000) is None
    assert mc.price_per_1m(unpriced.id) is None
    assert mc.estimate_cost_usd("totally-unknown-model", 1000, 1000) is None


def test_retired_models_keep_pricing_but_are_not_selectable():
    retired = [m for m in mc.MODELS if m.status == "retired"]
    assert retired, "expected some retired models"
    for m in retired:
        # Historical usage records must still price correctly.
        assert m.has_pricing
        assert m.id not in mc.selectable_ids()


def test_defaults_all_point_at_real_catalog_entries():
    for provider, tiers in mc.DEFAULT_MODELS.items():
        for tier, model_id in tiers.items():
            info = mc.resolve(model_id)
            assert info is not None, f"{provider}/{tier} -> unknown model {model_id}"
            assert info.provider == provider
            assert info.status != "retired", f"{provider}/{tier} defaults to a retired model"


def test_default_model_rejects_an_unknown_provider():
    assert mc.default_model("anthropic", fast=True) == "claude-haiku-4-5"
    with pytest.raises(ValueError):
        mc.default_model("nope")


def test_thinking_support_is_declared_not_assumed():
    # llm.py gates the `thinking` param on this; sending it to a model without
    # support is an HTTP 400.
    assert mc.get("claude-haiku-4-5").supports_thinking is False
    assert mc.get("claude-opus-4-8").supports_thinking is True


def test_catalog_payload_is_json_serialisable_and_shaped_for_the_frontend():
    import json

    payload = mc.catalog_payload()
    json.dumps(payload)  # must not raise — frozensets would
    assert payload["pricing_unit"] == "usd_per_1m_tokens"
    assert payload["models"], "empty catalog"
    entry = payload["models"][0]
    for key in ("id", "provider", "display_name", "has_pricing", "capabilities", "status"):
        assert key in entry
    assert isinstance(entry["capabilities"], list)
    # An unpriced model reports null, never a placeholder number.
    for m in payload["models"]:
        if not m["has_pricing"]:
            assert m["input_per_1m"] is None and m["output_per_1m"] is None
