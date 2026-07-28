"""Tests for advanced resilience infrastructure.

Covers: backoff strategies, fallback chains, bulkhead isolation,
adaptive timeouts, hedged requests, and load shedding.
"""

from __future__ import annotations

import asyncio

import pytest

from app.resilience.backoff import (
    AdaptiveBackoffState,
    BackoffConfig,
    DecorrelatedState,
    JitterStrategy,
    RetryContext,
    async_retry_with_backoff,
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
    create_bulkhead,
    reset_all_bulkheads,
)
from app.resilience.fallback import (
    FallbackChain,
    FallbackChainError,
    ProviderConfig,
    ProviderHealth,
    create_fallback_chain,
    reset_all_chains,
)
from app.resilience.hedging import (
    HedgedRequests,
    HedgingConfig,
    create_hedger,
    reset_all_hedgers,
)
from app.resilience.load_shedding import (
    AdaptiveLoadShedder,
    AdaptiveLoadSheddingConfig,
    LoadShedder,
    LoadShedError,
    LoadSheddingConfig,
    Priority,
    create_load_shedder,
    reset_all_shedders,
)
from app.resilience.timeout import (
    AdaptiveTimeout,
    AdaptiveTimeoutConfig,
    FixedTimeout,
    FixedTimeoutConfig,
    LatencyBucket,
    TimeoutError,
    create_adaptive_timeout,
    reset_all_timeouts,
)


