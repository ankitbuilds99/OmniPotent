"""Single source of truth for LLM model identifiers, pricing, and capabilities.

Before this module the codebase carried six independent pricing tables and about
ten hand-written lists of model IDs. They disagreed: gpt-4o was billed at both
$5/$15 and $2.50/$10 per 1M tokens depending on which module you asked, some
tables were denominated per-1k and others per-1M, several IDs were retired
(``claude-3-opus-20240229``) or never existed (``claude-haiku-4-20250514``), and
``app/observability/metrics.py`` matched models by substring so ``claude-opus-4-8``
resolved to the ``claude-opus-4`` row and over-reported cost threefold.

Everything that needs to name a model, price a call, or validate user input
should import from here.

Pricing notes
-------------
All prices are **USD per 1,000,000 tokens** — one unit, everywhere. Helpers are
provided for the per-1k denomination some callers still expose in their public
API, so nobody has to remember a factor of 1000.

``input_per_1m``/``output_per_1m`` are ``None`` for models whose public price we
have not verified. That is deliberate: :func:`estimate_cost_usd` returns ``None``
for them rather than inventing a number. A caller that gets ``None`` should skip
the cost metric and say so, which is strictly better than the previous behaviour
of silently applying an unrelated model's rate. Fill a price in only from the
vendor's published pricing page, and update ``PRICING_LAST_VERIFIED``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

# Bump when any price below is checked against the vendor's pricing page.
PRICING_LAST_VERIFIED = "2026-08-18"

# What `resolve()` accepts as a dated snapshot suffix, e.g. the `20250514` in
# `claude-sonnet-4-20250514`. Deliberately just a date: it is the one suffix
# that names the *same* model, so it is the one suffix that may inherit that
# model's price. `-5` or `-latest` name something else. See resolve().
_SNAPSHOT_SUFFIX = re.compile(r"\d{8}")

ModelStatus = Literal["current", "legacy", "retired"]

# Capability tokens used by the routers. Kept as plain strings so callers can
# keep using set literals, but centralised so they don't drift.
CAP_CHAT = "chat"
CAP_TOOLS = "tools"
CAP_VISION = "vision"
CAP_REASONING = "reasoning"
CAP_LONG_CONTEXT = "long_context"


class UnknownModelError(ValueError):
    """Raised when a model ID isn't in the catalog and the caller requires one."""

    def __init__(self, model_id: str) -> None:
        super().__init__(
            f"unknown model {model_id!r}. Known IDs: {', '.join(sorted(_BY_ID))}. "
            "Add it to app/model_catalog.py if it is real."
        )
        self.model_id = model_id


@dataclass(frozen=True)
class ModelInfo:
    """One model: how to name it, what it costs, what it can do."""

    id: str
    provider: str
    display_name: str
    max_context: int
    max_output: int
    # USD per 1M tokens. None = price not verified; see the module docstring.
    input_per_1m: float | None = None
    output_per_1m: float | None = None
    # Anthropic prompt-caching read rate, when published.
    cached_input_per_1m: float | None = None
    capabilities: frozenset[str] = field(default_factory=lambda: frozenset({CAP_CHAT}))
    # Extended/adaptive thinking. Sending `thinking` to a model without it is a 400.
    supports_thinking: bool = False
    # Newer Anthropic reasoning models reject temperature/top_p/top_k with a 400.
    supports_temperature: bool = True
    status: ModelStatus = "current"
    # Alternative spellings seen in the wild: dated snapshot IDs, `-latest`
    # aliases, and the short names older code used.
    aliases: tuple[str, ...] = ()

    @property
    def has_pricing(self) -> bool:
        return self.input_per_1m is not None and self.output_per_1m is not None

    @property
    def input_per_1k(self) -> float | None:
        return None if self.input_per_1m is None else self.input_per_1m / 1000.0

    @property
    def output_per_1k(self) -> float | None:
        return None if self.output_per_1m is None else self.output_per_1m / 1000.0

    @property
    def cached_input_per_1k(self) -> float | None:
        if self.cached_input_per_1m is None:
            return None
        return self.cached_input_per_1m / 1000.0


_ANTHROPIC_CAPS = frozenset({CAP_CHAT, CAP_TOOLS, CAP_VISION, CAP_LONG_CONTEXT})
_ANTHROPIC_REASONING = _ANTHROPIC_CAPS | {CAP_REASONING}

