"""Performance module for OmniPotent."""

from app.performance.request_coalescing import (
    BatchCoalescer,
    CoalescingStats,
    RequestCoalescer,
    coalesce,
    compute_request_key,
    get_all_stats,
    get_coalescer,
)

__all__ = [
    "BatchCoalescer",
    "CoalescingStats",
    "RequestCoalescer",
    "coalesce",
    "compute_request_key",
    "get_all_stats",
    "get_coalescer",
]
