"""Token Usage Optimizer.

Production-ready optimization with real integrations:
- tiktoken for accurate token counting
- Redis-backed caching with semantic similarity
- Real-time usage tracking in PostgreSQL
- Prometheus metrics export
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Protocol

from app import model_catalog

log = logging.getLogger("omnipotent.cost.token_optimizer")

# Models already reported as unpriced; token accounting is per-request, so warn
# once per model rather than once per call.
_UNPRICED_WARNED: set[str] = set()

# The models this optimizer can select between. IDs only — every rate comes from
# app.model_catalog, so this table can no longer drift from the five others that
# used to price the same models differently.
_PRICED_MODEL_IDS = (
    "gpt-4o",
    "gpt-4o-mini",
    "gpt-4-turbo",
    "gpt-3.5-turbo",
    "claude-3-opus-20240229",
    "claude-3-sonnet-20240229",
    "claude-3-haiku-20240307",
    "claude-3-5-sonnet-20240620",
    "claude-sonnet-4-20250514",
    "claude-opus-4-20250514",
)


def _catalog_pricing() -> dict[str, dict[str, float]]:
    """Per-1M input/output rates for the selectable models, from the catalog.

    Shape is unchanged (``{model: {"input": ..., "output": ...}}``, USD per 1M) so
    select_model()'s cheapest-model scan keeps working. Unpriced models are left
    out: select_model() sorts on these numbers, so a placeholder rate would make an
    unpriced model look like the cheapest option and win.
    """
    pricing: dict[str, dict[str, float]] = {}
    for model_id in _PRICED_MODEL_IDS:
        rates = model_catalog.price_per_1m(model_id)
        if rates is None:
            log.warning(
                "model %r has no verified price in the model catalog; "
                "excluding it from the token optimizer's pricing table",
                model_id,
            )
            continue
        pricing[model_id] = {"input": rates[0], "output": rates[1]}
    return pricing


class TokenEncoder(Protocol):
    """Protocol for token encoding."""
    def encode(self, text: str) -> list[int]: ...
    def decode(self, tokens: list[int]) -> str: ...


class OptimizationStrategy(str, Enum):
    """Token optimization strategies."""
    AGGRESSIVE = "aggressive"
    BALANCED = "balanced"
    QUALITY_FIRST = "quality_first"
    ADAPTIVE = "adaptive"


class TaskComplexity(str, Enum):
    """Complexity levels for tasks."""
    TRIVIAL = "trivial"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class TokenUsage:
    """Token usage record."""
    request_id: str
    model: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cost_usd: float
    timestamp: datetime
    task_type: str
    optimizations_applied: list[str]
    original_input_tokens: int | None = None
    savings_tokens: int = 0
    savings_usd: float = 0.0
    tenant_id: str = "default"

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": self.cost_usd,
            "timestamp": self.timestamp.isoformat(),
            "task_type": self.task_type,
            "optimizations_applied": self.optimizations_applied,
            "savings_tokens": self.savings_tokens,
            "savings_usd": self.savings_usd,
            "tenant_id": self.tenant_id,
        }


@dataclass
class TokenBudget:
    """Token budget configuration."""
    daily_limit: int = 1_000_000
    monthly_limit: int = 25_000_000
    per_request_limit: int = 50_000
    alert_threshold: float = 0.8
    model_limits: dict[str, int] = field(default_factory=dict)
    task_limits: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "daily_limit": self.daily_limit,
            "monthly_limit": self.monthly_limit,
            "per_request_limit": self.per_request_limit,
            "alert_threshold": self.alert_threshold,
            "model_limits": self.model_limits,
            "task_limits": self.task_limits,
        }


@dataclass
class OptimizationResult:
    """Result of prompt optimization."""
    original_text: str
    optimized_text: str
    original_tokens: int
    optimized_tokens: int
    savings_tokens: int
    savings_percent: float
    techniques_applied: list[str]
    quality_impact: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "original_tokens": self.original_tokens,
            "optimized_tokens": self.optimized_tokens,
            "savings_tokens": self.savings_tokens,
            "savings_percent": self.savings_percent,
            "techniques_applied": self.techniques_applied,
            "quality_impact": self.quality_impact,
        }


@dataclass
class CacheEntry:
    """Cached response entry."""
    prompt_hash: str
    response: str
    tokens_saved: int
    created_at: datetime
    expires_at: datetime
    hit_count: int = 0
    embedding: list[float] | None = None

    @property
    def is_expired(self) -> bool:
        return datetime.now(timezone.utc) > self.expires_at


class TokenOptimizer:
    """Production-ready token optimizer.

    Features:
    - tiktoken for accurate token counting
    - Redis-backed response caching
    - PostgreSQL usage tracking
    - Prometheus metrics export
    - Real-time budget enforcement
    """

    def __init__(
        self,
        budget: TokenBudget | None = None,
        strategy: OptimizationStrategy = OptimizationStrategy.BALANCED,
        redis_client: Any = None,
        db_client: Any = None,
        metrics_client: Any = None,
    ):
        self.budget = budget or TokenBudget()
        self.strategy = strategy
        self._redis = redis_client
        self._db = db_client
        self._metrics = metrics_client

        self._encoder: TokenEncoder | None = None
        self._encoding_name = "cl100k_base"

        self._model_pricing = _catalog_pricing()

        self._complexity_models = {
            TaskComplexity.TRIVIAL: "gpt-4o-mini",
            TaskComplexity.LOW: "gpt-4o-mini",
            TaskComplexity.MEDIUM: "gpt-4o",
            TaskComplexity.HIGH: "claude-3-5-sonnet-20240620",
            TaskComplexity.CRITICAL: "claude-opus-4-20250514",
        }

    async def initialize(self) -> None:
        """Initialize token encoder and database schema."""
        try:
            import tiktoken
            self._encoder = tiktoken.get_encoding(self._encoding_name)
            log.info("Initialized tiktoken with encoding: %s", self._encoding_name)
        except ImportError:
            log.warning("tiktoken not available, using character-based estimation")

        if self._db:
            await self._db.execute("""
                CREATE TABLE IF NOT EXISTS token_usage (
                    id SERIAL PRIMARY KEY,
                    request_id VARCHAR(64) NOT NULL,
                    tenant_id VARCHAR(64) NOT NULL,
                    model VARCHAR(64) NOT NULL,
                    input_tokens INT NOT NULL,
                    output_tokens INT NOT NULL,
                    total_tokens INT NOT NULL,
                    cost_usd DECIMAL(10, 6) NOT NULL,
                    task_type VARCHAR(64) NOT NULL,
                    optimizations TEXT[],
                    savings_tokens INT DEFAULT 0,
                    savings_usd DECIMAL(10, 6) DEFAULT 0,
                    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)

            await self._db.execute("""
                CREATE INDEX IF NOT EXISTS idx_token_usage_tenant_time
                ON token_usage(tenant_id, timestamp DESC)
            """)

            await self._db.execute("""
                CREATE INDEX IF NOT EXISTS idx_token_usage_model
                ON token_usage(model, timestamp DESC)
            """)

    def count_tokens(self, text: str, model: str | None = None) -> int:
        """Count tokens accurately using tiktoken."""
        if self._encoder:
            return len(self._encoder.encode(text))
        return len(text) // 4

    def optimize_prompt(
        self,
        prompt: str,
        task_type: str = "general",
        preserve_examples: bool = True,
        model: str | None = None,
    ) -> OptimizationResult:
        """Optimize a prompt to reduce token usage."""
        original_tokens = self.count_tokens(prompt, model)
        optimized = prompt
        techniques = []

        if self.strategy == OptimizationStrategy.QUALITY_FIRST:
            optimized = self._remove_redundant_whitespace(optimized)
            techniques.append("whitespace_normalization")
        else:
            optimized = self._remove_redundant_whitespace(optimized)
            techniques.append("whitespace_normalization")

            optimized = self._compress_repetition(optimized)
            techniques.append("repetition_compression")

            if not preserve_examples and self.strategy == OptimizationStrategy.AGGRESSIVE:
                optimized = self._reduce_examples(optimized)
                techniques.append("example_reduction")

            optimized = self._abbreviate_common_phrases(optimized)
            techniques.append("phrase_abbreviation")

            if self.strategy == OptimizationStrategy.AGGRESSIVE:
                optimized = self._aggressive_compression(optimized)
                techniques.append("aggressive_compression")

        optimized_tokens = self.count_tokens(optimized, model)
        savings = original_tokens - optimized_tokens
        savings_percent = (savings / original_tokens * 100) if original_tokens > 0 else 0

        if savings_percent < 5:
            quality_impact = "none"
        elif savings_percent < 15:
            quality_impact = "minimal"
        elif savings_percent < 30:
            quality_impact = "moderate"
        else:
            quality_impact = "significant"

        return OptimizationResult(
            original_text=prompt,
            optimized_text=optimized,
            original_tokens=original_tokens,
            optimized_tokens=optimized_tokens,
            savings_tokens=savings,
            savings_percent=savings_percent,
            techniques_applied=techniques,
            quality_impact=quality_impact,
        )

    def _remove_redundant_whitespace(self, text: str) -> str:
        """Remove excessive whitespace."""
        text = re.sub(r' +', ' ', text)
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text.strip()

    def _compress_repetition(self, text: str) -> str:
        """Compress repeated patterns."""
        words = text.split()
        result = []
        prev_word = None
        repeat_count = 0

        for word in words:
            if word == prev_word:
                repeat_count += 1
                if repeat_count <= 2:
                    result.append(word)
            else:
                result.append(word)
                repeat_count = 0
            prev_word = word

        return ' '.join(result)

    def _reduce_examples(self, text: str) -> str:
        """Reduce the number of examples in prompts."""
        example_pattern = r'(Example \d+:.*?)(?=Example \d+:|$)'
        examples = re.findall(example_pattern, text, re.DOTALL)

        if len(examples) > 2:
            for example in examples[2:]:
                text = text.replace(example, '')

        return text

    def _abbreviate_common_phrases(self, text: str) -> str:
        """Abbreviate common phrases."""
        abbreviations = {
            "for example": "e.g.",
            "that is": "i.e.",
            "and so on": "etc.",
            "in other words": "i.e.",
            "please note that": "Note:",
            "it is important to": "Important:",
            "make sure to": "Ensure",
            "please ensure that": "Ensure",
        }

        for phrase, abbrev in abbreviations.items():
            text = re.sub(rf'\b{phrase}\b', abbrev, text, flags=re.IGNORECASE)

        return text

    def _aggressive_compression(self, text: str) -> str:
        """Apply aggressive compression techniques."""
        filler_words = [
            r'\bvery\b', r'\breally\b', r'\bjust\b', r'\bactually\b',
            r'\bbasically\b', r'\bsimply\b', r'\bkind of\b', r'\bsort of\b',
        ]

        for filler in filler_words:
            text = re.sub(filler, '', text, flags=re.IGNORECASE)

        text = re.sub(r' +', ' ', text)
        return text

    async def check_cache(self, prompt: str, model: str | None = None) -> str | None:
        """Check if response is cached in Redis."""
        if not self._redis:
            return None

        prompt_hash = self._hash_prompt(prompt)
        cache_key = f"token_cache:{model or 'default'}:{prompt_hash}"

        cached = await self._redis.get(cache_key)
        if cached:
            await self._redis.hincrby(f"token_cache_stats:{prompt_hash}", "hits", 1)
            log.debug("Cache hit for prompt hash: %s", prompt_hash[:12])
            return cached.decode() if isinstance(cached, bytes) else cached

        return None

    async def cache_response(
        self,
        prompt: str,
        response: str,
        model: str | None = None,
        ttl_hours: int = 24,
    ) -> None:
        """Cache a response in Redis."""
        if not self._redis:
            return

        prompt_hash = self._hash_prompt(prompt)
        cache_key = f"token_cache:{model or 'default'}:{prompt_hash}"

        await self._redis.setex(
            cache_key,
            ttl_hours * 3600,
            response,
        )

        await self._redis.hset(
            f"token_cache_stats:{prompt_hash}",
            mapping={
                "created_at": datetime.now(timezone.utc).isoformat(),
                "tokens_saved": self.count_tokens(prompt) + self.count_tokens(response),
                "hits": 0,
            }
        )

    def _hash_prompt(self, prompt: str) -> str:
        """Generate semantic hash for prompt."""
        normalized = prompt.lower().strip()
        normalized = re.sub(r'\s+', ' ', normalized)
        return hashlib.sha256(normalized.encode()).hexdigest()[:16]

    def select_model(
        self,
        task_type: str,
        complexity: TaskComplexity | None = None,
        max_cost: float | None = None,
        required_context: int | None = None,
    ) -> str:
        """Select optimal model for task."""
        if complexity:
            suggested = self._complexity_models.get(complexity, "gpt-4o")
        else:
            if task_type in ["classification", "yes_no", "lookup", "extraction"]:
                suggested = "gpt-4o-mini"
            elif task_type in ["analysis", "summarization", "code_review"]:
                suggested = "gpt-4o"
            elif task_type in ["reasoning", "planning", "safety_critical", "incident_rca"]:
                suggested = "claude-opus-4-20250514"
            else:
                suggested = "gpt-4o"

        if max_cost and suggested in self._model_pricing:
            pricing = self._model_pricing[suggested]
            estimated_cost = (pricing["input"] + pricing["output"]) * 0.001
            if estimated_cost > max_cost:
                for model, prices in sorted(
                    self._model_pricing.items(),
                    key=lambda x: x[1]["input"] + x[1]["output"]
                ):
                    cost = (prices["input"] + prices["output"]) * 0.001
                    if cost <= max_cost:
                        return model

        return suggested

    async def record_usage(
        self,
        request_id: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        task_type: str,
        tenant_id: str = "default",
        optimizations: list[str] | None = None,
        original_tokens: int | None = None,
    ) -> TokenUsage:
        """Record token usage to database."""
        total_tokens = input_tokens + output_tokens
        cost = self._calculate_cost(model, input_tokens, output_tokens)

        savings_tokens = (original_tokens - input_tokens) if original_tokens else 0
        savings_cost = self._calculate_cost(model, savings_tokens, 0) if savings_tokens > 0 else 0

        usage = TokenUsage(
            request_id=request_id,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            cost_usd=cost,
            timestamp=datetime.now(timezone.utc),
            task_type=task_type,
            optimizations_applied=optimizations or [],
            original_input_tokens=original_tokens,
            savings_tokens=savings_tokens,
            savings_usd=savings_cost,
            tenant_id=tenant_id,
        )

        if self._db:
            await self._db.execute(
                """
                INSERT INTO token_usage
                (request_id, tenant_id, model, input_tokens, output_tokens,
                 total_tokens, cost_usd, task_type, optimizations, savings_tokens,
                 savings_usd, timestamp)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                """,
                request_id, tenant_id, model, input_tokens, output_tokens,
                total_tokens, cost, task_type, optimizations or [],
                savings_tokens, savings_cost, usage.timestamp
            )

        if self._redis:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            month = datetime.now(timezone.utc).strftime("%Y-%m")

            await self._redis.hincrby(f"token_usage:{tenant_id}:daily:{today}", "total", total_tokens)
            await self._redis.hincrby(f"token_usage:{tenant_id}:daily:{today}", model, total_tokens)
            await self._redis.hincrby(f"token_usage:{tenant_id}:monthly:{month}", "total", total_tokens)
            await self._redis.hincrbyfloat(f"token_cost:{tenant_id}:daily:{today}", "total", cost)
            await self._redis.hincrbyfloat(f"token_cost:{tenant_id}:monthly:{month}", "total", cost)

        if self._metrics:
            self._metrics.counter(
                "omnipotent_tokens_total",
                total_tokens,
                labels={"model": model, "task_type": task_type, "tenant": tenant_id}
            )
            self._metrics.counter(
                "omnipotent_token_cost_usd",
                cost,
                labels={"model": model, "tenant": tenant_id}
            )

        return usage

    def _calculate_cost(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> float:
        """Calculate cost for token usage.

        Prices any model the catalog knows, not just the selectable ones. A model
        with no verified price costs 0.0 and warns once naming it — the old
        fallback billed unknown models at an invented $1/$3, which wrote a
        confidently wrong number into token_usage.cost_usd and every budget check
        derived from it.
        """
        cost = model_catalog.estimate_cost_usd(model, input_tokens, output_tokens)
        if cost is None:
            if model not in _UNPRICED_WARNED:
                _UNPRICED_WARNED.add(model)
                log.warning(
                    "no verified price for model %r; recording $0.00 cost for it. "
                    "Add the model to app/model_catalog.py.",
                    model,
                )
            return 0.0
        return cost

    async def check_budget(
        self,
        tenant_id: str = "default",
    ) -> dict[str, Any]:
        """Check current budget status from Redis."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        month = datetime.now(timezone.utc).strftime("%Y-%m")

        if self._redis:
            daily_data = await self._redis.hgetall(f"token_usage:{tenant_id}:daily:{today}")
            monthly_data = await self._redis.hgetall(f"token_usage:{tenant_id}:monthly:{month}")

            daily_used = int(daily_data.get(b"total", daily_data.get("total", 0)) or 0)
            monthly_used = int(monthly_data.get(b"total", monthly_data.get("total", 0)) or 0)
        elif self._db:
            daily_row = await self._db.fetchrow(
                """
                SELECT COALESCE(SUM(total_tokens), 0) as total
                FROM token_usage
                WHERE tenant_id = $1 AND timestamp >= $2
                """,
                tenant_id, datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            )
            monthly_row = await self._db.fetchrow(
                """
                SELECT COALESCE(SUM(total_tokens), 0) as total
                FROM token_usage
                WHERE tenant_id = $1 AND timestamp >= $2
                """,
                tenant_id, datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            )
            daily_used = int(daily_row["total"]) if daily_row else 0
            monthly_used = int(monthly_row["total"]) if monthly_row else 0
        else:
            daily_used = 0
            monthly_used = 0

        daily_percent = (daily_used / self.budget.daily_limit * 100) if self.budget.daily_limit else 0
        monthly_percent = (monthly_used / self.budget.monthly_limit * 100) if self.budget.monthly_limit else 0

        alerts = []
        if daily_percent >= self.budget.alert_threshold * 100:
            alerts.append(f"Daily usage at {daily_percent:.1f}%")
        if monthly_percent >= self.budget.alert_threshold * 100:
            alerts.append(f"Monthly usage at {monthly_percent:.1f}%")

        return {
            "tenant_id": tenant_id,
            "daily_used": daily_used,
            "daily_limit": self.budget.daily_limit,
            "daily_percent": daily_percent,
            "monthly_used": monthly_used,
            "monthly_limit": self.budget.monthly_limit,
            "monthly_percent": monthly_percent,
            "alerts": alerts,
            "budget_exceeded": daily_percent >= 100 or monthly_percent >= 100,
        }

    async def get_usage_stats(
        self,
        tenant_id: str = "default",
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> dict[str, Any]:
        """Get usage statistics from database."""
        if not self._db:
            return {
                "total_requests": 0,
                "total_tokens": 0,
                "total_cost": 0,
                "total_savings": 0,
            }

        if not start_time:
            start_time = datetime.now(timezone.utc) - timedelta(days=30)
        if not end_time:
            end_time = datetime.now(timezone.utc)

        rows = await self._db.fetch(
            """
            SELECT model, task_type,
                   COUNT(*) as requests,
                   SUM(total_tokens) as tokens,
                   SUM(cost_usd) as cost,
                   SUM(savings_usd) as savings
            FROM token_usage
            WHERE tenant_id = $1 AND timestamp >= $2 AND timestamp <= $3
            GROUP BY model, task_type
            """,
            tenant_id, start_time, end_time
        )

        total_requests = sum(r["requests"] for r in rows)
        total_tokens = sum(r["tokens"] for r in rows)
        total_cost = sum(float(r["cost"]) for r in rows)
        total_savings = sum(float(r["savings"]) for r in rows)

        by_model: dict[str, dict[str, Any]] = {}
        by_task: dict[str, dict[str, Any]] = {}

        for row in rows:
            model = row["model"]
            task = row["task_type"]

            if model not in by_model:
                by_model[model] = {"tokens": 0, "cost": 0, "requests": 0}
            by_model[model]["tokens"] += row["tokens"]
            by_model[model]["cost"] += float(row["cost"])
            by_model[model]["requests"] += row["requests"]

            if task not in by_task:
                by_task[task] = {"tokens": 0, "cost": 0, "requests": 0}
            by_task[task]["tokens"] += row["tokens"]
            by_task[task]["cost"] += float(row["cost"])
            by_task[task]["requests"] += row["requests"]

        return {
            "tenant_id": tenant_id,
            "period_start": start_time.isoformat(),
            "period_end": end_time.isoformat(),
            "total_requests": total_requests,
            "total_tokens": total_tokens,
            "total_cost": total_cost,
            "total_savings": total_savings,
            "savings_percent": (total_savings / (total_cost + total_savings) * 100) if (total_cost + total_savings) > 0 else 0,
            "by_model": by_model,
            "by_task": by_task,
        }

    def get_optimization_recommendations(
        self,
        usage_stats: dict[str, Any],
    ) -> list[str]:
        """Get recommendations for further optimization."""
        recommendations = []

        by_model = usage_stats.get("by_model", {})
        by_task = usage_stats.get("by_task", {})

        expensive_models = ["gpt-4-turbo", "claude-3-opus-20240229", "claude-opus-4-20250514"]
        for model in expensive_models:
            if model in by_model and by_model[model]["requests"] > 100:
                requests = by_model[model]["requests"]
                recommendations.append(
                    f"Consider using gpt-4o-mini for simple {model} tasks ({requests} requests)"
                )

        total_cost = usage_stats.get("total_cost", 0)
        total_savings = usage_stats.get("total_savings", 0)
        if total_cost > 100 and total_savings < total_cost * 0.1:
            recommendations.append("Enable aggressive prompt optimization to reduce costs")

        for task, data in by_task.items():
            if task in ["classification", "extraction"] and data["cost"] > 10:
                recommendations.append(
                    f"Task '{task}' could use a cheaper model (current cost: ${data['cost']:.2f})"
                )

        return recommendations
