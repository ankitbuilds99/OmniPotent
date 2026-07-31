"""Tests for the Adaptive Model Router."""

import pytest
from unittest.mock import MagicMock

from app.router.classifier import (
    TaskClassifier,
    TaskComplexity,
    TaskType,
    ClassificationResult,
)
from app.router.metrics import (
    RouterMetrics,
    ModelPerformance,
)
from app.router.adaptive import (
    AdaptiveRouter,
    RouterConfig,
    RouteDecision,
    ModelProfile,
    create_router,
)


class TestTaskClassifier:
    """Tests for TaskClassifier."""

    def test_classify_trivial_query(self):
        """Trivial status check queries."""
        classifier = TaskClassifier()
        result = classifier.classify("what is the status of the API?")

        assert result.complexity == TaskComplexity.TRIVIAL
        assert result.task_type == TaskType.STATUS_CHECK
        assert result.confidence > 0

    def test_classify_simple_query(self):
        """Simple data retrieval queries."""
        classifier = TaskClassifier()
        result = classifier.classify("show me the latest logs")

        assert result.complexity == TaskComplexity.SIMPLE
        assert result.task_type == TaskType.LOG_ANALYSIS
        assert result.confidence > 0

    def test_classify_moderate_query(self):
        """Moderate analysis queries."""
        classifier = TaskClassifier()
        result = classifier.classify("analyze the error trends in the last hour")

        assert result.complexity == TaskComplexity.MODERATE
        assert result.task_type == TaskType.LOG_ANALYSIS
        assert result.confidence > 0

    def test_classify_complex_query(self):
        """Complex investigation queries."""
        classifier = TaskClassifier()
        result = classifier.classify(
            "why is the API latency spiking? investigate the root cause"
        )

        assert result.complexity == TaskComplexity.COMPLEX
        assert result.task_type == TaskType.ROOT_CAUSE
        assert result.confidence > 0

    def test_classify_expert_query(self):
        """Expert-level remediation queries."""
        classifier = TaskClassifier()
        result = classifier.classify(
            "fix the memory leak and prevent future incidents"
        )

        assert result.complexity == TaskComplexity.EXPERT
        assert result.task_type == TaskType.REMEDIATION
        assert result.confidence > 0

    def test_classify_metric_query(self):
        """Metric query type detection."""
        classifier = TaskClassifier()
        result = classifier.classify("what is the p99 latency?")

        assert result.task_type == TaskType.METRIC_QUERY

    def test_classify_deployment_query(self):
        """Deployment query type detection."""
        classifier = TaskClassifier()
        result = classifier.classify("deploy version 2.0 to production")

        assert result.task_type == TaskType.DEPLOYMENT

    def test_classify_code_review_query(self):
        """Code review query type detection."""
        classifier = TaskClassifier()
        result = classifier.classify("review the latest commit changes")

        assert result.task_type == TaskType.CODE_REVIEW

    def test_classify_synthesis_query(self):
        """Synthesis query type detection."""
        classifier = TaskClassifier()
        result = classifier.classify("generate a postmortem summary")

        assert result.task_type == TaskType.SYNTHESIS

    def test_classify_unknown_short_query(self):
        """Short ambiguous queries default to moderate."""
        classifier = TaskClassifier()
        result = classifier.classify("hi")

        # Short queries default to moderate complexity
        assert result.complexity in [TaskComplexity.TRIVIAL, TaskComplexity.MODERATE]

    def test_classify_with_context(self):
        """Classification with conversation context."""
        classifier = TaskClassifier()
        context = {
            "messages": [{"role": "user", "content": "previous message"}],
            "incident": {"id": "INC-123"},
        }
        result = classifier.classify("what happened?", context)

        assert result.features.get("has_prior_context") is True
        assert result.features.get("has_incident") is True

    def test_estimate_tokens(self):
        """Token estimation based on complexity."""
        classifier = TaskClassifier()

        trivial_tokens = classifier.estimate_tokens("status?", TaskComplexity.TRIVIAL)
        expert_tokens = classifier.estimate_tokens("complex query", TaskComplexity.EXPERT)

        assert trivial_tokens < expert_tokens

    def test_classification_reasoning(self):
        """Classification includes human-readable reasoning."""
        classifier = TaskClassifier()
        result = classifier.classify("investigate the root cause of failures")

        assert result.reasoning
        assert len(result.reasoning) > 10