MODELS: tuple[ModelInfo, ...] = (
    # ---------------------------------------------------------------- Anthropic
    # Claude 5 family. Pricing is intentionally unset until verified against
    # anthropic.com/pricing — see the module docstring.
    ModelInfo(
        id="claude-opus-5",
        provider="anthropic",
        display_name="Claude Opus 5",
        max_context=200_000,
        max_output=64_000,
        capabilities=_ANTHROPIC_REASONING,
        supports_thinking=True,
        supports_temperature=False,
    ),
    ModelInfo(
        id="claude-sonnet-5",
        provider="anthropic",
        display_name="Claude Sonnet 5",
        max_context=200_000,
        max_output=64_000,
        capabilities=_ANTHROPIC_REASONING,
        supports_thinking=True,
        supports_temperature=False,
    ),
    ModelInfo(
        id="claude-fable-5",
        provider="anthropic",
        display_name="Claude Fable 5",
        max_context=200_000,
        max_output=64_000,
        capabilities=_ANTHROPIC_REASONING,
        supports_thinking=True,
        supports_temperature=False,
    ),
    ModelInfo(
        id="claude-haiku-4-5",
        provider="anthropic",
        display_name="Claude Haiku 4.5",
        max_context=200_000,
        max_output=64_000,
        input_per_1m=1.00,
        output_per_1m=5.00,
        cached_input_per_1m=0.10,
        capabilities=_ANTHROPIC_CAPS,
        # Haiku 4.5 has no adaptive-thinking parameter; sending one is a 400.
        supports_thinking=False,
        aliases=("claude-haiku-4-5-20251001",),
    ),
    ModelInfo(
        id="claude-opus-4-8",
        provider="anthropic",
        display_name="Claude Opus 4.8",
        max_context=200_000,
        max_output=64_000,
        capabilities=_ANTHROPIC_REASONING,
        supports_thinking=True,
        supports_temperature=False,
        status="legacy",
    ),
    ModelInfo(
        id="claude-sonnet-4-6",
        provider="anthropic",
        display_name="Claude Sonnet 4.6",
        max_context=200_000,
        max_output=64_000,
        capabilities=_ANTHROPIC_REASONING,
        supports_thinking=True,
        supports_temperature=False,
        status="legacy",
    ),
    ModelInfo(
        id="claude-opus-4-1",
        provider="anthropic",
        display_name="Claude Opus 4.1",
        max_context=200_000,
        max_output=32_000,
        input_per_1m=15.00,
        output_per_1m=75.00,
        cached_input_per_1m=1.50,
        capabilities=_ANTHROPIC_REASONING,
        supports_thinking=True,
        status="legacy",
        aliases=("claude-opus-4-1-20250805",),
    ),
    ModelInfo(
        id="claude-opus-4",
        provider="anthropic",
        display_name="Claude Opus 4",
        max_context=200_000,
        max_output=32_000,
        input_per_1m=15.00,
        output_per_1m=75.00,
        cached_input_per_1m=1.50,
        capabilities=_ANTHROPIC_REASONING,
        supports_thinking=True,
        status="legacy",
        aliases=("claude-opus-4-20250514",),
    ),
    ModelInfo(
        id="claude-sonnet-4",
        provider="anthropic",
        display_name="Claude Sonnet 4",
        max_context=200_000,
        max_output=64_000,
        input_per_1m=3.00,
        output_per_1m=15.00,
        cached_input_per_1m=0.30,
        capabilities=_ANTHROPIC_REASONING,
        supports_thinking=True,
        status="legacy",
        aliases=("claude-sonnet-4-20250514",),
    ),
    ModelInfo(
        id="claude-3-5-sonnet",
        provider="anthropic",
        display_name="Claude 3.5 Sonnet",
        max_context=200_000,
        max_output=8_192,
        input_per_1m=3.00,
        output_per_1m=15.00,
        cached_input_per_1m=0.30,
        capabilities=_ANTHROPIC_CAPS,
        status="legacy",
        aliases=(
            "claude-3-5-sonnet-latest",
            "claude-3-5-sonnet-20241022",
            "claude-3-5-sonnet-20240620",
        ),
    ),
    ModelInfo(
        id="claude-3-5-haiku",
        provider="anthropic",
        display_name="Claude 3.5 Haiku",
        max_context=200_000,
        max_output=8_192,
        input_per_1m=0.80,
        output_per_1m=4.00,
        cached_input_per_1m=0.08,
        capabilities=_ANTHROPIC_CAPS,
        status="legacy",
        aliases=("claude-3-5-haiku-latest", "claude-3-5-haiku-20241022"),
    ),
    # Claude 3 is retired. Kept so historical usage records still price correctly;
    # excluded from selectable_ids() so nothing new can be pointed at it.
    ModelInfo(
        id="claude-3-opus",
        provider="anthropic",
        display_name="Claude 3 Opus",
        max_context=200_000,
        max_output=4_096,
        input_per_1m=15.00,
        output_per_1m=75.00,
        cached_input_per_1m=1.50,
        capabilities=_ANTHROPIC_CAPS,
        status="retired",
        aliases=("claude-3-opus-latest", "claude-3-opus-20240229"),
    ),
    ModelInfo(
        id="claude-3-sonnet",
        provider="anthropic",
        display_name="Claude 3 Sonnet",
        max_context=200_000,
        max_output=4_096,
        input_per_1m=3.00,
        output_per_1m=15.00,
        capabilities=_ANTHROPIC_CAPS,
        status="retired",
        aliases=("claude-3-sonnet-20240229",),
    ),
    ModelInfo(
        id="claude-3-haiku",
        provider="anthropic",
        display_name="Claude 3 Haiku",
        max_context=200_000,
        max_output=4_096,
        input_per_1m=0.25,
        output_per_1m=1.25,
        capabilities=_ANTHROPIC_CAPS,
        status="retired",
        aliases=("claude-3-haiku-20240307",),
    ),
    # ------------------------------------------------------------------- OpenAI
    ModelInfo(
        id="gpt-4o",
        provider="openai",
        display_name="GPT-4o",
        max_context=128_000,
        max_output=16_384,
        # $5/$15 was the May-2024 launch price and was later halved. Three modules
        # still carried the launch price; this is the current one.
        input_per_1m=2.50,
        output_per_1m=10.00,
        cached_input_per_1m=1.25,
        capabilities=frozenset({CAP_CHAT, CAP_TOOLS, CAP_VISION}),
        aliases=("gpt-4o-2024-08-06", "gpt-4o-2024-11-20"),
    ),
    ModelInfo(
        id="gpt-4o-mini",
        provider="openai",
        display_name="GPT-4o mini",
        max_context=128_000,
        max_output=16_384,
        input_per_1m=0.15,
        output_per_1m=0.60,
        cached_input_per_1m=0.075,
        capabilities=frozenset({CAP_CHAT, CAP_TOOLS, CAP_VISION}),
        aliases=("gpt-4o-mini-2024-07-18",),
    ),
    ModelInfo(
        id="gpt-4-turbo",
        provider="openai",
        display_name="GPT-4 Turbo",
        max_context=128_000,
        max_output=4_096,
        input_per_1m=10.00,
        output_per_1m=30.00,
        capabilities=frozenset({CAP_CHAT, CAP_TOOLS, CAP_VISION}),
        status="legacy",
    ),
    ModelInfo(
        id="gpt-3.5-turbo",
        provider="openai",
        display_name="GPT-3.5 Turbo",
        max_context=16_385,
        max_output=4_096,
        input_per_1m=0.50,
        output_per_1m=1.50,
        capabilities=frozenset({CAP_CHAT, CAP_TOOLS}),
        status="retired",
    ),
    ModelInfo(
        id="o1",
        provider="openai",
        display_name="OpenAI o1",
        max_context=200_000,
        max_output=100_000,
        input_per_1m=15.00,
        output_per_1m=60.00,
        capabilities=frozenset({CAP_CHAT, CAP_REASONING}),
        supports_temperature=False,
        status="legacy",
    ),
    ModelInfo(
        id="o1-mini",
        provider="openai",
        display_name="OpenAI o1-mini",
        max_context=128_000,
        max_output=65_536,
        input_per_1m=3.00,
        output_per_1m=12.00,
        capabilities=frozenset({CAP_CHAT, CAP_REASONING}),
        supports_temperature=False,
        status="legacy",
    ),
    # ------------------------------------------------------------------- Google
    ModelInfo(
        id="gemini-2.0-flash",
        provider="gemini",
        display_name="Gemini 2.0 Flash",
        max_context=1_000_000,
        max_output=8_192,
        input_per_1m=0.075,
        output_per_1m=0.30,
        capabilities=frozenset({CAP_CHAT, CAP_TOOLS, CAP_VISION, CAP_LONG_CONTEXT}),
    ),
    ModelInfo(
        id="gemini-1.5-pro",
        provider="gemini",
        display_name="Gemini 1.5 Pro",
        max_context=2_000_000,
        max_output=8_192,
        input_per_1m=1.25,
        output_per_1m=5.00,
        capabilities=frozenset({CAP_CHAT, CAP_TOOLS, CAP_VISION, CAP_LONG_CONTEXT}),
        status="legacy",
    ),
    ModelInfo(
        id="gemini-1.5-flash",
        provider="gemini",
        display_name="Gemini 1.5 Flash",
        max_context=1_000_000,
        max_output=8_192,
        input_per_1m=0.075,
        output_per_1m=0.30,
        capabilities=frozenset({CAP_CHAT, CAP_TOOLS, CAP_VISION, CAP_LONG_CONTEXT}),
        status="legacy",
    ),
    # --------------------------------------------------------------------- Groq
    ModelInfo(
        id="llama-3.3-70b-versatile",
        provider="groq",
        display_name="Llama 3.3 70B (Groq)",
        max_context=128_000,
        max_output=32_768,
        input_per_1m=0.59,
        output_per_1m=0.79,
        capabilities=frozenset({CAP_CHAT, CAP_TOOLS}),
    ),
    ModelInfo(
        id="llama-3.1-8b-instant",
        provider="groq",
        display_name="Llama 3.1 8B Instant (Groq)",
        max_context=128_000,
        max_output=8_192,
        input_per_1m=0.05,
        output_per_1m=0.08,
        capabilities=frozenset({CAP_CHAT, CAP_TOOLS}),
    ),
    # ----------------------------------------------------------------- DeepSeek
    ModelInfo(
        id="deepseek-chat",
        provider="deepseek",
        display_name="DeepSeek Chat",
        max_context=64_000,
        max_output=8_192,
        input_per_1m=0.27,
        output_per_1m=1.10,
        cached_input_per_1m=0.07,
        capabilities=frozenset({CAP_CHAT, CAP_TOOLS}),
    ),
)

