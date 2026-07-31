"""Tests for middleware stack (tracing, error handling, metrics)."""

import pytest


class TestTracingMiddleware:
    """Tests for request tracing."""

    def test_trace_context_creation(self):
        """Test TraceContext creation."""
        from app.middleware.tracing import TraceContext

        ctx = TraceContext(
            trace_id="abc123",
            span_id="def456",
        )

        assert ctx.trace_id == "abc123"
        assert ctx.span_id == "def456"
        assert ctx.parent_span_id is None

    def test_trace_context_child_span(self):
        """Test creating child spans."""
        from app.middleware.tracing import TraceContext

        parent = TraceContext(
            trace_id="abc123",
            span_id="parent",
        )

        child = parent.child_span()

        assert child.trace_id == parent.trace_id
        assert child.parent_span_id == parent.span_id
        assert child.span_id != parent.span_id

    def test_trace_context_duration(self):
        """Test duration calculation."""
        import time
        from app.middleware.tracing import TraceContext

        ctx = TraceContext(trace_id="test", span_id="test")
        time.sleep(0.01)

        assert ctx.duration_ms >= 10

    def test_trace_context_to_headers(self):
        """Test converting to HTTP headers."""
        from app.middleware.tracing import TraceContext

        ctx = TraceContext(
            trace_id="abc123",
            span_id="def456",
            parent_span_id="parent",
        )

        headers = ctx.to_headers()

        assert headers["x-trace-id"] == "abc123"
        assert headers["x-span-id"] == "def456"
        assert headers["x-parent-span-id"] == "parent"

    def test_trace_context_attributes(self):
        """Test setting attributes."""
        from app.middleware.tracing import TraceContext

        ctx = TraceContext(trace_id="test", span_id="test")
        ctx.set_attribute("http.method", "GET")
        ctx.set_attribute("http.status_code", 200)

        log_dict = ctx.to_log_dict()

        assert log_dict["http.method"] == "GET"
        assert log_dict["http.status_code"] == 200

    def test_get_set_trace_context(self):
        """Test context variable operations."""
        from app.middleware.tracing import (
            TraceContext,
            get_trace_context,
            set_trace_context,
        )

        assert get_trace_context() is None

        ctx = TraceContext(trace_id="test", span_id="test")
        set_trace_context(ctx)

        assert get_trace_context() == ctx

        set_trace_context(None)
        assert get_trace_context() is None


class TestRequestMetrics:
    """Tests for request metrics collection."""

    def test_record_request(self):
        """Test recording a request."""
        from app.middleware.metrics import RequestMetrics

        metrics = RequestMetrics()

        metrics.record_request(
            endpoint="/api/chat",
            method="POST",
            status_code=200,
            duration_ms=150.0,
        )

        assert metrics.total_requests == 1
        assert metrics.requests_by_status[200] == 1
        assert metrics.requests_by_endpoint["/api/chat"] == 1
        assert metrics.requests_by_method["POST"] == 1
        assert metrics.avg_latency_ms == 150.0

    def test_error_rate_calculation(self):
        """Test error rate calculation."""
        from app.middleware.metrics import RequestMetrics

        metrics = RequestMetrics()

        for _ in range(8):
            metrics.record_request("/api", "GET", 200, 100.0)

        for _ in range(2):
            metrics.record_request("/api", "GET", 500, 100.0, error_type="InternalError")

        assert metrics.total_requests == 10
        assert metrics.total_errors == 2
        assert metrics.error_rate == 0.2

    def test_latency_buckets(self):
        """Test latency histogram buckets."""
        from app.middleware.metrics import RequestMetrics

        metrics = RequestMetrics()

        metrics.record_request("/api", "GET", 200, 25.0)
        metrics.record_request("/api", "GET", 200, 75.0)
        metrics.record_request("/api", "GET", 200, 150.0)
        metrics.record_request("/api", "GET", 200, 750.0)
        metrics.record_request("/api", "GET", 200, 6000.0)

        # Updated bucket assertions for new bucket system
        assert metrics.latency_count == 5
        assert metrics.p50_latency_ms > 0
        assert metrics.p95_latency_ms > 0

    def test_prometheus_export(self):
        """Test Prometheus format export."""
        from app.middleware.metrics import RequestMetrics

        metrics = RequestMetrics()
        metrics.record_request("/api", "GET", 200, 100.0)
        metrics.record_request("/api", "POST", 500, 200.0, error_type="Error")

        prometheus = metrics.to_prometheus()

        assert "devcopilot_http_requests_total" in prometheus
        assert "devcopilot_http_errors_total" in prometheus
        assert 'devcopilot_http_requests_total{status="200"} 1' in prometheus

    def test_metrics_reset(self):
        """Test metrics reset."""
        from app.middleware.metrics import RequestMetrics

        metrics = RequestMetrics()
        metrics.record_request("/api", "GET", 200, 100.0)

        assert metrics.total_requests == 1

        metrics.reset()

        assert metrics.total_requests == 0
        assert metrics.error_rate == 0.0