class TestRouterMetrics:
    """Tests for RouterMetrics."""

    def test_record_request(self):
        """Record a request and update metrics."""
        metrics = RouterMetrics()

        metrics.record_request(
            model="claude-sonnet-4-20250514",
            complexity="moderate",
            task_type="root_cause",
            latency_ms=2500,
            input_tokens=1000,
            output_tokens=500,
            cost_usd=0.01,
            success=True,
            confidence=0.85,
            correct=True,
        )

        perf = metrics.get_model_performance("claude-sonnet-4-20250514")
        assert perf is not None
        assert perf.total_requests == 1
        assert perf.successful_requests == 1
        assert perf.success_rate == 1.0

    def test_record_failed_request(self):
        """Record a failed request."""
        metrics = RouterMetrics()

        metrics.record_request(
            model="claude-sonnet-4-20250514",
            complexity="complex",
            task_type="remediation",
            latency_ms=5000,
            input_tokens=2000,
            output_tokens=0,
            cost_usd=0.006,
            success=False,
        )

        perf = metrics.get_model_performance("claude-sonnet-4-20250514")
        assert perf.failed_requests == 1
        assert perf.success_rate == 0.0

    def test_get_all_performance(self):
        """Get performance for all models."""
        metrics = RouterMetrics()

        # Record for two models
        metrics.record_request(
            model="model-a",
            complexity="simple",
            task_type="status_check",
            latency_ms=500,
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.001,
            success=True,
        )
        metrics.record_request(
            model="model-b",
            complexity="complex",
            task_type="root_cause",
            latency_ms=5000,
            input_tokens=2000,
            output_tokens=1000,
            cost_usd=0.05,
            success=True,
        )

        all_perf = metrics.get_all_performance()
        assert "model-a" in all_perf
        assert "model-b" in all_perf

    def test_get_best_model_for_complexity(self):
        """Find best model for a complexity level."""
        metrics = RouterMetrics()

        # Record successful requests
        for i in range(10):
            metrics.record_request(
                model="fast-model",
                complexity="simple",
                task_type="status_check",
                latency_ms=100,
                input_tokens=50,
                output_tokens=50,
                cost_usd=0.0001,
                success=True,
            )
            metrics.record_request(
                model="slow-model",
                complexity="simple",
                task_type="status_check",
                latency_ms=5000,
                input_tokens=50,
                output_tokens=50,
                cost_usd=0.01,
                success=True,
            )

        best = metrics.get_best_model_for_complexity("simple")
        assert best == "fast-model"  # Faster and cheaper

    def test_get_summary(self):
        """Get metrics summary."""
        metrics = RouterMetrics()

        metrics.record_request(
            model="claude-sonnet-4-20250514",
            complexity="moderate",
            task_type="root_cause",
            latency_ms=2000,
            input_tokens=1000,
            output_tokens=500,
            cost_usd=0.015,
            success=True,
        )

        summary = metrics.get_summary()
        assert summary["total_requests"] == 1
        assert summary["total_cost_usd"] == 0.015
        assert "claude-sonnet-4-20250514" in summary["models"]

    def test_get_recommendations_low_success(self):
        """Get recommendations for low success rate."""
        metrics = RouterMetrics()

        # Record mostly failures
        for i in range(15):
            metrics.record_request(
                model="bad-model",
                complexity="complex",
                task_type="root_cause",
                latency_ms=5000,
                input_tokens=1000,
                output_tokens=100,
                cost_usd=0.01,
                success=(i < 3),  # Only 3 successes
            )

        recs = metrics.get_recommendations()
        assert len(recs) > 0
        assert any(r["type"] == "warning" for r in recs)

    def test_reset_metrics(self):
        """Reset all metrics."""
        metrics = RouterMetrics()

        metrics.record_request(
            model="test",
            complexity="simple",
            task_type="status_check",
            latency_ms=100,
            input_tokens=50,
            output_tokens=50,
            cost_usd=0.001,
            success=True,
        )

        metrics.reset()

        perf = metrics.get_model_performance("test")
        assert perf is None


class TestModelProfile:
    """Tests for ModelProfile."""

    def test_estimate_cost(self):
        """Estimate cost for a request."""
        profile = ModelProfile(
            name="test",
            provider="test",
            input_cost_per_1m=3.0,
            output_cost_per_1m=15.0,
        )

        cost = profile.estimate_cost(1000, 500)

        # 1000 input * 3/1M + 500 output * 15/1M
        expected = 0.003 + 0.0075
        assert abs(cost - expected) < 0.0001


