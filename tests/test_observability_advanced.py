"""Tests for advanced observability infrastructure."""

import pytest


class TestMetricsRegistry:
    """Tests for the metrics registry."""

    def test_counter_increment(self):
        """Test counter increments correctly."""
        from app.observability.metrics import MetricsRegistry, MetricType

        registry = MetricsRegistry()
        registry.register("test_counter", MetricType.COUNTER, "Test counter")

        registry.counter_inc("test_counter", 1.0)
        registry.counter_inc("test_counter", 2.0)

        assert registry.get_value("test_counter") == 3.0

    def test_counter_with_labels(self):
        """Test counter with labels."""
        from app.observability.metrics import MetricsRegistry, MetricType

        registry = MetricsRegistry()
        registry.register("test_labeled", MetricType.COUNTER, "Labeled counter")

        registry.counter_inc("test_labeled", 1.0, labels={"env": "prod"})
        registry.counter_inc("test_labeled", 2.0, labels={"env": "staging"})
        registry.counter_inc("test_labeled", 3.0, labels={"env": "prod"})

        assert registry.get_value("test_labeled", labels={"env": "prod"}) == 4.0
        assert registry.get_value("test_labeled", labels={"env": "staging"}) == 2.0

    def test_gauge_set(self):
        """Test gauge set and get."""
        from app.observability.metrics import MetricsRegistry, MetricType

        registry = MetricsRegistry()
        registry.register("test_gauge", MetricType.GAUGE, "Test gauge")

        registry.gauge_set("test_gauge", 42.0)
        assert registry.get_value("test_gauge") == 42.0

        registry.gauge_set("test_gauge", 100.0)
        assert registry.get_value("test_gauge") == 100.0

    def test_gauge_increment(self):
        """Test gauge increment."""
        from app.observability.metrics import MetricsRegistry, MetricType

        registry = MetricsRegistry()
        registry.register("test_gauge_inc", MetricType.GAUGE, "Test gauge inc")

        registry.gauge_inc("test_gauge_inc", 10.0)
        registry.gauge_inc("test_gauge_inc", 5.0)
        registry.gauge_inc("test_gauge_inc", -3.0)

        assert registry.get_value("test_gauge_inc") == 12.0

    def test_histogram_observe(self):
        """Test histogram observations."""
        from app.observability.metrics import MetricsRegistry, MetricType

        registry = MetricsRegistry()
        registry.register("test_histogram", MetricType.HISTOGRAM, "Test histogram")

        registry.histogram_observe("test_histogram", 0.5)
        registry.histogram_observe("test_histogram", 1.5)
        registry.histogram_observe("test_histogram", 2.0)

        output = registry.render_prometheus()

        assert "test_histogram_count" in output
        assert "test_histogram_sum" in output
        assert "test_histogram_bucket" in output

    def test_prometheus_rendering(self):
        """Test Prometheus exposition format."""
        from app.observability.metrics import MetricsRegistry, MetricType

        registry = MetricsRegistry()
        registry.register("prom_test", MetricType.COUNTER, "Prometheus test")
        registry.counter_inc("prom_test", 5.0, labels={"method": "GET"})

        output = registry.render_prometheus()

        assert "# HELP prom_test" in output
        assert "# TYPE prom_test counter" in output
        assert 'prom_test{method="GET"}' in output
        assert "5.0" in output