class TestRequestLogger:
    """Tests for request logging."""

    def test_request_log_creation(self):
        """Test RequestLog dataclass."""
        from app.middleware.request_logger import RequestLog

        log = RequestLog(
            method="POST",
            path="/api/chat",
            status_code=200,
            duration_ms=150.5,
            client_ip="192.168.1.1",
            trace_id="abc123",
        )

        assert log.method == "POST"
        assert log.path == "/api/chat"
        assert log.status_code == 200

    def test_request_log_to_dict(self):
        """Test log serialization."""
        from app.middleware.request_logger import RequestLog

        log = RequestLog(
            method="GET",
            path="/healthz",
            status_code=200,
            duration_ms=5.0,
            trace_id="test",
        )

        data = log.to_dict()

        assert data["method"] == "GET"
        assert data["path"] == "/healthz"
        assert data["duration_ms"] == 5.0
        assert "timestamp" in data

    def test_redact_headers(self):
        """Test header redaction."""
        from app.middleware.request_logger import _redact_headers

        headers = {
            "content-type": "application/json",
            "authorization": "Bearer secret-token",
            "x-api-key": "api-key-123",
        }

        redacted = _redact_headers(headers)

        assert redacted["content-type"] == "application/json"
        assert redacted["authorization"] == "[REDACTED]"
        assert redacted["x-api-key"] == "[REDACTED]"

    def test_redact_body(self):
        """Test body redaction."""
        from app.middleware.request_logger import _redact_body

        body = {
            "username": "john",
            "password": "secret123",
            "api_key": "key123",
            "message": "Hello world",
        }

        redacted = _redact_body(body)

        assert redacted["username"] == "john"
        assert redacted["password"] == "[REDACTED]"
        assert redacted["api_key"] == "[REDACTED]"
        assert redacted["message"] == "Hello world"


class TestErrorHandlerMiddleware:
    """Tests for error handling middleware."""

    def test_error_response_format(self):
        """Test standardized error response."""
        from app.middleware.error_handler import _error_response

        response = _error_response(
            status_code=400,
            error="VALIDATION_ERROR",
            message="Invalid input",
            request_id="req_123",
        )

        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_copilot_error_handler(self):
        """Test handling CopilotError."""
        from unittest.mock import MagicMock
        from app.middleware.error_handler import copilot_error_handler
        from app.error_handling.exceptions import ValidationError

        request = MagicMock()
        error = ValidationError("Invalid field", field="email")

        response = await copilot_error_handler(request, error)

        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_generic_error_handler(self):
        """Test handling unexpected exceptions."""
        from unittest.mock import MagicMock
        from app.middleware.error_handler import generic_error_handler

        request = MagicMock()
        request.url.path = "/api/test"

        response = await generic_error_handler(request, ValueError("Unexpected"))

        assert response.status_code == 500
