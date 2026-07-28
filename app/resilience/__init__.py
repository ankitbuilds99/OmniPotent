"""Advanced resilience infrastructure for DevOps Copilot.

Provides:
- Decorrelated jitter backoff (AWS recommended)
- Multi-provider fallback chains with health-aware routing
- Bulkhead isolation pattern
- Adaptive timeouts based on latency percentiles
- Hedged requests for tail latency reduction
- Priority-based load shedding

Also re-exports legacy primitives from app/resilience.py for backwards compatibility.
"""

from app.resilience_core import (
    CircuitBreaker as LegacyCircuitBreaker,
    CircuitOpenError as LegacyCircuitOpenError,
    backoff_delays,
    is_retryable,
    retry_call,
)
from app.resilience.backoff import (
    AdaptiveBackoffState,
    BackoffConfig,
    DecorrelatedState,
    JitterStrategy,
    RetryContext,
    async_retry_with_backoff,
    compute_adaptive_delay,
    compute_delay,
    compute_delay_decorrelated,
    compute_delay_equal_jitter,
    compute_delay_full_jitter,
    compute_delay_no_jitter,
    record_adaptive_outcome,
    retry_with_backoff,
)
from app.resilience.bulkhead import (
    Bulkhead,
    BulkheadConfig,
    BulkheadFullError,
    BulkheadStats,
    create_bulkhead,
    get_bulkhead,
    reset_all_bulkheads,
)
from app.resilience.fallback import (
    FallbackChain,
    FallbackChainError,
    Provider,
    ProviderConfig,
    ProviderHealth,
    ProviderStats,
    create_fallback_chain,
    get_fallback_chain,
    reset_all_chains,
)
from app.resilience.hedging import (
    HedgedRequests,
    HedgingConfig,
    HedgingStats,
    create_hedger,
    get_hedger,
    reset_all_hedgers,
)
from app.resilience.load_shedding import (
    AdaptiveLoadShedder,
    AdaptiveLoadSheddingConfig,
    LoadShedder,
    LoadShedError,
    LoadSheddingConfig,
    LoadSheddingStats,
    Priority,
    create_adaptive_shedder,
    create_load_shedder,
    get_load_shedder,
    reset_all_shedders,
)
from app.resilience.timeout import (
    AdaptiveTimeout,
    AdaptiveTimeoutConfig,
    FixedTimeout,
    FixedTimeoutConfig,
    LatencyBucket,
    TimeoutError,
    TimeoutStats,
    create_adaptive_timeout,
    create_fixed_timeout,
    get_adaptive_timeout,
    reset_all_timeouts,
)

__all__ = [
    # Legacy (from app/resilience_core.py)
    "LegacyCircuitBreaker",
    "LegacyCircuitOpenError",
    "backoff_delays",
    "is_retryable",
    "retry_call",
    # Backoff
    "JitterStrategy",
    "BackoffConfig",
    "DecorrelatedState",
    "AdaptiveBackoffState",
    "RetryContext",
    "compute_delay",
    "compute_delay_no_jitter",
    "compute_delay_full_jitter",
    "compute_delay_equal_jitter",
    "compute_delay_decorrelated",
    "compute_adaptive_delay",
    "record_adaptive_outcome",
    "retry_with_backoff",
    "async_retry_with_backoff",
    # Fallback
    "ProviderHealth",
    "ProviderStats",
    "ProviderConfig",
    "Provider",
    "FallbackChainError",
    "FallbackChain",
    "get_fallback_chain",
    "create_fallback_chain",
    "reset_all_chains",
    # Bulkhead
    "BulkheadFullError",
    "BulkheadStats",
    "BulkheadConfig",
    "Bulkhead",
    "get_bulkhead",
    "create_bulkhead",
    "reset_all_bulkheads",
    # Timeout
    "TimeoutError",
    "LatencyBucket",
    "AdaptiveTimeoutConfig",
    "TimeoutStats",
    "AdaptiveTimeout",
    "FixedTimeoutConfig",
    "FixedTimeout",
    "get_adaptive_timeout",
    "create_adaptive_timeout",
    "create_fixed_timeout",
    "reset_all_timeouts",
    # Hedging
    "HedgingConfig",
    "HedgingStats",
    "HedgedRequests",
    "get_hedger",
    "create_hedger",
    "reset_all_hedgers",
    # Load Shedding
    "Priority",
    "LoadSheddingConfig",
    "LoadSheddingStats",
    "LoadShedError",
    "LoadShedder",
    "AdaptiveLoadSheddingConfig",
    "AdaptiveLoadShedder",
    "get_load_shedder",
    "create_load_shedder",
    "create_adaptive_shedder",
    "reset_all_shedders",
]