class TestRecordFunctions:
    """Tests for metric recording functions."""

    def test_record_llm_call(self):
        """Test recording LLM calls."""
        from app.observability.metrics import get_metrics_registry, record_llm_call

        registry = get_metrics_registry()
        registry.reset()

        record_llm_call(
            provider="anthropic",
            model="claude-sonnet-4",
            input_tokens=1000,
            output_tokens=500,
            latency_seconds=2.5,
            cached=True,
        )

        output = registry.render_prometheus()
        assert "copilot_llm_calls_total" in output
        assert "copilot_llm_tokens_input_total" in output
        assert "copilot_llm_cache_hits_total" in output

    def test_record_llm_error(self):
        """Test recording LLM errors."""
        from app.observability.metrics import get_metrics_registry, record_llm_call

        registry = get_metrics_registry()
        registry.reset()

        record_llm_call(
            provider="openai",
            model="gpt-4o",
            input_tokens=100,
            output_tokens=0,
            latency_seconds=1.0,
            error="RateLimitError",
        )

        output = registry.render_prometheus()
        assert "copilot_llm_errors_total" in output
        assert "RateLimitError" in output

    def test_record_mcp_tool_call(self):
        """Test recording MCP tool calls."""
        from app.observability.metrics import get_metrics_registry, record_mcp_tool_call

        registry = get_metrics_registry()
        registry.reset()

        record_mcp_tool_call(
            server="github",
            tool="list_issues",
            latency_seconds=0.5,
            success=True,
        )

        output = registry.render_prometheus()
        assert "copilot_mcp_tool_calls_total" in output
        assert "github" in output

    def test_record_mcp_tool_error(self):
        """Test recording MCP tool errors."""
        from app.observability.metrics import get_metrics_registry, record_mcp_tool_call

        registry = get_metrics_registry()
        registry.reset()

        record_mcp_tool_call(
            server="kubernetes",
            tool="get_pods",
            latency_seconds=5.0,
            success=False,
            error="ConnectionTimeout",
        )

        output = registry.render_prometheus()
        assert "copilot_mcp_tool_errors_total" in output
        assert "ConnectionTimeout" in output

    def test_record_investigation_lifecycle(self):
        """Test recording investigation start and complete."""
        from app.observability.metrics import (
            get_metrics_registry,
            record_investigation_complete,
            record_investigation_start,
        )

        registry = get_metrics_registry()
        registry.reset()

        record_investigation_start(org_id="org_123")
        record_investigation_complete(
            duration_seconds=45.0,
            status="success",
            tokens_used=5000,
            org_id="org_123",
        )

        output = registry.render_prometheus()
        assert "copilot_investigations_started_total" in output
        assert "copilot_investigations_completed_total" in output
        assert "copilot_tenant_investigations_total" in output

    def test_record_approval(self):
        """Test recording approval decisions."""
        from app.observability.metrics import get_metrics_registry, record_approval

        registry = get_metrics_registry()
        registry.reset()

        record_approval(approved=True, latency_seconds=120.0)

        output = registry.render_prometheus()
        assert "copilot_approvals_total" in output
        assert "copilot_approvals_latency_seconds" in output

    def test_record_circuit_breaker(self):
        """Test recording circuit breaker state."""
        from app.observability.metrics import (
            get_metrics_registry,
            record_circuit_breaker_state,
        )

        registry = get_metrics_registry()
        registry.reset()

        record_circuit_breaker_state("anthropic", "closed")
        output = registry.render_prometheus()
        assert "copilot_circuit_breaker_state" in output
        assert "0" in output

        record_circuit_breaker_state("anthropic", "open")
        output = registry.render_prometheus()
        assert "2" in output

    def test_record_injection_attempt(self):
        """Test recording injection attempts."""
        from app.observability.metrics import (
            get_metrics_registry,
            record_injection_attempt,
        )

        registry = get_metrics_registry()
        registry.reset()

        record_injection_attempt(pattern="ignore_instructions", source="user_message")

        output = registry.render_prometheus()
        assert "copilot_injection_attempts_total" in output
        assert "ignore_instructions" in output


