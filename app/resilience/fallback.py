"""Multi-provider fallback chains with health-aware routing.

Provides automatic failover between providers, health tracking,
and intelligent provider selection based on success rates.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Generic, TypeVar

log = logging.getLogger("devcopilot.resilience.fallback")

T = TypeVar("T")


class ProviderHealth(Enum):
    """Health status of a provider."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


@dataclass
class ProviderStats:
    """Statistics for a provider."""

    total_calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    total_latency_ms: float = 0.0
    last_success_time: float | None = None
    last_failure_time: float | None = None
    consecutive_failures: int = 0
    consecutive_successes: int = 0

    @property
    def success_rate(self) -> float:
        """Calculate success rate (0.0 to 1.0)."""
        if self.total_calls == 0:
            return 1.0
        return self.successful_calls / self.total_calls

    @property
    def avg_latency_ms(self) -> float:
        """Calculate average latency in milliseconds."""
        if self.successful_calls == 0:
            return 0.0
        return self.total_latency_ms / self.successful_calls


@dataclass
class ProviderConfig:
    """Configuration for a single provider in the fallback chain.

    Attributes:
        name: Unique identifier for the provider
        priority: Lower number = higher priority (tried first)
        weight: Weight for weighted routing (higher = more traffic)
        health_threshold: Success rate below which provider is unhealthy
        degraded_threshold: Success rate below which provider is degraded
        cooldown_seconds: How long to avoid unhealthy provider
        max_consecutive_failures: Failures before marking unhealthy
    """

    name: str
    priority: int = 0
    weight: float = 1.0
    health_threshold: float = 0.5
    degraded_threshold: float = 0.8
    cooldown_seconds: float = 30.0
    max_consecutive_failures: int = 3


@dataclass
class Provider(Generic[T]):
    """A provider in the fallback chain.

    Attributes:
        config: Provider configuration
        call_fn: Function to call the provider
        stats: Runtime statistics
        health: Current health status
        unhealthy_since: When the provider became unhealthy
    """

    config: ProviderConfig
    call_fn: Callable[..., T]
    stats: ProviderStats = field(default_factory=ProviderStats)
    health: ProviderHealth = ProviderHealth.HEALTHY
    unhealthy_since: float | None = None


class FallbackChainError(Exception):
    """Raised when all providers in a fallback chain fail."""

    def __init__(
        self,
        message: str,
        provider_errors: dict[str, Exception],
    ):
        super().__init__(message)
        self.provider_errors = provider_errors


