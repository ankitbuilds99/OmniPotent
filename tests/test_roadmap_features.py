"""Tests for roadmap features: session binding, debug mode, request coalescing, etc."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.security.session_binding import (
    BindingStrength,
    DeviceFingerprint,
    SessionBindingManager,
)
from app.debug.debug_mode import (
    DebugLevel,
    DebugSession,
    DebugTimer,
    debug_enabled,
    debug_span,
)
from app.performance.request_coalescing import (
    RequestCoalescer,
    compute_request_key,
)
from app.llm_advanced.model_router import (
    ComplexityAnalyzer,
    ModelRouter,
    RoutingContext,
    TaskComplexity,
)
from app.analytics.cost_attribution import (
    CostTracker,
)
from app.resilience.graceful_degradation import (
    DegradationLevel,
    FeaturePriority,
    GracefulDegradation,
    SystemLoad,
)
from app.llm_advanced.semantic_cache import (
    SemanticCache,
    cosine_similarity,
)
from app.integrations.slack_bot import (
    SlackBlockBuilder,
    SlackBot,
    SlackEventType,
)


class TestSessionBinding:
    def test_create_session(self):
        manager = SessionBindingManager("test-secret")
        fingerprint = DeviceFingerprint(
            user_agent="Mozilla/5.0",
            accept_language="en-US",
        )

        session = manager.create_session(
            session_id="sess-123",
            user_id="user-456",
            fingerprint=fingerprint,
        )

        assert session.session_id == "sess-123"
        assert session.user_id == "user-456"
        assert session.fingerprint_hash is not None

    def test_verify_session_success(self):
        manager = SessionBindingManager("test-secret")
        fingerprint = DeviceFingerprint(
            user_agent="Mozilla/5.0",
            accept_language="en-US",
        )

        manager.create_session("sess-123", "user-456", fingerprint)
        result = manager.verify_session("sess-123", fingerprint)

        assert result.valid is True
        assert result.confidence >= 0.9

    def test_verify_session_fingerprint_mismatch(self):
        manager = SessionBindingManager(
            "test-secret",
            default_strength=BindingStrength.STRICT,
        )
        fingerprint1 = DeviceFingerprint(
            user_agent="Mozilla/5.0",
            accept_language="en-US",
        )
        fingerprint2 = DeviceFingerprint(
            user_agent="Chrome/100.0",
            accept_language="fr-FR",
        )

        manager.create_session("sess-123", "user-456", fingerprint1)
        result = manager.verify_session("sess-123", fingerprint2)

        assert result.valid is False
        assert result.anomaly_detected is True

    def test_invalidate_session(self):
        manager = SessionBindingManager("test-secret")
        fingerprint = DeviceFingerprint(
            user_agent="Mozilla/5.0",
            accept_language="en-US",
        )

        manager.create_session("sess-123", "user-456", fingerprint)
        assert manager.invalidate_session("sess-123") is True

        result = manager.verify_session("sess-123", fingerprint)
        assert result.valid is False
        assert result.reason == "session_not_found"


class TestDebugMode:
    def test_debug_session_context(self):
        with DebugSession(level=DebugLevel.DETAILED) as session:
            assert debug_enabled() is True
            assert session.context.level == DebugLevel.DETAILED

        assert debug_enabled() is False

    def test_debug_span(self):
        with DebugSession(level=DebugLevel.VERBOSE) as session:
            with session.span("test-operation", {"key": "value"}) as span:
                span.add_event("checkpoint", {"data": 123})

            assert len(session.context.spans) == 1
            assert session.context.spans[0].name == "test-operation"
            assert len(session.context.spans[0].events) == 1

    def test_debug_timer(self):
        timer = DebugTimer(name="test-timer")
        timer.checkpoint("step1")
        timer.checkpoint("step2")

        summary = timer.summary()
        assert summary["name"] == "test-timer"
        assert len(summary["checkpoints"]) == 2

    def test_debug_decorator(self):
        @debug_span("decorated-func")
        def my_function(x: int) -> int:
            return x * 2

        with DebugSession(level=DebugLevel.BASIC):
            result = my_function(5)
            assert result == 10


class TestRequestCoalescing:
    @pytest.mark.asyncio
    async def test_coalesce_identical_requests(self):
        coalescer: RequestCoalescer[int] = RequestCoalescer(
            name="test",
            max_coalesce_time_ms=10,
        )

        call_count = 0

        async def expensive_operation() -> int:
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.05)
            return 42

        results = await asyncio.gather(
            coalescer.execute("key1", expensive_operation),
            coalescer.execute("key1", expensive_operation),
            coalescer.execute("key1", expensive_operation),
        )

        assert all(r == 42 for r in results)
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_different_keys_not_coalesced(self):
        coalescer: RequestCoalescer[str] = RequestCoalescer(
            name="test",
            max_coalesce_time_ms=10,
        )

        call_count = 0

        async def operation() -> str:
            nonlocal call_count
            call_count += 1
            return f"result-{call_count}"

        result1 = await coalescer.execute("key1", operation)
        result2 = await coalescer.execute("key2", operation)

        assert result1 != result2
        assert call_count == 2

    def test_compute_request_key(self):
        key1 = compute_request_key("arg1", "arg2", option="value")
        key2 = compute_request_key("arg1", "arg2", option="value")
        key3 = compute_request_key("arg1", "arg3", option="value")

        assert key1 == key2
        assert key1 != key3


class TestModelRouter:
    def test_complexity_analyzer(self):
        analyzer = ComplexityAnalyzer()

        assert analyzer.analyze("list all users") == TaskComplexity.TRIVIAL
        assert analyzer.analyze("explain this error") == TaskComplexity.SIMPLE
        assert analyzer.analyze("analyze the performance") == TaskComplexity.COMPLEX
        assert analyzer.analyze("compare options A and B") == TaskComplexity.MODERATE

    def test_route_simple_query(self):
        router = ModelRouter()
        context = RoutingContext(
            query="what is 2+2",
            estimated_input_tokens=10,
            estimated_output_tokens=5,
        )

        decision = router.route(context)

        assert decision.model is not None
        assert decision.complexity in [TaskComplexity.TRIVIAL, TaskComplexity.SIMPLE]
        assert decision.estimated_cost >= 0

    def test_route_with_capability_requirement(self):
        router = ModelRouter()
        context = RoutingContext(
            query="analyze this image",
            estimated_input_tokens=100,
            estimated_output_tokens=500,
            required_capabilities={"vision"},
        )

        decision = router.route(context)

        assert "vision" in decision.model.capabilities

    def test_route_with_cost_constraint(self):
        router = ModelRouter()
        context = RoutingContext(
            query="write a long essay",
            estimated_input_tokens=1000,
            estimated_output_tokens=4000,
            max_cost=0.01,
        )

        decision = router.route(context)

        assert decision.estimated_cost <= 0.01 or decision.reason == "fallback_to_default"


class TestCostAttribution:
    def test_record_cost(self):
        tracker = CostTracker()

        entry = tracker.record(
            tenant_id="tenant-1",
            model="gpt-4o",
            input_tokens=1000,
            output_tokens=500,
            user_id="user-1",
            feature="investigation",
        )

        assert entry.tenant_id == "tenant-1"
        assert entry.amount > Decimal(0)

    def test_cost_summary(self):
        tracker = CostTracker()

        tracker.record("tenant-1", "gpt-4o", 1000, 500)
        tracker.record("tenant-1", "gpt-4o-mini", 500, 200)

        now = datetime.now(timezone.utc)
        summary = tracker.get_summary(
            tenant_id="tenant-1",
            start=now - timedelta(hours=1),
            end=now + timedelta(hours=1),
        )

        assert summary.total_requests == 2
        assert summary.total_amount > Decimal(0)
        assert len(summary.by_model) == 2

    def test_budget_check(self):
        tracker = CostTracker()
        tracker.set_budget(
            tenant_id="tenant-1",
            monthly_limit=Decimal("10.00"),
            hard_limit=True,
        )

        can_proceed, reason = tracker.can_proceed("tenant-1", Decimal("5.00"))
        assert can_proceed is True

    def test_budget_exceeded(self):
        tracker = CostTracker()
        tracker.set_budget(
            tenant_id="tenant-1",
            monthly_limit=Decimal("0.01"),
            hard_limit=True,
        )

        tracker.record("tenant-1", "gpt-4o", 10000, 5000)

        can_proceed, reason = tracker.can_proceed("tenant-1", Decimal("0.01"))
        assert can_proceed is False
        assert "exceeded" in reason


class TestGracefulDegradation:
    def test_feature_registration(self):
        degradation = GracefulDegradation()

        config = degradation.register_feature(
            name="expensive-feature",
            priority=FeaturePriority.LOW,
            fallback_value="cached-result",
        )

        assert config.name == "expensive-feature"
        assert config.priority == FeaturePriority.LOW

    def test_feature_enabled_at_normal_level(self):
        degradation = GracefulDegradation()
        degradation.register_feature("low-priority", FeaturePriority.LOW)
        degradation.register_feature("high-priority", FeaturePriority.HIGH)

        assert degradation.is_feature_enabled("low-priority") is True
        assert degradation.is_feature_enabled("high-priority") is True

    def test_feature_disabled_under_load(self):
        degradation = GracefulDegradation()
        degradation.register_feature("optional-feature", FeaturePriority.OPTIONAL)

        degradation.force_level(DegradationLevel.ELEVATED)

        assert degradation.is_feature_enabled("optional-feature") is False

    def test_system_load_updates_level(self):
        degradation = GracefulDegradation(auto_degrade=True)

        high_load = SystemLoad(
            cpu_percent=95,
            memory_percent=90,
            error_rate=0.15,
            avg_latency_ms=8000,
        )

        degradation.update_load(high_load)

        assert degradation._current_level.value > DegradationLevel.NORMAL.value

    @pytest.mark.asyncio
    async def test_execute_with_fallback(self):
        degradation = GracefulDegradation()
        degradation.register_feature(
            name="test-feature",
            priority=FeaturePriority.LOW,
            fallback_value="fallback",
        )
        degradation.force_level(DegradationLevel.SEVERE)

        async def real_operation() -> str:
            return "real-result"

        result = await degradation.execute("test-feature", real_operation)

        assert result == "fallback"


class TestSemanticCache:
    @pytest.mark.asyncio
    async def test_exact_cache_hit(self):
        cache = SemanticCache()

        await cache.set(
            query="what is python",
            response="Python is a programming language",
            model="gpt-4o",
        )

        entry = await cache.get("what is python", "gpt-4o")

        assert entry is not None
        assert entry.response == "Python is a programming language"

    @pytest.mark.asyncio
    async def test_cache_miss(self):
        cache = SemanticCache()

        entry = await cache.get("unknown query", "gpt-4o")

        assert entry is None

    @pytest.mark.asyncio
    async def test_cache_expiration(self):
        cache = SemanticCache(default_ttl=timedelta(seconds=-1))

        await cache.set(
            query="test",
            response="response",
            model="gpt-4o",
        )

        entry = await cache.get("test", "gpt-4o")

        assert entry is None

    def test_cosine_similarity(self):
        vec1 = [1.0, 0.0, 0.0]
        vec2 = [1.0, 0.0, 0.0]
        vec3 = [0.0, 1.0, 0.0]

        assert cosine_similarity(vec1, vec2) == pytest.approx(1.0)
        assert cosine_similarity(vec1, vec3) == pytest.approx(0.0)


class TestSlackBot:
    def test_block_builder(self):
        blocks = (
            SlackBlockBuilder()
            .header("Test Header")
            .section("Test content")
            .divider()
            .context("Context info")
            .build()
        )

        assert len(blocks) == 4
        assert blocks[0]["type"] == "header"
        assert blocks[1]["type"] == "section"
        assert blocks[2]["type"] == "divider"
        assert blocks[3]["type"] == "context"

    def test_verify_signature(self):
        bot = SlackBot(
            bot_token="xoxb-test",
            signing_secret="test-signing-secret-for-unit-tests",  # noqa: S106
        )

        timestamp = str(int(datetime.now().timestamp()))
        body = b"token=test&command=/test"

        is_valid = bot.verify_request(timestamp, body, "invalid-sig")

        assert is_valid is False

    @pytest.mark.asyncio
    async def test_handle_slash_command(self):
        bot = SlackBot(
            bot_token="xoxb-test",
            signing_secret="test-secret",
        )

        payload = {
            "command": "/copilot-help",
            "text": "",
            "user_id": "U123",
            "user_name": "testuser",
            "channel_id": "C456",
            "channel_name": "general",
            "response_url": "https://hooks.slack.com/response",
            "trigger_id": "trigger123",
        }

        response = await bot.handle_event(SlackEventType.SLASH_COMMAND, payload)

        assert "blocks" in response
        assert len(response["blocks"]) > 0

    def test_parse_slash_command(self):
        bot = SlackBot(
            bot_token="xoxb-test",
            signing_secret="test-secret",
        )

        body = b"command=%2Finvestigate&text=why+is+it+slow"
        parsed = bot.parse_slash_command(body)

        assert parsed["command"] == "/investigate"
        assert parsed["text"] == "why is it slow"