class TestSLOTracker:
    """Tests for SLO tracking."""

    def test_define_slo(self):
        """Test defining an SLO."""
        from app.observability.slo import SLOTracker

        tracker = SLOTracker()
        tracker.define_slo(
            name="test_slo",
            target=0.95,
            window_seconds=3600,
            metric_type="ratio",
            description="Test SLO",
        )

        status = tracker.get_status("test_slo")
        assert status is not None
        assert status.target == 0.95

    def test_record_and_calculate_ratio(self):
        """Test recording observations and calculating ratio."""
        from app.observability.slo import SLOTracker

        tracker = SLOTracker()
        tracker.define_slo("success_rate", target=0.95, metric_type="ratio")

        for _ in range(8):
            tracker.record("success_rate", 1.0)
        for _ in range(2):
            tracker.record("success_rate", 0.0)

        status = tracker.get_status("success_rate")
        assert status is not None
        assert status.current == 0.8
        assert status.is_breaching

    def test_calculate_error_budget(self):
        """Test error budget calculation."""
        from app.observability.slo import SLOTracker

        tracker = SLOTracker()
        tracker.define_slo("budget_test", target=0.95, metric_type="ratio")

        for _ in range(95):
            tracker.record("budget_test", 1.0)
        for _ in range(5):
            tracker.record("budget_test", 0.0)

        status = tracker.get_status("budget_test")
        assert status is not None
        assert status.budget_remaining == pytest.approx(0.0, abs=0.01)

    def test_burn_rate_calculation(self):
        """Test burn rate calculation."""
        from app.observability.slo import SLOTracker

        tracker = SLOTracker()
        tracker.define_slo("burn_test", target=0.95, metric_type="ratio")

        for _ in range(90):
            tracker.record("burn_test", 1.0)
        for _ in range(10):
            tracker.record("burn_test", 0.0)

        status = tracker.get_status("burn_test")
        assert status is not None
        assert status.burn_rate_1h == pytest.approx(2.0, abs=0.1)

    def test_latency_slo(self):
        """Test latency p99 SLO."""
        from app.observability.slo import SLOTracker

        tracker = SLOTracker()
        tracker.define_slo("latency", target=60.0, metric_type="latency_p99")

        for i in range(99):
            tracker.record("latency", 30.0 + i * 0.1)
        tracker.record("latency", 100.0)

        status = tracker.get_status("latency")
        assert status is not None
        assert status.current > 60.0

    def test_prometheus_rendering(self):
        """Test SLO Prometheus metrics rendering."""
        from app.observability.slo import SLOTracker

        tracker = SLOTracker()
        tracker.define_slo("render_test", target=0.99, metric_type="ratio")
        tracker.record("render_test", 1.0)

        output = tracker.render_prometheus()

        assert "slo_target" in output
        assert "slo_current" in output
        assert "slo_budget_remaining" in output
        assert "slo_burn_rate_1h" in output
        assert "slo_is_breaching" in output

    def test_get_all_statuses(self):
        """Test getting all SLO statuses."""
        from app.observability.slo import SLOTracker

        tracker = SLOTracker()
        tracker.define_slo("slo_a", target=0.95, metric_type="ratio")
        tracker.define_slo("slo_b", target=0.99, metric_type="ratio")

        statuses = tracker.get_all_statuses()
        assert len(statuses) == 2
        assert {s.name for s in statuses} == {"slo_a", "slo_b"}


class TestSLOConvenienceFunctions:
    """Tests for SLO convenience functions."""

    def test_record_investigation_slo(self):
        """Test recording investigation SLO."""
        from app.observability.slo import get_slo_tracker, record_investigation_slo

        tracker = get_slo_tracker()
        tracker.reset()

        record_investigation_slo(success=True, latency_seconds=30.0)
        record_investigation_slo(success=True, latency_seconds=45.0)
        record_investigation_slo(success=False, latency_seconds=120.0)

        status = tracker.get_status("investigation_success_rate")
        assert status is not None
        assert status.current == pytest.approx(0.667, abs=0.01)

    def test_record_llm_slo(self):
        """Test recording LLM SLO."""
        from app.observability.slo import get_slo_tracker, record_llm_slo

        tracker = get_slo_tracker()
        tracker.reset()

        for _ in range(99):
            record_llm_slo(success=True)
        record_llm_slo(success=False)

        status = tracker.get_status("llm_success_rate")
        assert status is not None
        assert status.current == 0.99