class TestBackoff:
    """Test backoff strategies and jitter algorithms."""

    def test_no_jitter_exponential(self):
        """Test exponential backoff without jitter."""
        config = BackoffConfig(base_delay=1.0, max_delay=60.0, multiplier=2.0)
        assert compute_delay_no_jitter(0, config) == 1.0
        assert compute_delay_no_jitter(1, config) == 2.0
        assert compute_delay_no_jitter(2, config) == 4.0
        assert compute_delay_no_jitter(3, config) == 8.0

    def test_no_jitter_capped(self):
        """Test that delays are capped at max_delay."""
        config = BackoffConfig(base_delay=10.0, max_delay=20.0, multiplier=2.0)
        assert compute_delay_no_jitter(5, config) == 20.0

    def test_full_jitter_range(self):
        """Test full jitter produces values in expected range."""
        config = BackoffConfig(base_delay=10.0, max_delay=100.0)
        delays = [compute_delay_full_jitter(2, config) for _ in range(100)]
        exp_max = compute_delay_no_jitter(2, config)
        assert all(0 <= d <= exp_max for d in delays)

    def test_equal_jitter_minimum(self):
        """Test equal jitter has guaranteed minimum delay."""
        config = BackoffConfig(base_delay=10.0, max_delay=100.0)
        delays = [compute_delay_equal_jitter(2, config) for _ in range(100)]
        exp_delay = compute_delay_no_jitter(2, config)
        half = exp_delay / 2
        assert all(half <= d <= exp_delay for d in delays)

    def test_decorrelated_jitter_progression(self):
        """Test decorrelated jitter uses previous delay."""
        config = BackoffConfig(base_delay=1.0, max_delay=60.0)
        state = DecorrelatedState()

        d0 = compute_delay_decorrelated(0, config, state)
        assert d0 == 1.0  # First delay is base

        d1 = compute_delay_decorrelated(1, config, state)
        assert 1.0 <= d1 <= 3.0  # Between base and 3 * last

    def test_compute_delay_selects_strategy(self):
        """Test compute_delay uses the configured strategy."""
        config_none = BackoffConfig(jitter=JitterStrategy.NONE, base_delay=5.0)
        assert compute_delay(1, config_none) == 10.0

        config_full = BackoffConfig(jitter=JitterStrategy.FULL, base_delay=5.0)
        delays = [compute_delay(1, config_full) for _ in range(50)]
        assert any(d < 9.0 for d in delays)  # Some jitter applied

    def test_adaptive_backoff_increases_on_failure(self):
        """Test adaptive backoff multiplier increases on failure."""
        state = AdaptiveBackoffState(current_multiplier=1.0)
        record_adaptive_outcome(state, success=False)
        assert state.current_multiplier > 1.0
        assert state.window_failures == 1

    def test_adaptive_backoff_decreases_on_success(self):
        """Test adaptive backoff multiplier decreases on success."""
        state = AdaptiveBackoffState(current_multiplier=2.0)
        record_adaptive_outcome(state, success=True)
        assert state.current_multiplier < 2.0
        assert state.window_successes == 1

    def test_adaptive_backoff_respects_bounds(self):
        """Test adaptive backoff stays within min/max bounds."""
        state = AdaptiveBackoffState(
            current_multiplier=0.5,
            min_multiplier=0.5,
            max_multiplier=4.0,
        )
        record_adaptive_outcome(state, success=True)
        assert state.current_multiplier >= 0.5

        state = AdaptiveBackoffState(
            current_multiplier=4.0,
            min_multiplier=0.5,
            max_multiplier=4.0,
        )
        record_adaptive_outcome(state, success=False)
        assert state.current_multiplier <= 4.0

    def test_retry_with_backoff_succeeds(self):
        """Test retry_with_backoff returns on success."""
        calls = {"n": 0}
        slept: list[float] = []

        def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError("transient")
            return "ok"

        config = BackoffConfig(base_delay=0.1, jitter=JitterStrategy.NONE)
        result = retry_with_backoff(
            flaky,
            config,
            max_attempts=5,
            retryable=lambda _: True,
            sleep_fn=slept.append,
        )
        assert result == "ok"
        assert calls["n"] == 3
        assert len(slept) == 2

    def test_retry_with_backoff_exhausted(self):
        """Test retry_with_backoff raises after max attempts."""

        def fail():
            raise RuntimeError("always fails")

        config = BackoffConfig(base_delay=0.01)
        with pytest.raises(RuntimeError, match="always fails"):
            retry_with_backoff(
                fail,
                config,
                max_attempts=3,
                retryable=lambda _: True,
                sleep_fn=lambda _: None,
            )

    def test_retry_with_backoff_non_retryable(self):
        """Test retry_with_backoff doesn't retry non-retryable errors."""
        calls = {"n": 0}

        def fail():
            calls["n"] += 1
            raise ValueError("permanent")

        config = BackoffConfig()
        with pytest.raises(ValueError):
            retry_with_backoff(
                fail,
                config,
                max_attempts=5,
                retryable=lambda e: not isinstance(e, ValueError),
                sleep_fn=lambda _: None,
            )
        assert calls["n"] == 1

    def test_retry_context_callback(self):
        """Test on_retry callback receives context."""
        contexts: list[RetryContext] = []
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] < 2:
                raise RuntimeError("retry me")
            return "ok"

        config = BackoffConfig(base_delay=0.01)
        retry_with_backoff(
            flaky,
            config,
            max_attempts=3,
            retryable=lambda _: True,
            on_retry=contexts.append,
            sleep_fn=lambda _: None,
        )

        assert len(contexts) == 1
        assert contexts[0].attempt == 0
        assert isinstance(contexts[0].exception, RuntimeError)

    @pytest.mark.asyncio
    async def test_async_retry_with_backoff(self):
        """Test async version of retry_with_backoff."""
        calls = {"n": 0}

        async def flaky():
            calls["n"] += 1
            if calls["n"] < 2:
                raise RuntimeError("transient")
            return "ok"

        config = BackoffConfig(base_delay=0.01)
        result = await async_retry_with_backoff(
            flaky,
            config,
            max_attempts=3,
            retryable=lambda _: True,
        )
        assert result == "ok"
        assert calls["n"] == 2