# Per-provider defaults: (main reasoning model, cheap fast model).
DEFAULT_MODELS: dict[str, dict[str, str]] = {
    "anthropic": {"main": "claude-opus-4-8", "fast": "claude-haiku-4-5"},
    "openai": {"main": "gpt-4o", "fast": "gpt-4o-mini"},
    "gemini": {"main": "gemini-1.5-pro", "fast": "gemini-1.5-flash"},
    "groq": {"main": "llama-3.3-70b-versatile", "fast": "llama-3.1-8b-instant"},
    "deepseek": {"main": "deepseek-chat", "fast": "deepseek-chat"},
}

_BY_ID: dict[str, ModelInfo] = {m.id: m for m in MODELS}
_BY_ALIAS: dict[str, ModelInfo] = {a: m for m in MODELS for a in m.aliases}

# Sanity: an alias must not shadow a real ID, and IDs must be unique.
assert len(_BY_ID) == len(MODELS), "duplicate model id in MODELS"
assert not (_BY_ID.keys() & _BY_ALIAS.keys()), "alias collides with a model id"


def resolve(model_id: str | None) -> ModelInfo | None:
    """Look up a model, tolerating dated snapshot suffixes. None if unknown.

    Resolution order is exact ID, then alias, then the longest ID that the key
    extends *by a dated snapshot stamp only* -- so ``claude-sonnet-4-20250514``
    finds ``claude-sonnet-4``, and nothing else does.

    That last restriction is the point. A bare ``-`` boundary cannot tell a date
    stamp from a version bump: ``claude-opus-4-5`` and ``claude-opus-4-20250514``
    are the same shape to it, so the fallback happily answered the Opus-4 row --
    $15/$75 and a 32k output cap -- for a *different, newer model*. That is the
    same threefold cost over-report the substring matching before it caused,
    reached by a longer route. ``claude-opus-4-8`` reads correctly today only
    because it has its own row, which is luck rather than a rule: every point
    release not yet catalogued borrowed its predecessor's price, and did so
    confidently, so nothing downstream ever saw the ``None`` that the unpriced
    path is built to surface.

    A snapshot stamp is a same-model rebuild, which is why inheriting the parent
    row is sound there and only there. An unrecognised suffix returns ``None``:
    the caller then gets "I do not know this model" instead of a neighbour's
    numbers, and `PRICING_LAST_VERIFIED` above says what to do about it.
    """
    if not model_id:
        return None
    key = model_id.strip()
    if not key:
        return None
    if key in _BY_ID:
        return _BY_ID[key]
    if key in _BY_ALIAS:
        return _BY_ALIAS[key]
    best: ModelInfo | None = None
    for candidate in MODELS:
        if not key.startswith(candidate.id + "-"):
            continue
        if not _SNAPSHOT_SUFFIX.fullmatch(key[len(candidate.id) + 1 :]):
            continue
        if best is None or len(candidate.id) > len(best.id):
            best = candidate
    return best