class TestTracing:
    """Tests for distributed tracing."""

    def test_tracing_disabled_by_default(self):
        """Test that tracing is disabled when env var not set."""
        from app.observability.tracing import _reset_for_tests, is_enabled

        _reset_for_tests()
        assert not is_enabled()

    def test_span_noop_when_disabled(self):
        """Test that span is noop when tracing disabled."""
        from app.observability.tracing import _reset_for_tests, span

        _reset_for_tests()

        with span("test_span", {"key": "value"}) as s:
            assert s is None

    def test_traced_decorator_noop(self):
        """Test traced decorator works when disabled."""
        from app.observability.tracing import _reset_for_tests, traced

        _reset_for_tests()

        @traced("test_function")
        def my_function(x: int) -> int:
            return x * 2

        assert my_function(5) == 10

    @pytest.mark.asyncio
    async def test_traced_async_decorator_noop(self):
        """Test traced async decorator works when disabled."""
        from app.observability.tracing import _reset_for_tests, traced

        _reset_for_tests()

        @traced("test_async_function")
        async def my_async_function(x: int) -> int:
            return x * 2

        assert await my_async_function(5) == 10

    def test_add_span_attributes_noop(self):
        """Test add_span_attributes is noop when disabled."""
        from app.observability.tracing import _reset_for_tests, add_span_attributes

        _reset_for_tests()
        add_span_attributes(key="value", count=42)

    def test_record_exception_noop(self):
        """Test record_exception is noop when disabled."""
        from app.observability.tracing import _reset_for_tests, record_exception

        _reset_for_tests()
        record_exception(ValueError("test error"), {"context": "test"})

    def test_get_trace_context_empty(self):
        """Test get_trace_context returns empty when disabled."""
        from app.observability.tracing import _reset_for_tests, get_trace_context

        _reset_for_tests()
        assert get_trace_context() == {}

    def test_baggage_noop(self):
        """Test baggage operations are noop when disabled."""
        from app.observability.tracing import (
            _reset_for_tests,
            get_baggage,
            set_baggage,
        )

        _reset_for_tests()
        set_baggage("tenant_id", "org_123")
        assert get_baggage("tenant_id") is None

    def test_llm_span_noop(self):
        """Test llm_span is noop when disabled."""
        from app.observability.tracing import _reset_for_tests, llm_span

        _reset_for_tests()

        with llm_span("anthropic", "claude-sonnet-4") as s:
            assert s is None

    def test_mcp_span_noop(self):
        """Test mcp_span is noop when disabled."""
        from app.observability.tracing import _reset_for_tests, mcp_span

        _reset_for_tests()

        with mcp_span("github", "list_issues") as s:
            assert s is None

    def test_investigation_span_noop(self):
        """Test investigation_span is noop when disabled."""
        from app.observability.tracing import _reset_for_tests, investigation_span

        _reset_for_tests()

        with investigation_span("thread_123", "org_456") as s:
            assert s is None


class TestCostEstimation:
    """Tests for LLM cost estimation."""

    def test_anthropic_sonnet_cost(self):
        """Test Anthropic Sonnet cost calculation."""
        from app.observability.metrics import _estimate_llm_cost

        cost = _estimate_llm_cost("anthropic", "claude-sonnet-4", 1_000_000, 500_000)
        expected = 3.0 + 7.5
        assert cost == pytest.approx(expected, abs=0.01)

    def test_anthropic_opus_cost(self):
        """Test Anthropic Opus cost calculation."""
        from app.observability.metrics import _estimate_llm_cost

        cost = _estimate_llm_cost("anthropic", "claude-opus-4", 100_000, 50_000)
        expected = 1.5 + 3.75
        assert cost == pytest.approx(expected, abs=0.01)

    def test_anthropic_haiku_cost(self):
        """Test Anthropic Haiku cost calculation."""
        from app.observability.metrics import _estimate_llm_cost

        cost = _estimate_llm_cost("anthropic", "claude-haiku-4", 1_000_000, 1_000_000)
        expected = 0.25 + 1.25
        assert cost == pytest.approx(expected, abs=0.01)

    def test_openai_gpt4o_cost(self):
        """Test OpenAI GPT-4o cost calculation."""
        from app.observability.metrics import _estimate_llm_cost

        cost = _estimate_llm_cost("openai", "gpt-4o", 1_000_000, 500_000)
        expected = 2.5 + 5.0
        assert cost == pytest.approx(expected, abs=0.01)

    def test_unknown_model_default_cost(self):
        """Test unknown model uses default pricing."""
        from app.observability.metrics import _estimate_llm_cost

        cost = _estimate_llm_cost("unknown", "mystery-model", 1_000_000, 1_000_000)
        expected = 1.0 + 3.0
        assert cost == pytest.approx(expected, abs=0.01)