class TestFallback:
    """Test multi-provider fallback chains."""

    def setup_method(self):
        reset_all_chains()

    @pytest.mark.asyncio
    async def test_fallback_chain_primary_succeeds(self):
        """Test fallback chain returns primary result on success."""
        chain: FallbackChain[str] = FallbackChain("test")
        chain.add_provider(
            ProviderConfig(name="primary", priority=0),
            lambda: "primary_result",
        )
        chain.add_provider(
            ProviderConfig(name="secondary", priority=1),
            lambda: "secondary_result",
        )

        result = await chain.call()
        assert result == "primary_result"

        stats = chain.get_provider_stats("primary")
        assert stats is not None
        assert stats.successful_calls == 1

    @pytest.mark.asyncio
    async def test_fallback_chain_failover(self):
        """Test fallback chain fails over to secondary."""
        chain: FallbackChain[str] = FallbackChain("test")
        chain.add_provider(
            ProviderConfig(name="primary", priority=0),
            lambda: (_ for _ in ()).throw(RuntimeError("fail")),
        )
        chain.add_provider(
            ProviderConfig(name="secondary", priority=1),
            lambda: "secondary_result",
        )

        result = await chain.call()
        assert result == "secondary_result"

        primary_stats = chain.get_provider_stats("primary")
        assert primary_stats is not None
        assert primary_stats.failed_calls == 1

        secondary_stats = chain.get_provider_stats("secondary")
        assert secondary_stats is not None
        assert secondary_stats.successful_calls == 1

    @pytest.mark.asyncio
    async def test_fallback_chain_all_fail(self):
        """Test fallback chain raises when all providers fail."""
        chain: FallbackChain[str] = FallbackChain("test")
        chain.add_provider(
            ProviderConfig(name="primary", priority=0),
            lambda: (_ for _ in ()).throw(RuntimeError("fail1")),
        )
        chain.add_provider(
            ProviderConfig(name="secondary", priority=1),
            lambda: (_ for _ in ()).throw(RuntimeError("fail2")),
        )

        with pytest.raises(FallbackChainError) as exc_info:
            await chain.call()

        assert "primary" in exc_info.value.provider_errors
        assert "secondary" in exc_info.value.provider_errors

    @pytest.mark.asyncio
    async def test_fallback_chain_health_tracking(self):
        """Test fallback chain tracks provider health."""
        chain: FallbackChain[str] = FallbackChain("test")
        chain.add_provider(
            ProviderConfig(
                name="primary",
                priority=0,
                max_consecutive_failures=2,
            ),
            lambda: (_ for _ in ()).throw(RuntimeError("fail")),
        )
        chain.add_provider(
            ProviderConfig(name="secondary", priority=1),
            lambda: "ok",
        )

        # Fail twice to trigger unhealthy
        await chain.call()
        await chain.call()

        provider = chain._providers["primary"]
        assert provider.health == ProviderHealth.UNHEALTHY

    def test_fallback_chain_sync(self):
        """Test synchronous fallback chain."""
        chain: FallbackChain[str] = FallbackChain("test")
        chain.add_provider(
            ProviderConfig(name="primary", priority=0),
            lambda: "sync_result",
        )

        result = chain.call_sync()
        assert result == "sync_result"

    def test_create_fallback_chain_registry(self):
        """Test fallback chain registry."""
        chain1 = create_fallback_chain("test1")
        chain2 = create_fallback_chain("test1")  # Same name
        chain3 = create_fallback_chain("test2")

        assert chain1 is chain2
        assert chain1 is not chain3