def get(model_id: str) -> ModelInfo:
    """resolve(), but raise UnknownModelError instead of returning None."""
    info = resolve(model_id)
    if info is None:
        raise UnknownModelError(model_id)
    return info


def is_known(model_id: str | None) -> bool:
    return resolve(model_id) is not None


def price_per_1m(model_id: str) -> tuple[float, float] | None:
    """(input, output) USD per 1M tokens, or None if unknown/unpriced."""
    info = resolve(model_id)
    if info is None or not info.has_pricing:
        return None
    return (info.input_per_1m, info.output_per_1m)  # type: ignore[return-value]


def price_per_1k(model_id: str) -> tuple[float, float] | None:
    """(input, output) USD per 1k tokens, for callers whose API is per-1k."""
    per_1m = price_per_1m(model_id)
    return None if per_1m is None else (per_1m[0] / 1000.0, per_1m[1] / 1000.0)


def estimate_cost_usd(
    model_id: str,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int = 0,
) -> float | None:
    """Cost of one call in USD, or None when the model's price isn't known.

    Returning None is the point: callers must decide what to do about an
    unpriced model (skip the metric, warn, surface "unknown") instead of
    silently recording a number derived from a different model's rate.
    """
    info = resolve(model_id)
    if info is None or not info.has_pricing:
        return None
    billable_input = max(0, input_tokens - cached_input_tokens)
    cost = (billable_input / 1_000_000) * info.input_per_1m  # type: ignore[operator]
    cost += (output_tokens / 1_000_000) * info.output_per_1m  # type: ignore[operator]
    if cached_input_tokens:
        cached_rate = (
            info.cached_input_per_1m
            if info.cached_input_per_1m is not None
            else info.input_per_1m
        )
        cost += (cached_input_tokens / 1_000_000) * cached_rate  # type: ignore[operator]
    return cost