class FallbackChain(Generic[T]):
    """Multi-provider fallback chain with health-aware routing.

    Provides automatic failover between providers with:
    - Priority-based ordering
    - Health tracking and automatic recovery
    - Weighted routing for load distribution
    - Cooldown periods for unhealthy providers
    """

    def __init__(
        self,
        name: str,
        clock_fn: Callable[[], float] = time.monotonic,
    ):
        self.name = name
        self._clock = clock_fn
        self._providers: dict[str, Provider[T]] = {}
        self._lock = asyncio.Lock()

    def add_provider(
        self,
        config: ProviderConfig,
        call_fn: Callable[..., T],
    ) -> None:
        """Add a provider to the chain."""
        self._providers[config.name] = Provider(
            config=config,
            call_fn=call_fn,
        )
        log.debug("Added provider '%s' to fallback chain '%s'", config.name, self.name)

    def remove_provider(self, name: str) -> None:
        """Remove a provider from the chain."""
        if name in self._providers:
            del self._providers[name]
            log.debug("Removed provider '%s' from fallback chain '%s'", name, self.name)

    def get_provider_stats(self, name: str) -> ProviderStats | None:
        """Get statistics for a specific provider."""
        provider = self._providers.get(name)
        return provider.stats if provider else None

    def get_all_stats(self) -> dict[str, ProviderStats]:
        """Get statistics for all providers."""
        return {name: p.stats for name, p in self._providers.items()}

    def _update_health(self, provider: Provider[T]) -> None:
        """Update provider health based on stats."""
        stats = provider.stats
        config = provider.config

        if stats.consecutive_failures >= config.max_consecutive_failures:
            if provider.health != ProviderHealth.UNHEALTHY:
                provider.health = ProviderHealth.UNHEALTHY
                provider.unhealthy_since = self._clock()
                log.warning(
                    "Provider '%s' marked UNHEALTHY after %d consecutive failures",
                    config.name,
                    stats.consecutive_failures,
                )
        elif stats.success_rate < config.health_threshold:
            if provider.health != ProviderHealth.UNHEALTHY:
                provider.health = ProviderHealth.UNHEALTHY
                provider.unhealthy_since = self._clock()
                log.warning(
                    "Provider '%s' marked UNHEALTHY (success rate: %.2f%%)",
                    config.name,
                    stats.success_rate * 100,
                )
        elif stats.success_rate < config.degraded_threshold:
            provider.health = ProviderHealth.DEGRADED
            log.info(
                "Provider '%s' marked DEGRADED (success rate: %.2f%%)",
                config.name,
                stats.success_rate * 100,
            )
        else:
            if provider.health != ProviderHealth.HEALTHY:
                log.info(
                    "Provider '%s' recovered to HEALTHY (success rate: %.2f%%)",
                    config.name,
                    stats.success_rate * 100,
                )
            provider.health = ProviderHealth.HEALTHY
            provider.unhealthy_since = None

    def _is_available(self, provider: Provider[T]) -> bool:
        """Check if a provider is available for use."""
        if provider.health == ProviderHealth.HEALTHY:
            return True

        if provider.health == ProviderHealth.DEGRADED:
            return True

        if provider.unhealthy_since is not None:
            elapsed = self._clock() - provider.unhealthy_since
            if elapsed >= provider.config.cooldown_seconds:
                log.info(
                    "Provider '%s' cooldown expired, allowing probe",
                    provider.config.name,
                )
                return True

        return False

    def _get_ordered_providers(self) -> list[Provider[T]]:
        """Get providers ordered by priority and health."""
        available = [p for p in self._providers.values() if self._is_available(p)]

        def sort_key(p: Provider[T]) -> tuple[int, int, int]:
            health_order = {
                ProviderHealth.HEALTHY: 0,
                ProviderHealth.DEGRADED: 1,
                ProviderHealth.UNHEALTHY: 2,
            }
            return (health_order[p.health], p.config.priority, -p.stats.consecutive_successes)

        return sorted(available, key=sort_key)

    def _record_success(self, provider: Provider[T], latency_ms: float) -> None:
        """Record a successful call."""
        stats = provider.stats
        stats.total_calls += 1
        stats.successful_calls += 1
        stats.total_latency_ms += latency_ms
        stats.last_success_time = self._clock()
        stats.consecutive_failures = 0
        stats.consecutive_successes += 1
        self._update_health(provider)

    def _record_failure(self, provider: Provider[T], exception: Exception) -> None:
        """Record a failed call."""
        stats = provider.stats
        stats.total_calls += 1
        stats.failed_calls += 1
        stats.last_failure_time = self._clock()
        stats.consecutive_successes = 0
        stats.consecutive_failures += 1
        self._update_health(provider)
        log.debug(
            "Provider '%s' failed (consecutive: %d): %s",
            provider.config.name,
            stats.consecutive_failures,
            exception,
        )

    async def call(self, *args: Any, **kwargs: Any) -> T:
        """Call the fallback chain.

        Tries providers in priority/health order until one succeeds.

        Returns:
            Result from the first successful provider

        Raises:
            FallbackChainError: If all providers fail
        """
        providers = self._get_ordered_providers()

        if not providers:
            raise FallbackChainError(
                f"No available providers in chain '{self.name}'",
                {},
            )

        errors: dict[str, Exception] = {}

        for provider in providers:
            start = self._clock()
            try:
                result = provider.call_fn(*args, **kwargs)
                if asyncio.iscoroutine(result):
                    result = await result

                latency_ms = (self._clock() - start) * 1000
                self._record_success(provider, latency_ms)

                log.debug(
                    "Provider '%s' succeeded (latency: %.2fms)",
                    provider.config.name,
                    latency_ms,
                )
                return result  # type: ignore[return-value]

            except Exception as exc:
                self._record_failure(provider, exc)
                errors[provider.config.name] = exc

        raise FallbackChainError(
            f"All providers failed in chain '{self.name}'",
            errors,
        )

    def call_sync(self, *args: Any, **kwargs: Any) -> T:
        """Synchronous version of call().

        Note: This does not support async provider functions.
        """
        providers = self._get_ordered_providers()

        if not providers:
            raise FallbackChainError(
                f"No available providers in chain '{self.name}'",
                {},
            )

        errors: dict[str, Exception] = {}

        for provider in providers:
            start = self._clock()
            try:
                result = provider.call_fn(*args, **kwargs)
                latency_ms = (self._clock() - start) * 1000
                self._record_success(provider, latency_ms)

                log.debug(
                    "Provider '%s' succeeded (latency: %.2fms)",
                    provider.config.name,
                    latency_ms,
                )
                return result

            except Exception as exc:
                self._record_failure(provider, exc)
                errors[provider.config.name] = exc

        raise FallbackChainError(
            f"All providers failed in chain '{self.name}'",
            errors,
        )

    def reset_provider(self, name: str) -> None:
        """Reset a provider's stats and health."""
        provider = self._providers.get(name)
        if provider:
            provider.stats = ProviderStats()
            provider.health = ProviderHealth.HEALTHY
            provider.unhealthy_since = None
            log.info("Reset provider '%s'", name)

    def reset_all(self) -> None:
        """Reset all providers' stats and health."""
        for name in self._providers:
            self.reset_provider(name)


_fallback_chains: dict[str, FallbackChain[Any]] = {}


def get_fallback_chain(name: str) -> FallbackChain[Any] | None:
    """Get a fallback chain by name."""
    return _fallback_chains.get(name)


def create_fallback_chain(name: str) -> FallbackChain[Any]:
    """Create or get a fallback chain by name."""
    if name not in _fallback_chains:
        _fallback_chains[name] = FallbackChain(name)
    return _fallback_chains[name]


def reset_all_chains() -> None:
    """Reset all fallback chains (for testing)."""
    for chain in _fallback_chains.values():
        chain.reset_all()
    _fallback_chains.clear()