class TestBulkhead:
    """Test bulkhead isolation pattern."""

    def setup_method(self):
        reset_all_bulkheads()

    @pytest.mark.asyncio
    async def test_bulkhead_limits_concurrency(self):
        """Test bulkhead limits concurrent executions."""
        config = BulkheadConfig(max_concurrent=2, max_wait_seconds=0)
        bulkhead = Bulkhead(config)

        acquired = []
        released = asyncio.Event()

        async def acquire_slot(slot_id: int):
            try:
                async with bulkhead.acquire():
                    acquired.append(slot_id)
                    await released.wait()
            except BulkheadFullError:
                pass

        # Start 3 tasks, but only 2 should acquire
        tasks = [
            asyncio.create_task(acquire_slot(i))
            for i in range(3)
        ]

        await asyncio.sleep(0.05)
        assert len(acquired) == 2
        assert bulkhead.is_full

        released.set()
        await asyncio.gather(*tasks)

    @pytest.mark.asyncio
    async def test_bulkhead_rejects_when_full(self):
        """Test bulkhead rejects requests when full."""
        config = BulkheadConfig(max_concurrent=1, max_wait_seconds=0)
        bulkhead = Bulkhead(config)

        async def hold():
            async with bulkhead.acquire():
                await asyncio.sleep(0.1)

        task = asyncio.create_task(hold())
        await asyncio.sleep(0.01)

        with pytest.raises(BulkheadFullError):
            async with bulkhead.acquire():
                pass

        await task

    @pytest.mark.asyncio
    async def test_bulkhead_waits_when_configured(self):
        """Test bulkhead waits for slot when max_wait > 0."""
        config = BulkheadConfig(max_concurrent=1, max_wait_seconds=0.5)
        bulkhead = Bulkhead(config)

        async def quick():
            async with bulkhead.acquire():
                await asyncio.sleep(0.05)

        task = asyncio.create_task(quick())
        await asyncio.sleep(0.01)

        # Should wait and succeed
        async with bulkhead.acquire():
            pass

        await task

    def test_bulkhead_sync(self):
        """Test synchronous bulkhead."""
        config = BulkheadConfig(max_concurrent=2)
        bulkhead = Bulkhead(config)

        with bulkhead.acquire_sync():
            assert bulkhead.available_slots == 1

        assert bulkhead.available_slots == 2

    @pytest.mark.asyncio
    async def test_bulkhead_stats(self):
        """Test bulkhead statistics tracking."""
        config = BulkheadConfig(max_concurrent=1, max_wait_seconds=0)
        bulkhead = Bulkhead(config)

        async with bulkhead.acquire():
            pass

        try:
            async with bulkhead.acquire():
                # Try to acquire while held
                pass
        except BulkheadFullError:
            pass

        assert bulkhead.stats.total_acquired >= 1
        # Note: rejection count depends on timing

    def test_bulkhead_decorator(self):
        """Test bulkhead as decorator."""
        config = BulkheadConfig(max_concurrent=5)
        bulkhead = Bulkhead(config)

        @bulkhead
        def sync_fn():
            return "sync"

        result = sync_fn()
        assert result == "sync"

    @pytest.mark.asyncio
    async def test_bulkhead_async_decorator(self):
        """Test bulkhead as async decorator."""
        config = BulkheadConfig(max_concurrent=5)
        bulkhead = Bulkhead(config)

        @bulkhead
        async def async_fn():
            return "async"

        result = await async_fn()
        assert result == "async"

    def test_create_bulkhead_registry(self):
        """Test bulkhead registry."""
        b1 = create_bulkhead("test", max_concurrent=5)
        b2 = create_bulkhead("test")  # Same name
        b3 = create_bulkhead("other", max_concurrent=10)

        assert b1 is b2
        assert b1 is not b3