def default_model(provider: str, fast: bool = False) -> str:
    """The main (or fast) model ID for a provider."""
    try:
        return DEFAULT_MODELS[provider]["fast" if fast else "main"]
    except KeyError:
        raise ValueError(
            f"unknown provider {provider!r} (use {'|'.join(sorted(DEFAULT_MODELS))})"
        ) from None


def models_for(provider: str | None = None, *, include_retired: bool = False) -> list[ModelInfo]:
    """Catalog entries, optionally filtered to one provider."""
    out = [m for m in MODELS if provider is None or m.provider == provider]
    if not include_retired:
        out = [m for m in out if m.status != "retired"]
    return out


def selectable_ids(provider: str | None = None) -> list[str]:
    """Model IDs a user may pick — retired models are excluded."""
    return [m.id for m in models_for(provider)]


def to_dict(info: ModelInfo) -> dict:
    """JSON-serialisable form, for the /models API and the frontend picker."""
    return {
        "id": info.id,
        "provider": info.provider,
        "display_name": info.display_name,
        "max_context": info.max_context,
        "max_output": info.max_output,
        "input_per_1m": info.input_per_1m,
        "output_per_1m": info.output_per_1m,
        "cached_input_per_1m": info.cached_input_per_1m,
        "has_pricing": info.has_pricing,
        "capabilities": sorted(info.capabilities),
        "supports_thinking": info.supports_thinking,
        "supports_temperature": info.supports_temperature,
        "status": info.status,
    }


def catalog_payload(include_retired: bool = False) -> dict:
    """Full catalog for GET /api/models."""
    return {
        "pricing_last_verified": PRICING_LAST_VERIFIED,
        "pricing_unit": "usd_per_1m_tokens",
        "defaults": DEFAULT_MODELS,
        "models": [to_dict(m) for m in models_for(include_retired=include_retired)],
    }