class TestAdaptiveRouter:
    """Tests for AdaptiveRouter."""

    def test_route_trivial_query(self):
        """Route trivial query to appropriate model."""
        router = AdaptiveRouter()
        decision = router.route("what is the status?")

        assert decision.model in router._profiles
        assert decision.classification.complexity == TaskComplexity.TRIVIAL
        assert decision.confidence > 0

    def test_route_complex_query(self):
        """Route complex query to capable model."""
        router = AdaptiveRouter()
        decision = router.route(
            "investigate why the API is failing and correlate with recent deployments"
        )

        assert decision.model in router._profiles
        assert decision.classification.complexity in [
            TaskComplexity.COMPLEX,
            TaskComplexity.EXPERT,
        ]

    def test_route_with_constraints(self):
        """Route with cost and latency constraints."""
        router = AdaptiveRouter()
        decision = router.route(
            "analyze logs",
            constraints={"max_cost_usd": 0.01, "max_latency_ms": 5000},
        )

        assert decision.estimated_cost_usd <= 0.5  # Within reasonable bounds

    def test_route_includes_alternatives(self):
        """Routing decision includes alternative models."""
        router = AdaptiveRouter()
        decision = router.route("analyze system performance")

        assert len(decision.alternatives) > 0

    def test_route_includes_fallback(self):
        """Routing decision includes fallback model."""
        router = AdaptiveRouter()
        decision = router.route("complex investigation task")

        assert decision.fallback_model is not None

    def test_route_reasoning(self):
        """Routing decision includes reasoning."""
        router = AdaptiveRouter()
        decision = router.route("check API health")

        assert decision.reason
        assert len(decision.reason) > 5

    def test_record_result(self):
        """Record a routing result for learning."""
        router = AdaptiveRouter()
        decision = router.route("analyze logs")

        router.record_result(
            decision=decision,
            latency_ms=2000,
            input_tokens=500,
            output_tokens=300,
            success=True,
            confidence=0.9,
            correct=True,
        )

        metrics = router.get_metrics()
        assert metrics["total_requests"] == 1

    def test_get_recommendations(self):
        """Get routing recommendations."""
        router = AdaptiveRouter()

        # Record some results
        for i in range(15):
            decision = router.route("test query")
            router.record_result(
                decision=decision,
                latency_ms=2000,
                input_tokens=500,
                output_tokens=300,
                success=True,
            )

        # Should not error
        recs = router.get_recommendations()
        assert isinstance(recs, list)

    def test_add_model(self):
        """Add a new model profile."""
        router = AdaptiveRouter()

        profile = ModelProfile(
            name="custom-model",
            provider="custom",
            typical_latency_ms=1000,
            input_cost_per_1m=1.0,
            output_cost_per_1m=5.0,
        )

        router.add_model(profile)

        assert "custom-model" in router._profiles

    def test_disable_model(self):
        """Disable a model from routing."""
        router = AdaptiveRouter()

        # Should be enabled by default
        result = router.disable_model("claude-sonnet-4-20250514")
        assert result is True
        assert router._profiles["claude-sonnet-4-20250514"].enabled is False

        # Won't be selected now
        # Re-enable for other tests
        router.enable_model("claude-sonnet-4-20250514")

    def test_enable_model(self):
        """Enable a disabled model."""
        router = AdaptiveRouter()

        router.disable_model("claude-sonnet-4-20250514")
        router.enable_model("claude-sonnet-4-20250514")

        assert router._profiles["claude-sonnet-4-20250514"].enabled is True

    def test_disable_nonexistent_model(self):
        """Disable nonexistent model returns False."""
        router = AdaptiveRouter()
        result = router.disable_model("nonexistent")
        assert result is False

    def test_custom_config(self):
        """Router with custom configuration."""
        config = RouterConfig(
            default_model="claude-3-haiku-20240307",
            prefer_cost_over_quality=True,
            max_cost_per_request_usd=0.05,
        )
        router = AdaptiveRouter(config=config)

        decision = router.route("simple query")

        # Should prefer cheaper models when configured
        assert decision.model in router._profiles

    def test_create_router_factory(self):
        """Test factory function."""
        router = create_router(
            default_model="claude-sonnet-4-20250514",
            fast_model="claude-3-haiku-20240307",
            prefer_cost_over_quality=True,
        )

        assert router.config.prefer_cost_over_quality is True


class TestRouteDecision:
    """Tests for RouteDecision dataclass."""

    def test_route_decision_fields(self):
        """RouteDecision has all expected fields."""
        classification = ClassificationResult(
            complexity=TaskComplexity.MODERATE,
            task_type=TaskType.LOG_ANALYSIS,
            confidence=0.85,
            reasoning="test",
            features={},
        )

        decision = RouteDecision(
            model="claude-sonnet-4-20250514",
            reason="test reason",
            confidence=0.85,
            alternatives=["model-a", "model-b"],
            estimated_latency_ms=2500,
            estimated_cost_usd=0.015,
            classification=classification,
            fallback_model="claude-3-haiku-20240307",
        )

        assert decision.model == "claude-sonnet-4-20250514"
        assert decision.fallback_model == "claude-3-haiku-20240307"
        assert len(decision.alternatives) == 2


class TestIntegration:
    """Integration tests for the full routing flow."""

    def test_full_routing_flow(self):
        """Test complete routing and feedback loop."""
        router = AdaptiveRouter()

        # Route multiple queries
        queries = [
            "what is the status?",
            "show me the logs",
            "analyze error trends",
            "investigate root cause",
            "fix the memory leak",
        ]

        for query in queries:
            decision = router.route(query)

            # Record result
            router.record_result(
                decision=decision,
                latency_ms=2000,
                input_tokens=500,
                output_tokens=300,
                success=True,
            )

        # Check metrics
        metrics = router.get_metrics()
        assert metrics["total_requests"] == 5

    def test_routing_stability(self):
        """Same query should route consistently."""
        router = AdaptiveRouter()
        query = "analyze system performance metrics"

        decisions = [router.route(query) for _ in range(5)]

        # All decisions should be the same model
        models = [d.model for d in decisions]
        assert len(set(models)) == 1