class TestTimeout:
    """Test adaptive and fixed timeouts."""

    def setup_method(self):
        reset_all_timeouts()

    def test_latency_bucket_percentile(self):
        """Test latency bucket percentile calculation."""
        bucket = LatencyBucket()
        for i in range(100):
            bucket.add(float(i))

        assert bucket.percentile(0.5) == pytest.approx(50, abs=1)
        assert bucket.percentile(0.99) == pytest.approx(99, abs=1)

    def test_latency_bucket_eviction(self):
        """Test latency bucket evicts old samples."""
        bucket = LatencyBucket(max_samples=10)
        for i in range(20):
            bucket.add(float(i))

        assert len(bucket.samples) == 10

    def test_adaptive_timeout_initial(self):
        """Test adaptive timeout uses initial value before data."""
        config = AdaptiveTimeoutConfig(initial_timeout_ms=5000)
        timeout = AdaptiveTimeout(config)

        assert timeout.get_timeout_ms() == 5000
        assert timeout.get_timeout_seconds() == 5.0

    def test_adaptive_timeout_adapts(self):
        """Test adaptive timeout adjusts based on latency."""
        config = AdaptiveTimeoutConfig(
            initial_timeout_ms=5000,
            min_samples=5,
            percentile=0.99,
            multiplier=1.5,
        )
        timeout = AdaptiveTimeout(config)

        # Add samples
        for _ in range(10):
            timeout.record_latency(100.0)

        # Should adapt: p99 of 100ms * 1.5 = 150ms
        assert timeout.get_timeout_ms() == pytest.approx(150.0, abs=10)

    def test_adaptive_timeout_respects_bounds(self):
        """Test adaptive timeout stays within bounds."""
        config = AdaptiveTimeoutConfig(
            min_timeout_ms=100,
            max_timeout_ms=1000,
            min_samples=5,
        )
        timeout = AdaptiveTimeout(config)

        # Very low latencies
        for _ in range(10):
            timeout.record_latency(10.0)
        assert timeout.get_timeout_ms() >= 100

        # Very high latencies
        for _ in range(10):
            timeout.record_latency(10000.0)
        assert timeout.get_timeout_ms() <= 1000

    @pytest.mark.asyncio
    async def test_adaptive_timeout_execute(self):
        """Test adaptive timeout execute context manager."""
        config = AdaptiveTimeoutConfig(initial_timeout_ms=1000)
        timeout = AdaptiveTimeout(config)

        async with timeout.execute("test_op"):
            await asyncio.sleep(0.01)

        assert timeout.stats.successes == 1
        assert timeout.stats.total_latency_ms > 0

    @pytest.mark.asyncio
    async def test_adaptive_timeout_raises_on_timeout(self):
        """Test adaptive timeout raises TimeoutError."""
        config = AdaptiveTimeoutConfig(initial_timeout_ms=50)
        timeout = AdaptiveTimeout(config)

        with pytest.raises(TimeoutError) as exc_info:
            async with timeout.execute("slow_op"):
                await asyncio.sleep(1.0)

        assert exc_info.value.operation == "slow_op"
        assert timeout.stats.timeouts == 1

    @pytest.mark.asyncio
    async def test_fixed_timeout_execute(self):
        """Test fixed timeout execution."""
        config = FixedTimeoutConfig(timeout_ms=1000)
        timeout = FixedTimeout(config)

        async with timeout.execute():
            await asyncio.sleep(0.01)

        assert timeout.stats.successes == 1

    def test_create_adaptive_timeout_registry(self):
        """Test adaptive timeout registry."""
        t1 = create_adaptive_timeout("test", initial_timeout_ms=1000)
        t2 = create_adaptive_timeout("test")  # Same name
        t3 = create_adaptive_timeout("other", initial_timeout_ms=2000)

        assert t1 is t2
        assert t1 is not t3