class TestObservabilityModuleExports:
    """Tests for module exports."""

    def test_metrics_exports(self):
        """Test metrics module exports."""
        from app.observability import (
            MetricsRegistry,
            MetricType,
            get_metrics_registry,
            record_approval,
            record_circuit_breaker_state,
            record_injection_attempt,
            record_investigation_complete,
            record_investigation_start,
            record_llm_call,
            record_mcp_tool_call,
        )

        assert MetricsRegistry is not None
        assert MetricType is not None
        assert callable(get_metrics_registry)
        assert callable(record_llm_call)
        assert callable(record_mcp_tool_call)
        assert callable(record_investigation_start)
        assert callable(record_investigation_complete)
        assert callable(record_approval)
        assert callable(record_circuit_breaker_state)
        assert callable(record_injection_attempt)

    def test_tracing_exports(self):
        """Test tracing module exports."""
        from app.observability import (
            add_span_attributes,
            get_baggage,
            get_trace_context,
            get_tracer,
            init_tracing,
            investigation_span,
            is_enabled,
            llm_span,
            mcp_span,
            record_exception,
            set_baggage,
            span,
            traced,
        )

        assert callable(init_tracing)
        assert callable(is_enabled)
        assert callable(get_tracer)
        assert callable(span)
        assert callable(traced)
        assert callable(add_span_attributes)
        assert callable(record_exception)
        assert callable(get_trace_context)
        assert callable(set_baggage)
        assert callable(get_baggage)
        assert callable(llm_span)
        assert callable(mcp_span)
        assert callable(investigation_span)

    def test_slo_exports(self):
        """Test SLO module exports."""
        from app.observability import (
            SLODefinition,
            SLOStatus,
            SLOTracker,
            get_slo_tracker,
            record_investigation_slo,
            record_llm_slo,
        )

        assert SLODefinition is not None
        assert SLOStatus is not None
        assert SLOTracker is not None
        assert callable(get_slo_tracker)
        assert callable(record_investigation_slo)
        assert callable(record_llm_slo)


class TestAdditionalMetricsFunctions:
    """Tests for additional metrics recording functions."""

    def test_record_websocket_connection(self):
        """Test WebSocket connection tracking."""
        from app.observability.metrics import (
            get_metrics_registry,
            record_websocket_connection,
        )

        registry = get_metrics_registry()
        registry.reset()

        record_websocket_connection(1)
        record_websocket_connection(1)
        record_websocket_connection(-1)

        output = registry.render_prometheus()
        assert "copilot_websocket_connections_active" in output

    def test_record_websocket_message(self):
        """Test WebSocket message tracking."""
        from app.observability.metrics import (
            get_metrics_registry,
            record_websocket_message,
        )

        registry = get_metrics_registry()
        registry.reset()

        record_websocket_message("received")
        record_websocket_message("sent")

        output = registry.render_prometheus()
        assert "copilot_websocket_messages_total" in output

    def test_record_cache_access(self):
        """Test cache access tracking."""
        from app.observability.metrics import get_metrics_registry, record_cache_access

        registry = get_metrics_registry()
        registry.reset()

        record_cache_access(hit=True)
        record_cache_access(hit=True)
        record_cache_access(hit=False)

        output = registry.render_prometheus()
        assert "copilot_cache_hits_total" in output
        assert "copilot_cache_misses_total" in output

    def test_record_auth_failure(self):
        """Test auth failure tracking."""
        from app.observability.metrics import get_metrics_registry, record_auth_failure

        registry = get_metrics_registry()
        registry.reset()

        record_auth_failure("invalid_token")
        record_auth_failure("expired_token")

        output = registry.render_prometheus()
        assert "copilot_auth_failures_total" in output
        assert "invalid_token" in output

    def test_record_rate_limit(self):
        """Test rate limit tracking."""
        from app.observability.metrics import get_metrics_registry, record_rate_limit

        registry = get_metrics_registry()
        registry.reset()

        record_rate_limit("/api/investigate")

        output = registry.render_prometheus()
        assert "copilot_rate_limit_hits_total" in output
        assert "/api/investigate" in output