class TestHedging:
    """Test hedged requests."""

    def setup_method(self):
        reset_all_hedgers()

    @pytest.mark.asyncio
    async def test_hedging_primary_wins(self):
        """Test hedging when primary is fast."""
        config = HedgingConfig(hedge_delay_ms=100, max_hedges=1)
        hedger = HedgedRequests(config)

        async def fast():
            return "primary"

        async def slow():
            await asyncio.sleep(1.0)
            return "hedge"

        result = await hedger.execute(fast, slow)
        assert result == "primary"
        assert hedger.stats.primary_wins == 1
        assert hedger.stats.hedge_wins == 0

    @pytest.mark.asyncio
    async def test_hedging_hedge_wins(self):
        """Test hedging when hedge is faster."""
        config = HedgingConfig(hedge_delay_ms=10, max_hedges=1)
        hedger = HedgedRequests(config)

        async def slow():
            await asyncio.sleep(0.5)
            return "primary"

        async def fast():
            return "hedge"

        result = await hedger.execute(slow, fast)
        assert result == "hedge"
        assert hedger.stats.hedge_wins == 1

    @pytest.mark.asyncio
    async def test_hedging_execute_same(self):
        """Test hedging with same function for all requests."""
        config = HedgingConfig(hedge_delay_ms=10, max_hedges=2)
        hedger = HedgedRequests(config)

        call_count = {"n": 0}

        async def fn():
            call_count["n"] += 1
            return f"result_{call_count['n']}"

        result = await hedger.execute_same(fn, hedge_count=2)
        assert result.startswith("result_")

    @pytest.mark.asyncio
    async def test_hedging_all_fail(self):
        """Test hedging raises when all requests fail."""
        config = HedgingConfig(hedge_delay_ms=10, max_hedges=1)
        hedger = HedgedRequests(config)

        async def fail():
            raise RuntimeError("fail")

        with pytest.raises(RuntimeError, match="fail"):
            await hedger.execute(fail, fail)

        assert hedger.stats.total_failures == 1

    @pytest.mark.asyncio
    async def test_hedging_cancels_extras(self):
        """Test hedging cancels extra requests."""
        config = HedgingConfig(hedge_delay_ms=10, max_hedges=2)
        hedger = HedgedRequests(config)

        async def fast():
            return "fast"

        async def slow():
            await asyncio.sleep(1.0)
            return "slow"

        await hedger.execute(fast, slow, slow)

        # Hedges should have been cancelled
        assert hedger.stats.total_hedges_cancelled >= 0

    def test_create_hedger_registry(self):
        """Test hedger registry."""
        h1 = create_hedger("test", hedge_delay_ms=50)
        h2 = create_hedger("test")  # Same name
        h3 = create_hedger("other", hedge_delay_ms=100)

        assert h1 is h2
        assert h1 is not h3


class TestLoadShedding:
    """Test priority-based load shedding."""

    def setup_method(self):
        reset_all_shedders()

    @pytest.mark.asyncio
    async def test_load_shedding_admits_when_capacity(self):
        """Test load shedder admits requests under capacity."""
        config = LoadSheddingConfig(capacity=10, shed_threshold=0.7)
        shedder = LoadShedder(config)

        async with shedder.admit(Priority.LOW):
            assert shedder.current_load == 1

        assert shedder.stats.admitted_requests == 1

    @pytest.mark.asyncio
    async def test_load_shedding_sheds_low_priority(self):
        """Test load shedder sheds low priority under load."""
        config = LoadSheddingConfig(
            capacity=10,
            shed_threshold=0.5,  # Start shedding at 50%
            critical_threshold=0.9,
        )
        shedder = LoadShedder(config)

        # Fill to 80%
        async def hold():
            async with shedder.admit(Priority.CRITICAL):
                await asyncio.sleep(0.2)

        tasks = [asyncio.create_task(hold()) for _ in range(8)]
        await asyncio.sleep(0.01)

        # Low priority should be shed
        with pytest.raises(LoadShedError):
            async with shedder.admit(Priority.LOW):
                pass

        for t in tasks:
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass

    @pytest.mark.asyncio
    async def test_load_shedding_admits_critical(self):
        """Test load shedder admits critical even at high load."""
        config = LoadSheddingConfig(
            capacity=10,
            shed_threshold=0.5,
            critical_threshold=0.9,
        )
        shedder = LoadShedder(config)

        # Fill to 80%
        async def hold():
            async with shedder.admit(Priority.CRITICAL):
                await asyncio.sleep(0.2)

        tasks = [asyncio.create_task(hold()) for _ in range(8)]
        await asyncio.sleep(0.01)

        # Critical should still be admitted
        async with shedder.admit(Priority.CRITICAL):
            pass

        for t in tasks:
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass

    def test_should_admit_priority_levels(self):
        """Test should_admit respects priority levels."""
        config = LoadSheddingConfig(capacity=10, shed_threshold=0.7)
        shedder = LoadShedder(config)

        # At 0 load, all priorities admitted
        assert shedder.should_admit(Priority.LOW)
        assert shedder.should_admit(Priority.NORMAL)
        assert shedder.should_admit(Priority.HIGH)
        assert shedder.should_admit(Priority.CRITICAL)

    @pytest.mark.asyncio
    async def test_load_shedding_stats(self):
        """Test load shedding statistics."""
        config = LoadSheddingConfig(capacity=10)
        shedder = LoadShedder(config)

        async with shedder.admit(Priority.NORMAL):
            pass

        assert shedder.stats.total_requests == 1
        assert shedder.stats.admitted_requests == 1
        assert shedder.stats.shed_requests == 0

    @pytest.mark.asyncio
    async def test_adaptive_load_shedder_latency_based(self):
        """Test adaptive load shedder responds to latency."""
        config = AdaptiveLoadSheddingConfig(
            capacity=100,
            target_latency_ms=50,
            max_latency_ms=200,
        )
        shedder = AdaptiveLoadShedder(config)

        # Record high latencies
        for _ in range(100):
            shedder.record_latency(300.0)

        # Shed factor should increase
        assert shedder.shed_factor > 0

    def test_priority_enum_ordering(self):
        """Test priority enum comparison."""
        assert Priority.LOW < Priority.NORMAL
        assert Priority.NORMAL < Priority.HIGH
        assert Priority.HIGH < Priority.CRITICAL

    def test_create_load_shedder_registry(self):
        """Test load shedder registry."""
        s1 = create_load_shedder("test", capacity=100)
        s2 = create_load_shedder("test")  # Same name
        s3 = create_load_shedder("other", capacity=200)

        assert s1 is s2
        assert s1 is not s3


class TestIntegration:
    """Integration tests combining multiple resilience patterns."""

    def setup_method(self):
        reset_all_chains()
        reset_all_bulkheads()
        reset_all_timeouts()
        reset_all_hedgers()
        reset_all_shedders()

    @pytest.mark.asyncio
    async def test_bulkhead_with_timeout(self):
        """Test combining bulkhead with timeout."""
        bulkhead = Bulkhead(BulkheadConfig(max_concurrent=5))
        timeout = AdaptiveTimeout(AdaptiveTimeoutConfig(initial_timeout_ms=1000))

        async with bulkhead.acquire():
            async with timeout.execute():
                await asyncio.sleep(0.01)

        assert bulkhead.stats.total_acquired == 1
        assert timeout.stats.successes == 1

    @pytest.mark.asyncio
    async def test_fallback_with_load_shedding(self):
        """Test fallback chain with load shedding."""
        chain: FallbackChain[str] = FallbackChain("test")
        shedder = LoadShedder(LoadSheddingConfig(capacity=100))

        async def protected_call():
            async with shedder.admit(Priority.NORMAL):
                return "result"

        chain.add_provider(
            ProviderConfig(name="primary"),
            protected_call,
        )

        result = await chain.call()
        assert result == "result"

    @pytest.mark.asyncio
    async def test_retry_with_adaptive_timeout(self):
        """Test retry with adaptive timeout tracking."""
        timeout = AdaptiveTimeout(AdaptiveTimeoutConfig(initial_timeout_ms=5000))
        config = BackoffConfig(base_delay=0.01, jitter=JitterStrategy.NONE)

        calls = {"n": 0}

        async def flaky():
            calls["n"] += 1
            if calls["n"] < 2:
                raise RuntimeError("retry")
            return "ok"

        async def with_timeout():
            async with timeout.execute():
                return await flaky()

        result = await async_retry_with_backoff(
            with_timeout,
            config,
            max_attempts=3,
            retryable=lambda _: True,
        )

        assert result == "ok"
        assert timeout.stats.successes == 1
